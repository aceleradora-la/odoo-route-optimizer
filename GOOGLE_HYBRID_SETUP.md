# Modo Híbrido: matrices de Google + solver OR-Tools propio

Guía paso a paso para configurar el modo **híbrido**: Google Routes API calcula los
tiempos y distancias reales por calle, y tu microservicio **OR-Tools** (Docker) decide
el orden de visita.

## Quién hace qué

| Pieza | Rol | Dónde corre |
|---|---|---|
| **Google Routes API** (`computeRouteMatrix`) | Calles y direcciones: tiempos/distancias reales de manejo entre cada par de puntos | Nube de Google (pago, con tier gratuito) |
| **OR-Tools** | Motor de cálculo: decide el **orden de visita** y el reparto entre vehículos | Tu servidor, Docker (`ortools_service`) |
| ~~OSRM~~ | Ya no se usa en este modo | Podés detener el contenedor |

Costo: se cobra por **elemento** de matriz = `(paradas + 1)²` por optimización.
El tier Essentials incluye **10.000 elementos gratis/mes** — un lote diario de 10 paradas
consume ~2.700 elementos/mes: **USD 0**.

---

## Parte 1 — Google Cloud (una sola vez)

1. **Crear el proyecto**
   - Entrá a https://console.cloud.google.com/ con una cuenta Google.
   - Menú superior → selector de proyecto → **Nuevo proyecto** → nombre (ej. `rutas-amoedo`) → Crear.

2. **Habilitar facturación**
   - Menú ☰ → **Facturación** → asociá una tarjeta.
   - Aunque el uso quede en $0, Google exige cuenta de facturación activa.
   - Recomendado: **Facturación → Presupuestos y alertas** → crear presupuesto de USD 10
     con alerta al 50%/90% para enterarte si algo se dispara.

3. **Habilitar la API**
   - Menú ☰ → **APIs y servicios → Biblioteca**.
   - Buscá **"Routes API"** → Habilitar.
   - ⚠️ Es "Routes API" (la nueva). NO confundir con "Directions API" ni "Distance Matrix API" (legacy).

4. **Crear la API key**
   - **APIs y servicios → Credenciales → + Crear credenciales → Clave de API**.
   - Copiá la clave (empieza con `AIza...`).

5. **Restringir la clave** (importante para que nadie te la use si se filtra)
   - Click en la clave recién creada → editar:
   - **Restricciones de aplicaciones** → *Direcciones IP* → agregá la IP pública de tu
     servidor Odoo (la clave solo funcionará desde ahí).
   - **Restricciones de API** → *Restringir clave* → tildá solamente **Routes API**.
   - Guardar.

## Parte 2 — Servidor Docker

Nada que instalar. Solo verificar que el microservicio OR-Tools esté corriendo:

```bash
docker compose -f ortools_service/docker-compose.yml ps
# debe mostrar ortools-service Up
```

Opcional — apagar OSRM (ya no se usa en modo híbrido):

```bash
docker stop <contenedor-osrm>
```

> Si más adelante querés volver al modo 100% self-hosted, levantás OSRM de nuevo y
> cambiás el proveedor en Ajustes. No hay que reinstalar nada.

## Parte 3 — Odoo

**Ajustes → Inventario → Optimización de rutas → Proveedores**:

| Campo | Valor |
|---|---|
| **Proveedor de matrices** | `Google Routes API (pago)` |
| **Proveedor del solver** | `OR-Tools (self-hosted)` ← sin cambios |
| **Google API key (Routes API)** | la clave `AIza...` del paso 4 |

Los campos *Google project id* y *service account JSON* **quedan vacíos** — solo se usan
para el modo "Google completo" (Route Optimization API), no para el híbrido.

La configuración de **Servicio OR-Tools** (URL, API key, API simple) queda exactamente
como está.

## Parte 4 — Probar

1. Abrí un traslado por lote con 2+ entregas geolocalizadas.
2. **Optimizar ruta** → Optimizar.
3. Verificá en el chatter: `Ruta optimizada (N paradas, ...)`.
4. En el servidor, los logs del microservicio muestran que recibió la matriz (ahora de Google):

   ```bash
   docker compose -f ortools_service/docker-compose.yml logs --tail 20 ortools-service
   # POST /optimize: N locations, 1 vehicles  ← la matriz vino de Google
   ```

## Errores comunes

| Mensaje | Causa | Solución |
|---|---|---|
| `Error de Google Routes API: Google HTTP 403 ... API key not valid` | Clave mal copiada o restringida a otra IP | Revisar restricciones de la clave en GCP |
| `Error de Google Routes API: Google HTTP 403 ... Routes API has not been used` | La API no está habilitada en el proyecto | Parte 1, paso 3 |
| `Error de Google Routes API: Google HTTP 429` | Se superó la cuota | Revisar cuotas en GCP (raro con este volumen) |
| `Falta configurar la Google API key` | Se eligió Google como proveedor sin cargar la clave | Parte 3 |

## Control de gastos

- Cada click en **Optimizar ruta** consume `(paradas+1)²` elementos.
- Consumo real: consola GCP → **APIs y servicios → Routes API → Métricas**.
- El presupuesto con alertas del Paso 2 avisa por email antes de generar cargos.
