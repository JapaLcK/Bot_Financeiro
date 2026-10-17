"""F1: card `periodo` (1 a 12 meses inteiros), teto de 12 meses nas tools de data, `falha_interna`
e `tool_choice="none"` na última volta. Banco real; só a OpenAI é falsa.

Fonte única: o `periodo` soma o `TOTAIS_SQL` mês a mês, o mesmo do `GET /api/app/mes-detalhes`; a tool
`get_period_summary` de meses inteiros lê a MESMA função. Controles negativos (medidos desligando o
conserto, ver o relato do PR): `_meses_inteiros` -> None, leitor trocado, `falha_interna`,
`tool_choice`, o campo renomeado do modelo.
"""
import json
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

import db
from _cards_apoio import M, M0, _http, mostrar, mundo, valores_de_b_em  # noqa: F401  (mundo é fixture)
from _ia_stream_helpers import openai_falso, rodada
from conftest import usuario_pagante
from core.services.ai_chat import cards, runner, streaming
from core.services.ai_chat._context import CARDS_DO_TURNO, CURRENT_USER_MESSAGE
from core.services.ai_chat.tools import get_tool
from core.services.ai_chat.tools import launches
from test_api_app_sessao import completo
from tests.test_resumo_mes_regra import INICIO, semeia_a
from utils_date import now_tz

ULTIMO = INICIO.replace(day=28) + timedelta(days=4)
ULTIMO = ULTIMO - timedelta(days=ULTIMO.day)  # último dia do mês M
PRIMEIRO_M0 = (INICIO - timedelta(days=1)).replace(day=1)
AGORA = f"{now_tz().year:04d}-{now_tz().month:02d}"


def _mes(delta: int, base: str = AGORA) -> str:
    i = int(base[:4]) * 12 + int(base[5:]) - 1 + delta
    return f"{i // 12:04d}-{i % 12 + 1:02d}"


def _tool(nome, uid, **args):
    return get_tool(nome).execute(uid, args)


def _com_emissor(fn):
    t = streaming.EMISSOR.set(lambda e: None)
    try:
        return fn()
    finally:
        streaming.EMISSOR.reset(t)


def _periodo(uid, **args):
    r, blocos, _ = mostrar(uid, card="periodo", **args)
    return r, (blocos[0]["abas"][0]["recursos"]["periodo"] if blocos else None)


def _dec(x):
    return Decimal(str(x))


# ── paridade com a fonte, mês a mês ────────────────────────────────────────────

def confere_paridade(uid):
    _, p = _periodo(uid, de=M0, ate=M)
    http = _http(uid)
    por_mes = {m: http.get("/api/app/mes-detalhes", params={"mes": m}).json() for m in (M0, M)}
    assert [x["mes"] for x in p["meses"]] == [M0, M]
    for x in p["meses"]:  # cada barra = o que o painel mostra naquele mês
        assert (x["entrou"], x["saiu"]) == (por_mes[x["mes"]]["entrou"], por_mes[x["mes"]]["saiu"])
    assert _dec(p["entrou"]) == sum(_dec(x["entrou"]) for x in p["meses"])
    assert _dec(p["saiu"]) == sum(_dec(x["saiu"]) for x in p["meses"]) > 0
    assert _dec(p["saldo"]) == _dec(p["entrou"]) - _dec(p["saiu"])
    for c in p["categorias"]:  # cada categoria = soma da mesma nos dois meses
        soma = sum(_dec(k["valor"]) for d in por_mes.values() for k in d["categorias"] if k["categoria"] == c["categoria"])
        assert _dec(c["valor"]) == soma, c["categoria"]
    assert {c["categoria"] for c in p["categorias"]} == {k["categoria"] for d in por_mes.values() for k in d["categorias"]}
    return p


def test_periodo_bate_com_o_mes_detalhes_mes_a_mes(mundo):
    a, _ = mundo
    p = confere_paridade(a)
    assert _dec(p["saiu"]) == Decimal("490.00")  # a 1ª parcela da compra de M0 cai na fatura de M


def test_paridade_pega_leitor_trocado(mundo, monkeypatch):
    """Controle negativo: as categorias passam a vir de `get_top_expense_categories` (cartão pela data da compra)."""
    a, _ = mundo
    confere_paridade(a)  # positivo: o leitor real passa
    real = cards.registro()

    def errado(uid, args):
        top = db.get_top_expense_categories(uid, PRIMEIRO_M0, ULTIMO, limit=None)
        (_, o), = real["periodo"].ler(uid, args)
        p = o["periodo"].model_dump(mode="json")
        p["categorias"] = [{"categoria": c["categoria"], "valor": f"{c['total']:.2f}", "quantidade": 1,
                            "percentual": 0, "maiores": []} for c in top]
        return [(None, {"periodo": p})]

    monkeypatch.setattr(cards, "registro", lambda: {**real, "periodo": cards.Card(errado, real["periodo"].visao)})
    with pytest.raises(AssertionError):
        confere_paridade(a)


