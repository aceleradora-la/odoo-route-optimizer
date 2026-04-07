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

4. En **Ajustes → Inventario → Route optimization**, configurar la URL base de OSRM y la URL del servicio OR-Tools.

## Contrato del servicio OR-Tools

Documentado en el docstring de `route_optimizer/services/route_service.py`.

## Licencia

LGPL-3 (ver `LICENSE`).
