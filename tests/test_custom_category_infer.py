"""
Regressão: categoria CUSTOM criada na tela (user_categories) sem regra de
keyword deve ser reconhecida na inferência de um lançamento.

Bug relatado: usuário cria a categoria "gastos com minha namorada" e lança
"gastei 400 com minha namorada" — o gasto ia parar em "outros" porque
infer_category só olhava user_category_rules / LOCAL_RULES / IA, nunca as
categorias custom que o usuário cadastrou visualmente.
"""
from __future__ import annotations

import re
from datetime import datetime, time, timedelta

import pytest

import db
from db.categories import (
    create_user_category,
    list_custom_category_names,
    list_user_category_rules,
    set_user_category_archived,
)
from core.handlers.credit import try_handle_natural_credit_purchase
from core.handlers.launches import list_launches, spend_query
from core.services.category_service import (
    custom_category_match,
    infer_category,
    learn_from_inference,
    _distinctive_tokens,
)
from utils_date import today_tz
from utils_text import normalize_text


# --- unit do helper de tokens distintivos --------------------------------

@pytest.mark.parametrize("name,expected", [
    ("gastos com minha namorada", ["namorada"]),
    ("Gastos com Pet",            ["pet"]),          # 3 letras, palavra inteira → ok
    ("gastos com minha mãe",      ["mae"]),          # acento cai na normalização
    ("cachorro do vizinho",       ["cachorro", "vizinho"]),
    ("gastos gerais",             []),               # só tokens genéricos
    ("gastos do dia",             []),               # "dia" é genérico curto
    ("faculdade",                 ["faculdade"]),
])
def test_distinctive_tokens(name, expected):
    assert _distinctive_tokens(name) == expected


# --- match contra categorias custom do usuário ---------------------------

def test_custom_category_match_namorada(pro_user_id):
    create_user_category(pro_user_id, "gastos com minha namorada")
    hit = custom_category_match(pro_user_id, normalize_text("gastei 400 com minha namorada"))
    assert hit == "gastos com minha namorada"


def test_custom_category_no_false_positive(pro_user_id):
    create_user_category(pro_user_id, "gastos com minha namorada")
    # nenhum token distintivo ("namorada") aparece → não casa
    assert custom_category_match(pro_user_id, normalize_text("gastei 50 no mercado")) is None


def test_infer_usa_categoria_custom(pro_user_id):
    create_user_category(pro_user_id, "gastos com minha namorada")
    res = infer_category(pro_user_id, "gastei 400 com minha namorada", allow_ai=False)
    assert res.category == "gastos com minha namorada"
    assert res.reason == "user_category"


def test_infer_cai_em_outros_sem_categoria_custom(pro_user_id):
    # sem categoria custom cadastrada e sem regra local, "namorada" não existe
    res = infer_category(pro_user_id, "gastei 400 com minha namorada", allow_ai=False)
    assert res.category == "outros"
    assert res.reason == "default"


def test_regra_de_keyword_vence_categoria_custom(pro_user_id):
    # user_rule (passo B) tem prioridade sobre a categoria custom (passo B2)
    create_user_category(pro_user_id, "gastos com minha namorada")
    db.upsert_category_rule(pro_user_id, "namorada", "lazer")
    res = infer_category(pro_user_id, "gastei 400 com minha namorada", allow_ai=False)
    assert res.category == "lazer"
    assert res.reason == "user_rule"


def test_preserva_acento_do_nome_cadastrado(pro_user_id):
    # o nome retornado deve bater EXATAMENTE com o cadastrado (com acento),
    # senão o lançamento não agrupa com a categoria no dashboard.
    create_user_category(pro_user_id, "saúde da família")
    res = infer_category(pro_user_id, "gastei 200 com a saúde da família", allow_ai=False)
    assert res.category == "saúde da família"
    assert res.reason == "user_category"


def test_categoria_arquivada_nao_casa(pro_user_id):
    cat = create_user_category(pro_user_id, "gastos com minha namorada")
    set_user_category_archived(pro_user_id, cat["id"], True)
    assert custom_category_match(pro_user_id, normalize_text("gastei 400 com minha namorada")) is None


def test_user_category_nao_cria_regra_aprendida(pro_user_id):
    create_user_category(pro_user_id, "gastos com minha namorada")

    learn_from_inference(
        pro_user_id,
        "gastei 400 com minha namorada",
        "gastos com minha namorada",
        target_hint="minha namorada",
        reason="user_category",
    )

    assert list_user_category_rules(pro_user_id) == []


def test_categoria_arquivada_nao_volta_por_regra_aprendida(pro_user_id):
    cat = create_user_category(pro_user_id, "gastos com minha namorada")
    learn_from_inference(
        pro_user_id,
        "gastei 400 com minha namorada",
        "gastos com minha namorada",
        target_hint="minha namorada",
        reason="user_category",
    )

    set_user_category_archived(pro_user_id, cat["id"], True)
    res = infer_category(pro_user_id, "gastei 400 com minha namorada", allow_ai=False)

    assert res.category == "outros"
    assert res.reason == "default"


def test_list_custom_category_names_nao_roda_seed(monkeypatch, pro_user_id):
    def _fail_seed(_user_id):
        raise AssertionError("inferência não deve semear categorias nesse caminho")

    monkeypatch.setattr("db.categories.ensure_user_categories_seeded", _fail_seed)

    assert list_custom_category_names(pro_user_id) == []


def test_auto_aprendizado_nao_rouba_token_de_categoria_custom(pro_user_id):
    # Regressão do cliente (pedromaeda35): "namorada cinema" casa cinema→lazer
    # (LOCAL_RULES) e o auto-aprendizado gravava namorada→lazer, sequestrando
    # todo lançamento com "namorada" pra lazer — a categoria custom nunca era
    # alcançada. O guard local não pega porque "namorada" não está nas LOCAL_RULES.
    create_user_category(pro_user_id, "gastos com minha namorada")

    learn_from_inference(pro_user_id, "namorada cinema", "lazer", reason="local_rule")

    # nenhuma regra aprendida contém "namorada" (o token pertence à categoria custom)
    rules = list_user_category_rules(pro_user_id)
    assert not any("namorada" in kw for kw, _ in rules)

    # e um gasto com namorada cai na categoria custom, não em lazer
    res = infer_category(pro_user_id, "gastei 400 com minha namorada", allow_ai=False)
    assert res.category == "gastos com minha namorada"
    assert res.reason == "user_category"


def test_guard_nao_bloqueia_token_neutro(pro_user_id):
    # O guard é preciso: "cinema" não é token de nenhuma categoria custom e é
    # canonicamente lazer → continua sendo aprendido normalmente.
    create_user_category(pro_user_id, "gastos com minha namorada")
    learn_from_inference(pro_user_id, "fui no cinema", "lazer", reason="local_rule")
    assert db.get_memorized_category(pro_user_id, "fui no cinema") == "lazer"


def test_list_launches_com_categoria_filtra_pela_custom(pro_user_id):
    # "me liste os gastos com namorada" caía em launches.list e mostrava os
    # últimos N de TODAS as categorias. Agora resolve a categoria custom e
    # responde só o gasto dela (BUG 1).
    create_user_category(pro_user_id, "gastos com minha namorada")
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 111, "cinema", None,
        categoria="gastos com minha namorada",
    )
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 50, "mercado", None, categoria="mercado",
    )

    resp = list_launches(pro_user_id, original_text="me liste os gastos com namorada")

    assert "111" in resp                     # o gasto da categoria aparece
    assert "namorada" in resp.lower()        # respondeu sobre a categoria certa
    assert "mercado" not in resp.lower()     # gasto de outra categoria não vaza
    assert "Últimos" not in resp             # não é a listagem geral


def test_list_launches_nao_categoria_cai_na_geral(pro_user_id):
    # "no cartão" casa o regex com/no/na mas "cartao" NÃO é categoria (nem
    # sistema nem custom) → não deve delegar pra spend_query e responder
    # "não teve gastos em cartao", escondendo os lançamentos reais. Cai na
    # listagem geral (comportamento pré-fix).
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 50, "mercado", None, categoria="mercado",
    )
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 30, "uber", None, categoria="transporte",
    )
    resp = list_launches(pro_user_id, original_text="liste os gastos no cartão")
    assert "Últimos" in resp
    assert "mercado" in resp.lower()
    assert "não teve gastos" not in resp.lower()


def test_list_launches_frase_solta_nao_vira_pseudo_categoria(pro_user_id):
    # "com a família toda" → "a familia toda" não é categoria → listagem geral.
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 50, "mercado", None, categoria="mercado",
    )
    resp = list_launches(pro_user_id, original_text="liste os gastos com a família toda")
    assert "Últimos" in resp
    assert "não teve gastos" not in resp.lower()


def test_spend_query_sistema_vence_custom_homonima(pro_user_id):
    # Regressão: custom "saúde da minha mãe" (token "saude") sombreava a
    # categoria de SISTEMA "saúde". "quanto gastei com saúde" deve somar a saúde
    # do sistema (>0), não a custom (que fica em R$ 0).
    create_user_category(pro_user_id, "saúde da minha mãe")
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 240, "consulta", None, categoria="saúde",
    )
    resp = spend_query(pro_user_id, "quanto gastei com saúde")
    assert "240" in resp
    assert "não teve gastos" not in resp.lower()


def test_list_launches_sem_categoria_lista_geral(pro_user_id):
    # sem categoria mencionada, segue a listagem geral (não delega)
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 50, "mercado", None, categoria="mercado",
    )
    resp = list_launches(pro_user_id, original_text="meus lançamentos")
    assert "Últimos" in resp


