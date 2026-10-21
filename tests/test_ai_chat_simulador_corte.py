"""`show_card simulador` com `cortes` ("simula cortar 30% do mercado"): o servidor faz a conta do POST
`/simulador/habitos` e o card mostra só o resultado. Postgres real, relógio congelado (`dia_fixo`).
O pytest não fala com o modelo: o `cortes` na 1ª chamada, sem recusar, só o staging prova."""
import json
import math
from pathlib import Path
from decimal import Decimal as D

import pytest

from _cards_apoio import mostrar
from _ia_stream_helpers import conta, eventos_sse, executar, linhas, openai_falso, rodada
from api.v2.simulador import SimuladorHabitos
from conftest import promote_to_pro, usuario_pagante
from core.services import previsao_v2
from core.services.ai_chat import cards, system_prompt
from core.services.ai_chat._context import CARDS_DO_TURNO, FALHAS_DE_CARD_DO_TURNO
from core.services.ai_chat.cards_corte import MAX_CORTES
from core.services.ai_chat.tools.mostrar_card import SCHEMA, SCHEMA_METAS
from core.services.ai_guard import check
from core.services.previsao_variavel import cortar, repartir
from test_api_app_sessao import completo
from test_senha_obrigatoria import H
from tests.test_api_v2_simulador import cliente, dia_fixo, uid  # noqa: F401 (fixtures)
from tests.test_api_v2_simulador_habitos import com_historico, corte, ok as post_ok, post
from tests.test_cashflow_snapshot import q

FIX_HABITOS = json.loads((Path(__file__).parent / "frontend" / "api_v2_respostas.json").read_text(encoding="utf-8"))["simulador"]["habitos"]


def pronto(u):
    q("update auth_accounts set open_finance_onboarding_completed_at=now() where user_id=%s", (u,))


def pede(u, *cortes, **args):
    """(resultado, blocos, eventos, o card ainda cabe no turno?) pela tool real, com as vagas do turno no contexto."""
    t = CARDS_DO_TURNO.set([]), FALHAS_DE_CARD_DO_TURNO.set([])
    try:
        r, blocos, ev = mostrar(u, card="simulador", **({"cortes": list(cortes)} if cortes else {}), **args)
        return r, blocos, ev, bool(cards.cards_livres(u, ("simulador",)))
    finally:
        CARDS_DO_TURNO.reset(t[0])
        FALHAS_DE_CARD_DO_TURNO.reset(t[1])


def habitos(blocos):
    return blocos[0]["abas"][0]["recursos"]["simulador_habitos"]


def sem_hora(d):
    return {k: v for k, v in d.items() if k not in ("calculado_em", "valido_ate")}


def visao_de(u, *cortes):
    return cards.montar("simulador", u, {"meses": [], "dias": None, "cortes": list(cortes)})[1]["abas"][0]


@pytest.fixture
def espiao(monkeypatch):
    monkeypatch.setattr(previsao_v2, "ler_snapshot", lambda *a: pytest.fail("leitura financeira"))


@pytest.fixture
def dois(uid, dia_fixo):
    com_historico(uid, dia_fixo, mercado=600, lazer=300)
    pronto(uid)
    return uid


M30 = {"categoria": "Mércado", "percentual": 30}


# ── 1. a mesma conta do POST ───────────────────────────────────────────────────

def test_a_conta_e_a_do_post_e_o_bloco_mantem_o_mes_e_a_previsao(dois):
    r, blocos, _, _ = pede(dois, M30)
    assert "error" not in r and len(blocos) == 1
    rec = blocos[0]["abas"][0]["recursos"]
    assert {"detalhes", "previsao", "simulador_habitos"} <= set(rec)  # o build antigo segue com os sliders
    h = rec["simulador_habitos"]
    assert SimuladorHabitos.model_validate(h).model_dump(mode="json") == h
    assert set(h) == set(FIX_HABITOS)
    assert sem_hora(h) == sem_hora(post_ok(dois, corte(("mercado", 30))))


