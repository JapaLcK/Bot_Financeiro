"""Admin desconectado NÃO pode liquidar saque de afiliado (issue #372).

`admin_affiliate_payout_paid` e `admin_affiliate_payout_reject`
(core/admin_dashboard.py) leem o corpo dentro de um
`try/except Exception: payload = {}`. O `except` largo engolia também a
`ClientDisconnect`, e o handler SEGUIA: `mark_payout_paid` grava
`status='paid'` + comissões `'paid'` e dá `commit()` (db/affiliates.py).
O admin não vê nada (o uvicorn descarta a resposta de conexão caída) e a rota
não desfaz — `status='requested'` é a única que o `where` aceita.

**As asserções são NO BANCO, de propósito.** Medir status HTTP mediria nada:
antes do conserto o handler devolvia `{"ok": True}` para um socket que já
morreu. O que separa o certo do errado é a linha do `affiliate_payouts`.
"""
from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from starlette.requests import ClientDisconnect, Request

import core.admin_dashboard as admin_dashboard
import db
from db import ensure_user
from db.affiliates import (
    create_affiliate,
    record_commission_for_invoice,
    record_referral,
    request_payout,
)

# (sufixo da rota, status esperado do payout, status esperado da comissão,
#  a comissão continua ligada ao payout?)
ACOES = [
    pytest.param("paid", "paid", "paid", True, id="paid"),
    pytest.param("reject", "rejected", "pending", False, id="reject"),
]


def _endpoint(sufixo: str):
    """O handler real, pego do app — as rotas são closures de
    `register_admin_routes`, não têm nome importável."""
    path = "/admin/api/affiliates/payouts/{payout_id}/" + sufixo
    for rota in admin_dashboard_app().router.routes:
        if getattr(rota, "path", None) == path and "POST" in (getattr(rota, "methods", None) or ()):
            return rota.endpoint
    raise AssertionError(f"rota não registrada: {path}")


def admin_dashboard_app():
    import frontend.finance_bot_websocket_custom as dashboard

    return dashboard.app


def _chamar(sufixo: str, payout_id: int, corpo: bytes | None):
    """Chama o handler direto (sem TestClient: o assunto aqui é o corpo, não a
    autenticação do admin). `corpo=None` = cliente sumiu antes do corpo chegar —
    a `ClientDisconnect` nasce do ponto REAL onde o starlette a levanta
    (`Request.stream()`), como na fixture `rotas_de_corpo` de
    tests/test_error_pages.py.
    """
    async def _receive():
        if corpo is None:
            return {"type": "http.disconnect"}
        return {"type": "http.request", "body": corpo, "more_body": False}

    scope = {
        "type": "http",
        "method": "POST",
        "path": f"/admin/api/affiliates/payouts/{payout_id}/{sufixo}",
        "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
    }
    request = Request(scope, _receive)
    return asyncio.run(
        _endpoint(sufixo)(payout_id=payout_id, request=request, username="admin")
    )


def _linha_do_payout(payout_id: int) -> tuple[str, str | None]:
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select status, note from affiliate_payouts where id = %s", (payout_id,))
            row = cur.fetchone()
    return row["status"], row["note"]


def _comissoes(payout_id: int, affiliate_id: int) -> list[dict]:
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select status, payout_id from affiliate_commissions where affiliate_id = %s",
                (affiliate_id,),
            )
            return cur.fetchall()


def _set_note(payout_id: int, note: str) -> None:
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("update affiliate_payouts set note = %s where id = %s", (note, payout_id))
        conn.commit()


@pytest.fixture(autouse=True)
def _sem_log_no_banco(monkeypatch):
    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(admin_dashboard, "log_system_event", _noop)


