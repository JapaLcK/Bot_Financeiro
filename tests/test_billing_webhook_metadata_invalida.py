"""`metadata.finbot_user_id` inválido no `POST /billing/webhook` (#859).

`_resolve_user` fazia `int(uid)` sobre texto vindo de fora. `"abc"` dava 500 e o
Stripe reentregava por dias; pior, `" 12 "`, `"1_000"`, `"+12"`, `"012"` e dígitos
árabes o `int()` ACEITA — o evento agia sobre OUTRO usuário (isolamento). Agora
`stripe_sdk.uid_de_metadata` só aceita dígitos ASCII canônicos de 1 a 2^63-1; o resto
vale como "ausente": tenta o `customer`, senão 200 sem efeito + warning sem o valor cru.
Os 6 ramos que chamam `_resolve_user` estão aqui (5 de `_RAMOS` + `checkout.session.expired`).

CONTROLES (medidos no G1; saídas no relato do PR) — em `_resolve_user`, volte para
`if uid: return int(uid)`:
  * `test_valor_invalido_responde_200` VERMELHO nos valores que o `int()` rejeita
    ("abc", "12.0", "9"*30, "-5"...) em todos os ramos (exceção/500);
  * `test_valor_que_int_aceitaria_nao_age_sobre_outro_usuario` VERMELHO nos ramos
    com efeito visível (plano/status/grant/funil/e-mail do usuário vizinho).
POSITIVOS do grupo: `test_valor_valido_continua_resolvendo_o_dono` (o conserto não
recusa tudo) e `test_valor_invalido_cai_no_customer` (invalido = ausente, e ausente
já resolvia pelo customer).
FORA DE ESCOPO (D4b): id canônico mas inexistente ("999999999") segue 500 por FK.
"""
from __future__ import annotations

import pytest

import db
from _billing_grants_helpers import conta, espiao_email, garantir_system_event_logs, set_customer
from db.connection import get_conn
from test_billing_webhook_event_loop_db import _RAMOS, _espiar
from test_billing_dunning_eventos import _SUB
from test_billing_webhook_lifecycle import _T_LIFE, _cleanup_trial, _fake_sub, _post, _setup

_RAMOS = dict(_RAMOS) | {
    "checkout.session.expired": (
        lambda uid: {"type": "checkout.session.expired", "id": "evt_inv_exp", "created": _T_LIFE,
                     "data": {"object": {"metadata": {"finbot_user_id": str(uid)},
                                         "id": "cs_inv_exp"}}}, None),
}
_AVISO = "stripe_metadata_user_invalido"
# O que o `int()` rejeita, o que ele aceitaria com outro valor, e as bordas do bigint.
INVALIDOS = ["abc", "12.0", "-5", "9" * 30, "9" * 5000, "9223372036854775808", "1_000", "٣",
             " 12 ", "+5", "007", "0", "1e3", "١٢", "²", "①"]


_RAMO = "checkout.session.completed"
# Todos os ramos recebem os valores CURTOS; o resto da lista roda só no ramo de dinheiro
# (a função é uma só, o que muda por ramo é o call site e o efeito).
_CURTOS = ["abc", "٣", "9" * 30]
_VALORES = [(t, v) for t in _RAMOS for v in _CURTOS] + [(_RAMO, v) for v in INVALIDOS if v not in _CURTOS]
_FORMAS = [(t, f) for t in _RAMOS for f in ("espacos", "underscore")] + [
    (_RAMO, f) for f in ("mais", "zero", "arabe")]


@pytest.fixture(autouse=True)
def _event_logs():
    garantir_system_event_logs()


def _com_metadata(tipo: str, valor, **objeto):
    evento = _RAMOS[tipo][0](0)
    obj = evento["data"]["object"]
    obj["metadata"] = {"finbot_user_id": valor}
    obj.update(objeto)
    return evento


def _subs(tipo):
    status = _RAMOS[tipo][1]
    return {_SUB: _fake_sub(status)} if status else None


def _avisos() -> list[str]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select details::text as d from system_event_logs where event_type = %s", (_AVISO,))
        return [r["d"] for r in cur.fetchall()]


def _vizinho(user_id, sufixo):
    """Usuário real e `pro` ativo: `deleted` rebaixa, `payment_failed` marca, `checkout` concede."""
    from datetime import datetime, timedelta, timezone
    vid = int(db.register_auth_user(f"viz-{sufixo}-{user_id}@t.com", "senha-forte-123")["user_id"])
    conta(vid, "pro", datetime.now(timezone.utc) + timedelta(days=20), "active")
    return vid


def _foto(uid):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select plan, plan_expires_at, last_payment_status, past_due_since"
                    " from auth_accounts where user_id = %s", (uid,))
        conta_ = dict(cur.fetchone())
        cur.execute("select count(*) as n from plan_grants where user_id = %s", (uid,))
        grants = cur.fetchone()["n"]
        cur.execute("select count(*) as n from checkout_funnel_events where user_id = %s", (uid,))
        return conta_, grants, cur.fetchone()["n"]


