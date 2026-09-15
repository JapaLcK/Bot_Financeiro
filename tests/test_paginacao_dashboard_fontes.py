"""Paginação all precisa desempatar IDs iguais de duas tabelas distintas."""
import asyncio

import db
import frontend.finance_bot_websocket_custom as dashboard
from test_tipo_legado_no_dashboard import _hoje_as


def test_paginas_com_credito_e_lancamento_de_mesmo_id(pro_user_id):
    agora = _hoje_as(9)
    card = db.create_card(pro_user_id, "Cartão teste", 28, 5)
    db.add_credit_purchase_installments(user_id=pro_user_id, card_id=card,
        valor_total=10, categoria="outros", nota="semente", purchased_at=agora.date().replace(day=5), installments=1)
    bill = db.list_open_bills(pro_user_id)[0]["id"]
    ids = [8_000_000_000_000 + pro_user_id * 100 + i for i in range(30)]
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("delete from credit_transactions where user_id=%s", (pro_user_id,))
        for ident in ids:
            cur.execute("insert into launches(id,user_id,tipo,valor,categoria,criado_em) values(%s,%s,'despesa',10,'outros',%s)",
                        (ident, pro_user_id, agora))
            cur.execute("insert into credit_transactions(id,user_id,card_id,bill_id,valor,purchased_at,created_at) values(%s,%s,%s,%s,10,%s,%s)",
                        (ident, pro_user_id, card, bill, agora.date(), agora))
        conn.commit()
    vistos = []
    # Limite ímpar corta um par de fontes em cada fronteira de página.
    for page in range(1, 10):
        data = asyncio.run(dashboard.get_financial_data(pro_user_id,
            year=agora.year, month=agora.month, filter_type="all", limit=7, page=page))
        assert data["launches_pagination"]["total"] == 60
        vistos.extend((int(r["id"]), r["tipo"]) for r in data["recent_launches"])
    esperado = [(ident, tipo) for ident in ids for tipo in ("despesa", "credito")]
    assert vistos == esperado
    assert len(set(vistos)) == 60