def test_maiores_sao_as_5_do_topo_em_ordem_e_nao_vao_para_o_modelo(mundo):
    a, _ = mundo
    r, p = _periodo(a, de=M0, ate=M)
    for c in p["categorias"]:
        v = [_dec(m["valor"]) for m in c["maiores"]]
        assert v == sorted(v, reverse=True) and len(v) <= 5 and sum(v) <= _dec(c["valor"])
        if c["quantidade"] <= 5:
            assert sum(v) == _dec(c["valor"]) and len(v) == c["quantidade"]
    assert any(c["maiores"] for c in p["categorias"])
    assert "maiores" not in json.dumps(r) and "periodo" not in r["abas"][0]  # tokens: só em get_largest_expenses
    assert r["abas"][0]["saiu"] == float(p["saiu"]) and "mes" not in r["abas"][0]  # sem `mes`: a aba é única


def test_percentual_e_sobre_a_soma_das_categorias(mundo):
    a, _ = mundo
    _, p = _periodo(a, de=M0, ate=M)
    base = sum(_dec(c["valor"]) for c in p["categorias"])
    assert all(c["percentual"] == int((_dec(c["valor"]) * 100 / base).quantize(1, "ROUND_HALF_UP")) for c in p["categorias"])


# ── tool = card ────────────────────────────────────────────────────────────────

def test_get_period_summary_de_meses_inteiros_e_o_total_do_card(mundo):
    a, _ = mundo
    _, p = _periodo(a, de=M0, ate=M)
    r = _tool("get_period_summary", a, start_date=PRIMEIRO_M0.isoformat(), end_date=ULTIMO.isoformat())
    assert (r["despesa"], r["receita"]) == (float(p["saiu"]), float(p["entrou"]))
    assert r["cartao_por"] == "fatura" and (r["start_date"], r["end_date"]) == (PRIMEIRO_M0.isoformat(), ULTIMO.isoformat())


def test_periodo_livre_leva_o_cartao_e_bate_com_as_categorias(mundo):
    """Q3: 3 a 8 do mês M inclui o cartão pela data da compra; total = Σ das categorias da lista."""
    a, _ = mundo
    ini, fim = INICIO.replace(day=3).isoformat(), INICIO.replace(day=8).isoformat()
    r = _tool("get_period_summary", a, start_date=ini, end_date=fim)
    cats = _tool("get_top_categories", a, start_date=ini, end_date=fim, limit=20)["categories"]
    so_launches = db.get_summary_by_period(a, INICIO.replace(day=3), INICIO.replace(day=8))["despesa"]
    assert r["despesa"] == round(sum(c["total"] for c in cats), 2) > float(so_launches)
    assert r["cartao_por"] == "data_da_compra" and r["cartao_incluido"] is True


# ── teto de 12 meses ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("hoje,start,end,esperado,limitado", [
    ("2026-03-15", "2025-04-01", "2026-03-31", ("2025-04-01", "2026-03-31"), False),     # 12: passa
    ("2026-03-15", "2025-03-01", "2026-03-31", ("2025-04-01", "2026-03-31"), True),      # 13: corta
    ("2026-01-15", "2025-01-31", "2026-01-31", ("2025-02-01", "2026-01-31"), True),      # virada de janeiro
    ("2026-01-15", "2025-02-01", "2026-01-31", ("2025-02-01", "2026-01-31"), False),
    ("2026-01-15", "2025-10-01", "2026-12-31", ("2025-10-01", "2026-01-31"), False),     # entra no futuro: fim do mês corrente
    ("2026-01-15", "2024-06-01", "2027-01-01", ("2025-02-01", "2026-01-31"), True),      # os dois
    ("2026-03-15", "2026-05-01", "2026-05-31", ("2026-05-01", "2026-05-31"), False),     # mês só futuro: intacto
    ("2026-12-10", "2026-01-01", "2026-12-31", ("2026-01-01", "2026-12-31"), False),     # "este ano" em dezembro
    ("2026-03-15", "2026-03-15", "2026-04-10", ("2026-03-15", "2026-03-31"), False),     # começa HOJE: o fim futuro é cortado
])
def test_limita_periodo(monkeypatch, hoje, start, end, esperado, limitado):
    monkeypatch.setattr(launches, "today_tz", lambda: date.fromisoformat(hoje))
    s, e, extra = launches._limita_periodo(date.fromisoformat(start), date.fromisoformat(end))
    assert (s.isoformat(), e.isoformat()) == esperado
    assert bool(extra.get("limitado_a_12_meses")) is limitado
    if limitado:
        assert extra["aviso_limite"]
    # o fim foi cortado para o mês corrente: campo próprio, independente do teto de 12
    fim_cortado = date.fromisoformat(end) > e
    assert bool(extra.get("fim_ajustado")) is fim_cortado
    if fim_cortado:
        assert extra["aviso_fim"]
    if extra:  # `pedido` é sempre o período ORIGINAL
        assert extra["pedido"] == {"start_date": start, "end_date": end}


