"""G5e — a ADOÇÃO pela porta do one-shot `scripts/adotar_items_of_orfaos.py`.

Arquivo próprio (CLAUDE.md §0.5): o assunto aqui é o que o script ESCREVE quando
adota, com Postgres e o app inteiro — os três arquivos `test_of_webhook_adopt*.py`
cobrem a adoção pela porta do webhook, e os irmãos deste cobrem os outros assuntos
do script (`test_adotar_items_alvos.py`, em QUEM ele mexe; `_prazo.py`, a espera;
`_saida.py`, o que o operador lê). Os helpers vêm de
`test_of_webhook_adopt_guards.py`, que é a fonte única deles (CLAUDE.md §0.1);
copiá-los para cá seria a mesma duplicação que já custou uma rodada.

Por que o `--apply` precisa de teste PRÓPRIO, e não herda o do webhook: o
conserto do webhook só vale para conexão FUTURA (o `item/created` de quem já
está travado passou faz tempo). Quem destrava o usuário travado HOJE é este
script, e a sequência inteira que o destrava — adota → o card aparece na tela →
"Remover todas as conexões" enumera e APAGA o item na Pluggy → o
`avoidDuplicates` libera a reconexão — estava provada ponta a ponta só pela
versão via webhook (`test_adotado_o_delete_enumera_e_apaga_o_item_na_pluggy`).
O ramo `elif apply:` de `_executar` não era executado por teste nenhum: o único
que chamava `_executar` passava `apply=False, delete=True`.

CONTROLE NEGATIVO do grupo: trocar `elif apply:` por `elif False:` em
`_executar` (ou devolver `None` de `_adota_item_orfao`) deixa
`test_script_apply_adota_e_destrava_a_reconexao_do_dono` e
`test_script_apply_em_item_congelado_em_updating_agenda_sync` vermelhos —
os dois estão VERDES hoje, que é o que os torna discriminantes.
2º CONTROLE NEGATIVO, da adoção retroativa: apagar o `not fresco or` do gate de
sync em `_adota_item_orfao` (a `main` antes deste conserto) deixa vermelho só o
`test_script_apply_em_item_congelado_em_updating_agenda_sync` — e o par positivo
dele, `test_of_webhook_adopt.py::test_adocao_nao_agenda_sync_de_item_que_a_pluggy_ainda_esta_montando`,
fica VERDE nos dois lados: é ele que prova que o `item/created` fresco não passou
a agendar sync à toa.
CONTROLE POSITIVO do grupo: o mesmo
`test_script_apply_adota_e_destrava_a_reconexao_do_dono` — sem ele, o caso de
recusa deste arquivo passaria num script que não adota NADA, que é pior que o bug.
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard
from test_of_item_ownership import _auth, eventos  # noqa: F401
from test_of_webhook_adopt_guards import _limpa_item, _mock_item, _registry, webhook_pluggy  # noqa: F401


# ── o `--apply`: a única porta que destrava quem JÁ está travado ─────────────

def test_script_apply_adota_e_destrava_a_reconexao_do_dono(
        user_id, monkeypatch, eventos, webhook_pluggy):
    """A sequência inteira pela porta do SCRIPT, com banco real.

    O assert que importa não é o da conexão — é o `list_pluggy_item_ids`: é ele
    que o `_disconnect_sob_lock` usa para enumerar o que apagar na Pluggy. Sem a
    linha local, ele devolvia `[]`, o item continuava vivo lá e o
    `avoidDuplicates` do connect token recusava item novo: o usuário ficava
    travado sem saída pelo produto.
    """
    from scripts.adotar_items_of_orfaos import _executar

    item = "s-apply"
    _mock_item(monkeypatch, user_id)
    apagados: list[str] = []
    monkeypatch.setattr("frontend.routes.open_finance.create_pluggy_api_key", lambda: "k")
    monkeypatch.setattr("frontend.routes.open_finance.delete_pluggy_item",
                        lambda item_id, api_key=None: apagados.append(item_id))
    # o rastro do bug antigo: o webhook viu o item e NÃO conseguiu atribuir dono
    db.register_item(None, provider_item_id=item, origin="webhook")
    client = TestClient(dashboard.app)
    try:
        assert db.get_connections_by_item_id(item) == [], "pré-condição: nenhuma linha local"
        assert item not in db.list_pluggy_item_ids(user_id), \
            "pré-condição: o delete não alcança o item"

        asyncio.run(_executar([item], apply=True, delete=False))

        linhas = db.get_connections_by_item_id(item)
        assert len(linhas) == 1 and int(linhas[0]["user_id"]) == user_id, linhas
        assert [r["origin"] for r in _registry(item)] == ["webhook", "webhook_adopt"], \
            _registry(item)
        assert db.list_pluggy_item_ids(user_id) == [item], (
            "'Remover todas as conexões' continua sem enxergar o item: a Pluggy "
            "segue com ele vivo e o avoidDuplicates recusa a reconexão")

        # o card na tela do dono
        snap = client.get(f"/open-finance/{user_id}", headers=_auth(client, user_id)).json()
        assert [c["provider_item_id"] for c in snap["connections"]] == [item], snap

        # e o delete pelo app enumera e apaga do lado da Pluggy
        resp = client.delete(f"/open-finance/{user_id}", headers=_auth(client, user_id))
        assert resp.status_code == 200, resp.text
        assert apagados == [item], "o item ficaria órfão na Pluggy, travando a reconexão"
        assert db.get_connections_by_item_id(item) == []
    finally:
        db.disconnect_open_finance_connection(user_id)
        _limpa_item(item)


def test_script_apply_nao_ressuscita_o_banco_que_o_usuario_removeu(
        user_id, monkeypatch, eventos, webhook_pluggy):
    """CONTROLE NEGATIVO da guarda, pela porta do SCRIPT (o webhook tem o par dele).

    Banco conectado e depois REMOVIDO fica sem conexão local e COM rastro de dono
    — indistinguível, para o predicado, de item órfão (docstring de
    `db.item_registry_origins`). A lista não o oferece; mas `--item` PULA a
    lista, e é por lá que um `--apply` reataria a conexão e agendaria sync,
    re-importando na carteira que o usuário acabou de limpar.
    """
    from scripts.adotar_items_lista import alvos, listar_items_sem_conexao
    from scripts.adotar_items_of_orfaos import _executar

    item = "s-removido"
    _mock_item(monkeypatch, user_id)
    db.register_item(user_id, provider_item_id=item, origin="pluggy_item")
    try:
        # O par que o `main` compõe, e não um atalho com outro nome: UMA leitura,
        # e `alvos` decidindo em cima dela. (Havia um `listar_items_orfaos()` que
        # embrulhava exatamente isto e que o `main` não chamava — o teste media um
        # caminho que ninguém percorre.)
        sem_conexao = listar_items_sem_conexao()
        assert item not in alvos(None, sem_conexao), "a lista não podia oferecê-lo"
        # ...e a MESMA leitura tem de VÊ-LO, em banco real: é o grupo que o
        # dry-run reporta à parte (adoção pela metade OU removido — sem separar).
        assert sem_conexao[item] == {"pluggy_item"}

        asyncio.run(_executar([item], apply=True, delete=False))

        assert db.get_connections_by_item_id(item) == [], \
            "o banco que o usuário REMOVEU voltou pelo script"
        assert db.list_pluggy_item_ids(user_id) == []
        assert webhook_pluggy == [], "e com sync agendado, que re-importa o que ele apagou"
        motivos = [e["details"].get("motivo") for e in eventos
                   if e["event"] == "of_webhook_adopt_skipped"]
        assert motivos == ["rastro_com_dono"], motivos
    finally:
        db.disconnect_open_finance_connection(user_id)
        _limpa_item(item)


def test_script_apply_em_item_congelado_em_updating_agenda_sync(
        user_id, monkeypatch, eventos, webhook_pluggy):
    """A QUEIXA DO DONO em produção: "o card apareceu, mas fica carregando pra sempre".

    O status realista de quem ficou travado é `CREATED`/`UPDATING` — o
    `_item_remoto` dos outros arquivos devolve `UPDATED`. A adoção copiava esse
    status congelado para a conexão e NÃO agendava sync, apostando no
    `item/updated` seguinte. A aposta se paga no `item/created` FRESCO (o próximo
    é esperado em segundos) e é FALSA aqui: o item foi abandonado dias atrás e não
    vem evento nenhum.

    Os dois asserts, e o que cada um prova (medido, não deduzido):
      • `connection_ui_state` devolve "Atualizando…" na linha recém-escrita: é o
        card do dono, e é PRÉ-CONDIÇÃO, não o discriminante. Medido: o rótulo
        segue "Atualizando…" mesmo depois de um sync que deu certo, porque o ramo
        do `health` decide antes do `sem_sync` (`pluggy_health.py:498-504`) —
        tirá-lo de lá é outro PR;
      • o sync TEM de ser agendado: o assert DISCRIMINANTE, vermelho na `main`,
        onde `webhook_pluggy` volta vazio. O que ele compra é o EXTRATO — contas e
        transações entram na hora; sem ele, nada lê a Pluggy por essa conexão.

    O par oposto (item/created fresco continua SEM agendar) é
    `test_of_webhook_adopt.py::test_adocao_nao_agenda_sync_de_item_que_a_pluggy_ainda_esta_montando`.
    """
    from core.services.pluggy_health import connection_ui_state
    from scripts.adotar_items_of_orfaos import _executar

    item = "s-created"
    monkeypatch.setattr("frontend.routes.open_finance.get_pluggy_item",
                        lambda item_id, api_key=None: {"id": item_id, "status": "CREATED",
                                                       "clientUserId": str(user_id),
                                                       "connector": {"id": 612, "name": "Nubank"}})
    db.register_item(None, provider_item_id=item, origin="webhook")
    try:
        asyncio.run(_executar([item], apply=True, delete=False))

        linhas = db.get_connections_by_item_id(item)
        assert len(linhas) == 1, "a adoção acontece"
        assert db.list_pluggy_item_ids(user_id) == [item]
        # O card do dono, como a tela o desenha depois da adoção.
        assert connection_ui_state(linhas[0])["label"] == "Atualizando…"
        assert webhook_pluggy == [item], (
            "adoção retroativa sem sync agendado: nenhum evento vem depois e nada "
            "lê a Pluggy — a carteira do dono fica sem conta e sem transação")
    finally:
        db.disconnect_open_finance_connection(user_id)
        _limpa_item(item)
