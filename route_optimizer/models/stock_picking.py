# -*- coding: utf-8 -*-
import html as html_lib
import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _html_to_text(value):
    """Texto plano de un campo html.

    No se usa html2plaintext de odoo.tools a propósito: un import que cambie de
    lugar entre versiones tumba la carga del módulo entero, y esto son cuatro
    líneas sin dependencias.
    """
    if not value:
        return ""
    text = re.sub(r"<br\s*/?>|</p>|</div>|</li>", " ", value, flags=re.IGNORECASE)
    text = _HTML_TAG_RE.sub("", text)
    text = html_lib.unescape(text).replace("\xa0", " ")
    return " ".join(text.split())


def _safe_float(val):
    try:
        return float(val or 0)
    except (TypeError, ValueError):
        return 0.0


def _move_packaging(move):
    """Cantidad y UdM de embalaje de un movimiento, según la versión de Odoo.

    Odoo 19 renombró los campos de stock.move:
        17.0 / 18.0 -> product_packaging_qty / product_packaging_id
        19.0        -> packaging_uom_qty     / packaging_uom_id
    Devuelve (cantidad, registro_uom) o (0.0, None) si no aplica.
    """
    for qty_field, uom_field in (
        ("packaging_uom_qty", "packaging_uom_id"),
        ("product_packaging_qty", "product_packaging_id"),
    ):
        if qty_field in move._fields and uom_field in move._fields:
            qty = _safe_float(getattr(move, qty_field, None))
            uom = getattr(move, uom_field, None)
            if qty > 0 and uom:
                return qty, uom
    return 0.0, None


