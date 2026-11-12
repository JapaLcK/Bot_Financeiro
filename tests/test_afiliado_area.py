"""Área do afiliado: o que `/api/affiliate/*` e `/api/v2/me` entregam a quem.

Postgres real, monólito real, Bearer de uma sessão real. Toda identidade sai da
sessão: nenhum id do cliente muda de quem é o dado.

Exemplo dos saldos (`_ana`): R$ 30 em carência, 120 disponíveis, 60 em saque, 200
pagos e 15 estornados; geradas = 410.

Controles negativos (rodados à mão, §3 do `CLAUDE.md`):
  - tire a trava de `request_payout` → vermelho em `test_trava_*`.
  - troque a trava por `for update` → vermelho em `test_trava_nao_segura_o_webhook`.
  - volte `paid_cents` à soma de comissões → vermelho em `test_ja_pago_conta_so_*`.
  - volte `requested_cents` à soma de comissões → vermelho em `test_em_saque_*`.
Controle positivo do isolamento: B saca o dele depois da tentativa de A.
"""
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import core.admin_dashboard as admin_dashboard
import db
import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import bearer, csrf, sessao_de
from _afiliado_stripe import simular_stripe
from _billing_grants_helpers import garantir_system_event_logs
from conftest import promote_to_pro
from core.services import email_service
from db.affiliates import (
    COMMISSION_HOLD_DAYS,
    create_affiliate,
    get_affiliate_stats,
    mark_payout_paid,
    record_commission_for_invoice,
    record_referral,
    reject_payout,
    request_payout,
    reverse_commission,
    set_affiliate_status,
)
from db.connection import get_conn
from frontend.routes.shared import AUTH_COOKIE_NAME, DASHBOARD_COOKIE_NAME
from test_billing_webhook_lifecycle import _FakeStripe, _T_LIFE, _fake_sub, _post

ME = "/api/affiliate/me"
SAQUE = "/api/affiliate/payout"
V2_ME = "/api/v2/me"
SENHA = "senha-forte-123"
PIX = "chave-pix-de-teste@example.com"


@pytest.fixture(autouse=True)
def _painel_aberto(monkeypatch):
    monkeypatch.setenv("DASHBOARD_V2_TODOS", "1")


def _conta(plano=True):
    email = f"afil-{uuid.uuid4().hex[:10]}@example.com"
    uid = int(db.register_auth_user(email, SENHA)["user_id"])
    if plano:
        promote_to_pro(uid)
        db.mark_onboarding_completed(uid)
    return SimpleNamespace(uid=uid, email=email, h=bearer(uid))


def _afiliado(plano=True):
    c = _conta(plano)
    c.aff = create_affiliate(c.uid, commission_bps=1000)  # 10%: os valores destes testes contam com ele
    return c


def _comissao(c, reais, disponivel=True):
    """Indicado novo e a 1ª cobrança dele. Comissão de 10%, então `reais * 10` na fatura."""
    indicado = _conta(plano=False)
    assert record_referral(c.aff["code"], indicado.uid)
    fatura = f"in_{uuid.uuid4().hex[:12]}"
    linha = record_commission_for_invoice(indicado.uid, fatura, reais * 1000)
    _conferida(linha["id"])
    if disponivel:
        _vence(linha["id"])
    return SimpleNamespace(id=linha["id"], indicado=indicado, fatura=fatura)


def _conferida(comissao_id):
    """A comissão já passou pela conferência na Stripe (A5), sem reembolso. Quem mede a conferência é
    `tests/test_afiliado_conferencia.py`; aqui ela só não pode ficar no caminho."""
    with get_conn() as conn:
        conn.execute("update affiliate_commissions set payment_check_pending = false where id = %s", (comissao_id,))
        conn.commit()


def _vence(comissao_id):
    with get_conn() as conn:
        conn.execute("update affiliate_commissions set available_at = now() - interval '1 day'"
                     " where id = %s", (comissao_id,))
        conn.commit()


