"""
Cobre o parser de parcelamento no handler tradicional. Caso real do Lucas:
'parcelei 600 em 3' (sem 'x') caía em 1x porque a regex exigia 'x' literal.
"""
import db
from core.handlers.credit import handle


def _seed_card(user_id: int, name: str = "Nubank") -> int:
    card_id = db.create_card(user_id, name, closing_day=10, due_day=17)
    db.set_default_card(user_id, card_id)
    return card_id


def _last_group_id_count(user_id: int):
    """Retorna (group_id, qtd_parcelas) do parcelamento mais recente do user."""
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select group_id, count(*) as n "
                "from credit_transactions "
                "where user_id = %s and group_id is not null "
                "group by group_id "
                "order by max(created_at) desc "
                "limit 1",
                (user_id,),
            )
            row = cur.fetchone()
            return (row["group_id"], int(row["n"])) if row else (None, 0)


def test_parcelei_600_em_3_sem_x(user_id):
    """Bug do Lucas: 'parcelei 600 em 3' deve criar 3 parcelas (não 1)."""
    _seed_card(user_id)
    msg = handle(user_id, "parcelei 600 em 3 celular")
    assert msg is not None
    _, n = _last_group_id_count(user_id)
    assert n == 3, f"esperava 3 parcelas, criou {n}"


def test_parcelei_600_em_3x_continua_funcionando(user_id):
    """Não regredir o formato antigo com 'x'."""
    _seed_card(user_id)
    msg = handle(user_id, "parcelei 600 em 3x celular")
    assert msg is not None
    _, n = _last_group_id_count(user_id)
    assert n == 3


def test_parcelei_300_em_3_vezes(user_id):
    """'em 3 vezes' (português coloquial)."""
    _seed_card(user_id)
    msg = handle(user_id, "parcelei 300 em 3 vezes geladeira")
    assert msg is not None
    _, n = _last_group_id_count(user_id)
    assert n == 3


def test_parcelei_300_em_5_parcelas(user_id):
    """'em N parcelas' explícito."""
    _seed_card(user_id)
    msg = handle(user_id, "parcelei 500 em 5 parcelas tv")
    assert msg is not None
    _, n = _last_group_id_count(user_id)
    assert n == 5


def test_parcelar_500_3x_sem_em(user_id):
    """Fallback do regex antigo: '500 3x' (sem 'em')."""
    _seed_card(user_id)
    msg = handle(user_id, "parcelar 500 3x sofa")
    assert msg is not None
    _, n = _last_group_id_count(user_id)
    assert n == 3