def test_resolve_query_category_ignora_periodo(pro_user_id):
    # Codex P2: custom cujo token é palavra de período não pode sequestrar uma
    # pergunta de período. "quanto gastei esta semana" NÃO é a categoria custom
    # "fim de semana" — o match só vale no trecho da categoria (extracted), e
    # "semana" foi removido pelo _extract_query_category.
    from core.handlers.launches import _resolve_query_category
    create_user_category(pro_user_id, "fim de semana")
    assert _resolve_query_category(pro_user_id, "quanto gastei esta semana") is None
    # mas mencionar a categoria explicitamente ainda resolve
    assert _resolve_query_category(pro_user_id, "quanto gastei com fim de semana") == "fim de semana"


def test_resolve_query_category_exato_vence_fuzzy(pro_user_id):
    # Codex: com customs sobrepostas, "cachorro" deve resolver pra a categoria
    # EXATA "cachorro", não pra "cachorro do vizinho" (que o fuzzy pegaria por
    # empate + ordenação length desc).
    from core.handlers.launches import _resolve_query_category
    create_user_category(pro_user_id, "cachorro")
    create_user_category(pro_user_id, "cachorro do vizinho")
    assert _resolve_query_category(pro_user_id, "quanto gastei com cachorro") == "cachorro"
    # e mencionar a mais específica ainda resolve ela (tem 2 tokens no texto)
    assert _resolve_query_category(
        pro_user_id, "quanto gastei com cachorro do vizinho"
    ) == "cachorro do vizinho"


def test_spend_query_honra_date_filter_resolvido(pro_user_id):
    # Codex P2: após a clarificação ("gastos com saúde dia 4" + "abril"), o router
    # grava a data ISO em entities e RE-EXECUTA com o original_text inalterado.
    # "dia 4" não é período pro parse textual, então sem ler entities a resposta
    # virava o total do MÊS todo em vez do dia pedido.
    from datetime import datetime, time, timedelta
    from utils_date import today_tz

    dia = today_tz().replace(day=4)
    if dia > today_tz():                       # dia 4 ainda não chegou neste mês
        dia = (dia.replace(day=1) - timedelta(days=1)).replace(day=4)
    outro = dia + timedelta(days=1)

    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 30, "consulta", None, categoria="saúde",
        criado_em=datetime.combine(dia, time(12, 0)),
    )
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 500, "exame", None, categoria="saúde",
        criado_em=datetime.combine(outro, time(12, 0)),
    )

    resp = spend_query(
        pro_user_id,
        "gastos com saúde dia 4",
        entities={"date_filter": dia.isoformat()},
    )
    assert "30,00" in resp          # só o dia pedido
    assert "530" not in resp        # não o mês inteiro


def test_spend_query_periodo_do_texto_vence_date_filter(pro_user_id):
    # Guarda da correção acima: date_filter é só FALLBACK. Se o texto traz um
    # período de verdade, ele vence — senão "em julho" (mês inteiro) colapsaria
    # no único dia que estiver em entities.
    from datetime import datetime, time, timedelta
    from utils_date import today_tz

    ref = today_tz().replace(day=15)
    if ref >= today_tz():
        ref = (ref.replace(day=1) - timedelta(days=1)).replace(day=15)
    mes_label = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
                 "agosto", "setembro", "outubro", "novembro", "dezembro"][ref.month - 1]

    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 70, "consulta", None, categoria="saúde",
        criado_em=datetime.combine(ref, time(12, 0)),
    )
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 90, "exame", None, categoria="saúde",
        criado_em=datetime.combine(ref + timedelta(days=1), time(12, 0)),
    )

    # entities aponta pra UM dia, mas o texto pede o mês → o mês vence
    resp = spend_query(
        pro_user_id,
        f"quanto gastei com saúde em {mes_label}",
        entities={"date_filter": ref.isoformat()},
    )
    assert "160,00" in resp


def _ruido_mais_recente(user_id, quando):
    """10 despesas de OUTRA categoria, MAIS RECENTES que a linha esperada.

    Obrigatório em todo teste desta seção: sem isso a listagem geral (o fallback
    COM o bug, que mostra os 10 últimos de tudo) exibiria a linha esperada por
    acaso e o teste ficaria verde com e sem a correção.
    """
    for i in range(10):
        db.add_launch_and_update_balance(
            user_id, "despesa", 10 + i, "mercado", None, categoria="mercado",
            criado_em=quando + timedelta(minutes=i + 1),
        )


def _hoje_as(hora: int):
    return datetime.combine(today_tz(), time(hora, 0))


def test_lista_receita_da_categoria(pro_user_id):
    # Codex P2 (`_responder_categoria`): a palavra de receita só desligava a
    # delegação e caía na listagem GERAL, que não filtra por tipo nem por
    # categoria — com 10 despesas mais recentes, a receita pedida sumia.
    base = _hoje_as(9)
    db.add_launch_and_update_balance(
        pro_user_id, "receita", 300, "dividendos itau", None,
        categoria="rendimentos", criado_em=base,
    )
    _ruido_mais_recente(pro_user_id, base)

    resp = list_launches(pro_user_id, original_text="liste as receitas em rendimentos")
    assert "300" in resp
    assert "Últimos" not in resp             # não é a listagem geral
    assert "mercado" not in resp.lower()     # outra categoria não vaza
    assert "não teve gastos" not in resp.lower()


def test_lista_receita_verbo_fora_do_regex(pro_user_id):
    # Codex P2 (`_PEDE_RECEITA_RE`): "caiu" é verbo de receita que o parser já
    # reconhece (RECEITA_START_VERBS) mas o regex de consulta não tinha → ia pro
    # spend_query expense-only e respondia "não teve gastos em rendimentos".
    base = _hoje_as(9)
    db.add_launch_and_update_balance(
        pro_user_id, "receita", 300, "dividendos itau", None,
        categoria="rendimentos", criado_em=base,
    )
    _ruido_mais_recente(pro_user_id, base)

    resp = list_launches(pro_user_id, original_text="o que caiu em rendimentos")
    assert "300" in resp
    assert "não teve gastos" not in resp.lower()
    assert "mercado" not in resp.lower()


def test_lista_receita_com_periodo(pro_user_id):
    # O branch de data tinha o mesmo buraco: get_launches_by_period não filtra
    # categoria, então "hoje" trazia TODAS as categorias do dia.
    base = _hoje_as(9)
    db.add_launch_and_update_balance(
        pro_user_id, "receita", 300, "dividendos itau", None,
        categoria="rendimentos", criado_em=base,
    )
    _ruido_mais_recente(pro_user_id, base)

    resp = list_launches(pro_user_id, original_text="liste as receitas em rendimentos hoje")
    assert "300" in resp
    assert "hoje" in resp.lower()
    assert "mercado" not in resp.lower()


def test_lista_neutro_mostra_os_dois_tipos(pro_user_id):
    # Sem palavra de tipo nenhuma o default é a LISTAGEM (os dois tipos), não o
    # spend_query expense-only. É a propriedade que faz a lista de palavras
    # deixar de ser load-bearing.
    base = _hoje_as(9)
    db.add_launch_and_update_balance(
        pro_user_id, "receita", 300, "dividendos itau", None,
        categoria="rendimentos", criado_em=base,
    )
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 40, "taxa custodia", None,
        categoria="rendimentos", criado_em=base,
    )
    _ruido_mais_recente(pro_user_id, base)

    resp = list_launches(pro_user_id, original_text="liste os lançamentos em rendimentos")
    assert "300" in resp
    assert "40,00" in resp
    assert "não teve gastos" not in resp.lower()
    assert "mercado" not in resp.lower()


def test_lista_neutro_categoria_vazia(pro_user_id):
    # Categoria existe mas não tem lançamento: a resposta fala em LANÇAMENTOS,
    # não em "gastos" (que mentiria sobre o que foi perguntado). Antes deste PR a
    # frase pra ESTA pergunta vinha do spend_query e era "Você não teve gastos em
    # lazer neste mês" — daí o `assert "não teve gastos" not in resp`.
    base = _hoje_as(9)
    _ruido_mais_recente(pro_user_id, base)

    resp = list_launches(pro_user_id, original_text="liste os lançamentos em lazer")
    assert "não teve lançamentos" in resp.lower()
    assert "lazer" in resp.lower()
    assert "não teve gastos" not in resp.lower()


def test_lista_receita_custom(pro_user_id):
    # Mesma regra vale pra categoria CUSTOM — user_categories não tem coluna de
    # tipo, quem tem tipo é o lançamento.
    create_user_category(pro_user_id, "freela")
    base = _hoje_as(9)
    db.add_launch_and_update_balance(
        pro_user_id, "receita", 900, "site do cliente", None,
        categoria="freela", criado_em=base,
    )
    _ruido_mais_recente(pro_user_id, base)

    resp = list_launches(pro_user_id, original_text="liste as receitas com freela")
    assert "900" in resp
    assert "Últimos" not in resp
    assert "mercado" not in resp.lower()


def test_spend_query_receita_nao_responde_gasto(pro_user_id):
    # spend_query é intent próprio, alcançável direto pelo roteador (o LLM manda
    # "quanto entrou em rendimentos" pra cá). Corrigir só o list_launches deixava
    # este irmão quebrado.
    base = _hoje_as(9)
    db.add_launch_and_update_balance(
        pro_user_id, "receita", 300, "dividendos itau", None,
        categoria="rendimentos", criado_em=base,
    )
    _ruido_mais_recente(pro_user_id, base)

    resp = spend_query(pro_user_id, "quanto entrou em rendimentos")
    assert "300" in resp
    assert "não teve gastos" not in resp.lower()


