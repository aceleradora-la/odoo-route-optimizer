# Route Optimizer para Odoo

Planificá, repartí y seguí tus entregas desde Odoo. Community y Enterprise.

![Route Optimizer — Reparto](route_optimizer_delivery/static/description/banner.png)

## Módulos

| | Módulo | Qué hace | Odoo |
|---|---|---|---|
| <img src="route_optimizer/static/description/icon.png" width="40"> | **Route Optimizer** `route_optimizer` | Ordena las entregas de un lote: varios vehículos, capacidad, ventanas horarias, hoja de ruta en PDF y por WhatsApp. | 17 · 18 · 19 · 20 |
| <img src="route_optimizer_delivery/static/description/icon.png" width="40"> | **Reparto** `route_optimizer_delivery` | App del chofer con prueba de entrega, seguimiento para el cliente y despacho con mapa. | 18 · 19 · 20 |
| <img src="route_optimizer_fleet/static/description/icon.png" width="40"> | **Flota** `route_optimizer_fleet` | Vehículo de Flota en el optimizador, con su capacidad precargada. | 17 · 18 · 19 · 20 |
| <img src="delivery_zone/static/description/icon.png" width="40"> | **Zonas de Entrega** `delivery_zone` | Zonas detectadas desde Google My Maps y lotes automáticos por zona. No requiere los demás. | 17 · 18 · 19 · 20 |

## Instalación

1. Cloná la rama de tu versión de Odoo (`17.0`, `18.0` o `19.0`) y sumá la carpeta al `addons_path`. Esta rama (`20.0`) ya está adaptada a Odoo 20.
2. En Odoo 18 y 19, instalá `stock_partner_delivery_window` de [OCA/stock-logistics-workflow](https://github.com/OCA/stock-logistics-workflow) y sus dependencias.
3. Levantá OSRM y el servicio OR-Tools de [`ortools_service/`](ortools_service/): [manual de despliegue](DEPLOYMENT_MANUAL_SECURE.md).
4. Instalá los módulos y completá **Ajustes → Inventario → Optimización de rutas**.

## Motores de ruteo

| Opción | Matrices | Solver | Costo |
|---|---|---|---|
| Propio (por defecto) | OSRM | OR-Tools | El servidor |
| Híbrido, con tráfico real | Google Routes | OR-Tools | Por elemento de matriz |
| Google completo | — | Google Route Optimization | Por envío |

Guía del modo híbrido: [GOOGLE_HYBRID_SETUP.md](GOOGLE_HYBRID_SETUP.md).

Con Google como motor, las horas estimadas y el dibujo de cada ruta salen de Routes API con tráfico (un pedido por ruta). El mapa del despacho puede ser OpenStreetMap o Google Maps (*Ajustes → Inventario → Reparto*): con Google suma tráfico en vivo y satélite, y pide una clave de Maps JavaScript API aparte, restringida por dominio.

## Uso

- **Optimizar**: en un traslado por lote, botón *Optimizar ruta*. Si la flota no carga toda lo mismo, cargá un renglón por vehículo con su capacidad. Las entregas marcadas como urgentes se visitan primero. El descanso del chofer se configura en *Ajustes → Inventario → Optimización de rutas*.
- **Repartir**: el chofer es el *Responsable* del lote y entra por el menú *Reparto* (`/odoo/reparto`); desde ahí instala la app en su celular. Alcanza con el grupo *Chofer de reparto*.
- **Seguir**: cada entrega tiene un link público de seguimiento, en la pestaña *Entrega* del traslado. Con la ruta en la calle, la app informa la ubicación del chofer cada minuto y las horas estimadas se corrigen solas; si la app queda cerrada, se estiman desde la última entrega.
- **Despachar**: *Inventario → Operaciones → Tablero de despacho* (`/odoo/despacho`). Elegí entregas en la lista, en el mapa o con el lazo; el panel ordena las rutas por lo que cuesta sumarlas. *Publicar a choferes* confirma las rutas en borrador. *En vivo* muestra camiones, atrasos e incidencias.
- **Zonas**: pegá la URL de tu Google My Maps en *Ajustes → Inventario → Zonas de entrega* y sincronizá.

- **Sin conexión**: con la app abierta, el chofer sigue cargando entregas sin señal; quedan en el teléfono y se envían solas al volver la conexión. Abrir la app desde cero sin señal no es posible.
- **WhatsApp automático**: *Ajustes → Inventario → Reparto*. Usa WhatsApp Business (Cloud API de Meta) y necesita una plantilla aprobada por aviso, con tres variables en el cuerpo: `{{1}}` nombre, `{{2}}` pedido, `{{3}}` link de seguimiento. Cargá los teléfonos con código de país.
- **SMS**: mismo lugar. Proveedores: Twilio, Vonage, Plivo, Telnyx, Infobip, ClickSend y Bird. Puede salir siempre o solo como respaldo cuando el WhatsApp no llegó. Los textos se editan en Ajustes ({empresa}, {nombre}, {pedido}, {link}); sin acentos entran 160 caracteres por SMS. Hay un botón para mandar un SMS de prueba.
- **Reportes**: *Inventario → Reportes → Reparto*: entregas (a tiempo, tarde, fallidas por motivo, por chofer) y rutas (km y tiempo planificados contra reales).

Los avisos por correo, WhatsApp y SMS al cliente y la validación del traslado al entregar vienen apagados: se activan en *Ajustes → Inventario → Reparto*.

## Desarrollo

```bash
odoo-bin -d test -i route_optimizer_delivery --test-tags /route_optimizer_delivery --stop-after-init
python tools/validate_views.py 19.0
python tools/port_to_version.py 20.0   # lleva el árbol de 19.0 a 20.0, 18.0 o 17.0
```

## Desarrollado por

**[aceleradora.la](https://aceleradora.la)** — implementación y desarrollo Odoo.
Soporte y consultas: [info@aceleradora.la](mailto:info@aceleradora.la)

Licencia [LGPL-3](LICENSE).
