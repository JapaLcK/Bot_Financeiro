from __future__ import annotations

import logging
import os
import urllib.parse

DEFAULT_DASHBOARD_BASE_URL = "http://localhost:8000"


def get_dashboard_base_url() -> str:
    base_url = (os.getenv("DASHBOARD_URL") or DEFAULT_DASHBOARD_BASE_URL).strip()
    if base_url.startswith("DASHBOARD_URL="):
        base_url = base_url[len("DASHBOARD_URL="):]
    return base_url.rstrip("/")


def base_url_publica() -> str:
    """Base https pública do site; fora de https (dev/localhost) cai no domínio
    de produção. Usada nos links que vão para fora do app (WhatsApp)."""
    base = get_dashboard_base_url()
    return base if base.startswith("https://") else "https://pigbankai.com"


def link_whatsapp(texto: str, *, seletor: bool = False) -> str:
    """Deep link do chat com o Piggy, com `texto` pré-preenchido. É o ÚNICO
    construtor: site, e-mail, /wa e /teste passam por aqui. O número real fica no
    servidor (WHATSAPP_NUMBER, lido a cada chamada).

    Sem número devolve "" (falha fechada): o `wa.me/?text=` abre o seletor de
    contatos, e um texto com o código de vínculo (uso único) iria para quem a
    pessoa escolhesse. `seletor=True` é só para texto SEM segredo (saudação):
    aí cai no seletor genérico, degradado mas sem quebrar a página nem o e-mail."""
    number = "".join(ch for ch in os.getenv("WHATSAPP_NUMBER", "") if ch.isascii() and ch.isdigit())
    text = urllib.parse.quote(texto)
    if number:
        return f"https://api.whatsapp.com/send?phone={number}&text={text}"
    return f"https://wa.me/?text={text}" if seletor else ""


logger = logging.getLogger(__name__)


def build_dashboard_link(
    user_id: int,
    hours: float = 5 / 60,
    view: str | None = None,
    next_path: str | None = None,
) -> str | None:
    """
    Gera link autenticado /d/{code}.

    - view: aba do dashboard (overview/investments/open-finance) — destino interno.
    - next_path: rota alternativa (ex: "/precos", "/conta") — tem prioridade sobre view.
      Usado pra mandar o user logado direto pra paginas fora do /app.
    """
    base_url = get_dashboard_base_url()

    try:
        from db import create_dashboard_session
        import urllib.parse

        code = create_dashboard_session(user_id, hours=hours)
        if next_path and next_path.startswith("/"):
            return f"{base_url}/d/{code}?next={urllib.parse.quote(next_path, safe='/')}"
        suffix = ""
        if view in {"overview", "investments", "open-finance"}:
            suffix = f"?view={view}"
        return f"{base_url}/d/{code}{suffix}"
    except Exception:
        logger.exception("Falha ao gerar link seguro do dashboard para user_id=%s", user_id)
        return None
