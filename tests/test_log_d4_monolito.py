"""PR-9c2 da fatia D (#657, D4): os prints de cadastro, cobrança e WebSocket do monólito sem `str(exc)`.

Cada ponto imprime a causa (`motivo=… sqlstate=… status=…`) no lugar do texto do erro, pela rota real:
o cadastro pelo `/auth/verify-email`, o webhook do Stripe com assinatura de verdade (`_enviar` de
`test_afiliado_reembolso.py`), a troca de plano pela rota e o socket pelo `websocket_connect`.
`_erro_real` é um 22P02 de verdade, com o texto do usuário (`_ALHEIO`) na mensagem.

Forma por caso: o prefixo seguido da causa PRESENTE (sem ele, um print que nunca roda passaria
só pela ausência), o texto do erro AUSENTE da saída inteira, e o desfecho de hoje (o positivo):
200, o estado que o evento grava, a sessão do cadastro, o socket vivo (`ping` → `pong`).

O dublê é sempre a função que o `try` chama. `_user_email`, `send_email`, `send_founder_email_once`
por dentro e `recent_event_exists` por dentro engolem a própria falha e nunca chegam ao print.

CONTROLES (CLAUDE.md §3), medidos no PR: `{exc}` de volta em 1 ponto de cada evento do webhook, no
`change-plan`, num `_apply_*` e no `[ws]` → o caso daquele ponto vermelho; sem o `t = None` → W2
vermelho na 1ª mensagem (o socket cai); `payload.get('type')` de volta no `[ws]` → W2 vermelho na 3ª.

CLASSE CEGA: a forma da linha no log do Railway só se vê em produção.
"""
from __future__ import annotations

import json
import uuid

import pytest
import stripe
from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import sessao_de
from _billing_grants_helpers import garantir_system_event_logs
from _log_dono_helpers import _ALHEIO, _erro_real, _marcador, _uid, _vazou
from conftest import promote_to_pro
from db.connection import get_conn
from db_support import invalidate_auth_user_cache
from test_afiliado_reembolso import _enviar, mundo  # noqa: F401 (`mundo` é fixture)
from test_billing_checkout import _reset_rate_limiter  # noqa: F401 (fixture autouse)
from test_billing_webhook_lifecycle import _cleanup_trial, _fake_sub
from test_of_log_dono_coluna import _levanta
from test_prospect_referrals import _signup
from test_ws_subscription_gate import _connect

CAUSA = "motivo=InvalidTextRepresentation sqlstate=22P02 status=None"


def _saida(capsys) -> str:
    s = capsys.readouterr()
    return s.out + s.err


def _confere(saida: str, *prefixos: str) -> None:
    for p in prefixos:
        assert f"{p}{CAUSA}" in saida, (p, saida)
    assert not (v := _vazou(saida, str(_ALHEIO))), f"{v!r} vazou: {saida}"


def _dubla(mp, alvo: str, erro) -> None:
    """`alvo` é o atributo de módulo que o `try` resolve na hora da chamada. O dublê leva o nome do
    original: o `_fire_email` imprime `fn.__name__`."""
    f = _levanta(erro)
    f.__name__ = alvo.rsplit(".", 1)[1]
    mp.setattr(alvo, f)


# ── cadastro ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("cookie, alvo, prefixo", [
    ("ref_code", "db.affiliates.record_referral", "[affiliates] atribuicao de referral falhou user={u}: "),
    ("prospect_code", "db.prospects.record_prospect_referral",
     "[prospects] atribuicao de prospect falhou user={u}: "),
], ids=["referral", "prospect"])
def test_cadastro_com_a_atribuicao_falhando_imprime_a_causa(monkeypatch, capsys, cookie, alvo, prefixo):
    _dubla(monkeypatch, alvo, _erro_real())
    client = TestClient(dashboard.app)
    client.cookies.set(cookie, f"c{uuid.uuid4().hex[:10]}")
    r = _signup(client, cookie_code=None)["response"]
    uid = int(r.json()["user_id"])
    _confere(_saida(capsys), prefixo.format(u=uid))
    cookies = r.headers.get_list("set-cookie")
    assert any(h.startswith(f"{dashboard.DASHBOARD_COOKIE_NAME}=") for h in cookies), cookies
    assert any(h.startswith(f"{cookie}=") and "max-age=0" in h.lower() for h in cookies), cookies


