# adapters/whatsapp/wa_client.py
import os
from typing import Optional, Dict, Any
import httpx
import requests

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

    r = requests.post(url, headers=headers, json=payload, timeout=20)

    if r.status_code == 401:
        print("SEND_ERROR: token inválido/expirado")
        print(r.text)
        return None

    if r.status_code >= 400:
        raise RuntimeError(f"WA send_text failed {r.status_code}: {r.text}")

    return r.json()

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

    r = requests.post(url, headers=headers, json=payload, timeout=20)
    if r.status_code == 401:
        print("SEND_ERROR: token inválido/expirado")
        print(r.text)
        return None
    if r.status_code >= 400:
        raise RuntimeError(f"WA send_interactive_buttons failed {r.status_code}: {r.text}")
    return r.json()


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

    r = requests.post(url, headers=headers, json=payload, timeout=20)
    if r.status_code == 401:
        print("SEND_ERROR: token inválido/expirado")
        print(r.text)
        return None
    if r.status_code >= 400:
        raise RuntimeError(f"WA send_interactive_list failed {r.status_code}: {r.text}")
    return r.json()


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
