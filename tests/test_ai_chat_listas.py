"""PR C2: "últimos lançamentos" da IA lido pelo `listar` do Extrato (`db/lancamentos_leitura.recentes`).

Banco real. A lista é a do Extrato: os itens em comum com `/api/app/lancamentos` são IDÊNTICOS campo a campo;
as diferenças são declaradas (P-4): a janela vai até o mês SEGUINTE (a compra no cartão feita depois do fechamento,
que o Extrato só mostra no mês da fatura), o guardado entra e o parcelamento vem em UMA linha por data (a menor parcela).

CONTROLES NEGATIVOS (injetados e restaurados na entrega, ver o relato):
  • `listar(..., tipo="saida")` em `recentes` -> `test_itens_em_comum_com_o_extrato_sao_identicos`;
  • `fim` = o do Extrato (dia 1 do mês seguinte) em `recentes` -> `test_compra_depois_do_fechamento_aparece`;
  • `com_guardado=False` em `recentes` -> `test_guardado_aparece`;
  • `_item(r)` sem o `user_seq` -> `test_user_seq_so_nos_l`;
  • `sem_categoria` sem o filtro no `listar` -> `test_sem_categoria_...` (receita, interno e categorizada na lista);
  • o filtro sem `not x.interno` -> `test_sem_categoria_so_a_saida_...` (o saque em espécie e o guardado entram);
  • o filtro sem o `outros` -> `test_sem_categoria_...` (a "Outros" fora);
  • `uma_por_compra` desligado em `recentes` -> `test_parcelado_*` e `test_itens_em_comum_*`;
  • início da janela em M-10 (ou M-12) -> `test_janela_de_12_meses_*`; sem o corte do plano ou sem o motivo ->
    `test_plano_essencial_*`; `categoria` pela chave -> `test_categoria_*`; `descricao` sem o fallback -> `test_compra_sem_nota_*`;
  • `partition by x.group_id` sem `x.dia` -> `test_open_finance_cada_parcela_*`; o `sem_categoria` depois da janela ->
    `test_sem_categoria_so_a_parcela_1_*`; `categoria_cru` nulo ou `descricao` sem o `.strip()` -> `test_compra_de_cartao_com_categoria_*`;
  • `valor` como texto, sem `moeda` ou sem `pode` -> `test_o_modelo_recebe_valor_*`.
CONTROLES POSITIVOS: o item do Extrato continua aparecendo; a compra antes do fechamento está nos dois lados.
"""
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta

import pytest

import db
from conftest import usuario_pagante
from core.services import ai_guard
from core.services.ai_chat.tools.listas_ia import ultimos_lancamentos
from db import lancamentos_leitura
from tests._patrimonio_helpers import q
from tests.test_api_app_sessao import cliente, completo
from utils_date import now_tz, today_tz


def _do_app(uid, **params) -> dict[str, dict]:
    r = cliente(uid).get("/api/app/lancamentos", params={"limite": 100, **params})
    assert r.status_code == 200, r.text
    return {i["id"]: i for i in r.json()["itens"]}


def _bloco(uid, limite=50) -> dict[str, dict]:
    """Os itens da lista da IA como o contrato da v2 os tem (o `numero` fica de fora)."""
    from api.v2.lancamentos import Lancamento
    d = lancamentos_leitura.recentes(uid, now_tz(), limite)
    return {i["id"]: Lancamento(**i).model_dump(mode="json") for i in d["itens"]}


def _compra(uid, nome, fechamento, valor=40):
    card = db.create_card(uid, nome, closing_day=fechamento, due_day=28)
    ct = db.add_imported_credit_purchase(uid, card, valor, "mercado", today_tz(), f"ct-{uid}-{nome}")[0]
    return f"c{ct}"


def _parcelado(uid, nome, parcelas, dias_atras, categoria="compras online", fechamento=28):
    """Compra em N parcelas feita há `dias_atras` dias; devolve o `c<id>` da 1ª parcela."""
    card = db.create_card(uid, f"Banco {nome}", closing_day=fechamento, due_day=10)
    grupo, _ = db.add_credit_purchase_installments(uid, card, 100 * parcelas, categoria, nome,
                                                   today_tz() - timedelta(days=dias_atras), parcelas)
    return f"c{grupo['tx_ids'][0]}"


