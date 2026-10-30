"""Vínculo WhatsApp: token forte `pbl`+32 hex no lugar do código de 6 dígitos.

Negativo: voltar `create_link_code` para 6 dígitos deixa vermelho o formato e o ponta a ponta
(o regex só aceita o token); tirar o `delete ... returning` atômico deixa vermelho o consumo duplo.
Positivo: o token válido vincula, e o aviso à vítima que lança não desfaz o vínculo.
"""
import re
import threading
import uuid

import db
from _paywall_gate_helpers import com_plano
from test_senha_obrigatoria_whatsapp import _dono_do_numero, _manda, enviadas  # noqa: F401
from test_vincular_codigo_numero_novo import VINCULADO, _numero_novo


def test_formato_e_consumo_unico():
    u = com_plano()
    code = db.create_link_code(u)
    assert re.fullmatch(r"pbl[0-9a-f]{32}", code)
    res, out = [], threading.Lock()

    def _c():
        r = db.consume_link_code(code)
        with out:
            res.append(r)

    ts = [threading.Thread(target=_c) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(res, key=lambda x: x is None) == [u, None, None, None]


def test_expirado_devolve_none():
    assert db.consume_link_code(db.create_link_code(com_plano(), minutes_valid=-1)) is None


def test_ponta_a_ponta_maiuscula_avisa_e_nao_reusa(enviadas, monkeypatch):
    import core.services.email_service as es
    import core.services.push_service as ps

    avisos = []
    monkeypatch.setattr(es, "send_whatsapp_linked_notice", lambda to, m: avisos.append((to, m)))
    monkeypatch.setattr(ps, "send_push_to_user", lambda *a, **k: avisos.append(("push", a[0])))
    u = com_plano()
    code = db.create_link_code(u)
    n = _numero_novo()
    _manda(n, f"VINCULAR {code.upper()}.")
    assert _dono_do_numero(n) == u and enviadas[0].startswith(VINCULADO), enviadas
    assert ("push", u) in avisos
    email = db.get_auth_user(u)["email"]
    mail = [a for a in avisos if a[0] == email]
    assert len(mail) == 1 and "*" in mail[0][1], avisos  # e-mail da dona, número mascarado
    assert all(n not in str(a) for a in avisos)  # número só mascarado
    enviadas.clear()
    _manda(_numero_novo(), f"vincular {code}")
    assert not enviadas or not enviadas[0].startswith(VINCULADO), enviadas


def test_codigo_de_6_digitos_recusado(enviadas):
    u = com_plano()
    n = _numero_novo()
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "insert into link_codes(code, user_id, expires_at) values ('123456', %s, now() + interval '5 min')",
                (u,),
            )
        conn.commit()
    _manda(n, "vincular 123456")
    assert _dono_do_numero(n) != u


def test_aviso_que_lanca_nao_desfaz_vinculo(enviadas, monkeypatch):
    import core.services.email_service as es

    def _boom(*a, **k):
        raise RuntimeError("smtp")

    monkeypatch.setattr(es, "send_whatsapp_linked_notice", _boom)
    u = com_plano()
    n = _numero_novo()
    _manda(n, f"vincular {db.create_link_code(u)}")
    assert _dono_do_numero(n) == u


def _semear_teto(tentativas):
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("delete from auth_rate_limits where bucket='link-code-global'")
            cur.execute(
                "insert into auth_rate_limits(bucket, identifier, window_started_at, attempts, updated_at) "
                "values ('link-code-global', '*', now(), %s, now())",
                (tentativas,),
            )
        conn.commit()


def test_teto_global_so_alerta(monkeypatch):
    import core.observability as obs

    eventos = []
    monkeypatch.setattr(obs, "log_system_event_sync", lambda *a, **k: eventos.append(a[1]))
    _semear_teto(50)
    u = com_plano()
    assert db.consume_link_code("pbl" + "0" * 32) is None
    assert "link_code_global_ceiling" in eventos
    assert db.consume_link_code(db.create_link_code(u)) == u  # não bloqueia