def test_lista_inclui_cartao(pro_user_id):
    # Excluir o cartão reproduziria o mesmo bug: categoria cujo gasto todo está no
    # crédito listaria vazio. Linha de cartão não tem user_seq → renderiza com 💳
    # e sem "#N" (o "#N" é o que o usuário digita em "apagar #N").
    card_id = db.create_card(pro_user_id, "Nubank", closing_day=10, due_day=17)
    db.add_credit_purchase(pro_user_id, card_id, 250, "saúde", "dentista", today_tz())
    _ruido_mais_recente(pro_user_id, _hoje_as(9))

    resp = list_launches(pro_user_id, original_text="liste os lançamentos em saúde")
    assert "250" in resp
    assert "💳" in resp
    assert "dentista" in resp.lower()


# --- rodada 7/8: o gate de tipo não pode ler o nome da categoria ---------

@pytest.mark.parametrize("frase,categoria,esperado", [
    # o nome da categoria fica FORA da detecção de tipo. "compras online" é
    # categoria de SISTEMA (`CATEGORY_LABELS`) e "compras" casava _PEDE_GASTO_RE
    # sobre o texto inteiro → toda pergunta por receita virava expense-only.
    ("liste as receitas em compras online",       "compras online",            "receita"),
    ("o que caiu em compras online",              "compras online",            "receita"),
    ("quanto entrou em compras online",           "compras online",            "receita"),
    ("liste as receitas em gastos com minha namorada",
                                                  "gastos com minha namorada", "receita"),
    ("o que recebi em despesas da empresa",       "despesas da empresa",       "receita"),
    ("quanto entrou em compras do mes",           "compras do mes",            "receita"),
    # as duas classes fora do nome da categoria → os dois tipos
    ("quero ver as receitas, nao os gastos, em compras online",
                                                  "compras online",            None),
    ("liste os gastos e as receitas em compras online",
                                                  "compras online",            None),
    ("liste os gastos e receitas em rendimentos", "rendimentos",               None),
    # RODADA 8 — a forma que o usuário digita: o nome da categoria SEM preposição
    # antes dele. O corte posicional (primeiro "com|em|no|na") cortava DENTRO do
    # nome e deixava o "gastos"/"ganhos" DO PRÓPRIO NOME do lado da pergunta →
    # tipo errado e metade do dinheiro sumia da resposta.
    ("gastos com minha namorada",                 "gastos com minha namorada", None),
    ("me mostra gastos com minha namorada",       "gastos com minha namorada", None),
    ("gastos com minha namorada esse mes",        "gastos com minha namorada", None),
    ("ganhos com meu freela",                     "ganhos com meu freela",     None),
    ("despesas com a casa",                       "despesas com a casa",       None),
    ("compras no mercado livre",                  "compras no mercado livre",  None),
    # mas a palavra que o usuário acrescentou FORA do nome continua valendo
    ("quanto gastei em gastos com minha namorada",
                                                  "gastos com minha namorada", "despesa"),
    ("quanto entrou em ganhos com meu freela",    "ganhos com meu freela",     "receita"),
    # ...INCLUSIVE quando ela é a MESMA palavra do nome. O corte por conjunto
    # de tokens apagava as duas ocorrências e a instrução do usuário sumia
    # junto com o rótulo (é a raiz dos dois P2 do Codex, aqui no eixo TIPO).
    ("liste os gastos em gastos com minha namorada",
                                                  "gastos com minha namorada", "despesa"),
    ("liste os ganhos em ganhos com meu freela",  "ganhos com meu freela",     "receita"),
    # RODADA 14 — e quando o usuário digita o nome INCOMPLETO ("namorada" em vez
    # de "minha namorada"), não existe trecho contíguo e cai no fallback. Ele
    # removia toda palavra do rótulo, inclusive a que só existe como PERGUNTA
    # nesta frase: some o único "gastos" e a listagem de despesa volta com
    # receita junto. Só sai do texto a palavra do nome que está no PEDIDO
    # (`_extract_query_category` → "namorada"/"freela").
    ("me liste os gastos com namorada",           "gastos com minha namorada", "despesa"),
    ("me liste os ganhos com freela",             "ganhos com meu freela",     "receita"),
    # O TETO da regra, fixado de propósito: tirando o "me liste os", sobra
    # exatamente o nome abreviado — e as duas leituras usam as MESMAS palavras.
    # Como só "namorada" resolveu a categoria, o "gastos" conta como pergunta e
    # o tipo vira despesa. O nome CRU E COMPLETO ("gastos com minha namorada",
    # a linha da RODADA 8 acima) segue neutro.
    ("gastos com namorada",                       "gastos com minha namorada", "despesa"),
    # e o que já funcionava continua
    ("quanto gastei em lazer",                    "lazer",                     "despesa"),
    ("liste os lancamentos em lazer",             "lazer",                     None),
])
def test_tipo_pedido_ignora_o_nome_da_categoria(frase, categoria, esperado):
    from core.handlers.launches import _tipo_pedido
    assert _tipo_pedido(frase, categoria) == esperado


@pytest.mark.parametrize("frase,categoria,esperado", [
    # eixo FORMATO, independente do tipo: quem pede número recebe número.
    ("quanto gastei em mercado",            "mercado",         True),
    ("quanto foi em mercado",               "mercado",         True),
    ("total em mercado",                    "mercado",         True),
    ("quanto gasteo em mercado",            "mercado",         True),   # typo não muda o escopo
    ("quanto entrou em rendimentos",        "rendimentos",     True),   # total DE RECEITA
    ("me mostra quanto gastei em mercado",  "mercado",         True),   # total vence lista
    # quem pede lista recebe lista
    ("me mostra os lancamentos em mercado", "mercado",         False),
    ("liste os gastos em mercado",          "mercado",         False),  # lista DE DESPESA
    ("extrato de mercado",                  "mercado",         False),
    ("gastos com minha namorada",           "gastos com minha namorada", False),
    # palavra de total que faz parte do NOME não pede total (mesma regra do tipo)
    ("gastos com total da obra",            "total da obra",   False),
    # ...mas a MESMA palavra escrita pelo usuário FORA do nome continua
    # pedindo o número. P2 do Codex: com o corte por conjunto de tokens os
    # DOIS "total" sumiam e o handler devolvia a LISTA pra quem pediu total.
    ("qual o total de gastos com total da obra", "total da obra", True),
    ("me mostra o total gasto com total da obra", "total da obra", True),
    # este já passava ANTES (o "quanto" não colide com nenhuma palavra do nome):
    # é guarda de que o corte por trecho não come a instrução, não discriminante.
    ("quanto foi o total da obra",          "total da obra",   True),
    # RODADA 14, o mesmo P2 no eixo FORMATO: nome digitado INCOMPLETO ("obra"
    # por "total da obra nova") → sem trecho contíguo → o fallback comia o
    # "total" que o usuário escreveu e devolvia LISTA pra quem pediu número.
    ("qual o total com obra",               "total da obra nova", True),
    # guarda, NÃO discriminante: palavra do rótulo que não está no texto não tem
    # ocorrência pra remover, então o fallback velho já acertava esta.
    ("liste os lancamentos com obra",       "total da obra",   False),
])
def test_pede_total_ignora_o_nome_da_categoria(frase, categoria, esperado):
    from core.handlers.launches import _pede_total
    assert _pede_total(frase, categoria) is esperado


@pytest.mark.parametrize("categoria,frase,custom", [
    # "compras online" é de SISTEMA (`CATEGORY_LABELS`) — não precisa de custom
    # nenhuma pra reproduzir. "gastos com minha namorada" é a categoria da queixa
    # original (docstring deste arquivo).
    ("compras online", "liste os lançamentos em compras online", False),
    ("gastos com minha namorada",
     "me mostra os lançamentos em gastos com minha namorada", True),
])
def test_nome_da_categoria_nao_esconde_receita(pro_user_id, categoria, frase, custom):
    # e2e do parametrize acima: unit verde com o handler chamando outra coisa não
    # provaria nada. Pergunta NEUTRA numa categoria cujo NOME tem palavra de
    # gasto: sem o corte do trecho, "compras"/"gastos" força expense-only e a
    # receita da categoria some.
    if custom:
        create_user_category(pro_user_id, categoria)
    base = _hoje_as(9)
    db.add_launch_and_update_balance(
        pro_user_id, "receita", 300, "estorno da loja", None,
        categoria=categoria, criado_em=base,
    )
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 40, "compra da loja", None,
        categoria=categoria, criado_em=base,
    )
    _ruido_mais_recente(pro_user_id, base)

    resp = list_launches(pro_user_id, original_text=frase)
    assert "300,00" in resp
    assert "40,00" in resp
    assert "não teve gastos" not in resp.lower()
    assert "mercado" not in resp.lower()


def test_palavra_de_total_do_usuario_sobrevive_ao_nome(pro_user_id):
    # P2 do Codex, ponta a ponta: "total" é o nome da categoria E o pedido do
    # usuário. Apagando os dois, o handler responde a LISTA pra quem pediu o
    # número. O e2e é necessário porque o unit acima prova o gate, não a resposta.
    create_user_category(pro_user_id, "total da obra")
    base = _hoje_as(9)
    for i, v in enumerate((100, 250)):
        db.add_launch_and_update_balance(
            pro_user_id, "despesa", v, f"material {i}", None,
            categoria="total da obra", criado_em=base + timedelta(minutes=i),
        )

    resp = list_launches(
        pro_user_id, original_text="qual o total de gastos com total da obra"
    )
    assert "Você gastou **R$ 350,00**" in resp      # formato TOTAL
    assert "🧾 **Lançamentos em" not in resp        # e não o cabeçalho da LISTA


