# -*- coding: utf-8 -*-
{
    "name": "Zonas de Entrega",
    "version": "17.0.1.0.1",
    "category": "Inventory/Delivery",
    "summary": "Zonas de entrega por contacto, con autodetección desde Google My Maps "
    "y agrupación automática de lotes por zona",
    "description": """
Zonas de Entrega
================

Agrega el concepto de **Zona de entrega** a los contactos y a las órdenes de entrega.

Funcionalidades
---------------
* Tabla de configuración de zonas (creación manual)
* Autodetección de la zona por geolocalización contra un **Google My Maps** público
  (no requiere API key ni cuenta de Google: se lee el KML del mapa compartido)
* Zona en el contacto y en cada dirección de entrega, con herencia al contacto
  principal cuando la dirección no tiene zona propia
* Zona en la orden de entrega (calculada, sobrescribible), con filtro y agrupación
* Nueva opción **Zona de entrega** en la agrupación automática de lotes del
  Tipo de Operación, junto a Contacto, País de destino y Ubicaciones

Requisitos
----------
* Contactos geolocalizados (``base_geolocalize``) para la autodetección
* Un mapa de Google My Maps con las zonas dibujadas como polígonos, compartido
  como «cualquiera con el enlace»
    """,
    "author": "Aceleradora LA",
    "website": "https://github.com/aceleradora-la/odoo-route-optimizer",
    "support": "ignacio_nav@hotmail.com",
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
    "images": ["static/description/icon.png"],
    "installable": True,
    "application": False,
}
