"""
Cobre as tools Tier 2 da E2 (analítics que respondem perguntas como
'onde gastei mais?', 'gastei mais em abril ou maio?', etc).
"""
from datetime import date, datetime, timedelta
from unittest.mock import patch

import db
from core.services.ai_chat.tools import launches as tools_launches
from core.services.ai_chat.tools.launches import (
    _compare_periods,
    _forecast_month_end,
    _get_largest_expenses,
    _get_spending_trend,
    _get_top_categories,
)


# ─── get_top_categories ─────────────────────────────────────────────────────

def test_top_categories_agrega_despesas_e_credito(user_id):
    """Despesa em launches + compra em cartão na mesma categoria somam juntos."""
    # Despesa de R$ 50 em alimentação
    db.add_launch_and_update_balance(
        user_id, "despesa", 50, "mercado", "compra", categoria="alimentação",
    )

    # Compra no cartão de R$ 80 em alimentação
    card_id = db.create_card(user_id, "Nubank", closing_day=31, due_day=17)
    db.set_default_card(user_id, card_id)
    db.add_credit_purchase(
        user_id, card_id, 80, "alimentação", "ifood", date.today(),
    )

    result = _get_top_categories(user_id, {})
    cats = {c["categoria"]: c["total"] for c in result["categories"]}
    assert cats["alimentação"] == 130.0


def test_top_categories_exclui_movimentacao_interna(user_id):
    """Aportes/resgates de investimento NÃO contam como gasto."""
    # Despesa real
    db.add_launch_and_update_balance(
        user_id, "despesa", 100, "uber", "uber", categoria="transporte",
    )
    # Aporte de investimento (interno)
    db.add_launch_and_update_balance(
        user_id, "despesa", 500, "aporte", "aporte",
        categoria="investimento_aporte", is_internal_movement=True,
    )

    result = _get_top_categories(user_id, {})
    cats = {c["categoria"] for c in result["categories"]}
    assert "transporte" in cats
    assert "investimento_aporte" not in cats


def test_top_categories_ordena_por_total_desc(user_id):
    db.add_launch_and_update_balance(user_id, "despesa", 10, "x", "x", categoria="lazer")
    db.add_launch_and_update_balance(user_id, "despesa", 50, "y", "y", categoria="alimentação")
    db.add_launch_and_update_balance(user_id, "despesa", 30, "z", "z", categoria="transporte")

    result = _get_top_categories(user_id, {})
    cats_in_order = [c["categoria"] for c in result["categories"]]
    assert cats_in_order[:3] == ["alimentação", "transporte", "lazer"]


def test_top_categories_respeita_limit(user_id):
    for i, cat in enumerate(["a", "b", "c", "d", "e", "f", "g"]):
        db.add_launch_and_update_balance(user_id, "despesa", 10 + i, cat, cat, categoria=cat)

    result = _get_top_categories(user_id, {"limit": 3})
    assert len(result["categories"]) == 3


def test_top_categories_filtra_por_periodo(pro_user_id):
    user_id = pro_user_id  # meses inteiros levam o corte do plano; o Essencial não veria maio
    """Despesa fora do range não aparece."""
    db.add_launch_and_update_balance(
        user_id, "despesa", 100, "antiga", "antiga",
        categoria="lazer",
        criado_em=datetime(2026, 3, 15),  # março
    )
    db.add_launch_and_update_balance(
        user_id, "despesa", 50, "recente", "recente",
        categoria="alimentação",
        criado_em=datetime(2026, 5, 5),  # maio
    )

    # Filtra abril em diante — só a recente
    result = _get_top_categories(user_id, {"start_date": "2026-04-01", "end_date": "2026-12-31"})
    cats = {c["categoria"] for c in result["categories"]}
    assert "alimentação" in cats
    assert "lazer" not in cats


def test_top_categories_end_anterior_a_start(user_id):
    result = _get_top_categories(user_id, {"start_date": "2026-05-01", "end_date": "2026-04-01"})
    assert "error" in result


def test_top_categories_vazio_quando_sem_gastos(user_id):
    result = _get_top_categories(user_id, {})
    assert result["categories"] == []
    assert result["count"] == 0


# ─── get_largest_expenses ───────────────────────────────────────────────────

