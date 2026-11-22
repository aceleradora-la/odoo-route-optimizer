# -*- coding: utf-8 -*-
{
    "name": "Route Optimizer — Flota",
    "version": "18.0.1.1.0",
    "category": "Inventory/Delivery",
    "summary": "Elegí el vehículo de Flota al optimizar y precargá su capacidad",
    "description": """Agrega el vehículo de Flota al optimizador de rutas y precarga su capacidad de
peso y volumen.""",
    "author": "aceleradora.la",
    "website": "https://aceleradora.la",
    "support": "info@aceleradora.la",
    "maintainers": ["aceleradora-la"],
    "license": "LGPL-3",
    "depends": [
        "route_optimizer",
        "fleet",
    ],
    "data": [
        "views/route_optimizer_wizard_views.xml",
    ],
    "images": ["static/description/banner.png"],
    "installable": True,
    "application": False,
}
