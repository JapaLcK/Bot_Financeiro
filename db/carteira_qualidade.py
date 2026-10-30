"""Composição verificável da Carteira; leitura pura compartilhada, sem Q37."""
from decimal import Decimal


def delta_verificavel(r):
    """Mesmo pré-voo de efeito/proveniência para composição e realização da Carteira."""
    from .accounts import ORIGEM_CARTEIRA, _validar_efeitos
    from .bank_movements import uses_bank_movement_lock

    effects = r['efeitos']
    if not isinstance(effects, dict):
        return None
    try:
        delta = _validar_efeitos(effects, escopo_conta_corrente=False)
        if not delta.is_finite() or delta != delta.quantize(Decimal('0.01')):
            return None
    except (ValueError, TypeError, ArithmeticError):
        return None
    if delta == 0:
        return delta  # sombra OF e registro de banco não movem a Carteira.
    if (r['origem'] != ORIGEM_CARTEIRA or uses_bank_movement_lock(r['source'], effects)
            # Escritores físicos omitem funding_source ou gravam None; outro metadado não prova espécie.
            or effects.get('funding_source') is not None
            or (r['source'] != 'manual' and not r['especie'])
            or effects.get('bill_id') is not None):
        return None
    expected = r['valor'] if r['tipo'] == 'receita' else -r['valor'] if r['tipo'] == 'despesa' else None
    if expected is None or not expected.is_finite() or expected != delta:
        return None
    return delta


def nao_confirmada(cur, user_id, balance):
    from .open_finance_cash import VINCULADO_SQL

    cur.execute(f"select id, tipo, valor, origem, source, efeitos, {VINCULADO_SQL} as especie "
                "from launches where user_id=%s", (user_id,))
    rows = cur.fetchall()
    if balance is None:
        # Linha ausente com histórico não representa carteira inicial zero.
        cur.execute("""select exists(select 1 from open_finance_connections where user_id=%s)
             or exists(select 1 from credit_cards where user_id=%s)
             or exists(select 1 from investments where user_id=%s)
             or exists(select 1 from pockets where user_id=%s)
             or exists(select 1 from recurring_expenses where user_id=%s)
             or exists(select 1 from recurring_incomes where user_id=%s)
             or exists(select 1 from bill_instances where user_id=%s)
             or exists(select 1 from credit_bills where user_id=%s) as historico""",
                    (user_id,) * 8)
        return bool(rows) or cur.fetchone()['historico']
    if not balance.is_finite():
        return True
    total = Decimal(0)
    for r in rows:
        delta = delta_verificavel(r)
        if delta is None:
            return True
        total += delta
    return total != balance


# Saldo inicial (onboarding), ajuste da tela (`adjust-balance` grava "ajuste") e o rótulo canônico do chat. Os três
# escritores gravam interno; "ajuste" também é nome possível de categoria do usuário (`#ajuste`), que nasce não interna.
CATEGORIAS_SALDO_INFORMADO = ("saldo_inicial", "ajuste", "ajuste_saldo")


def saldo_informado(cur, user_id) -> bool:
    """O usuário já disse quanto tem na Carteira alguma vez (saldo inicial ou ajuste). Sem isso, o saldo é o zero de
    nascença somado aos gastos, e "Seu saldo está no vermelho" seria alarme falso (PL-06 B3, emenda 1)."""
    cur.execute("select exists(select 1 from launches where user_id = %s and categoria = any(%s) "
                "and is_internal_movement) as sim", (user_id, list(CATEGORIAS_SALDO_INFORMADO)))
    return cur.fetchone()["sim"]
