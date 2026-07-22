# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


def _safe_float(val):
    try:
        return float(val or 0)
    except (TypeError, ValueError):
        return 0.0


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
    route_optimizer_partner_phone = fields.Char(
        string="Teléfono de contacto",
        compute="_compute_route_optimizer_partner_phone",
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
        """Single line for lists / PDF (street, city, etc.)."""
        self.ensure_one()
        p = self.partner_id
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
        for pick in self:
            moves = pick.move_ids.filtered(lambda m: m.state != "cancel")

            # Decidir si usar packaging o UdM estándar
            use_packaging = any(
                _safe_float(getattr(m, "packaging_uom_qty", None)) > 0
                and getattr(m, "packaging_uom_id", None)
                for m in moves
            )

            totals = {}  # {uom_id: (total_qty, uom_name)}
            for move in moves:
                if use_packaging:
                    pkg_qty = _safe_float(getattr(move, "packaging_uom_qty", None))
                    pkg_uom = getattr(move, "packaging_uom_id", None)
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

            pick.route_optimizer_products_summary = " | ".join(parts)

    @api.depends("partner_id")
    def _compute_route_optimizer_time_window(self):
        has_pref = "delivery_time_preference" in self.env["res.partner"]._fields
        for pick in self:
            if not has_pref or not pick.partner_id:
                pick.route_optimizer_time_window = ""
                continue
            pref = getattr(pick.partner_id, "delivery_time_preference", "anytime")
            if pref == "workdays":
                pick.route_optimizer_time_window = _("Días hábiles")
            elif pref == "time_windows":
                windows = []
                for w in getattr(pick.partner_id, "delivery_time_window_ids", []):
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

    def _route_optimizer_delivery_partner(self):
        """Partner used for stop coordinates (outgoing customer deliveries)."""
        self.ensure_one()
        return self.partner_id

    def action_route_optimizer_from_picking(self):
        """Secondary entry: open the optimizer wizard for the batch of this transfer."""
        self.ensure_one()
        if not self.batch_id:
            raise UserError(_("Este traslado no forma parte de un lote."))
        return self.batch_id.action_route_optimizer_wizard()
