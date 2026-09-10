"""Espaço de estados do aviso de fim do Grátis, compartilhado pelos dois
arquivos de teste do assunto.

Sem prefixo `test_` de propósito (mesmo motivo de `_billing_grants_helpers.py`):
o pytest não coleta este arquivo. Ele existe para
`test_aviso_fim_do_gratis.py` (o diferencial SQL × predicado) e
`test_aviso_fim_do_gratis_lote.py` (o laço do script) não carregarem duas
cópias do mesmo `montar_base` — cópia de helper é como dois testes passam a
medir coisas diferentes achando que medem a mesma.

Cada conta aqui é um jeito conhecido de a SQL do aviso e
`plan_service.tem_direito_hoje` divergirem; os controles declarados dos dois
arquivos citam estes nomes.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from _billing_grants_helpers import conta as _conta
from db.connection import get_conn
from db_support import invalidate_auth_user_cache
from scripts.aviso_fim_do_gratis import EVENTO, listar_contas_do_aviso

# Faixa própria deste assunto — a SQL do aviso é uma VARREDURA DE BASE (não tem
# `user_id`), então o universo da comparação é recortado por estes ids.
BASE = 771000

GRANDFATHERED = BASE + 1
PAGO_VIGENTE = BASE + 2
PAGO_EXPIRADO = BASE + 3
TRIALING = BASE + 4
GRANT_ADMIN = BASE + 5
CARENCIA_3D = BASE + 6
CARENCIA_8D = BASE + 7
CAIXA_MISTA = BASE + 8
FREE_PURO = BASE + 9
SEM_PLANO = BASE + 10
NUNCA_ESCOLHEU = BASE + 11
SO_WHATSAPP = BASE + 12
PAGO_EXPIRADO_EM_CARENCIA = BASE + 13

UNIVERSO = {
    GRANDFATHERED, PAGO_VIGENTE, PAGO_EXPIRADO, TRIALING, GRANT_ADMIN,
    CARENCIA_3D, CARENCIA_8D, CAIXA_MISTA, FREE_PURO, SEM_PLANO,
    PAGO_EXPIRADO_EM_CARENCIA,
}

# Quem PERDE acesso no corte — a lista que o e-mail tem de alcançar.
ESPERADO_AVISAR = {PAGO_EXPIRADO, CARENCIA_8D, FREE_PURO, SEM_PLANO}


def agora() -> datetime:
    return datetime.now(timezone.utc)


def montar(uid, plan, expires, status="active", relogio_dias=None,
           escolheu=True) -> None:
    """Uma conta do espaço de estados. `relogio_dias` = idade de
    `past_due_since` em dias (None = relógio zerado)."""
    from db import ensure_user
    ensure_user(uid)          # FK auth_accounts.user_id → users.id
    _conta(uid, plan, expires, status)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update auth_accounts set plan_selected_at = %s,"
                "       past_due_since = %s where user_id = %s",
                (agora() - timedelta(days=200) if escolheu else None,
                 None if relogio_dias is None
                 else agora() - timedelta(days=relogio_dias),
                 uid),
            )
        conn.commit()
    invalidate_auth_user_cache(uid)


def montar_base() -> None:
    hoje = agora()
    montar(GRANDFATHERED, "pro", None, "active")
    montar(PAGO_VIGENTE, "essencial", hoje + timedelta(days=30), "active")
    montar(PAGO_EXPIRADO, "pro", hoje - timedelta(days=1), "canceled")
    montar(TRIALING, "pro_max", hoje + timedelta(days=10), "trialing")
    montar(CARENCIA_3D, "free", None, "past_due", relogio_dias=3)
    # O ASSINANTE REAL em dunning: plano pago, período vencido, relógio na
    # carência. Os dois `CARENCIA_*` são `plan='free'` e não cobrem isto —
    # ver o controle E.
    montar(PAGO_EXPIRADO_EM_CARENCIA, "pro", hoje - timedelta(days=1),
           "past_due", relogio_dias=3)
    montar(CARENCIA_8D, "free", None, "past_due", relogio_dias=8)
    montar(CAIXA_MISTA, "free", None, "Past_Due", relogio_dias=3)
    montar(FREE_PURO, "free", None, "inactive")
    # `plan` é NOT NULL no schema, então "sem plano" no banco é string vazia —
    # o valor fora da escada que os dois lados normalizam (`coalesce`/`get`).
    montar(SEM_PLANO, "", None, "inactive")
    montar(NUNCA_ESCOLHEU, "free", None, "inactive", escolheu=False)

    # Grant do admin: a MESMA escrita do painel (`set_account_plan`), que grava
    # `plan`/`plan_expires_at` e não fala com a Stripe.
    montar(GRANT_ADMIN, "free", None, "inactive")
    from core.admin_dashboard import set_account_plan
    set_account_plan("pro", 12, user_id=GRANT_ADMIN)
    invalidate_auth_user_cache(GRANT_ADMIN)


def avisados() -> set[int]:
    return {int(r["user_id"]) for r in listar_contas_do_aviso()}


def limpar_dedupe() -> None:
    """Apaga a marca de dedupe destas contas. Sem isto, um teste que roda
    `--apply` cala o próximo — dependência de ORDEM disfarçada de teste verde."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "delete from system_event_logs where event_type = %s"
                "   and user_id = any(%s)",
                (EVENTO, sorted(UNIVERSO)),
            )
        conn.commit()
