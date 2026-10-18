"""PR C2: "últimos lançamentos" da IA lido pelo `listar` do Extrato (`db/lancamentos_leitura.recentes`).

Banco real. A lista é a do Extrato: os itens em comum com `/api/app/lancamentos` são IDÊNTICOS campo a campo;
a diferença é declarada (P-4): a janela vai até o mês SEGUINTE (a compra no cartão feita depois do fechamento,
que o Extrato só mostra no mês da fatura) e o guardado entra.

CONTROLES NEGATIVOS (injetados e restaurados na entrega, ver o relato):
  • `listar(..., tipo="saida")` em `recentes` -> `test_itens_em_comum_com_o_extrato_sao_identicos`;
  • `fim` = o do Extrato (dia 1 do mês seguinte) em `recentes` -> `test_compra_depois_do_fechamento_aparece`;
  • `com_guardado=False` em `recentes` -> `test_guardado_aparece`;
  • `_item(r)` sem o `user_seq` -> `test_user_seq_so_nos_l`;
  • `sem_categoria` sem o filtro no `listar` -> `test_sem_categoria_...` (receita, interno e categorizada na lista);
  • o filtro sem `not x.interno` -> `test_sem_categoria_so_a_saida_...` (o saque em espécie e o guardado entram);
  • o filtro sem o `outros` -> `test_sem_categoria_...` (a "Outros" fora).
CONTROLES POSITIVOS: o item do Extrato continua aparecendo; a compra antes do fechamento está nos dois lados.
"""
from __future__ import annotations

from datetime import date

import pytest

import db
from conftest import usuario_pagante
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


@pytest.fixture
def mundo():
    """Uma conta com: receita, despesa, despesa em "outros", saque interno, guardado, uma compra de cartão que entra na fatura DESTE
    mês e outra, feita hoje DEPOIS do fechamento, que entra na do mês seguinte."""
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
    return uid, ids


def test_itens_em_comum_com_o_extrato_sao_identicos_e_a_diferenca_e_a_declarada(mundo):
    uid, ids = mundo
    tool, app = _bloco(uid), _do_app(uid)
    assert app and set(app) & set(tool) >= {ids["receita"], ids["despesa"], ids["interno"], ids["cartao_no_mes"]}  # positivo
    for i in set(app) & set(tool):
        assert tool[i] == app[i], i
    assert set(app) <= set(tool)  # a janela da IA contém a do mês do Extrato
    assert set(tool) - set(app) == {ids["guardado"], ids["cartao_tarde"]}  # e a diferença é só esta (P-4)


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
    assert len(ultimos_lancamentos(uid, {"limit": 2})["itens"]) == 2 and len(ultimos_lancamentos(uid, {})["itens"]) == 7


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
