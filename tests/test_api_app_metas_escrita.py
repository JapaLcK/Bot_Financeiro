"""Escrita de meta pelo APP (`POST /api/app/metas`, `/editar`, `/apagar`) com Bearer de usuário comum.

A aba Metas do app cria, edita e apaga por estas rotas (decisão E1-A do dono: todo usuário do app, sem a chave
`dashboard_v2_enabled`). O que se prova aqui, pelo monólito real (sessão, banco, CSRF): o Bearer escreve sem cookie
de CSRF; o usuário SEM a chave escreve e lê pelo `/api/app` enquanto o `/api/v2` segue fechado para ele; o corpo
que o app manda (só o diff) é aceito; e a meta de um usuário não aparece nem se edita nem se apaga pelo outro.

Controles (mutação do relato do PR): listar ou apagar sem o `user_id` em `db/metas.py` → vermelho em
`test_outro_usuario_*`; tirar o `dependency_overrides[usuario_atual]` de `api/nativo/app.py` → vermelho em
`test_usuario_sem_a_chave_*` (o app voltaria a exigir a chave do painel).
"""
import pytest
from fastapi.testclient import TestClient

import frontend.finance_bot_websocket_custom as dashboard
from conftest import usuario_pagante
from tests.test_api_app_sessao import cliente, completo

CRIAR = {"nome": "Viagem", "tipo": "meta", "alvo": "1234.50", "fonte": {"tipo": "investido"}}


@pytest.fixture(autouse=True)
def sem_beta(monkeypatch):
    monkeypatch.setenv("DASHBOARD_V2_BETA_EMAILS", "")
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", "")


class App(TestClient):
    """O app manda `credentials: omit`: nenhum cookie vai (o TestClient guardaria o Set-Cookie de CSRF do pai e o reenviaria, e cookie sem header é 403)."""

    def request(self, *args, **kw):
        self.cookies.clear()
        return super().request(*args, **kw)


def app_cliente(uid):
    c = App(dashboard.app)
    c.headers.update(cliente(completo(uid)).headers)
    return c


def metas(c) -> list[dict]:
    r = c.get("/api/app/metas")
    assert r.status_code == 200, r.text
    return r.json()["metas"]


def codigo(r) -> tuple[int, str]:
    return r.status_code, r.json()["error"]["code"]


def test_usuario_sem_a_chave_cria_edita_e_apaga_com_bearer_sem_csrf():
    c = app_cliente(usuario_pagante())
    assert codigo(c.get("/api/v2/metas")) == (404, "dashboard_v2_disabled")  # o painel segue fechado para ele
    r = c.post("/api/app/metas", json=CRIAR)
    assert r.status_code == 200, r.text
    mid = r.json()["id"]
    [m] = metas(c)
    assert (m["id"], m["nome"], m["tipo"], m["alvo"], m["fonte"]["tipo"]) == (mid, "Viagem", "meta", "1234.50", "investido")
    # o app edita SÓ o que mudou
    assert c.post("/api/app/metas/editar", json={"id": mid, "nome": "Japão"}).status_code == 200
    assert (metas(c)[0]["nome"], metas(c)[0]["alvo"]) == ("Japão", "1234.50")
    assert c.post("/api/app/metas/editar", json={"id": mid, "data_alvo": "2099-12-31"}).status_code == 200
    assert metas(c)[0]["data_alvo"] == "2099-12-31"
    assert c.post("/api/app/metas/editar", json={"id": mid, "data_alvo": None}).status_code == 200  # "Tirar prazo"
    assert metas(c)[0]["data_alvo"] is None
    assert c.post("/api/app/metas/apagar", json={"id": mid}).json() == {"id": mid}
    assert metas(c) == []
    assert codigo(c.post("/api/app/metas/apagar", json={"id": mid})) == (404, "meta_nao_encontrada")  # apagar de novo: o app trata como feito


def test_reserva_so_uma_e_o_409_chega_ao_app_com_o_codigo():
    c = app_cliente(usuario_pagante())
    reserva = {"nome": "Reserva de emergência", "tipo": "reserva", "meses": 6, "fonte": {"tipo": "investido"}}
    assert c.post("/api/app/metas", json=reserva).status_code == 200
    assert codigo(c.post("/api/app/metas", json=reserva)) == (409, "reserva_ja_existe")
    assert len([m for m in metas(c) if m["tipo"] == "reserva"]) == 1


def test_o_422_do_app_traz_o_loc_do_campo_e_nao_grava():
    c = app_cliente(usuario_pagante())
    r = c.post("/api/app/metas", json={**CRIAR, "nome": "  "})
    assert (r.status_code, r.json()["error"]["code"]) == (422, "validation_error")
    assert any("nome" in d["loc"] for d in r.json()["error"]["details"])
    assert metas(c) == []


def test_outro_usuario_nao_ve_nem_edita_nem_apaga_a_meta_criada_no_app():
    a, b = app_cliente(usuario_pagante()), app_cliente(usuario_pagante())
    mid = a.post("/api/app/metas", json=CRIAR).json()["id"]
    assert metas(b) == [] and [m["id"] for m in metas(a)] == [mid]
    de_a = b.post("/api/app/metas/editar", json={"id": mid, "nome": "invadida"})
    fantasma = b.post("/api/app/metas/editar", json={"id": 9_000_000_000_000_000_000, "nome": "invadida"})
    assert codigo(de_a) == codigo(fantasma) == (404, "meta_nao_encontrada") and de_a.json() == fantasma.json()
    assert codigo(b.post("/api/app/metas/apagar", json={"id": mid})) == (404, "meta_nao_encontrada")
    assert [m["nome"] for m in metas(a)] == ["Viagem"]
    assert metas(b) == []


@pytest.mark.parametrize("rota,corpo", [("", CRIAR), ("/editar", {"id": 1, "nome": "x"}), ("/apagar", {"id": 1})])
def test_sem_sessao_nao_escreve(rota, corpo):
    r = TestClient(dashboard.app).post("/api/app/metas" + rota, json=corpo, headers={"X-PigBank-Client": "app"})
    assert (r.status_code, r.json()["error"]["code"]) == (401, "unauthenticated")
