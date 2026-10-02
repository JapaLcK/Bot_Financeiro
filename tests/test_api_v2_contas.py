"""`GET /api/v2/contas` (`db/contas_hoje.py`) pelo monólito real: sessão, banco real.

Um caso por estado da conta, e em todos a paridade com a foto do patrimônio
(`total == calcular().carteira + calcular().bancos`) e com o saldo consolidado —
salvo as divergências declaradas, afirmadas aqui de forma explícita: USD novo ×
BRL velho (o `BANK_ACCOUNTS_SQL` filtra a moeda antes do `distinct on`, ver
`CONTAS_BANCO_SQL` em `db/patrimonio.py`) e saldo não finito na coluna (o `sum`
do consolidado vira NaN/±Inf; a foto e este bloco somam 0).
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

import db
import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import sessao_de
from api.v2.contas import Contas
from conftest import usuario_pagante
from core.services.pluggy_sync import normalize_pluggy_account
from tests._patrimonio_helpers import (AGORA, conexao, conta, foto, horas_atras, lancamento, q,
                                       tx_banco)

D = Decimal
CONTAS = "/api/v2/contas"
CAMPOS = {"id", "instituicao", "nome", "saldo", "moeda", "no_total", "conexao",
          "sincronizado_em", "motivos"}


@pytest.fixture
def uid(monkeypatch):
    monkeypatch.setenv("OF_CASH_ENABLED", "1")
    monkeypatch.setenv("DASHBOARD_V2_BETA_EMAILS", "")
    u = usuario_pagante()
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", str(u))
    db.set_balance(u, D("100"))
    return u


def get(quem, rota=CONTAS, **params):
    client = TestClient(dashboard.app)
    client.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, sessao_de(quem)["dashboard"])
    r = client.get(rota, params=params)
    assert r.status_code == 200, r.text
    return r


def _cartao(cid):
    q("""insert into open_finance_accounts (connection_id, provider_account_id, name, type,
             currency, balance, raw, updated_at)
         values (%s, 'card-1', 'Cartão', 'CREDIT', 'BRL', 900, %s, %s)""",
      (cid, Jsonb({"currencyCode": "BRL", "balance": 900}), AGORA))


def _sync(cid, *contas):
    db.save_open_finance_sync(cid, [normalize_pluggy_account(
        {"type": "BANK", "currencyCode": "BRL", **c}) for c in contas])


def _coluna(uid, valor):
    c = conexao(uid, f"item-{uid}")
    conta(c, "acc-1", "100")
    q("update open_finance_accounts set balance=%s::numeric where connection_id=%s", (valor, c))


def _fusao(uid):
    brl = conta(conexao(uid, f"item-{uid}"), "acc-1", "1000")
    tx_banco(brl, "tx-fundida", "-30", imported_launch_id=lancamento(uid, 30))


def _conciliacao(uid):
    brl = conta(conexao(uid, f"item-{uid}"), "acc-1", "1000")
    tx_banco(brl, "tx-p", "-30", match_launch_id=lancamento(uid), reconciliation_status="pending")


def _omitida(uid):
    c = conexao(uid, f"item-{uid}")
    _sync(c, {"id": "a-1", "balance": 10}, {"id": "a-2", "balance": 10})
    _sync(c, {"id": "a-1", "balance": 10})  # segundos depois, sem a a-2


def _duas(uid, *linhas):
    """Mesma conta em conexões em ordem: a última é a mais nova."""
    for i, (status, saldo, moeda) in enumerate(linhas):
        conta(conexao(uid, f"item-{i}-{uid}", status=status), "acc-1", saldo, moeda=moeda, code=moeda)


NAO_FINITO = "não finito"  # o consolidado diverge de propósito (docstring)

# id: (montar, total, carteira, motivos da carteira, [(saldo, no_total, motivos)],
#      consolidado: None = igual ao total; senão o valor divergente declarado)
CASOS = {
    "fresca": (lambda u: conta(conexao(u, f"item-{u}"), "acc-1", "1000"),
               "1100", "100", [], [("1000", True, [])], None),
    "reconexao_conta_uma_vez": (lambda u: _duas(u, ("UPDATED", "777", "BRL"), ("UPDATED", "1000", "BRL")),
                                "1100", "100", [], [("1000", True, [])], None),
    "outra_moeda": (lambda u: conta(conexao(u, f"item-{u}"), "acc-1", "50", moeda="USD", code="USD"),
                    "100", "100", [], [("50", False, ["outra_moeda"])], None),
    "usd_novo_brl_velho": (lambda u: _duas(u, ("UPDATED", "50", "BRL"), ("UPDATED", "50", "USD")),
                           "100", "100", [], [("50", False, ["outra_moeda"])], "150"),
    "conexao_pausada": (lambda u: conta(conexao(u, f"item-{u}", status="PAUSED"), "acc-1", "500"),
                        "100", "100", [], [(None, False, ["conexao_pausada"])], None),
    "reconexao_mais_nova_pausada": (lambda u: _duas(u, ("UPDATED", "500", "BRL"), ("DELETED", "500", "BRL")),
                                    "100", "100", [], [(None, False, ["conexao_pausada"])], None),
    "saldo_ausente_no_sync": (lambda u: _sync(conexao(u, f"item-{u}"), {"id": "acc-1"}),
                              "100", "100", [], [(None, False, ["saldo_ausente"])], None),
    "saldo_ilegivel_no_sync": (lambda u: _sync(conexao(u, f"item-{u}"), {"id": "acc-1", "balance": "abc"}),
                               "100", "100", [], [(None, False, ["saldo_ausente"])], None),
    "nan": (lambda u: _coluna(u, "NaN"), "100", "100", [], [(None, False, ["saldo_ausente"])], NAO_FINITO),
    "infinity": (lambda u: _coluna(u, "Infinity"), "100", "100", [],
                 [(None, False, ["saldo_ausente"])], NAO_FINITO),
    "menos_infinity": (lambda u: _coluna(u, "-Infinity"), "100", "100", [],
                       [(None, False, ["saldo_ausente"])], NAO_FINITO),
    "omitida_do_ultimo_sync": (_omitida, "120", "100", [],
                               [("10", True, []), ("10", True, ["conta_fora_do_ultimo_sync"])], None),
    "sync_pela_metade": (lambda u: conta(conexao(u, f"item-{u}", sync=horas_atras(2),
                                                 tentativa=horas_atras(0)), "acc-1", "1000"),
                         "1100", "100", [], [("1000", True, ["banco_desatualizado"])], None),
    "sync_velho_49h": (lambda u: conta(conexao(u, f"item-{u}", sync=horas_atras(49)), "acc-1", "1000"),
                       "1100", "100", [], [("1000", True, ["banco_desatualizado"])], None),
    "nunca_sincronizada": (lambda u: conta(conexao(u, f"item-{u}", sync=None), "acc-1", "1000"),
                           "1100", "100", [], [("1000", True, ["banco_desatualizado"])], None),
    "moeda_presumida": (lambda u: conta(conexao(u, f"item-{u}"), "acc-1", "10", code=None),
                        "110", "100", [], [("10", True, ["moeda_presumida"])], None),
    "cartao_fora": (lambda u: _cartao(conexao(u, f"item-{u}")), "100", "100", [], [], None),
    "sem_banco": (lambda u: None, "100", "100", [], [], None),
    "carteira_com_fusao": (_fusao, "1100", "100", [], [("1000", True, [])], None),
    "conciliacao_pendente": (_conciliacao, "1070", "70", ["conciliacao_pendente"],
                             [("1000", True, [])], None),
}


@pytest.mark.parametrize("caso", CASOS)
def test_um_caso_por_estado_com_paridade(uid, caso):
    montar, total, carteira, mot_carteira, esperadas, consolidado = CASOS[caso]
    montar(uid)
    r = get(uid)
    corpo = Contas.model_validate(r.json())
    assert corpo.carteira.saldo == D(carteira)
    assert corpo.carteira.motivos == ["carteira_nao_confirmada", *mot_carteira]
    assert [(c.saldo, c.no_total, c.motivos) for c in corpo.contas] == [
        (None if s is None else D(s), n, m) for s, n, m in esperadas]
    assert corpo.total == D(total)
    assert corpo.fora_do_total == sum(not n for _, n, _ in esperadas)
    vistos = set(corpo.carteira.motivos).union(*(c.motivos for c in corpo.contas))
    assert set(corpo.motivos) == vistos and len(corpo.motivos) == len(vistos)
    # Dinheiro sai como texto decimal; nada do provedor sai.
    assert isinstance(r.json()["total"], str)
    assert all(set(c) == CAMPOS for c in r.json()["contas"])
    assert "acc-1" not in r.text and "a-2" not in r.text and "raw" not in r.text

    f = foto(uid)
    assert corpo.total == f["carteira"] + f["bancos"]
    cb = db.get_consolidated_balance(uid)["consolidated"]
    if consolidado is None:
        assert cb == corpo.total
    elif consolidado == NAO_FINITO:
        assert not cb.is_finite(), cb
    else:
        assert cb == D(consolidado) != corpo.total


def test_conta_fresca_traz_instituicao_estado_e_data(uid):
    conta(conexao(uid, f"item-{uid}"), "acc-1", "1000")
    c = get(uid).json()["contas"][0]
    assert (c["instituicao"], c["nome"], c["moeda"], c["conexao"]) == ("Banco", "Conta", "BRL", "updated")
    assert c["sincronizado_em"] is not None and c["saldo"] == "1000"


def test_conta_pausada_traz_o_estado_da_conexao(uid):
    conta(conexao(uid, f"item-{uid}", status="PAUSED"), "acc-1", "500")
    assert get(uid).json()["contas"][0]["conexao"] == "paused"


def test_especie_incompleta_na_carteira_com_banco_e_o_saque_desligado(uid, monkeypatch):
    conta(conexao(uid, f"item-{uid}"), "acc-1", "1000")
    monkeypatch.delenv("OF_CASH_ENABLED")
    assert "especie_incompleta" in get(uid).json()["carteira"]["motivos"]


def test_movimento_pendente_na_carteira(uid):
    conta(conexao(uid, f"item-{uid}"), "acc-1", "1000")
    q("""insert into bank_movement_declarations (launch_id, user_id, amount, declared_at)
         values (%s, %s, -30, now())""", (lancamento(uid), uid))
    assert "movimentos_pendentes" in get(uid).json()["carteira"]["motivos"]


# ── Isolamento ────────────────────────────────────────────────────────────────

def test_b_nao_ve_nada_de_a_nem_com_o_mesmo_id_do_provedor(uid, monkeypatch):
    a = uid
    id_a = conta(conexao(a, f"item-a-{a}"), "acc-x", "1000")
    b = usuario_pagante()
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", f"{a},{b}")
    db.set_balance(b, D("7"))
    id_b = conta(conexao(b, f"item-b-{b}"), "acc-x", "5000")  # conexão mais nova, mesmo id

    de_b = get(b, user_id=a, uid=a).json()
    assert ([c["id"] for c in de_b["contas"]], de_b["total"]) == ([id_b], "5007")
    de_a = get(a).json()  # controle positivo: A vê o dele
    assert ([c["id"] for c in de_a["contas"]], de_a["total"]) == ([id_a], "1100")