def test_sem_cortes_o_card_e_o_de_sempre(dois):
    for cortes in ({}, {"cortes": None}, {"cortes": []}):
        r, blocos, _ = mostrar(dois, card="simulador", **cortes)
        assert "simulador_habitos" not in blocos[0]["abas"][0]["recursos"] and "gasto_do_dia_a_dia" in r["abas"][0]


# ── 2. resolução do nome ───────────────────────────────────────────────────────

@pytest.mark.parametrize("nome", ["mercado", "Mercado", "MERCADO", "mércado", " mercado "])
def test_nome_que_casa(dois, nome):
    r, blocos, _, _ = pede(dois, {"categoria": nome, "percentual": 30})
    assert habitos(blocos)["cortes"][0]["chave"] == "mercado"


@pytest.mark.parametrize("nome", ["comida", "merc"])
def test_nome_que_nao_casa_nao_emite_e_o_card_continua_livre(dois, nome):
    r, blocos, ev, livre = pede(dois, {"categoria": nome, "percentual": 30})
    assert r["error"] == "categoria_fora_da_estimativa" and r["categorias"] == ["mercado", "lazer"]
    assert nome not in json.dumps({k: v for k, v in r.items() if k != "categorias"}) and not blocos and not ev and livre


def test_nome_ambiguo_nao_casa(uid, dia_fixo):
    com_historico(uid, dia_fixo, **{"e-commerce": 100, "e commerce": 200})
    pronto(uid)
    r, blocos, ev, livre = pede(uid, {"categoria": "E.Commerce", "percentual": 10})
    assert r["error"] == "categoria_fora_da_estimativa" and not blocos and not ev and livre
    assert pede(uid, {"categoria": "e commerce", "percentual": 10})[1]  # controle: o exato casa


def test_mesma_categoria_duas_vezes(dois):
    r, blocos, ev, livre = pede(dois, {"categoria": "Mercado", "percentual": 10}, {"categoria": "mercado", "valor": 5})
    assert r["error"] == "categorias_repetidas" and not blocos and not ev and livre


def test_nomes_da_lista_cortados_em_60_e_ate_15(uid, dia_fixo):
    com_historico(uid, dia_fixo, **{f"{i:02d}" + "x" * 100: 10 + i for i in range(20)})
    pronto(uid)
    r = pede(uid, {"categoria": "nenhuma", "percentual": 10})[0]
    assert len(r["categorias"]) == 15 and {len(n) for n in r["categorias"]} == {60}


# ── 3. formato: erro antes de ler a snapshot ──────────────────────────────────

_RUINS = [
    [{"categoria": "m", "percentual": p}] for p in (0, 101, "30", 30.5, None, True)
] + [
    "mercado", [{"categoria": "m", "percentual": 10}] * (MAX_CORTES + 1), [{"categoria": "x" * 81, "percentual": 10}],
    [{"categoria": " ", "percentual": 10}], [{"categoria": "m", "percentual": 10, "hero": 1}], ["m"],
] + [
    [{"categoria": "m", "valor": v}] for v in (0, -5, 200.555, "200", math.nan, math.inf, True, 1e-05, 10**400)
] + [
    [{"categoria": "m", "percentual": 10, "valor": 5}], [{"categoria": "m"}], [{"todas": True}],
    [{"categoria": "m", "todas": True, "percentual": 10}], [{"todas": True, "percentual": 10}] * 2,
    [{"todas": 1, "percentual": 10}],
]


@pytest.mark.parametrize("cortes", _RUINS, ids=range(len(_RUINS)))
def test_formato_invalido_vira_erro_sem_ler_nem_emitir(dois, monkeypatch, cortes):
    monkeypatch.setattr(previsao_v2, "ler_snapshot", lambda *a: pytest.fail("leitura financeira"))
    r, blocos, ev = mostrar(dois, card="simulador", cortes=cortes)
    assert r["error"] == "corte_invalido" and not blocos and not ev


