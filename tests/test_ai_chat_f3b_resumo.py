"""F3b da IA, parte 2: o card de gasto entrega ao modelo só o RESUMO (a tabela está na tela, o bloco segue inteiro), a
categoria citada vem em `categoria_pedida`, e a fatura do app traz só as 3 maiores compras. Conversa pelo runner
(OpenAI falsa, banco real). Parte 1 (comparar e janelas): `test_ai_chat_f3b.py`."""
import json
from decimal import Decimal

import pytest

import db
from _cards_apoio import M, M0, mostrar, mundo  # noqa: F401  (mundo é fixture)
from _ia_stream_helpers import executar, openai_falso, rodada
from core.services.ai_chat import system_prompt as sp
from core.services.ai_chat._context import CARDS_DO_TURNO
from core.services.ai_chat.tools import SCHEMAS, SCHEMAS_APP, get_tool, mostrar_card
from test_ai_chat_f3 import _tool_msgs
from test_ai_chat_f3b import _dias
from test_ai_chat_uma_fonte_app import com_emissor
from conftest import usuario_pagante
from test_api_app_sessao import completo
from utils_date import today_tz


# ── mudança 3: o modelo recebe o resumo; o bloco do app segue inteiro ────────────────────────────

def _cats(bloco, recurso="periodo"):
    r = bloco["abas"][0]["recursos"][recurso]
    return r["categorias"]


def test_visao_do_periodo_e_o_resumo_e_o_bloco_traz_a_tabela_inteira(mundo):
    a, _ = mundo
    r, (bloco,), _ = mostrar(a, card="periodo", de=M0, ate=M)
    visao, p = r["abas"][0], bloco["abas"][0]["recursos"]["periodo"]
    assert len(p["categorias"]) >= 4 and len(p["meses"]) == 2  # o bloco: a tabela inteira
    assert "meses" not in visao and len(visao["categorias"]) == 3 and visao["categorias_total"] == len(p["categorias"])
    maiores = sorted((Decimal(c["valor"]) for c in p["categorias"]), reverse=True)[:3]
    assert [Decimal(str(c["valor"])) for c in visao["categorias"]] == maiores  # as 3 maiores, em ordem
    saiu = {m["mes"]: m["saiu"] for m in p["meses"] if m["entrou"] != "0.00" or m["saiu"] != "0.00"}
    assert visao["maior_mes"] == {"mes": max(saiu, key=lambda k: Decimal(saiu[k])), "saiu": float(max(map(Decimal, saiu.values())))}
    assert visao["menor_mes"]["saiu"] == float(min(map(Decimal, saiu.values())))
    assert {"inicio", "fim", "entrou", "saiu", "saldo", "motivos"} <= set(visao)


def test_categoria_pedida_e_o_valor_do_get_category_spend_e_sem_categoria_ela_nao_vem(mundo, monkeypatch):
    a, _ = mundo
    _, (bloco,), _ = mostrar(a, card="periodo", de=M0, ate=M)
    quarta = _cats(bloco)[3]["categoria"]  # fora do top 3 da visão
    pedido = {"start_date": _dias(M0)[0], "end_date": _dias(M)[1], "categoria": quarta}
    fora = get_tool("get_category_spend").execute(a, pedido)
    openai_falso(monkeypatch,
                 rodada(None, ("get_category_spend", pedido)),
                 rodada(None, ("show_card", {"card": "periodo", "de": M0, "ate": M, "categoria": quarta})),
                 rodada("Veja o card."))
    executar(a, f"{quarta} de {M0} a {M}")
    recusa, card = _tool_msgs(a)[:2]
    assert recusa["error"] == "periodo_no_card" and recusa["message"].endswith(f' e categoria={json.dumps(quarta, ensure_ascii=False)}')
    assert card["abas"][0]["categoria_pedida"] == {"categoria": quarta, "valor": fora["total"]}  # fonte única

    # negativo: sem `categoria` o valor da 4ª não está na visão
    r, _, _ = mostrar(a, card="periodo", de=M0, ate=M)
    assert "categoria_pedida" not in r["abas"][0] and fora["total"] not in {c["valor"] for c in r["abas"][0]["categorias"]}


