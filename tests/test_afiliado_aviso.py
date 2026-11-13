"""Aviso de reembolso ao admin: o dinheiro não depende dele (decisão do dono D1 = "Gravar e 200").

O reembolso grava rótulo e pendência antes de qualquer aviso; aviso que não sai responde 200 e o
`_afiliado_alerta_worker` reenvia (`reenviar_pendentes`), em lotes de 20, parando na 1ª falha, com o HTTP
fora da transação. Portados da integração com 6faf6bdb os testes que medem algo que a suíte não media.

Rota real, Postgres real; o canal é o `_send` de verdade com `requests.post` trocado (fixture `canal`).
"""
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
import requests

from core.services import admin_notify
from core.services.afiliado_reembolso import reenviar_pendentes
from db.affiliates import request_payout
from db.connection import get_conn
from test_afiliado_area import _afiliado
from test_afiliado_pendencia import (  # noqa: F401
    URL,
    _limpa_fila,
    _pagar,
    _parcial,
    _quadros,
    _rejeitar,
    _rev,
    adm,
    canal,
)
from test_afiliado_reembolso import _cobranca, _enviar, _paga, _status, mundo  # noqa: F401
from test_afiliado_reembolso import _logs as _tabela_de_logs  # noqa: F401  (autouse: cria system_event_logs)


def _falha(canal, monkeypatch, modo):
    if modo == "sem_url":
        monkeypatch.delenv("ADMIN_NOTIFY_WEBHOOK_URL")
    elif modo == "timeout":
        canal.erro = requests.exceptions.Timeout()
    else:
        canal.status = 503


def _reembolso(a, tipo, evento=None):
    return _enviar("charge.refunded", _cobranca(a.fatura, devolvido=100_000 if tipo == "total" else 30_000),
                   evento=evento)


# ── Cenário do dono: canal falha, e o dinheiro segue preso ──────────────────

MODOS = pytest.mark.parametrize("modo", ["503", "timeout", "sem_url"])
TIPOS = pytest.mark.parametrize("tipo", ["total", "parcial"])


@MODOS
@TIPOS
def test_canal_falha_e_rejeicao_nao_libera(canal, adm, monkeypatch, modo, tipo):
    """Saque de R$ 170 (A de R$ 100 reembolsada, B de R$ 70). Rejeitado, A nunca volta a ser sacável."""
    c = _afiliado()
    a, b = _paga(c), _paga(c, 70)
    saque = request_payout(c.aff["id"])["id"]
    _falha(canal, monkeypatch, modo)
    assert _reembolso(a, tipo).status_code == 200
    assert _rejeitar(adm, saque).status_code == 200
    q = _quadros(c)
    if tipo == "total":
        assert _status(a)["status"] == "reversed" and (q["available"], q["review"]) == (70, 0)
    else:
        assert _status(a)["status"] == "pending" and (q["available"], q["review"]) == (70, 100)
    novo = request_payout(c.aff["id"])
    assert novo["amount_cents"] == 7_000
    assert _pagar(adm, novo["id"]).status_code == 200  # positivo: B sai
    assert _status(b)["status"] == "paid" and _status(a)["status"] != "paid"


@MODOS
@TIPOS
def test_canal_falha_e_marcar_pago_recusa(canal, adm, monkeypatch, modo, tipo):
    c = _afiliado()
    a, _ = _paga(c), _paga(c, 70)
    saque = request_payout(c.aff["id"])["id"]
    _falha(canal, monkeypatch, modo)
    assert _reembolso(a, tipo).status_code == 200
    assert _pagar(adm, saque).status_code == 409
    assert dict(_status(a)) == {"status": "pending", "payout_id": saque}


@pytest.mark.parametrize("valor, enviada", [(URL, URL), (f"  {URL}\n", URL), (f"{URL}\r\n", URL),
                                            (" \n\t", None)])
def test_url_do_canal_sem_espacos(monkeypatch, valor, enviada):
    """O `_send` real (sem `mundo`): valor colado com espaço ou quebra de linha no painel (#1507)."""
    urls = []
    monkeypatch.setattr(requests, "post",
                        lambda url, json, timeout: urls.append(url) or SimpleNamespace(status_code=204, text=""))
    monkeypatch.setenv("ADMIN_NOTIFY_WEBHOOK_URL", valor)
    assert admin_notify.canal_configurado() is (enviada is not None)
    assert admin_notify._send("x") is (enviada is not None)
    assert urls == ([enviada] if enviada else [])


# ── Worker: parada, lote, concorrência ──────────────────────────────────────

