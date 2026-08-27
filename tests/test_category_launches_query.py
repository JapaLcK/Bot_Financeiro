"""`list_launches_by_category`: colunas novas e o filtro que casa com o donut.

Passou a devolver `id`, `is_internal_movement`, `nota`, `alvo` e `criado_em`, e
a aceitar `include_internal`.

O `id` existe pro dashboard poder abrir Editar/Excluir na lista de uma
categoria. Ele é NULO na perna do crédito de propósito: `launches` e
`credit_transactions` têm sequências próprias (o id colide) e a perna do
crédito FIXA `tipo='despesa'` — um `credit_transactions.id` saindo daqui com
tipo='despesa' faria o chamador rotear o delete pro endpoint de launches e
apagar OUTRO registro do usuário.

Controle negativo do grupo: trocar `null::int as id` por `ct.id` em
db/accounts.py deixa `test_linha_de_credito_volta_sem_id` vermelho.
Controle positivo: `test_linha_de_launches_volta_com_id_real` prova que a
perna que PODE ser apagada continua trazendo o handle certo, e
`test_saida_do_whatsapp_nao_mudou` prova que os 3 chamadores do bot não viram
diferença.
"""
from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import db
from core.handlers.credit import try_handle_natural_credit_purchase
from core.handlers.launches import list_launches, spend_query
from utils_date import today_tz


def _hoje_as(hora: int):
    return datetime.combine(today_tz(), time(hora, 0))


def _compra_no_credito(user_id: int, texto: str) -> str:
    """Faz a compra no crédito pelo caminho de produção e devolve a CATEGORIA
    que ela recebeu (a inferência escolhe: 'farmacia' vira 'saúde')."""
    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    db.set_default_card(user_id, card_id)
    assert try_handle_natural_credit_purchase(user_id, texto) is not None
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select categoria from credit_transactions where user_id=%s "
            "order by id desc limit 1",
            (user_id,),
        )
        return cur.fetchone()["categoria"]


def test_linha_de_credito_volta_sem_id(pro_user_id):
    cat = _compra_no_credito(pro_user_id, "gastei 90 no crédito na farmacia")

    rows, _ = db.list_launches_by_category(pro_user_id, cat)
    credito = [r for r in rows if r["fonte"] == "credito"]
    assert credito, f"a compra no crédito não apareceu: {rows}"
    for r in credito:
        # Se isto virar um inteiro, o dashboard mostra Editar/Excluir numa linha
        # de cartão e o delete vai pro endpoint errado com um id de OUTRA tabela.
        assert r["id"] is None, r
        assert r["user_seq"] is None, r
        assert r["is_internal_movement"] is False, r


def test_linha_de_launches_volta_com_id_real(pro_user_id):
    lid = db.add_launch_and_update_balance(
        pro_user_id, "despesa", 50, "pao", None,
        categoria="mercado", criado_em=_hoje_as(9),
    )
    rows, _ = db.list_launches_by_category(pro_user_id, "mercado")
    assert len(rows) == 1, rows
    assert rows[0]["fonte"] == "launches"
    assert rows[0]["id"] is not None
    assert rows[0]["is_internal_movement"] is False
    # O id tem que ser o handle REAL da linha, não um número qualquer: é ele que
    # o DELETE /launches/{id} recebe.
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select id from launches where user_id=%s and categoria='mercado'",
            (pro_user_id,),
        )
        assert rows[0]["id"] == cur.fetchone()["id"]
    if isinstance(lid, int):
        assert rows[0]["id"] == lid


def test_movimento_interno_vem_marcado(pro_user_id):
    # É o que esconde Editar/Excluir no dashboard (`editable` de
    # _renderLaunchDetail): movimentação interna não tem edição.
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 700, "pagamento da fatura", None,
        categoria="pagamento_fatura", criado_em=_hoje_as(9),
        is_internal_movement=True,
    )
    rows, resumo = db.list_launches_by_category(pro_user_id, "pagamento_fatura")
    assert len(rows) == 1, rows
    assert rows[0]["is_internal_movement"] is True
    # A query NÃO filtra movimento interno (docstring): some da lista seria pior.
    assert resumo["n_total"] == 1
    assert resumo["despesa"] == 700.0


