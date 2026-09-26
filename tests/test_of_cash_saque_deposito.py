"""Q41 grupo 1: saque do banco move a Carteira; depósito pergunta; o par fica
fora de gasto/receita e o consolidado não muda. Sinais falsos seguem gasto."""
from decimal import Decimal

import db
from conftest import usuario_pagante
from db.open_finance_cash import answer_link
from tests._of_cash_helpers import (  # noqa: F401  (fixture)
    caixa, carteira, conecta, dia, launches_visiveis, links, q, sync, tx,
)


def _consolidado(uid):
    return db.get_consolidated_balance(uid)["consolidated"]


def test_saque_credita_a_carteira_e_o_consolidado_nao_muda(caixa):
    uid = usuario_pagante()
    c = conecta(uid, f"item-{uid}")
    sync(c, uid, [], saldo="1000")
    antes = _consolidado(uid)

    sync(c, uid, [tx("t1", -200, dia(10))], saldo="800")

    assert carteira(uid) == Decimal("200")
    assert _consolidado(uid) == antes == Decimal("1000")
    assert launches_visiveis(uid) == [], "o par saque/carteira entrou em gasto ou receita"
    (link,) = links(uid)
    assert (link["status"], link["kind"], link["origem"]) == ("ativo", "saque", "auto")


def test_deposito_pergunta_e_so_debita_com_a_resposta(caixa):
    uid = usuario_pagante()
    c = conecta(uid, f"item-{uid}")
    sync(c, uid, [tx("d1", 300, dia(10), op="DEPOSITO", desc="Transfers"),
                  tx("d2", 50, dia(11), op="DEPOSITO", desc="Transfers")])

    d1, d2 = links(uid)
    assert (d1["status"], d2["status"]) == ("perguntar_fraco", "perguntar_fraco")
    assert carteira(uid) == 0, "depósito debitou a carteira sem perguntar"
    assert launches_visiveis(uid) == [], "a entrada no banco contou como receita enquanto pergunta"

    assert answer_link(uid, d1["id"], "cash")["changed"]
    assert answer_link(uid, d2["id"], "not_cash")["changed"]
    assert answer_link(uid, d1["id"], "cash")["changed"] is False, "botão tocado 2x debitou 2x"
    assert carteira(uid) == Decimal("-300")
    assert [r["status"] for r in links(uid)] == ["ativo", "nao_dinheiro"]
    sync(c, uid, [tx("d1", 300, dia(10), op="DEPOSITO", desc="Transfers"),
                  tx("d2", 50, dia(11), op="DEPOSITO", desc="Transfers")])
    assert [Decimal(str(r["valor"])) for r in launches_visiveis(uid)] == [Decimal("50")], (
        "o depósito que NÃO era dinheiro tem de voltar a contar como receita")


def test_sinais_falsos_seguem_gasto(caixa):
    """Positivo: lotérica (CARTAO), tarifa de saque e empréstimo não mexem na Carteira."""
    uid = usuario_pagante()
    c = conecta(uid, f"item-{uid}")
    sync(c, uid, [
        tx("l1", -80, dia(10), op="CARTAO", desc="LOTERICA SAQUE FACIL", category="Shopping"),
        tx("f1", -6.5, dia(10), op="TARIFA_SERVICOS_AVULSOS", desc="Tarifa saque", category="Fees"),
        tx("e1", 900, dia(11), op="OPERACAO_CREDITO", desc="Deposito emprestimo", category="Loans"),
    ])
    assert links(uid) == []
    assert carteira(uid) == 0
    assert len(launches_visiveis(uid)) == 3


def test_pix_saque_so_pergunta(caixa):
    uid = usuario_pagante()
    c = conecta(uid, f"item-{uid}")
    sync(c, uid, [tx("p1", -100, dia(10), op="PIX", desc="Pix Saque Loja X", category="Transfer - PIX")])
    (link,) = links(uid)
    assert (link["kind"], link["status"]) == ("fraco", "perguntar_fraco")
    assert carteira(uid) == 0
    answer_link(uid, link["id"], "cash")
    assert carteira(uid) == Decimal("100")


def test_anterior_ao_corte_e_historico(caixa):
    """Saque de antes da 1ª conexão (ou da ativação) nunca mexe na Carteira."""
    uid = usuario_pagante()
    c = conecta(uid, f"item-{uid}")
    sync(c, uid, [tx("h1", -200, dia(20, mes=1)), tx("h2", -40, dia(5, mes=12).replace(year=2025))])
    assert [r["status"] for r in links(uid)] == ["historico", "historico"]
    assert carteira(uid) == 0


def test_switch_desligado_nao_muda_nada(monkeypatch):
    monkeypatch.delenv("OF_CASH_ENABLED", raising=False)
    uid = usuario_pagante()
    c = conecta(uid, f"item-{uid}")
    sync(c, uid, [tx("t1", -200, dia(10))])
    assert carteira(uid) == 0
    assert links(uid) == []
    assert q("select count(*) as n from launches where user_id=%s", (uid,), True)[0]["n"] == 1


def test_editar_categoria_do_par_nao_vira_receita(caixa):
    uid = usuario_pagante()
    sync(conecta(uid, f"item-{uid}"), uid, [tx("t1", -200, dia(10))])
    lid = links(uid)[0]["launch_id"]
    outro, _, _ = db.add_launch_and_update_balance(uid, "receita", 10, "pai", None,
                                                    categoria="transferencia_interna",
                                                    is_internal_movement=True)

    assert db.update_launch_fields(uid, lid, categoria="outros")
    assert db.update_launch_fields(uid, outro, categoria="outros")

    interno = {r["id"]: r["i"] for r in q(
        "select id, is_internal_movement as i from launches where id = any(%s)", ([lid, outro],), True)}
    assert interno[lid] is True, "recategorizar o saque o transformou em receita"
    assert interno[outro] is False, "positivo: lançamento comum segue a categoria"
