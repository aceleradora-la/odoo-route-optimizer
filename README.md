# odoo-route-optimizer

Módulos Odoo para optimizar rutas de entrega en **lotes de transferencias** (`stock.picking.batch`) usando **OSRM** (matriz tiempo/distancia) y un **microservicio HTTP con Google OR-Tools**.

## Ramas y versiones de Odoo

| Rama   | Odoo |
|--------|------|
| `18.0` | 18.0 |
| `19.0` | 19.0 |

Instalá el código desde la rama que coincida con tu versión de Odoo.

## Módulos

- **`route_optimizer`**: configuración (URLs OSRM / OR-Tools), asistente desde el batch, clientes HTTP, aplicación de orden (`batch_sequence`) y reparto multi-vehículo opcional.
- **`route_optimizer_fleet`** (opcional): campo `fleet.vehicle` en el asistente; requiere el módulo `fleet`.

## Instalación

1. Clonar el repo y cambiar a la rama adecuada:

   ```bash
   git clone https://github.com/<tu-usuario>/odoo-route-optimizer.git
   cd odoo-route-optimizer
   git checkout 18.0   # o 19.0
   ```

2. Copiar (o enlazar) las carpetas `route_optimizer` y, si aplica, `route_optimizer_fleet` al `addons` de tu instancia.

3. Actualizar lista de aplicaciones e instalar **Route Optimizer (OSRM + OR-Tools)**.

4. En **Ajustes → Inventario → Route optimization**, configurar:
   - **OSRM base URL**: solo la raíz del servidor, p. ej. `http://195.179.231.4:5000`. **No** incluyas `/table/v1/` (Odoo arma `.../table/v1/driving/{coords}` solo).
   - **OR-Tools service URL**: p. ej. `http://195.179.231.4:8080/optimize`.
   - **Simple OR-Tools API** viene activado por defecto (formato `locations` + `distance_matrix` y respuesta `optimized_route`). Desmarcá solo si usás el contrato extendido. Solo un vehículo en este modo.

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
