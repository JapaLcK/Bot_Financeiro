"""Painel de funil do admin: `/admin/funil` (HTML) e `/admin/api/funil` (JSON).

Cobre: o MESMO controle de acesso do `/admin`; agregados em janelas 7d/30d com
`created_at` manipulado; etapas CUMULATIVAS (Pix grava só `completed`, sem `started`);
divisão por zero; "medido desde" de `viewed_pricing`; a resposta como LISTA FECHADA
(nenhum campo de pessoa); e os links externos (nunca com ID inventado).

O banco da suíte é compartilhado entre testes, então os agregados são medidos por
DELTA (antes × depois de criar as fixtures), como em `test_admin_users_panel.py`.
"""
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import core.admin_dashboard as admin_dashboard
import frontend.finance_bot_websocket_custom as dashboard
from core import funil_dashboard
from db import ensure_user, get_conn
from token_utils import make_dashboard_token

NOW = datetime.now(timezone.utc)


@pytest.fixture(autouse=True)
def configured_admin(monkeypatch):
    async def _noop_log(*args, **kwargs):
        return None

    monkeypatch.setattr(admin_dashboard, "ADMIN_DASHBOARD_PASSWORD", "secret-admin")
    monkeypatch.setattr(admin_dashboard, "ADMIN_DASHBOARD_PASSWORD_HASH", "")
    monkeypatch.setattr(admin_dashboard, "log_system_event", _noop_log)
    monkeypatch.setattr(admin_dashboard, "STRIPE_SECRET_KEY", "")
    try:
        dashboard.limiter._storage.reset()
    except Exception:
        pass


def _admin_client() -> TestClient:
    client = TestClient(dashboard.app, base_url="https://testserver")
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, "test-admin-csrf")
    login = client.post(
        "/admin/auth/login",
        headers={dashboard.CSRF_HEADER_NAME: "test-admin-csrf"},
        json={"username": "admin", "password": "secret-admin"},
    )
    assert login.status_code == 200
    return client


