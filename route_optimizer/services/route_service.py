# -*- coding: utf-8 -*-
"""
Build matrices from OSRM, call OR-Tools service, apply results to pickings/batches.

Expected OR-Tools microservice contract (JSON POST body produced by Odoo)::

    {
        "version": 1,
        "num_vehicles": int,
        "depot_index": 0,
        "matrix": [[float, ...], ...],   # square, same order as nodes (depot + pickings)
        "matrix_metric": "duration" | "distance",
        "demands": [0, w1, w2, ...],     # depot demand 0, then shipping_weight per stop
        "vehicle_capacities": [float, ...],
        "picking_ids": [int, ...],       # same order as matrix columns after depot
        # Optional (OCA delivery windows, duration matrix only):
        "route_start_seconds": int,
        "service_times": [0, 600, ...],
        "time_windows": [[start, end], ...],
        "time_windows_list": [[[s,e], ...], ...],  # multiple intervals per node
    }

Successful response (one of)::

    {"success": true, "ordered_picking_ids": [id, ...]}

Or single-vehicle route::

    {"success": true, "routes": [{"node_indices": [0, 2, 1, 0]}]}

Or multi-vehicle (num_vehicles > 1)::

    {
        "success": true,
        "routes": [
            {"node_indices": [0, 1, 3, 0]},
            {"node_indices": [0, 2, 0]}
        ]
    }

Node index 0 is always the depot; indices 1..n map to picking_ids[i-1].
"""
import datetime

import pytz

from odoo import _, fields
from odoo.exceptions import UserError

from . import delivery_windows
from . import google_client
from . import osrm_client
from . import ortools_client
from urllib.parse import urlsplit, urlunsplit


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


def _normalize_ortools_service_url(raw_url, simple_ortools):
    """
    Accept either:
    - Base URL (scheme://host:port)
    - Full endpoint URL ending with /optimize or /vrp

    And return a full endpoint URL matching the selected mode:
    - Simple: /optimize
    - Extended: /vrp
    """
    expected_path = "/optimize" if simple_ortools else "/vrp"
    url = (raw_url or "").strip()
    if not url:
        return ""

    parts = urlsplit(url)
    # If user pasted a bare host:port without scheme, urlsplit puts it in path. Try to recover.
    if not parts.scheme and not parts.netloc and parts.path and "://" not in url:
        url = "http://" + url
        parts = urlsplit(url)

    base = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
    if not base:
        # fallback to raw; better error downstream
        return url

    path = (parts.path or "").rstrip("/")
    # If user already set the correct endpoint, respect it.
    if path in ("/optimize", "/vrp") and path == expected_path:
        return urlunsplit((parts.scheme, parts.netloc, expected_path, parts.query, parts.fragment))

    # If user provided only base (no path) or unknown path, enforce expected.
    return base + expected_path


def _get_depot_partner(batch, depot_partner=None):
    """Warehouse address partner, then company partner."""
    if depot_partner:
        return depot_partner
    wh = batch.warehouse_id
    if wh and wh.partner_id:
        return wh.partner_id
    return batch.company_id.partner_id


def _partner_coords(partner):
    lat = partner.partner_latitude
    lng = partner.partner_longitude
    if lat is False or lat is None or lng is False or lng is None:
        return None
    try:
        latf = float(lat)
        lngf = float(lng)
    except (TypeError, ValueError):
        return None
    if latf == 0.0 and lngf == 0.0:
        return None
    # OSRM expects lon, lat
    return (lngf, latf)


