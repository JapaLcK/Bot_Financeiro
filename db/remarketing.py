"""db/remarketing.py — a régua de remarketing do checkout abandonado
(docs/plano-remarketing.md). PR 1: só o T0 e o "já comprou"; nada envia.
"""
from __future__ import annotations

from .connection import get_conn

# `ja_comprou(user_id)` é a ÚNICA porta: o lote do PR 6 a chama por candidato. Este fragmento
# é só a parte SQL (parcial: a allowlist e a carência de inadimplência ficam em Python), e é
# parametrizado por `_params(uid)`. Compra = QUALQUER uma das pernas (plano §1), todas por
# `user_id`. Grant de qualquer fonte e status: trial, revogado, vencido, admin, pix e legacy
# contam (quem já foi cliente fica fora, P2). As demais pegam o que não tem grant: ex-assinante
# de antes de `plan_grants`, vigente que o backfill de boot não alcança (`plan_expires_at`
# nulo), Pix pago cujo dreno falhou antes do grant. Plano pago = a lista do `plan_service`
# (vigente ou vencido); status e Pix = só os que existem depois de haver pagamento (`_params`).
_JA_COMPROU_SQL = """(
  exists (select 1 from plan_grants where user_id = %(uid)s)
  or exists (select 1 from checkout_funnel_events where user_id = %(uid)s and kind = 'completed')
  or exists (select 1 from pix_charges where user_id = %(uid)s and status = any(%(pix_pagos)s))
  or exists (select 1 from auth_accounts where user_id = %(uid)s
             and lower(coalesce(plan, '')) = any(%(pagos)s))
  or exists (select 1 from auth_accounts where user_id = %(uid)s
             and lower(coalesce(last_payment_status, '')) = any(%(assinou)s))
)"""

# Status de `pix_charges` (alfabeto: check `pix_charges_status_valido`, db/schema.py) em que o
# dinheiro chegou alguma vez: paid, paid_orphan (pago, sem dono ainda) e os três de estorno,
# que só se alcançam a partir de um pagamento. FORA: draft, creating, pending, canceling,
# canceled e expired (nunca pagou). Não há constante pronta (as outras são tuplas locais).
_PIX_PAGOS = ("paid", "paid_orphan", "refunded", "refunded_partial", "chargeback")


def _params(uid: int) -> dict:
    from core.services import plan_service  # noqa: PLC0415 — db/ não importa core/ no topo
    from core.services.billing_dunning import PAST_DUE_PAYMENT_STATUSES

    return {
        "uid": uid,
        "pix_pagos": list(_PIX_PAGOS),
        # a mesma derivação de `scripts/aviso_fim_do_gratis.PLANOS_PAGOS`
        "pagos": sorted(p for p, t in plan_service._STORED_PLAN_TO_TIER.items() if t != "free"),
        # Status de `last_payment_status` que só existem depois de haver assinatura:
        # grandfathered (vitalício), active, trialing, canceled e os de atraso (past_due,
        # unpaid). FORA: `incomplete` e `incomplete_expired` (1ª cobrança que nunca fechou:
        # abandono, o público da régua) e `inactive` (o padrão da conta nova).
        "assinou": ["grandfathered", "active", "trialing", "canceled",
                    *(x for x in PAST_DUE_PAYMENT_STATUSES if x != "incomplete")],
    }


def registrar_t0(user_id: int, origem: str) -> None:
    """Grava o T0 uma vez e nunca o zera: a 2ª chamada não muda nada.
    Levanta em falha; quem chama decide (as duas rotas engolem e logam)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "insert into remarketing_regua (user_id, origem) values (%s, %s)"
            " on conflict (user_id) do nothing",
            (int(user_id), origem),
        )
        conn.commit()


def ja_comprou(user_id: int) -> bool:
    """O usuário já comprou, em qualquer sentido (P2: reconquista é outra campanha).

    O Pix recebido alguma vez é uma perna SQL (`pix_charges`), não `pix_cobre_agora`: P2 não
    pede janela em curso, e ela importa o monólito.
    "Tem acesso pago agora" vem de `plan_service.tem_direito_hoje` (plano vigente OU
    carência de inadimplência), a regra do gate, sem cópia. Não uso `has_app_access`: ela
    devolve True para todo mundo com `ACCESS_GATE_ENABLED=0` e muda de regra com o v2
    desligado. `tem_direito_hoje` é pura sobre um dict: leio as 4 colunas que ela usa, sem
    `get_auth_user` (que decifra PII, grava auditoria e tem cache de 10 s).
    Exclusão agendada NÃO entra aqui: é outra pergunta (o `/retomar` manda quem comprou
    para o login e quem está saindo para a /precos), e o PR 6 a lê ao lado.
    """
    from core.services import plan_service  # noqa: PLC0415

    uid = int(user_id)
    if uid in plan_service._ACCESS_ALLOWLIST:
        return True
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(f"select {_JA_COMPROU_SQL} as ok", _params(uid))
        if cur.fetchone()["ok"]:
            return True
        # `auth_accounts.user_id` não é unique: basta uma linha com direito.
        cur.execute("select plan, plan_expires_at, past_due_since, last_payment_status"
                    " from auth_accounts where user_id = %s", (uid,))
        contas = cur.fetchall()
        conn.commit()
    return any(plan_service.tem_direito_hoje(c) for c in contas)
