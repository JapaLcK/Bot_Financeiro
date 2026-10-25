"""Compra parcelada do Open Finance: UM group_id por compra, e o nome é o da loja.

Produção media 15 parcelas de 2 compras em 15 grupos: a chave levava a descrição com o "n/N", e o
mês de origem falhava quando várias parcelas chegam no mesmo dia. Postgres real, import de produção.

Controles negativos (CLAUDE.md §3), cada um vermelho quando o conserto sai:
- a chave antiga (descrição inteira + mês de origem) em `chave_grupo_of` → test_doze_parcelas_..., test_chave_*,
  test_sem_parcela_* (o "n/N" cortado);
- o filtro antigo de `uma_por_compra` (sem `origem = 'cartao'`) → test_recentes_parcelas_do_of_*;
- o nome antigo (`nota or "Parcelamento"`) nos dois leitores → test_nome_*;
- o lateral sem `xc.user_id = %s` em `list_installment_groups` → test_nome_nao_empresta_*.
Positivos: compras iguais em dias/meses distintos seguem separadas, o parcelamento manual segue em uma
linha, a nota do usuário vence o nome do banco.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

import db
from conftest import usuario_pagante
from db import lancamentos_leitura
from core.handlers import credit
from db.parcelas_of import chave_grupo_of, sem_parcela
from tests._of_cash_helpers import conecta, dia, q
from tests.test_api_app_sessao import completo
from tests.test_of_categoria_pt import _cartao
from utils_date import now_tz, today_tz


def _p(ident, n, total, data, desc="Loja", compra="2026-03-10T04:59:50.001Z", valor=50):
    raw = {"totalInstallments": total, "installmentNumber": n}
    if compra:
        raw["purchaseDate"] = compra
    return {"provider_transaction_id": ident, "description": f"{desc} {n}/{total}", "amount": Decimal(valor),
            "transaction_date": data, "transacted_at": None, "category": "Shopping",
            "raw": {"creditCardMetadata": raw}}


def _grupos(uid):
    return q("select group_id::text g, count(*) n from credit_transactions where user_id=%s "
             "group by group_id order by min(id)", (uid,), True)


@pytest.mark.parametrize("desc,n,total,esperado", [
    ("Loja 2/12", 2, 12, "Loja"), ("LOJA 02/12", 2, 12, "LOJA"), ("LOJA PARC 02/12", 2, 12, "LOJA"),
    ("LOJA - 2/12", 2, 12, "LOJA"), ("LOJA Parcela 2 de 12", 2, 12, "LOJA"), ("LOJA2/12", 2, 12, "LOJA"),
    ("a 9/9", 9, 9, "a"), ("a 99/99", 99, 99, "a"), ("a *a&a 9/9", 9, 9, "a *a&a"), ("LOJA 10/12", 10, 12, "LOJA"),
    # não corta: outra parcela, número maior, nada sobra, não é o fim, sem n/total
    ("PAG 10/12", 3, 12, "PAG 10/12"), ("PEDIDO 1010/12", 10, 12, "PEDIDO 1010/12"),
    ("2/12", 2, 12, "2/12"), ("Loja 2/12 extra", 2, 12, "Loja 2/12 extra"),
    ("PAG 10/12", None, None, "PAG 10/12"), ("Loja 3/12", 2, 12, "Loja 3/12"), (None, 2, 12, ""),
    # Tester: travessão longo, parênteses e ponto final; e a cauda não corta o meio de um número
    ("Loja — 2/12", 2, 12, "Loja"), ("Loja (2/12)", 2, 12, "Loja"), ("Loja (Parcela 2 de 12)", 2, 12, "Loja"),
    ("Loja 2/12.", 2, 12, "Loja"), ("X" * 30 + " PEDIDO 1010/12", 10, 12, "X" * 30 + " PEDIDO 1010/12"),
    ("Y" * 200 + " Loja 2/12", 2, 12, "Y" * 200 + " Loja"),
])
def test_sem_parcela_so_corta_o_n_de_N_do_fim(desc, n, total, esperado):
    assert sem_parcela(desc, n, total) == esperado


def test_chave_nao_depende_da_parcela_nem_do_mes_da_parcela():
    k = lambda desc, n, data, compra="2026-04-10T04:59:50.001Z": chave_grupo_of(  # noqa: E731
        7, desc, {"creditCardMetadata": {"purchaseDate": compra, "totalAmount": 600}}, data, n, 12)
    assert k("Loja 1/12", 1, dia(10, 4)) == k("Loja 2/12", 2, dia(20, 4)) == k("LOJA 12/12", 12, dia(5, 3))
    assert k("Loja 2/12", 2, dia(20, 4)) != k("Loja 2/12", 2, dia(20, 4), "2026-05-10T04:59:50.001Z")  # positivo
    assert k("Loja 2/12", 2, dia(20, 4)) != chave_grupo_of(8, "Loja 2/12", {"creditCardMetadata": {
        "purchaseDate": "2026-04-10", "totalAmount": 600}}, dia(20, 4), 2, 12)  # outro cartão
    # purchaseDate inválido cai no mês de origem, como era
    m = lambda n, data: chave_grupo_of(7, f"Loja {n}/12", {"creditCardMetadata": {"purchaseDate": "lixo"}}, data, n, 12)  # noqa: E731
    assert m(1, dia(10, 4)) == m(3, dia(10, 6)) != m(3, dia(10, 5))


def test_doze_parcelas_com_a_mesma_data_de_compra_sao_um_grupo(user_id):
    txs = [_p("p1", 1, 12, dia(10, 3))] + [_p(f"p{n}", n, 12, dia(20, 4)) for n in range(2, 13)]  # 2..12 no mesmo dia
    _cartao(user_id, txs)
    g = _grupos(user_id)
    assert len(g) == 1 and g[0]["n"] == 12


def test_compras_iguais_em_dias_de_compra_diferentes_seguem_separadas(user_id):
    a, b = "2026-03-10T10:00:00.000Z", "2026-05-02T10:00:00.000Z"
    _cartao(user_id, [_p("a1", 1, 3, dia(10, 3), compra=a), _p("a2", 2, 3, dia(10, 4), compra=a),
                      _p("b1", 1, 3, dia(2, 5), compra=b), _p("b2", 2, 3, dia(2, 6), compra=b)])
    assert sorted(x["n"] for x in _grupos(user_id)) == [2, 2]


def test_sem_purchasedate_segue_o_mes_de_origem(user_id):
    _cartao(user_id, [_p("a1", 1, 3, dia(10, 3), compra=None), _p("a2", 2, 3, dia(10, 4), compra=None),
                      _p("b1", 1, 3, dia(10, 5), compra=None), _p("b2", 2, 3, dia(10, 6), compra=None)])
    assert sorted(x["n"] for x in _grupos(user_id)) == [2, 2]


def _of(uid, n=3):
    """Compra do OF de `n` parcelas, todas com a data de ontem; devolve (cartão, [ids das parcelas])."""
    ontem = today_tz() - timedelta(days=1)
    _cartao(uid, [_p(f"r{i}", i, n, ontem, compra=ontem.isoformat()) for i in range(1, n + 1)])
    return [r["id"] for r in q("select id from credit_transactions where user_id=%s order by installment_no", (uid,), True)]


def test_recentes_mostra_cada_parcela_do_of_do_mesmo_dia_e_o_manual_continua_em_uma_linha():
    uid = completo(usuario_pagante())
    ids = _of(uid)
    card = db.create_card(uid, "Manual", closing_day=28, due_day=10)
    db.add_credit_purchase_installments(uid, card, 300, "compras", "Sofa", today_tz() - timedelta(days=3), 3)
    itens = lancamentos_leitura.recentes(uid, now_tz(), 50)["itens"]
    assert {f"c{i}" for i in ids} <= {i["id"] for i in itens}  # as 3 do OF
    manual = [i for i in itens if i["id"].startswith("c") and int(i["id"][1:]) not in ids]
    assert len(manual) == 1  # positivo: o manual colapsa


def test_nome_da_compra_do_of_e_a_loja_nos_dois_leitores_e_a_nota_vence():
    uid = completo(usuario_pagante())
    _of(uid)
    (grupo,) = db.list_installment_groups(uid)
    assert grupo["nota"] == "Loja"  # não "Parcelamento", não "Loja 1/3"
    (det,) = db.list_installment_groups_detailed(uid)
    assert det["name"] == "Loja"
    assert db.update_installment_group_meta(uid, grupo["group_id"], nome="Meu sofá")
    assert db.list_installment_groups(uid)[0]["nota"] == "Meu sofá"
    assert db.list_installment_groups_detailed(uid)[0]["name"] == "Meu sofá"


def test_parcelamento_manual_sem_nota_segue_sem_nome():
    uid = completo(usuario_pagante())
    card = db.create_card(uid, "Manual", closing_day=28, due_day=10)
    db.add_credit_purchase_installments(uid, card, 300, "compras", None, today_tz(), 3)
    assert not db.list_installment_groups(uid)[0]["nota"]
    assert db.list_installment_groups_detailed(uid)[0]["name"] == "Parcelamento"


def test_nome_nao_empresta_a_descricao_de_outro_usuario():
    uid, outro = completo(usuario_pagante()), completo(usuario_pagante())
    ids = _of(uid)
    cid = conecta(outro, f"item-x-{outro}")
    db.save_open_finance_sync(cid, [{
        "provider_account_id": f"cred-x-{cid}", "name": "Cartão", "type": "CREDIT", "currency": "BRL",
        "balance": Decimal("-1"), "raw": {}, "transactions": [_p("x1", 1, 3, dia(10), desc="SEGREDO")]}])
    q("update open_finance_transactions set imported_credit_tx_id=%s where provider_transaction_id='x1'", (ids[0],))
    (grupo,) = db.list_installment_groups(uid)
    (det,) = db.list_installment_groups_detailed(uid)
    assert grupo["nota"] == det["name"] == "Loja"


def test_sem_parcela_em_texto_longo_e_rapido():
    import time
    t = time.monotonic()
    sem_parcela("A" + " " * 20000 + "x", 2, 12)  # o search na string inteira levava ~9 s
    assert time.monotonic() - t < 0.5


def test_fatura_do_whatsapp_mostra_cada_parcela_da_compra_na_mesma_fatura(user_id):
    hoje = today_tz()
    _cartao(user_id, [_p(f"w{n}", n, 4, hoje) for n in (2, 3, 4)])  # antecipação: 3 parcelas na mesma fatura
    assert len(_grupos(user_id)) == 1
    texto = credit.handle(user_id, "fatura")
    assert all(f"{n}/4" in texto for n in (2, 3, 4)), texto
    # o "faltam" de cada bloco conta só as parcelas DEPOIS dele (o resumo é do grupo inteiro)
    assert "faltam **2** parcela(s) = R$ 100,00" in texto and "faltam **1** parcela(s) = R$ 50,00" in texto, texto
    assert "R$ 150,00" not in texto.split("Parcelamentos nesta fatura")[1].split("Outras compras")[0], texto
