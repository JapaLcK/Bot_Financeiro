"""Issue #321 — NUL/surrogate no PATH das rotas anônimas (as que o #369 não pega).

O #369 fechou as rotas anônimas com modelo Pydantic (`_CorpoSemVeneno` recusa na
borda com 422). Estas são outras: o veneno vem no **path param**, que não passa
por modelo nenhum e chega cru ao `cur.execute`.

VARREDURA DA CATEGORIA — medida em 2026-09-10 nesta árvore, REMEÇA antes de
reusar o número (§2). O comando:

    pares = [(m, r.path, {q.name: q.type_.__name__ for q in r.dependant.path_params})
             for r in app.routes if "{" in (getattr(r, "path", "") or "")
             for m in sorted((getattr(r, "methods", None) or set()) - {"HEAD", "OPTIONS"})]

Deu **120** pares método × path com path param (o `/ws/{user_id}` é WebSocket,
não tem `methods` e fica fora da conta), dos quais **4** respondiam 500 anônimo.
Os outros 116, cada grupo com o motivo medido:
  - **97** declaram TODOS os params como `int` (`{user_id}`, `{bill_id}`…) → 422
    do próprio FastAPI, antes do handler. É guarda nativa, não código nosso
    (§0.6). As 9 rotas de path do `/admin` estão AQUI DENTRO — são todas `int`,
    não uma categoria própria.
  - **14** têm `{user_id}` int na frente de um param `str`
    (`/pockets/{user_id}/{pocket_name:path}` e irmãs) → exigem sessão do dono do
    user_id; são a fatia autenticada que a #321 adiou, não a anônima.
  - **5** são anônimas com param só-`str` e já estavam fechadas:
    `/billing/pix/{public_token}` chama `resolve_dashboard_user_id` antes do SQL;
    `/i/{code}` já validava o formato — é a gêmea de `/r/`, e é dela que o
    conserto de `/r/` foi copiado em vez de inventado (§0.1); `/blog/{slug}`,
    `/brand/{path:path}` e `/fonts/{name}` não tocam o banco (medidas com NUL:
    302 / 404 / 404, idênticas ao valor legítimo).

**Só o NUL morde no path** (medido): o decodificador de URL do Starlette
neutraliza o surrogate antes do handler, então `%ED%A0%80` já saía 401/404/410/302
limpo. Os casos de surrogate seguem aqui mesmo assim — são o controle de que a
guarda nova não MUDOU o que já estava certo, e o dia em que o Starlette mudar
esse comportamento eles ficam vermelhos em vez de silenciosos.

No fim do arquivo mora a rota AUTENTICADA que herdou a mesma guarda de
`db.reports` de carona (`POST /auth/dashboard-link`): ela não é da categoria do
título, mas mede a MESMA função, e separar em outro arquivo é como dois testes
passam a medir coisas diferentes achando que medem a mesma.

**Onde o veneno cai é a resposta que a rota JÁ dava para "não existe"** — 401,
302, 410, 404 —, nunca um 422 novo. Em `/r/{code}` isso é requisito, não estilo:
o docstring da rota diz "sem vazar se o código existe", e um 422 só no código
envenenado criaria o oráculo que o 302 existe para não dar.

CONTROLE NEGATIVO (§3): `test_negativo_*` desliga cada guarda no módulo em que
ela mora e exige o 500 de volta. São QUATRO, um por guarda, porque uma só não
discrimina: desligar a de `db.reports` deixa `/r/`, `/auth/...` verdes.

CONTROLE POSITIVO (§3): `test_positivo_*`. Sem eles, uma guarda que recusasse
TUDO (`return None` no topo) passaria em todos os casos de veneno e seria pior
que o bug — magic link do bot, link de afiliado e download de dados parariam de
funcionar. Por isso o positivo de `/r/` exige a ATRIBUIÇÃO gravada, não só o 302.
"""
import json
import uuid

import pytest
from fastapi.testclient import TestClient

import db.affiliates as db_affiliates
import db.google_auth as db_google_auth
import db.privacy as db_privacy
import db.reports as db_reports
import frontend.finance_bot_websocket_custom as dashboard
from frontend.routes import shared
from db import ensure_user
from db.affiliates import create_affiliate, record_referral
from db.google_auth import create_pending_google_signup
from db.privacy import create_data_export_token
from db.reports import consume_dashboard_session, create_dashboard_session

NUL = "a%00b"
SURR_ALTO = "a%ED%A0%80b"
SURR_BAIXO = "a%ED%B0%80b"
VENENOS = [NUL, SURR_ALTO, SURR_BAIXO]
VENENO_IDS = ["nul", "surrogate_alto", "surrogate_baixo"]

