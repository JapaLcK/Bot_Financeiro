"""Semeadura dos testes de assinaturas: conexão, contas e transações pelo
`db.save_open_finance_sync` de verdade, e o Recurring Payments pelo
`salvar_recorrencias`. Banco real; nada mockado aqui."""
import uuid
from datetime import date
from decimal import Decimal

import db
from db.of_recurring import salvar_recorrencias
from utils_date import add_months, clamp_day

HOJE = date(2026, 10, 1)


def conexao(uid: int) -> int:
    item = {"id": f"rp-{uuid.uuid4().hex[:12]}", "status": "UPDATED",
            "connector": {"id": 612, "name": "Nubank"}}
    return db.save_pluggy_open_finance_item(uid, item)["id"]


def tx(tid, valor, dia, *, category=None, merchant=None, cc=None, desc="Compra"):
    raw = {"id": tid}
    if merchant is not None:
        raw["merchant"] = merchant
    if cc is not None:
        raw["creditCardMetadata"] = cc
    return {"provider_transaction_id": tid, "description": desc, "amount": Decimal(str(valor)),
            "transaction_date": dia, "category": category, "raw": raw}


def mensais(prefixo, valores, *, ultima=date(2026, 9, 5), **kw) -> list[dict]:
    """Uma transação por mês terminando em `ultima`, na ordem de `valores`."""
    out = []
    n = len(valores)
    for i, v in enumerate(valores):
        y, m = add_months(ultima.year, ultima.month, i - (n - 1))
        out.append(tx(f"{prefixo}-{i}", v, date(y, m, clamp_day(y, m, ultima.day)), **kw))
    return out


def conta(acc_id, txs, *, tipo="BANK", numero=None, nome="Conta", saldo="0"):
    """Saldo 0 em reais por padrão: a conta entra na base sem mudar o número dela
    (só recorrência de conta na base entra na Previsão). `saldo=None` = saldo ausente."""
    raw = {"currencyCode": "BRL"} | ({"balance": saldo} if saldo is not None else {})
    return {"provider_account_id": acc_id, "name": nome, "type": tipo,
            "raw": raw | ({"number": numero} if numero else {}), "transactions": txs}


def rp(desc, media, txs) -> dict:
    return {"description": desc, "averageAmount": media, "regularityScore": 0.9,
            "occurrences": [t["provider_transaction_id"] for t in txs]}


def semeia(uid, contas, recorrencias) -> int:
    cid = conexao(uid)
    db.save_open_finance_sync(cid, contas)
    salvar_recorrencias(cid, recorrencias)
    return cid


def netflix_no_cartao(uid, *, valor=-39.9, numero="1234") -> int:
    txs = mensais("nf", [valor] * 3, desc="NETFLIX.COM")
    return semeia(uid, [conta("acc-cc", txs, tipo="CREDIT", numero=numero, nome="Nubank Mastercard")],
                  [rp("NETFLIX.COM", valor, txs)])


def dez_e_vinte_centavos(uid) -> None:
    """Dois serviços ativos de R$ 0,10 e R$ 0,20: em float a soma dá 0.30000000000000004."""
    nf = mensais("nf", ["-0.10"] * 3, desc="NETFLIX.COM")
    sp = mensais("sp", ["-0.20"] * 3, ultima=date(2026, 9, 12), desc="Spotify")
    semeia(uid, [conta("acc-1", nf + sp)], [rp("NETFLIX.COM", -0.1, nf), rp("Spotify", -0.2, sp)])