def test_categoria_pedida_casa_sem_acento_nem_caixa_e_a_inexistente_nao_traz_numero(mundo):
    a, _ = mundo
    _, (bloco,), _ = mostrar(a, card="periodo", de=M0, ate=M)
    todas = {c["categoria"]: c["valor"] for c in _cats(bloco)}
    assert "farmacia" in todas  # o bloco guarda sem acento; o usuário digita com
    r, _, _ = mostrar(a, card="periodo", de=M0, ate=M, categoria=" FARMÁCIA ")
    assert r["abas"][0]["categoria_pedida"] == {"categoria": "farmacia", "valor": float(todas["farmacia"])}
    r, _, _ = mostrar(a, card="categorias", meses=[M], categoria="Sem Categoria")  # o vazio é "sem categoria"
    assert r["abas"][0]["categoria_pedida"]["categoria"] == "sem categoria" and r["abas"][0]["categoria_pedida"]["valor"] > 0
    r, _, _ = mostrar(a, card="periodo", de=M0, ate=M, categoria="viagem")
    assert r["abas"][0]["categoria_pedida"] == {"categoria": "viagem", "encontrada": False}


@pytest.mark.parametrize("categoria,erro", [(5, True), ("x" * 81, True), ("x" * 80, False), ("   ", False), (None, False)])
def test_categoria_do_show_card_e_validada(mundo, categoria, erro):
    a, _ = mundo
    r, blocos, _ = mostrar(a, card="categorias", meses=[M], categoria=categoria)
    assert (r.get("error") == "categoria_invalida") == erro and bool(blocos) != erro


def test_categorias_resume_a_aba_com_saldo_e_sem_as_origens(mundo):
    a, _ = mundo
    r, (bloco,), _ = mostrar(a, card="categorias", meses=[M0, M])
    for aba, bl in zip(r["abas"], bloco["abas"]):
        d = bl["recursos"]["detalhes"]
        assert len(aba["categorias"]) == min(1, len(d["categorias"])) and aba["categorias_total"] == len(d["categorias"])  # 2 abas: a maior (F3c)
        assert aba["saldo"] == float(Decimal(d["entrou"]) - Decimal(d["saiu"])) and "origens" not in aba
        assert [c["valor"] for c in aba["categorias"]] == [float(c["valor"]) for c in d["categorias"][:1]]


def test_renda_resume_pela_soma_das_linhas_do_bloco_e_o_calendario_so_traz_o_maior_dia(mundo):
    a, _ = mundo
    r, (bloco,), _ = mostrar(a, card="renda", quantidade_meses=4)
    linhas, v = bloco["abas"][0]["recursos"]["renda"]["evolution"], r["abas"][0]
    entrou, saiu = (sum(Decimal(str(l[k])) for l in linhas) for k in ("income", "expense"))
    assert (v["entrou"], v["saiu"], v["saldo"]) == (float(entrou), float(saiu), float(entrou - saiu)) and saiu > 0
    assert "evolution" not in v and v["months"] == 4 and "tendencia" in v
    com = [l for l in linhas if l["income"] or l["expense"]]
    assert v["maior_mes"] == {"mes": max(com, key=lambda l: l["expense"])["month"], "saiu": max(l["expense"] for l in com)}
    assert v["menor_mes"]["saiu"] == min(l["expense"] for l in com)
    # o valor de UM mês ("quanto entrou em setembro") sai daqui: sem `por_mes` o modelo só tem a soma da janela inteira
    assert [(m["mes"], m["receita"], m["despesa"]) for m in v["por_mes"]] == [(l["month"], float(Decimal(str(l["income"]))), float(Decimal(str(l["expense"])))) for l in linhas]

    r, (bloco,), _ = mostrar(a, card="calendario", meses=[M])
    dias, v = bloco["abas"][0]["recursos"]["detalhes"]["dias"], r["abas"][0]
    assert "dias" not in v and v["entrou"] > 0
    maior = max(dias, key=lambda d: Decimal(d["saiu"]))
    assert v["maior_dia"] == {"dia": maior["dia"], "saiu": float(maior["saiu"])}


def test_prompt_e_schema_levam_a_categoria_e_o_resumo_so_no_app():
    for metas in (False, True):
        p = sp.prompt_do_usuario(metas, True)
        assert 'passe categoria="mercado" e o valor sai de `categoria_pedida`' in p and "só o resumo" in p
        assert "todos_os_itens=true" in p and "encontrada=false" in p and "get_category_spend" in p
        assert "ele devolve o número em texto, sem abrir outro card" in p and "com o card já na tela" not in p
        assert "Nos cards de gasto periodo, categorias, renda e calendario o resultado traz só o resumo" in p  # T3 só vale p/ os 4
        assert "O resultado do card traz só o resumo" not in p
        assert "siga a `message` (ela diz se há \"e mais\" e quantas)" in p and "menos itens que" not in p  # o "e mais" é da message, que sabe se houve corte
        assert 'diga "e mais N"' not in p and "`itens_total` é quantas são" not in p
        assert "categoria_pedida" not in sp.prompt_do_usuario(metas, False)  # o WhatsApp não tem card
    for schema in (mostrar_card.SCHEMA, mostrar_card.SCHEMA_METAS):
        desc = schema["function"]["parameters"]["properties"]["categoria"]["description"]
        assert desc.startswith("categoria: só para periodo/categorias") and "categoria_pedida" in desc


