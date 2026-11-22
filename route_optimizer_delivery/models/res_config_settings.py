# -*- coding: utf-8 -*-
from odoo import _, fields, models
from odoo.exceptions import UserError

from ..services import sms_client, whatsapp_client


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
    route_delivery_load_control = fields.Selection(
        [
            ("none", "Sin control"),
            ("picking", "Por remito"),
            ("package", "Por bulto (paquetes de Odoo)"),
        ],
        string="Control de carga del vehículo",
        config_parameter="route_optimizer_delivery.load_control",
        default="none",
        help="Antes de salir, el chofer escanea lo que sube al vehículo. Por bulto usa los "
        "paquetes de Odoo de cada traslado («Poner en paquete»); un traslado sin paquetes "
        "se controla por su remito.",
    )
    route_delivery_load_required = fields.Boolean(
        string="No salir con carga incompleta",
        config_parameter="route_optimizer_delivery.load_required",
        help="Apagado, la app avisa qué falta y deja salir igual; queda anotado en la ruta.",
    )
    route_delivery_load_scan_only = fields.Boolean(
        string="Solo por escaneo",
        config_parameter="route_optimizer_delivery.load_scan_only",
        help="No deja tildar un bulto a mano: hay que escanearlo o escribir su código.",
    )
    route_delivery_unload_control = fields.Boolean(
        string="Controlar bultos al entregar",
        config_parameter="route_optimizer_delivery.unload_control",
        help="En cada parada la app pide escanear los bultos que se bajan. Si falta alguno "
        "avisa antes de confirmar y lo deja anotado en el traslado.",
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
    route_delivery_sms_enabled = fields.Boolean(
        string="Avisar al cliente por SMS",
        config_parameter="route_optimizer_delivery.sms_enabled",
    )
    route_delivery_sms_provider = fields.Selection(
        sms_client.PROVIDERS,
        string="Proveedor de SMS",
        config_parameter="route_optimizer_delivery.sms_provider",
        default="twilio",
    )
    route_delivery_sms_mode = fields.Selection(
        [
            ("always", "Siempre"),
            ("fallback", "Solo si no salió por WhatsApp"),
        ],
        string="Cuándo enviar",
        config_parameter="route_optimizer_delivery.sms_mode",
        default="fallback",
        help="«Solo si no salió por WhatsApp» manda el SMS cuando WhatsApp está apagado, "
        "el contacto no tiene WhatsApp válido o el envío falló: se paga un solo aviso.",
    )
    route_delivery_sms_account = fields.Char(
        string="Usuario de la API",
        config_parameter="route_optimizer_delivery.sms_account",
        help="Twilio: Account SID · Vonage: API key · Plivo: Auth ID · ClickSend: usuario. "
        "Telnyx, Infobip y Bird no lo usan.",
    )
    route_delivery_sms_secret = fields.Char(
        string="Clave de la API",
        config_parameter="route_optimizer_delivery.sms_secret",
        help="Twilio: Auth Token · Vonage: API secret · Plivo: Auth Token · Telnyx: API key · "
        "Infobip: API key · ClickSend: API key · Bird: API key.",
    )
    route_delivery_sms_sender = fields.Char(
        string="Remitente",
        config_parameter="route_optimizer_delivery.sms_sender",
        help="Número propio con código de país o nombre corto, según lo que permita el "
        "proveedor en cada país. En Twilio también puede ser un Messaging Service (MG...).",
    )
    route_delivery_sms_base_url = fields.Char(
        string="URL de la cuenta",
        help="Infobip: la URL propia de la cuenta. Bird: solo si la región no sale de la clave.",
        config_parameter="route_optimizer_delivery.sms_base_url",
    )
    route_delivery_sms_text_on_the_way = fields.Char(
        string="Texto: salió a reparto",
        config_parameter="route_optimizer_delivery.sms_text_on_the_way",
        default="{empresa}: tu pedido {pedido} salio a reparto. Seguilo aca: {link}",
    )
    route_delivery_sms_text_next = fields.Char(
        string="Texto: sos el próximo",
        config_parameter="route_optimizer_delivery.sms_text_next",
        default="{empresa}: estamos yendo, sos la proxima parada. {link}",
    )
    route_delivery_sms_text_delivered = fields.Char(
        string="Texto: entregado",
        config_parameter="route_optimizer_delivery.sms_text_delivered",
        default="{empresa}: entregamos tu pedido {pedido}. Gracias!",
    )
    route_delivery_sms_text_failed = fields.Char(
        string="Texto: no entregado",
        config_parameter="route_optimizer_delivery.sms_text_failed",
        default="{empresa}: no pudimos entregar tu pedido {pedido}. Te contactamos para coordinar.",
    )
    route_delivery_sms_test_number = fields.Char(
        string="Número de prueba",
        help="Con código de país. No se guarda: es solo para el botón de prueba.",
    )

    def action_route_delivery_sms_test(self):
        """Manda un SMS de prueba con lo que hay cargado en pantalla (guardalo antes)."""
        self.ensure_one()
        icp = self.env["ir.config_parameter"].sudo()
        number = whatsapp_client.normalize_phone(
            self.route_delivery_sms_test_number, self.route_delivery_whatsapp_country_code
        )
        if not number:
            raise UserError(_("Cargá un número de prueba con código de país."))
        credentials = {
            key: icp.get_param("route_optimizer_delivery.sms_%s" % key) or ""
            for key in ("account", "secret", "sender", "base_url")
        }
        provider = icp.get_param("route_optimizer_delivery.sms_provider") or "twilio"
        text = _("Prueba de avisos de reparto de %s.") % (self.env.company.name or "")
        try:
            reference = sms_client.send(provider, credentials, number, text)
        except sms_client.SmsError as error:
            raise UserError(_("El proveedor rechazó el SMS: %s") % error) from error
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "title": _("SMS de prueba enviado"),
                "message": _("Aceptado por el proveedor (referencia %s).") % (reference or "-"),
                "sticky": False,
            },
        }