@pytest.mark.parametrize("nome,frase,tipo_pedido", [
    ("gastos com minha namorada", "me liste os gastos com namorada",  "despesa"),
    ("ganhos com meu freela",     "me liste os ganhos com freela",    "receita"),
])
def test_nome_incompleto_nao_come_a_instrucao(pro_user_id, nome, frase, tipo_pedido):
    # RODADA 14, ponta a ponta. O usuário digita o nome PELA METADE — é o que ele
    # faz no WhatsApp: a categoria se chama "gastos com minha namorada" e ele
    # escreve "com namorada". Sem trecho contíguo, o fallback removia toda
    # palavra do rótulo e levava junto o único "gastos" da frase, que era a
    # INSTRUÇÃO dele → `_tipo_pedido` None → a lista de despesa vinha com a
    # receita junto (e a de receita, com a despesa).
    create_user_category(pro_user_id, nome)
    base = _hoje_as(9)
    db.add_launch_and_update_balance(
        pro_user_id, "receita", 300, "estorno da loja", None,
        categoria=nome, criado_em=base,
    )
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 40, "compra da loja", None,
        categoria=nome, criado_em=base,
    )
    _ruido_mais_recente(pro_user_id, base)

    resp = list_launches(pro_user_id, original_text=frase)
    dentro, fora = ("40,00", "300,00") if tipo_pedido == "despesa" else ("300,00", "40,00")
    assert dentro in resp
    assert fora not in resp
    # e a categoria certa foi resolvida (não caiu na listagem geral, que traria
    # o ruído de mercado)
    assert "mercado" not in resp.lower()


@pytest.mark.parametrize("nome", [
    "fim de semana",            # P2 do Codex: "semana" → semana corrente
    "festa junina de julho",    # irmão: nome de mês → julho inteiro
    "reforma do mes passado",   # irmão: "mes passado" → mês anterior
])
def test_nome_da_categoria_nao_vira_filtro_de_data(pro_user_id, nome):
    # P2 do Codex, a metade CALADA da mesma raiz: `_resolve_period` lia o texto
    # CRU, então a palavra de período que faz parte do NOME virava janela e
    # lançamento antigo sumia da lista sem uma linha de aviso. É a rodada 3
    # espelhada — lá o período roubava a categoria, aqui a categoria rouba o
    # período.
    create_user_category(pro_user_id, nome)
    base = _hoje_as(9)
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 4000, "churrasco antigo", None,
        categoria=nome, criado_em=base - timedelta(days=90),
    )
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 77, "pizza de hoje", None,
        categoria=nome, criado_em=base,
    )
    _ruido_mais_recente(pro_user_id, base)

    resp = list_launches(pro_user_id, original_text=f"liste os lançamentos em {nome}")
    assert "churrasco antigo" in resp       # 90 dias atrás: fora de QUALQUER
    assert "4.000,00" in resp               # janela que o nome sugeriria
    assert "pizza de hoje" in resp
    assert "(de sempre)" in resp            # e o escopo anunciado é "sem janela"


def test_periodo_do_nome_nao_encolhe_o_total(pro_user_id):
    # o mesmo roubo de janela no caminho de TOTAL (`_total_categoria`), onde ele
    # não esconde linha: esconde dinheiro dentro de um número só.
    create_user_category(pro_user_id, "fim de semana")
    hoje = today_tz()
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 4000, "churrasco do dia 1", None,
        categoria="fim de semana",
        criado_em=datetime.combine(hoje.replace(day=1), time(0, 5)),
    )
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 77, "pizza de hoje", None,
        categoria="fim de semana", criado_em=_hoje_as(9),
    )

    resp = spend_query(pro_user_id, "quanto gastei em fim de semana")
    # o rótulo é o discriminante que vale em QUALQUER dia do mês: com o texto
    # cru o "semana" do nome dava "esta semana"; o default do caminho de total é
    # o mês corrente.
    assert "neste mês" in resp
    # "semana" solto não serve: o NOME da categoria sai no rótulo da resposta
    # ("em **fim de semana**"). O que não pode aparecer é a JANELA.
    assert "esta semana" not in resp
    # e o número do mês inteiro (nos dias em que a semana corrente não começa no
    # dia 1, este é também o que a janela roubada escondia)
    assert "R$ 4.077,00" in resp


def test_lista_nao_vaza_entre_usuarios(pro_user_id):
    # CLAUDE.md §5: isolamento por usuário é regra dura. A listagem nova monta SQL
    # com union all — duas pernas, dois `where user_id`.
    # O fixture `user_id` NÃO serve de segundo usuário: `pro_user_id` depende dele
    # e os dois são o MESMO id (fixture `pro_user_id` em tests/conftest.py).
    import uuid as _uuid
    vizinho = int(_uuid.uuid4().int % 10_000_000_000)
    db.ensure_user(vizinho)

    base = _hoje_as(9)
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 11, "meu cinema", None,
        categoria="lazer", criado_em=base,
    )
    db.add_launch_and_update_balance(
        vizinho, "despesa", 8888, "cinema do vizinho", None,
        categoria="lazer", criado_em=base,
    )
    card_vizinho = db.create_card(vizinho, "Nubank", closing_day=28, due_day=5)
    db.add_credit_purchase(vizinho, card_vizinho, 7777, "lazer", "show do vizinho", today_tz())

    resp = list_launches(pro_user_id, original_text="liste os lançamentos em lazer")
    assert "11,00" in resp
    assert "8.888,00" not in resp
    assert "7.777,00" not in resp           # a perna do cartão tem seu próprio where
    assert "vizinho" not in resp
    assert "💸 Gastos: R$ 11,00" in resp     # nem no sumário


def test_pede_os_dois_tipos_mostra_os_dois(pro_user_id):
    # BLOQUEIA #2: "gasto vence" respondia só R$ 40,00 e sumia com os R$ 300,00.
    # Os dois asserts juntos ficam VERMELHOS nas duas precedências erradas
    # (gasto-primeiro perde os 300; receita-primeiro perde os 40).
    base = _hoje_as(9)
    db.add_launch_and_update_balance(
        pro_user_id, "receita", 300, "dividendos itau", None,
        categoria="rendimentos", criado_em=base,
    )
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 40, "taxa custodia", None,
        categoria="rendimentos", criado_em=base,
    )
    _ruido_mais_recente(pro_user_id, base)

    resp = list_launches(pro_user_id, original_text="liste os gastos e receitas em rendimentos")
    assert "300,00" in resp
    assert "40,00" in resp
    assert "💰 Receitas: R$ 300,00" in resp
    assert "💸 Gastos: R$ 40,00" in resp


def test_um_tipo_pedido_exclui_o_outro(pro_user_id):
    # O filtro `tipo` tem que chegar na SQL: com receita E despesa na mesma
    # categoria, pedir receita não pode trazer a despesa nem somá-la no rodapé.
    base = _hoje_as(9)
    db.add_launch_and_update_balance(
        pro_user_id, "receita", 300, "dividendos itau", None,
        categoria="rendimentos", criado_em=base,
    )
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 40, "taxa custodia", None,
        categoria="rendimentos", criado_em=base,
    )
    _ruido_mais_recente(pro_user_id, base)

    resp = list_launches(pro_user_id, original_text="liste as receitas em rendimentos")
    assert "300,00" in resp
    assert "40,00" not in resp            # a despesa não vaza na lista
    assert "taxa custodia" not in resp
    assert "Gastos:" not in resp          # nem no sumário


def test_periodo_exclui_linha_de_fora(pro_user_id):
    # A janela tem que chegar na SQL: a linha do mês passado não pode aparecer
    # numa pergunta com "hoje".
    base = _hoje_as(9)
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 77, "cinema", None,
        categoria="lazer", criado_em=base,
    )
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 4000, "viagem antiga", None,
        categoria="lazer", criado_em=base - timedelta(days=40),
    )
    _ruido_mais_recente(pro_user_id, base)

    resp = list_launches(pro_user_id, original_text="liste os lançamentos em lazer hoje")
    assert "77,00" in resp
    assert "4.000,00" not in resp
    assert "viagem antiga" not in resp
    assert "💸 Gastos: R$ 77,00" in resp   # o sumário respeita a mesma janela


def test_corte_de_20_anuncia_e_nao_mente_no_total(pro_user_id):
    # BLOQUEIA #3: 25 despesas de R$ 112,00 = R$ 2.800,00. Somar só as 20 linhas
    # exibidas dava "💸 Gastos: R$ 2.240,00" com rótulo de total, discordando do
    # "quanto gastei em lazer" (spend_query) pra mesma pergunta.
    base = _hoje_as(9)
    for i in range(25):
        db.add_launch_and_update_balance(
            pro_user_id, "despesa", 112, f"lazer-{i:02d}", None,
            categoria="lazer", criado_em=base + timedelta(minutes=i),
        )

    resp = list_launches(pro_user_id, original_text="liste os lançamentos em lazer")
    assert "💸 Gastos: R$ 2.800,00" in resp          # total REAL, não o das 20
    assert "mostrando os 20 mais recentes de 25" in resp
    assert "lazer-24" in resp                        # ordem desc: o mais novo entra
    assert "lazer-00" not in resp                    # o mais velho é o cortado

    # e a pergunta "de total" pra mesma categoria dá o MESMO número
    assert "2.800,00" in spend_query(pro_user_id, "quanto gastei em lazer")