# ── troca de plano ───────────────────────────────────────────────────────────

def test_change_plan_com_o_email_falhando_imprime_a_causa_e_agenda(monkeypatch, capsys):
    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setattr(dashboard, "_exigir_credencial", lambda uid: None)
    monkeypatch.setattr(dashboard, "_resolve_price_id", lambda plan, interval: "price_novo")
    monkeypatch.setattr(dashboard, "_find_active_subscription", lambda s, cust: {"id": "sub_x", "schedule": "sch_x"})
    monkeypatch.setattr(dashboard, "_sub_price_id", lambda sub: "price_velho")
    monkeypatch.setattr(dashboard, "_sub_period_end_ts", lambda sub: 2_000_000_000)
    monkeypatch.setattr(stripe.SubscriptionSchedule, "retrieve",
                        lambda *a, **k: {"id": "sch_x", "phases": [{"start_date": 1_900_000_000}]})
    monkeypatch.setattr(stripe.SubscriptionSchedule, "modify", lambda *a, **k: {"id": "sch_x"})
    _dubla(monkeypatch, "core.services.email_service.send_plan_change_scheduled_email", _erro_real())
    uid = _uid()
    db.set_stripe_customer(uid, f"cus_{uid}")
    c = TestClient(dashboard.app)
    c.headers.update({"Authorization": f"Bearer {sessao_de(uid)['access']}",
                      dashboard.APP_CLIENT_HEADER: "app", "Content-Type": "application/json"})
    r = c.post("/billing/change-plan", json={"plan": "pro", "interval": "monthly"})
    assert r.status_code == 200, r.text
    assert r.json()["effective_at"] == "2033-05-18", r.json()
    _confere(_saida(capsys), f"[billing] email troca agendada user={uid}: ")


# ── webhook do Stripe ────────────────────────────────────────────────────────

_CAPI, _GA4 = "core.services.meta_capi.capi_configured", "core.services.ga4_mp.mp_configured"
_ES, _AN = "core.services.email_service", "core.services.admin_notify"

# evento → (o que levanta, prefixos esperados, (plano, status) gravados).
_EVENTOS = {
    "checkout.session.completed": (
        ["core.observability.recent_event_exists", f"{_ES}.send_pro_welcome_email",
         f"{_ES}.send_founder_email_once", f"{_AN}.notify_new_pro", _CAPI, _GA4],
        ["[billing] dedup de email falhou user={u}: ", "[billing] email send_pro_welcome_email falhou user={u}: ",
         "[billing] email do fundador falhou user={u}: ", "[billing] admin notify falhou user={u}: ",
         "[billing] rastreio checkout: log falhou: ", "[billing] meta capi checkout (trialing) falhou user={u}: ",
         "[billing] ga4 purchase (checkout) falhou user={u}: "],
        ("pro", "trialing")),
    # A falha da comissão não é print: vira o log `affiliate_commission_failed`, coberto pela
    # conversa real em `test_afiliado_comissao_primeira.py` (e este usuário não tem indicação).
    "invoice.paid": (
        [_CAPI, _GA4],
        ["[billing] rastreio invoice: log falhou: ",
         "[billing] meta capi invoice purchase falhou user={u}: ", "[billing] ga4 purchase (invoice) falhou user={u}: "],
        ("pro", "active")),
    "invoice.payment_failed": (
        [f"{_AN}.notify_payment_failed"],
        ["[billing] admin notify payment_failed falhou user={u}: "],
        (None, "past_due")),
    "customer.subscription.deleted": (
        [f"{_ES}.send_subscription_canceled_email", f"{_AN}.notify_subscription_canceled"],
        ["[billing] email canceled falhou user={u}: ", "[billing] admin notify canceled falhou user={u}: "],
        ("free", "canceled")),
}


