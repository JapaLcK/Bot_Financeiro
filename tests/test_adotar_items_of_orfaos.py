"""G5e — o one-shot `scripts/adotar_items_of_orfaos.py`.

Arquivo próprio (CLAUDE.md §0.5): o assunto aqui é o SCRIPT, não o webhook — os
três arquivos `test_of_webhook_adopt*.py` cobrem a adoção pela porta do webhook.
Os helpers vêm de `test_of_webhook_adopt_guards.py`, que é a fonte única deles
(CLAUDE.md §0.1); copiá-los para cá seria a mesma duplicação que já custou uma
rodada.

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
`test_script_apply_com_status_de_item_created_real_nao_agenda_sync` vermelhos —
os dois estão VERDES hoje, que é o que os torna discriminantes.
CONTROLE POSITIVO do grupo: o mesmo
`test_script_apply_adota_e_destrava_a_reconexao_do_dono` — sem ele, o caso de
recusa abaixo passaria num script que não adota NADA, que é pior que o bug.
"""

from __future__ import annotations

import asyncio

import pytest
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
    from scripts.adotar_items_of_orfaos import _executar, listar_items_orfaos

    item = "s-removido"
    _mock_item(monkeypatch, user_id)
    db.register_item(user_id, provider_item_id=item, origin="pluggy_item")
    try:
        assert item not in listar_items_orfaos(), "a lista não podia oferecê-lo"

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


def test_script_apply_com_status_de_item_created_real_nao_agenda_sync(
        user_id, monkeypatch, eventos, webhook_pluggy):
    """O status realista de quem ficou travado é `CREATED`/`UPDATING`, não `UPDATED`.

    O `_item_remoto` dos outros arquivos devolve `UPDATED` — o ramo que AGENDA
    sync. Quem fechou a aba no meio do widget (o caso que este script existe para
    destravar) está no ramo oposto: adota e NÃO agenda, porque a Pluggy ainda
    está montando o item e o sync acharia zero conta.
    """
    from scripts.adotar_items_of_orfaos import _executar

    item = "s-created"
    monkeypatch.setattr("frontend.routes.open_finance.get_pluggy_item",
                        lambda item_id, api_key=None: {"id": item_id, "status": "CREATED",
                                                       "clientUserId": str(user_id),
                                                       "connector": {"id": 612, "name": "Nubank"}})
    db.register_item(None, provider_item_id=item, origin="webhook")
    try:
        asyncio.run(_executar([item], apply=True, delete=False))

        assert len(db.get_connections_by_item_id(item)) == 1, "a adoção acontece"
        assert db.list_pluggy_item_ids(user_id) == [item]
        assert webhook_pluggy == [], "sync agendado num item que a Pluggy ainda está montando"
    finally:
        db.disconnect_open_finance_connection(user_id)
        _limpa_item(item)


# ── a lista, os args e o laço (vindos de `test_of_webhook_adopt.py`) ─────────

def test_script_lista_so_o_item_sem_conexao_e_o_default_e_dry_run(user_id, monkeypatch):
    """UM check do que decide o script: o predicado e o default dos args.

    Se o predicado quebrar, o `--apply` passa a mexer em item que JÁ tem conexão;
    se o default deixar de ser dry-run, um `-m scripts.…` sem flag escreve.
    """
    from scripts.adotar_items_of_orfaos import listar_items_orfaos, parse_args

    assert parse_args([]).apply is False and parse_args([]).delete is False
    assert parse_args(["--apply"]).apply is True

    _mock_item(monkeypatch, user_id)
    # o rastro do bug antigo: o webhook viu e NÃO conseguiu atribuir
    db.register_item(None, provider_item_id="item-orfao-script", origin="webhook")
    db.save_pluggy_open_finance_item(
        user_id, {"id": "item-com-conexao", "status": "UPDATED",
                  "connector": {"id": 612, "name": "Nubank"}})
    db.register_item(user_id, provider_item_id="item-com-conexao", origin="pluggy_item")
    # banco CONECTADO e depois REMOVIDO (o porquê de ele ficar assim para sempre:
    # docstring de `db.item_registry_origins`): sem conexão, COM rastro de dono.
    db.register_item(user_id, provider_item_id="item-removido", origin="pluggy_item")
    # e o item que passou pelos dois estados: rastro sem dono E rastro com dono
    db.register_item(None, provider_item_id="item-removido-2", origin="webhook")
    db.register_item(user_id, provider_item_id="item-removido-2", origin="webhook_adopt")
    # item de OUTRO provider: `ITEMS_SEM_CONEXAO` é compartilhado com o painel e
    # não restringe provider — sem o filtro do script, ele entra na lista e o
    # `--apply` manda o id para a Pluggy (auth + GET, 404 no log).
    db.register_item(None, provider_item_id="item-belvo", origin="webhook", provider="belvo")
    try:
        orfaos = listar_items_orfaos()
        assert "item-orfao-script" in orfaos
        assert "item-com-conexao" not in orfaos, "item COM conexão não é órfão"
        # `--apply` sobre estes dois RESSUSCITARIA o removido (ver
        # `listar_items_orfaos`), e inflaria o número que o operador lê para
        # decidir rodar o `--apply --delete`, que é irreversível.
        assert "item-removido" not in orfaos, "banco REMOVIDO pelo usuário virou alvo de adoção"
        assert "item-removido-2" not in orfaos, "basta UMA linha com dono para não ser órfão"
        assert "item-belvo" not in orfaos, "item de outro provider virou alvo da Pluggy"
    finally:
        db.disconnect_open_finance_connection(user_id)
        for i in ("item-orfao-script", "item-com-conexao", "item-removido",
                  "item-removido-2", "item-belvo"):
            _limpa_item(i)


