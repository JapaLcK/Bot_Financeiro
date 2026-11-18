"""#1538, PR 2: 4xx da fatura (404, 400) no QR e no "Marcar pago" é "sem confirmação" (409 e marca), não 503.

Mesmos helpers de `test_afiliado_conferir_pagar`; a Stripe falsa é a do `mundo`.
"""
import pytest
import stripe

import core.services.afiliado_reembolso as servico
from core.services.afiliado_reembolso import conferir_pendentes
from db.connection import get_conn
from test_afiliado_conferir_pagar import DISPUTA, _paga, _afiliado, _dois, _marcada, _pagar, _pix, _rejeitar, _saque, adm  # noqa: F401
from test_afiliado_conferencia_admin import _db, _decide, _marca
from test_afiliado_exclusao import _saque_com_pix
from test_afiliado_reembolso import mundo  # noqa: F401


def _erro(status):
    return stripe.InvalidRequestError("No such invoice", "invoice", http_status=status)


@pytest.mark.parametrize("status", [404, 400])
@pytest.mark.parametrize("rota", ["pix", "paid"])
def test_4xx_da_fatura_marca_e_recusa_com_409(adm, mundo, rota, status):
    c = _afiliado()
    a, b, saque = _dois(c)
    mundo.stripe.falha = _erro(status)
    r = _pix(adm, saque) if rota == "pix" else _pagar(adm, saque)
    assert (r.status_code, r.json()["detail"]) == (409, DISPUTA) and "brcode" not in r.text, r.text
    assert _marcada(a) and _marcada(b) and _saque(saque)["status"] == "requested"


def test_4xx_rejeitar_e_o_worker_grava_stripe_404(adm, mundo):
    """Rejeitar solta o saque; a passada do worker grava stripe_404 e a marca fica."""
    c = _afiliado()
    a, _, saque = _dois(c)
    mundo.stripe.falha = _erro(404)
    assert _pix(adm, saque).status_code == 409
    assert _rejeitar(adm, saque).status_code == 200
    assert conferir_pendentes() == 0
    with get_conn() as conn:
        motivo = conn.execute("select payment_check_reason from affiliate_commissions where id = %s", (a.id,)).fetchone()
    assert motivo["payment_check_reason"] == "stripe_404" and _marcada(a)


def test_queda_de_rede_continua_503(adm, mundo):
    """Positivo do conserto: só 4xx da fatura vira sem confirmação; a queda segue travando o pagamento."""
    c = _afiliado()
    a, _, saque = _dois(c)
    mundo.stripe.falha = stripe.APIConnectionError("fora")
    assert _pix(adm, saque).status_code == 503 and not _marcada(a)


@pytest.mark.parametrize("erro", [stripe.PermissionError("sem permissão", http_status=403),
                                  stripe.InvalidRequestError("argumento", "invoice")])
def test_403_e_erro_local_sem_status_sao_stripe_fora(adm, mundo, erro):
    """Chave sem permissão e erro de argumento local não são "fatura sem confirmação": 503, sem marcar."""
    c = _afiliado()
    a, _, saque = _dois(c)
    mundo.stripe.falha = erro
    r = _pix(adm, saque)
    assert r.status_code == 503 and not _marcada(a), r.text


@pytest.mark.parametrize("caso", ["liberada", "disputa", "reembolso"])
def test_liberar_vale_no_qr_do_saque(adm, mundo, caso):
    """A liberada sem Stripe confirmando não volta a travar o QR; disputa e reembolso seguem travando. (A não liberada que trava: `test_4xx_da_fatura_*`.)"""
    c = _afiliado()
    a, _ = _paga(c), _paga(c, 70)
    marcada = _marca(a)
    mundo.stripe.cobrancas[a.fatura] = []  # sem_cobranca
    assert servico.conferir_comissao(a.id, a.fatura, adiar=True, lido=_db(a)["payment_check_marked_at"]) is False
    assert _decide(adm, marcada, "release").status_code == 200
    saque = _saque_com_pix(c)
    if caso == "disputa":
        mundo.stripe.cobra(a.fatura, disputed=True)
        mundo.stripe.disputas[a.fatura] = [{"status": "needs_response", "amount": 100_000}]
    if caso == "reembolso":
        mundo.stripe.cobra(a.fatura, devolvido=100_000)
    r = _pix(adm, saque)
    if caso == "liberada":
        assert r.status_code == 200 and not _marcada(a), r.text
    else:
        assert r.status_code == 409 and "brcode" not in r.text, r.text
        assert caso == "reembolso" or (r.json()["detail"] == DISPUTA and _marcada(a))
