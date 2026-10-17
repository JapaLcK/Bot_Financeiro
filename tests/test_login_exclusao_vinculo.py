"""Exclusão agendada no /d/ e no verify-login (#989).

#989: `GET /d/{code}` e `POST /auth/mfa/verify-login` emitiam sessão para conta com
exclusão agendada (o callback e o `/auth/login` já recusavam). Agora a checagem vem
ANTES do desafio de MFA no `/d/` e ANTES de consumir o TOTP no verify-login.

Banco e rotas são os de verdade. Tirar a guarda de exclusão do `/d/` deixa o x1
vermelho; tirar a do verify-login deixa o x3 vermelho. Os casos sem exclusão
(x2, x4 e o fim do x3) são o controle positivo: o caminho legítimo segue verde.
"""
import pytest
from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard
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
    uid, _, _ = _conta(mfa)
    db.schedule_account_deletion(uid, SENHA)
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
    db.schedule_account_deletion(a, SENHA)
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
    db.schedule_account_deletion(uid, SENHA)        # a conta agenda a exclusão em outra aba

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
    db.schedule_account_deletion(a, SENHA)
    antes_a, antes_b = _sessoes(a), _sessoes(b)

    cruzado = _verifica(cb, desafio_b, segredo_a)   # TOTP da A no desafio da B
    assert cruzado.status_code == 400 and cruzado.json()["code"] == "mfa_code_invalid"
    assert _sessoes(b) == antes_b

    assert _verifica(cb, desafio_b, segredo_b).status_code == 200
    assert _sessoes(b) == antes_b + 1
    assert _verifica(ca, desafio_a, segredo_a).json()["code"] == "conta_em_exclusao"
    assert _sessoes(a) == antes_a