def _ana():
    c = _afiliado()
    _comissao(c, 200)
    assert mark_payout_paid(request_payout(c.aff["id"])["id"])
    _comissao(c, 60)
    request_payout(c.aff["id"])
    _comissao(c, 120)
    _comissao(c, 30, disponivel=False)
    assert reverse_commission(_comissao(c, 15).id)
    return c


def _me(c, **params):
    r = TestClient(dashboard.app).get(ME, headers=c.h, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _sacar(c, **extra):
    return TestClient(dashboard.app).post(SAQUE, headers=c.h, json={"pix_key": PIX, **extra})


def _saques(c):
    with get_conn() as conn:
        return conn.execute("select id, amount_cents, status from affiliate_payouts"
                            " where affiliate_id = %s order by id", (c.aff["id"],)).fetchall()


def _v2(c):
    return TestClient(dashboard.app).get(V2_ME, headers=c.h)


# ── Quem é quem ──────────────────────────────────────────────────────────────

def test_nao_afiliado_nao_tem_area():
    c = _conta()
    for r in (TestClient(dashboard.app).get(ME, headers=c.h), _sacar(c)):
        assert r.status_code == 404, r.text
        assert "code" not in r.json() and "link" not in r.json()
    assert _v2(c).json()["afiliado"] is False
    with get_conn() as conn:
        assert conn.execute("select 1 from affiliates where user_id = %s", (c.uid,)).fetchone() is None


def test_afiliado_ativo_ve_codigo_link_e_saldos_exatos():
    c = _ana()
    assert _v2(c).json()["afiliado"] is True
    m = _me(c)
    assert m["code"] == c.aff["code"] and m["link"].endswith(f"/r/{c.aff['code']}")
    assert m["hold_days"] == COMMISSION_HOLD_DAYS
    s = m["stats"]
    assert (s["held"], s["available"], s["requested"], s["paid"]) == (30, 120, 60, 200)
    assert s["referrals"] == 5


def test_invariante_os_quadros_somam_as_geradas_sem_estornadas():
    s = _me(_ana())["stats"]
    assert s["generated"] == 410  # com a estornada seriam 425
    assert s["held"] + s["available"] + s["requested"] + s["paid"] == s["generated"]


def test_isolamento_id_do_cliente_nao_muda_de_quem_e_o_saque():
    a, b = _afiliado(), _afiliado()
    _comissao(a, 100)
    de_b = _comissao(b, 70)

    assert _me(b)["stats"]["available"] == 70
    forjado = {"user_id": b.uid, "affiliate_id": b.aff["id"]}
    m = _me(a, **forjado)
    assert m["code"] == a.aff["code"] and m["stats"]["available"] == 100

    r = _sacar(a, amount=999999, **forjado)
    assert r.status_code == 200, r.text
    assert r.json()["payout"]["amount"] == 100
    assert [p["amount_cents"] for p in _saques(a)] == [100_00]
    assert _saques(b) == []
    assert _me(a)["stats"]["requested"] == 100 and _me(b)["stats"]["requested"] == 0
    with get_conn() as conn:
        assert conn.execute("select payout_id from affiliate_commissions where id = %s",
                            (de_b.id,)).fetchone()["payout_id"] is None

    r = _sacar(b)  # positivo: a guarda não recusa quem é dono do saldo
    assert r.status_code == 200, r.text
    assert r.json()["payout"]["amount"] == 70


def test_sem_sessao_e_cookies_de_contas_diferentes_sao_401():
    a, b = _afiliado(), _afiliado()
    _comissao(a, 100)
    client = TestClient(dashboard.app)
    assert client.get(ME).status_code == 401
    assert client.post(SAQUE, headers=csrf(client), json={"pix_key": PIX}).status_code == 401

    client.cookies.set(AUTH_COOKIE_NAME, sessao_de(a.uid)["access"])
    client.cookies.set(DASHBOARD_COOKIE_NAME, sessao_de(b.uid)["dashboard"])
    assert client.get(ME).status_code == 401
    assert client.post(SAQUE, headers=csrf(client), json={"pix_key": PIX}).status_code == 401
    assert _saques(a) == [] and _saques(b) == []

    sessao = sessao_de(a.uid)  # positivo: o par coerente da mesma conta entra
    client.cookies.set(AUTH_COOKIE_NAME, sessao["access"])
    client.cookies.set(DASHBOARD_COOKIE_NAME, sessao["dashboard"])
    assert client.get(ME).json()["code"] == a.aff["code"]


def test_desativado_consulta_saca_e_nao_acumula():
    c = _afiliado()
    _comissao(c, 100)
    futura = _conta(plano=False)
    assert record_referral(c.aff["code"], futura.uid)
    assert set_affiliate_status(c.aff["id"], "disabled")

    assert _v2(c).json()["afiliado"] is True
    m = _me(c)
    assert m["status"] == "disabled" and m["stats"]["available"] == 100
    assert _sacar(c).status_code == 200
    assert [p["status"] for p in _saques(c)] == ["requested"]
    assert record_commission_for_invoice(futura.uid, f"in_{uuid.uuid4().hex}", 100_000) is None


def test_sem_plano_le_a_area_mas_nao_o_painel():
    c = _afiliado(plano=False)
    assert _me(c)["code"] == c.aff["code"]
    r = _v2(c)
    assert r.status_code == 402 and r.json()["error"]["code"] == "plan_selection_required"


def test_payout_id_acompanha_o_saque_e_a_rejeicao():
    c = _afiliado()
    _comissao(c, 100)
    assert _me(c)["commissions"][0]["payout_id"] is None
    saque = _sacar(c).json()["payout"]["id"]
    assert _me(c)["commissions"][0]["payout_id"] == saque
    assert reject_payout(saque, "chave errada")
    assert _me(c)["commissions"][0]["payout_id"] is None


def test_lista_negra_nada_do_indicado_nem_da_chave_pix_sai_na_resposta():
    c = _afiliado()
    comissao = _comissao(c, 100)
    saque = _sacar(c)
    assert saque.status_code == 200, saque.text
    with get_conn() as conn:
        linha = conn.execute("select pix_key_enc, pix_key_hash from affiliates where id = %s",
                             (c.aff["id"],)).fetchone()
    assert linha["pix_key_enc"] and linha["pix_key_hash"]
    client = TestClient(dashboard.app)
    proibidos = [comissao.indicado.email, str(comissao.indicado.uid), comissao.fatura, PIX,
                 linha["pix_key_enc"], linha["pix_key_hash"],
                 "referred_user_id", "stripe_invoice_id", "pix_key_enc", "pix_key_hash"]
    for texto in (saque.text, client.get(ME, headers=c.h).text, client.get(V2_ME, headers=c.h).text):
        assert [p for p in proibidos if p in texto] == []


def test_fluxo_real_do_link_ao_pix_comissao_so_na_primeira_cobranca(monkeypatch):
    c = _afiliado()
    codigos = {}
    monkeypatch.setattr(email_service, "send_verification_email",
                        lambda to, code, *a, **k: codigos.update({to: code}) or True)
    monkeypatch.setattr(email_service, "send_welcome_email", lambda *a, **k: True)
    monkeypatch.setattr(email_service, "send_pro_charged_email", lambda *a, **k: True)
    garantir_system_event_logs()

    visitante = TestClient(dashboard.app)
    assert visitante.get(f"/r/{c.aff['code']}", follow_redirects=False).status_code == 302
    email = f"afil-novo-{uuid.uuid4().hex[:10]}@example.com"
    telefone = f"55119{uuid.uuid4().int % 100_000_000:08d}"
    r = visitante.post("/auth/register", headers=csrf(visitante),
                       json={"email": email, "password": SENHA, "phone": telefone, "name": "Bia"})
    assert r.status_code == 200, r.text
    r = visitante.post("/auth/verify-email", headers=csrf(visitante),
                       json={"email": email, "code": codigos[email]})
    assert r.status_code == 200, r.text
    novo = int(r.json()["user_id"])
    assert _me(c)["stats"]["referrals"] == 1

    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "sk_test_xxx")
    fake = _FakeStripe()
    monkeypatch.setitem(__import__("sys").modules, "stripe", fake)
    simular_stripe(monkeypatch, fake)  # sem reembolso: o "Marcar pago" confere a Stripe antes
    stripe = TestClient(dashboard.app)
    fake.subs["sub_afil"] = _fake_sub("active")

    def cobranca(evento, fatura, quando):
        objeto = {"metadata": {"finbot_user_id": str(novo)}, "subscription": "sub_afil",
                  "amount_paid": 60_000, "id": fatura}
        r = _post(stripe, fake, {"type": "invoice.paid", "id": evento, "created": quando,
                                 "data": {"object": objeto}})
        assert r.status_code == 200, r.text

    cobranca("evt_afil_1", f"in_a_{novo}", _T_LIFE)
    cobranca("evt_afil_1b", f"in_a_{novo}", _T_LIFE + 1)  # reentrega da mesma fatura
    cobranca("evt_afil_2", f"in_b_{novo}", _T_LIFE + 2)   # renovação
    m = _me(c)
    assert len(m["commissions"]) == 1
    assert m["stats"]["held"] == 60 and m["stats"]["available"] == 0

    assert _sacar(c).status_code == 400  # ainda em carência
    _conferida(m["commissions"][0]["id"])  # sem STRIPE_SECRET_KEY aqui: a marca fica, e é o lado seguro (A5)
    _vence(m["commissions"][0]["id"])
    r = _sacar(c)
    assert r.status_code == 200, r.text
    saque = r.json()["payout"]["id"]

    monkeypatch.setattr(admin_dashboard, "ADMIN_DASHBOARD_PASSWORD", "secret-admin")
    monkeypatch.setattr(admin_dashboard, "ADMIN_DASHBOARD_PASSWORD_HASH", "")
    admin = TestClient(dashboard.app, base_url="https://testserver")
    assert admin.post("/admin/auth/login", headers=csrf(admin), json={
        "username": admin_dashboard.ADMIN_DASHBOARD_USERNAME, "password": "secret-admin",
    }).status_code == 200
    r = admin.post(f"/admin/api/affiliates/payouts/{saque}/paid", headers=csrf(admin), json={})
    assert r.status_code == 200, r.text

    s = _me(c)["stats"]
    assert (s["held"], s["available"], s["requested"], s["paid"], s["generated"]) == (0, 0, 0, 60, 60)


