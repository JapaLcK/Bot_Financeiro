"""PR-A: o que a declaração de despesa/receita "paga pelo banco" faz (e não faz) com os totais.

Resumo conta a linha UMA vez (com ou sem par), a Carteira não se move (`delta_conta` 0), o
saldo consolidado não muda, e a Previsão ganha o motivo `declaracao_bancaria_a_conferir`.
Controle negativo: `apply_delta=True` no escritor deixa vermelho `test_carteira_nao_se_move`.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import db
from core.services.cashflow_snapshot import carregar
from db.accounts import _validar_efeitos
from db.bank_movements import bank_movement_summary
from db.resumo_mes import mes_de, totais_do_mes
from tests._patrimonio_helpers import q
from tests.test_bank_movements import _bank
from tests.test_declaracao_fluxo_reconcilia import declara, sincroniza, tx
from utils_date import _tz, today_tz


def test_carteira_nao_se_move(user_id):
    cid, _ = _bank(user_id)
    antes = (db.get_balance(user_id), db.get_consolidated_balance(user_id)["consolidated"])
    lid = declara(user_id)
    assert (db.get_balance(user_id), db.get_consolidated_balance(user_id)["consolidated"]) == antes
    sincroniza(cid, user_id, tx())
    assert (db.get_balance(user_id), db.get_consolidated_balance(user_id)["consolidated"]) == antes
    ef = q("select efeitos, origem from launches where id=%s", (lid,))
    assert Decimal(str(ef["efeitos"]["delta_conta"])) == 0 and ef["origem"] is None
    assert ef["efeitos"]["funding_source"] == {"kind": "bank", "of_account_id": None, "account_ambiguous": False}
    assert _validar_efeitos(ef["efeitos"], escopo_conta_corrente=False) == 0  # a allowlist do apagar conhece as chaves


def test_resumo_por_mes_conta_uma_vez(user_id):
    _bank(user_id)
    hoje = today_tz()
    mes_passado = datetime.now(_tz()).replace(day=1) - timedelta(days=3)
    declara(user_id, 50)
    declara(user_id, 80, "receita", criado_em=mes_passado)
    t_hoje, t_passado = totais_do_mes(user_id, hoje), totais_do_mes(user_id, mes_passado.date())
    assert (t_hoje["saiu"], t_hoje["entrou"]) == (50, 0)
    assert (t_passado["saiu"], t_passado["entrou"]) == (0, 80)
    assert mes_de(hoje)[0] != mes_de(mes_passado.date())[0]


def test_resumo_do_banco_sem_par_e_a_conferir(user_id):
    _bank(user_id)
    declara(user_id, 50)
    declara(user_id, 80, "receita")
    r = bank_movement_summary(user_id)
    assert r["pending_count"] == 2 and r["patrimony_status"] == "to_review"
    assert (r["declared_outflows"], r["declared_inflows"]) == (50, 80)


def test_resumo_do_banco_zera_ao_casar(user_id):
    cid, _ = _bank(user_id)
    declara(user_id, 50)
    sincroniza(cid, user_id, tx())
    assert bank_movement_summary(user_id)["pending_count"] == 0
    assert totais_do_mes(user_id, today_tz())["saiu"] == 50


def test_previsao_ganha_o_motivo_enquanto_sem_par(user_id):
    cid, _ = _bank(user_id)
    lid = declara(user_id)
    cod = lambda: [(m.codigo, m.origem_id) for m in carregar(user_id, today_tz(), today_tz() + timedelta(days=30)).motivos]
    assert ("declaracao_bancaria_a_conferir", lid) in cod()
    sincroniza(cid, user_id, tx())
    assert not any(c == "declaracao_bancaria_a_conferir" for c, _ in cod())


def test_documenta_leitores_de_declaracao_sem_par_hoje(user_id):
    """DOCUMENTA o comportamento atual (decisão "A conferir na hora ou após 3 dias" é do PR-B):
    despesa declarada sem par já liga `to_review`, `movimentos_pendentes` (patrimônio, contas de
    hoje) e o aviso do relatório; `pending_outflows` ignora conta NULL e não reduz nenhuma conta."""
    from db.bank_movements import pending_outflows
    from db.contas_hoje import listar
    from db.patrimonio import calcular
    from core.reports.formatting import finish_report
    cid, _ = _bank(user_id)
    declara(user_id)
    with db.get_conn() as conn, conn.cursor() as cur:
        assert "movimentos_pendentes" in calcular(cur, user_id)["motivos"]
        assert "movimentos_pendentes" in listar(cur, user_id)["carteira"]["motivos"]
        assert pending_outflows(cur, user_id) == {}  # conta NULL: nenhuma disponibilidade reduzida
        conn.rollback()
    assert bank_movement_summary(user_id)["patrimony_status"] == "to_review"
    assert db.pending_bank_outflows(user_id) == {}
    linhas = []
    finish_report(user_id, linhas)
    assert any("não confirmadas" in x for x in linhas)
    sincroniza(cid, user_id, tx())  # com par, todos os leitores voltam ao normal
    with db.get_conn() as conn, conn.cursor() as cur:
        assert "movimentos_pendentes" not in calcular(cur, user_id)["motivos"]
        conn.rollback()
    assert bank_movement_summary(user_id)["patrimony_status"] == "confirmed"
