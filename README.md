# odoo-route-optimizer

Módulos Odoo para optimizar rutas de entrega en **lotes de transferencias** (`stock.picking.batch`) usando **OSRM** (matriz tiempo/distancia) y un **microservicio HTTP con Google OR-Tools**.

## Ramas y versiones de Odoo

| Rama   | Odoo | Community | Enterprise | Ventanas horarias (OCA) |
|--------|------|-----------|------------|--------------------------|
| `17.0` | 17.0 | ✅ | ✅ | ❌ no portado a OCA 17 |
| `18.0` | 18.0 | ✅ | ✅ | ✅ |
| `19.0` | 19.0 | ✅ | ✅ | ✅ |

Instalá el código desde la rama que coincida con tu versión de Odoo.

**Community**: los tres módulos funcionan en Odoo Community. Solo dependen de módulos
del core (`stock_picking_batch`, `base_geolocalize`, `contacts`, `fleet`, `web`), que
están disponibles en ambas ediciones. No se usa ningún módulo Enterprise.

<details>
<summary>Diferencias entre ramas (para mantenimiento)</summary>

El código Python es tolerante a versión por diseño, así que las ramas difieren en muy
poco. Todo el delta está declarado en `tools/port_to_version.py`:

| | 17.0 | 18.0 / 19.0 |
|---|---|---|
| Tag de lista en las vistas | `<tree>` | `<list>` |
| `view_mode` de las acciones | `tree,form` | `list,form` |
| Dependencia OCA de ventanas horarias | quitada | incluida |

Resuelto en el propio código, sin divergencia entre ramas:

- Campos de embalaje: `product_packaging_*` (17/18) y `packaging_uom_*` (19)
  se detectan en tiempo de ejecución (`_move_packaging`).
- Dominios de agrupación: Odoo 17/18 devuelven `list`, Odoo 19 un objeto `Domain`
  (`_extend_domain`).
- `_get_auto_batch_description` no existe en Odoo 17: el `super()` se resuelve con
  `getattr`.
- La acción de servidor no fija grupo, porque el campo cambió de nombre
  (`groups_id` en 17/18, `group_ids` en 19).

Para sincronizar una rama con las novedades de `19.0`:

```bash
git checkout 18.0            # o 17.0
git checkout 19.0 -- .
python tools/port_to_version.py 18.0
python tools/validate_views.py 18.0
```

</details>

## Módulos

