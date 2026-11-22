# -*- coding: utf-8 -*-
{
    "name": "Route Optimizer — Reparto",
    "version": "19.0.1.0.0",
    "category": "Inventory/Delivery",
    "summary": "App del chofer con prueba de entrega, seguimiento para el cliente y tablero de despacho",
    "description": """
Route Optimizer — Reparto
=========================

Lleva la hoja de ruta optimizada a la calle.

App del chofer (celular)
------------------------
* Se instala en el celular como una app propia (igual que Código de Barras)
* Paradas en orden de visita, con navegación, llamada y WhatsApp a un toque
* Escaneo del remito para abrir la parada y de bultos/productos para controlarla
* Prueba de entrega: quién recibe, DNI, bultos, cobro, foto y firma
* Entrega parcial por producto y entregas fallidas con motivo
* Teclado numérico en cantidades, DNI, bultos e importes

Seguimiento para el cliente
---------------------------
* Link público por entrega, sin iniciar sesión
* Estado, hora estimada y cuántas paradas faltan
* Avisos por correo (en camino, sos el próximo, entregado) y por WhatsApp

Tablero de despacho
-------------------
* Todas las rutas del día con su avance, en columnas y en el mapa
* Arrastrar paradas entre rutas o para cambiar el orden
* Posición del chofer y entregas sin asignar
    """,
    "author": "Aceleradora LA",
    "website": "https://github.com/aceleradora-la/odoo-route-optimizer",
    "maintainers": ["aceleradora-la"],
    "license": "LGPL-3",
    "depends": [
        "route_optimizer",
        "mail",
    ],
    "data": [
        "security/route_delivery_security.xml",
        "security/ir.model.access.csv",
        "data/route_delivery_fail_reason_data.xml",
        "data/mail_template_data.xml",
        "views/route_delivery_tracking_templates.xml",
        "views/route_delivery_fail_reason_views.xml",
        "views/res_config_settings_views.xml",
        "views/stock_picking_views.xml",
        "views/stock_picking_batch_views.xml",
        "views/route_delivery_menus.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "route_optimizer_delivery/static/src/app/*",
            "route_optimizer_delivery/static/src/board/*",
        ],
    },
    "images": ["static/description/icon.png"],
    "installable": True,
    "application": True,
}