def test_largest_expenses_retorna_o_maior_individual(user_id):
    """O caso real do Lucas: tem R$ 150, R$ 22, R$ 349 — maior é o de 349."""
    db.add_launch_and_update_balance(user_id, "despesa", 150, "laranja", "Mandei 150 pra Laranja", categoria="outros")
    db.add_launch_and_update_balance(user_id, "despesa", 22.46, "x", "x", categoria="outros")
    db.add_launch_and_update_balance(user_id, "despesa", 349, "stanley", "stanley presente", categoria="lazer")

    result = _get_largest_expenses(user_id, {"limit": 1})
    assert len(result["expenses"]) == 1
    top = result["expenses"][0]
    assert top["valor"] == 349.0
    assert top["categoria"] == "lazer"


def test_largest_expenses_ordena_desc(user_id):
    db.add_launch_and_update_balance(user_id, "despesa", 10, "a", "a", categoria="outros")
    db.add_launch_and_update_balance(user_id, "despesa", 80, "b", "b", categoria="outros")
    db.add_launch_and_update_balance(user_id, "despesa", 50, "c", "c", categoria="outros")

    result = _get_largest_expenses(user_id, {})
    values = [e["valor"] for e in result["expenses"]]
    assert values == [80.0, 50.0, 10.0]


def test_largest_expenses_inclui_credito(user_id):
    """Compra no cartão deve aparecer junto com despesas normais."""
    card_id = db.create_card(user_id, "Nubank", closing_day=31, due_day=17)
    db.set_default_card(user_id, card_id)
    db.add_launch_and_update_balance(user_id, "despesa", 50, "x", "x", categoria="outros")
    db.add_credit_purchase(user_id, card_id, 200, "lazer", "show", date.today())

    result = _get_largest_expenses(user_id, {"limit": 2})
    fontes = {e["fonte"] for e in result["expenses"]}
    assert "credito" in fontes
    assert "launches" in fontes
    assert result["expenses"][0]["valor"] == 200.0
    assert result["expenses"][0]["fonte"] == "credito"


def test_largest_expenses_exclui_movimentacao_interna_e_refund(user_id):
    """Aporte de investimento e reembolso de cartão não contam."""
    db.add_launch_and_update_balance(
        user_id, "despesa", 1000, "aporte", "aporte",
        categoria="investimento_aporte", is_internal_movement=True,
    )
    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    db.set_default_card(user_id, card_id)
    db.add_credit_refund(
        user_id=user_id, card_id=card_id, valor=500,
        categoria="lazer", nota="estornado", purchased_at=date.today(),
    )
    db.add_launch_and_update_balance(user_id, "despesa", 50, "real", "real", categoria="outros")

    result = _get_largest_expenses(user_id, {})
    assert len(result["expenses"]) == 1
    assert result["expenses"][0]["valor"] == 50.0


def test_largest_expenses_respeita_limit(user_id):
    for i in range(7):
        db.add_launch_and_update_balance(
            user_id, "despesa", 10 + i, f"d{i}", f"d{i}", categoria="outros",
        )
    result = _get_largest_expenses(user_id, {"limit": 3})
    assert len(result["expenses"]) == 3


def test_largest_expenses_empate_de_valor_tem_ordem_estavel(user_id):
    """Empate em R$ 30 (lançamentos e cartão, dois no mesmo dia) sai pela data mais recente,
    depois `fonte` e `id` — antes saía em ordem que variava entre duas chamadas iguais, e no
    `limit` o empate decidia QUAL gasto entrava (flake do CI em test_ia_resumo_do_mes)."""
    d = date.today() - timedelta(days=10)
    card_id = db.create_card(user_id, "Nubank", closing_day=31, due_day=17)

    def lanc(alvo, dia):  # inseridos fora da ordem esperada: o scan cru não acerta por acaso
        db.add_launch_and_update_balance(user_id, "despesa", 30, alvo, None, categoria="outros",
                                         criado_em=datetime.combine(dia, datetime.min.time().replace(hour=12)))

    lanc("velho", d - timedelta(days=2))
    lanc("novo_a", d)
    lanc("novo_b", d)
    db.add_credit_purchase(user_id, card_id, 30, "outros", "cartao", d - timedelta(days=1))
    db.add_launch_and_update_balance(user_id, "despesa", 100, "topo", None, categoria="outros",
                                     criado_em=datetime.combine(d, datetime.min.time().replace(hour=12)))
    periodo = {"start_date": (d - timedelta(days=3)).isoformat(), "end_date": (d + timedelta(days=1)).isoformat()}

    todos = [e["descricao"] for e in _get_largest_expenses(user_id, {**periodo, "limit": 20})["expenses"]]
    assert todos == ["topo", "novo_a", "novo_b", "cartao", "velho"]
    for n in (2, 3, 4):  # o empate na borda do `limit` (antes do join) escolhe o mesmo prefixo
        assert [e["descricao"] for e in _get_largest_expenses(user_id, {**periodo, "limit": n})["expenses"]] == todos[:n]


