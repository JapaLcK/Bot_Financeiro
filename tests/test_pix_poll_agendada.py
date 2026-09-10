"""`agendada` no POLL: a mesma fórmula do checkout, medida na mesma linha.

O grupo do Pix era CEGO a `agendada` sempre falso. `test_pix_rotas_billing.py`
só afirma `False` para `access_starts_at` nulo — e um poll que devolvesse
`False` SEMPRE passava por ele. Medido: com `"agendada": False` fixo em
`frontend/routes/billing_pix.py`, `tests/test_pix_*.py` dava 309 passed e
`tests/frontend/welcome_pro_pix.test.mjs` 25 pass / 0 fail. Zero vermelho —
enquanto na /home quem comprou agendado leria "seu ano já começou".

A fórmula (`inicio > agora`) está medida em `test_pix_checkout.py`, mas para o
`resposta()` do CHECKOUT; quem a /home lê desde o modal novo é o POLL. Aqui as
DUAS montagens são comparadas na MESMA linha, em três datas (§0.7).

CONTROLE NEGATIVO (rodado): `"agendada": False` fixo no poll → este arquivo
vermelho no caso `+365d`. POSITIVO: os casos `-1s` e nulo, que provam que o
conserto não é "devolve True".
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.billing_pix as rotas
from _billing_grants_helpers import conta
from core.services.pix_checkout_resposta import resposta
from db.connection import get_conn
from db.pix_charges import buscar_por_public_token, criar_cobranca

client = TestClient(dashboard.app)


def test_poll_e_checkout_dao_o_mesmo_agendada_na_mesma_linha(user_id, monkeypatch):
    conta(user_id, "free", None)
    linha = criar_cobranca(user_id, public_token=uuid.uuid4().hex, plan="pro_max",
                           plan_stored="pro_max", price_cents=49900, credit_cents=0,
                           amount_cents=49900, duration_days=365)
    monkeypatch.setattr(rotas.shared, "resolve_dashboard_user_id", lambda req: user_id)
    agora = datetime.now(timezone.utc)

    for quando, esperado in ((agora + timedelta(days=365), True),
                             (agora - timedelta(seconds=1), False),
                             (None, False)):
        # `access_starts_at` só é escrito no PAGAMENTO (§7) e o que se mede aqui
        # é a LEITURA: update cru, sem passar pela saga.
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("update pix_charges set access_starts_at = %s where id = %s",
                        (quando, linha["id"]))
            conn.commit()
        do_poll = client.get(f"/billing/pix/{linha['public_token']}").json()["agendada"]
        do_checkout = resposta(
            buscar_por_public_token(user_id, linha["public_token"]), "000201")["agendada"]
        assert do_poll is esperado and do_poll == do_checkout, (
            f"starts_at={quando}: poll={do_poll!r} checkout={do_checkout!r}, "
            f"esperado {esperado!r}"
        )