# ── categoria_pedida: a chave exata do get_category_spend primeiro, o texto solto só se for único ──────────────

def _lista(*nomes):
    return {"categorias": [{"categoria": n, "valor": float(10 * (i + 1))} for i, n in enumerate(nomes)]}


@pytest.mark.parametrize("nomes,pedida,esperado", [
    (("e-commerce", "casa"), "e commerce", {"categoria": "e-commerce", "valor": 10.0}),            # sem chave exata: o único candidato
    (("e commerce", "e-commerce"), "E COMMERCE", {"categoria": "e commerce", "valor": 10.0}),       # a chave exata vence o texto solto
    (("e-commerce", "e.commerce"), "e commerce", {"categoria": "e commerce", "encontrada": False}),  # ambíguo: sem número
    ((None, "casa"), "🛒", {"categoria": "🛒", "encontrada": False}),                               # só símbolo não vira "sem categoria"
    ((None, "casa"), "SEM CATEGORIA", {"categoria": "sem categoria", "valor": 10.0}),               # positivo: o vazio é "sem categoria"
    (("farmácia",), "Farmacia", {"categoria": "farmácia", "valor": 10.0}),                          # positivo: caixa e acento
    (("Café", "cafe-"), "cafe", {"categoria": "Café", "valor": 10.0}),                             # só a dobra de acento da chave desempata: o texto solto acharia 2
])
def test_categoria_pedida_casa_pela_chave_exata_e_so_cai_no_texto_solto_se_for_unico(nomes, pedida, esperado):
    from core.services.ai_chat.cards_resumos import _top3
    assert _top3(_lista(*nomes), pedida)["categoria_pedida"] == esperado


def test_cat_key_em_python_e_igual_a_cat_key_sql_no_banco():
    """Fonte única (§0.7): o Python reimplementa a chave que o SQL calcula. Limite: grego, İ e dígrafos divergem por locale."""
    from db.connection import cat_key, cat_key_sql, get_conn
    textos = ["Café", "CAFÉ", "Ação", "AÇÃO", "pingüino", "Mañana", "MAÑANA", "Niño", "ÀÈÌÒÙ âêîôû", "  Farmácia  ",
              "e-commerce", "Sem Categoria", "", "   ", None]
    with get_conn() as conn, conn.cursor() as cur:
        for t in textos:
            cur.execute(f"select {cat_key_sql('%s::text')} as k", (t,))
            assert cat_key(t) == cur.fetchone()["k"], t


def test_conversa_categoria_nao_encontrada_manda_ao_get_category_spend_que_devolve_o_dado(mundo, monkeypatch):
    """`encontrada: false` não é zero: com o `periodo` já na tela a recusa libera o dado."""
    a, _ = mundo
    datas = {"start_date": _dias(M0)[0], "end_date": _dias(M)[1], "categoria": "viagem"}
    openai_falso(monkeypatch,
                 rodada(None, ("show_card", {"card": "periodo", "de": M0, "ate": M, "categoria": "viagem"})),
                 rodada(None, ("get_category_spend", datas)),
                 rodada("Pronto."))
    executar(a, "quanto gastei com viagem?")
    card, dado = _tool_msgs(a)[:2]
    assert card["abas"][0]["categoria_pedida"] == {"categoria": "viagem", "encontrada": False}
    assert "error" not in dado and dado["total"] == 0


def test_recusa_de_get_category_spend_com_categoria_acima_do_teto_devolve_o_dado(mundo):
    a, _ = mundo
    args = {"start_date": _dias(M0)[0], "end_date": _dias(M)[1]}
    ex = get_tool("get_category_spend").execute
    assert com_emissor(ex, a, {**args, "categoria": "x" * 80})["error"] == "periodo_no_card"  # positivo: no teto, vai ao card
    assert "error" not in com_emissor(ex, a, {**args, "categoria": "x" * 81})  # acima dele o show_card recusaria: o dado


