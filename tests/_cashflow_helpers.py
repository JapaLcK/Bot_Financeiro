"""Fontes unitárias do snapshot; dinheiro/isolamento real têm testes próprios."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal


def _mock_sources(monkeypatch, *, saldo=0.0, incomes=(), expenses=(), bills=(), card_bills=(), uids=None):
    import core.services.cashflow as cf
    import core.services.cashflow_forecast as ff
    import core.services.decision_simulator as ds
    import core.services.cashflow_snapshot as cs
    from db import contas_hoje, recurring, recurring_income, bills as dbills, cards

    def anota(name, uid):
        if uids is not None:
            uids.setdefault(name, []).append(uid)

    class Cursor:
        def execute(self, sql, params=()):
            self.rows = [{'balance': Decimal(str(saldo))}] if sql.startswith('select balance from accounts') else []
        def fetchone(self):
            return self.rows[0] if self.rows else None
        def fetchall(self):
            return self.rows

    reference_today = date.today()
    def normalize(rows, income=False):
        result=[]
        for i, raw in enumerate(rows):
            v=dict(raw);v.setdefault('id', i+1);v.setdefault('name', 'Receita' if income else 'Gasto fixo')
            v.setdefault('frequency', 'monthly')
            if v['frequency'] in ('monthly','annual'):
                v.setdefault('start_date', reference_today)
            else:
                v.setdefault('start_date', None)
            v.setdefault('payment_type', 'account');v.setdefault('payment_mode', 'autopay')
            v['amount'] = Decimal(str(v['amount'])) if v.get('amount') is not None else None
            result.append(v)
        return result

    def accounts(cur, uid, *, agora=None):
        anota('contas_hoje', uid)
        return {'total': Decimal(str(saldo)), 'carteira': {'saldo': Decimal(str(saldo)), 'motivos': []},
                'contas': [], 'motivos': []}
    def expenses_read(cur, uid, inactive=False):
        anota('ler_recorrentes', uid);return normalize(expenses)
    def incomes_read(cur, uid, inactive=False):
        anota('ler_receitas', uid);return normalize(incomes, True)
    def instances_read(cur, uid, *args):
        anota('ler_instancias', uid)
        rows=[]
        for i,b in enumerate(bills):
            item=dict(id=i+1, recurring_id=None, paid_amount=None, launch_id=None, variable_amount=False)
            item.update(b); rows.append(item)
        return rows
    def card_read(cur, uid):
        anota('ler_faturas', uid)
        return [dict(id=i+1, card_id=i+1, period_start=b['due_date'].replace(day=1),
                     period_end=b['due_date'], closing_day=b['due_date'].day, due_day=b['due_date'].day,
                     total=Decimal(str(b['remaining'])), paid_amount=Decimal(0), status='open',
                     card_name=b['card_name'], open_finance_account_id=None, currency='BRL',
                     account_raw=None, connection_status=None, last_sync_at=None, parcelas_of=False)
                for i,b in enumerate(card_bills)]
    monkeypatch.setattr(contas_hoje, 'listar', accounts)
    monkeypatch.setattr(recurring, 'ler_recorrentes', expenses_read)
    monkeypatch.setattr(recurring_income, 'ler_receitas', incomes_read)
    monkeypatch.setattr(dbills, 'ler_instancias', instances_read)
    monkeypatch.setattr(cards, 'ler_faturas', card_read)
    import db.bank_movements, db.open_finance
    monkeypatch.setattr(db.bank_movements, '_declarations', lambda cur, uid: [])
    monkeypatch.setattr(db.open_finance, 'actionable_pending_params', lambda cur, uid: (uid,))
    def load(uid, today, until):
        nonlocal reference_today
        reference_today = today
        return cs.ler(Cursor(), uid, today, until, datetime.now(timezone.utc), False)
    for m in (cf,ff,ds):
        monkeypatch.setattr(m, 'carregar', load)
    return cf


def fontes_que_mudam(monkeypatch):
    from db import contas_hoje, recurring, recurring_income, bills, cards
    cf=_mock_sources(monkeypatch, saldo=1000, bills=[
        {'status':'pending','due_date':date.today()+timedelta(days=10),'amount':700,'name':'Aluguel'}])
    calls={}
    for mod,name in ((contas_hoje,'listar'),(recurring,'ler_recorrentes'),(recurring_income,'ler_receitas'),
                     (bills,'ler_instancias'),(cards,'ler_faturas')):
        before=getattr(mod,name)
        key='contas_hoje' if name=='listar' else name
        def wrapped(*args,_before=before,_key=key,**kwargs):
            calls[_key]=calls.get(_key,0)+1
            out=_before(*args,**kwargs)
            if calls[_key]>1:
                if _key=='contas_hoje':
                    out['carteira']['saldo']=Decimal(10)
                else:
                    return []
            return out
        monkeypatch.setattr(mod,name,wrapped)
    return calls


def compactar(items, *, data=False):
    """Compatibilidade dos campos numéricos; identidade/qualidade têm prova separada."""
    keys=('date','tipo','nome','valor') if data else ('tipo','nome','valor')
    return [{k:item[k] for k in keys} for item in items]
