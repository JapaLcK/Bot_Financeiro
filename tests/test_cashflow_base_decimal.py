"""M1: base bancária exata e paridade dos leitores reais, em DB pytest_UUID."""
from datetime import date, timedelta
from decimal import Decimal as D

import pytest
import db
from db import contas_hoje, patrimonio
from db.open_finance import merged_wallet_delta
from core.services.cashflow_snapshot import carregar
from core.services.ai_chat.tools.bills import _check_cashflow
from tests._patrimonio_helpers import (
    conexao, conta, q, tx_banco, caixinha, posicao, investimento_manual,
)


def conferir(uid, esperado, *, bancos, carteira=D(0), incerta=False):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute('set transaction isolation level repeatable read, read only')
        cur.execute('select current_database() as nome')
        assert cur.fetchone()['nome'].startswith('pytest_')
        cur.execute('select balance from accounts where user_id=%s', (uid,))
        bruto = cur.fetchone()['balance']
        ajuste = merged_wallet_delta(cur, uid)
        # Oracle SQL usa os mesmos componentes observados, sem aritmética Python arredondada.
        cur.execute('select %s::numeric + %s::numeric + %s::numeric as total',
                    (bruto, ajuste, bancos))
        assert cur.fetchone()['total'] == esperado
        antes = contas_hoje.listar(cur, uid)
        foto = patrimonio.calcular(cur, uid)
        conn.rollback()
    snapshot = carregar(uid, date.today(), date.today() + timedelta(days=30))
    tool = _check_cashflow(uid, {'days': 30})
    assert snapshot.base['saldo'] == esperado
    assert antes['total'] == esperado
    assert foto['bancos'] == bancos
    assert antes['carteira']['saldo'] == foto['carteira'] == carteira
    assert foto['total'] == esperado
    assert tool['saldo_atual'] == tool['projetado'] == float(esperado)
    assert tool['balance_source'] == snapshot.base['balance_source'] == 'consolidated'
    assert ('carteira_nao_confirmada' in antes['motivos']) is incerta
    assert ('carteira_nao_confirmada' in foto['motivos']) is incerta
    assert ('carteira_nao_confirmada' in snapshot.base['motivos']) is incerta
    assert tool['estado'] == 'a_conferir' and tool['cabe_nas_premissas'] is False
    assert {'cobertura_bancaria_nao_comprovada', 'gastos_variaveis_nao_estimados'} <= {
        m['codigo'] for m in tool['motivos']}
    with db.get_conn() as conn, conn.cursor() as cur:
        assert contas_hoje.listar(cur, uid) == antes
        cur.execute('select balance from accounts where user_id=%s', (uid,))
        assert cur.fetchone()['balance'] == bruto
        conn.rollback()


@pytest.mark.parametrize('saldos,esperado', [
    (['10', '20.01'], '30.01'),
    (['100', '.01', '-100'], '.01'),
    (['1e28', '.01', '-1e28'], '.01'),
    (['1e100', '.01', '-1e100'], '.01'),
    (['1e150', '.01', '-1e150'], '.01'),
    (['1e100', *['.01'] * 41, '-1e100'], '.41'),
], ids=['normal', 'cancelamento-normal', 'magnitude28', 'magnitude100', 'magnitude150', '43-contas'])
def test_m1_bancos_base_tool_contas_foto(pro_small_uid, monkeypatch, saldos, esperado):
    monkeypatch.setenv('OF_CONSOLIDATED_BALANCE_ENABLED', '1')
    cid = conexao(pro_small_uid, f'm1-{pro_small_uid}')
    for i, saldo in enumerate(saldos):
        conta(cid, f'{i:03}', D(saldo))
    bancos = q('select sum(balance) as total from open_finance_accounts where connection_id=%s',
               (cid,))['total']
    assert bancos == D(esperado)
    conferir(pro_small_uid, D(esperado), bancos=bancos)


