"""#657 C3a (PR-8a do grupo D), o topo HTTP: o 500 não tratado grava o dono na coluna `user_id`
de `system_event_logs` (o da SESSÃO validada, nunca o do path), o `source` é o TEMPLATE da rota,
e nem a linha nem o stderr levam a linha final do erro (o `DETAIL` do psycopg, DP11 c).

Rotas reais pelo `TestClient`, sessão real (`_issue_session_token`, com `jti`; um caso no ramo
legado sem `jti`), Postgres real, `build_user_export_zip` e `delete_user_data` reais.
T-H1 monólito, A logado; T-H2 `/api/v2` e `/api/app`; T-H3 o `request.state` não vaza para a
requisição seguinte; T-H4 admin vendo B (path ≠ sessão); T-H5 sem rota; T-H6 o helper nunca
levanta; T-H7 os irmãos `valor_nao_finito_no_banco`.

CONTROLES (CLAUDE.md §3), medidos no PR: `source` de volta a `request.url.path` → T-H1 vermelho;
uid pelo `request.path_params` → T-H4 vermelho; `message = str(exc)` → T-H1 vermelho;
`format_exc()` no `traceback` → T-H1 vermelho; tirar a linha do `resolve_dashboard_user_id` →
T-H1 vermelho; a do `_get_current_user` → T-H1 `auth_me` vermelho; a linha de cada resolvedor
movida para ANTES da validação da sessão → o caso dele em `sessao_nao_validada` vermelho. Positivos: o 500/503 e o
corpo de hoje (`test_error_pages.py`, `test_api_v2_erros.py`), e B intacto na exclusão de A.
"""
from __future__ import annotations

import json
import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

import api.v2.erros as erros
import api.v2.me as v2_me
import core.admin_dashboard as admin
import core.limite_corpo as limite_corpo
import db
import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.shared as shared
from _apoio_auth_app import sessao_de
from _log_dono_helpers import (  # noqa: F401 (banco_real é fixture autouse)
    FONE, _exportados, _isola_e_apaga, _levanta, _linhas, _marca, _marcador, _sem_vazamento, _uid,
    _uma, _violacao, banco_real)
from test_api_app_sessao import completo

EVT = "http_unhandled_exception"
HIST = "GET /history/{user_id}/list"


def _cliente(uid: int, *, legado: bool = False) -> TestClient:
    c = TestClient(dashboard.app, raise_server_exceptions=False)
    token = (dashboard._make_jwt(uid, db.get_auth_user(uid)["email"]) if legado
             else sessao_de(uid)["access"])
    c.headers["Authorization"] = f"Bearer {token}"
    return c


def _linha_do_500(marca: int, source: str, *proibidos: str) -> dict:
    linha = _uma(_linhas(marca, "event_type = %s and source = %s", (EVT, source)), EVT, source)
    assert linha["message"] == "UniqueViolation", linha
    det = linha["details"]
    assert det["exc_type"] == "UniqueViolation" and det["sqlstate"] == "23505", det
    assert 'File "' in det["traceback"], det          # os quadros ficam: o "onde quebrou"
    _sem_vazamento(linha, *proibidos)
    return linha


def _stderr_limpo(err: str, source: str, *proibidos: str) -> None:
    assert f"[unhandled] {source}: UniqueViolation sqlstate=23505" in err, err
    for p in (*proibidos, FONE, "DETAIL"):
        assert p not in err, f"{p!r} no stderr"


# ── T-H1 monólito ────────────────────────────────────────────────────────────