def test_teto_global_grava_uma_vez_por_janela(monkeypatch):
    import core.observability as obs

    eventos = []
    monkeypatch.setattr(obs, "log_system_event_sync", lambda *a, **k: eventos.append(a[1]))
    _semear_teto(49)
    for _ in range(3):  # 50 (no teto), 51 (transição), 52 (acima)
        db.consume_link_code("pbl" + "0" * 32)
    assert eventos == ["link_code_global_ceiling"]


# --- Contrato D5: só POST /auth/link-code gera código; os demais caminhos devolvem "" ---
def _n_codigos(u):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) as n from link_codes where user_id=%s", (u,))
        return cur.fetchone()["n"]


def _cliente(monkeypatch):
    import frontend.finance_bot_websocket_custom as dash
    from fastapi.testclient import TestClient
    from _apoio_auth_app import csrf, noop_log

    monkeypatch.setattr(dash, "log_auth_login_event", noop_log)
    c = TestClient(dash.app)
    return dash, c, csrf(c)


def test_login_nao_gera_codigo(monkeypatch):
    u = com_plano()
    dash, c, h = _cliente(monkeypatch)
    monkeypatch.setattr(db, "login_auth_user", lambda e, p: {"user_id": u, "email": e, "plan": "free"})
    antes = _n_codigos(u)
    r = c.post("/auth/login", headers=h, json={"email": "d5@example.com", "password": "secret123"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["link_code"] == "" and d["whatsapp_link"] == ""
    assert _n_codigos(u) == antes


def _codigos_do_email(email):
    from core.crypto import hash_pii_optional
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select count(*) as n from link_codes l join auth_accounts a on a.user_id=l.user_id "
            "where a.email_hash=%s", (hash_pii_optional(email, kind="email"),))
        return cur.fetchone()["n"]


def test_cadastro_nao_gera_codigo(monkeypatch):
    """Caminho real: verificação de e-mail no banco -> confirm_email_verification_impl -> boas_vindas."""
    import core.services.email_service as es

    dash, c, h = _cliente(monkeypatch)
    enviados = []
    monkeypatch.setattr(es, "send_welcome_email", lambda *a, **k: enviados.append(a))
    email = f"d5n{uuid.uuid4().hex[:10]}@example.com"
    code = db.create_email_verification(email, "secret123", None)
    r = c.post("/auth/verify-email", headers=h, json={"email": email, "code": code})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["link_code"] == "" and d["whatsapp_link"] == ""
    assert len(enviados) == 1 and enviados[0][1] == ""  # boas-vindas rodou, sem código
    assert _codigos_do_email(email) == 0


def test_cadastro_google_nao_gera_codigo():
    """Caminho real: pending_google_signups -> consume_pending_google_signup."""
    sub = f"sub{uuid.uuid4().hex}"
    email = f"d5g{uuid.uuid4().hex[:10]}@example.com"
    token = db.create_pending_google_signup(sub, email, "Fulana")
    res = db.consume_pending_google_signup(token, "Fulana", "11987654321")
    assert res["link_code"] == "" and res["email"] == email
    assert _n_codigos(res["user_id"]) == 0


def test_post_link_code_gera_um_token(monkeypatch):
    u = com_plano()
    monkeypatch.setenv("WHATSAPP_NUMBER", "5511999999999")  # sem número o link é "" (falha fechada)
    dash, c, h = _cliente(monkeypatch)
    dash.app.dependency_overrides[dash._get_current_user] = lambda: u
    monkeypatch.setattr(dash, "_exigir_credencial", lambda uid: None)
    try:
        antes = _n_codigos(u)
        r = c.post("/auth/link-code", headers=h)
    finally:
        dash.app.dependency_overrides.pop(dash._get_current_user, None)
    assert r.status_code == 200, r.text
    d = r.json()
    assert re.fullmatch(r"pbl[0-9a-f]{32}", d["link_code"])
    assert "vincular%20" + d["link_code"] in d["whatsapp_link"]
    assert _n_codigos(u) == antes + 1