# (template do path, status que a rota JÁ dá para "não existe")
ROTAS = [
    ("/d/{}", 401),
    ("/r/{}", 302),
    ("/i/{}", 302),
    ("/auth/account/export/download/{}", 410),
    ("/auth/google/pending/{}", 404),
]
ROTA_IDS = ["d_magic_link", "r_afiliado", "i_prospect", "export_download", "google_pending"]


def _client() -> TestClient:
    # raise_server_exceptions=False: sem isso o 500 vira exceção propagada e o
    # teste nunca chega a ler o status que quer medir.
    return TestClient(dashboard.app, base_url="https://testserver",
                      raise_server_exceptions=False)


@pytest.mark.parametrize("veneno", VENENOS, ids=VENENO_IDS)
@pytest.mark.parametrize("template,status_de_inexistente", ROTAS, ids=ROTA_IDS)
def test_veneno_no_path_cai_no_status_de_inexistente(template, status_de_inexistente, veneno):
    """Nem 500, nem status novo: o mesmo que a rota devolve para um id que não existe."""
    resp = _client().get(template.format(veneno), follow_redirects=False)
    assert resp.status_code == status_de_inexistente, (
        f"{template.format(veneno)} devolveu {resp.status_code}, "
        f"esperado {status_de_inexistente} (o mesmo de 'não existe')"
    )


def test_r_envenenado_e_indistinguivel_de_codigo_inexistente(user_id):
    """O 302 do veneno tem de ser IGUAL ao de um código que não existe — incluindo
    a ausência do `set-cookie`. É o oráculo que um 422 abriria (ver docstring)."""
    # Um cliente NOVO por requisição: o `csrf_token` só sai na primeira resposta
    # de cada cliente (middleware), então reusar um cliente compararia o
    # middleware, não a rota — o 2º set-cookie sairia vazio por construção.
    inexistente = _client().get("/r/NAOEXISTE123", follow_redirects=False)
    envenenado = _client().get(f"/r/{NUL}", follow_redirects=False)

    def _cookies_da_rota(resp):
        return [h for h in resp.headers.get_list("set-cookie")
                if not h.startswith(f"{dashboard.CSRF_COOKIE_NAME}=")]

    assert envenenado.status_code == inexistente.status_code == 302
    assert envenenado.headers["location"] == inexistente.headers["location"] == "/"
    assert _cookies_da_rota(envenenado) == _cookies_da_rota(inexistente) == []


# ─── Controle POSITIVO: o caminho legítimo continua funcionando ───────────────

def test_positivo_d_magic_link_do_bot_ainda_loga(user_id):
    """O magic link que o bot manda por WhatsApp. Sem este teste, um
    `return None` no topo de `consume_dashboard_session` passaria em tudo acima."""
    code = create_dashboard_session(user_id)
    resp = _client().get(f"/d/{code}", follow_redirects=False)

    # 302 para /app, não 200: o 401 da página "link expirado" é o que sai quando
    # `consume_dashboard_session` devolve None — é ele que este teste exclui.
    assert resp.status_code == 302, f"magic link válido devolveu {resp.status_code}"
    assert resp.headers["location"] == "/app"
    assert any(h.startswith(f"{shared.DASHBOARD_COOKIE_NAME}=")
               for h in resp.headers.get_list("set-cookie")), "não criou sessão"
    # uso único: o mesmo code não vale duas vezes
    assert consume_dashboard_session(code) is None


def test_positivo_r_afiliado_valido_seta_cookie_e_atribui(user_id):
    """302 + cookie NÃO bastam: o que paga comissão é a ATRIBUIÇÃO. Este é o
    caso que morre se a guarda nova recusar código legítimo."""
    code = create_affiliate(user_id)["code"]
    resp = _client().get(f"/r/{code.lower()}", follow_redirects=False)

    assert resp.status_code == 302
    cookie = next((h for h in resp.headers.get_list("set-cookie")
                   if h.startswith("ref_code=")), None)
    assert cookie is not None and code in cookie, "cookie de atribuição não saiu"

    indicado = int(uuid.uuid4().int % 10_000_000_000)
    ensure_user(indicado)
    try:
        assert record_referral(code, indicado) is True, "referral não foi gravado"
    finally:
        with db_reports.get_conn() as conn, conn.cursor() as cur:
            cur.execute("delete from affiliate_referrals where referred_user_id = %s", (indicado,))
            cur.execute("delete from accounts where user_id = %s", (indicado,))
            cur.execute("delete from users where id = %s", (indicado,))
            conn.commit()


