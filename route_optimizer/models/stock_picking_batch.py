# -*- coding: utf-8 -*-
from odoo import _, fields, models


class StockPickingBatch(models.Model):
    _inherit = "stock.picking.batch"

    route_optimizer_last_message = fields.Char(
        string="Last route optimization",
        copy=False,
        readonly=True,
    )
    route_optimizer_visit_summary = fields.Text(
        string="Visit order (last run)",
        copy=False,
        readonly=True,
        help="Numbered list of transfers after the last optimization. Same order as column "
        "“Visit order” / batch sequence on the Transfers tab.",
    )

    def action_route_optimizer_wizard(self):
        self.ensure_one()
        default_fleet_vehicle_id = None
        # Optional integration: if stock_picking_batch has a fleet vehicle field (commonly `vehicle_id`),
        # pass it as default for the optimizer wizard. The wizard field itself is provided by
        # `route_optimizer_fleet`, so this stays safe when the bridge isn't installed.
        if "vehicle_id" in self._fields and self.vehicle_id:
            default_fleet_vehicle_id = self.vehicle_id.id
        return {
            "name": _("Optimize route"),
            "type": "ir.actions.act_window",
            "res_model": "route.optimizer.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_batch_id": self.id,
                "default_fleet_vehicle_id": default_fleet_vehicle_id,
            },
        }

    def _route_optimizer_pickings_visit_order(self):
        """Pickings sorted for UI/report: first unload = lowest batch_sequence."""
        self.ensure_one()
        return self.picking_ids.sorted(lambda p: (p.batch_sequence or 0, p.id))

    def action_print_delivery_route(self):
        self.ensure_one()
        return self.env.ref(
            "route_optimizer.action_report_batch_delivery_route"
        ).report_action(self)
