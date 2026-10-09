"""Fase 1a: o fixo/receita manual × a recorrência do Open Finance, a comparação antes ×
depois nos consumidores do motor (Q18) e a conversa pelo `handle_incoming`.

Casos 15–18 com relógio congelado (`ler_em`). A comparação U1–U6 roda nos consumidores
de verdade, que leem `date.today()`: a semeadura é relativa a hoje, com a última
cobrança há 20–23 dias num dia <= 28, o que dá 1, 2 e 3 ciclos em 30, 60 e 90 dias
para qualquer data (n-ésimo ciclo = última + n meses: hoje+5..11, +36..42, +66..72,
+97..103). O "antes" é literal, calculado à mão a partir da semeadura."""
from datetime import date, timedelta
from decimal import Decimal as D

import pytest

import db
from _apoio_assinaturas import HOJE, conta, mensais, rp, semeia
from _fusao_of_helpers import ia_fora, manda  # noqa: F401 (ia_fora é fixture)
from conftest import usuario_pagante
from core.services import previsao_v2
from core.services.ai_chat.tools.bills import _check_cashflow
from core.services.cashflow import _projection
from core.services.cashflow_forecast import forecast_horizons
from core.services.decision_simulator import Simulacao, simulate
from db.recurring import create_recurring_expense
from db.recurring_income import create_recurring_income
from tests.test_manual_launches_carteira_piggy import _connect_fake_bank
from tests.test_previsao_recorrencias_of import codigos, do_banco, ler_em, netflix, salario


def saldo_90(s, hoje=HOJE):
    return _projection(hoje, s.base, s.ocorrencias, hoje + timedelta(days=90))["projetado"]


def test_15_manual_casado_conta_e_o_do_banco_fica_fora(user_id):
    db.set_balance(user_id, D(1000))
    create_recurring_expense(user_id, "Netflix", 39.90, "assinaturas", 5, "account", start_date=HOJE)
    txs = mensais("nf", ["-39.90"] * 3, ultima=date(2026, 9, 6), desc="NETFLIX.COM")
    semeia(user_id, [conta("acc-nf", txs)], [rp("NETFLIX.COM", -39.9, txs)])
    s = ler_em(user_id)
    es = do_banco(s)
    assert len(es) == 3 and not any(e.incluida for e in es)
    assert all("recorrencia_banco_igual_a_fixo_manual" in codigos(e) for e in es)
    assert saldo_90(s) == saldo_90(ler_em(user_id, bancos=False)) == D("880.30")


def test_16_casa_so_pelo_valor_exato(user_id):
    create_recurring_expense(user_id, "Aluguel", 2000, "moradia", 10, "account", start_date=HOJE)
    txs = mensais("al", ["-2000.00"] * 3, ultima=date(2026, 9, 9), desc="PIX FULANO")
    semeia(user_id, [conta("acc-al", txs)], [rp("PIX FULANO", -2000, txs)])
    es = do_banco(ler_em(user_id))
    assert es and all(not e.incluida and "recorrencia_banco_igual_a_fixo_manual" in codigos(e) for e in es)


def test_17_dois_manuais_candidatos_nao_casam(user_id):
    db.set_balance(user_id, D(1000))
    create_recurring_expense(user_id, "Academia", 100, "saude", 5, "account", start_date=HOJE)
    create_recurring_expense(user_id, "Academia Smart", 100, "saude", 6, "account", start_date=HOJE)
    txs = mensais("ac", ["-100"] * 3, desc="ACADEMIA SMART")
    semeia(user_id, [conta("acc-ac", txs)], [rp("ACADEMIA SMART", -100, txs)])
    s = ler_em(user_id)
    es = do_banco(s)
    assert es and all(e.incluida and "recorrencia_banco_pode_repetir_manual" in codigos(e) for e in es)
    assert saldo_90(s) == D("100")  # 1000 − 2 manuais × 3 − o do banco × 3


def test_18_receita_ambigua_nunca_soma_as_duas(user_id):
    db.set_balance(user_id, D(1000))
    create_recurring_income(user_id, "Trabalho", 5000, "salario", 5, start_date=HOJE)
    salario(user_id, valor="6500", ultima=date(2026, 9, 4))
    s = ler_em(user_id)
    es = do_banco(s)
    assert es and all(not e.incluida and "recorrencia_banco_pode_repetir_manual" in codigos(e) for e in es)
    assert saldo_90(s) == D("16000")