def test_largest_expenses_end_anterior_a_start(user_id):
    result = _get_largest_expenses(user_id, {"start_date": "2026-05-01", "end_date": "2026-04-01"})
    assert "error" in result


def test_largest_expenses_vazio_sem_gastos(user_id):
    result = _get_largest_expenses(user_id, {})
    assert result["expenses"] == []
    assert result["count"] == 0


def _of_tx(uid, launch_id, nome="Itau", ident="x"):
    """Conexão + conta + transação OF de `uid` apontando para `launch_id`."""
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "insert into open_finance_connections (user_id, provider, provider_item_id, status,"
            " institution_id, institution_name) values (%s,'pluggy',%s,'UPDATED','i',%s) returning id",
            (uid, ident, nome),
        )
        c = cur.fetchone()["id"]
        cur.execute(
            "insert into open_finance_accounts (connection_id, provider_account_id, name, type)"
            " values (%s,'a','cc','BANK') returning id", (c,))
        a = cur.fetchone()["id"]
        cur.execute(
            "insert into open_finance_transactions (account_id, provider_transaction_id, description,"
            " amount, transaction_date, imported_launch_id) values (%s,'t','d',-1,current_date,%s)",
            (a, launch_id),
        )


def _lancamento(uid, valor=99):
    db.add_launch_and_update_balance(uid, "despesa", valor, "x", "x", categoria="outros")
    return db.get_largest_expenses(uid, date.today().replace(day=1), date.today(), limit=1)


def _launch_id(uid):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select max(id) as i from launches where user_id=%s", (uid,))
        return cur.fetchone()["i"]


def test_largest_expenses_parcela_e_cartao(pro_user_id):
    card_id = db.create_card(pro_user_id, "Nubank", closing_day=10, due_day=17)
    db.add_credit_purchase_installments(pro_user_id, card_id, 300, "lazer", "tv", date.today(), 3)
    rows = db.get_largest_expenses(pro_user_id, date.today(), date.today(), limit=5)
    assert rows and all(r["instituicao"] == "Nubank" for r in rows)
    assert {r["parcela"] for r in rows} <= {"1/3", "2/3", "3/3"} and rows[0]["parcela"]


def test_largest_expenses_instituicao_open_finance(user_id):
    _lancamento(user_id)
    _of_tx(user_id, _launch_id(user_id), nome="Itau")
    assert _lancamento_rows(user_id)[0]["instituicao"] == "Itau"


def _lancamento_rows(uid):
    return db.get_largest_expenses(uid, date.today().replace(day=1), date.today(), limit=1)


def test_largest_expenses_instituicao_nao_vaza_entre_usuarios(user_id):
    from tests.conftest import _cleanup_user
    import uuid
    outro = int(uuid.uuid4().int % 10_000_000_000)
    db.ensure_user(outro)
    try:
        _lancamento(user_id)
        # conexão do OUTRO usuário apontando para o lançamento de `user_id`
        _of_tx(outro, _launch_id(user_id), nome="BancoDoOutro", ident="b")
        r = _lancamento_rows(user_id)[0]
        assert r["instituicao"] is None
    finally:
        _cleanup_user(outro)


def test_largest_expenses_credito_nao_pega_banco_de_launch_de_mesmo_id(user_id):
    card_id = db.create_card(user_id, "CartaoA", closing_day=10, due_day=17)
    db.add_credit_purchase_installments(user_id, card_id, 300, "lazer", "tv", date.today(), 3)
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select id from credit_transactions where user_id=%s limit 1", (user_id,))
        ct_id = cur.fetchone()["id"]
    _lancamento(user_id, valor=1)
    with db.get_conn() as conn, conn.cursor() as cur:  # launch com o MESMO id da compra
        cur.execute("update launches set id=%s where id=%s", (ct_id, _launch_id(user_id)))
    _of_tx(user_id, ct_id, nome="BancoColide")
    rows = db.get_largest_expenses(user_id, date.today(), date.today(), limit=5)
    cred = [r for r in rows if r["fonte"] == "credito"]
    assert cred and all(r["instituicao"] == "CartaoA" for r in cred)


