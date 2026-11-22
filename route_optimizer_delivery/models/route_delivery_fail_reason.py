# -*- coding: utf-8 -*-
from odoo import fields, models


class RouteDeliveryFailReason(models.Model):
    _name = "route.delivery.fail.reason"
    _description = "Motivo de entrega fallida"
    _order = "sequence, id"

    name = fields.Char(string="Motivo", required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    require_note = fields.Boolean(
        string="Pide aclaración",
        help="El chofer tiene que escribir un comentario al elegir este motivo.",
    )