def test_formato_valido_passa_nas_pontas(dois):
    for pedido in ({"percentual": 1}, {"percentual": 100}, {"valor": 0.01}, {"valor": 600}, {"valor": 12.5}, {"valor": None, "percentual": 5}):
        r, blocos, _, _ = pede(dois, {"categoria": "mercado", **pedido}, {"categoria": "lazer", "valor": None, "percentual": 10, "todas": False})
        assert "error" not in r and blocos, pedido


def test_cortes_com_outro_card_e_erro_sem_marcar_falha(dois, espiao):
    t = FALHAS_DE_CARD_DO_TURNO.set([])
    try:
        r, blocos, ev = mostrar(dois, card="hero", cortes=[M30])
        assert r["error"] == "cortes_so_no_simulador" and not blocos and not ev and FALHAS_DE_CARD_DO_TURNO.get() == []
    finally:
        FALHAS_DE_CARD_DO_TURNO.reset(t)


# ── 4 e 5. plano e estimativa nula ────────────────────────────────────────────

def test_abaixo_do_pro_recebe_pro_required_sem_ler(uid, espiao):
    promote_to_pro(uid, "pro")  # 'pro' gravado = tier Plus
    pronto(uid)
    r, blocos, ev, livre = pede(uid, M30)
    assert r["error"] == "pro_required" and not blocos and not ev and not livre


def test_sem_estimativa_e_indisponivel_sem_bloco_e_sem_numero(uid, dia_fixo):
    pronto(uid)
    r, blocos, ev, livre = pede(uid, M30)
    assert r["error"] == "indisponivel" and "sem número" in r["message"] and not blocos and not ev and not livre


# ── 6. isolamento ─────────────────────────────────────────────────────────────

def test_a_lista_e_a_estimativa_de_a_mesmo_com_user_id_de_b_nos_args(uid, dia_fixo, monkeypatch):
    com_historico(uid, dia_fixo, mercado=600)
    outro = usuario_pagante("pro_max")
    com_historico(outro, dia_fixo, lazer=7777)
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", f"{uid},{outro}")
    pronto(uid)
    r, blocos, ev, _ = pede(uid, {"categoria": "lazer", "percentual": 10}, user_id=outro)
    assert r["error"] == "categoria_fora_da_estimativa" and r["categorias"] == ["mercado"]
    assert "7777" not in json.dumps(r) and "lazer" not in json.dumps(r) and not blocos
    ok_ = pede(uid, {"categoria": "mercado", "percentual": 10}, user_id=outro)[1]
    assert "7777" not in json.dumps(ok_) and "lazer" not in json.dumps(ok_)


# ── 7 e 8. a visão do modelo e o ai_guard; R$ exato ───────────────────────────

def nao_sustentado(texto, visao):
    return [c for c in check(texto, [json.dumps(visao)]) if not c.supported]


def test_visao_sustenta_o_texto_e_so_sob_chave_monetaria(dois):
    v = visao_de(dois, {"categoria": "mercado", "percentual": 30})
    c = v["corte"]
    assert c["total_economia_mensal"] == 180.0 and c["categorias"][0]["percentual"] == 30 and c["em_todas"] is False
    assert c["saldo_a_mais_em_90_dias"] == 540.0
    frase = "Cortando 30% do mercado, sobra R$ 180,00 por mês."
    assert nao_sustentado(frase, v) == []
    mutada = json.loads(json.dumps(v).replace("total_economia_mensal", "economia").replace("economia_valor", "economia"))
    assert nao_sustentado(frase, mutada)  # controle: sem chave monetária o número não é evidência