# ── Trava do saque ───────────────────────────────────────────────────────────

def _esperando_lock(n):
    """`n` conexões paradas em lock de afiliado. Com prazo: sem a trava ninguém para."""
    fim = time.monotonic() + 5
    while time.monotonic() < fim:
        with get_conn() as conn:
            achadas = conn.execute(
                """
                select count(distinct l.pid) as n
                  from pg_locks l join pg_stat_activity a on a.pid = l.pid
                 where not l.granted and a.datname = current_database()
                   and a.query ilike '%%affiliate%%'
                """).fetchone()["n"]
        if achadas >= n:
            return True
        time.sleep(0.02)
    return False


def _segura_comissao(conn, comissao_id):
    conn.execute("select id from affiliate_commissions where id = %s for update", (comissao_id,))


def test_trava_saque_espera_quem_segura_o_afiliado():
    c = _afiliado()
    _comissao(c, 100)
    with ThreadPoolExecutor(1) as pool, get_conn() as segura:
        segura.execute("select id from affiliates where id = %s for no key update", (c.aff["id"],))
        pedido = pool.submit(request_payout, c.aff["id"])
        try:
            assert _esperando_lock(1)
            assert not pedido.done()
        finally:
            segura.commit()
        assert pedido.result(timeout=10)["amount_cents"] == 100_00


