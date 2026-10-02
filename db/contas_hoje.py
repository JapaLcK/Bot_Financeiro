"""
db/contas_hoje.py — o bloco de contas do Resumo (`GET /api/v2/contas`): o saldo de
hoje da Carteira e de cada conta do banco.

Mesmo recorte e mesmos critérios da foto do patrimônio (`db/patrimonio.py`): a
Carteira com a fusão devolvida, `CONTAS_BANCO_SQL` (uma linha por identidade do
provedor, a da conexão mais nova) e `desatualizada`/`sem_saldo`/`fora_do_sync`.
Cartão, posições e caixinhas ficam fora (o SQL só pega `type='BANK'`). Só lê, pelo
cursor recebido: quem chama abre a transação (repeatable read, só leitura).

`total` = `calcular()["carteira"] + calcular()["bancos"]`: soma o finito da COLUNA
de toda conta viva em reais, como a foto — inclusive a de `saldo_ausente`, em que
o sync grava 0. A conta com `no_total=false` não muda o total.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from .open_finance import PENDING_RECONCILIATION_SQL, actionable_pending_params, merged_wallet_delta
from .open_finance_state import _TERMINAL
from .patrimonio import (BANCO_VELHO, CONTAS_BANCO_SQL, desatualizada, finito, fora_do_sync,
                         ler_conexoes, sem_saldo, ultima_geracao)

MOTIVOS_CARTEIRA = ("carteira_nao_confirmada", "conciliacao_pendente", "movimentos_pendentes",
                    "especie_incompleta")
MOTIVOS_CONTA = ("banco_desatualizado", "saldo_ausente", "moeda_presumida",
                 "conta_fora_do_ultimo_sync", "outra_moeda", "conexao_pausada")


def _sim(pares) -> list[str]:
    return [m for m, sim in pares if sim]


def listar(cur, user_id: int) -> dict:
    from .bank_movements import _declarations
    from .open_finance_cash import enabled as especie_ligada

    cur.execute("select balance from accounts where user_id=%s", (user_id,))
    row = cur.fetchone()
    carteira = (row["balance"] if row else Decimal(0)) + merged_wallet_delta(cur, user_id)

    conexoes, estados = ler_conexoes(cur, user_id)
    por_id = {c["id"]: c for c in conexoes}
    vivas = [c for c in conexoes if (c["status"] or "").upper() not in _TERMINAL]
    limite = datetime.now(timezone.utc) - BANCO_VELHO
    ultima = ultima_geracao(cur, user_id, "open_finance_accounts")
    cur.execute(PENDING_RECONCILIATION_SQL, actionable_pending_params(cur, user_id))
    conciliacao = cur.fetchone()["pending_count"]
    motivos_carteira = _sim((
        ("carteira_nao_confirmada", True),  # até existir a confirmação da Q37
        ("conciliacao_pendente", conciliacao > 0),
        ("movimentos_pendentes", any(not d["matched_transaction_id"]
                                     for d in _declarations(cur, user_id))),
        ("especie_incompleta", not especie_ligada() and bool(vivas)),
    ))

    total, contas = carteira, []
    cur.execute(CONTAS_BANCO_SQL, (user_id,))
    for r in cur.fetchall():
        c = por_id[r["connection_id"]]
        conta = {"id": r["id"], "instituicao": c["institution_name"], "nome": r["name"],
                 "moeda": r["currency"], "conexao": estados[str(c["id"])],
                 "sincronizado_em": c["last_sync_at"]}
        if r["connection_status"] in _TERMINAL:
            conta.update(saldo=None, no_total=False, motivos=["conexao_pausada"])
        else:
            em_reais, ausente = r["currency"] == "BRL", sem_saldo(r)
            conta.update(saldo=None if ausente else r["balance"], no_total=em_reais and not ausente,
                         motivos=_sim((
                             ("banco_desatualizado", desatualizada(c, estados[str(c["id"])], limite)),
                             ("saldo_ausente", ausente),
                             ("moeda_presumida", not r["currency_code"]),
                             ("conta_fora_do_ultimo_sync", fora_do_sync(r, ultima)),
                             ("outra_moeda", not em_reais),
                         )))
            if em_reais and finito(r["balance"]):
                total += r["balance"]
        contas.append(conta)

    vistos = set(motivos_carteira).union(*(c["motivos"] for c in contas))
    return {
        "total": total,
        "motivos": [m for m in MOTIVOS_CARTEIRA + MOTIVOS_CONTA if m in vistos],
        "fora_do_total": sum(not c["no_total"] for c in contas),
        "carteira": {"saldo": carteira, "motivos": motivos_carteira},
        "contas": contas,
    }