def test_resumo_e_ordem_inalterados(pro_user_id):
    # Cronológico decrescente, misturando as duas pernas, e o resumo cobrindo
    # TODAS as linhas (window aggregate antes do LIMIT) — nada disso podia mudar.
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 10, "manha", None,
        categoria="lazer", criado_em=_hoje_as(8),
    )
    db.add_launch_and_update_balance(
        pro_user_id, "receita", 40, "estorno", None,
        categoria="lazer", criado_em=_hoje_as(20),
    )
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 20, "tarde", None,
        categoria="lazer", criado_em=_hoje_as(14),
    )

    rows, resumo = db.list_launches_by_category(pro_user_id, "lazer")
    assert [r["descricao"] for r in rows] == ["estorno", "tarde", "manha"], rows
    assert resumo == {"n_total": 3, "despesa": 30.0, "receita": 40.0}

    # limit corta a lista, não o resumo
    rows2, resumo2 = db.list_launches_by_category(pro_user_id, "lazer", limit=1)
    assert len(rows2) == 1
    assert resumo2 == resumo


def test_saida_do_whatsapp_nao_mudou(pro_user_id):
    """Os 3 chamadores de `list_launches_by_category` leem por NOME DE CHAVE.

    Duas chaves novas no dict são invisíveis pra eles — e este teste é o que
    prova, exercitando a listagem e o total pelo texto que o usuário digita.
    """
    cat = _compra_no_credito(pro_user_id, "gastei 88 no crédito na farmacia")
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 112, "consulta", None,
        categoria=cat, criado_em=_hoje_as(9),
    )

    lista = list_launches(pro_user_id, original_text=f"liste os lancamentos em {cat}")
    assert "112,00" in lista, lista
    assert "88,00" in lista, lista
    # A linha de cartão continua marcada com 💳 (não tem "#N" pra apagar) e a de
    # launches continua com o "#N" — o formato da linha não mudou.
    assert "💳" in lista, lista
    assert "#" in lista, lista

    # `spend_query` continua respondendo o mesmo número de sempre (ele soma por
    # `sum_spent_in_category_period`, não por esta query — não é o que mudou).
    total = spend_query(pro_user_id, f"quanto gastei em {cat}")
    assert "112,00" in total, total


# ── `nota` e `alvo` crus, separados do rótulo `descricao` ──────────────────
# `descricao` é coalesce(alvo, nota, '—'): num lançamento com os DOIS
# preenchidos ele é o ALVO. O dashboard pré-preenchia o campo "Descrição" do
# editor com ele e o PATCH gravava o alvo POR CIMA da nota real. Três escritores
# de produção gravam os dois diferentes: recurring_charger.py:302 (alvo
# 'recorrente:Netflix' + nota 'Cobrança automática · Netflix'), db/bills.py:235
# e db/cards.py:1234 — este último com a nota original DENTRO da nota nova
# ("Antecipou parcela 3/12 — <nota> (10/02/2026)"), irrecuperável.
#
# Controle negativo do grupo: apague `nota,` e `alvo,` do select de launches em
# db/accounts.py e `test_nota_e_alvo_nao_saem_do_descricao` fica vermelho
# (KeyError). Controle positivo: `descricao` continua o mesmo rótulo de antes
# (é o que o WhatsApp imprime) — asserido no mesmo teste.
def test_nota_e_alvo_nao_saem_do_descricao(pro_user_id):
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 49.9,
        "recorrente:Netflix", "Cobrança automática · Netflix",
        categoria="assinaturas", criado_em=_hoje_as(9),
    )
    rows, _ = db.list_launches_by_category(pro_user_id, "assinaturas")
    assert len(rows) == 1, rows
    r = rows[0]
    # o rótulo pronto (WhatsApp) não mudou…
    assert r["descricao"] == "recorrente:Netflix"
    # …e as duas colunas cruas vêm separadas, cada uma com o valor REAL.
    assert r["nota"] == "Cobrança automática · Netflix"
    assert r["alvo"] == "recorrente:Netflix"


def test_lancamento_sem_alvo_e_sem_nota_nao_inventa_travessao(pro_user_id):
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 12, None, None,
        categoria="mercado", criado_em=_hoje_as(9),
    )
    r = db.list_launches_by_category(pro_user_id, "mercado")[0][0]
    assert r["descricao"] == "—"     # rótulo de tela
    assert r["nota"] is None         # o que vai pro formulário
    assert r["alvo"] is None


