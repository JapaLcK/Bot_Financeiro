"""G5e — em QUEM o one-shot mexe: a lista de órfãos, o `--item` e as guardas.

Arquivo próprio (CLAUDE.md §0.5), cortado por assunto do irmão
`test_adotar_items_of_orfaos.py`: lá se prova o que o script ESCREVE quando adota;
aqui, quem entra na mira antes de qualquer escrita — e com `--delete` a mira é
irreversível. A espera pelo sync e o que o operador LÊ são dos outros dois
(`test_adotar_items_prazo.py`, `test_adotar_items_saida.py`).

Os helpers vêm de `test_of_webhook_adopt_guards.py`, fonte única deles (§0.1).
"""

from __future__ import annotations

import asyncio
import sys

import pytest

import db
from test_of_webhook_adopt_guards import _limpa_item, _mock_item


def test_script_lista_so_o_item_sem_conexao_e_o_default_e_dry_run(user_id, monkeypatch):
    """UM check do que decide o script: o predicado e o default dos args.

    Se o predicado quebrar, o `--apply` passa a mexer em item que JÁ tem conexão;
    se o default deixar de ser dry-run, um `-m scripts.…` sem flag escreve.
    """
    from scripts.adotar_items_lista import alvos, listar_items_sem_conexao
    from scripts.adotar_items_of_orfaos import parse_args

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
        # O par que o `main` compõe (`sem_conexao = listar_items_sem_conexao()` e
        # `alvos(args.item, sem_conexao)`), e não um embrulho com outro nome que
        # o `main` não chama.
        orfaos = alvos(None, listar_items_sem_conexao())
        assert "item-orfao-script" in orfaos
        assert "item-com-conexao" not in orfaos, "item COM conexão não é órfão"
        # `--apply` sobre estes dois RESSUSCITARIA o removido (ver
        # `alvos`), e inflaria o número que o operador lê para
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

    # O cão de guarda mira quem o `main` CHAMA: era `listar_items_orfaos`, que o
    # `main` deixou de chamar quando passou a ler os dois grupos — o `pytest.fail`
    # ficou numa função que ninguém executa (provado: 16 passed com o
    # curto-circuito do `--item` removido). Agora é a leitura de verdade.
    monkeypatch.setattr(script, "listar_items_sem_conexao",
                        lambda: pytest.fail("consultou a lista em vez de usar o --item"))
    monkeypatch.setattr(script, "_executar",
                        lambda *a, **k: pytest.fail("mexeu em item COM conexão viva"))
    # `signal.signal` dublado: o `main` de verdade põe SIGPIPE em SIG_DFL, e isso
    # vale para o PROCESSO — depois deste teste, a sessão inteira do pytest perdia
    # o `BrokenPipeError` e morria muda num pipe fechado. Quem prova a linha de
    # verdade é `test_adotar_items_saida.py::test_pipe_fechado_…`, em subprocesso.
    monkeypatch.setattr(script.signal, "signal", lambda *_: None)
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


def test_item_vazio_e_um_alvo_explicito_e_nao_a_lista_inteira():
    """P1 de DINHEIRO IRREVERSÍVEL: `--item ""` apagava TODOS os órfãos na Pluggy.

    `args.item` era testado por VERDADE, não por `is not None`: string vazia caía
    no modo LISTA em silêncio — e `--item "$ITEM_ID"` com a variável não setada é
    exatamente o que o shell entrega. Medido em banco real com `--item "" --apply
    --delete`: alvos `['r5-vaz-a', 'r5-vaz-b']` onde o esperado era `['']`. O
    `delete_pluggy_item` rodava em CADA órfão do registry, sem confirmação e sem
    volta, e a guarda de conexão viva (`if args.item and ...`) era pulada junto.

    São DUAS guardas e cada metade deste teste discrimina uma:
      • `alvos` com `is not None` — quem DECIDE. Mutação: voltar para `if item` →
        o 1º assert devolve os dois ids;
      • a recusa no `parse_args` — a porta por onde o vazio chega. Mutação: apagar
        os dois `ap.error` do `--item` → o laço abaixo para de levantar.
    CONTROLE POSITIVO nas duas: `--item` legítimo continua passando inteiro (sem
    ele, tudo passaria num script que recusa todo `--item` e não mira ninguém).
    """
    from scripts.adotar_items_lista import alvos
    from scripts.adotar_items_of_orfaos import parse_args

    sem_conexao = {"r5-vaz-a": set(), "r5-vaz-b": set(), "r5-dono": {"webhook_adopt"}}
    assert alvos("", sem_conexao) == [""], "vazio virou a lista inteira"
    assert alvos(None, sem_conexao) == ["r5-vaz-a", "r5-vaz-b"], "sem --item: os órfãos"
    assert alvos("r5-qualquer", sem_conexao) == ["r5-qualquer"]

    uuid = "8a5b1c2d-3e4f-5a6b-7c8d-9e0f1a2b3c4d"
    for ruim in ("", "   ", "a/b", "x?include=all", "../accounts", "7", "0012"):
        with pytest.raises(SystemExit):
            parse_args(["--item", ruim, "--apply", "--delete"])
    args = parse_args(["--item", uuid, "--apply", "--delete"])
    assert args.item == uuid and args.delete is True