def _em(uid, dia, nome, valor=10, **kw):
    return f"l{db.add_launch_and_update_balance(uid, 'despesa', valor, nome, None, criado_em=datetime.combine(dia, time(12)), **kw)[0]}"


@pytest.fixture
def mundo():
    """Uma conta com: receita, despesa, despesa em "outros", saque interno, guardado, uma compra de cartão que entra na fatura DESTE
    mês e outra, feita hoje DEPOIS do fechamento, que entra na do mês seguinte, e uma compra em 12x de 75 dias atrás."""
    hoje = today_tz()
    if hoje.day == 1:
        pytest.skip("dia 1: não existe fechamento anterior a hoje para a compra 'depois do fechamento'")
    uid = completo(usuario_pagante())
    q("update accounts set balance = 5000 where user_id = %s returning user_id", (uid,))
    ids = {
        "receita": db.add_launch_and_update_balance(uid, "receita", 1000, "Salário", None)[0],
        "despesa": db.add_launch_and_update_balance(uid, "despesa", 50, "Mercado Extra", None, categoria="alimentação")[0],
        "interno": db.add_launch_and_update_balance(uid, "receita", 200, "Saque em dinheiro", None,
                                                    categoria="transferencia_interna", is_internal_movement=True)[0],
    }
    db.create_pocket(uid, "Viagem")
    ids["guardado"] = db.pocket_deposit_from_account(uid, "Viagem", 120, "dep")[0]
    ids["sem_categoria"] = db.add_launch_and_update_balance(uid, "despesa", 15, "Sem cat", None, categoria="outros")[0]
    ids = {k: f"l{v}" for k, v in ids.items()}
    ids["cartao_no_mes"] = _compra(uid, "Banco A", 31)
    ids["cartao_tarde"] = _compra(uid, "Banco B", hoje.day - 1)
    ids["parcelado"] = _parcelado(uid, "Notebook", 12, 75)  # 12x há 75 dias: a janela traz ~4 faturas dele
    return uid, ids


def test_itens_em_comum_com_o_extrato_sao_identicos_e_a_diferenca_e_a_declarada(mundo):
    uid, ids = mundo
    tool, app = _bloco(uid), _do_app(uid)
    # o Extrato mostra do 12x a parcela da fatura do mês (outra linha, que a IA não repete); a IA, a menor parcela
    notebook = {i for i, v in app.items() if v["descricao"] == "Notebook"}
    assert notebook and ids["parcelado"] not in notebook  # premissa: o parcelado está nos dois lados, com linhas diferentes
    assert [i for i, v in tool.items() if v["descricao"] == "Notebook"] == [ids["parcelado"]]
    app = {i: v for i, v in app.items() if i not in notebook}
    assert app and set(app) & set(tool) >= {ids["receita"], ids["despesa"], ids["interno"], ids["cartao_no_mes"]}  # positivo
    for i in set(app) & set(tool):
        assert tool[i] == app[i], i
    assert set(app) <= set(tool)  # a janela da IA contém a do mês do Extrato
    assert set(tool) - set(app) == {ids["guardado"], ids["cartao_tarde"], ids["parcelado"]}  # e a diferença é só esta


def test_compra_depois_do_fechamento_aparece(mundo):
    uid, ids = mundo
    tarde = _bloco(uid)[ids["cartao_tarde"]]
    assert tarde["fatura"] > f"{today_tz():%Y-%m}" and tarde["data"] == today_tz().isoformat()  # premissa: fatura do mês seguinte
    assert ids["cartao_tarde"] not in _do_app(uid)  # o Extrato do mês corrente não a mostra
    assert ids["cartao_no_mes"] in _bloco(uid)  # positivo: a compra antes do fechamento está nos dois lados


def test_guardado_aparece(mundo):
    uid, ids = mundo
    g = _bloco(uid)[ids["guardado"]]
    assert g["guardado"] is True and g["interno"] is True and g["pode"] == []
    ia = {i["id"]: i for i in ultimos_lancamentos(uid, {})["itens"]}
    assert ia[ids["guardado"]]["guardado"] is True and ia[ids["guardado"]]["tipo"] == "saída"  # sai da conta: não é "despesa"
    assert ia[ids["despesa"]]["guardado"] is False and ia[ids["despesa"]]["tipo"] == "despesa"  # positivo


