"""O "n de N pagas" (`db.parcelas_of.progresso`) na IA, no WhatsApp e no card `parcelas` do chat.

Banco real. Open Finance traz só as parcelas a partir da que está na fatura (a 5/12, a 3/12 já paga...), então
contar as parcelas VISTAS dava "11/12 pagas" e sumia a compra cuja parcela vista estava numa fatura fechada.
"""
from datetime import date
from uuid import uuid4

import db
from _pendencia_credito_helpers import diga, novo_uid
from core.services.ai_chat import cards
from core.services.ai_chat.tools.cards import _list_installments
from tests._patrimonio_helpers import q


def _of(uid, card, loja, n, total, status="open"):
    """A parcela `n` de `total` do Open Finance; `status` é o da fatura onde ela caiu."""
    grupo = uuid4()
    tx, _ = db.add_imported_credit_purchase(uid, card, 100, "outros", date.today(), f"of-{grupo}-{n}",
                                            installment_no=n, installments_total=total, group_id=grupo)
    q("update credit_bills set status = %s where user_id = %s "
      "and id = (select bill_id from credit_transactions where id = %s) returning id", (status, uid, tx))
    q("update credit_transactions set nota = %s where user_id = %s and id = %s returning id", (loja, uid, tx))


def _tres_vezes_com_a_1a_fechada(uid, card):
    db.add_credit_purchase_installments(uid, card, 300, "outros", "geladeira", date.today(), installments=3)
    q("update credit_bills set status = 'closed' where user_id = %s and id = (select bill_id from credit_transactions "
      "where user_id = %s and installment_no = 1) returning id", (uid, uid))


def _leitura(uid):
    """(bloco, visão do modelo) do card `parcelas`, como o `montar` os tira (sem o portão de onboarding)."""
    c = cards.registro()["parcelas"]
    ((_, objs),) = c.ler(uid, {})
    return objs["parcelas"], c.visao({n: cards._py(o) for n, o in objs.items()})["installments"]


def _visao(uid):
    return _leitura(uid)[1]


def _grupos(out):
    return {g["nota"]: g for g in out["groups"]}


def test_a_5_de_12_na_fatura_aberta_conta_as_4_anteriores_nos_tres_canais():
    uid = novo_uid()
    _of(uid, db.create_card(uid, "Banco", 10, 17), "Sofa", 5, 12)
    g = _grupos(_list_installments(uid, {}))["Sofa"]
    assert (g["pagas"], g["n_parcelas"]) == (4, 12)
    (v,) = _visao(uid)
    assert (v["pagas"], v["n_parcelas"]) == (4, 12) and not {"n_paid", "n_pending", "installments_total"} & set(v)
    texto = diga(uid, "meus parcelamentos")
    assert "4/12 pagas" in texto and "Total:" not in texto  # vistas não cobrem as 12: sem dinheiro inventado


def test_so_a_3_de_12_na_fatura_fechada_aparece_com_o_progresso_e_sem_total_nem_restante():
    uid = novo_uid()
    _of(uid, db.create_card(uid, "Banco", 10, 17), "Notebook", 3, 12, status="closed")
    g = _grupos(_list_installments(uid, {}))["Notebook"]
    assert g["pagas"] == 3  # antes sumia: n_pending == 0
    assert g["total"] is None and g["total_pending"] is None  # não viu as 12: o modelo não recebe dinheiro inventado
    (v,) = _visao(uid)
    assert v["pagas"] == 3
    assert v["total"] is None and v["paid_amount"] is None and v["remaining_amount"] is None  # a visão do card também
    texto = diga(uid, "meus parcelamentos")
    assert "3/12 pagas" in texto and "Total:" not in texto and "Restante:" not in texto


def test_a_12_de_12_fechada_some_da_tool_do_whatsapp_e_da_visao_mas_fica_no_bloco():
    uid = novo_uid()
    _of(uid, db.create_card(uid, "Banco A", 10, 17), "Quitada", 12, 12, status="closed")  # um cartão por fatura
    _of(uid, db.create_card(uid, "Banco B", 10, 17), "Aberta", 2, 4)
    assert set(_grupos(_list_installments(uid, {}))) == {"Aberta"}
    assert set(_grupos(_list_installments(uid, {"only_pending": False}))) == {"Quitada", "Aberta"}  # positivo: pedir tudo traz
    assert [v["name"] for v in _visao(uid)] == ["Aberta"]
    assert {g["name"] for g in _leitura(uid)[0]["installments"]} == {"Quitada", "Aberta"}
    texto = diga(uid, "meus parcelamentos")
    assert "Aberta" in texto and "Quitada" not in texto


def test_positivo_manual_3x_com_a_1a_fechada_segue_1_de_3_com_total_e_restante():
    uid = novo_uid()
    _tres_vezes_com_a_1a_fechada(uid, db.create_card(uid, "Nubank", 10, 17))
    g = _list_installments(uid, {})["groups"][0]
    assert (g["pagas"], g["n_parcelas"], g["total"], g["total_pending"]) == (1, 3, 300.0, 200.0)
    (v,) = _visao(uid)
    assert (v["total"], v["remaining_amount"]) == (300.0, 200.0)  # positivo: com todas vistas, a visão tem o dinheiro
    texto = diga(uid, "meus parcelamentos")
    assert "Total: R$ 300,00 | Restante: R$ 200,00 (1/3 pagas)" in texto


def test_conversa_gastei_no_mercado_e_depois_meus_parcelamentos_mostra_4_de_12():
    uid = novo_uid()
    _of(uid, db.create_card(uid, "Banco", 10, 17), "Sofa", 5, 12)
    diga(uid, "gastei 50 no mercado")
    assert "4/12 pagas" in diga(uid, "meus parcelamentos")
