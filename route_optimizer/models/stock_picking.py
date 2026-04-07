# -*- coding: utf-8 -*-
from odoo import _, models
from odoo.exceptions import UserError


class StockPicking(models.Model):
    _inherit = "stock.picking"

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
