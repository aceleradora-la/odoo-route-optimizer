# -*- coding: utf-8 -*-
from odoo import api, fields, models


class StockPickingType(models.Model):
    _inherit = "stock.picking.type"

    batch_group_by_geo_delivery_zone = fields.Boolean(
        string="Zona de entrega",
        help="Agrupa automáticamente en un mismo lote los traslados de la misma "
        "zona de entrega.",
    )

    @api.model
    def _get_batch_group_by_keys(self):
        """Registra la zona como criterio válido de agrupación.

        stock_picking_batch usa esta lista tanto para la restricción que exige al
        menos un criterio cuando los lotes automáticos están activos, como para
        decidir si el tipo de operación está agrupado. Sin esto, tildar solo
        «Zona de entrega» daría error de validación.
        """
        return super()._get_batch_group_by_keys() + ["batch_group_by_geo_delivery_zone"]
