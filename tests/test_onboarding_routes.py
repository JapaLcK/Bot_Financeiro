"""Wizard de primeira configuração: rotas, compatibilidade com o app iOS,
migration e o endpoint de progresso.

O teste mais importante deste arquivo é o do 302 de `/onboarding?token=`.
`mobile/ios/App/App/AppDelegate.swift:177` monta `<site>/onboarding?token=T` em
Swift COMPILADO ao tratar o deeplink do login com Google. Todo app já instalado
constrói essa URL sozinho e só um build novo na App Store mudaria — o que nunca
alcança 100% da base. Sem o redirect, o cadastro por Google quebra em toda a
base instalada, e nenhum teste de navegador pegaria isso.
"""

import pathlib
import re

import pytest
from fastapi.testclient import TestClient

import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.onboarding as onboarding_routes
from frontend.routes.shared import FRONTEND_DIR

client = TestClient(dashboard.app)

REPO = pathlib.Path(__file__).resolve().parents[1]


def _csrf(c: TestClient) -> dict[str, str]:
    """Cookie + header de CSRF, como o csrf_middleware exige de todo método
    não-seguro. Mesma convenção de tests/test_auth_cookie.py."""
    token = "test-csrf-token"
    c.cookies.set(dashboard.CSRF_COOKIE_NAME, token)
    return {dashboard.CSRF_HEADER_NAME: token}


# ── Compatibilidade com o app iOS já instalado ───────────────────────────────