@pytest.mark.parametrize('fundida', [False, True], ids=['carteira-banco', 'carteira-fusao-banco'])
def test_m1_carteira_banco_ajuste_uma_vez(pro_small_uid, monkeypatch, fundida):
    uid = pro_small_uid
    monkeypatch.setenv('OF_CONSOLIDATED_BALANCE_ENABLED', '1')
    cid = conexao(uid, f'm1-wallet-{uid}')
    aid = conta(cid, 'a', D('-1e100'))
    if fundida:
        lid, *_ = db.add_launch_and_update_balance(uid, 'despesa', D('.01'), 'Fusão', None)
        tx_banco(aid, 'fundida-1', D('-.01'), imported_launch_id=lid)
        tx_banco(aid, 'fundida-2', D('-.01'), imported_launch_id=lid)
    else:
        conta(cid, 'b', D('.01'))
    # Saldo observado legado sem teto NUMERIC; qualidade residual deve continuar incerta.
    q('update accounts set balance=%s where user_id=%s', (D('1e100'), uid))
    bancos = D('-1e100') if fundida else D('-' + '9' * 100 + '.99')
    carteira = D('1' + '0' * 100 + '.01') if fundida else D('1e100')
    conferir(uid, D('.01'), bancos=bancos, carteira=carteira, incerta=True)


def test_m1_patrimonio_preserva_partes_fora_do_caixa(pro_small_uid, monkeypatch):
    uid = pro_small_uid
    monkeypatch.setenv('OF_CONSOLIDATED_BALANCE_ENABLED', '1')
    cid = conexao(uid, f'm1-assets-{uid}')
    for pid, saldo in [('a', D('1e100')), ('b', D('.01')), ('c', D('-1e100'))]:
        conta(cid, pid, saldo)
    caixinha(uid, 'Bolso', D(2))
    posicao(cid, 'investimento', D(3))
    investimento_manual(uid, 'Manual', D(4))
    with db.get_conn() as conn, conn.cursor() as cur:
        hoje, foto = contas_hoje.listar(cur, uid), patrimonio.calcular(cur, uid)
        conn.rollback()
    assert hoje['total'] == carregar(uid, date.today(), date.today() + timedelta(days=30)).base['saldo'] == D('.01')
    assert _check_cashflow(uid, {'days': 30})['saldo_atual'] == .01
    assert (foto['bancos'], foto['caixinhas'], foto['investimentos_banco'], foto['investimentos_manuais'], foto['total']) == (D('.01'), D(2), D(3), D(4), D('9.01'))


@pytest.mark.parametrize('saldo,bolso', [
    ('NaN', 'Infinity'), ('NaN', '-Infinity'), ('NaN', 'NaN'),
    ('Infinity', '0'), ('-Infinity', '0'),
])
def test_m1_nao_finitos_preservam_indisponivel(pro_small_uid, saldo, bolso):
    uid = pro_small_uid
    q('update accounts set balance=%s::numeric where user_id=%s', (saldo, uid))
    caixinha(uid, 'Observação não finita', D(bolso))
    with db.get_conn() as conn, conn.cursor() as cur:
        hoje, foto = contas_hoje.listar(cur, uid), patrimonio.calcular(cur, uid)
        conn.rollback()
    assert hoje['carteira']['saldo'].as_tuple() == D(saldo).as_tuple()
    assert foto['carteira'].as_tuple() == D(saldo).as_tuple()
    assert hoje['total'].as_tuple() == foto['total'].as_tuple() == D(saldo).as_tuple()
    snapshot = carregar(uid, date.today(), date.today() + timedelta(days=30))
    tool = _check_cashflow(uid, {'days': 30})
    assert snapshot.base['saldo'] is None
    assert tool['saldo_atual'] is tool['projetado'] is None
    assert tool['estado'] == 'indisponivel'
    assert 'carteira_nao_confirmada' in hoje['motivos']
    assert 'carteira_nao_confirmada' in foto['motivos']