# ── os derivados em Decimal e o calendário sem gasto ─────────────────────────────────────────────

def test_renda_soma_em_decimal_as_linhas_com_centavos(mundo):
    from tests.test_resumo_mes_regra import _em
    uid = completo(usuario_pagante(plan="pro_max"))
    for tipo, valor, quando in (("receita", 0.10, None), ("despesa", 0.10, None), ("receita", 0.20, _em(5)), ("despesa", 0.10, _em(5))):
        db.add_launch_and_update_balance(uid, tipo, valor, "x", None, categoria="casa" if tipo == "despesa" else None, criado_em=quando)
    r, _, _ = mostrar(uid, card="renda", quantidade_meses=2)
    v = r["abas"][0]
    assert (v["entrou"], v["saiu"], v["saldo"]) == (0.3, 0.2, 0.1)  # em float: 0.30000000000000004 e 0.10000000000000003


def test_categorias_calcula_o_saldo_em_decimal(mundo):
    from tests.test_resumo_mes_regra import _em
    uid = completo(usuario_pagante(plan="pro_max"))
    db.add_launch_and_update_balance(uid, "receita", 1040.10, "x", None, criado_em=_em(5))
    db.add_launch_and_update_balance(uid, "despesa", 490.20, "x", None, categoria="casa", criado_em=_em(5))
    r, _, _ = mostrar(uid, card="categorias", meses=[M])
    assert r["abas"][0]["saldo"] == 549.9  # em float: 549.8999999999999


def test_calendario_sem_gasto_nao_tem_maior_dia(mundo):
    from tests.test_resumo_mes_regra import _em
    uid = completo(usuario_pagante(plan="pro_max"))
    db.add_launch_and_update_balance(uid, "receita", 100, "x", None, criado_em=_em(5))  # um dia com entrada e saída zero
    r, (bloco,), _ = mostrar(uid, card="calendario", meses=[M])
    assert bloco["abas"][0]["recursos"]["detalhes"]["dias"] and r["abas"][0]["maior_dia"] is None


# ── a fatura do app: as 3 maiores da fatura INTEIRA, e a lista só a pedido ───────────────────────

def _fatura(uid, valores):
    cid = db.create_card(uid, "Nubank", 10, 17)
    for v in valores:
        db.add_credit_purchase(uid, cid, v, "lazer", f"compra {v}", today_tz())


def _fatura_no_app(uid, monkeypatch, **args):
    openai_falso(monkeypatch, rodada(None, ("get_open_bill", {"card_name": "Nubank", **args})), rodada("Pronto."))
    executar(uid, "quais as compras da minha fatura do Nubank?")
    return _tool_msgs(uid)[-1]


@pytest.mark.parametrize("args,esperado", [({}, [9999, 55, 54]), ({"todos_os_itens": "false"}, [9999, 55, 54])])
def test_conversa_fatura_com_mais_de_50_compras_as_3_maiores_saem_da_fatura_inteira(pro_user_id, monkeypatch, args, esperado):
    """A compra de R$ 9999 é a mais ANTIGA: fica fora das 50 que `get_open_bill_summary` lê. `"false"` não é `True`."""
    uid = completo(pro_user_id)
    _fatura(uid, [9999, *range(1, 56)])
    fatura = _fatura_no_app(uid, monkeypatch, **args)
    assert fatura["error"] == "total_no_card" and fatura["itens_total"] == 56 and "total" not in fatura
    assert [i["valor"] for i in fatura["items"]] == esperado and "e mais 53" in fatura["message"]


def test_conversa_fatura_as_3_maiores_sao_compras_e_nao_estornos(pro_user_id, monkeypatch):
    """O estorno do OFX é gravado POSITIVO com `is_refund`; o de `add_credit_refund`, negativo. Nenhum entra no corte."""
    uid = completo(pro_user_id)
    _fatura(uid, [10, 20, 30, 40])
    cid, hoje = db.get_card_id_by_name(uid, "Nubank"), today_tz()
    db.add_credit_refund(uid, cid, 7, "lazer", "estorno manual", hoje)
    db.import_credit_ofx_bulk(uid, cid, [{"external_id": "e1", "posted_at": hoje, "valor": 5000, "tipo": "estorno",
                                          "categoria": "lazer", "nota": "estorno ofx"}], "h1", hoje, hoje)
    fatura = _fatura_no_app(uid, monkeypatch)
    assert fatura["itens_total"] == 4 and [i["valor"] for i in fatura["items"]] == [40, 30, 20]  # sem estorno entre as 3 maiores
    assert "e mais 1" in fatura["message"] and "maiores compras" in fatura["message"]
    pedidas = _fatura_no_app(uid, monkeypatch, todos_os_itens=True)  # a lista completa traz os estornos, como antes
    assert len(pedidas["items"]) == 6 and sum(i["is_refund"] for i in pedidas["items"]) == 2 and "e mais" not in pedidas["message"]


