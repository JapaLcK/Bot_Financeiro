"""O `cliente_stripe` de produção (#1499) visto pelos falsos de API de módulo: cada chamada do `StripeClient`
vira a chamada de módulo equivalente em `sys.modules["stripe"]`, resolvida NA HORA, para valer o monkeypatch
feito depois (`mundo.no(fake)`, métodos trocados no meio do teste).

Limite: com ele, os testes de conferência, script e e-book ficam verdes com ou sem a migração. Quem prova que a
produção usa o cliente com prazo é `tests/test_stripe_prazo.py`, na rede. Sem prefixo `test_`: não é coletado.
"""
import sys
from types import SimpleNamespace


def _stripe():
    return sys.modules["stripe"]


class ClienteDoModulo:
    def __init__(self, key):
        k = {"api_key": key}
        sessao = lambda: _stripe().checkout.Session  # noqa: E731
        self.v1 = SimpleNamespace(
            invoice_payments=SimpleNamespace(list=lambda params: _stripe().InvoicePayment.list(**params, **k)),
            payment_intents=SimpleNamespace(
                retrieve=lambda pi, params: _stripe().PaymentIntent.retrieve(pi, **params, **k)),
            disputes=SimpleNamespace(list=lambda params: _stripe().Dispute.list(**params, **k)),
            checkout=SimpleNamespace(sessions=SimpleNamespace(
                retrieve=lambda sid: sessao().retrieve(sid, **k),
                line_items=SimpleNamespace(list=lambda sid, params: sessao().list_line_items(sid, **params, **k)))),
        )


def instalar(monkeypatch):
    import core.services.ebook_entrega as ebook_entrega  # noqa: PLC0415
    monkeypatch.setattr(ebook_entrega, "cliente_stripe", ClienteDoModulo)
