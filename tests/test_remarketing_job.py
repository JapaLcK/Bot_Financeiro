"""Régua de remarketing, PR 6 (docs/plano-remarketing.md): o lote `rodar_uma_vez`.

Postgres real; só o envio é falso (`send_email` e `send_template`). Controles rodados no PR 6:
- `ligado()` sempre devolvendo um instante         → `test_desligado_...` vermelho;
- janelas trocadas (ex.: E1 até 13 h)              → `test_janelas` vermelho;
- `do nothing` trocado por `do update` no claim    → `test_claim_...` e `test_duas_threads_...` vermelhos;
- sem a releitura antes do WhatsApp                → `test_relê_...` vermelho;
- `user_id <> %(uid)s` tirado de `numero_de_outra_conta` → `test_so_a_propria_conta_...` vermelho;
- sem o `em_andamento`                             → `test_adia_...` vermelho.
"""
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest

import db
import db.remarketing as rm
from _lifespan_probe import sondar
from conftest import _cleanup_user
from core.services import email_service, remarketing
from core.services.remarketing_emails import montar
from core.services.retomada import ler_token
from db.connection import get_conn
from tests._helpers_pii import bind_identity_pii, insert_auth_account_pii

AGORA = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
DESDE = AGORA - timedelta(days=30)


def _sql(q, a=()):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(q, a)
        linhas = cur.fetchall() if cur.description else None
        conn.commit()
    return linhas


def _fone():
    return f"5565{9}{uuid.uuid4().int % 100_000_000:08d}"


@pytest.fixture()
def mundo(monkeypatch):
    """Cria contas com T0 e troca os dois envios por espiões."""
    uids, enviados = [], {"email": [], "wa": []}
    monkeypatch.setenv("REMARKETING_DESDE", DESDE.isoformat())
    for e in (1, 2, 3):
        monkeypatch.setenv(f"WA_TEMPLATE_REMARKETING_{e}", f"rm{e}_tpl")
    monkeypatch.setattr(email_service, "send_email", lambda to, subject, html, **kw: enviados["email"].append(
        {"to": to, "html": html, **kw}) or True)
    import adapters.whatsapp.wa_client as wa
    monkeypatch.setattr(wa, "send_template", lambda to, nome, **kw: enviados["wa"].append(
        {"to": to, "nome": nome, **kw}) or {"messages": []})

    def conta(t0_atras=timedelta(hours=1), origem="assinar", fone="auto", **cols):
        uid = int(uuid.uuid4().int % 10_000_000_000)
        db.ensure_user(uid)
        uids.append(uid)
        fone = _fone() if fone == "auto" else fone
        with get_conn() as conn, conn.cursor() as cur:
            insert_auth_account_pii(cur, uid, f"rm-{uuid.uuid4().hex[:10]}@example.com", phone=fone)
            for col, val in cols.items():
                cur.execute(f"update auth_accounts set {col} = %s where user_id = %s", (val, uid))
            cur.execute("insert into remarketing_regua (user_id, t0, origem) values (%s, %s, %s)",
                        (uid, AGORA - t0_atras, origem))
            conn.commit()
        return uid, fone

    mundo = type("Mundo", (), {"conta": staticmethod(conta), "enviados": enviados, "uids": uids})
    yield mundo
    for u in uids:
        _cleanup_user(u)


def _envio(uid, etapa=1):
    linhas = _sql("select * from remarketing_envios where user_id = %s and etapa = %s", (uid, etapa))
    return linhas[0] if linhas else None


# ── 1. desligado = zero queries ──────────────────────────────────────────────

@pytest.mark.parametrize("valor", ["", "   ", "lixo", "2026-11-20T12:00:00"])   # vazia, inválida, sem fuso
def test_desligado_nao_toca_o_banco(monkeypatch, valor):
    monkeypatch.setenv("REMARKETING_DESDE", valor)

    def proibido(*a, **k):
        raise AssertionError("tocou o banco")
    monkeypatch.setattr(rm, "get_conn", proibido)
    assert remarketing.ligado() is None
    assert remarketing.rodar_uma_vez(AGORA) == 0


def test_ligado_com_fuso():
    import os
    os.environ["REMARKETING_DESDE"] = "2026-11-20T09:00:00-03:00"
    try:
        assert remarketing.ligado() == datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
    finally:
        del os.environ["REMARKETING_DESDE"]


