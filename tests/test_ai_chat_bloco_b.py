"""
Cobre as 3 read tools do Sprint 2 Bloco B (cartão):
- get_total_debt: soma agregada das faturas em aberto
- list_installments: parcelamentos ativos
- forecast_next_bill: projeção da próxima fatura

São tools de leitura, sem efeito colateral pra além de criar bills vazias
quando `forecast_next_bill` é chamada (side effect existente do helper
`get_next_bill_summary`).
"""
from datetime import date

import db
from core.services.ai_chat.tools.cards import (
    _forecast_next_bill,
    _get_total_debt,
    _list_installments,
)


# ─── get_total_debt ─────────────────────────────────────────────────────────


def test_total_debt_zero_sem_cartao(user_id):
    out = _get_total_debt(user_id, {})
    assert out == {"total_debt": 0, "overdue_debt": 0, "bills": [], "count": 0}


def test_total_debt_soma_faturas_em_aberto(user_id):
    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    db.add_credit_purchase(user_id, card_id, 100, "lazer", "show", date.today())
    db.add_credit_purchase(user_id, card_id, 50, "alimentação", "ifood", date.today())

    out = _get_total_debt(user_id, {})
    assert out["total_debt"] == 150.0
    assert out["overdue_debt"] == 0.0
    assert out["count"] == 1  # uma fatura aberta
    assert out["bills"][0]["card_name"] == "Nubank"
    assert out["bills"][0]["remaining"] == 150.0
    assert out["bills"][0]["is_overdue"] is False


def test_total_debt_agrega_dois_cartoes(pro_user_id):
    nubank = db.create_card(pro_user_id, "Nubank", closing_day=10, due_day=17)
    itau = db.create_card(pro_user_id, "Itaú", closing_day=15, due_day=22)
    db.add_credit_purchase(pro_user_id, nubank, 200, "x", "x", date.today())
    db.add_credit_purchase(pro_user_id, itau, 80, "y", "y", date.today())

    out = _get_total_debt(pro_user_id, {})
    assert out["total_debt"] == 280.0
    assert out["count"] == 2


def test_total_debt_inclui_fatura_closed_com_saldo_como_overdue(user_id):
    """
    Bill `closed` (fatura já fechou) com saldo residual é débito atrasado.
    Deve aparecer em `get_total_debt` com `is_overdue=True` e contribuir
    pro `overdue_debt`. A reconciliação preguiçosa de list_open_bills NÃO
    deve reabrir essa bill (segue closed).
    """
    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    db.add_credit_purchase(user_id, card_id, 100, "lazer", "show", date.today())

    # Fecha a bill manualmente com saldo (simula fatura passada não paga).
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update credit_bills set status='closed', paid_amount=20 "
                "where user_id=%s and card_id=%s",
                (user_id, card_id),
            )
        conn.commit()

    out = _get_total_debt(user_id, {})
    assert out["count"] == 1
    assert out["total_debt"] == 80.0
    assert out["overdue_debt"] == 80.0
    assert out["bills"][0]["is_overdue"] is True

    # Confere que list_open_bills NÃO reabriu (closed continua closed).
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select status from credit_bills where user_id=%s and card_id=%s",
                (user_id, card_id),
            )
            row = cur.fetchone()
    assert row["status"] == "closed", (
        "reconciliação preguiçosa não deve reabrir bill closed (B1)"
    )


# ─── list_installments ──────────────────────────────────────────────────────


def test_list_installments_vazio_sem_parcelamentos(user_id):
    out = _list_installments(user_id, {})
    assert out == {"groups": [], "count": 0}


def test_list_installments_mostra_grupo_parcelado(user_id):
    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    db.add_credit_purchase_installments(
        user_id, card_id, 300, "lazer", "geladeira", date.today(), installments=3
    )

    out = _list_installments(user_id, {})
    assert out["count"] == 1
    g = out["groups"][0]
    assert g["card_name"] == "Nubank"
    assert g["nota"] == "geladeira"
    assert g["n_total"] == 3
    assert g["n_pending"] == 3
    assert g["total"] == 300.0
    assert g["total_pending"] == 300.0
    # 3 parcelas pendentes → 3 datas de vencimento ordenadas
    assert len(g["upcoming_due_dates"]) == 3
    assert g["upcoming_due_dates"] == sorted(g["upcoming_due_dates"])
    # Cada data é ISO yyyy-mm-dd
    for s in g["upcoming_due_dates"]:
        date.fromisoformat(s)


def test_list_installments_only_pending_false_inclui_quitado(user_id):
    """Com only_pending=False, deveriam aparecer parcelamentos sem bills abertas.

    Como `add_credit_purchase_installments` cria as bills com status='open' por
    padrão, e nesse teste a gente não fecha nenhuma, o filtro tem efeito nulo
    aqui. Mantém o teste pra cobrir a flag não-default.
    """
    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    db.add_credit_purchase_installments(
        user_id, card_id, 100, "x", "tv", date.today(), installments=2
    )

    out_all = _list_installments(user_id, {"only_pending": False})
    assert out_all["count"] == 1


