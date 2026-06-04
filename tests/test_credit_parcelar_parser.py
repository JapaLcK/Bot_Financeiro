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


def _group_summary(user_id: int, group_id):
    """Retorna (qtd, total, valor_parcela, nota) do parcelamento `group_id`."""
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select count(*) as n, sum(valor) as total, "
                "min(valor) as vmin, max(nota) as nota "
                "from credit_transactions "
                "where user_id = %s and group_id = %s",
                (user_id, group_id),
            )
            row = cur.fetchone()
            return (
                int(row["n"]),
                float(row["total"]),
                float(row["vmin"]),
                row["nota"],
            )


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


# ── "Nx de Y" = valor da PARCELA (juros já embutido) → total = parcela × N ────

def test_parcelei_12x_de_79_90_usa_valor_parcela(user_id):
    """'parcelei 12x de 79,90' → 12 parcelas, total 958,80 (não 12÷algo).

    O '12x de 79,90' é o que a maquininha/app mostra com o juros embutido; o
    bot multiplica pelo número de parcelas em vez de exigir a conta na mão.
    """
    _seed_card(user_id)
    msg = handle(user_id, "parcelei 12x de 79,90 celular")
    assert msg is not None
    gid, n = _last_group_id_count(user_id)
    assert n == 12, f"esperava 12 parcelas, criou {n}"
    _, total, vparc, nota = _group_summary(user_id, gid)
    assert total == 958.80, f"esperava total 958,80, veio {total}"
    assert vparc == 79.90, f"esperava parcela 79,90, veio {vparc}"
    # o "de" e o valor da parcela não podem vazar na descrição
    assert nota == "celular", f"descrição suja: {nota!r}"


def test_parcelei_em_12x_de_79_90(user_id):
    """Variação com 'em': 'parcelei em 12x de 79,90' → mesmo resultado."""
    _seed_card(user_id)
    msg = handle(user_id, "parcelei em 12x de 79,90 celular")
    assert msg is not None
    gid, n = _last_group_id_count(user_id)
    assert n == 12
    _, total, vparc, nota = _group_summary(user_id, gid)
    assert total == 958.80
    assert vparc == 79.90
    assert nota == "celular"


def test_parcelei_3_vezes_de_100(user_id):
    """'3 vezes de 100' (coloquial) → total 300."""
    _seed_card(user_id)
    msg = handle(user_id, "parcelei 3 vezes de 100 geladeira")
    assert msg is not None
    gid, n = _last_group_id_count(user_id)
    assert n == 3
    _, total, vparc, _ = _group_summary(user_id, gid)
    assert total == 300.00
    assert vparc == 100.00


def test_parcelar_total_em_12x_continua_dividindo(user_id):
    """Regressão: 'parcelar 958,80 em 12x' (TOTAL, sem 'de') ainda divide."""
    _seed_card(user_id)
    msg = handle(user_id, "parcelar 958,80 em 12x celular")
    assert msg is not None
    gid, n = _last_group_id_count(user_id)
    assert n == 12
    _, total, vparc, _ = _group_summary(user_id, gid)
    assert total == 958.80, f"total deveria ser o informado 958,80, veio {total}"
    assert vparc == 79.90, f"958,80÷12 = 79,90, veio {vparc}"