def test_fim_ajustado_chega_a_tool_sem_marcar_o_teto(monkeypatch, mundo):
    a, _ = mundo
    hoje = date(2026, 10, 29)
    monkeypatch.setattr(launches, "today_tz", lambda: hoje)
    r = _tool("get_period_summary", a, start_date="2026-10-26", end_date="2026-11-01")
    assert r["end_date"] == "2026-10-31" and r["fim_ajustado"] is True and r["aviso_fim"]
    assert "limitado_a_12_meses" not in r and r["pedido"]["end_date"] == "2026-11-01"
    r = _tool("get_period_summary", a, start_date="2024-01-01", end_date="2026-10-20")  # só o teto
    assert r["limitado_a_12_meses"] is True and "fim_ajustado" not in r
    r = _tool("get_period_summary", a, start_date="2024-01-01", end_date="2026-12-31")  # os dois
    assert r["limitado_a_12_meses"] and r["fim_ajustado"] and r["pedido"]["end_date"] == "2026-12-31"
    assert r["start_date"] == "2025-11-01" and r["end_date"] == "2026-10-31"


def test_card_corta_13_meses_com_indicador_so_na_visao(mundo):
    a, _ = mundo
    r, p = _periodo(a, de=_mes(-12), ate=AGORA)  # 13 meses
    assert [m["mes"] for m in p["meses"]] == [_mes(-11 + i) for i in range(12)]
    assert r["limitado_a_12_meses"] is True and r["pedido"] == {"de": _mes(-12), "ate": AGORA}
    assert "limitado_a_12_meses" not in json.dumps(p)  # o bloco é o JSON do endpoint
    r, p = _periodo(a, de=_mes(-11), ate=AGORA)  # positivo: 12 passam
    assert len(p["meses"]) == 12 and "limitado_a_12_meses" not in r


def test_card_ate_no_futuro_vira_o_mes_atual_e_de_no_futuro_nao_gera_bloco(mundo):
    a, _ = mundo
    r, p = _periodo(a, de=_mes(-1), ate=_mes(+5))
    assert p["fim"] == AGORA and len(p["meses"]) == 2
    # a visão leva o mesmo indicador da tool (`_limita_periodo`); o bloco segue sendo o JSON do endpoint
    assert r["fim_ajustado"] is True and r["aviso_fim"] == launches._AVISO_FIM
    assert r["pedido"] == {"de": _mes(-1), "ate": _mes(+5)} and "limitado_a_12_meses" not in r
    assert "fim_ajustado" not in json.dumps(p)
    r, p = _periodo(a, de=_mes(-1), ate=AGORA)  # positivo: sem corte não há indicador
    assert "fim_ajustado" not in r and "aviso_fim" not in r and "pedido" not in r
    r, p = _periodo(a, de=_mes(-20), ate=_mes(+2))  # os dois cortes: os dois indicadores, um só `pedido`
    assert r["fim_ajustado"] and r["limitado_a_12_meses"] and r["pedido"] == {"de": _mes(-20), "ate": _mes(+2)}
    r, blocos, ev = mostrar(a, card="periodo", de=_mes(+1), ate=_mes(+2))
    assert r["error"] == "mes_futuro" and "get_period_summary" in r["message"] and not blocos and not ev
    for ruim in ({"de": "2026-13", "ate": AGORA}, {"de": AGORA, "ate": _mes(-1)}, {"de": AGORA}, {}):
        r, blocos, ev = mostrar(a, card="periodo", **ruim)
        assert r["error"] == "periodo_invalido" and "Corrija" in r["message"] and not blocos and not ev


def test_mes_futuro_no_show_card_tem_codigo_proprio(mundo):
    a, _ = mundo
    r, blocos, _ = mostrar(a, card="resumo", meses=[_mes(+1)])
    assert r["error"] == "mes_futuro" and "Mês no futuro ou fora do formato" not in r["message"] and not blocos