def test_spend_query_neutro_nao_esconde_receita(pro_user_id):
    # spend_query também é alcançável direto ("quanto foi em rendimentos" cai
    # aqui). A guarda tem que ser `tipo != 'despesa'`: trocada por
    # `tipo == 'receita'`, o neutro volta pro caminho expense-only.
    base = _hoje_as(9)
    db.add_launch_and_update_balance(
        pro_user_id, "receita", 300, "dividendos itau", None,
        categoria="rendimentos", criado_em=base,
    )
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 40, "taxa custodia", None,
        categoria="rendimentos", criado_em=base,
    )

    resp = spend_query(pro_user_id, "quanto foi em rendimentos")
    assert "300,00" in resp
    assert "💰 Receitas: R$ 300,00" in resp


def _insere_launch_cru(user_id, tipo, valor, categoria, nota, criado_em):
    """Insert direto: add_launch_and_update_balance rejeita tipo != despesa/receita
    (`add_launch_and_update_balance`), e estes testes precisam dos outros tipos."""
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "insert into launches(user_id, tipo, valor, alvo, nota, categoria, criado_em) "
                "values (%s,%s,%s,%s,%s,%s,%s)",
                (user_id, tipo, valor, nota, None, categoria, criado_em),
            )
        conn.commit()


def test_lista_nao_mostra_tipo_interno(pro_user_id):
    # criar_caixinha & cia existem em launches pra rollback/auditoria e não são
    # movimentação — o filtro tem que estar na SQL (mesmo _INTERNAL_TIPOS da
    # listagem geral).
    base = _hoje_as(9)
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 77, "cinema", None,
        categoria="lazer", criado_em=base,
    )
    _insere_launch_cru(pro_user_id, "criar_caixinha", 9999, "lazer", "caixinha viagem", base)

    resp = list_launches(pro_user_id, original_text="liste os lançamentos em lazer")
    assert "77,00" in resp
    assert "9.999,00" not in resp
    assert "caixinha viagem" not in resp


def test_lista_inclui_tipo_legado_entrada(pro_user_id):
    # Nenhum writer atual escreve 'entrada', mas muito read path de produção
    # ainda trata o valor (ver o comentário de `_TIPO_ALIASES`, db/accounts.py —
    # sem contagem, que é número que ninguém reproduz). Filtrar só
    # `tipo = 'receita'` deixava a linha legada invisível na lista E fora do
    # sumário — dinheiro sumido sem aviso.
    base = _hoje_as(9)
    _insere_launch_cru(pro_user_id, "entrada", 500, "rendimentos", "juros antigos", base)

    resp = list_launches(pro_user_id, original_text="liste as receitas em rendimentos")
    assert "500,00" in resp
    assert "💰 Receitas: R$ 500,00" in resp


def test_cartao_entra_pelo_mes_da_fatura(pro_user_id):
    # Mesma regra do dashboard e do sum_spent_in_category_period: a compra conta
    # no mês da FATURA, não no dia da compra. closing_day escolhido pra que o
    # period_end nunca caia em hoje (se caísse, ref == fechamento e a fatura
    # fecharia hoje mesmo).
    hoje = today_tz()
    closing = 1 if hoje.day != 1 else 2
    card_id = db.create_card(pro_user_id, "Nubank", closing_day=closing, due_day=17)
    db.add_credit_purchase(pro_user_id, card_id, 250, "saúde", "dentista", hoje)
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 33, "farmacia", None,
        categoria="saúde", criado_em=_hoje_as(9),
    )

    resp = list_launches(pro_user_id, original_text="liste os lançamentos em saúde hoje")
    assert "33,00" in resp                 # o lançamento de hoje entra
    assert "250,00" not in resp            # a compra no crédito é da fatura seguinte
    assert "💸 Gastos: R$ 33,00" in resp


def test_list_launches_gasto_em_categoria_ainda_delega(pro_user_id):
    # Guarda da correção acima: a palavra de receita não pode desligar a
    # delegação para perguntas de GASTO — senão volta o bug original
    # (listar os últimos 10 de tudo em vez da categoria pedida).
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 80, "farmacia", None, categoria="saúde",
    )
    resp = list_launches(pro_user_id, original_text="liste os gastos com saúde")
    assert "80" in resp
    assert "Últimos" not in resp        # foi pro caminho categoria-aware


def test_compra_natural_credito_usa_categoria_custom(pro_user_id):
    card_id = db.create_card(pro_user_id, "Nubank", closing_day=10, due_day=17)
    db.set_default_card(pro_user_id, card_id)
    create_user_category(pro_user_id, "gastos com minha namorada")

    msg = try_handle_natural_credit_purchase(
        pro_user_id,
        "gastei 400 no crédito com minha namorada",
    )

    assert msg is not None
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select categoria, nota from credit_transactions "
                "where user_id=%s order by id desc limit 1",
                (pro_user_id,),
            )
            row = cur.fetchone()

    assert row["categoria"] == "gastos com minha namorada"
    assert row["nota"] == "minha namorada"


# --- rodada 8: e2e dos dois eixos ----------------------------------------

@pytest.mark.parametrize("frase", [
    "gastos com minha namorada",                # o nome CRU, sem preposição antes
    "me mostra gastos com minha namorada",
    "gastos com minha namorada esse mes",
])
def test_nome_cru_da_categoria_nao_esconde_receita(pro_user_id, frase):
    # BLOQUEANTE 1 da rodada 8. A queixa original do cliente: R$ 700,00 de receita
    # e R$ 20,00 de despesa numa categoria chamada "gastos com minha namorada".
    # Com o corte POSICIONAL, "gastos com minha namorada" cortava no "com" de
    # DENTRO do nome, sobrava "gastos" do lado da pergunta, o tipo virava despesa
    # e a resposta era "💸 Você gastou R$ 20,00" — os R$ 700,00 sumiam.
    # Os 8 casos da rodada 7 não pegavam isto: todos tinham preposição ANTES da
    # categoria ("... em gastos com minha namorada"), que não é como se digita.
    create_user_category(pro_user_id, "gastos com minha namorada")
    base = _hoje_as(9)
    db.add_launch_and_update_balance(
        pro_user_id, "receita", 700, "presente devolvido", None,
        categoria="gastos com minha namorada", criado_em=base,
    )
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 20, "cinema", None,
        categoria="gastos com minha namorada", criado_em=base,
    )
    _ruido_mais_recente(pro_user_id, base)

    resp = list_launches(pro_user_id, original_text=frase)
    assert "700,00" in resp
    assert "20,00" in resp
    assert "Você gastou" not in resp          # não é resposta de total expense-only
    assert "mercado" not in resp.lower()      # nem a listagem geral


def test_nome_cru_da_categoria_nao_esconde_despesa(pro_user_id):
    # O espelho: nome com palavra de RECEITA esconde a DESPESA. Sem o espelho, um
    # fix que só tratasse "gastos" ficaria verde com metade da classe aberta.
    create_user_category(pro_user_id, "ganhos com meu freela")
    base = _hoje_as(9)
    db.add_launch_and_update_balance(
        pro_user_id, "receita", 900, "site do cliente", None,
        categoria="ganhos com meu freela", criado_em=base,
    )
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 30, "dominio", None,
        categoria="ganhos com meu freela", criado_em=base,
    )
    _ruido_mais_recente(pro_user_id, base)

    resp = list_launches(pro_user_id, original_text="ganhos com meu freela")
    assert "900,00" in resp
    assert "30,00" in resp                    # a despesa não some da lista
    assert "💸 Gastos: R$ 30,00" in resp      # nem do sumário
    assert "mercado" not in resp.lower()


def test_total_de_categoria_de_receita_nao_responde_gasto(pro_user_id):
    # BLOQUEANTE 2. O caminho de TOTAL era o spend_query, expense-only: mandar
    # "total em rendimentos" pra lá reabria o bug original ("você não teve gastos
    # em rendimentos" com a receita no banco). O total honra o eixo TIPO.
    base = _hoje_as(9)
    db.add_launch_and_update_balance(
        pro_user_id, "receita", 900, "dividendos itau", None,
        categoria="rendimentos", criado_em=base,
    )

    resp = spend_query(pro_user_id, "total em rendimentos")
    assert "900,00" in resp
    assert "💰 Receitas: R$ 900,00" in resp
    assert "não teve gastos" not in resp.lower()
    assert "neste mês" in resp                # ESCOPO no rótulo, obrigatório


def test_total_tem_sempre_o_mesmo_escopo(pro_user_id):
    # BLOQUEANTE 2. R$ 50,00 hoje e R$ 9.000,00 há 200 dias: "quanto gastei"
    # respondia o mês (R$ 50,00, com escopo) e "quanto foi"/"total" respondia 200
    # dias (R$ 9.050,00, SEM escopo) — mesmo rótulo "💸 Gastos:", 180x de
    # diferença, escolhido por uma palavra que o usuário não sabe que é gate.
    base = _hoje_as(9)
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 50, "feira", None,
        categoria="mercado", criado_em=base,
    )
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 9000, "estoque antigo", None,
        categoria="mercado", criado_em=base - timedelta(days=200),
    )

    for frase in ("quanto gastei em mercado", "quanto foi em mercado",
                  "total em mercado", "quanto gasteo em mercado"):
        resp = spend_query(pro_user_id, frase)
        assert "50,00" in resp, frase
        assert "9.050,00" not in resp, frase   # o total de 200 dias não se disfarça
        assert "neste mês" in resp, frase      # e o escopo está escrito