# ── 2. janelas ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("atras,etapa", [
    (timedelta(minutes=29, seconds=59), None), (timedelta(minutes=30), 1), (timedelta(hours=11, minutes=59), 1),
    (timedelta(hours=12), None), (timedelta(hours=23, minutes=59), None), (timedelta(hours=24), 2),
    (timedelta(hours=35, minutes=59), 2), (timedelta(hours=36), None), (timedelta(hours=47), None),
    (timedelta(hours=48), 3), (timedelta(hours=59, minutes=59), 3), (timedelta(hours=60), None)])
def test_janelas(mundo, atras, etapa):
    uid, _ = mundo.conta(t0_atras=atras)
    for anterior in range(1, (etapa or 1)):          # E2/E3 só com a anterior enviada
        _sql("insert into remarketing_envios (user_id, etapa, status) values (%s, %s, 'enviado')", (uid, anterior))
    assert [e for u, e in rm.candidatos(DESDE, AGORA) if u == uid] == ([etapa] if etapa else [])


def test_t0_antes_do_desde_e_etapa_ja_reivindicada_nao_voltam(mundo):
    velho, _ = mundo.conta()
    _sql("update remarketing_regua set t0 = %s where user_id = %s", (DESDE - timedelta(minutes=1), velho))
    feito, _ = mundo.conta()
    assert rm.reivindicar(feito, 1)
    assert [u for u, _e in rm.candidatos(DESDE, AGORA) if u in (velho, feito)] == []


# ── 3. claim atômico ─────────────────────────────────────────────────────────

def test_claim_so_um_ganha(mundo):
    uid, _ = mundo.conta()
    assert rm.reivindicar(uid, 1) is True
    assert rm.reivindicar(uid, 1) is False
    assert _envio(uid)["status"] == "enviando"


