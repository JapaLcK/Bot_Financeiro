"""Avisos proativos do Open Finance: salário identificado (Fase 5) e reconectar (P1 #6).

A DETECÇÃO já é testada (db.detect_open_finance_salary / db.list_connections_needing_reconnect).
Aqui é só o ENVIO via template WhatsApp — DORMENTE até o Lucas criar o template Meta e setar
a env (mesmo padrão do lembrete de boleto). Sem env, cada função retorna na hora e nada é enviado.
"""
from __future__ import annotations

import logging
import os

from core.observability import log_system_event_sync, recent_event_exists
from core.reports.reports_daily import filtrar_por_acesso
from utils_text import fmt_brl

from db import (
    detect_open_finance_salary,
    list_connections_needing_reconnect,
    list_identities_by_user,
    list_open_finance_user_ids,
)

logger = logging.getLogger(__name__)

# Dedupe por marcador em `system_event_logs` (mesmo par leitura/escrita do
# `engagement_scheduler`), porque a tarefa roda a cada 6 h e sem isto o mesmo
# aviso sairia 4x por dia enquanto a condição durar. 7 d é o mesmo horizonte de
# `list_connections_needing_reconnect(within_days=7)`; 25 d dá um aviso por mês
# de salário e pega o mês seguinte com 28-31 d (decisão do dono: uma vez por mês).
OF_RECONNECT_EVENT = "of_reconnect_template_sent"
OF_RECONNECT_DEDUPE_DAYS = 7.0
OF_SALARY_EVENT = "of_salary_template_sent"
OF_SALARY_DEDUPE_DAYS = 25.0


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
        # O corte do Grátis, na MESMA posição dos outros laços proativos
        # (`adapters/whatsapp/wa_app.py`, `core/reports/reports_daily.py`):
        # depois dos filtros baratos. `list_open_finance_user_ids` não tem termo
        # de acesso, e sem esta linha um cortado com conexões retidas continua
        # recebendo o VALOR DO SALÁRIO por template pago.
        if not filtrar_por_acesso([uid]):
            continue
        if recent_event_exists(OF_SALARY_EVENT, uid, OF_SALARY_DEDUPE_DAYS):
            continue
        params = [fmt_brl(float(cand["valor"]))]
        enviou = False
        for to in _targets(uid):
            try:
                if send_template(to, cfg["name"], language_code=cfg["language_code"], named_body_params=params) is None:
                    # `None` é o 401 da Meta (token inválido/expirado), que `send_template` NÃO levanta
                    # (`adapters/whatsapp/wa_client.py`, ramo `whatsapp_token_invalid`); todo outro erro chega no `except`.
                    logger.warning("[of_proactive] salario: send_template recusado user_id=%s erro=token_invalido", uid)
                    continue
                sent += 1
                enviou = True
            except Exception as exc:
                # Só o TIPO: `str(exc)` pode ecoar o número (PII).
                logger.warning("[of_proactive] salario: send_template falhou user_id=%s erro=%s",
                               uid, type(exc).__name__)
        if enviou:
            log_system_event_sync(
                "info", OF_SALARY_EVENT, "Confirmacao de salario enviada via template WhatsApp.",
                source="open_finance_proactive", user_id=uid,
                details={"template_name": cfg["name"], "launch_id": cand["launch_id"]},
            )
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
        # Mesma razão do irmão do salário, logo acima.
        if not filtrar_por_acesso([uid]):
            continue
        if recent_event_exists(OF_RECONNECT_EVENT, uid, OF_RECONNECT_DEDUPE_DAYS):
            continue
        banks = ", ".join(sorted({(n.get("institution_name") or "seu banco") for n in need}))
        enviou = False
        for to in _targets(uid):
            try:
                if send_template(to, cfg["name"], language_code=cfg["language_code"], named_body_params=[banks]) is None:
                    # Mesmo `None` do salário: 401 (`whatsapp_token_invalid`) não levanta.
                    logger.warning("[of_proactive] reconnect: send_template recusado user_id=%s erro=token_invalido", uid)
                    continue
                sent += 1
                enviou = True
            except Exception as exc:
                logger.warning("[of_proactive] reconnect: send_template falhou user_id=%s erro=%s",
                               uid, type(exc).__name__)
        if enviou:
            log_system_event_sync(
                "info", OF_RECONNECT_EVENT, "Aviso de reconexao enviado via template WhatsApp.",
                source="open_finance_proactive", user_id=uid,
                details={"template_name": cfg["name"], "connections": len(need)},
            )
    return {"ok": True, "dormant": False, "sent": sent}