def test_a_lista_tambem_passa_pela_regua_do_item(monkeypatch, capsys):
    """A IRMÃ da guarda do `--item`: o modo LISTA não passava por guarda nenhuma.

    A régua morava só no `parse_args`, que é a porta do id DIGITADO. `--apply
    --delete` SEM `--item` pega o id do REGISTRY — cuja escrita não tem régua
    (`provider_item_id` guarda o que vier no corpo do webhook) — e ia direto para
    `DELETE /items/{id}`, que é irreversível. MEDIDO 2026-09-10 com o `_item_path`
    real no lugar do dublê, sobre os ids deste teste: `0` e `77` chegaram INTEIROS
    ao delete, e `a/b`/`ação` só morreram DENTRO do cliente. O argumento que
    justificou a guarda do `--item` — remoto não é guarda de operação
    irreversível, e no dia em que o id existir lá, ele apaga — vale palavra por
    palavra aqui, e aqui nem digitação há.

    Mede o LAÇO de `_executar`, não o `parse_args`: o laço é onde os DOIS modos se
    juntam, e mirar a junção é o que fecha a CLASSE em vez da instância (§2).

    MUTAÇÃO: apagar o `if motivo := recusa_id(item_id): … continue` de `_executar`
    → os seis ids chegam ao delete (o dublê nem barra `a/b`) e o 1º assert cai.
    CONTROLE POSITIVO dentro do mesmo assert: `zz-lista-ok` continua sendo
    apagado — sem ele, tudo passaria num laço que recusasse TODOS os ids.
    """
    import core.services.pluggy as pluggy_svc
    import scripts.adotar_items_of_orfaos as script

    deletados: list[str] = []
    monkeypatch.setattr(pluggy_svc, "get_pluggy_item",
                        lambda i, api_key=None: {"status": "UPDATED"})
    monkeypatch.setattr(pluggy_svc, "delete_pluggy_item",
                        lambda item_id, api_key=None: deletados.append(item_id))

    ruins = ["0", "77", "a/b", "ação", "x" * 80, ""]
    asyncio.run(script._executar(ruins + ["zz-lista-ok"], apply=True, delete=True))

    assert deletados == ["zz-lista-ok"], deletados
    saida = capsys.readouterr().out
    assert saida.count("PULADO") == len(ruins), saida


def test_o_dry_run_nao_conta_nem_prescreve_o_que_a_regua_recusa(monkeypatch, capsys):
    """O BECO e o NÚMERO, nos DOIS grupos — porque a régua é UMA, no ponto em que a
    lista NASCE.

    O beco: a ferramenta que existe para destravar recusava a cura que imprimia. A
    régua da ESCRITA (nenhuma) e a da LEITURA divergem, e a divergência saía na
    tela — o dry-run listava o usuário travado e mandava rodar `--item <ID> --apply
    --delete` com id que o `parse_args` devolve rc=2. MEDIDO com ids gravados pelo
    webhook em `tests/test_corpo_json_string_venenosa.py`: `42`, `007`, `a/b`,
    `ação` e um de 80 chars eram listados e recusados.

    O número: o conserto anterior pôs a régua num `if` por GRUPO, no com-dono, e
    deixou a IRMÃ aberta — o grupo dos ÓRFÃOS era contado, impresso e prescrito sem
    passar por ela. Reproduzido: `6 item(s) órfão(s): 42, 007, a/b, ação, xxxx…(80),
    ok-8a5b1c2d` seguido de `Rode com --apply para adotar, ou --apply --delete`. O
    operador lê SEIS e decide rodar o irreversível; o script age em UM. Por isso o
    filtro é único e roda onde a lista nasce (`main`), antes de virar os dois grupos.

    A DECISÃO (docstring de `_dica_de_recusa`): a leitura continua recusando, e o
    que faltava era DIZER isso — os recusados saem num bloco só, fora das duas
    contas, com a dica.

    MUTAÇÃO: devolver o filtro para dentro do bloco do com-dono (`if recusados :=
    [i for i in meio …]`) → o 1º assert lê `3 item(s) órfão(s)`. CONTROLE POSITIVO
    nos dois grupos: `ok-…` continua contado como órfão, `meio-…` continua contado e
    com a cura prescrita, e o `parse_args` continua aceitando o id bom — sem eles, o
    teste passaria num script que recusasse todo mundo e não mirasse ninguém.
    """
    import scripts.adotar_items_of_orfaos as script
    from scripts.adotar_items_of_orfaos import parse_args

    orfaos_ruins, meio_ruins = ["42", "a/b"], ["007", "ação", "x" * 80]
    bom, bom_meio = "ok-8a5b1c2d", "meio-3c4d"
    monkeypatch.setattr(script.signal, "signal", lambda *_: None)
    monkeypatch.setattr(script, "listar_items_sem_conexao",
                        lambda: {**{i: set() for i in orfaos_ruins + [bom]},
                                 **{i: {"webhook_adopt"} for i in meio_ruins + [bom_meio]}})
    monkeypatch.setattr(sys, "argv", ["adotar"])
    script.main()
    saida = capsys.readouterr().out

    # os DOIS números contam só quem o script pode mesmo mirar...
    assert f"1 item(s) órfão(s): {bom}" in saida, saida
    assert "1 item(s) COM rastro de dono" in saida, saida
    # ...e a cura CONTINUA prescrita para quem a régua aceita
    assert "--item <ID> --apply --delete" in saida and bom_meio in saida, saida

    linhas = saida.splitlines()
    conta_orfaos = next(l for l in linhas if "item(s) órfão(s)" in l)
    conta_meio = next(l for l in linhas if "COM rastro de dono" in l)
    for ruim in orfaos_ruins + meio_ruins:
        # sai UMA vez, no bloco que o tira das duas contas — nunca numa delas
        assert saida.count(repr(ruim)) == 1, (ruim, saida)
        assert ruim not in conta_orfaos and ruim not in conta_meio, (ruim, saida)
        with pytest.raises(SystemExit):
            parse_args(["--item", ruim, "--apply", "--delete"])
    assert parse_args(["--item", bom, "--apply", "--delete"]).item == bom
