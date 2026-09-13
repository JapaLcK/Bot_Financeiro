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

Quem decide os ALVOS (a lista de órfãos, ou o `--item`) é `scripts/adotar_items_lista.py`.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys

import db
from core.services.pluggy_sync import _env_int
# `recusa_id` é a régua ÚNICA do que pode virar `/items/{id}` (por que ela existe
# e o que ela recusa: docstring dela). As três portas que a usam estão aqui:
# `parse_args`, o `main` (onde a lista NASCE) e o laço de `_executar`.
from scripts.adotar_items_lista import (_dica_de_recusa, alvos,
                                        listar_items_sem_conexao, recusa_id)

# Teto da espera pelo sync que a adoção agenda — e da SAÍDA do processo (ver
# `_abandona`). 120s = 6× o `PLUGGY_TIMEOUT` (20s, `core/services/pluggy.py:38`)
# de UMA requisição, e os syncs dos N items rodam em paralelo (uma task cada),
# então o prazo é o do item mais lento, não a soma.
#
# NÃO é o teto do sync: esse é `PLUGGY_TIMEOUT` × até 60 páginas × contas × até 3
# tentativas (`_SYNC_MAX_ATTEMPTS`) = dezenas de minutos num dia ruim. Prazo que
# cobrisse o pior caso não é prazo, é a ausência dele — quem está esperando aqui
# é um operador num terminal, e para ele o que importa é voltar ao prompt — o que
# ficou pendente ele lê no dry-run, não nesta espera. Quem quiser esperar mais
# sobe a env var (o próprio `_abandona` diz como).
#
# `float(os.getenv(...))` cru era a ÚNICA env var do repositório que derrubava o
# processo NO IMPORT: medido, `ADOCAO_SYNC_ESPERA_SEC` valendo `""`, `"abc"` ou
# `"  "` levantava `ValueError` antes do `main` e matava junto o `--help` e o
# dry-run — caminhos que nem chegam perto da espera.
#
# `_env_int` é REUSADO de `pluggy_sync` em vez de virar a 4ª cópia do helper
# (§0.1 — as outras são `scripts/account_deletion_job._env_int` e
# `core/services/security_alerts._int_env`). Preço MEDIDO do import, 2026-09-10
# (remeça, §2): +123 módulos / +0,067s sobre os 532 / 0,205s do `import db`.
#
# `int` e não `float`: `float("1e400")` virava `inf`, que é a ausência de prazo.
# O `int` NÃO o recusa pelo caminho do lixo, como esta linha já afirmou: `1e400`
# é `ValueError` e cai no default, mas `1` seguido de 400 zeros é int válido, e
# `asyncio.wait` estoura `OverflowError` nele — o operador recebia traceback em
# vez do resumo. O teto de 24h NÃO deriva daí (o limite técnico fica perto de 10**400):
# é teto de PRODUTO — quem espera é um operador num terminal, e prazo que não cabe num
# dia é a ausência de prazo com outra roupa. Segundo fracionário deixa de valer na env
# var (os testes patcham a CONSTANTE, não a variável). `max(0, ...)`: negativo já se
# comporta como 0 no `asyncio.wait`, e o clamp só evita "prazo de -5s estourado"; 0 é
# HONRADO, não corrigido — é "adota e me devolve o prompt".
_ESPERA_SYNC_SEC = min(24 * 60 * 60, max(0, _env_int("ADOCAO_SYNC_ESPERA_SEC", 120)))


