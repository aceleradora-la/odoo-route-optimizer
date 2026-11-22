# -*- coding: utf-8 -*-
from odoo import models


class RouteOptimizerWizard(models.TransientModel):
    _inherit = "route.optimizer.wizard"

    def action_optimize(self):
        """Tras optimizar, recalcula la hora estimada de cada parada.

        Con varios vehículos el optimizador reparte el lote en lotes nuevos: se
        recalculan todos los que comparten tipo de operación y fueron tocados.
        """
        before = self.env["stock.picking.batch"].search([], order="id desc", limit=1).id or 0
        res = super().action_optimize()
        batches = self.batch_id | self.env["stock.picking.batch"].search([("id", ">", before)])
        batches._route_delivery_update_etas()
        return res
