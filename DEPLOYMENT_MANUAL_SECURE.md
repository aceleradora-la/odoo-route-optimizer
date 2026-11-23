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

### C) Publicarlo con clave (recomendado)

OSRM no trae autenticación. Si Odoo no corre en el mismo servidor, dejá el contenedor
escuchando solo en local (`-p 127.0.0.1:5000:5000`) y publicalo por nginx con HTTPS,
exigiendo una clave en el header `X-API-KEY`:

```nginx
server {
    server_name osrm.midominio.com;
    listen 80;

    location / {
        if ($http_x_api_key != "PEGAR_CLAVE_GENERADA") { return 403; }
        proxy_pass http://127.0.0.1:5000;
        proxy_read_timeout 120s;
    }
}
```

Después `certbot --nginx -d osrm.midominio.com`. En Odoo: **URL base de OSRM**
`https://osrm.midominio.com` y la misma clave en **API key de OSRM**.

---

## 3) Contenedor 2: OR-Tools (FastAPI) con API Key

Este servicio se consume desde Odoo. Se recomienda **API key por header**:

- Header: `X-API-KEY`

**Código de referencia en este repositorio:** carpeta [`ortools_service/`](ortools_service/) (`main.py`, `Dockerfile`, `docker-compose.yml`). Copiá esa carpeta al servidor tal cual está en la rama de tu versión.

Soporta en **`POST /vrp`** (contrato extendido Odoo):

- `time_windows` y `time_windows_list` (varias franjas por parada; OR en el solver)
- `route_start_seconds` (salida del depósito)
- `service_times` (tiempo de atención por parada en la dimensión Tiempo)
- Capacidad peso/volumen, `max_stops_per_vehicle`, `max_route_duration_seconds`
- Capacidad distinta por vehículo (`vehicle_capacities_weight` / `vehicle_capacities_volume`, un valor por vehículo)
- `priorities` (paradas urgentes primero)
- `pickups` (1 = retiro: la carga sube en esa parada en vez de bajar)
- `break_earliest_seconds`, `break_latest_seconds`, `break_duration_seconds` (descanso del chofer)
- Devuelve `vehicle_index` y, cuando hay tiempos, `arrival_seconds` por ruta

### A) Estructura de carpeta

```text
~/or-tools-test/    # o ortools_service/ del repo
  Dockerfile
  main.py
  docker-compose.yml
  .env
```

### B) `main.py`

Usá el archivo [`ortools_service/main.py`](ortools_service/main.py) del repositorio: no lo copies
desde este manual, así no queda desactualizado. La API key no va en el código: se configura con
`API_KEY` en `.env`.

Cada vez que actualices los módulos de Odoo, actualizá también este archivo y reconstruí el
contenedor (paso F). Con un `main.py` viejo, la optimización sigue funcionando pero ignora las
prioridades y el descanso del chofer.


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

