"""Conferir antes de pagar: o QR, o "Marcar pago" e o "Pagamento já realizado" consultam a Stripe por
cada fatura do saque, porque o `charge.refunded` pode se perder. Regra única: `devolvido_na_stripe`
decide, `registrar_reembolso` grava, e a regra de bloqueio que já existe recusa (D10 = A para disputa).

Rota real, Postgres real; a Stripe é falsa só na rede (`tests/_afiliado_stripe.py`, via `mundo`).
"""
import gc
from types import SimpleNamespace
import threading
import time
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest
import stripe

import core.admin_dashboard as admin_dashboard
import core.services.afiliado_reembolso as servico
from core.services.afiliado_reembolso import conferir_pendentes, tratar_evento
from db.affiliate_reviews import SaqueAberto, registrar_reembolso, resolver_parcial
from db.affiliates import reject_payout, request_payout
from db.connection import get_conn
from test_afiliado_area import _afiliado, _esperando_lock, _painel_aberto  # noqa: F401
from test_afiliado_exclusao import _excluir, _saque_com_pix
from test_afiliado_pendencia import (  # noqa: F401
    OVERVIEW,
    _pagar,
    _paga,
    _quadros,
    _realizado,
    _rejeitar,
    _rev,
    _saque,
    adm,
    canal,
)
from test_afiliado_reembolso import _evento, _logs, _segura, mundo  # noqa: F401

PIX = "/admin/api/affiliates/payouts/{}/pix"
FORA = "Não foi possível conferir reembolsos na Stripe agora. Tente de novo em instantes."
DISPUTA = ("Uma comissão deste saque tem disputa aberta na Stripe. Rejeite o saque para liberar as outras; "
           "ela volta quando a disputa fechar.")


def _pix(adm, saque):
    return adm.get(PIX.format(saque))


def _marcada(com):
    with get_conn() as conn:
        return conn.execute("select payment_check_pending from affiliate_commissions where id = %s",
                            (com.id,)).fetchone()["payment_check_pending"]


def _dois(c):
    a, b = _paga(c), _paga(c, 70)
    return a, b, _saque_com_pix(c)


# ── Reembolso achado ─────────────────────────────────────────────────────────

def test_webhook_perdido_qr_acha_reembolso(adm, mundo):
    c = _afiliado()
    a, _, saque = _dois(c)
    mundo.stripe.cobra(a.fatura, devolvido=100_000)
    r = _pix(adm, saque)
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "Reembolso de cliente achado na Stripe: o saque ficou bloqueado para revisão."
    assert "brcode" not in r.text and _rev(a.fatura)["status"] == "open"


def test_webhook_perdido_marcar_pago_acha(adm, mundo):
    c = _afiliado()
    a, _, saque = _dois(c)
    mundo.stripe.cobra(a.fatura, devolvido=100_000)
    r = _pagar(adm, saque)
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "Reembolso achado na Stripe: o saque ficou bloqueado. Se o Pix já saiu, use Pagamento já realizado."
    assert _saque(saque)["status"] == "requested"
    assert _realizado(adm, saque).status_code == 200
    assert (_rev(a.fatura)["status"], _rev(a.fatura)["recovery_cents"]) == ("recovery", 10_000)


def test_parcial_achado_vai_para_revisao(adm, mundo):
    c = _afiliado()
    a, _, saque = _dois(c)
    mundo.stripe.cobra(a.fatura, devolvido=30_000)
    assert _pix(adm, saque).status_code == 409
    assert (_rev(a.fatura)["status"], _rev(a.fatura)["tipo"]) == ("open", "partial")


# ── Stripe fora, sem chave, prazo ────────────────────────────────────────────

