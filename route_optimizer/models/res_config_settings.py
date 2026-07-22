# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


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
        string="URL base de OSRM",
        config_parameter="route_optimizer.osrm_url",
        help="URL base sin barra final, ej. https://router.project-osrm.org",
    )
    route_optimizer_osrm_profile = fields.Char(
        string="Perfil OSRM",
        config_parameter="route_optimizer.osrm_profile",
        default="driving",
    )
    route_optimizer_ortools_url = fields.Char(
        string="URL del servicio OR-Tools",
        config_parameter="route_optimizer.ortools_url",
        help="Endpoint HTTP que acepta el payload JSON del VRP y devuelve rutas ordenadas.",
    )
    route_optimizer_ortools_api_key = fields.Char(
        string="API key de OR-Tools",
        config_parameter="route_optimizer.ortools_api_key",
        help="API key opcional enviada como header X-API-KEY al servicio OR-Tools.",
    )
    route_optimizer_ortools_simple_api = fields.Boolean(
        string="API simple de OR-Tools",
        config_parameter="route_optimizer.ortools_simple_api",
        default=True,
        help="Recomendado para endpoints /optimize típicos: JSON con locations + distance_matrix "
        "y respuesta optimized_route. Desmarcar solo si el servicio usa el contrato extendido "
        "(version, matrix, picking_ids, …). Solo un vehículo.",
    )
    route_optimizer_timeout = fields.Integer(
        string="Timeout HTTP (segundos)",
        config_parameter="route_optimizer.timeout",
        default=60,
    )
    route_optimizer_max_stops_per_vehicle = fields.Integer(
        string="Máx. paradas por vehículo",
        config_parameter="route_optimizer.max_stops_per_vehicle",
        help="Límite duro opcional para forzar la división de paradas entre vehículos.",
    )
    route_optimizer_max_route_duration_minutes = fields.Integer(
        string="Duración máx. de ruta (minutos)",
        config_parameter="route_optimizer.max_route_duration_minutes",
        help="Límite duro opcional de duración por ruta de vehículo (requiere optimizar por duración).",
    )
    route_optimizer_use_delivery_windows = fields.Boolean(
        string="Respetar ventanas horarias del cliente",
        config_parameter="route_optimizer.use_delivery_windows",
        default=True,
        help="Con el módulo OCA «Stock Partner Delivery Window» instalado, envía las ventanas "
        "horarias al servicio /vrp de OR-Tools (requiere «Optimizar por duración»).",
    )
    route_optimizer_route_start_hour = fields.Float(
        string="Hora de salida de ruta",
        config_parameter="route_optimizer.route_start_hour",
        default=8.0,
        help="Hora local en que los vehículos salen del depósito (0–24). Se usa con las ventanas horarias.",
    )
    route_optimizer_service_time_seconds = fields.Integer(
        string="Tiempo de servicio por parada (segundos)",
        config_parameter="route_optimizer.service_time_seconds",
        default=600,
        help="Tiempo estimado en cada parada, sumado a la dimensión de tiempo de viaje.",
    )
    route_optimizer_block_outside_windows = fields.Boolean(
        string="Bloquear optimización fuera de ventana",
        config_parameter="route_optimizer.block_outside_windows",
        default=False,
        help="Si está activo, rechaza optimizar cuando la fecha/hora planificada de un traslado "
        "está fuera de la ventana horaria del cliente (reglas OCA).",
    )

    @api.constrains("route_optimizer_route_start_hour")
    def _check_route_start_hour(self):
        for rec in self:
            try:
                hour = float(rec.route_optimizer_route_start_hour or 8.0)
            except (TypeError, ValueError):
                hour = 8.0
            if not (0 <= hour < 24):
                raise ValidationError(_("La hora de salida de ruta debe estar entre 0 y 23.9."))

    @api.model
    def get_values(self):
        res = super().get_values()
        icp = self.env["ir.config_parameter"].sudo()
        res["route_optimizer_ortools_simple_api"] = self._param_bool(
            icp.get_param("route_optimizer.ortools_simple_api", "True"),
            default=True,
        )
        res["route_optimizer_use_delivery_windows"] = self._param_bool(
            icp.get_param("route_optimizer.use_delivery_windows", "True"),
            default=True,
        )
        res["route_optimizer_block_outside_windows"] = self._param_bool(
            icp.get_param("route_optimizer.block_outside_windows", "False"),
            default=False,
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
        icp.set_param(
            "route_optimizer.use_delivery_windows",
            "True" if self.route_optimizer_use_delivery_windows else "False",
        )
        icp.set_param(
            "route_optimizer.block_outside_windows",
            "True" if self.route_optimizer_block_outside_windows else "False",
        )
