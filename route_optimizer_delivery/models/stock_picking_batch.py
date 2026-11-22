# -*- coding: utf-8 -*-
import logging
import math
from datetime import datetime, time, timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.route_optimizer.services import delivery_windows, google_client, osrm_client, route_service

_logger = logging.getLogger(__name__)

# Sin servidor de ruteo se estima en línea recta: factor de desvío de calles y
# velocidad media urbana con semáforos.
DETOUR_FACTOR = 1.4
URBAN_SPEED_KMH = 28.0
DEFAULT_SERVICE_SECONDS = 300
# Pasado este tiempo, la última posición del chofer ya no sirve como origen.
POSITION_FRESH_MINUTES = 15
ROUTING_TIMEOUT = 8
# Polilíneas de una ruta partida en varios pedidos, guardadas en un solo campo de texto
# (los caracteres de una polilínea codificada nunca incluyen el salto de línea).
POLYLINE_SEPARATOR = "\n"


def _haversine_m(a, b):
    """Distancia en metros entre dos (lon, lat)."""
    lon1, lat1, lon2, lat2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


class StockPickingBatch(models.Model):
    _inherit = "stock.picking.batch"

    route_delivery_started_at = fields.Datetime(string="Salida de la ruta", copy=False, readonly=True)
    route_delivery_finished_at = fields.Datetime(string="Fin de la ruta", copy=False, readonly=True)
    route_delivery_last_latitude = fields.Float(digits=(10, 7), copy=False, readonly=True)
    route_delivery_last_longitude = fields.Float(digits=(10, 7), copy=False, readonly=True)
    route_delivery_last_position_at = fields.Datetime(
        string="Última posición", copy=False, readonly=True
    )
    route_delivery_polyline = fields.Text(
        string="Recorrido por calles",
        copy=False,
        readonly=True,
        help="Polilíneas codificadas del recorrido pendiente, una por línea.",
    )
    route_delivery_remaining_km = fields.Float(string="Km pendientes", copy=False, readonly=True)
    route_delivery_remaining_minutes = fields.Integer(
        string="Minutos pendientes", copy=False, readonly=True
    )
    route_delivery_routing_source = fields.Char(copy=False, readonly=True)
    route_delivery_eta_refreshed_at = fields.Datetime(copy=False, readonly=True)
    route_delivery_stop_count = fields.Integer(
        string="Paradas", compute="_compute_route_delivery_progress"
    )
    route_delivery_done_count = fields.Integer(
        string="Entregadas", compute="_compute_route_delivery_progress"
    )
    route_delivery_failed_count = fields.Integer(
        string="No entregadas", compute="_compute_route_delivery_progress"
    )
    route_delivery_progress = fields.Integer(
        string="Avance (%)", compute="_compute_route_delivery_progress"
    )

    @api.depends("picking_ids.route_delivery_state", "picking_ids.state")
    def _compute_route_delivery_progress(self):
        for batch in self:
            stops = batch.picking_ids.filtered(lambda p: p.state != "cancel")
            done = stops.filtered(lambda p: p.route_delivery_state in ("delivered", "partial"))
            failed = stops.filtered(lambda p: p.route_delivery_state == "failed")
            batch.route_delivery_stop_count = len(stops)
            batch.route_delivery_done_count = len(done)
            batch.route_delivery_failed_count = len(failed)
            batch.route_delivery_progress = (
                round(100 * (len(done) + len(failed)) / len(stops)) if stops else 0
            )

    # Se repiten los depends del core y de route_optimizer: al sobrescribir un compute,
    # Odoo toma las dependencias del método más derivado.
    @api.depends(
        "company_id", "picking_type_id", "state", "picking_ids.state", "picking_ids.route_delivery_state"
    )
    def _compute_allowed_picking_ids(self):
        """Una parada ya visitada sigue en su ruta aunque su traslado esté validado.

        Odoo solo admite traslados pendientes en un lote. Con «Validar el traslado
        al entregar», cada parada entregada queda en Hecho mientras el resto sigue
        pendiente, y tiene que seguir en la ruta para el avance, el tablero y el
        seguimiento del cliente.
        """
        super()._compute_allowed_picking_ids()
        for batch in self:
            batch.allowed_picking_ids |= batch.picking_ids.filtered(
                lambda p: p.state == "done" and p.route_delivery_state != "pending"
            )

    # ------------------------------------------------------------------
    # Horas estimadas
    # ------------------------------------------------------------------

    def _route_delivery_stops(self):
        self.ensure_one()
        return self._route_optimizer_pickings_visit_order().filtered(lambda p: p.state != "cancel")

    def _route_delivery_origin(self):
        """(lon, lat) desde donde sigue la ruta: el chofer, la última parada o el depósito."""
        self.ensure_one()
        if self.route_delivery_last_position_at and (
            fields.Datetime.now() - self.route_delivery_last_position_at
            < timedelta(minutes=POSITION_FRESH_MINUTES)
        ):
            return (self.route_delivery_last_longitude, self.route_delivery_last_latitude)
        visited = self.picking_ids.filtered(
            lambda p: p.route_delivery_state != "pending" and p.route_delivery_done_at
        ).sorted("route_delivery_done_at")
        if visited:
            coords = route_service._partner_coords(visited[-1]._route_optimizer_delivery_partner())
            if coords:
                return coords
        return route_service._partner_coords(route_service._get_depot_partner(self))

    def _route_delivery_departure(self):
        """Hora desde la que se cuentan las estimaciones (UTC, naive)."""
        self.ensure_one()
        now = fields.Datetime.now()
        if self.route_delivery_started_at:
            return now
        icp = self.env["ir.config_parameter"].sudo()
        tz = pytz.timezone(delivery_windows._routing_timezone(self.env, self))
        base = self.scheduled_date or now
        local = pytz.utc.localize(base).astimezone(tz)
        if not self.scheduled_date or (local.hour, local.minute) == (0, 0):
            # Sin hora propia, la ruta sale a la hora de salida configurada.
            try:
                start_hour = float(icp.get_param("route_optimizer.route_start_hour", "8") or 8)
            except ValueError:
                start_hour = 8.0
            hours = int(start_hour)
            local = tz.localize(
                datetime.combine(local.date(), time(hours, int(round((start_hour - hours) * 60)) % 60))
            )
        planned = local.astimezone(pytz.utc).replace(tzinfo=None)
        return max(planned, now) if planned.date() <= now.date() else planned

    @api.model
    def _route_delivery_routing_provider(self):
        """Quién calcula el recorrido por calles: 'google', 'osrm' o False (línea recta).

        Google se usa cuando ya es el proveedor elegido para optimizar: un pedido
        computeRoutes por ruta, con tráfico. OSRM, cuando hay servidor configurado.
        """
        icp = self.env["ir.config_parameter"].sudo()
        matrix = (icp.get_param("route_optimizer.matrix_provider") or "osrm").strip()
        solver = (icp.get_param("route_optimizer.solver_provider") or "ortools").strip()
        if "google" in (matrix, solver) and (icp.get_param("route_optimizer.google_api_key") or "").strip():
            return "google"
        if (icp.get_param("route_optimizer.osrm_url") or "").strip():
            return "osrm"
        return False

    def _route_delivery_path(self, points, departure=None):
        """Tramos entre puntos consecutivos y dibujo del recorrido.

        :param points: lista de (lon, lat) o None donde falta la coordenada
        :return: {"seconds": [..], "meters": [..], "polylines": [..], "source": str}
                 con None en los tramos que tocan un punto sin coordenadas.
        Si el servicio de ruteo no responde, estima en línea recta: el chofer y el
        tablero no pueden quedar frenados por un servicio externo.
        """
        count = len(points) - 1
        seconds, meters = [None] * count, [None] * count
        known = [i for i, pt in enumerate(points) if pt]
        coords = [points[i] for i in known]
        icp = self.env["ir.config_parameter"].sudo()
        provider = self._route_delivery_routing_provider()
        result = None
        if len(coords) >= 2 and provider and not self.env.context.get("route_delivery_no_routing"):
            try:
                if provider == "google":
                    stamp = None
                    if departure and departure > fields.Datetime.now() + timedelta(minutes=1):
                        stamp = departure.strftime("%Y-%m-%dT%H:%M:%SZ")
                    result = google_client.compute_route(
                        icp.get_param("route_optimizer.google_api_key"),
                        coords,
                        departure_rfc3339=stamp,
                        timeout=ROUTING_TIMEOUT,
                    )
                elif len(coords) <= 100:
                    result = osrm_client.fetch_route(
                        icp.get_param("route_optimizer.osrm_url"),
                        icp.get_param("route_optimizer.osrm_profile") or "driving",
                        coords,
                        timeout=ROUTING_TIMEOUT,
                    )
            except Exception as error:  # noqa: BLE001 - cualquier falla cae a la estimación
                _logger.info("El ruteo (%s) no respondió, se estima en línea recta: %s", provider, error)
                result = None
        for pos in range(len(known) - 1):
            a, b = known[pos], known[pos + 1]
            if result:
                leg_s, leg_m = result["durations"][pos], result["distances"][pos]
            else:
                leg_m = _haversine_m(points[a], points[b]) * DETOUR_FACTOR
                leg_s = leg_m / (URBAN_SPEED_KMH / 3.6)
            # El tramo se imputa a la llegada al punto b.
            seconds[b - 1], meters[b - 1] = leg_s, leg_m
        return {
            "seconds": seconds,
            "meters": meters,
            "polylines": (result or {}).get("polylines") or [],
            "source": provider if result else "estimate",
        }

    def _route_delivery_update_etas(self):
        icp = self.env["ir.config_parameter"].sudo()
        try:
            service = int(icp.get_param("route_optimizer.service_time_seconds") or 0)
        except ValueError:
            service = 0
        service = service or DEFAULT_SERVICE_SECONDS
        for batch in self:
            pending = batch._route_delivery_stops().filtered(
                lambda p: p.route_delivery_state == "pending"
            )
            if not pending:
                batch.write(
                    {
                        "route_delivery_polyline": False,
                        "route_delivery_remaining_km": 0.0,
                        "route_delivery_remaining_minutes": 0,
                    }
                )
                continue
            points = [batch._route_delivery_origin()] + [
                route_service._partner_coords(p._route_optimizer_delivery_partner()) for p in pending
            ]
            departure = batch._route_delivery_departure()
            path = batch._route_delivery_path(points, departure=departure)
            clock = departure
            for picking, leg in zip(pending, path["seconds"]):
                # Sin coordenadas se asume un tramo corto para no cortar la cadena.
                clock += timedelta(seconds=leg if leg is not None else 600)
                eta = clock.replace(microsecond=0)
                if picking.route_delivery_eta != eta:
                    picking.route_delivery_eta = eta
                clock += timedelta(seconds=service)
            batch.write(
                {
                    "route_delivery_polyline": POLYLINE_SEPARATOR.join(path["polylines"]) or False,
                    "route_delivery_remaining_km": sum(m or 0.0 for m in path["meters"]) / 1000.0,
                    "route_delivery_remaining_minutes": round((clock - departure).total_seconds() / 60.0),
                    "route_delivery_routing_source": path["source"],
                    "route_delivery_eta_refreshed_at": fields.Datetime.now(),
                }
            )
        return True

    # ------------------------------------------------------------------
    # App del chofer
    # ------------------------------------------------------------------

    @api.model
    def _route_delivery_is_dispatcher(self):
        return self.env.user.has_group("stock.group_stock_user")

    @api.model
    def _route_delivery_check_app_user(self):
        if not self.env.user.has_group("route_optimizer_delivery.group_route_delivery_driver"):
            raise AccessError(_("Tu usuario no tiene habilitada la app de reparto."))

    def _route_delivery_app_batch(self, batch_id):
        """Ruta sobre la que puede operar el usuario, ya elevada: el chofer no es
        usuario de Inventario y solo llega a las rutas de las que es responsable."""
        self._route_delivery_check_app_user()
        batch = self.sudo().browse(int(batch_id or 0)).exists()
        if not batch:
            raise UserError(_("La ruta ya no existe."))
        if not self._route_delivery_is_dispatcher() and batch.user_id != self.env.user:
            raise AccessError(_("Esa ruta está asignada a otro chofer."))
        return batch

    def _route_delivery_app_picking(self, picking_id):
        picking = self.env["stock.picking"].sudo().browse(int(picking_id or 0)).exists()
        if not picking or not picking.batch_id:
            raise UserError(_("La parada ya no está en una ruta."))
        self._route_delivery_app_batch(picking.batch_id.id)
        return picking

    def _route_delivery_summary(self):
        self.ensure_one()
        stops = self._route_delivery_stops()
        pending = stops.filtered(lambda p: p.route_delivery_state == "pending")
        vehicle = ""
        if "vehicle_id" in self._fields and self.vehicle_id:
            vehicle = self.vehicle_id.display_name
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description or "",
            "driver": self.user_id.name or "",
            "driver_id": self.user_id.id or False,
            "vehicle": vehicle,
            "state": self.state,
            "scheduled": fields.Datetime.to_string(self.scheduled_date) if self.scheduled_date else False,
            "started": bool(self.route_delivery_started_at),
            "started_at": fields.Datetime.to_string(self.route_delivery_started_at)
            if self.route_delivery_started_at
            else False,
            "finished": bool(self.route_delivery_finished_at),
            "stop_count": len(stops),
            "pending_count": len(pending),
            "done_count": self.route_delivery_done_count,
            "failed_count": self.route_delivery_failed_count,
            "progress": self.route_delivery_progress,
            "weight": sum(stops.mapped("shipping_weight")),
            "next_stop": (pending[0]._route_optimizer_delivery_partner().display_name or pending[0].name)
            if pending
            else "",
            "position": {
                "lat": self.route_delivery_last_latitude,
                "lon": self.route_delivery_last_longitude,
                "at": fields.Datetime.to_string(self.route_delivery_last_position_at),
            }
            if self.route_delivery_last_position_at
            else False,
            "gmaps_url": self.route_optimizer_gmaps_url or False,
            "remaining_km": round(self.route_delivery_remaining_km, 1),
            "remaining_minutes": self.route_delivery_remaining_minutes,
        }

    def _route_delivery_app_payload(self):
        """Ruta completa para la app: resumen, paradas con su detalle y motivos."""
        self.ensure_one()
        icp = self.env["ir.config_parameter"].sudo()

        def flag(key):
            return icp.get_param("route_optimizer_delivery.%s" % key) in ("True", "1", "true")

        data = self._route_delivery_summary()
        data.update(
            {
                "stops": [p._route_delivery_stop_data(detail=True) for p in self._route_delivery_stops()],
                "reasons": self.env["route.delivery.fail.reason"]
                .sudo()
                .search_read([], ["name", "require_note"]),
                "require_signature": flag("require_signature"),
                "require_photo": flag("require_photo"),
            }
        )
        return data

    @api.model
    def route_delivery_app_routes(self):
        """Rutas del chofer: las de estos días y cualquiera que haya quedado abierta."""
        self._route_delivery_check_app_user()
        now = fields.Datetime.now()
        domain = [
            ("state", "!=", "cancel"),
            ("picking_ids", "!=", False),
            "|",
            "&",
            ("route_delivery_started_at", "!=", False),
            ("route_delivery_finished_at", "=", False),
            "&",
            ("scheduled_date", ">=", now - timedelta(days=2)),
            ("scheduled_date", "<=", now + timedelta(days=2)),
        ]
        if not self._route_delivery_is_dispatcher():
            domain = [("user_id", "=", self.env.uid)] + domain
        batches = self.sudo().search(domain, order="scheduled_date, id", limit=60)
        batches = batches.filtered(lambda b: b.state != "draft" or b.route_delivery_started_at)
        return {
            "dispatcher": self._route_delivery_is_dispatcher(),
            "user": self.env.user.name,
            "routes": [b._route_delivery_summary() for b in batches],
        }

    @api.model
    def route_delivery_app_route(self, batch_id):
        return self._route_delivery_app_batch(batch_id)._route_delivery_app_payload()

    def _route_delivery_set_position(self, lat, lon):
        self.ensure_one()
        try:
            lat, lon = float(lat or 0.0), float(lon or 0.0)
        except (TypeError, ValueError):
            return False
        if not lat and not lon:
            return False
        self.write(
            {
                "route_delivery_last_latitude": lat,
                "route_delivery_last_longitude": lon,
                "route_delivery_last_position_at": fields.Datetime.now(),
            }
        )
        return True

    @api.model
    def route_delivery_app_start(self, batch_id, lat=None, lon=None):
        batch = self._route_delivery_app_batch(batch_id)
        if not batch.route_delivery_started_at:
            batch.write(
                {"route_delivery_started_at": fields.Datetime.now(), "route_delivery_finished_at": False}
            )
            batch._route_delivery_set_position(lat, lon)
            batch._route_delivery_update_etas()
            pending = batch._route_delivery_stops().filtered(
                lambda p: p.route_delivery_state == "pending"
            )
            for picking in pending:
                # Foto de lo prometido: contra esto se mide el atraso.
                picking.route_delivery_planned_eta = picking.route_delivery_eta
            pending._route_delivery_ensure_token()
            pending._route_delivery_notify("on_the_way")
            batch._route_delivery_notify_next()
        return batch._route_delivery_app_payload()

    @api.model
    def route_delivery_app_ping(self, batch_id, lat, lon):
        """Posición del chofer; liviano porque corre cada minuto."""
        batch = self._route_delivery_app_batch(batch_id)
        moved = batch._route_delivery_set_position(lat, lon)
        if moved and batch.route_delivery_started_at and not batch.route_delivery_finished_at:
            batch._route_delivery_refresh_etas_if_stale()
        return moved

    def _route_delivery_refresh_etas_if_stale(self):
        """Recalcula las horas con la posición real del chofer, como mucho cada N minutos.

        Sin esto las horas solo se corregían al completar una parada: un chofer
        parado en el tráfico seguía mostrando la hora vieja al cliente. El intervalo
        es configurable (0 lo apaga) porque con Google cada recálculo es un pedido pago.
        """
        self.ensure_one()
        icp = self.env["ir.config_parameter"].sudo()
        try:
            minutes = int(icp.get_param("route_optimizer_delivery.eta_refresh_minutes", "5") or 0)
        except ValueError:
            minutes = 5
        if minutes <= 0:
            return False
        last = self.route_delivery_eta_refreshed_at
        if last and fields.Datetime.now() - last < timedelta(minutes=minutes):
            return False
        self._route_delivery_update_etas()
        self._route_delivery_notify_next()
        return True

    @api.model
    def route_delivery_app_scan(self, code, batch_id=None):
        """Busca qué abrir con un código escaneado: una parada (remito, pedido o
        bulto) o una hoja de ruta."""
        self._route_delivery_check_app_user()
        code = (code or "").strip()
        if not code:
            return {"error": _("Código vacío.")}
        Picking = self.env["stock.picking"].sudo()
        domain = ["|", ("name", "=ilike", code), ("origin", "=ilike", code)]
        scope = [("batch_id", "=", int(batch_id))] if batch_id else [("batch_id", "!=", False)]
        picking = Picking.search(scope + domain, order="id desc", limit=1)
        if not picking and "result_package_id" in self.env["stock.move.line"]._fields:
            line = self.env["stock.move.line"].sudo().search(
                [("result_package_id.name", "=ilike", code), ("picking_id.batch_id", "!=", False)]
                + ([("picking_id.batch_id", "=", int(batch_id))] if batch_id else []),
                order="id desc",
                limit=1,
            )
            picking = line.picking_id
        if picking:
            self._route_delivery_app_batch(picking.batch_id.id)
            return {"batch_id": picking.batch_id.id, "picking_id": picking.id}
        if not batch_id:
            batch = self.sudo().search([("name", "=ilike", code)], limit=1)
            if batch:
                self._route_delivery_app_batch(batch.id)
                return {"batch_id": batch.id, "picking_id": False}
        return {
            "error": _("«%s» no es una parada de esta ruta.") % code
            if batch_id
            else _("No encontré ninguna ruta ni parada con «%s».") % code
        }

    def _route_delivery_notify_next(self):
        """Avisa «sos el próximo» a la primera parada pendiente de una ruta en curso."""
        for batch in self.filtered("route_delivery_started_at"):
            pending = batch._route_delivery_stops().filtered(
                lambda p: p.route_delivery_state == "pending"
            )
            if pending and not pending[0].route_delivery_next_notified:
                pending[0].route_delivery_next_notified = True
                pending[0]._route_delivery_notify("next")

    def _route_delivery_after_stop(self):
        """Tras completar una parada: recalcula horas, avisa al siguiente y cierra
        la ruta si no queda nada pendiente."""
        for batch in self:
            pending = batch._route_delivery_stops().filtered(
                lambda p: p.route_delivery_state == "pending"
            )
            if not pending:
                if not batch.route_delivery_finished_at:
                    batch.route_delivery_finished_at = fields.Datetime.now()
                continue
            if not batch.route_delivery_started_at:
                batch.route_delivery_started_at = fields.Datetime.now()
            batch._route_delivery_update_etas()
            batch._route_delivery_notify_next()

    @api.model
    def route_delivery_app_deliver(self, picking_id, vals):
        picking = self._route_delivery_app_picking(picking_id)
        batch = picking.batch_id
        picking._route_delivery_register_delivered(vals or {})
        batch._route_delivery_set_position((vals or {}).get("lat"), (vals or {}).get("lon"))
        batch._route_delivery_after_stop()
        return batch._route_delivery_app_payload()

    @api.model
    def route_delivery_app_fail(self, picking_id, vals):
        picking = self._route_delivery_app_picking(picking_id)
        batch = picking.batch_id
        picking._route_delivery_register_failed(vals or {})
        batch._route_delivery_set_position((vals or {}).get("lat"), (vals or {}).get("lon"))
        batch._route_delivery_after_stop()
        return batch._route_delivery_app_payload()

    @api.model
    def route_delivery_app_reopen(self, picking_id):
        """Corrige una parada cargada por error: vuelve a Pendiente."""
        picking = self._route_delivery_app_picking(picking_id)
        picking.action_route_delivery_reset()
        return picking.batch_id._route_delivery_app_payload()

    # ------------------------------------------------------------------
    # Tablero de despacho (usuarios de Inventario, con sus permisos)
    # ------------------------------------------------------------------

    @api.model
    def _route_dispatch_day_bounds(self, day):
        tz = pytz.timezone(self.env.user.tz or "UTC")
        local_day = fields.Date.to_date(day) if day else fields.Date.context_today(self)
        start = tz.localize(datetime.combine(local_day, time.min)).astimezone(pytz.utc)
        return local_day, start.replace(tzinfo=None), (start + timedelta(days=1)).replace(tzinfo=None)

    def _route_dispatch_capacity(self):
        """Capacidad de peso del vehículo de la ruta (kg), o 0 si no se conoce.

        Solo hay dato con Flota instalado y un vehículo en el lote; se reutiliza la
        misma búsqueda de campos que el asistente de optimización.
        """
        self.ensure_one()
        if "vehicle_id" not in self._fields or not self.vehicle_id:
            return 0.0
        wizard = self.env["route.optimizer.wizard"]
        if not hasattr(wizard, "_fleet_capacity_candidates"):
            return 0.0
        category = getattr(getattr(self.vehicle_id, "model_id", None), "category_id", None)
        capacity = 0.0
        if category and "weight_capacity" in category._fields:
            capacity = category.weight_capacity or 0.0
        return float(capacity or wizard._fleet_capacity_candidates(self.vehicle_id) or 0.0)

    def _route_dispatch_route_data(self):
        self.ensure_one()
        data = self._route_delivery_summary()
        stops = self._route_delivery_stops()
        delays = [
            (p.route_delivery_eta - p.route_delivery_planned_eta).total_seconds() / 60.0
            for p in stops
            if p.route_delivery_state == "pending" and p.route_delivery_eta and p.route_delivery_planned_eta
        ]
        data.update(
            {
                "stops": [p._route_delivery_stop_data() for p in stops],
                "polylines": (self.route_delivery_polyline or "").split(POLYLINE_SEPARATOR)
                if self.route_delivery_polyline
                else [],
                "capacity": self._route_dispatch_capacity(),
                "delay_minutes": round(max(delays)) if delays else 0,
                "routing_source": self.route_delivery_routing_source or "",
            }
        )
        return data

    def route_dispatch_route(self):
        """Una sola ruta con su mapa, para la pestaña Recorrido del formulario del lote."""
        self.ensure_one()
        data = self._route_dispatch_route_data()
        coords = route_service._partner_coords(route_service._get_depot_partner(self))
        data["depot"] = {"lon": coords[0], "lat": coords[1], "name": ""} if coords else False
        data["map"] = self._route_dispatch_map_config()
        return data

    @api.model
    def _route_dispatch_map_config(self):
        icp = self.env["ir.config_parameter"].sudo()
        provider = (icp.get_param("route_optimizer_delivery.map_provider") or "osm").strip()
        google_key = (icp.get_param("route_optimizer_delivery.google_maps_key") or "").strip()
        return {
            "provider": "google" if provider == "google" and google_key else "osm",
            "google_key": google_key if provider == "google" else "",
            "tile_url": (icp.get_param("route_optimizer_delivery.tile_url") or "").strip()
            or "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        }

    @api.model
    def route_dispatch_data(self, day=None):
        local_day, start, end = self._route_dispatch_day_bounds(day)
        batches = self.search(
            [("state", "!=", "cancel"), ("scheduled_date", ">=", start), ("scheduled_date", "<", end)],
            order="id",
        )
        icp = self.env["ir.config_parameter"].sudo()
        states = ["confirmed", "assigned", "waiting"]
        if route_service._param_bool(icp.get_param("route_optimizer.allow_done_in_batch", "False")):
            states.append("done")
        unassigned = self.env["stock.picking"].search(
            [
                ("picking_type_code", "=", "outgoing"),
                ("batch_id", "=", False),
                ("state", "in", states),
                ("route_delivery_state", "=", "pending"),
                ("scheduled_date", ">=", start),
                ("scheduled_date", "<", end),
            ],
            order="scheduled_date, id",
            limit=300,
        )
        depot = False
        company_partner = self.env.company.partner_id
        warehouse = batches[:1].warehouse_id or self.env["stock.warehouse"].search(
            [("company_id", "=", self.env.company.id)], limit=1
        )
        coords = route_service._partner_coords(warehouse.partner_id or company_partner)
        if coords:
            depot = {"lon": coords[0], "lat": coords[1], "name": (warehouse.partner_id or company_partner).display_name}
        driver_group = self.env.ref("route_optimizer_delivery.group_route_delivery_driver")
        drivers = self.env["res.users"].sudo().search(
            [("all_group_ids", "in", driver_group.ids), ("share", "=", False)]
            if "all_group_ids" in self.env["res.users"]._fields
            else [("groups_id", "in", driver_group.ids), ("share", "=", False)],
            order="name",
        )
        return {
            "day": fields.Date.to_string(local_day),
            "routes": [batch._route_dispatch_route_data() for batch in batches],
            "unassigned": [p._route_delivery_stop_data() for p in unassigned],
            "depot": depot,
            "drivers": [{"id": user.id, "name": user.name} for user in drivers],
            "map": self._route_dispatch_map_config(),
            "routing": self._route_delivery_routing_provider() or "estimate",
        }

    # --- Inserción más barata ---------------------------------------------

    def _route_dispatch_chain(self):
        """Puntos (lon, lat) del recorrido pendiente: origen + paradas pendientes."""
        self.ensure_one()
        pending = self._route_delivery_stops().filtered(lambda p: p.route_delivery_state == "pending")
        coords = [route_service._partner_coords(p._route_optimizer_delivery_partner()) for p in pending]
        return pending, [self._route_delivery_origin()] + coords

    @staticmethod
    def _route_dispatch_insertion(chain, point):
        """Mejor lugar para meter `point` en la cadena: (índice entre pendientes, metros extra).

        El costo de cada lugar es el desvío en línea recta corregido por el factor de
        calles: alcanza para comparar rutas entre sí sin pedirle nada al ruteo.
        """
        known = [(i, pt) for i, pt in enumerate(chain) if pt]
        if not point or not known:
            return len(chain) - 1, 0.0
        best_index, best_cost = len(chain) - 1, None
        for pos, (i, a) in enumerate(known):
            if pos + 1 < len(known):
                b = known[pos + 1][1]
                cost = _haversine_m(a, point) + _haversine_m(point, b) - _haversine_m(a, b)
            else:
                cost = _haversine_m(a, point)  # al final de la ruta
            if best_cost is None or cost < best_cost:
                best_index, best_cost = i, cost
        return best_index, max(best_cost or 0.0, 0.0) * DETOUR_FACTOR

    def _route_dispatch_plan(self, pickings):
        """Dónde entraría cada entrega de `pickings` en esta ruta, una tras otra.

        :return: (posiciones [(picking, índice entre pendientes)], metros extra totales)
        """
        self.ensure_one()
        pending, chain = self._route_dispatch_chain()
        chain = list(chain)
        order = list(pending)
        placements, extra = [], 0.0
        for picking in pickings:
            point = route_service._partner_coords(picking._route_optimizer_delivery_partner())
            index, cost = self._route_dispatch_insertion(chain, point)
            chain.insert(index + 1, point)
            order.insert(index, picking)
            extra += cost
        for picking in pickings:
            placements.append((picking, order.index(picking)))
        return placements, extra, order

    @api.model
    def route_dispatch_suggest(self, picking_ids, day=None):
        """Rutas del día ordenadas por lo que cuesta sumarles estas entregas."""
        pickings = self.env["stock.picking"].browse([int(i) for i in picking_ids or []]).exists()
        pickings = pickings.filtered(lambda p: p.route_delivery_state == "pending")
        if not pickings:
            return []
        _local_day, start, end = self._route_dispatch_day_bounds(day)
        batches = self.search(
            [
                ("state", "!=", "cancel"),
                ("scheduled_date", ">=", start),
                ("scheduled_date", "<", end),
                ("route_delivery_finished_at", "=", False),
            ],
            order="id",
        )
        icp = self.env["ir.config_parameter"].sudo()
        try:
            service = int(icp.get_param("route_optimizer.service_time_seconds") or 0) or DEFAULT_SERVICE_SECONDS
        except ValueError:
            service = DEFAULT_SERVICE_SECONDS
        options = []
        for batch in batches:
            moving = pickings.filtered(lambda p: p.batch_id != batch)
            if not moving:
                continue
            placements, extra_m, order = batch._route_dispatch_plan(moving)
            extra_min = extra_m / (URBAN_SPEED_KMH / 3.6) / 60.0 + len(moving) * service / 60.0
            load = sum(batch._route_delivery_stops().mapped("shipping_weight")) + sum(moving.mapped("shipping_weight"))
            capacity = batch._route_dispatch_capacity()
            first = placements[0][1]
            before = order[first - 1] if first > 0 else False
            after = order[first + 1] if first + 1 < len(order) else False
            options.append(
                {
                    "batch_id": batch.id,
                    "name": batch.name,
                    "driver": batch.user_id.name or "",
                    "extra_minutes": round(extra_min),
                    "extra_km": round(extra_m / 1000.0, 1),
                    "position": first + 1,
                    "stop_count": len(order),
                    "before": (before._route_optimizer_delivery_partner().display_name or before.name) if before else "",
                    "after": (after._route_optimizer_delivery_partner().display_name or after.name) if after else "",
                    "load": round(load, 1),
                    "capacity": capacity,
                    "over_capacity": bool(capacity and load > capacity + 0.001),
                    "started": bool(batch.route_delivery_started_at),
                }
            )
        # Primero las que entran; entre ellas, la que menos se desvía.
        options.sort(key=lambda o: (o["over_capacity"], o["extra_minutes"], o["extra_km"]))
        return options

    @api.model
    def route_dispatch_assign(self, picking_ids, batch_id):
        """Suma las entregas a la ruta, cada una en su lugar más barato."""
        target = self.browse(int(batch_id or 0)).exists()
        if not target or target.state == "cancel":
            raise UserError(_("La ruta ya no está disponible."))
        pickings = self.env["stock.picking"].browse([int(i) for i in picking_ids or []]).exists()
        visited = pickings.filtered(lambda p: p.route_delivery_state != "pending")
        if visited:
            raise UserError(_("%s ya fue visitada: no se puede mover.") % visited[0].name)
        pickings = pickings.filtered(lambda p: p.batch_id != target)
        if not pickings:
            return True
        sources = pickings.batch_id
        _placements, _extra, order = target._route_dispatch_plan(pickings)
        visited_stops = target._route_delivery_stops().filtered(lambda p: p.route_delivery_state != "pending")
        pickings.write({"batch_id": target.id})
        for position, stop in enumerate(list(visited_stops) + order, 1):
            if stop.batch_sequence != position * 10:
                stop.batch_sequence = position * 10
        (sources | target)._route_delivery_update_etas()
        return True

    @api.model
    def route_dispatch_move(self, picking_id, batch_id, index=None):
        """Mueve una parada a otra ruta (o la saca) y/o cambia su posición."""
        picking = self.env["stock.picking"].browse(int(picking_id)).exists()
        if not picking:
            raise UserError(_("La entrega ya no existe."))
        if picking.route_delivery_state != "pending":
            raise UserError(_("%s ya fue visitada: no se puede mover.") % picking.name)
        source = picking.batch_id
        target = self.browse(int(batch_id or 0)).exists()
        if not target:
            picking.write({"batch_id": False, "batch_sequence": 0, "route_delivery_eta": False})
            source._route_delivery_update_etas()
            return True
        if target.state == "cancel":
            raise UserError(_("La ruta %s está cancelada.") % target.name)
        order = [p for p in target._route_optimizer_pickings_visit_order() if p != picking]
        index = len(order) if index is None else max(0, min(int(index), len(order)))
        order.insert(index, picking)
        if source != target:
            picking.batch_id = target.id
        for position, stop in enumerate(order, 1):
            if stop.batch_sequence != position * 10:
                stop.batch_sequence = position * 10
        (source | target)._route_delivery_update_etas()
        return True

    @api.model
    def route_dispatch_unassign(self, picking_ids):
        pickings = self.env["stock.picking"].browse([int(i) for i in picking_ids or []]).exists()
        pickings = pickings.filtered(lambda p: p.batch_id and p.route_delivery_state == "pending")
        sources = pickings.batch_id
        pickings.write({"batch_id": False, "batch_sequence": 0, "route_delivery_eta": False})
        sources._route_delivery_update_etas()
        return True

    @api.model
    def route_dispatch_new_route(self, day, picking_ids):
        """Abre una ruta nueva con estas entregas (toma el tipo de operación de la primera)."""
        if isinstance(picking_ids, int):
            picking_ids = [picking_ids]
        pickings = self.env["stock.picking"].browse([int(i) for i in picking_ids or []]).exists()
        pickings = pickings.filtered(lambda p: p.route_delivery_state == "pending")
        if not pickings:
            raise UserError(_("Elegí al menos una entrega pendiente."))
        sources = pickings.batch_id
        batch = self.create(
            {"picking_type_id": pickings[0].picking_type_id.id, "company_id": pickings[0].company_id.id}
        )
        # Vecino más cercano desde el depósito: un orden razonable hasta que se optimice.
        _placements, _extra, order = batch._route_dispatch_plan(pickings)
        pickings.write({"batch_id": batch.id})
        for position, stop in enumerate(order, 1):
            stop.batch_sequence = position * 10
        if not batch.scheduled_date:
            batch.scheduled_date = self._route_dispatch_day_bounds(day)[1] + timedelta(hours=12)
        (sources | batch)._route_delivery_update_etas()
        return batch.id

    @api.model
    def route_dispatch_set_driver(self, batch_id, user_id):
        batch = self.browse(int(batch_id)).exists()
        if not batch:
            raise UserError(_("La ruta ya no existe."))
        batch.user_id = int(user_id) if user_id else False
        return True

    @api.model
    def route_dispatch_publish(self, day=None):
        """Confirma las rutas en borrador del día: recién ahí las ve el chofer."""
        _local_day, start, end = self._route_dispatch_day_bounds(day)
        drafts = self.search(
            [("state", "=", "draft"), ("scheduled_date", ">=", start), ("scheduled_date", "<", end)]
        ).filtered("picking_ids")
        without_driver = drafts.filtered(lambda b: not b.user_id)
        for batch in drafts - without_driver:
            batch.action_confirm()
        return {
            "published": len(drafts - without_driver),
            "without_driver": without_driver.mapped("name"),
        }

    @api.model
    def route_dispatch_optimize(self, batch_ids):
        """Optimiza el orden de cada ruta con el motor configurado (un vehículo por ruta)."""
        done, errors = [], []
        for batch in self.browse([int(i) for i in batch_ids or []]).exists():
            if batch.route_delivery_started_at:
                continue  # una ruta en la calle no se reordena sola
            try:
                with self.env.cr.savepoint():
                    route_service.optimize_batch(self.env, batch, num_vehicles=1, use_duration=True)
                    batch._route_delivery_update_etas()
                done.append(batch.name)
            except UserError as error:
                errors.append("%s: %s" % (batch.name, str(error).split("\n")[0]))
        return {"optimized": done, "errors": errors}

    @api.model
    def route_dispatch_retry(self, picking_id):
        """Una entrega fallida vuelve a pendiente, al final de su ruta."""
        picking = self.env["stock.picking"].browse(int(picking_id)).exists()
        if not picking or not picking.batch_id:
            raise UserError(_("La entrega ya no está en una ruta."))
        batch = picking.batch_id
        last = max(batch.picking_ids.mapped("batch_sequence") or [0])
        picking.batch_sequence = last + 10
        picking.action_route_delivery_reset()
        return True

    @api.model
    def route_dispatch_postpone(self, picking_id):
        """Saca la entrega de la ruta y la deja pendiente para el día siguiente."""
        picking = self.env["stock.picking"].browse(int(picking_id)).exists()
        if not picking:
            raise UserError(_("La entrega ya no existe."))
        batch = picking.batch_id
        values = {
            "batch_id": False,
            "batch_sequence": 0,
            "route_delivery_state": "pending",
            "route_delivery_done_at": False,
            "route_delivery_eta": False,
            "route_delivery_planned_eta": False,
            "route_delivery_next_notified": False,
        }
        if picking.state not in ("done", "cancel"):
            values["scheduled_date"] = (picking.scheduled_date or fields.Datetime.now()) + timedelta(days=1)
        picking.write(values)
        batch._route_delivery_after_stop()
        return True

    def action_route_delivery_update_etas(self):
        self._route_delivery_update_etas()
        return True