def test_em_reais_a_economia_e_exata_e_a_visao_nao_tem_percentual(dois):
    r, blocos, _, _ = pede(dois, {"categoria": "mercado", "valor": 200})
    h = habitos(blocos)
    c = h["cortes"][0]
    assert (c["economia_mensal"], c["valor_pedido"], c["percentual"]) == ("200.00", "200.00", 33)
    assert h["com_corte"]["delta_vs_atual"] == "600.00" and h["economia_mensal"] == "200.00"
    v = visao_de(dois, {"categoria": "mercado", "valor": 200})
    assert "percentual" not in v["corte"]["categorias"][0] and v["corte"]["categorias"][0]["economia_valor"] == 200.0
    assert nao_sustentado("Com isso sobra R$ 200,00 por mês.", v) == []
    # o caminho que o plano proíbe (R$ -> % inteiro) erra o centavo
    assert post_ok(dois, corte(("mercado", 33)))["economia_mensal"] == "198.00"


# ── 9. bordas do valor ────────────────────────────────────────────────────────

def test_valor_igual_a_estimativa_passa_e_vira_100(dois):
    r, blocos, _, _ = pede(dois, {"categoria": "mercado", "valor": 600})
    c = habitos(blocos)["cortes"][0]
    assert (c["percentual"], c["economia_mensal"]) == (100, "600.00")


def test_valor_acima_da_estimativa_e_erro_com_o_teto_e_o_card_continua_livre(dois):
    r, blocos, ev, livre = pede(dois, {"categoria": "mercado", "valor": 600.01})
    assert r["error"] == "valor_acima_da_estimativa" and r["despesa_mensal"] == 600.0 and r["categoria"] == "mercado"
    assert not blocos and not ev and livre


# ── 10 e 11. todas, e o específico ganha ─────────────────────────────────────

def test_todas_em_percentual_e_o_post_com_todas_as_chaves(dois):
    r, blocos, _, _ = pede(dois, {"todas": True, "percentual": 10})
    assert sem_hora(habitos(blocos)) == sem_hora(post_ok(dois, corte(("mercado", 10), ("lazer", 10))))
    assert visao_de(dois, {"todas": True, "percentual": 10})["corte"]["em_todas"] is True


def test_especifico_ganha_do_todas(dois):
    r, blocos, _, _ = pede(dois, {"todas": True, "percentual": 10}, {"categoria": "lazer", "percentual": 50})
    assert sem_hora(habitos(blocos)) == sem_hora(post_ok(dois, corte(("mercado", 10), ("lazer", 50))))


def test_especifico_em_reais_e_o_resto_em_percentual(dois):
    r, blocos, _, _ = pede(dois, {"categoria": "lazer", "valor": 100}, {"todas": True, "percentual": 10})
    por = {c["chave"]: (c["economia_mensal"], c["valor_pedido"]) for c in habitos(blocos)["cortes"]}
    assert por == {"mercado": ("60.00", None), "lazer": ("100.00", "100.00")}


# ── 12. todas em reais ───────────────────────────────────────────────────────

def test_todas_em_reais_divide_proporcional_e_a_soma_fecha(uid, dia_fixo):
    com_historico(uid, dia_fixo, alfa=100, beta=100, gama=100)
    pronto(uid)
    r, blocos, _, _ = pede(uid, {"todas": True, "valor": 100})
    por = [c["economia_mensal"] for c in habitos(blocos)["cortes"]]
    assert por == ["33.34", "33.33", "33.33"] and habitos(blocos)["economia_mensal"] == "100.00"


def test_todas_em_reais_proporcional_a_despesa(dois):
    r, blocos, _, _ = pede(dois, {"todas": True, "valor": 300})
    assert [c["economia_mensal"] for c in habitos(blocos)["cortes"]] == ["200.00", "100.00"]
    assert [c["valor_pedido"] for c in habitos(blocos)["cortes"]] == ["200.00", "100.00"]


def test_todas_em_reais_acima_da_soma_e_erro(dois):
    r, blocos, ev, livre = pede(dois, {"todas": True, "valor": 900.01})
    assert r["error"] == "valor_acima_da_estimativa" and r["despesa_mensal"] == 900.0 and not blocos and livre
    assert pede(dois, {"todas": True, "valor": 900})[1]  # controle: a soma exata passa
    # o específico sai da soma do `todas`
    r = pede(dois, {"categoria": "lazer", "percentual": 10}, {"todas": True, "valor": 600.01})[0]
    assert r["error"] == "valor_acima_da_estimativa" and r["despesa_mensal"] == 600.0


