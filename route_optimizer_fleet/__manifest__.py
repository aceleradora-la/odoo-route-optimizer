# -*- coding: utf-8 -*-
{
    "name": "Route Optimizer — Fleet bridge",
    "version": "17.0.1.0.5",
    "category": "Inventory/Delivery",
    "summary": "Vincula vehículos de Flota con el optimizador de rutas y precarga capacidades",
    "description": """
Route Optimizer — Puente con Flota
==================================

Agrega el vehículo de Flota (fleet.vehicle) al asistente de optimización
de rutas y precarga automáticamente las capacidades de peso y volumen
desde la categoría del modelo del vehículo.

* Campo "Vehículo de flota" en el asistente de optimización
* Precarga de capacidad de peso/volumen desde el vehículo o su categoría
* Compatible con campos personalizados y de Studio (x_capacity, etc.)
* Si el lote tiene un vehículo asignado, se usa por defecto
    """,
    "author": "Aceleradora LA",
    "website": "https://github.com/aceleradora-la/odoo-route-optimizer",
    "support": "ignacio_nav@hotmail.com",
    "maintainers": ["aceleradora-la"],
    "license": "LGPL-3",
    "depends": [
        "route_optimizer",
        "fleet",
    ],
    "data": [
        "views/route_optimizer_wizard_views.xml",
    ],
    "images": ["static/description/icon.png"],
    "installable": True,
    "application": False,
}