def test_user_seq_so_nos_l(mundo):
    uid, ids = mundo
    ia = {i["id"]: i for i in ultimos_lancamentos(uid, {})["itens"]}
    for chave in ("receita", "despesa", "interno"):
        seq = q("select user_seq from launches where user_id = %s and id = %s", (uid, int(ids[chave][1:])))["user_seq"]
        assert ia[ids[chave]]["user_seq"] == seq
    assert "user_seq" not in ia[ids["cartao_no_mes"]]  # compra de cartão não tem #N: o id é o `c…`


def test_limit_de_1_a_50(mundo, monkeypatch):
    uid, _ = mundo
    visto = []
    real = lancamentos_leitura.recentes
    monkeypatch.setattr(lancamentos_leitura, "recentes", lambda u, agora, limite, **kw: visto.append(limite) or real(u, agora, limite, **kw))
    for pedido in ({}, {"limit": 0}, {"limit": 2}, {"limit": -5}, {"limit": 999}):
        ultimos_lancamentos(uid, pedido)
    assert visto == [10, 10, 2, 1, 50]  # 0 = não informado: o padrão
    assert len(ultimos_lancamentos(uid, {"limit": 2})["itens"]) == 2 and len(ultimos_lancamentos(uid, {})["itens"]) == 8


def test_isolamento_entre_usuarios(mundo):
    a, ids_a = mundo
    b = usuario_pagante()
    db.add_launch_and_update_balance(b, "despesa", 33, "Padaria do B", None)
    db.create_card(b, "Cartão B", closing_day=5, due_day=28)
    da_b = {i["descricao"] for i in ultimos_lancamentos(b, {})["itens"]}
    assert da_b == {"Padaria do B"}  # positivo: B vê o dele
    da_a = ultimos_lancamentos(a, {})["itens"]
    assert "Padaria do B" not in {i["descricao"] for i in da_a} and {i["id"] for i in da_a} >= set(ids_a.values())


# ── sem categoria ────────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def sem_cat():
    """Entram: despesa sem categoria, em "Outros" (caixa e acento), compra de cartão sem categoria do provedor e em
    "outros". Ficam de fora: despesa categorizada, receita, saque interno, guardado e compra de cartão categorizada."""
    uid = completo(usuario_pagante())
    q("update accounts set balance = 5000 where user_id = %s returning user_id", (uid,))
    l = lambda *a, **k: f"l{db.add_launch_and_update_balance(uid, *a, **k)[0]}"  # noqa: E731
    entram = {"vazia": l("despesa", 10, "Vazia", None), "outros": l("despesa", 20, "Outros", None, categoria="Outros"),
              "nula": l("despesa", 30, "Nula", None)}
    q("update launches set categoria = null where user_id = %s and id = %s returning id", (uid, int(entram["vazia"][1:])))
    q("update launches set categoria = '' where user_id = %s and id = %s returning id", (uid, int(entram["nula"][1:])))
    card = db.create_card(uid, "Banco", closing_day=31, due_day=28)
    hoje = today_tz()
    entram["cartao_nulo"] = f"c{db.add_imported_credit_purchase(uid, card, 40, None, hoje, f'ct-{uid}-1')[0]}"
    entram["cartao_outros"] = f"c{db.add_imported_credit_purchase(uid, card, 50, 'Outros', hoje, f'ct-{uid}-2')[0]}"
    ficam = {"categorizada": l("despesa", 60, "Categorizada", None, categoria="alimentação"),
             "receita": l("receita", 1000, "Salário", None, categoria="outros"),
             "interno": l("despesa", 70, "Saque", None, categoria="outros", is_internal_movement=True),
             "cartao_categorizado": f"c{db.add_imported_credit_purchase(uid, card, 80, 'mercado', hoje, f'ct-{uid}-3')[0]}"}
    db.create_pocket(uid, "Viagem")
    ficam["guardado"] = f"l{db.pocket_deposit_from_account(uid, 'Viagem', 90, 'dep')[0]}"
    q("update launches set categoria = null where user_id = %s and id = %s returning id", (uid, int(ficam["guardado"][1:])))
    return uid, entram, ficam


