"""A janela por data (`db.bank_declaracoes.janela`, bisect) não muda o conjunto de candidatos.

Compara, em casos aleatórios (semente fixa), o filtro ingênuo (todas as transações pelo `_eligible`
real, fluxo e aporte, automático e manual) com o indexado. O custo é o conserto; o resultado não pode
mudar, inclusive a unicidade nos dois sentidos, que conta TODOS os candidatos elegíveis.
Controle negativo: janela de +-2 dias em vez de `RECON_DATE_WINDOW` deixa o teste vermelho.
"""
import random
from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal

from db.bank_declaracoes import janela
from db.bank_movements import _eligible
from utils_date import _tz

HOJE = datetime(2026, 10, 1, 12, tzinfo=_tz())
CATS = [None, "Groceries", "Fixed income", "Same person transfer"]


def _caso(rng):
    n_tx, n_d = rng.randint(0, 30), rng.randint(1, 12)
    txs = [{"id": i, "transaction_date": (HOJE + timedelta(days=rng.randint(-12, 12))).date(),
            "amount": Decimal(rng.choice([-50, -50, 50, -52.3, -50.04, -2, 2, 3000])),
            "account_id": rng.choice([1, 2]), "economic_kind": rng.random() < .2,
            "category": rng.choice(CATS), "description": rng.choice(["Mercado", "Aplicacao", "Pix"])}
           for i in range(n_tx)]
    txs.sort(key=lambda t: (t["transaction_date"], t["id"]))  # a ordem de `_transactions`
    decls = [{"launch_id": 100 + i, "amount": Decimal(rng.choice([-50, 50, -52.3, -2, 3000])),
              "declared_at": HOJE + timedelta(days=rng.randint(-12, 12), hours=rng.randint(-11, 11)),
              "tipo": rng.choice(["despesa", "receita", "aporte_investimento"]),
              "account_id": rng.choice([None, None, 1]), "account_ambiguous": rng.random() < .1}
             for i in range(n_d)]
    return txs, decls


def test_janela_indexada_igual_a_ingenua_em_2000_casos():
    rng = random.Random(498)
    comparados = 0
    for _ in range(250):
        txs, decls = _caso(rng)
        dates = [t["transaction_date"] for t in txs]
        for manual in (False, True):
            ingenuo = {d["launch_id"]: [t["id"] for t in txs if _eligible(d, t, manual=manual)] for d in decls}
            indexado = {d["launch_id"]: [t["id"] for t in janela(txs, dates, d) if _eligible(d, t, manual=manual)]
                        for d in decls}
            assert indexado == ingenuo
            assert Counter(i for v in indexado.values() for i in v) == Counter(i for v in ingenuo.values() for i in v)
            comparados += len(decls)
    assert comparados >= 2000
