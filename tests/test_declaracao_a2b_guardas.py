"""PR-A2b-1: o que não pode mudar (caixinha e aporte, ADR 0003), a leitura do modal e do Histórico, o
backfill do boot e o isolamento por usuário. Pelas rotas do /app com Postgres real.

Controles (formato de `docs/controles_declarados.md`):
- G3: trocar `method == "manual" and e_fluxo(declaration)` por `method == "manual"` no `_bind` -> VERMELHO
  `test_g3_aporte_escolhido_a_mao_nao_ganha_a_flag`; tirar o `e_fluxo(d) and` de
  `(e_fluxo(d) and d["manual_only"])` no reconcile -> VERMELHO `test_g3_aporte_com_flag_forjada_casa_como_hoje`.
- G4: no grafo do reconcile, `for d in pending if not (e_fluxo(d) and d["manual_only"])` -> VERMELHO
  `test_g4_so_manual_continua_disputando_o_candidato`. Positivo: `test_g4_sem_a_rival_casa_sozinha`.
- G8: tirar os dois `if not e_fluxo(d): raise ValueError("MOVEMENT_ACTION_UNSUPPORTED")` -> VERMELHO
  `test_g8_aporte_recusa_trocar_e_desfazer`.
- G9: voltar a `d["account_id"] is None` sem o `and not fluxo` -> VERMELHO
  `test_g9_requires_review_do_fluxo_e_do_aporte` (o lado do aporte é o positivo).
- G11: `false AS bank_declaration` no ramo de launches de `list_history` -> VERMELHO
  `test_g11_bank_declaration_no_historico`.
- Backfill: tirar `l.tipo in ('despesa', 'receita') and` do update do boot -> VERMELHO
  `test_backfill_liga_so_o_fluxo_manual`.
"""
from __future__ import annotations

import uuid

import pytest

import db
from conftest import _cleanup_user, promote_to_pro
from db.schema import init_db
from tests._patrimonio_helpers import q
from tests._retrato_declaracao import em, retrato, t, tid
from tests.test_bank_movements import _bank, _deposit, _sync, _tx
from tests.test_declaracao_a2b_acoes import H, T1, ana, cliente, confirma, desfaz
from tests.test_declaracao_fluxo_reconcilia import declara, sincroniza


def _decl(lid):
    return q("select * from bank_movement_declarations where launch_id=%s", (lid,))


def _aporte(uid, category="Fixed income"):
    """Caixinha "viagem" de R$ 500 paga pelo banco e a saída do extrato já importada."""
    promote_to_pro(uid)
    cid, source = _bank(uid)
    lid = _deposit(uid, source)
    _sync(cid, 500, _tx(category=category))
    db.import_open_finance_launches(uid)
    return cid, lid, cliente(uid)


def test_g3_aporte_escolhido_a_mao_nao_ganha_a_flag(user_id):
    _, lid, c = _aporte(user_id, category="Same person transfer")  # transferência genérica: só a escolha manual liga
    assert _decl(lid)["matched_transaction_id"] is None
    assert confirma(c, user_id, lid, "tx1").status_code == 200
    d = _decl(lid)
    assert d["confirmation_method"] == "manual" and d["manual_only"] is False


def test_g3_escolher_o_mesmo_par_automatico_do_aporte_nao_muda_nada(user_id):
    _, lid, c = _aporte(user_id)  # categoria padrão: casa sozinho
    assert _decl(lid)["confirmation_method"] == "automatic"
    assert confirma(c, user_id, lid, "tx1").status_code == 200
    d = _decl(lid)
    assert d["confirmation_method"] == "automatic" and d["manual_only"] is False  # a regra da flag é só do fluxo


def test_g3_aporte_com_flag_forjada_casa_como_hoje(user_id):
    promote_to_pro(user_id)
    cid, source = _bank(user_id)
    lid = _deposit(user_id, source)
    q("update bank_movement_declarations set manual_only=true where launch_id=%s returning 1", (lid,))
    _sync(cid, 500, _tx())
    db.import_open_finance_launches(user_id)
    assert _decl(lid)["confirmation_method"] == "automatic"


def test_g4_so_manual_continua_disputando_o_candidato(user_id):
    promote_to_pro(user_id)
    cid, _ = _bank(user_id)
    c = cliente(user_id)
    d1 = declara(user_id, criado_em=em(0))
    r = c.post(f"/open-finance/{user_id}/movements/undo", json={"launch_id": d1, "transaction_id": 0}, headers=H)
    assert r.json() == {"ok": True, "changed": False}  # sem par: só liga a flag (S0 -> S1)
    declara(user_id, criado_em=em(0))
    sincroniza(cid, user_id, t("M", -50, 0, "MERCADO BOM PRECO"))
    assert [x[2] for x in retrato(user_id)["D"]] == [None, None]  # D2 não casa: T é de duas


def test_g4_sem_a_rival_casa_sozinha(user_id):
    promote_to_pro(user_id)
    cid, _ = _bank(user_id)
    declara(user_id, criado_em=em(0))
    sincroniza(cid, user_id, t("M", -50, 0, "MERCADO BOM PRECO"))
    assert [x[2] for x in retrato(user_id)["D"]] == ["M"]


