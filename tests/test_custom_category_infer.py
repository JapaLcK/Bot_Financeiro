"""
Regressão: categoria CUSTOM criada na tela (user_categories) sem regra de
keyword deve ser reconhecida na inferência de um lançamento.

Bug relatado: usuário cria a categoria "gastos com minha namorada" e lança
"gastei 400 com minha namorada" — o gasto ia parar em "outros" porque
infer_category só olhava user_category_rules / LOCAL_RULES / IA, nunca as
categorias custom que o usuário cadastrou visualmente.
"""
from __future__ import annotations

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


def test_list_launches_receita_em_categoria_nao_delega(pro_user_id):
    # Codex P2: spend_query soma só tipo='despesa' (sum_spent_in_category_period).
    # "rendimentos" é categoria de RECEITA (dividendos, juros), então delegar
    # respondia "você não teve gastos em rendimentos" e sumia com o lançamento.
    db.add_launch_and_update_balance(
        pro_user_id, "receita", 300, "dividendos itau", None, categoria="rendimentos",
    )
    resp = list_launches(pro_user_id, original_text="liste as receitas em rendimentos")
    assert "300" in resp
    assert "não teve gastos" not in resp.lower()


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


def test_cleanup_script_apply_exige_user(monkeypatch):
    # Codex P1: --apply global apagaria regras criadas de propósito (sem coluna
    # de proveniência). --apply exige --user pra forçar revisão cliente a cliente.
    import sys
    from scripts.cleanup_poisoned_category_rules import main
    monkeypatch.setattr(sys, "argv", ["cleanup", "--apply"])
    with pytest.raises(SystemExit):
        main()


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
