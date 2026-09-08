# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..services import google_client, ortools_client, osrm_client, route_service

# Dos puntos cercanos en CABA para tests de conexión (matriz 2x2 trivial).
_TEST_COORDS = [(-58.3816, -34.6037), (-58.3712, -34.6083)]


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

    route_optimizer_matrix_provider = fields.Selection(
        [("osrm", "OSRM (self-hosted)"), ("google", "Google Routes API (pago)")],
        string="Proveedor de matrices",
        config_parameter="route_optimizer.matrix_provider",
        default="osrm",
        help="Servicio que calcula las matrices de distancia/tiempo entre paradas. "
        "Se ignora si el solver es Google (calcula sus propias matrices).",
    )
    route_optimizer_solver_provider = fields.Selection(
        [("ortools", "OR-Tools (self-hosted)"), ("google", "Google Route Optimization (pago)")],
        string="Proveedor del solver",
        config_parameter="route_optimizer.solver_provider",
        default="ortools",
        help="Servicio que resuelve el orden de la ruta (VRP). Google Route Optimization "
        "requiere un service account de GCP, no una API key.",
    )
    route_optimizer_google_api_key = fields.Char(
        string="Google API key (Routes API)",
        config_parameter="route_optimizer.google_api_key",
        help="API key de GCP con Routes API habilitada. Solo para el proveedor de matrices.",
    )
    route_optimizer_google_project_id = fields.Char(
        string="Google project id",
        config_parameter="route_optimizer.google_project_id",
        help="ID del proyecto de GCP con Route Optimization API habilitada.",
    )
    # NOTE: no config_parameter here — res.config.settings rejects Text fields with
    # config_parameter; persisted manually in get_values/set_values below.
    route_optimizer_google_service_account_json = fields.Text(
        string="Google service account (JSON)",
        help="Contenido completo del archivo JSON del service account de GCP. "
        "Requerido solo si el solver es Google Route Optimization.",
    )
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
    route_optimizer_allow_done_in_batch = fields.Boolean(
        string="Permitir traslados validados en los lotes",
        config_parameter="route_optimizer.allow_done_in_batch",
        help="Habilita agregar traslados en estado Hecho a un traslado por lote, "
        "para armar la hoja de ruta cuando el reparto físico ocurre después de "
        "validar. Odoo por defecto solo admite traslados pendientes.",
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
        res["route_optimizer_google_service_account_json"] = (
            icp.get_param("route_optimizer.google_service_account_json", "") or ""
        )
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
            "route_optimizer.google_service_account_json",
            (self.route_optimizer_google_service_account_json or "").strip(),
        )
        icp.set_param(
            "route_optimizer.use_delivery_windows",
            "True" if self.route_optimizer_use_delivery_windows else "False",
        )
        icp.set_param(
            "route_optimizer.block_outside_windows",
            "True" if self.route_optimizer_block_outside_windows else "False",
        )

    # ------------------------------------------------------------------
    # Botones "Probar conexión"
    # ------------------------------------------------------------------

    def _route_optimizer_test_ok(self, title, message):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": title,
                "message": message,
                "type": "success",
                "sticky": False,
            },
        }

    def action_route_optimizer_test_osrm(self):
        """Consulta una matriz 2x2 trivial contra el servidor OSRM configurado."""
        self.ensure_one()
        if not (self.route_optimizer_osrm_url or "").strip():
            raise UserError(_("Cargá primero la URL base de OSRM."))
        try:
            osrm_client.fetch_table(
                self.route_optimizer_osrm_url,
                (self.route_optimizer_osrm_profile or "driving").strip(),
                _TEST_COORDS,
                timeout=15,
            )
        except osrm_client.OsrmError as e:
            raise UserError(_("OSRM: falló la conexión.\n\n%s") % str(e)) from e
        return self._route_optimizer_test_ok(
            _("OSRM"), _("Conexión exitosa: el servidor respondió la matriz de prueba.")
        )

    def action_route_optimizer_test_ortools(self):
        """Envía un problema trivial (2 nodos) al microservicio OR-Tools configurado."""
        self.ensure_one()
        if not (self.route_optimizer_ortools_url or "").strip():
            raise UserError(_("Cargá primero la URL del servicio OR-Tools."))
        simple = bool(self.route_optimizer_ortools_simple_api)
        url = route_service._normalize_ortools_service_url(
            self.route_optimizer_ortools_url, simple
        )
        api_key = (self.route_optimizer_ortools_api_key or "").strip() or None
        matrix = [[0, 10], [10, 0]]
        try:
            if simple:
                ortools_client.solve_simple_distance_api(
                    url, ["__TEST_DEPOT__", "__TEST_STOP__"], matrix, timeout=15, api_key=api_key
                )
            else:
                ortools_client.solve_vrp(
                    url,
                    {
                        "version": 1,
                        "num_vehicles": 1,
                        "depot_index": 0,
                        "matrix": matrix,
                        "matrix_metric": "distance",
                        "picking_ids": [0],
                    },
                    timeout=15,
                    api_key=api_key,
                )
        except ortools_client.OrtoolsServiceError as e:
            msg = str(e)
            if "403" in msg:
                msg += "\n\n" + _(
                    "El servicio rechazó la API key. Verificá que coincida con la variable "
                    "API_KEY del archivo .env del contenedor."
                )
            raise UserError(_("OR-Tools: falló la conexión.\n\n%s") % msg) from e
        return self._route_optimizer_test_ok(
            _("OR-Tools"),
            _("Conexión exitosa: el solver resolvió el problema de prueba (API key válida)."),
        )

    def action_route_optimizer_test_google(self):
        """Valida la API key (matriz 2x2, 4 elementos) y/o el service account (token OAuth)."""
        self.ensure_one()
        checks = []
        api_key = (self.route_optimizer_google_api_key or "").strip()
        if self.route_optimizer_matrix_provider == "google":
            if not api_key:
                raise UserError(_("Cargá primero la Google API key (Routes API)."))
            try:
                google_client.fetch_route_matrix(api_key, _TEST_COORDS, timeout=15)
            except google_client.GoogleApiError as e:
                raise UserError(_("Google Routes API: falló la conexión.\n\n%s") % str(e)) from e
            checks.append(_("Routes API OK (matriz de prueba, 4 elementos)"))
        if self.route_optimizer_solver_provider == "google":
            sa_json = (self.route_optimizer_google_service_account_json or "").strip()
            if not sa_json:
                raise UserError(_("Cargá primero el JSON del service account."))
            if not (self.route_optimizer_google_project_id or "").strip():
                raise UserError(_("Cargá primero el Google project id."))
            try:
                google_client._service_account_token(sa_json, timeout=15)
            except google_client.GoogleApiError as e:
                raise UserError(
                    _("Google service account: falló la autenticación.\n\n%s") % str(e)
                ) from e
            checks.append(_("Service account OK (token OAuth emitido)"))
        if not checks:
            raise UserError(
                _("Ningún proveedor está configurado como Google; no hay nada que probar.")
            )
        return self._route_optimizer_test_ok(_("Google"), " · ".join(checks))
