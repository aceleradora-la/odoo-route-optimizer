# -*- coding: utf-8 -*-
{
    "name": "Zonas de Entrega",
    "version": "19.0.1.0.3",
    "category": "Inventory/Delivery",
    "summary": "Zonas de entrega por contacto, detectadas desde Google My Maps, y lotes automáticos por zona",
    "description": """Zona de entrega en contactos y entregas, detectada por geolocalización contra un
Google My Maps público, con lotes automáticos por zona.""",
    "author": "aceleradora.la",
    "website": "https://aceleradora.la",
    "support": "info@aceleradora.la",
    "maintainers": ["aceleradora-la"],
    "license": "LGPL-3",
    "depends": [
        "stock_picking_batch",
        "base_geolocalize",
        "contacts",
    ],
    "data": [
        "security/ir.model.access.csv",
        "data/delivery_zone_server_actions.xml",
        "report/stock_picking_reports.xml",
        "views/delivery_zone_views.xml",
        "views/res_config_settings_views.xml",
        "views/res_partner_views.xml",
        "views/stock_picking_views.xml",
        "views/stock_picking_type_views.xml",
    ],
    "images": ["static/description/banner.png"],
    "installable": True,
    "application": False,
}
