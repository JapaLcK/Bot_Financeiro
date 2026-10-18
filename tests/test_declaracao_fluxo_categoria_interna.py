"""Despesa/receita declarada "paga pelo banco" não vira movimentação interna por edição de categoria (P2 do Codex).

`update_launch_fields` marca `is_internal_movement` pela categoria; `resumo_mes`/totais excluem o interno, mas a
conciliação continua tratando a linha como gasto do banco: a despesa SUMIA dos totais. Agora a edição que a
tornaria interna é recusada (`LaunchCategoryLockedError`, subclasse de `LaunchDateLockedError`), em todas as
portas: v2, PATCH /launches do /app, tool da IA, WhatsApp e o lote. Categoria comum continua editando; aporte,
caixinha e dinheiro puro não mudam.
Controle negativo: sem `_recusa_interna_declarada` em `update_launch_fields` o Resumo perde a despesa
(`saiu` 0) e as portas de edição ficam vermelhas; sem ela no lote, vermelho `test_lote`.
"""
from __future__ import annotations

import pytest

import db
from adapters.whatsapp import wa_runtime
from conftest import usuario_pagante
from core.services.ai_chat.tools.extrato_ia import ao_confirmar, recategorizar
from db.accounts import LaunchCategoryLockedError, LaunchDateLockedError
from db.resumo_mes import totais_do_mes
from tests._patrimonio_helpers import q
from tests.test_api_v2_lancamentos_escrita import codigo, post
from tests.test_api_v2_resumo_mes import libera  # noqa: F401 (fixture)
from tests.test_bank_movements import _bank, _deposit
from tests.test_category_launches_query import _cliente_logado
from tests.test_declaracao_fluxo_reconcilia import decl, declara, sincroniza, tx
from utils_date import today_tz

INTERNAS = ["investimento_aporte", "investimento_resgate", "transferencia_interna", "pagamento_fatura",
            "ajuste_saldo", "investimentos", "cripto", "tesouro"]


def _declarada(com_par=True):
    uid = usuario_pagante()  # id pequeno: o WhatsApp troca id grande por hash
    cid, _ = _bank(uid)
    lid = declara(uid)
    if com_par:
        sincroniza(cid, uid, tx())
        assert decl(lid)["matched_transaction_id"]
    return uid, lid


def _intacta(uid, lid, categoria_antes="mercado"):
    l = q("select categoria, is_internal_movement i from launches where id=%s", (lid,))
    assert (l["categoria"], l["i"]) == (categoria_antes, False)
    assert totais_do_mes(uid, today_tz())["saiu"] == 50  # a despesa segue no Resumo


@pytest.mark.parametrize("com_par", [True, False])
@pytest.mark.parametrize("categoria", INTERNAS)
def test_v2_recusa_categoria_interna(libera, categoria, com_par):
    uid, lid = _declarada(com_par)
    libera(uid)
    assert codigo(post(uid, "editar", {"id": f"l{lid}", "categoria": categoria})) == (409, "nao_editavel")
    _intacta(uid, lid)


def test_v2_categoria_comum_continua_editando(libera):
    uid, lid = _declarada()
    libera(uid)
    r = post(uid, "editar", {"id": f"l{lid}", "categoria": "lazer"})
    assert r.status_code == 200, r.text
    assert q("select categoria from launches where id=%s", (lid,))["categoria"] == "lazer"
    assert totais_do_mes(uid, today_tz())["saiu"] == 50


def test_app_patch_launches_recusa_com_frase_e_aceita_comum():
    uid, lid = _declarada()
    client, headers = _cliente_logado(uid)
    r = client.patch(f"/launches/{uid}/{lid}", json={"categoria": "investimento_aporte"}, headers=headers)
    assert r.status_code == 409 and "declarado como pago pelo banco" in r.json()["detail"], r.text
    _intacta(uid, lid)
    assert client.patch(f"/launches/{uid}/{lid}", json={"categoria": "lazer"}, headers=headers).status_code == 200


def test_ia_recusa_com_frase_e_aceita_comum():
    uid, lid = _declarada()
    msg = recategorizar(uid, ao_confirmar(uid, {"launch_id": f"l{lid}", "new_category": "investimento_aporte"}))
    assert "declarado como pago pelo banco" in msg and "agora é" not in msg
    _intacta(uid, lid)
    assert "agora é" in recategorizar(uid, ao_confirmar(uid, {"launch_id": f"l{lid}", "new_category": "lazer"}))


def test_whatsapp_recusa_com_frase_e_aceita_comum():
    uid, lid = _declarada()
    assert "declarado como pago pelo banco" in wa_runtime._apply_recategorize(uid, lid, "investimento_aporte")
    _intacta(uid, lid)
    assert "atualizada" in wa_runtime._apply_recategorize(uid, lid, "lazer")


def test_lote_recusa_e_aceita_comum():
    uid, lid = _declarada()
    with pytest.raises(LaunchCategoryLockedError):
        db.update_launch_categories_bulk(uid, [(lid, "investimento_aporte")])
    _intacta(uid, lid)
    assert db.update_launch_categories_bulk(uid, [(lid, "lazer")]) == 1


def test_a_recusa_e_um_date_locked_error_para_as_portas_que_ja_o_tratam():
    assert issubclass(LaunchCategoryLockedError, LaunchDateLockedError)


def test_aporte_caixinha_e_dinheiro_puro_seguem_como_antes():
    uid = usuario_pagante()
    _, source = _bank(uid)
    aporte = _deposit(uid, source)  # declaração de aporte: não é fluxo
    assert db.update_launch_fields(uid, aporte, categoria="investimento_aporte")
    puro = db.add_launch_and_update_balance(uid, "despesa", 20, "x", None, "mercado")[0]
    assert db.update_launch_fields(uid, puro, categoria="investimento_aporte")
    assert q("select is_internal_movement i from launches where id=%s", (puro,))["i"] is True
