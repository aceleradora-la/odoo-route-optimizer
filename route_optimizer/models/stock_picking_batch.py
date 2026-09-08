# -*- coding: utf-8 -*-
import base64
import io
from urllib.parse import quote as url_quote

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..services.route_service import _param_bool


def _build_qr_data_uri(text):
    """Generate a PNG QR code as a base64 data URI (works in wkhtmltopdf without HTTP)."""
    try:
        import qrcode  # available in Odoo's standard dependencies
        qr = qrcode.QRCode(box_size=4, border=2)
        qr.add_data(text)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()
        return f"data:image/png;base64,{b64}"
    except Exception:
        return False


class StockPickingBatch(models.Model):
    _inherit = "stock.picking.batch"

    route_optimizer_last_message = fields.Char(
        string="Última optimización de ruta",
        copy=False,
        readonly=True,
    )
    route_optimizer_visit_summary = fields.Text(
        string="Orden de visita (última corrida)",
        copy=False,
        readonly=True,
        help="Lista numerada de traslados tras la última optimización. Mismo orden que la columna "
        "'Orden de visita' / secuencia del lote en la pestaña Traslados.",
    )
    route_optimizer_gmaps_url = fields.Char(
        string="Ruta en Google Maps",
        compute="_compute_route_optimizer_gmaps_url",
        help="URL de indicaciones de Google Maps con todas las paradas en orden optimizado.",
    )
    route_optimizer_gmaps_qr_src = fields.Char(
        string="QR Google Maps",
        compute="_compute_route_optimizer_gmaps_url",
        help="URL del QR code para el PDF de la hoja de ruta.",
    )

    @api.depends(
        "picking_ids.batch_sequence",
        "picking_ids.state",
        "picking_ids.partner_id.partner_latitude",
        "picking_ids.partner_id.partner_longitude",
        "picking_ids.partner_id.street",
        "picking_ids.partner_id.city",
        "picking_ids.partner_id.country_id",
        "warehouse_id.partner_id.partner_latitude",
        "warehouse_id.partner_id.partner_longitude",
    )
    def _compute_route_optimizer_gmaps_url(self):
        for batch in self:
            pickings = batch._route_optimizer_pickings_visit_order().filtered(
                lambda p: p.state != "cancel"
            )
            if not pickings:
                batch.route_optimizer_gmaps_url = False
                batch.route_optimizer_gmaps_qr_src = False
                continue

            depot_partner = None
            if batch.warehouse_id and batch.warehouse_id.partner_id:
                depot_partner = batch.warehouse_id.partner_id
            elif batch.company_id:
                depot_partner = batch.company_id.partner_id

            points = []
            if depot_partner:
                pt = self._route_optimizer_gmaps_point(depot_partner)
                if pt:
                    points.append(pt)

            for pick in pickings:
                partner = pick._route_optimizer_delivery_partner()
                if partner:
                    pt = self._route_optimizer_gmaps_point(partner)
                    if pt:
                        points.append(pt)

            if len(points) >= 2:
                gmaps_url = "https://www.google.com/maps/dir/" + "/".join(points)
                batch.route_optimizer_gmaps_url = gmaps_url
                batch.route_optimizer_gmaps_qr_src = _build_qr_data_uri(gmaps_url)
            else:
                batch.route_optimizer_gmaps_url = False
                batch.route_optimizer_gmaps_qr_src = False

    @staticmethod
    def _route_optimizer_gmaps_point(partner):
        """lat,lon if geocoded, else URL-encoded address string."""
        try:
            lat = float(partner.partner_latitude or 0)
            lon = float(partner.partner_longitude or 0)
            if lat != 0.0 or lon != 0.0:
                return f"{lat},{lon}"
        except (TypeError, ValueError):
            pass
        parts = [
            x for x in (
                partner.street,
                partner.city,
                partner.country_id.name if partner.country_id else "",
            )
            if x
        ]
        if parts:
            return url_quote(", ".join(parts))
        return None

    # Se repiten los depends del core (idénticos en Odoo 17/18/19): al sobrescribir
    # un compute, Odoo toma las dependencias del método más derivado, así que sin
    # esto el campo dejaría de recalcularse al cambiar de tipo de operación o estado.
    @api.depends("company_id", "picking_type_id", "state")
    def _compute_allowed_picking_ids(self):
        """Permite sumar traslados ya validados cuando el ajuste está activo.

        Odoo limita los traslados de un lote a los estados pendientes
        (waiting/confirmed/assigned, más draft si el lote está en borrador). Ese
        cálculo alimenta dos cosas a la vez: el dominio del campo picking_ids y
        el _sanity_check() que corre al asignar el lote. Ampliándolo acá se
        destraban las dos, sin tocar el core.

        Sirve para operaciones donde el traslado se valida al salir del depósito
        y el reparto físico ocurre después: la hoja de ruta se arma con traslados
        que ya están en Hecho.
        """
        super()._compute_allowed_picking_ids()
        icp = self.env["ir.config_parameter"].sudo()
        if not _param_bool(icp.get_param("route_optimizer.allow_done_in_batch", "False")):
            return
        for batch in self:
            domain = [
                ("company_id", "=", batch.company_id.id),
                ("state", "=", "done"),
            ]
            if batch.picking_type_id:
                domain.append(("picking_type_id", "=", batch.picking_type_id.id))
            done_pickings = self.env["stock.picking"].search(domain)
            batch.allowed_picking_ids |= done_pickings

    def action_route_optimizer_wizard(self):
        self.ensure_one()
        default_fleet_vehicle_id = None
        if "vehicle_id" in self._fields and self.vehicle_id:
            default_fleet_vehicle_id = self.vehicle_id.id
        return {
            "name": _("Optimizar ruta"),
            "type": "ir.actions.act_window",
            "res_model": "route.optimizer.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_batch_id": self.id,
                "default_fleet_vehicle_id": default_fleet_vehicle_id,
            },
        }

    def action_open_gmaps_route(self):
        self.ensure_one()
        if not self.route_optimizer_gmaps_url:
            raise UserError(_("No hay una ruta optimizada disponible. Ejecutá 'Optimizar ruta' primero."))
        return {
            "type": "ir.actions.act_url",
            "url": self.route_optimizer_gmaps_url,
            "target": "new",
        }

    def action_share_whatsapp(self):
        self.ensure_one()
        text = self._route_optimizer_whatsapp_text()
        return {
            "type": "ir.actions.act_url",
            "url": "https://wa.me/?text=" + url_quote(text),
            "target": "new",
        }

    def _route_optimizer_whatsapp_text(self):
        """Pre-formatted WhatsApp message with the route summary."""
        self.ensure_one()
        pickings = self._route_optimizer_pickings_visit_order().filtered(
            lambda p: p.state != "cancel"
        )
        lines = [f"*Hoja de Ruta — {self.name}*"]
        if self.scheduled_date:
            lines.append(f"Fecha: {fields.Datetime.to_string(self.scheduled_date)}")
        lines.append(f"Paradas: {len(pickings)}\n")

        for i, pick in enumerate(pickings, 1):
            p = pick._route_optimizer_delivery_partner()
            name = p.display_name if p else pick.name
            address = pick.route_optimizer_delivery_address or ""
            phone = pick.route_optimizer_partner_phone if p else ""
            line = f"{i}. {name}"
            if address:
                line += f"\n   {address}"
            if phone:
                line += f"\n   Tel: {phone}"
            lines.append(line)

        if self.route_optimizer_gmaps_url:
            lines.append(f"\nRuta Google Maps:\n{self.route_optimizer_gmaps_url}")

        return "\n".join(lines)

    def _route_optimizer_pickings_visit_order(self):
        """Pickings sorted for UI/report: first unload = lowest batch_sequence."""
        self.ensure_one()
        return self.picking_ids.sorted(lambda p: (p.batch_sequence or 0, p.id))

    def action_print_delivery_route(self):
        self.ensure_one()
        return self.env.ref(
            "route_optimizer.action_report_batch_delivery_route"
        ).report_action(self)