def test_sem_categoria_traz_a_saida_sem_categoria_do_lancamento_e_do_cartao(sem_cat):
    from core.services.ai_chat.tools.listas_ia import sem_categoria
    uid, entram, _ = sem_cat
    r = sem_categoria(uid, {})
    assert set(r) == {"itens", "motivos"}  # sem total
    assert {i["id"] for i in r["itens"]} >= set(entram.values())
    assert all(i["tipo"] == "despesa" and i["categoria"] in (None, "outros") for i in r["itens"]), r["itens"]


def test_sem_categoria_so_a_saida_nao_interna_e_categorizada_fica_de_fora(sem_cat):
    from core.services.ai_chat.tools.listas_ia import sem_categoria
    uid, entram, ficam = sem_cat
    ids = {i["id"] for i in sem_categoria(uid, {})["itens"]}
    assert ids == set(entram.values()), (ids ^ set(entram.values()), ficam)


def test_sem_categoria_e_o_item_do_extrato_e_o_user_seq_so_nos_l(sem_cat):
    from core.services.ai_chat.tools.listas_ia import sem_categoria
    uid, entram, _ = sem_cat
    ia = {i["id"]: i for i in sem_categoria(uid, {})["itens"]}
    app = _do_app(uid)
    from api.v2.lancamentos import Lancamento
    d = {i["id"]: Lancamento(**i).model_dump(mode="json") for i in lancamentos_leitura.recentes(uid, now_tz(), 50, sem_categoria=True)["itens"]}
    assert set(d) == set(ia) and all(d[i] == app[i] for i in d)  # os mesmos itens do Extrato, campo a campo
    assert "user_seq" in ia[entram["vazia"]] and "user_seq" not in ia[entram["cartao_nulo"]]


def test_sem_categoria_isolamento(sem_cat):
    from core.services.ai_chat.tools.listas_ia import sem_categoria
    a, entram, _ = sem_cat
    b = usuario_pagante()
    db.add_launch_and_update_balance(b, "despesa", 33, "Do B", None, categoria="outros")
    assert [i["descricao"] for i in sem_categoria(b, {})["itens"]] == ["Do B"]
    assert "Do B" not in {i["descricao"] for i in sem_categoria(a, {})["itens"]}


# ── uma linha por parcelamento ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("so_sem_categoria", [False, True], ids=["ultimos", "sem_categoria"])
def test_parcelado_vem_em_uma_linha_a_da_menor_parcela(so_sem_categoria):
    """12x há 75 dias: a janela traz as faturas até o mês seguinte (até 4 linhas, todas com a data da compra)."""
    uid = completo(usuario_pagante())
    primeira = _parcelado(uid, "Notebook", 12, 75, None if so_sem_categoria else "compras online")
    itens = lancamentos_leitura.recentes(uid, now_tz(), 50, sem_categoria=so_sem_categoria)["itens"]
    assert [(i["id"], i["parcela"]["n"], i["parcela"]["total"]) for i in itens] == [(primeira, 1, 12)]


def test_parcelado_comprado_hoje_depois_do_fechamento_aparece_pela_parcela_1():
    hoje = today_tz()
    if hoje.day == 1:
        pytest.skip("dia 1: não existe fechamento anterior a hoje")
    uid = completo(usuario_pagante())
    primeira = _parcelado(uid, "Celular", 3, 0, fechamento=hoje.day - 1)
    (item,) = lancamentos_leitura.recentes(uid, now_tz(), 50)["itens"]
    assert item["id"] == primeira and item["parcela"]["n"] == 1 and item["fatura"] > f"{hoje:%Y-%m}"  # fatura do mês que vem


def test_o_limite_conta_depois_de_tirar_as_parcelas_repetidas():
    uid = completo(usuario_pagante())
    q("update accounts set balance = 5000 where user_id = %s returning user_id", (uid,))
    ids = [_em(uid, today_tz() - timedelta(days=1), "A"), _em(uid, today_tz() - timedelta(days=2), "B")]
    _parcelado(uid, "Notebook", 12, 75)
    # 2 lançamentos + 1 parcelamento = 3 linhas: com o limite 4 não sobra vaga para parcela repetida
    assert len(lancamentos_leitura.recentes(uid, now_tz(), 4)["itens"]) == 3
    assert [i["id"] for i in lancamentos_leitura.recentes(uid, now_tz(), 2)["itens"]] == ids  # positivo: o limite corta


