"""A marca `launches.origem` (PR 2a): quem grava e quem não grava, pelo canal de produção.

Marca: o escritor da carteira por padrão (`/app`, WhatsApp, quick_entry, IA, saldo inicial e
ajuste) e o saque/depósito automático da Q41 (`_credita`, literal comparado com
`ORIGEM_CARTEIRA`). Cada linha marcada sai na `GET /api/v2/lancamentos` com o `pode` da carteira.
Não marca: antecipar parcela e estorno de fatura do cartão manual (`origem=None`: o `efeitos`
não guarda o que desfazer), a sombra do Open Finance, caixinha e aporte.

Controles por mutação (relato do PR): padrão do escritor em `None` deixa vermelho
`test_todo_canal_da_carteira_marca`; tirar o `origem=None` da antecipação deixa vermelho
`test_o_que_nao_e_carteira_fica_sem_marca`.
"""
from __future__ import annotations

from datetime import timedelta

import db
from core.services.quick_entry import handle_quick_entry
from conftest import usuario_pagante
from db.accounts import ORIGEM_CARTEIRA
from test_category_launches_query import _cliente_logado
from tests._fusao_of_helpers import ia_fora, manda  # noqa: F401 (fixture)
from tests._of_cash_helpers import caixa, conecta, sync, tx  # noqa: F401 (fixture)
from tests._patrimonio_helpers import q
from tests.test_api_v2_lancamentos import ok
from tests.test_api_v2_resumo_mes import libera  # noqa: F401 (fixture)
from tests.test_fonte_unica_q36 import _ia
from tests.test_manual_launches_carteira_piggy import _importa_of_tx
from utils_date import today_tz

TUDO = ["categoria", "descricao", "data", "valor", "apagar"]


def _linhas(uid) -> list[dict]:
    return q("select id, tipo, alvo, valor, origem from launches where user_id = %s order by id", (uid,), True)


def _ok(r):
    assert r.status_code == 200, r.text
    return r.json()


def test_todo_canal_da_carteira_marca(libera, ia_fora):
    uid = usuario_pagante()
    client, h = _cliente_logado(uid)
    _ok(client.post(f"/account/{uid}/initial-balance", headers=h, json={"amount": 300}))  # conta virgem
    _ok(client.post(f"/launches/{uid}", headers=h, json={"tipo": "despesa", "valor": 30, "alvo": "feira"}))
    _ok(client.post(f"/account/{uid}/adjust-balance", headers=h, json={"target_balance": 250}))
    # a conversa: duas mensagens de assuntos diferentes pelo handle_incoming
    r1 = manda(uid, "gastei 50 no mercado em dinheiro")
    r2 = manda(uid, "recebi 100 de salário em dinheiro")
    handle_quick_entry(uid, "gastei 15 na padaria em dinheiro")
    _ia(uid, "add_launch", {"tipo": "despesa", "valor": 20, "alvo": "banca", "forma_pagamento": "dinheiro"})
    libera(uid)

    linhas = _linhas(uid)
    assert len(linhas) == 7, (linhas, r1, r2, ia_fora)
    assert {r["origem"] for r in linhas} == {ORIGEM_CARTEIRA}, linhas
    itens = {i["id"]: i for i in ok(uid)["itens"]}
    for r in linhas:
        i = itens[f"l{r['id']}"]
        assert (i["origem"], i["pode"]) == ("carteira", TUDO), (r, i)
    assert not ia_fora, ia_fora


def test_saque_automatico_da_q41_marca_com_a_mesma_constante(caixa, libera):
    uid = usuario_pagante()
    c = conecta(uid, f"item-{uid}")
    sync(c, uid, [], saldo="1000")
    sync(c, uid, [tx("t1", -200, today_tz())], saldo="800")
    libera(uid)
    (saque,) = q("select id, origem from launches where user_id = %s and coalesce(source, 'manual') = 'manual'",
                 (uid,), True)
    assert saque["origem"] == ORIGEM_CARTEIRA  # o literal de `_credita` é a constante (§0.7)
    i = {i["id"]: i for i in ok(uid)["itens"]}[f"l{saque['id']}"]
    assert (i["origem"], i["pode"]) == ("carteira", ["descricao", "apagar"])


def test_o_que_nao_e_carteira_fica_sem_marca():
    uid = usuario_pagante()
    db.set_balance(uid, 1000)  # sem lançamento: só para a caixinha e o aporte terem de onde sair
    cartao = db.create_card(uid, "Nubank", closing_day=10, due_day=17)
    grupo, _ = db.add_credit_purchase_installments(uid, cartao, 300, "casa", "3x", today_tz(), 3)
    db.anticipate_installment(uid, grupo["group_id"])
    _, _, fatura = db.add_credit_purchase(uid, cartao, 100, "outros", "compra", today_tz() + timedelta(days=40))
    q("update credit_bills set paid_amount = total + 50 where id = %s returning id", (fatura,))
    assert db.rebuild_bill_totals(uid, refund_overpayments=True)["refunded"] > 0
    _importa_of_tx(uid, today_tz(), "42.00", "PADARIA", f"tx-origem-{uid}")
    db.create_pocket(uid, "Reserva")
    db.pocket_deposit_from_account(uid, "Reserva", 10)
    db.create_investment_db(uid, "CDB", 1.0, "cdi")
    db.investment_deposit_from_account(uid, "CDB", 10)

    linhas = _linhas(uid)
    alvos = {(r["alvo"] or "").split(":")[0] for r in linhas}
    assert {"antecipacao", "estorno_fatura", "PADARIA"} <= alvos, linhas
    assert {"deposito_caixinha", "aporte_investimento"} <= {r["tipo"] for r in linhas}, linhas
    assert [r for r in linhas if r["origem"] is not None] == []