def test_g8_aporte_recusa_trocar_e_desfazer(user_id):
    cid, lid, c = _aporte(user_id)
    _sync(cid, 0, _tx(), _tx(txid="tx2"))
    db.import_open_finance_launches(user_id)
    antes = retrato(user_id)
    assert antes["D"][0][2] == "tx1" and antes["D"][0][3] == "automatic"
    assert desfaz(c, user_id, lid, "tx1").status_code == 409
    assert confirma(c, user_id, lid, "tx2", troca="tx1").status_code == 409
    assert retrato(user_id) == antes


def test_g9_requires_review_do_fluxo_e_do_aporte(user_id):
    promote_to_pro(user_id)
    cid, source = _bank(user_id)
    c = cliente(user_id)
    declara(user_id, criado_em=em(0))
    aporte = _deposit(user_id, source)
    q("update bank_movement_declarations set account_id=null where launch_id=%s returning 1", (aporte,))
    rows = {m["fluxo"]: m for m in c.get(f"/open-finance/{user_id}/movements").json()["movements"]}
    assert rows[True]["requires_review"] is False and rows[True]["tipo"] == "despesa"
    assert rows[False]["requires_review"] is True  # aporte sem conta: igual a hoje


def test_g11_bank_declaration_no_historico(user_id):
    cid, casada_id, c = ana(user_id)  # casada com T1
    solta_id = declara(user_id, criado_em=em(0), alvo="farmacia", categoria="saude")  # sem par
    comum = db.add_launch_and_update_balance(user_id, "despesa", 30, "padaria", None)[0]
    itens = c.get(f"/history/{user_id}/list?limit=50").json()["items"]
    marcados = {i["id"]: i["bank_declaration"] for i in itens}
    sombra_t2 = q("select imported_launch_id from open_finance_transactions where id=%s",
                  (tid(user_id, "T2"),))["imported_launch_id"]
    assert marcados == {casada_id: True, solta_id: True, comum: False, sombra_t2: False}


def test_backfill_ignora_declaracao_com_conta(user_id):
    """O fluxo é a declaração SEM conta (`d.account_id is null`): com conta, o backfill não liga a flag."""
    promote_to_pro(user_id)
    cid, _ = _bank(user_id)
    d = declara(user_id, criado_em=em(0))
    q("""update bank_movement_declarations set confirmation_method='manual',
                account_id=(select a.id from open_finance_accounts a where a.connection_id=%s limit 1)
          where launch_id=%s returning 1""", (cid, d))
    assert _decl(d)["account_id"] is not None
    init_db()
    assert _decl(d)["manual_only"] is False


def test_backfill_liga_so_o_fluxo_manual(user_id):
    """DP-8 = a: o update do boot liga a flag só no fluxo casado à mão (o código novo já grava junto)."""
    promote_to_pro(user_id)
    cid, source = _bank(user_id)
    legado = declara(user_id, criado_em=em(0))
    auto = declara(user_id, criado_em=em(0), alvo="farmacia", categoria="saude")
    sincroniza(cid, user_id, T1, t("F", -50, 0, "FARMACIA SAO JOAO"))
    aporte = _deposit(user_id, source)
    # O aporte vai sem conta, como o legado migrado sem conta achada: só o filtro de tipo o separa do fluxo.
    q("""update bank_movement_declarations set confirmation_method='manual', manual_only=false
          where launch_id = any(%s) returning 1""", ([legado, aporte],))
    q("update bank_movement_declarations set account_id=null where launch_id=%s returning 1", (aporte,))
    init_db()
    init_db()  # idempotente
    assert (_decl(legado)["manual_only"], _decl(auto)["manual_only"], _decl(aporte)["manual_only"]) == (True, False, False)


@pytest.fixture
def outro():
    uid = int(uuid.uuid4().int % 10_000_000_000)
    db.ensure_user(uid)
    promote_to_pro(uid)
    yield uid
    _cleanup_user(uid)


def test_isolamento_entre_usuarios(user_id, outro):
    _, d, c = ana(user_id)
    antes = retrato(user_id)
    b = cliente(outro)
    t1 = tid(user_id, "T1")
    for path, body in (("undo", {"launch_id": d, "transaction_id": t1}),
                       ("confirm", {"launch_id": d, "transaction_id": tid(user_id, "T2"), "replace_transaction_id": t1})):
        assert b.post(f"/open-finance/{outro}/movements/{path}", json=body, headers=H).status_code == 404
        assert b.post(f"/open-finance/{user_id}/movements/{path}", json=body, headers=H).status_code in (401, 403)
        assert c.post(f"/open-finance/{user_id}/movements/{path}", json=body).status_code == 403  # sem CSRF
    assert b.get(f"/open-finance/{outro}/movements?launch_id={d}").json()["movements"] == []
    assert b.get(f"/open-finance/{user_id}/movements?launch_id={d}").status_code in (401, 403)
    assert retrato(user_id) == antes
