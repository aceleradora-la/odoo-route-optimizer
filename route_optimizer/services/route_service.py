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
        "picking_ids": [int, ...]        # same order as matrix columns after depot
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
from odoo import _
from odoo.exceptions import UserError

from . import osrm_client
from . import ortools_client


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


def optimize_batch(env, batch, num_vehicles=1, use_duration=True, depot_partner=None, vehicle_capacity=None):
    """
    Run OSRM + OR-Tools and apply ordering / batch splits.

    :param env: odoo.api.Environment
    :param batch: stock.picking.batch record
    :param num_vehicles: number of vehicles for VRP (>=1)
    :param use_duration: if True, optimize on duration matrix; else distance
    :param depot_partner: optional res.partner for depot coordinates
    :param vehicle_capacity: optional float capacity per vehicle (same for all if set)
    """
    batch.ensure_one()
    if batch.state in ("done", "cancel"):
        raise UserError(_("Cannot optimize a batch that is done or cancelled."))

    pickings = batch.picking_ids.filtered(lambda p: p.state != "cancel")
    if not pickings:
        raise UserError(_("There are no transfers in this batch."))

    icp = env["ir.config_parameter"].sudo()
    base_url = icp.get_param("route_optimizer.osrm_url") or ""
    profile = icp.get_param("route_optimizer.osrm_profile") or "driving"
    ortools_url = icp.get_param("route_optimizer.ortools_url") or ""
    timeout = int(icp.get_param("route_optimizer.timeout") or 60)
    # Default True: self-hosted /optimize services (FastAPI) expect locations + distance_matrix.
    # Set ir.config_parameter to "False" for the extended VRP JSON contract.
    simple_ortools = icp.get_param("route_optimizer.ortools_simple_api", "True") == "True"

    depot = _get_depot_partner(batch, depot_partner=depot_partner)
    depot_coords = _partner_coords(depot)
    if not depot_coords:
        raise UserError(
            _("Depot partner %(name)s is missing valid partner_latitude / partner_longitude.")
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
            _("Missing coordinates for delivery partners on transfers: %s")
            % (", ".join(missing))
        )

    if not stops:
        raise UserError(_("No stops with coordinates could be built."))

    coordinates_lonlat = [depot_coords] + [s["coords"] for s in stops]

    try:
        table = osrm_client.fetch_table(base_url, profile, coordinates_lonlat, timeout=timeout)
    except osrm_client.OsrmError as e:
        raise UserError(_("OSRM error: %s") % str(e)) from e

    picking_ids_order = [s["picking"].id for s in stops]
    demands = [0]
    for s in stops:
        w = s["picking"].shipping_weight or 0.0
        demands.append(float(w))

    n = len(demands)
    nv = max(1, int(num_vehicles or 1))
    if nv > len(stops):
        raise UserError(
            _("Number of vehicles (%(v)s) cannot exceed the number of delivery stops (%(s)s).")
            % {"v": nv, "s": len(stops)}
        )

    # Minimal HTTP API: {locations, distance_matrix} -> {optimized_route: [...]}
    if simple_ortools:
        if nv > 1:
            raise UserError(
                _(
                    "Simple OR-Tools API only supports one vehicle. "
                    "Set Vehicles to 1 or disable Simple OR-Tools API in settings."
                )
            )
        dist_m = table["distances"]
        if len(dist_m) != n or any(len(row) != n for row in dist_m):
            raise UserError(_("OSRM distance matrix size does not match the number of nodes."))
        matrix_int = _matrix_to_int_meters(dist_m)
        depot_label = "__ODOO_DEPOT__"
        locations = [depot_label] + [f"P{pid}" for pid in picking_ids_order]
        try:
            result = ortools_client.solve_simple_distance_api(
                ortools_url, locations, matrix_int, timeout=timeout
            )
        except ortools_client.OrtoolsServiceError as e:
            raise UserError(_("OR-Tools service error: %s") % str(e)) from e
        ordered_ids = _ordered_pickings_from_simple_route(
            result, depot_label, picking_ids_order
        )
        _apply_order_single_batch(batch, ordered_ids)
        msg = _("Route optimized (%(n)s stops, distance).") % {"n": len(ordered_ids)}
        batch.write({"route_optimizer_last_message": msg})
        return {"message": msg}

    matrix = table["durations"] if use_duration else table["distances"]
    metric = "duration" if use_duration else "distance"

    if len(matrix) != n or any(len(row) != n for row in matrix):
        raise UserError(_("OSRM matrix size does not match the number of nodes."))

    capacities = []
    if vehicle_capacity and vehicle_capacity > 0:
        capacities = [float(vehicle_capacity)] * nv
    else:
        # Large default so capacity does not bind unless service requires it
        total_demand = sum(demands[1:])
        capacities = [max(total_demand * 2, 1.0)] * nv

    payload = {
        "version": 1,
        "num_vehicles": nv,
        "depot_index": 0,
        "matrix": matrix,
        "matrix_metric": metric,
        "demands": demands,
        "vehicle_capacities": capacities,
        "picking_ids": picking_ids_order,
    }

    try:
        result = ortools_client.solve_vrp(ortools_url, payload, timeout=timeout)
    except ortools_client.OrtoolsServiceError as e:
        raise UserError(_("OR-Tools service error: %s") % str(e)) from e

    routes = result.get("routes") or []

    # Multi-vehicle: prefer structured routes when num_vehicles > 1
    if nv > 1 and routes:
        _apply_multi_vehicle_routes(env, batch, routes, picking_ids_order)
        msg = _("VRP applied: %(v)s vehicles, metric %(metric)s.") % {"v": nv, "metric": metric}
        batch.write({"route_optimizer_last_message": msg})
        return {"message": msg}

    if result.get("ordered_picking_ids"):
        ordered_ids = [int(x) for x in result["ordered_picking_ids"]]
        _apply_order_single_batch(batch, ordered_ids)
        msg = _("Route optimized (%(n)s stops, %(metric)s).") % {"n": len(ordered_ids), "metric": metric}
        batch.write({"route_optimizer_last_message": msg})
        return {"message": msg}

    if routes:
        nodes = routes[0].get("node_indices") or routes[0].get("nodes") or []
        ordered_ids = []
        for idx in nodes:
            if idx == 0:
                continue
            if 1 <= idx < len(picking_ids_order) + 1:
                ordered_ids.append(picking_ids_order[idx - 1])
        if ordered_ids:
            _apply_order_single_batch(batch, ordered_ids)
            msg = _("Route optimized (%(n)s stops, %(metric)s).") % {"n": len(ordered_ids), "metric": metric}
            batch.write({"route_optimizer_last_message": msg})
            return {"message": msg}

    raise UserError(
        _("Could not interpret OR-Tools response. Expected ordered_picking_ids or routes.")
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
            _("Could not interpret OR-Tools response: missing optimized_route (or route).")
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


def _apply_order_single_batch(batch, ordered_picking_ids):
    """Set batch_sequence on pickings following optimized order."""
    seq = 10
    for pid in ordered_picking_ids:
        picking = batch.picking_ids.filtered(lambda p, pid=pid: p.id == pid)
        if picking:
            picking.write({"batch_sequence": seq})
            seq += 10


def _apply_multi_vehicle_routes(env, original_batch, routes, picking_ids_order):
    """Assign pickings to batches: first route keeps original_batch; others get new batches."""
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

    if not vehicle_routes or not vehicle_routes[0]:
        raise UserError(_("Empty routes from OR-Tools."))

    # Move secondary vehicles to new batches first so original batch only keeps route 0.
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

    first = vehicle_routes[0]
    _apply_order_single_batch(original_batch, first)