# ─── forecast_next_bill ─────────────────────────────────────────────────────


def test_forecast_next_bill_zero_sem_cartao(user_id):
    out = _forecast_next_bill(user_id, {})
    assert out == {"total": 0, "cards": [], "count": 0}


def test_forecast_next_bill_pega_parcelamento_futuro(user_id):
    """Parcela de N/3 cai na fatura DO MÊS SEGUINTE — forecast deve enxergar."""
    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    db.add_credit_purchase_installments(
        user_id, card_id, 300, "lazer", "tv", date.today(), installments=3
    )

    out = _forecast_next_bill(user_id, {})
    assert out["count"] == 1
    assert out["cards"][0]["card_name"] == "Nubank"
    # A próxima fatura tem 1 parcela de 100 (300/3).
    assert out["total"] == 100.0


def test_forecast_next_bill_filtra_por_card_name(pro_user_id):
    db.create_card(pro_user_id, "Nubank", closing_day=10, due_day=17)
    itau = db.create_card(pro_user_id, "Itaú", closing_day=15, due_day=22)
    db.add_credit_purchase_installments(
        pro_user_id, itau, 200, "x", "y", date.today(), installments=2
    )

    out = _forecast_next_bill(pro_user_id, {"card_name": "Itaú"})
    assert out["count"] == 1
    assert out["cards"][0]["card_name"] == "Itaú"


def test_forecast_next_bill_card_inexistente_retorna_erro(user_id):
    out = _forecast_next_bill(user_id, {"card_name": "Bradesco"})
    assert "error" in out


# ─── Bug fix: bill paga reaberta quando ganha parcela nova ──────────────────


def test_parcelamento_em_bill_paga_reabre_pra_open(user_id):
    """
    Bug visto em prod: Lucas pagou fatura X, depois parcelou algo cuja
    parcela caía em X. A bill X virou `total > paid_amount` mas status
    continuou `paid` — `list_open_bills` ignorou. Get_total_debt
    sub-reportava a dívida e a fatura sumia da listagem.

    Fix: `add_credit_purchase_installments` agora reabre bill paid/closed
    quando a parcela nova faz o total ficar > paid_amount.
    """
    from datetime import date as _date
    import db
    today = _date.today()

    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    db.set_default_card(user_id, card_id)

    # 1. Compra que cai na fatura corrente, paga a fatura inteira.
    _, _, bill_id = db.add_credit_purchase(
        user_id, card_id, 100, "outros", "compra", today,
    )
    db.add_launch_and_update_balance(user_id, "receita", 200, None, "seed")
    db.pay_bill_amount(user_id, card_id, "Nubank", 100.0)

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select status, total, paid_amount from credit_bills where id=%s",
                (bill_id,),
            )
            row = cur.fetchone()
    assert row["status"] == "paid"
    assert float(row["total"]) == 100.0
    assert float(row["paid_amount"]) == 100.0

    # 2. Parcelamento 4x — a 1ª parcela cai exatamente na bill que acabou
    # de ser paga. Outras 3 vão pra bills futuras.
    db.add_credit_purchase_installments(
        user_id, card_id, 400, "outros", "tv", today, installments=4
    )

    # A bill paga deve ter sido reaberta: total > paid_amount.
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select status, total, paid_amount, paid_at from credit_bills where id=%s",
                (bill_id,),
            )
            row = cur.fetchone()
    assert row["status"] == "open", (
        f"bill com saldo devedor ficou status={row['status']} — bug!"
    )
    assert row["paid_at"] is None
    assert float(row["total"]) == 200.0  # 100 antiga + 100 parcela
    assert float(row["paid_amount"]) == 100.0  # paid permanece

    # E aparece em list_open_bills com remaining=100.
    out = _get_total_debt(user_id, {})
    bill_ids = {b["bill_id"] for b in out["bills"]}
    assert bill_id in bill_ids, "bill reaberta deve aparecer em get_total_debt"