def _pendentes(c, n, monkeypatch):
    """`n` pendências parciais sem aviso: o canal está fora quando o reembolso chega."""
    monkeypatch.delenv("ADMIN_NOTIFY_WEBHOOK_URL")
    for _ in range(n):
        _parcial(_paga(c, 10), 1_000)
    monkeypatch.setenv("ADMIN_NOTIFY_WEBHOOK_URL", URL)


def test_para_na_primeira_falha(canal, monkeypatch):
    """Canal fora: 1 tentativa por volta, e não 20. A reivindicada fica com o claim até o prazo."""
    _limpa_fila()
    _pendentes(_afiliado(), 3, monkeypatch)
    tentativas = []
    real = requests.post
    monkeypatch.setattr(requests, "post", lambda *a, **k: tentativas.append(1) or real(*a, **k))
    canal.status = 503
    assert (reenviar_pendentes(), len(tentativas)) == (0, 1)
    canal.status = 204
    assert reenviar_pendentes() == 2  # a que falhou segue reivindicada
    with get_conn() as conn:
        conn.execute("update affiliate_refund_reviews set notify_claimed_until = now() - interval '1 second'"
                     " where notified_at is null")
        conn.commit()
    assert reenviar_pendentes() == 1 and len(tentativas) == 4
    with get_conn() as conn:
        assert conn.execute("select count(*) as n from affiliate_refund_reviews where notified_at is null").fetchone()["n"] == 0


def test_lotes_de_20(canal, monkeypatch):
    _limpa_fila()
    _pendentes(_afiliado(), 25, monkeypatch)
    assert [reenviar_pendentes() for _ in range(3)] == [20, 5, 0]
    assert len(canal.enviados) == 25


def test_envio_concorrente_nao_espera_nem_prende_transacao(canal, monkeypatch):
    """Com o 1º envio parado dentro do HTTP: nenhuma conexão presa, e o 2º worker não reenvia nem espera."""
    _limpa_fila()
    _pendentes(_afiliado(), 1, monkeypatch)
    dentro, solta = threading.Event(), threading.Event()
    real = requests.post

    def post_lento(*args, **kw):
        dentro.set()
        solta.wait(10)
        return real(*args, **kw)

    monkeypatch.setattr(requests, "post", post_lento)
    with ThreadPoolExecutor(1) as pool:
        primeira = pool.submit(reenviar_pendentes)
        assert dentro.wait(10)
        with get_conn() as conn:
            presas = conn.execute("select count(*) as n from pg_stat_activity where datname = current_database()"
                                  " and state like 'idle in transaction%%' and pid <> pg_backend_pid()").fetchone()["n"]
        inicio = time.monotonic()
        segunda = reenviar_pendentes()
        demora = time.monotonic() - inicio
        solta.set()
        assert primeira.result(timeout=15) == 1
    assert (presas, segunda, len(canal.enviados)) == (0, 0, 1) and demora < 3


# ── Log: `error` quando o aviso não sai, com o evento ───────────────────────

def _logs(commission_id):
    with get_conn() as conn:
        return [(x["level"], x["details"]) for x in conn.execute(
            "select level, details from system_event_logs where event_type = 'affiliate_refund_commission_kept'"
            " and (details->>'commission_id')::bigint = %s order by id", (commission_id,)).fetchall()]


@pytest.mark.parametrize("modo", ["sem_url", "503"])
def test_log_aviso_falho_e_error_com_event_id(canal, monkeypatch, modo):
    c = _afiliado()
    a = _paga(c)
    _falha(canal, monkeypatch, modo)
    evento = f"evt_{uuid.uuid4().hex}"
    assert _reembolso(a, "parcial", evento).status_code == 200
    [(nivel, detalhes)] = _logs(a.id)
    assert nivel == "error" and detalhes["event_id"] == evento and detalhes["alerta_entregue"] is False


def test_log_aviso_entregue_e_warning_e_worker_sem_evento(canal, monkeypatch):
    """Positivo: entregue segue `warning`. O worker não tem evento: o log dele vem sem `event_id`."""
    _limpa_fila()
    c = _afiliado()
    a, b = _paga(c), _paga(c)
    evento = f"evt_{uuid.uuid4().hex}"
    assert _reembolso(a, "parcial", evento).status_code == 200
    assert [(n, d["event_id"], d["alerta_entregue"]) for n, d in _logs(a.id)] == [("warning", evento, True)]
    monkeypatch.delenv("ADMIN_NOTIFY_WEBHOOK_URL")
    assert _reembolso(b, "parcial").status_code == 200
    monkeypatch.setenv("ADMIN_NOTIFY_WEBHOOK_URL", URL)
    canal.status = 503
    assert reenviar_pendentes() == 0
    nivel, detalhes = _logs(b.id)[-1]
    assert nivel == "error" and "event_id" not in detalhes