def test_th1_history_de_a_grava_a_coluna_o_template_e_so_os_quadros(monkeypatch, capfd):
    a, b, mark_a, mark_b = _uid(), _uid(), _marcador(), _marcador()
    marca = _marca()
    monkeypatch.setattr(db, "list_history", _levanta(mark_a))
    assert _cliente(a).get(f"/history/{a}/list").status_code == 500
    linha_a = _linha_do_500(marca, HIST, mark_a)
    assert linha_a["user_id"] == a
    _stderr_limpo(capfd.readouterr().err, HIST, mark_a, f"/history/{a}/")

    monkeypatch.setattr(db, "list_history", _levanta(mark_b))
    marca_b = _marca()
    assert _cliente(b).get(f"/history/{b}/list").status_code == 500
    linha_b = _linha_do_500(marca_b, HIST, mark_b)
    assert linha_b["user_id"] == b

    bruto, ids = _exportados(a)
    evento = next(e for e in json.loads(bruto)["dados"]["eventos_sistema"] if e["id"] == linha_a["id"])
    assert "traceback" not in evento["details"] and evento["details"]["sqlstate"] == "23505", evento  # A9
    # pelo efeito: `str` e não `json.dumps`, que escaparia as aspas e passaria com a pilha dentro
    assert 'File "' not in str(evento), evento
    _isola_e_apaga(a, b, {linha_a["id"]}, linha_b["id"], marca)


def test_th1_auth_me_pelo_get_current_user_e_o_ramo_legado(monkeypatch):
    """`/auth/me` resolve pelo `_get_current_user` (o outro resolvedor), com `jti` e sem."""
    a = _uid()
    for legado in (False, True):
        mark = _marcador()
        monkeypatch.setattr(db, "should_show_mfa_onboarding", _levanta(mark))
        marca = _marca()
        assert _cliente(a, legado=legado).get("/auth/me").status_code == 500
        assert _linha_do_500(marca, "GET /auth/me", mark)["user_id"] == a, legado


@pytest.mark.parametrize("modulo, path, source", [
    (shared, "/history/{}/list", HIST), (dashboard, "/auth/me", "GET /auth/me"),
], ids=["resolve_dashboard_user_id", "get_current_user"])
def test_th1_sessao_nao_validada_deixa_a_coluna_nula(monkeypatch, modulo, path, source):
    """O uid só entra no `request.state` DEPOIS da sessão validada: `_resolve_page_user_id` e o
    `quiz_signup` engolem o 401 e seguem, e gravariam o uid de uma sessão revogada."""
    a, mark = _uid(), _marcador()
    c = _cliente(a)
    monkeypatch.setattr(modulo, "get_active_session", _levanta(mark))
    marca = _marca()
    assert c.get(path.format(a)).status_code == 500
    assert _linha_do_500(marca, source, mark)["user_id"] is None


def test_th1_ramo_legado_do_dashboard_token(monkeypatch):
    a, mark = _uid(), _marcador()
    monkeypatch.setattr(db, "list_history", _levanta(mark))
    c = TestClient(dashboard.app, raise_server_exceptions=False)
    c.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, dashboard.make_dashboard_token(a, hours=1))  # sem jti
    marca = _marca()
    assert c.get(f"/history/{a}/list").status_code == 500
    assert _linha_do_500(marca, HIST, mark)["user_id"] == a


# ── T-H2 sub-apps ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("prefixo", ["/api/v2", "/api/app"])
def test_th2_sub_app_template_com_o_prefixo_e_o_dono_da_sessao(monkeypatch, capfd, prefixo):
    b, mark = completo(_uid()), _marcador()
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", str(b))
    monkeypatch.setattr(v2_me, "get_plan_tier", _levanta(mark))
    marca = _marca()
    r = _cliente(b).get(f"{prefixo}/me")
    assert r.status_code == 500 and r.json()["error"]["code"] == "internal_error", r.text
    source = f"GET {prefixo}/me"
    assert _linha_do_500(marca, source, mark)["user_id"] == b
    _stderr_limpo(capfd.readouterr().err, source, mark)


# ── T-H3 o estado é por requisição ───────────────────────────────────────────

@pytest.fixture
def rota_anonima():
    path = f"/__th3-{uuid.uuid4().hex}"
    mark = _marcador()

    @dashboard.app.get(path)
    async def _quebra():
        raise _violacao(mark)

    yield path, mark
    dashboard.app.router.routes = [r for r in dashboard.app.router.routes if getattr(r, "path", None) != path]


