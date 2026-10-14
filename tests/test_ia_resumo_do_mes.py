"""As tools de resumo da IA no mês inteiro dão o número da regra única do mês (Q18), pela CONVERSA.

Medido em produção (2026-10-10): com `WA_IA_PRIMEIRO=1`, "gastos em outubro" ia à IA, que chamava
`get_period_summary` (só `launches`) e dizia "Despesas 2.935,25, você está no positivo" quando a
regra do mês (a do /painel e do "Gastos em <mês>") dava 4.357,46: faltavam as compras no cartão.

Os dados são os de `tests/test_resumo_mes_regra.py` no mês anterior: ENTROU 1040 / SAIU 490
contra 1040 / 260 só em `launches` (o cartão soma 230; o estorno de 25 não abate). O modelo é
falso, mas lê o JSON que a tool entregou e escreve o número dele: o que a conversa mostra é o
que a tool devolveu. Período livre (dia 3 a 8) inclui o cartão pela data da compra (Q3): 390, `cartao_por: data_da_compra`. `compare_periods` (PL-07): `mes=` pela fatura, 4 datas pela data da compra.

Controle NEGATIVO (§3), medido: `_meses_inteiros` devolvendo sempre None (tudo vira período livre,
o `get_summary_by_period` de antes) deixa vermelhos os casos de mês de summary/compare/guarda; só
`_janela` devolvendo `by_bill_month=False` deixa vermelhos os de categorias e maiores gastos.
Controle POSITIVO: período livre, mês sem cartão, usuário B e mês futuro seguem como devem.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

import db
from conftest import usuario_pagante
from db import resumo_mes
from core.services.ai_chat.tools import get_tool
from core.services.ai_guard import check
from tests._ia_falsa_helpers import chamada, com_tools, liga_flag, openai_falso, texto
from tests._pendencia_credito_helpers import diga
from tests.test_resumo_mes_regra import FIM, INICIO, _lanc, semeia_a, semeia_b
from utils_date import today_tz

_MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto",
          "setembro", "outubro", "novembro", "dezembro"]
ULTIMO = FIM - timedelta(days=1)  # último dia do mês anterior


def _ultimo_do_mes(d: date) -> date:
    return (d.replace(day=1) + timedelta(days=32)).replace(day=1) - timedelta(days=1)


def _tool(nome, uid, **args):
    return get_tool(nome).execute(uid, args)


def _par():
    a, b = usuario_pagante(), usuario_pagante()
    semeia_a(a)
    semeia_b(b)
    return a, b


def _semeia_corrente(uid):
    """Mês corrente: receita 100, gasto 40 e uma compra de 100 no cartão que fecha no dia 31
    (a fatura fecha depois de hoje, dentro do mês). Regra: entrou 100, saiu 140, saldo -40."""
    cartao = db.create_card(uid, "Nubank", closing_day=31, due_day=10)
    db.add_launch_and_update_balance(uid, "receita", 100, "salário", None)
    db.add_launch_and_update_balance(uid, "despesa", 40, "mercado", None)
    db.add_credit_purchase(uid, cartao, 100, "mercado", "no cartão", today_tz())


def _conversa(monkeypatch, uid, frase, args):
    """A frase pelo `handle_incoming` com o modelo falso: rodada 1 chama a tool, rodada 2 repete
    o número que a tool entregou. Devolve (resposta, JSON da tool)."""
    liga_flag(monkeypatch)
    visto: dict = {}

    def le_a_tool(messages):
        visto.update(json.loads(next(m for m in reversed(messages) if m["role"] == "tool")["content"]))
        return texto(f"🐷 Despesas {visto['despesa']} saldo {visto['saldo_periodo']}")

    openai_falso(monkeypatch, com_tools(chamada("get_period_summary", args)), le_a_tool)
    resposta = diga(uid, frase)
    assert visto, f"o modelo falso não foi chamado: {frase!r} ficou no roteador ({resposta!r})"
    return resposta, visto


@pytest.mark.parametrize("frase", [
    "quanto gastei mês passado",
    f"gastos de {_MESES[INICIO.month - 1]}",
])
def test_mes_anterior_inclui_o_cartao_pela_fatura(monkeypatch, frase):
    a, b = _par()  # o B semeado não pode mexer no número de A
    resposta, d = _conversa(monkeypatch, a, frase,
                            {"start_date": INICIO.isoformat(), "end_date": ULTIMO.isoformat()})
    assert (d["receita"], d["despesa"], d["saldo_periodo"]) == (1040.0, 490.0, 550.0)  # 490, não 465
    assert d["cartao_incluido"] is True and "conciliacao_pendente" in d["motivos"]  # o par pendente do semeia_a
    assert "incompleto" in d["nota"]
    assert (d["start_date"], d["end_date"]) == (INICIO.isoformat(), ULTIMO.isoformat())
    assert "Despesas 490.0" in resposta, resposta


def _hoje_ate(fim):
    hoje = today_tz()
    return {"start_date": hoje.replace(day=1).isoformat(), "end_date": fim(hoje).isoformat()}


@pytest.mark.parametrize("frase, args", [
    (f"gastos em {_MESES[today_tz().month - 1]}", lambda: {}),
    ("quanto gastei esse mês", lambda: _hoje_ate(lambda h: h)),
    (f"como foram meus gastos de {_MESES[today_tz().month - 1]} inteiro", lambda: _hoje_ate(_ultimo_do_mes)),
])
def test_mes_corrente_nas_tres_formas_de_argumento(monkeypatch, frase, args):
    uid = usuario_pagante()
    _semeia_corrente(uid)
    _, d = _conversa(monkeypatch, uid, frase, args())
    assert (d["receita"], d["despesa"], d["saldo_periodo"]) == (100.0, 140.0, -40.0)  # negativo
    assert d["cartao_incluido"] is True


def test_mes_sem_cartao_e_so_launches():
    uid = usuario_pagante()
    _lanc(uid, "despesa", 100, 3)
    _lanc(uid, "receita", 300, 4)
    r = _tool("get_period_summary", uid, start_date=INICIO.isoformat(), end_date=ULTIMO.isoformat())
    assert (r["receita"], r["despesa"], r["saldo_periodo"], r["cartao_incluido"]) == (300.0, 100.0, 200.0, True)


def test_periodo_livre_soma_cartao_pela_data_da_compra():
    a, _ = _par()
    r = _tool("get_period_summary", a, start_date=INICIO.replace(day=3).isoformat(),
              end_date=INICIO.replace(day=8).isoformat())
    assert (r["receita"], r["despesa"]) == (1040.0, 390.0)  # 260 + os 130 do cartão pela data da compra
    assert r["cartao_incluido"] is True and r["cartao_por"] == "data_da_compra" and "motivos" not in r
    assert "data da compra" in r["nota"]


def _futuro():
    prox = _ultimo_do_mes(today_tz()) + timedelta(days=1)
    return prox, _ultimo_do_mes(prox)


def _semeia_futuro(uid, nome="Nubank"):
    """Compra de 300 em 3x hoje, cartão que fecha no dia 31: o mês seguinte tem uma parcela de 100."""
    cartao = db.create_card(uid, nome, closing_day=31, due_day=10)
    db.add_credit_purchase_installments(uid, cartao, 300, "casa", "3x", today_tz(), 3)


def test_mes_futuro_nao_e_erro_nem_zero_inventado_e_bate_com_as_listas():
    """`agent_chat._answer` transforma qualquer `{"error"}` de tool em DataQueryError. O mês futuro
    é o número da regra (parcela já lançada), igual nas listas; não é previsão."""
    uid = usuario_pagante()
    _semeia_futuro(uid)
    prox, fim = _futuro()
    mes = {"start_date": prox.isoformat(), "end_date": fim.isoformat()}
    r = _tool("get_period_summary", uid, **mes)
    assert "error" not in r
    assert (r["receita"], r["despesa"], r["saldo_periodo"], r["cartao_incluido"]) == (0.0, 100.0, -100.0, True)
    assert "mes_futuro" in r["motivos"] and "não é previsão" in r["nota"] and "forecast_month_end" not in r["nota"]
    assert (r["start_date"], r["end_date"]) == (prox.isoformat(), fim.isoformat())
    assert round(sum(i["valor"] for i in _tool("get_largest_expenses", uid, limit=20, **mes)["expenses"]), 2) == 100.0
    assert round(sum(c["total"] for c in _tool("get_top_categories", uid, limit=20, **mes)["categories"]), 2) == 100.0
    assert r["despesa"] == resumo_mes.resumo_do_mes(uid, prox.year, prox.month)["saiu"] == 100


def test_compare_corrente_x_futuro_mantem_o_lado_valido():
    uid = usuario_pagante()
    _semeia_corrente(uid)
    _semeia_futuro(uid, "Itaú")  # +100 no corrente (parcela 1) e +100 no mês seguinte (parcela 2)
    hoje = today_tz()
    prox, fim = _futuro()
    r = _tool("compare_periods", uid, period_a_start=hoje.replace(day=1).isoformat(),
              period_a_end=_ultimo_do_mes(hoje).isoformat(), period_b_start=prox.isoformat(), period_b_end=fim.isoformat())
    assert "error" not in r
    # PL-07: as duas janelas são meses inteiros, então cartão pela FATURA (como o painel e o que a main garantia): A é o
    # anterior (mês corrente: 240 = 40 + 100 do cartão + a parcela 1 do futuro) e B o atual (o mês seguinte, só a
    # parcela 2 = 100, "número da regra"). O lado futuro não derruba o válido; `escolha` diz que não é previsão.
    assert (r["anterior"]["inicio"], r["atual"]["inicio"]) == (hoje.replace(day=1).isoformat(), prox.isoformat())
    assert (r["receita"]["total_anterior"], r["despesa"]["total_anterior"], r["despesa"]["total_atual"]) == (100.0, 240.0, 100.0)
    assert (r["despesa"]["total_delta"], r["receita"]["total_delta"]) == (-140.0, -100.0)
    assert r["base"]["cartao"] == "fatura" and r["motivo"] is None
    assert "só entra o que já está lançado" in r["escolha"] and "não é previsão" in r["escolha"]
    assert "contam zero" not in r["escolha"]
    # o lado futuro traz SÓ o lançado: a parcela 2 do 3x ("casa", 100), igual à parcela 1 do mês corrente
    casa = next(c for c in r["categorias"] if c["categoria"] == "casa")
    assert (casa["total_atual"], casa["total_anterior"], casa["estado"]) == (100.0, 100.0, "igual")
    assert next(c for c in r["categorias"] if c["categoria"] == "mercado")["total_atual"] == 0.0  # nada projetado
    # mês futuro pedido por `mes` não é previsão: erro que diz o mês corrente, para o modelo tentar de novo
    assert f"{hoje:%Y-%m}" in _tool("compare_periods", uid, mes=f"{prox:%Y-%m}")["error"]


def test_mes_futuro_pelo_turno_do_reporter_nao_levanta_data_query_error():
    from types import SimpleNamespace

    from core.services import agent_chat as chat
    from tests.test_agent_chat_conversation import ScriptedClient, response

    prox, fim = _futuro()

    class Call:
        id = "futuro_1"
        function = SimpleNamespace(name="get_period_summary", arguments=json.dumps(
            {"start_date": prox.isoformat(), "end_date": fim.isoformat()}))

        def model_dump(self):
            return {"id": self.id, "type": "function", "function": {
                "name": self.function.name, "arguments": self.function.arguments}}

    client = ScriptedClient([response(calls=[Call()]), response("Ainda não há gasto registrado nesse mês."),
                             response('{"valid":true,"reason":"ok"}')])
    out = chat._answer(client, usuario_pagante(), "reporter", "quanto vou gastar mês que vem?", [], needs_data=True)
    assert "Ainda não há gasto" in out


def test_compare_periods_mes_x_mes_e_mes_x_livre_sem_typeerror():
    a, _ = _par()
    antes = (INICIO - timedelta(days=1)).replace(day=1)  # mês sem dados
    r = _tool("compare_periods", a, period_a_start=antes.isoformat(), period_a_end=(INICIO - timedelta(days=1)).isoformat(),
              period_b_start=INICIO.isoformat(), period_b_end=ULTIMO.isoformat())
    # as duas janelas são meses inteiros (o anterior, vazio): fatura, o MESMO número de `mes=` e do painel
    assert (r["despesa"]["total_atual"], r["despesa"]["total_delta"], r["receita"]["total_delta"]) == (490.0, 490.0, 1040.0)
    assert r["base"]["cartao"] == "fatura" and r["base"]["cartao_lancamentos"] == {"atual": 3, "anterior": 0}
    r = _tool("compare_periods", a, mes=f"{INICIO:%Y-%m}")  # o mesmo mês pelo argumento `mes`: fatura
    assert (r["modo"], r["despesa"]["total_atual"], r["motivo"]) == ("mes_fechado", 490.0, "sem_base")
    assert r["base"]["cartao"] == "fatura"

    r = _tool("compare_periods", a, period_a_start=INICIO.isoformat(), period_a_end=ULTIMO.isoformat(),
              period_b_start=INICIO.replace(day=3).isoformat(), period_b_end=INICIO.replace(day=8).isoformat())
    # dia 3 a 8 (80 + 50 de cartão, comprados no dia 5): 390, o mesmo número que `get_period_summary` dá para o período
    assert (r["despesa"]["total_anterior"], r["despesa"]["total_atual"], r["despesa"]["total_delta"]) == (1389.0, 390.0, -999.0)
    assert r["base"]["cartao"] == "data_da_compra" and r["base"]["cartao_lancamentos"] == {"atual": 2, "anterior": 3}
    assert "data da compra" in r["escolha"]  # uma janela inteira e outra parcial: data da compra nas DUAS, dito na escolha
    livre = _tool("get_period_summary", a, start_date=INICIO.replace(day=3).isoformat(), end_date=INICIO.replace(day=8).isoformat())
    assert livre["despesa"] == r["despesa"]["total_atual"] and livre["cartao_por"] == "data_da_compra"


def test_categorias_e_maiores_gastos_do_mes_somam_a_despesa_da_regra():
    a, _ = _par()
    mes = {"start_date": INICIO.isoformat(), "end_date": ULTIMO.isoformat()}
    despesa = _tool("get_period_summary", a, **mes)["despesa"]
    cats = _tool("get_top_categories", a, limit=20, **mes)["categories"]
    assert round(sum(c["total"] for c in cats), 2) == despesa == 490.0
    itens = _tool("get_largest_expenses", a, limit=20, **mes)["expenses"]
    assert round(sum(i["valor"] for i in itens), 2) == 490.0
    # a parcela do mês vem solta ("1 de 3"), não como compra de 300 em 3x
    assert any(i["parcelamento"] == "parcela 1 de 3 — R$ 100,00 neste período" for i in itens)


def test_categorias_do_mes_corrente_incluem_a_fatura_que_fecha_depois_de_hoje():
    uid = usuario_pagante()
    _semeia_corrente(uid)  # a fatura fecha no dia 31: depois de hoje, dentro do mês
    despesa = _tool("get_period_summary", uid)["despesa"]
    cats = _tool("get_top_categories", uid, limit=20)  # sem datas = dia 1 até hoje
    assert despesa == 140.0 and round(sum(c["total"] for c in cats["categories"]), 2) == 140.0
    assert cats["end_date"] == _ultimo_do_mes(today_tz()).isoformat()
    assert round(sum(i["valor"] for i in _tool("get_largest_expenses", uid, limit=20)["expenses"]), 2) == 140.0


def test_periodo_livre_das_tools_irmas_segue_pela_data_da_compra():
    a, _ = _par()
    # dia 3 a 8: launches (260) + as compras do dia 5 no cartão (80 e 50), pela data da compra
    cats = _tool("get_top_categories", a, limit=20, start_date=INICIO.replace(day=3).isoformat(),
                 end_date=INICIO.replace(day=8).isoformat())["categories"]
    assert round(sum(c["total"] for c in cats), 2) == 260.0 + 80.0 + 50.0


def test_o_numero_da_tool_passa_pelo_ai_guard():
    """O runner serializa com `default=str`: `Decimal` viraria string e a afirmação certa sairia
    como "sem evidência" (o guard só lê número JSON como valor)."""
    a, _ = _par()
    r = _tool("get_period_summary", a, start_date=INICIO.isoformat(), end_date=ULTIMO.isoformat())
    evidencia = json.dumps(r, ensure_ascii=False, default=str)
    claims = check("🐷 Você gastou R$ 490,00 e recebeu R$ 1.040,00.", [evidencia], "gastos do mês")
    assert claims and all(c.supported for c in claims), claims


def test_projecao_avisa_que_nao_tem_o_cartao():
    r = _tool("forecast_month_end", usuario_pagante())
    assert r["cartao_incluido"] is False and "SEM as compras no cartão" in r["nota_metodologia"]


def test_category_spend_do_mes_corrente_inclui_a_fatura_que_fecha_depois_de_hoje():
    uid = usuario_pagante()
    _semeia_corrente(uid)  # mercado: só os 100 no cartão que fecha no dia 31 (os 40 são "outros")
    top = {c["categoria"]: c["total"] for c in _tool("get_top_categories", uid, limit=20)["categories"]}
    r = _tool("get_category_spend", uid, categoria="mercado")
    assert r["total"] == top["mercado"] == 100.0
    assert r["end_date"] == _ultimo_do_mes(today_tz()).isoformat()


def _corta_em(monkeypatch, corte):
    monkeypatch.setattr("core.services.plan_service.history_earliest_date", lambda uid, now=None: corte)


@pytest.mark.parametrize("corte, esperado", [
    (FIM, 0.0),                    # a janela do plano corta o mês todo
    (INICIO.replace(day=6), 360.0),  # corta no meio: os 130 de launches de antes do dia 6 saem; o cartão fica
    (None, 490.0),                 # plano sem limite de histórico
])
def test_corte_da_janela_do_plano_vale_nas_listas_e_no_category_spend(monkeypatch, corte, esperado):
    a, _ = _par()
    _corta_em(monkeypatch, corte)
    mes = {"start_date": INICIO.isoformat(), "end_date": ULTIMO.isoformat()}
    resumo = _tool("get_period_summary", a, **mes)
    top = _tool("get_top_categories", a, limit=20, **mes)
    maiores = _tool("get_largest_expenses", a, limit=20, **mes)
    assert resumo["despesa"] == esperado
    assert round(sum(c["total"] for c in top["categories"]), 2) == esperado
    assert round(sum(i["valor"] for i in maiores["expenses"]), 2) == esperado
    # category_spend de cada categoria do mês SEM corte soma ao mesmo número (inclui as zeradas pelo corte)
    cats = {"sem categoria", "outros", "mercado", "casa", "farmácia"}
    soma = sum(_tool("get_category_spend", a, categoria=c, **mes)["total"] for c in cats)
    assert round(soma, 2) == esperado
    cortou = corte is not None
    if corte == FIM:  # mês todo cortado: lista vazia e o intervalo do mês pedido, nunca início > fim
        for r in (top, maiores, _tool("get_category_spend", a, categoria="mercado", **mes)):
            assert (r["start_date"], r["end_date"]) == (INICIO.isoformat(), ULTIMO.isoformat()), r
        assert top["categories"] == [] and maiores["expenses"] == []
    for r in (resumo, top, maiores, _tool("get_category_spend", a, categoria="mercado", **mes)):
        assert ("inicio_do_historico" in r.get("motivos", [])) is cortou, r
    if cortou and corte != FIM:
        assert top["start_date"] == corte.isoformat()


@pytest.mark.parametrize("corte, motivo", [
    (INICIO, True),                       # o mês pedido começa no corte: `>=` (com `>` o motivo sumia)
    (INICIO.replace(day=2), True),        # corte dentro do mês pedido
    (INICIO - timedelta(days=1), False),  # último dia do mês anterior: o mês pedido está inteiro
])
def test_motivo_inicio_do_historico_na_fronteira_do_dia_1(monkeypatch, corte, motivo):
    """`resumo_do_mes` também levanta o motivo quando o corte cai no mês ANTERIOR (o `anterior` dele
    sai None), mas o mês pedido está inteiro: nas tools, resumo e listas só dizem sim com corte >= dia 1."""
    a, _ = _par()
    _corta_em(monkeypatch, corte)
    mes = {"start_date": INICIO.isoformat(), "end_date": ULTIMO.isoformat()}
    for nome, extra in [("get_period_summary", {}), ("get_top_categories", {"limit": 20}),
                        ("get_largest_expenses", {"limit": 20}), ("get_category_spend", {"categoria": "mercado"})]:
        r = _tool(nome, a, **extra, **mes)
        assert ("inicio_do_historico" in r.get("motivos", [])) is motivo, nome
        if nome == "get_period_summary":  # sem aviso falso de "incompleto" no texto que o modelo lê
            assert ("inicio_do_historico" in r["nota"]) is motivo


@pytest.mark.parametrize("tool, args", [
    ("get_period_summary", {"end_date": "2026-09-31"}),
    ("get_top_categories", {"start_date": "2026-13-01"}),
    ("get_largest_expenses", {"end_date": "2026-09-31"}),
    ("get_category_spend", {"categoria": "mercado", "end_date": "ontem"}),
    ("compare_periods", {"period_a_start": "2026-08-01", "period_a_end": "2026-08-31",
                         "period_b_start": "2026-09-01", "period_b_end": "2026-09-31"}),
])
def test_data_invalida_e_erro_nao_hoje_em_silencio(tool, args):
    erro = _tool(tool, usuario_pagante(), **args)
    campo = next(k for k, v in args.items() if k.endswith("_date") and v in ("2026-09-31", "2026-13-01", "ontem")
                 or k.startswith("period_") and v == "2026-09-31")
    assert erro == {"error": f"data inválida: {campo} (use AAAA-MM-DD)"}


@pytest.mark.parametrize("ruim", ["10/10/2026", "2026-10-10x", "lixo"])
def test_formatos_que_nao_sao_data_iso_seguem_invalidos(ruim):
    assert "AAAA-MM-DD" in _tool("get_period_summary", usuario_pagante(), start_date=ruim)["error"]


_VARIANTES = [lambda d: f"{d}T00:00:00", lambda d: f"{d}T00:00:00Z", lambda d: f"{d} 00:00:00", lambda d: f" {d} "]


@pytest.mark.parametrize("variante", _VARIANTES)
def test_data_com_hora_ou_espacos_vale_a_data_nas_cinco_tools(variante):
    """O modelo manda `2026-10-01T00:00:00`; recusar quebra o turno do agente (erro de tool)."""
    a, _ = _par()
    pura = {"start_date": INICIO.isoformat(), "end_date": ULTIMO.isoformat()}
    suja = {k: variante(v) for k, v in pura.items()}
    for nome, extra in [("get_period_summary", {}), ("get_top_categories", {"limit": 20}),
                        ("get_largest_expenses", {"limit": 20}), ("get_category_spend", {"categoria": "mercado"})]:
        r = _tool(nome, a, **suja, **extra)
        assert "error" not in r, (nome, r)
        assert r == _tool(nome, a, **pura, **extra), nome
    cmp_ = {"period_a_start": INICIO.replace(day=3), "period_a_end": INICIO.replace(day=8),
            "period_b_start": INICIO, "period_b_end": ULTIMO}
    pura = {k: v.isoformat() for k, v in cmp_.items()}
    r = _tool("compare_periods", a, **{k: variante(v) for k, v in pura.items()})
    assert "error" not in r and r == _tool("compare_periods", a, **pura)
    assert (r["despesa"]["total_atual"], r["despesa"]["total_anterior"]) == (1389.0, 390.0)  # B (mês) é o atual, A (dia 3 a 8) o anterior


def test_data_omitida_continua_no_padrao():
    assert "error" not in _tool("get_category_spend", usuario_pagante(), categoria="mercado", end_date="")


def test_usuario_b_com_cartao_e_fatura_no_mesmo_mes_nao_mexe_nas_listas_de_a():
    a = usuario_pagante()
    semeia_a(a)
    mes = {"start_date": INICIO.isoformat(), "end_date": ULTIMO.isoformat()}

    def le():
        return (_tool("get_top_categories", a, limit=20, **mes), _tool("get_largest_expenses", a, limit=20, **mes),
                _tool("get_category_spend", a, categoria="mercado", **mes),
                _tool("get_category_spend", a, categoria="sem categoria", **mes))  # onde caem os launches de B

    antes = le()
    b = usuario_pagante()
    semeia_b(b)  # B: launches 7777/5555 e 888 em "mercado" no cartão, fatura que fecha no mesmo mês
    assert le() == antes
    assert antes[3]["total"] == 230.0
    assert antes[2]["total"] == 80.0  # só a compra à vista de A no cartão; nada dos 888 de B


# No FIM do arquivo: o `get_largest_expenses` do teste do usuário B empata em 100.0 (lançamento x parcela) e a
# ordem do empate muda com o volume de linhas que os testes anteriores deixam; estes não podem vir antes dele.
def test_compare_traz_o_aviso_de_dado_incompleto_da_regra_do_mes():
    a, _ = _par()  # o par de conciliação pendente do semeia_a
    r = _tool("compare_periods", a, mes=f"{INICIO:%Y-%m}")
    assert r["motivos"] == ["conciliacao_pendente"] and "incompleto" in r["nota"]
    limpo = usuario_pagante()
    _lanc(limpo, "despesa", 100, 3)
    r = _tool("compare_periods", limpo, mes=f"{INICIO:%Y-%m}")
    assert r["motivos"] == [] and r["nota"] is None


def test_numero_do_compare_passa_pelo_ai_guard():
    a, _ = _par()
    r = _tool("compare_periods", a, mes=f"{INICIO:%Y-%m}")
    evidencia = json.dumps(r, ensure_ascii=False)  # sem default=
    assert all(c.supported for c in check("🐷 Você gastou R$ 490,00 e recebeu R$ 1.040,00.", [evidencia], "compara"))
    assert any(not c.supported for c in check("🐷 Você gastou R$ 491,00.", [evidencia], "compara"))
