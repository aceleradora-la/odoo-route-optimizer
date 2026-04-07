# -*- coding: utf-8 -*-
from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    route_optimizer_osrm_url = fields.Char(
        string="OSRM base URL",
        config_parameter="route_optimizer.osrm_url",
        help="Base URL without trailing slash, e.g. https://router.project-osrm.org",
    )
    route_optimizer_osrm_profile = fields.Char(
        string="OSRM profile",
        config_parameter="route_optimizer.osrm_profile",
        default="driving",
    )
    route_optimizer_ortools_url = fields.Char(
        string="OR-Tools service URL",
        config_parameter="route_optimizer.ortools_url",
        help="HTTP endpoint that accepts the VRP JSON payload and returns ordered routes.",
    )
    route_optimizer_timeout = fields.Integer(
        string="HTTP timeout (seconds)",
        config_parameter="route_optimizer.timeout",
        default=60,
    )
