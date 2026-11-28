"""O guard da sessão de leitor, rota a rota, sobre o `app` real (monólito + /api/v2 + /api/app).

Matriz GERADA de `app.routes` (nada de lista escrita à mão): todo método não seguro com token de
leitor dá 403 `somente_leitura`. Toda rota GET é classificada em NEGADO (constante do guard) ou
PERMITIDO (tabela abaixo); rota nova sem classificação reprova. Controle negativo: com o guard
desligado a matriz fica vermelha (`test_controle_negativo_*`). Controle positivo: o dono nunca
recebe esse 403 e o leitor continua lendo.
"""
import re

import pytest
from fastapi.testclient import TestClient

import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import csrf, sessao_de
from _apoio_leitura import leitor_de, url_de
from conftest import promote_to_pro
from frontend.routes import leitura_guard
from test_rotas_senha_obrigatoria import _rotas

BARRADO = {"error": "somente_leitura"}
NAO_SEGUROS = {"POST", "PUT", "PATCH", "DELETE"}

# Classificação de TODA rota GET que o leitor pode chamar (o resto está em NEGADO_LEITOR). Cada linha leva a classe:
#   "mascara"  = devolve lançamento/conta/recorrente/previsão/texto livre com nome; sai pelo helper único
#                (`core/services/leitura_mascara.py`) e entra na tabela de `tests/test_leitura_mascara.py`
#   "sem_nome" = asset, página, agregado ou catálogo: nenhum campo de pessoa
# Rota GET nova sem linha aqui (nem em NEGADO_LEITOR) reprova `test_toda_rota_get_esta_classificada`.
_V2_SEM_NOME = ("bancos|caixinhas|cartoes|categorias|comparacao|contas|eventos|guia|investido|me|metas|orcamentos-categoria|"
                "patrimonio|progresso-milhao|renda|rendimento|resumo-do-mes|mes-detalhes")
_V2_MASCARA = "assinaturas|contas-a-pagar|gastos|insights|lancamentos|orcamento-domestico|parcelas|previsao"
_C = lambda classe, *ps: tuple((re.compile(p), classe) for p in ps)  # noqa: E731
PERMITIDO_LEITOR = (
    *_C("sem_nome",
        r"\.(js|css|png|json|xml|txt|html)$",                       # asset estático
        r"^/(fonts|brand|quiz/assets|static)/",                      # idem
        r"^/(\.well-known/apple-app-site-association|health|auth/validate|auth/me|wa/webhook|webhook)$",
        r"^/(app|agents|afiliado|assinar|black-friday|blog|cadastro|changelog|comandos|comandos-app|como-funciona|"
        r"completar-cadastro|continuar-compra|familia/entrar|funcionalidades|home|inicio-antiga|login|lp|oferta|"
        r"onboarding|painel|precos|privacy|q|quiz|recuperar-senha|redefinir-senha|reset-password|settings|suporte|"
        r"suporte/contato|teste|termos|vsl|wa|whatsapp)?$",          # páginas (HTML): sem dado de usuário
        r"^/blog/[^/]+$", r"^/api/(blog/news|commands-catalog)$",   # conteúdo público
        r"^/api/(v2|app)/(" + _V2_SEM_NOME + r")$",                  # dados do dono pela sessão, sem texto de pessoa
        r"^/account/[^/]+/setup-status$", r"^/analytics/[^/]+/(categories|evolution|weekday-pattern)$",  # agregados
        r"^/bills/[^/]+$", r"^/budgets/[^/]+(/status)?$", r"^/cards/[^/]+/summary$", r"^/categories/[^/]+$",
        r"^/expenses/daily/[^/]+$", r"^/goals/[^/]+/status$", r"^/history/[^/]+(/quick-stats)?$",
        r"^/investments/[^/]+/rates$", r"^/onboarding/",
        ),
    *_C("mascara",
        r"^/api/(v2|app)/(" + _V2_MASCARA + r")$", r"^/api/(v2|app)/gastos/categoria$",
        r"^/data/[^/]+$", r"^/history/[^/]+/list$", r"^/forecast/[^/]+$", r"^/recurring-bills/[^/]+/projection$",
        r"^/recurring-(expenses|incomes)/[^/]+$", r"^/household-budget/[^/]+/status$",
        r"^/categories/[^/]+/launches$", r"^/analytics/[^/]+/(kpis|patterns)$", r"^/insights/[^/]+/current$",
        r"^/bills/[^/]+/[^/]+$", r"^/installments/[^/]+/list$",
        ),
)