def optimize_batch(
    env,
    batch,
    num_vehicles=1,
    use_duration=True,
    depot_partner=None,
    vehicle_capacity=None,
    vehicle_volume_capacity=None,
    max_stops_per_vehicle=None,
    max_route_duration_minutes=None,
):
    """
    Run OSRM + OR-Tools and apply ordering / batch splits.

    :param env: odoo.api.Environment
    :param batch: stock.picking.batch record
    :param num_vehicles: number of vehicles for VRP (>=1)
    :param use_duration: if True, optimize on duration matrix; else distance
    :param depot_partner: optional res.partner for depot coordinates
    :param vehicle_capacity: optional float capacity per vehicle (same for all if set)
    :param vehicle_volume_capacity: optional float volume capacity per vehicle (same for all if set)
    :param max_stops_per_vehicle: optional int hard limit
    :param max_route_duration_minutes: optional int hard limit
    """
    batch.ensure_one()
    if batch.state in ("done", "cancel"):
        raise UserError(_("No se puede optimizar un lote que está finalizado o cancelado."))

    pickings = batch.picking_ids.filtered(lambda p: p.state != "cancel")
    if not pickings:
        raise UserError(_("No hay traslados en este lote."))

    icp = env["ir.config_parameter"].sudo()
    base_url = icp.get_param("route_optimizer.osrm_url") or ""
    profile = icp.get_param("route_optimizer.osrm_profile") or "driving"
    ortools_url = icp.get_param("route_optimizer.ortools_url") or ""
    ortools_api_key = (icp.get_param("route_optimizer.ortools_api_key") or "").strip() or None
    timeout = int(icp.get_param("route_optimizer.timeout") or 60)
    # Default True: self-hosted /optimize services (FastAPI) expect locations + distance_matrix.
    # Set ir.config_parameter to "False" for the extended VRP JSON contract.
    simple_ortools = _param_bool(icp.get_param("route_optimizer.ortools_simple_api", "True"), default=True)
    ortools_url = _normalize_ortools_service_url(ortools_url, simple_ortools)

    depot = _get_depot_partner(batch, depot_partner=depot_partner)
    depot_coords = _partner_coords(depot)
    if not depot_coords:
        raise UserError(
            _("El depósito %(name)s no tiene coordenadas válidas (partner_latitude / partner_longitude).")
            % {"name": depot.display_name}
        )

    stops = []
    missing = []
    for picking in pickings:
        partner = picking._route_optimizer_delivery_partner()
        coords = _partner_coords(partner)
        if not coords:
            missing.append(picking.name or str(picking.id))
            continue
        stops.append({"picking": picking, "partner": partner, "coords": coords})

    if missing:
        raise UserError(
            _("Faltan coordenadas en los partners de entrega de los traslados: %s")
            % (", ".join(missing))
        )

    if not stops:
        raise UserError(_("No se pudieron construir paradas con coordenadas."))

    coordinates_lonlat = [depot_coords] + [s["coords"] for s in stops]

    picking_ids_order = [s["picking"].id for s in stops]
    demands_weight = [0]
    demands_volume = [0]
    for s in stops:
        w = s["picking"].shipping_weight or 0.0
        demands_weight.append(float(w))
        v = 0.0
        if "shipping_volume" in s["picking"]._fields:
            try:
                v = float(s["picking"].shipping_volume or 0.0)
            except (TypeError, ValueError):
                v = 0.0
        demands_volume.append(v)

    n = len(demands_weight)
    nv = max(1, int(num_vehicles or 1))
    if nv > len(stops):
        raise UserError(
            _("La cantidad de vehículos (%(v)s) no puede superar la cantidad de paradas (%(s)s).")
            % {"v": nv, "s": len(stops)}
        )

    # Google Route Optimization solves matrix + VRP in one call: no matrix step needed.
    solver_provider = (icp.get_param("route_optimizer.solver_provider") or "ortools").strip()
    if solver_provider == "google":
        return _run_google_solver(
            env,
            batch,
            stops,
            depot_coords,
            nv,
            vehicle_capacity,
            max_route_duration_minutes,
            icp,
            timeout,
        )

    table = _fetch_matrix(icp, coordinates_lonlat, timeout, base_url, profile)

    # Minimal HTTP API: {locations, distance_matrix} -> {optimized_route: [...]}
    if simple_ortools:
        return _run_simple_api(
            batch, table, n, nv, picking_ids_order, ortools_url, timeout, ortools_api_key
        )

    return _run_vrp_api(
        env,
        batch,
        table,
        n,
        nv,
        picking_ids_order,
        demands_weight,
        demands_volume,
        vehicle_capacity,
        vehicle_volume_capacity,
        max_stops_per_vehicle,
        max_route_duration_minutes,
        use_duration,
        ortools_url,
        timeout,
        ortools_api_key,
        icp,
        stops,
    )


