"""Desfazer movimento de investimento: só o ÚLTIMO do investimento se desfaz.

O snapshot do resgate (`investment_lot_withdrawals[].before`) é ABSOLUTO: aplicá-lo
depois de outro movimento no mesmo lote (ou em lote vizinho, pelo PEPS) apaga o
efeito do movimento posterior e cria dinheiro. O aporte, desfeito fora de ordem,
invalida o PEPS dos resgates que vieram depois. Então recusa-se o que não é o
mais recente.
"""

from .accounts import InvestmentMovementNotLast, LaunchUnsafeRollback

_CHAVES_INVESTIMENTO = (
    "delta_invest", "investment_lot_create", "investment_lot_withdrawals",
    "create_investment", "delete_investment",
)

MENSAGEM_NAO_E_O_ULTIMO = (
    "Esse movimento não é o mais recente do investimento (ou o investimento foi "
    "apagado). Desfaça os mais novos primeiro."
)


def touches_investment(efeitos) -> bool:
    # A caixinha grava `"delta_invest": None`: valor vazio não é efeito.
    return isinstance(efeitos, dict) and any(efeitos.get(k) for k in _CHAVES_INVESTIMENTO)


def guard_last_investment_movement(cur, user_id: int, launch_id: int, efeitos: dict) -> None:
    """Chame com `_lock_user` já pego e na MESMA transação do rollback.

    Vale para todo movimento: aporte, resgate, criar e apagar o investimento.
    Movimento posterior = launch deste usuário com `id` maior cujo `efeitos` cite o
    mesmo nome em `delta_invest`, `delete_investment` ou `create_investment`. Por
    `id`, não `criado_em`: a data é editável pelo usuário, o `id` e o `efeitos` não.
    O accrual diário não é launch e não conta — ele é função de saldo, taxa,
    `last_date` e índices, e a restauração devolve o par saldo/`last_date`.

    Por `lower()`, não igualdade exata: o índice uq_investments_user_lower_name (#596)
    faz do nome sem caixa a identidade do investimento. Com igualdade exata, apagar
    "CDB" → criar "cdb" → desfazer o apagar passava pela guarda e virava no-op (o
    `on conflict do nothing` pula a recriação e o launch some).

    ponytail: liga pelo NOME gravado no `efeitos`. Não existe renomear investimento;
    no dia em que existir, gravar `investment_id` no `delta_invest` e ligar por ele.
    """
    nome = next((efeitos[k].get("nome") for k in ("delta_invest", "create_investment",
                                                  "delete_investment")
                 if isinstance(efeitos.get(k), dict)), None)
    if not nome:
        raise LaunchUnsafeRollback(
            "movimento de investimento sem 'nome': não dá para saber se é o mais "
            "recente.",
            "efeito_incompleto",
        )
    # Só trava (quem não existe é recusado adiante pelo `rowcount` do lote). Põe o
    # desfazer na ordem conta → launch → investimento → lotes, a mesma do
    # `accrue_all_investments` (investimento → lotes): sem ela, os dois se
    # esperavam em ordem trocada.
    cur.execute(
        "select id from investments where user_id=%s and lower(name)=lower(%s) for update",
        (user_id, nome),
    )
    cur.execute(
        """
        select 1 from launches
         where user_id=%s and id>%s
           and lower(%s) in (lower(efeitos->'delta_invest'->>'nome'),
                             lower(efeitos->'delete_investment'->>'nome'),
                             lower(efeitos->'create_investment'->>'nome'))
         limit 1
        """,
        (user_id, launch_id, nome),
    )
    if cur.fetchone():
        raise InvestmentMovementNotLast(MENSAGEM_NAO_E_O_ULTIMO)
