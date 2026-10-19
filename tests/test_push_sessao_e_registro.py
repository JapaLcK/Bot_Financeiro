"""PR 1 do push no app (servidor): quem recebe (D4), a baixa com dono e o tópico (D3).

Caminho real: login do app (`_issue_session_token`), `POST /api/push/register` pela
rota e `push_service.send_push_to_user` de verdade; só o APNs é espião
(`tests/_apoio_push.py`).

CONTROLES (medidos em 2026-10-18; remeça se mexer no código):
  Negativo D4: tirar o `join auth_sessions` e o `exists (... auth_refresh_tokens)` de
      `db.push.list_push_tokens` → b, c, d, e, f, g vermelhos; a verde (positivo).
  Negativo baixa: tirar o `and user_id = %s` do DELETE → o teste da baixa alheia vermelho;
      a baixa do próprio dono verde (positivo).
  Negativo D3: tirar "DeviceTokenNotForTopic" do conjunto que apaga → o caso 400 apaga vermelho;
      TopicDisallowed/BadTopic/200 (mantêm) e a baixa de BadDeviceToken verdes.
"""
import pytest
from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import sessao_de, sql
from _apoio_push import apns, conta_paga, envia, registra

TOK = "tok-aparelho-1"


def _token_existe(token):
    with db.get_conn() as c, c.cursor() as cur:
        cur.execute("select 1 from push_tokens where token = %s", (token,))
        return cur.fetchone() is not None


def _ativa(uid, monkeypatch):
    espiao = apns(monkeypatch)
    conta_paga(uid)
    return espiao, registra(uid, TOK)


# ── D4: só sessão ativa com refresh utilizável recebe ────────────────────────

def test_a_sessao_ativa_recebe(user_id, monkeypatch):
    espiao, _ = _ativa(user_id, monkeypatch)
    assert envia(user_id) == 1 and espiao.tokens() == [TOK]


def test_b_sessao_revogada_nao_recebe(user_id, monkeypatch):
    espiao, s = _ativa(user_id, monkeypatch)
    sql("update auth_sessions set revoked_at = now() where jti = %s", s["jti"])
    assert envia(user_id) == 0 and espiao.chamadas == []


@pytest.mark.parametrize("mudanca", [
    "expires_at = now() - interval '1 minute'",   # c: refresh vencido
    "used_at = now()",                            # d: usado e sem sucessor
    "revoked_at = now()",                         # d: revogado
])
def test_c_d_refresh_inutilizavel_nao_recebe(user_id, monkeypatch, mudanca):
    espiao, s = _ativa(user_id, monkeypatch)
    sql(f"update auth_refresh_tokens set {mudanca} where session_jti = %s", s["jti"])
    assert envia(user_id) == 0 and espiao.chamadas == []


def test_refresh_rotacionado_continua_recebendo(user_id, monkeypatch):
    """Positivo do d: o refresh usado TEM sucessor (rotação real) → segue entregável."""
    espiao, s = _ativa(user_id, monkeypatch)
    r = TestClient(dashboard.app).post(
        "/auth/refresh", json={}, headers={
            "Authorization": f"Bearer {s['refresh']}", dashboard.APP_CLIENT_HEADER: "app",
            "Content-Type": "application/json"})
    assert r.status_code == 200, r.text
    assert envia(user_id) == 1


def test_e_token_sem_jti_nao_recebe(user_id, monkeypatch):
    espiao = apns(monkeypatch)
    conta_paga(user_id)
    sessao_de(user_id)
    db.register_push_token(user_id, TOK)   # registro antigo: sem sessão
    assert envia(user_id) == 0 and espiao.chamadas == []


def test_f_token_de_a_com_jti_de_b_nao_recebe(user_id, monkeypatch):
    espiao = apns(monkeypatch)
    conta_paga(user_id)
    outro = user_id + 1
    db.ensure_user(outro)
    conta_paga(outro)
    db.register_push_token(user_id, TOK, session_jti=sessao_de(outro)["jti"])
    assert envia(user_id) == 0 and espiao.chamadas == []


def test_g_logout_do_app_para_o_push(user_id, monkeypatch):
    espiao, s = _ativa(user_id, monkeypatch)
    assert envia(user_id) == 1
    r = TestClient(dashboard.app).post("/auth/logout", headers={
        "Authorization": f"Bearer {s['access']}", dashboard.APP_CLIENT_HEADER: "app",
        "Content-Type": "application/json"})
    assert r.status_code == 200, r.text
    assert envia(user_id) == 0 and len(espiao.chamadas) == 1


def test_logout_e_novo_login_no_mesmo_aparelho_volta_a_receber(user_id, monkeypatch):
    """O re-registro do MESMO token com a sessão nova troca o jti (`on conflict`)."""
    espiao, s = _ativa(user_id, monkeypatch)
    sql("update auth_sessions set revoked_at = now() where jti = %s", s["jti"])
    assert envia(user_id) == 0
    registra(user_id, TOK)   # sessão nova, mesmo aparelho
    assert envia(user_id) == 1 and espiao.tokens() == [TOK]


# ── baixa com dono ───────────────────────────────────────────────────────────

def _baixa(uid, token):
    return TestClient(dashboard.app).post(
        "/api/push/unregister", headers={"Authorization": f"Bearer {sessao_de(uid)['access']}"},
        json={"token": token})


def test_baixa_de_outro_usuario_responde_200_e_nao_apaga(user_id, monkeypatch):
    _ativa(user_id, monkeypatch)
    outro = user_id + 1
    db.ensure_user(outro)
    conta_paga(outro)
    r = _baixa(outro, TOK)
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert _token_existe(TOK)


def test_baixa_do_proprio_dono_apaga(user_id, monkeypatch):
    _ativa(user_id, monkeypatch)
    r = _baixa(user_id, TOK)
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert not _token_existe(TOK)


# ── D3: tópico ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("status,reason,apaga", [
    (400, "DeviceTokenNotForTopic", True),
    (400, "BadDeviceToken", True),
    (410, "Unregistered", True),
    (400, "TopicDisallowed", False),
    (400, "BadTopic", False),
    (400, "MissingTopic", False),
    (200, "", False),
])
def test_so_token_do_aparelho_errado_e_apagado(user_id, monkeypatch, status, reason, apaga):
    espiao, _ = _ativa(user_id, monkeypatch)
    espiao.respostas[TOK] = (status, reason)
    envia(user_id)
    assert _token_existe(TOK) is (not apaga)


def test_sem_apns_topic_nao_ha_http_e_devolve_zero(user_id, monkeypatch):
    espiao = apns(monkeypatch, topico=None)
    conta_paga(user_id)
    registra(user_id, TOK)
    assert envia(user_id) == 0 and espiao.chamadas == [] and _token_existe(TOK)


def test_topico_vai_no_cabecalho(user_id, monkeypatch):
    espiao, _ = _ativa(user_id, monkeypatch)
    envia(user_id)
    assert espiao.chamadas[0]["headers"]["apns-topic"] == "com.pigbankai.mobile"