- **`route_optimizer`**: configuración (URLs OSRM / OR-Tools), asistente desde el batch, clientes HTTP, aplicación de orden (`batch_sequence`) y reparto multi-vehículo opcional. Integra ventanas horarias del contacto vía OCA **`stock_partner_delivery_window`** cuando ese módulo está instalado (la integración es opcional: si falta, el resto sigue funcionando).
- **`route_optimizer_fleet`** (opcional): campo `fleet.vehicle` en el asistente; requiere el módulo `fleet`.
- **`delivery_zone`** (independiente): zonas de entrega por contacto, con autodetección desde un **Google My Maps** público (sin API key), filtro/agrupación por zona en las órdenes de entrega y **agrupación automática de lotes por zona** en el Tipo de Operación. No requiere `route_optimizer`. Ver [Zonas de entrega](#zonas-de-entrega).

## Proveedores: self-hosted vs. Google (pago)

Desde la versión 19.0.1.3.0 el módulo soporta proveedores intercambiables por configuración
(**Ajustes → Inventario → Optimización de rutas → Proveedores**). Las dos piezas se eligen
por separado:

| Combinación | Matrices | Solver | Credenciales | Costo |
|---|---|---|---|---|
| **Self-hosted** (default) | OSRM | OR-Tools | ninguna (Docker propio) | solo el VPS |
| **Híbrido** (recomendado para tráfico real) | Google Routes API | OR-Tools | API key de GCP | ~USD 5–10 por 1.000 elementos de matriz |
| **Google completo** | — (no aplica) | Google Route Optimization | Service account de GCP + project id | por envío optimizado (tier gratuito mensual inicial) |

Notas:

- **Híbrido**: `computeRouteMatrix` devuelve tiempos con tráfico real. Solo requiere una
  API key con *Routes API* habilitada. El solver sigue siendo tu microservicio OR-Tools
  (gratis, sin límite de paradas).
- **Google completo**: `optimizeTours` recibe las paradas con lat/lng, capacidades y
  ventanas horarias, y devuelve las rutas resueltas — no se usa matriz ni OSRM ni OR-Tools.
  ⚠️ No acepta API key: hay que crear un **service account** en GCP, habilitar
  *Route Optimization API* y pegar el JSON de la clave en Ajustes.
- Con los defaults (`OSRM + OR-Tools`) el comportamiento es exactamente el de siempre:
  las credenciales de Google solo se piden si elegís un proveedor de Google.

👉 **Guía paso a paso del modo híbrido** (GCP, API key, Docker y Odoo):
[GOOGLE_HYBRID_SETUP.md](GOOGLE_HYBRID_SETUP.md)

## Instalación

1. Clonar el repo y cambiar a la rama adecuada:

   ```bash
   git clone https://github.com/<tu-usuario>/odoo-route-optimizer.git
   cd odoo-route-optimizer
   git checkout 18.0   # o 19.0
   ```

2. Copiar (o enlazar) las carpetas `route_optimizer` y, si aplica, `route_optimizer_fleet` al `addons` de tu instancia.

3. Instalar desde OCA (rama `19.0`) **`stock_partner_delivery_window`** y sus dependencias (`base_time_window`, etc.).

4. Actualizar lista de aplicaciones e instalar **Route Optimizer (OSRM + OR-Tools)**.

5. En **Ajustes → Inventario → Route optimization**, configurar:
   - **OSRM base URL**: solo la raíz del servidor, p. ej. `http://195.179.231.4:5000`. **No** incluyas `/table/v1/` (Odoo arma `.../table/v1/driving/{coords}` solo).
   - **OR-Tools service URL**: p. ej. `http://195.179.231.4:8080/optimize`.
   - **Simple OR-Tools API** viene activado por defecto (formato `locations` + `distance_matrix` y respuesta `optimized_route`). Desmarcá solo si usás el contrato extendido (multi-vehículo, capacidades, **ventanas horarias**).
   - **Respect partner delivery windows**, hora de salida y tiempo de atención por parada (requieren API extendida `/vrp` y **Optimizar por duración**).

6. Reconstruir el contenedor OR-Tools desde [`ortools_service/`](ortools_service/) (ver [`DEPLOYMENT_MANUAL_SECURE.md`](DEPLOYMENT_MANUAL_SECURE.md)).

## Ventanas horarias (OCA)

Con **Optimizar por duración** y API extendida, Odoo envía al `/vrp` las ventanas del contacto para el **día de la semana** de la fecha programada de cada traslado. Varias franjas el mismo día (p. ej. mañana y tarde) se respetan en el solver. Configurá ventanas en el contacto (OCA) y la **fecha programada** en cada traslado del lote.

## Orden de visitas

Tras optimizar, el orden queda en:

1. **Visit order (last run)** en el formulario del lote (lista numerada: transferencia + contacto).
2. Pestaña **Traslados**: filas ordenadas por **Visit order** (`batch_sequence`): arriba = primera descarga. Columna opcional **Address**.
3. **Imprimir ruta** en la cabecera del lote: PDF **Delivery route (visit order)** con secuencia, cliente, dirección y referencia de cada transferencia (mismo orden que la ruta).

## Zonas de entrega

Módulo **`delivery_zone`**, independiente del optimizador (no lo requiere). Agrega el
concepto de *zona de entrega* al contacto, a la orden de entrega y a la agrupación
automática de lotes.

### Definir las zonas

Dos formas, combinables:

1. **Manual**: Inventario → Configuración → *Zonas de entrega* → crear.
2. **Desde Google My Maps** (autodetección por geolocalización):
   - Creá un mapa en [Google My Maps](https://www.google.com/mymaps) y dibujá cada zona
     con la herramienta de **polígono** (los marcadores de punto se ignoran).
   - Compartilo como **«cualquiera con el enlace»**.
   - Pegá la URL (o el `mid`) en *Ajustes → Inventario → Zonas de entrega* y usá
     **Probar mapa** para verificar, luego **Sincronizar zonas**.
   - No requiere API key, cuenta de Google ni OAuth: se lee el KML público del mapa.

Si los polígonos se superponen, el desempate configurable decide si gana la zona más
chica (default) o la primera coincidencia.

### Asignar la zona a los contactos

- Botón **Detectar zona** en el formulario del contacto (requiere que esté geolocalizado
  con `base_geolocalize`).
- Acción masiva **Detectar zona de entrega** desde la lista de contactos.
- O asignarla a mano en el campo *Zona de entrega*.

Una **dirección de entrega** sin zona propia hereda la del contacto principal.

### Usarla en las entregas

- **Filtro y agrupación** por zona en la lista de órdenes de entrega.
- **Lotes automáticos por zona**: en el Tipo de Operación de entregas, dentro de
  *Traslados por lote y olas → Agrupación por lotes*, tildá **Zona de entrega** (junto a
  Contacto, País de destino, etc.). Los traslados de la misma zona caen en el mismo lote.

## Contrato del servicio OR-Tools

Documentado en el docstring de `route_optimizer/services/route_service.py`.

## Publicar en tu GitHub (rama `18.0` y `19.0`)

Desde esta carpeta ya hay un repositorio Git con las ramas **`18.0`** y **`19.0`**. No puedo crear el repositorio remoto sin que inicies sesión en GitHub en tu máquina.

1. Creá en GitHub un repositorio vacío: **https://github.com/new** → nombre `odoo-route-optimizer` (sin README ni `.gitignore` si ya existen aquí).

2. Autenticación (una vez):

   ```bash
   "C:\Program Files\GitHub CLI\gh.exe" auth login
   ```

3. Enlazar y subir ambas ramas (reemplazá `TU_USUARIO`):

   ```powershell
   cd C:\Users\ignac\route-optimizer
   git remote add origin https://github.com/TU_USUARIO/odoo-route-optimizer.git
   git push -u origin 18.0
   git push -u origin 19.0
   ```

4. En GitHub: **Settings → General → Default branch** → elegí `18.0` (recomendado para la rama estable actual).

Alternativa con `gh` (tras `auth login`):

```bash
cd C:\Users\ignac\route-optimizer
gh repo create TU_USUARIO/odoo-route-optimizer --public --source=. --remote=origin --push
```

Eso sube solo la rama actual; luego ejecutá `git push -u origin 18.0` y `git push -u origin 19.0` si hace falta.

## Licencia

LGPL-3 (ver `LICENSE`).
