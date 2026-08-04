"""Avisos proativos do Open Finance: salário identificado (Fase 5) e reconectar (P1 #6).

A DETECÇÃO já é testada (db.detect_open_finance_salary / db.list_connections_needing_reconnect).
Aqui é só o ENVIO via template WhatsApp — DORMENTE até o Lucas criar o template Meta e setar
a env (mesmo padrão do lembrete de boleto). Sem env, cada função retorna na hora e nada é enviado.
"""
from __future__ import annotations

import os

from utils_text import fmt_brl

from db import (
    detect_open_finance_salary,
    list_connections_needing_reconnect,
    list_identities_by_user,
    list_open_finance_user_ids,
)


def _template_cfg(name_env: str) -> dict | None:
    name = (os.getenv(name_env) or "").strip()
    if not name:
        return None
    return {"name": name, "language_code": (os.getenv("WA_PROACTIVE_TEMPLATE_LANGUAGE") or "pt_BR").strip()}


def _targets(user_id: int) -> list[str]:
    # Import tardio pra evitar ciclo com o app WhatsApp.
    from adapters.whatsapp.wa_app import _dedupe_whatsapp_targets
    try:
        return _dedupe_whatsapp_targets(list_identities_by_user(user_id))
    except Exception:
        return []


def run_salary_notifications() -> dict:
    """Pra cada usuário com OF, se há salário recém-identificado, manda confirmação. Dormente sem template."""
    cfg = _template_cfg("OF_SALARY_TEMPLATE_NAME")
    if not cfg:
        return {"ok": True, "dormant": True, "sent": 0}

    from adapters.whatsapp.wa_client import send_template
    sent = 0
    for uid in list_open_finance_user_ids():
        try:
            cand = detect_open_finance_salary(uid)
        except Exception:
            continue
        if not cand:
            continue
        params = [fmt_brl(float(cand["valor"]))]
        for to in _targets(uid):
            try:
                send_template(to, cfg["name"], language_code=cfg["language_code"], named_body_params=params)
                sent += 1
            except Exception:
                pass
    return {"ok": True, "dormant": False, "sent": sent}


def run_reconnect_notifications() -> dict:
    """Avisa usuários cujos bancos estão em erro / com consentimento vencendo. Dormente sem template."""
    cfg = _template_cfg("OF_RECONNECT_TEMPLATE_NAME")
    if not cfg:
        return {"ok": True, "dormant": True, "sent": 0}

    from adapters.whatsapp.wa_client import send_template
    sent = 0
    for uid in list_open_finance_user_ids():
        try:
            need = list_connections_needing_reconnect(uid)
        except Exception:
            continue
        if not need:
            continue
        banks = ", ".join(sorted({(n.get("institution_name") or "seu banco") for n in need}))
        for to in _targets(uid):
            try:
                send_template(to, cfg["name"], language_code=cfg["language_code"], named_body_params=[banks])
                sent += 1
            except Exception:
                pass
    return {"ok": True, "dormant": False, "sent": sent}