def test_largest_expenses_a_vista_sem_parcela(user_id):
    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    db.add_credit_purchase_installments(user_id, card_id, 80, "lazer", "jogo", date.today(), 1)
    r = db.get_largest_expenses(user_id, date.today(), date.today(), limit=1)[0]
    assert r["fonte"] == "credito" and r["parcela"] is None


def test_largest_expenses_manual_sem_instituicao_nem_parcela(user_id):
    r = _lancamento(user_id)[0]
    assert r["instituicao"] is None and r["parcela"] is None


def _periodo_livre():
    # "Hoje" = dia 28: nem "dia 1 até hoje" (no dia 1) nem a janela dia 1..15 viram o mês corrente
    # inteiro, o único que lê o cartão pela fatura e solta a parcela. Estes testes medem o texto
    # do período livre (compra pela data), em qualquer dia do mês.
    return patch.object(tools_launches, "today_tz", lambda: date.today().replace(day=28))


def _tool(uid, limit=20):
    with _periodo_livre():
        return _get_largest_expenses(uid, {"start_date": date.today().isoformat(), "limit": limit})["expenses"]


def _parcelado(uid, card, valor, nota, n, cat="lazer"):
    db.add_credit_purchase_installments(uid, card, valor, cat, nota, date.today(), n)


def test_tool_a_limit_nao_corta_parcela_antes_da_juncao(user_id):
    card = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    _parcelado(user_id, card, 300, "tv", 3)
    db.add_launch_and_update_balance(user_id, "despesa", 150, "x", "x", categoria="outros")
    db.add_launch_and_update_balance(user_id, "despesa", 120, "y", "y", categoria="outros")
    es = _tool(user_id, limit=3)
    assert [(e["valor"], e["parcelamento"]) for e in es] == [(300, "compra de R$ 300,00 em 3x"), (150, None), (120, None)]


def test_tool_b_compras_iguais_nao_se_juntam(user_id):
    card = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    _parcelado(user_id, card, 30, "Uber", 3)
    _parcelado(user_id, card, 30, "Uber", 3)
    assert [e["valor"] for e in _tool(user_id)] == [30, 30]


def test_tool_b_duas_sem_nota_nao_se_juntam(user_id):
    card = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    _parcelado(user_id, card, 90, None, 3)
    _parcelado(user_id, card, 60, None, 3)
    assert [e["valor"] for e in _tool(user_id)] == [90, 60]


def test_tool_legado_sem_group_id_nao_agrega(user_id):
    card = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    _parcelado(user_id, card, 80, "x", 1)
    _parcelado(user_id, card, 70, "x", 1)
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("update credit_transactions set group_id = null where user_id=%s", (user_id,))
    assert [e["valor"] for e in _tool(user_id)] == [80, 70]


def test_tool_c_centavos_sem_valor_parcela(user_id):
    card = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    _parcelado(user_id, card, 100, "tv", 3)
    (e,) = _tool(user_id)
    assert (e["valor"], e["parcelamento"]) == (100, "compra de R$ 100,00 em 3x") and "valor_parcela" not in e
    assert "parcela" not in e and "parcelas" not in e  # campos crus não vão à IA


def test_tool_avista_e_manual_ficam_iguais(user_id):
    card = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    _parcelado(user_id, card, 80, "jogo", 1)
    db.add_launch_and_update_balance(user_id, "despesa", 50, "x", "x", categoria="outros")
    es = _tool(user_id)
    assert [(e["valor"], e["parcelamento"]) for e in es] == [(80, None), (50, None)]


def test_chamador_cru_continua_com_parcelas_separadas(user_id):
    card = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    _parcelado(user_id, card, 300, "tv", 3)
    rows = db.get_largest_expenses(user_id, date.today(), date.today(), limit=10)
    assert sorted((r["valor"], r["parcela"]) for r in rows) == [(100, "1/3"), (100, "2/3"), (100, "3/3")]  # empate de valor: ordem livre
    assert all("parcelas" not in r for r in rows)


