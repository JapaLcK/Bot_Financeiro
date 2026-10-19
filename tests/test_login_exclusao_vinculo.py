"""Exclusão agendada e vínculo Google antes do código (#989, #988).

#989: `GET /d/{code}` e `POST /auth/mfa/verify-login` emitiam sessão para conta com
exclusão agendada (o callback e o `/auth/login` já recusavam). Agora a checagem vem
ANTES do desafio de MFA no `/d/` e ANTES de consumir o TOTP no verify-login.

#988: o auto-vínculo por e-mail (Google, Apple) gravava `auth_identities` antes de
a conta provar o segundo fator e mesmo para conta recusada por exclusão. Agora só
grava se a conta não tem MFA e não está agendada (`_vincular_se_permitido`).
Os casos da Apple estão em `test_auth_apple_troca.py` (a6, a6c, a6d).

Banco e rotas são os de verdade. Tirar a guarda de exclusão do `/d/` deixa o x1
vermelho; tirar a do verify-login deixa o x3 vermelho. Os casos sem exclusão
(x2, x4 e o fim do x3) são o controle positivo: o caminho legítimo segue verde.
Tirar a checagem de MFA do helper `_vincular_se_permitido` deixa o y1 vermelho; tirar a de
exclusão, o y3. O y4 e o y5 são o controle positivo: conta comum segue vinculando e
vínculo prévio segue valendo.
"""
import pytest
from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import google_de_mentira, login_google, marca_exclusao
from test_google_web_mfa import (
    SENHA, _conta, _desafio, _eventos, _logins_ok, _sem_sessao, _sessoes, _verifica, _web,
)


def _desafios(uid: int) -> int:
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) as n from mfa_login_challenges where user_id = %s", (uid,))
        return int(cur.fetchone()["n"])


def _desagenda(uid: int) -> None:
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "update auth_accounts set deletion_status = null, deletion_scheduled_for = null"
            " where user_id = %s", (uid,))
        conn.commit()


def _sub(email: str) -> str:
    return f"sub-{email}"          # o mesmo que `google_de_mentira` devolve


# ── #989: GET /d/{code} ──────────────────────────────────────────────────────

@pytest.mark.parametrize("mfa", [False, True], ids=["sem_mfa", "com_mfa"])
def test_x1_d_de_conta_agendada_recusa_sem_sessao_sem_desafio(mfa):
    uid, _, segredo = _conta(mfa)
    marca_exclusao(uid, SENHA, segredo)
    codigo = db.create_dashboard_session(uid)      # depois: o agendamento apaga os antigos
    antes, desafios = _sessoes(uid), _desafios(uid)

    r = TestClient(dashboard.app).get(f"/d/{codigo}", follow_redirects=False)

    assert r.status_code == 403, r.text
    _sem_sessao(r, uid, antes)
    assert _logins_ok(uid) == 0
    assert _desafios(uid) == desafios, "criou desafio de MFA para conta recusada"
    assert _eventos(uid, "mfa_pending") == 0