@pytest.mark.parametrize("rota", ["pix", "paid"])
def test_stripe_fora(adm, mundo, rota):
    c = _afiliado()
    a, _, saque = _dois(c)
    mundo.stripe.falha = stripe.APIConnectionError("fora")
    r = _pix(adm, saque) if rota == "pix" else _pagar(adm, saque)
    assert r.status_code == 503, r.text
    esperado = FORA if rota == "pix" else FORA + " Se o Pix já saiu, use Pagamento já realizado."
    assert r.json()["detail"] == esperado and "brcode" not in r.text
    assert _saque(saque)["status"] == "requested" and _rev(a.fatura) is None


@pytest.mark.parametrize("rota", ["pix", "paid"])
def test_sem_chave_recusa(adm, mundo, monkeypatch, rota):
    c = _afiliado()
    _, _, saque = _dois(c)
    monkeypatch.delenv("STRIPE_SECRET_KEY")
    r = _pix(adm, saque) if rota == "pix" else _pagar(adm, saque)
    assert r.status_code == 503 and _saque(saque)["status"] == "requested"


def test_prazo_estourado(adm, mundo, monkeypatch):
    """O prazo real é 20 s; aqui ele cai para 0,5 s e a Stripe demora 2 s, sem reembolso: sem o prazo seria 200.
    O executor é próprio, então o `TestClient` não espera a thread ao fechar o laço e o tempo se mede."""
    monkeypatch.setattr(admin_dashboard, "PRAZO_CONFERENCIA_S", 0.5)
    conta = _conta(monkeypatch)
    c = _afiliado()
    _, _, saque = _dois(c)
    mundo.stripe.atraso = 2
    t0 = time.monotonic()
    r = _pix(adm, saque)
    dur = time.monotonic() - t0
    conta.espera()
    assert r.status_code == 503 and r.json()["detail"] == FORA
    assert dur < 1.5, dur


def _conta(monkeypatch):
    """Conta as conferências que começaram e as que terminaram. A thread que estourou o prazo segue
    ocupando a vaga até acabar, e o teste seguinte do mesmo worker precisa dela livre: `espera()`."""
    real = servico.conferir_saque
    c = SimpleNamespace(chamadas=[], fins=[])

    def contada(payout_id):
        c.chamadas.append(payout_id)
        try:
            return real(payout_id)
        finally:
            c.fins.append(payout_id)

    def espera(prazo=15):
        fim = time.monotonic() + prazo
        while len(c.fins) < len(c.chamadas) and time.monotonic() < fim:
            time.sleep(0.05)
        assert len(c.fins) == len(c.chamadas)
    c.espera = espera
    monkeypatch.setattr(servico, "conferir_saque", contada)
    return c


def test_rajada_lenta_recusa_na_hora_sem_consultar(adm, mundo, monkeypatch):
    """Stripe lenta (o SDK espera até 80 s por chamada): a conferência roda num executor próprio de
    `VAGAS_CONFERENCIA` threads, nunca no executor padrão que as outras rotas usam via `to_thread`. Cheio,
    o QR recusa com 503 na hora, sem consultar."""
    monkeypatch.setattr(admin_dashboard, "PRAZO_CONFERENCIA_S", 0.3)
    vagas = getattr(admin_dashboard, "VAGAS_CONFERENCIA", 4)
    conta = _conta(monkeypatch)
    c = _afiliado()
    _, _, saque = _dois(c)
    mundo.stripe.atraso = 3
    with ThreadPoolExecutor(vagas) as pool:
        primeiras = list(pool.map(lambda _: _pix(adm, saque).status_code, range(vagas)))
    # Prazo folgado na seguinte: sem a recusa na hora, ela esperaria na fila do executor e consultaria.
    monkeypatch.setattr(admin_dashboard, "PRAZO_CONFERENCIA_S", 5)
    t0 = time.monotonic()
    r = _pix(adm, saque)
    dur = time.monotonic() - t0
    conta.espera()
    assert primeiras == [503] * vagas and (r.status_code, r.json()["detail"]) == (503, FORA)
    assert len(conta.chamadas) == vagas, "com as vagas cheias, a seguinte consultou a Stripe"
    assert dur < 1, dur


# ── "Pagamento já realizado" nunca recusa ───────────────────────────────────