def _abandona(motivo: str, codigo: int, delete: bool) -> None:
    """Volta ao prompt sem esperar o sync — `os._exit` de propósito, não por pressa.

    Prazo na ESPERA não basta: o sync roda em `asyncio.to_thread`, e uma saída
    normal (ou um Ctrl+C) passa pelo encerramento do `asyncio.run`, que JUNTA a
    thread antes de devolver o terminal. Medido em 3.13.2, com este script e um
    sync de 12s: prazo de 2s na espera → o processo só volta ao prompt em 12,3s;
    Ctrl+C aos 3s → honrado aos 12,3s. O prazo tem de ser da espera E da saída —
    daí o `os._exit`, que é o único que não junta thread.

    ponytail: `os._exit` também pula o fechamento do pool de conexões. Num
    one-shot isso é o mesmo que o processo morrer, e o Postgres derruba a sessão
    com o socket; o que NÃO se pode pular é o flush do que foi impresso, que é
    explícito aqui porque `os._exit` também o pularia.

    A mensagem NÃO classifica item nenhum, e isso é o conserto. As três versões
    anteriores deduziam o desfecho de cada item a partir do estado do PROCESSO
    (a lista do laço, as tasks de sync vivas), e cada uma mentia num ponto de
    morte diferente: o sinal chega ANTES de a lista existir (janela medida de
    0,15–0,21s entre o `signal.signal` e o preenchimento dela, reproduzida em 4
    de 30 varreduras — e o texto dizia "tudo foi processado" com 12 items
    travados); o item que estourou sumia do resumo; o item em voo saía em dois
    grupos; e em `--delete` o conselho era `--apply`, que ADOTA o que o operador
    mandou apagar. Não havia versão certa dessa dedução — o processo pode morrer
    em qualquer ponto. Quem sabe o estado é o banco — e o dry-run, que o lê sem
    escrever, PASSOU A MOSTRAR os dois grupos (`main`): antes disso, mandar rodá-lo
    era prescrever uma ferramenta cega justo para o item da adoção pela metade.

    Sobra o que é fato observável DE DENTRO do processo, verdadeiro em qualquer
    ponto de morte — inclusive antes do primeiro item: o que ele imprimiu (já
    saiu, linha a linha), em que modo estava, e que o sync morre junto.
    """
    modo = "--apply --delete" if delete else "--apply"
    print(f"\n{motivo}. O processo estava em `{modo}` e PAROU AQUI.")
    # Sem enumerar os desfechos possíveis: em `--delete` eles nem são os mesmos
    # ("apagado na Pluggy", não "adotado"), e a linha impressa já diz qual foi.
    print("Esta mensagem não afirma o estado de item nenhum — daqui de dentro "
          "não dá para saber. O que já saiu são as linhas acima, uma por item "
          "que chegou a ter desfecho. O item que estava em voo na hora da "
          "parada pode ter escrito sem imprimir nada.")
    print("Quem sabe o estado de cada um é o banco. O dry-run o lê e NÃO escreve nada:\n"
          "    .venv/bin/python -m scripts.adotar_items_of_orfaos\n"
          "  Ele imprime os DOIS grupos: sem dono nenhum (não foi adotado) e COM dono e sem\n"
          "  conexão (adoção pela metade OU banco que o usuário removeu — ele não separa os\n"
          "  dois, e diz isso). O que ele NÃO vê é o `--delete`: apagar na Pluggy não muda nada\n"
          "  LOCAL (a linha do registry fica), então a lista sai idêntica à de antes — do que\n"
          "  foi apagado, o único registro é o que já está impresso acima.")
    print("O sync roda DENTRO deste processo: ele morre aqui e NÃO continua do lado do\n"
          "servidor. E item adotado por ESTE script não tem evento nenhum a caminho: adoção\n"
          "retroativa não tem `item/created` atrás dela, que é o motivo de o script existir.\n"
          "Quem importa o extrato depois é o /refresh do app, quando o usuário PUXA A TELA —\n"
          "e o card dele diz Atualizando…, então ele não tem por que puxar. Até lá a carteira\n"
          "segue sem conta e sem transação. Para esperar mais na próxima rodada:\n"
          "ADOCAO_SYNC_ESPERA_SEC=<segundos inteiros>.")
    sys.stdout.flush()
    os._exit(codigo)


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
    # Fronteira de confiança em caminho IRREVERSÍVEL — o §0.2 não simplifica isto.
    # O `--item "$ITEM_ID"` que o dry-run manda copiar vira string VAZIA quando a
    # variável não está setada, e vazio não é "não pediram item" (ver `alvos`).
    # A régua é a MESMA do laço e a mesma da URL da Pluggy (§0.7): o que muda aqui
    # é só o desfecho — rc=2 antes de ler o banco, porque aqui houve DIGITAÇÃO.
    if args.item is not None and (motivo := recusa_id(args.item)):
        # Id VAZIO é sintoma de shell, não de registry: `--item "$VAR"` com a
        # variável não setada. A outra dica não serve, e esta só cabe aqui.
        dica = ('  Id vazio costuma ser `--item "$VAR"` com a variável não setada — '
                "e isso, com --delete, miraria a lista INTEIRA."
                if not args.item.strip() else _dica_de_recusa())
        ap.error(f"--item {args.item!r} {motivo}.{dica}")
    return args


