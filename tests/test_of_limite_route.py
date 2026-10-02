"""GET /open-finance/{user_id}/limite e o `_enforce_bank_limit` que ela espelha.

A — a rota: valores ABSOLUTOS por estado de plano × conexões, auth e isolamento.
B — concordância: em todo estado de A, `pode_adicionar` ⟺ o `_enforce_bank_limit`
    (sem item) não levanta, com o mesmo code/message. Verde por construção hoje;
    existe para pegar uma rota futura que reimplemente a regra. Mais P1: no teto,
    o enforce com item próprio passa enquanto a rota diz False.
C — caracterização do `_enforce_bank_limit` (escrita e verde ANTES de extrair
    `_veredito_do_teto`; igual depois): prova que a extração foi mecânica.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from fastapi.testclient import TestClient

os.environ.setdefault("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())

import core.services.plan_service as plan_service
import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.open_finance as of_routes
from conftest import em_carencia, promote_to_pro
from core.services import billing_copy
from db import ensure_user
from db.connection import get_conn

FRASE_V1 = "No plano grátis você conecta 1 banco. Assine o Pro para conectar mais."
FRASE_1 = "Seu plano conecta até 1 banco. Faça upgrade pra conectar mais: /precos"
FRASE_5 = "Seu plano conecta até 5 bancos. Faça upgrade pra conectar mais: /precos"


def _semeia(user_id: int, n: int, provider: str = "pluggy", status: str = "UPDATED") -> list[str]:
    itens = [f"lim-{provider}-{status}-{user_id}-{i}" for i in range(n)]
    with get_conn() as conn, conn.cursor() as cur:
        for item in itens:
            cur.execute(
                "insert into open_finance_connections "
                "(user_id, provider, provider_item_id, status, institution_id, institution_name) "
                "values (%s, %s, %s, %s, '612', 'Nubank')",
                (user_id, provider, item, status),
            )
        conn.commit()
    return itens


def _v1(monkeypatch, gate: bool, pro: bool = False) -> None:
    monkeypatch.setenv("PLANS_V2_ENABLED", "0")
    monkeypatch.setenv("OF_BANK_LIMIT_ENABLED", "1" if gate else "0")
    monkeypatch.setenv("OF_FREE_BANK_LIMIT", "1")
    monkeypatch.setattr(of_routes, "is_pro", lambda _uid: pro)


def _enforce(user_id: int, item: str | None = None) -> None:
    asyncio.run(of_routes._enforce_bank_limit(user_id, item))


def _recusa(user_id: int, item: str | None = None) -> dict:
    with pytest.raises(HTTPException) as exc:
        _enforce(user_id, item)
    assert exc.value.status_code == 402
    return exc.value.detail


# ── C: caracterização do _enforce_bank_limit ──────────────────────────────────

def test_c_v1_desligado_passa(user_id, monkeypatch):
    _semeia(user_id, 3)
    _v1(monkeypatch, gate=False)
    _enforce(user_id)


def test_c_v1_pro_passa(user_id, monkeypatch):
    _semeia(user_id, 3)
    _v1(monkeypatch, gate=True, pro=True)
    _enforce(user_id)


def test_c_v1_ligado_no_teto_recusa_com_o_limite_da_env(user_id, monkeypatch):
    _semeia(user_id, 1)
    _v1(monkeypatch, gate=True)
    assert _recusa(user_id) == {"code": "OF_BANK_LIMIT", "limit": 1, "message": FRASE_V1}


def test_c_v1_ligado_no_teto_com_item_proprio_passa(user_id, monkeypatch):
    (item,) = _semeia(user_id, 1)
    _v1(monkeypatch, gate=True)
    _enforce(user_id, item)


def test_c_v2_no_teto_recusa(user_id):
    promote_to_pro(user_id, "essencial")
    _semeia(user_id, 1)
    assert _recusa(user_id) == {
        "code": "OF_BANK_LIMIT", "limit": 1,
        "message": "Seu plano conecta até 1 banco. Faça upgrade pra conectar mais: /precos",
    }


def test_c_v2_limite_zero_com_item_proprio_passa(user_id):
    em_carencia(user_id)
    (item,) = _semeia(user_id, 1)
    _enforce(user_id, item)  # P1 vem ANTES do limite 0
    assert _recusa(user_id)["limit"] == 0  # e sem item o limite 0 recusa


# ── A: a rota ─────────────────────────────────────────────────────────────────

def _auth(client: TestClient, user_id: int) -> None:
    client.cookies.set(dashboard.AUTH_COOKIE_NAME, dashboard._make_jwt(user_id, "of-lim@t.com"))
    client.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, dashboard.make_dashboard_token(user_id, hours=1))


def _limite(user_id: int, como: int | None = None):
    client = TestClient(dashboard.app)
    _auth(client, user_id if como is None else como)
    return client.get(f"/open-finance/{user_id}/limite")


def _ilimitado(monkeypatch):
    real = plan_service.get_user_limits
    monkeypatch.setattr(plan_service, "get_user_limits", lambda uid: {**real(uid), "of_banks_max": None})


# nome → (montagem, (of_banks_max, em_uso, pode_adicionar, code, message))
ESTADOS = {
    "carencia": (lambda u, m: em_carencia(u),
                 (0, 0, False, "OF_BANK_LIMIT", billing_copy.OPEN_FINANCE_EM_CARENCIA)),
    "essencial_0": (lambda u, m: promote_to_pro(u, "essencial"), (1, 0, True, None, None)),
    "essencial_1": (lambda u, m: (promote_to_pro(u, "essencial"), _semeia(u, 1)),
                    (1, 1, False, "OF_BANK_LIMIT", FRASE_1)),
    "essencial_2_downgrade": (lambda u, m: (promote_to_pro(u, "essencial"), _semeia(u, 2)),
                              (1, 2, False, "OF_BANK_LIMIT", FRASE_1)),
    "plus_1": (lambda u, m: (promote_to_pro(u, "pro"), _semeia(u, 1)), (2, 1, True, None, None)),
    "pro_max_4": (lambda u, m: (promote_to_pro(u, "pro_max"), _semeia(u, 4)), (5, 4, True, None, None)),
    "pro_max_5": (lambda u, m: (promote_to_pro(u, "pro_max"), _semeia(u, 5)),
                  (5, 5, False, "OF_BANK_LIMIT", FRASE_5)),
    "ilimitado_3": (lambda u, m: (promote_to_pro(u, "essencial"), _semeia(u, 3), _ilimitado(m)),
                    (None, 3, True, None, None)),
    "essencial_paused": (lambda u, m: (promote_to_pro(u, "essencial"), _semeia(u, 1, status="PAUSED")),
                         (1, 0, True, None, None)),
    "essencial_mock": (lambda u, m: (promote_to_pro(u, "essencial"), _semeia(u, 1, provider="mock_pluggy")),
                       (1, 0, True, None, None)),
    "v1_desligado_3": (lambda u, m: (promote_to_pro(u), _semeia(u, 3), _v1(m, gate=False)),
                       (None, 3, True, None, None)),
    "v1_ligado_1": (lambda u, m: (promote_to_pro(u), _semeia(u, 1), _v1(m, gate=True)),
                    (1, 1, False, "OF_BANK_LIMIT", FRASE_V1)),
    "v1_pro_1": (lambda u, m: (promote_to_pro(u), _semeia(u, 1), _v1(m, gate=True, pro=True)),
                 (None, 1, True, None, None)),
}


@pytest.mark.parametrize("estado", ESTADOS)
def test_a_rota_por_estado(user_id, monkeypatch, estado):
    montagem, (teto, em_uso, pode, code, message) = ESTADOS[estado]
    montagem(user_id, monkeypatch)
    r = _limite(user_id)
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "of_banks_max": teto, "em_uso": em_uso,
                        "pode_adicionar": pode, "code": code, "message": message}


def test_a_anonimo_401(user_id):
    promote_to_pro(user_id, "essencial")
    assert TestClient(dashboard.app).get(f"/open-finance/{user_id}/limite").status_code == 401


def test_a_sem_plano_402_do_gate_comum_sem_corpo_do_teto(user_id):
    # o teto vira 200 + pode_adicionar=False (estado "carencia"); sem plano, o 402 é do gate de dados
    r = _limite(user_id)
    assert r.status_code == 402, r.text
    assert r.json() == {"detail": {"error": "subscription_required"}}


def test_a_sessao_de_outro_usuario_403_sem_dado_dele(user_id):
    outro = int(uuid.uuid4().int % 10_000_000_000)
    ensure_user(outro)
    promote_to_pro(user_id, "essencial")
    promote_to_pro(outro, "pro_max")
    _semeia(outro, 3)
    r = _limite(outro, como=user_id)
    assert r.status_code == 403, r.text
    assert "em_uso" not in r.text and "of_banks_max" not in r.text and str(outro) not in r.text


def test_a_bearer_do_app_sem_cookie(user_id):
    promote_to_pro(user_id, "essencial")
    token = dashboard.make_dashboard_token(user_id, hours=1)
    r = TestClient(dashboard.app).get(
        f"/open-finance/{user_id}/limite",
        headers={"Authorization": f"Bearer {token}", dashboard.APP_CLIENT_HEADER: "app"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["pode_adicionar"] is True


def test_a_isolamento_cada_um_ve_so_o_seu(user_id):
    b = int(uuid.uuid4().int % 10_000_000_000)
    ensure_user(b)
    promote_to_pro(user_id, "essencial")
    promote_to_pro(b, "essencial")
    _semeia(user_id, 1)
    ra, rb = _limite(user_id).json(), _limite(b).json()
    assert (ra["em_uso"], ra["pode_adicionar"]) == (1, False)
    assert (rb["em_uso"], rb["pode_adicionar"]) == (0, True)


# ── B: concordância rota × enforce ────────────────────────────────────────────

@pytest.mark.parametrize("estado", ESTADOS)
def test_b_rota_concorda_com_o_enforce(user_id, monkeypatch, estado):
    ESTADOS[estado][0](user_id, monkeypatch)
    body = _limite(user_id).json()
    if body["pode_adicionar"]:
        _enforce(user_id)
    else:
        detail = _recusa(user_id)
        assert (detail["code"], detail["message"]) == (body["code"], body["message"])


def test_b_p1_reconexao_passa_no_teto(user_id):
    promote_to_pro(user_id, "essencial")
    (item,) = _semeia(user_id, 1)
    assert _limite(user_id).json()["pode_adicionar"] is False
    _enforce(user_id, item)