def test_realizado_com_stripe_fora(adm, mundo):
    c = _afiliado()
    a, b, saque = _dois(c)
    mundo.stripe.falha = stripe.APIConnectionError("fora")
    assert _realizado(adm, saque).status_code == 200
    assert _saque(saque)["status"] == "paid" and _marcada(a) and _marcada(b)
    mundo.stripe.falha = None
    mundo.stripe.cobra(a.fatura, devolvido=100_000)
    assert conferir_pendentes() >= 2
    assert (_rev(a.fatura)["status"], _rev(a.fatura)["recovery_cents"]) == ("recovery", 10_000)
    assert not _marcada(a) and not _marcada(b) and _rev(b.fatura) is None


def test_realizado_acha_reembolso_antes_de_registrar(adm, mundo):
    c = _afiliado()
    a, _, saque = _dois(c)
    mundo.stripe.cobra(a.fatura, devolvido=100_000)
    assert _realizado(adm, saque).status_code == 200
    assert (_rev(a.fatura)["status"], _rev(a.fatura)["recovery_cents"]) == ("recovery", 10_000)


def test_sem_reembolso_paga(adm, mundo):
    """Positivo: QR e "Marcar pago" saem, com 2 chamadas por fatura em cada consulta."""
    c = _afiliado()
    _, _, saque = _dois(c)
    antes = mundo.stripe.consultas
    r = _pix(adm, saque)
    assert r.status_code == 200 and r.json()["amount_cents"] == 17_000
    assert mundo.stripe.consultas - antes == 4  # 2 faturas × (InvoicePayment.list + PaymentIntent.retrieve)
    assert _pagar(adm, saque).status_code == 200 and _saque(saque)["status"] == "paid"
    assert mundo.stripe.consultas - antes == 8


def test_orfao_realizado_acha_reembolso(adm, mundo):
    c = _afiliado()
    a, _, saque = _dois(c)
    _excluir(c)
    mundo.stripe.cobra(a.fatura, devolvido=100_000)
    assert _realizado(adm, saque).status_code == 200
    assert (_rev(a.fatura)["status"], _rev(a.fatura)["recovery_cents"]) == ("recovery", 10_000)


# ── D10 = A: disputa aberta recusa, marca, e o admin rejeita ─────────────────

def test_disputa_aberta(adm, mundo):
    c = _afiliado()
    a, b, saque = _dois(c)
    mundo.stripe.cobra(a.fatura, disputed=True)
    mundo.stripe.disputas[a.fatura] = [{"status": "needs_response", "amount": 100_000}]
    r = _pix(adm, saque)
    assert r.status_code == 409
    assert r.json()["detail"] == ("Uma comissão deste saque tem disputa aberta na Stripe. Rejeite o saque para liberar "
                                  "as outras; ela volta quando a disputa fechar.")
    assert _marcada(a) and not _marcada(b)
    r = _pagar(adm, saque)  # "Marcar pago" também recusa: sem pendência, só a consulta vê a disputa
    assert (r.status_code, r.json()["detail"]) == (409, DISPUTA) and _saque(saque)["status"] == "requested"
    assert _rejeitar(adm, saque).status_code == 200
    assert request_payout(c.aff["id"])["amount_cents"] == 7_000
    assert _quadros(c)["held"] == 100


# ── Corrida e conexão ────────────────────────────────────────────────────────

def _rejeita_durante(saque):
    trava = threading.Lock()

    def rejeita():
        with trava:
            if _saque(saque)["status"] == "requested":
                reject_payout(saque, None, "adm2")
    return rejeita


def test_qr_status_muda_durante_a_conferencia(adm, mundo):
    """Outro admin rejeita o saque enquanto a Stripe responde: o QR relê o saque inteiro e não sai."""
    c = _afiliado()
    _, _, saque = _dois(c)
    mundo.stripe.durante = _rejeita_durante(saque)
    r = _pix(adm, saque)
    assert (r.status_code, r.json()["detail"]) == (409, "Este saque não está aberto."), r.text[:120]
    assert _saque(saque)["status"] == "rejected"


