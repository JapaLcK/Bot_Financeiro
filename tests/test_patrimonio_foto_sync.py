"""`conta_fora_do_ultimo_sync` (`db/patrimonio.calcular`), pelos saves REAIS do sync.

Conta ou posição que deixou de vir no /accounts ou /investments fica no espelho com
o saldo velho: soma, e o motivo marca a dúvida. Critério: `updated_at` abaixo do
máximo da mesma conexão na mesma tabela (cada save carimba a chamada com um `now`).
"""
from __future__ import annotations

from decimal import Decimal

import pytest

import db
from conftest import usuario_pagante
from core.services.pluggy_sync import normalize_pluggy_account, normalize_pluggy_investment
from tests._patrimonio_helpers import conexao, foto, q

D = Decimal
MOTIVO = "conta_fora_do_ultimo_sync"


@pytest.fixture
def uid(monkeypatch):
    monkeypatch.setenv("OF_CASH_ENABLED", "1")
    return usuario_pagante()


def _sync(cid, tipo, ids, **kw):
    if tipo == "conta":
        db.save_open_finance_sync(cid, [normalize_pluggy_account(
            {"id": i, "type": "BANK", "currencyCode": "BRL", "balance": 10}) for i in ids])
    else:
        db.save_open_finance_investments(cid, [normalize_pluggy_investment(
            {"id": i, "currencyCode": "BRL", "balance": 10}) for i in ids], **kw)


def _soma(f, tipo):
    return f["bancos"] if tipo == "conta" else f["investimentos_banco"]


TIPOS = pytest.mark.parametrize("tipo", ["conta", "posicao"])


@TIPOS
@pytest.mark.parametrize("syncs,motivo,soma", [
    ([["a-1", "a-2"], ["a-1"]], True, 20),          # o 2º, segundos depois, omitiu a a-2
    ([["a-1", "a-2"], ["a-1", "a-2"]], False, 20),
    ([["a-1", "a-2"]], False, 20),
    ([["a-1"], ["a-1", "a-2"], ["a-1"], ["a-1", "a-2"]], False, 20),  # voltou
    # Limite declarado: o último sync omitiu TODAS; o máximo segue sendo o do 1º.
    ([["a-1", "a-2"], []], False, 20),
])
def test_syncs_em_sequencia(uid, tipo, syncs, motivo, soma):
    c = conexao(uid, f"item-{uid}")
    for ids in syncs:
        _sync(c, tipo, ids)
    f = foto(uid)
    assert (MOTIVO in f["motivos"]) is motivo
    assert _soma(f, tipo) == D(soma)  # a omitida segue na soma


def test_leitura_completa_poda_a_posicao_e_nao_sobra_motivo(uid):
    c = conexao(uid, f"item-{uid}")
    _sync(c, "posicao", ["a-1", "a-2"])
    _sync(c, "posicao", ["a-1"], leitura_completa=True)
    f = foto(uid)
    assert MOTIVO not in f["motivos"] and f["investimentos_banco"] == D(10)


@TIPOS
def test_conexao_mais_nova_nao_envelhece_a_outra(uid, tipo):
    _sync(conexao(uid, f"item-x-{uid}"), tipo, ["a-1"])
    _sync(conexao(uid, f"item-y-{uid}"), tipo, ["b-1"])  # mais tarde, outra conexão
    f = foto(uid)
    assert MOTIVO not in f["motivos"] and _soma(f, tipo) == D(20)


def test_posicao_sem_updated_at_nao_liga(uid):
    c = conexao(uid, f"item-{uid}")
    _sync(c, "posicao", ["a-1", "a-2"])
    q("""update open_finance_investments set updated_at=null
          where connection_id=%s and provider_investment_id='a-1'""", (c,))
    assert MOTIVO not in foto(uid)["motivos"]


@TIPOS
def test_nao_cruza_usuario(uid, tipo):
    _sync(conexao(uid, f"item-{uid}"), tipo, ["a-1", "a-2"])
    antes = foto(uid)
    b = usuario_pagante()
    cb = conexao(b, f"item-b-{b}")  # mesmos ids do provedor, sync mais novo, omissão
    _sync(cb, tipo, ["a-1", "a-2"])
    _sync(cb, tipo, ["a-1"])
    assert MOTIVO in foto(b)["motivos"]
    assert MOTIVO not in antes["motivos"] and foto(uid) == antes