@pytest.fixture()
def saque(user_id):
    """Saque em aberto (`status='requested'`) com 1 comissão travada nele.

    As três colunas que o `request_payout` exige (db/affiliates.py):
    `invoice_amount_cents` acima do mínimo, `available_at` no passado e
    `status='pending'` — sem elas ele recusa com "Saldo disponível (R$ 0.00)".
    """
    indicado = int(uuid.uuid4().int % 10_000_000_000)
    ensure_user(indicado)
    aff = create_affiliate(user_id)
    record_referral(aff["code"], indicado)
    comissao = record_commission_for_invoice(indicado, f"in_test_{uuid.uuid4().hex[:12]}", 100000)
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update affiliate_commissions set available_at = now() - interval '1 day' where id = %s",
                (comissao["id"],),
            )
        conn.commit()
    payout = request_payout(aff["id"])
    assert payout["status"] == "requested"

    yield {"payout_id": int(payout["id"]), "affiliate_id": int(aff["id"])}

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("delete from affiliate_commissions where affiliate_id = %s", (aff["id"],))
            cur.execute("delete from affiliate_payouts where affiliate_id = %s", (aff["id"],))
            cur.execute("delete from accounts where user_id = %s", (indicado,))
            cur.execute("delete from users where id = %s", (indicado,))
        conn.commit()


@pytest.mark.parametrize("sufixo, _pago, _comissao, _ligada", ACOES)
def test_cliente_que_some_nao_liquida_o_saque(saque, sufixo, _pago, _comissao, _ligada):
    """Controle NEGATIVO: sem o `except ClientDisconnect: raise`, o handler segue
    e o banco vira 'paid'/'rejected' — dinheiro liquidado sem mandato.
    """
    try:
        retorno = _chamar(sufixo, saque["payout_id"], corpo=None)
    except ClientDisconnect:
        retorno = ClientDisconnect

    # A asserção do BANCO vem primeiro e fora do `pytest.raises` de propósito:
    # com o conserto revertido o handler devolve `{"ok": True}` e um
    # `pytest.raises` falharia ANTES de olhar a linha — o vermelho apontaria
    # "não levantou" em vez do defeito real, que é o saque liquidado.
    status, _note = _linha_do_payout(saque["payout_id"])
    assert status == "requested", (
        f"saque liquidado sem mandato do admin ({sufixo}): status={status}, retorno={retorno}"
    )
    assert retorno is ClientDisconnect, "a ClientDisconnect tem de subir pro middleware (499)"

    comissoes = _comissoes(saque["payout_id"], saque["affiliate_id"])
    assert [c["status"] for c in comissoes] == ["pending"]
    assert [c["payout_id"] for c in comissoes] == [saque["payout_id"]]


@pytest.mark.parametrize("sufixo, pago, comissao, ligada", ACOES)
def test_requisicao_normal_com_note_continua_liquidando(saque, sufixo, pago, comissao, ligada):
    """Controle POSITIVO 1: o caminho legítimo COM corpo segue idêntico ao de
    hoje. Sem ele, o mutante "aborta sempre" passaria — e é pior que o bug.
    """
    corpo = json.dumps({"note": "pix enviado"}).encode()
    assert _chamar(sufixo, saque["payout_id"], corpo=corpo) == {"ok": True}

    status, note = _linha_do_payout(saque["payout_id"])
    assert (status, note) == (pago, "pix enviado")

    comissoes = _comissoes(saque["payout_id"], saque["affiliate_id"])
    assert [c["status"] for c in comissoes] == [comissao]
    assert [c["payout_id"] for c in comissoes] == [saque["payout_id"] if ligada else None]


@pytest.mark.parametrize("sufixo, pago, comissao, ligada", ACOES)
def test_requisicao_normal_sem_corpo_continua_liquidando(saque, sufixo, pago, comissao, ligada):
    """Controle POSITIVO 2: `note` é opcional, então o painel manda POST SEM
    corpo nenhum — corpo vazio tem de continuar caindo no `payload = {}` e o
    `coalesce(%s, note)` tem de preservar a nota anterior. É este caso que
    proíbe reusar o `_json_object_body`, que transformaria isto em 400.
    """
    _set_note(saque["payout_id"], "nota previa")

    assert _chamar(sufixo, saque["payout_id"], corpo=b"") == {"ok": True}

    status, note = _linha_do_payout(saque["payout_id"])
    assert (status, note) == (pago, "nota previa")

    comissoes = _comissoes(saque["payout_id"], saque["affiliate_id"])
    assert [c["status"] for c in comissoes] == [comissao]
    assert [c["payout_id"] for c in comissoes] == [saque["payout_id"] if ligada else None]
