"""Apoio dos testes do app nativo — sessão, CSRF e limpeza de rate limit.

Módulo comum e não `tests/conftest.py` de propósito: o conftest é infraestrutura
da suíte inteira, e o que mora lá roda para todos os 8 mil testes. Isto aqui
serve a dois arquivos. Importar irmão pelo nome já é padrão na suíte
(`from _max_lines_baseline import LEGADOS`, em `tests/test_max_lines_python.py`).
"""
import pytest
from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard

# Rota de escrita usada como alvo: POST, não é isenta de CSRF, e resolve
# identidade por `resolve_dashboard_user_id` SEM gate de plano
# (`frontend/routes/push.py:7-9`). Isso mantém o teste sobre autenticação, e
# não sobre assinatura.
ALVO = "/api/push/register"
CORPO = {"token": "device-token-de-teste", "platform": "ios", "environment": "sandbox"}

UID = 987_654_321
EMAIL = "app-bearer@example.com"


async def noop_log(*args, **kwargs):
    return None


def limpa_rate_limits(bucket: str, email: str) -> None:
    """Zera o teto PERSISTENTE de `_check_auth_rate_limits`.

    O storage em memória do slowapi já é zerado por teste pelo conftest
    (`_zera_rate_limit_em_memoria`), mas este outro mora no banco e sobrevive.
    Os identificadores levam prefixo — `ip:` e `email:`
    montados em `_check_auth_rate_limits` — e o teste que só apagava o
    e-mail cru não apagava nada.
    """
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "delete from auth_rate_limits where bucket = %s and identifier = any(%s)",
            (bucket, [f"email:{email.strip().lower()}", "ip:testclient", "ip:"]),
        )
        conn.commit()


def csrf(client: TestClient) -> dict[str, str]:
    token = "test-csrf-token"
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, token)
    return {dashboard.CSRF_HEADER_NAME: token}


def req(path: str, *, host: str = "10.0.0.1", cookies: dict | None = None):
    """Request mínimo do Starlette, só com o que `chave_de_rate_limit` lê."""
    from starlette.requests import Request as StarletteRequest

    cabecalhos = []
    if cookies:
        biscoito = "; ".join(f"{k}={v}" for k, v in cookies.items())
        cabecalhos.append((b"cookie", biscoito.encode()))
    return StarletteRequest(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": cabecalhos,
            "client": (host, 1234),
            "scheme": "http",
            "server": ("testserver", 80),
        }
    )


@pytest.fixture
def sessao():
    """Sessão real emitida direto, e um cliente com o cookie jar VAZIO.

    Sem passar pelo `/auth/login`: o teto de 5/min daquela rota é da rota, não
    do que este arquivo mede, e fazer 14 logins para testar CSRF transformaria
    o rate limit no assunto. Quem exercita o login de verdade são os dois
    testes do bloco "Tokens no corpo", que é onde ele importa.

    `_issue_session_token` é a MESMA função que o login usa, então a sessão,
    o `jti` e o refresh são os de produção.
    """
    db.ensure_user(UID)
    access, jti, refresh = dashboard._issue_session_token(UID, EMAIL, req("/auth/login"))
    dash = dashboard.make_dashboard_token(
        UID, hours=dashboard.DASHBOARD_SESSION_HOURS, jti=jti
    )
    return TestClient(dashboard.app), {
        "access_token": access,
        "refresh_token": refresh,
        "dashboard_token": dash,
        "expires_in": dashboard.AUTH_COOKIE_MAX_AGE,
    }


def login_http(monkeypatch, *, como_app: bool):
    """Login de verdade pela rota, com a credencial mockada. Devolve o corpo."""
    db.ensure_user(UID)
    limpa_rate_limits("login", EMAIL)
    monkeypatch.setattr(
        db,
        "login_auth_user",
        lambda e, p: {"user_id": UID, "email": e.strip().lower(), "plan": "free"},
    )
    monkeypatch.setattr(db, "create_link_code", lambda user_id, minutes_valid: "ABC123")
    monkeypatch.setattr(dashboard, "log_auth_login_event", noop_log)

    client = TestClient(dashboard.app)
    cabecalhos = csrf(client)
    if como_app:
        cabecalhos[dashboard.APP_CLIENT_HEADER] = "app"
    r = client.post(
        "/auth/login",
        headers=cabecalhos,
        json={"email": EMAIL, "password": "seja-o-que-for"},
    )
    assert r.status_code == 200, r.text
    return r.json(), r

