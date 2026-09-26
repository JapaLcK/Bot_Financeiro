"""`/auth/me` → `cobranca_em_atraso`: o front troca "assine → /precos" por
"Atualizar cartão → /conta" só na carência de cobrança (assinante com tier
`free`, que a /precos recusaria com 409). O front não recalcula: só lê o campo.

Chama o handler direto, com o `user_id` que o `Depends` resolveria.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import db
import db_support
from conftest import em_carencia, promote_to_pro
import frontend.finance_bot_websocket_custom as dashboard


def _me(uid: int) -> dict:
    return asyncio.run(dashboard.auth_me(user_id=uid))


def test_carencia_da_true_sem_reler_a_conta(user_id, monkeypatch):
    em_carencia(user_id)
    lidas = []
    real = db.get_auth_user
    monkeypatch.setattr(db, "get_auth_user", lambda uid: lidas.append(uid) or real(uid))

    assert _me(user_id)["cobranca_em_atraso"] is True
    # Só a leitura do próprio handler: o julgamento usa o `user_dict` em mão.
    assert lidas == [user_id]


def test_plus_vigente_da_false(user_id):
    promote_to_pro(user_id)
    assert _me(user_id)["cobranca_em_atraso"] is False


def test_cortado_da_false(user_id):
    promote_to_pro(user_id)
    db.mark_plan_selected(user_id)
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(  # plano vencido, `canceled`, sem relógio de carência
            "update auth_accounts set plan_expires_at=%s, past_due_since=null,"
            "       last_payment_status='canceled' where user_id=%s",
            (datetime.now(timezone.utc) - timedelta(days=1), user_id),
        )
        conn.commit()
    db_support.invalidate_auth_user_cache(user_id)

    me = _me(user_id)
    assert me["plan_tier"] == "free" and me["cobranca_em_atraso"] is False