def classe_da_rota(url: str) -> str | None:
    return next((c for g, c in PERMITIDO_LEITOR if g.search(url)), None)


def _gets():
    return sorted({(p, url_de(p)) for m, p, _ in _rotas() if m == "GET"})


def _barrada(r) -> bool:
    return r.status_code == 403 and r.headers.get("content-type", "").startswith("application/json") and r.json() == BARRADO


@pytest.fixture
def mundo(monkeypatch, user_id):
    promote_to_pro(user_id, plan="pro_max")
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", str(user_id))
    leitor = leitor_de(user_id)
    dono = sessao_de(user_id)
    return user_id, leitor, {"Authorization": f"Bearer {dono['access']}"}


def _ofensores(client, headers, rotas):
    """Rotas não seguras que NÃO deram 403 somente_leitura."""
    return [(m, p) for m, p, _ in rotas if m in NAO_SEGUROS
            and (m, p) not in leitura_guard.ESCRITA_PERMITIDA
            and not _barrada(client.request(m, url_de(p), headers=headers))]


# ── 1. matriz gerada ─────────────────────────────────────────────────────────

def test_todo_metodo_nao_seguro_e_barrado_para_o_leitor(mundo):
    _, leitor, _ = mundo
    rotas = list(_rotas())
    assert len([1 for m, *_ in rotas if m in NAO_SEGUROS]) > 150, "a descida de rotas quebrou"
    assert {p for m, p, _ in rotas if m == "POST"} >= {"/api/v2/lancamentos/carteira", "/api/app/metas", "/launches/{user_id}"}
    assert _ofensores(TestClient(dashboard.app), leitor["bearer"], rotas) == []


def test_controle_negativo_guard_desligado_deixa_a_matriz_vermelha(mundo, monkeypatch):
    """Sem o guard, rotas sem resolvedor de sessão (a 2ª trava não as alcança) passam: o grupo mede algo."""
    _, leitor, _ = mundo
    monkeypatch.setattr(leitura_guard, "e_leitor", lambda conn: False)
    sem_resolvedor = [("POST", "/auth/register", None), ("POST", "/auth/forgot-password", None)]
    assert _ofensores(TestClient(dashboard.app), leitor["bearer"], sem_resolvedor) == [
        ("POST", "/auth/register"), ("POST", "/auth/forgot-password")]


def test_segunda_trava_barra_rota_com_resolvedor_mesmo_sem_o_guard(mundo, monkeypatch):
    uid, leitor, _ = mundo
    monkeypatch.setattr(leitura_guard, "e_leitor", lambda conn: False)
    c = TestClient(dashboard.app)
    corpo = {"tipo": "despesa", "valor": 10, "alvo": "teste", "descricao": "x", "categoria": "outros", "nome": "x"}
    for path in ("/api/v2/lancamentos/carteira", "/api/app/metas", f"/launches/{uid}"):
        r = c.post(path, headers=leitor["bearer"], json=corpo)
        assert r.status_code == 403 and "somente_leitura" in r.text, (path, r.status_code, r.text)