def test_list_open_bills_reconcilia_zumbi_com_saldo(user_id):
    """
    Reconciliação preguiçosa: bill que ficou status='paid' mas tem total
    > paid_amount (por bug anterior, edição manual, etc) deve ser
    REABERTA quando alguém chama list_open_bills.

    Cobre casos onde o caller que escreveu na bill esqueceu de atualizar
    o status — sem isso, dados antigos ficam "presos" mostrando dívida
    menor do que é.
    """
    import db
    today = date.today()

    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    db.set_default_card(user_id, card_id)
    db.add_launch_and_update_balance(user_id, "receita", 500, None, "seed")

    _, _, bill_id = db.add_credit_purchase(
        user_id, card_id, 100, "outros", "compra", today,
    )
    db.pay_bill_amount(user_id, card_id, "Nubank", 100.0)

    # Força estado inconsistente: total cresce sem reabrir (simula bug
    # passado que possa ter deixado bills nesse estado em prod).
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update credit_bills set total = total + 50 where id=%s",
                (bill_id,),
            )
        conn.commit()

    # Pré-condição: bill está paid com total > paid
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select status, total, paid_amount from credit_bills where id=%s", (bill_id,))
            row = cur.fetchone()
    assert row["status"] == "paid"
    assert float(row["total"]) == 150.0
    assert float(row["paid_amount"]) == 100.0

    # list_open_bills deve reconciliar e listar a bill
    rows = db.list_open_bills(user_id)
    bill_ids = {r["id"] for r in rows}
    assert bill_id in bill_ids

    # E o status no DB ficou 'open' depois da reconciliação
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select status from credit_bills where id=%s", (bill_id,))
            row = cur.fetchone()
    assert row["status"] == "open"


def test_rebuild_bill_totals_recalcula_e_reabre(user_id):
    """Helper de reconciliação forte — usado pra reparar DB inconsistente
    em prod. Recalcula `total` somando credit_transactions reais e reabre
    bills com saldo devedor.

    Cenário: bill com total fora de sincronia com a soma das transações
    + status='paid' indevido.
    """
    import db

    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    db.set_default_card(user_id, card_id)
    db.add_launch_and_update_balance(user_id, "receita", 500, None, "seed")

    _, _, bill_id = db.add_credit_purchase(
        user_id, card_id, 100, "outros", "compra", date.today(),
    )

    # Bagunça o estado: total fica errado E status virou 'paid'.
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update credit_bills set total = 0, status='paid', paid_at=now() "
                "where id=%s",
                (bill_id,),
            )
        conn.commit()

    # Pré-condição: bill bagunçada.
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select total, status from credit_bills where id=%s", (bill_id,))
            row = cur.fetchone()
    assert float(row["total"]) == 0.0
    assert row["status"] == "paid"

    # Reconcilia.
    out = db.rebuild_bill_totals(user_id)
    assert out["totals_updated"] >= 1
    assert out["reopened"] >= 1

    # Pós: total recalculado, status reaberto.
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select total, status, paid_at from credit_bills where id=%s", (bill_id,))
            row = cur.fetchone()
    assert float(row["total"]) == 100.0
    assert row["status"] == "open"
    assert row["paid_at"] is None


def test_rebuild_bill_totals_idempotente(user_id):
    """Rodar 2x não muda nada na 2ª passada."""
    import db

    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    db.set_default_card(user_id, card_id)
    db.add_launch_and_update_balance(user_id, "receita", 500, None, "seed")
    db.add_credit_purchase(user_id, card_id, 100, "outros", "compra", date.today())

    db.rebuild_bill_totals(user_id)
    out2 = db.rebuild_bill_totals(user_id)
    assert out2["totals_updated"] == 0
    assert out2["reopened"] == 0
    assert out2["paid_clamped"] == 0
    assert out2["refunded"] == 0


def test_rebuild_clampa_overpayment_sem_refund(user_id):
    """B: paid > total é clampado pra paid=total, sem criar launch.

    Cenário: estado herdado de bug anterior (undo_installment_group antigo
    que não revertia paid_amount). Bill #250 do print real do Lucas era
    assim: total=100, paid=200, status='paid' → sumia da listagem.
    """
    import db
    today = date.today()

    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    db.set_default_card(user_id, card_id)
    db.add_launch_and_update_balance(user_id, "receita", 500, None, "seed")

    _, _, bill_id = db.add_credit_purchase(
        user_id, card_id, 100, "outros", "compra", today,
    )

    # Bagunça: força paid > total (simula estado herdado de bug).
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update credit_bills set paid_amount=200, status='paid', paid_at=now() "
                "where id=%s",
                (bill_id,),
            )
        conn.commit()

    # Saldo da conta antes do rebuild
    saldo_antes = db.get_balance(user_id)

    out = db.rebuild_bill_totals(user_id)
    assert out["paid_clamped"] == 1
    assert out["refunded"] == 0  # sem refund, só clamp

    # Bill: paid clampado pra total. Status mantém (paid >= total)
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select total, paid_amount, status from credit_bills where id=%s", (bill_id,))
            row = cur.fetchone()
    assert float(row["total"]) == 100.0
    assert float(row["paid_amount"]) == 100.0

    # Saldo da conta NÃO foi tocado (sem estorno)
    assert db.get_balance(user_id) == saldo_antes