def test_credito_traz_nota_legivel_e_alvo_nulo(pro_user_id):
    cat = _compra_no_credito(pro_user_id, "gastei 90 no crédito na farmacia")
    rows, _ = db.list_launches_by_category(pro_user_id, cat)
    credito = [r for r in rows if r["fonte"] == "credito"]
    assert credito, rows
    for r in credito:
        assert r["alvo"] is None
        # A linha de cartão não é editável (id nulo), então a `nota` aqui só
        # precisa ser o rótulo que a tela mostra — nunca volta num PATCH.
        assert r["nota"] == r["descricao"]
        assert r["nota"]


def test_criado_em_traz_o_instante_cheio(pro_user_id):
    # O editor do dashboard preenche "Data e hora" com `criado_em`. Com só
    # `data` (um date) o campo abria VAZIO pela lista da categoria e preenchido
    # pela Visão Geral — mesmo lançamento, dois caminhos, telas diferentes.
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 10, "pao", None,
        categoria="mercado", criado_em=_hoje_as(9),
    )
    r = db.list_launches_by_category(pro_user_id, "mercado")[0][0]
    assert r["data"] == today_tz()
    assert r["criado_em"].hour == 9, r["criado_em"]
    assert r["criado_em"].astimezone(ZoneInfo("America/Sao_Paulo")).date() == r["data"]


# ── include_internal: a lista não pode contradizer o donut ────────────────
# A barra da Distribuição do mês é `lastData.expense_categories`
# (frontend/dashboard.js), e isso vem da QUERY 6 de `get_financial_data`
# (frontend/finance_bot_websocket_custom.py) — não de `get_top_expense_categories`,
# que NENHUMA rota do dashboard chama (só core/handlers/launches.py:936 e a tool
# da IA). A versão anterior deste teste comparava a lista com a função errada:
# passava sem tocar a barra que o PR promete não contradizer.
#
# Controle NEGATIVO do grupo (dois consertos, um teste cada):
#   • troque `include_internal=False` por True em
#     `test_filtro_do_donut_bate_com_a_lista` → vermelho (750 ≠ 50);
#   • volte a query 6 para `AND tipo = 'despesa'` (singular) →
#     `test_linha_legada_saida_conta_igual_nos_dois` fica vermelho (donut 5,
#     lista 105).
# Controle POSITIVO: `test_default_continua_mostrando_tudo` prova que o caminho
# do WhatsApp continua vendo tudo.


def _donut_do_mes(user_id, ano, mes):
    """A barra da Distribuição, pela query 6 de verdade: {categoria: total}."""
    import asyncio

    import frontend.finance_bot_websocket_custom as dashboard

    data = asyncio.run(dashboard.get_financial_data(user_id, year=ano, month=mes))
    return {c["categoria"]: c["total"] for c in data["expense_categories"]}


def _cenario_donut(user_id):
    db.add_launch_and_update_balance(
        user_id, "despesa", 50, "compra", None,
        categoria="mercado", criado_em=_hoje_as(9),
    )
    db.add_launch_and_update_balance(
        user_id, "despesa", 700, "transferencia interna", None,
        categoria="mercado", criado_em=_hoje_as(10), is_internal_movement=True,
    )
    db.add_launch_and_update_balance(
        user_id, "receita", 40, "estorno", None,
        categoria="mercado", criado_em=_hoje_as(11),
    )
    hoje = today_tz()
    return hoje.replace(day=1), _donut_do_mes(user_id, hoje.year, hoje.month)["mercado"]


def test_filtro_do_donut_bate_com_a_lista(pro_user_id):
    ini, donut_total = _cenario_donut(pro_user_id)
    rows, resumo = db.list_launches_by_category(
        pro_user_id, "mercado", ini, today_tz(), "despesa", 50,
        include_internal=False,
    )
    assert resumo["despesa"] == donut_total == 50.0, (resumo, donut_total)
    assert resumo["receita"] == 0.0, resumo
    assert [r["descricao"] for r in rows] == ["compra"], rows


def test_default_continua_mostrando_tudo(pro_user_id):
    ini, _ = _cenario_donut(pro_user_id)
    rows, resumo = db.list_launches_by_category(pro_user_id, "mercado", ini, today_tz())
    assert resumo == {"n_total": 3, "despesa": 750.0, "receita": 40.0}, resumo
    assert len(rows) == 3