def test_open_finance_cada_parcela_e_uma_cobranca_com_data_propria_e_a_de_ontem_aparece():
    """OF: 4 parcelas em datas diferentes (90, 60, 30 e 1 dia atrás). Só as de mesma data viram uma linha."""
    from uuid import uuid4

    uid = completo(usuario_pagante())
    q("update accounts set balance = 5000 where user_id = %s returning user_id", (uid,))
    card = db.create_card(uid, "Banco OF", closing_day=28, due_day=10)
    grupo, hoje = uuid4(), today_tz()
    c = [f"c{db.add_imported_credit_purchase(uid, card, 100, 'compras online', hoje - timedelta(days=d), f'of-{grupo}-{n}', installment_no=n, installments_total=6, group_id=grupo)[0]}"
         for n, d in enumerate((90, 60, 30, 1), 1)]
    l5, l8 = _em(uid, hoje - timedelta(days=5), "L5"), _em(uid, hoje - timedelta(days=8), "L8")
    assert [i["id"] for i in lancamentos_leitura.recentes(uid, now_tz(), 3)["itens"]] == [c[3], l5, l8]  # a de ontem, não a 1ª
    assert {i["id"] for i in lancamentos_leitura.recentes(uid, now_tz(), 50)["itens"]} >= set(c)  # positivo: cada cobrança na sua data


def test_sem_categoria_so_a_parcela_1_categorizada_a_compra_aparece_e_o_inverso_continua_certo():
    uid = completo(usuario_pagante())
    manual = _parcelado(uid, "Sofa", 4, 75, None)
    q("update credit_transactions set categoria = 'casa' where user_id = %s and id = %s returning id", (uid, int(manual[1:])))
    sem = [i["id"] for i in lancamentos_leitura.recentes(uid, now_tz(), 50, sem_categoria=True)["itens"]]
    assert len(sem) == 1 and sem != [manual]  # a janela corre só sobre as parcelas sem categoria: sobra uma delas
    inverso = _parcelado(uid, "Mesa", 4, 75, "casa")
    q("update credit_transactions set categoria = null where user_id = %s and id = %s returning id", (uid, int(inverso[1:])))
    ids = [i["id"] for i in lancamentos_leitura.recentes(uid, now_tz(), 50, sem_categoria=True)["itens"]]
    assert inverso in ids and len(ids) == 2  # positivo: a parcela 1 sem categoria é a linha do grupo


def test_compra_de_cartao_com_categoria_personalizada_chega_com_acento_e_nota_so_de_espacos_cai_no_fallback():
    from db.estabelecimentos import SEM_DESCRICAO

    uid = completo(usuario_pagante())
    card = db.create_card(uid, "Banco", closing_day=31, due_day=28)
    ct = db.add_imported_credit_purchase(uid, card, 40, "Café", today_tz(), f"ct-{uid}-ac")[0]
    q("update credit_transactions set nota = '   ' where user_id = %s and id = %s returning id", (uid, ct))
    (item,) = ultimos_lancamentos(uid, {})["itens"]
    assert item["categoria"] == "Café" and item["descricao"] == SEM_DESCRICAO


# ── a janela e o plano ───────────────────────────────────────────────────────────────────────────

def test_janela_de_12_meses_o_dia_1_de_m_menos_11_entra_e_o_fim_de_m_menos_12_nao():
    uid = completo(usuario_pagante())
    q("update accounts set balance = 5000 where user_id = %s returning user_id", (uid,))
    hoje = today_tz()
    ano, mes = (hoje.year, hoje.month - 11) if hoje.month > 11 else (hoje.year - 1, hoje.month + 1)
    inicio = date(ano, mes, 1)
    dentro, fora = _em(uid, inicio, "Dentro"), _em(uid, inicio - timedelta(days=1), "Fora")
    ids = {i["id"] for i in lancamentos_leitura.recentes(uid, now_tz(), 50)["itens"]}
    assert dentro in ids and fora not in ids