def test_duas_threads_um_so_envio(mundo):
    uid, _ = mundo.conta()
    barreira, erros = threading.Barrier(2), []

    def roda():
        try:
            barreira.wait()
            remarketing._um(uid, 1, AGORA)
        except Exception as exc:   # pragma: no cover
            erros.append(exc)
    ts = [threading.Thread(target=roda) for _ in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not erros
    assert len(mundo.enviados["email"]) == 1 and len(mundo.enviados["wa"]) == 1


# ── caminho feliz e a forma do envio ─────────────────────────────────────────

def test_envia_os_dois_canais_e_fecha(mundo):
    uid, fone = mundo.conta()
    assert remarketing.rodar_uma_vez(AGORA) >= 1
    e, w = mundo.enviados["email"][0], mundo.enviados["wa"][0]
    token = re.search(r"/retomar\?t=([^&]+)&utm_source=email&utm_medium=remarketing&utm_campaign=rm1", e["html"].replace("&amp;", "&")).group(1)
    assert ler_token(token) == (uid, 1)
    assert e["from_addr"] == email_service.EMAIL_FROM_FOUNDER and e["log_recipient"] is False
    assert "List-Unsubscribe" in e["headers"] and f"uid={uid}" in e["headers"]["List-Unsubscribe"]
    assert (w["nome"], w["language_code"]) == ("rm1_tpl", "pt_BR") and ler_token(w["url_button_param"]) == (uid, 1)
    assert w["to"] == fone
    r = _envio(uid)
    assert (r["status"], r["email_ok"], r["whatsapp_ok"], r["motivo"]) == ("enviado", True, True, None)


# ── 4. relê o estado entre os canais ─────────────────────────────────────────

def test_relê_o_estado_entre_os_canais(mundo, monkeypatch):
    uid, _ = mundo.conta()

    def email_e_depois_comprou(to, subject, html, **kw):
        mundo.enviados["email"].append(to)
        _sql("update auth_accounts set last_payment_status = 'active' where user_id = %s", (uid,))
        return True
    monkeypatch.setattr(email_service, "send_email", email_e_depois_comprou)
    remarketing._um(uid, 1, AGORA)
    assert len(mundo.enviados["email"]) == 1 and mundo.enviados["wa"] == []
    r = _envio(uid)
    assert (r["email_ok"], r["whatsapp_ok"], r["motivo"]) == (True, None, "wa:barrado")


# ── 5. opt-outs independentes ────────────────────────────────────────────────

def test_opt_out_de_email_corta_so_o_email(mundo):
    uid, _ = mundo.conta(engagement_opt_out=True)
    remarketing._um(uid, 1, AGORA)
    assert mundo.enviados["email"] == [] and len(mundo.enviados["wa"]) == 1


def test_opt_out_de_whatsapp_corta_so_o_whatsapp(mundo):
    uid, _ = mundo.conta(whatsapp_updates_opt_out=True)
    remarketing._um(uid, 1, AGORA)
    assert len(mundo.enviados["email"]) == 1 and mundo.enviados["wa"] == []
    assert _envio(uid)["motivo"] == "wa:opt_out"


# ── 6. adiamento ─────────────────────────────────────────────────────────────

def _started(uid, minutos, sessao="cs_x", extra_kind=None):
    sessao += uuid.uuid4().hex[:6]
    for kind in ["started"] + ([extra_kind] if extra_kind else []):
        _sql("insert into checkout_funnel_events (user_id, session_id, kind, created_at) values (%s, %s, %s, %s)",
             (uid, sessao, kind, AGORA - timedelta(minutes=minutos)))


def _pix(uid, status, minutos):
    ref = uuid.uuid4().int % 10**15
    _sql("insert into pix_charges (user_id, external_reference, plan, plan_stored, price_cents, credit_cents,"
         " amount_cents, public_token, status, created_at) values (%s, %s, 'pro', 'pro', 1, 0, 1, %s, %s, %s)",
         (uid, f"pix:{ref}", str(ref), status, AGORA - timedelta(minutes=minutos)))


@pytest.mark.parametrize("quando,adia", [
    (lambda u: _started(u, 10), True), (lambda u: _started(u, 90), False),
    (lambda u: _started(u, 10, extra_kind="expired"), False),
    (lambda u: _pix(u, "pending", 10), True), (lambda u: _pix(u, "draft", 10), True),
    (lambda u: _pix(u, "pending", 90), False), (lambda u: _pix(u, "canceled", 10), False)])
def test_adia_com_checkout_aberto(mundo, quando, adia):
    uid, _ = mundo.conta()
    quando(uid)
    remarketing._um(uid, 1, AGORA)
    assert (_envio(uid) is None) == adia
    assert (mundo.enviados["email"] == []) == adia


# ── 7. número de outra conta ─────────────────────────────────────────────────

def test_numero_de_outra_conta_pula_o_whatsapp(mundo):
    uid, fone = mundo.conta()
    outro, _ = mundo.conta()                        # `phone_hash` é unique: o mesmo número só como identidade
    with get_conn() as conn, conn.cursor() as cur:
        bind_identity_pii(cur, "whatsapp", fone, outro)
        conn.commit()
    remarketing._um(uid, 1, AGORA)
    assert len(mundo.enviados["email"]) == 1 and mundo.enviados["wa"] == []
    assert _envio(uid)["motivo"] == "wa:outra_conta"


def test_variante_do_nono_digito(mundo):
    uid, _ = mundo.conta(fone="5511987654321")
    mundo.conta(fone="551187654321")               # o mesmo celular sem o 9
    assert rm.numero_de_outra_conta(uid, "5511987654321") is True


def test_identidade_de_whatsapp_de_outro_usuario(mundo):
    uid, fone = mundo.conta()
    outro = int(uuid.uuid4().int % 10_000_000_000)
    db.ensure_user(outro)
    mundo.uids.append(outro)
    with get_conn() as conn, conn.cursor() as cur:
        bind_identity_pii(cur, "whatsapp", fone, outro)
        conn.commit()
    assert rm.numero_de_outra_conta(uid, fone) is True


def test_so_a_propria_conta_nao_bloqueia(mundo):
    uid, fone = mundo.conta()
    with get_conn() as conn, conn.cursor() as cur:
        bind_identity_pii(cur, "whatsapp", fone, uid)   # a identidade é dela mesma
        conn.commit()
    assert rm.numero_de_outra_conta(uid, fone) is False
    remarketing._um(uid, 1, AGORA)
    assert len(mundo.enviados["wa"]) == 1


# ── 8. origem, template, telefone ────────────────────────────────────────────

def test_origem_precos_so_email(mundo):
    uid, _ = mundo.conta(origem="precos")
    remarketing._um(uid, 1, AGORA)
    assert len(mundo.enviados["email"]) == 1 and mundo.enviados["wa"] == []
    assert _envio(uid)["motivo"] == "wa:origem"


def test_sem_template_e_sem_telefone(mundo, monkeypatch):
    sem_fone, _ = mundo.conta(fone=None)
    remarketing._um(sem_fone, 1, AGORA)
    assert _envio(sem_fone)["motivo"] == "wa:sem_telefone"
    monkeypatch.setenv("WA_TEMPLATE_REMARKETING_2", "")
    com_fone, _ = mundo.conta()
    remarketing._um(com_fone, 2, AGORA)
    assert _envio(com_fone, 2)["motivo"] == "wa:sem_template"
    assert mundo.enviados["wa"] == [] and len(mundo.enviados["email"]) == 2


# ── 9. isolamento A × B ──────────────────────────────────────────────────────

def test_nada_de_b_no_envio_de_a(mundo):
    a, fa = mundo.conta()
    b, fb = mundo.conta()
    remarketing._um(a, 1, AGORA)
    assert len(mundo.enviados["email"]) == 1 and len(mundo.enviados["wa"]) == 1
    e, w = mundo.enviados["email"][0], mundo.enviados["wa"][0]
    assert w["to"] == fa and fb not in str(mundo.enviados) and ler_token(w["url_button_param"]) == (a, 1)
    assert f"uid={a}" in e["headers"]["List-Unsubscribe"] and f"uid={b}" not in str(e)
    assert _envio(b) is None


# ── 10. falha de um não derruba o lote ───────────────────────────────────────

def test_falha_de_um_nao_derruba_o_lote(mundo, monkeypatch):
    ruim, _ = mundo.conta(t0_atras=timedelta(hours=1))
    bom, _ = mundo.conta(t0_atras=timedelta(hours=2))
    real = rm.ja_comprou
    monkeypatch.setattr(rm, "ja_comprou", lambda u: (_ for _ in ()).throw(RuntimeError("x")) if u == ruim else real(u))
    remarketing.rodar_uma_vez(AGORA)
    assert _envio(bom)["status"] == "enviado" and _envio(ruim) is None


def test_falha_do_email_nao_corta_o_whatsapp(mundo, monkeypatch):
    uid, _ = mundo.conta()
    monkeypatch.setattr(email_service, "send_email", lambda *a, **k: False)
    remarketing._um(uid, 1, AGORA)
    r = _envio(uid)
    assert (r["status"], r["email_ok"], r["whatsapp_ok"]) == ("enviado", False, True)


# ── 11. exclusão e quem já comprou ───────────────────────────────────────────

def test_exclusao_agendada_e_ja_comprou_nao_enviam(mundo):
    saindo, _ = mundo.conta(deletion_status="scheduled", deletion_scheduled_for=AGORA + timedelta(days=5))
    comprou, _ = mundo.conta(last_payment_status="active")
    for u, motivo in ((saindo, "excluido"), (comprou, "comprou")):
        remarketing._um(u, 1, AGORA)
        r = _envio(u)                    # fechada: sai da fila do `candidatos`
        assert (r["status"], r["motivo"]) == ("sem_canal", motivo)
    assert mundo.enviados == {"email": [], "wa": []}


# ── 12. forma do botão, payload sem o kwarg, HTML ────────────────────────────

def _payload_do_template(monkeypatch, **kw):
    import adapters.whatsapp.wa_client as wa
    vistos = []
    monkeypatch.setattr(wa, "_wa_config", lambda **_k: ("tok", "123", "http://graph.test"))
    resp = type("R", (), {"status_code": 200, "text": "{}", "json": lambda self: {"messages": []}})()
    monkeypatch.setattr(wa.requests, "post", lambda url, headers, json, timeout: vistos.append(json) or resp)
    wa.send_template("5565999990000", "tpl", **kw)
    return vistos[0]


def test_botao_de_url_e_payload_antigo_intacto(monkeypatch):
    base = {"messaging_product": "whatsapp", "to": "5565999990000", "type": "template",
            "template": {"name": "tpl", "language": {"code": "pt_BR"}}}
    assert _payload_do_template(monkeypatch) == base
    com = _payload_do_template(monkeypatch, url_button_param="TOK")
    assert com["template"]["components"] == [{"type": "button", "sub_type": "url", "index": "0",
                                              "parameters": [{"type": "text", "text": "TOK"}]}]


@pytest.mark.parametrize("etapa", [1, 2, 3])
def test_html_com_desconto_e_escapa(etapa):
    assunto, html, texto = montar(etapa, 'https://x.test/r?a=1&b="2"', 'https://x.test/u?a=<s>&b=2', "https://x.test")
    assert not re.search(r"\bOFF\b", assunto)   # o desconto está no corpo (PR 6b), não no assunto
    assert 'b="2"' not in html and "&quot;" in html and "<s>" not in html and "&lt;s&gt;" in html
    assert "https://x.test/u?a=<s>&b=2" in texto


def test_processo_do_app_liga_o_worker():
    resultado, diagnostico = sondar({"remarketing": "core.services.remarketing:rodar_uma_vez"})
    assert resultado == {"remarketing": True, "do_disco": []}, diagnostico
