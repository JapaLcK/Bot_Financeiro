# adapters/whatsapp/wa_client.py
import os
from typing import Optional, Dict, Any
import httpx
import requests

from core.observability import log_system_event_sync

WA_TOKEN = (os.getenv("WA_TOKEN") or "").strip()
WA_PHONE_NUMBER_ID = (os.getenv("WA_PHONE_NUMBER_ID") or "").strip()

if not WA_TOKEN:
    print("⚠️ WA_TOKEN vazio. Verifique o .env")
if not WA_PHONE_NUMBER_ID:
    print("⚠️ WA_PHONE_NUMBER_ID vazio. Verifique o .env")


def _env(*names: str, default: str = "") -> str:
    for n in names:
        v = os.getenv(n)
        if v and v.strip():
            return v.strip()
    return default


def _wa_config(
    access_token: Optional[str] = None,
    phone_number_id: Optional[str] = None,
    graph_version: Optional[str] = None,
):
    token = (access_token or _env("WA_ACCESS_TOKEN", "WA_TOKEN"))
    pnid = (phone_number_id or _env("WA_PHONE_NUMBER_ID"))
    ver = (graph_version or _env("WA_GRAPH_VERSION", default="v21.0"))

    if not token:
        raise RuntimeError("WA token vazio. Defina WA_TOKEN (ou WA_ACCESS_TOKEN) no .env")
    if not pnid:
        raise RuntimeError("WA phone_number_id vazio. Defina WA_PHONE_NUMBER_ID no .env")

    base = f"https://graph.facebook.com/{ver}"
    return token, pnid, base


def send_text(
    to: str,
    body: str,
    *,
    access_token: Optional[str] = None,
    phone_number_id: Optional[str] = None,
    graph_version: Optional[str] = None,
):
    token, pnid, base = _wa_config(
        access_token=access_token,
        phone_number_id=phone_number_id,
        graph_version=graph_version,
    )
    url = f"{base}/{pnid}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": body},
    }

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=20)
    except Exception as exc:
        log_system_event_sync(
            "error",
            "whatsapp_send_exception",
            f"Excecao ao enviar mensagem WhatsApp: {exc}",
            source="wa_client",
            details={"to": to, "kind": "text"},
        )
        raise

    if r.status_code == 401:
        print("SEND_ERROR: token inválido/expirado")
        print(r.text)
        log_system_event_sync(
            "error",
            "whatsapp_token_invalid",
            "Token do WhatsApp invalido ou expirado durante envio de texto.",
            source="wa_client",
            details={"to": to, "kind": "text", "response": r.text[:500]},
        )
        return None

    if r.status_code >= 400:
        log_system_event_sync(
            "error",
            "whatsapp_send_failed",
            f"Falha ao enviar mensagem WhatsApp ({r.status_code}).",
            source="wa_client",
            details={"to": to, "kind": "text", "status_code": r.status_code, "response": r.text[:500]},
        )
        raise RuntimeError(f"WA send_text failed {r.status_code}: {r.text}")

    response = r.json()
    log_system_event_sync(
        "info",
        "whatsapp_send_success",
        "Mensagem de texto enviada para o WhatsApp.",
        source="wa_client",
        details={
            "to": to,
            "kind": "text",
            "message_ids": [m.get("id") for m in response.get("messages", []) if m.get("id")],
        },
    )
    return response