def _fetch_matrix(icp, coordinates_lonlat, timeout, osrm_base_url, osrm_profile):
    """Dispatch matrix computation to the configured provider (osrm | google).

    Both providers return the same contract: {"durations": [[s]], "distances": [[m]]}.
    """
    provider = (icp.get_param("route_optimizer.matrix_provider") or "osrm").strip()
    if provider == "google":
        api_key = (icp.get_param("route_optimizer.google_api_key") or "").strip()
        if not api_key:
            raise UserError(
                _("Falta configurar la Google API key (Routes API) en Ajustes → Optimización de rutas.")
            )
        try:
            return google_client.fetch_route_matrix(api_key, coordinates_lonlat, timeout=timeout)
        except google_client.GoogleApiError as e:
            raise UserError(_("Error de Google Routes API: %s") % str(e)) from e
    try:
        return osrm_client.fetch_table(
            osrm_base_url, osrm_profile, coordinates_lonlat, timeout=timeout
        )
    except osrm_client.OsrmError as e:
        raise UserError(_("Error de OSRM: %s") % str(e)) from e


def _rfc3339(dt_aware):
    return dt_aware.astimezone(pytz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run_google_solver(
    env,
    batch,
    stops,
    depot_coords,
    nv,
    vehicle_capacity,
    max_route_duration_minutes,
    icp,
    timeout,
):
    """
    Solve the whole VRP with Google Route Optimization (optimizeTours).
    No matrix step: the API takes lat/lng per stop and computes travel itself.
    Reuses the existing apply/report helpers so downstream behavior is identical.
    """
    project_id = (icp.get_param("route_optimizer.google_project_id") or "").strip()
    sa_json = (icp.get_param("route_optimizer.google_service_account_json") or "").strip()
    if not project_id or not sa_json:
        raise UserError(
            _(
                "Para usar Google Route Optimization configurá el project id y el JSON "
                "del service account en Ajustes → Optimización de rutas."
            )
        )

    tz = pytz.timezone(delivery_windows._routing_timezone(env, batch))
    base_dt = None
    if batch.scheduled_date:
        base_dt = delivery_windows.localize_delivery_datetime(env, batch, batch.scheduled_date)
    if not base_dt:
        base_dt = datetime.datetime.now(tz)
    local_midnight = tz.localize(datetime.datetime(base_dt.year, base_dt.month, base_dt.day))

    try:
        route_start_hour = float(icp.get_param("route_optimizer.route_start_hour", "8") or 8)
    except (TypeError, ValueError):
        route_start_hour = 8.0
    route_start_hour = max(0.0, min(route_start_hour, 24.0))
    global_start = local_midnight + datetime.timedelta(seconds=int(route_start_hour * 3600))
    global_end = local_midnight + datetime.timedelta(days=1)

    try:
        service_time = int(icp.get_param("route_optimizer.service_time_seconds", "600") or 600)
    except (TypeError, ValueError):
        service_time = 600
    service_time = max(0, service_time)

    use_windows = delivery_windows._param_bool(
        icp.get_param("route_optimizer.use_delivery_windows", "True"), True
    ) and "delivery_time_preference" in env["res.partner"]._fields

    day_seconds = 24 * 3600
    infeasible = []
    shipments = []
    for s in stops:
        lon, lat = s["coords"]
        delivery = {"arrivalLocation": {"latitude": lat, "longitude": lon}}
        if service_time:
            delivery["duration"] = f"{service_time}s"

        if use_windows:
            partner = s["partner"]
            local_dt = delivery_windows.planned_delivery_datetime(s["picking"], batch, env)
            weekday = local_dt.weekday() if local_dt else local_midnight.weekday()
            windows_by_partner = delivery_windows._partner_delivery_windows(partner, weekday)
            intervals = delivery_windows._intervals_for_partner(
                partner, weekday, local_dt, windows_by_partner
            )
            if intervals is None:
                ref = s["picking"].name or str(s["picking"].id)
                infeasible.append(f"{ref} ({partner.display_name})")
                intervals = []
            full_day = len(intervals) == 1 and intervals[0][0] <= 0 and intervals[0][1] >= day_seconds
            if intervals and not full_day:
                delivery["timeWindows"] = [
                    {
                        "startTime": _rfc3339(local_midnight + datetime.timedelta(seconds=iv[0])),
                        "endTime": _rfc3339(
                            local_midnight + datetime.timedelta(seconds=min(iv[1], day_seconds))
                        ),
                    }
                    for iv in intervals
                ]

        shipment = {"deliveries": [delivery]}
        w = s["picking"].shipping_weight or 0.0
        if vehicle_capacity and vehicle_capacity > 0 and w > 0:
            shipment["loadDemands"] = {"weight": {"amount": str(int(round(float(w))))}}
        shipments.append(shipment)

    if infeasible:
        raise UserError(
            _(
                "No se puede optimizar: las siguientes entregas están planificadas en un día "
                "no laborable para clientes con preferencia «Días hábiles»:\n%(stops)s"
            )
            % {"stops": "\n".join(infeasible)}
        )

    depot_lon, depot_lat = depot_coords
    depot_location = {"latitude": depot_lat, "longitude": depot_lon}
    vehicle_tpl = {"startLocation": depot_location, "endLocation": depot_location}
    if vehicle_capacity and vehicle_capacity > 0:
        vehicle_tpl["loadLimits"] = {
            "weight": {"maxLoad": str(int(round(float(vehicle_capacity))))}
        }
    try:
        mmins = int(max_route_duration_minutes) if max_route_duration_minutes else 0
    except (TypeError, ValueError):
        mmins = 0
    if mmins > 0:
        vehicle_tpl["routeDurationLimit"] = {"maxDuration": f"{mmins * 60}s"}

    model = {
        "shipments": shipments,
        "vehicles": [dict(vehicle_tpl) for _i in range(nv)],
        "globalStartTime": _rfc3339(global_start),
        "globalEndTime": _rfc3339(global_end),
    }

    try:
        result = google_client.optimize_tours(
            project_id, sa_json, model, timeout=max(int(timeout), 120)
        )
    except google_client.GoogleApiError as e:
        raise UserError(_("Error de Google Route Optimization: %s") % str(e)) from e

    picking_ids_order = [s["picking"].id for s in stops]
    node_routes = []
    for r in result.get("routes") or []:
        nodes = [0]
        for visit in r.get("visits") or []:
            # shipmentIndex 0 is omitted by Google's JSON encoding.
            si = int(visit.get("shipmentIndex", 0))
            if 0 <= si < len(picking_ids_order):
                nodes.append(si + 1)
        nodes.append(0)
        node_routes.append({"node_indices": nodes})

    if not any(len(r["node_indices"]) > 2 for r in node_routes):
        raise UserError(_("Google Route Optimization no devolvió rutas con paradas."))

    skipped = result.get("skippedShipments") or []
    warn = ""
    if skipped:
        warn = " " + _("Advertencia: %(n)s entregas sin asignar (capacidad o ventana horaria).") % {
            "n": len(skipped)
        }

    if nv > 1:
        batch_orders = _apply_multi_vehicle_routes(env, batch, node_routes, picking_ids_order)
        msg = _("VRP aplicado (Google): %(v)s vehículos.") % {"v": nv} + warn
        for i, (b, pids) in enumerate(batch_orders):
            if i == 0:
                _write_optimization_result(b, msg, pids)
            else:
                _write_optimization_result(b, _("Ruta optimizada para este vehículo (Google)."), pids)
        return {"message": msg}

    ordered_ids = [
        picking_ids_order[i - 1] for i in node_routes[0]["node_indices"] if i != 0
    ]
    _apply_order_single_batch(batch, ordered_ids)
    msg = _("Ruta optimizada con Google (%(n)s paradas).") % {"n": len(ordered_ids)} + warn
    _write_optimization_result(batch, msg, ordered_ids)
    return {"message": msg}


def _run_simple_api(batch, table, n, nv, picking_ids_order, ortools_url, timeout, ortools_api_key):
    """Call the minimal /optimize endpoint (single vehicle, distance matrix)."""
    if nv > 1:
        raise UserError(
            _(
                "Simple OR-Tools API only supports one vehicle. "
                "Set Vehicles to 1 or disable Simple OR-Tools API in settings."
            )
        )
    dist_m = table["distances"]
    if len(dist_m) != n or any(len(row) != n for row in dist_m):
        raise UserError(_("El tamaño de la matriz de distancias de OSRM no coincide con la cantidad de nodos."))
    matrix_int = _matrix_to_int_meters(dist_m)
    depot_label = "__ODOO_DEPOT__"
    locations = [depot_label] + [f"P{pid}" for pid in picking_ids_order]
    try:
        result = ortools_client.solve_simple_distance_api(
            ortools_url, locations, matrix_int, timeout=timeout, api_key=ortools_api_key
        )
    except ortools_client.OrtoolsServiceError as e:
        raise UserError(_("Error del servicio OR-Tools: %s") % str(e)) from e
    ordered_ids = _ordered_pickings_from_simple_route(result, depot_label, picking_ids_order)
    _apply_order_single_batch(batch, ordered_ids)
    msg = _("Ruta optimizada (%(n)s paradas, distancia).") % {"n": len(ordered_ids)}
    _write_optimization_result(batch, msg, ordered_ids)
    return {"message": msg}


def _build_vrp_payload(
    table,
    n,
    nv,
    picking_ids_order,
    demands_weight,
    demands_volume,
    vehicle_capacity,
    vehicle_volume_capacity,
    max_stops_per_vehicle,
    max_route_duration_minutes,
    use_duration,
):
    """Assemble the JSON payload for the extended /vrp endpoint."""
    matrix = table["durations"] if use_duration else table["distances"]
    metric = "duration" if use_duration else "distance"

    if len(matrix) != n or any(len(row) != n for row in matrix):
        raise UserError(_("El tamaño de la matriz de OSRM no coincide con la cantidad de nodos."))

    if vehicle_capacity and vehicle_capacity > 0:
        capacities_weight = [float(vehicle_capacity)] * nv
    else:
        total_demand = sum(demands_weight[1:])
        capacities_weight = [max(total_demand * 2, 1.0)] * nv

    if vehicle_volume_capacity and vehicle_volume_capacity > 0:
        capacities_volume = [float(vehicle_volume_capacity)] * nv
    else:
        total_vol = sum(demands_volume[1:])
        capacities_volume = [max(total_vol * 2, 0.000001)] * nv

    payload = {
        "version": 1,
        "num_vehicles": nv,
        "depot_index": 0,
        "matrix": matrix,
        "matrix_metric": metric,
        # Backward-compatible keys (weight)
        "demands": demands_weight,
        "vehicle_capacities": capacities_weight,
        # Explicit keys (recommended)
        "demands_weight": demands_weight,
        "vehicle_capacities_weight": capacities_weight,
        "demands_volume": demands_volume,
        "vehicle_capacities_volume": capacities_volume,
        "picking_ids": picking_ids_order,
    }

    try:
        ms = int(max_stops_per_vehicle) if max_stops_per_vehicle else 0
    except (TypeError, ValueError):
        ms = 0
    if ms > 0:
        payload["max_stops_per_vehicle"] = ms

    try:
        mmins = int(max_route_duration_minutes) if max_route_duration_minutes else 0
    except (TypeError, ValueError):
        mmins = 0
    if mmins > 0:
        payload["max_route_duration_seconds"] = mmins * 60

    return payload, metric


def _run_vrp_api(
    env,
    batch,
    table,
    n,
    nv,
    picking_ids_order,
    demands_weight,
    demands_volume,
    vehicle_capacity,
    vehicle_volume_capacity,
    max_stops_per_vehicle,
    max_route_duration_minutes,
    use_duration,
    ortools_url,
    timeout,
    ortools_api_key,
    icp,
    stops,
):
    """Call the extended /vrp endpoint and apply results to the batch."""
    payload, metric = _build_vrp_payload(
        table,
        n,
        nv,
        picking_ids_order,
        demands_weight,
        demands_volume,
        vehicle_capacity,
        vehicle_volume_capacity,
        max_stops_per_vehicle,
        max_route_duration_minutes,
        use_duration,
    )

    # OCA stock_partner_delivery_window: time windows per stop (requires duration matrix).
    if use_duration:
        time_payload = delivery_windows.build_vrp_time_payload(env, batch, stops, icp)
        if time_payload:
            payload.update(time_payload)

    try:
        result = ortools_client.solve_vrp(ortools_url, payload, timeout=timeout, api_key=ortools_api_key)
    except ortools_client.OrtoolsServiceError as e:
        raise UserError(_("Error del servicio OR-Tools: %s") % str(e)) from e

    routes = result.get("routes") or []

    if nv > 1 and routes:
        batch_orders = _apply_multi_vehicle_routes(env, batch, routes, picking_ids_order)
        msg = _("VRP aplicado: %(v)s vehículos, métrica %(metric)s.") % {"v": nv, "metric": metric}
        for i, (b, pids) in enumerate(batch_orders):
            if i == 0:
                _write_optimization_result(b, msg, pids)
            else:
                _write_optimization_result(b, _("Ruta optimizada para este vehículo (VRP)."), pids)
        return {"message": msg}

    if result.get("ordered_picking_ids"):
        ordered_ids = [int(x) for x in result["ordered_picking_ids"]]
        _apply_order_single_batch(batch, ordered_ids)
        msg = _("Ruta optimizada (%(n)s paradas, %(metric)s).") % {"n": len(ordered_ids), "metric": metric}
        _write_optimization_result(batch, msg, ordered_ids)
        return {"message": msg}

    if routes:
        nodes = routes[0].get("node_indices") or routes[0].get("nodes") or []
        ordered_ids = [
            picking_ids_order[idx - 1]
            for idx in nodes
            if idx != 0 and 1 <= idx < len(picking_ids_order) + 1
        ]
        if ordered_ids:
            _apply_order_single_batch(batch, ordered_ids)
            msg = _("Ruta optimizada (%(n)s paradas, %(metric)s).") % {"n": len(ordered_ids), "metric": metric}
            _write_optimization_result(batch, msg, ordered_ids)
            return {"message": msg}

    raise UserError(
        _("No se pudo interpretar la respuesta de OR-Tools. Se esperaba ordered_picking_ids o routes.")
    )


def _matrix_to_int_meters(matrix):
    """OSRM returns floats and nulls for unreachable pairs; OR-Tools demo expects int meters."""
    big = 999_999_999
    out = []
    for row in matrix:
        r = []
        for x in row:
            if x is None:
                r.append(big)
            else:
                try:
                    r.append(int(round(float(x))))
                except (TypeError, ValueError):
                    r.append(big)
        out.append(r)
    return out


def _ordered_pickings_from_simple_route(result, depot_label, picking_ids_order):
    """Parse optimized_route using labels P{picking.id} and depot_label."""
    route = result.get("optimized_route")
    if not route:
        route = result.get("route")
    if not route:
        raise UserError(
            _("No se pudo interpretar la respuesta de OR-Tools: falta optimized_route (o route).")
        )
    label_to_id = {f"P{pid}": pid for pid in picking_ids_order}
    ordered_ids = []
    seen = set()
    for label in route:
        if label == depot_label:
            continue
        pid = label_to_id.get(label)
        if pid is not None and pid not in seen:
            ordered_ids.append(pid)
            seen.add(pid)
    for pid in picking_ids_order:
        if pid not in seen:
            ordered_ids.append(pid)
    return ordered_ids


def _format_visit_order_display(env, ordered_picking_ids):
    """Human-readable numbered lines for the batch form (visit order)."""
    if not ordered_picking_ids:
        return ""
    # Single browse + exists() so the ORM prefetches all records in one query.
    existing = env["stock.picking"].browse(ordered_picking_ids).exists()
    by_id = {p.id: p for p in existing}
    lines = []
    pos = 0
    for pid in ordered_picking_ids:
        picking = by_id.get(pid)
        if not picking:
            continue
        pos += 1
        partner = picking.partner_id.display_name if picking.partner_id else ""
        ref = picking.name or str(picking.id)
        lines.append(
            _("%(pos)s. %(picking)s — %(partner)s")
            % {"pos": pos, "picking": ref, "partner": partner}
        )
    return "\n".join(lines)


def _write_optimization_result(batch, message, ordered_picking_ids):
    """Persist short status + numbered visit list for the user."""
    batch.write(
        {
            "route_optimizer_last_message": message,
            "route_optimizer_visit_summary": _format_visit_order_display(
                batch.env, ordered_picking_ids
            ),
        }
    )


def _apply_order_single_batch(batch, ordered_picking_ids):
    """Set batch_sequence on pickings following optimized order."""
    by_id = {p.id: p for p in batch.picking_ids}
    seq = 10
    for pid in ordered_picking_ids:
        picking = by_id.get(pid)
        if picking:
            if picking.batch_sequence != seq:
                picking.write({"batch_sequence": seq})
            seq += 10


def _apply_multi_vehicle_routes(env, original_batch, routes, picking_ids_order):
    """Assign pickings to batches; return [(batch, ordered_picking_ids), ...] in stable order."""
    vehicle_routes = []
    for r in routes:
        nodes = r.get("node_indices") or r.get("nodes") or []
        pids = []
        for idx in nodes:
            if idx == 0:
                continue
            if 1 <= idx < len(picking_ids_order) + 1:
                pids.append(picking_ids_order[idx - 1])
        vehicle_routes.append(pids)

    # Some solvers may return empty routes for unused vehicles (e.g., [0, 0]).
    # Keep only routes that actually carry at least one stop.
    vehicle_routes = [r for r in vehicle_routes if r]
    if not vehicle_routes:
        raise UserError(_("OR-Tools devolvió rutas vacías."))

    batch_orders = []

    # Move secondary vehicles to new batches first so original batch only keeps the first non-empty route.
    for extra_pick_ids in vehicle_routes[1:]:
        if not extra_pick_ids:
            continue
        new_batch = env["stock.picking.batch"].create(
            {
                "picking_type_id": original_batch.picking_type_id.id,
                "company_id": original_batch.company_id.id,
                "user_id": original_batch.user_id.id,
            }
        )
        seq = 10
        for pid in extra_pick_ids:
            picking = env["stock.picking"].browse(pid)
            picking.write({"batch_id": new_batch.id, "batch_sequence": seq})
            seq += 10
        if original_batch.state == "in_progress" and new_batch.state == "draft":
            new_batch.action_confirm()
        batch_orders.append((new_batch, extra_pick_ids))

    first = vehicle_routes[0]
    _apply_order_single_batch(original_batch, first)
    return [(original_batch, first)] + batch_orders