def test_th3_a_quebra_e_a_anonima_seguinte_nao_herda_o_dono(monkeypatch, rota_anonima):
    a, mark_a = _uid(), _marcador()
    path, mark = rota_anonima
    monkeypatch.setattr(db, "list_history", _levanta(mark_a))
    c = _cliente(a)
    marca = _marca()
    assert c.get(f"/history/{a}/list").status_code == 500
    assert c.get(path).status_code == 500                       # mesmo cliente, ainda com o Bearer de A
    assert _linha_do_500(marca, HIST, mark_a)["user_id"] == a
    assert _linha_do_500(marca, f"GET {path}", mark)["user_id"] is None


# ── T-H4 admin vendo B: a coluna é da sessão, não do path ────────────────────

def test_th4_admin_em_users_b_nao_poe_a_linha_na_conta_de_b(monkeypatch):
    b, mark = _uid(), _marcador()

    async def _admin(*a, **k):
        return "admin"

    async def _quebra(*a, **k):
        raise _violacao(mark)

    monkeypatch.setattr(admin, "get_current_admin", _admin)
    monkeypatch.setattr(admin, "fetch_admin_user_detail", _quebra)
    marca = _marca()
    assert TestClient(dashboard.app, raise_server_exceptions=False).get(f"/admin/api/users/{b}").status_code == 500
    assert _linha_do_500(marca, "GET /admin/api/users/{user_id}", mark)["user_id"] is None
    assert not _linhas(marca, "user_id = %s", (b,))
    assert not any(e["event_type"] == EVT for e in json.loads(_exportados(b)[0])["dados"]["eventos_sistema"])


# ── T-H5 sem rota ────────────────────────────────────────────────────────────

def test_th5_excecao_antes_do_roteamento_vira_sem_rota(monkeypatch):
    mark = _marcador()
    monkeypatch.setattr(limite_corpo, "limites_da_rota", _levanta(mark))
    marca = _marca()
    r = TestClient(dashboard.app, raise_server_exceptions=False).get("/__qualquer")
    assert r.status_code == 500 and r.json() == {"error": "Erro interno do servidor."}
    assert _linha_do_500(marca, "GET <sem rota>", mark)["user_id"] is None


# ── T-H6 o helper nunca levanta ──────────────────────────────────────────────

class _ScopeQuebrado(dict):
    def get(self, *a):
        raise RuntimeError("scope quebrado")


@pytest.mark.parametrize("scope, esperado", [
    ({"type": "http", "method": "POST"}, ("POST <sem rota>", None)),
    ({"type": "http", "method": "GET", "route": object(), "state": {"session_user_id": 7}}, ("GET <sem rota>", 7)),
    (_ScopeQuebrado(type="http"), ("<sem rota>", None)),
], ids=["sem_route_sem_state", "route_sem_path", "scope_que_levanta"])
def test_th6_origem_do_erro_nunca_levanta(scope, esperado):
    assert admin.origem_do_erro(Request(scope)) == esperado


# ── T-H7 irmãos `valor_nao_finito_no_banco` ──────────────────────────────────

@pytest.mark.parametrize("rota", ["monolito", "v2"])
def test_th7_valor_nao_finito_template_e_dono_da_sessao(monkeypatch, rota):
    a = completo(_uid())
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", str(a))
    monkeypatch.setattr(dashboard, "violacao_finito", lambda e: "finito_valor")
    monkeypatch.setattr(erros, "violacao_finito", lambda e: "finito_valor")

    def _check(*a, **k):
        raise psycopg.errors.CheckViolation("Failing row contains (secreto)")

    monkeypatch.setattr(db, "list_history", _check)
    monkeypatch.setattr(v2_me, "get_plan_tier", _check)
    path, source = ((f"/history/{a}/list", HIST) if rota == "monolito" else ("/api/v2/me", "GET /api/v2/me"))
    marca = _marca()
    assert _cliente(a).get(path).status_code == 400
    linha = _uma(_linhas(marca, "event_type = 'valor_nao_finito_no_banco'"), "valor_nao_finito_no_banco", source)
    assert linha["user_id"] == a and "secreto" not in json.dumps(linha), linha