class StockPicking(models.Model):
    _inherit = "stock.picking"

    route_optimizer_delivery_address = fields.Char(
        string="Dirección de entrega",
        compute="_compute_route_optimizer_delivery_address",
    )
    route_optimizer_products_summary = fields.Char(
        string="Productos",
        compute="_compute_route_optimizer_products_summary",
    )
    route_optimizer_time_window = fields.Char(
        string="Ventana horaria",
        compute="_compute_route_optimizer_time_window",
    )
    route_optimizer_note_text = fields.Char(
        string="Notas internas (texto)",
        compute="_compute_route_optimizer_note_text",
        help="Notas internas del traslado en texto plano, vacío si no hay nada "
        "escrito. Se usa en la hoja de ruta: el campo note es html y un valor "
        "'vacío' suele ser <p><br></p>, que en una condición da verdadero e "
        "imprimiría una fila en blanco.",
    )
    route_optimizer_partner_phone = fields.Char(
        string="Teléfono de contacto",
        compute="_compute_route_optimizer_partner_phone",
    )
    route_optimizer_carrier_name = fields.Char(
        string="Transporte",
        compute="_compute_route_optimizer_carrier_name",
        help="Nombre del transportista cuando la parada es su depósito y no el "
        "domicilio del cliente. Vacío en las entregas directas.",
    )

    @api.depends(
        "partner_id",
        "partner_id.street",
        "partner_id.street2",
        "partner_id.zip",
        "partner_id.city",
        "partner_id.state_id",
        "partner_id.country_id",
    )
    def _compute_route_optimizer_delivery_address(self):
        for picking in self:
            picking.route_optimizer_delivery_address = (
                picking._route_optimizer_format_delivery_address()
            )

    def _route_optimizer_format_delivery_address(self):
        """Dirección de la parada en una línea, para listas y PDF.

        Usa el partner efectivo: si el pedido va por transporte, la dirección
        que se imprime es la del transportista, que es adonde va el camión.
        """
        self.ensure_one()
        p = self._route_optimizer_delivery_partner()
        if not p:
            return ""
        parts = []
        if p.street:
            parts.append(p.street.strip())
        if p.street2:
            parts.append(p.street2.strip())
        city_bits = [x for x in (p.zip, p.city) if x]
        if city_bits:
            parts.append(" ".join(city_bits))
        if p.state_id:
            parts.append(p.state_id.name)
        if p.country_id:
            parts.append(p.country_id.name)
        return ", ".join(parts)

    @api.depends(
        "move_ids.product_id",
        "move_ids.product_uom_qty",
        "move_ids.product_uom",
        "move_ids.state",
    )
    def _compute_route_optimizer_products_summary(self):
        # Etiqueta fija en castellano, como el resto de los títulos del reporte.
        # Antes salía de fields_get, que devuelve el nombre del campo en el idioma
        # del render, y en el PDF aparecía "Number of Packages".
        #
        # `number_of_packages` lo aporta el módulo `delivery`, por eso el acceso
        # sigue siendo defensivo. No se puede declarar en @api.depends porque el
        # campo puede no existir en la instalación.
        picking_fields = self.env["stock.picking"]._fields
        pkg_label = "Bultos" if "number_of_packages" in picking_fields else ""

        for pick in self:
            moves = pick.move_ids.filtered(lambda m: m.state != "cancel")

            # Decidir si usar packaging o UdM estándar
            use_packaging = any(_move_packaging(m)[0] > 0 for m in moves)

            totals = {}  # {uom_id: (total_qty, uom_name)}
            for move in moves:
                if use_packaging:
                    pkg_qty, pkg_uom = _move_packaging(move)
                    if pkg_qty > 0 and pkg_uom:
                        key = pkg_uom.id
                        name = pkg_uom.name if hasattr(pkg_uom, "name") else str(pkg_uom)
                        totals[key] = (totals.get(key, (0.0, name))[0] + pkg_qty, name)
                else:
                    qty = _safe_float(move.product_uom_qty)
                    uom = move.product_uom
                    if qty > 0 and uom:
                        totals[uom.id] = (totals.get(uom.id, (0.0, uom.name))[0] + qty, uom.name)

            parts = []
            for total_qty, uom_name in totals.values():
                qty_str = (
                    str(int(total_qty))
                    if total_qty == int(total_qty)
                    else f"{total_qty:.2f}".rstrip("0").rstrip(".")
                )
                parts.append(f"{qty_str} {uom_name}")

            if pkg_label:
                try:
                    n_packages = int(pick.number_of_packages or 0)
                except (TypeError, ValueError):
                    n_packages = 0
                if n_packages > 0:
                    parts.append(f"{pkg_label}: {n_packages}")

            pick.route_optimizer_products_summary = " | ".join(parts)

    @api.depends("note")
    def _compute_route_optimizer_note_text(self):
        """Pasa las notas internas de html a texto plano.

        Se descartan las etiquetas y los espacios no separables: así un campo
        que quedó con <p><br></p> —lo que deja el editor al borrar el texto—
        se resuelve como vacío y la hoja de ruta no imprime una fila en blanco.
        """
        for pick in self:
            pick.route_optimizer_note_text = _html_to_text(pick.note)

    @api.depends("partner_id")
    def _compute_route_optimizer_time_window(self):
        has_pref = "delivery_time_preference" in self.env["res.partner"]._fields
        for pick in self:
            # La ventana que importa es la de quien recibe: si va por transporte,
            # la del transportista, no la del cliente final.
            partner = pick._route_optimizer_delivery_partner()
            if not has_pref or not partner:
                pick.route_optimizer_time_window = ""
                continue
            pref = getattr(partner, "delivery_time_preference", "anytime")
            if pref == "workdays":
                pick.route_optimizer_time_window = _("Días hábiles")
            elif pref == "time_windows":
                windows = []
                for w in getattr(partner, "delivery_time_window_ids", []):
                    st = getattr(w, "time_window_start", None)
                    en = getattr(w, "time_window_end", None)
                    if st is not None and en is not None:
                        try:
                            s = int(round(float(st) * 3600))
                            e = int(round(float(en) * 3600))
                            windows.append(
                                f"{s // 3600:02d}:{(s % 3600) // 60:02d}"
                                f"–{e // 3600:02d}:{(e % 3600) // 60:02d}"
                            )
                        except (TypeError, ValueError):
                            pass
                pick.route_optimizer_time_window = ", ".join(windows)
            else:
                pick.route_optimizer_time_window = ""

    # Sin @api.depends sobre carrier_id: ese campo lo aporta stock_delivery y
    # declararlo tumbaría la carga del módulo donde no esté instalado. El campo
    # no se almacena, así que se recalcula en cada lectura.
    def _compute_route_optimizer_carrier_name(self):
        for pick in self:
            carrier_partner = pick._route_optimizer_carrier_partner()
            pick.route_optimizer_carrier_name = (
                carrier_partner.display_name if carrier_partner else ""
            )

    @api.depends("partner_id")
    def _compute_route_optimizer_partner_phone(self):
        for pick in self:
            p = pick.partner_id
            if not p:
                pick.route_optimizer_partner_phone = ""
                continue
            phone = getattr(p, "phone", "") or ""
            mobile = getattr(p, "mobile", "") or ""
            pick.route_optimizer_partner_phone = phone or mobile

    # ------------------------------------------------------------------
    # Secuencia de visita al entrar a un lote
    # ------------------------------------------------------------------

    def _route_optimizer_assign_batch_sequence(self):
        """Da una secuencia propia a cada traslado que entra a un lote sin una.

        Odoo deja batch_sequence en 0 al agregar traslados, y su drag & drop
        solo numera las filas que movés: el resto queda empatado en 0. Con
        empates, el orden de visita lo termina definiendo el _order de
        stock.picking (priority, scheduled_date, id), que puede cambiar si se
        edita una fecha o una prioridad.

        Asignando valores distintos desde el principio, arrastrar filas funciona
        como se espera y el orden queda estable y coincide con la impresión.
        """
        pending = self.filtered(lambda p: p.batch_id and not p.batch_sequence)
        for batch in pending.batch_id:
            sequences = batch.picking_ids.mapped("batch_sequence")
            next_seq = max(sequences) if sequences else 0
            for picking in pending.filtered(lambda p: p.batch_id == batch):
                next_seq += 10
                picking.batch_sequence = next_seq

    @api.model_create_multi
    def create(self, vals_list):
        pickings = super().create(vals_list)
        pickings._route_optimizer_assign_batch_sequence()
        return pickings

    def write(self, vals):
        res = super().write(vals)
        # Si el propio write trae batch_sequence (ej. el drag & drop de Odoo),
        # se respeta ese valor y no se toca nada.
        if vals.get("batch_id") and "batch_sequence" not in vals:
            self._route_optimizer_assign_batch_sequence()
        return res

    def _route_optimizer_carrier_partner(self):
        """Contacto del transporte, si el pedido va por un método de entrega con dirección.

        En Odoo el campo carrier_id lo aporta el módulo stock_delivery, y la
        dirección del transportista la agrega delivery_carrier_partner (OCA) como
        delivery.carrier.partner_id. Ambos son opcionales, por eso el acceso es
        defensivo: sin esos módulos, el método devuelve vacío y todo sigue
        funcionando contra el cliente.
        """
        self.ensure_one()
        empty = self.env["res.partner"].browse()
        if "carrier_id" not in self._fields or not self.carrier_id:
            return empty
        partner = getattr(self.carrier_id, "partner_id", empty)
        if not partner:
            return empty
        # Sin dirección no sirve como parada: se sigue usando la del cliente.
        if not (partner.street or partner.street2 or partner.city):
            return empty
        return partner

    def _route_optimizer_delivery_partner(self):
        """Contacto que define la parada de la ruta.

        Si el pedido tiene método de entrega con dirección, el camión va al
        depósito del transportista, no al domicilio del cliente. Mismo criterio
        que ya usa el remito al imprimir el transporte.
        """
        self.ensure_one()
        return self._route_optimizer_carrier_partner() or self.partner_id

    def action_route_optimizer_from_picking(self):
        """Secondary entry: open the optimizer wizard for the batch of this transfer."""
        self.ensure_one()
        if not self.batch_id:
            raise UserError(_("Este traslado no forma parte de un lote."))
        return self.batch_id.action_route_optimizer_wizard()