EST = {"categorias": [{"chave": "a", "nome": "A", "despesa_mensal": D("100.00")},
                      {"chave": "b", "nome": "B", "despesa_mensal": D("100.00")},
                      {"chave": "c", "nome": "C", "despesa_mensal": D("100.00")}]}


def test_repartir_pura():
    p = repartir(EST, {"a", "b", "c"}, D("100"))
    assert p == {"a": D("33.34"), "b": D("33.33"), "c": D("33.33")} and sum(p.values()) == D("100")
    assert repartir(EST, {"b", "c"}, D("0.01")) == {"b": D("0.01")}  # parte de 0,00 fica fora


def test_repartir_nunca_passa_da_despesa_da_categoria():
    est = {"categorias": [{"chave": k, "nome": k, "despesa_mensal": D(v)} for k, v in (("a", "0.01"), ("b", "0.03"), ("c", "0.03"))]}
    p = repartir(est, {"a", "b", "c"}, D("0.06"))
    assert p == {"a": D("0.01"), "b": D("0.03"), "c": D("0.02")} and sum(p.values()) == D("0.06")
    assert all(p[c["chave"]] <= c["despesa_mensal"] for c in est["categorias"] if c["chave"] in p)


def test_todas_em_reais_de_um_centavo_nao_gera_linha_de_zero(dois):
    r, blocos, _, _ = pede(dois, {"todas": True, "valor": 0.01})
    assert [(c["chave"], c["economia_mensal"]) for c in habitos(blocos)["cortes"]] == [("mercado", "0.01")]


def test_todas_quando_todas_ja_tem_corte_proprio_e_erro_sem_marcar_falha(dois):
    r, blocos, ev, livre = pede(dois, {"categoria": "mercado", "percentual": 50}, {"categoria": "lazer", "percentual": 50},
                                {"todas": True, "valor": 5})
    assert r["error"] == "corte_invalido" and "tire o item todas" in r["message"] and not blocos and livre


def test_em_todas_exige_mais_de_uma_categoria(uid, dia_fixo):
    com_historico(uid, dia_fixo, mercado=600)
    pronto(uid)
    assert visao_de(uid, {"categoria": "mercado", "percentual": 30})["corte"]["em_todas"] is False


def test_cortar_com_valores_pura():
    linhas, total = cortar(EST, {"c": 50}, {"a": D("40")})
    assert [(l["chave"], l["percentual"], l["economia_mensal"], l["valor_pedido"]) for l in linhas] == [
        ("a", 40, D("40"), D("40")), ("c", 50, D("50.00"), None)] and total == D("90.00")
    assert cortar(EST, {"a": 10}, {"a": D("100")})[0][0]["percentual"] == 100  # o valor ganha da chave repetida
    assert cortar(EST, {"a": 10}) == cortar(EST, {"a": 10}, None)  # sem `valores`, o de sempre
    assert cortar(EST, {"a": 10})[0][0]["valor_pedido"] is None


# ── 13. o POST não ampliou ───────────────────────────────────────────────────

def test_post_nao_aceita_valor_e_devolve_valor_pedido_nulo(dois):
    r = post(dois, {"cortes": [{"categoria": "mercado", "valor": "200"}]})
    assert r.status_code == 422
    assert all(c["valor_pedido"] is None for c in post_ok(dois, corte(("mercado", 30)))["cortes"])
    assert all("valor_pedido" in c for c in FIX_HABITOS["cortes"])


# ── 14. a conversa pelo runner real ──────────────────────────────────────────

def _tool(u, i):
    return [json.loads(l["content"]) for l in linhas(u) if l["role"] == "tool"][i]


