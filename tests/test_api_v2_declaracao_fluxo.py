"""PR-A pela ROTA REAL (monólito, sessão, CSRF, Postgres): a despesa/receita declarada "paga
pelo banco" na v2 (`origem="banco"`, `pode`, `fundido`, motivo), no Resumo do mês, na edição e no
apagar, e a escolha manual entre candidatos pelo modal (`/open-finance/{uid}/movements`).

Controles negativos (rodados): sem o ramo `declarada` do `PODE_SQL` ficam vermelhos a lista (sem e com
par), editar e apagar; sem o `when l.declarada then 'banco'`, a lista; sem `l.fundido or l.casada`,
`..._fundido_...`; sem o motivo novo, `..._banco_a_conferir`.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard
from conftest import usuario_pagante
from tests._patrimonio_helpers import q
from tests.test_api_v2_lancamentos import ok as lista
from tests.test_api_v2_lancamentos_escrita import codigo, post
from tests.test_api_v2_resumo_mes import libera  # noqa: F401 (fixture)
from tests.test_api_v2_resumo_mes import ok as resumo
from tests.test_bank_movements import _bank
from tests.test_declaracao_fluxo_reconcilia import decl, declara, n_linhas, sincroniza, tx
from utils_date import today_tz

PODE = ["categoria", "descricao", "apagar"]


def linha(uid, lid):
    return {i["id"]: i for i in lista(uid)["itens"]}[f"l{lid}"]


def test_lista_sem_par_e_banco_a_conferir(libera):
    uid = usuario_pagante()
    _bank(uid)
    lid = declara(uid)
    libera(uid)
    i = linha(uid, lid)
    assert i["origem"] == "banco" and i["pode"] == PODE and not i["fundido"]
    assert "conciliacao_pendente" in i["motivos"]
    assert Decimal(str(i["valor"])) == 50


def test_lista_com_par_fundido_e_instituicao_sem_motivo(libera):
    uid = usuario_pagante()
    cid, _ = _bank(uid)
    lid = declara(uid)
    sincroniza(cid, uid, tx())
    libera(uid)
    i = linha(uid, lid)
    assert i["origem"] == "banco" and i["pode"] == PODE and i["fundido"] is True
    assert i["instituicao"] == "Nubank" and "conciliacao_pendente" not in i["motivos"]
    assert n_linhas(uid, 50) == 1  # uma linha só na lista também
    assert len([x for x in lista(uid)["itens"] if Decimal(str(x["valor"])) == 50]) == 1


def test_resumo_do_mes_conta_uma_vez_nas_duas_fases(libera):
    uid = usuario_pagante()
    cid, _ = _bank(uid)
    declara(uid)
    libera(uid)
    assert Decimal(str(resumo(uid)["saiu"])) == 50
    sincroniza(cid, uid, tx())
    assert Decimal(str(resumo(uid)["saiu"])) == 50


def test_editar_valor_ou_data_e_409_e_texto_edita(libera):
    uid = usuario_pagante()
    _bank(uid)
    lid = declara(uid)
    libera(uid)
    for corpo in ({"valor": "60"}, {"data": (today_tz() - timedelta(days=1)).isoformat()}):
        assert codigo(post(uid, "editar", {"id": f"l{lid}", **corpo})) == (409, "nao_editavel")
    assert q("select valor from launches where id=%s", (lid,))["valor"] == 50
    r = post(uid, "editar", {"id": f"l{lid}", "categoria": "lazer", "descricao": "cinema"})
    assert r.status_code == 200, r.text
    assert q("select categoria, alvo from launches where id=%s", (lid,))["alvo"] == "cinema"


def test_apagar_pela_v2(libera):
    uid = usuario_pagante()
    cid, _ = _bank(uid)
    lid = declara(uid)
    sincroniza(cid, uid, tx())
    libera(uid)
    r = post(uid, "apagar", {"id": f"l{lid}"})
    assert r.status_code == 200, r.text
    assert decl(lid) is None and n_linhas(uid, 50) == 1  # a transação do banco volta como sombra
    assert Decimal(str(resumo(uid)["saiu"])) == 50
    assert db.get_balance(uid) == 0


def _modal(uid):
    c = TestClient(dashboard.app)
    c.cookies.set(dashboard.AUTH_COOKIE_NAME, dashboard._make_jwt(uid, "decl@test.com"))
    c.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, dashboard.make_dashboard_token(uid, hours=1))
    c.cookies.set(dashboard.CSRF_COOKIE_NAME, "decl-test")
    return c, {dashboard.CSRF_HEADER_NAME: "decl-test"}


def test_modal_oferece_e_confirma_a_escolha_manual_e_a_sombra_some(libera):
    uid = usuario_pagante()
    cid, _ = _bank(uid)
    lid = declara(uid)
    sincroniza(cid, uid, tx(-52.30, "a"), tx(-48, "b"))  # nenhum no automático (0,05), os dois no manual (5,00)
    assert decl(lid)["matched_transaction_id"] is None
    c, h = _modal(uid)
    [mov] = c.get(f"/open-finance/{uid}/movements").json()["movements"]
    assert sorted(x["amount"] for x in mov["candidates"]) == [-52.3, -48.0]
    escolhida = next(x["id"] for x in mov["candidates"] if x["amount"] == -52.3)
    r = c.post(f"/open-finance/{uid}/movements/confirm", json={"launch_id": lid, "transaction_id": escolhida}, headers=h)
    assert r.status_code == 200, r.text
    d = decl(lid)
    assert d["matched_transaction_id"] == escolhida and d["confirmation_method"] == "manual"
    sombras = q("select valor from launches where user_id=%s and source='open_finance' order by valor",
                (uid,), fetch=True)
    assert [Decimal(s["valor"]) for s in sombras] == [48]  # só a sombra da outra; a escolhida sumiu


def test_lista_e_resumo_mostram_o_valor_do_extrato_depois_de_casar(libera):
    """Opção B pela rota real: o lançamento casado mostra o valor do banco (50,04), uma linha, e o
    Resumo do mês soma o mesmo valor."""
    uid = usuario_pagante()
    cid, _ = _bank(uid)
    lid = declara(uid)
    sincroniza(cid, uid, tx(-50.04))
    libera(uid)
    i = linha(uid, lid)
    assert Decimal(str(i["valor"])) == Decimal("50.04") and i["fundido"] is True
    assert Decimal(str(resumo(uid)["saiu"])) == Decimal("50.04")
