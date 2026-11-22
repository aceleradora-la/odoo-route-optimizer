# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    route_delivery_validate_on_delivery = fields.Boolean(
        string="Validar el traslado al entregar",
        config_parameter="route_optimizer_delivery.validate_on_delivery",
        help="Al marcar una parada como entregada desde la app, valida el traslado con "
        "las cantidades entregadas (lo no entregado queda en una entrega parcial). "
        "Dejalo apagado si el traslado se valida al salir del depósito.",
    )
    route_delivery_notify_email = fields.Boolean(
        string="Avisar al cliente por correo",
        config_parameter="route_optimizer_delivery.notify_email",
        help="Envía un correo con el link de seguimiento cuando sale la ruta, cuando "
        "el cliente es la próxima parada y cuando se entrega o falla la entrega.",
    )
    route_delivery_require_signature = fields.Boolean(
        string="Firma obligatoria",
        config_parameter="route_optimizer_delivery.require_signature",
    )
    route_delivery_require_photo = fields.Boolean(
        string="Foto obligatoria",
        config_parameter="route_optimizer_delivery.require_photo",
    )
    route_delivery_eta_window_minutes = fields.Integer(
        string="Margen de la hora estimada (min)",
        config_parameter="route_optimizer_delivery.eta_window_minutes",
        default=30,
        help="Al cliente se le muestra una franja: hora estimada ± este margen.",
    )
    route_delivery_tile_url = fields.Char(
        string="Mapa base (URL de teselas)",
        config_parameter="route_optimizer_delivery.tile_url",
        help="Vacío usa OpenStreetMap. Formato: https://servidor/{z}/{x}/{y}.png",
    )
    route_delivery_map_provider = fields.Selection(
        [("osm", "OpenStreetMap"), ("google", "Google Maps")],
        string="Mapa del tablero",
        config_parameter="route_optimizer_delivery.map_provider",
        default="osm",
    )
    route_delivery_google_maps_key = fields.Char(
        string="Clave de Google Maps (navegador)",
        config_parameter="route_optimizer_delivery.google_maps_key",
        help="Clave de Maps JavaScript API. Va al navegador de quien usa el tablero: "
        "creá una clave aparte de la de Routes API y restringila por dominio.",
    )
    route_delivery_eta_refresh_minutes = fields.Integer(
        string="Actualizar horas con el GPS cada (min)",
        config_parameter="route_optimizer_delivery.eta_refresh_minutes",
        default=5,
        help="Con la ruta en la calle, las horas estimadas se recalculan desde la posición "
        "real del chofer con esta frecuencia. 0 = solo al completar cada parada. Con "
        "Google como motor, cada recálculo es un pedido a Routes API.",
    )
    route_delivery_whatsapp_enabled = fields.Boolean(
        string="Avisar al cliente por WhatsApp",
        config_parameter="route_optimizer_delivery.whatsapp_enabled",
        help="Envía los avisos por WhatsApp Business (Cloud API de Meta) usando plantillas "
        "aprobadas. Cada plantilla lleva tres variables: nombre, pedido y link de seguimiento.",
    )
    route_delivery_whatsapp_phone_id = fields.Char(
        string="Identificador del número (Phone number ID)",
        config_parameter="route_optimizer_delivery.whatsapp_phone_id",
    )
    route_delivery_whatsapp_token = fields.Char(
        string="Token de acceso",
        config_parameter="route_optimizer_delivery.whatsapp_token",
    )
    route_delivery_whatsapp_language = fields.Char(
        string="Idioma de las plantillas",
        config_parameter="route_optimizer_delivery.whatsapp_language",
        default="es_AR",
    )
    route_delivery_whatsapp_country_code = fields.Char(
        string="Código de país por defecto",
        config_parameter="route_optimizer_delivery.whatsapp_country_code",
        help="Se antepone a los teléfonos cargados sin código de país (54 para Argentina).",
    )
    route_delivery_whatsapp_tpl_on_the_way = fields.Char(
        string="Plantilla: salió a reparto",
        config_parameter="route_optimizer_delivery.whatsapp_tpl_on_the_way",
        default="reparto_en_camino",
    )
    route_delivery_whatsapp_tpl_next = fields.Char(
        string="Plantilla: sos el próximo",
        config_parameter="route_optimizer_delivery.whatsapp_tpl_next",
        default="reparto_proxima_parada",
    )
    route_delivery_whatsapp_tpl_delivered = fields.Char(
        string="Plantilla: entregado",
        config_parameter="route_optimizer_delivery.whatsapp_tpl_delivered",
        default="reparto_entregado",
    )
    route_delivery_whatsapp_tpl_failed = fields.Char(
        string="Plantilla: no entregado",
        config_parameter="route_optimizer_delivery.whatsapp_tpl_failed",
        default="reparto_no_entregado",
    )