# Manual casado que começa no futuro: o 1º ciclo dele é 08/12 (início 06/12, dia 8). O
# banco (dia 5) conta sozinho em out e nov; em 05/12 já é o ciclo do manual, mesmo
# antes do início e do dia dele — senão dezembro contaria duas vezes.
@pytest.mark.parametrize("receita", [False, True], ids=["fixo", "receita"])
def test_18b_manual_que_comeca_depois_nao_apaga_o_banco_de_antes(user_id, receita):
    db.set_balance(user_id, D(1000))
    if receita:
        create_recurring_income(user_id, "Trabalho", 5000, "salario", 8, start_date=date(2026, 12, 6))
        salario(user_id)
    else:
        create_recurring_expense(user_id, "Netflix", 39.90, "assinaturas", 8, "account",
                                 start_date=date(2026, 12, 6))
        netflix(user_id)
    s = ler_em(user_id)
    assert [(e.data, e.incluida) for e in do_banco(s)] == [
        (date(2026, 10, 5), True), (date(2026, 11, 5), True), (date(2026, 12, 5), False)]
    assert "recorrencia_banco_igual_a_fixo_manual" in codigos(do_banco(s)[2])
    assert not any({"recorrencia_banco_igual_a_fixo_manual", "recorrencia_banco_pode_repetir_manual"}
                   & codigos(e) for e in do_banco(s)[:2])
    assert saldo_90(s) == D("16000" if receita else "880.30")  # 3 ciclos, nenhum em dobro


# ── Antes × depois (Q18) ─────────────────────────────────────────────────────

def _ultima():
    d = date.today() - timedelta(days=20)
    while d.day > 28:
        d -= timedelta(days=1)
    return d


def _u(n, uid):
    u = _ultima()
    db.set_balance(uid, D(1000))
    if n == 1:
        create_recurring_expense(uid, "Luz", 100, "moradia", (date.today() + timedelta(days=5)).day, "account",
                                 frequency="once", start_date=date.today() + timedelta(days=5))
    elif n == 2:
        semeia(uid, [conta("acc-u2", mensais("u2", ["-10", "-55", "-7"], ultima=u, desc="MERCADO"))], [])
    elif n == 3:
        netflix(uid, ultima=u)
        salario(uid, ultima=u)
    elif n == 4:
        create_recurring_expense(uid, "Netflix", 39.90, "assinaturas", u.day, "account")
        netflix(uid, ultima=u)
    elif n == 5:
        create_recurring_income(uid, "Trabalho", 5000, "salario", u.day)
        salario(uid, valor="6500", ultima=u, desc="EMPRESA X")
    else:
        netflix(uid, ultima=u, tipo="CREDIT")


# (antes, depois) em 30/60/90 dias, à mão: Netflix 39,90 e salário 5000 por ciclo.
ESPERADO = {
    1: (["900", "900", "900"],) * 2,
    2: (["1000", "1000", "1000"],) * 2,
    3: (["1000", "1000", "1000"], ["5960.10", "10920.20", "15880.30"]),
    4: (["960.10", "920.20", "880.30"],) * 2,
    5: (["6000", "11000", "16000"],) * 2,
    6: (["1000", "1000", "1000"],) * 2,
}


@pytest.mark.parametrize("n", range(1, 7))
def test_antes_e_depois_por_centavo_em_todos_os_consumidores(n, monkeypatch):
    monkeypatch.delenv("OF_CONSOLIDATED_BALANCE_ENABLED", raising=False)
    uid = usuario_pagante("pro_max")
    _u(n, uid)
    antes, depois = (list(map(D, x)) for x in ESPERADO[n])
    hoje = date.today()
    sem_banco = ler_em(uid, hoje=hoje, dias=90, bancos=False)
    assert [_projection(hoje, sem_banco.base, sem_banco.ocorrencias, hoje + timedelta(days=k))["projetado"]
            for k in (30, 60, 90)] == antes
    v2 = [m["saldo"] for m in previsao_v2.ler_previsao(uid, 90, (30, 60, 90), True)["marcos"]]
    fc = forecast_horizons(uid, (30, 60, 90))["horizons"]
    tool = [_check_cashflow(uid, {"days": k})["projetado"] for k in (30, 60, 90)]
    sim = simulate(uid, Simulacao(cenarios=[{"nome": "x", "preco": 1}]))["atual"]["saldo_final_90"]
    assert v2 == depois
    assert [D(str(fc[str(k)]["projetado"])) for k in (30, 60, 90)] == depois
    assert [D(str(t)) for t in tool] == depois
    assert D(str(sim)) == depois[2]


# ── Conversa pelo handle_incoming ────────────────────────────────────────────

# O número é o de 30 dias da tabela ESPERADO: a conversa vê o mesmo motor.
@pytest.mark.parametrize("n,esperado", [(3, "R$ 5.960,10"), (4, "R$ 960,10")], ids=["U3", "U4"])
def test_conversa_gasto_pendente_e_previsao_com_recorrencia(n, esperado, monkeypatch, ia_fora):
    monkeypatch.delenv("OF_CONSOLIDATED_BALANCE_ENABLED", raising=False)
    uid = usuario_pagante("pro_max")
    _u(n, uid)
    _connect_fake_bank(uid)  # banco com saldo: o gasto pergunta de onde saiu
    assert "IA-NAO" not in manda(uid, "gastei 50 no mercado")
    antes = db.get_pending_action(uid)
    assert antes and antes["action_type"] == "payment_method_choice"
    resposta = manda(uid, "previsão de saldo daqui 30 dias")
    assert esperado in resposta and "condicional" in resposta.lower(), resposta
    depois = db.get_pending_action(uid)
    assert (depois["action_type"], depois["payload"], depois["created_at"]) == (
        antes["action_type"], antes["payload"], antes["created_at"])
    assert not ia_fora