def test_renda_acima_de_12_corta_com_indicador(mundo):
    a, _ = mundo
    r, (bloco,), _ = mostrar(a, card="renda", quantidade_meses=18)
    assert bloco["abas"][0]["recursos"]["renda"]["months"] == 12
    assert r["limitado_a_12_meses"] and r["pedido"] == {"quantidade_meses": 18}
    r, _, _ = mostrar(a, card="renda", quantidade_meses=12)
    assert "limitado_a_12_meses" not in r  # positivo


def test_override_da_tendencia_manda_o_n_cru(monkeypatch):
    from core.services.ai_chat._context import CURRENT_USER_MESSAGE
    t = CURRENT_USER_MESSAGE.set("tendência dos últimos 18 meses")
    try:
        chamadas = [{"function": {"name": "report_out_of_scope", "arguments": "{}"}}]
        _com_emissor(lambda: runner._maybe_override_trend(chamadas, 1))
    finally:
        CURRENT_USER_MESSAGE.reset(t)
    assert json.loads(chamadas[0]["function"]["arguments"]) == {"card": "renda", "quantidade_meses": 18}


def test_tools_de_data_cortam_sem_erro_e_o_agent_chat_nao_quebra(mundo):
    a, _ = mundo
    ini, fim = _mes(-14) + "-01", date.today().isoformat()
    for nome, extra in (("get_period_summary", {}), ("get_top_categories", {}), ("get_largest_expenses", {}),
                        ("get_category_spend", {"categoria": "mercado"})):
        r = _tool(nome, a, start_date=ini, end_date=fim, **extra)
        assert "error" not in r and r["limitado_a_12_meses"] is True, nome
        assert r["pedido"]["start_date"] == ini
    r = _tool("compare_periods", a, period_a_start=ini, period_a_end=fim, period_b_start=INICIO.isoformat(),
              period_b_end=ULTIMO.isoformat())
    assert "error" not in r and r["anterior"]["limitado_a_12_meses"] is True
    r = _tool("get_spending_trend", a, months=18)
    assert r["months"] == 12 and r["limitado_a_12_meses"] and r["pedido"] == {"months": 18}
    r = _tool("get_investment_contributions", a, start_date=ini, end_date=fim)
    assert "error" not in r and r["limitado_a_12_meses"] is True
    from core.services import agent_chat  # mesmas tools: `error` viraria DataQueryError
    for kind, nome in (("reporter", "get_period_summary"), ("xerife", "get_top_categories")):
        assert "error" not in agent_chat.execute_read(a, kind, nome, {"start_date": ini, "end_date": fim})


# ── recusa no app × WhatsApp ───────────────────────────────────────────────────

def test_recusa_no_app_de_varios_meses_inteiros_com_a_lista(mundo):
    a, _ = mundo
    ini, fim = PRIMEIRO_M0.isoformat(), ULTIMO.isoformat()
    for nome, extra in (("get_top_categories", {}), ("get_category_spend", {"categoria": "mercado"})):
        r = _com_emissor(lambda: _tool(nome, a, start_date=ini, end_date=fim, **extra))
        assert r["error"] == "periodo_no_card" and f"de={M0} e ate={M}" in r["message"], r
    r = _com_emissor(lambda: _tool("get_top_categories", a, start_date=INICIO.isoformat(), end_date=fim))
    assert r["error"] == "mes_inteiro_no_card"  # 1 mês segue indo para `categorias`
    r = _com_emissor(lambda: _tool("get_period_summary", a, start_date=ini, end_date=fim))
    assert r["error"] == "periodo_no_card" and f"de={M0} e ate={M}" in r["message"]  # F3: o resumo também vai ao card
    r = _com_emissor(lambda: _tool("get_top_categories", a, start_date=_mes(-14) + "-01", end_date=date.today().isoformat()))
    assert r["error"] == "periodo_no_card" and "12 meses" in r["message"]  # passou de 12: o modelo explica o limite


def test_sem_emissor_responde_pela_fatura_e_periodo_livre_responde(mundo):
    a, _ = mundo
    r = _tool("get_top_categories", a, start_date=PRIMEIRO_M0.isoformat(), end_date=ULTIMO.isoformat(), limit=20)
    assert "error" not in r and round(sum(c["total"] for c in r["categories"]), 2) == 490.0  # cartão pela fatura
    r = _com_emissor(lambda: _tool("get_top_categories", a, start_date=INICIO.replace(day=3).isoformat(),
                                   end_date=INICIO.replace(day=8).isoformat()))
    assert "error" not in r  # período livre no app


# ── plano e isolamento ─────────────────────────────────────────────────────────

