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

from core.services import push_service

from db import (
    detect_open_finance_salary,
    limpar_push_reconectar,
    list_connections_needing_reconnect,
    list_identities_by_user,
    list_open_finance_user_ids,
    list_push_tokens,
    push_bank_reconnect_bloqueado,
    reivindicar_push_reconectar,
    soltar_push_reconectar,
)

logger = logging.getLogger(__name__)

# Dedupe por marcador em `system_event_logs` (mesmo par leitura/escrita do
# `engagement_scheduler`), porque a tarefa roda a cada 6 h e sem isto o mesmo
# aviso sairia 4x por dia enquanto a condição durar. 7 d: um "reconecte" por
# semana enquanto a tela pedir ação (o antigo `within_days=7` saiu no PR-D da
# Onda 5, junto com a perna do consentimento); 25 d dá um aviso por mês
# de salário e pega o mês seguinte com 28-31 d (decisão do dono: uma vez por mês).
OF_RECONNECT_EVENT = "of_reconnect_template_sent"
OF_RECONNECT_DEDUPE_DAYS = 7.0
OF_SALARY_EVENT = "of_salary_template_sent"
OF_SALARY_DEDUPE_DAYS = 25.0
# Push do app (APNs): texto genérico, sem nome de banco (regra da Apple, push_service).
PUSH_TITULO = "PigBank"
PUSH_CORPO = "Um banco precisa de atenção. Toque para ver."
# Parâmetros NOMEADOS do corpo ({{banks}}, {{valor}}): têm de bater com o template aprovado na Meta.
OF_RECONNECT_PARAM = "banks"
OF_SALARY_PARAM = "valor"  # template de salário ainda não existe na Meta; criar com este nome.


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
        params = {OF_SALARY_PARAM: fmt_brl(float(cand["valor"]))}
        enviou = False
        for to in _targets(uid):
            try:
                if send_template(to, cfg["name"], language_code=cfg["language_code"], named_body_params=params) is None:
                    # `None` é o 401 da Meta (token inválido/expirado), que `send_template` NÃO levanta
                    # (`adapters/whatsapp/wa_client.py`, ramo `whatsapp_token_invalid`); todo outro erro chega no `except`.
                    logger.warning("[of_proactive] salario: send_template recusado user_id=%s erro=token_invalido", uid,
                                   extra={"user_id": uid})
                    continue
                sent += 1
                enviou = True
            except Exception as exc:
                # Só o TIPO: `str(exc)` pode ecoar o número (PII).
                logger.warning("[of_proactive] salario: send_template falhou user_id=%s erro=%s",
                               uid, type(exc).__name__, extra={"user_id": uid})
        if enviou:
            log_system_event_sync(
                "info", OF_SALARY_EVENT, "Confirmacao de salario enviada via template WhatsApp.",
                source="open_finance_proactive", user_id=uid,
                details={"template_name": cfg["name"], "launch_id": cand["launch_id"]},
            )
    return {"ok": True, "dormant": False, "sent": sent}


def run_reconnect_notifications() -> dict:
    """Avisa usuários cujos bancos a tela manda reconectar (`avisa_reconectar`). Dormente sem template."""
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
                if send_template(to, cfg["name"], language_code=cfg["language_code"], named_body_params={OF_RECONNECT_PARAM: banks}) is None:
                    # Mesmo `None` do salário: 401 (`whatsapp_token_invalid`) não levanta.
                    logger.warning("[of_proactive] reconnect: send_template recusado user_id=%s erro=token_invalido", uid,
                                   extra={"user_id": uid})
                    continue
                sent += 1
                enviou = True
            except Exception as exc:
                logger.warning("[of_proactive] reconnect: send_template falhou user_id=%s erro=%s",
                               uid, type(exc).__name__, extra={"user_id": uid})
        if enviou:
            log_system_event_sync(
                "info", OF_RECONNECT_EVENT, "Aviso de reconexao enviado via template WhatsApp.",
                source="open_finance_proactive", user_id=uid,
                details={"template_name": cfg["name"], "connections": len(need)},
            )
    return {"ok": True, "dormant": False, "sent": sent}


def run_reconnect_push() -> dict:
    """Push "um banco precisa de atenção" no app, UM por usuário por episódio.

    Mesma regra do aviso do WhatsApp (`list_connections_needing_reconnect`). A
    trava `open_finance_connections.push_reconectar_em` garante um push por
    transição (a conexão tem vários escritores e a transição também acontece só
    com o tempo); ela volta a NULL quando a conexão volta a ficar saudável na tela
    (não basta deixar de pedir reconexão: LOGIN_ERROR → ERROR → LOGIN_ERROR é um
    episódio só), ou quando o envio não chega a aparelho nenhum. Dormente sem as envs do APNs.
    """
    if not push_service.is_configured():
        return {"dormant": True}
    sent = 0
    for uid in list_open_finance_user_ids():
        try:
            # Limpa ANTES de ler: quem foi curado entre a leitura e a limpeza seria
            # lido como quebrado e reivindicado de novo (2º push no mesmo episódio).
            limpar_push_reconectar(uid)
            need = list_connections_needing_reconnect(uid)
            ids = [int(c["id"]) for c in need]
            if not ids:
                continue
            # Barras ANTES de reivindicar: quem não pode receber agora não gasta a trava.
            if not filtrar_por_acesso([uid]):
                continue
            if push_bank_reconnect_bloqueado(uid) or not list_push_tokens(uid):
                continue
            claimed = reivindicar_push_reconectar(uid, ids)
            if not claimed:
                continue
            n = 0
            try:
                n = push_service.send_push_to_user(
                    uid, PUSH_TITULO, PUSH_CORPO,
                    data={"rota": "conexoes"}, collapse_id="of-reconectar",
                )
            finally:
                if n == 0:  # nada chegou (ou o envio levantou): a próxima varredura tenta
                    soltar_push_reconectar(uid, claimed)
            sent += n
        except Exception as exc:
            logger.warning("[of_proactive] push reconnect falhou user_id=%s erro=%s",
                           uid, type(exc).__name__, extra={"user_id": uid})
    return {"dormant": False, "sent": sent}