def _funil(client=None) -> dict:
    r = (client or _admin_client()).get("/admin/api/funil")
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture()
def contas():
    """Cria contas pelo helper e apaga tudo no fim (eventos antes: FK set null)."""
    uids = []

    def _mk(dias_atras=2, *, plan="free", pay="inactive", quiz=False, source="web",
            trial_dias=None, onboarding=False, phone="pending", past_due_dias=None):
        uid = int(uuid.uuid4().int % 10_000_000_000)
        ensure_user(uid)
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into auth_accounts
                        (user_id, email, password_hash, plan, last_payment_status,
                         signup_source, signup_quiz, created_at, trial_started_at,
                         onboarding_completed_at, phone_status, past_due_since)
                    values (%s, %s, 'x', %s, %s, %s, %s, now() - make_interval(days => %s),
                            case when %s::int is null then null
                                 else now() - make_interval(days => %s::int) end,
                            case when %s then now() else null end, %s,
                            case when %s::int is null then null
                                 else now() - make_interval(days => %s::int) end)
                    """,
                    (uid, f"funil-{uid}@test.local", plan, pay, source,
                     json.dumps({"versao": 1, "respostas": None}) if quiz else None,
                     dias_atras, trial_dias, trial_dias, onboarding, phone,
                     past_due_dias, past_due_dias),
                )
            conn.commit()
        uids.append(uid)
        return uid

    yield _mk
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("delete from checkout_funnel_events where user_id = any(%s)", (uids,))
            cur.execute("delete from pix_charges where user_id = any(%s)", (uids,))
            cur.execute("delete from ebook_entregas where user_id = any(%s)", (uids,))
            cur.execute("delete from prospect_referrals where referred_user_id = any(%s)", (uids,))
            cur.execute("delete from affiliates where user_id = any(%s)", (uids,))  # cascata nos referrals
            cur.execute("delete from user_identities where user_id = any(%s)", (uids,))
            cur.execute("delete from auth_accounts where user_id = any(%s)", (uids,))
        conn.commit()


def _evento(uid, kind, *, dias_atras=1, session=None):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "insert into checkout_funnel_events (user_id, session_id, kind, created_at) "
                "values (%s, %s, %s, now() - make_interval(days => %s))",
                (uid, session, kind, dias_atras),
            )
        conn.commit()


def _pix(cur, uid, status, pago):
    cur.execute(
        """
        insert into pix_charges (user_id, external_reference, plan, plan_stored, price_cents,
            credit_cents, amount_cents, public_token, status, paid_at,
            access_starts_at, access_expires_at)
        values (%s, %s, 'pro', 'pro', 1000, 0, 1000, %s, %s,
                case when %s then now() else null end,
                case when %s then now() else null end,
                case when %s then now() + interval '365 days' else null end)
        """,
        (uid, f"pix:{uuid.uuid4().int % 10**12}", uuid.uuid4().hex, status, pago, pago, pago),
    )


def _etapas(j, janela):
    return {e["id"]: e["n"] for e in j["janelas"][janela]["etapas"]}


def _delta(antes, depois, janela):
    a, d = _etapas(antes, janela), _etapas(depois, janela)
    return {k: d[k] - a[k] for k in d}


# ── Acesso: o mesmo do /admin, por construção ──────────────────────────────

def test_api_sem_auth_da_401():
    assert TestClient(dashboard.app).get("/admin/api/funil").status_code == 401


def test_pagina_sem_sessao_redireciona_para_o_login():
    r = TestClient(dashboard.app).get("/admin/funil", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/admin/login"


def test_usuario_comum_nao_entra(contas):
    uid = contas()
    token = make_dashboard_token(uid)
    c = TestClient(dashboard.app, base_url="https://testserver")
    c.cookies.set("auth_token", token)
    c.cookies.set("admin_auth_token", token)  # JWT de usuário no cookie do admin
    assert c.get("/admin/api/funil").status_code == 401
    assert c.get("/admin/api/funil", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert c.get("/admin/funil", follow_redirects=False).status_code == 303


def test_admin_recebe_pagina_e_json_sem_cache():
    c = _admin_client()
    pagina = c.get("/admin/funil", follow_redirects=False)
    assert pagina.status_code == 200
    assert pagina.headers["cache-control"] == "no-store"
    assert "PigBank — Funil" in pagina.text
    api = c.get("/admin/api/funil")
    assert api.status_code == 200
    assert api.headers["cache-control"] == "no-store"


def test_so_leitura():
    c = _admin_client()
    for m in ("post", "put", "patch", "delete"):
        r = getattr(c, m)("/admin/api/funil", headers={dashboard.CSRF_HEADER_NAME: "test-admin-csrf"})
        assert r.status_code == 405, m


# ── Etapas: cumulativas, janelas, divisão por zero ─────────────────────────

def test_etapas_cumulativas_e_janelas(contas):
    antes = _funil()
    a = contas(2)   # Pix: só `completed`, sem `started` — conta em "abriram checkout"
    _evento(a, "completed", session="cs_pix_a")
    b = contas(2)   # só viu a /precos
    _evento(b, "viewed_pricing")
    c = contas(2)   # abriu e expirou, nunca concluiu
    _evento(c, "started", session="cs_c")
    _evento(c, "expired", session="cs_c")
    contas(2)       # cadastrou e sumiu
    d = contas(20)  # coorte só de 30d; concluiu há 19 dias
    _evento(d, "started", dias_atras=19, session="cs_d")
    _evento(d, "completed", dias_atras=19, session="cs_d")
    depois = _funil()

    assert _delta(antes, depois, "7d") == {
        "cadastros": 4, "viram_precos": 3, "abriram_checkout": 2, "concluiram": 1}
    assert _delta(antes, depois, "30d") == {
        "cadastros": 5, "viram_precos": 4, "abriram_checkout": 3, "concluiram": 2}
    w0, w1 = antes["janelas"]["7d"], depois["janelas"]["7d"]
    assert w1["abandono"] - w0["abandono"] == 1                      # c: abriu − concluiu
    assert w1["expiraram_sem_concluir"] - w0["expiraram_sem_concluir"] == 1
    assert w1["checkout"]["sessoes_expiradas"] - w0["checkout"]["sessoes_expiradas"] == 1
    assert w1["checkout"]["sessoes_abertas"] - w0["checkout"]["sessoes_abertas"] == 1  # só c


def test_evento_anterior_ao_coorte_nao_conta(contas):
    """Os flags só olham eventos DEPOIS do início do coorte (7d)."""
    antes = _funil()
    u = contas(2)
    _evento(u, "completed", dias_atras=10, session="cs_velho")
    depois = _funil()
    assert _delta(antes, depois, "7d")["concluiram"] == 0
    assert _delta(antes, depois, "7d")["cadastros"] == 1
    assert _delta(antes, depois, "30d")["concluiram"] == 1


def test_taxas_nulas_na_divisao_por_zero_e_calculo_das_etapas():
    vazio = funil_dashboard._etapas(0, 0, 0, 0, viram_medido=True)
    assert all(e["taxa_etapa"] is None and e["taxa_acum"] is None for e in vazio)
    # viram=0 mas há cadastros: abriram/viram divide por zero
    e = {x["id"]: x for x in funil_dashboard._etapas(10, 0, 0, 0, viram_medido=True)}
    assert e["viram_precos"]["taxa_etapa"] == 0.0
    assert e["abriram_checkout"]["taxa_etapa"] is None
    cheio = {x["id"]: x for x in funil_dashboard._etapas(100, 50, 20, 10, viram_medido=True)}
    assert cheio["viram_precos"]["taxa_etapa"] == 0.5
    assert cheio["abriram_checkout"]["taxa_etapa"] == 0.4
    assert cheio["concluiram"]["taxa_etapa"] == 0.5
    assert cheio["concluiram"]["taxa_acum"] == 0.1


def test_medido_desde_anula_a_taxa_de_precos_quando_o_coorte_e_anterior(contas):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("delete from checkout_funnel_events where kind = 'viewed_pricing'")
        conn.commit()
    u = contas(20)
    _evento(u, "viewed_pricing", dias_atras=15)   # a medição começou há 15 dias
    j = _funil()
    assert j["viewed_pricing_desde"] is not None
    w30, w7 = j["janelas"]["30d"], j["janelas"]["7d"]
    assert w30["viram_precos_medido"] is False   # coorte de 30d começa antes da medição
    p = {e["id"]: e for e in w30["etapas"]}
    assert p["viram_precos"]["taxa_etapa"] is None and p["viram_precos"]["taxa_acum"] is None
    assert p["abriram_checkout"]["taxa_etapa"] is None   # também passa por "viram"
    assert w7["viram_precos_medido"] is True              # o de 7d começa depois
    # medição mais antiga que o coorte de 30d: a taxa volta a valer
    _evento(u, "viewed_pricing", dias_atras=40)
    assert _funil()["janelas"]["30d"]["viram_precos_medido"] is True


def test_sem_nenhum_viewed_pricing_nao_ha_taxa(contas):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("delete from checkout_funnel_events where kind = 'viewed_pricing'")
        conn.commit()
    j = _funil()
    assert j["viewed_pricing_desde"] is None
    assert j["janelas"]["7d"]["viram_precos_medido"] is False


# ── Canais ─────────────────────────────────────────────────────────────────

def _canais(j, janela):
    return {c["canal"]: c for c in j["janelas"][janela]["canais"]}


def test_canal_com_precedencia_e_quiz_so_sim_nao(contas):
    antes = _funil()
    q = contas(2, quiz=True)
    _evento(q, "completed", session="cs_q")
    p = contas(2, quiz=True)   # prospecção vence o quiz
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("insert into prospect_referrals (code, referred_user_id) values ('funil-t', %s)", (p,))
        conn.commit()
    contas(2, source="app")
    depois = _funil()
    a, d = _canais(antes, "7d"), _canais(depois, "7d")
    assert d["quiz"]["cadastros"] - a["quiz"]["cadastros"] == 1
    assert d["quiz"]["concluiram"] - a["quiz"]["concluiram"] == 1
    assert d["prospeccao"]["cadastros"] - a["prospeccao"]["cadastros"] == 1
    assert d["direto"]["cadastros"] - a["direto"]["cadastros"] == 1
    assert [c["canal"] for c in depois["janelas"]["7d"]["canais"]] == list(funil_dashboard.CANAIS)
    origens = {o["origem"]: o for o in depois["janelas"]["7d"]["origens"]}
    assert "app" in origens and "web" in origens


# ── Demais blocos ──────────────────────────────────────────────────────────

def test_estado_ativacao_trial_atraso(contas):
    antes = _funil()
    t = contas(2, plan="pro", pay="trialing", trial_dias=1)
    p = contas(2, plan="pro", pay="active", trial_dias=1, onboarding=True, phone="confirmed")
    _evento(p, "completed", session="cs_ativ")
    q = contas(2)   # concluiu, mas só tem movimentação interna: não é "1º lançamento"
    _evento(q, "completed", session="cs_ativ_q")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("insert into launches (user_id, tipo, valor) values (%s, 'despesa', 10)", (p,))
            cur.execute("insert into launches (user_id, tipo, valor, is_internal_movement) "
                        "values (%s, 'despesa', 10, true)", (q,))
        conn.commit()
    contas(2, plan="pro", pay="past_due", past_due_dias=10)
    contas(2, plan="pro", pay="past_due", past_due_dias=2)
    depois = _funil()
    a, d = antes["janelas"]["7d"], depois["janelas"]["7d"]
    assert d["estado_atual"]["trial"] - a["estado_atual"]["trial"] == 1
    assert d["estado_atual"]["paying"] - a["estado_atual"]["paying"] == 1
    assert d["estado_atual"]["past_due"] - a["estado_atual"]["past_due"] == 2
    ativ = {k: d["ativacao"][k] - a["ativacao"][k] for k in a["ativacao"]}
    assert ativ == {"concluiram": 2, "onboarding": 1, "whatsapp": 1, "lancamento": 1}
    tr = {k: d["trial"][k] - a["trial"][k] for k in a["trial"]}
    assert tr == {"iniciaram": 2, "em_trial": 1, "pagando": 1, "cancelaram": 0, "outros": 0}
    assert depois["atraso"]["total"] - antes["atraso"]["total"] == 2
    assert depois["atraso"]["alem_carencia"] - antes["atraso"]["alem_carencia"] == 1


def test_pix_e_ebook(contas):
    antes = _funil()
    u = contas(2)
    with get_conn() as conn:
        with conn.cursor() as cur:
            for i, (status, pago) in enumerate(
                    [("pending", False), ("expired", False), ("canceled", False), ("paid", True)]):
                _pix(cur, u, status, pago)
            for resultado, fechada in [("enviado", True), ("nao_comprou", True), ("estornado", True), (None, False)]:
                cur.execute(
                    "insert into ebook_entregas (user_id, session_id, ebook_price, resultado, fechada_em) "
                    "values (%s, %s, 'p', %s, case when %s then now() else null end)",
                    (u, uuid.uuid4().hex, resultado, fechada),
                )
        conn.commit()
    depois = _funil()
    p = {k: depois["janelas"]["7d"]["pix"][k] - antes["janelas"]["7d"]["pix"][k]
         for k in ("gerados", "pagos", "expirados", "cancelados", "abertos")}
    assert p == {"gerados": 4, "pagos": 1, "expirados": 1, "cancelados": 1, "abertos": 1}
    e = {k: depois["janelas"]["7d"]["ebook"][k] - antes["janelas"]["7d"]["ebook"][k]
         for k in antes["janelas"]["7d"]["ebook"]}
    assert e == {"entregas": 4, "enviados": 1, "nao_comprou": 1, "estornados": 1, "pendentes": 1}


def test_ebook_pendente_e_fechada_em_nao_resultado(contas):
    """`pendentes` olha `fechada_em`, não `resultado`: o schema permite os dois cruzamentos."""
    u = contas(2)
    antes = _funil()["janelas"]["7d"]["ebook"]["pendentes"]

    def _entrega(resultado, fechada):
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "insert into ebook_entregas (user_id, session_id, ebook_price, resultado, fechada_em) "
                    "values (%s, %s, 'p', %s, case when %s then now() else null end)",
                    (u, uuid.uuid4().hex, resultado, fechada))
            conn.commit()

    _entrega("enviado", False)   # tem resultado, sem fechamento: pendente
    assert _funil()["janelas"]["7d"]["ebook"]["pendentes"] - antes == 1
    _entrega(None, True)         # fechada sem resultado: não é pendente
    assert _funil()["janelas"]["7d"]["ebook"]["pendentes"] - antes == 1


def test_valores_fechados_de_canal_e_estado(contas):
    contas(2)
    contas(2, quiz=True)
    contas(2, plan="pro", pay="active")
    for w in _funil()["janelas"].values():
        cad = w["etapas"][0]["n"]
        assert {c["canal"] for c in w["canais"]} <= set(funil_dashboard.CANAIS)
        assert set(w["estado_atual"]) <= set(admin_dashboard._USER_STATUSES)
        # canal/estado fora do conjunto não vaza como chave: some do total (soma != cadastros)
        assert sum(c["cadastros"] for c in w["canais"]) == cad
        assert sum(w["estado_atual"].values()) == cad


# ── Rodada do Tester: bordas, precedência e regras de cada contagem ────────

def _recuar(uid, horas):
    """Fixa `created_at` da conta a `horas` atrás (o helper `contas` só fala em dias)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("update auth_accounts set created_at = now() - make_interval(hours => %s) "
                        "where user_id = %s", (horas, uid))
        conn.commit()