def test_conversa_fatura_todos_os_itens_o_corte_conta_compras_e_nao_as_linhas(pro_user_id, monkeypatch):
    """49 compras + 2 estornos mais recentes: as 50 linhas da lista deixam 1 compra de fora, e a mensagem tem de dizer."""
    uid = completo(pro_user_id)
    _fatura(uid, range(1, 50))
    cid, hoje = db.get_card_id_by_name(uid, "Nubank"), today_tz()
    for n in (1, 2):
        db.add_credit_refund(uid, cid, 5, "lazer", f"estorno {n}", hoje)
    fatura = _fatura_no_app(uid, monkeypatch, todos_os_itens=True)
    assert len(fatura["items"]) == 50 and fatura["itens_total"] == 49 and "48 compras mais recentes de 49" in fatura["message"]


def test_conversa_fatura_todos_os_itens_acima_do_teto_diz_que_a_lista_e_limitada(pro_user_id, monkeypatch):
    uid = completo(pro_user_id)
    _fatura(uid, [9999, *range(1, 56)])
    fatura = _fatura_no_app(uid, monkeypatch, todos_os_itens=True)
    assert len(fatura["items"]) == 50 and fatura["itens_total"] == 56
    assert "50 compras mais recentes de 56" in fatura["message"] and "e mais" not in fatura["message"]


@pytest.mark.parametrize("valores,args,instrucao", [
    ([], {}, False), ([10, 20], {}, False), ([10, 20, 30], {}, False),    # sem corte, sem instrução
    ([10, 20, 30, 40, 50], {"todos_os_itens": True}, False),              # a lista toda foi pedida e veio toda
    ([10, 20, 30, 40, 50], {}, True),                                     # positivo: houve corte
])
def test_conversa_fatura_so_manda_citar_as_3_e_o_e_mais_n_quando_houve_corte(pro_user_id, monkeypatch, valores, args, instrucao):
    uid = completo(pro_user_id)
    _fatura(uid, valores)
    fatura = _fatura_no_app(uid, monkeypatch, **args)
    assert fatura["itens_total"] == len(valores) and ("e mais" in fatura["message"]) == instrucao
    assert sorted((i["valor"] for i in fatura["items"]), reverse=True) == sorted(valores, reverse=True)[:3 if not args else 50]


def test_get_open_bill_sem_o_card_ou_no_whatsapp_devolve_todos_os_itens_e_o_total(pro_user_id):
    uid = completo(pro_user_id)
    _fatura(uid, [10, 50, 30, 90, 70])
    fora = get_tool("get_open_bill").execute(uid, {"card_name": "Nubank"})  # sem emissor = WhatsApp
    assert fora["total"] == 250.0 and len(fora["items"]) == 5 and "itens_total" not in fora
    t = CARDS_DO_TURNO.set(["fatura"])  # o card já está na tela: o total volta, e os itens sem corte
    try:
        app = com_emissor(get_tool("get_open_bill").execute, uid, {"card_name": "Nubank"})
    finally:
        CARDS_DO_TURNO.reset(t)
    assert app["total"] == 250.0 and len(app["items"]) == 5 and "itens_total" not in app


def test_todos_os_itens_so_existe_no_schema_do_app():
    props = lambda ss: next(s for s in ss if s["function"]["name"] == "get_open_bill")["function"]["parameters"]["properties"]  # noqa: E731
    assert "todos_os_itens" in props(SCHEMAS_APP) and "todos_os_itens" not in props(SCHEMAS)


def test_get_bill_items_overview_filtra_por_user_id(pro_user_id):
    uid, outro = completo(pro_user_id), completo(usuario_pagante(plan="pro_max"))
    _fatura(uid, [10, 20])
    bill_id = db.get_open_bill_summary(uid, db.get_card_id_by_name(uid, "Nubank"))[0]["id"]
    assert db.get_bill_items_overview(uid, bill_id)[0] == 2
    assert db.get_bill_items_overview(outro, bill_id) == (0, [])  # a fatura de A, lida como B
