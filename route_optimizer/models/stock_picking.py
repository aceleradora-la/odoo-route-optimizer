# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class StockPicking(models.Model):
    _inherit = "stock.picking"

    route_optimizer_delivery_address = fields.Char(
        string="Delivery address",
        compute="_compute_route_optimizer_delivery_address",
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
            picking.route_optimizer_delivery_address = picking._route_optimizer_format_delivery_address()

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

    def _route_optimizer_delivery_partner(self):
        """Partner used for stop coordinates (outgoing customer deliveries)."""
        self.ensure_one()
        p = self.partner_id
        return p

    def action_route_optimizer_from_picking(self):
        """Secondary entry: open the optimizer wizard for the batch of this transfer."""
        self.ensure_one()
        if not self.batch_id:
            raise UserError(_("This transfer is not part of a batch."))
        return self.batch_id.action_route_optimizer_wizard()