def test_paid_rejeitado_durante_a_conferencia(adm, mundo):
    """O "Marcar pago" já relê na trava (`status = 'requested'` no update): rejeitado no meio, não paga."""
    c = _afiliado()
    _, _, saque = _dois(c)
    mundo.stripe.durante = _rejeita_durante(saque)
    assert _pagar(adm, saque).status_code == 404 and _saque(saque)["status"] == "rejected"


def test_qr_com_revisao_durante_a_conferencia(adm, mundo, canal):
    """Outro admin tenta revisar com valor menor durante a consulta: `SaqueAberto`, e o QR recusa pelo
    bloqueio relido. O saque não muda de valor (D11)."""
    c = _afiliado()
    a, _, saque = _dois(c)
    trava, erros = threading.Lock(), []

    def revisa():
        with trava:
            if _rev(a.fatura) is None:
                registrar_reembolso(a.fatura, "partial", 30_000, datetime.now(timezone.utc))
                try:  # sem o `try`, a exceção sobe pela consulta e o QR daria 503 da Stripe
                    resolver_parcial(_rev(a.fatura)["id"], "adm2", 6_000, "outro admin revisou", 30_000)
                except Exception as exc:  # noqa: BLE001
                    erros.append(exc)
    mundo.stripe.durante = revisa
    r = _pix(adm, saque)
    assert [type(e) for e in erros] == [SaqueAberto]
    assert r.status_code == 409, r.text[:120]
    assert _saque(saque)["amount_cents"] == 17_000

def test_corrida_webhook_entre_consulta_e_trava(adm, mundo, monkeypatch):
    """A consulta não acha nada; o webhook entra antes da trava. A regra que já existe recusa."""
    import core.services.afiliado_reembolso as servico
    c = _afiliado()
    a, _, saque = _dois(c)
    real = servico.conferir_saque
    pool = ThreadPoolExecutor(1)
    webhook = []

    with get_conn() as segura:
        _segura(segura, a)

        def consulta_e_webhook(payout_id):
            resultado = real(payout_id)
            webhook.append(pool.submit(tratar_evento, _evento(a.fatura)))
            assert _esperando_lock(1)
            return resultado

        monkeypatch.setattr(servico, "conferir_saque", consulta_e_webhook)
        resposta = []
        t = threading.Thread(target=lambda: resposta.append(_pagar(adm, saque)))
        t.start()
        assert _esperando_lock(2)
        segura.commit()
        t.join(30)
    assert webhook[0].exception(timeout=15) is None
    pool.shutdown()
    assert resposta[0].status_code == 409 and _saque(saque)["status"] == "requested"


def test_consulta_nao_segura_transacao(adm, mundo):
    """Durante o HTTP da Stripe, nenhuma conexão deste processo fica `idle in transaction`. Só as deste
    processo (com `-n 4`, os outros workers seguram transações de propósito, `_segura`) e paradas há mais de
    200 ms: a Stripe falsa demora 300 ms, e uma thread que sobrou de outro teste pega entre dois comandos dura ms."""
    c = _afiliado()
    _, _, saque = _dois(c)
    vistas = []

    def mede():
        nossas = [c.info.backend_pid for c in gc.get_objects() if isinstance(c, psycopg.Connection) and not c.closed]
        with get_conn() as conn:
            conn.execute("commit")
            vistas.append(conn.execute("select count(*) as n from pg_stat_activity where pid = any(%s)"
                                       " and state like 'idle in transaction%%' and pid <> pg_backend_pid()"
                                       " and state_change < clock_timestamp() - interval '200 milliseconds'",
                                       (nossas,)).fetchone()["n"])

    mundo.stripe.atraso = 0.3
    mundo.stripe.durante = mede
    assert _pix(adm, saque).status_code == 200
    assert vistas and set(vistas) == {0}, vistas
