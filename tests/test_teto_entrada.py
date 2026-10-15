"""TETO_ENTRADA (R$ 1 bi, decisão do dono 2026-10-15): teto de NEGÓCIO de todo valor digitado.

O banco segue em VALOR_MAX (1e13, CHECK `finito_*`): dado antigo acima do teto continua legível,
somável, apagável, pagável e editável em campo que não é valor (categoria, nota, data).
Cada porta mede os dois lados da borda: 1e9 entra, 1e9 + 1 centavo não."""
from decimal import Decimal

import pytest

import db
from conftest import usuario_pagante
from test_valor_nao_finito_dinheiro import _conta, _diga
from test_valor_nao_finito_painel import _cliente
from utils_text import VALOR_MAX, valor_finito, valor_perigoso, valor_valido

LANC = "select valor::text v from launches where user_id=%s"


def test_unidade_borda_do_teto():
    assert valor_valido(1e9) and not valor_valido(1e9 + 0.01)
    assert valor_valido(Decimal("1000000000")) and not valor_valido(Decimal("1000000000.0001"))
    assert valor_perigoso("1000000000", 1e9) is None
    assert valor_perigoso("1000000000,01", 1000000000.01) == "nao_entendi"
    assert valor_finito(9e12) and not valor_finito(VALOR_MAX + 1)  # controle: o técnico não mudou
    assert valor_finito(1e9, 10**9) and not valor_finito(-1e9 - 0.01, 10**9)


def test_whatsapp_no_teto_registra_acima_nao_grava_e_a_conversa_segue():
    uid = usuario_pagante()

    assert "Despesa registrada" in _diga(uid, "gastei 1000000000 no mercado")
    assert len(_conta(LANC, uid)) == 1

    _diga(uid, "gastei 1000000000,01 no mercado")
    assert len(_conta(LANC, uid)) == 1, "um centavo acima do teto gravou"

    assert "Despesa registrada" in _diga(uid, "gastei 50 no mercado")
    assert len(_conta(LANC, uid)) == 2


def test_tool_da_ia_no_teto_grava_e_acima_recusa():
    from core.services.ai_chat.tools.launches import _add_launch_execute

    uid = usuario_pagante()
    recusa = _add_launch_execute(uid, {"tipo": "despesa", "valor": 1e9 + 0.01, "descricao": "x"})
    assert "registrada" not in recusa and _conta(LANC, uid) == [], recusa

    ok = _add_launch_execute(uid, {"tipo": "despesa", "valor": 1e9, "descricao": "x"})
    assert "registrada" in ok.lower() and len(_conta(LANC, uid)) == 1, ok


def test_painel_lancamento_no_teto_200_e_acima_400():
    uid = usuario_pagante()
    c, h = _cliente(uid)
    corpo = '{{"tipo":"despesa","valor":{},"alvo":"x","nota":"x"}}'

    assert c.post(f"/launches/{uid}", headers=h, content=corpo.format("1000000000")).status_code == 200
    assert c.post(f"/launches/{uid}", headers=h, content=corpo.format("1000000000.01")).status_code == 400
    assert len(_conta(LANC, uid)) == 1


def test_painel_ajuste_de_saldo_alvo_no_teto_200_acima_400_e_limpeza_de_dado_antigo_200():
    uid = usuario_pagante()
    c, h = _cliente(uid)
    url = f"/account/{uid}/adjust-balance"

    assert c.post(url, headers=h, content='{"target_balance":1000000000}').status_code == 200
    assert c.post(url, headers=h, content='{"target_balance":1000000000.01}').status_code == 400

    # Carteira antiga acima do teto (semeada por SQL): zerar é o caminho de limpeza, e o
    # DELTA (-9e12) não tem teto, só o alvo.
    db.set_balance(uid, Decimal("9000000000000"))
    r = c.post(url, headers=h, content='{"target_balance":0}')
    assert r.status_code == 200, r.text
    assert float(_conta("select balance::text b from accounts where user_id=%s", uid)[0]["b"]) == 0.0


