#!/usr/bin/env python3
"""Quem é ÓRFÃO, e em quem o one-shot vai MEXER. Só leitura — nada aqui escreve.

Arquivo próprio (CLAUDE.md §0.5), cortado por assunto: `adotar_items_of_orfaos.py`
ficou com o EFEITO (adotar, apagar na Pluggy, a espera pelo sync e a saída do
processo) e aqui mora a decisão de sobre QUEM ele age. O porquê do script existir
está na docstring de lá.
"""
from __future__ import annotations

import db
from core.services.pluggy import _ITEM_ID_OK
from db.open_finance_state import ITEMS_SEM_CONEXAO


def recusa_id(item: str) -> str:
    """Por que este id NÃO pode ir para a Pluggy — string VAZIA quando pode.

    A CLASSE, num lugar só (§0.7) — as três portas DESTE SCRIPT, e só elas: o
    `--item` digitado (`parse_args`, rc=2 antes de qualquer leitura), a LISTA no
    ponto em que ela NASCE (`main`, antes de contar, imprimir ou prescrever
    qualquer id) e o laço de `_executar`, último portão antes da chamada.
    FORA daqui ela não roda, e a frase anterior dizia que sim: `pluggy_sync.py`
    (217, 534, 793), `open_finance_trial_expiry.py:81` e o
    `delete_pluggy_items_best_effort` (`open_finance.py:1898`) montam `/items/{id}`
    com id vindo do banco sem passar por ela — quem os cobre é o `_ITEM_ID_OK` de
    dentro do cliente. Estendê-la aos três é outro PR; aqui ela existe porque o
    alvo é escolhido por um operador e o `--delete` não volta.

    A versão anterior morava só no `parse_args`, e `--apply --delete` SEM
    `--item` não passava por guarda nenhuma. MEDIDO 2026-09-10 com o `_item_path`
    real e os ids `['0', '77', 'a/b', 'ação', 'zz-lista-ok']`: `0` e `77`
    chegaram INTEIROS ao `DELETE /items/{id}`, que é irreversível; os dois do
    meio só morreram dentro do cliente. O argumento que justifica a guarda do
    `--item` — remoto não é guarda de operação irreversível, e no dia em que o id
    existir lá, ele apaga — vale palavra por palavra aqui, e aqui nem digitação
    há: o id vem do registry, cuja ESCRITA não tem régua nenhuma
    (`provider_item_id` aceita qualquer string vinda do corpo do webhook).

    São DUAS réguas, e a segunda não é redundante:
      • `_ITEM_ID_OK` é a que JÁ existe para os três montadores de `/items/{id}`
        (§0.1). Ela NÃO exige UUID, de propósito — `core/services/pluggy.py:120`
        diz por quê: "exigir UUID recusaria id de sandbox e de ambiente de teste
        sem fechar nada a mais". Esta função não adota a premissa contrária;
      • só-dígitos é o recorte que pega o erro já cometido esta semana,
        `--item <user_id>`. Ele não conflita com o de cima: a Pluggy EMITE UUID, e
        200.000 `uuid4()` (medido 2026-09-10, com e sem hífen) deram 0 só-dígitos
        — a régua não recusa id dela no formato de hoje. O que sobra de risco é
        sandbox/homolog, que é exatamente o que o `_ITEM_ID_OK` protege.
    `isdigit` sem a surpresa de unicode que `core/services/pix_checkout.py` teve
    de blindar com `isascii()`: o charset ASCII do `_ITEM_ID_OK` já passou antes.
    """
    if not _ITEM_ID_OK.match(item):
        return "não é id de item da Pluggy (caractere que muda a URL, vazio, ou > 64)"
    if item.isdigit():
        return "é só dígitos: isso é um user_id, não o id de item da Pluggy"
    return ""


def _dica_de_recusa() -> str:
    """O que fazer quando o id RECUSADO veio do dry-run, e não de um dedo errado.

    O beco: a ESCRITA do registry não tem régua (`provider_item_id` guarda o que
    vier no corpo do webhook) e a LEITURA tem, então o dry-run chegava a listar —
    e a PRESCREVER `--item <ID> --apply --delete` para — id que o `parse_args`
    recusa. Medido com ids gravados por `tests/test_corpo_json_string_venenosa.py`:
    `42`, `007`, `a/b`, `ação` e um de 80 chars saíam da lista e voltavam rc=2.

    A decisão: a régua de leitura CONTINUA recusando o que a escrita aceitou, e
    não é teimosia. Para o que nem vira URL é certeza (`_item_path` os barra dentro
    do cliente); para o só-dígitos é HIPÓTESE forte, não teorema — `core/services/
    pluggy.py:120` diz que exigir UUID recusaria id de sandbox, e por isso o texto
    abaixo oferece a saída pelo painel em vez de afirmar que o item não existe lá.
    O que faltava era dizer isso, e o `main` deixou de contar e de prescrever esses
    ids (é ele quem filtra a lista onde ela nasce).

    Não fala do id VAZIO: esse é sintoma de DIGITAÇÃO (`--item "$VAR"` com a
    variável não setada) e a dica dele mora no `parse_args`, a única porta onde
    alguém digita. Aqui o texto tem de servir também ao dry-run, que nunca lista
    id vazio vindo de shell.

    Fica de fora, e de propósito: apagar a linha do registry. O rastro é o que faz
    o item aparecer no dry-run, e o script inteiro é construído para não apagá-lo
    (docstring do módulo). Limpeza de rastro envenenado é outro PR, com outra régua.
    """
    # Serve às DUAS portas (o erro do `parse_args` e a prescrição do dry-run), então
    # não presume qual das duas o operador está lendo nem de quem foi o engano.
    return ("\n  Id assim quase certamente não veio da Pluggy: o registry ACEITA na escrita"
            "\n  o que ela nunca emitiria — quem escreve é o corpo do webhook. O provável é"
            "\n  não haver nada a apagar lá, o `avoidDuplicates` não estar preso neste id e"
            "\n  a reconexão pelo app não depender disto. Se você tem certeza do contrário"
            "\n  (sandbox/homolog), o caminho é o painel da Pluggy: este script não manda"
            "\n  id que ela não emite.")