def test_essencial_recebe_os_meses_cortados_pelo_historico():
    uid = completo(usuario_pagante(plan="essencial"))
    antigo = datetime.combine(date.today() - timedelta(days=150), datetime.min.time()).replace(hour=12)
    db.add_launch_and_update_balance(uid, "despesa", 111, "antigo", None, criado_em=antigo)
    db.add_launch_and_update_balance(uid, "despesa", 22, "recente", None)
    r, p = _periodo(uid, de=_mes(-5), ate=AGORA)
    assert len(p["meses"]) == 6 and p["meses"][0]["saiu"] == "0.00" and _dec(p["saiu"]) == 22
    assert p["historico_desde"] and "inicio_do_historico" in p["motivos"]


def test_resumo_do_periodo_diz_desde_quando_o_plano_enxerga():
    """WhatsApp: `get_period_summary` devolvia o início PEDIDO e `historico_desde` ausente, enquanto as
    tools de lista já devolviam o corte em `start_date`: o modelo dizia "maio a outubro" com dado só
    desde o corte. Agora as três levam `historico_desde` (a semântica do card: o corte quando cortou)."""
    from core.services.plan_service import history_earliest_date
    uid = completo(usuario_pagante(plan="essencial"))
    antigo = datetime.combine(date.today() - timedelta(days=150), datetime.min.time()).replace(hour=12)
    db.add_launch_and_update_balance(uid, "despesa", 111, "antigo", None, criado_em=antigo)
    db.add_launch_and_update_balance(uid, "despesa", 22, "recente", None)
    ini, fim = _mes(-5) + "-01", date.today().isoformat()
    corte = history_earliest_date(uid, now_tz())
    r = _tool("get_period_summary", uid, start_date=ini, end_date=fim)
    assert corte.isoformat() > ini and r["historico_desde"] == corte.isoformat()
    assert "inicio_do_historico" in r["motivos"] and "Limite do plano" in r["nota"] and "incompleto" not in r["nota"]
    top = _tool("get_top_categories", uid, start_date=ini, end_date=fim)
    assert top["start_date"] == top["historico_desde"] == r["historico_desde"]  # as tools de lista dizem o mesmo
    # positivo: quem enxerga tudo não leva corte (None, não a data pedida)
    assert _tool("get_period_summary", uid=completo(usuario_pagante(plan="plus")), start_date=ini, end_date=fim)["historico_desde"] is None
    # a regra do cartão vem também nas listas (a regra 10 do prompt manda dizê-la): meses inteiros = fatura; livre = data da compra
    assert {r["cartao_por"], top["cartao_por"]} == {"fatura"}
    meio = date.today().replace(day=15).isoformat()
    for nome in ("get_top_categories", "get_largest_expenses"):
        assert _tool(nome, uid, start_date=meio, end_date=meio)["cartao_por"] == "data_da_compra"
        assert _tool(nome, uid, start_date=_mes(0) + "-01", end_date=fim)["cartao_por"] == "fatura"
    assert _tool("get_category_spend", uid, start_date=ini, end_date=fim, categoria="x")["cartao_por"] == "fatura"


def test_corte_exatamente_no_dia_1_do_periodo_nao_tem_historico_desde(monkeypatch):
    """O mês pedido está inteiro: o motivo vale, mas `historico_desde` é None (não a data do pedido). Pega `>` -> `>=`."""
    from core.services import plan_service
    from db import mes_detalhes
    uid = completo(usuario_pagante(plan="plus"))
    ini, fim = _mes(0) + "-01", date.today().isoformat()  # 1 mês: o motivo sai com o corte no dia 1
    corte = date.fromisoformat(ini)
    monkeypatch.setattr(plan_service, "history_earliest_date", lambda *a, **k: corte)
    monkeypatch.setattr(mes_detalhes, "history_earliest_date", lambda *a, **k: corte)
    for nome in ("get_period_summary", "get_top_categories"):
        r = _tool(nome, uid, start_date=ini, end_date=fim)
        assert r["historico_desde"] is None and "inicio_do_historico" in r["motivos"], nome
    # positivo: um dia depois do dia 1 já há data
    corte = corte + timedelta(days=1)
    for nome in ("get_period_summary", "get_top_categories"):
        assert _tool(nome, uid, start_date=ini, end_date=fim)["historico_desde"] == corte.isoformat(), nome


def test_periodo_de_a_nao_tem_nada_de_b(mundo):
    a, b = mundo
    ra, pa = _periodo(a, de=M0, ate=M)
    _, pb = _periodo(b, de=M0, ate=M)
    texto = json.dumps([pa, ra])
    assert "SEGREDO" not in texto
    assert not valores_de_b_em([pa, ra])  # número exato: "7777" também é trecho de id ("l17777") e de hora
    ids_a = {m["id"] for c in pa["categorias"] for m in c["maiores"]}
    ids_b = {m["id"] for c in pb["categorias"] for m in c["maiores"]}
    assert ids_a and ids_b and not (ids_a & ids_b)  # ids de linha são TEXTO (l123/c45)
    assert _dec(pb["entrou"]) == 5555 and _dec(pa["entrou"]) == 1040