def test_conversa_abre_o_simulador_corta_e_corta_em_reais(dois, monkeypatch):
    mudos = lambda ev: [e for e in ev if e["tipo"] in ("passo", "bloco")]  # noqa: E731
    openai_falso(monkeypatch, rodada(None, ("show_card", {"card": "simulador"})), rodada("Aqui está."))
    _, ev, _ = executar(dois, "abre o simulador")
    assert "simulador_habitos" not in [e for e in ev if e["tipo"] == "bloco"][0]["bloco"]["abas"][0]["recursos"]

    frase = "Cortando 30% do mercado, sobra R$ 180,00 por mês."
    openai_falso(monkeypatch,
                 rodada(None, ("show_card", {"card": "simulador", "cortes": [{"categoria": "comida", "percentual": 30}]})),
                 rodada(None, ("show_card", {"card": "simulador", "cortes": [{"categoria": "Mercado", "percentual": 30}]})),
                 rodada(frase))
    r, ev, _ = executar(dois, "simula cortar 30% do mercado")
    assert [e["tipo"] for e in mudos(ev)] == ["passo", "bloco"]  # o erro não emitiu; o acerto, um passo e um bloco
    assert habitos([e["bloco"] for e in ev if e["tipo"] == "bloco"])["cortes"][0]["economia_mensal"] == "180.00"
    assert _tool(dois, 1)["error"] == "categoria_fora_da_estimativa" and _tool(dois, 1)["categorias"] == ["mercado", "lazer"]
    assert r.reply == frase

    openai_falso(monkeypatch, rodada(None, ("show_card", {"card": "simulador", "cortes": [{"categoria": "mercado", "valor": 200}]})),
                 rodada("Sobram R$ 200,00 por mês."))
    r, ev, _ = executar(dois, "e se eu gastar R$ 200 a menos no mercado?")
    h = habitos([e["bloco"] for e in ev if e["tipo"] == "bloco"])
    assert h["cortes"][0]["valor_pedido"] == "200.00" and r.reply == "Sobram R$ 200,00 por mês."


def test_conversa_pelo_endpoint_sse(monkeypatch, dia_fixo):
    u, c = conta()
    completo(u)
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", str(u))
    com_historico(u, dia_fixo, mercado=600, lazer=300)
    openai_falso(monkeypatch, rodada(None, ("show_card", {"card": "simulador", "cortes": [{"categoria": "mercado", "percentual": 30}]})),
                 rodada("Sobram R$ 180,00 por mês."))
    ev = eventos_sse(c.post("/ai/chat/stream", json={"message": "simula cortar 30% do mercado"}, headers=H))
    tipos = [e["tipo"] for e in ev]
    assert tipos.index("passo") < tipos.index("bloco") < tipos.index("fim") and tipos.count("bloco") == 1
    assert habitos([next(e for e in ev if e["tipo"] == "bloco")["bloco"]])["economia_mensal"] == "180.00"


# ── 16. schema e prompt ───────────────────────────────────────────────────────

@pytest.mark.parametrize("schema", [SCHEMA, SCHEMA_METAS], ids=["base", "metas"])
def test_schema_tem_cortes(schema):
    p = schema["function"]["parameters"]["properties"]["cortes"]
    assert set(p["items"]["properties"]) == {"categoria", "todas", "percentual", "valor"} and p["maxItems"] == MAX_CORTES
    assert "o resultado do corte pedido em % ou em R$" in schema["function"]["description"]


@pytest.mark.parametrize("adendo", [system_prompt.ADENDO_CARDS, system_prompt.ADENDO_CARDS_METAS], ids=["base", "metas"])
def test_prompt_tem_as_frases_do_corte(adendo):
    for frase in ("JÁ NA PRIMEIRA chamada", "NUNCA vai para `report_out_of_scope`", "diga no texto qual usou",
                  "`{categoria, valor}`", "valor_acima_da_estimativa",
                  "EXCEÇÃO só neste corte", "SEMPRE diga em uma frase a sobra por mês (`total_economia_mensal`)"):
        assert frase in adendo, frase
