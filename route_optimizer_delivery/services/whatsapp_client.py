# -*- coding: utf-8 -*-
"""Cliente mínimo de WhatsApp Business (Cloud API de Meta) para avisos al cliente.

Los mensajes que inicia la empresa tienen que salir de una PLANTILLA aprobada por
Meta. Cada aviso usa una plantilla con tres variables en el cuerpo:

    {{1}} nombre del cliente   {{2}} referencia del pedido   {{3}} link de seguimiento
"""
import json
import urllib.error
import urllib.request

GRAPH_URL = "https://graph.facebook.com/{version}/{phone_id}/messages"


class WhatsappError(Exception):
    """El aviso no se pudo enviar (credenciales, plantilla o número inválidos)."""


def normalize_phone(raw, default_country_code=""):
    """Número como lo espera la API: solo dígitos, con código de país.

    Acepta "+54 9 11 5555-1234", "0054911..." o un número nacional (se le antepone el
    código de país por defecto). Devuelve "" si no queda un número utilizable.
    """
    text = (raw or "").strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits:
        return ""
    international = text.startswith("+") or digits.startswith("00")
    if digits.startswith("00"):
        digits = digits[2:]
    if not international:
        digits = digits.lstrip("0")
        code = "".join(ch for ch in (default_country_code or "") if ch.isdigit())
        if code and not digits.startswith(code):
            digits = code + digits
    return digits if 8 <= len(digits) <= 15 else ""


def _post(url, payload, token, timeout):
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Authorization": "Bearer %s" % token, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = ""
        try:
            body = json.loads(error.read().decode("utf-8"))
            detail = (body.get("error") or {}).get("message") or ""
        except Exception:  # noqa: BLE001 - el cuerpo del error es informativo
            pass
        raise WhatsappError("WhatsApp HTTP %s: %s" % (error.code, detail or error.reason)) from error
    except urllib.error.URLError as error:
        raise WhatsappError("WhatsApp sin conexión: %s" % error.reason) from error
    except json.JSONDecodeError as error:
        raise WhatsappError("WhatsApp devolvió una respuesta inválida.") from error


def send_template(phone_id, token, to, template, language, parameters, version="v26.0", timeout=10):
    """Envía una plantilla y devuelve el id del mensaje."""
    if not (phone_id and token):
        raise WhatsappError("Faltan el identificador del número o el token de WhatsApp.")
    if not to:
        raise WhatsappError("El contacto no tiene un teléfono válido.")
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {
            "name": template,
            "language": {"code": language},
            "components": [
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": str(value)[:1000]} for value in parameters],
                }
            ],
        },
    }
    result = _post(GRAPH_URL.format(version=version, phone_id=phone_id), payload, token, timeout)
    messages = result.get("messages") or []
    return messages[0].get("id") if messages else False
