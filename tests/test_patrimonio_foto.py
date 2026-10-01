"""A conta do patrimônio (`db/patrimonio.calcular`): composição, isolamento e motivos.

O job, a gravação e a privacidade estão em `tests/test_patrimonio_foto_job.py`.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

import db
import db.bills as B
from conftest import usuario_pagante
from core.services.pluggy_sync import normalize_pluggy_investment
from tests._fusao_of_helpers import conecta_banco, ia_fora, manda  # noqa: F401 (fixture)
from tests._patrimonio_helpers import (caixinha, conexao, conta, foto, horas_atras,
                                       investimento_manual, lancamento, posicao, q, tx_banco)
from utils_date import today_tz

D = Decimal


@pytest.fixture
def uid(monkeypatch):
    monkeypatch.setenv("OF_CASH_ENABLED", "1")
    return usuario_pagante()


def _cenario_completo(uid):
    db.set_balance(uid, D("100"))
    velha = conexao(uid, f"item-velho-{uid}")          # mesma conta em duas conexões
    conta(velha, "acc-brl", "777")
    posicao(velha, "inv-500", "444")
    c = conexao(uid, f"item-{uid}")
    brl = conta(c, "acc-brl", "1000")
    conta(c, "acc-usd", "50", moeda="USD", code="USD")
    tx_banco(brl, "tx-fundida", "-30", imported_launch_id=lancamento(uid, 30))  # carteira 70 + 30
    espelhada = posicao(c, "inv-500", "500")
    caixinha(uid, "Caixinha do banco", "500", of_investment_id=espelhada)
    posicao(c, "inv-usd", "50", moeda="USD", code="USD")
    posicao(c, "inv-resgatada", "300", status="TOTAL_WITHDRAWAL")
    posicao(conexao(uid, f"item-pausado-{uid}", status="PAUSED"), "inv-pausada", "999")
    caixinha(uid, "Viagem", "50")
    investimento_manual(uid, "CDB manual", "70")


def test_composicao_exata(uid):
    _cenario_completo(uid)
    f = foto(uid)
    assert {k: f[k] for k in ("carteira", "bancos", "investimentos_banco", "caixinhas",
                              "investimentos_manuais", "total")} == {
        "carteira": D("100"), "bancos": D("1000"), "investimentos_banco": D("500"),
        "caixinhas": D("50"), "investimentos_manuais": D("70"), "total": D("1720")}
    assert f["base"]["contas"] == ["acc-brl"]
    assert f["base"]["posicoes"] == ["inv-500"]
    assert f["base"]["fora"] == {"moeda": 2, "resgatada": 1, "pausada": 1, "caixinha_espelhada": 0}
    assert sorted(f["base"]["conexoes"].values()) == ["paused", "updated", "updated"]
    assert f["motivos"] == ["carteira_nao_confirmada", "manual_e_banco"]


def test_carteira_e_bancos_batem_com_o_saldo_consolidado_na_conversa(uid, ia_fora):
    conecta_banco(uid, "800")
    B.create_boleto(uid, "Luz", 120.0, today_tz(), category="moradia")
    inicio = foto(uid)
    for texto in ("gastei 50 no mercado", "dinheiro", "paguei a luz", "dinheiro"):
        manda(uid, texto)
        f, cb = foto(uid), db.get_consolidated_balance(uid)
        assert f["carteira"] + f["bancos"] == cb["consolidated"], texto
        assert f["carteira"] == cb["manual"], texto
    assert inicio["carteira"] - f["carteira"] == D("170"), "a conversa não mexeu na carteira"


def test_outro_usuario_com_os_mesmos_ids_nao_muda_a_foto(uid):
    _cenario_completo(uid)
    antes = foto(uid)
    b = usuario_pagante()
    cb = conexao(b, f"item-b-{b}")  # conexão mais nova que as de A, mesmos ids do provedor
    conta(cb, "acc-brl", "5000")
    posicao(cb, "inv-500", "9999")
    posicao(cb, "inv-pausada", "1")
    conta(cb, "acc-usd", "1", moeda="USD", code="USD")
    conta(cb, "acc-usd-b", "1", moeda="USD", code="USD")
    investimento_manual(b, "CDB manual", "1")
    caixinha(b, "Viagem", "1")
    db.set_balance(b, D("12345"))
    assert foto(uid) == antes


@pytest.mark.parametrize("contas,bancos,moeda", [
    ([("UPDATED", "acc-usd", "USD")], 0, 1),                                # só a conta em dólar
    ([("UPDATED", "acc-1", "BRL")], 50, 0),                                 # real vai para a soma
    ([("PAUSED", "acc-usd", "USD")], 0, 0),                                 # pausada já está fora
    ([("UPDATED", "acc-usd", "USD"), ("PAUSED", "acc-usd", "USD")], 0, 0),  # a mais nova pausou
    ([("UPDATED", "acc-usd", "USD"), ("UPDATED", "acc-usd", "USD")], 0, 1),  # reconectou
])
def test_conta_em_outra_moeda_fica_fora_e_contada(uid, contas, bancos, moeda):
    for i, (status, pid, m) in enumerate(contas):  # conexões em ordem: a última é a mais nova
        conta(conexao(uid, f"item-{i}-{uid}", status=status), pid, "50", moeda=m, code=m)
    f = foto(uid)
    assert (f["bancos"], f["total"]) == (D(bancos), f["carteira"] + D(bancos))
    assert f["base"]["fora"]["moeda"] == moeda
    assert f["motivos"] == ["carteira_nao_confirmada"]


# ── motivos ──────────────────────────────────────────────────────────────────

def _limpo(uid, **kw):
    c = conexao(uid, f"item-{uid}", **kw)
    return c, conta(c, "acc-1", "100")


def test_base_limpa_so_tem_a_carteira_nao_confirmada(uid):
    _limpo(uid, sync=horas_atras(47))
    assert foto(uid)["motivos"] == ["carteira_nao_confirmada"]


def test_sem_banco_nao_ha_motivo_de_banco_nem_de_especie(monkeypatch):
    monkeypatch.delenv("OF_CASH_ENABLED", raising=False)
    assert foto(usuario_pagante())["motivos"] == ["carteira_nao_confirmada"]


def test_especie_incompleta_com_banco_e_o_saque_desligado(uid, monkeypatch):
    _limpo(uid)
    monkeypatch.delenv("OF_CASH_ENABLED")
    assert "especie_incompleta" in foto(uid)["motivos"]


@pytest.mark.parametrize("estado", [
    {"tentativa": horas_atras(0)},          # a última tentativa falhou depois do sync
    {"sync": horas_atras(49)},              # sync velho
    {"sync": None},                         # nunca sincronizou
    {"status": "ERROR"},                    # estado != updated
    {"status": "LOGIN_ERROR"},
])
def test_banco_desatualizado(uid, estado):
    kw = {"sync": horas_atras(1), **estado}
    if "tentativa" in estado:
        kw["sync"] = horas_atras(2)
    _limpo(uid, **kw)
    assert "banco_desatualizado" in foto(uid)["motivos"]


def test_conexao_pausada_nao_desatualiza_nem_entra(uid):
    _limpo(uid, sync=horas_atras(1), tentativa=horas_atras(2))
    conexao(uid, f"item-p-{uid}", status="PAUSED", sync=None)
    assert foto(uid)["motivos"] == ["carteira_nao_confirmada"]


def test_movimento_pendente(uid):
    _limpo(uid)
    q("""insert into bank_movement_declarations (launch_id, user_id, amount, declared_at)
         values (%s, %s, -30, now())""", (lancamento(uid), uid))
    assert "movimentos_pendentes" in foto(uid)["motivos"]


def test_conciliacao_pendente(uid):
    _, brl = _limpo(uid)
    tx_banco(brl, "tx-p", "-30", match_launch_id=lancamento(uid), reconciliation_status="pending")
    assert "conciliacao_pendente" in foto(uid)["motivos"]


@pytest.mark.parametrize("manual,banco,esperado", [
    ("investimento", True, True), ("caixinha", True, True),
    ("investimento", False, False), (None, True, False),
])
def test_manual_e_banco(uid, manual, banco, esperado):
    c, _ = _limpo(uid)
    if manual == "investimento":
        investimento_manual(uid, "CDB", "70")
    elif manual == "caixinha":
        caixinha(uid, "Viagem", "70")
    if banco:
        posicao(c, "inv-1", "500")
    assert ("manual_e_banco" in foto(uid)["motivos"]) is esperado


@pytest.mark.parametrize("status,kw", [
    ("PAUSED", {}), ("DELETED", {}),                       # trial vencido pausa a conexão
    ("UPDATED", {"moeda": "USD", "code": "USD"}), ("UPDATED", {"status": "TOTAL_WITHDRAWAL"}),
])
def test_caixinha_espelhada_com_posicao_fora_vira_motivo(uid, status, kw):
    c = conexao(uid, f"item-{uid}", status=status)
    caixinha(uid, "Caixinha do banco", "500", of_investment_id=posicao(c, "inv-1", "500", **kw))
    f = foto(uid)
    assert f["total"] == f["carteira"] and f["caixinhas"] == f["investimentos_banco"] == 0
    assert "caixinha_espelhada_fora" in f["motivos"]
    assert f["base"]["fora"]["caixinha_espelhada"] == 1


def test_caixinha_espelhada_contada_ou_vazia_nao_vira_motivo(uid):
    velha = conexao(uid, f"item-velho-{uid}")
    antiga = posicao(velha, "inv-1", "400")          # reconectou: a posição vem pela nova
    posicao(conexao(uid, f"item-{uid}"), "inv-1", "500")
    caixinha(uid, "Caixinha do banco", "500", of_investment_id=antiga)
    zerada = posicao(conexao(uid, f"item-p-{uid}", status="PAUSED"), "inv-2", "0")
    caixinha(uid, "Vazia", "0", of_investment_id=zerada)
    f = foto(uid)
    assert f["investimentos_banco"] == D("500") and f["base"]["fora"]["caixinha_espelhada"] == 0
    assert "caixinha_espelhada_fora" not in f["motivos"]


def test_moeda_presumida_na_conta_e_na_posicao(uid):
    c, _ = _limpo(uid)
    posicao(c, "inv-1", "500", code=None)
    assert "moeda_presumida" in foto(uid)["motivos"]
    c2 = conexao(uid, f"item-2-{uid}")
    q("delete from open_finance_investments where connection_id=%s", (c,))
    conta(c2, "acc-2", "10", code=None)
    assert "moeda_presumida" in foto(uid)["motivos"]


_SEM_CHAVE = object()


@pytest.mark.parametrize("saldo,ausente,coluna", [
    (_SEM_CHAVE, True, "0"), (None, True, "0"), ("abc", True, "0"),  # o sync grava 0
    ("NaN", True, "NaN"),               # `_to_decimal` aceita "NaN": a coluna fica NaN
    ("Infinity", True, "Infinity"), ("-Infinity", True, "-Infinity"),
    (0, False, "0"), ("0", False, "0"), (1234.56, False, "1234.56"),
])
def test_saldo_ausente_pelo_sync_da_pluggy(uid, saldo, ausente, coluna):
    c, _ = _limpo(uid)
    raw = {"id": "inv-1", "currencyCode": "BRL"}
    if saldo is not _SEM_CHAVE:
        raw["balance"] = saldo
    db.save_open_finance_investments(c, [normalize_pluggy_investment(raw)])
    f = foto(uid)
    assert ("saldo_ausente" in f["motivos"]) is ausente
    assert f["base"]["posicoes"] == ["inv-1"]
    gravado = q("select balance from open_finance_investments where connection_id=%s",
                (c,))["balance"]
    assert gravado.is_nan() if coluna == "NaN" else gravado == D(coluna)
    inv = gravado if gravado.is_finite() else D(0)  # não finito soma 0, não envenena o total
    assert (f["investimentos_banco"], f["total"]) == (inv, f["carteira"] + D("100") + inv)


@pytest.mark.parametrize("tabela,bancos,inv", [
    ("open_finance_accounts", "0", "500"), ("open_finance_investments", "100", "0")])
@pytest.mark.parametrize("valor", ["NaN", "Infinity", "-Infinity"])
def test_saldo_nao_finito_na_coluna_soma_zero(uid, tabela, bancos, inv, valor):
    c, _ = _limpo(uid)
    posicao(c, "inv-1", "500")  # o `raw` diz 500: só a coluna estragou
    q(f"update {tabela} set balance=%s::numeric where connection_id=%s", (valor, c))
    f = foto(uid)
    assert (f["bancos"], f["investimentos_banco"]) == (D(bancos), D(inv))
    assert f["total"] == f["carteira"] + D(bancos) + D(inv)
    assert "saldo_ausente" in f["motivos"]


def test_saldo_ausente_na_coluna(uid):
    c, _ = _limpo(uid)
    posicao(c, "inv-1", None)
    f = foto(uid)
    assert "saldo_ausente" in f["motivos"] and f["investimentos_banco"] == 0