def test_rebuild_com_refund_cria_launch_de_estorno(user_id):
    """B + estorno: clampa E devolve a diferença pra conta corrente."""
    import db
    today = date.today()

    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    db.set_default_card(user_id, card_id)
    db.add_launch_and_update_balance(user_id, "receita", 500, None, "seed")

    _, _, bill_id = db.add_credit_purchase(
        user_id, card_id, 100, "outros", "compra", today,
    )

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update credit_bills set paid_amount=200, status='paid', paid_at=now() "
                "where id=%s",
                (bill_id,),
            )
        conn.commit()

    saldo_antes = db.get_balance(user_id)
    out = db.rebuild_bill_totals(user_id, refund_overpayments=True)

    assert out["paid_clamped"] == 1
    assert out["refunded"] == 100.0  # 200 paid - 100 total
    # Saldo subiu pelo estorno
    assert float(db.get_balance(user_id)) - float(saldo_antes) == 100.0


def test_undo_installment_mantem_paid_bills_intactas(user_id):
    """Option B (2026-05-14): apagar parcelamento com parcelas pagas
    NÃO estorna dinheiro. Tx em fatura paga viram órfãs (group_id=null,
    nota+sufixo), fatura paga continua intacta. Tx em open bill são
    deletadas. Sem refund launch."""
    import db
    today = date.today()

    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    db.set_default_card(user_id, card_id)
    db.add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")

    out, _vtotal = db.add_credit_purchase_installments(
        user_id, card_id, 500, "outros", "tv", today, installments=5
    )
    group_id = out["group_id"]

    # Paga a 1ª fatura (R$ 100) inteira
    open_bills = db.list_open_bills(user_id)
    bill_paga = open_bills[0]
    db.pay_bill_amount(user_id, card_id, "Nubank", 100.0, bill_id=bill_paga["id"])

    saldo_antes = float(db.get_balance(user_id))

    result = db.undo_installment_group(user_id, group_id)
    assert result is not None
    assert result["refunded"] == 0.0  # nunca estorna agora
    assert result["refund_launch_id"] is None
    assert result["orphaned_count"] == 1  # 1 tx em paid bill
    assert result["removed_count"] == 4   # 4 tx em open bills

    # Bill paga: intacta (total e paid preservados)
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select paid_amount, total, status from credit_bills where id=%s", (bill_paga["id"],))
            row = cur.fetchone()
    assert float(row["total"]) == 100.0
    assert float(row["paid_amount"]) == 100.0
    assert row["status"] == "paid"

    # Tx órfã: group_id null, nota com sufixo
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select group_id, nota from credit_transactions "
                "where user_id=%s and bill_id=%s and is_refund=false",
                (user_id, bill_paga["id"]),
            )
            tx_orphan = cur.fetchone()
    assert tx_orphan["group_id"] is None
    assert "[Parcelamento removido" in (tx_orphan["nota"] or "")

    # Saldo da conta NÃO mudou (sem estorno)
    assert float(db.get_balance(user_id)) == saldo_antes


def test_undo_installment_sem_pagamento_nao_estorna(user_id):
    """A: undo de parcelamento NÃO pago — sem estorno, sem launch."""
    import db
    today = date.today()

    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    db.set_default_card(user_id, card_id)
    db.add_launch_and_update_balance(user_id, "receita", 500, None, "seed")

    out, _ = db.add_credit_purchase_installments(
        user_id, card_id, 500, "outros", "tv", today, installments=5
    )

    saldo_antes = float(db.get_balance(user_id))
    result = db.undo_installment_group(user_id, out["group_id"])

    assert result["refunded"] == 0
    assert result["refund_launch_id"] is None
    # Saldo intacto
    assert float(db.get_balance(user_id)) == saldo_antes


def test_forecast_next_bill_pega_open_mais_proxima_nao_mais_distante(user_id):
    """
    Bug visto em prod: parcelei 500 em 5 → forecast_next_bill retornou
    bill da PARCELA i=4 (mais distante) em vez da i=0 (próxima de hoje).

    Fix: forecast lê list_open_bills (ordenada por period_end asc) e
    pega a 1ª de cada cartão — sempre a mais próxima do today.
    """
    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    db.set_default_card(user_id, card_id)
    db.add_credit_purchase_installments(
        user_id, card_id, 500, "outros", "tv", date.today(), installments=5
    )

    out = _forecast_next_bill(user_id, {})
    assert out["count"] == 1  # 1 cartão
    assert out["total"] == 100.0  # 1 parcela de 100, não 100 da última nem 500 do total

    # A bill retornada deve ser a mais cedo (period_end mais próximo de hoje).
    open_bills = db.list_open_bills(user_id)
    expected_first_id = open_bills[0]["id"]
    assert out["cards"][0]["bill_id"] == expected_first_id
