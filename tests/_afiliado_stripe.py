"""Stripe falsa só na rede, para quem confere faturas de comissão: `InvoicePayment.list(invoice=...)`,
`PaymentIntent.retrieve` e `Dispute.list`. Sem configuração, toda fatura tem uma cobrança paga sem
reembolso: é a Stripe que o QR, o "Marcar pago" e o "Pagamento já realizado" consultam antes de pagar.

`cobra(fatura, devolvido=..., disputed=...)` muda a cobrança; `disputas[fatura]` lista as disputas;
`falha` faz a listagem levantar; `atraso` a deixa lenta; `consultas` conta as chamadas HTTP.
Sem prefixo `test_`: o pytest não coleta este arquivo.
"""
import time
from types import SimpleNamespace

from _stripe_cliente_falso import instalar


def simular_stripe(monkeypatch, stripe_mod=None, por_pagamento=None):
    if stripe_mod is None:
        import stripe as stripe_mod
    f = SimpleNamespace(cobrancas={}, disputas={}, falha=None, consultas=0, atraso=0.0, durante=None)

    def listar(**k):
        if "invoice" not in k:
            return por_pagamento(**k)
        f.consultas += 1
        if f.atraso:
            time.sleep(f.atraso)
        if f.durante:
            f.durante()
        if f.falha:
            raise f.falha
        return {"data": [{"status": "paid", "payment": {"type": "payment_intent", "payment_intent": f"pi_{k['invoice']}"}}]}

    def intent(pi, expand, api_key):
        f.consultas += 1
        fatura = pi[3:]
        cobranca = f.cobrancas.get(fatura, {"amount": 100_000, "amount_refunded": 0, "disputed": False})
        return {"latest_charge": {"id": f"ch_{fatura}", **cobranca}}

    for nome, metodo, funcao in (("InvoicePayment", "list", listar), ("PaymentIntent", "retrieve", intent),
                                 ("Dispute", "list", lambda charge, limit, api_key: {"data": f.disputas.get(charge[3:], [])})):
        if not hasattr(stripe_mod, nome):
            monkeypatch.setattr(stripe_mod, nome, type(nome, (), {}), raising=False)
        monkeypatch.setattr(getattr(stripe_mod, nome), metodo, staticmethod(funcao), raising=False)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_xxx")
    instalar(monkeypatch)  # o `cliente_stripe` de produção cai nestes falsos (#1499)
    f.cobra = lambda fatura, devolvido=0, amount=100_000, disputed=False: f.cobrancas.__setitem__(
        fatura, {"amount": amount, "amount_refunded": devolvido, "disputed": disputed})
    return f
