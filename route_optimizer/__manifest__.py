# -*- coding: utf-8 -*-
{
    "name": "Route Optimizer (OSRM + OR-Tools)",
    "version": "18.0.1.0.5",
    "category": "Inventory",
    "summary": "Optimize delivery routes from batch transfers using OSRM and an external OR-Tools service",
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
    "installable": True,
    "application": False,
    "external_dependencies": {
        "python": [],
    },
}
