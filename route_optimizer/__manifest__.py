# -*- coding: utf-8 -*-
{
    "name": "Route Optimizer (OSRM + OR-Tools)",
    "version": "17.0.1.3.2",
    "category": "Inventory/Delivery",
    "summary": "Optimización de rutas de entrega para traslados por lote usando OSRM y OR-Tools",
    "description": """
Optimizador de Rutas de Entrega
===============================

Optimiza el orden de visita de los traslados de un lote (stock.picking.batch)
usando OSRM para las matrices de distancia/tiempo y un microservicio OR-Tools
(incluido, deploy con Docker) para resolver el VRP.

Funcionalidades
---------------
* Optimización de ruta con un click desde el traslado por lote
* Soporte multi-vehículo: divide el lote en varios lotes optimizados
* Restricciones de capacidad (peso y volumen) por vehículo
* Ventanas horarias de entrega por cliente (OCA stock_partner_delivery_window)
* Límites duros: máximo de paradas y duración máxima de ruta
* Hoja de Ruta en PDF con QR de Google Maps, teléfonos, productos y firma
* Compartir la ruta completa por WhatsApp con un click
* Link directo a Google Maps con todas las paradas en orden

Requisitos
----------
* Servidor OSRM (Docker) para matrices de distancia/tiempo
* Microservicio OR-Tools incluido en el repositorio (carpeta ortools_service)
* Coordenadas geográficas en los contactos (base_geolocalize)
    """,
    "author": "Aceleradora LA",
    "website": "https://github.com/aceleradora-la/odoo-route-optimizer",
    "support": "ignacio_nav@hotmail.com",
    "maintainers": ["aceleradora-la"],
    "license": "LGPL-3",
    "depends": [
        "stock_picking_batch",
        "base_geolocalize",
        "web",
    ],
    "data": [
        "security/ir.model.access.csv",
        "report/batch_delivery_route_report.xml",
        "report/batch_delivery_route_templates.xml",
        "views/res_config_settings_views.xml",
        "views/route_optimizer_wizard_views.xml",
        "views/stock_picking_batch_views.xml",
        "views/stock_picking_batch_transfer_list_views.xml",
        "views/stock_picking_views.xml",
    ],
    "images": ["static/description/icon.png"],
    "installable": True,
    "application": False,
    "external_dependencies": {
        "python": ["qrcode"],
    },
}
