# -*- coding: utf-8 -*-
import logging
import math
from datetime import datetime, time, timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.route_optimizer.services import delivery_windows, osrm_client, route_service

_logger = logging.getLogger(__name__)

# Sin servidor de ruteo se estima en línea recta: factor de desvío de calles y
# velocidad media urbana con semáforos.
DETOUR_FACTOR = 1.4
URBAN_SPEED_KMH = 28.0
DEFAULT_SERVICE_SECONDS = 300
# Pasado este tiempo, la última posición del chofer ya no sirve como origen.
POSITION_FRESH_MINUTES = 15
OSRM_ETA_TIMEOUT = 6


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

    def _route_delivery_leg_seconds(self, points):
        """Segundos de viaje entre puntos consecutivos; None donde falta la coordenada.

        Usa OSRM si es el proveedor configurado (Google cobra por elemento de la
        matriz, y esto corre en cada parada). Si no responde, estima en línea recta.
        """
        legs = [None] * (len(points) - 1)
        known = [i for i, pt in enumerate(points) if pt]
        icp = self.env["ir.config_parameter"].sudo()
        provider = (icp.get_param("route_optimizer.matrix_provider") or "osrm").strip()
        base_url = (icp.get_param("route_optimizer.osrm_url") or "").strip()
        durations = None
        if provider == "osrm" and base_url and 2 <= len(known) <= 100:
            try:
                table = osrm_client.fetch_table(
                    base_url,
                    icp.get_param("route_optimizer.osrm_profile") or "driving",
                    [points[i] for i in known],
                    timeout=OSRM_ETA_TIMEOUT,
                )
                durations = table.get("durations")
            except Exception as error:  # noqa: BLE001 - cualquier falla cae a la estimación
                _logger.info("OSRM no respondió para las horas estimadas: %s", error)
        for pos in range(len(known) - 1):
            a, b = known[pos], known[pos + 1]
            seconds = None
            if durations:
                try:
                    seconds = float(durations[pos][pos + 1])
                except (IndexError, TypeError, ValueError):
                    seconds = None
            if seconds is None:
                meters = _haversine_m(points[a], points[b]) * DETOUR_FACTOR
                seconds = meters / (URBAN_SPEED_KMH / 3.6)
            # El tramo se imputa a la llegada al punto b.
            legs[b - 1] = seconds
        return legs

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
                continue
            points = [batch._route_delivery_origin()] + [
                route_service._partner_coords(p._route_optimizer_delivery_partner()) for p in pending
            ]
            legs = batch._route_delivery_leg_seconds(points)
            clock = batch._route_delivery_departure()
            for picking, leg in zip(pending, legs):
                # Sin coordenadas se asume un tramo corto para no cortar la cadena.
                clock += timedelta(seconds=leg if leg is not None else 600)
                if picking.route_delivery_eta != clock:
                    picking.route_delivery_eta = clock.replace(microsecond=0)
                clock += timedelta(seconds=service)
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
            pending._route_delivery_ensure_token()
            pending._route_delivery_notify("on_the_way")
            batch._route_delivery_notify_next()
        return batch._route_delivery_app_payload()

    @api.model
    def route_delivery_app_ping(self, batch_id, lat, lon):
        """Posición del chofer; liviano porque corre cada minuto."""
        batch = self._route_delivery_app_batch(batch_id)
        return batch._route_delivery_set_position(lat, lon)

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
        routes = []
        for batch in batches:
            data = batch._route_delivery_summary()
            data["stops"] = [p._route_delivery_stop_data() for p in batch._route_delivery_stops()]
            routes.append(data)
        return {
            "day": fields.Date.to_string(local_day),
            "routes": routes,
            "unassigned": [p._route_delivery_stop_data() for p in unassigned],
            "depot": depot,
            "tile_url": (icp.get_param("route_optimizer_delivery.tile_url") or "").strip()
            or "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        }

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
    def route_dispatch_new_route(self, day, picking_id):
        """Crea una ruta nueva a partir de una entrega (toma su tipo de operación)."""
        picking = self.env["stock.picking"].browse(int(picking_id)).exists()
        if not picking:
            raise UserError(_("La entrega ya no existe."))
        source = picking.batch_id
        batch = self.create(
            {"picking_type_id": picking.picking_type_id.id, "company_id": picking.company_id.id}
        )
        picking.write({"batch_id": batch.id, "batch_sequence": 10})
        if not batch.scheduled_date:
            batch.scheduled_date = self._route_dispatch_day_bounds(day)[1] + timedelta(hours=12)
        (source | batch)._route_delivery_update_etas()
        return batch.id

    def action_route_delivery_update_etas(self):
        self._route_delivery_update_etas()
        return True