def test_trava_dois_pedidos_simultaneos_dao_um_saque_e_a_mensagem_certa():
    """Os dois passam da checagem de saque aberto sem a trava: o segundo só descobre
    na comissão e responde "abaixo do mínimo" para quem tem o saldo em análise."""
    c = _afiliado()
    comissao = _comissao(c, 100)
    with ThreadPoolExecutor(2) as pool, get_conn() as segura:
        _segura_comissao(segura, comissao.id)
        try:
            primeiro = pool.submit(request_payout, c.aff["id"])
            assert _esperando_lock(1)
            segundo = pool.submit(request_payout, c.aff["id"])
            assert _esperando_lock(2)
        finally:
            segura.commit()
        erros = [f.exception(timeout=10) for f in (primeiro, segundo)]
    recusas = [str(e) for e in erros if e]
    assert len(recusas) == 1 and "já tem um saque em análise" in recusas[0]
    assert [p["amount_cents"] for p in _saques(c)] == [100_00]


def test_trava_nao_segura_o_webhook_que_grava_comissao():
    """Saque em voo com a trava na mão: a FK da comissão nova segue passando."""
    c = _afiliado()
    comissao = _comissao(c, 100)
    novo = _conta(plano=False)
    assert record_referral(c.aff["code"], novo.uid)
    with ThreadPoolExecutor(2) as pool, get_conn() as segura:
        _segura_comissao(segura, comissao.id)
        try:
            pedido = pool.submit(request_payout, c.aff["id"])
            assert _esperando_lock(1)
            gravada = pool.submit(record_commission_for_invoice, novo.uid,
                                  f"in_{uuid.uuid4().hex}", 100_000)
            assert gravada.result(timeout=5)["amount_cents"] == 100_00
        finally:
            segura.commit()
        assert pedido.result(timeout=10)["amount_cents"] == 100_00


