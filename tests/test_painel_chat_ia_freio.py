"""O freio do chat com a IA no /painel: `PAINEL_CHAT_IA_USER_IDS` (lista de user_id, lida a cada pedido).
Ausente, vazia ou sem id válido = ninguém. Chega ao cliente em `GET /api/v2/me` (`chat_ia`) e vira o passo
`piggy.pergunta` do guia (`dado: real`, texto sem "de exemplo")."""
from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import sessao_de
from api.v2.guia import PASSOS, Guia
from conftest import usuario_pagante
from core.services.plan_service import painel_chat_ia

ENV = "PAINEL_CHAT_IA_USER_IDS"


@pytest.mark.parametrize("valor", [None, "", "  ", ",", "abc", "1.5", "-7", "٧", "outro@x.com", pytest.param("9" * 5000, id="digitos-demais")])
def test_sem_id_valido_ninguem(monkeypatch, valor):
    monkeypatch.delenv(ENV, raising=False) if valor is None else monkeypatch.setenv(ENV, valor)
    assert painel_chat_ia(7) is False


def test_listado_sim_e_outro_nao(monkeypatch):
    monkeypatch.setenv(ENV, "3, 7 ,x,0012")
    assert [painel_chat_ia(i) for i in (3, 7, 12, 8, 70)] == [True, True, True, False, False]


def test_lido_a_cada_pedido_sem_reiniciar(monkeypatch):
    monkeypatch.setenv(ENV, "7")
    assert painel_chat_ia(7) is True
    monkeypatch.setenv(ENV, "8")
    assert painel_chat_ia(7) is False
    monkeypatch.delenv(ENV)
    assert painel_chat_ia(8) is False


def test_token_ruim_avisa_sem_vazar_a_env(monkeypatch, caplog):
    monkeypatch.setenv(ENV, "7,segredo-que-nao-pode-vazar")
    with caplog.at_level(logging.WARNING):
        assert painel_chat_ia(7) is True
    assert "1 token" in caplog.text and "segredo" not in caplog.text


def test_id_de_18_digitos_vale_e_de_19_nao(monkeypatch):
    monkeypatch.setenv(ENV, f"{'9' * 18},{'8' * 19}")
    assert painel_chat_ia(int("9" * 18)) is True and painel_chat_ia(int("8" * 19)) is False


def test_aviso_uma_vez_por_valor_da_env(monkeypatch, caplog):
    avisos = lambda: [r for r in caplog.records if "PAINEL_CHAT_IA" in r.getMessage()]  # noqa: E731
    with caplog.at_level(logging.WARNING):
        monkeypatch.setenv(ENV, "7,lixo-aviso-unico")
        for _ in range(3):
            painel_chat_ia(7)
        assert len(avisos()) == 1
        monkeypatch.setenv(ENV, "7,outro-lixo-aviso-unico")  # valor novo avisa de novo
        painel_chat_ia(7)
        assert len(avisos()) == 2
        monkeypatch.setenv(ENV, "7")  # corrigida e estragada de novo: avisa outra vez
        painel_chat_ia(7)
        monkeypatch.setenv(ENV, "7,outro-lixo-aviso-unico")
        painel_chat_ia(7)
        assert len(avisos()) == 3
    assert "lixo" not in caplog.text


@pytest.fixture
def uid(monkeypatch):
    u = usuario_pagante()
    monkeypatch.setenv("DASHBOARD_V2_BETA_EMAILS", "")
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", str(u))
    return u


def _get(quem, url):
    c = TestClient(dashboard.app)
    c.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, sessao_de(quem)["dashboard"])
    r = c.get(url)
    assert r.status_code == 200, r.text
    return r.json()


def test_me_manda_chat_ia_pelo_usuario_da_sessao(uid, monkeypatch):
    assert _get(uid, "/api/v2/me")["chat_ia"] is False
    monkeypatch.setenv(ENV, str(uid))
    assert _get(uid, "/api/v2/me")["chat_ia"] is True
    monkeypatch.setenv(ENV, str(uid + 1))
    assert _get(uid, "/api/v2/me")["chat_ia"] is False


PIGGY = next(p for p in PASSOS if p["id"] == "piggy.pergunta")


def _passo_piggy(quem):
    g = Guia.model_validate(_get(quem, "/api/v2/guia")).model_dump()
    return next(p for p in g["passos"] if p["id"] == "piggy.pergunta")


def test_guia_passo_piggy_real_so_com_o_freio_ligado(uid, monkeypatch):
    antes = _passo_piggy(uid)
    assert antes["dado"] == "exemplo" and "exemplo" in antes["fala"]["apresenta"]  # controle: sem o freio, o de sempre
    assert antes["fala"] == PIGGY["fala"]
    monkeypatch.setenv(ENV, str(uid))
    real = _passo_piggy(uid)
    assert real["dado"] == "real" and "exemplo" not in real["fala"]["apresenta"].lower()
    assert (real["fala"]["titulo"], real["fala"]["texto"]) == (PIGGY["fala"]["titulo"], PIGGY["fala"]["texto"])
    outros = Guia.model_validate(_get(uid, "/api/v2/guia")).model_dump()["passos"]
    outros = [p for p in outros if p["id"] != PIGGY["id"]]
    assert outros and all(p["dado"] == "real" for p in outros)  # os outros passos não mudam


def test_todos_abre_para_qualquer_um_e_desligar_fecha(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    monkeypatch.setenv("PAINEL_CHAT_IA_TODOS", "1")
    assert painel_chat_ia(7) is True
    monkeypatch.setenv("PAINEL_CHAT_IA_TODOS", "0")
    assert painel_chat_ia(7) is False
    monkeypatch.setenv(ENV, "7")  # a lista segue valendo com a env desligada
    assert painel_chat_ia(7) is True and painel_chat_ia(8) is False
