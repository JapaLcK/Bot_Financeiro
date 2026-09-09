#!/usr/bin/env python3
"""Adota (ou apaga na Pluggy) os items de Open Finance que ficaram órfãos.

O problema que ele destrava está descrito em UM lugar (CLAUDE.md §0.7): a
docstring de `frontend/routes/open_finance._adota_item_orfao`, "Por que existe".
Resumo de uma linha: item vivo na Pluggy sem linha local nenhuma aqui.

O webhook passou a adotar sozinho (`_adota_item_orfao`), mas isso só vale de agora
em diante: este script é o one-shot que destrava quem já está travado.

O ÚNICO rastro de um item órfão é o `open_finance_item_registry` — o `GET /items`
da Pluggy devolve 401 (docs/CLAUDE.md, db/open_finance_state.py), então o universo
remoto não é enumerável. Se o dry-run vier VAZIO, a leitura correta não é "não há
item órfão": é que o webhook não estava configurado quando o item foi criado, e
então não existe rastro nenhum dele em lugar nenhum.

Rodar de novo é BARULHENTO, não destrutivo — em nenhum dos dois modos ele é
idempotente no EFEITO. Adotado o item, ele deixa de aparecer na lista (ganhou
conexão e rastro com dono), mas `register_item` é INSERT puro: uma tentativa que
falhe no meio deixa mais uma linha de rastro por rodada. Com `--delete`, nada
apaga a linha do registry — que é justamente o rastro que se quer manter —, então
cada rodada nova imprime o erro do `get_pluggy_item` (404, item já apagado lá).

Escrever exige SEMPRE `--apply` — inclusive para apagar, que é a operação
irreversível.

Uso:
    .venv/bin/python -m scripts.adotar_items_of_orfaos                    # dry-run (DEFAULT)
    .venv/bin/python -m scripts.adotar_items_of_orfaos --apply            # adota
    .venv/bin/python -m scripts.adotar_items_of_orfaos --apply --delete   # apaga na Pluggy
    .venv/bin/python -m scripts.adotar_items_of_orfaos --item ID --apply --delete   # um item
"""
from __future__ import annotations

import argparse
import asyncio

import db
from db.open_finance_state import ITEMS_SEM_CONEXAO