def _grava_tipo_legado(user_id, tipo, valor, categoria, quando):
    """Linha `tipo='saida'` — o valor LEGADO que `_TIPO_ALIASES` (db/accounts.py)
    conta como despesa. Não há escritor que o produza hoje (consultado na
    produção em 27/08/2026: zero linhas `saida`/`entrada` em `launches`), então
    o teste tem que construí-lo: sem isso ele passaria com e sem o conserto."""
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "insert into launches (user_id, tipo, valor, categoria, nota, "
                "criado_em, is_internal_movement) values (%s,%s,%s,%s,%s,%s,false)",
                (user_id, tipo, valor, categoria, "legado", quando),
            )
        conn.commit()


def test_linha_legada_saida_conta_igual_nos_dois(pro_user_id):
    """Donut e lista têm que somar o MESMO conjunto.

    Com a query 6 na forma singular (`tipo = 'despesa'`) a barra somava R$ 5 e a
    lista, que usa `_TIPO_ALIASES` ('despesa','saida'), somava R$ 105 — clicar
    na barra abria um número diferente do que ela mostrava.
    """
    hoje = today_tz()
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 5, "pao", None,
        categoria="mercado", criado_em=_hoje_as(9),
    )
    _grava_tipo_legado(pro_user_id, "saida", 100, "mercado", _hoje_as(10))

    donut = _donut_do_mes(pro_user_id, hoje.year, hoje.month)
    _, resumo = db.list_launches_by_category(
        pro_user_id, "mercado", hoje.replace(day=1), hoje, "despesa", 50,
        include_internal=False,
    )
    assert donut["mercado"] == resumo["despesa"] == 105.0, (donut, resumo)


def test_receita_legada_entrada_nao_entra_no_donut(pro_user_id):
    """Controle POSITIVO do plural: 'saida' entrou, 'entrada' NÃO pode entrar —
    o alias vizinho é receita, e somá-lo inflaria a barra de despesa."""
    hoje = today_tz()
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 5, "pao", None,
        categoria="mercado", criado_em=_hoje_as(9),
    )
    _grava_tipo_legado(pro_user_id, "entrada", 900, "mercado", _hoje_as(10))

    donut = _donut_do_mes(pro_user_id, hoje.year, hoje.month)
    assert donut["mercado"] == 5.0, donut


# ══ B2. A lista tem que mostrar o MESMO instante que a Visão Geral ═══════════
# `data` saía de `dt.date()`, que é o dia no fuso da SESSÃO do Postgres (UTC no
# Railway, o do SO na máquina local). E `has_time`/`posted_at` não vinham, então
# o front caía sempre no galho "só data" do `fmtLaunchWhen`: a mesma despesa
# aparecia "10/03, 00:30" na Visão Geral e "09/03" aqui.
#
# Controle NEGATIVO do par abaixo: volte `"data": _dia_app_tz(r["dt"])` para
# `r["dt"].date()` em db/accounts.py — `test_data_e_o_dia_de_parede_em_sao_paulo`
# fica vermelho em QUALQUER fuso de sessão diferente de America/Sao_Paulo (um
# dos dois instantes cruza a meia-noite pra leste, o outro pra oeste).
# Controle POSITIVO: `test_has_time_e_posted_at_espelham_a_visao_geral` prova
# que o par novo não passou a mentir hora onde ela não existe (OFX).

_SP = ZoneInfo("America/Sao_Paulo")


def test_data_e_o_dia_de_parede_em_sao_paulo(pro_user_id):
    # 10/03 00:30 em SP → 09/03 no fuso a oeste; 10/03 21:30 em SP → 11/03 em UTC.
    # Os dois têm que sair como 10/03 nesta lista, que é o dia que o usuário viu.
    for hora, minuto in ((0, 30), (21, 30)):
        db.add_launch_and_update_balance(
            pro_user_id, "despesa", 10, f"pao {hora}", None, categoria="mercado",
            criado_em=datetime(2026, 3, 10, hora, minuto, tzinfo=_SP),
        )
    rows, _ = db.list_launches_by_category(pro_user_id, "mercado")
    assert [r["data"] for r in rows] == [date(2026, 3, 10), date(2026, 3, 10)], [
        (r["descricao"], r["data"], r["criado_em"]) for r in rows
    ]
    # e o instante cheio continua batendo com o dia, lido em São Paulo
    for r in rows:
        assert r["criado_em"].astimezone(_SP).date() == r["data"], r


