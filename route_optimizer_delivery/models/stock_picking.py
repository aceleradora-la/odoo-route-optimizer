# -*- coding: utf-8 -*-
import base64
import binascii
import logging
import uuid
from datetime import timedelta
from urllib.parse import quote as url_quote

from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare

from odoo.addons.route_optimizer.models.stock_picking import _move_uom

from ..services import sms_client, whatsapp_client

_logger = logging.getLogger(__name__)

MAX_PHOTOS = 6

DELIVERY_STATES = [
    ("pending", "Pendiente"),
    ("delivered", "Entregado"),
    ("partial", "Entrega parcial"),
    ("failed", "No entregado"),
]


def _b64_payload(data):
    """Acepta un data URL o base64 pelado y devuelve base64 válido (texto) o False.

    Se devuelve como texto: es lo que aceptan los campos binarios en todas las
    versiones (Odoo 20 rechaza `bytes`, que ya no sabe si vienen codificados).
    """
    if not data:
        return False
    if isinstance(data, str) and data.startswith("data:"):
        data = data.split(",", 1)[-1]
    try:
        base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError, TypeError):
        raise UserError(_("La imagen recibida no es válida. Probá sacarla de nuevo."))
    return data.decode() if isinstance(data, bytes) else data


class StockPicking(models.Model):
    _inherit = "stock.picking"

    route_delivery_state = fields.Selection(
        DELIVERY_STATES,
        string="Estado de entrega",
        default="pending",
        copy=False,
        index=True,
        tracking=True,
    )
    route_delivery_done_at = fields.Datetime(string="Hora de entrega", copy=False, readonly=True)
    route_delivery_user_id = fields.Many2one(
        "res.users", string="Entregó", copy=False, readonly=True
    )
    route_delivery_fail_reason_id = fields.Many2one(
        "route.delivery.fail.reason", string="Motivo de no entrega", copy=False
    )
    route_delivery_note = fields.Text(string="Comentario del chofer", copy=False)
    route_delivery_receiver_name = fields.Char(string="Recibió", copy=False)
    route_delivery_receiver_doc = fields.Char(string="DNI de quien recibió", copy=False)
    route_delivery_packages = fields.Integer(string="Bultos entregados", copy=False)
    route_delivery_cod_amount = fields.Float(string="Cobrado en la entrega", copy=False)
    route_delivery_partial_detail = fields.Text(
        string="Detalle de entrega parcial", copy=False, readonly=True
    )
    route_delivery_signature = fields.Image(
        string="Firma", copy=False, max_width=1024, max_height=1024
    )
    route_delivery_photo_ids = fields.Many2many(
        "ir.attachment",
        "route_delivery_picking_photo_rel",
        "picking_id",
        "attachment_id",
        string="Fotos de la entrega",
        copy=False,
    )
    route_delivery_latitude = fields.Float(
        string="Latitud de la entrega", digits=(10, 7), copy=False, readonly=True
    )
    route_delivery_longitude = fields.Float(
        string="Longitud de la entrega", digits=(10, 7), copy=False, readonly=True
    )
    route_delivery_eta = fields.Datetime(
        string="Hora estimada",
        copy=False,
        readonly=True,
        help="Hora estimada de llegada a esta parada. Se recalcula al optimizar, al "
        "salir la ruta y cada vez que se completa una parada.",
    )
    route_delivery_planned_eta = fields.Datetime(
        string="Hora prevista al salir",
        copy=False,
        readonly=True,
        help="Hora estimada que tenía la parada cuando salió la ruta. La diferencia con "
        "la hora estimada actual es el atraso.",
    )
    route_delivery_token = fields.Char(
        string="Token de seguimiento", copy=False, index=True, readonly=True
    )
    route_delivery_tracking_url = fields.Char(
        string="Link de seguimiento", compute="_compute_route_delivery_tracking_url"
    )
    route_delivery_next_notified = fields.Boolean(copy=False)
    route_delivery_load_data = fields.Json(
        copy=False,
        readonly=True,
        help="Control de carga: qué bultos de este traslado ya se subieron al vehículo.",
    )
    route_delivery_client_id = fields.Char(
        copy=False,
        readonly=True,
        help="Identificador que genera la app para cada entrega: si el mismo envío llega "
        "dos veces (reintento tras quedarse sin señal), no se registra de nuevo.",
    )

    # ------------------------------------------------------------------
    # Seguimiento
    # ------------------------------------------------------------------

    @api.depends("route_delivery_token")
    def _compute_route_delivery_tracking_url(self):
        for picking in self:
            if picking.route_delivery_token:
                picking.route_delivery_tracking_url = "%s/reparto/t/%s" % (
                    picking.get_base_url(),
                    picking.route_delivery_token,
                )
            else:
                picking.route_delivery_tracking_url = False

    def _route_delivery_step(self):
        """En qué punto del reparto está la entrega: (paso, paradas que faltan antes).

        scheduled → on_the_way → next → delivered, o failed / cancel.
        """
        self.ensure_one()
        state = self.route_delivery_state or "pending"
        if self.state == "cancel":
            return "cancel", 0
        if state in ("delivered", "partial"):
            return "delivered", 0
        if state == "failed":
            return "failed", 0
        batch = self.batch_id
        ahead = 0
        if batch:
            pending = batch._route_delivery_stops().filtered(
                lambda p: p.route_delivery_state == "pending"
            )
            ahead = list(pending).index(self) if self in pending else 0
        if batch.route_delivery_started_at:
            return ("next" if ahead == 0 else "on_the_way"), ahead
        return "scheduled", ahead

    def route_delivery_pod_data(self):
        """Datos de la tarjeta de entrega del formulario del traslado."""
        self.ensure_one()
        step, ahead = self._route_delivery_step()
        partner = self._route_delivery_customer()
        return {
            "step": step,
            "ahead": ahead,
            "in_route": bool(self.batch_id),
            "route": self.batch_id.name or "",
            "route_id": self.batch_id.id or False,
            "driver": self.batch_id.user_id.name or "",
            "has_phone": bool(partner.phone),
            "photos": [{"id": att.id, "name": att.name} for att in self.route_delivery_photo_ids],
            "has_signature": bool(self.route_delivery_signature),
            "late_minutes": round(
                (self.route_delivery_eta - self.route_delivery_planned_eta).total_seconds() / 60.0
            )
            if self.route_delivery_eta and self.route_delivery_planned_eta
            else 0,
        }

    def _route_delivery_ensure_token(self):
        for picking in self.filtered(lambda p: not p.route_delivery_token):
            picking.sudo().route_delivery_token = uuid.uuid4().hex
        return True

    def write(self, vals):
        res = super().write(vals)
        if vals.get("batch_id"):
            # El link existe desde que la entrega entra a una ruta.
            self._route_delivery_ensure_token()
        return res

    @api.model_create_multi
    def create(self, vals_list):
        pickings = super().create(vals_list)
        pickings.filtered("batch_id")._route_delivery_ensure_token()
        return pickings

    def _route_delivery_customer(self):
        """Contacto al que se le avisa: siempre el cliente, aunque la parada sea
        el depósito de un transportista."""
        self.ensure_one()
        return self.partner_id

    def _route_delivery_whatsapp_url(self):
        self.ensure_one()
        self._route_delivery_ensure_token()
        partner = self._route_delivery_customer()
        phone = "".join(ch for ch in (partner.phone or "") if ch.isdigit())
        company = self.company_id.name or ""
        text = _(
            "Hola %(name)s, te escribimos de %(company)s por tu pedido %(ref)s. "
            "Podés seguir la entrega acá: %(url)s"
        ) % {
            "name": partner.name or "",
            "company": company,
            "ref": self.origin or self.name,
            "url": self.route_delivery_tracking_url,
        }
        return "https://wa.me/%s?text=%s" % (phone, url_quote(text))

    def action_route_delivery_open_tracking(self):
        self.ensure_one()
        self._route_delivery_ensure_token()
        return {
            "type": "ir.actions.act_url",
            "url": self.route_delivery_tracking_url,
            "target": "new",
        }

    def action_route_delivery_whatsapp(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_url",
            "url": self._route_delivery_whatsapp_url(),
            "target": "new",
        }

    def action_route_delivery_reset(self):
        """Vuelve la parada a Pendiente (no deshace la validación del traslado)."""
        self.write(
            {
                "route_delivery_state": "pending",
                "route_delivery_done_at": False,
                "route_delivery_user_id": False,
                "route_delivery_fail_reason_id": False,
                "route_delivery_next_notified": False,
            }
        )
        self.batch_id.write({"route_delivery_finished_at": False})
        self.batch_id._route_delivery_update_etas()
        return True

    def _route_delivery_notify(self, kind):
        """Avisa al cliente del paso `kind` por los canales que estén activos.

        Los retiros no se avisan: los textos y plantillas hablan de una entrega.
        """
        self = self.filtered(lambda p: not p._route_optimizer_is_pickup())
        if not self:
            return
        self._route_delivery_notify_email(kind)
        reached = self._route_delivery_notify_whatsapp(kind)
        self._route_delivery_notify_sms(kind, reached)

    def _route_delivery_notify_email(self, kind):
        icp = self.env["ir.config_parameter"].sudo()
        if icp.get_param("route_optimizer_delivery.notify_email") not in ("True", "1", "true"):
            return
        template = self.env.ref(
            "route_optimizer_delivery.mail_template_route_delivery_%s" % kind,
            raise_if_not_found=False,
        )
        if not template:
            return
        for picking in self:
            if not picking._route_delivery_customer().email:
                continue
            picking._route_delivery_ensure_token()
            try:
                template.sudo().send_mail(picking.id, force_send=False)
            except Exception:  # un correo que falla no puede frenar al chofer
                _logger.exception("No se pudo enviar el aviso %s de %s", kind, picking.name)

    def _route_delivery_notify_whatsapp(self, kind):
        """Aviso por WhatsApp Business con la plantilla configurada para `kind`.

        Si falla (número inválido, plantilla sin aprobar, token vencido) queda una
        nota en el traslado y el reparto sigue.
        """
        icp = self.env["ir.config_parameter"].sudo()

        def param(key, default=""):
            return (icp.get_param("route_optimizer_delivery.%s" % key) or default).strip()

        reached = self.browse()
        if param("whatsapp_enabled") not in ("True", "1", "true"):
            return reached
        template = param("whatsapp_tpl_%s" % kind)
        if not template:
            return reached
        for picking in self:
            partner = picking._route_delivery_customer()
            number = whatsapp_client.normalize_phone(partner.phone, param("whatsapp_country_code"))
            if not number:
                continue
            picking._route_delivery_ensure_token()
            try:
                whatsapp_client.send_template(
                    param("whatsapp_phone_id"),
                    param("whatsapp_token"),
                    number,
                    template,
                    param("whatsapp_language", "es_AR"),
                    [partner.name or "", picking.origin or picking.name, picking.route_delivery_tracking_url],
                    version=param("whatsapp_api_version", "v26.0"),
                )
                reached |= picking
            except whatsapp_client.WhatsappError as error:
                _logger.warning("WhatsApp %s de %s: %s", kind, picking.name, error)
                picking.sudo().message_post(body=_("No se pudo avisar por WhatsApp: %s") % error)
        return reached

    def _route_delivery_notify_sms(self, kind, reached_by_whatsapp=None):
        """Aviso por SMS con el proveedor configurado.

        En modo «respaldo» solo se manda a quienes no les llegó el WhatsApp. Un envío
        que falla deja una nota en el traslado y no frena el reparto.
        """
        icp = self.env["ir.config_parameter"].sudo()

        def param(key, default=""):
            return (icp.get_param("route_optimizer_delivery.%s" % key) or default).strip()

        if param("sms_enabled") not in ("True", "1", "true"):
            return self.browse()
        template = param("sms_text_%s" % kind)
        if not template:
            return self.browse()
        targets = self
        if param("sms_mode", "fallback") == "fallback" and reached_by_whatsapp:
            targets = self - reached_by_whatsapp
        credentials = {key: param("sms_%s" % key) for key in ("account", "secret", "sender", "base_url")}
        sent = self.browse()
        for picking in targets:
            partner = picking._route_delivery_customer()
            number = whatsapp_client.normalize_phone(partner.phone, param("whatsapp_country_code"))
            if not number:
                continue
            picking._route_delivery_ensure_token()
            values = {
                "empresa": picking.company_id.name or "",
                "nombre": partner.name or "",
                "pedido": picking.origin or picking.name,
                "link": picking.route_delivery_tracking_url,
            }
            text = template
            for key, value in values.items():
                text = text.replace("{%s}" % key, value)
            try:
                sms_client.send(param("sms_provider", "twilio"), credentials, number, text)
                sent |= picking
            except sms_client.SmsError as error:
                _logger.warning("SMS %s de %s: %s", kind, picking.name, error)
                picking.sudo().message_post(body=_("No se pudo avisar por SMS: %s") % error)
        return sent

    # ------------------------------------------------------------------
    # Datos para la app y el tablero
    # ------------------------------------------------------------------

    def _route_delivery_kind(self):
        """delivery (se entrega), pickup (se retira) o return (se retira una devolución)."""
        self.ensure_one()
        if not self._route_optimizer_is_pickup():
            return "delivery"
        returned = ("return_id" in self._fields and self.return_id) or any(
            self.move_ids.mapped("origin_returned_move_id")
        )
        return "return" if returned else "pickup"

    def _route_delivery_packages(self):
        """Paquetes de Odoo del traslado, con lo que la app necesita de cada uno."""
        self.ensure_one()
        lines = self.move_line_ids
        if "result_package_id" not in lines._fields:
            return []
        packages = lines.mapped("result_package_id")
        result = []
        for package in packages.sorted("name"):
            weight = 0.0
            for field in ("shipping_weight", "weight"):
                if field in package._fields and package[field]:
                    weight = package[field]
                    break
            kind = package.package_type_id.name if "package_type_id" in package._fields else ""
            result.append(
                {
                    "key": "p%d" % package.id,
                    "code": package.name or "",
                    "label": package.name or "",
                    "type": kind or "",
                    "weight": round(weight, 2),
                }
            )
        return result

    def _route_delivery_load_items(self, mode):
        """Qué hay que subir al vehículo por este traslado, según el modo de control.

        `package`: cada paquete de Odoo es un bulto a escanear; un traslado sin
        paquetes se controla entero, por su remito. `picking`: siempre por remito.
        Los retiros no cargan nada al salir.
        """
        self.ensure_one()
        if mode not in ("picking", "package") or self._route_optimizer_is_pickup():
            return []
        items = self._route_delivery_packages() if mode == "package" else []
        if not items:
            items = [
                {
                    "key": "k%d" % self.id,
                    "code": self.name or "",
                    "alt_code": self.origin or "",
                    "label": self.name or "",
                    "type": "",
                    "weight": round(self.shipping_weight or 0.0, 2),
                }
            ]
        loaded = (self.route_delivery_load_data or {}).get("loaded") or {}
        for item in items:
            item["loaded"] = item["key"] in loaded
        return items

    def _route_delivery_moves(self):
        self.ensure_one()
        return self.move_ids.filtered(lambda m: m.state != "cancel")

    @staticmethod
    def _route_delivery_move_qty(move):
        """Cantidad a entregar de un movimiento: lo hecho si ya está validado."""
        if move.state == "done":
            return move.quantity
        return move.product_uom_qty

    def _route_delivery_uom_is_integer(self, uom):
        """True si la unidad se cuenta (unidades, docenas, packs): la app abre el
        teclado sin coma. Peso, volumen o longitud necesitan decimales."""
        unit = self.env.ref("uom.product_uom_unit", raise_if_not_found=False)
        if not unit or not uom:
            return False
        if "category_id" in uom._fields:  # Odoo 17/18: categorías de unidades
            return uom.category_id == unit.category_id
        root = uom
        while root.relative_uom_id:  # Odoo 19: cadena de unidades relativas
            root = root.relative_uom_id
        return root == unit

    def _route_delivery_stop_data(self, detail=False):
        self.ensure_one()
        partner = self._route_optimizer_delivery_partner()
        customer = self._route_delivery_customer()
        point = self.env["stock.picking.batch"]._route_optimizer_gmaps_point(partner) if partner else None
        lat = partner.partner_latitude if partner else 0.0
        lon = partner.partner_longitude if partner else 0.0
        data = {
            "id": self.id,
            "name": self.name,
            "origin": self.origin or "",
            "batch_id": self.batch_id.id,
            "sequence": self.batch_sequence or 0,
            "partner": (partner.display_name if partner else "") or self.name,
            "customer": customer.display_name or "",
            "address": self.route_optimizer_delivery_address or "",
            "phone": self.route_optimizer_partner_phone or customer.phone or "",
            "window": self.route_optimizer_time_window or "",
            "note": self.route_optimizer_note_text or "",
            "state": self.route_delivery_state or "pending",
            "picking_state": self.state,
            "eta": fields.Datetime.to_string(self.route_delivery_eta) if self.route_delivery_eta else False,
            "done_at": fields.Datetime.to_string(self.route_delivery_done_at)
            if self.route_delivery_done_at
            else False,
            "fail_reason": self.route_delivery_fail_reason_id.name or "",
            "lat": lat or False,
            "lon": lon or False,
            "maps_url": ("https://www.google.com/maps/dir/?api=1&destination=%s" % point) if point else False,
            "weight": self.shipping_weight or 0.0,
            "kind": self._route_delivery_kind(),
            "zone": self.geo_delivery_zone_id.name or "" if "geo_delivery_zone_id" in self._fields else "",
            "late_minutes": round(
                (self.route_delivery_eta - self.route_delivery_planned_eta).total_seconds() / 60.0
            )
            if self.route_delivery_eta and self.route_delivery_planned_eta
            else 0,
            "scheduled": fields.Datetime.to_string(self.scheduled_date) if self.scheduled_date else False,
        }
        if not detail:
            return data
        lines = []
        for move in self._route_delivery_moves():
            uom = _move_uom(move)
            lines.append(
                {
                    "id": move.id,
                    "product": move.product_id.display_name,
                    "barcode": move.product_id.barcode or "",
                    "qty": self._route_delivery_move_qty(move),
                    "uom": uom.name or "",
                    "integer": self._route_delivery_uom_is_integer(uom),
                }
            )
        packages = self._route_delivery_packages()
        load_mode = self.env["stock.picking.batch"]._route_delivery_load_config()["mode"]
        self._route_delivery_ensure_token()
        data.update(
            {
                "lines": lines,
                "packages": packages,
                "load_items": self._route_delivery_load_items(load_mode),
                "tracking_url": self.route_delivery_tracking_url,
                "whatsapp_url": self._route_delivery_whatsapp_url() if customer.phone else False,
                "receiver_name": self.route_delivery_receiver_name or "",
                "receiver_doc": self.route_delivery_receiver_doc or "",
                "delivered_packages": self.route_delivery_packages or 0,
                "cod_amount": self.route_delivery_cod_amount or 0.0,
                "driver_note": self.route_delivery_note or "",
                "partial_detail": self.route_delivery_partial_detail or "",
                "photo_count": len(self.route_delivery_photo_ids),
                "has_signature": bool(self.route_delivery_signature),
            }
        )
        return data

    # ------------------------------------------------------------------
    # Registro de la entrega
    # ------------------------------------------------------------------

    def _route_delivery_attach_photos(self, photos):
        self.ensure_one()
        photos = [p for p in (photos or []) if p][:MAX_PHOTOS]
        attachments = self.env["ir.attachment"]
        for index, photo in enumerate(photos, 1):
            attachments |= self.env["ir.attachment"].create(
                {
                    "name": _("Entrega %(ref)s - foto %(n)s.jpg") % {"ref": self.name, "n": index},
                    # `raw` (bytes) existe en todas las versiones; `datas` se fue en Odoo 20.
                    "raw": base64.b64decode(_b64_payload(photo)),
                    "res_model": "stock.picking",
                    "res_id": self.id,
                    "mimetype": "image/jpeg",
                }
            )
        if attachments:
            self.route_delivery_photo_ids = [(4, att.id) for att in attachments]
        return attachments

    def _route_delivery_already_sent(self, vals):
        """True si este mismo envío de la app ya se registró (reintento)."""
        self.ensure_one()
        client_id = (vals or {}).get("client_id")
        return bool(
            client_id
            and self.route_delivery_client_id == client_id
            and self.route_delivery_state != "pending"
        )

    def _route_delivery_client_vals(self, vals):
        """Identificador y hora del envío. Lo cargado sin señal se manda después: vale
        la hora en que el chofer lo hizo, si es creíble (ni futura ni de hace días)."""
        now = fields.Datetime.now()
        when = now
        try:
            sent = fields.Datetime.to_datetime(vals.get("client_time"))
        except (TypeError, ValueError):
            sent = None
        if sent and now - timedelta(hours=48) <= sent <= now + timedelta(minutes=5):
            when = min(sent, now)
        return {"route_delivery_done_at": when, "route_delivery_client_id": vals.get("client_id") or False}

    def _route_delivery_position_vals(self, vals):
        try:
            lat = float(vals.get("lat") or 0.0)
            lon = float(vals.get("lon") or 0.0)
        except (TypeError, ValueError):
            return {}
        if not lat and not lon:
            return {}
        return {"route_delivery_latitude": lat, "route_delivery_longitude": lon}

    def _route_delivery_register_delivered(self, vals):
        """Marca la parada como entregada (total o parcial) con su prueba de entrega."""
        self.ensure_one()
        if self.state == "cancel":
            raise UserError(_("El traslado %s está cancelado.") % self.name)
        icp = self.env["ir.config_parameter"].sudo()

        def flag(key):
            return icp.get_param("route_optimizer_delivery.%s" % key) in ("True", "1", "true")

        signature = _b64_payload(vals.get("signature"))
        photos = vals.get("photos") or []
        if flag("require_signature") and not (signature or self.route_delivery_signature):
            raise UserError(_("Falta la firma de quien recibe."))
        if flag("require_photo") and not (photos or self.route_delivery_photo_ids):
            raise UserError(_("Falta la foto de la entrega."))

        delivered = {}
        for line in vals.get("lines") or []:
            try:
                delivered[int(line["id"])] = max(0.0, float(line.get("qty") or 0.0))
            except (KeyError, TypeError, ValueError):
                raise UserError(_("Hay una cantidad que no es un número."))

        moves = self._route_delivery_moves()
        partial_lines = []
        total = 0.0
        for move in moves:
            planned = self._route_delivery_move_qty(move)
            qty = delivered.get(move.id, planned)
            if float_compare(qty, planned, precision_rounding=0.0001) > 0:
                raise UserError(
                    _("%(product)s: no se puede entregar más de %(qty)s.")
                    % {"product": move.product_id.display_name, "qty": planned}
                )
            delivered[move.id] = qty
            total += qty
            if float_compare(qty, planned, precision_rounding=0.0001) < 0:
                partial_lines.append(
                    "%s: %g / %g %s"
                    % (move.product_id.display_name, qty, planned, _move_uom(move).name or "")
                )
        pickup = self._route_optimizer_is_pickup()
        if moves and not total:
            raise UserError(
                _("No se retiró nada: usá «No retirado» y elegí el motivo.")
                if pickup
                else _("No se entregó nada: usá «No entregado» y elegí el motivo.")
            )

        try:
            packages = int(float(vals.get("packages") or 0))
            cod = float(vals.get("cod_amount") or 0.0)
        except (TypeError, ValueError):
            raise UserError(_("Bultos y cobro tienen que ser números."))

        values = {
            "route_delivery_state": "partial" if partial_lines else "delivered",
            "route_delivery_user_id": self.env.uid,
            "route_delivery_fail_reason_id": False,
            "route_delivery_receiver_name": (vals.get("receiver_name") or "").strip(),
            "route_delivery_receiver_doc": (vals.get("receiver_doc") or "").strip(),
            "route_delivery_packages": packages,
            "route_delivery_cod_amount": cod,
            "route_delivery_note": (vals.get("note") or "").strip(),
            "route_delivery_partial_detail": "\n".join(partial_lines),
        }
        if signature:
            values["route_delivery_signature"] = signature
        values.update(self._route_delivery_client_vals(vals))
        values.update(self._route_delivery_position_vals(vals))
        self.write(values)
        attachments = self._route_delivery_attach_photos(photos)

        if flag("validate_on_delivery") and self.state not in ("done", "cancel"):
            self._route_delivery_validate_picking(delivered)

        if pickup:
            title = _("Retiro parcial") if partial_lines else _("Retirado")
        else:
            title = _("Entrega parcial") if partial_lines else _("Entregado")
        body = Markup("<b>%s</b>") % title
        details = []
        if values["route_delivery_receiver_name"]:
            details.append(
                (_("Entregó: %s") if pickup else _("Recibió: %s")) % values["route_delivery_receiver_name"]
            )
        if values["route_delivery_receiver_doc"]:
            details.append(_("DNI: %s") % values["route_delivery_receiver_doc"])
        if packages:
            details.append(_("Bultos: %s") % packages)
        if cod:
            details.append(_("Cobrado: %s") % cod)
        details.extend(partial_lines)
        unscanned = [str(label) for label in (vals.get("unscanned") or [])][:50]
        if unscanned:
            details.append(_("Bultos sin escanear al entregar: %s") % ", ".join(unscanned))
        if values["route_delivery_note"]:
            details.append(values["route_delivery_note"])
        for detail in details:
            body += Markup("<br/>") + escape(detail)
        self.message_post(body=body, attachment_ids=attachments.ids)
        self._route_delivery_notify("delivered")
        return True

    def _route_delivery_register_failed(self, vals):
        self.ensure_one()
        reason = self.env["route.delivery.fail.reason"].browse(int(vals.get("reason_id") or 0)).exists()
        if not reason:
            raise UserError(_("Elegí el motivo por el que no se entregó."))
        note = (vals.get("note") or "").strip()
        if reason.require_note and not note:
            raise UserError(_("Contá qué pasó: este motivo pide una aclaración."))
        values = {
            "route_delivery_state": "failed",
            "route_delivery_user_id": self.env.uid,
            "route_delivery_fail_reason_id": reason.id,
            "route_delivery_note": note,
        }
        values.update(self._route_delivery_client_vals(vals))
        values.update(self._route_delivery_position_vals(vals))
        self.write(values)
        attachments = self._route_delivery_attach_photos(vals.get("photos"))
        body = Markup("<b>%s</b>: %s") % (
            _("No retirado") if self._route_optimizer_is_pickup() else _("No entregado"),
            reason.name,
        )
        if note:
            body += Markup("<br/>") + escape(note)
        self.message_post(body=body, attachment_ids=attachments.ids)
        self._route_delivery_notify("failed")
        return True

    def _route_delivery_validate_picking(self, delivered):
        """Valida el traslado con lo entregado; lo que falta queda en una entrega parcial.

        Si Odoo no deja validar (falta stock, lotes sin cargar...), la prueba de
        entrega queda guardada igual y el motivo se anota en el traslado: el chofer
        no puede quedar frenado por un tema de inventario.
        """
        self.ensure_one()
        batch, sequence = self.batch_id, self.batch_sequence
        try:
            with self.env.cr.savepoint():
                if self.state == "draft":
                    self.action_confirm()
                for move in self._route_delivery_moves():
                    move.quantity = delivered.get(move.id, move.product_uom_qty)
                    move.picked = True
                result = self.with_context(
                    skip_backorder=True, skip_sms=True, skip_immediate=True
                ).button_validate()
                if result is not True and self.state != "done":
                    raise UserError(_("Odoo pidió un paso manual para validar."))
                if batch and self.batch_id != batch:
                    # Odoo saca del lote al traslado que se valida suelto; en una ruta
                    # tiene que seguir figurando como parada ya visitada.
                    self.write({"batch_id": batch.id, "batch_sequence": sequence})
        except (UserError, ValidationError) as error:
            self.message_post(
                body=_("Entregado, pero el traslado no se pudo validar solo: %s") % error
            )
            return False
        return True
