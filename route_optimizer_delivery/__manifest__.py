# -*- coding: utf-8 -*-
{
    "name": "Route Optimizer — Reparto",
    "version": "19.0.1.0.1",
    "category": "Inventory/Delivery",
    "summary": "App del chofer con prueba de entrega, seguimiento para el cliente y tablero de despacho",
    "description": """Lleva la ruta a la calle: app del chofer para el celular con prueba de entrega,
link de seguimiento para el cliente y tablero de despacho con mapa.""",
    "author": "aceleradora.la",
    "website": "https://aceleradora.la",
    "support": "info@aceleradora.la",
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
    "images": ["static/description/banner.png"],
    "installable": True,
    "application": True,
}
