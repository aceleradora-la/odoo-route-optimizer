# -*- coding: utf-8 -*-
from odoo import _, fields, models


class StockPickingBatch(models.Model):
    _inherit = "stock.picking.batch"

    route_optimizer_last_message = fields.Char(
        string="Last route optimization",
        copy=False,
        readonly=True,
    )

    def action_route_optimizer_wizard(self):
        self.ensure_one()
        return {
            "name": _("Optimize route"),
            "type": "ir.actions.act_window",
            "res_model": "route.optimizer.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_batch_id": self.id,
            },
        }