def test_positivo_i_prospect_valido_seta_cookie():
    """A gêmea que já estava certa: continua certa depois do PR."""
    resp = _client().get("/i/ABC12345", follow_redirects=False)
    assert resp.status_code == 302
    assert any(h.startswith("prospect_code=ABC12345")
               for h in resp.headers.get_list("set-cookie"))


def test_positivo_export_download_com_token_valido_entrega_o_zip(user_id):
    token, _ = create_data_export_token(user_id)
    resp = _client().get(f"/auth/account/export/download/{token}", follow_redirects=False)

    assert resp.status_code == 200, f"token válido devolveu {resp.status_code}"
    assert resp.headers["content-type"] == "application/zip"
    assert resp.content[:2] == b"PK", "o corpo não é um ZIP"


def test_positivo_google_pending_com_token_valido_devolve_o_email(user_id):
    email = f"pend-{uuid.uuid4().hex[:8]}@321.example.com"
    token = create_pending_google_signup(f"sub-{uuid.uuid4().hex[:8]}", email, "Fulano")
    try:
        resp = _client().get(f"/auth/google/pending/{token}", follow_redirects=False)
        assert resp.status_code == 200, f"token válido devolveu {resp.status_code}"
        assert resp.json()["email"] == email
        assert resp.json()["name_hint"] == "Fulano"
    finally:
        with db_reports.get_conn() as conn, conn.cursor() as cur:
            cur.execute("delete from pending_google_signups where token = %s", (token,))
            conn.commit()


# ─── Controle NEGATIVO: desligar a guarda tem de trazer o 500 de volta ────────
# Um por guarda, e cada um num caso que estava VERDE (§3): desligar a de
# `db.reports` não mexe em `/r/` nem nas de `/auth/`, então um negativo só não
# mediria nada sobre as outras três.

@pytest.mark.parametrize("modulo,template", [
    (db_reports, "/d/{}"),
    (db_privacy, "/auth/account/export/download/{}"),
    (db_google_auth, "/auth/google/pending/{}"),
], ids=["d_magic_link", "export_download", "google_pending"])
def test_negativo_sem_tem_veneno_a_rota_volta_a_dar_500(monkeypatch, modulo, template):
    monkeypatch.setattr(modulo, "tem_veneno", lambda _valor: False)
    resp = _client().get(template.format(NUL), follow_redirects=False)
    assert resp.status_code == 500, (
        f"com a guarda DESLIGADA, {template} devolveu {resp.status_code} e não 500 — "
        "o teste do veneno não está medindo esta guarda"
    )


def test_negativo_sem_o_regex_o_r_volta_a_dar_500(monkeypatch):
    """A guarda de `/r/` é o regex da gêmea, não o `tem_veneno` — por isso o
    negativo dela é separado: desligar o `tem_veneno` deixa `/r/` verde."""
    class _SempreCasa:
        def fullmatch(self, _valor):
            return True

    monkeypatch.setattr(db_affiliates, "_CODE_RE", _SempreCasa())
    resp = _client().get(f"/r/{NUL}", follow_redirects=False)
    assert resp.status_code == 500, (
        f"com o regex DESLIGADO, /r/ devolveu {resp.status_code} e não 500"
    )


# ─── A leitura ficou mais restritiva: prove que a ESCRITA cabe nela ──────────

def test_codigo_escolhido_a_mao_continua_achavel_pela_leitura(user_id):
    """A guarda nova de `get_affiliate_by_code` troca `if not code` por um regex:
    é mais restritiva do que era. O risco que ELA cria não é 500, é o contrário
    — recusar código que a ESCRITA aceitou, e aí o link `/r/{code}` nunca
    resolve e a comissão nunca é paga.

    O caso extremo é o código escolhido à mão pelo admin
    (`core/admin_dashboard.py:2078` chama `create_affiliate(user_id, code, bps)`
    com o que veio do formulário), porque só ele escapa do `_generate_code`.
    Os dois lados passam pelo mesmo `_normalize_code` antes do mesmo `_CODE_RE`
    — este teste é o que fica vermelho no dia em que um dos dois mudar sozinho.
    """
    escolhido = f" a{uuid.uuid4().hex[:6]} "   # espaço e minúscula: o que o form manda
    aff = create_affiliate(user_id, code=escolhido)
    try:
        assert aff["code"] == escolhido.strip().upper()
        achado = db_affiliates.get_affiliate_by_code(escolhido.lower())
        assert achado is not None, "código que a escrita gravou não é achado pela leitura"
        assert achado["id"] == aff["id"]
    finally:
        with db_reports.get_conn() as conn, conn.cursor() as cur:
            cur.execute("delete from affiliates where id = %s", (aff["id"],))
            conn.commit()


# ─── Inundação de log (o motivo de `/d/` ser a pior das quatro) ───────────────

