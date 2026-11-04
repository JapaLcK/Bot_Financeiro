"""Contrato: a renovação da sessão do navegador confirma a conta antes de renovar.

O cliente diz em `X-PB-Conta` a conta que espera (`-` quando ainda não sabe), e
o servidor devolve a conta da sessão no mesmo cabeçalho em toda resposta 2xx
autenticada. O `/auth/refresh` do navegador responde 401 `sessao_divergente`,
sem rotacionar nada e limpando os cookies, quando a conta esperada ou a dos
cookies não é a dona do refresh, ou quando não há nenhuma das duas para
comparar. O sucesso devolve `user_id` e `confirmada`. O canal do app (refresh no
`Authorization`) fica fora dessas regras. Banco e monólito reais.
"""
from fastapi.testclient import TestClient

import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import csrf
from core.refresh_tokens import _hash
from core.sessions import revoke_session
from frontend.routes.shared import AUTH_COOKIE_NAME, DASHBOARD_COOKIE_NAME
from test_sessao_divergente import ab, escreve, gravados, jarro, um  # noqa: F401 — `ab` é fixture

CONTA = dashboard.CONTA_HEADER


def renova(c, conta=None):
    headers = {**csrf(c), "Content-Type": "application/json"}
    if conta is not None:
        headers[CONTA] = str(conta)
    return c.post("/auth/refresh", headers=headers)


def intacto(refresh):
    """O refresh não foi consumido nem revogado."""
    return um("select count(*) from auth_refresh_tokens where token_hash = %s"
              " and used_at is null and revoked_at is null",
              _hash(refresh)) == 1


def sessoes(x):
    return um("select count(*) from auth_sessions where user_id = %s", x.uid)


def nomes(c):
    return {k.name for k in c.cookies.jar}


def recusado(r, c):
    assert r.status_code == 401, r.text
    assert r.json() == {"detail": "sessao_divergente"}
    assert not {AUTH_COOKIE_NAME, DASHBOARD_COOKIE_NAME, dashboard.REFRESH_COOKIE_NAME} & nomes(c)


# ── Recusas ──────────────────────────────────────────────────────────────────

def test_r1_conta_esperada_diferente_da_dona_do_refresh(ab):
    """Só o refresh de A no navegador (o resto venceu) e a página esperando B."""
    a, b = ab
    c = jarro(refresh=a.s["refresh"])
    antes = sessoes(a)
    recusado(renova(c, b.uid), c)
    assert intacto(a.s["refresh"])
    assert sessoes(a) == antes
    assert escreve(c).status_code == 401
    assert gravados(a, b) == [0, 0]


def test_r2_sem_conta_esperada_e_sem_cookie_de_sessao(ab):
    """Cliente que não manda o cabeçalho, com só o refresh: nada a comparar."""
    a, b = ab
    c = jarro(refresh=a.s["refresh"])
    recusado(renova(c), c)
    assert intacto(a.s["refresh"])
    assert escreve(c).status_code == 401
    assert gravados(a, b) == [0, 0]


def test_r8_access_e_dashboard_de_contas_diferentes(ab):
    a, b = ab
    c = jarro(auth=a.s["access"], refresh=a.s["refresh"], dash=b.s["dashboard"])
    assert escreve(c).status_code == 401
    recusado(renova(c, b.uid), c)
    assert intacto(a.s["refresh"])
    assert escreve(c).status_code == 401
    assert gravados(a, b) == [0, 0]


def test_r9_dashboard_de_outra_conta_mesmo_com_a_sessao_dela_encerrada(ab):
    """O cookie de dashboard conta pela assinatura e pelo prazo, não pelo banco."""
    a, b = ab
    revoke_session(b.uid, b.s["jti"])
    c = jarro(refresh=a.s["refresh"], dash=b.s["dashboard"])
    assert escreve(c).status_code == 401
    recusado(renova(c), c)
    assert intacto(a.s["refresh"])
    assert escreve(c).status_code == 401
    assert gravados(a, b) == [0, 0]