def test_has_time_e_posted_at_espelham_a_visao_geral(pro_user_id):
    """As duas telas leem o mesmo par (`LAUNCH_HAS_TIME_SQL` + `posted_at`)."""
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 11, "manual", None,
        categoria="mercado", criado_em=_hoje_as(9),
    )
    # linha de extrato: só a DATA é confiável (o banco não manda hora)
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "update launches set source='ofx', posted_at=%s "
            "where user_id=%s and alvo='manual'",
            (date(2026, 3, 10), pro_user_id),
        )
        conn.commit()
    r = db.list_launches_by_category(pro_user_id, "mercado")[0][0]
    assert r["has_time"] is False, r
    assert r["posted_at"] == date(2026, 3, 10), r

    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 12, "digitado", None,
        categoria="mercado", criado_em=_hoje_as(10),
    )
    novo = [x for x in db.list_launches_by_category(pro_user_id, "mercado")[0]
            if x["alvo"] == "digitado"][0]
    assert novo["has_time"] is True, novo


def test_credito_nao_promete_hora_que_nao_tem(pro_user_id):
    """`credit_transactions.purchased_at` é `date` — a lista imprime "dd/mm"."""
    cat = _compra_no_credito(pro_user_id, "gastei 90 no crédito na farmacia")
    credito = [r for r in db.list_launches_by_category(pro_user_id, cat)[0]
               if r["fonte"] == "credito"]
    assert credito, "a compra no crédito sumiu"
    for r in credito:
        assert r["has_time"] is False, r
        assert r["posted_at"] == r["data"], r


# ══ B4. A barra "sem categoria" do donut × a lista que ela abre ══════════════
# O donut agrupa por `COALESCE(categoria,'sem categoria')` e esta lista casava
# contra a coluna CRUA: `norm(NULL)` não é 'sem categoria', então a barra dizia
# R$ 100 e a lista abria vazia — a contradição que a feature existe pra impedir.
# Chega em produção sem SQL cru: `sync_credit_transactions` (db/open_finance.py)
# passa `r["category"]` sem `or "outros"`, e `add_imported_credit_purchase`
# grava o valor cru.
#
# Controle NEGATIVO: troque `cat_key_sql` por `cat_norm_sql` nas 3 linhas de
# `list_launches_by_category` — este teste fica vermelho (n_total 0 ≠ 1).
# Controle POSITIVO: `test_categoria_normal_nao_virou_sem_categoria`.

def _compra_importada_sem_categoria(user_id, categoria=None, ext="a"):
    """Compra de cartão importada do Open Finance SEM categoria do provedor —
    o caminho que grava `credit_transactions.categoria` NULO em produção.
    Devolve o (ano, mês) da FATURA, que é por onde donut e lista alocam."""
    cards = db.list_cards(user_id)
    card_id = cards[0]["id"] if cards else db.create_card(
        user_id, "Nubank", closing_day=10, due_day=17,
    )
    tx_id, _ = db.add_imported_credit_purchase(
        user_id, card_id, -100, categoria, today_tz(), f"of-{ext}",
    )
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select b.period_end from credit_bills b "
            "join credit_transactions t on t.bill_id = b.id where t.id=%s",
            (tx_id,),
        )
        pe = cur.fetchone()["period_end"]
    return pe.year, pe.month


def test_barra_sem_categoria_do_donut_abre_a_lista_dela(pro_user_id):
    import asyncio

    import frontend.finance_bot_websocket_custom as dashboard

    ano, mes = _compra_importada_sem_categoria(pro_user_id, None)

    data = asyncio.run(dashboard.get_financial_data(pro_user_id, year=ano, month=mes))
    barra = [c for c in data["expense_categories"] if c["total"] == 100.0]
    assert len(barra) == 1, data["expense_categories"]
    rotulo = barra[0]["categoria"]
    assert rotulo == "sem categoria", barra

    # Clicar na barra manda EXATAMENTE esse rótulo pra rota.
    _, resumo = db.list_launches_by_category(
        pro_user_id, rotulo, tipo="despesa", include_internal=False,
    )
    assert resumo["n_total"] == 1, resumo
    assert resumo["despesa"] == barra[0]["total"], (resumo, barra)