def test_juntar_parcelas_nao_vaza_entre_usuarios(user_id):
    from tests.conftest import _cleanup_user
    import uuid
    outro = int(uuid.uuid4().int % 10_000_000_000)
    db.ensure_user(outro)
    try:
        c1 = db.create_card(user_id, "Meu", closing_day=10, due_day=17)
        c2 = db.create_card(outro, "DoOutro", closing_day=10, due_day=17)
        _parcelado(user_id, c1, 300, "tv", 3)
        _parcelado(outro, c2, 900, "carro", 3)
        rows = db.get_largest_expenses(user_id, date.today(), date.today(), limit=10, juntar_parcelas=True)
        assert [(r["valor"], r["instituicao"]) for r in rows] == [(300, "Meu")]
    finally:
        _cleanup_user(outro)


def test_juntar_parcelas_credito_nao_pega_banco_de_launch_de_mesmo_id(user_id):
    card = db.create_card(user_id, "CartaoA", closing_day=10, due_day=17)
    _parcelado(user_id, card, 300, "tv", 3)
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select min(id) as i from credit_transactions where user_id=%s", (user_id,))
        ct_id = cur.fetchone()["i"]
    _lancamento(user_id, valor=1)
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("update launches set id=%s where id=%s", (ct_id, _launch_id(user_id)))
    _of_tx(user_id, ct_id, nome="BancoColide")
    rows = db.get_largest_expenses(user_id, date.today(), date.today(), limit=5, juntar_parcelas=True)
    cred = [r for r in rows if r["fonte"] == "credito"]
    assert cred and all(r["instituicao"] == "CartaoA" for r in cred)