def test_x2_d_isolamento_conta_agendada_nao_afeta_outra():
    """Controle positivo, não isolamento: o /d/ da B segue funcionando com a A agendada
    (sobrevive à remoção da guarda). O teste de isolamento é o x4."""
    a, _, _ = _conta(False)
    b, _, _ = _conta(False)
    marca_exclusao(a, SENHA)
    antes_b = _sessoes(b)
    r = TestClient(dashboard.app).get(f"/d/{db.create_dashboard_session(b)}", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/app"
    assert _sessoes(b) == antes_b + 1
    assert _sessoes(a) == 0


# ── #989: POST /auth/mfa/verify-login ────────────────────────────────────────

def test_x3_verify_login_de_conta_agendada_depois_do_desafio_recusa_e_nao_queima_o_totp(monkeypatch):
    uid, email, segredo = _conta(True)
    client, r = _web(monkeypatch, email)
    desafio = _desafio(r)                           # login com MFA: desafio vivo
    antes = _sessoes(uid)
    marca_exclusao(uid, SENHA, segredo)             # a conta marca a exclusão em outra aba

    fim = _verifica(client, desafio, segredo)       # TOTP correto

    assert fim.status_code == 400, fim.text
    assert fim.json()["code"] == "conta_em_exclusao"
    assert "exclusão" in fim.json()["detail"]
    _sem_sessao(fim, uid, antes)
    assert _logins_ok(uid) == 0

    # A checagem veio ANTES do consumo: sem o agendamento, o mesmo desafio e o
    # mesmo código ainda valem.
    _desagenda(uid)
    ok = _verifica(client, desafio, segredo)
    assert ok.status_code == 200, ok.text
    assert _sessoes(uid) == antes + 1


def test_x4_verify_login_isolamento_entre_contas(monkeypatch):
    """Com a A agendada, o desafio da B (positivo) conclui; o código da A não abre o da B."""
    a, email_a, segredo_a = _conta(True)
    b, email_b, segredo_b = _conta(True)
    ca, ra = _web(monkeypatch, email_a)
    cb, rb = _web(monkeypatch, email_b)
    desafio_a, desafio_b = _desafio(ra), _desafio(rb)
    marca_exclusao(a, SENHA, segredo_a)
    antes_a, antes_b = _sessoes(a), _sessoes(b)

    cruzado = _verifica(cb, desafio_b, segredo_a)   # TOTP da A no desafio da B
    assert cruzado.status_code == 400 and cruzado.json()["code"] == "mfa_code_invalid"
    assert _sessoes(b) == antes_b

    assert _verifica(cb, desafio_b, segredo_b).status_code == 200
    assert _sessoes(b) == antes_b + 1
    assert _verifica(ca, desafio_a, segredo_a).json()["code"] == "conta_em_exclusao"
    assert _sessoes(a) == antes_a


# ── #988: Google (web, app antigo, nativo) ───────────────────────────────────

APPS = pytest.mark.parametrize("app", [0, 1, 2], ids=["web", "app_antigo", "nativo"])


@APPS
def test_y1_conta_com_mfa_sem_vinculo_nao_ganha_vinculo(monkeypatch, app):
    uid, email, _ = _conta(True)
    google_de_mentira(monkeypatch, email)
    login_google(app)
    assert db.find_user_by_google_sub(_sub(email)) is None


def test_y2_web_com_mfa_continua_sem_vinculo_depois_do_verify_login(monkeypatch):
    uid, email, segredo = _conta(True)
    client, r = _web(monkeypatch, email)
    assert _verifica(client, _desafio(r), segredo).status_code == 200
    assert db.find_user_by_google_sub(_sub(email)) is None


@APPS
def test_y3_conta_agendada_e_recusada_sem_vinculo(monkeypatch, app):
    uid, email, _ = _conta(False)
    marca_exclusao(uid, SENHA)
    google_de_mentira(monkeypatch, email)
    _, r = login_google(app)
    destino = "pigbank://auth?erro=conta_em_exclusao" if app == 2 else "/?google_error="
    assert r.status_code == 302 and r.headers["location"].startswith(destino), r.headers["location"]
    assert db.find_user_by_google_sub(_sub(email)) is None


@APPS
def test_y4_positivo_conta_sem_mfa_e_sem_exclusao_continua_vinculando(monkeypatch, app):
    """A guarda não recusa tudo: o auto-vínculo de conta comum segue gravando."""
    uid, email, _ = _conta(False)
    google_de_mentira(monkeypatch, email)
    _, r = login_google(app)
    assert r.status_code == 302 and "erro" not in r.headers["location"], r.headers["location"]
    assert db.find_user_by_google_sub(_sub(email)) == uid


def test_y5_positivo_conta_com_mfa_e_vinculo_previo_continua_pedindo_o_codigo(monkeypatch):
    uid, email, segredo = _conta(True)
    db.link_google_identity(uid, _sub(email), email)
    client, r = _web(monkeypatch, email)
    assert _verifica(client, _desafio(r), segredo).status_code == 200
    assert db.find_user_by_google_sub(_sub(email)) == uid
