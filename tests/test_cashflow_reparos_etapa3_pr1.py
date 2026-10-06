from datetime import date, timedelta
from decimal import Decimal as D
from pathlib import Path

import pytest
from psycopg.types.json import Jsonb

import db
from core.services.cashflow import _projection, validar_extra
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