def test_script_apagar_na_pluggy_exige_apply(capsys):
    """`--delete` sozinho já era destrutivo na PRIMEIRA invocação — só o
    `--apply`, que é o menos destrutivo dos dois, exigia flag."""
    from scripts.adotar_items_of_orfaos import parse_args

    with pytest.raises(SystemExit):
        parse_args(["--delete"])
    assert "--apply" in capsys.readouterr().err
    assert parse_args(["--apply", "--delete"]).delete is True


def test_script_item_explicito_pula_a_lista_menos_o_que_tem_conexao_viva(
        user_id, monkeypatch, capsys):
    """A única saída do item que a lista NÃO vê (rastro com dono e sem conexão, de
    uma adoção que falhou no meio): explícito, operacional, sem reabrir a lista.

    Pular a lista é pular o predicado dela: com um id digitado errado, `--item
    --apply --delete` apagava na Pluggy — IRREVERSÍVEL — o item de um usuário
    SAUDÁVEL, e a linha local ficava apontando para item morto, sem aviso na tela.
    """
    import scripts.adotar_items_of_orfaos as script

    monkeypatch.setattr(script, "listar_items_orfaos",
                        lambda: pytest.fail("consultou a lista em vez de usar o --item"))
    monkeypatch.setattr(script, "_executar",
                        lambda *a, **k: pytest.fail("mexeu em item COM conexão viva"))
    monkeypatch.setattr("sys.argv", ["adotar", "--item", "z-preso"])

    script.main()   # dry-run: sem --apply, não escreve nada

    # CONTROLE POSITIVO da guarda abaixo: item SEM conexão local continua passando
    # — sem ele, tudo passaria numa guarda que recusa todo `--item`.
    assert "z-preso" in capsys.readouterr().out

    db.save_pluggy_open_finance_item(user_id, {"id": "z-vivo", "status": "UPDATED",
                                               "connector": {"id": 612, "name": "Nubank"}})
    try:
        assert db.get_connections_by_item_id("z-vivo"), "pré-condição: conexão viva"
        monkeypatch.setattr("sys.argv", ["adotar", "--item", "z-vivo", "--apply", "--delete"])
        with pytest.raises(SystemExit):
            script.main()
    finally:
        db.disconnect_open_finance_connection(user_id)
        _limpa_item("z-vivo")


def test_script_segue_para_o_proximo_item_quando_um_falha(monkeypatch, capsys):
    """O try cobria só o `get_pluggy_item`: falha do `delete_pluggy_item` no item
    2 subia e o item 3 nunca era processado — num one-shot que existe para
    destravar N usuários, esse é o pior desfecho."""
    import core.services.pluggy as pluggy_svc
    import scripts.adotar_items_of_orfaos as script

    # A chave da Pluggy FALHANDO: ela era montada uma vez, antes do laço e fora de
    # todo `try` — 401/timeout dela derrubava o one-shot com ZERO item processado,
    # o mesmo desfecho que o `try` por item veio evitar.
    monkeypatch.setattr(pluggy_svc, "create_pluggy_api_key",
                        lambda: (_ for _ in ()).throw(RuntimeError("Pluggy 401")))
    monkeypatch.setattr(pluggy_svc, "get_pluggy_item",
                        lambda i, api_key=None: {"id": i, "status": "UPDATED",
                                                 "clientUserId": "1",
                                                 "connector": {"name": "Nubank"}})
    monkeypatch.setattr(pluggy_svc, "delete_pluggy_item",
                        lambda item_id, api_key=None: (_ for _ in ()).throw(RuntimeError("Pluggy 500"))
                        if item_id == "i2" else None)

    asyncio.run(script._executar(["i1", "i2", "i3"], apply=False, delete=True))

    saida = capsys.readouterr().out
    assert "i3" in saida, f"o loop parou no i2 e nunca chegou no i3:\n{saida}"
    assert "i1" in saida, f"o run morreu antes do primeiro item:\n{saida}"
