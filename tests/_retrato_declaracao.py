"""Retrato completo do usuário para os testes da declaração bancária (plano A2b, seção 3).

Todo teste compara `retrato(uid) == {...}`: lançamentos, transações do banco, declarações, saldo da
Carteira e as saídas do mês. Rótulos estáveis no lugar dos ids:
- lançamento: `decl <alvo>` (tem declaração), `sombra <id do banco>` (open_finance) ou `<source> <alvo>`;
- transação: o `provider_transaction_id`.
`dia` é o deslocamento em dias a partir de `BASE`. A asserção mostra a linha a mais ou a menos, e o
total vem do mesmo retrato: transação que existe sem sombra nem vínculo aparece na lista T.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from db.resumo_mes import totais_do_mes
from tests._patrimonio_helpers import q
from utils_date import _tz, today_tz

# BASE, BASE+1 e BASE+2 caem no mesmo mês; no dia 1 ou 2 elas ficam no futuro, o que o sync aceita.
BASE = today_tz() - timedelta(days=2) if today_tz().day >= 3 else today_tz()


def em(dias=0):
    """`criado_em` de uma declaração em BASE + `dias`, na hora de agora."""
    return datetime.combine(BASE + timedelta(days=dias), datetime.now(_tz()).time(), tzinfo=_tz())


def t(ptid, amount, dias, desc, category="Groceries"):
    """Transação do extrato em BASE + `dias` (o formato do `save_open_finance_sync`)."""
    return {"provider_transaction_id": ptid, "description": desc, "amount": amount,
            "transaction_date": BASE + timedelta(days=dias), "category": category}


def tid(uid, ptid):
    return q("""select t.id from open_finance_transactions t
                  join open_finance_accounts a on a.id=t.account_id
                  join open_finance_connections c on c.id=a.connection_id
                 where c.user_id=%s and t.provider_transaction_id=%s""", (uid, ptid))["id"]


# Linhas do retrato, com os valores que o código grava (`delta_conta` da declaração sai do escritor como
# '0.0'; o da sombra, do import, como '0').
def decl(dia, valor=50.0, alvo="mercado", tipo="despesa"):
    return (f"decl {alvo}", tipo, valor, dia, "manual", "0.0", None)


def sombra(ptid, valor, dia, tipo="despesa"):
    return (f"sombra {ptid}", tipo, valor, dia, "open_finance", "0", None)


def solta(ptid, amount, dia):
    return (ptid, amount, dia, f"sombra {ptid}", None, "imported")


def casada(ptid, amount, dia, alvo="mercado"):
    return (ptid, amount, dia, f"decl {alvo}", f"decl {alvo}", "bank_movement_confirmed")


def _dia(valor):
    d = valor.astimezone(_tz()).date() if isinstance(valor, datetime) else valor
    return (d - BASE).days


def _rotulos(uid):
    rows = q("""select l.id, l.alvo, l.source, l.efeitos->'open_finance'->>'provider_transaction_id' ptid,
                       exists(select 1 from bank_movement_declarations d where d.launch_id=l.id) declarada
                  from launches l where l.user_id=%s""", (uid,), fetch=True)
    return {r["id"]: (f"decl {r['alvo']}" if r["declarada"] else
                      f"sombra {r['ptid']}" if r["source"] == "open_finance" else
                      f"{r['source'] or 'manual'} {r['alvo']}") for r in rows}


def retrato(uid, mes=None):
    nomes = _rotulos(uid)
    lanc = q("""select id, tipo, valor, criado_em, source, efeitos->>'delta_conta' delta, origem,
                       is_internal_movement
                  from launches where user_id=%s""", (uid,), fetch=True)
    txs = q("""select t.provider_transaction_id ptid, t.amount, t.transaction_date, t.imported_launch_id,
                      t.match_launch_id, t.reconciliation_status
                 from open_finance_transactions t
                 join open_finance_accounts a on a.id=t.account_id
                 join open_finance_connections c on c.id=a.connection_id
                where c.user_id=%s""", (uid,), fetch=True)
    decls = q("""select d.launch_id, d.amount, t.provider_transaction_id par, d.confirmation_method,
                        d.manual_only, d.requires_review
                   from bank_movement_declarations d
                   left join open_finance_transactions t on t.id=d.matched_transaction_id
                  where d.user_id=%s""", (uid,), fetch=True)
    saldo = q("select balance from accounts where user_id=%s", (uid,))
    mes = mes or BASE
    saiu = float(totais_do_mes(uid, mes)["saiu"])
    # O total sai do retrato: soma das despesas não internas do mês. Transação do banco sem linha nenhuma
    # (nem sombra nem declaração) fica visível em T, e não somada às escondidas.
    soma = sum(float(r["valor"]) for r in lanc if r["tipo"] == "despesa" and not r["is_internal_movement"]
               and r["criado_em"].astimezone(_tz()).date().replace(day=1) == mes.replace(day=1))
    assert saiu == soma, f"saiu={saiu} difere da soma das linhas={soma}"
    return {
        "L": sorted((nomes[r["id"]], r["tipo"], float(r["valor"]), _dia(r["criado_em"]), r["source"],
                     r["delta"], r["origem"]) for r in lanc),
        "T": sorted((r["ptid"], float(r["amount"]), _dia(r["transaction_date"]), nomes.get(r["imported_launch_id"]),
                     nomes.get(r["match_launch_id"]), r["reconciliation_status"]) for r in txs),
        "D": sorted((nomes[r["launch_id"]], float(r["amount"]), r["par"], r["confirmation_method"],
                     r["manual_only"], r["requires_review"]) for r in decls),
        "C": float(saldo["balance"]) if saldo else None,
        "saiu": saiu,
    }