def _objeto(tipo: str, uid: int, sub_id: str) -> dict:
    meta = {"finbot_user_id": str(uid)}
    return {
        "checkout.session.completed": {"id": f"cs_{uid}", "metadata": meta, "subscription": sub_id},
        "invoice.paid": {"id": f"in_{uid}", "metadata": meta, "subscription": sub_id, "amount_paid": 1990,
                         "currency": "brl", "billing_reason": "subscription_cycle"},
        "invoice.payment_failed": {"id": f"in_{uid}", "metadata": meta, "attempt_count": 1},
        "customer.subscription.deleted": {"id": sub_id, "metadata": meta},
    }[tipo]


@pytest.mark.parametrize("tipo", list(_EVENTOS))
def test_webhook_com_o_efeito_falhando_imprime_a_causa_e_responde_200(mundo, monkeypatch, capsys, tipo):  # noqa: F811
    garantir_system_event_logs()
    alvos, prefixos, (plano, status) = _EVENTOS[tipo]
    uid = int(db.register_auth_user(f"wh9c-{uuid.uuid4().hex[:10]}@t.com", "senha-forte-123")["user_id"])
    sub_id = f"sub_9c_{uid}"
    with get_conn() as conn:   # o trial ancora no telefone; o cancelamento parte de um plano pago
        conn.execute("update auth_accounts set phone_hash = %s, plan = %s where user_id = %s",
                     (f"ph_wh_{uid}", "pro" if plano == "free" else "free", uid))
        conn.commit()
    invalidate_auth_user_cache(uid)   # escrita em SQL cru; o `get_auth_user` tem cache
    mundo.subs[sub_id] = _fake_sub("trialing" if tipo == "checkout.session.completed" else "active")
    erro = _erro_real()
    for alvo in alvos:
        _dubla(monkeypatch, alvo, erro)
    try:
        r = _enviar(tipo, _objeto(tipo, uid, sub_id))
        assert r.status_code == 200, r.text
        _confere(_saida(capsys), *(p.format(u=uid) for p in prefixos))
        conta = db.get_auth_user(uid)
        assert (conta["plan"] if plano else None, conta["last_payment_status"]) == (plano, status), conta
    finally:
        _cleanup_trial(uid)


# ── WebSocket ────────────────────────────────────────────────────────────────

def _vivo(ws) -> None:
    """O socket sobreviveu ao erro: `ping` → `pong`, e um `refresh` válido → `update`."""
    ws.send_text(json.dumps({"type": "ping"}))
    assert ws.receive_json()["type"] == "pong"
    ws.send_text(json.dumps({"type": "refresh"}))
    assert ws.receive_json()["type"] == "update"


def _socket(uid: int, capsys, *mensagens: str) -> str:
    promote_to_pro(uid)
    with _connect(TestClient(dashboard.app), uid) as ws:
        assert ws.receive_json()["type"] == "snapshot"
        for m in mensagens:
            ws.send_text(m)
        _vivo(ws)
    return _saida(capsys)


def test_w1_campo_do_cliente_invalido_imprime_a_causa(user_id, capsys):
    mark = _marcador()
    saida = _socket(user_id, capsys, json.dumps({"type": "refresh", "page": mark}))
    assert "[ws] error handling message type=refresh: motivo=ValueError sqlstate=None status=None" in saida, saida
    assert mark not in saida, saida


def test_w2_json_quebrado_nao_ecoa_o_tipo_da_mensagem_anterior(user_id, capsys):
    """`{x` antes de qualquer `type` lido (é o que exige o `t = None`), um texto que não é JSON (vira
    `{"type": <texto>}`, nenhum ramo, nenhum print) e `{x` de novo, que hoje imprimia o texto."""
    mark = _marcador()
    saida = _socket(user_id, capsys, "{x", mark, "{x")
    linhas = [ln for ln in saida.splitlines() if ln.startswith("[ws] error handling message")]
    assert linhas == ["[ws] error handling message type=outro: motivo=JSONDecodeError sqlstate=None status=None"] * 2, \
        saida
    assert mark not in saida, saida
