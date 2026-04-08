# Manual de Despliegue (Secure Edition)

Ecosistema de optimización logística con:

- **OSRM**: motor de mapas para obtener **matriz de distancias/tiempos**.
- **Microservicio OR-Tools (FastAPI)**: optimizador VRP accesible por HTTP.
- **Odoo Route Optimizer**: módulo Odoo que orquesta OSRM + OR-Tools.

Este manual asume **Ubuntu** y despliegue con **Docker + Docker Compose**.

---

## 1) Preparación del servidor

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y docker.io docker-compose-plugin wget openssl
sudo systemctl enable --now docker
```

### Generar una API key segura (guardala)

Recomendado:

```bash
openssl rand -base64 32
```

Alternativa (si no tenés openssl):

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

---

## 2) Contenedor 1: OSRM (Argentina)

**Notas de seguridad**

- OSRM no trae autenticación nativa.
- Lo recomendado es **no exponerlo públicamente** y/o restringir acceso por firewall (UFW), VPN o allowlist por IP.
- Si necesitás auth, ponelo detrás de un reverse proxy (Nginx/Traefik) y aplicá Basic Auth o allowlist.

### A) Preparar y procesar el mapa

```bash
mkdir -p ~/osrm-data && cd ~/osrm-data
wget http://download.geofabrik.de/south-america/argentina-latest.osm.pbf

docker run --rm -t -v "${PWD}:/data" osrm/osrm-backend \
  osrm-extract -p /opt/car.lua /data/argentina-latest.osm.pbf

docker run --rm -t -v "${PWD}:/data" osrm/osrm-backend \
  osrm-partition /data/argentina-latest.osrm

docker run --rm -t -v "${PWD}:/data" osrm/osrm-backend \
  osrm-customize /data/argentina-latest.osrm
```

### B) Levantar el servicio

```bash
docker run -d --name osrm-argentina \
  --restart always \
  -p 5000:5000 \
  -v "${PWD}:/data" \
  osrm/osrm-backend osrm-routed --algorithm mld /data/argentina-latest.osrm
```

---

## 3) Contenedor 2: OR-Tools (FastAPI) con API Key

Este servicio se consume desde Odoo. Se recomienda **API key por header**:

- Header: `X-API-KEY`

### A) Estructura de carpeta

```text
~/or-tools-test/
  Dockerfile
  main.py
  docker-compose.yml
  .env
```

### B) `main.py` (auth por env var, sin hardcode)

> Importante: **no** hardcodear la API key en el código. Se configura con `API_KEY` en `.env`.

```python
from fastapi import FastAPI, HTTPException, Security, Depends
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel, Field
from typing import List
from ortools.constraint_solver import routing_enums_pb2, pywrapcp
import os

API_KEY_NAME = "X-API-KEY"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

app = FastAPI()

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
    # Backward-compatible (weight)
    demands: List[float] = []
    vehicle_capacities: List[float] = []
    # Recommended explicit dimensions
    demands_weight: List[float] = []
    vehicle_capacities_weight: List[float] = []
    demands_volume: List[float] = []
    vehicle_capacities_volume: List[float] = []
    picking_ids: List[int] = []
    # Optional constraints:
    max_stops_per_vehicle: int = 0
    max_route_duration_seconds: int = 0
    # Optional time windows (one per node, includes depot at index 0): [[start,end], ...]
    time_windows: List[List[int]] = []

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
    search_parameters.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC

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

    return {"success": True, "optimized_route": route_labels, "total_distance": int(solution.ObjectiveValue())}

