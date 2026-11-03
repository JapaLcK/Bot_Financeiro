"""#657 C3 (PR-8 do grupo D), os sites de conta e auth: a falha grava o dono na coluna `user_id`
de `system_event_logs`, só com tipo + sqlstate (D6), sem `exc_info` e sem o texto cru (o `DETAIL`
do psycopg, que no `create_refresh_token` traz o hash do token e no `create_dashboard_session` o
código do link). E o desfecho de hoje NÃO muda: o que nega continua negando (replay de refresh,
trial), o que engole continua engolindo (MFA devolve os códigos de backup, logout limpa os
cookies, reset conclui, magic link redireciona).

Chamada direta ou rota real, Postgres real, o `_DashboardHandler` do root. Nenhum site do C3 está
em laço por usuário (são por requisição ou por evento): o `FALHA_NO` não se aplica.
T-A1 auth por HTTP (logout, reset, magic link); T-A2 MFA; T-A3 trial; T-A4 link do dashboard e
replay de refresh. Todos rodam A e B com exportação e exclusão reais (`_a_e_b`/`_isola_e_apaga`).
O 503 do checkout quando a elegibilidade falha é
`test_billing_checkout.py::test_checkout_v2_falha_fechada_se_elegibilidade_indisponivel`.

CONTROLES (CLAUDE.md §3), medidos no PR: tirar o `extra` do logout → T-A1 logout vermelho; tirar o
`as exc` do MFA (`NameError` no `except`) → T-A2 vermelho (os códigos se perdem); `exc_info=True`
de volta no magic link → T-A1 magic vermelho (`traceback` no `details`).
"""
from __future__ import annotations

import os

import pyotp
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

import core.refresh_tokens as refresh_tokens
import core.sessions as sessions
import db
import db.mfa as mfa
import db.plans as plans
import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import sessao_de
from _log_dono_helpers import (  # noqa: F401 (banco_real é fixture autouse)
    _isola_e_apaga, _levanta, _limpa, _linhas, _marca, _marcador, _uid, _uma, banco_real)
from core.dashboard_links import build_dashboard_link
from db.connection import get_conn

