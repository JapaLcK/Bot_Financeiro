from datetime import date, timedelta
from decimal import Decimal as D
from pathlib import Path

import pytest
from psycopg.types.json import Jsonb

import db
from core.services.cashflow import _projection, project, validar_extra
from core.services.cashflow_forecast import forecast_with_trajectory
from core.services.cashflow_snapshot import Ocorrencia, centavos
from core.services.ai_chat.tools.cards import _forecast_next_bill
from db.bills import create_boleto, mark_bill_paid
from db.recurring import create_recurring_expense
from db.recurring_income import create_recurring_income
from test_cashflow_snapshot import q, snapshot


def test_reparo_extra_grande_preserva_centavos_antes_da_apresentacao():
    today = date.today()
    base = dict(saldo=D('0.05'), balance_source='manual', of_bank_count=0, banks_excluded=False)
    out = _projection(today, base, [Ocorrencia('instancia', 1, 'x', today,
                      'boleto', 'Pequeno', D('0.02'), 'saida')], today, D('1' + '0' * 100 + '.01'))
    assert out['projetado'] == D('-' + '9' * 100 + '.98')
    assert out['boleto_novo'] == D('1' + '0' * 100 + '.01')
    # HALF_EVEN pode carregar mais um dígito integral ao apresentar centavos.
    carry = _projection(today, dict(base, saldo=D(0)), [], today, D('9' * 100 + '.995'))
    assert carry['boleto_novo'] == D('1e100') and carry['projetado'] == D('-1e100')
    # Cancelar o grande com receita mantém o centavo da base.
    base['saldo'] = D('0.01')
    out = _projection(today, base, [Ocorrencia('receita_recorrente', 1, 'x', today,
                      'receita', 'Grande', D('1e100'), 'entrada')], today, D('1e100'))
    assert out['projetado'] == D('0.01')
    # Trocar o sinal também preserva todos os dígitos: saída grande + ajuste assinado.
    large = D('1' + '0' * 100 + '.01')
    e = Ocorrencia('instancia', 1, 'x', today, 'boleto', 'Grande', large, 'saida')
    out = _projection(today, base, [e], today, large.copy_negate())
    assert e.assinado == large.copy_negate() and out['projetado'] == D('0.01')
    assert out['boletos_ate'] == large
    from core.services.cashflow_forecast import _trajectory
    revenue = Ocorrencia('receita_recorrente', 2, 'x', today, 'receita', 'Compensa', large, 'entrada')
    trajectory = _trajectory(today, base, [e, revenue], 1, 0)
    assert trajectory['trajectory'][0]['saldo_projetado'] == D('0.01')
    assert _projection(today, base, [], today, D('0e-1000000000'))['projetado'] == D('0.01')


@pytest.mark.parametrize('amount', ['1e309', '-1e309', '1e-400', 'nan', 'inf', True])
def test_reparo_extra_fora_do_number_legado_recusado_sem_limite_de_produto(amount):
    with pytest.raises(ValueError):
        validar_extra(amount)


@pytest.mark.parametrize('amount', ['1e309', '1e-400'])
def test_reparo_extra_nao_representavel_erro_compartilhado_tool_http(pro_small_uid, amount):
    from core.services.ai_chat.tools.bills import _check_cashflow
    from tests.test_manual_launches_carteira_piggy import _dashboard_client
    out = _check_cashflow(pro_small_uid, {'days': 30, 'amount': amount})
    assert out['error'] == 'invalid_args'
    client, headers = _dashboard_client(pro_small_uid, f'reparo-{pro_small_uid}@example.invalid')
    response = client.get(f'/recurring-bills/{pro_small_uid}/projection', params={
        'date': str(date.today() + timedelta(days=30)), 'amount': amount}, headers=headers)
    assert response.status_code == 400