def test_lista_sem_periodo_anuncia_o_escopo(pro_user_id):
    # O outro lado do eixo FORMATO: a LISTA não tem janela (decisão do dono), e é
    # justamente por isso que o sumário dela precisa dizer "de sempre" — senão é
    # o mesmo número sem escopo, só que embaixo de uma lista.
    base = _hoje_as(9)
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 50, "feira", None,
        categoria="mercado", criado_em=base,
    )
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 9000, "estoque antigo", None,
        categoria="mercado", criado_em=base - timedelta(days=200),
    )

    resp = list_launches(pro_user_id, original_text="me mostra os lançamentos em mercado")
    assert "💸 Gastos: R$ 9.050,00 (de sempre)" in resp
    # e com período, o escopo já está no cabeçalho — não repete
    resp_hoje = list_launches(pro_user_id, original_text="me mostra os lançamentos em mercado hoje")
    assert "💸 Gastos: R$ 50,00" in resp_hoje
    assert "de sempre" not in resp_hoje


def test_limit_pedido_vale_na_listagem_de_categoria(pro_user_id):
    # NÃO-BLOQUEANTE 3: entities["limit"] era descartado no caminho de categoria e
    # o corte ficava sempre em 20 — parede de texto no WhatsApp pra quem pediu 3.
    base = _hoje_as(9)
    for i in range(25):
        db.add_launch_and_update_balance(
            pro_user_id, "despesa", 112, f"lazer-{i:02d}", None,
            categoria="lazer", criado_em=base + timedelta(minutes=i),
        )

    resp = list_launches(
        pro_user_id, limit=3, entities={"limit": 3},
        original_text="me mostra os lançamentos em lazer",
    )
    assert "mostrando os 3 mais recentes de 25" in resp
    assert "lazer-24" in resp
    assert "lazer-21" not in resp
    assert "💸 Gastos: R$ 2.800,00 (de sempre)" in resp   # o total continua o REAL

    # sem pedido explícito, o default do caminho de categoria segue 20 — e NÃO o
    # 10 que o roteador usa na listagem geral (ele não distingue "pediu 10" de
    # "ninguém pediu nada").
    resp20 = list_launches(
        pro_user_id, limit=10, entities={},
        original_text="me mostra os lançamentos em lazer",
    )
    assert "mostrando os 20 mais recentes de 25" in resp20

    # lixo do LLM não derruba o handler
    for lixo in ("tres", None, 99999):
        assert "lazer-24" in list_launches(
            pro_user_id, entities={"limit": lixo},
            original_text="me mostra os lançamentos em lazer",
        )


def test_periodo_nao_vira_categoria_com_custom_homonima(pro_user_id):
    # NÃO-BLOQUEANTE 4: o fix da rodada 5 (custom_category_match no TRECHO, não no
    # texto inteiro) não tinha teste que ficasse vermelho — em "quanto gastei esta
    # semana" o `if not extracted: return None` cortava antes de chegar no fuzzy.
    # Este input chega lá: extrai "pizza" (categoria nenhuma), e o texto INTEIRO
    # tem "semana", token distintivo da custom "fim de semana".
    from core.handlers.launches import _resolve_query_category
    create_user_category(pro_user_id, "fim de semana")
    assert _resolve_query_category(pro_user_id, "quanto gastei com pizza esta semana") is None
    # e a categoria de verdade continua resolvendo
    assert _resolve_query_category(
        pro_user_id, "quanto gastei com fim de semana"
    ) == "fim de semana"


def test_tipo_desconhecido_no_db_nao_esconde_dinheiro(pro_user_id):
    # NÃO-BLOQUEANTE 7: `_TIPO_ALIASES.get(tipo, (tipo,))` filtrava por um valor
    # que não existe na coluna → lista vazia e n_total=0, com cara de "não tem
    # nada aqui". Numa camada chamável por outro handler, degradar pra VAZIO num
    # caminho de dinheiro é a pior saída; degradar pra "os dois" só mostra a mais.
    base = _hoje_as(9)
    db.add_launch_and_update_balance(
        pro_user_id, "receita", 300, "dividendos itau", None,
        categoria="rendimentos", criado_em=base,
    )
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 40, "taxa custodia", None,
        categoria="rendimentos", criado_em=base,
    )

    _, resumo = db.list_launches_by_category(pro_user_id, "rendimentos", tipo="xyz")
    assert resumo["n_total"] == 2
    assert resumo["receita"] == 300 and resumo["despesa"] == 40

    # e a variação de caixa passa a filtrar de verdade em vez de zerar
    _, maiusc = db.list_launches_by_category(pro_user_id, "rendimentos", tipo="DESPESA")
    assert maiusc["n_total"] == 1 and maiusc["despesa"] == 40


# --- rodada 9: rótulo com underscore, vazio que diz o tipo, interno que explica ---

# Os CINCO rótulos canônicos com underscore de `CATEGORY_LABELS`. A lista está
# escrita à mão de propósito: se um sexto aparecer, este teste NÃO quebra sozinho
# — o `test_os_cinco_rotulos_com_underscore_estao_cobertos` abaixo é quem falha,
# apontando pro lugar certo.
_ROTULOS_UNDERSCORE = [
    "pagamento_fatura",
    "investimento_aporte",
    "investimento_resgate",
    "transferencia_interna",
    "ajuste_saldo",
]


def test_os_cinco_rotulos_com_underscore_estao_cobertos():
    # Guarda da guarda: o parametrize abaixo cobre "os rótulos com underscore",
    # não "cinco exemplares". Se `CATEGORY_LABELS` ganhar outro, isto acusa.
    from utils_text import CATEGORY_LABELS
    assert sorted(v for v in set(CATEGORY_LABELS.values()) if "_" in v) == sorted(
        _ROTULOS_UNDERSCORE
    )


@pytest.mark.parametrize("rotulo", _ROTULOS_UNDERSCORE)
@pytest.mark.parametrize("forma", ["underscore", "espaco"])
def test_nome_com_underscore_sai_do_texto_da_pergunta(rotulo, forma):
    # BLOQUEANTE B1: `_fora_do_nome_da_categoria` montava o conjunto de tokens do
    # nome com `.split()`, e o rótulo canônico com underscore é UM token só —
    # nenhuma palavra do nome era removida em NENHUM dos cinco. Mas
    # `canonicalize_category_label` aceita de propósito a forma digitada com
    # espaço ("Aceita rótulos digitados com espaço", utils_text.py), que é a
    # forma que o usuário escreve. Contratos contraditórios.
    from core.handlers.launches import _fora_do_nome_da_categoria
    escrito = rotulo if forma == "underscore" else rotulo.replace("_", " ")
    frase = f"liste os lancamentos em {escrito}"
    fora = _fora_do_nome_da_categoria(frase, rotulo).split()
    # nenhuma palavra do NOME sobrevive do lado da pergunta
    for palavra in rotulo.split("_"):
        assert palavra not in fora, f"{rotulo} ({forma}): '{palavra}' sobreviveu em {fora}"


def test_pagamento_de_fatura_nao_vira_pergunta_de_gasto():
    # O rótulo em que o B1 tinha consequência de comportamento, não só de tokens:
    # "pagamento" é palavra de `_PEDE_GASTO_RE`, então o NOME da categoria fazia
    # uma pergunta NEUTRA virar expense-only — exatamente a classe que os dois
    # eixos existem pra matar.
    from core.handlers.launches import _tipo_pedido, _extract_query_category
    frase = "liste os lancamentos em pagamento de fatura"
    # e o pipeline real chega no rótulo canônico a partir da forma com espaço
    assert _extract_query_category(frase) == "pagamento_fatura"
    assert _tipo_pedido(frase, "pagamento_fatura") is None
    # a pergunta de gasto de verdade continua sendo lida como gasto
    assert _tipo_pedido("quanto gastei em pagamento de fatura", "pagamento_fatura") == "despesa"


def test_lista_vazia_diz_qual_tipo_filtrou(pro_user_id):
    # BLOQUEANTE B3 — e BLOQUEANTE B6 na forma do teste. A garantia é
    # CONTRASTIVA ("a palavra acompanha o tipo que a query filtrou") e NENHUM dos
    # três casos a prova sozinho. O caso 1 em especial não pode: pediu gasto, não
    # tem gasto, resposta certa = "gastos" — que é LITERALMENTE o que o código
    # antigo respondia pra qualquer pergunta ("Você não teve gastos em X"). Como
    # param separado ele passava com o B3 revertido, verde por construção.
    # Junto dos outros dois num só corpo, a redação fixa fica vermelha.
    base = _hoje_as(9)
    db.add_launch_and_update_balance(
        pro_user_id, "receita", 1200, "dividendos", None,
        categoria="rendimentos", criado_em=base,
    )
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 80, "feira", None,
        categoria="mercado", criado_em=base,
    )

    PALAVRAS = {"gastos", "receitas", "lançamentos"}
    casos = [
        # (frase, palavra esperada, dinheiro do OUTRO tipo que não pode vazar)
        ("liste os gastos em rendimentos", "gastos", "1.200,00"),
        ("liste as receitas em mercado", "receitas", "80,00"),
        ("liste os lancamentos em beleza", "lançamentos", None),  # vazia de verdade
    ]
    for frase, esperado, nao_pode_vazar in casos:
        resp = list_launches(pro_user_id, original_text=frase).lower()
        assert esperado in resp, (frase, resp)
        for outra in PALAVRAS - {esperado}:
            assert outra not in resp, (frase, outra, resp)
        if nao_pode_vazar:
            assert nao_pode_vazar not in resp, (frase, resp)