def test_isolamento_pega_leitor_que_le_o_outro_usuario(mundo, monkeypatch):
    """Controle negativo: o leitor do `periodo` passa a ler B; a checagem de marca de B tem de ver."""
    a, b = mundo
    real = cards.registro()
    vaza = cards.Card(lambda uid, args: real["periodo"].ler(b, args), real["periodo"].visao)
    monkeypatch.setattr(cards, "registro", lambda: {**real, "periodo": vaza})
    _, p = _periodo(a, de=M0, ate=M)
    with pytest.raises(AssertionError):  # a checagem de marca do teste acima tem de ver o que era de B
        assert not valores_de_b_em(p)


# ── contrato com o app ─────────────────────────────────────────────────────────

def _conforme(real, fixture, caminho="periodo"):
    """Chaves E tipos, recursivamente (listas pelo 1º elemento); `None` de um lado vale por qualquer valor."""
    if real is None or fixture is None:
        return
    assert type(real) is type(fixture), (caminho, type(real), type(fixture))
    if isinstance(fixture, dict):
        assert real.keys() == fixture.keys(), (caminho, real.keys(), fixture.keys())
        for k in fixture:
            _conforme(real[k], fixture[k], f"{caminho}.{k}")
    elif isinstance(fixture, list) and fixture and real:
        _conforme(real[0], fixture[0], caminho + "[0]")


def test_bloco_real_tem_a_forma_da_fixture_do_app(mundo):
    from pathlib import Path
    fix = json.loads((Path(__file__).parent / "fixtures" / "ai_chat_bloco_periodo.json").read_text())
    a, _ = mundo
    _, p = _periodo(a, de=M0, ate=M)
    _conforme(p, fix)
    assert any(c["maiores"] for c in p["categorias"])  # o 1º `maiores` real tem todas as chaves do lançamento
    _conforme(next(c["maiores"] for c in p["categorias"] if c["maiores"]), fix["categorias"][0]["maiores"])


# ── runner ─────────────────────────────────────────────────────────────────────

def test_excecao_em_tool_de_leitura_vira_falha_interna_sem_vazar(monkeypatch, pro_user_id):
    def explode(*a, **k):
        raise RuntimeError("psycopg: SELECT segredo FROM users")
    monkeypatch.setattr(launches, "_resumo_periodo", explode)
    reg = openai_falso(monkeypatch, rodada(None, ("get_period_summary", {})), rodada("Não consegui ler agora."))
    assert runner.chat(pro_user_id, "quanto gastei", monthly_limit=100) == "Não consegui ler agora."
    historico = json.dumps(reg.creates[1]["messages"])
    assert "falha_interna" in historico and "segredo" not in historico


def test_ultima_volta_vai_sem_tool_e_responde_texto(monkeypatch, pro_user_id):
    n = runner.MAX_TOOL_LOOPS
    reg = openai_falso(monkeypatch, *[rodada(None, ("get_balance", {})) for _ in range(n - 1)], rodada("Com o que já li: ok."))
    r = runner.chat(pro_user_id, "e o saldo?", monthly_limit=100)
    assert r == "Com o que já li: ok." and r != runner.ERROR_MSG
    assert [c.get("tool_choice") for c in reg.creates] == [None] * (n - 1) + ["none"]


def test_turno_normal_nao_manda_tool_choice(monkeypatch, pro_user_id):
    reg = openai_falso(monkeypatch, rodada("Oi!"))
    assert runner.chat(pro_user_id, "oi", monthly_limit=100) == "Oi!"
    assert "tool_choice" not in reg.creates[0]