@pytest.mark.parametrize('income', [False, True])
@pytest.mark.parametrize('outside', [False, True])
def test_reparo_data_inicio_tem_precedencia_so_quando_fora_comprovado(user_id, income, outside):
    today = date.today()
    start = today + timedelta(days=100 if outside else 1)
    if income:
        r = create_recurring_income(user_id, 'Renda legada', 10, 'outros', start.day, start_date=start)
        table = 'recurring_incomes'
    else:
        r = create_recurring_expense(user_id, 'Gasto legado', 10, 'outros', start.day,
                                     'account', frequency='once', start_date=start)
        table = 'recurring_expenses'
    q(f'update {table} set frequency=%s,amount=%s where id=%s and user_id=%s', ('legada', 10 if income else 0, r['id'], user_id))
    s = snapshot(user_id, 30)
    if outside:
        assert not s.ocorrencias and not any(m.origem_id == r['id'] for m in s.motivos)
    else:
        assert s.ocorrencias[0].data is None
        assert s.ocorrencias[0].valor == (D(10) if income else None)
        assert any(m.codigo == 'calendario_recorrente_desconhecido' for m in s.motivos)


@pytest.mark.parametrize('change', [
    {'efeitos': {'delta_conta': -20, 'chave_futura': 1}},
    {'efeitos': {'delta_conta': -20.001}},
    {'efeitos': {'delta_conta': -20, 'funding_source': {'kind': 'bank'}}},
    {'efeitos': {'delta_conta': -20, 'funding_source': []}},
    {'efeitos': {'delta_conta': -20, 'funding_source': {'kind': 'legada'}}},
    {'efeitos': {'delta_conta': -20, 'bill_id': 99}},
    {'origem': None}, {'source': 'importado'}, {'tipo': 'receita'}, {'valor': D(21)},
])
def test_reparo_realizacao_reutiliza_composicao_e_nao_aceita_contradicoes(user_id, change):
    db.add_launch_and_update_balance(user_id, 'receita', 100, 'Dinheiro', None)
    due = date.today() + timedelta(days=2)
    b = create_boleto(user_id, 'Antecipado', 20, due)
    mark_bill_paid(user_id, b['id'], 20, metodo='carteira')
    lid = q('select launch_id from bill_instances where id=%s and user_id=%s', (b['id'], user_id))[0]['launch_id']
    field, value = next(iter(change.items()))
    q(f'update launches set {field}=%s where id=%s and user_id=%s',
      (Jsonb(value) if field == 'efeitos' else value, lid, user_id))
    s = snapshot(user_id)
    e = next(e for e in s.ocorrencias if e.fonte == 'instancia')
    assert e.realizacao == 'a_conferir' and e.assinado == D(-20)
    assert 'carteira_nao_confirmada' in {m.codigo for m in s.motivos}
    assert any(m.codigo == 'realizacao_boleto_a_conferir' for m in e.motivos)
    assert s.base['saldo'] == D(80)
    forecast = forecast_with_trajectory(user_id)
    assert next(c for c in forecast['compromissos'] if c['chave'] == e.chave)['realizacao'] == 'a_conferir'
    assert any(c['chave'] == e.chave for c in forecast['trajectory'][1]['compromissos'])
    assert forecast['horizons']['30']['n_boletos'] == 1
    assert forecast['horizons']['30']['boletos_ate'] == 20
    assert forecast['horizons']['30']['projetado'] == 60


