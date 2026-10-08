"""Snapshot de caixa/compromissos: leitura única, proveniência e qualidade."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from core.services.cashflow_contract import (Motivo, Ocorrencia, Snapshot, centavos, dinheiro, legado, somar)


# Enumeração dos escritores do registro db.pending e tools de confirmação da IA.
# Preferência/categoria/orçamento não alteram caixa. Tipo futuro desconhecido não vale zero.
NAO_FINANCEIRAS = frozenset({'credit_card_set_primary', 'recategorize_launch_text',
                            'recategorize_launch_offer'})
NAO_FINANCEIRAS_IA = frozenset({
    'set_budget', 'delete_budget', 'recategorize_launch', 'create_category_rule',
    'delete_category_rule', 'enable_daily_report', 'disable_daily_report',
    'set_daily_report_hour', 'open_dashboard', 'report_out_of_scope',
})
INTENCOES_SEM_CAIXA = frozenset({
    'budget.set', 'budgets.set', 'launch.recategorize', 'launches.recategorize',
    'help', 'help.tutorial', 'greeting', 'balance.check', 'launches.list',
    'launches.spend_query', 'pockets.list', 'investments.list', 'cdi.check',
    'dashboard.open', 'emails.resubscribe', 'emails.unsubscribe',
})


def pendencia_financeira(tipo, payload, *, ia=False):
    if tipo in (NAO_FINANCEIRAS_IA if ia else NAO_FINANCEIRAS):
        return False
    if tipo == 'clarification' and isinstance(payload, dict):
        intent = payload.get('intent') or payload.get('intent_name')
        if intent in INTENCOES_SEM_CAIXA or isinstance(intent, str) and intent.startswith(('categories.', 'report.')):
            return False
    return True  # escritor futuro/forma ilegível não recebe efeito zero.


def _data(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def _motivo(s, codigo, direcao='ambos', ident=None, ciclo=None, efeito=None):
    m = Motivo(codigo, direcao, ident, ciclo, efeito)
    s.motivos.append(m)
    return m


def _datas(s, r, income, until):
    from core.services.cashflow import _recurring_occurrence_dates
    frequency = r.get('frequency')
    start = _data(r.get('start_date'))
    if start is not None and start > until:
        return []  # início comprovadamente fora da janela prevalece sobre legado desconhecido.
    day = r.get('pay_day' if income else 'due_day')
    month = r.get('pay_month' if income else 'due_month')
    valid = (frequency in (('monthly', 'annual') if income else
                          ('once', 'daily', 'weekly', 'monthly', 'annual')) and start is not None)
    if frequency in ('monthly', 'annual'):
        valid = valid and isinstance(day, int) and 1 <= day <= 31
    if frequency == 'annual':
        valid = valid and isinstance(month, int) and 1 <= month <= 12
    if not valid:
        _motivo(s, 'calendario_recorrente_desconhecido', 'ambos', r['id'])
        return [None]
    if start < s.hoje:
        _motivo(s, 'realizacao_passada_desconhecida', 'ambos', r['id'])
    return _recurring_occurrence_dates(day, frequency, month, start,
                                       s.hoje - timedelta(days=1), until)


def ler(cur, user_id: int, today: date, until: date, now: datetime,
        bancos_permitidos: bool) -> Snapshot:
    from db import contas_hoje
    from db.carteira_qualidade import delta_verificavel
    from db.open_finance_cash import VINCULADO_SQL
    from db.bank_movements import _declarations
    from db.recurring import ler_recorrentes
    from db.recurring_income import ler_receitas
    from db.bills import ler_instancias
    from db.cards import ler_faturas, calendario_fatura
    from db.open_finance import ACTIONABLE_PENDING_SQL, actionable_pending_params
    from db.ai_chat import PENDING_TTL_MINUTES
    from db.patrimonio import BANCO_VELHO
    from utils_date import _tz

    accounts = contas_hoje.listar(cur, user_id, agora=now)
    cur.execute('select balance from accounts where user_id=%s', (user_id,))
    wallet_row = cur.fetchone()
    wallet = dinheiro(accounts['carteira']['saldo'])
    banks = [c for c in accounts['contas'] if c['no_total']
             and 'moeda_presumida' not in c['motivos']]
    bank_count = len(accounts['contas'])
    available = wallet_row is not None or not accounts['motivos'] or bool(banks)
    balance = wallet if wallet is not None and available else None
    if bancos_permitidos and banks:
        balance = somar((balance or Decimal(0), *(c['saldo'] for c in banks)))
    base = {'saldo': balance, 'balance_source': 'consolidated' if bancos_permitidos and banks else
            'manual' if balance is not None else 'unavailable', 'of_bank_count': bank_count,
            'banks_excluded': bool(bank_count and not bancos_permitidos),
            'motivos': list(accounts['motivos'])}
    s = Snapshot(today, now, base)
    next_day = datetime.combine(today + timedelta(days=1), datetime.min.time(), _tz())
    s.valido_ate = next_day
    for code in accounts['motivos']:
        if code not in ('conciliacao_pendente', 'movimentos_pendentes'):
            _motivo(s, code)
    if base['banks_excluded']:
        _motivo(s, 'bancos_excluidos')
    for c in accounts['contas']:
        if c['sincronizado_em']:
            boundary = c['sincronizado_em'] + BANCO_VELHO
            if boundary > now:
                s.valido_ate = min(s.valido_ate, boundary)
    if bank_count:
        _motivo(s, 'cobertura_bancaria_nao_comprovada')

    cur.execute(ACTIONABLE_PENDING_SQL, actionable_pending_params(cur, user_id))
    for p in cur.fetchall():
        delta = dinheiro(p['d'])
        _motivo(s, 'conciliacao_a_conferir', 'so_melhora' if delta and delta > 0 else
                'so_piora' if delta and delta < 0 else 'ambos', p['id'], efeito=-delta if delta is not None else None)
    for d in _declarations(cur, user_id):
        if not d['matched_transaction_id']:
            amount = dinheiro(d['amount'])
            _motivo(s, 'declaracao_bancaria_a_conferir',
                    'so_melhora' if amount is not None and amount < 0 else
                    'so_piora' if amount is not None and amount > 0 else 'ambos',
                    d['launch_id'], efeito=amount)

    cur.execute('select action_type,payload,expires_at from pending_actions '
                'where user_id=%s and expires_at>%s', (user_id, now))
    for p in cur.fetchall():
        if pendencia_financeira(p['action_type'], p['payload']):
            _motivo(s, 'acao_financeira_pendente', ident=p['action_type'])
            s.valido_ate = min(s.valido_ate, p['expires_at'])
    cutoff = now - timedelta(minutes=PENDING_TTL_MINUTES)
    cur.execute('select tool_name,tool_args,created_at from ai_pending_actions '
                'where user_id=%s and created_at>=%s', (user_id, cutoff))
    for p in cur.fetchall():
        if pendencia_financeira(p['tool_name'], p['tool_args'], ia=True):
            _motivo(s, 'acao_financeira_pendente', ident=p['tool_name'])
            s.valido_ate = min(s.valido_ate, p['created_at'] + timedelta(minutes=PENDING_TTL_MINUTES))

    recs = ler_recorrentes(cur, user_id, True)
    rec_by_id = {r['id']: r for r in recs}
    instances = ler_instancias(cur, user_id)
    by_cycle = {(b['recurring_id'], _data(b['due_date'])): b for b in instances if b['recurring_id']}
    receitas = ler_receitas(cur, user_id)
    for income, rows in ((True, receitas), (False, recs)):
        for r in rows:
            if not r['is_active']:
                continue
            for d in _datas(s, r, income, until):
                b = by_cycle.get((r['id'], d)) if not income else None
                if b:
                    continue  # a instância do ciclo prevalece, inclusive no valor/qualidade.
                amount = dinheiro(r.get('amount'))
                variable = bool(r.get('variable_amount'))
                quality = 'estimado' if variable and amount is not None and amount > 0 else 'conhecido'
                reasons = []
                if amount is None or amount <= 0:
                    amount, quality = None, 'desconhecido'
                    reasons.append(_motivo(s, 'valor_recorrente_desconhecido',
                                           'so_piora' if income else 'so_melhora', r['id']))
                elif variable:
                    reasons.append(_motivo(s, 'valor_recorrente_estimado', 'ambos', r['id']))
                if income:
                    reasons.append(_motivo(s, 'receita_nao_garantida', 'so_melhora', r['id'], efeito=amount))
                card = not income and r.get('payment_type') == 'credit_card'
                if card:
                    reasons.append(_motivo(s, 'incorporacao_cartao_nao_comprovada', 'so_melhora', r['id']))
                s.ocorrencias.append(Ocorrencia('receita_recorrente' if income else 'gasto_recorrente',
                    r['id'], d.isoformat() if d else 'desconhecido', d, 'receita' if income else 'gasto_fixo', r['name'],
                    amount, 'entrada' if income else 'saida', quality,
                    'conhecida' if d else 'desconhecida', 'a_conferir' if d is None or d <= today or card else 'prevista',
                    tuple(reasons), not card and d is not None))

    for b in instances:
        d = _data(b['due_date'])
        if d and d > until:
            continue
        amount = dinheiro(b['amount'])
        reasons, realized = [], False
        if amount is None or amount <= 0:
            amount = None
            reasons.append(_motivo(s, 'valor_boleto_desconhecido', 'so_melhora', b['id']))
        if b['launch_id']:
            cur.execute(f'select tipo,valor,efeitos,origem,source,{VINCULADO_SQL} as especie '
                        'from launches where id=%s and user_id=%s',
                        (b['launch_id'], user_id))
            l = cur.fetchone()
            if l:
                delta = delta_verificavel(l)
                realized = (delta is not None and delta < 0 and dinheiro(b['paid_amount']) == -delta)
        rec = rec_by_id.get(b['recurring_id'])
        ambiguous = rec and (not rec['is_active'] or rec['payment_type'] == 'credit_card')
        if b['variable_amount'] and amount is not None and not realized:
            reasons.append(_motivo(s, 'valor_boleto_estimado', 'ambos', b['id']))
        if not realized:
            reasons.append(_motivo(s, 'realizacao_boleto_a_conferir', 'so_piora', b['id'],
                                   d.isoformat() if d else None, amount))
        if d is None:
            reasons.append(_motivo(s, 'data_boleto_desconhecida', 'ambos', b['id']))
        if ambiguous:
            reasons.append(_motivo(s, 'instancia_recorrente_a_conferir', 'ambos', b['id']))
        s.ocorrencias.append(Ocorrencia('instancia', b['id'], d.isoformat() if d else 'desconhecido',
            d, 'boleto', b['name'] or 'Boleto', amount, 'saida',
            'desconhecido' if amount is None else 'estimado' if b['variable_amount'] else 'conhecido',
            'conhecida' if d else 'desconhecida', 'realizada' if realized else 'a_conferir',
            tuple(reasons), not ambiguous))

    if bancos_permitidos:  # sem os bancos na base, despesa deles seria incoerente
        from core.services.previsao_recorrencias import anexar
        anexar(cur, s, user_id, until, recs, receitas, instances, {c['id'] for c in banks})

    for b in ler_faturas(cur, user_id):
        total, paid, remaining, invalid_value = valores_fatura(b)
        due, date_quality = calendario_fatura(b)
        known_date = date_quality == 'conhecida'
        if due > until:
            continue
        reasons = []
        if invalid_value:
            reasons.append(_motivo(s, 'valor_fatura_a_conferir', 'ambos', b['id']))
        if remaining == 0 and not invalid_value:
            continue
        if not known_date:
            reasons.append(_motivo(s, 'calendario_fatura_presumido', 'ambos', b['id']))
        if b['open_finance_account_id']:
            if b['currency'] != 'BRL':
                reasons.append(_motivo(s, 'moeda_fatura_desconhecida', 'ambos', b['id']))
            reasons.append(_motivo(s, 'cobertura_fatura_nao_comprovada', 'ambos', b['id']))
            if b['parcelas_of']:
                reasons.append(_motivo(s, 'parcelas_futuras_incompletas', 'so_melhora', b['id']))
        else:
            reasons.append(_motivo(s, 'cartao_manual_cobertura_incompleta', 'so_melhora', b['id']))
        reasons.append(_motivo(s, 'realizacao_fatura_a_conferir', 'so_piora', b['id'], efeito=remaining))
        if b['status'] == 'paid' or (paid is not None and paid > 0):
            reasons.append(_motivo(s, 'pagamento_fatura_reflexo_desconhecido', 'ambos', b['id']))
        valid_amount = not invalid_value and remaining > 0 and (
            not b['open_finance_account_id'] or b['currency'] == 'BRL')
        s.ocorrencias.append(Ocorrencia('fatura', b['id'], b['period_end'].isoformat(),
            due, 'fatura_cartao', b['card_name'], remaining if valid_amount else None,
            'saida', 'conhecido' if valid_amount else 'desconhecido',
            'conhecida' if known_date else 'presumida', 'a_conferir', tuple(reasons)))
    _motivo(s, 'gastos_variaveis_nao_estimados', 'so_melhora')
    return s


def valores_fatura(b):
    """Total observado e restante utilizável; crédito/incoerência não vira caixa."""
    total, paid = dinheiro(b['total']), dinheiro(b['paid_amount'])
    remaining = somar((total, paid.copy_negate())) if total is not None and paid is not None else None
    invalid = (remaining is None or remaining < 0 or total is None or total < 0
               or paid is None or paid < 0)
    return total, paid, None if invalid else remaining, invalid


def carregar(user_id: int, today: date, until: date) -> Snapshot:
    from db.connection import get_conn
    from core.services.plan_service import consolidated_balance_enabled
    now = datetime.now(timezone.utc)
    banks = consolidated_balance_enabled(user_id)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute('set transaction isolation level repeatable read, read only')
        s = ler(cur, user_id, today, until, now, banks)
        conn.rollback()
    return s