def test_fallback_do_whatsapp_usa_o_mesmo_erro_do_runner(monkeypatch, pro_user_id):
    from core.services import ai_chat_commands as cmds
    assert "falha do meu lado" in runner.ERROR_MSG and "Deu ruim" not in runner.ERROR_MSG
    monkeypatch.setattr("core.services.ai_chat.chat", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert cmds.handle_ai_chat_command(pro_user_id, "piggy oi", "whatsapp") == runner.ERROR_MSG


# ── revisão: corte do plano no meio do mês, escrita, números ──────────────────

def _lanc_mes(uid, valor, dia, categoria="mercado", tipo="despesa", moeda="BRL"):
    from tests.test_resumo_mes_regra import _em, q
    return q("""insert into launches (user_id, tipo, valor, criado_em, categoria, currency)
                values (%s, %s, %s, %s, %s, %s) returning id""", (uid, tipo, valor, _em(dia), categoria, moeda))


def _ym(ano_mes: str) -> tuple[int, int]:
    return int(ano_mes[:4]), int(ano_mes[5:])


def test_corte_do_plano_no_meio_do_1o_mes_vale_nos_totais_e_nas_categorias(monkeypatch):
    """Corte no dia 15 de M: o gasto do dia 3 não entra em `meses[0]`, nem no total, nem nas
    categorias; e `meses[0]` bate com `ler` do mesmo mês."""
    from db import mes_detalhes
    uid = usuario_pagante(plan="plus")
    _lanc_mes(uid, 100, 3)
    _lanc_mes(uid, 1000, 4, "salario", "receita")
    _lanc_mes(uid, 40, 20)
    _lanc_mes(uid, 500, 25, "salario", "receita")
    corte = INICIO.replace(day=15)
    monkeypatch.setattr(mes_detalhes, "history_earliest_date", lambda *a, **k: corte)
    p = mes_detalhes.ler_periodo(uid, _ym(M), _ym(AGORA), now_tz())
    mes = mes_detalhes.ler(uid, *_ym(M), now_tz())
    assert (p["meses"][0]["entrou"], p["meses"][0]["saiu"]) == (mes["entrou"], mes["saiu"]) == (Decimal(500), Decimal(40))
    assert p["saiu"] == Decimal(40) and sum(c["valor"] for c in p["categorias"]) == Decimal(40)
    assert p["historico_desde"] == corte.isoformat() and "inicio_do_historico" in p["motivos"]
    # positivo: sem corte entra tudo
    monkeypatch.setattr(mes_detalhes, "history_earliest_date", lambda *a, **k: None)
    assert mes_detalhes.ler_periodo(uid, _ym(M), _ym(AGORA), now_tz())["saiu"] == Decimal(140)


def test_periodo_livre_com_7_categorias_soma_todas():
    uid = usuario_pagante(plan="plus")
    for i, v in enumerate((10, 20, 30, 40, 50, 60, 70, 80)):
        _lanc_mes(uid, v, 4, f"cat{i}")
    ini, fim = INICIO.replace(day=3).isoformat(), INICIO.replace(day=8).isoformat()
    r = _tool("get_period_summary", uid, start_date=ini, end_date=fim)
    assert r["despesa"] == 360.0 and r["cartao_por"] == "data_da_compra"


def test_maiores_com_6_linhas_corta_em_5_e_percentual_e_sobre_as_categorias():
    from api.nativo.mes_detalhes import periodo
    uid = usuario_pagante(plan="plus")
    for v in (1, 2, 3, 4, 5, 6):
        _lanc_mes(uid, v, 4, "mercado")
    _lanc_mes(uid, 15, 5, "bar", moeda="USD")  # entra em `saiu`, fora das categorias
    p = periodo(_ym(M), _ym(M), uid)
    assert [c.valor for c in p.categorias] == [Decimal(21)] and len(p.categorias[0].maiores) == 5
    assert [m.valor for m in p.categorias[0].maiores] == [Decimal(v) for v in (6, 5, 4, 3, 2)]
    assert p.saiu > p.categorias[0].valor and p.categorias[0].percentual == 100  # base: Σ categorias, não `saiu`


def test_excecao_em_escrita_auto_executada_nao_vai_ao_historico(monkeypatch):
    from dataclasses import replace
    real = get_tool("add_launch")

    def explode(uid, args):
        raise RuntimeError("psycopg SELECT segredo")
    monkeypatch.setattr(runner, "get_tool", lambda n: replace(real, execute=explode))
    tok = CURRENT_USER_MESSAGE.set("gastei 1 real")  # a âncora de valor lê o texto do usuário
    try:
        historico, terminal = runner._dispatch_tool(1, "add_launch", {"tipo": "despesa", "valor": 1})
    finally:
        CURRENT_USER_MESSAGE.reset(tok)
    assert "falha_interna" in historico and "segredo" not in historico and terminal == runner.ERROR_MSG


def test_show_card_so_marca_o_teto_de_12_na_renda(mundo):
    a, _ = mundo
    r, _, _ = mostrar(a, card="contas", quantidade_meses=50)
    assert "limitado_a_12_meses" not in r


def test_mensagens_do_periodo_apontam_o_caminho_certo(mundo):
    a, _ = mundo
    t = CARDS_DO_TURNO.set([])
    try:
        mostrar(a, card="periodo", de=M0, ate=M)  # 1ª: gasta a vaga
        r, _, _ = mostrar(a, card="periodo", de=M0, ate=M)
    finally:
        CARDS_DO_TURNO.reset(t)
    assert r["error"] == "card_repetido" and "compare_periods" in r["message"] and "meses=[" not in r["message"]
    r, _, _ = mostrar(a, card="periodo", de=M0, ate=M, meses=[M0, M])
    assert r["error"] == "meses_demais" and "só de e ate" in r["message"]
    r, _, _ = mostrar(a, card="resumo", meses=[M0, M])  # positivo: os outros cards seguem com o texto antigo
    assert r["error"] == "meses_demais" and "show_card periodo" in r["message"]


def test_recusa_periodo_no_card_so_com_o_fim_cortado_nao_cita_o_teto_de_12(mundo):
    a, _ = mundo
    ini = _mes(-1) + "-01"
    fim_futuro = (date.today().replace(day=1) + timedelta(days=100)).isoformat()  # "este ano": entra no futuro
    r = _com_emissor(lambda: _tool("get_top_categories", a, start_date=ini, end_date=fim_futuro))
    assert r["error"] == "periodo_no_card" and f"de={_mes(-1)} e ate={AGORA}" in r["message"], r
    assert launches._AVISO_FIM in r["message"] and "12 meses" not in r["message"]  # só o corte que houve
    r = _com_emissor(lambda: _tool("get_top_categories", a, start_date=_mes(-20) + "-01", end_date=fim_futuro))
    assert launches._AVISO_FIM in r["message"] and launches._AVISO_12 in r["message"]  # os dois


def test_compare_periods_repassa_o_fim_ajustado_so_no_lado_cortado(mundo):
    a, _ = mundo
    fim_futuro = (date.today().replace(day=1) + timedelta(days=100)).isoformat()
    r = _tool("compare_periods", a, period_a_start=_mes(-3) + "-01", period_a_end=_mes(-2) + "-15",
              period_b_start=_mes(-1) + "-01", period_b_end=fim_futuro)
    assert r["atual"]["fim_ajustado"] is True and r["atual"]["aviso_fim"] == launches._AVISO_FIM
    assert r["atual"]["pedido"]["end_date"] == fim_futuro
    assert "fim_ajustado" not in r["anterior"] and "aviso_fim" not in r["anterior"]


def test_schemas_nao_publicam_maximum_para_o_modelo_poder_pedir_18():
    q = get_tool("show_card").schema["function"]["parameters"]["properties"]["quantidade_meses"]
    m = get_tool("get_spending_trend").schema["function"]["parameters"]["properties"]["months"]
    assert "maximum" not in q and "maximum" not in m


def test_category_spend_livre_diz_fatura_porque_e_pela_fatura_que_soma():
    """Período livre: `_janela` diz data_da_compra, mas `sum_spent_in_category_period` soma o cartão pela
    fatura. Compra de 80 no dia 5 (cartão fecha dia 28), período 5 a 8: total 0 e o rótulo não pode
    dizer que o cartão entrou pela data da compra. Pega: tirar o `cartao_por` fixo de `_get_category_spend`."""
    uid = completo(usuario_pagante(plan="plus"))
    passado = (date.today().replace(day=1) - timedelta(days=1)).replace(day=5)
    db.add_credit_purchase(uid, db.create_card(uid, "Nubank", closing_day=28, due_day=5), 80, "mercado", "vista", passado)
    ini, fim = passado.isoformat(), (passado + timedelta(days=3)).isoformat()
    cs = _tool("get_category_spend", uid, start_date=ini, end_date=fim, categoria="mercado")
    assert cs["total"] == 0 and cs["cartao_por"] == "fatura"
    # as outras listas somam o período livre pela data da compra e dizem isso
    assert _tool("get_top_categories", uid, start_date=ini, end_date=fim)["cartao_por"] == "data_da_compra"


def test_nota_do_resumo_continua_dizendo_incompleto_para_motivo_que_nao_e_do_plano(monkeypatch):
    """Só `inicio_do_historico` deixou de ser "incompleto"; `banco_desatualizado` etc. seguem avisando.
    Pega: apagar o ramo `outros` de `_resumo_periodo`."""
    real = launches.resumo_mes.resumo_do_mes
    monkeypatch.setattr(launches.resumo_mes, "resumo_do_mes", lambda *a, **k: {**real(*a, **k), "motivos": ["banco_desatualizado"]})
    uid = completo(usuario_pagante(plan="plus"))
    r = _tool("get_period_summary", uid, start_date=_mes(0) + "-01", end_date=date.today().isoformat())
    assert "banco_desatualizado" in r["motivos"] and "desatualizado ou incompleto (banco_desatualizado)" in r["nota"]