def test_borda_da_janela_7d_e_30d(contas):
    antes = _funil()
    for horas in (6 * 24 + 23, 7 * 24 + 1, 29 * 24 + 23, 30 * 24 + 1):
        _recuar(contas(2), horas)
    depois = _funil()
    assert _delta(antes, depois, "7d")["cadastros"] == 1     # só a de 6d23h
    assert _delta(antes, depois, "30d")["cadastros"] == 3    # 6d23h, 7d01h e 29d23h; não a de 30d01h


def test_precedencia_afiliado_prospeccao_quiz_direto(contas):
    antes = _funil()
    dono = contas(2)
    afil, pros, quiz = contas(2, quiz=True), contas(2, quiz=True), contas(2, quiz=True)
    contas(2)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("insert into affiliates (user_id, code) values (%s, %s) returning id",
                        (dono, f"funil{uuid.uuid4().hex[:8]}"))
            af = cur.fetchone()["id"]
            cur.execute("insert into affiliate_referrals (affiliate_id, referred_user_id) values (%s, %s)", (af, afil))
            for u in (afil, pros):   # afiliado também é prospecção e quiz: afiliado vence
                cur.execute("insert into prospect_referrals (code, referred_user_id) values ('funil-p', %s)", (u,))
        conn.commit()
    a, d = _canais(antes, "7d"), _canais(_funil(), "7d")
    assert {c: d[c]["cadastros"] - a[c]["cadastros"] for c in funil_dashboard.CANAIS} == {
        "afiliado": 1, "prospeccao": 1, "quiz": 1, "direto": 2}   # direto: `dono` e a conta sem nada


