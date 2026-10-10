"""DELETE /ai/pending: cancela a pendência do chat IA SÓ do usuário da sessão.

TestClient e Postgres reais. Controle negativo do B1: tirar o `where user_id`
do delete em `db/ai_chat.py:clear_pending_action` deixa B1 vermelho.
"""
import db
from db.connection import get_conn
from test_senha_obrigatoria import H, _com_senha, conta_paga_sem_credencial


def _conta():
    uid, _, client = conta_paga_sem_credencial()
    _com_senha(uid)
    return uid, client


def _arma(uid, resumo):
    assert db.ai_set_pending_action(uid, "delete_all_launches", {}, resumo)


def test_b1_isolamento_so_a_pendencia_da_sessao():
    a, ca = _conta()
    b, _ = _conta()
    _arma(a, "apagar tudo de A")
    _arma(b, "apagar tudo de B")
    r = ca.request("DELETE", f"/ai/pending?user_id={b}", json={"user_id": b}, headers=H)
    assert r.status_code == 200 and r.json() == {"cancelada": "apagar tudo de A"}, r.text
    assert db.ai_get_pending_action(a) is None
    assert db.ai_get_pending_action(b) is not None


def test_b2_idempotente_sem_pendencia():
    _, c = _conta()
    for _ in range(2):
        r = c.delete("/ai/pending", headers=H)
        assert r.status_code == 200 and r.json() == {"cancelada": None}, r.text


def test_b3_vencida_devolve_null_e_apaga():
    uid, c = _conta()
    _arma(uid, "velha")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("update ai_pending_actions set created_at = now() - interval '11 minutes'"
                    " where user_id = %s", (uid,))
        conn.commit()
    r = c.delete("/ai/pending", headers=H)
    assert r.status_code == 200 and r.json() == {"cancelada": None}, r.text
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) as n from ai_pending_actions where user_id = %s", (uid,))
        assert cur.fetchone()["n"] == 0


def test_b6_sem_sessao_401():
    from test_quiz_conta import _navegador
    r = _navegador().delete("/ai/pending", headers=H)
    assert r.status_code == 401, r.text


def _pendencia_existe(uid):
    return db.ai_get_pending_action(uid) is not None


def test_b7_bearer_sem_cookie_cancela():
    from _apoio_auth_app import sessao_de
    from fastapi.testclient import TestClient
    import frontend.finance_bot_websocket_custom as dashboard
    uid, _ = _conta()
    _arma(uid, "apagar tudo")
    r = TestClient(dashboard.app).request(  # jar vazio: sem cookie nenhum
        "DELETE", "/ai/pending", json={},
        headers={"Authorization": f"Bearer {sessao_de(uid)['access']}"},
    )
    assert r.status_code == 200 and r.json() == {"cancelada": "apagar tudo"}, r.text
    assert not _pendencia_existe(uid)


def test_b8_cookie_sem_csrf_403_e_pendencia_sobrevive():
    uid, c = _conta()
    _arma(uid, "apagar tudo")
    r = c.request("DELETE", "/ai/pending", json={})  # cookie de sessão, sem header de CSRF
    assert r.status_code == 403, r.text
    assert _pendencia_existe(uid)


def test_b9_cota_esgotada_a_pendencia_viva_cancela_mesmo_assim():
    from core.services.plan_service import ai_chat_allowed, ai_monthly_limit_for
    uid, c = _conta()
    _arma(uid, "apagar tudo")
    for _ in range(ai_monthly_limit_for(uid)):
        db.ai_increment_usage(uid)
    assert ai_chat_allowed(uid) is False  # o gate de plano/cota fecharia aqui
    r = c.delete("/ai/pending", headers=H)
    assert r.status_code == 200 and r.json() == {"cancelada": "apagar tudo"}, r.text
    assert not _pendencia_existe(uid)


def test_b10_aviso_usa_a_linha_apagada_nao_uma_leitura_anterior(monkeypatch):
    # Corrida: se o clear LÊ antes de apagar, outra requisição troca a pendência no meio.
    # O aviso tem de descrever a linha que o DELETE apagou (o summary devolvido).
    import db.pending as pending
    uid, _ = _conta()
    assert db.ai_set_pending_action(uid, "recategorize_launch", {}, "recategorizar")
    real, troca = pending.ler_para_aviso, [True]

    def le_e_troca(cur, user_id, **kw):
        lido = real(cur, user_id, **kw)
        if troca:
            troca.clear()
            with get_conn() as conn, conn.cursor() as c2:  # outra conexão, commit
                c2.execute("update ai_pending_actions set tool_name = 'add_launch', summary = 'lançar'"
                           " where user_id = %s", (user_id,))
                conn.commit()
        return lido

    avisos = []
    monkeypatch.setattr(pending, "ler_para_aviso", le_e_troca)
    monkeypatch.setattr(pending, "avisar_mudanca_financeira",
                        lambda cur, u, antes, depois, **kw: avisos.append((antes, depois)))
    apagada = db.ai_clear_pending_action(uid)
    ferramenta = {"lançar": "add_launch", "recategorizar": "recategorize_launch"}[apagada]
    assert len(avisos) == 1 and avisos[0][0][0] == ferramenta and avisos[0][1] is None, (apagada, avisos)