async def _executar(items: list[str], *, apply: bool, delete: bool) -> None:
    # Import tardio só para manter o app FastAPI fora de quem não o usa (`--help`,
    # `parse_args`, dry-run): ele custa ~3× os módulos de `import db` (medido em
    # 2026-09-08 com `python -c "import sys; import M; print(len(sys.modules))"`;
    # remede antes de reusar o número, CLAUDE.md §2).
    # NÃO é por variável de ambiente: a versão anterior deste comentário dizia
    # que o import "exige DATABASE_URL/JWT_SECRET" e que "o dry-run tem de rodar
    # sem isso", e as duas metades são falsas — o import roda com as duas APAGADAS do
    # ambiente, e o dry-run lê o banco (`listar_items_sem_conexao` abre a conexão).
    from core.services.pluggy import delete_pluggy_item, get_pluggy_item
    from frontend.routes.open_finance import _adota_item_orfao

    for item_id in items:
        # ÚLTIMO portão antes da chamada irreversível, e o único por onde os DOIS
        # modos passam: todo id que chega ao `delete_pluggy_item`/`_adota_item_orfao`
        # passou pela MESMA régua. Hoje ele não é alcançável em produção — o `--item`
        # morre no `parse_args`, e a lista é filtrada onde NASCE (`main`) —, e é por
        # isso que fica: é a fronteira de saída, e as duas portas de cima podem mudar.
        # Medição e a classe inteira: docstring de `recusa_id`.
        # PULA em vez de abortar, e a diferença para o `parse_args` é a origem: lá
        # houve digitação e o processo inteiro é suspeito; aqui é UMA linha
        # envenenada do registry, e um one-shot que existe para destravar N
        # usuários não pode parar por causa dela. Silencioso é que não pode ser —
        # em `--delete`, o que está impresso é o único registro da rodada.
        if motivo := recusa_id(item_id):
            print(f"{item_id!r}: PULADO, não vai para a Pluggy — {motivo}")
            continue
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
    #
    # COM PRAZO, e o prazo NÃO é conforto: esperar sem teto converteu um script
    # que voltava ao prompt em 0,0s num script que dura o que o sync durar — sem
    # teto e sem Ctrl+C (medido: SIGINT aos 3s de um sync de 12s honrado só aos
    # 12,3s; num sync de 45s, o SIGINT aos 6s ainda não tinha matado nada aos 25s,
    # e só o SIGKILL encerrou). É a segunda metade da queixa do dono ("como que eu
    # paro"), e ela não pode ser o preço da primeira.
    pendentes = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pendentes:
        await asyncio.wait(pendentes, timeout=_ESPERA_SYNC_SEC)
        if any(not t.done() for t in pendentes):
            _abandona(f"prazo de {_ESPERA_SYNC_SEC:g}s estourado", 1, delete)