def test_categoria_vazia_string_cai_na_mesma_barra(pro_user_id):
    """'' e NULL são a mesma coisa pro usuário — e a barra não pode sair sem
    rótulo, porque um `categoria=` vazio é 400 na rota."""
    import asyncio

    import frontend.finance_bot_websocket_custom as dashboard

    ano, mes = _compra_importada_sem_categoria(pro_user_id, None, ext="nula")
    _compra_importada_sem_categoria(pro_user_id, "", ext="vazia")

    data = asyncio.run(dashboard.get_financial_data(pro_user_id, year=ano, month=mes))
    barras = {c["categoria"]: c["total"] for c in data["expense_categories"]}
    assert barras == {"sem categoria": 200.0}, barras

    _, resumo = db.list_launches_by_category(
        pro_user_id, "sem categoria", tipo="despesa", include_internal=False,
    )
    assert resumo["n_total"] == 2 and resumo["despesa"] == 200.0, resumo


def test_categoria_normal_nao_virou_sem_categoria(pro_user_id):
    """Controle positivo: o colapso do vazio não pode engolir categoria real."""
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 30, "pao", None, categoria="mercado",
        criado_em=_hoje_as(9),
    )
    _, mercado = db.list_launches_by_category(pro_user_id, "mercado")
    _, vazia = db.list_launches_by_category(pro_user_id, "sem categoria")
    assert mercado["n_total"] == 1, mercado
    assert vazia["n_total"] == 0, vazia


# ══ D1. "Carregar mais": offset ═════════════════════════════════════════════
# A tela escrevia "Mostrando 50 de 312" e não havia como ver o resto — o pedido
# era ver TODOS os lançamentos da categoria.
#
# Controle NEGATIVO: tire o `offset %s` do SQL (ou fixe `offset` em 0) em
# db/accounts.py e `test_paginas_nao_repetem_nem_pulam_linha` fica vermelho (a
# página 2 volta igual à 1).
# Controle POSITIVO: `test_offset_default_nao_muda_os_3_chamadores_do_whatsapp`
# prova que o parâmetro novo é aditivo — os 3 chamadores do bot passam `tipo` e
# `limit` por keyword e nada mais.

def _n_lancamentos(user_id, n, categoria="lazer"):
    for i in range(n):
        db.add_launch_and_update_balance(
            user_id, "despesa", 10 + i, f"item {i:02d}", None,
            categoria=categoria, criado_em=_hoje_as(8),
        )


def test_paginas_nao_repetem_nem_pulam_linha(pro_user_id):
    """A soma das páginas tem que ser exatamente o conjunto todo — nem linha
    repetida, nem linha sumida. É o que a ordem TOTAL do `order by` garante:
    com desempate instável, OFFSET devolve a mesma linha duas vezes."""
    _n_lancamentos(pro_user_id, 7)
    inteiro, resumo = db.list_launches_by_category(pro_user_id, "lazer", limit=50)
    assert resumo["n_total"] == 7

    paginado = []
    for off in (0, 3, 6):
        pag, res = db.list_launches_by_category(pro_user_id, "lazer", limit=3, offset=off)
        # o resumo é window aggregate ANTES do LIMIT: continua sendo o total REAL
        assert res == resumo, (off, res, resumo)
        paginado += pag

    chaves = [(r["descricao"], r["valor"]) for r in paginado]
    assert len(chaves) == len(set(chaves)) == 7, chaves
    assert chaves == [(r["descricao"], r["valor"]) for r in inteiro], (chaves, inteiro)


def test_primeira_pagina_nao_muda_com_o_carregar_mais(pro_user_id):
    """Controle POSITIVO: pedir a página 2 não pode mexer na 1."""
    _n_lancamentos(pro_user_id, 5)
    p1a, _ = db.list_launches_by_category(pro_user_id, "lazer", limit=2)
    db.list_launches_by_category(pro_user_id, "lazer", limit=2, offset=2)
    p1b, _ = db.list_launches_by_category(pro_user_id, "lazer", limit=2)
    assert [r["descricao"] for r in p1a] == [r["descricao"] for r in p1b]


