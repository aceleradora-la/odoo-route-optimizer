# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class StockPicking(models.Model):
    _inherit = "stock.picking"

    route_optimizer_delivery_address = fields.Char(
        string="Delivery address",
        compute="_compute_route_optimizer_delivery_address",
    )
    route_optimizer_products_summary = fields.Char(
        string="Products",
        compute="_compute_route_optimizer_products_summary",
    )
    route_optimizer_time_window = fields.Char(
        string="Time window",
        compute="_compute_route_optimizer_time_window",
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
            parts = []
            for move in moves:
                qty = move.product_uom_qty
                name = move.product_id.display_name if move.product_id else ""
                uom = move.product_uom.name if move.product_uom else ""
                qty_str = (
                    str(int(qty))
                    if qty == int(qty)
                    else f"{qty:.2f}".rstrip("0").rstrip(".")
                )
                parts.append(f"{qty_str} {uom} {name}".strip())
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
                pick.route_optimizer_time_window = _("Weekdays")
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

    def _route_optimizer_delivery_partner(self):
        """Partner used for stop coordinates (outgoing customer deliveries)."""
        self.ensure_one()
        return self.partner_id

    def action_route_optimizer_from_picking(self):
        """Secondary entry: open the optimizer wizard for the batch of this transfer."""
        self.ensure_one()
        if not self.batch_id:
            raise UserError(_("This transfer is not part of a batch."))
        return self.batch_id.action_route_optimizer_wizard()
