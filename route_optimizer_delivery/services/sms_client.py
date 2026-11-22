# -*- coding: utf-8 -*-
"""Envío de SMS por el proveedor que elija la empresa.

Todos se usan igual: ``send(provider, credentials, to, text)``. Las credenciales son
siempre las mismas cuatro claves, con el significado que cada proveedor les da:

    account   usuario de la API (Account SID, API key, Auth ID, usuario...)
    secret    clave o token secreto
    sender    remitente: un número propio o un nombre corto, según el proveedor
    base_url  Infobip: la URL propia de la cuenta (xxxxx.api.infobip.com).
              Bird: opcional, solo si la región no sale de la clave.

``to`` va solo con dígitos y código de país (5491155550001).
"""
import base64
import json
import urllib.error
import urllib.parse
import urllib.request

PROVIDERS = [
    ("twilio", "Twilio"),
    ("vonage", "Vonage"),
    ("plivo", "Plivo"),
    ("telnyx", "Telnyx"),
    ("infobip", "Infobip"),
    ("clicksend", "ClickSend"),
    ("bird", "Bird (ex MessageBird)"),
]


class SmsError(Exception):
    """El SMS no se pudo enviar (credenciales, remitente, saldo o número inválidos)."""


def is_gsm7(text):
    """True si el texto entra en el alfabeto básico de SMS (160 caracteres por mensaje).

    Con acentos como «á» o «ó» el mensaje pasa a Unicode y entran solo 70 por SMS: el
    mismo aviso se cobra como dos o tres.
    """
    basic = (
        "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
        "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
    )
    return all(ch in basic for ch in text or "")


def _basic(user, password):
    token = base64.b64encode(("%s:%s" % (user, password)).encode("utf-8")).decode("ascii")
    return "Basic %s" % token