def test_total_em_categoria_interna_explica_em_vez_de_negar(pro_user_id):
    # BLOQUEANTE B2 (decisão do dono): `sum_spent_in_category_period` filtra
    # `is_internal_movement` (certo — pagar fatura não é gasto novo) e a lista
    # não filtra (certo — senão "liste os lançamentos em investimento_aporte"
    # vem vazio). Medido antes: R$ 3.000,00 de HOJE em pagamento_fatura faziam
    # "quanto gastei" responder "você não teve gastos" e "liste" mostrar os
    # R$ 3.000,00 — mesma janela, negação contra afirmação.
    # `is_internal_movement` explícito: `add_launch_and_update_balance` NÃO deriva
    # a flag da categoria (default False) — quem deriva é o chamador, com
    # `is_internal_category` (`parse_receita_despesa_natural`, parsers.py). Sem
    # passar aqui, a linha entraria
    # como gasto normal e o teste ficaria verde sem exercitar nada.
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 3000, "fatura nubank", None,
        categoria="pagamento_fatura", criado_em=_hoje_as(9),
        is_internal_movement=True,
    )

    total = spend_query(pro_user_id, "quanto gastei em pagamento de fatura")
    assert "3.000,00" in total, total
    assert "não teve gastos" not in total.lower(), total
    assert "movimenta" in total.lower(), total

    # e o mesmo número da LISTA, que é o ponto: as duas respostas param de
    # se contradizer
    lista = list_launches(pro_user_id, original_text="liste os lancamentos em pagamento de fatura")
    assert "3.000,00" in lista


def test_categoria_interna_vazia_continua_negando(pro_user_id):
    # A explicação do B2 só vale quando houve movimento. Zero movimentado →
    # mensagem de vazio normal, senão "🔁 R$ 0,00 movimentados" viraria ruído.
    resp = spend_query(pro_user_id, "quanto gastei em pagamento de fatura")
    assert "não teve gastos" in resp.lower(), resp
    assert "movimenta" not in resp.lower(), resp


def test_categoria_normal_zerada_nao_ganha_a_explicacao(pro_user_id):
    # E a explicação NÃO pode vazar pra categoria comum: "quanto gastei em lazer"
    # sem nada em lazer continua sendo "você não teve gastos".
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 50, "pao", None,
        categoria="mercado", criado_em=_hoje_as(9),
    )
    resp = spend_query(pro_user_id, "quanto gastei em lazer")
    assert "não teve gastos" in resp.lower(), resp
    assert "movimenta" not in resp.lower(), resp


def test_dois_eixos_e2e_com_nome_que_contem_palavra_de_total(pro_user_id):
    # Os units dos dois eixos recebem `categoria` NA MÃO — nada ali prova que o
    # pipeline real (`_extract_query_category` → `_resolve_query_category`)
    # produz aquela categoria pra aquela frase. Este é o caso mais hostil dos
    # dois eixos de uma vez, e os dois têm resposta DIFERENTE aqui:
    #
    #   FORMATO: "total" faz parte do NOME → NÃO pede total. Resposta = LISTA.
    #   TIPO:    "gastos" está FORA do nome ("total da obra") → é palavra que o
    #            usuário escreveu como pergunta. Resposta = DESPESA.
    #
    # É a diferença pra "gastos com minha namorada", onde a frase inteira É o
    # nome e o "gastos" é rótulo. O corte de `_fora_do_nome_da_categoria` separa
    # os dois casos sem regra especial nenhuma.
    #
    # O pipeline aqui não é óbvio: `_extract_query_category` joga "total" e "da"
    # fora (`_CAT_STOP_WORDS`), sobra "obra", e é o fuzzy por token distintivo
    # (`custom_category_match`) que devolve "total da obra".
    create_user_category(pro_user_id, "total da obra")
    base = _hoje_as(9)
    db.add_launch_and_update_balance(
        pro_user_id, "receita", 700, "reembolso do pedreiro", None,
        categoria="total da obra", criado_em=base,
    )
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 90, "cimento", None,
        categoria="total da obra", criado_em=base,
    )
    _ruido_mais_recente(pro_user_id, base)

    from core.handlers.launches import _resolve_query_category
    assert _resolve_query_category(pro_user_id, "gastos com total da obra") == "total da obra"

    resp = list_launches(pro_user_id, original_text="gastos com total da obra")
    # FORMATO=lista: o "total" do nome não vira pedido de total
    assert "Lançamentos em total da obra" in resp, resp
    assert "Total em" not in resp, resp
    # TIPO=despesa: o "gastos" que o usuário escreveu vale
    assert "90,00" in resp, resp
    assert "700,00" not in resp, resp
    assert "mercado" not in resp.lower(), resp

    # e a pergunta NEUTRA na mesma categoria mostra os DOIS tipos — é o que prova
    # que o expense-only acima veio da palavra do usuário, não do nome.
    neutro = list_launches(pro_user_id, original_text="liste os lancamentos em total da obra")
    assert "700,00" in neutro and "90,00" in neutro, neutro


# Categorias que a produção grava com `is_internal_movement=True` e que o
# predicado do NOME (`is_internal_category`) classifica como False. Não são
# hipotéticas — cada uma tem escritor:
#   ajuste                    → `adjust_balance_route` (tipo depende do sinal do
#                               delta: "receita" se > 0, "despesa" se < 0)
#   saldo_inicial             → `set_initial_balance_route` (tipo="receita" fixo)
#   estorno_pagamento_fatura  → `rebuild_bill_totals`     (tipo="receita" fixo)
# Comando que produz a lista de tipos:
#   grep -rn 'categoria="ajuste"\|categoria="saldo_inicial"\|categoria="estorno_pagamento_fatura"' \
#        -B 8 --include="*.py" db/ frontend/ | grep -E 'tipo=|"receita"|"despesa"'
#
# Só o 'ajuste' chega HOJE no caminho de despesa (os outros dois só gravam
# receita, e o caminho de receita não tem a divergência: total e lista saem os
# dois do `resumo` de `list_launches_by_category`). Os três estão aqui porque a
# guarda é da CLASSE "nome não-interno + flag True", não do 'ajuste'.
_INTERNAS_QUE_O_NOME_NAO_DENUNCIA = ["ajuste", "saldo_inicial", "estorno_pagamento_fatura"]


@pytest.mark.parametrize("categoria", _INTERNAS_QUE_O_NOME_NAO_DENUNCIA)
def test_total_explica_pela_FLAG_e_nao_pelo_NOME_da_categoria(pro_user_id, categoria):
    # BLOQUEANTE B4: a versão anterior só ia atrás do dinheiro filtrado quando
    # `is_internal_category(categoria)` era True — um predicado sobre o NOME. Mas
    # quem grava `is_internal_movement` não é esse predicado (são 8+ escritores,
    # dois com predicado estritamente menor e vários com True/False cravado).
    #
    # Resultado antes, medido no 'ajuste': TOTAL negava ("não teve gastos em
    # ajuste") e LISTA mostrava os R$ 700,00 — o bug do B2 intacto numa categoria
    # que o nome não denuncia.
    from utils_text import is_internal_category
    # o que torna estes casos diferentes do pagamento_fatura do B2:
    assert is_internal_category(categoria) is False

    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 700, "movimento interno", None,
        categoria=categoria, criado_em=_hoje_as(9), is_internal_movement=True,
    )

    total = spend_query(pro_user_id, f"quanto gastei em {categoria}")
    assert "700,00" in total, total
    assert "não teve gastos" not in total.lower(), total
    assert "movimenta" in total.lower(), total

    lista = list_launches(pro_user_id, original_text=f"liste os lancamentos em {categoria}")
    assert "700,00" in lista, lista


def test_total_parcial_diz_quanto_ficou_de_fora(pro_user_id):
    # BLOQUEANTE B5: a categoria com linhas dos DOIS lados da flag. O branch
    # anterior só disparava em `total <= 0`, então com R$ 100,00 de gasto real e
    # R$ 500,00 de movimentação interna a resposta era "💸 Você gastou R$ 100,00"
    # e a lista somava R$ 600,00 — R$ 500,00 engolidos calados, no mesmo dia e na
    # mesma categoria. Pior que a negação do B2: número errado com cara de certo.
    #
    # Não é cenário de laboratório: `_charge_one` (recurring_charger) crava
    # is_internal_movement=False pra qualquer categoria escolhida pelo usuário,
    # enquanto os importadores marcam True — os dois lados na mesma categoria.
    base = _hoje_as(9)
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 100, "taxa da corretora", None,
        categoria="criptomoedas", criado_em=base,
    )
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 500, "aporte btc", None,
        categoria="criptomoedas", criado_em=base, is_internal_movement=True,
    )

    total = spend_query(pro_user_id, "quanto gastei em criptomoedas")
    # o número do DASHBOARD não muda: gasto continua sendo o filtrado
    assert "100,00" in total, total
    # ...mas o que ficou de fora é anunciado, com valor
    assert "500,00" in total, total
    assert "movimenta" in total.lower(), total

    # e o que a LISTA soma (R$ 600,00) deixa de contradizer o total
    lista = list_launches(pro_user_id, original_text="liste os gastos em criptomoedas")
    assert "600,00" in lista, lista


def test_total_sem_nada_fora_nao_ganha_a_explicacao(pro_user_id):
    # O espelho do B5: gasto normal, nada filtrado → nenhuma linha de
    # movimentação. Sem isso a explicação viraria ruído em toda resposta.
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 50, "pao", None,
        categoria="mercado", criado_em=_hoje_as(9),
    )
    resp = spend_query(pro_user_id, "quanto gastei em mercado")
    assert "50,00" in resp, resp
    assert "movimenta" not in resp.lower(), resp