def listar_items_sem_conexao() -> dict[str, set[str]]:
    """Todo item do registry SEM conexão local, e as origens COM DONO de cada um.

    Uma leitura, dois grupos. O de origens não-vazias é o que `alvos` descarta,
    e o descarte era SILENCIOSO: o dry-run — que a mensagem de `_abandona`
    manda rodar como fonte de verdade — não imprimia uma linha sobre ele. Quem os
    separa em blocos com saídas diferentes é o `main` do script — que antes de
    separar tira daqui os ids que `recusa_id` recusa, para que nenhum dos dois
    grupos os CONTE (é o ponto único onde a lista nasce).

    Só items da PLUGGY: `ITEMS_SEM_CONEXAO` é compartilhado com o painel e não
    restringe provider (de propósito — o painel conta todos), mas tudo que o script
    faz com o id (`_adota_item_orfao`, `delete_pluggy_item`) fala com a Pluggy. Sem
    o filtro, um item de outro provider entrava na lista e o `--apply` gastava auth
    + GET para logar 404. Não é alcançável hoje (nenhum chamador de produção passa
    `provider=` a `register_item`), e é exatamente por isso que viraria erro
    silencioso no dia do segundo provider.

    ponytail: uma query por item candidato em vez de um `having` no mesmo SELECT.
    Num one-shot sobre um punhado de items é mais barato que manter duas versões
    do predicado; se a lista crescer para milhares, é `where not exists`.
    """
    provider = "pluggy"
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"select distinct r.provider_item_id as item {ITEMS_SEM_CONEXAO}"
            " and r.provider = %s order by 1", (provider,))
        vistos = [r["item"] for r in (cur.fetchall() or [])]
    # Origens, nunca `user_id`: o operador decide sobre o ITEM, e nada de outro usuário
    # sai daqui (docstring de `item_registry_origins`).
    return {i: db.item_registry_origins(i, provider=provider) for i in vistos}


def alvos(item: str | None, sem_conexao: dict[str, set[str]]) -> list[str]:
    """Os ids em que o script vai MEXER — e com `--delete` isso é IRREVERSÍVEL.

    `item is not None`, e NÃO `if item`: testado por verdade, `--item ""` caía no
    modo LISTA em silêncio. Medido em banco real com `--item "" --apply --delete`:
    alvos `['r5-vaz-a', 'r5-vaz-b']` onde o esperado era `['']`. A linha que o
    dry-run manda copiar é `--item "$ITEM_ID"`, e o shell entrega string VAZIA
    quando a variável não está setada — o `--delete` varria TODOS os órfãos do
    registry, sem confirmação e sem volta. Esta linha é a que DECIDE, e é ela que
    não pode confundir "vazio" com "não pediram item nenhum"; quem RECUSA o id
    ruim é `recusa_id`, nas três portas que a docstring dela lista.

    O modo LISTA é "item que o webhook viu e NUNCA conseguiu atribuir a um dono":
    parte do predicado do painel (`ITEMS_SEM_CONEXAO`, uma fonte só) e aperta por
    cima dele com o MESMO `db.item_registry_origins` que a adoção pelo webhook usa
    (§0.7 — o `having count(r.user_id) = 0` que já morou aqui era uma segunda
    versão da mesma regra, em SQL): origens vazias = nenhuma linha de rastro deste
    item tem dono. Filtra por `user_id` e não por `origin`, porque item que passou
    pelos dois estados tem linha dos dois tipos e basta UM dono para não ser órfão.
    A diferença é a que separa adotar de RESSUSCITAR (docstring de
    `db.item_registry_origins`): um `--apply` sobre banco REMOVIDO o reataria com
    sync agendado, re-importando na carteira que o usuário limpou.

    Havia um `listar_items_orfaos()` guardando esta prosa — `alvos(None,
    listar_items_sem_conexao())` com outro nome, sem chamador de produção (o `main`
    precisa dos DOIS grupos e chama `alvos` direto) e exercitado só por teste, que
    então media um caminho que ninguém percorre. Apagado: os testes passam a
    compor o mesmo par que o `main` compõe.

    ponytail: o teto é a adoção que falhou DEPOIS do `register_item` (a ordem é de
    propósito, ver `_adota_item_orfao`) — ela deixa rastro com dono e sem conexão,
    indistinguível de banco removido, e o modo LISTA deixa de oferecê-la. Deixar
    de VER é outra coisa: o dry-run a reporta em bloco próprio, junto com o banco
    removido e dizendo que não separa os dois (`main`). Trocar "não ressuscita o
    que o usuário removeu" por "não perde o item de uma adoção que falhou" seria o
    negócio errado. Separar os dois exige o disconnect deixar o próprio rastro no
    registry (`origin='disconnect'`) — mudança de escrita em outro fluxo, outro PR.
    A saída para esse item é operacional e explícita: `--item ID --apply --delete`
    (ver `parse_args`), e o dry-run só a prescreve para id que a régua aceita.
    """
    return ([item] if item is not None
            else [i for i, origens in sem_conexao.items() if not origens])