def send_template(
    to: str,
    template_name: str,
    *,
    language_code: str = "pt_BR",
    body_params: Optional[list[str]] = None,
    access_token: Optional[str] = None,
    phone_number_id: Optional[str] = None,
    graph_version: Optional[str] = None,
):
    token, pnid, base = _wa_config(
        access_token=access_token,
        phone_number_id=phone_number_id,
        graph_version=graph_version,
    )
    url = f"{base}/{pnid}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    template: Dict[str, Any] = {
        "name": template_name,
        "language": {"code": language_code},
    }
    if body_params:
        template["components"] = [
            {
                "type": "body",
                "parameters": [{"type": "text", "text": p} for p in body_params],
            }
        ]

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": template,
    }

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=20)
    except Exception as exc:
        log_system_event_sync(
            "error",
            "whatsapp_send_exception",
            f"Excecao ao enviar template WhatsApp: {exc}",
            source="wa_client",
            details={
                "to": to,
                "kind": "template",
                "template_name": template_name,
                "language_code": language_code,
            },
        )
        raise

    if r.status_code == 401:
        print("SEND_ERROR: token inválido/expirado")
        print(r.text)
        log_system_event_sync(
            "error",
            "whatsapp_token_invalid",
            "Token do WhatsApp invalido ou expirado durante envio de template.",
            source="wa_client",
            details={
                "to": to,
                "kind": "template",
                "template_name": template_name,
                "language_code": language_code,
                "response": r.text[:500],
            },
        )
        return None

    if r.status_code >= 400:
        log_system_event_sync(
            "error",
            "whatsapp_send_failed",
            f"Falha ao enviar template WhatsApp ({r.status_code}).",
            source="wa_client",
            details={
                "to": to,
                "kind": "template",
                "template_name": template_name,
                "language_code": language_code,
                "status_code": r.status_code,
                "response": r.text[:500],
            },
        )
        raise RuntimeError(f"WA send_template failed {r.status_code}: {r.text}")

    response = r.json()
    log_system_event_sync(
        "info",
        "whatsapp_send_success",
        "Template enviado para o WhatsApp.",
        source="wa_client",
        details={
            "to": to,
            "kind": "template",
            "template_name": template_name,
            "language_code": language_code,
            "message_ids": [m.get("id") for m in response.get("messages", []) if m.get("id")],
        },
    )
    return response


def send_typing_indicator(
    message_id: str,
    *,
    access_token: Optional[str] = None,
    phone_number_id: Optional[str] = None,
    graph_version: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    token, pnid, base = _wa_config(
        access_token=access_token,
        phone_number_id=phone_number_id,
        graph_version=graph_version,
    )
    url = f"{base}/{pnid}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id,
        "typing_indicator": {"type": "text"},
    }

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=20)
    except Exception as exc:
        log_system_event_sync(
            "warning",
            "whatsapp_typing_indicator_exception",
            f"Excecao ao enviar typing indicator do WhatsApp: {exc}",
            source="wa_client",
            details={"message_id": message_id},
        )
        raise

    if r.status_code == 401:
        print("SEND_ERROR: token inválido/expirado")
        print(r.text)
        log_system_event_sync(
            "error",
            "whatsapp_token_invalid",
            "Token do WhatsApp invalido ou expirado durante envio de typing indicator.",
            source="wa_client",
            details={"message_id": message_id, "kind": "typing_indicator", "response": r.text[:500]},
        )
        return None

    if r.status_code >= 400:
        log_system_event_sync(
            "warning",
            "whatsapp_typing_indicator_failed",
            f"Falha ao enviar typing indicator do WhatsApp ({r.status_code}).",
            source="wa_client",
            details={"message_id": message_id, "status_code": r.status_code, "response": r.text[:500]},
        )
        raise RuntimeError(f"WA send_typing_indicator failed {r.status_code}: {r.text}")

    response = r.json()
    log_system_event_sync(
        "info",
        "whatsapp_typing_indicator_success",
        "Typing indicator do WhatsApp enviado.",
        source="wa_client",
        details={"message_id": message_id},
    )
    return response

