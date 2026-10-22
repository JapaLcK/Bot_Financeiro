"""Declaração de despesa/receita "paga pelo banco": elegibilidade contra a transação do extrato.

Mesma tabela e mesma rotina de conferência do aporte (`db/bank_movements.py`; a manual em
`db/bank_conferencia.py`), regras próprias:
valor quase exato (R$ 0,05), janela de 3 dias e `account_id` NULL = "qualquer conta BANK ativa".
Fluxo = launch despesa/receita COM `account_id` NULL; o NULL fica mesmo depois de casar (preenchê-lo
jogaria a declaração no caminho do aporte, que exige categoria de aplicação).
O vínculo é aditivo: sem par a declaração segue "a conferir" e não expira (ADR 0003).

INVARIANTES x ESCRITORES (a classe de achados do #969: cada invariante tem um escritor por porta, e quem
checa e escreve precisa fazer os dois sob o MESMO mutex do usuário, `bank_movements._lock_user`, a linha de
`accounts`, sempre o primeiro lock). Invariantes: I1 não é interna; I2 valor/data = projeção (declarado
sem par, extrato com par); I3 uma sombra por transação; I4 par automático único nos dois sentidos; I5
`delta_conta` 0, Carteira intocada; I6 tudo filtrado por `user_id`; I7 escolha explícita, troca ou desfazer
=> `manual_only`; o reconcile nunca liga fluxo `manual_only` (ele segue em `claims`); aporte nunca recebe a flag;
I8 "foi em dinheiro" convertido mexe no saldo uma vez (três camadas se cobrem: o mutex do usuário, a releitura
de `efeitos` depois do `for update` da linha da declaração e o CAS do `delete ... returning`; quem perde a
corrida não aplica delta; I5 é quebrado DE PROPÓSITO, uma vez); I10 um lançamento da Carteira resolve no
máximo uma declaração (o marcador `efeitos.foi_em_dinheiro`, por CAS, também no convertido); I11 selecionar
preserva o contexto da declaração na Carteira (`efeitos.foi_em_dinheiro.declaracao`) sem alterar os campos dela.
(I9 fica reservado para o lembrete do A2c.)
  criar (`add_launch_and_update_balance`)  I1 recusa argumento; I5; I6   mutex = o `update accounts`, e o
                                           reconcile na mesma transação (cursor externo é recusado)
  editar categoria / lote                  I1 recusa categoria interna   SEM mutex, de propósito: a
                                           declaração nasce com o lançamento e só some com ele (propriedade
                                           imutável); v2 toma o mutex pelo `pode`
  editar valor/data                        I2 recusa pelo `pode` + `LaunchDateLockedError`   mutex (pode/data)
  renomear/mesclar categoria (cascata)     I1 recusa nome interno com declaração   mutex SÓ no nome interno
                                           (era o buraco da corrida com a criação); fundir_categorias idem
  regras automáticas de categoria          só na criação de lançamento; não escrevem em declarada
  migração do boot (`schema.py`)           I1 não marca a declarada (marca `funding_source`)   SCHEMA_INIT_LOCK;
                                           a marca é imutável, então não corre com o escritor
  apagar um/lote/tudo                      I3 devolve a sombra, I6   mutex (`uses_bank_movement_lock`)
  sync/import/reconcile/desconexão         I2 projeção, I3, I4, I7   mutex, checa e escreve na transação
  escolher manual / rejeitar par           I2, I4, I7   mutex (`bank_conferencia.confirm_bank_movement`,
                                                        `_locked_tx`)
  trocar (`confirm` com `replace`)         I2, I3, I4, I6, I7   mutex -> `_transactions(lock)` -> `_locked_tx`;
                                           CAS no par visto; sombra do antigo e bind do novo na mesma transação
  desfazer (`undo_bank_movement`)          I2, I3, I6, I7   mutex -> `_locked_tx`; CAS no par visto
  desfazer fusão vizinha (`_desfaz`)       só FUSED_STATUSES; não alcança `bank_movement_confirmed`   mutex
  foi em dinheiro (`bank_dinheiro`)        I5 (só converter), I6, I8, I10, I11   mutex -> `for update` de C ->
                                           CAS do marcador / `delete ... returning` -> `accounts`
  merge de contas / reset / exclusão       I6 (a declaração muda de dono ou cai junto)   locks próprios de
                                           `users.py`/`privacy.py`, fora desta tabela
Célula check-then-write SEM mutex que sobra: nenhuma sobre propriedade mutável (as duas "SEM mutex" acima
checam propriedade imutável da linha).
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


# "É Carteira" (manual com `delta_conta` numérico e diferente de 0), sobre `launches` SEM alias. Uma só fonte:
# o estado `carteira` da v2 (`db/lancamentos.py`) e o candidato do "foi em dinheiro" (`db/bank_dinheiro.py`).
CARTEIRA_SQL = """(coalesce(source, 'manual') = 'manual'
        and jsonb_typeof(efeitos -> 'delta_conta') = 'number'
        and (efeitos ->> 'delta_conta')::numeric <> 0)"""


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


def devolve_sombra(cur, user_id: int, launch_id: int, novas: list) -> list[dict]:
    """Apagar uma declaração de fluxo JÁ casada: a transação do banco volta como sombra na MESMA transação
    do apagar (a fundida faz o mesmo em `reconciliation._desfaz`, que só enxerga FUSED_STATUSES e não
    `bank_movement_confirmed`). Mesmo helper do import (`_insert_of_shadow`: idempotente por `external_id`),
    então um import depois não duplica. Devolve as transações que voltaram (o aviso do apagar); [] se não
    é fluxo casado. `novas` recebe a categoria nova do catálogo (garantida depois do commit)."""
    from .open_finance import _insert_of_shadow, classify_open_finance_launch
    from .open_finance_categories import categoria_pigbank
    from .reconciliation import ReconciliationConflict, _locked_tx
    cur.execute(f"""select dec.matched_transaction_id t from bank_movement_declarations dec
                      join launches on launches.id = dec.launch_id and launches.user_id = dec.user_id
                     where dec.launch_id=%s and dec.user_id=%s and dec.matched_transaction_id is not null
                       and {DECLARADA_SQL}""", (launch_id, user_id))
    achou = cur.fetchone()
    if not achou:
        return []
    o = _locked_tx(cur, user_id, achou["t"])
    shadow_id, _ = _insert_of_shadow(cur, user_id, o, classify_open_finance_launch(
        o["amount"], o["category"], o["description"]))
    if shadow_id is None:
        raise ReconciliationConflict("SHADOW_NOT_CREATED")
    cur.execute("""update open_finance_transactions set imported_launch_id=%s, match_launch_id=null,
                          reconciliation_status='imported' where id=%s""", (shadow_id, o["id"]))
    novas.extend(filter(None, [categoria_pigbank(o["category"])]))
    return [o]


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
