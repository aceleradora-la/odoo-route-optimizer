"""
OR-Tools VRP microservice for Odoo Route Optimizer.

Deploy with Docker (see DEPLOYMENT_MANUAL_SECURE.md). Auth: X-API-KEY header.
"""
from __future__ import annotations

import os
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, Security
from fastapi.security.api_key import APIKeyHeader
from ortools.constraint_solver import pywrapcp, routing_enums_pb2
from pydantic import BaseModel, Field

API_KEY_NAME = "X-API-KEY"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

app = FastAPI(title="Odoo Route Optimizer OR-Tools")


def _get_configured_api_key() -> str:
    api_key = (os.getenv("API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("API_KEY no configurada")
    return api_key


async def get_api_key(header_value: str = Security(api_key_header)):
    try:
        configured = _get_configured_api_key()
    except RuntimeError:
        raise HTTPException(status_code=500, detail="API_KEY no configurada en el servidor")
    if header_value == configured:
        return header_value
    raise HTTPException(status_code=403, detail="Acceso denegado: API KEY inválida")


class RouteRequest(BaseModel):
    locations: List[str]
    distance_matrix: List[List[int]]
    num_vehicles: int = Field(default=1, ge=1)


class VrpRequest(BaseModel):
    version: int = 1
    num_vehicles: int = Field(default=1, ge=1)
    depot_index: int = Field(default=0, ge=0)
    matrix: List[List[float]]
    matrix_metric: str = "distance"
    demands: List[float] = []
    vehicle_capacities: List[float] = []
    demands_weight: List[float] = []
    vehicle_capacities_weight: List[float] = []
    demands_volume: List[float] = []
    vehicle_capacities_volume: List[float] = []
    picking_ids: List[int] = []
    max_stops_per_vehicle: int = 0
    max_route_duration_seconds: int = 0
    time_windows: List[List[int]] = []
    time_windows_list: List[List[List[int]]] = []
    route_start_seconds: int = 0
    service_times: List[int] = []


def _apply_time_windows(
    routing,
    manager,
    time_dimension,
    node_idx: int,
    intervals: List[List[int]],
):
    """Apply one or more [start, end] intervals (seconds) at a node."""
    index = manager.NodeToIndex(node_idx)
    cumul = time_dimension.CumulVar(index)
    valid = []
    for tw in intervals:
        if not tw or len(tw) != 2:
            continue
        start, end = int(tw[0]), int(tw[1])
        if end < start:
            continue
        valid.append((start, end))
    if not valid:
        return
    if len(valid) == 1:
        start, end = valid[0]
        cumul.SetRange(start, end)
        return
    solver = routing.solver()
    clauses = []
    for start, end in valid:
        clauses.append(
            solver.And(
                cumul >= start,
                cumul <= end,
            )
        )
    solver.Add(solver.Or(clauses))


@app.post("/optimize")
async def optimize(request: RouteRequest, token: str = Depends(get_api_key)):
    n = len(request.distance_matrix)
    if n == 0 or any(len(row) != n for row in request.distance_matrix):
        raise HTTPException(status_code=422, detail="distance_matrix debe ser NxN")
    if len(request.locations) != n:
        raise HTTPException(status_code=422, detail="locations debe tener el mismo largo que distance_matrix")

    num_vehicles = int(request.num_vehicles or 1)
    depot = 0

    manager = pywrapcp.RoutingIndexManager(n, num_vehicles, depot)
    routing = pywrapcp.RoutingModel(manager)

    def distance_callback(from_index, to_index):
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        return int(request.distance_matrix[from_node][to_node])

    transit_callback_index = routing.RegisterTransitCallback(distance_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_callback_index)

    search_parameters = pywrapcp.DefaultRoutingSearchParameters()
    search_parameters.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    )

    solution = routing.SolveWithParameters(search_parameters)
    if not solution:
        return {"success": False, "error": "No solution found"}

    if num_vehicles > 1:
        routes: List[dict] = []
        for v in range(num_vehicles):
            index = routing.Start(v)
            nodes = []
            while not routing.IsEnd(index):
                nodes.append(manager.IndexToNode(index))
                index = solution.Value(routing.NextVar(index))
            nodes.append(manager.IndexToNode(index))
            routes.append({"node_indices": nodes})
        return {"success": True, "routes": routes, "total_cost": int(solution.ObjectiveValue())}

    route_labels = []
    index = routing.Start(0)
    while not routing.IsEnd(index):
        route_labels.append(request.locations[manager.IndexToNode(index)])
        index = solution.Value(routing.NextVar(index))
    route_labels.append(request.locations[manager.IndexToNode(index)])

    return {
        "success": True,
        "optimized_route": route_labels,
        "total_distance": int(solution.ObjectiveValue()),
    }


@app.post("/vrp")
async def vrp(request: VrpRequest, token: str = Depends(get_api_key)):
    n = len(request.matrix)
    if n == 0 or any(len(row) != n for row in request.matrix):
        raise HTTPException(status_code=422, detail="matrix debe ser NxN")
    if request.depot_index != 0:
        raise HTTPException(status_code=422, detail="Solo se soporta depot_index=0")

    num_vehicles = int(request.num_vehicles or 1)
    depot = 0
    service_times = request.service_times or [0] * n
    if len(service_times) < n:
        service_times = list(service_times) + [0] * (n - len(service_times))
    service_times = [int(max(0, x)) for x in service_times[:n]]

    manager = pywrapcp.RoutingIndexManager(n, num_vehicles, depot)
    routing = pywrapcp.RoutingModel(manager)

    def cost_callback(from_index, to_index):
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        val = request.matrix[from_node][to_node]
        try:
            return int(round(float(val)))
        except Exception:
            return 999_999_999

    transit_callback_index = routing.RegisterTransitCallback(cost_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_callback_index)

    demands_weight = request.demands_weight or request.demands
    caps_weight = request.vehicle_capacities_weight or request.vehicle_capacities
    if demands_weight and caps_weight:
        if len(demands_weight) != n:
            raise HTTPException(status_code=422, detail="demands_weight debe tener largo N")
        if len(caps_weight) != num_vehicles:
            raise HTTPException(
                status_code=422, detail="vehicle_capacities_weight debe tener largo num_vehicles"
            )

        def demand_callback(from_index):
            node = manager.IndexToNode(from_index)
            try:
                return int(round(float(demands_weight[node])))
            except Exception:
                return 0

        demand_callback_index = routing.RegisterUnaryTransitCallback(demand_callback)
        routing.AddDimensionWithVehicleCapacity(
            demand_callback_index,
            0,
            [int(round(float(c))) for c in caps_weight],
            True,
            "Capacity",
        )

    if request.demands_volume and request.vehicle_capacities_volume:
        if len(request.demands_volume) != n:
            raise HTTPException(status_code=422, detail="demands_volume debe tener largo N")
        if len(request.vehicle_capacities_volume) != num_vehicles:
            raise HTTPException(
                status_code=422,
                detail="vehicle_capacities_volume debe tener largo num_vehicles",
            )

        def volume_callback(from_index):
            node = manager.IndexToNode(from_index)
            try:
                return int(round(float(request.demands_volume[node])))
            except Exception:
                return 0

        vol_cb = routing.RegisterUnaryTransitCallback(volume_callback)
        routing.AddDimensionWithVehicleCapacity(
            vol_cb,
            0,
            [int(round(float(c))) for c in request.vehicle_capacities_volume],
            True,
            "CapacityVolume",
        )

    if request.max_stops_per_vehicle and request.max_stops_per_vehicle > 0:

        def stop_callback(from_index):
            node = manager.IndexToNode(from_index)
            return 0 if node == 0 else 1

        stop_cb = routing.RegisterUnaryTransitCallback(stop_callback)
        routing.AddDimensionWithVehicleCapacity(
            stop_cb,
            0,
            [int(request.max_stops_per_vehicle)] * num_vehicles,
            True,
            "Stops",
        )

    use_time = bool(request.time_windows or request.time_windows_list)
    if use_time:
        day_horizon = 24 * 3600
        route_horizon = (
            int(request.max_route_duration_seconds)
            if request.max_route_duration_seconds
            else day_horizon
        )
        route_horizon = max(route_horizon, day_horizon)

        def time_callback(from_index, to_index):
            from_node = manager.IndexToNode(from_index)
            travel = cost_callback(from_index, to_index)
            return travel + service_times[from_node]

        time_cb = routing.RegisterTransitCallback(time_callback)
        # Large slack so vehicles can wait until a delivery window opens.
        routing.AddDimension(
            time_cb,
            day_horizon,
            route_horizon,
            False,
            "Time",
        )
        time_dimension = routing.GetDimensionOrDie("Time")
        route_start = int(request.route_start_seconds or 0)

        tw_list = request.time_windows_list
        if tw_list and len(tw_list) == n:
            for node_idx, intervals in enumerate(tw_list):
                _apply_time_windows(routing, manager, time_dimension, node_idx, intervals)
        elif request.time_windows and len(request.time_windows) == n:
            for node_idx, tw in enumerate(request.time_windows):
                if tw and len(tw) == 2:
                    _apply_time_windows(routing, manager, time_dimension, node_idx, [tw])

        for v in range(num_vehicles):
            start_index = routing.Start(v)
            time_dimension.CumulVar(start_index).SetRange(route_start, route_start)
            if request.max_route_duration_seconds and request.max_route_duration_seconds > 0:
                end_index = routing.End(v)
                time_dimension.CumulVar(end_index).SetMax(int(request.max_route_duration_seconds))

    search_parameters = pywrapcp.DefaultRoutingSearchParameters()
    search_parameters.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    )

    solution = routing.SolveWithParameters(search_parameters)
    if not solution:
        return {"success": False, "error": "No solution found"}

    routes: List[dict] = []
    for v in range(num_vehicles):
        index = routing.Start(v)
        nodes = []
        while not routing.IsEnd(index):
            nodes.append(manager.IndexToNode(index))
            index = solution.Value(routing.NextVar(index))
        nodes.append(manager.IndexToNode(index))
        routes.append({"node_indices": nodes})

    return {"success": True, "routes": routes, "total_cost": int(solution.ObjectiveValue())}