def _mes(d, k):
    m = d.month - 1 + k
    return d.replace(year=d.year + m // 12, month=m % 12 + 1, day=15)


def _parcelas_of(uid, card, n=3, grupo="g", valor=200, nota="TV LOJA", total=None, so=None):
    """Formato Open Finance/OFX: 1 linha por parcela, data do mês dela, mesmo group_id."""
    import uuid
    g = uuid.uuid5(uuid.NAMESPACE_OID, grupo) if grupo else None
    with db.get_conn() as conn, conn.cursor() as cur:
        for i in range(n):
            if so is not None and i not in so:
                continue
            d = _mes(date.today(), i)
            bill = db.get_or_create_open_bill(uid, card, d)
            cur.execute(
                "insert into credit_transactions (bill_id,user_id,card_id,valor,categoria,nota,purchased_at,"
                "group_id,installment_no,installments_total,is_refund) values (%s,%s,%s,%s,'lazer',%s,%s,%s,%s,%s,false)",
                (bill, uid, card, valor, nota, d, g, i + 1, total or n))


def _fim_do_mes(d):
    return _mes(d, 1).replace(day=1) - timedelta(days=1)


def _janela(uid, meses):
    # Termina no ÚLTIMO dia do mês: `_mes` fixa o dia 15, e a compra de hoje (dia > 15) ficaria fora.
    rows = db.get_largest_expenses(uid, date.today().replace(day=1), _fim_do_mes(_mes(date.today(), meses - 1)),
                                   limit=20, juntar_parcelas=True)
    return [(r["valor"], r.get("parcelas"), r["parcela"], r["data"]) for r in rows]


def test_juntar_manual_as_n_na_janela(user_id):
    card = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    _parcelado(user_id, card, 600, "tv", 3)
    assert _janela(user_id, 1) == [(600, 3, None, date.today().isoformat())]


def test_juntar_of_janela_de_um_mes_mostra_n_de_N(user_id):
    card = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    _parcelas_of(user_id, card)
    assert _janela(user_id, 1) == [(200, None, "1/3", _mes(date.today(), 0).isoformat())]


def test_juntar_of_janela_cobre_as_n(user_id):
    card = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    _parcelas_of(user_id, card)
    # data = a da 1ª parcela (min): é quando a compra começou a pesar
    assert _janela(user_id, 3) == [(600, 3, None, _mes(date.today(), 0).isoformat())]


def test_juntar_of_dois_de_tres_nao_junta_parcial(user_id):
    card = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    _parcelas_of(user_id, card)
    assert sorted(x[:3] for x in _janela(user_id, 2)) == [(200, None, "1/3"), (200, None, "2/3")]


def test_juntar_orfa_sem_group_id_mostra_n_de_N(user_id):
    card = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    _parcelas_of(user_id, card, grupo=None, so={1})
    assert [x[:3] for x in _janela(user_id, 3)] == [(200, None, "2/3")]


def test_juntar_a_vista_sem_parcela_nem_parcelas(user_id):
    card = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    _parcelas_of(user_id, card, n=1, valor=80)
    assert [x[:3] for x in _janela(user_id, 1)] == [(80, None, None)]


def test_juntar_instituicao_nao_pega_cartao_de_outro_usuario(user_id):
    from tests.conftest import _cleanup_user
    import uuid
    outro = int(uuid.uuid4().int % 10_000_000_000)
    db.ensure_user(outro)
    try:
        meu = db.create_card(user_id, "Meu", closing_day=10, due_day=17)
        # compra de `user_id` com card_id de um cartão do OUTRO usuário
        dele = db.create_card(outro, "CartaoDoOutro", closing_day=10, due_day=17)
        _parcelas_of(user_id, meu, n=1, valor=80)
        with db.get_conn() as conn, conn.cursor() as cur:
            cur.execute("update credit_transactions set card_id=%s where user_id=%s", (dele, user_id))
        for juntar in (False, True):
            r = db.get_largest_expenses(user_id, date.today().replace(day=1), _mes(date.today(), 0),
                                        limit=5, juntar_parcelas=juntar)
            assert [x["instituicao"] for x in r] == [None]
    finally:
        _cleanup_user(outro)


# ─── compare_periods ────────────────────────────────────────────────────────

def test_compare_periods_diff_correto(pro_user_id):
    """Período A com R$ 100 despesa, B com R$ 250 mais R$ 30 no cartão (fatura de maio) → delta +180."""
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 100, "a", "a",
        categoria="outros",
        criado_em=datetime(2026, 4, 10),
    )
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 250, "b", "b",
        categoria="outros",
        criado_em=datetime(2026, 5, 5),
    )
    cartao = db.create_card(pro_user_id, "Nubank", closing_day=10, due_day=17)
    db.add_credit_purchase(pro_user_id, cartao, 30, "outros", "c", date(2026, 5, 5))

    result = _compare_periods(pro_user_id, {
        "period_a_start": "2026-04-01", "period_a_end": "2026-04-30",
        "period_b_start": "2026-05-01", "period_b_end": "2026-05-31",
    })
    assert result["modo"] == "periodos_informados"
    assert (result["anterior"]["inicio"], result["atual"]["fim"]) == ("2026-04-01", "2026-05-31")
    assert result["despesa"]["total_anterior"] == 100.0
    assert result["despesa"]["total_atual"] == 280.0
    assert result["despesa"]["total_delta"] == 180.0
    assert result["base"]["cartao"] == "fatura"  # as duas janelas são meses inteiros: a regra do painel
    assert result["base"]["cartao_lancamentos"] == {"atual": 1, "anterior": 0}


def test_compare_periods_falta_arg(pro_user_id):
    result = _compare_periods(pro_user_id, {"period_a_start": "2026-04-01"})
    assert "error" in result


def test_compare_periods_end_anterior_a_start(pro_user_id):
    result = _compare_periods(pro_user_id, {
        "period_a_start": "2026-05-01", "period_a_end": "2026-04-01",
        "period_b_start": "2026-05-01", "period_b_end": "2026-05-31",
    })
    assert "error" in result


# ─── get_spending_trend ─────────────────────────────────────────────────────

def test_spending_trend_retorna_dados_dos_meses(pro_user_id):
    """Cria gasto em meses diferentes, confere que aparecem na lista."""
    today = date.today()
    # Gasto no mês corrente
    db.add_launch_and_update_balance(pro_user_id, "despesa", 50, "x", "x", categoria="outros")

    result = _get_spending_trend(pro_user_id, {"months": 3})
    assert result["months"] == 3
    assert isinstance(result["data"], list)
    # Pelo menos o mês corrente deve aparecer (com o gasto que criamos)
    current_month = [r for r in result["data"]
                     if r["year"] == today.year and r["month"] == today.month]
    assert len(current_month) == 1
    assert current_month[0]["despesa"] >= 50.0


def test_spending_trend_clamp_limite(pro_user_id):
    result = _get_spending_trend(pro_user_id, {"months": 100})
    assert result["months"] == 12  # teto de 12 (uma regra só: MAX_MESES_PERIODO)
    assert result["limitado_a_12_meses"] and result["pedido"] == {"months": 100}


