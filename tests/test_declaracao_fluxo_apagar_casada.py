"""Codex P2 (#969): apagar uma despesa/receita declarada JÁ casada devolve a transação do banco como sombra
na MESMA transação do apagar (antes ela sumia da lista e dos totais até o próximo import).

`delete_launch_and_rollback` é o ponto único: o v2 `apagar`, o `DELETE /launches` do /app, a tool de IA, o
WhatsApp (singular e em lote) e o "apagar tudo" passam por ele. A sombra é a do import (`_insert_of_shadow`):
um import depois não duplica. Só fluxo declarado casado; aporte, sem par e dinheiro puro não mudam. O
"apagar tudo" também recria (a regra da FUNDIDA, de não recriar nele, fica como está e é testada em outros arquivos).
Controle negativo: sem `devolve_sombra` no apagar, o Resumo cai a 0 e os testes de volta ficam vermelhos.
"""
from __future__ import annotations

from decimal import Decimal

import db
from conftest import usuario_pagante
from db.bank_declaracoes import devolve_sombra
from db.resumo_mes import totais_do_mes
from tests._patrimonio_helpers import q
from tests.test_api_v2_resumo_mes import libera  # noqa: F401 (fixture)
from tests.test_bank_movements import _bank, _deposit
from tests.test_category_launches_query import _cliente_logado
from tests.test_declaracao_fluxo_reconcilia import decl, declara, n_linhas, saiu, sincroniza, tx
from utils_date import today_tz

D = Decimal
CAMPOS = "tipo, valor, categoria, alvo, nota, source, is_internal_movement, posted_at, currency, efeitos"


def _casada(uid=None):
    uid = uid or usuario_pagante()
    cid, _ = _bank(uid)
    lid = declara(uid)
    sincroniza(cid, uid, tx())
    assert decl(lid)["matched_transaction_id"]
    return uid, cid, lid


def _sombra(uid):
    return q(f"select id, {CAMPOS} from launches where user_id=%s and source='open_finance'", (uid,))


def test_apagar_devolve_a_sombra_do_import_e_o_resumo_nao_cai():
    uid, _, lid = _casada()
    assert saiu(uid) == 50
    aviso = db.delete_launch_and_rollback(uid, lid)
    assert aviso and "continua na sua lista" in aviso
    assert saiu(uid) == 50 and n_linhas(uid, 50) == 1
    t = q("""select o.imported_launch_id i, o.match_launch_id m, o.reconciliation_status s
               from open_finance_transactions o""")
    assert t["i"] == _sombra(uid)["id"] and t["m"] is None and t["s"] == "imported"
    db.import_open_finance_launches(uid)  # idempotente: continua uma sombra e o Resumo em 50
    assert n_linhas(uid, 50) == 1 and saiu(uid) == 50


def test_a_sombra_devolvida_e_igual_a_de_um_import_normal():
    uid, _, lid = _casada()
    db.delete_launch_and_rollback(uid, lid)
    outro = usuario_pagante()
    cid2, _ = _bank(outro)
    sincroniza(cid2, outro, tx())  # import normal, sem declaração
    a, b = _sombra(uid), _sombra(outro)
    assert {k: v for k, v in a.items() if k != "id"} == {k: v for k, v in b.items() if k != "id"}
    assert D(str(a["efeitos"]["delta_conta"])) == 0


def test_app_delete_launches_devolve_a_sombra_e_o_aviso():
    uid, _, lid = _casada()
    client, headers = _cliente_logado(uid)
    r = client.delete(f"/launches/{uid}/{lid}", headers=headers)
    assert r.status_code == 200, r.text
    assert "continua na sua lista" in (r.json().get("aviso") or "")
    assert saiu(uid) == 50 and n_linhas(uid, 50) == 1


def test_v2_apagar_devolve_a_sombra(libera):
    from tests.test_api_v2_lancamentos_escrita import post
    uid, _, lid = _casada()
    libera(uid)
    assert post(uid, "apagar", {"id": f"l{lid}"}).status_code == 200
    assert saiu(uid) == 50 and n_linhas(uid, 50) == 1


def test_apagar_tudo_devolve_a_sombra_da_declarada_casada():
    """Diferente da fundida (decisão do dono: o "apagar tudo" não recria), aqui sumiria dado real do banco
    até um import que pode nunca vir (conexão pausada ou desconectada)."""
    uid, _, lid = _casada()
    assert saiu(uid) == 50
    r = db.delete_all_launches_and_rollback(uid)
    assert r["deleted"] == 1 and decl(lid) is None and not r["errors"]
    assert saiu(uid) == 50 and n_linhas(uid, 50) == 1  # a sombra já voltou, na mesma transação
    t = q("select imported_launch_id i, reconciliation_status s from open_finance_transactions")
    assert t["i"] == _sombra(uid)["id"] and t["s"] == "imported"
    db.import_open_finance_launches(uid)  # idempotente
    assert n_linhas(uid, 50) == 1 and saiu(uid) == 50


def test_apagar_tudo_so_consulta_a_declaracao_das_linhas_pagas_pelo_banco(monkeypatch):
    """Volume: mil linhas comuns não fazem consulta de declaração; só a paga pelo banco passa por `devolve_sombra`."""
    import db.bank_declaracoes as bd
    uid, _, lid = _casada()
    for i in range(300):
        db.add_launch_and_update_balance(uid, "despesa", 1 + i % 7, "x", "y", "mercado")
    chamadas = []
    original = bd.devolve_sombra
    monkeypatch.setattr(bd, "devolve_sombra", lambda *a, **k: chamadas.append(a[2]) or original(*a, **k))
    r = db.delete_all_launches_and_rollback(uid)
    assert r["deleted"] == 301 and chamadas == [lid]
    assert saiu(uid) == 50  # só a sombra do banco sobrou


def test_apagar_tudo_com_falha_na_sombra_mantem_a_linha_e_o_vinculo(monkeypatch):
    import db.open_finance as of
    uid, _, lid = _casada()
    monkeypatch.setattr(of, "_insert_of_shadow", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("falha")))
    r = db.delete_all_launches_and_rollback(uid)
    assert r["deleted"] == 0 and r["errors"]  # a linha não saiu pela metade
    assert decl(lid)["matched_transaction_id"] and q("select id from launches where id=%s", (lid,))
    assert saiu(uid) == 50


def test_sem_par_aporte_e_dinheiro_puro_nao_mudam():
    uid = usuario_pagante()
    cid, source = _bank(uid)
    sem_par = declara(uid, 70)
    with db.get_conn() as conn, conn.cursor() as cur:
        assert devolve_sombra(cur, uid, sem_par, []) == []
        aporte = _deposit(uid, source)
        assert devolve_sombra(cur, uid, aporte, []) == []
        conn.rollback()
    db.delete_launch_and_rollback(uid, sem_par)  # sem transação: nada a devolver
    assert n_linhas(uid, 70) == 0
    puro = db.add_launch_and_update_balance(uid, "despesa", 20, "x", "y", "mercado")[0]
    db.delete_launch_and_rollback(uid, puro)
    assert n_linhas(uid, 20) == 0
