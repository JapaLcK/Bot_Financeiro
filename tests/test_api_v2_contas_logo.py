"""`logo_proprio` e `logo_url` por conta em `GET /api/v2/contas` (o card `contas` do chat desenha o bloco, sem outra consulta).

A regra é UMA só, `db.open_finance_state.logos_da_conexao`, a mesma do snapshot do Open Finance (`tests/test_of_logo_proprio.py`).
O servidor só ACRESCENTA os dois campos; precedência no cliente: próprio > `logo_url` da Pluggy > inicial.

CONTROLES (mutação, medida em 2026-10-21; remeça se mexer):
  negativos:
    `contas_hoje` sem os dois campos no dict da conta        -> test_conector_proprio_*, test_conector_fora_*, test_catalogo_*
    `logos_da_conexao` sem a checagem `provider == "pluggy"`  -> test_mock_pluggy_nao_tem_logo
    `logos_da_conexao` sem `logo_url_valida` no raw           -> test_url_que_nao_e_https_e_descartada
    SQL_LOGO_URL fora do select de `ler_conexoes`            -> test_conector_proprio_*, test_url_que_nao_e_https_e_descartada
  positivos: a conta sem logo (institution_id "1") devolve os dois null, a Carteira segue sem logo, B só vê o dele.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from psycopg.types.json import Jsonb

import core.services.pluggy_catalogo as cat
from conftest import usuario_pagante
from tests._patrimonio_helpers import conexao, conta, q
from tests.test_api_v2_contas import get, uid  # noqa: F401  (a fixture `uid` é a das contas)

L612 = "https://cdn.pluggy.ai/assets/connector-icons/612.svg"
L775 = "https://cdn.pluggy.ai/assets/connector-icons/775.svg"
RAIZ = Path(__file__).parent.parent


def _conta(quem, institution_id, imagem=None, item="item", provider="pluggy"):
    cid = conexao(quem, f"{item}-{quem}")
    raw = {"connector": {"imageUrl": imagem}} if imagem is not None else {}
    q("update open_finance_connections set institution_id=%s, provider=%s, raw=%s where id=%s",
      (institution_id, provider, Jsonb(raw), cid))
    conta(cid, f"acc-{item}-{quem}", "10")


def _logos(quem):
    return [(c["logo_proprio"], c["logo_url"]) for c in get(quem).json()["contas"]]


def test_conector_proprio_traz_o_arquivo_e_o_logo_da_pluggy(uid):
    _conta(uid, "612", L612)
    assert _logos(uid) == [("/brand/bancos/18236120.png", L612)]


def test_conector_fora_da_tabela_so_traz_o_logo_da_pluggy(uid):
    _conta(uid, "775", L775)
    assert _logos(uid) == [(None, L775)]


def test_sem_logo_nenhum_e_null_e_a_conta_segue_na_lista(uid):
    _conta(uid, "1")
    assert _logos(uid) == [(None, None)]


def test_catalogo_empresta_o_logo_quando_o_raw_nao_tem(uid, monkeypatch):
    monkeypatch.setattr(cat, "_CONNECTORS_CACHE", {False: {"ts": 0.0, "data": [{"id": "775", "imageUrl": L775}]}})
    _conta(uid, "775")
    assert _logos(uid) == [(None, L775)]


@pytest.mark.parametrize("ruim", ["http://cdn.pluggy.ai/a.svg", "javascript:alert(1)", "https://x.com/a b.svg", 7, {"a": 1}])
def test_url_que_nao_e_https_e_descartada(uid, ruim):
    _conta(uid, "775", ruim)
    assert _logos(uid) == [(None, None)]


def test_mock_pluggy_nao_tem_logo(uid):
    """O id do `mock_pluggy` é de outro mundo: mesmo `institution_id` do mapa (612), sem arquivo próprio."""
    _conta(uid, "612", provider="mock_pluggy")
    assert _logos(uid) == [(None, None)]


def test_carteira_e_conta_manual_nao_tem_logo(uid):
    _conta(uid, "612", L612)
    assert set(get(uid).json()["carteira"]) == {"saldo", "motivos"}


def test_b_so_ve_o_logo_da_conta_dele(uid, monkeypatch):
    a, b = uid, usuario_pagante()
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", f"{a},{b}")
    _conta(a, "612", L612, item="a")
    _conta(b, "775", L775, item="b")
    assert _logos(b) == [(None, L775)]
    assert _logos(a) == [("/brand/bancos/18236120.png", L612)]  # positivo: A vê o dele


def test_logo_das_fixtures_do_navegador_e_arquivo_real_do_servidor():
    """As fixtures que o harness serve (e as capturas) só citam arquivos que o servidor entrega e a regra do cliente aceita."""
    f = json.loads((RAIZ / "tests/frontend/api_v2_respostas.json").read_text(encoding="utf-8"))["contas"]
    proprios = {c["logo_proprio"] for v in f.values() for c in v["contas"]} - {None}
    assert proprios and proprios <= {cat.logo_proprio(k) for k in cat._LOGO_PROPRIO_ISPB}
    assert all((RAIZ / "frontend" / p.removeprefix("/")).is_file() for p in proprios)


def test_card_contas_manda_o_logo_ao_cliente_e_nao_ao_modelo(uid):
    """O bloco do chat leva `logo_proprio`/`logo_url` (o cliente desenha o ladrilho); a visão do modelo não leva URL nenhuma."""
    from core.services.ai_chat import cards
    _conta(uid, "612", L612)
    card = cards.registro()["contas"]
    ((_, objs),) = card.ler(uid, {})
    (c,) = cards._fio(objs["contas"])["contas"]
    assert (c["logo_proprio"], c["logo_url"]) == ("/brand/bancos/18236120.png", L612)  # positivo: o bloco segue com os dois
    (v,) = card.visao({"contas": cards._py(objs["contas"])})["contas"]
    assert not {"logo_proprio", "logo_url"} & v.keys()
    assert v.keys() == c.keys() - {"logo_proprio", "logo_url"} and v["saldo"] == 10.0  # positivo: o resto da conta fica (dinheiro como número)