def test_spending_trend_inclui_credito(pro_user_id):
    """Compra no cartão deve entrar como despesa do mês."""
    card_id = db.create_card(pro_user_id, "Nubank", closing_day=10, due_day=17)
    db.set_default_card(pro_user_id, card_id)
    db.add_credit_purchase(pro_user_id, card_id, 200, "outros", "x", date.today())

    result = _get_spending_trend(pro_user_id, {"months": 1})
    today = date.today()
    cur = next((r for r in result["data"]
                if r["year"] == today.year and r["month"] == today.month), None)
    assert cur is not None
    assert cur["despesa"] >= 200.0


# ─── forecast_month_end ─────────────────────────────────────────────────────
# Lê o mês pelo mesmo leitor do card `categorias` (`db.mes_detalhes.ler`); o cruzamento com o card,
# o cartão e o usuário B estão em `tests/test_ia_resumo_do_mes.py`.

def _ritmo(r, valor):
    return round(valor * r["dias_no_mes"] / r["dias_corridos"], 2)


def test_forecast_month_end_sem_gastos_projeta_zero(pro_user_id):
    result = _forecast_month_end(pro_user_id, {})
    assert result["despesa"] == 0.0
    assert result["receita"] == 0.0
    assert result["despesa_projetada"] == 0.0
    assert result["saldo_projetado"] == 0.0


def test_forecast_month_end_projeta_baseado_em_ritmo(pro_user_id):
    """Gasto fora do cartão = R$ 100 → o ritmo diário extrapola pro mês."""
    db.add_launch_and_update_balance(pro_user_id, "despesa", 100, "x", "x", categoria="outros")

    result = _forecast_month_end(pro_user_id, {})
    assert result["despesa"] == result["despesa_no_ritmo"] == 100.0
    assert result["despesa_cartao"] == 0.0
    assert result["despesa_projetada"] == _ritmo(result, 100)
    for key in ("dias_corridos", "dias_no_mes", "saldo_ate_agora", "vai_fechar_negativo"):
        assert key in result


def test_forecast_month_end_vai_fechar_negativo(pro_user_id):
    """Despesa real > receita → vai_fechar_negativo."""
    db.add_launch_and_update_balance(pro_user_id, "receita", 50, None, "seed")
    db.add_launch_and_update_balance(pro_user_id, "despesa", 200, "x", "x", categoria="outros")

    result = _forecast_month_end(pro_user_id, {})
    assert result["vai_fechar_negativo"] is True


def test_forecast_month_end_aporte_interno_nao_entra_no_saldo_ate_agora(pro_user_id):
    """Guardar/aportar não é gasto no painel do mês: não entra em `saiu`."""
    db.add_launch_and_update_balance(pro_user_id, "receita", 1000, None, "salario")
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 500, "carteira", "aporte",
        categoria="investimento_aporte",
        is_internal_movement=True,
    )

    result = _forecast_month_end(pro_user_id, {})
    assert result["despesa"] == 0.0
    assert result["receita"] == 1000.0
    assert result["saldo_ate_agora"] == 1000.0


def test_forecast_month_end_aporte_interno_nao_entra_em_saiu_nem_na_projecao(pro_user_id):
    """Aporte de R$ 300 fora do `saiu`: nem soma, nem escala pelo ritmo. Só os R$ 50 comuns."""
    db.add_launch_and_update_balance(pro_user_id, "receita", 5000, None, "salario")
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 300, "carteira", "aporte",
        categoria="investimento_aporte",
        is_internal_movement=True,
    )
    db.add_launch_and_update_balance(pro_user_id, "despesa", 50, "x", "x", categoria="outros")

    result = _forecast_month_end(pro_user_id, {})
    assert result["despesa"] == result["despesa_no_ritmo"] == 50.0
    assert result["despesa_projetada"] == _ritmo(result, 50)
    assert result["saldo_projetado"] == round(5000 - _ritmo(result, 50), 2)



# ─── get_spending_trend summary (média, tendência) ──────────────────────────

