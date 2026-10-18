"""PR C2: as listas de lançamento da IA são cards do app (`ultimos_lancamentos`, `sem_categoria`) e a confirmação de
APAGAR mostra o item no card `ultimos_lancamentos` (1 linha). O card sai pela própria tool (`mostrar_da_tool`), só para o
app que o declara em `cards` e com vaga; sem emissor (WhatsApp) e com o build antigo o resultado é o dado, sem bloco.
O pytest NÃO fala com o modelo: aqui se prova o que o código empurra; se o modelo segue o `card_na_tela`, só o staging diz.

CONTROLES NEGATIVOS (injetados e restaurados na entrega, ver o relato):
  • `DA_TOOL` sem os 2 ids -> `test_bloco_*` e `test_app_que_*`;
  • `mostrar_itens` sem o corte de `MAX_NO_CARD` -> `test_o_bloco_leva_ate_10_e_o_modelo_todos`;
  • `armar_apagar` chamado no `ao_confirmar` (antes de armar) -> `test_card_da_confirmacao_*` (sai com o `_OUTRO_PEDIDO`);
  • `ao_armar` fora do `delete_launch` -> `test_card_da_confirmacao_sai_depois_de_armar`.
CONTROLES POSITIVOS: o card sai no caso legítimo; a recategorização arma sem card; a pendência fica armada.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

import pytest

import db
from api.v2.lancamentos import Lancamento
from conftest import usuario_pagante
from core.services.ai_chat import cards, runner, streaming
from core.services.ai_chat._context import CARDS_DO_APP, CARDS_DO_TURNO
from core.services.ai_chat.tools import get_tool
from core.services.ai_chat.tools._base import OUTRO_PEDIDO
from _ia_stream_helpers import executar, openai_falso, rodada
from test_ai_chat_periodo import _conforme
from tests._fusao_of_helpers import manda, uid_pro  # noqa: F401 (fixture)
from tests._ia_falsa_helpers import chamada, com_tools, texto
from tests._ia_falsa_helpers import openai_falso as openai_falso_conversa
from tests.test_ai_chat_listas import mundo, sem_cat  # noqa: F401 (fixtures)
from tests.test_ai_chat_tool_card import _chama, _tool_msgs, _turno
from tests.test_api_app_sessao import completo

DO_APP = cards.DA_TOOL
FIXTURES = Path(__file__).parent / "fixtures"
LISTAS = {"list_recent_launches": "ultimos_lancamentos", "get_uncategorized_launches": "sem_categoria"}


def _fixture(card):
    return json.loads((FIXTURES / f"ai_chat_bloco_{card}.json").read_text())


# ── 1. o app declarou o card: bloco antes do fim, e o modelo recebe a lista inteira ───────────────

def test_bloco_de_ultimos_lancamentos_tem_a_forma_da_fixture_e_o_modelo_recebe_a_lista(monkeypatch, mundo):
    uid, ids = mundo
    _, (bloco,), _ = _turno(monkeypatch, uid, ("list_recent_launches", {}))
    assert bloco["card"] == "ultimos_lancamentos" and bloco["abas"][0]["mes"] is None
    rec = bloco["abas"][0]["recursos"]["ultimos_lancamentos"]
    fix = _fixture("ultimos_lancamentos")
    _conforme(rec, fix["abas"][0]["recursos"]["ultimos_lancamentos"], "ultimos_lancamentos")
    assert fix["card"] == "ultimos_lancamentos" and set(rec) == {"itens", "motivos"}  # sem total
    (visao,) = _tool_msgs(uid)
    assert visao["card_na_tela"] is True and [i["id"] for i in visao["itens"]] == [i["id"] for i in rec["itens"]]
    assert {ids["despesa"], ids["cartao_tarde"], ids["guardado"]} <= {i["id"] for i in visao["itens"]}  # o modelo acha o id


def test_bloco_de_sem_categoria_tem_a_forma_da_fixture_e_nao_tem_total(monkeypatch, sem_cat):
    uid, entram, _ = sem_cat
    _, (bloco,), _ = _turno(monkeypatch, uid, ("get_uncategorized_launches", {}))
    rec = bloco["abas"][0]["recursos"]["sem_categoria"]
    fix = _fixture("sem_categoria")
    assert bloco["card"] == fix["card"] == "sem_categoria"
    _conforme(rec, fix["abas"][0]["recursos"]["sem_categoria"], "sem_categoria")
    assert set(rec) == {"itens", "motivos"} and {i["id"] for i in rec["itens"]} == set(entram.values())
    (visao,) = _tool_msgs(uid)
    assert visao["card_na_tela"] is True and len(visao["itens"]) == len(rec["itens"])


@pytest.mark.parametrize("card", ["ultimos_lancamentos", "sem_categoria"])
def test_a_fixture_cobre_todos_os_campos_do_lancamento(card):
    """O app (`lancamentoSchema`) lê o item inteiro: fixture com campo a menos esconde a quebra."""
    itens = _fixture(card)["abas"][0]["recursos"][card]["itens"]
    assert all(set(i) == set(Lancamento.model_fields) for i in itens)
    assert {i["id"][0] for i in itens} == {"l", "c"}


def test_o_bloco_leva_ate_10_e_o_modelo_todos(monkeypatch, mundo):
    uid, _ = mundo
    for n in range(8):
        db.add_launch_and_update_balance(uid, "despesa", 5 + n, f"Extra {n}", None)
    _, (bloco,), _ = _turno(monkeypatch, uid, ("list_recent_launches", {"limit": 20}))
    assert len(bloco["abas"][0]["recursos"]["ultimos_lancamentos"]["itens"]) == 10  # o app mostra 5 e um "+5"
    (visao,) = _tool_msgs(uid)
    assert len(visao["itens"]) == 15 and visao["card_na_tela"] is True  # o "apaga o 12º" precisa do id


def test_lista_vazia_nao_vira_card(monkeypatch, uid_pro):
    completo(uid_pro)
    _, blocos, _ = _turno(monkeypatch, uid_pro, ("list_recent_launches", {}))
    (visao,) = _tool_msgs(uid_pro)
    assert not blocos and visao == {"itens": [], "motivos": []}


# ── 2. sem emissor (WhatsApp) e app antigo: o dado, sem bloco ────────────────────────────────────

@pytest.mark.parametrize("nome", LISTAS)
def test_sem_emissor_nada_sai_e_o_resultado_e_so_o_dado(monkeypatch, mundo, nome):
    uid, _ = mundo
    CARDS_DO_APP.set(DO_APP)  # mesmo com a capacidade no contexto: sem emissor não há app
    try:
        openai_falso(monkeypatch, rodada(None, (nome, {})), rodada("Comentário."))
        assert runner.chat(uid, "pergunta", monthly_limit=100)
    finally:
        CARDS_DO_APP.set(())
    (visao,) = _tool_msgs(uid)
    assert list(visao) == ["itens", "motivos"]  # nenhuma chave de card
    assert all(i["id"][0] in "lc" for i in visao["itens"])


@pytest.mark.parametrize("nome,card", LISTAS.items())
@pytest.mark.parametrize("declarado", ["nada", "so_o_que_o_build_31_conhece"])
def test_app_que_nao_declarou_o_card_recebe_o_dado_sem_bloco(monkeypatch, mundo, nome, card, declarado):
    uid, _ = mundo
    # builds 31 a 33 declaram só estes dois; o servidor não emite o que o app não conhece
    _, blocos, _ = _turno(monkeypatch, uid, (nome, {}), () if declarado == "nada" else ("maiores_gastos", "fim_do_mes"))
    (visao,) = _tool_msgs(uid)
    assert not blocos and list(visao) == ["itens", "motivos"]


# ── 3. teto de vagas e repetição ───────────────────────────────────────────────────────────────────

def test_vagas_gastas_o_card_nao_sai_e_a_tool_devolve_o_dado(mundo):
    uid, _ = mundo
    r, blocos, usados = _chama(uid, ("list_recent_launches", {}), usados=["contas", "hero", "resumo"])
    assert not blocos and "card_na_tela" not in r and len(r["itens"]) == 7 and len(usados) == 3


def test_vaga_livre_o_card_gasta_uma_vaga_e_o_quarto_nao_sai(mundo):
    uid, _ = mundo
    r, blocos, usados = _chama(uid, ("list_recent_launches", {}), usados=["contas", "hero"])
    assert len(blocos) == 1 and r["card_na_tela"] and usados == ["contas", "hero", "ultimos_lancamentos"]
    r, blocos, _ = _chama(uid, ("get_uncategorized_launches", {}), usados=usados)  # a 4ª
    assert not blocos and "card_na_tela" not in r


def test_repetida_no_turno_so_a_primeira_emite(monkeypatch, mundo):
    uid, _ = mundo
    tool = ("list_recent_launches", {})
    openai_falso(monkeypatch, rodada(None, tool), rodada(None, tool), rodada("Comentário."))
    _, eventos, _ = executar(uid, "pergunta", cards=DO_APP)
    assert [e["bloco"]["card"] for e in eventos if e["tipo"] == "bloco"] == ["ultimos_lancamentos"]
    primeira, segunda = _tool_msgs(uid)
    assert primeira["card_na_tela"] and "card_na_tela" not in segunda and len(segunda["itens"]) == 7  # a 2ª: o dado


# ── 4. a confirmação de apagar mostra o item (P-5) ────────────────────────────────────────────────

@contextmanager
def _app(uid, declarado=DO_APP):
    """Um emissor de app: cada evento guarda também a pendência que existia NO MOMENTO de sair."""
    ev, t1 = [], streaming.EMISSOR.set(lambda e: ev.append((e, db.ai_get_pending_action(uid))))
    t2, t3 = CARDS_DO_APP.set(declarado), CARDS_DO_TURNO.set([])
    runner._PENDENCIA_DO_TURNO.set(None)
    try:
        yield ev
    finally:
        CARDS_DO_TURNO.reset(t3)
        CARDS_DO_APP.reset(t2)
        streaming.EMISSOR.reset(t1)


def _blocos(ev):
    return [(e["bloco"], pend) for e, pend in ev if e["tipo"] == "bloco"]


def test_card_da_confirmacao_sai_depois_de_armar_com_o_item_em_uma_linha(mundo):
    uid, ids = mundo
    with _app(uid) as ev:
        historico, final = runner._dispatch_tool(uid, "delete_launch", {"launch_id": ids["despesa"]})
    assert final is None and json.loads(historico)["status"] == "pending_user_confirmation"
    ((bloco, pend),) = _blocos(ev)
    assert pend and pend["tool_name"] == "delete_launch"  # a pendência já existia quando o card saiu
    rec = bloco["abas"][0]["recursos"]["ultimos_lancamentos"]
    assert bloco["card"] == "ultimos_lancamentos" and [i["id"] for i in rec["itens"]] == [ids["despesa"]] and rec["motivos"] == []
    assert rec["itens"][0]["descricao"] == "Mercado Extra"


def test_card_da_confirmacao_nao_sai_com_outro_pedido_pendente(mundo):
    uid, ids = mundo
    runner._dispatch_tool(uid, "delete_category_rule", {"keyword": "uber"})  # a outra pendência, viva
    with _app(uid) as ev:
        historico, final = runner._dispatch_tool(uid, "delete_launch", {"launch_id": ids["despesa"]})
    assert final == OUTRO_PEDIDO and not _blocos(ev)  # card na tela levaria a um "sim" que executaria o OUTRO pedido
    assert db.ai_get_pending_action(uid)["tool_name"] == "delete_category_rule"


@pytest.mark.parametrize("declarado", [(), ("maiores_gastos", "fim_do_mes")], ids=["sem_cards", "build_31"])
def test_card_da_confirmacao_so_para_o_app_que_o_declara(mundo, declarado):
    uid, ids = mundo
    with _app(uid, declarado) as ev:
        _, final = runner._dispatch_tool(uid, "delete_launch", {"launch_id": ids["despesa"]})
    assert final is None and not _blocos(ev) and db.ai_get_pending_action(uid)  # armou, sem card


def test_card_da_confirmacao_respeita_o_teto_de_vagas_e_a_lista_ja_na_tela(mundo):
    uid, ids = mundo
    with _app(uid) as ev:
        CARDS_DO_TURNO.get().extend(["contas", "hero", "resumo"])  # sem vaga
        _, final = runner._dispatch_tool(uid, "delete_launch", {"launch_id": ids["despesa"]})
    assert final is None and not _blocos(ev)


def test_recusa_no_validate_nao_arma_nem_mostra_card(mundo):
    uid, ids = mundo
    with _app(uid) as ev:
        _, final = runner._dispatch_tool(uid, "delete_launch", {"launch_id": ids["guardado"]})  # guardado: sem `apagar`
    assert final and "caixinha" in final and not _blocos(ev) and db.ai_get_pending_action(uid) is None


def test_recategorizar_arma_sem_card(mundo):
    uid, ids = mundo
    with _app(uid) as ev:
        _, final = runner._dispatch_tool(uid, "recategorize_launch", {"launch_id": ids["despesa"], "new_category": "lazer"})
    assert final is None and db.ai_get_pending_action(uid)["tool_name"] == "recategorize_launch" and not _blocos(ev)


def test_card_da_confirmacao_pelo_stream(monkeypatch, mundo):
    uid, ids = mundo
    _, blocos, _ = _turno(monkeypatch, uid, ("delete_launch", {"launch_id": ids["despesa"]}))
    (bloco,) = blocos
    assert [i["id"] for i in bloco["abas"][0]["recursos"]["ultimos_lancamentos"]["itens"]] == [ids["despesa"]]
    (msg,) = _tool_msgs(uid)
    assert msg["status"] == "pending_user_confirmation" and "Mercado Extra" in msg["summary"]


# ── 5. a conversa pelo `handle_incoming`: lista, "apaga o 2º", "sim" ──────────────────────────────

def test_conversa_ultimos_lancamentos_apaga_o_segundo_e_o_sim_apaga_so_ele(uid_pro, monkeypatch):
    from core.services.ai_chat.tools.listas_ia import ultimos_lancamentos
    db.add_launch_and_update_balance(uid_pro, "receita", 1000, "seed", None)
    mercado, _, _ = db.add_launch_and_update_balance(uid_pro, "despesa", 50, "Mercado Extra", None)
    padaria, _, _ = db.add_launch_and_update_balance(uid_pro, "despesa", 20, "Padaria", None)  # a mais nova: a 1ª da lista
    saldo = db.get_balance(uid_pro)

    openai_falso_conversa(monkeypatch, com_tools(chamada("list_recent_launches", {}, "c1")),
                 lambda m: texto("🐷 Seus últimos: " + ", ".join(
                     i["descricao"] for i in json.loads([x for x in m if x.get("role") == "tool"][-1]["content"])["itens"])))
    r = manda(uid_pro, "piggy meus últimos lançamentos")
    assert 0 <= r.find("Padaria") < r.find("Mercado Extra")  # o WhatsApp não tem card: o texto lista, a mais nova primeiro

    segundo = ultimos_lancamentos(uid_pro, {})["itens"][1]  # o modelo "lembra" a lista da vez anterior
    assert segundo["id"] == f"l{mercado}"
    openai_falso_conversa(monkeypatch, com_tools(chamada("delete_launch", {"launch_id": segundo["id"]}, "c2")),
                 lambda m: texto("🐷 Confirma: " + json.loads([x for x in m if x.get("role") == "tool"][-1]["content"])["summary"] + "?"))
    r = manda(uid_pro, "piggy apaga o 2º")
    assert '"Mercado Extra", R$ 50,00' in r and db.ai_get_pending_action(uid_pro)["tool_args"]["launch_id"] == f"l{mercado}"

    r = manda(uid_pro, "sim")
    assert "Apagado" in r and "Mercado Extra" in r
    assert db.get_balance(uid_pro) == saldo + 50
    restantes = {i["descricao"] for i in ultimos_lancamentos(uid_pro, {})["itens"]}
    assert "Mercado Extra" not in restantes and "Padaria" in restantes and padaria