def test_expiraram_sem_concluir_ignora_quem_concluiu(contas):
    antes = _funil()
    concluiu, so_expirou = contas(2), contas(2)
    for kind in ("started", "expired", "completed"):
        _evento(concluiu, kind, session="cs_exp_ok")
    _evento(so_expirou, "expired", session="cs_exp_so")
    depois = _funil()
    assert (depois["janelas"]["7d"]["expiraram_sem_concluir"]
            - antes["janelas"]["7d"]["expiraram_sem_concluir"]) == 1


def test_sessao_expirada_reentregue_conta_uma_vez(contas):
    antes = _funil()
    u = contas(2)
    _evento(u, "expired", session="cs_reentrega")
    _evento(u, "expired", session="cs_reentrega")
    depois = _funil()
    assert (depois["janelas"]["7d"]["checkout"]["sessoes_expiradas"]
            - antes["janelas"]["7d"]["checkout"]["sessoes_expiradas"]) == 1


def test_ativacao_ignora_conclusao_fora_da_janela(contas):
    antes = _funil()
    u = contas(20)
    _evento(u, "completed", dias_atras=10, session="cs_ativ_fora")
    depois = _funil()
    assert depois["janelas"]["7d"]["ativacao"]["concluiram"] == antes["janelas"]["7d"]["ativacao"]["concluiram"]
    assert (depois["janelas"]["30d"]["ativacao"]["concluiram"]
            - antes["janelas"]["30d"]["ativacao"]["concluiram"]) == 1