def test_plano_essencial_corta_a_janela_e_diz_por_que():
    """Essencial enxerga 90 dias: o de 85 dias aparece, o de 95 não, e o `motivos` avisa (o card e o modelo)."""
    essencial = completo(usuario_pagante(plan="essencial"))
    pro = completo(usuario_pagante())
    ids = {}
    for uid in (essencial, pro):
        q("update accounts set balance = 5000 where user_id = %s returning user_id", (uid,))
        ids[uid] = (_em(uid, today_tz() - timedelta(days=85), "d85"), _em(uid, today_tz() - timedelta(days=95), "d95"))
    r = ultimos_lancamentos(essencial, {})
    assert {i["id"] for i in r["itens"]} == {ids[essencial][0]} and r["motivos"] == ["inicio_do_historico"]
    r = ultimos_lancamentos(pro, {})  # positivo: quem enxerga tudo vê os dois e não leva o motivo
    assert {i["id"] for i in r["itens"]} == set(ids[pro]) and "inicio_do_historico" not in r["motivos"]


# ── o que o modelo vê ────────────────────────────────────────────────────────────────────────────

def test_o_modelo_recebe_valor_numero_moeda_e_pode(mundo):
    uid, ids = mundo
    r = ultimos_lancamentos(uid, {})
    ia = {i["id"]: i for i in r["itens"]}
    despesa = ia[ids["despesa"]]
    assert despesa["valor"] == 50.0 and isinstance(despesa["valor"], float)  # o `ai_guard` só lê número JSON
    assert despesa["moeda"] == "BRL" and "apagar" in despesa["pode"] and ia[ids["guardado"]]["pode"] == []
    visto = lambda texto: {c.token: c.supported for c in ai_guard.check(texto, ai_guard.tool_results(  # noqa: E731
        [{"role": "tool", "content": json.dumps(r, ensure_ascii=False)}]))}
    ok, solto = visto("Foram R$ 50,00 no mercado."), visto("Foram R$ 77,00 no mercado.")
    assert ok and all(ok.values()) and solto and not any(solto.values()), (ok, solto)


def test_categoria_chega_como_o_usuario_a_ve_e_a_ida_e_volta_nao_cria_outra():
    from core.services.ai_chat.tools import extrato_ia

    uid = completo(usuario_pagante())
    q("update accounts set balance = 5000 where user_id = %s returning user_id", (uid,))
    hoje = today_tz()
    cafe, salario = _em(uid, hoje, "Padaria", categoria="Café"), _em(uid, hoje, "Folha", categoria="salário")  # fora do catálogo
    outro = _em(uid, hoje, "Outro", categoria="mercado")
    ia = {i["id"]: i for i in ultimos_lancamentos(uid, {})["itens"]}
    assert ia[cafe]["categoria"] == "Café" and ia[salario]["categoria"] == "salário"
    assert ia[outro]["categoria"] == "mercado"  # positivo: o catálogo segue com o nome canônico
    # o modelo devolve o nome que recebeu: grava a mesma categoria, sem gêmea sem acento
    args = extrato_ia.ao_confirmar(uid, {"launch_id": outro, "new_category": ia[salario]["categoria"]})
    assert "agora é" in extrato_ia.recategorizar(uid, args)
    gravada = q("select categoria from launches where user_id = %s and id = %s", (uid, int(outro[1:])))["categoria"]
    nomes = {n["name"] for n in q("select name from user_categories where user_id = %s", (uid,), fetch=True)}
    assert gravada == "salário" and "salário" in nomes and "salario" not in nomes  # criou a dele, uma só


def test_compra_sem_nota_e_sem_descricao_do_banco_chega_com_o_texto_de_sem_descricao(sem_cat):
    from core.services.ai_chat.tools import extrato_ia
    from core.services.ai_chat.tools.listas_ia import sem_categoria
    from db.estabelecimentos import SEM_DESCRICAO

    uid, entram, _ = sem_cat
    ia = {i["id"]: i for i in sem_categoria(uid, {})["itens"]}
    assert ia[entram["cartao_nulo"]]["descricao"] == SEM_DESCRICAO
    item = lancamentos_leitura.um_item(uid, "c", int(entram["cartao_nulo"][1:]))
    assert extrato_ia._resumo_item(item)["descricao"] == SEM_DESCRICAO  # a pendência diz o mesmo que a lista
    assert ia[entram["vazia"]]["descricao"] == "Vazia"  # positivo