def test_onboarding_com_token_redireciona_pro_completar_cadastro():
    resp = client.get("/onboarding", params={"token": "abc123"}, follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/completar-cadastro?token=abc123"


def test_redirect_preserva_token_com_caractere_especial():
    resp = client.get("/onboarding", params={"token": "a b/c+d"}, follow_redirects=False)
    assert resp.status_code == 302
    loc = resp.headers["location"]
    assert loc.startswith("/completar-cadastro?token=")
    # Nada de barra ou espaço crus vazando pro query string.
    assert " " not in loc and "/c" not in loc.split("token=")[1]


def test_appdelegate_ainda_aponta_pro_onboarding():
    """Se algum dia o Swift passar a montar outra URL, o 302 vira código morto —
    e este teste avisa em vez de deixar a compatibilidade apodrecer sozinha."""
    swift = (REPO / "mobile/ios/App/App/AppDelegate.swift").read_text(encoding="utf-8")
    assert "/onboarding?token=" in swift


def test_callback_do_google_manda_pro_destino_final():
    src = (REPO / "frontend/finance_bot_websocket_custom.py").read_text(encoding="utf-8")
    assert '"/completar-cadastro?token={token}"' in src.replace("f\"", "\"")


# ── As duas páginas ──────────────────────────────────────────────────────────

def test_onboarding_sem_token_serve_o_wizard():
    resp = client.get("/onboarding")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert resp.headers["cache-control"] == "no-store"
    assert 'data-step="1"' in resp.text
    assert "/comecar.js" in resp.text


def test_completar_cadastro_serve_a_pagina_do_google():
    resp = client.get("/completar-cadastro")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-store"
    assert "/auth/google/complete-signup" in resp.text


# ── Assets: sem rota, 404 — não há StaticFiles mount neste projeto ───────────

@pytest.mark.parametrize("path,ctype", [
    ("/comecar.js", "application/javascript"),
    ("/comecar.css", "text/css"),
])
def test_assets_do_wizard_sao_servidos(path, ctype):
    resp = client.get(path)
    assert resp.status_code == 200, path
    assert resp.headers["content-type"].startswith(ctype), path


def test_html_referencia_apenas_assets_com_rota():
    """Todo /algo.css e /algo.js citado pelo comecar.html tem de ter rota."""
    html = (FRONTEND_DIR / "comecar.html").read_text(encoding="utf-8")
    refs = set(re.findall(r'(?:src|href)="(/[^"?]+\.(?:js|css))', html))
    assert refs, "o HTML deveria referenciar assets"
    for ref in refs:
        assert client.get(ref).status_code == 200, ref


# ── Migration: o que impede a base atual de cair no wizard ───────────────────

@pytest.mark.parametrize("column", ["plan_selected_at", "onboarding_completed_at"])
def test_coluna_de_gate_tem_backfill_por_default(column):
    """O par `add column ... default now()` + `drop default` é o que carimba as
    contas existentes e faz só as novas nascerem NULL.

    Testado contra a FONTE (estilo test_phosphor_subset.py) e não contra o banco
    de propósito: dropar a coluna e re-rodar init_db mutaria o schema
    compartilhado pela suíte inteira.

    Controle negativo: apagar `default now()` do ADD COLUMN, ou apagar a linha do
    DROP DEFAULT, deixa este teste vermelho — e sem ele o primeiro deploy jogaria
    100% da base num wizard de primeira configuração.
    """
    src = (REPO / "db/schema.py").read_text(encoding="utf-8")
    add = f"add column if not exists {column} timestamptz default now()"
    drop = f"alter column {column} drop default"
    assert add in src, f"{column} sem DEFAULT now() no ADD COLUMN"
    assert drop in src, f"{column} sem DROP DEFAULT"
    assert src.index(add) < src.index(drop), f"{column}: DROP DEFAULT antes do ADD"


def test_coluna_de_passo_existe():
    src = (REPO / "db/schema.py").read_text(encoding="utf-8")
    assert "add column if not exists onboarding_step smallint not null default 0" in src


# ── Endpoint de progresso ────────────────────────────────────────────────────

def _stub_db(monkeypatch, store):
    monkeypatch.setattr(onboarding_routes, "get_onboarding_state",
                        lambda uid: {"step": store.get(uid, {}).get("step", 0),
                                     "completed": store.get(uid, {}).get("completed", False)})
    monkeypatch.setattr(onboarding_routes, "set_onboarding_step",
                        lambda uid, step: store.setdefault(uid, {}).__setitem__(
                            "step", max(step, store.get(uid, {}).get("step", 0))))
    monkeypatch.setattr(onboarding_routes, "mark_onboarding_completed",
                        lambda uid: store.setdefault(uid, {}).__setitem__("completed", True))


def test_endpoint_nao_aceita_user_id_do_cliente(monkeypatch):
    """O usuário sai SÓ da sessão. Nada de {user_id} no caminho e nada de id no
    corpo — senão qualquer sessão válida escreveria no onboarding de terceiros.

    Controle negativo: fazer o endpoint ler um `user_id` do corpo faz a escrita
    cair no usuário 99 e este teste ficar vermelho.
    """
    store = {}
    _stub_db(monkeypatch, store)
    monkeypatch.setattr(onboarding_routes.shared, "resolve_dashboard_user_id", lambda req: 7)

    resp = client.post("/onboarding/state", json={"step": 3, "user_id": 99}, headers=_csrf(client))
    assert resp.status_code == 200
    assert store == {7: {"step": 3}}, "escreveu no usuário errado"

    # E não existe variante com user_id no caminho — comportamental, não por
    # introspecção do Starlette: o que importa é que essa URL não responda.
    fora = client.post("/onboarding/7/state", json={"step": 1}, headers=_csrf(client))
    assert fora.status_code == 404, "existe rota de onboarding com user_id no caminho"

    # Só as linhas de decorador: procurar no arquivo inteiro pegaria a própria
    # docstring, que explica que NÃO há {user_id} no caminho.
    decoradores = [
        linha for linha in
        (REPO / "frontend/routes/onboarding.py").read_text(encoding="utf-8").splitlines()
        if linha.lstrip().startswith("@router.")
    ]
    assert decoradores, "nenhuma rota encontrada no router"
    assert all("{user_id}" not in linha for linha in decoradores), decoradores


def test_endpoint_exige_sessao_valida(monkeypatch):
    from fastapi import HTTPException

    def _no_session(req):
        raise HTTPException(status_code=401, detail="Token de dashboard inválido ou expirado.")

    monkeypatch.setattr(onboarding_routes.shared, "resolve_dashboard_user_id", _no_session)
    assert client.post("/onboarding/state", json={"step": 2}, headers=_csrf(client)).status_code == 401
    assert client.get("/onboarding/state").status_code == 401


def test_passo_fora_da_faixa_e_recusado(monkeypatch):
    _stub_db(monkeypatch, {})
    monkeypatch.setattr(onboarding_routes.shared, "resolve_dashboard_user_id", lambda req: 7)
    assert client.post("/onboarding/state", json={"step": 99}, headers=_csrf(client)).status_code == 400
    assert client.post("/onboarding/state", json={"step": -1}, headers=_csrf(client)).status_code == 400


def test_passo_nao_regride(monkeypatch):
    """Voltar no wizard não pode apagar o progresso já alcançado.

    Controle negativo: tirar o `and onboarding_step < %s` do UPDATE em
    db/reports.py faz o passo 2 sobrescrever o 4.
    """
    store = {}
    _stub_db(monkeypatch, store)
    monkeypatch.setattr(onboarding_routes.shared, "resolve_dashboard_user_id", lambda req: 7)
    client.post("/onboarding/state", json={"step": 4}, headers=_csrf(client))
    client.post("/onboarding/state", json={"step": 2}, headers=_csrf(client))
    assert store[7]["step"] == 4
    # E o SQL real carrega a guarda (o stub acima só imita o comportamento).
    src = (REPO / "db/reports.py").read_text(encoding="utf-8")
    assert "and onboarding_step < %s" in src


def test_completed_e_idempotente_no_sql():
    """Controle negativo: remover o `is null` faz a 2ª chamada reescrever o
    timestamp original da conclusão."""
    src = (REPO / "db/reports.py").read_text(encoding="utf-8")
    assert "where user_id=%s and onboarding_completed_at is null" in src


def test_telemetria_nao_derruba_a_escrita(monkeypatch):
    """Perder um evento de funil é barato; perder o progresso do usuário não."""
    store = {}
    _stub_db(monkeypatch, store)
    monkeypatch.setattr(onboarding_routes.shared, "resolve_dashboard_user_id", lambda req: 7)

    import core.admin_dashboard as admin

    async def _boom(*a, **k):
        raise RuntimeError("log fora")

    monkeypatch.setattr(admin, "log_system_event", _boom)
    resp = client.post("/onboarding/state", json={"step": 2, "event": "view"}, headers=_csrf(client))
    assert resp.status_code == 200
    assert store[7]["step"] == 2


# ── O wizard antigo do dashboard.js foi absorvido ───────────────────────────

def test_wizard_antigo_do_dashboard_foi_removido():
    """O wizard de setup que morava em dashboard.js fazia 2 dos passos novos
    (saldo inicial e cartões) com a mesma copy e os mesmos endpoints, guardando
    o "pular" só em localStorage. Manter os dois seria duas fontes de verdade
    competindo pelo mesmo usuário virgem.

    Trava contra reintrodução por merge: qualquer um dos símbolos abaixo voltando
    a dashboard.js/dashboard.css deixa este teste vermelho.
    """
    js = (REPO / "frontend/dashboard.js").read_text(encoding="utf-8")
    css = (REPO / "frontend/dashboard.css").read_text(encoding="utf-8")
    html = (REPO / "frontend/dashboard.html").read_text(encoding="utf-8")

    for simbolo in ("wizard-overlay", "maybeOpenWizardOnLoad", "openWizard",
                    "pigbank_wizard_skipped_at", "_wizardState"):
        assert simbolo not in js, f"{simbolo} voltou pro dashboard.js"
        assert simbolo not in html, f"{simbolo} voltou pro dashboard.html"

    for regra in (".wizard-modal", ".wizard-progress", ".wizard-dot", ".wizard-step"):
        assert regra not in css, f"{regra} voltou pro dashboard.css"


def test_setup_status_continua_existindo():
    """O endpoint sobrevive à remoção — o wizard novo é seu único consumidor."""
    src = (REPO / "frontend/finance_bot_websocket_custom.py").read_text(encoding="utf-8")
    assert '"/account/{user_id}/setup-status"' in src
