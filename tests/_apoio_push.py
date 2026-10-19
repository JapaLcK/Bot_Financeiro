"""Apoio dos testes do push do app: a ÚNICA borda trocada é o APNs.

Conta, sessão (a de produção, `_issue_session_token`), registro (a rota
`POST /api/push/register`) e envio (`push_service.send_push_to_user` real) são
de verdade; só o `provider token` (JWT assinado com a .p8) e o `httpx.Client`
que sairia para a Apple são trocados, e as envs `APNS_*` vão por monkeypatch.
"""
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

import frontend.finance_bot_websocket_custom as dashboard
from conftest import promote_to_pro
from core.services import push_service
from _apoio_auth_app import ALVO, sessao_de

TOPICO = "com.pigbankai.mobile"


class Apns:
    """O APNs de mentira: `chamadas` (token, corpo, cabeçalhos) e `respostas`
    (token → (status, reason)); sem entrada, 200."""

    def __init__(self):
        self.chamadas: list[dict] = []
        self.respostas: dict[str, tuple[int, str]] = {}

    def tokens(self) -> list[str]:
        return [c["token"] for c in self.chamadas]


def apns(monkeypatch, *, topico: str | None = TOPICO) -> Apns:
    for k, v in (("APNS_KEY_ID", "KID"), ("APNS_TEAM_ID", "TEAM"), ("APNS_AUTH_KEY", "pem")):
        monkeypatch.setenv(k, v)
    if topico is None:
        monkeypatch.delenv("APNS_TOPIC", raising=False)
    else:
        monkeypatch.setenv("APNS_TOPIC", topico)
    monkeypatch.setattr(push_service, "_provider_token", lambda: "jwt")
    espiao = Apns()

    class _Resp:
        def __init__(self, status, reason):
            self.status_code, self._reason = status, reason

        def json(self):
            return {"reason": self._reason}

    class _Client:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, headers=None, content=None):
            token = url.rsplit("/", 1)[1]
            espiao.chamadas.append({"token": token, "url": url, "headers": headers or {},
                                    "body": json.loads(content)})
            return _Resp(*espiao.respostas.get(token, (200, "")))

    monkeypatch.setattr(push_service, "httpx", SimpleNamespace(Client=_Client))
    return espiao


def registra(uid: int, token: str, *, sessao: dict | None = None):
    """Registra `token` pela ROTA, com o Bearer de uma sessão da conta (a dada,
    ou uma nova). Devolve a sessão usada."""
    sessao = sessao or sessao_de(uid)
    r = TestClient(dashboard.app).post(
        ALVO, headers={"Authorization": f"Bearer {sessao['access']}"},
        json={"token": token, "platform": "ios", "environment": "sandbox"})
    assert r.status_code == 200, r.text
    return sessao


def conta_paga(uid: int) -> int:
    """Conta com direito de uso e credencial (a `user_id` do conftest é só `users`)."""
    promote_to_pro(uid)
    return uid


def envia(uid: int) -> int:
    return push_service.send_push_to_user(uid, "t", "c")

