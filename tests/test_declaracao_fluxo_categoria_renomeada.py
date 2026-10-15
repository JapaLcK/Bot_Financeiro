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


def _declaradas_internas(uid):
    from utils_text import is_internal_category
    rows = q("""select l.id, l.categoria from launches l join bank_movement_declarations d
                  on d.launch_id = l.id and d.user_id = l.user_id where l.user_id = %s""", (uid,), fetch=True)
    return [r for r in rows if is_internal_category(r["categoria"])]


def test_rename_interno_x_declarar_nao_burla_a_invariante(monkeypatch):
    """Corrida: o rename checa "sem declaração", a declaração commita na categoria antiga e só então a cascata
    troca o nome para o interno. O rename interno toma o mutex do usuário antes de checar (o do escritor), então
    a declaração espera e nasce DEPOIS do rename, com o nome velho (inofensivo). Sem o lock: declarada com `cripto`."""
    import threading
    import psycopg
    from concurrent.futures import ThreadPoolExecutor
    from db.categories import update_user_category
    from tests.test_declaracao_fluxo_reconcilia import declara
    uid = usuario_pagante()
    _bank(uid)
    cat = create_user_category(uid, "viagens")
    checado, declarado, rename_thread = threading.Event(), threading.Event(), []
    original = psycopg.Cursor.execute

    def execute(self, query, *a, **k):
        r = original(self, query, *a, **k)
        if rename_thread and threading.get_ident() == rename_thread[0] and "lower(categoria)=lower(%s)" in str(query) \
                and "limit 1" in str(query):
            checado.set()
            declarado.wait(1.5)  # com o lock a declaração não consegue commitar aqui: a espera vence
        return r

    monkeypatch.setattr(psycopg.Cursor, "execute", execute)

    def renomear():
        rename_thread.append(threading.get_ident())
        try:
            return update_user_category(uid, cat["id"], new_name="cripto")
        except ValueError as exc:
            return exc

    def declarar():
        assert checado.wait(5)
        lid = declara(uid, categoria="viagens")
        declarado.set()
        return lid

    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(renomear), pool.submit(declarar)
        a.result(timeout=20), b.result(timeout=20)
    assert _declaradas_internas(uid) == []


def test_rename_comum_nao_toma_o_mutex(monkeypatch):
    """O lock é só do nome interno: renomear para um nome comum não serializa com ninguém."""
    import db.bank_movements as bm
    uid, cat, _ = _com_categoria_custom()
    chamadas = []
    original = bm._lock_user
    monkeypatch.setattr(bm, "_lock_user", lambda cur, u: chamadas.append(u) or original(cur, u))
    assert _patch(uid, cat["id"], "passeios").status_code == 200
    assert chamadas == []
    cat2 = create_user_category(uid, "outra")
    assert _patch(uid, cat2["id"], "cripto").status_code == 200 and chamadas == [uid]  # interno, sem declaração


def test_declarar_primeiro_x_rename_interno_o_rename_espera_e_e_recusado(monkeypatch):
    from db.categories import update_user_category
    from tests.test_bank_movements_concurrency import _ordered_pair
    from tests.test_declaracao_fluxo_reconcilia import declara
    uid = usuario_pagante()
    _bank(uid)
    cat = create_user_category(uid, "viagens")

    def renomear():
        try:
            return update_user_category(uid, cat["id"], new_name="cripto")
        except ValueError as exc:
            return str(exc)

    _, resultado = _ordered_pair(monkeypatch, lambda: declara(uid, categoria="viagens"), renomear,
                                 esperar_travado=True)
    assert resultado == "CATEGORIA_DECLARADA_INTERNA" and _declaradas_internas(uid) == []