# ── Renovações legítimas ─────────────────────────────────────────────────────

def test_r3_sem_conta_esperada_com_o_dashboard_da_mesma_conta(ab):
    a, _ = ab
    c = jarro(refresh=a.s["refresh"], dash=a.s["dashboard"])
    r = renova(c)
    assert r.status_code == 200, r.text
    assert (r.json()["user_id"], r.json()["confirmada"]) == (a.uid, True)


def test_r4_conta_esperada_igual_a_dona_do_refresh(ab):
    a, b = ab
    c = jarro(refresh=a.s["refresh"])
    r = renova(c, a.uid)
    assert r.status_code == 200, r.text
    assert (r.json()["user_id"], r.json()["confirmada"]) == (a.uid, True)
    assert r.headers[CONTA] == str(a.uid)
    assert escreve(c).status_code == 200
    assert gravados(a, b) == [1, 0]


def test_r5_conta_ainda_desconhecida_renova_sem_confirmar(ab):
    a, _ = ab
    c = jarro(refresh=a.s["refresh"])
    r = renova(c, "-")
    assert r.status_code == 200, r.text
    assert (r.json()["user_id"], r.json()["confirmada"]) == (a.uid, False)


def test_r6_canal_do_app_fica_fora(ab):
    a, b = ab
    r = TestClient(dashboard.app).post("/auth/refresh", headers={
        "Authorization": f"Bearer {a.s['refresh']}",
        dashboard.APP_CLIENT_HEADER: "app",
        "Content-Type": "application/json",
        CONTA: str(b.uid),
    })
    assert r.status_code == 200, r.text
    assert r.json()["user_id"] == a.uid


# ── O cabeçalho nas respostas ────────────────────────────────────────────────

def test_r7_conta_vem_nas_respostas_autenticadas(ab):
    a, b = ab
    c = jarro(auth=a.s["access"], dash=a.s["dashboard"])
    for rota in ("/auth/me", "/auth/dashboard-profile", "/api/v2/me", f"/settings/{a.uid}/security"):
        r = c.get(rota)
        assert r.status_code == 200, (rota, r.status_code)
        assert r.headers.get(CONTA) == str(a.uid), rota
    # Resolve a sessão de A e responde não-2xx: o cabeçalho não acompanha.
    r = c.get(f"/settings/{b.uid}/security")
    assert r.status_code == 403 and CONTA not in r.headers
    anonimo = TestClient(dashboard.app)
    assert CONTA not in anonimo.get("/login").headers
    r = anonimo.get("/auth/me")
    assert r.status_code == 401 and CONTA not in r.headers


def test_r8b_sem_refresh_e_cookies_de_contas_diferentes(ab):
    """Sem refresh, o par que não concorda também encerra (e não vira o 400 de sessão de pé)."""
    a, b = ab
    c = jarro(auth=a.s["access"], dash=b.s["dashboard"])
    recusado(renova(c, b.uid), c)


def test_r10_refresh_sem_dona_conhecida_nao_renova(ab, monkeypatch):
    """`session_of` sem resposta (token desconhecido ou banco indisponível): 401, sem consumir."""
    a, b = ab
    monkeypatch.setattr("core.refresh_tokens.session_of", lambda _t: None)
    c = jarro(refresh=a.s["refresh"])
    r = renova(c, b.uid)
    assert r.status_code == 401 and r.json() == {"detail": "invalid_refresh_token"}
    assert intacto(a.s["refresh"])
    assert not {AUTH_COOKIE_NAME, DASHBOARD_COOKIE_NAME, dashboard.REFRESH_COOKIE_NAME} & nomes(c)

    c = jarro(refresh=a.s["refresh"], dash=b.s["dashboard"])
    r = renova(c)
    assert r.status_code == 401 and r.json() == {"detail": "invalid_refresh_token"}
    assert intacto(a.s["refresh"])
    assert escreve(c).status_code == 401
    assert gravados(a, b) == [0, 0]
