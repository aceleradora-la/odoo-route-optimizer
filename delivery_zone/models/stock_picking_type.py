# -*- coding: utf-8 -*-
from odoo import fields, models


class StockPickingType(models.Model):
    _inherit = "stock.picking.type"

    batch_group_by_geo_delivery_zone = fields.Boolean(
        string="Zona de entrega",
        help="Agrupa automáticamente en un mismo lote los traslados de la misma "
        "zona de entrega.",
    )