@pytest.mark.parametrize('total,paid,status,restante', [
    (0, -1, 'paid', None), (100, 110, 'open', None), (-10, -10, 'paid', None),
    (100, 30, 'paid', 70), (100, 30, 'open', 70), (100, 100, 'paid', 'ausente'),
])
def test_reparo_faturas_compartilham_validacao_sem_escrever(user_id, total, paid, status, restante):
    card = db.create_card(user_id, 'Fatura reparo', 10, 17)
    db.add_credit_purchase(user_id, card, 100, 'outros', 'Compra', date.today())
    q('update credit_bills set total=%s,paid_amount=%s,status=%s where user_id=%s and card_id=%s',
      (total, paid, status, user_id, card))
    before = q('select total,paid_amount,status from credit_bills where user_id=%s', (user_id,))
    s, out = snapshot(user_id, 90), _forecast_next_bill(user_id, {})
    after = q('select total,paid_amount,status from credit_bills where user_id=%s', (user_id,))
    assert before == after
    if restante == 'ausente':
        assert not s.ocorrencias and out['cards'] == []
    else:
        e = next(e for e in s.ocorrencias if e.fonte == 'fatura')
        assert out['count'] == 1 and out['total'] == total
        assert out['cards'][0]['restante'] == restante and e.valor == (D(restante) if restante is not None else None)
        assert ('valor_fatura_a_conferir' in out['cards'][0]['motivos']) == (restante is None)
        assert ('valor_fatura_a_conferir' in {m.codigo for m in e.motivos}) == (restante is None)
        forecast = forecast_with_trajectory(user_id)
        detail = next(c for c in forecast['compromissos'] if c['chave'] == e.chave)
        assert detail['realizacao'] == 'a_conferir' and detail['valor'] == restante
        assert {m['codigo'] for m in detail['motivos']} == {m.codigo for m in e.motivos}
        assert forecast['horizons']['90']['faturas_cartao'] == (restante or 0)


def test_reparo_gate_aplica_predicado_real_a_todos_novos_modulos():
    import test_max_lines_python as gate
    root = Path(__file__).resolve().parents[1]
    for name in ('core/services/cashflow_snapshot.py', 'core/services/cashflow_contract.py', 'db/carteira_qualidade.py'):
        assert not gate._estoura(name, (root / name).read_bytes())
    assert centavos(D('2.685')) == D('2.68')
    assert centavos(D('-0.004')) == D(0) and not centavos(D('-0.004')).is_signed()


def test_reparo_vinculo_especie_real_e_pagamento_fisico_continuam_comprovados(user_id, monkeypatch):
    from tests._of_cash_helpers import conecta, sync, tx, ATIVACAO
    monkeypatch.setenv('OF_CASH_ENABLED', '1')
    q('insert into of_cash_activation(activated_at) values (%s) '
      'on conflict (id) do update set activated_at=excluded.activated_at', (ATIVACAO,))
    cid = conecta(user_id, f'especie-reparo-{user_id}')
    sync(cid, user_id, [tx('saque-reparo', -100, date.today())], saldo='0')
    from db.open_finance_cash import VINCULADO_SQL
    rows = q(f'select id,{VINCULADO_SQL} as especie from launches where user_id=%s and origem=%s',
             (user_id, 'carteira'))
    assert len(rows) == 1 and rows[0]['especie']
    s = snapshot(user_id)
    assert s.base['saldo'] == D(100)
    assert 'carteira_nao_confirmada' not in {m.codigo for m in s.motivos}
    due = date.today() + timedelta(days=2)
    b = create_boleto(user_id, 'Dinheiro do saque', 20, due)
    mark_bill_paid(user_id, b['id'], 20, metodo='carteira')
    s = snapshot(user_id)
    e = next(e for e in s.ocorrencias if e.fonte == 'instancia')
    assert s.base['saldo'] == D(80) and e.realizacao == 'realizada' and e.assinado == 0
    assert 'carteira_nao_confirmada' not in {m.codigo for m in s.motivos}


