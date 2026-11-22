"""
OR-Tools VRP microservice for Odoo Route Optimizer.

Deploy with Docker (see DEPLOYMENT_MANUAL_SECURE.md). Auth: X-API-KEY header.
"""
from __future__ import annotations

import logging
import os
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, Security
from fastapi.security.api_key import APIKeyHeader
from ortools.constraint_solver import pywrapcp, routing_enums_pb2
from pydantic import BaseModel, Field

API_KEY_NAME = "X-API-KEY"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("ortools_service")

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
    # 1 = parada urgente: el solver intenta visitarla entre las primeras de su ruta.
    priorities: List[int] = []
    # 1 = la parada es un retiro: la carga sube en vez de bajar. Vacío = todo entregas.
    pickups: List[int] = []
    # Descanso obligatorio del chofer (segundos desde medianoche); 0 = sin descanso.
    break_earliest_seconds: int = 0
    break_latest_seconds: int = 0
    break_duration_seconds: int = 0


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
    logger.info("POST /optimize: %d locations, %d vehicles", len(request.locations), request.num_vehicles)
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

    # Con restricciones blandas o descansos, la primera solución suele ser mejorable:
    # se le da un tiempo corto de búsqueda local.
    if urgent or has_break:
        search_parameters.local_search_metaheuristic = (
            routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
        )
        search_parameters.time_limit.FromSeconds(3 if n <= 60 else 8)

    solution = routing.SolveWithParameters(search_parameters)
    if not solution:
        logger.warning("/optimize: no solution found for %d locations", n)
        return {"success": False, "error": "No solution found"}

    logger.info("/optimize: solved, total_distance=%d", int(solution.ObjectiveValue()))
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
    logger.info(
        "POST /vrp: %d nodes, %d vehicles, metric=%s, time_windows=%s",
        len(request.matrix),
        request.num_vehicles,
        request.matrix_metric,
        bool(request.time_windows or request.time_windows_list),
    )
    n = len(request.matrix)
    if n == 0 or any(len(row) != n for row in request.matrix):
        raise HTTPException(status_code=422, detail="matrix debe ser NxN")
    # NOTE: variable depot_index is not yet implemented; only index 0 is supported.
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

    pickups = [bool(x) for x in (request.pickups or [])]
    if pickups and len(pickups) != n:
        raise HTTPException(status_code=422, detail="pickups debe tener largo N")
    has_pickups = any(pickups)

    def add_capacity(name, demands, capacities):
        """Tope de carga por vehículo.

        Solo entregas: la carga acumulada es lo que salió del depósito. Con retiros,
        el vehículo sale con todo lo que entrega, baja en cada entrega y sube en cada
        retiro, y el tope vale en cada tramo: un retiro temprano solo entra si ya
        hay lugar.
        """
        if len(demands) != n:
            raise HTTPException(status_code=422, detail="demands de %s debe tener largo N" % name)
        if len(capacities) != num_vehicles:
            raise HTTPException(
                status_code=422, detail="capacidades de %s debe tener largo num_vehicles" % name
            )
        amounts = []
        for value in demands:
            try:
                amounts.append(int(round(float(value))))
            except Exception:
                amounts.append(0)
        caps = [int(round(float(c))) for c in capacities]

        if not has_pickups:
            cb = routing.RegisterUnaryTransitCallback(lambda index: amounts[manager.IndexToNode(index)])
            routing.AddDimensionWithVehicleCapacity(cb, 0, caps, True, name)
            return

        def delivered(index):
            node = manager.IndexToNode(index)
            return 0 if pickups[node] else amounts[node]

        def load_change(index):
            node = manager.IndexToNode(index)
            return amounts[node] if pickups[node] else -amounts[node]

        out_cb = routing.RegisterUnaryTransitCallback(delivered)
        routing.AddDimensionWithVehicleCapacity(out_cb, 0, caps, True, name + "Out")
        load_cb = routing.RegisterUnaryTransitCallback(load_change)
        routing.AddDimensionWithVehicleCapacity(load_cb, 0, caps, False, name)
        out_dim = routing.GetDimensionOrDie(name + "Out")
        load_dim = routing.GetDimensionOrDie(name)
        solver = routing.solver()
        for v in range(num_vehicles):
            # Sale cargado exactamente con lo que va a entregar.
            solver.Add(load_dim.CumulVar(routing.Start(v)) == out_dim.CumulVar(routing.End(v)))

    demands_weight = request.demands_weight or request.demands
    caps_weight = request.vehicle_capacities_weight or request.vehicle_capacities
    if demands_weight and caps_weight:
        add_capacity("Capacity", demands_weight, caps_weight)

    if request.demands_volume and request.vehicle_capacities_volume:
        add_capacity("CapacityVolume", request.demands_volume, request.vehicle_capacities_volume)

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

    has_break = request.break_duration_seconds > 0 and request.matrix_metric == "duration"
    # La dimensión de tiempo hace falta para las ventanas, para el descanso y para que
    # el tope de duración se cumpla aunque no haya ventanas cargadas.
    use_time = bool(
        request.time_windows
        or request.time_windows_list
        or has_break
        or (request.max_route_duration_seconds > 0 and request.matrix_metric == "duration")
    )
    time_dimension = None
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
                time_dimension.CumulVar(end_index).SetMax(
                    route_start + int(request.max_route_duration_seconds)
                )

        if has_break:
            # Un descanso por vehículo, que empieza dentro de la franja indicada. No corta
            # la atención de una parada: cae entre paradas o durante un viaje.
            earliest = max(int(request.break_earliest_seconds), route_start)
            latest = max(int(request.break_latest_seconds), earliest)
            visit_transit = [0] * routing.Size()
            for index in range(routing.Size()):
                visit_transit[index] = service_times[manager.IndexToNode(index)]
            solver = routing.solver()
            for v in range(num_vehicles):
                interval = solver.FixedDurationIntervalVar(
                    earliest, latest, int(request.break_duration_seconds), False, f"break_{v}"
                )
                time_dimension.SetBreakIntervalsOfVehicle([interval], v, visit_transit)

    urgent = [
        node
        for node, value in enumerate(request.priorities[:n])
        if node != depot and int(value or 0) > 0
    ]
    if urgent:
        # Posición de cada parada en su ruta (1 = primera). A las urgentes se les pide
        # quedar entre las primeras; cada lugar de más cuesta como un tramo promedio, así
        # el solver las adelanta salvo que el desvío sea desproporcionado.
        def position_callback(from_index):
            return 0 if manager.IndexToNode(from_index) == depot else 1

        position_cb = routing.RegisterUnaryTransitCallback(position_callback)
        routing.AddDimension(position_cb, 0, n, True, "Position")
        position_dimension = routing.GetDimensionOrDie("Position")
        arcs = [
            float(request.matrix[i][j]) for i in range(n) for j in range(n) if i != j
        ]
        penalty = max(1, int(sum(arcs) / max(len(arcs), 1)))
        early_slots = max(0, -(-len(urgent) // num_vehicles) - 1)
        for node in urgent:
            position_dimension.SetCumulVarSoftUpperBound(
                manager.NodeToIndex(node), early_slots, penalty
            )

    search_parameters = pywrapcp.DefaultRoutingSearchParameters()
    search_parameters.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    )

    # Con restricciones blandas o descansos, la primera solución suele ser mejorable:
    # se le da un tiempo corto de búsqueda local.
    if urgent or has_break:
        search_parameters.local_search_metaheuristic = (
            routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
        )
        search_parameters.time_limit.FromSeconds(3 if n <= 60 else 8)

    solution = routing.SolveWithParameters(search_parameters)
    if not solution:
        logger.warning("/vrp: no solution found for %d nodes, %d vehicles", n, num_vehicles)
        return {"success": False, "error": "No solution found"}

    logger.info("/vrp: solved, total_cost=%d", int(solution.ObjectiveValue()))
    routes: List[dict] = []
    for v in range(num_vehicles):
        index = routing.Start(v)
        nodes = []
        while not routing.IsEnd(index):
            nodes.append(manager.IndexToNode(index))
            index = solution.Value(routing.NextVar(index))
        nodes.append(manager.IndexToNode(index))
        route = {"node_indices": nodes, "vehicle_index": v}
        if time_dimension is not None:
            # Hora de llegada a cada nodo (segundos desde medianoche), ya con el descanso.
            arrivals = []
            index = routing.Start(v)
            while not routing.IsEnd(index):
                arrivals.append(int(solution.Min(time_dimension.CumulVar(index))))
                index = solution.Value(routing.NextVar(index))
            arrivals.append(int(solution.Min(time_dimension.CumulVar(index))))
            route["arrival_seconds"] = arrivals
        routes.append(route)

    return {"success": True, "routes": routes, "total_cost": int(solution.ObjectiveValue())}