os.environ.setdefault("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())
CAUSA_PG = "causa=UniqueViolation sqlstate=23505"
MONOLITO = "frontend.finance_bot_websocket_custom"


def _linha(marca: int, uid: int, nivel: str, source: str, prefixo: str, *proibidos: str,
           causa: str = CAUSA_PG) -> dict:
    linha = _uma(_linhas(marca, "user_id = %s", (uid,)), f"logger.{nivel}", source, prefixo)
    assert linha["user_id"] == uid and causa in linha["message"], linha
    _limpa(linha, *proibidos)
    return linha


def _a_e_b(caso, novo=_uid) -> None:
    """Roda `caso(uid, marca) -> linha` (ou uma tupla de linhas) para A e para B (cada conta
    criada por `novo`), e prova o isolamento real: TODA linha de A sai no ZIP de A e não no de B,
    e some na exclusão de A, deixando a de B."""
    a, b, marca = novo(), novo(), _marca()
    ids = {}
    for uid in (a, b):
        linhas = caso(uid, marca)
        ids[uid] = [x["id"] for x in (linhas if isinstance(linhas, tuple) else (linhas,))]
    _isola_e_apaga(a, b, set(ids[a]), set(ids[b]), marca)


# ── T-A1 auth por HTTP ───────────────────────────────────────────────────────

def test_ta1_logout_com_revogacao_falhando_limpa_os_cookies(monkeypatch):
    """O `user_id_raw` do logout é `str` (o `sub` do JWT): a coluna tem de casar com o int de A."""
    def caso(uid, marca):
        mark = _marcador()
        monkeypatch.setattr(dashboard, "revoke_session", _levanta(mark))
        r = TestClient(dashboard.app).post("/auth/logout", headers={
            "Authorization": f"Bearer {sessao_de(uid)['access']}", dashboard.APP_CLIENT_HEADER: "app",
            "Content-Type": "application/json"})
        assert r.status_code == 200, r.text
        assert any(c.startswith("auth_token=") and "Max-Age=0" in c for c in r.headers.get_list("set-cookie"))
        return _linha(marca, uid, "warning", MONOLITO, "logout: falha ao revogar", mark)
    _a_e_b(caso)


def test_ta1_reset_com_revogacao_falhando_conclui_e_audita(monkeypatch):
    monkeypatch.setattr(db, "consume_password_reset_token", lambda token, senha: int(token))

    def caso(uid, marca):
        mark = _marcador()
        monkeypatch.setattr(sessions, "revoke_other_sessions", _levanta(mark))
        c = TestClient(dashboard.app)
        c.cookies.set(dashboard.CSRF_COOKIE_NAME, "csrf-reset")
        r = c.post("/auth/reset-password", headers={dashboard.CSRF_HEADER_NAME: "csrf-reset"},
                   json={"token": str(uid), "new_password": "senha-forte-123"})
        assert r.status_code == 200, r.text
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("select 1 from audit_events where user_id = %s and event = 'password_reset_completed'",
                        (uid,))
            assert cur.fetchone(), "a auditoria do reset não foi gravada"
            conn.commit()
        return _linha(marca, uid, "warning", MONOLITO, "reset_revoke_sessions", mark)
    _a_e_b(caso)


def test_ta1_magic_link_com_refresh_falhando_redireciona_com_o_dashboard_token(monkeypatch):
    def caso(uid, marca):
        mark, code = _marcador(), db.create_dashboard_session(uid)
        monkeypatch.setattr(refresh_tokens, "create_refresh_token", _levanta(f"{mark} {code}"))
        r = TestClient(dashboard.app).get(f"/d/{code}", follow_redirects=False)
        assert r.status_code == 302, r.text
        assert "dashboard_token=" in r.headers["set-cookie"]
        return _linha(marca, uid, "warning", MONOLITO, "magic link:", mark, code)
    _a_e_b(caso)


# ── T-A2 MFA ─────────────────────────────────────────────────────────────────

def test_ta2_mfa_com_a_marca_falhando_devolve_os_codigos(monkeypatch):
    def caso(uid, marca):
        mark = _marcador()
        segredo = mfa.setup_secret(uid, db.get_auth_user(uid)["email"])["secret"]
        monkeypatch.setattr(mfa, "mark_mfa_onboarding_shown", _levanta(mark))
        codigos = mfa.verify_and_enable(uid, pyotp.TOTP(segredo).now())
        assert len(codigos) == 10 and mfa.get_mfa_status(uid)["enabled"]
        return _linha(marca, uid, "warning", "db.mfa", "mfa onboarding mark falhou", mark)
    _a_e_b(caso)


# ── T-A3 trial: o erro tipado continua, com a causa ──────────────────────────

@pytest.mark.parametrize("funcao, erro", [
    (plans.claim_trial_for_user, plans.TrialClaimError),
    (plans.motivo_trial_indisponivel, plans.TrialEligibilityError),
], ids=["claim", "motivo"])
def test_ta3_trial_com_o_banco_falhando_levanta_o_erro_tipado(monkeypatch, funcao, erro):
    def caso(uid, marca):
        mark = _marcador()
        monkeypatch.setattr(plans, "get_conn", _levanta(mark))
        with pytest.raises(erro) as e:
            funcao(uid)
        assert type(e.value.__cause__).__name__ == "UniqueViolation"
        return _linha(marca, uid, "warning", "db.plans", funcao.__name__, mark)
    _a_e_b(caso)


# ── T-A4 link do dashboard e replay de refresh ───────────────────────────────

def test_ta4_link_falhando_devolve_none_e_replay_revoga_com_o_dono(monkeypatch):
    a, b, mark = _uid(), _uid(), _marcador()
    jti = sessions.create_session(a, ip="1.1.1.1")
    token = refresh_tokens.create_refresh_token(a, jti)
    marca = _marca()
    assert refresh_tokens.consume_refresh_token(token) is not None
    assert refresh_tokens.consume_refresh_token(token) is None            # replay: nega
    assert sessions.get_active_session(jti) is None                       # e revoga a sessão
    replay = _linha(marca, a, "warning", "core.refresh_tokens", "[refresh] possível replay", token,
                   causa="")  # sem exceção: o replay é detectado, não falha

    monkeypatch.setattr(db, "create_dashboard_session", _levanta(mark))
    assert build_dashboard_link(b) is None
    link_b = _linha(marca, b, "error", "core.dashboard_links", "Falha ao gerar link seguro", mark)
    _isola_e_apaga(a, b, {replay["id"]}, link_b["id"], marca)
