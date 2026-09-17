"""Aviso "a conferir" — fonte única `core/services/funding.aviso_conferir`.

Aparece em /saldo (core/handlers/balance.py), na resposta de lançamento
(core/handlers/launches.py), na IA (core/services/ai_chat/tools/balance.py) e
nos relatórios (core/reports/formatting.py). `reconciliations.js` (Etapa 3)
espelha o mesmo cálculo em JS; a fixture `tests/fixtures/aviso_conferir.json`
é lida pelos dois lados (CLAUDE.md §0.7).

Sinal (contrato do PR 1, `db/open_finance.py`): `delta_se_confirmar` já vem
com o sinal certo. "pode ser" = exibido + delta — despesa pendente confirma
para CIMA (o dinheiro "some" da Carteira só quando confirmado), receita
pendente confirma para BAIXO (o dinheiro pendente já aparece na Carteira
antes de ser fundido). As duas provas concretas estão em
`test_saldo_despesa_pendente_pode_ser_maior` e
`test_saldo_receita_pendente_pode_ser_menor`.

Negativo do grupo: cada teste de superfície SÓ passa com a chamada de
`aviso_conferir` no lugar certo — apagá-la em qualquer uma derruba o teste
dela (conferido manualmente, ver relato do Coder).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import db
from core.services import funding
from core.services.ai_chat.tools.balance import _get_balance
from core.reports.reports_daily import build_daily_report_text

from tests._fusao_of_helpers import (  # noqa: F401 (uid_pro/ia_fora são fixtures)
    ia_fora, manda, uid_pro,
)
from tests.test_reconciliacao_resolver import pendencia
from tests.test_reconciliacao_resumo_e_guardas import RECEITA

_FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "aviso_conferir.json").read_text())


# ── helper × fixture (espelhada em JS na Etapa 3) ───────────────────────────

def test_helper_bate_a_fixture_compartilhada_com_o_js():
    for caso in _FIXTURE:
        rec = {"pending_count": caso["pending_count"],
               "delta_se_confirmar": caso["delta_se_confirmar"]}
        aviso = funding.aviso_conferir(caso["exibido"], rec)
        if caso["pending_count"] <= 0:
            assert aviso == "", caso["nome"]
            continue
        assert aviso.startswith(f"⚠ {caso['pending_count']} lançamento(s) a conferir"), caso["nome"]
        if caso["esperado_pode_ser"] is None:
            assert "pode ser" not in aviso, caso["nome"]
        else:
            assert aviso == (f"⚠ {caso['pending_count']} lançamento(s) a conferir"
                              f" · pode ser {caso['esperado_pode_ser']}"), caso["nome"]


def test_sem_pendencia_ou_rec_none_e_vazio():
    assert funding.aviso_conferir(100, None) == ""
    assert funding.aviso_conferir(100, {"pending_count": 0, "delta_se_confirmar": 5}) == ""


# ── /saldo (WhatsApp) ────────────────────────────────────────────────────────

def test_saldo_despesa_pendente_pode_ser_maior(uid_pro, ia_fora):
    """`pendencia()` default: despesa de -1 pendente, exibido 112,88."""
    pendencia(uid_pro)
    resp = manda(uid_pro, "/saldo")
    assert "⚠ 1 lançamento(s) a conferir · pode ser R$ 113,88" in resp
    assert " Confira no dashboard." in resp


def test_saldo_receita_pendente_pode_ser_menor(uid_pro, ia_fora):
    pendencia(uid_pro, **RECEITA)
    resp = manda(uid_pro, "/saldo")
    cb = db.get_consolidated_balance(uid_pro)
    rec = cb["reconciliation"]
    assert float(rec["delta_se_confirmar"]) < 0, "receita: o delta é negativo"
    assert "⚠ 1 lançamento(s) a conferir · pode ser R$ 188,26" in resp


def test_saldo_sem_pendencia_nao_cita_a_conferir(uid_pro, ia_fora):
    db.add_launch_and_update_balance(uid_pro, "receita", 10, None, "seed")
    resp = manda(uid_pro, "/saldo")
    assert "a conferir" not in resp
    # byte a byte igual ao formato de antes deste PR: nada além das seções
    # já existentes (Conta Corrente / Hoje / Gastos do mês), sem linha extra.
    assert resp.startswith("🏦 *Conta Corrente*: R$ 10,00\n\n📋 *Hoje*: nenhum gasto registrado\n\n📊 *Gastos em ")
    assert resp.endswith(": R$ 0,00")


# ── resposta de lançamento ───────────────────────────────────────────────────

def test_resposta_de_lancamento_com_pendencia_mostra_aviso(uid_pro, ia_fora):
    pendencia(uid_pro)
    resp = manda(uid_pro, "gastei 50 no almoço")
    assert "⚠ 1 lançamento(s) a conferir · pode ser R$" in resp
    assert "ID: #" in resp
    assert resp.index("a conferir") < resp.index("ID: #"), "aviso tem de vir antes do ID"


def test_resposta_de_lancamento_sem_pendencia_nao_cita_a_conferir(uid_pro, ia_fora):
    resp = manda(uid_pro, "gastei 20 no busão")
    assert "a conferir" not in resp


# ── IA (get_balance) ─────────────────────────────────────────────────────────

def test_ia_get_balance_floats_e_aviso(uid_pro, ia_fora):
    pendencia(uid_pro)
    out = _get_balance(uid_pro, {})

    # json.dumps SEM default=str, só nos campos que este PR adiciona: se
    # `delta_se_confirmar` voltasse a ser Decimal, isto levanta TypeError — o
    # controle negativo deste caso (runner.py:518 só cobre o erro com
    # default=str, mas o tipo já teria virado string, não número).
    # `bank_movements` já tinha Decimal cru antes deste PR — fora de escopo,
    # por isso não entra nesta checagem.
    json.dumps({"reconciliation": out["reconciliation"], "aviso_conferir": out["aviso_conferir"]})

    assert isinstance(out["reconciliation"]["delta_se_confirmar"], float)
    assert out["reconciliation"] == {"pending_count": 1, "delta_se_confirmar": 1.0}
    assert out["aviso_conferir"] == "⚠ 1 lançamento(s) a conferir · pode ser R$ 113,88"


def test_ia_get_balance_sem_pendencia_aviso_vazio(uid_pro, ia_fora):
    db.add_launch_and_update_balance(uid_pro, "receita", 10, None, "seed")
    out = _get_balance(uid_pro, {})
    assert out["aviso_conferir"] == ""
    assert out["reconciliation"] == {"pending_count": 0, "delta_se_confirmar": 0.0}


# ── relatório diário ─────────────────────────────────────────────────────────

def test_relatorio_diario_com_pendencia_mostra_aviso(uid_pro, ia_fora):
    pendencia(uid_pro)
    texto = build_daily_report_text(uid_pro)
    assert "⚠ 1 lançamento(s) a conferir · pode ser R$ 113,88" in texto


def test_relatorio_diario_sem_pendencia_nao_cita_a_conferir(uid_pro, ia_fora):
    db.add_launch_and_update_balance(uid_pro, "receita", 10, None, "seed")
    texto = build_daily_report_text(uid_pro)
    assert "a conferir" not in texto
