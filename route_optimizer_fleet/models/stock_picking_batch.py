# -*- coding: utf-8 -*-
from odoo import models


class StockPickingBatch(models.Model):
    _inherit = "stock.picking.batch"

    def _route_optimizer_set_vehicle(self, vehicle_ref):
        """Asigna al lote el vehículo de Flota que le tocó al optimizar.

        El campo vehicle_id del lote lo aporta otro módulo (despacho con flota);
        si no está instalado, el dato queda anotado en el historial del lote.
        """
        vehicle = self.env["fleet.vehicle"].browse(int(vehicle_ref)).exists()
        if not vehicle:
            return False
        for batch in self:
            if "vehicle_id" in batch._fields:
                batch.vehicle_id = vehicle.id
            else:
                batch.message_post(body="Vehículo asignado al optimizar: %s" % vehicle.display_name)
        return True
