"""PR-B (ADR 0006), retrato completo (lançamentos, extrato, declarações, Carteira e saídas do mês) depois de a
declaração NASCER PELA CONVERSA e de o banco chegar, nos dois sentidos da conciliação (plano, seção 9, S2a-S2e).

O dinheiro não é criado nem perdido: a Carteira fica em R$ 200,00 em todos os retratos e as saídas do mês contam o
gasto uma vez (a declaração e a transação que casou são o MESMO gasto). A comparação é do retrato inteiro, não de um campo.

Controle negativo: `apply_delta=True` no ramo de declaração do escritor (a Carteira passa a mudar) deixa vermelhos todos
os retratos (`C`). Positivo: S2a/S2c, onde o par existe e o gasto é contado uma vez.
"""
from __future__ import annotations

import pytest

import db
import db.bank_conferencia as conferencia
from db.resumo_mes import totais_do_mes
from tests._declaracao_b import ana, carteira, liga, manda_wamid, n_lancamentos, q  # noqa: F401
from tests._fusao_of_helpers import ia_fora, uid_pro  # noqa: F401
from tests._retrato_declaracao import BASE, casada, decl, retrato, sombra, solta, t, tid
from tests.test_bank_movements import _bank
from tests.test_declaracao_fluxo_reconcilia import sincroniza
from utils_date import today_tz

HOJE = (today_tz() - BASE).days  # o dia da declaração, em relação a BASE
LOJA = "SUPERMERCADO XYZ"


def R(L, T, D, saiu, C=200.0):
    return {"L": sorted(L), "T": sorted(T), "D": sorted(D), "C": C, "saiu": saiu}


def declara_pela_conversa(uid, valor=50, alvo="mercado"):
    """"gastei 50 no mercado" -> "banco" (a forma na resposta; a forma dita na frase também deixa o alvo limpo)."""
    manda_wamid(uid, f"gastei {valor} no {alvo}")
    return manda_wamid(uid, "banco")[0]


@pytest.fixture
def banco(ana):
    cid, _ = _bank(ana)
    return cid


def test_s2a_banco_chega_depois_e_casa_sozinho(ana, liga, banco):
    declara_pela_conversa(ana)
    assert retrato(ana) == R([decl(HOJE)], [], [("decl mercado", -50.0, None, None, False, False)], 50.0)
    sincroniza(banco, ana, t("T1", -50, HOJE, LOJA))
    assert retrato(ana) == R([decl(HOJE)], [casada("T1", -50.0, HOJE)],
                             [("decl mercado", -50.0, "T1", "automatic", False, False)], 50.0)


def test_s2b_valor_diferente_nao_casa_e_a_escolha_no_app_fica_com_o_do_banco(ana, liga, banco):
    declara_pela_conversa(ana)
    sincroniza(banco, ana, t("T1", -52.30, HOJE, LOJA))
    assert retrato(ana) == R([decl(HOJE), sombra("T1", 52.3, HOJE)], [solta("T1", -52.3, HOJE)],
                             [("decl mercado", -50.0, None, None, False, False)], 102.3)
    lid = q("select launch_id from bank_movement_declarations where user_id=%s", (ana,))["launch_id"]
    conferencia.confirm_bank_movement(ana, lid, tid(ana, "T1"))
    assert retrato(ana) == R([decl(HOJE, valor=52.3)], [casada("T1", -52.3, HOJE)],
                             [("decl mercado", -50.0, "T1", "manual", True, False)], 52.3)


def test_s2c_transacao_ja_existia_casa_na_hora_e_a_resposta_diz(ana, liga, banco):
    sincroniza(banco, ana, t("T1", -50, HOJE, LOJA))
    db.import_open_finance_launches(ana)
    assert retrato(ana)["saiu"] == 50.0  # só a sombra
    r = declara_pela_conversa(ana)
    assert "✅ Já estava no seu extrato e conferi:" in r and LOJA in r and "R$ 50,00" in r, r
    assert retrato(ana) == R([decl(HOJE)], [casada("T1", -50.0, HOJE)],
                             [("decl mercado", -50.0, "T1", "automatic", False, False)], 50.0)


def test_s2c_sem_evidencia_de_comercio_nao_casa_e_a_resposta_e_a_de_a_conferir(ana, liga, banco):
    sincroniza(banco, ana, t("T1", -50, HOJE, "FARMACIA SAO JOAO"))
    r = declara_pela_conversa(ana)
    assert "Ainda não apareceu no seu extrato" in r, r
    assert retrato(ana) == R([decl(HOJE), sombra("T1", 50.0, HOJE)], [solta("T1", -50.0, HOJE)],
                             [("decl mercado", -50.0, None, None, False, False)], 100.0)


def test_s2d_duas_declaracoes_iguais_e_um_pix_nada_casa_e_o_gasto_real_e_um(ana, liga, banco):
    declara_pela_conversa(ana)
    declara_pela_conversa(ana)  # wamids diferentes: dois gastos
    sincroniza(banco, ana, t("T1", -50, HOJE, LOJA))
    sem_par = ("decl mercado", -50.0, None, None, False, False)
    assert retrato(ana) == R([decl(HOJE), decl(HOJE), sombra("T1", 50.0, HOJE)], [solta("T1", -50.0, HOJE)],
                             [sem_par, sem_par], 150.0)
    lid = q("select launch_id from bank_movement_declarations where user_id=%s order by launch_id limit 1", (ana,))["launch_id"]
    conferencia.confirm_bank_movement(ana, lid, tid(ana, "T1"))
    # (o `retrato` não ordena par `None` com `str`: lê-se o total e a Carteira direto)
    assert float(totais_do_mes(ana, BASE)["saiu"]) == 100.0 and carteira(ana) == 200.0  # os dois gastos reais


def test_receita_declarada_conta_como_entrada_e_nao_mexe_na_carteira(ana, liga, banco):
    manda_wamid(ana, "recebi 1000 de salario")
    manda_wamid(ana, "pix")
    r = retrato(ana)
    assert r["C"] == 200.0 and r["L"][0][1] == "receita" and r["L"][0][2] == 1000.0 and r["saiu"] == 0.0, r