def test_trava_recusa_solta_a_linha_e_afiliado_sumido_e_value_error():
    c = _afiliado()
    with pytest.raises(ValueError, match="abaixo do mínimo"):
        request_payout(c.aff["id"])
    with get_conn() as conn:
        conn.execute("select id from affiliates where id = %s for no key update nowait", (c.aff["id"],))
        conn.rollback()
    with pytest.raises(ValueError, match="Afiliado não encontrado"):
        request_payout(-1)


# ── "Já pago" ────────────────────────────────────────────────────────────────

def test_ja_pago_nao_encolhe_quando_o_indicado_exclui_a_conta():
    c = _afiliado()
    comissao = _comissao(c, 100)
    assert mark_payout_paid(request_payout(c.aff["id"])["id"])
    assert get_affiliate_stats(c.aff["id"])["paid_cents"] == 100_00

    db.delete_user_data(comissao.indicado.uid)

    with get_conn() as conn:  # a comissão fica, sem a pessoa
        assert conn.execute("select referred_user_id from affiliate_commissions where id = %s",
                            (comissao.id,)).fetchone() == {"referred_user_id": None}
    s = _me(c)["stats"]
    assert (s["paid"], s["generated"]) == (100, 100)


def _saque_de_100_e_indicado_de_60_excluido():
    c = _afiliado()
    excluido = _comissao(c, 60)
    _comissao(c, 40)
    saque = request_payout(c.aff["id"])["id"]
    db.delete_user_data(excluido.indicado.uid)
    return c, saque


def test_em_saque_nao_encolhe_quando_o_indicado_exclui_a_conta():
    c, saque = _saque_de_100_e_indicado_de_60_excluido()
    s = _me(c)["stats"]
    assert (s["requested"], s["paid"], s["generated"]) == (100, 0, 100)
    assert mark_payout_paid(saque)
    s = _me(c)["stats"]
    assert (s["requested"], s["paid"], s["generated"]) == (0, 100, 100)


def test_rejeicao_depois_da_exclusao_devolve_a_comissao_inteira():
    c, saque = _saque_de_100_e_indicado_de_60_excluido()
    assert reject_payout(saque)
    s = _me(c)["stats"]
    assert (s["available"], s["requested"], s["paid"], s["generated"]) == (100, 0, 0, 100)


def test_ja_pago_conta_so_o_saque_pago_e_so_o_do_afiliado():
    a, b = _afiliado(), _afiliado()
    _comissao(a, 100)
    _comissao(b, 70)
    assert mark_payout_paid(request_payout(a.aff["id"])["id"])
    assert reject_payout(request_payout(b.aff["id"])["id"])
    assert get_affiliate_stats(a.aff["id"])["paid_cents"] == 100_00
    assert get_affiliate_stats(b.aff["id"])["paid_cents"] == 0