def test_spending_trend_calcula_medias(pro_user_id):
    """Cria gasto só no mês corrente → média = valor / N meses pedidos."""
    db.add_launch_and_update_balance(pro_user_id, "despesa", 300, "x", "x", categoria="outros")
    db.add_launch_and_update_balance(pro_user_id, "receita", 600, None, "seed")

    result = _get_spending_trend(pro_user_id, {"months": 3})
    summary = result["summary"]
    # 300 / 1 mes com dado retornado
    # Mas só temos 1 row de dado (mês corrente) — média conta sobre os rows
    n_rows = len(result["data"])
    assert summary["media_despesa"] == round(300.0 / n_rows, 2)
    assert summary["media_receita"] == round(600.0 / n_rows, 2)


def test_spending_trend_tendencia_subindo(pro_user_id):
    """Mês anterior com R$ 100, mês atual com R$ 300 → tendência 'subindo'."""
    today = date.today()
    # Mês anterior (calcular data)
    if today.month == 1:
        prev_y, prev_m = today.year - 1, 12
    else:
        prev_y, prev_m = today.year, today.month - 1
    prev_dt = datetime(prev_y, prev_m, 15)

    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 100, "x", "x", categoria="outros", criado_em=prev_dt,
    )
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 300, "y", "y", categoria="outros",
    )

    result = _get_spending_trend(pro_user_id, {"months": 2})
    assert result["summary"]["tendencia_despesa"] == "subindo"
    # 300 vs 100 = +200% de variação
    assert result["summary"]["variacao_pct"] > 10


def test_spending_trend_tendencia_estavel_com_1_mes(pro_user_id):
    """1 mês só → não dá pra ter tendência."""
    db.add_launch_and_update_balance(pro_user_id, "despesa", 100, "x", "x", categoria="outros")

    result = _get_spending_trend(pro_user_id, {"months": 1})
    assert result["summary"]["tendencia_despesa"] == "estavel"
    assert result["summary"]["variacao_pct"] == 0.0


def test_top_categories_exclui_reembolso_cartao(user_id):
    """credit_transactions com is_refund=true não conta como gasto."""
    card_id = db.create_card(user_id, "Nubank", closing_day=31, due_day=17)
    db.set_default_card(user_id, card_id)
    db.add_credit_purchase(user_id, card_id, 100, "lazer", "show", date.today())
    # Reembolso de R$ 100 — mesma categoria
    db.add_credit_refund(
        user_id=user_id, card_id=card_id, valor=100,
        categoria="lazer", nota="cancelado", purchased_at=date.today(),
    )

    result = _get_top_categories(user_id, {})
    cats = {c["categoria"]: c["total"] for c in result["categories"]}
    # Compra (100) sem subtração do reembolso — porque a tool agrega APENAS
    # gastos, e o reembolso é ignorado. Total = 100.
    assert cats.get("lazer") == 100.0


def _tool_janela(uid, meses):
    # Janela do dia 1 ao dia 1 do mês seguinte (inclui a compra e o lançamento de HOJE, qualquer que seja o
    # dia) com "hoje" no dia 28 desse mês seguinte: o fim não é fim de mês nem >= hoje, então nunca vira
    # "meses inteiros" (que lê o cartão pela fatura). `_mes` fixa o dia 15 e deixava hoje (dia > 15) de fora.
    fim = _fim_do_mes(_mes(date.today(), meses - 1)) + timedelta(days=1)
    with patch.object(tools_launches, "today_tz", lambda: fim.replace(day=28)):
        return _get_largest_expenses(uid, {"start_date": date.today().replace(day=1).isoformat(),
                                           "end_date": fim.isoformat(), "limit": 20})["expenses"]


def test_parcelamento_manual_compra_inteira(user_id):
    card = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    _parcelado(user_id, card, 600, "tv", 3)
    assert [e["parcelamento"] for e in _tool_janela(user_id, 1)] == ["compra de R$ 600,00 em 3x"]


def test_parcelamento_of_uma_parcela(user_id):
    card = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    _parcelas_of(user_id, card)
    es = _tool_janela(user_id, 1)
    assert [e["parcelamento"] for e in es] == ["parcela 1 de 3 — R$ 200,00 neste período"]
    assert "parcela" not in es[0] and "parcelas" not in es[0]


def test_parcelamento_none_a_vista_e_lancamento(user_id):
    card = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    _parcelado(user_id, card, 80, "jogo", 1)
    db.add_launch_and_update_balance(user_id, "despesa", 50, "x", "x", categoria="outros")
    assert [e["parcelamento"] for e in _tool_janela(user_id, 1)] == [None, None]