def listar_items_orfaos() -> list[str]:
    """Item que o webhook viu e NUNCA conseguiu atribuir a um dono.

    Parte do mesmo predicado do painel (`ITEMS_SEM_CONEXAO`, uma fonte só) e
    aperta por cima dele com o MESMO `db.item_registry_origins` que a adoção pelo
    webhook usa (CLAUDE.md §0.7 — o `having count(r.user_id) = 0` que morava aqui
    era uma segunda versão da mesma regra em SQL): vazio = nenhuma linha de
    rastro deste item tem dono.

    A diferença é a que separa adotar de RESSUSCITAR, e a regra mora na docstring
    de `db.item_registry_origins` (por que banco REMOVIDO casa com o predicado do
    painel para sempre). O que ela custa AQUI: um `--apply` sobre um removido o
    reataria com sync agendado, re-importando na carteira que o usuário limpou.

    Filtra por `user_id`, não por `origin`: item que teve as duas coisas (webhook
    sem dono e, depois, conexão pelo navegador) tem linha dos dois tipos, e é a
    presença de QUALQUER dono que decide.

    ponytail: o teto é a adoção que falhou DEPOIS do `register_item` (a ordem é
    de propósito, ver `_adota_item_orfao`) — ela deixa rastro com dono e sem
    conexão, indistinguível de banco removido, e este script deixa de vê-la.
    Trocar "não ressuscita o que o usuário removeu" por "não perde o item de uma
    adoção que falhou" seria o negócio errado. Separar os dois exige o disconnect
    deixar o próprio rastro no registry (`origin='disconnect'`) — mudança de
    escrita em outro fluxo, outro PR. A saída para esse item é operacional e
    explícita: `--item ID --apply --delete` (ver `parse_args`).

    Só items da PLUGGY: `ITEMS_SEM_CONEXAO` é compartilhado com o painel e não
    restringe provider (de propósito — o painel conta todos), mas tudo que este
    script faz com o id (`_adota_item_orfao`, `delete_pluggy_item`) fala com a
    Pluggy. Sem o filtro, um item de outro provider entrava na lista e o
    `--apply` gastava auth + GET para logar 404. Não é alcançável hoje (nenhum
    chamador de produção passa `provider=` a `register_item`), e é exatamente por
    isso que viraria erro silencioso no dia do segundo provider.

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
    return [i for i in vistos if not db.item_registry_origins(i, provider=provider)]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="escreve de verdade (sem isto, dry-run)")
    ap.add_argument("--delete", action="store_true",
                    help="com --apply: apaga o item na Pluggy em vez de adotar "
                         "(libera o avoidDuplicates). É irreversível.")
    # A ÚNICA saída para o item que ficou com rastro COM dono e SEM conexão (a
    # adoção que falhou entre o `register_item` e a escrita da conexão): a lista
    # não o mostra — é indistinguível de banco removido — e a retentativa do
    # `item/created` não o readota (guarda do `_adota_item_orfao`). Adotar por
    # aqui também é recusado, de propósito: `--apply --delete` apaga o item na
    # Pluggy, o `avoidDuplicates` libera, e o usuário reconecta pelo widget.
    # Fora do default e com id explícito digitado pelo operador: nada aqui
    # ressuscita nada sozinho.
    ap.add_argument("--item", metavar="ITEM_ID",
                    help="opera SÓ neste item, pulando a lista. Para o item que a "
                         "lista não vê (rastro com dono, sem conexão): use com "
                         "--apply --delete e peça a reconexão ao usuário. Item com "
                         "conexão LOCAL viva é recusado (ver main).")
    args = ap.parse_args(argv)
    if args.delete and not args.apply:
        # `--delete` sozinho já apagava item na Pluggy na PRIMEIRA invocação, sem
        # nenhuma confirmação — só o `--apply` (que é o menos destrutivo dos dois)
        # exigia flag. Uma porta, uma chave.
        ap.error("--delete apaga item na Pluggy: use junto com --apply.")
    return args


async def _executar(items: list[str], *, apply: bool, delete: bool) -> None:
    # Import tardio só para manter o app FastAPI fora de quem não o usa (`--help`,
    # `parse_args`, dry-run): ele custa ~3× os módulos de `import db` (medido em
    # 2026-09-08 com `python -c "import sys; import M; print(len(sys.modules))"`;
    # remede antes de reusar o número, CLAUDE.md §2).
    # NÃO é por variável de ambiente: a versão anterior deste comentário dizia
    # que o import "exige DATABASE_URL/JWT_SECRET" e que "o dry-run tem de rodar
    # sem isso", e as duas metades são falsas — o import roda com as duas APAGADAS do
    # ambiente, e o dry-run lê o banco (`listar_items_orfaos` abre `db.get_conn()`).
    from core.services.pluggy import delete_pluggy_item, get_pluggy_item
    from frontend.routes.open_finance import _adota_item_orfao

    for item_id in items:
        # UM try por ITEM, cobrindo o corpo inteiro: com ele só no
        # `get_pluggy_item`, uma falha do `delete_pluggy_item` (ou da adoção) no
        # item 2 subia e o item 3 nunca era processado — num one-shot que se roda
        # para destravar N usuários, isso é o pior desfecho possível.
        try:
            if delete:
                remote = await asyncio.to_thread(get_pluggy_item, item_id)
                dono = remote.get("clientUserId")
                conector = (remote.get("connector") or {}).get("name")
                print(f"{item_id}: status={remote.get('status')} banco={conector} dono={dono}")
                # Sem chave pré-fabricada: `delete_pluggy_item` cria a dele
                # (`api_key or create_pluggy_api_key()`). Uma chave só, montada
                # antes do laço, ficava FORA de todo `try` — falha dela (401,
                # timeout) derrubava o one-shot com ZERO item processado, que é
                # exatamente o desfecho que o `try` por item veio evitar.
                # ponytail: MEDIDO — 2 POST /auth por item aqui (um do
                # `get_pluggy_item`, outro do `delete_pluggy_item`). Num one-shot
                # de punhado de items é mais barato que a rodada perdida.
                await asyncio.to_thread(delete_pluggy_item, item_id)
                print("   apagado na Pluggy")
            elif apply:
                # O `GET /items` que imprimia status/banco/dono saiu daqui: o
                # `_adota_item_orfao` faz o MESMO GET logo em seguida, e eram 2
                # POST /auth + 2 GET por item para imprimir uma linha. Agora é 1
                # de cada, e o dono sai no print abaixo.
                adotado = await _adota_item_orfao(item_id, "script_adopt")
                print(f"{item_id}: adotado por {adotado}" if adotado else
                      f"{item_id}: NÃO adotado (sem dono, já teve dono, usuário "
                      "inexistente ou teto do plano — ver system_event_logs)")
        except Exception as exc:  # noqa: BLE001 — one-shot: reporta e segue
            print(f"{item_id}: ERRO ({type(exc).__name__}: {exc}) — segue para o próximo")

    # A adoção agenda o sync numa task de fundo (`_schedule_pluggy_sync`); sem
    # esperar, o `asyncio.run` fecha o loop e mata a coleta pela metade.
    pendentes = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pendentes:
        await asyncio.gather(*pendentes, return_exceptions=True)


def main() -> None:
    args = parse_args()
    items = [args.item] if args.item else listar_items_orfaos()

    # `--item` pula a LISTA, logo pula o predicado dela: um id digitado errado
    # mandava para a Pluggy o item de um usuário SAUDÁVEL. Com `--delete` isso é
    # IRREVERSÍVEL — o item morre lá, a linha local continua apontando para ele,
    # e o usuário não recebe aviso nenhum na tela. Recusa aqui, antes de qualquer
    # escrita (operação irreversível em caminho de dinheiro é carve-out do §0.2).
    if args.item and db.get_connections_by_item_id(args.item):
        raise SystemExit(
            f"{args.item} tem conexão LOCAL viva: não é órfão, e este script não "
            "é a porta para removê-lo. Confira o id; se o usuário quer mesmo "
            "removê-lo, o caminho é o app (DELETE /open-finance/{uid}), que apaga "
            "os dois lados.")
    if not items:
        print("Nenhum item órfão no registry.")
        print("ATENÇÃO: registry vazio NÃO prova que não há item órfão na Pluggy — o "
              "GET /items dela devolve 401 e o registry só tem o que o webhook viu. "
              "Item criado com o webhook desconfigurado não deixou rastro nenhum.")
        return

    print(f"{len(items)} item(s) órfão(s): {', '.join(items)}")
    if not args.apply:
        print("Dry-run (default). Rode com --apply para adotar, "
              "ou --apply --delete para apagar na Pluggy.")
        return
    asyncio.run(_executar(items, apply=args.apply, delete=args.delete))


if __name__ == "__main__":
    main()