def test_dado_antigo_acima_do_teto_continua_editavel_e_a_conta_antiga_paga_sem_valor():
    import db.bills as B
    from test_bill_amount_pending import _monta_conta_variavel

    uid = usuario_pagante()
    c, h = _cliente(uid)
    # lançamento antigo de 9e12, semeado por SQL (a porta de hoje não o aceitaria)
    lid, *_ = db.add_launch_and_update_balance(user_id=uid, tipo="despesa", valor=1.0, alvo="x", nota="x")
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("update launches set valor=9000000000000 where id=%s", (lid,))
        conn.commit()
    r = c.patch(f"/launches/{uid}/{lid}", headers=h, content='{"categoria":"moradia"}')
    assert r.status_code == 200, r.text

    conta = _monta_conta_variavel(uid)
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("update bill_instances set amount=2000000000 where id=%s", (conta["id"],))
        conn.commit()
    pago = B.mark_bill_paid(uid, int(conta["id"]), metodo="carteira")
    assert pago is not None and pago["status"] == "paid"


def test_painel_renda_do_mes_no_teto_200_acima_400_e_a_renda_gravada_nao_muda():
    uid = usuario_pagante()
    c, h = _cliente(uid)
    url = f"/household-budget/{uid}/income"
    sql = "select amount::text v from household_budget_income where user_id=%s"
    corpo = '{{"month":"2026-10","amount":{}}}'

    assert c.put(url, headers=h, content=corpo.format("1000000000")).status_code == 200
    assert c.put(url, headers=h, content=corpo.format("1000000000.01")).status_code == 400
    assert [float(r["v"]) for r in _conta(sql, uid)] == [1e9]


def test_painel_investimento_aporte_inicial_no_teto_200_acima_400_e_nada_criado():
    uid = usuario_pagante()
    db.set_balance(uid, Decimal("5000000000"))
    c, h = _cliente(uid)
    url = f"/investments/{uid}"
    corpo = '{{"name":"{}","rate":1,"period":"yearly","initial_amount":{}}}'
    inv = "select 1 from investments where user_id=%s"

    assert c.post(url, headers=h, content=corpo.format("A", "1000000000")).status_code == 200
    n_inv, n_lanc = len(_conta(inv, uid)), len(_conta(LANC, uid))
    assert n_inv == 1 and n_lanc >= 1

    assert c.post(url, headers=h, content=corpo.format("B", "1000000000.01")).status_code == 400
    assert (len(_conta(inv, uid)), len(_conta(LANC, uid))) == (n_inv, n_lanc)


def test_pagar_conta_mede_o_valor_arredondado_a_centavos_que_sera_gravado():
    import db.bills as B
    from test_bill_amount_pending import _monta_conta_variavel

    uid = usuario_pagante()
    conta = _monta_conta_variavel(uid)
    with pytest.raises(ValueError, match="VALOR_INVALIDO"):  # arredonda para 1e9 + 0,01
        B.mark_bill_paid(uid, int(conta["id"]), 1000000000.006, metodo="carteira")
    pago = B.mark_bill_paid(uid, int(conta["id"]), 1000000000.004, metodo="carteira")  # arredonda para 1e9
    assert pago is not None and float(pago["paid_amount"]) == 1e9


def _conta_antiga_2bi(uid):
    from test_bill_amount_pending import _monta_conta_variavel
    conta = _monta_conta_variavel(uid)
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("update bill_instances set amount=2000000000 where id=%s", (conta["id"],))
        conn.commit()
    return int(conta["id"])


def test_conta_antiga_acima_do_teto_paga_pelo_valor_guardado_e_outro_valor_acima_segue_recusado():
    import db.bills as B

    uid = usuario_pagante()
    bid = _conta_antiga_2bi(uid)
    for ruim in (3e9, float("nan"), float("inf")):  # outro valor acima do teto; não finito mesmo "igual"
        with pytest.raises(ValueError, match="VALOR_INVALIDO"):
            B.mark_bill_paid(uid, bid, ruim, metodo="carteira")
    assert B.get_bill(uid, bid)["status"] == "pending"

    pago = B.mark_bill_paid(uid, bid, 2e9, metodo="carteira")  # o que o painel manda: a estimativa guardada
    assert pago is not None and float(pago["paid_amount"]) == 2e9


def test_painel_paga_conta_antiga_acima_do_teto_pelo_valor_guardado_200_e_outro_valor_400():
    uid = usuario_pagante()
    c, h = _cliente(uid)
    bid = _conta_antiga_2bi(uid)
    url = f"/recurring-bills/{uid}/{bid}/pay"

    assert c.post(url, headers=h, content='{"amount":3000000000,"metodo":"dinheiro"}').status_code == 400
    r = c.post(url, headers=h, content='{"amount":2000000000,"metodo":"dinheiro"}')
    assert r.status_code == 200, r.text
    assert r.json()["bill"]["status"] == "paid"