def test_offset_alem_do_fim_volta_vazio_com_total_certo(pro_user_id):
    _n_lancamentos(pro_user_id, 3)
    rows, resumo = db.list_launches_by_category(pro_user_id, "lazer", limit=50, offset=99)
    assert rows == []
    # sem linha, não há window aggregate: o resumo zera. O front NÃO recalcula o
    # total a partir daqui (só sobrescreve quando vem linha), então o rodapé
    # continua certo.
    assert resumo["n_total"] == 0, resumo


def test_offset_default_nao_muda_os_3_chamadores_do_whatsapp(pro_user_id):
    """Aditivo: quem não passa `offset` continua vendo a primeira página.
    Os 3 chamadores (core/handlers/launches.py:442, :782, :855) passam
    `tipo`/`limit` por KEYWORD — a assinatura nova não desloca nada."""
    _n_lancamentos(pro_user_id, 4)
    sem, r1 = db.list_launches_by_category(pro_user_id, "lazer", tipo="despesa", limit=2)
    com, r2 = db.list_launches_by_category(
        pro_user_id, "lazer", tipo="despesa", limit=2, offset=0)
    assert [r["descricao"] for r in sem] == [r["descricao"] for r in com]
    assert r1 == r2


def test_ordem_e_total_com_credito_e_launches_no_mesmo_dia(pro_user_id):
    """A perna do crédito tem `user_seq` NULO: era ali que o desempate faltava.
    Duas compras de cartão no mesmo dia empatavam, e OFFSET as embaralhava."""
    cat = _compra_no_credito(pro_user_id, "gastei 90 no crédito na farmacia")
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 11, "remedio", None,
        categoria=cat, criado_em=_hoje_as(9),
    )
    inteiro, resumo = db.list_launches_by_category(pro_user_id, cat, limit=50)
    assert resumo["n_total"] == len(inteiro) >= 2

    por_pagina = []
    for off in range(0, resumo["n_total"], 1):
        pag, _ = db.list_launches_by_category(pro_user_id, cat, limit=1, offset=off)
        por_pagina += pag
    assert ([(r["fonte"], r["valor"]) for r in por_pagina]
            == [(r["fonte"], r["valor"]) for r in inteiro]), (por_pagina, inteiro)


# ══ D3b. As DUAS listagens têm que dizer o mesmo dia ════════════════════════
# `list_launches_by_category` (esta feature) passou a usar o dia de PAREDE
# (`day_tz`, utils_date) e `list_launches` (core/handlers/launches.py:611, o
# "meus últimos lançamentos" do WhatsApp) continuava em `.date()` cru — o dia no
# fuso da SESSÃO do Postgres. Medido: lançamento às 21:30 de 26/08 em São Paulo,
# sessão em UTC (o Railway) → "liste lazer" dizia 26/08 e "meus últimos
# lançamentos" dizia 27/08. O bug do `.date()` era PRÉ-EXISTENTE; a divergência
# entre as duas portas nasceu aqui, e §2 pede a classe, não o caso.
#
# Controle NEGATIVO: volte `day_tz(criado)` para `criado.date()` em
# core/handlers/launches.py e este teste fica vermelho em qualquer sessão fora
# de America/Sao_Paulo (um dos dois instantes cruza a meia-noite pra cada lado).
# Controle POSITIVO: `test_data_e_o_dia_de_parede_em_sao_paulo` (acima) prova que
# a outra porta não mudou de resposta.

def test_as_duas_listagens_dizem_o_mesmo_dia(pro_user_id):
    # 00:30 cruza a meia-noite pra oeste, 21:30 pra leste: em qualquer fuso de
    # sessão, pelo menos um dos dois discorda se alguém voltar ao `.date()`.
    for hora, minuto in ((0, 30), (21, 30)):
        db.add_launch_and_update_balance(
            pro_user_id, "despesa", 10, f"pao {hora}", None, categoria="lazer",
            criado_em=datetime(2026, 3, 10, hora, minuto, tzinfo=_SP),
        )

    rows, _ = db.list_launches_by_category(pro_user_id, "lazer")
    assert {r["data"] for r in rows} == {date(2026, 3, 10)}, rows

    # a MESMA linha, pela outra porta — o texto que o usuário recebe no WhatsApp
    texto = list_launches(pro_user_id, original_text="meus últimos lançamentos")
    assert "10/03" in texto, texto
    for errado in ("09/03", "11/03"):
        assert errado not in texto, (
            f"as duas listagens divergem: a da categoria diz 10/03 e esta diz {errado}\n{texto}"
        )
