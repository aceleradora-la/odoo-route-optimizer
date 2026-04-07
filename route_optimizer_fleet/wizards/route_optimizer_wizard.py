# -*- coding: utf-8 -*-
from odoo import fields, models


class RouteOptimizerWizard(models.TransientModel):
    _inherit = "route.optimizer.wizard"

    fleet_vehicle_id = fields.Many2one(
        "fleet.vehicle",
        string="Fleet vehicle",
        help="Optional link for traceability; set capacity below for the solver.",
    )