def test_whatsapp_vinculado_por_identidade_ou_por_telefone_confirmado(contas):
    antes = _funil()
    por_identidade = contas(2)                          # telefone pendente + identidade whatsapp
    outro_provider = contas(2)                          # identidade de OUTRO provider: não conta
    por_telefone = contas(2, phone="confirmed")         # sem identidade, telefone confirmado
    nenhum = contas(2)
    with get_conn() as conn:
        with conn.cursor() as cur:
            for prov, u in (("whatsapp", por_identidade), ("discord", outro_provider)):
                cur.execute("insert into user_identities (provider, external_id, user_id) values (%s, %s, %s)",
                            (prov, uuid.uuid4().hex, u))
        conn.commit()
    for u in (por_identidade, outro_provider, por_telefone, nenhum):
        _evento(u, "completed", session=f"cs_wa_{u}")
    a, d = antes["janelas"]["7d"]["ativacao"], _funil()["janelas"]["7d"]["ativacao"]
    assert d["concluiram"] - a["concluiram"] == 4
    assert d["whatsapp"] - a["whatsapp"] == 2


def test_pix_geradas_exclui_draft_e_creating(contas):
    antes = _funil()
    with get_conn() as conn:
        with conn.cursor() as cur:
            for status in ("draft", "creating", "pending"):
                _pix(cur, contas(2), status, False)   # 1 por usuário: `uniq_pix_charge_ativa`
        conn.commit()
    a, d = antes["janelas"]["7d"]["pix"], _funil()["janelas"]["7d"]["pix"]
    assert d["gerados"] - a["gerados"] == 1      # só a `pending`
    assert d["abertos"] - a["abertos"] == 1