def test_controle_positivo_dono_nao_recebe_o_403_do_leitor(mundo):
    uid, _, dono = mundo
    c = TestClient(dashboard.app)
    for m, path in (("POST", "/api/v2/lancamentos/carteira"), ("POST", f"/launches/{uid}"),
                    ("POST", "/api/v2/metas"), ("DELETE", "/auth/account"), ("POST", "/auth/register")):
        assert not _barrada(c.request(m, path, headers=dono, json={})), (m, path)


def test_allowlist_de_escrita_passa_pelo_guard(mundo):
    _, leitor, _ = mundo
    c = TestClient(dashboard.app)
    assert not _barrada(c.post("/auth/refresh", headers=leitor["bearer"]))
    assert not _barrada(c.post("/auth/logout", headers=leitor["bearer"]))
    assert not _barrada(c.post("/auth/leitura/login", headers=leitor["bearer"], json={}))


def test_403_sai_com_security_headers_e_sem_cache(mundo):
    _, leitor, _ = mundo
    r = TestClient(dashboard.app).post("/launches/1", headers=leitor["bearer"])
    assert _barrada(r)
    assert r.headers["x-frame-options"] == "DENY" and "frame-ancestors" in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-store"


# ── 2. toda rota GET classificada ────────────────────────────────────────────

def test_toda_rota_get_esta_classificada():
    nao = [p for p, u in _gets()
           if not any(n.search(u) for n in leitura_guard.NEGADO_LEITOR)
           and classe_da_rota(u) is None]
    assert not nao, f"rota GET sem classificação (NEGADO no guard ou PERMITIDO aqui): {nao}"
    # a classificação não é vacuamente larga: os dois lados têm rota
    assert any(n.search(u) for p, u in _gets() for n in leitura_guard.NEGADO_LEITOR)
    assert {classe_da_rota(u) for p, u in _gets()} >= {"mascara", "sem_nome"}


def test_get_negada_da_403_ao_leitor_e_nao_ao_dono(mundo):
    uid, leitor, dono = mundo
    c = TestClient(dashboard.app, follow_redirects=False)
    negadas = [(p, u.replace("/1/", f"/{uid}/").replace("/1", f"/{uid}", 1) if "{user_id}" in p else u)
               for p, u in _gets() if any(n.search(u) for n in leitura_guard.NEGADO_LEITOR)]
    assert len(negadas) >= 20
    for p, u in negadas:
        assert _barrada(c.get(u, headers=leitor["bearer"])), ("leitor", p)
        assert not _barrada(c.get(u, headers=dono)), ("dono", p)


def test_get_permitida_nao_e_barrada_ao_leitor(mundo):
    uid, leitor, _ = mundo
    c = TestClient(dashboard.app, follow_redirects=False)
    for p, u in _gets():
        if any(n.search(u) for n in leitura_guard.NEGADO_LEITOR) or "eventos" in p:  # SSE não termina
            continue
        u = u.replace("/1/", f"/{uid}/") if "{user_id}" in p else u
        assert not _barrada(c.get(u, headers=leitor["bearer"])), p


# ── 8. mistura de credenciais ────────────────────────────────────────────────

def test_cookie_de_dono_com_bearer_de_leitor_barra_a_escrita(mundo):
    uid, leitor, _ = mundo
    c = TestClient(dashboard.app)
    c.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, sessao_de(uid)["dashboard"])
    assert _barrada(c.post(f"/launches/{uid}", headers=leitor["bearer"], json={}))


def test_cookie_de_leitor_com_bearer_de_dono_barra_a_escrita(mundo):
    uid, leitor, dono = mundo
    c = TestClient(dashboard.app)
    c.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, leitor["dashboard"])
    assert _barrada(c.post(f"/launches/{uid}", headers=dono, json={}))


# ── 9. o guard medido por lista LITERAL (independente das constantes) ────────