@pytest.mark.parametrize('offset', [-1, 0, 3])
def test_reparo_compromisso_realizado_sai_da_previsao_mas_fica_na_snapshot(user_id, offset):
    from core.services.decision_simulator import Simulacao, simulate
    today, future = date.today(), date.today() + timedelta(days=3)
    due = today + timedelta(days=offset)
    db.add_launch_and_update_balance(user_id, 'receita', 100, 'Dinheiro', None)
    paid = create_boleto(user_id, 'Antecipado', 20, due)
    mark_bill_paid(user_id, paid['id'], 20, metodo='carteira')
    opened = create_boleto(user_id, 'Ainda pendente', 10, future)
    s = snapshot(user_id)
    realized = next(e for e in s.ocorrencias if e.origem_id == paid['id'])
    pending = next(e for e in s.ocorrencias if e.origem_id == opened['id'])
    assert realized.chave == f"instancia:{paid['id']}:{due.isoformat()}"
    assert realized.valor == D(20) and realized.realizacao == 'realizada' and realized.assinado == 0
    assert s.base['saldo'] == D(80) and len(s.ocorrencias) == 2
    forecast = forecast_with_trajectory(user_id, days=3)
    projected = project(user_id, future, percurso=True)
    simulated = simulate(user_id, Simulacao(cenarios=[{'nome': 'Hipótese', 'preco': 1}]))
    assert [d['saldo_projetado'] for d in forecast['trajectory']] == [80, 80, 70]
    assert projected['projetado'] == projected['minimo_percurso'] == 70
    assert projected['boletos_ate'] == 10
    assert simulated['atual']['saldo_final_90'] == simulated['atual']['pior_dia']['saldo'] == 70
    assert simulated['atual']['pior_dia']['date'] == future.isoformat()
    observed = {
        'vencidos': [c['chave'] for c in forecast['vencidos']],
        'vencem_hoje': [c['chave'] for c in forecast['vencem_hoje']],
        'trajectory': [c['chave'] for d in forecast['trajectory'] for c in d['compromissos']],
        'worst_compromissos': [c['chave'] for c in forecast['worst_day']['compromissos']],
        'worst_causas': [c['chave'] for c in forecast['worst_day']['causas']],
        'topo': [c['chave'] for c in forecast['compromissos']],
        'n_boletos': projected['n_boletos'],
        'horizons_n_boletos': [h['n_boletos'] for h in forecast['horizons'].values()],
    }
    for field in ('vencidos', 'vencem_hoje', 'trajectory', 'worst_compromissos', 'worst_causas'):
        assert realized.chave not in observed[field], observed
    assert observed['trajectory'] == observed['worst_compromissos'] == observed['worst_causas'] == [pending.chave]
    assert observed['topo'] == [pending.chave], observed
    assert observed['n_boletos'] == 1 and observed['horizons_n_boletos'] == [1, 1, 1], observed
    assert next(e for e in snapshot(user_id).ocorrencias if e.chave == realized.chave) == realized


def test_reparo_previsao_preserva_desconhecido_e_excluido_sem_data(user_id):
    due = date.today() + timedelta(days=3)
    b = create_boleto(user_id, 'Valor desconhecido', 1, due)
    q('update bill_instances set amount=0 where id=%s and user_id=%s', (b['id'], user_id))
    card = db.create_card(user_id, 'Cartão', 10, 17)
    r = create_recurring_expense(user_id, 'Cartão sem calendário', 30, 'outros', due.day,
                                 'credit_card', card_id=card, frequency='once', start_date=due)
    q('update recurring_expenses set frequency=%s where id=%s and user_id=%s', ('legada', r['id'], user_id))
    s = snapshot(user_id)
    unknown = next(e for e in s.ocorrencias if e.fonte == 'instancia' and e.origem_id == b['id'])
    excluded = next(e for e in s.ocorrencias if e.fonte == 'gasto_recorrente' and e.origem_id == r['id'])
    assert unknown.valor is None and unknown.assinado == 0 and unknown.realizacao == 'a_conferir'
    assert excluded.data is None and not excluded.incluida and excluded.realizacao == 'a_conferir'
    forecast = forecast_with_trajectory(user_id, days=3)
    details = {c['chave']: c for c in forecast['compromissos']}
    assert set(details) == {unknown.chave, excluded.chave}
    assert details[unknown.chave]['valor'] is None and details[unknown.chave]['qualidade_valor'] == 'desconhecido'
    assert {m['codigo'] for m in details[unknown.chave]['motivos']} == {m.codigo for m in unknown.motivos}
    assert details[excluded.chave]['date'] is None and not details[excluded.chave]['incluida']
    assert {m['codigo'] for m in details[excluded.chave]['motivos']} == {m.codigo for m in excluded.motivos}
    assert [c['chave'] for c in forecast['trajectory'][2]['compromissos']] == [unknown.chave]
    assert forecast['horizons']['30']['n_boletos'] == 1
    assert forecast['horizons']['30']['boletos_ate'] == 0