@pytest.mark.parametrize("tipo,valor", _VALORES)
def test_valor_invalido_responde_200(user_id, monkeypatch, tipo, valor):
    uid, client, fake = _setup(monkeypatch, f"inv-{user_id}")
    antes = len(_avisos())
    try:
        r = _post(client, fake, _com_metadata(tipo, valor), subs=_subs(tipo))
        assert r.status_code == 200, r.text
        assert _foto(uid)[1:] == (0, 0), "ramo sem usuário resolvido escreveu algo"
        novos = _avisos()[antes:]
        assert len(novos) == 1 and all(valor not in d for d in novos if len(valor) > 2), novos
    finally:
        _cleanup_trial(uid)


def test_ausente_ou_vazio_nao_gera_aviso(user_id, monkeypatch):
    """`""` e metadata ausente são o caminho de sempre (`invoice.*`): sem warning."""
    uid, client, fake = _setup(monkeypatch, f"aus-{user_id}")
    antes = len(_avisos())
    try:
        assert _post(client, fake, _com_metadata("checkout.session.expired", "")).status_code == 200
        assert len(_avisos()) == antes
    finally:
        _cleanup_trial(uid)


@pytest.mark.parametrize("tipo,forma", _FORMAS)
def test_valor_que_int_aceitaria_nao_age_sobre_outro_usuario(user_id, monkeypatch, tipo, forma):
    uid, client, fake = _setup(monkeypatch, f"viz-{user_id}")
    vid = _vizinho(user_id, forma)
    n = str(vid)
    valor = {"espacos": f" {n} ", "underscore": n[0] + "_" + n[1:] if len(n) > 1 else n + "_0",
             "mais": f"+{n}", "zero": f"0{n}", "arabe": n.translate(str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩"))}[forma]
    assert int(valor) == vid, "a forma tem de ser uma que o `int()` aceita"
    emails = {}
    from core.services import email_service
    for nome in ("send_trial_ending_email", "send_payment_failed_email"):
        monkeypatch.setattr(email_service, nome, espiao_email(emails, nome))
    antes = _foto(vid)
    try:
        r = _post(client, fake, _com_metadata(tipo, valor), subs=_subs(tipo))
        assert r.status_code == 200, r.text
        assert _foto(vid) == antes, f"o evento agiu sobre o vizinho {vid} com {valor!r}"
        assert emails == {}, "e-mail saiu para o vizinho"
    finally:
        _cleanup_trial(uid)


@pytest.mark.parametrize("tipo", list(_RAMOS))
def test_valor_invalido_cai_no_customer(user_id, monkeypatch, tipo):
    """POSITIVO: inválido = ausente, e ausente resolve pelo `customer` (D4)."""
    uid, client, fake = _setup(monkeypatch, f"cus-{user_id}")
    cid = f"cus_inv_{uid}"
    set_customer(uid, cid)
    ondes = _espiar(monkeypatch, ("get_user_by_stripe_customer",))
    try:
        r = _post(client, fake, _com_metadata(tipo, "abc", customer=cid), subs=_subs(tipo))
        assert r.status_code == 200, r.text
        assert ondes["get_user_by_stripe_customer"], "não tentou o customer"
    finally:
        _cleanup_trial(uid)


def test_valor_valido_continua_resolvendo_o_dono(user_id, monkeypatch):
    """POSITIVO: sem este caso, um `_resolve_user` que devolvesse sempre `None`
    passaria em todos os negativos."""
    uid, client, fake = _setup(monkeypatch, f"ok-{user_id}")
    try:
        evento = _RAMOS["checkout.session.completed"][0](uid)
        r = _post(client, fake, evento, subs=_subs("checkout.session.completed"))
        assert r.status_code == 200, r.text
        assert db.get_auth_user(uid)["plan"] == "pro" and _foto(uid)[1] == 1
        r = _post(client, fake, _RAMOS["invoice.payment_failed"][0](uid),
                  subs=_subs("invoice.payment_failed"))
        assert r.status_code == 200 and db.get_auth_user(uid)["last_payment_status"] == "past_due"
    finally:
        _cleanup_trial(uid)


def test_uid_de_metadata_tabela():
    """Tabela entrada/saída da função pura (teste comum, sem controle)."""
    from core.services.stripe_sdk import uid_de_metadata

    validos = {"1": 1, "7": 7, "12": 12, "9223372036854775807": 2**63 - 1}
    for entrada, esperado in validos.items():
        assert uid_de_metadata(entrada) == esperado, entrada
    for entrada in [*INVALIDOS, "", None, 7, 7.0, True, b"7", [], "7\n", "7\x00", "٣"]:
        assert uid_de_metadata(entrada) is None, repr(entrada)