def _request(url, headers, form=None, payload=None, timeout=10):
    """POST con cuerpo de formulario o JSON; devuelve el JSON de la respuesta."""
    headers = dict(headers or {})
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
    else:
        data = urllib.parse.urlencode(form or {}).encode("utf-8")
        headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
    headers.setdefault("Accept", "application/json")
    request = urllib.request.Request(url, data=data, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        detail = ""
        try:
            detail = error.read().decode("utf-8")[:300]
        except Exception:  # noqa: BLE001 - el cuerpo del error es informativo
            pass
        raise SmsError("HTTP %s: %s" % (error.code, detail or error.reason)) from error
    except urllib.error.URLError as error:
        raise SmsError("Sin conexión con el proveedor: %s" % error.reason) from error
    try:
        return json.loads(body) if body else {}
    except json.JSONDecodeError as error:
        raise SmsError("El proveedor devolvió una respuesta inválida.") from error


def _twilio(cred, to, text, timeout):
    form = {"To": "+" + to, "Body": text}
    # Un remitente que empieza con MG es un Messaging Service, no un número.
    if cred["sender"].startswith("MG"):
        form["MessagingServiceSid"] = cred["sender"]
    else:
        form["From"] = cred["sender"]
    url = "https://api.twilio.com/2010-04-01/Accounts/%s/Messages.json" % urllib.parse.quote(cred["account"])
    result = _request(url, {"Authorization": _basic(cred["account"], cred["secret"])}, form=form, timeout=timeout)
    return result.get("sid")


def _vonage(cred, to, text, timeout):
    form = {"to": to, "from": cred["sender"], "text": text}
    if not is_gsm7(text):
        form["type"] = "unicode"
    # Las credenciales van en la cabecera: Vonage dejó de recomendar mandarlas en el cuerpo.
    result = _request(
        "https://rest.nexmo.com/sms/json",
        {"Authorization": _basic(cred["account"], cred["secret"])},
        form=form,
        timeout=timeout,
    )
    message = (result.get("messages") or [{}])[0]
    # Vonage responde 200 también cuando rechaza: el resultado va en `status`.
    if str(message.get("status")) != "0":
        raise SmsError(message.get("error-text") or "Vonage rechazó el mensaje.")
    return message.get("message-id")


def _plivo(cred, to, text, timeout):
    url = "https://api.plivo.com/v1/Account/%s/Message/" % urllib.parse.quote(cred["account"])
    result = _request(
        url,
        {"Authorization": _basic(cred["account"], cred["secret"])},
        payload={"src": cred["sender"], "dst": to, "text": text},
        timeout=timeout,
    )
    ids = result.get("message_uuid") or []
    return ids[0] if ids else False


def _telnyx(cred, to, text, timeout):
    result = _request(
        "https://api.telnyx.com/v2/messages",
        {"Authorization": "Bearer %s" % cred["secret"]},
        payload={"from": cred["sender"], "to": "+" + to, "text": text},
        timeout=timeout,
    )
    return (result.get("data") or {}).get("id")


def _infobip(cred, to, text, timeout):
    base = (cred.get("base_url") or "").strip().rstrip("/")
    if not base:
        raise SmsError("Falta la URL de la cuenta de Infobip.")
    if not base.startswith("http"):
        base = "https://" + base
    result = _request(
        # API v3: reemplaza a /sms/2/text/advanced (remitente en `sender`, texto en `content`).
        base + "/sms/3/messages",
        {"Authorization": "App %s" % cred["secret"]},
        payload={
            "messages": [
                {"sender": cred["sender"], "destinations": [{"to": to}], "content": {"text": text}}
            ]
        },
        timeout=timeout,
    )
    message = (result.get("messages") or [{}])[0]
    group = ((message.get("status") or {}).get("groupName") or "").upper()
    if group in ("REJECTED", "UNDELIVERABLE", "EXPIRED"):
        raise SmsError((message.get("status") or {}).get("description") or "Infobip rechazó el mensaje.")
    return message.get("messageId")


def _clicksend(cred, to, text, timeout):
    message = {"source": "odoo", "body": text, "to": "+" + to}
    if cred["sender"]:
        message["from"] = cred["sender"]
    result = _request(
        "https://rest.clicksend.com/v3/sms/send",
        {"Authorization": _basic(cred["account"], cred["secret"])},
        payload={"messages": [message]},
        timeout=timeout,
    )
    sent = ((result.get("data") or {}).get("messages") or [{}])[0]
    if result.get("response_code") != "SUCCESS" or sent.get("status") not in (None, "SUCCESS"):
        raise SmsError(sent.get("status") or result.get("response_msg") or "ClickSend rechazó el mensaje.")
    return sent.get("message_id")


def _bird(cred, to, text, timeout):
    # La región sale del prefijo de la clave (bk_eu1_... → eu1).
    parts = cred["secret"].split("_")
    host = (cred.get("base_url") or "").strip().rstrip("/")
    if not host:
        if len(parts) < 3 or parts[0] != "bk":
            raise SmsError("No se reconoce la región en la clave de Bird: cargá la URL de la cuenta.")
        host = "https://%s.platform.bird.com" % parts[1]
    elif not host.startswith("http"):
        host = "https://" + host
    result = _request(
        host + "/v1/sms/messages",
        {"Authorization": "Bearer %s" % cred["secret"]},
        payload={"from": cred["sender"], "to": "+" + to, "text": text, "category": "transactional"},
        timeout=timeout,
    )
    return result.get("id")


_SENDERS = {
    "twilio": _twilio,
    "vonage": _vonage,
    "plivo": _plivo,
    "telnyx": _telnyx,
    "infobip": _infobip,
    "clicksend": _clicksend,
    "bird": _bird,
}


def send(provider, credentials, to, text, timeout=10):
    """Envía un SMS y devuelve el identificador que le dio el proveedor."""
    sender = _SENDERS.get(provider)
    if not sender:
        raise SmsError("Proveedor de SMS desconocido: %s" % provider)
    cred = {key: (credentials.get(key) or "").strip() for key in ("account", "secret", "sender", "base_url")}
    if not cred["secret"]:
        raise SmsError("Falta la clave del proveedor de SMS.")
    if not to:
        raise SmsError("El contacto no tiene un teléfono válido.")
    if not (text or "").strip():
        raise SmsError("El mensaje está vacío.")
    return sender(cred, to, text, timeout)
