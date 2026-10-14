"""Codex P2 (#969): o caráter interno é derivado do NOME da categoria em vários lugares; a despesa/receita
declarada "paga pelo banco" não pode virar interna por NENHUM deles (sairia dos totais enquanto a
conciliação a trata como gasto do banco).

Irmãos varridos: edição de categoria (`update_launch_fields`, lote: teste próprio em
`test_declaracao_fluxo_categoria_interna.py`), RENOMEAR categoria (`cascata_nome_categoria`, que o
`PATCH /categories` e o `scripts/fundir_categorias.py` usam), a migração do boot de `db/schema.py` e a
criação (`DECLARACAO_INTERNA` no escritor). O sync do OF só mexe em launches `open_finance` e fundidos
(status auto_merged/confirmed): a declarada (`bank_movement_confirmed`) fica de fora; as regras de categoria
(`user_category_rules`) só valem na criação. Renomear categoria SEM declaração, ou para nome comum, não muda.
Controles negativos: sem o guard da cascata o rename passa (200) e o nome fica interno; sem o `not (...)`
da migração a declarada vira interna e some do Resumo.
"""
from __future__ import annotations

import db
from conftest import usuario_pagante
from db.categories import create_user_category
from db.resumo_mes import totais_do_mes
from tests._patrimonio_helpers import q
from tests.test_bank_movements import _bank
from tests.test_category_launches_query import _cliente_logado
from tests.test_declaracao_fluxo_reconcilia import declara, saiu
from utils_date import today_tz


def _com_categoria_custom(com_declaracao=True):
    uid = usuario_pagante()
    _bank(uid)
    cat = create_user_category(uid, "viagens")
    lid = declara(uid, categoria="viagens") if com_declaracao else db.add_launch_and_update_balance(
        uid, "despesa", 50, "x", "y", "viagens")[0]
    return uid, cat, lid


def _patch(uid, cat_id, nome):
    client, headers = _cliente_logado(uid)
    return client.patch(f"/categories/{uid}/{cat_id}", json={"name": nome}, headers=headers)


def test_rename_para_nome_interno_e_recusado_com_409_e_nada_muda():
    uid, cat, lid = _com_categoria_custom()
    for nome in ("cripto", "tesouro", "investimentos", "transferencia_interna"):
        r = _patch(uid, cat["id"], nome)
        assert r.status_code == 409 and "declarados como pagos pelo banco" in r.json()["detail"], (nome, r.text)
    l = q("select categoria, is_internal_movement i from launches where id=%s", (lid,))
    assert (l["categoria"], l["i"]) == ("viagens", False)
    assert q("select name from user_categories where id=%s", (cat["id"],))["name"] == "viagens"
    assert saiu(uid) == 50


def test_rename_para_nome_comum_continua_e_cascateia():
    uid, cat, lid = _com_categoria_custom()
    r = _patch(uid, cat["id"], "passeios")
    assert r.status_code == 200, r.text
    assert q("select categoria from launches where id=%s", (lid,))["categoria"] == "passeios"


def test_rename_de_categoria_sem_declaracao_para_nome_interno_nao_muda():
    uid, cat, lid = _com_categoria_custom(com_declaracao=False)
    r = _patch(uid, cat["id"], "cripto")
    assert r.status_code == 200, r.text  # lançamento comum: comportamento de antes
    assert q("select categoria from launches where id=%s", (lid,))["categoria"] == "cripto"


def test_migracao_do_boot_nao_marca_interna_a_declarada_mas_marca_a_comum():
    uid, _, lid = _com_categoria_custom()
    q("update launches set categoria='cripto' where id=%s returning id", (lid,))  # forjado: a recusa é das portas
    comum = db.add_launch_and_update_balance(uid, "despesa", 20, "x", "y", "cripto")[0]
    assert q("select is_internal_movement i from launches where id=%s", (comum,))["i"] is False
    from db.schema import init_db
    init_db()  # as migrações rodam a cada boot
    assert q("select is_internal_movement i from launches where id=%s", (lid,))["i"] is False
    assert q("select is_internal_movement i from launches where id=%s", (comum,))["i"] is True  # positivo
    assert totais_do_mes(uid, today_tz())["saiu"] == 50  # só a declarada: a comum virou interna
