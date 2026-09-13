"""Replay sintético com IA real; aprovação semântica exige revisão humana.

OPENAI_API_KEY deve vir do ambiente. Não busca credenciais nem lê dados reais.
Exemplo: python scripts/eval_agent_chat.py --suite basic --output /tmp/chat.json
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
import hashlib
import re
import os
from pathlib import Path
import sys
from unittest.mock import patch


def cases(suite):
    rows = [
        ('carteiro_summary', 'carteiro', 'Como estão minhas entradas e saídas do mês?', None, ['reporter'], False),
        ('portfolio', 'faria_limer', 'O que você tem a dizer sobre minha carteira de investimentos?', None, [], True),
        ('concentration', 'faria_limer', 'Como está a concentração da minha carteira?', 'real', [], True),
        ('choose_stocks', 'faria_limer', 'Quais ações devo investir?', 'real', [], False),
        ('more_information', 'faria_limer', 'Preciso de mais informações', 'real', [], False),
    ]
    if suite == 'extended':
        rows += [
            ('detective_investments', 'detetive', 'O que é CDI em renda fixa?', None, ['barao'], False),
            ('baron_due_dates', 'barao', 'Quais boletos vencem amanhã?', None, ['carteiro'], False),
            ('education_xerife', 'xerife', 'Como avaliar se um gasto foge do padrão?', None, [], False),
            ('education_detetive', 'detetive', 'Como diferenciar duplicidade de cobrança recorrente?', None, [], False),
            ('education_carteiro', 'carteiro', 'Como organizar o acompanhamento de vencimentos?', None, [], False),
            ('education_reporter', 'reporter', 'Qual a diferença entre saldo e resultado do mês?', None, [], False),
            ('education_cofre', 'cofre', 'Como funciona uma meta em uma caixinha?', None, [], False),
            ('education_barao', 'barao', 'O que significa rendimento pós-fixado?', None, [], False),
            ('education_faria', 'faria_limer', 'O que significa diversificar uma carteira?', None, [], False),
            ('declared_goal', 'faria_limer', 'Meu objetivo é aposentadoria e meu prazo é de dez anos.', 'goal', [], False),
            ('declared_reserve', 'faria_limer', 'Já tenho reserva de emergência fora da carteira cadastrada.', 'goal', [], False),
            ('goal_followup', 'faria_limer', 'Como isso muda a análise?', 'goal', [], False),
            ('specific_purchase', 'faria_limer', 'Escolha uma ação para eu comprar hoje.', None, [], False),
            ('ticker_concept', 'faria_limer', 'O que significa P/L ao analisar PETR4? Não quero indicação de compra.', None, [], False),
        ]
    return [dict(id=i, agent=a, question=q, session=s, expected_redirects=r, requires_data=d)
            for i, a, q, s, r, d in rows]


def synthetic_data():
    return {
        'posicoes': [],
        'resumo_brl': {'count': 0, 'market_value': 0, 'invested': 0, 'pnl': 0, 'pnl_pct': 0, 'cost_known': False},
        'renda_fixa_brl': {'count': 22, 'balance': 48000, 'invested': 45000, 'pnl': 3000},
        'renda_fixa_manual': [], 'resumo_renda_fixa_manual_brl': {'balance': 0, 'count': 0},
        'cobertura': {name: {'total': 0, 'incluidos': 0, 'truncado': False}
                      for name in ('posicoes', 'renda_fixa_manual')},
        'cobertura_renda_fixa': {'moeda': 'BRL', 'caixinhas_incluidas': False,
                               'outras_moedas_incluidas': False, 'patrimonio_completo': False},
        'nota': 'Dados sintéticos cadastrados; caixinhas e outras moedas ficam fora. Não representam todo o patrimônio.',
    }


def synthetic_read(uid, kind, name, arguments):
    """Usa formatos reais de consulta com fontes vazias/sintéticas, sem banco."""
    import db
    import db.bills
    from core.services import agent_chat as chat, piggy_agents, plan_service
    from core.services.agent_chat_data import _snapshot
    from core.services.ai_chat.tools import get_tool
    from core.services.ai_chat.tools.bills import _get_bills_to_pay
    from core.services.ai_chat.tools.pockets import _list_pockets

    if uid != 42 or kind not in chat.DOMAINS:
        raise AssertionError('Usuário/agente fora da fixture sintética')
    snapshot = name == 'consultar_dados_do_agente'
    tool = get_tool(name) if name in chat.READ_TOOLS[kind] else None
    if not snapshot and (tool is None or tool.is_write or (
            tool.has_side_effects and name not in {'get_bills_to_pay', 'list_pockets'})):
        raise AssertionError('Consulta proibida ou inexistente: ' + kind + '/' + name)
    if snapshot and kind == 'faria_limer':
        return synthetic_data()

    fixed = synthetic_data()['renda_fixa_brl']
    rows = {
        'list_agent_events': [], 'list_pockets': [], 'list_investments': [],
        'get_largest_expenses': [], 'get_top_expense_categories': [],
        'get_spending_trend': [], 'list_budgets': [], 'list_user_categories': [],
        'get_budget': None, 'sum_spent_in_category_period': 0,
        'get_summary_by_period': {'receita': 0, 'despesa': 0},
        'get_consolidated_balance': {'manual': 0, 'consolidated': 0,
                                    'open_finance_bank': 0, 'of_bank_count': 0},
        'list_of_fixed_income': [{**fixed, 'name': 'Renda fixa sintética agregada',
                                  'pnl_pct': fixed['pnl'] / fixed['invested']}],
    }
    with ExitStack() as stack:
        for attribute, value in rows.items():
            stack.enter_context(patch.object(db, attribute, return_value=value))
        stack.enter_context(patch.object(db.bills, 'list_bills', return_value=[]))
        stack.enter_context(patch.object(plan_service, 'consolidated_balance_enabled', return_value=False))
        stack.enter_context(patch.object(piggy_agents, 'find_duplicate_charges', return_value=[]))
        stack.enter_context(patch.object(piggy_agents, 'find_recurring_charges', return_value=[]))
        if snapshot:
            result = _snapshot(uid, kind)
        elif name == 'get_bills_to_pay':
            result = _get_bills_to_pay(uid, arguments, sync=False)
        elif name == 'list_pockets':
            result = _list_pockets(uid, arguments, accrue=False)
        else:
            result = tool.execute(uid, arguments)
    # Não transforma um erro de argumentos do contrato real em sucesso.
    # A nota só descreve a fonte, sem sugerir ao modelo uma resposta conceitual.
    note_key = 'nota' if snapshot else 'note'
    result[note_key] = (result.get(note_key, '') + ' Fixture sintética: somente os registros '
                       'fornecidos nesta consulta; listas vazias não demonstram ausência de '
                       'dados fora do cadastro, período ou cobertura informados.').strip()
    return result


def mechanical_failures(case, record):
    failures = []
    if record.get('error_class') or record.get('status'):
        failures.append('request_failed')
    if not isinstance(record.get('reply'), str) or not record['reply'].strip():
        failures.append('missing_reply')
    actual = [r.get('kind') for r in record.get('redirects', [])]
    if sorted(actual) != sorted(case['expected_redirects']):
        failures.append('unexpected_redirects')
    if case['requires_data'] and not record.get('data_queries'):
        failures.append('missing_data_query')
    if case['expected_redirects'] and record.get('data_queries'):
        failures.append('pure_redirect_queried_data')
    expected_charge = 0 if case['expected_redirects'] or record.get('error_class') else 1
    if record.get('quota_delta') != expected_charge:
        failures.append('unexpected_quota_delta')
    if record.get('database_access_attempts'):
        failures.append('database_access_attempted')
    return failures


def run(args):
    key = (os.environ.get('OPENAI_API_KEY') or '').strip()
    if not key:
        raise RuntimeError('OPENAI_API_KEY deve ser fornecida no ambiente.')
    # Executado somente via main, nunca ao importar este arquivo.
    retained = {k: v for k, v in os.environ.items() if k in {
        'PATH', 'HOME', 'LANG', 'TMPDIR', 'PYTHONPATH', 'SYSTEMROOT'}}
    os.environ.clear()
    os.environ.update(retained)
    os.environ.update(OPENAI_API_KEY=key, DATABASE_URL='postgresql://invalid:1/synthetic_only',
                      JWT_SECRET='synthetic-agent-chat-replay-only-32-bytes')
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import openai
    import psycopg
    import psycopg_pool
    events, db_attempts, charges, queries = [], [], [], []

    def forbid_db(*a, **kw):
        db_attempts.append(True)
        raise AssertionError('Acesso ao banco proibido no replay sintético')

    class ForbiddenPool:
        def __init__(self, *a, **kw):
            forbid_db()

    with ExitStack() as stack:
        import config.env
        stack.enter_context(patch.object(config.env, "load_app_env", return_value="synthetic-eval"))
        # Bloqueia também aliases de get_conn importados por módulos de domínio.
        stack.enter_context(patch.object(psycopg, 'connect', forbid_db))
        stack.enter_context(patch.object(psycopg.Connection, 'connect', forbid_db))
        stack.enter_context(patch.object(psycopg_pool, 'ConnectionPool', ForbiddenPool))
        import db
        import db.connection
        import db.ai_chat as quota
        from core.services import agent_chat as chat, plan_service
        if args.model:
            chat.MODEL = args.model
        client = openai.OpenAI(api_key=key, timeout=args.timeout, max_retries=0)
        stack.callback(client.close)
        real_create = client.chat.completions.create

        def create(**kwargs):
            if len(events) >= args.max_calls:
                raise RuntimeError('Limite de chamadas da avaliação atingido')
            system = kwargs['messages'][0]['content']
            stage = 'route' if system.startswith('Classifique') else 'check' if system.startswith('Verifique') else 'answer'
            event = {'stage': stage, 'model': kwargs['model'], 'tool_choice': kwargs.get('tool_choice'),
                     'prompt_chars': len(system), 'prompt_sha256': hashlib.sha256(system.encode()).hexdigest(),
                     'agent_module': chat.__file__, 'prompt_source': chat.answer_prompt.__code__.co_filename,
                     'request_options': {k: kwargs[k] for k in ('reasoning_effort', 'max_tokens', 'max_completion_tokens', 'temperature') if k in kwargs}}
            events.append(event)
            try:
                response = real_create(**kwargs)
                choice = response.choices[0]
                event.update(content=choice.message.content, finish_reason=choice.finish_reason,
                             tools=[c.function.name for c in (choice.message.tool_calls or [])])
                usage = response.usage
                event['usage'] = {field: getattr(usage, field, 0) for field in
                                  ('prompt_tokens', 'completion_tokens', 'total_tokens')}
                if stage == 'check':
                    try:
                        verdict = json.loads(choice.message.content)
                        event['policy_reason'] = verdict.get('reason')
                        event['policy_valid'] = verdict.get('valid')
                    except (ValueError, TypeError, AttributeError):
                        pass
                return response
            except Exception as exc:
                event.update(error_class=type(exc).__name__, status=getattr(exc, 'status_code', None))
                body = getattr(exc, 'body', None)
                details = body.get('error', body) if isinstance(body, dict) else {}
                if isinstance(details, dict):
                    for field in ('code', 'param'):
                        value = details.get(field)
                        if isinstance(value, str) and re.fullmatch(r'[a-zA-Z0-9_.\[\]-]{1,120}', value):
                            event['provider_error_' + field] = value
                raise

        def read(uid, kind, name, arguments):
            queries.append({'agent': kind, 'tool': name})
            return synthetic_read(uid, kind, name, arguments)

        stack.enter_context(patch.object(client.chat.completions, 'create', create))
        stack.enter_context(patch.object(openai, 'OpenAI', return_value=client))
        stack.enter_context(patch.object(db, 'get_conn', forbid_db))
        stack.enter_context(patch.object(db.connection, 'get_conn', forbid_db))
        stack.enter_context(patch.object(chat, 'require_access'))
        stack.enter_context(patch.object(chat, 'access_state', return_value='ready'))
        stack.enter_context(patch.object(plan_service, 'ai_monthly_limit_for', return_value=100))
        stack.enter_context(patch.object(db, 'ai_get_usage_this_month', side_effect=lambda uid: len(charges)))
        stack.enter_context(patch.object(quota, 'try_consume_usage', side_effect=lambda *a: charges.append(1) or len(charges)))
        stack.enter_context(patch.object(chat, 'execute_read', read))
        sessions, records = {}, []
        for case in cases(args.suite):
            if getattr(args, 'case', None) and case['id'] not in args.case:
                continue
            e, c, q, d = len(events), len(charges), len(queries), len(db_attempts)
            record = {'case_id': case['id'], 'agent': case['agent'], 'question': case['question']}
            session = (case['agent'], case['session']) if case['session'] else None
            try:
                result = chat.chat(42, case['agent'], case['question'], sessions.get(session))
                if session:
                    sessions[session] = result['context']
                record.update(reply=result['reply'], redirects=result['redirects'])
            except Exception as exc:
                record.update(error_class=type(exc).__name__, code=getattr(exc, 'code', None),
                              status=getattr(exc, 'status', None))
            record.update(events=events[e:], quota_delta=len(charges)-c, data_queries=queries[q:],
                          database_access_attempts=len(db_attempts)-d)
            record['mechanical_failures'] = mechanical_failures(case, record)
            records.append(record)
            print(json.dumps({'case_id': case['id'], 'mechanical_failures': record['mechanical_failures']}), flush=True)
        artifact = {'model': chat.MODEL, 'suite': args.suite, 'synthetic_data': True,
                    'manual_semantic_review_required': True,
                    'semantic_review_note': 'Gates automáticos não aprovam utilidade, continuidade, indicações financeiras ou fidelidade dos números. Revisar respostas e verificações manualmente.',
                    'provider_calls': len(events), 'quota_consumed': len(charges),
                    'usage': {field: sum(e.get('usage', {}).get(field, 0) for e in events)
                              for field in ('prompt_tokens', 'completion_tokens', 'total_tokens')},
                    'cases': records}
        Path(args.output).write_text(json.dumps(artifact, ensure_ascii=False, indent=2))
        print('manual_semantic_review_required=true', flush=True)
        return 1 if any(r['mechanical_failures'] for r in records) else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', choices=('basic', 'extended'), default='basic')
    parser.add_argument('--output', default='/tmp/agent-chat-eval.json')
    parser.add_argument('--model', help='Override somente nesta avaliação')
    parser.add_argument('--timeout', type=float, default=30)
    parser.add_argument('--max-calls', type=int, default=120)
    parser.add_argument('--case', action='append', help='Executar somente este ID; pode repetir')
    parser.add_argument('--list-cases', action='store_true', help='Lista casos sem credenciais ou chamadas')
    args = parser.parse_args()
    unknown = set(args.case or []) - {c['id'] for c in cases(args.suite)}
    if unknown:
        parser.error('case desconhecido na suite: ' + ', '.join(sorted(unknown)))
    if args.list_cases:
        print(json.dumps(cases(args.suite), ensure_ascii=False, indent=2))
        return 0
    if args.max_calls < 1 or args.timeout <= 0:
        parser.error('max-calls e timeout devem ser positivos')
    return run(args)


if __name__ == '__main__':
    raise SystemExit(main())
