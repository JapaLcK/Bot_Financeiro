"""A leitura leve dos agentes preserva o contrato completo dos demais fluxos."""
from datetime import date

import db
import db.investments as investments


def test_listagem_leve_preserva_campos_isolamento_e_default_com_lotes(monkeypatch, user_id):
    other_user_id = user_id + 1
    db.ensure_user(other_user_id)
    for uid in (user_id, other_user_id):
        db.create_investment(uid, 'Reserva', 1, 'monthly')
        with db.get_conn() as conn, conn.cursor() as cur:
            cur.execute('select id from investments where user_id=%s', (uid,))
            investment_id = cur.fetchone()['id']
            investments._insert_investment_lot(cur, uid, investment_id, 25, date(2026, 9, 1))
            conn.commit()

    complete = db.list_investments(user_id)
    assert len(complete) == 1
    assert len(complete[0]['lots']) == 1
    assert complete == db.list_investments(user_id, include_lots=True)

    def unexpected_lots(*args):
        raise AssertionError('a consulta leve não pode ler lotes no banco')

    monkeypatch.setattr(investments, '_fetch_lots_for_investments', unexpected_lots)
    lightweight = db.list_investments(user_id, include_lots=False)
    assert lightweight == [{key: value for key, value in row.items() if key != 'lots'} for row in complete]