def test_pix_pagas_e_paid_at_nao_o_status(contas):
    antes = _funil()
    u = contas(2)
    with get_conn() as conn:
        with conn.cursor() as cur:
            _pix(cur, u, "paid", False)                  # status 'paid' sem paid_at: não conta
            _pix(cur, u, "expired", True)                # com paid_at, status outro: conta
            _pix(cur, u, "canceled", True)
        conn.commit()
    a, d = antes["janelas"]["7d"]["pix"], _funil()["janelas"]["7d"]["pix"]
    assert d["pagos"] - a["pagos"] == 2
    assert d["gerados"] - a["gerados"] == 3


def test_emails_verificacao_conta_email_hash_distinto():
    h = f"funil-{uuid.uuid4().hex}"
    antes = _funil()["janelas"]["7d"]["emails_verificacao"]
    with get_conn() as conn:
        with conn.cursor() as cur:
            try:
                for _ in range(2):    # dois códigos para o MESMO email_hash
                    cur.execute(
                        "insert into email_verification_codes (email, email_hash, code, password_hash, expires_at) "
                        "values (%s, %s, '123456', 'x', now() + interval '15 minutes')", (f"{h}@test.local", h))
                cur.execute(      # sem hash: fora da contagem
                    "insert into email_verification_codes (email, code, password_hash, expires_at) "
                    "values (%s, '123456', 'x', now() + interval '15 minutes')", (f"{h}-n@test.local",))
                conn.commit()
                assert _funil()["janelas"]["7d"]["emails_verificacao"] - antes == 1
            finally:
                cur.execute("delete from email_verification_codes where email like %s", (f"{h}%",))
                conn.commit()


class _Req:
    def __init__(self, ua):
        self.headers = {"user-agent": ua}


def test_origens_e_lista_fechada_e_igual_ao_que_a_producao_grava(contas):
    from frontend.routes.shared import signup_source_from_request
    gravados = {signup_source_from_request(_Req(ua), provedor=p)
                for ua in ("Mozilla/5.0", "PigBankApp/1.0") for p in (None, "google", "apple")}
    assert set(funil_dashboard.ORIGENS) == gravados   # só vê provedor listado aqui

    hostis = ["vitima@exemplo.com", "<script>alert(1)</script>", "fx" + "y" * 298, "WEB", None]
    for h in hostis:
        contas(2, source=h)
    j = _funil()
    texto = json.dumps(j)
    for h in hostis[:3]:
        assert h not in texto
    assert "vitima" not in texto and "<script" not in texto and "yyyyyyyyyyyy" not in texto
    for janela in j["janelas"].values():
        nomes = {o["origem"] for o in janela["origens"]}
        assert nomes <= set(funil_dashboard.ORIGENS) | {"outro"}, nomes
        assert sum(o["cadastros"] for o in janela["origens"]) == janela["etapas"][0]["n"]


def test_admin_linka_o_painel_de_funil():
    admin = (Path(__file__).parent.parent / "frontend" / "admin-dashboard.html").read_text()
    assert 'href="/admin/funil"' in admin


# ── Lista fechada: nada de pessoa ──────────────────────────────────────────

_PROIBIDAS_EXATAS = {"user_id", "email", "nome", "name", "phone", "session_id", "respostas"}
_PROIBIDAS_PARTE = ("user_id", "quiz", "profile", "hash")

