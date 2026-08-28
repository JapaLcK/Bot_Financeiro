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

FUSO DA SESSÃO (`PGTZ`): os casos com JANELA (`start_date`/`end_date`) pedem uma
sessão entre UTC-12 e UTC+11 — medido verde em UTC, -03 e +09, vermelho em
`Pacific/Kiritimati` (+14). Não é a fixture: `list_launches_by_category` monta o
corte com `datetime.combine(start_date, 00:00)` NAIVE (db/accounts.py), que o
Postgres lê no fuso da SESSÃO, enquanto o lançamento é gravado no fuso do APP; em
+14 o gasto de hoje 09:00 em São Paulo (12:00Z) cai fora de
[hoje 00:00+14, amanhã 00:00+14) = [ontem 10:00Z, hoje 10:00Z). A produção roda a
sessão em `Etc/UTC`, dentro da faixa.
"""
from __future__ import annotations

import os
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import db
from db.connection import close_pool
from core.handlers.credit import try_handle_natural_credit_purchase
from core.handlers.launches import list_launches, spend_query
from utils_date import _tz, today_tz


def _hoje_as(hora: int):
    """Hoje às `hora` no fuso do APP — aware de propósito.

    Naive, o Postgres lê o instante no fuso da SESSÃO (`PGTZ`): em `Asia/Tokyo`
    a mesma linha voltava com outro dia de parede e `test_criado_em_traz_o_instante_cheio`
    ficava vermelho sem nada de errado no produto.
    """
    return datetime.combine(today_tz(), time(hora, 0), tzinfo=_tz())


def _dinheiro(resumo: dict) -> dict:
    """O resumo sem o `next_after` (a tupla de paginação keyset) — o que os 3
    chamadores do WhatsApp leem."""
    return {k: v for k, v in resumo.items() if k != "next_after"}


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
    assert _dinheiro(resumo) == {"n_total": 3, "despesa": 30.0, "receita": 40.0}

    # limit corta a lista, não o resumo
    rows2, resumo2 = db.list_launches_by_category(pro_user_id, "lazer", limit=1)
    assert len(rows2) == 1
    assert _dinheiro(resumo2) == _dinheiro(resumo)


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
    # Lido no fuso do APP: o instante volta do Postgres no fuso da SESSÃO
    # (`PGTZ`), então comparar `.hour` cru amarrava o teste a rodar em UTC.
    local = r["criado_em"].astimezone(ZoneInfo("America/Sao_Paulo"))
    assert local.hour == 9, r["criado_em"]
    assert local.date() == r["data"]


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
    assert _dinheiro(resumo) == {"n_total": 3, "despesa": 750.0, "receita": 40.0}, resumo
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
# Controle NEGATIVO do par abaixo: volte `"data": launch_day(...)` para
# `r["dt"].date()` em db/accounts.py — `test_data_e_o_dia_de_parede_em_sao_paulo`
# fica vermelho em QUALQUER fuso de sessão diferente de America/Sao_Paulo (um
# dos dois instantes cruza a meia-noite pra leste, o outro pra oeste).
# Controle POSITIVO: `test_has_time_e_posted_at_espelham_a_visao_geral` prova
# que o par novo não passou a mentir hora onde ela não existe (extrato importado
# pelo caminho de produção).

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


def _extrato_do_dia(user_id: int, dia: date) -> tuple[int, str]:
    """Importa UMA linha de extrato pelo caminho de PRODUÇÃO → (id, categoria).

    `import_statement_bytes` → `import_ofx_launches_bulk` é quem grava
    `source='ofx'`, `posted_at` = dia do extrato e `criado_em` = MEIO-DIA local
    DESSE MESMO dia (statement_import.py:666, igual a ofx_import.py:209). Um
    `update launches set source='ofx', posted_at=...` à mão fabricava uma linha
    que escritor NENHUM produz (criado_em = hoje): o teste passava e não media o
    produto.
    """
    from statement_import import import_statement_bytes

    csv = f'Data,Descrição,Valor\n{dia:%d/%m/%Y},MERCADO PAGUE MENOS,"-350,00"\n'
    rep = import_statement_bytes(user_id, csv.encode("utf-8"), f"extrato-{dia}.csv", "csv")
    assert rep["inserted"] == 1, rep
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select id, categoria, criado_em, posted_at from launches "
            "where user_id=%s and source='ofx' order by id desc limit 1",
            (user_id,),
        )
        r = cur.fetchone()
    # a forma real: os dois campos apontam pro MESMO dia
    assert r["posted_at"] == dia, r
    assert r["criado_em"].astimezone(_SP).date() == dia, r
    return r["id"], r["categoria"]


def _cliente_logado(user_id: int):
    """TestClient com os 3 cookies do dashboard + o header de CSRF."""
    from fastapi.testclient import TestClient

    import frontend.finance_bot_websocket_custom as dashboard

    client = TestClient(dashboard.app)
    client.cookies.set(dashboard.AUTH_COOKIE_NAME, dashboard._make_jwt(user_id, "cat@t.com"))
    client.cookies.set(dashboard.DASHBOARD_COOKIE_NAME,
                       dashboard.make_dashboard_token(user_id, hours=1))
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, "test-csrf-token")
    return client, {dashboard.CSRF_HEADER_NAME: "test-csrf-token",
                    "Content-Type": "application/json"}


def _linha(user_id: int, categoria: str, launch_id: int) -> dict:
    return [r for r in db.list_launches_by_category(user_id, categoria)[0]
            if r["id"] == launch_id][0]


def test_has_time_e_posted_at_espelham_a_visao_geral(pro_user_id):
    """As duas telas leem o mesmo par (`LAUNCH_HAS_TIME_SQL` + `posted_at`)."""
    lid, cat = _extrato_do_dia(pro_user_id, date(2026, 3, 10))
    r = _linha(pro_user_id, cat, lid)
    assert r["has_time"] is False, r
    assert r["posted_at"] == date(2026, 3, 10), r
    assert r["data"] == date(2026, 3, 10), r

    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 12, "digitado", None,
        categoria=cat, criado_em=_hoje_as(10),
    )
    novo = [x for x in db.list_launches_by_category(pro_user_id, cat)[0]
            if x["alvo"] == "digitado"][0]
    assert novo["has_time"] is True, novo


def _of_legado(user_id: int, dia: date, categoria: str, alvo: str) -> int:
    """Linha do Open Finance importada ANTES de c474fba (18/08/2026).

    Aquele importador gravava a `date` CRUA em `criado_em` (timestamptz) — meia-
    noite no fuso da SESSÃO, UTC na produção — e `efeitos` sem `time_known`, o
    que deixa `has_time=false`. Essas linhas continuam no banco e são a razão de
    a regra do `posted_at` existir fora do crédito: nelas `day_tz(criado_em)`
    devolve o DIA ANTERIOR. É SQL porque o importador de hoje não escreve mais
    assim (grava meio-dia local) — a forma é histórica, não inventada.
    """
    db.add_launch_and_update_balance(
        user_id, "despesa", 13, alvo, None, categoria=categoria, criado_em=_hoje_as(9),
    )
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "update launches set source='open_finance', posted_at=%s, criado_em=%s, "
            "efeitos='{\"delta_conta\": 0}'::jsonb "
            "where user_id=%s and alvo=%s returning id",
            (dia, dia, user_id, alvo),
        )
        lid = cur.fetchone()["id"]
        conn.commit()
    return lid


# As DUAS pernas do `has_time=false`, cada uma no seu teste porque cada uma é um
# controle negativo separado: com `launch_day` trocado por `day_tz(r["dt"])` em
# db/accounts.py e `PGTZ=UTC`, as duas ficam vermelhas — juntas num teste só, a
# primeira esconderia a segunda. Em sessão -03 elas passam mesmo quebradas: o par
# SÓ discrimina em UTC, que é o fuso da sessão da produção.
#
# O extrato OFX/CSV/PDF NÃO está aqui de propósito: os dois escritores gravam
# `criado_em` = meio-dia local do `posted_at` (statement_import.py:666,
# ofx_import.py:209), então `day_tz` já acerta e um teste com ele não
# discriminaria nada — era o que os dois casos antigos, fabricados com
# `update ... set source='ofx'`, faziam parecer que mediam.

def test_credito_diz_o_dia_da_compra_e_nao_do_instante(pro_user_id):
    """A UNION promove `ct.purchased_at::timestamp` (naive) a `timestamptz` pelo
    fuso da SESSÃO. A sessão da produção é `Etc/UTC` (medido no Railway em
    27/08/2026 08:46 UTC): 27/08 00:00 vira 27/08 00:00Z e `day_tz` devolvia
    26/08 — toda compra de cartão saía com o dia anterior."""
    cat = _compra_no_credito(pro_user_id, "gastei 90 no crédito na farmacia")
    credito = [r for r in db.list_launches_by_category(pro_user_id, cat)[0]
               if r["fonte"] == "credito"]
    assert credito, "a compra no crédito sumiu"
    for r in credito:
        assert r["data"] == r["posted_at"] == today_tz(), r


def test_open_finance_legado_diz_o_dia_da_transacao(pro_user_id):
    """A irmã da compra no crédito: linha do OF importada antes de c474fba,
    com a data crua guardada como meia-noite UTC (ver `_of_legado`)."""
    lid = _of_legado(pro_user_id, date(2026, 3, 10), "mercado", "open finance velho")
    ext = _linha(pro_user_id, "mercado", lid)
    assert ext["has_time"] is False, ext
    assert ext["data"] == date(2026, 3, 10), ext
    # e a OUTRA porta (o "meus últimos lançamentos" do WhatsApp) diz o mesmo dia
    texto = list_launches(pro_user_id, original_text="meus últimos lançamentos")
    assert "10/03" in texto, texto


def test_editar_a_data_de_um_extrato_vence_o_posted_at(pro_user_id):
    """O usuário mudou a data pelo dashboard: as duas telas têm que obedecer.

    `posted_at` manda quando não há hora confiável (`launch_day`, utils_date; e
    `fmtLaunchWhen` no front, dashboard.js:485). E ele era IMUTÁVEL: o PATCH
    gravava só `criado_em`, devolvia 200, a linha mudava no banco e a lista
    seguia imprimindo a data VELHA — sem nenhum caminho de conserto depois.

    Controle NEGATIVO: apague o `posted_at = case when ...` de
    `update_launch_fields` (db/accounts.py) — a lista e o WhatsApp voltam a
    dizer 10/03 depois de o usuário gravar 15/04.
    Controle POSITIVO: `test_editar_a_data_de_um_lancamento_manual_continua_valendo`.
    """
    lid, cat = _extrato_do_dia(pro_user_id, date(2026, 3, 10))

    client, headers = _cliente_logado(pro_user_id)
    resp = client.patch(
        f"/launches/{pro_user_id}/{lid}",
        json={"nota": "pao", "criado_em": "2026-04-15T09:00:00-03:00"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text

    r = _linha(pro_user_id, cat, lid)
    assert r["data"] == date(2026, 4, 15), r
    assert r["posted_at"] == date(2026, 4, 15), r
    assert r["criado_em"].astimezone(_SP).date() == date(2026, 4, 15), r
    # a outra porta não pode contradizer a tela
    texto = list_launches(pro_user_id, original_text="meus últimos lançamentos")
    assert "15/04" in texto and "10/03" not in texto, texto


def test_editar_a_data_de_um_lancamento_manual_continua_valendo(pro_user_id):
    """Controle POSITIVO: linha digitada não ganha data de postagem no caminho."""
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 30, "pao", None, categoria="mercado",
        criado_em=_hoje_as(9),
    )
    lid = db.list_launches_by_category(pro_user_id, "mercado")[0][0]["id"]

    client, headers = _cliente_logado(pro_user_id)
    resp = client.patch(
        f"/launches/{pro_user_id}/{lid}",
        json={"criado_em": "2026-04-15T09:00:00-03:00"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text

    r = _linha(pro_user_id, "mercado", lid)
    assert r["data"] == date(2026, 4, 15), r
    assert r["has_time"] is True, r
    assert r["posted_at"] is None, r


# ══ Quem é dono da data de uma linha do Open Finance ═════════════════════════
# O PROVEDOR. `sync_imported_open_finance_updates` (db/open_finance.py:1559-1588)
# compara `coalesce(posted_at, criado_em::date)` com o `transaction_date` do
# espelho e REESCREVE `posted_at` quando diferem — em toda sincronização Pluggy
# (core/services/pluggy_sync.py:218). Por isso `update_launch_fields` RECUSA
# `criado_em` nessas linhas (409) em vez de gravar e deixar a sync desfazer.
#
# Controle NEGATIVO do grupo: apague o `raise LaunchDateLockedError` de
# `update_launch_fields` (db/accounts.py) →
# `test_editar_a_nota_de_uma_linha_do_open_finance_nao_move_o_dia` fica VERMELHO
# (o PATCH volta 200 e o dia anda 10/03 → 09/03).
# Controle POSITIVO: `test_editar_a_data_de_um_extrato_vence_o_posted_at` e
# `test_editar_a_data_de_um_lancamento_manual_continua_valendo` (acima) provam
# que a recusa é SÓ do Open Finance, e
# `test_editar_so_a_nota_de_uma_linha_do_open_finance_grava` prova que o resto
# do formulário continua editável nessas linhas.

def _of_do_dia(user_id: int, dia: date, *, legado: bool = False,
               valor: str = "137.77", categoria: str = "mercado") -> tuple[int, int]:
    """Linha do Open Finance pelo caminho de PRODUÇÃO → (launch_id, of_tx_id).

    Espelho real (`save_pluggy_open_finance_item` + `save_open_finance_sync`) →
    `import_open_finance_launches`. É o vínculo `imported_launch_id` que sai daí
    que faz a sync do OF rodar de verdade no teste — com `update ... set
    source='open_finance'` à mão não existe espelho, e a sync não vê a linha.

    `legado=True` envelhece a linha para a forma de ANTES de c474fba
    (18/08/2026): `criado_em` = a `date` CRUA (meia-noite no fuso da SESSÃO, UTC
    na produção) e `efeitos` sem `time_known` (logo `has_time=false`). São
    EXATAMENTE as duas colunas que aquele commit mudou (`git show
    c474fba^:db/open_finance.py`); todo o resto — source, external_id,
    posted_at, o vínculo com o espelho — continua vindo do importador de hoje.
    """
    from decimal import Decimal

    conexao = db.save_pluggy_open_finance_item(
        user_id,
        {"id": f"item-of-{user_id}-{dia}", "connector": {"id": 612, "name": "Nubank"},
         "status": "UPDATED"},
    )
    tx_id = f"of-tx-{user_id}-{dia}"
    db.save_open_finance_sync(conexao["id"], [{
        "provider_account_id": f"acc-of-{user_id}",
        "name": "Nubank Conta", "type": "BANK", "subtype": "CHECKING_ACCOUNT",
        "currency": "BRL", "balance": Decimal("1000.00"), "raw": {},
        "transactions": [{
            "provider_transaction_id": tx_id,
            "description": "MERCADO PAGUE MENOS",
            "amount": Decimal("-" + valor),
            "transaction_date": dia,
            "transacted_at": None,
            "category": categoria,
            "raw": {},
        }],
    }])
    rep = db.import_open_finance_launches(user_id, conexao["id"])
    assert rep["inserted"] == 1, rep

    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select id, imported_launch_id from open_finance_transactions "
            "where provider_transaction_id=%s",
            (tx_id,),
        )
        r = cur.fetchone()
        lid, of_tx_id = r["imported_launch_id"], r["id"]
        if legado:
            cur.execute(
                "update launches set criado_em=%s, efeitos = efeitos - 'time_known' "
                "where id=%s",
                (dia, lid),
            )
        conn.commit()
    return lid, of_tx_id


def test_a_sync_do_open_finance_e_dona_da_data(pro_user_id):
    """A MEDIÇÃO por trás da recusa: o provedor reescreve `posted_at`.

    Sem isto, "a data da linha do OF é do banco" seria opinião. Aqui o espelho
    muda de 10/03 pra 12/03 e a linha importada segue o espelho — é o mesmo
    caminho que desfazia a edição do usuário.
    """
    lid, of_tx_id = _of_do_dia(pro_user_id, date(2026, 3, 10))
    assert _linha(pro_user_id, "mercado", lid)["data"] == date(2026, 3, 10)

    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("update open_finance_transactions set transaction_date=%s where id=%s",
                    (date(2026, 3, 12), of_tx_id))
        conn.commit()
    assert db.sync_imported_open_finance_updates(pro_user_id)["launches_updated"] == 1
    assert _linha(pro_user_id, "mercado", lid)["data"] == date(2026, 3, 12)


def test_editar_a_nota_de_uma_linha_do_open_finance_nao_move_o_dia(pro_user_id):
    """O corpo EXATO que o modal antigo mandava ao salvar só a descrição.

    `criado_em` reenviado igual ao que já estava no banco (o modal pré-preenche
    o campo e mandava sempre). Numa linha do OF legado — `criado_em` = meia-noite
    UTC — `day_tz` devolve o DIA ANTERIOR, e era ele que ia parar no `posted_at`,
    justamente o único campo certo daquela linha. Agora a rota recusa (409).
    """
    lid, _ = _of_do_dia(pro_user_id, date(2026, 3, 10), legado=True)
    antes = _linha(pro_user_id, "mercado", lid)
    assert antes["has_time"] is False and antes["data"] == date(2026, 3, 10), antes

    client, headers = _cliente_logado(pro_user_id)
    resp = client.patch(
        f"/launches/{pro_user_id}/{lid}",
        # 2026-03-09T21:00-03:00 == 2026-03-10T00:00Z: o MESMO instante que já
        # está gravado, que é o que `toLocalDatetimeInput` pré-preenche.
        json={"nota": "corrigindo so a descricao", "criado_em": "2026-03-09T21:00:00-03:00"},
        headers=headers,
    )
    # O DANO primeiro: com a guarda desligada isto falha com 09/03 (o dia
    # anterior), que é o bug relatado — e não só com "200 != 409".
    depois = _linha(pro_user_id, "mercado", lid)
    assert depois["data"] == date(2026, 3, 10), depois
    assert depois["posted_at"] == date(2026, 3, 10), depois
    # e a rota diz POR QUE recusou, em vez de fingir sucesso
    assert resp.status_code == 409, resp.text
    assert "banco conectado" in resp.json()["detail"], resp.text


def test_editar_so_a_nota_de_uma_linha_do_open_finance_grava(pro_user_id):
    """Controle POSITIVO: a recusa é da DATA, não do formulário inteiro.

    É este o caminho que o modal usa hoje (só manda o campo que mudou), e a
    sync do OF não toca em `nota`/`alvo` — então a edição sobrevive.
    """
    lid, _ = _of_do_dia(pro_user_id, date(2026, 3, 10), legado=True)

    client, headers = _cliente_logado(pro_user_id)
    resp = client.patch(
        f"/launches/{pro_user_id}/{lid}",
        json={"nota": "mercado do bairro"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text

    r = _linha(pro_user_id, "mercado", lid)
    assert r["nota"] == "mercado do bairro", r
    assert r["data"] == date(2026, 3, 10), r
    # e a sync seguinte não tem o que desfazer
    db.sync_imported_open_finance_updates(pro_user_id)
    r = _linha(pro_user_id, "mercado", lid)
    assert r["nota"] == "mercado do bairro" and r["data"] == date(2026, 3, 10), r


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


def test_categoria_vazia_volta_CRUA_e_nao_como_outros(pro_user_id):
    """O `coalesce(categoria,'outros')` era rótulo de mensagem de WhatsApp; desde
    que o dashboard abre o EDITOR por esta lista, ele virou dado — quem editasse
    só a nota ou a data mandava 'outros' de volta no PATCH e categorizava a
    transação sem pedir. Mesma classe do `nota` fabricado a partir de `descricao`.

    Controle NEGATIVO: volte `categoria` para
    `coalesce(nullif(categoria, ''), 'outros') as categoria` (e o mesmo em
    `ct.categoria`) em db/accounts.py — as duas asserções abaixo ficam vermelhas.
    Controle POSITIVO: `test_categoria_normal_nao_virou_sem_categoria` (abaixo)
    e os testes de `describeLaunch`/rótulo continuam provando que categoria REAL
    sai como está.
    """
    # perna do crédito: o caminho de produção (Open Finance sem categoria)
    _compra_importada_sem_categoria(pro_user_id, None, ext="crua")
    # perna de launches: `add_launch_and_update_balance` sempre grava alguma
    # categoria (db/accounts.py:78), então o NULL vem por SQL — é o que o dado
    # legado e a importação de extrato deixam na coluna.
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 40, "pao", None, categoria="mercado",
        criado_em=_hoje_as(9),
    )
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "update launches set categoria=null where user_id=%s and alvo='pao'",
            (pro_user_id,),
        )
        conn.commit()
    rows, _ = db.list_launches_by_category(pro_user_id, "sem categoria")
    assert len(rows) == 2, rows
    for r in rows:
        assert not r["categoria"], (
            f"a lista fabricou categoria={r['categoria']!r} numa linha sem categoria — "
            "o editor grava isso de volta"
        )


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


# ══ D1. "Carregar mais": keyset ════════════════════════════════════════════
# A tela escrevia "Mostrando 50 de 312" e não havia como ver o resto — o pedido
# era ver TODOS os lançamentos da categoria.
#
# Era OFFSET, e OFFSET não fecha a corrida que este produto tem TODO DIA: o bot
# escreve no banco com o dashboard aberto. Uma linha nova entra ACIMA do corte
# (a ordem é por data desc), empurra a fronteira, e a página 2 repete a última
# linha da 1 E come outra — com o `n_total` dizendo que está tudo lá.
#
# Controle NEGATIVO: troque o `where (dt, fonte, ord_id) < (%s, %s, %s)` por
# `offset` (`limit %s offset %s` com o número de linhas já vistas) em
# db/accounts.py — `test_lancamento_novo_no_meio_nao_repete_nem_come_linha` fica
# vermelho.
# Controle NEGATIVO da ORDEM TOTAL, medido nos dois planos: as três mutações do
# `order by` (`fonte desc`→`fonte asc`, `fonte` REMOVIDO e `ord_id desc`→
# `ord_id asc`) deixam `test_ordem_e_total_com_credito_e_launches_no_mesmo_dia`
# vermelho; e o `<` do keyset virado `<=` deixa 4 dos 34 testes deste ARQUIVO
# vermelhos em 2,1s (na suíte inteira são 6, com os 2 de tests/
# test_routes_categories.py) — em vez de pendurar, que era o comportamento antigo.
# Controle POSITIVO: `test_paginas_nao_repetem_nem_pulam_linha` (sem escrita
# concorrente, a paginação continua idêntica) e
# `test_after_default_nao_muda_os_3_chamadores_do_whatsapp` (o parâmetro novo
# entrou no FIM da assinatura — nenhum posicional que já existia mudou de lugar,
# e os 4 chamadores continuam vendo a primeira página).

def _n_lancamentos(user_id, n, categoria="lazer"):
    for i in range(n):
        db.add_launch_and_update_balance(
            user_id, "despesa", 10 + i, f"item {i:02d}", None,
            categoria=categoria, criado_em=_hoje_as(8),
        )


def _paginado(user_id, categoria, pagina, **kw):
    """Percorre a lista inteira pelo cursor, como o "Carregar mais" da tela.

    O que faz o laço terminar é o CURSOR ANDAR: `next_after` é a chave da última
    linha da página, e `where (dt, fonte, ord_id) < after` só pode devolver
    chave ESTRITAMENTE MENOR. Um `<`→`<=` no keyset devolve a última linha de
    novo — na página final o cursor para de andar e o `while True:` PENDURA até
    o timeout do job do CI, que é sinal pior que vermelho. Afirmar o avanço
    estrito para o laço nomeando a causa certa.

    Um teto de LINHAS (`n_total` da primeira página) não serve: uma linha que
    entra ABAIXO do cursor no meio da paginação — lançamento retroativo, import
    de extrato/OFX, a corrida normal deste produto — faz a soma das páginas
    passar do `n_total` da primeira SEM bug nenhum, e a mensagem culparia o
    keyset por algo que ele acertou.
    """
    todas, after = [], None
    while True:
        pag, res = db.list_launches_by_category(
            user_id, categoria, limit=pagina, after=after, **kw)
        todas += pag
        if not pag:
            return todas, res
        assert after is None or res["next_after"] < after, (
            "o CURSOR NÃO ANDOU — isto não é asserção de conteúdo. "
            f"{after} → {res['next_after']}, com limit={pagina} e a página "
            f"{[(r['fonte'], r['valor']) for r in pag]}. O keyset está "
            "devolvendo a mesma linha: o `<` de `(dt, fonte, ord_id)` virou "
            "`<=`? (db/accounts.py, `after_sql`)"
        )
        after = res["next_after"]


def test_paginas_nao_repetem_nem_pulam_linha(pro_user_id):
    """A soma das páginas tem que ser exatamente o conjunto todo — nem linha
    repetida, nem linha sumida."""
    _n_lancamentos(pro_user_id, 7)
    inteiro, resumo = db.list_launches_by_category(pro_user_id, "lazer", limit=50)
    assert resumo["n_total"] == 7

    paginado, _ = _paginado(pro_user_id, "lazer", 3)
    chaves = [(r["descricao"], r["valor"]) for r in paginado]
    assert len(chaves) == len(set(chaves)) == 7, chaves
    assert chaves == [(r["descricao"], r["valor"]) for r in inteiro], (chaves, inteiro)


def test_o_resumo_e_o_mesmo_em_todas_as_paginas(pro_user_id):
    """O keyset filtra FORA da subquery dos window aggregates: `n_total` e os
    totais continuam cobrindo TODAS as linhas, não só as que sobraram do corte.
    Sem isso o rodapé "N de M" mentiria a partir da página 2."""
    _n_lancamentos(pro_user_id, 7)
    _, inteiro = db.list_launches_by_category(pro_user_id, "lazer", limit=50)
    after = None
    for _ in range(3):
        pag, res = db.list_launches_by_category(
            pro_user_id, "lazer", limit=3, after=after)
        assert _dinheiro(res) == _dinheiro(inteiro), (res, inteiro)
        after = res["next_after"]
        assert pag


def test_lancamento_novo_no_meio_nao_repete_nem_come_linha(pro_user_id):
    """O cenário NORMAL deste produto: o bot grava enquanto o modal está aberto.

    Com OFFSET, a linha nova (que entra no TOPO) desloca a fronteira: a página 2
    repete a última linha da 1 e some com outra, e o usuário nunca vê a que
    sumiu. Com keyset a página 2 é "o que vem depois desta linha aqui" e a linha
    nova simplesmente não participa."""
    _n_lancamentos(pro_user_id, 6)
    p1, res1 = db.list_launches_by_category(pro_user_id, "lazer", limit=3)
    assert [r["descricao"] for r in p1] == ["item 05", "item 04", "item 03"], p1

    # chega uma transação pelo WhatsApp com a lista aberta (entra no topo)
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 99, "intruso", None,
        categoria="lazer", criado_em=_hoje_as(9),
    )

    p2, _ = db.list_launches_by_category(
        pro_user_id, "lazer", limit=3, after=res1["next_after"])
    assert [r["descricao"] for r in p2] == ["item 02", "item 01", "item 00"], p2
    vistos = [r["descricao"] for r in p1 + p2]
    assert len(vistos) == len(set(vistos)) == 6, vistos


def test_after_alem_do_fim_volta_vazio_com_total_certo(pro_user_id):
    _n_lancamentos(pro_user_id, 3)
    _, resumo = db.list_launches_by_category(pro_user_id, "lazer", limit=50)
    rows, vazio = db.list_launches_by_category(
        pro_user_id, "lazer", limit=50, after=resumo["next_after"])
    assert rows == []
    # sem linha, não há window aggregate: o resumo zera e `next_after` some. O
    # front NÃO recalcula o total a partir daqui (só sobrescreve quando vem
    # linha), então o rodapé continua certo.
    assert vazio["n_total"] == 0, vazio
    assert vazio["next_after"] is None, vazio


def test_after_default_nao_muda_os_3_chamadores_do_whatsapp(pro_user_id):
    """Aditivo: quem não passa `after` continua vendo a primeira página.

    O que torna a mudança segura é `after` ter entrado no FIM da assinatura, não
    o estilo de chamada: os 3 do WhatsApp (`_listar_categoria`, `_total_despesa`
    e `_total_categoria`, core/handlers/launches.py) passam
    `user_id, categoria, start, end` posicionais e só `tipo`/`limit` por
    keyword. O 4º chamador é a rota (`category_launches_route`,
    frontend/routes/categories.py), que passa tudo por keyword.
    """
    _n_lancamentos(pro_user_id, 4)
    sem, r1 = db.list_launches_by_category(pro_user_id, "lazer", tipo="despesa", limit=2)
    com, r2 = db.list_launches_by_category(
        pro_user_id, "lazer", tipo="despesa", limit=2, after=None)
    assert [r["descricao"] for r in sem] == [r["descricao"] for r in com]
    assert r1 == r2


def _ordem_sob_plano(user_id, categoria, pgoptions):
    """A lista inteira relida com o PLANNER forçado.

    `PGOPTIONS` é lido pelo libpq na hora de CONECTAR, então o pool precisa ser
    reaberto — `close_pool` (db/connection.py) existe pra suíte e faz a próxima
    chamada reabrir. Os dois planos são passados EXPLÍCITOS (`on` e `off`, não
    "o ambiente" e `off`) pra medição não virar tautologia quando quem roda o
    pytest já exportou `PGOPTIONS`.

    A mutação que PRECISA deste helper é o DESEMPATE REMOVIDO (`order by dt desc,
    user_seq desc nulls last`): ali as linhas de crédito ficam genuinamente
    empatadas e a permutação depende do plano. As três do bloco "Controle
    NEGATIVO da ORDEM TOTAL" (`fonte asc`, `fonte` removido, `ord_id asc`) NÃO
    dependem dele — elas ficam vermelhas já no assert de ordem estrita, com
    plano nenhum forçado.
    """
    antes = os.environ.get("PGOPTIONS")
    try:
        # dentro do try: um `close_pool()` que levantasse aqui vazaria o
        # `PGOPTIONS` deste teste para o resto da suíte.
        os.environ["PGOPTIONS"] = pgoptions
        close_pool()
        rows, _ = db.list_launches_by_category(user_id, categoria, limit=50)
        return [(r["fonte"], r["valor"]) for r in rows]
    finally:
        if antes is None:
            os.environ.pop("PGOPTIONS", None)
        else:
            os.environ["PGOPTIONS"] = antes
        close_pool()


def test_ordem_e_total_com_credito_e_launches_no_mesmo_dia(pro_user_id):
    """A perna do crédito tem `user_seq` NULO: era ali que o desempate faltava.
    Sem ordem total, o keyset pula ou repete exatamente como o OFFSET.

    TRÊS compras no MESMO `purchased_at`, o launch semeado no MESMO instante
    delas e com o MESMO `id` de uma delas — o empate é o cenário inteiro, e cada
    peça do seed exercita um componente da chave `(dt, fonte, ord_id)` (o mapa
    está no comentário do seed, abaixo). Com duas pernas em horas diferentes (o
    que este teste fazia antes) `fonte` nunca desempata nada: medido,
    `fonte desc`→`fonte asc` passava pelos 5071 testes E perdia linha de
    verdade na paginação.
    """
    cat = _compra_no_credito(pro_user_id, "gastei 11 no crédito na farmacia")
    for texto in ("gastei 22 no crédito na farmacia",
                  "gastei 33 no crédito na farmacia"):
        assert try_handle_natural_credit_purchase(pro_user_id, texto) is not None
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select categoria, purchased_at from credit_transactions "
                    "where user_id=%s", (pro_user_id,))
        compras = cur.fetchall()
    # O empate é o cenário; se a inferência espalhar as 3 em categorias
    # diferentes, ou se as datas divergirem, o teste deixa de medir o que promete.
    assert len(compras) == 3, compras
    assert {(r["categoria"], r["purchased_at"]) for r in compras} == {
        (cat, compras[0]["purchased_at"])}, compras

    # O launch entra no MESMO instante das compras. O `dt` da perna de crédito é
    # `purchased_at::timestamp`, que o Postgres lê no fuso da SESSÃO (`PGTZ`):
    # uma hora fixa em Python (`_hoje_as(9)`) empatava com ele só por
    # coincidência de fuso — sob `TZ=Asia/Tokyo` empatava, em São Paulo não.
    # Lendo o `dt` de volta e semeando com ELE, o empate existe em qualquer
    # `TZ`/`PGTZ`.
    so_credito, _ = db.list_launches_by_category(pro_user_id, cat, limit=1)
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 99, "remedio", None,
        categoria=cat, criado_em=so_credito[0]["criado_em"],
    )

    # E o `id` do launch passa a COLIDIR com o da compra do MEIO (22) — o 3º
    # componente da chave, que nenhum seed produzia: `launches` e
    # `credit_transactions` têm sequências próprias e os números não batem por
    # acaso. Sem a colisão, tirar `fonte` INTEIRO do `order by`
    # (`order by dt desc, ord_id desc`) passava pelos 34 testes deste arquivo E
    # perdia linha na paginação de verdade — medido nos dois planos. Ela tem que
    # ser no MEIO: o keyset compara `(dt, fonte, ord_id)` e 'launches' > 'credito',
    # então o launch só se perde quando existe uma compra ACIMA dele virando
    # cursor (a de 33). Colidido no topo, a paginação sobrevive e o teste volta a
    # não medir.
    #
    # A porta de produção não deixa escolher `id`, daí o UPDATE cru. Os números
    # saem de max+1 das DUAS tabelas, pra não esbarrar em linha de outro teste no
    # banco compartilhado, e a compra de 33 sobe junto pra perna de crédito
    # manter `valor` crescendo com `ord_id` (11 < 22 < 33) — é isso que o assert
    # de ordem estrita lê.
    #
    # QUEM MEDE O QUÊ no seed, pra ninguém "simplificar" e voltar a não medir:
    # as 3 compras no mesmo `purchased_at` + o launch no mesmo `dt` medem `dt`
    # (sem elas o `order by` desempata sozinho e o resto é decoração); as 3
    # compras com ids distintos medem `ord_id`; ESTA colisão mede `fonte`.
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select greatest((select max(id) from launches), "
                    "(select max(id) from credit_transactions)) + 1 as novo")
        novo = cur.fetchone()["novo"]
        for tabela, valor, id_novo in (("credit_transactions", 33, novo + 1),
                                       ("credit_transactions", 22, novo),
                                       ("launches", 99, novo)):
            cur.execute(f"update {tabela} set id=%s where user_id=%s and valor=%s",
                        (id_novo, pro_user_id, valor))
            assert cur.rowcount == 1, (
                f"o seed não colidiu o id: {cur.rowcount} linha(s) de {tabela} "
                f"com valor={valor}")

    inteiro, resumo = db.list_launches_by_category(pro_user_id, cat, limit=50)
    assert resumo["n_total"] == len(inteiro) == 4, (resumo, inteiro)
    assert len({r["criado_em"] for r in inteiro}) == 1, (
        "as 4 linhas deviam empatar em `dt` — sem empate `fonte` e `ord_id` não "
        f"desempatam nada e o teste deixa de medir: {[r['criado_em'] for r in inteiro]}")

    # A chave do keyset é `(dt, fonte, ord_id)`. `ord_id` não sai na LINHA (é o
    # id cru das duas tabelas, ver a docstring de `list_launches_by_category`),
    # então `valor` entra como proxy: 11 < 22 < 33 crescem junto com o id das
    # três compras. Estritamente decrescente = a ordem é TOTAL, que é o que o
    # `order by` promete — e isso não depende de paginação nenhuma.
    chaves = [(r["criado_em"], r["fonte"], r["valor"]) for r in inteiro]
    assert all(a > b for a, b in zip(chaves, chaves[1:])), (
        "a ordem não é total — ou duas linhas têm a mesma chave de keyset, ou a "
        f"lista não desce por (dt, fonte, ord_id): {chaves}")

    esperado = [(r["fonte"], r["valor"]) for r in inteiro]
    paginado = [(r["fonte"], r["valor"]) for r in _paginado(pro_user_id, cat, 1)[0]]
    assert paginado == esperado, f"paginação perdeu/repetiu: {paginado} != {esperado}"

    # A MESMA lista sob dois planos. Isto AUMENTA a chance de pegar a ordem
    # instável; não a garante — e não é "estrutural". Sem desempate as linhas de
    # crédito ficam GENUINAMENTE empatadas e o Postgres pode emitir qualquer
    # permutação, inclusive a certa: com
    # `enable_indexscan=off enable_bitmapscan=off` o Sort devolve exatamente a
    # decrescente e o assert de cima fica VERDE por acaso (medido para N = 3, 5,
    # 8, 13, 21 e 34 linhas empatadas — aumentar o seed não compra nada, o
    # planner não sorteia permutação, é determinístico por plano). Ler sob dois
    # planos pega o caso em que eles DISCORDAM — no seed deste teste eles
    # discordam e o negativo fica vermelho (medido: 1 failed, 33 passed nos DOIS
    # planos, com `order by dt desc, user_seq desc nulls last`). Mas a ordem de um
    # empate depende do plano E do estado físico/estatístico da tabela, e existe
    # estado em que os dois CONCORDAM e o negativo fica VERDE: medido num seed sem
    # a colisão de `id`, um `ANALYZE` nas três tabelas colapsou dez opções de
    # planner na mesma permutação, e com o heap em ordem física decrescente (três
    # `UPDATE` + `ANALYZE`, o que dado de produção sofre) o negativo passou. Com o
    # seed que ficou o `ANALYZE` já não vira o teste — mas isso é uma medição, não
    # garantia. Postgres 15 local; o CI é 16.
    com_indice = _ordem_sob_plano(
        pro_user_id, cat, "-c enable_indexscan=on -c enable_bitmapscan=on")
    sem_indice = _ordem_sob_plano(
        pro_user_id, cat, "-c enable_indexscan=off -c enable_bitmapscan=off")
    assert com_indice == sem_indice == esperado, (
        "a ordem depende do PLANO, logo o `order by` não é total: "
        f"com índice {com_indice}, sem índice {sem_indice}, esperado {esperado}")


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