def send_interactive_buttons(
    to: str,
    body: str,
    buttons: list[dict],  # [{"id": "...", "title": "..."}] — máx 3
    *,
    header: Optional[str] = None,
    footer: Optional[str] = None,
    access_token: Optional[str] = None,
    phone_number_id: Optional[str] = None,
    graph_version: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Envia uma mensagem interativa com até 3 botões de resposta rápida.
    Cada botão: {"id": "...", "title": "..."} (title máx 20 chars).
    """
    token, pnid, base = _wa_config(
        access_token=access_token,
        phone_number_id=phone_number_id,
        graph_version=graph_version,
    )
    url = f"{base}/{pnid}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    interactive: Dict[str, Any] = {
        "type": "button",
        "body": {"text": body},
        "action": {
            "buttons": [
                {"type": "reply", "reply": {"id": b["id"], "title": b["title"]}}
                for b in buttons[:3]
            ]
        },
    }
    if header:
        interactive["header"] = {"type": "text", "text": header}
    if footer:
        interactive["footer"] = {"text": footer}

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": interactive,
    }

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=20)
    except Exception as exc:
        log_system_event_sync(
            "error",
            "whatsapp_send_exception",
            f"Excecao ao enviar botoes do WhatsApp: {exc}",
            source="wa_client",
            details={"to": to, "kind": "interactive_buttons"},
        )
        raise
    if r.status_code == 401:
        print("SEND_ERROR: token inválido/expirado")
        print(r.text)
        log_system_event_sync(
            "error",
            "whatsapp_token_invalid",
            "Token do WhatsApp invalido ou expirado durante envio de botoes.",
            source="wa_client",
            details={"to": to, "kind": "interactive_buttons", "response": r.text[:500]},
        )
        return None
    if r.status_code >= 400:
        log_system_event_sync(
            "error",
            "whatsapp_send_failed",
            f"Falha ao enviar botoes do WhatsApp ({r.status_code}).",
            source="wa_client",
            details={"to": to, "kind": "interactive_buttons", "status_code": r.status_code, "response": r.text[:500]},
        )
        raise RuntimeError(f"WA send_interactive_buttons failed {r.status_code}: {r.text}")
    response = r.json()
    log_system_event_sync(
        "info",
        "whatsapp_send_success",
        "Mensagem interativa com botoes enviada para o WhatsApp.",
        source="wa_client",
        details={"to": to, "kind": "interactive_buttons"},
    )
    return response


def send_interactive_list(
    to: str,
    body: str,
    button_label: str,
    sections: list[dict],
    *,
    header: Optional[str] = None,
    footer: Optional[str] = None,
    access_token: Optional[str] = None,
    phone_number_id: Optional[str] = None,
    graph_version: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Envia uma mensagem interativa com lista de opções (máx 10 itens no total).
    sections: [{"title": "Seção", "rows": [{"id": "...", "title": "...", "description": "..."}]}]
    button_label: rótulo do botão que abre a lista (máx 20 chars).
    """
    token, pnid, base = _wa_config(
        access_token=access_token,
        phone_number_id=phone_number_id,
        graph_version=graph_version,
    )
    url = f"{base}/{pnid}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    interactive: Dict[str, Any] = {
        "type": "list",
        "body": {"text": body},
        "action": {
            "button": button_label,
            "sections": sections,
        },
    }
    if header:
        interactive["header"] = {"type": "text", "text": header}
    if footer:
        interactive["footer"] = {"text": footer}

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": interactive,
    }

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=20)
    except Exception as exc:
        log_system_event_sync(
            "error",
            "whatsapp_send_exception",
            f"Excecao ao enviar lista do WhatsApp: {exc}",
            source="wa_client",
            details={"to": to, "kind": "interactive_list"},
        )
        raise
    if r.status_code == 401:
        print("SEND_ERROR: token inválido/expirado")
        print(r.text)
        log_system_event_sync(
            "error",
            "whatsapp_token_invalid",
            "Token do WhatsApp invalido ou expirado durante envio de lista.",
            source="wa_client",
            details={"to": to, "kind": "interactive_list", "response": r.text[:500]},
        )
        return None
    if r.status_code >= 400:
        log_system_event_sync(
            "error",
            "whatsapp_send_failed",
            f"Falha ao enviar lista do WhatsApp ({r.status_code}).",
            source="wa_client",
            details={"to": to, "kind": "interactive_list", "status_code": r.status_code, "response": r.text[:500]},
        )
        raise RuntimeError(f"WA send_interactive_list failed {r.status_code}: {r.text}")
    response = r.json()
    log_system_event_sync(
        "info",
        "whatsapp_send_success",
        "Mensagem interativa com lista enviada para o WhatsApp.",
        source="wa_client",
        details={"to": to, "kind": "interactive_list"},
    )
    return response


def download_media(
    media_id: str,
    *,
    access_token: Optional[str] = None,
    graph_version: Optional[str] = None,
    timeout: int = 40,
) -> bytes:
    """
    Baixa mídia do WhatsApp Cloud API:
      1) GET /{media_id} -> pega "url"
      2) GET url com Authorization -> bytes
    """
    token, _pnid, base = _wa_config(access_token=access_token, graph_version=graph_version)
    headers = {"Authorization": f"Bearer {token}"}
    

    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        meta = client.get(f"{base}/{media_id}", headers=headers)
        meta.raise_for_status()
        meta_json = meta.json()

        url = meta_json.get("url")
        if not url:
            raise RuntimeError(f"Sem 'url' no metadata do media_id={media_id}: {meta_json}")

        blob = client.get(url, headers=headers)
        blob.raise_for_status()
        return blob.content
