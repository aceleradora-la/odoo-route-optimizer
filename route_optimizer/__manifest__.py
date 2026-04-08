# -*- coding: utf-8 -*-
{
    "name": "Route Optimizer (OSRM + OR-Tools)",
    "version": "18.0.1.0.2",
    "category": "Inventory",
    "summary": "Optimize delivery routes from batch transfers using OSRM and an external OR-Tools service",
    "license": "LGPL-3",
    "depends": [
        "stock_picking_batch",
        "base_geolocalize",
    ],
    "data": [
        "security/ir.model.access.csv",
        "views/res_config_settings_views.xml",
        "views/route_optimizer_wizard_views.xml",
        "views/stock_picking_batch_views.xml",
        "views/stock_picking_views.xml",
    ],
    "installable": True,
    "application": False,
    "external_dependencies": {
        "python": [],
    },
}