def main() -> None:
    # `| head -1` fecha o pipe no meio. O Python IGNORA o SIGPIPE e o entrega como
    # `BrokenPipeError` no `print` — MEDIDO com PYTHONUNBUFFERED=1: o `except` do laço
    # tenta imprimir o erro e quebra de novo, a exceção escapa do `asyncio.run`, a espera
    # e o `_abandona` nunca rodam, e o encerramento junta a thread do sync — o travamento
    # que este PR fecha, reaberto por um pipe. SIG_DFL devolve o Unix de sempre: morre no
    # ato, como o `os._exit` de `_abandona`. Antes do `parse_args`, para valer no `--help`.
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    args = parse_args()
    # `is not None` em TODAS as três: por verdade, `--item ""` virava modo lista em
    # silêncio e pulava junto a guarda de conexão viva abaixo (o porquê e a medição
    # estão em `alvos`, que é quem decide).
    sem_conexao = {} if args.item is not None else listar_items_sem_conexao()
    # A régua no PONTO ÚNICO ONDE A LISTA NASCE — antes de virar os dois grupos, logo
    # antes de qualquer CONTAGEM. Ela morava num `if` por GRUPO (só no com-dono), e o
    # dos órfãos era contado, impresso e prescrito sem passar por ela: `6 item(s)
    # órfão(s)` num `--apply --delete` que age em 1, e é esse número que o operador lê
    # para decidir rodar o irreversível. Os dois grupos são partições deste mesmo dict,
    # então aqui a régua vale para todo id que o script conta, imprime ou prescreve.
    # Chega sujo porque a ESCRITA do registry não tem régua nenhuma
    # (`frontend/routes/open_finance.py:1811`: id direto do corpo do webhook, dono NULL).
    if recusados := [i for i in sem_conexao if recusa_id(i)]:
        print(f"{len(recusados)} id(s) do registry FORA das duas contas abaixo — nenhum "
              "comando deste script os aceita: " + ", ".join(map(repr, recusados))
              + _dica_de_recusa())
        sem_conexao = {i: o for i, o in sem_conexao.items() if i not in recusados}
    items = alvos(args.item, sem_conexao)

    # `--item` pula a LISTA, logo pula o predicado dela: um id digitado errado
    # mandava para a Pluggy o item de um usuário SAUDÁVEL. Com `--delete` isso é
    # IRREVERSÍVEL — o item morre lá, a linha local continua apontando para ele,
    # e o usuário não recebe aviso nenhum na tela. Recusa aqui, antes de qualquer
    # escrita (operação irreversível em caminho de dinheiro é carve-out do §0.2).
    if args.item is not None and db.get_connections_by_item_id(args.item):
        raise SystemExit(
            f"{args.item} tem conexão LOCAL viva: não é órfão, e este script não "
            "é a porta para removê-lo. Confira o id; se o usuário quer mesmo "
            "removê-lo, o caminho é o app (DELETE /open-finance/{uid}), que apaga "
            "os dois lados.")
    # ANTES do early return: com a lista de órfãos vazia, "Nenhum item órfão" era a
    # resposta confiante e errada para o usuário TRAVADO pela adoção que morreu no meio
    # — o único estado sem saída pela tela, e justo o que sumia da única ferramenta que
    # a mensagem de `_abandona` manda rodar.
    if meio := {i: o for i, o in sem_conexao.items() if o}:
        print(f"{len(meio)} item(s) COM rastro de dono e SEM conexão local: "
              + ", ".join(f"{i} ({'/'.join(sorted(o))})" for i, o in meio.items()))
        print("  São DUAS coisas possíveis, e os dados de hoje NÃO as separam (é o teto que\n"
              "  `alvos` declara; a origem acima é pista, não veredito):\n"
              "   (a) adoção que morreu entre o registro e a escrita da conexão: o usuário\n"
              "       está TRAVADO, sem saída pela tela. A saída é operacional, um id por vez:\n"
              "         .venv/bin/python -m scripts.adotar_items_of_orfaos --item <ID> --apply --delete\n"
              "       e então peça a reconexão ao usuário pelo app;\n"
              "   (b) banco que o próprio usuário REMOVEU: estado esperado, nada a fazer — e o\n"
              "       comando de (a) apagaria na Pluggy um item que ninguém mais usa, o que é\n"
              "       irreversível e não devolve nada a ele. Na dúvida, pergunte a ele antes.")
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
    # Ctrl+C: o handler padrão levanta KeyboardInterrupt, que ainda passa pelo
    # encerramento do `asyncio.run` e fica preso na thread do sync (ver
    # `_abandona`). Instalado SÓ aqui, que é o único ponto que pode pendurar.
    def _ao_sinal(*_):
        # SIG_IGN antes de imprimir: um segundo Ctrl+C DENTRO do `_abandona`
        # reentrava no `print` e levantava `RuntimeError: reentrant call inside
        # BufferedWriter` — a exceção escapava do handler, o encerramento juntava a
        # thread do sync (o travamento que este PR fecha, reaberto) e o `rc` virava
        # 1, o código do PRAZO. Reproduzido só com ~20 µs entre os sinais (0 de 13
        # com 2 ms e com 300 ms): praticamente inalcançável por dedo humano, e a
        # linha é uma. Já estamos saindo — mais sinal não tem o que fazer.
        # ponytail: cobre o Ctrl+C, não o `_abandona` do PRAZO — um Ctrl+C no meio
        # do print DELE ainda reentra (janela de ~1 ms dentro de um prazo de 120s).
        # Subir o SIG_IGN para dentro de `_abandona` fecharia os dois e vazaria
        # SIG_IGN para o processo do pytest, que é o vazamento do SIGPIPE de novo.
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        _abandona("Ctrl+C", 130, args.delete)

    signal.signal(signal.SIGINT, _ao_sinal)
    asyncio.run(_executar(items, apply=args.apply, delete=args.delete))


if __name__ == "__main__":
    main()
