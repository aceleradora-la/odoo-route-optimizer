# -*- coding: utf-8 -*-
from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    @staticmethod
    def _param_bool(value, default=False):
        """Parse ir.config_parameter values as boolean (robust across 'False', 'false', '0', etc.)."""
        if value is None:
            return bool(default)
        if isinstance(value, bool):
            return value
        s = str(value).strip().lower()
        if s in ("1", "true", "t", "yes", "y", "on"):
            return True
        if s in ("0", "false", "f", "no", "n", "off", ""):
            return False
        return bool(default)

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
    route_optimizer_ortools_api_key = fields.Char(
        string="OR-Tools API key",
        config_parameter="route_optimizer.ortools_api_key",
        help="Optional API key sent as X-API-KEY header to the OR-Tools service.",
    )
    route_optimizer_ortools_simple_api = fields.Boolean(
        string="Simple OR-Tools API",
        config_parameter="route_optimizer.ortools_simple_api",
        default=True,
        help="Recommended for typical /optimize endpoints: JSON with locations + distance_matrix "
        "and response optimized_route. Uncheck only if your service uses the extended contract "
        "(version, matrix, picking_ids, …). Single-vehicle only.",
    )
    route_optimizer_timeout = fields.Integer(
        string="HTTP timeout (seconds)",
        config_parameter="route_optimizer.timeout",
        default=60,
    )
    route_optimizer_max_stops_per_vehicle = fields.Integer(
        string="Max stops per vehicle",
        config_parameter="route_optimizer.max_stops_per_vehicle",
        help="Optional hard limit to force splitting stops across vehicles.",
    )
    route_optimizer_max_route_duration_minutes = fields.Integer(
        string="Max route duration (minutes)",
        config_parameter="route_optimizer.max_route_duration_minutes",
        help="Optional hard limit per vehicle route duration (requires duration optimization).",
    )

    @api.model
    def get_values(self):
        res = super().get_values()
        icp = self.env["ir.config_parameter"].sudo()
        res["route_optimizer_ortools_simple_api"] = self._param_bool(
            icp.get_param("route_optimizer.ortools_simple_api", "True"),
            default=True,
        )
        # Ensure value shows even if config_parameter isn't picked up by the UI cache yet.
        res["route_optimizer_ortools_api_key"] = icp.get_param("route_optimizer.ortools_api_key", "") or ""
        return res

    def set_values(self):
        super().set_values()
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param(
            "route_optimizer.ortools_simple_api",
            "True" if self.route_optimizer_ortools_simple_api else "False",
        )
        icp.set_param(
            "route_optimizer.ortools_api_key",
            (self.route_optimizer_ortools_api_key or "").strip(),
        )