# GETs concretos que o leitor tem de receber 403: ao menos um caminho real por entrada de NEGADO_LEITOR.
# Escrita à mão de propósito: apagar uma entrada da constante tem de reprovar aqui.
GET_NEGADO_LITERAL = (
    "/d/abc123", "/auth/google/start", "/auth/google/callback", "/auth/account/export/download/tok",
    "/conta", "/billing/status", "/unsubscribe", "/retomar", "/retomar/abc", "/r/abc", "/i/abc", "/t/abc",
    "/recurring-bills/1", "/settings/sessions", "/auth/mfa/status", "/auth/dashboard-profile",
    "/api/v2/perfil", "/api/app/perfil", "/open-finance/1/connectors", "/api/affiliate/me",
    "/ai/messages", "/debug/x", "/admin", "/admin/x", "/api/v2/familia", "/api/app/familia/x",
    "/api/v2/conta/leitura", "/api/app/conta/leitura",
    "/open-finance/1", "/open-finance/1/movements", "/pockets/1/caixa/history", "/agents/1/feed", "/agents/1",
    "/analytics/1/top-merchants", "/cards/1/2/delete-impact", "/installments/1/g/delete-impact",  # PR 3d: sem categoria para mascarar
)


def test_get_negado_literal_da_403_ao_leitor(mundo):
    _, leitor, _ = mundo
    c = TestClient(dashboard.app, follow_redirects=False)
    for u in GET_NEGADO_LITERAL:
        assert _barrada(c.get(u, headers=leitor["bearer"])), u


def test_toda_entrada_de_negado_leitor_tem_caminho_literal():
    sem = [p.pattern for p in leitura_guard.NEGADO_LEITOR if not any(p.search(u) for u in GET_NEGADO_LITERAL)]
    assert not sem, f"entrada de NEGADO_LEITOR sem caminho na lista literal: {sem}"


def test_escrita_permitida_e_exatamente_a_esperada():
    assert leitura_guard.ESCRITA_PERMITIDA == {
        ("POST", "/auth/refresh"), ("POST", "/auth/logout"), ("POST", "/auth/leitura/login")}


# ── 10. 2ª trava em `_get_current_user` (rota fora do /api/v2, sem `usuario_sessao`) ──

def test_get_current_user_barra_o_leitor_sem_o_guard(mundo, monkeypatch):
    _, leitor, dono = mundo
    monkeypatch.setattr(leitura_guard, "e_leitor", lambda conn: False)   # guard desligado
    c = TestClient(dashboard.app)
    h = csrf(c)
    r = c.post("/auth/link-code", headers={**leitor["bearer"], **h})   # Depends(_get_current_user), não segura
    assert r.status_code == 403 and r.json()["detail"] == BARRADO, r.text
    assert c.post("/auth/link-code", headers={**dono, **h}).status_code == 200  # controle positivo: o dono passa


# ── 11. telemetria de página não corre em nome do dono ───────────────────────

def _linhas_funil(uid):
    import db
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select (select count(*) from remarketing_regua where user_id = %(u)s) as regua,"
                    " (select count(*) from checkout_funnel_events where user_id = %(u)s) as funil", {"u": uid})
        r = cur.fetchone()
    return r["regua"], r["funil"]


def test_precos_do_leitor_nao_grava_funil_nem_regua_e_o_do_dono_grava(mundo):
    uid, leitor, dono = mundo
    c = TestClient(dashboard.app, follow_redirects=False)
    assert c.get("/precos", headers=leitor["bearer"]).status_code == 200
    assert _linhas_funil(uid) == (0, 0)
    assert c.get("/precos", headers=dono).status_code == 200            # controle positivo
    assert _linhas_funil(uid) == (1, 1)


def test_me_do_v2_expoe_somente_leitura_so_ao_leitor(mundo):
    _, leitor, dono = mundo
    c = TestClient(dashboard.app)
    assert c.get("/api/v2/me", headers=leitor["bearer"]).json()["somente_leitura"] is True
    assert c.get("/api/v2/me", headers=dono).json()["somente_leitura"] is False