def test_lista_longa_cabe_no_limite_do_whatsapp(pro_user_id):
    # BLOQUEANTE B7: o WhatsApp rejeita texto acima de 4096 caracteres e não há
    # chunking em ponto nenhum do caminho de envio (grep por
    # "4096|chunk|split_message|MAX_MSG" em core/ e utils* = 0 hits). Antes deste
    # PR o caminho de categoria devolvia total + top 5 — quem criou a capacidade
    # de estourar foi este PR, então o teto é dívida DESTE PR.
    #
    # Ponta a ponta pelo Postgres de propósito, SEM monkeypatch: `len()` de uma
    # resposta real, não aritmética sobre o formato. Pior caso do formato:
    # descrição de 50 chars, valor de 7 dígitos, nome de categoria de 37, e o
    # `limit` máximo que `_limit_pedido` aceita (100). O marcador de cartão
    # ("💳", 1 char) é MAIS CURTO que o "#NNN" do lançamento, então a linha de
    # launches — que é a que este teste gera — é o caso pior dos dois.
    NOME = "gastos com a reforma da casa da praia"
    create_user_category(pro_user_id, NOME)
    base = _hoje_as(8)
    for i in range(100):
        db.add_launch_and_update_balance(
            pro_user_id, "despesa", 1234567.89,
            "supermercado extra hipermercado zona sul unidade 12", None,
            categoria=NOME, criado_em=base + timedelta(minutes=i),
        )

    resp = list_launches(pro_user_id, original_text=f"liste os lancamentos em {NOME}",
                         entities={"limit": 100})

    # 1) a medição mede alguma coisa: as MESMAS 100 linhas sem o corte estouram.
    #    Sem esta asserção o teste passaria mesmo se o formato encolhesse a ponto
    #    de o corte nunca disparar — verde por construção.
    from core.handlers import launches as _L
    teto = _L._WPP_MAX_CHARS
    try:
        _L._WPP_MAX_CHARS = 10 ** 9
        sem_corte = list_launches(pro_user_id, original_text=f"liste os lancamentos em {NOME}",
                                  entities={"limit": 100})
    finally:
        _L._WPP_MAX_CHARS = teto
    assert len(sem_corte) > 4096, len(sem_corte)

    # 2) o que sai cabe
    assert len(resp) < 4096, len(resp)

    # 3) o corte é ANUNCIADO, e o anúncio bate com a realidade nos DOIS números:
    #    N = linhas realmente renderizadas (não as 100 pedidas),
    #    M = total que EXISTE (100), não o truncado — é o número que responde
    #        "e o resto?". Anunciar o truncado seria esconder o corte no próprio
    #        aviso de corte.
    m = re.search(r"\(mostrando os (\d+) mais recentes de (\d+)\)", resp)
    assert m, resp[:200]
    n_mostrado, n_total = int(m.group(1)), int(m.group(2))
    assert n_total == 100, n_total
    assert n_mostrado == resp.count("\n#"), (n_mostrado, resp.count("\n#"))
    assert n_mostrado < 100, n_mostrado

    # 4) e o SUMÁRIO continua sendo o do período inteiro, não o das linhas
    #    exibidas: 100 × 1.234.567,89 = 123.456.789,00. Truncar a lista não pode
    #    encolher o total — é a classe "total parcial com cara de total".
    assert "123.456.789,00" in resp, resp[-200:]


def test_lista_curta_nao_anuncia_corte(pro_user_id):
    # Espelho: 3 linhas não são truncadas nem ganham o "(mostrando os N de M)".
    base = _hoje_as(9)
    for i in range(3):
        db.add_launch_and_update_balance(
            pro_user_id, "despesa", 10 + i, "padaria", None,
            categoria="alimentacao", criado_em=base + timedelta(minutes=i),
        )
    resp = list_launches(pro_user_id, original_text="liste os lancamentos em alimentacao")
    assert "mostrando os" not in resp, resp
    assert len(resp) < 4096


def _utf16(s: str) -> int:
    """Unidades UTF-16 do texto — a unidade em que o teto é medido.

    Calculado aqui, sem importar o helper do código sob teste: se o helper
    estiver errado, o teste precisa acusar em vez de concordar.
    """
    return len(s.encode("utf-16-le")) // 2


def test_lista_com_emoji_na_descricao_cabe_em_utf16(pro_user_id):
    # BLOQUEANTE B8: o corte media `len()` (codepoints) e o teto era 3900 com uma
    # "folga" de 196 estimada só sobre os emoji do FORMATO. A descrição vem do
    # WhatsApp e também tem emoji, sem truncagem nenhuma. Medido no Postgres com
    # esta descrição (50 emoji astrais) e o código ANTIGO: len(resp)=3820 (dentro
    # do teto de 3900, o corte parou satisfeito) e 5822 unidades UTF-16 — 1726
    # ACIMA de 4096 (o Manager mediu 3890/6042 na variante dele). O teste ASCII
    # ao lado é cego a isso: lá as duas contagens coincidem.
    NOME = "gastos com a reforma da casa da praia"
    create_user_category(pro_user_id, NOME)
    DESC = "💳💸💰🧾" * 12 + "💳💸"          # 50 emoji astrais = 100 unidades UTF-16
    base = _hoje_as(8)
    for i in range(100):
        db.add_launch_and_update_balance(
            pro_user_id, "despesa", 1234567.89, DESC, None,
            categoria=NOME, criado_em=base + timedelta(minutes=i),
        )

    resp = list_launches(pro_user_id, original_text=f"liste os lancamentos em {NOME}",
                         entities={"limit": 100})

    # 1) a medição mede alguma coisa: sem o corte estas mesmas linhas estouram.
    from core.handlers import launches as _L
    teto = _L._WPP_MAX_CHARS
    try:
        _L._WPP_MAX_CHARS = 10 ** 9
        sem_corte = list_launches(pro_user_id, original_text=f"liste os lancamentos em {NOME}",
                                  entities={"limit": 100})
    finally:
        _L._WPP_MAX_CHARS = teto
    assert _utf16(sem_corte) > 4096, _utf16(sem_corte)

    # 2) o que sai cabe NA UNIDADE DO WHATSAPP, não em codepoints.
    assert _utf16(resp) <= 4096, (_utf16(resp), len(resp))

    # 3) e o corte continua anunciado (o corte a mais não pode virar corte mudo).
    assert re.search(r"\(mostrando os (\d+) mais recentes de 100\)", resp), resp[:200]


def test_linha_unica_gigante_nao_estoura(pro_user_id):
    # Sub-achado do mesmo teto: o laço para em `n > 1`, então UMA linha grande
    # demais passava inteira. Medido antes: descrição de 5000 chars → resposta de
    # 5101 unidades UTF-16 (o Manager mediu 5095 na variante dele).
    # `launches.alvo`/`nota` são `text`, sem teto nenhum no schema.
    base = _hoje_as(9)
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 42.0, "a" * 5000, None,
        categoria="alimentacao", criado_em=base,
    )
    resp = list_launches(pro_user_id, original_text="liste os lancamentos em alimentacao")
    assert "42,00" in resp, resp[:200]
    assert _utf16(resp) <= 4096, _utf16(resp)


def test_descricao_com_quebra_de_linha_nao_forja_lancamento(pro_user_id):
    # O9: a descrição vem do WhatsApp e pode ter `\n`. Medido antes do fix: UM
    # lançamento com "linha1\nlinha2\nlinha3" saía com 6 linhas na resposta — e
    # uma descrição que começa com "\n#99 • despesa • R$ 9.999,00 • ..." se passa
    # por OUTRO lançamento dentro da própria listagem (auto-spoofing: o valor
    # forjado tem cara de linha real, com prefixo "#N" e tudo).
    FORJA = "\n#99 • despesa • R$ 9.999,00 • forjado • 01/01/2020"
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 42.0, "linha1\nlinha2" + FORJA, None,
        categoria="alimentacao", criado_em=_hoje_as(9),
    )
    resp = list_launches(pro_user_id, original_text="liste os lancamentos em alimentacao")

    # 1) 1 lançamento = 1 linha de lançamento. Nem 6, nem 2.
    linhas = [l for l in resp.split("\n") if re.match(r"^(#\d+|💳) • ", l)]
    assert len(linhas) == 1, linhas

    # 2) e a forja não virou linha própria: o texto sobrevive DENTRO da linha real
    #    (a de R$ 42,00), não como um lançamento de R$ 9.999,00.
    assert "42,00" in linhas[0], linhas[0]
    assert "9.999,00" in linhas[0], linhas[0]
    assert not any(l.startswith("#99 ") for l in resp.split("\n")), resp


def test_verbo_de_receita_nunca_casa_como_pergunta_de_gasto():
    # Observação 3 do Manager: `_PEDE_RECEITA_RE` é DERIVADO de
    # `RECEITA_START_VERBS` (parsers.py), acoplamento em mão única. Se um verbo de
    # despesa entrar naquela tupla, ele passa a marcar consulta como pergunta de
    # RECEITA e as duas regex casam a mesma frase — com `_tipo_pedido` devolvendo
    # None (ambos) onde devolvia 'despesa'. Medido: com "paguei " na tupla,
    # `_tipo_pedido("quanto paguei em mercado", "mercado")` vai de 'despesa' pra
    # None. Hoje: 0 violações.
    from parsers import RECEITA_START_VERBS
    from core.handlers.launches import _PEDE_GASTO_RE
    violacoes = [v for v in RECEITA_START_VERBS if _PEDE_GASTO_RE.search(v.strip())]
    assert violacoes == [], violacoes