_JANELA = {
    "dias", "inicio", "viram_precos_medido", "emails_verificacao", "etapas", "abandono",
    "expiraram_sem_concluir", "estado_atual", "canais", "origens", "checkout", "ativacao",
    "trial", "pix", "ebook",
}
_LINHA = {"cadastros", "viram_precos", "abriram_checkout", "concluiram", "taxa_conclusao"}
_CHAVES_PERMITIDAS = (
    {"gerado_em", "viewed_pricing_desde", "janelas", "atraso", "links", "7d", "30d",
     "total", "alem_carencia", "carencia_dias", "painel", "url", "olhar"}
    | _JANELA | _LINHA | {"canal", "origem", "id", "n", "taxa_etapa", "taxa_acum"}
    | {"free", "trial", "paying", "past_due", "canceled", "granted"}
    | {"pessoas", "sessoes_abertas", "sessoes_concluidas", "sessoes_expiradas", "conversao"}
    | {"concluiram", "onboarding", "whatsapp", "lancamento"}
    | {"iniciaram", "em_trial", "pagando", "cancelaram", "outros"}
    | {"gerados", "pagos", "expirados", "cancelados", "abertos", "taxa_pago"}
    | {"entregas", "enviados", "nao_comprou", "estornados", "pendentes"}
)


def _chaves(no, acc=None):
    acc = set() if acc is None else acc
    if isinstance(no, dict):
        for k, v in no.items():
            acc.add(k)
            _chaves(v, acc)
    elif isinstance(no, list):
        for v in no:
            _chaves(v, acc)
    return acc


def test_resposta_e_lista_fechada_sem_campo_de_pessoa(contas):
    u = contas(2, quiz=True, plan="pro", pay="active", onboarding=True, phone="confirmed")
    _evento(u, "completed", session="cs_lista")
    j = _funil()
    chaves = _chaves(j)
    # origens e canais viram valores, não chaves; as únicas chaves são as da lista.
    assert chaves <= _CHAVES_PERMITIDAS, sorted(chaves - _CHAVES_PERMITIDAS)
    for ch in chaves:
        assert ch not in _PROIBIDAS_EXATAS and not any(p in ch for p in _PROIBIDAS_PARTE), ch
    texto = json.dumps(j)
    assert f"funil-{u}@test.local" not in texto
    assert not re.search(rf"\b{u}\b", texto)


# ── Links externos ─────────────────────────────────────────────────────────

def _links(monkeypatch, *, meta="", ga4="", stripe=""):
    from frontend.routes import shared
    monkeypatch.setattr(shared, "META_PIXEL_ID", meta)
    monkeypatch.setattr(admin_dashboard, "STRIPE_SECRET_KEY", stripe)
    if ga4:
        monkeypatch.setenv("GA4_PROPERTY_ID", ga4)
    else:
        monkeypatch.delenv("GA4_PROPERTY_ID", raising=False)
    return {l["painel"]: l["url"] for l in funil_dashboard.links_externos()}


def test_links_sem_id_nao_aparecem(monkeypatch):
    nomes = _links(monkeypatch)
    assert not any("Meta" in n or "Google" in n or "Stripe" in n for n in nomes)
    assert nomes["Afiliados"] == "/admin"
    assert nomes["Microsoft Clarity"].startswith("https://clarity.microsoft.com/projects/view/")
    assert nomes["Microsoft Clarity"].endswith("/dashboard")


def test_links_com_id_usam_o_id_configurado(monkeypatch):
    nomes = _links(monkeypatch, meta="123456", ga4="987654", stripe="sk_live_x")
    assert nomes["Meta Events Manager"] == "https://business.facebook.com/events_manager2/list/pixel/123456/overview"
    assert nomes["Google Analytics 4"] == "https://analytics.google.com/analytics/web/#/p987654/reports/intelligenthome"
    assert nomes["Stripe, assinaturas"] == "https://dashboard.stripe.com/subscriptions"
    assert nomes["Stripe, pagamentos"] == "https://dashboard.stripe.com/payments"


def test_link_stripe_em_modo_teste(monkeypatch):
    nomes = _links(monkeypatch, stripe="sk_test_x")
    assert nomes["Stripe, assinaturas"] == "https://dashboard.stripe.com/test/subscriptions"
    assert nomes["Stripe, pagamentos"] == "https://dashboard.stripe.com/test/payments"