@app.post("/vrp")
async def vrp(request: VrpRequest, token: str = Depends(get_api_key)):
    n = len(request.matrix)
    if n == 0 or any(len(row) != n for row in request.matrix):
        raise HTTPException(status_code=422, detail="matrix debe ser NxN")
    if request.depot_index != 0:
        raise HTTPException(status_code=422, detail="Solo se soporta depot_index=0")

    num_vehicles = int(request.num_vehicles or 1)
    depot = 0

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

    # Capacidad (peso) (opcional)
    demands_weight = request.demands_weight or request.demands
    caps_weight = request.vehicle_capacities_weight or request.vehicle_capacities
    if demands_weight and caps_weight:
        if len(demands_weight) != n:
            raise HTTPException(status_code=422, detail="demands_weight debe tener largo N")
        if len(caps_weight) != num_vehicles:
            raise HTTPException(status_code=422, detail="vehicle_capacities_weight debe tener largo num_vehicles")

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

    # Capacidad (volumen) (opcional)
    if request.demands_volume and request.vehicle_capacities_volume:
        if len(request.demands_volume) != n:
            raise HTTPException(status_code=422, detail="demands_volume debe tener largo N")
        if len(request.vehicle_capacities_volume) != num_vehicles:
            raise HTTPException(status_code=422, detail="vehicle_capacities_volume debe tener largo num_vehicles")

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

    # Max stops per vehicle (opcional)
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

    # Time windows + max duration (opcional, requiere matriz de duración)
    if request.time_windows:
        if len(request.time_windows) != n:
            raise HTTPException(status_code=422, detail="time_windows debe tener largo N")
        time_dimension_name = "Time"
        routing.AddDimension(
            transit_callback_index,
            0,
            int(request.max_route_duration_seconds) if request.max_route_duration_seconds else 24 * 3600,
            True,
            time_dimension_name,
        )
        time_dimension = routing.GetDimensionOrDie(time_dimension_name)
        for node_idx, tw in enumerate(request.time_windows):
            if not tw or len(tw) != 2:
                continue
            start, end = int(tw[0]), int(tw[1])
            index = manager.NodeToIndex(node_idx)
            time_dimension.CumulVar(index).SetRange(start, end)
        if request.max_route_duration_seconds and request.max_route_duration_seconds > 0:
            for v in range(num_vehicles):
                end_index = routing.End(v)
                time_dimension.CumulVar(end_index).SetMax(int(request.max_route_duration_seconds))

    search_parameters = pywrapcp.DefaultRoutingSearchParameters()
    search_parameters.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC

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
```

### C) `Dockerfile`

```dockerfile
FROM python:3.11-slim
RUN apt-get update && apt-get install -y build-essential && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir ortools fastapi uvicorn
COPY main.py .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### D) `docker-compose.yml`

> Usá `env_file` para evitar exponer secretos en el YAML.

```yaml
services:
  ortools-service:
    build: .
    container_name: ortools-service
    restart: always
    ports:
      - "8080:8000"
    env_file:
      - .env
```

### E) `.env`

```env
API_KEY=PEGAR_TU_API_KEY_GENERADA
```

### F) Build y run (con compose)

```bash
cd ~/or-tools-test
docker compose down
docker compose up -d --build
docker compose logs -f
```

Verificar que el contenedor tomó el secreto:

```bash
docker compose exec ortools-service printenv API_KEY
```

---

## 4) Firewall (UFW)

Ejemplo básico (mejor: allowlist por IP de tu Odoo):

```bash
sudo ufw allow 5000/tcp
sudo ufw allow 8080/tcp

# Recomendado (ejemplo):
# sudo ufw delete allow 8080/tcp
# sudo ufw allow from [IP_DE_ODOO] to any port 8080 proto tcp
# sudo ufw delete allow 5000/tcp
# sudo ufw allow from [IP_DE_ODOO] to any port 5000 proto tcp

sudo ufw enable
sudo ufw status
```

---

## 5) Configuración en Odoo (módulo Route Optimizer)

En **Inventario → Ajustes → Route optimization**:

- **OSRM base URL**: `http://TU_IP:5000`
- **OSRM profile**: `driving`
- **OR-Tools service URL**: `http://TU_IP:8080`
- **OR-Tools API key**: pegá la misma key que tenés en `.env`
- **Simple OR-Tools API**:
  - Marcado: Odoo envía `locations + distance_matrix` (API simple).
  - Desmarcado: Odoo envía **payload extendido** (requiere que tu microservicio lo soporte).

---

## 6) Rotación de API Key

1. Generar una nueva key.
2. Actualizar `.env` (en el servidor).
3. Reiniciar:

```bash
docker compose up -d
```

1. Actualizar **OR-Tools API key** en Odoo.

---

## 7) Troubleshooting

### Puerto 8080 ocupado

Si `docker compose up` falla con “port is already allocated”, listá y liberá el contenedor que lo ocupa:

```bash
docker ps --format "table {{.Names}}\t{{.Ports}}"
docker stop ortools-service
docker rm ortools-service
```

### 403 Forbidden (API key inválida)

- Confirmar que Odoo tiene cargada **OR-Tools API key**.
- Confirmar que el contenedor tiene `API_KEY`:

```bash
docker compose exec ortools-service printenv API_KEY
```

- Probar con `curl`:

```bash
curl -i -X POST "http://TU_IP:8080/optimize" \
  -H "Content-Type: application/json" \
  -H "X-API-KEY: TU_API_KEY" \
  -d '{"locations":["DEPOT","A"],"distance_matrix":[[0,1],[1,0]],"num_vehicles":1}'
```

Si estás en modo **extendido**, probá `/vrp`:

```bash
curl -i -X POST "http://TU_IP:8080/vrp" \
  -H "Content-Type: application/json" \
  -H "X-API-KEY: TU_API_KEY" \
  -d '{"version":1,"num_vehicles":1,"depot_index":0,"matrix":[[0,1],[1,0]],"matrix_metric":"distance","demands":[0,0],"vehicle_capacities":[999],"picking_ids":[1]}'
```