def _conta_eventos() -> int:
    with db_reports.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) as n from system_event_logs")
        return int(cur.fetchone()["n"])


def test_inundacao_de_log_por_d_envenenado_grava_zero_linhas():
    """`/d/{code}` é anônima e NÃO tem `@limiter.limit` (medido:
    `"limiter.limit" not in inspect.getsource`). Enquanto respondia 500, cada
    requisição virava uma linha em `system_event_logs` pelo
    `admin_error_logging_middleware` — MEDIDO nesta árvore com a guarda
    desligada: 25 req → 25/25 em 500 e 25 linhas, em 0,25 s.

    Com a guarda, o 500 some e o middleware não grava nada — por isso o PR NÃO
    acrescenta rate limit: sem 500 não há linha, e o teto de `/d/` vira outro
    assunto (ponytail).
    """
    import asyncio

    import core.admin_dashboard as admin_dashboard
    asyncio.run(admin_dashboard.ensure_admin_tables())

    client = _client()
    antes = _conta_eventos()
    status = [client.get(f"/d/abc%00{i}", follow_redirects=False).status_code
              for i in range(25)]

    assert status.count(500) == 0, f"{status.count(500)} de 25 ainda em 500"
    assert _conta_eventos() == antes, (
        f"{_conta_eventos() - antes} linha(s) novas em system_event_logs — "
        "a inundação anônima continua aberta"
    )


# ─── O SEGUNDO chamador da MESMA guarda: `POST /auth/dashboard-link` ──────────
# A guarda mora em `consume_dashboard_session`, não na rota, e essa função tem
# DOIS chamadores (§2): `/d/{code}`, anônima, acima; e este, AUTENTICADO
# (`Depends(_get_current_user)`), que entrou de carona. O que ele fazia antes
# foi MEDIDO, não deduzido: `DashboardLinkBody` é `BaseModel` puro (NÃO herda
# `_CorpoSemVeneno`), o `code` chega cru ao handler, e os três venenos davam
# **500** — mais um 500 fechado por este PR, sem a inundação de log do `/d/`
# porque exige sessão. Aqui os TRÊS mordem, e não só o NUL como no path: no
# corpo JSON o surrogate chega intacto ao psycopg em vez de ser neutralizado
# pelo decodificador de URL do Starlette.
VENENOS_CRUS = ["\x00", "\ud800", "\udc00"]   # no corpo JSON, não percent-encoded
CSRF_LINK = "csrf-321-dashboard-link"


def _post_dashboard_link(uid: int, code: str):
    # Corpo escrito byte a byte: o `json=` do httpx recusa surrogate antes de sair.
    client = _client()
    client.cookies.set("auth_token", dashboard._make_jwt(uid, f"u{uid}@321.example.com"))
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, CSRF_LINK)
    return client.post("/auth/dashboard-link", content=json.dumps({"code": code}),
                       headers={"Content-Type": "application/json",
                                dashboard.CSRF_HEADER_NAME: CSRF_LINK})


@pytest.mark.parametrize("veneno", VENENOS_CRUS, ids=VENENO_IDS)
def test_veneno_no_corpo_do_dashboard_link_cai_no_401_de_inexistente(user_id, veneno):
    resp = _post_dashboard_link(user_id, f"abc{veneno}123")
    assert resp.status_code == 401, f"devolveu {resp.status_code}, esperado 401"
    # MESMO corpo do código que não existe: nada novo vaza para o cliente.
    assert resp.json() == _post_dashboard_link(user_id, "NAOEXISTE123").json()


def test_negativo_sem_tem_veneno_o_dashboard_link_volta_a_dar_500(monkeypatch, user_id):
    monkeypatch.setattr(db_reports, "tem_veneno", lambda _valor: False)
    resp = _post_dashboard_link(user_id, "abc\x00123")
    assert resp.status_code == 500, (
        f"com a guarda DESLIGADA, /auth/dashboard-link devolveu {resp.status_code} "
        "e não 500 — o teste acima não está medindo esta guarda"
    )


def test_positivo_dashboard_link_com_codigo_legitimo_ainda_vincula(user_id):
    """A carona não pode ter quebrado o caminho legítimo: o mesmo 200, o mesmo
    cookie de sessão e o mesmo uso único de antes."""
    code = create_dashboard_session(user_id)
    resp = _post_dashboard_link(user_id, code)

    assert resp.status_code == 200, f"código válido devolveu {resp.status_code}"
    assert resp.json()["expires_in"] > 0
    assert any(h.startswith(f"{shared.DASHBOARD_COOKIE_NAME}=")
               for h in resp.headers.get_list("set-cookie")), "não criou sessão"
    assert consume_dashboard_session(code) is None, "o código não foi consumido"
