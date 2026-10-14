"""Declaração de despesa/receita "paga pelo banco": elegibilidade contra a transação do extrato.

Mesma tabela e mesma rotina de conferência do aporte (`db/bank_movements.py`), regras próprias:
valor quase exato (R$ 0,05), janela de 3 dias e `account_id` NULL = "qualquer conta BANK ativa".
Fluxo = launch despesa/receita COM `account_id` NULL; o NULL fica mesmo depois de casar (preenchê-lo
jogaria a declaração no caminho do aporte, que exige categoria de aplicação).
O vínculo é aditivo: sem par a declaração segue "a conferir" e não expira (ADR 0003).
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from datetime import date, datetime, timedelta
from decimal import Decimal

from utils_date import _tz
from utils_text import STOPWORDS_PT

TIPOS_FLUXO = ("despesa", "receita")
FLUXO_CONGELA_DIAS = 45  # fluxo já casado há mais que isso sai da conferência (e do lock de leitura)
MANUAL_TOL_ABS, MANUAL_TOL_PCT = Decimal("5.00"), Decimal("0.10")
# Palavras que não nomeiam comércio: as stopwords do parser (sem as que são nome de categoria) mais as
# preposições/palavras de frase que sobram no alvo ("loja", "conta", "pix"...).
_FUNCIONAIS = (STOPWORDS_PT - {"salario", "reembolso"}) | {"pro", "sem", "que", "ate", "meu", "minha", "loja",
                                                           "conta", "compra", "pix"}


# O mesmo conceito de `e_fluxo` em SQL (um teste compara os dois), sobre `launches` SEM alias: despesa/receita
# declarada com `account_id` NULL. Aporte/resgate e declaração com conta explícita não mudam de leitura.
DECLARADA_SQL = """(tipo in ('despesa', 'receita') and exists (
        select 1 from bank_movement_declarations d
         where d.launch_id = launches.id and d.user_id = launches.user_id and d.account_id is null))"""
CASADA_SQL = f"""({DECLARADA_SQL} and exists (
        select 1 from bank_movement_declarations d where d.launch_id = launches.id
           and d.user_id = launches.user_id and d.matched_transaction_id is not null))"""


def e_fluxo(declaration: dict) -> bool:
    return declaration.get("tipo") in TIPOS_FLUXO and declaration.get("account_id") is None


def _dia(declaration: dict) -> date:
    return declaration["declared_at"].astimezone(_tz()).date()


def elegivel_fluxo(declaration: dict, tx: dict, *, manual: bool = False) -> bool:
    from .bank_movements import _money
    from .open_finance import RECON_AMOUNT_TOL, RECON_DATE_WINDOW, classify_open_finance_launch
    amount, tx_amount = _money(declaration.get("amount")), _money(tx["amount"])
    if not amount or not tx_amount or (amount > 0) != (tx_amount > 0):
        return False
    tol = max(MANUAL_TOL_ABS, MANUAL_TOL_PCT * abs(amount)) if manual else RECON_AMOUNT_TOL
    if abs(tx_amount - amount) > tol:
        return False
    if abs((tx["transaction_date"] - _dia(declaration)).days) > RECON_DATE_WINDOW:
        return False
    # Fluxo e aporte são disjuntos. `economic_kind` já implica `is_internal_movement` (as categorias de
    # aplicação são um subconjunto das internas); fica como defesa em profundidade.
    return not tx.get("economic_kind") and not classify_open_finance_launch(
        tx["amount"], tx.get("category"), tx.get("description"))["is_internal_movement"]


def janela(transactions: list[dict], dates: list[date], declaration: dict) -> list[dict]:
    """Só as transações a <= RECON_DATE_WINDOW dias da declaração. `transactions` vem ordenada por
    `transaction_date` (`_transactions`) e `dates` são as datas dela; mesma regra de data do
    `_eligible` dos dois caminhos, então o conjunto elegível não muda (só o custo: O(D x janela))."""
    from .open_finance import RECON_DATE_WINDOW
    dia, w = _dia(declaration), timedelta(days=RECON_DATE_WINDOW)
    return transactions[bisect_left(dates, dia - w):bisect_right(dates, dia + w)]


def projeta(cur, user_id: int, declaration: dict, tx: dict | None = None) -> None:
    """Fluxo casado: `launches.valor`/`criado_em` passam a ser os da transação do banco (valor absoluto,
    dia do extrato na hora local DECLARADA: o dia/mês do Resumo bate com o extrato sem inventar um horário
    e sem cruzar a meia-noite por UTC). Sem par (nunca casou, desvinculada, transação apagada): voltam ao
    declarado. O declarado original fica em `bank_movement_declarations.amount`/`declared_at`, e é SÓ
    ele que a elegibilidade lê. Idempotente: só escreve quando difere. Aporte/resgate não passam aqui."""
    from .bank_movements import _money
    if not e_fluxo(declaration):
        return
    if tx is None:
        valor, criado = abs(declaration["amount"]), declaration["declared_at"]
    else:
        hora = declaration["declared_at"].astimezone(_tz()).time()
        valor, criado = abs(_money(tx["amount"])), datetime.combine(tx["transaction_date"], hora, tzinfo=_tz())
    if (valor, criado) != (declaration["launch_valor"], declaration["launch_criado_em"]):
        cur.execute("update launches set valor=%s, criado_em=%s where id=%s and user_id=%s",
                    (valor, criado, declaration["launch_id"], user_id))
        declaration["launch_valor"], declaration["launch_criado_em"] = valor, criado


def projeta_todas(cur, user_id: int, declarations: list[dict], by_id: dict) -> None:
    for d in declarations:
        projeta(cur, user_id, d, by_id.get(d["matched_transaction_id"]))


def tem_evidencia(declaration: dict, tx: dict) -> bool:
    """Casamento AUTOMÁTICO do fluxo exige evidência do comércio: descrição do banco parecida com o ALVO da
    declaração (o estabelecimento que o parser extraiu) ou declaração genérica (sem estabelecimento). Mesmos
    primitivos do dinheiro (`open_finance.merchant_similarity` e o conjunto de genéricos). O alvo vem do
    parser com preposição ("com o dentista", "uber para o trabalho"): palavras funcionais saem antes de
    comparar, senão "com"/"para" casam qualquer "AMAZON.COM"/"PIX PARA MARIA". A NOTA (a frase inteira)
    não conta: seus verbos e preposições davam evidência a descrições sem relação. Sem evidência a
    transação segue candidata da escolha manual. Só o gatilho de CRIAR o vínculo: revalidar um já feito
    olha valor/data, e o manual nunca exige. Aporte: True."""
    from .open_finance import _is_generic_merchant, _normalize_merchant, merchant_similarity
    alvo = declaration.get("alvo")
    nucleo = " ".join(t for t in _normalize_merchant(alvo).split() if t not in _FUNCIONAIS)
    return (not e_fluxo(declaration) or _is_generic_merchant(alvo) or not nucleo
            or merchant_similarity(tx.get("description"), nucleo))


def congelada(declaration: dict, hoje: date) -> bool:
    return (e_fluxo(declaration) and declaration["matched_transaction_id"] is not None
            and (hoje - _dia(declaration)).days > FLUXO_CONGELA_DIAS)


def desde_da_leitura(declarations: list[dict]) -> date | None:
    """Primeiro dia de transação que alguma declaração ativa pode alcançar (None = sem declaração)."""
    from .open_finance import RECON_DATE_WINDOW
    return min(map(_dia, declarations)) - timedelta(days=RECON_DATE_WINDOW) if declarations else None
