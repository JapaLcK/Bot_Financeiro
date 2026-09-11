"""G5e — o que o OPERADOR LÊ do one-shot: ao ser abandonado, e no dry-run.

Arquivo próprio (CLAUDE.md §0.5), cortado por assunto de `test_adotar_items_prazo.py`
(que cobre quanto o processo espera): aqui, o texto que sai na tela e o que ele
promete. Nada aqui toca banco — a listagem é dublê; a query de verdade roda em
`test_adotar_items_of_orfaos.py`.

O texto é o ÚNICO registro do que um `--apply --delete` apagou: nada apaga a linha
do registry, então o dry-run de depois sai idêntico ao de antes. Por isso o flush
tem teste próprio, e em subprocesso.

`_roda_no_repo` vem do arquivo do prazo (fonte única do helper, §0.1).
"""

from __future__ import annotations

import asyncio
import os
import shlex
import signal
import subprocess
import sys

from test_adotar_items_prazo import _roda_no_repo


def _abandona_capturado(script, capsys, motivo="Ctrl+C", codigo=130, delete=False):
    saida: list[str] = []
    orig = script.os._exit
    script.os._exit = lambda c: saida.append(f"[exit {c}]")
    try:
        script._abandona(motivo, codigo, delete)
    finally:
        script.os._exit = orig
    return capsys.readouterr().out + "".join(saida)


def test_mensagem_e_a_mesma_em_qualquer_ponto_de_morte(monkeypatch, capsys):
    """O conserto inteiro num assert: a mensagem NÃO depende do estado do processo.

    As três versões anteriores classificavam os items em grupos deduzidos da
    lista do laço e das tasks vivas, e cada uma mentia num ponto de morte
    diferente — o pior ANTES do primeiro item, onde tudo estava vazio e o texto
    imprimia "tudo que a lista tinha foi processado" com 12 órfãos travados
    (janela real medida: 0,15–0,21s, 4 de 30 varreduras com SIGINT).

    A VERSÃO ANTERIOR DESTE PORTÃO era furável por 3 de 4 portas: chamava
    `_abandona` em 3 pontos sintéticos, NUNCA rodava `_executar`, e os três
    compartilhavam `motivo="Ctrl+C"`, `codigo=130` e `delete=False`. Agora são
    CINCO pontos, variando os três, e quatro deles chegam lá PELO `_executar`:

      1. fora de loop nenhum        (Ctrl+C, 130, delete=True)
      2. prazo estourado no laço    (prazo…, 1, delete=False)  ← via `_executar`
      3. prazo estourado em delete  (prazo…, 1, delete=True)   ← via `_executar`
      4. Ctrl+C com o item EM VOO   (Ctrl+C, 130, delete=False) ← dentro do laço
      5. o prazo DEPOIS desse Ctrl+C (prazo…, 1, delete=False)  ← lista pela metade
    e o `sys.argv` varia junto (4º eixo): sem isso, um ramo por `sys.argv` dava os
    textos iguais. MEDIDO 2026-09-10: um `if "--item" in sys.argv: print(…)`
    dentro de `_abandona` fica VERMELHO aqui.

    O que o portão prende: o texto de `_abandona` MENOS o eco é BYTE A BYTE igual
    nos cinco (stdout E stderr, ver `_espiao`); o eco traz o motivo e o modo; o
    código de saída é o que foi pedido. Mutações que passavam verdes e hoje ficam
    vermelhas: ramo por estado do processo com QUALQUER redação (o ponto 4 tem item
    em voo e lista pela metade, o 1 não tem laço nenhum), por `codigo == 1`, por
    `delete`, por `sys.argv` — e qualquer um deles impresso ANTES do eco ou em
    STDERR, os dois territórios que eram livres até esta rodada.

    ponytail: o que ele prende é o TEXTO DE `_abandona`, e só ele. Linha impressa
    por `_executar` ANTES da chamada é território livre: o `_espiao` faz
    `capsys.readouterr()` e a descarta de propósito (as linhas por item variam), e
    ali cabe a MESMA mentira — `print("0 de 3 item(s) foram processados")` uma linha
    acima do `_abandona`, nos DOIS call sites, fica verde, e é o que o operador lê,
    colado na frase que jura não classificar item nenhum. Fechar isso é medir a
    saída de outra função, outra superfície; aqui a defesa é revisão de diff. O
    outro teto: a comparação mede INDEPENDÊNCIA DO ESTADO, não veracidade —
    QUALQUER entrada CONSTANTE dentro do processo do pytest dá os textos iguais e
    passa verde, ainda que minta. As quatro formas, medidas em 2026-09-10, todas
    VERDES (19 passed):
      • frase constante e falsa, sem ramo nenhum ("nada ficou pela metade");
      • ramo por variável de AMBIENTE (`os.environ`);
      • ramo por CONSTANTE de módulo (`_ESPERA_SYNC_SEC >= 120`) — e esta não é
        rebuscada: derivar "nada ficou pela metade" do prazo configurado é algo que
        alguém escreve de boa-fé, e é falso;
      • ramo por `PYTEST_CURRENT_TEST`, que mente só FORA do pytest.
    Prender "frase falsa" exigiria o teste saber o desfecho de cada item, que é
    justamente o que `_abandona` não pode saber: teto do instrumento, não folga de
    escrita.
    """
    import scripts.adotar_items_of_orfaos as script

    saiu: list[int] = []
    monkeypatch.setattr(script.os, "_exit", saiu.append)
    monkeypatch.setattr(script, "_ESPERA_SYNC_SEC", 0.2)
    monkeypatch.setattr("core.services.pluggy.get_pluggy_item",
                        lambda item_id, api_key=None: {"status": "UPDATED"})
    monkeypatch.setattr("core.services.pluggy.delete_pluggy_item",
                        lambda item_id, api_key=None: None)
    mortes: list[tuple[str, int, bool, str]] = []
    _real_abandona = script._abandona

    def _espiao(motivo, codigo, delete):
        # O que entra na comparação é SÓ o que `_abandona` imprimiu: as linhas por
        # item que vieram antes dele são de `_executar` e variam de propósito (e
        # era por elas que "o texto inteiro" ficava vermelho à toa).
        capsys.readouterr()
        _real_abandona(motivo, codigo, delete)
        # `.out + .err`: um ramo por estado impresso em STDERR saía inteiro da
        # comparação. MEDIDO com o ramo por `asyncio.all_tasks()` em `sys.stderr`.
        _cap = capsys.readouterr()
        mortes.append((motivo, codigo, delete, _cap.out + _cap.err))

    monkeypatch.setattr(script, "_abandona", _espiao)

    async def _adota(item_id, last_event=None):
        if item_id == "i2":   # o Ctrl+C chega com o 2º de 3 items EM VOO
            script._abandona("Ctrl+C", 130, False)
        return 7

    monkeypatch.setattr("frontend.routes.open_finance._adota_item_orfao", _adota)

    async def _pelo_executar(itens, delete):
        # a task que não termina: é ela que faz o prazo estourar lá dentro
        asyncio.create_task(asyncio.sleep(30), name="pluggy_sync_pendurado")
        await script._executar(itens, apply=True, delete=delete)

    # 4º EIXO: o `sys.argv` também varia. Sem isto, os quatro pontos rodavam com o
    # argv do pytest e um ramo por `sys.argv` dentro de `_abandona` dava as quatro
    # caudas iguais — a 4ª porta pela qual o portão ainda era furável.
    monkeypatch.setattr(sys, "argv", ["adotar", "--apply", "--delete"])
    script._abandona("Ctrl+C", 130, True)
    for delete in (False, True):
        monkeypatch.setattr(sys, "argv", ["adotar", "--item", f"i-{delete}", "--apply"])
        asyncio.run(_pelo_executar(["i1"], delete))
    monkeypatch.setattr(sys, "argv", ["adotar"])
    asyncio.run(_pelo_executar(["i1", "i2", "i3"], False))

    def _eco(motivo, delete):
        modo = "--apply --delete" if delete else "--apply"
        return f"{motivo}. O processo estava em `{modo}` e PAROU AQUI."

    assert [m[:3] for m in mortes] == [
        ("Ctrl+C", 130, True),
        ("prazo de 0.2s estourado", 1, False),
        ("prazo de 0.2s estourado", 1, True),
        ("Ctrl+C", 130, False),
        # o 5º é o prazo da 3ª rodada, que estoura depois do Ctrl+C do ponto 4
        ("prazo de 0.2s estourado", 1, False)], [m[:3] for m in mortes]
    for motivo, _cod, delete, texto in mortes:
        assert _eco(motivo, delete) in texto, texto
    assert saiu == [130, 1, 1, 130, 1], saiu

    # O TEXTO INTEIRO menos o eco — e NÃO `split("PAROU AQUI.")[-1]`, que deixava
    # livre tudo o que fosse impresso ANTES do eco. Não era folga inócua: é a porta
    # por onde passa exatamente a classificação que este portão existe para prender.
    # MEDIDO 2026-09-10 com um ramo por `asyncio.all_tasks()` (a mentira das três
    # versões anteriores): ANTES do eco → 19 passed; o MESMO código 6 linhas
    # ABAIXO → 1 failed. O eco sai da comparação porque ele TEM de variar (motivo e
    # modo), e ele é afirmado à parte, no laço acima.
    # `removeprefix` e não `replace`: o eco é a PRIMEIRA linha, e o `replace` apagava
    # TODAS as ocorrências — mentira que repetisse o eco inline saía junto com ele.
    restos = {texto.removeprefix("\n" + _eco(motivo, delete))
              for motivo, _cod, delete, texto in mortes}
    assert len(restos) == 1, [texto for *_, texto in mortes]

    cauda = restos.pop()
    assert "não afirma o estado de item nenhum" in cauda, cauda
    assert ".venv/bin/python -m scripts.adotar_items_of_orfaos" in cauda, cauda
    assert "não continua" in cauda.lower(), cauda
    # a via que EXISTE para a adoção retroativa (o `item/updated` que o texto
    # prometia não vem: é o mesmo motivo de o gate `fresco` existir)
    assert "/refresh" in cauda and "PUXA A TELA" in cauda, cauda
    assert "item/updated" not in cauda, cauda


def test_em_delete_a_mensagem_nao_manda_rodar_apply(capsys):
    """`--apply --delete` NÃO é `--apply`, e o texto mandava rodar o outro.

    Em modo delete nada apaga a linha do registry (docstring do módulo), então o
    item apagado continua "ainda na lista" — o bullet casava SEMPRE e o conselho
    era `--apply`, que ADOTA o que o operador acabou de mandar apagar. Agora o
    único `--apply` do texto é o eco do modo em que o processo estava.
    """
    import scripts.adotar_items_of_orfaos as script

    delete = _abandona_capturado(script, capsys, delete=True)
    assert "`--apply --delete`" in delete, delete
    assert delete.count("--apply") == 1, f"sugeriu --apply num processo que APAGAVA:\n{delete}"
    # a mesma verdade, na redação de hoje: apagar na Pluggy não encolhe a lista
    assert "a linha do registry fica" in delete and "idêntica à de antes" in delete, delete

    # Par positivo: o modo adoção ecoa `--apply` e também não ganha conselho novo.
    adota = _abandona_capturado(script, capsys, delete=False)
    assert "`--apply`" in adota and adota.count("--apply") == 1, adota


def test_dry_run_mostra_o_item_que_a_lista_de_orfaos_descarta(monkeypatch, capsys):
    """A ferramenta que `_abandona` prescreve tem de ENXERGAR o que ela prescreve.

    Ponto de partida MEDIDO, com o registry em `r4-i2 → webhook_adopt` (rastro COM
    dono, SEM conexão) e `r4-i3` órfão: o dry-run imprimia `1 item(s) órfão(s):
    r4-i3` e NADA sobre o r4-i2 — o operador lia "adotado, tudo certo" para o
    usuário que está no único estado sem saída pela tela. Pior no 2º caso: com só
    o r4-i2 no banco, a saída era "Nenhum item órfão no registry.".

    O bloco NÃO promete separar (a) adoção pela metade de (b) banco removido — os
    dados de hoje não separam (`alvos`, ponytail). Reportar ambíguo
    é honesto; esconder não é, e era o que acontecia.

    MUTAÇÃO que fica vermelha AQUI: mover o bloco para DEPOIS do `if not items: …
    return` → o 2º caso cai (é exatamente onde ele estava escondido).
    A que NÃO fica, e a prosa anterior afirmava que sim: tirar o grupo com dono de
    `listar_items_sem_conexao` — este teste DUBLA essa função, então ele passa
    verde. Medido 2026-09-10 (`return {i: set() for i in vistos}`): caem os dois que
    leem o banco de verdade —
    `test_adotar_items_of_orfaos.py::…_nao_ressuscita_o_banco_que_o_usuario_removeu`
    e `test_adotar_items_alvos.py::…lista_so_o_item_sem_conexao…`.
    O `signal.signal` também é dublê: trocar o SIGPIPE do processo do pytest é
    efeito colateral, e quem prova a linha de verdade é o teste do pipe abaixo.
    """
    import scripts.adotar_items_of_orfaos as script

    monkeypatch.setattr(sys, "argv", ["adotar_items_of_orfaos"])
    monkeypatch.setattr(script.signal, "signal", lambda *_: None)
    monkeypatch.setattr(script, "listar_items_sem_conexao",
                        lambda: {"r4-i2": {"webhook_adopt"}, "r4-i3": set()})
    script.main()
    com_orfao = capsys.readouterr().out

    assert "1 item(s) órfão(s): r4-i3" in com_orfao, com_orfao
    assert "r4-i2 (webhook_adopt)" in com_orfao, com_orfao
    assert "--item <ID> --apply --delete" in com_orfao, com_orfao
    assert "reconexão" in com_orfao, com_orfao

    # o caso que o early return escondia inteiro: nenhum órfão, e um travado
    monkeypatch.setattr(script, "listar_items_sem_conexao",
                        lambda: {"r4-i2": {"webhook_adopt"}})
    script.main()
    so_meio = capsys.readouterr().out
    assert "Nenhum item órfão no registry." in so_meio, so_meio
    assert so_meio.index("r4-i2") < so_meio.index("Nenhum item órfão"), so_meio


def test_pipe_fechado_nao_troca_a_saida_por_traceback():
    """`… | head -1` REABRIA o travamento que este PR fecha.

    MEDIDO com PYTHONUNBUFFERED=1: o `print` levanta `BrokenPipeError`, o `except`
    do laço tenta imprimir o erro e quebra de novo, e a exceção escapa do
    `asyncio.run` — a espera e o `_abandona` nunca rodam, e o encerramento junta a
    thread do sync. Ctrl+C não salva: o `sys.stdout.flush()` de `_abandona` levanta
    antes do `os._exit`.

    Shell e subprocesso porque o defeito É o pipe, e não há pipe fechado dentro do
    pytest. Só a listagem é dublê: o que roda é o `main`, com o
    `signal.signal(SIGPIPE, SIG_DFL)` dentro dele.

    Os 20 mil items não são exagero, são a CONDIÇÃO: a escrita tem de continuar
    DEPOIS de o `head` sair. No terminal isso vem do tempo (um print por item, com
    rede no meio); aqui vem do volume, porque uma saída que CABE no buffer do pipe
    (medido: 46 KB passam inteiros no macOS, e a versão anterior deste teste ficava
    verde com e sem o conserto por causa disso) nunca chega a escrever em pipe
    fechado. 20 mil ids ≈ 280 KB, e aí a escrita bloqueia e leva EPIPE — sem
    depender de relógio.

    MUTAÇÃO: apagar essa linha de `main` → `BrokenPipeError` volta ao stderr.
    """
    codigo = ("import sys\n"
              "import scripts.adotar_items_of_orfaos as s\n"
              "sys.argv = ['adotar_items_of_orfaos']\n"
              "s.listar_items_sem_conexao = lambda: {'r4-i2': {'webhook_adopt'},\n"
              "    **{'orfao-%05d' % i: set() for i in range(20000)}}\n"
              "s.main()\n")
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    p = subprocess.run(
        f"{shlex.quote(sys.executable)} -c {shlex.quote(codigo)} | head -1",
        shell=True, cwd=raiz, capture_output=True, text=True, timeout=180,
        env={**os.environ, "PYTHONPATH": raiz, "PYTHONUNBUFFERED": "1"})
    assert "BrokenPipeError" not in p.stderr, p.stderr
    # controle positivo: a 1ª linha SAIU pelo pipe. Sem ele o teste passaria num
    # processo que morresse antes de imprimir qualquer coisa.
    assert "1 item(s) COM rastro de dono" in p.stdout, (p.stdout[:200], p.stderr)


def test_o_que_o_abandona_imprimiu_chega_ao_terminal_apesar_do_os_exit():
    """O `os._exit` não passa pelo flush do stdout — e fora do terminal o Python é
    block-buffered. Sem o `sys.stdout.flush()` explícito, some a mensagem INTEIRA e
    somem junto as linhas por item impressas antes dela, que são o único registro do
    que o `--delete` apagou (nada apaga a linha do registry: o dry-run de depois sai
    idêntico ao de antes).

    MEDIDO 2026-09-10 com stdout em pipe, Python 3.13.2, rodando exatamente o
    código deste teste: com o flush → rc 130 e 1427 bytes; sem ele → rc 130 e ZERO
    byte (nem a linha do item, nem a mensagem).

    Subprocesso porque o defeito é do FILE DESCRIPTOR: `capsys` lê o objeto Python
    e fica verde com e sem o flush — foi assim que essa mutação passou despercebida.
    `PYTHONUNBUFFERED=None` APAGA a variável: herdada do pytest, ela deixaria este
    teste verde com o conserto apagado (tautologia).

    MUTAÇÃO: apagar o `sys.stdout.flush()` de `_abandona` → os três asserts caem.
    """
    p = _roda_no_repo("import scripts.adotar_items_of_orfaos as s\n"
                      "print('r5-apagado: apagado na Pluggy')\n"
                      "s._abandona('Ctrl+C', 130, True)\n",
                      PYTHONUNBUFFERED=None)
    assert p.returncode == 130, (p.returncode, p.stdout, p.stderr)
    assert "r5-apagado: apagado na Pluggy" in p.stdout, (p.stdout, p.stderr)
    assert "PAROU AQUI" in p.stdout, (p.stdout, p.stderr)


def test_o_ctrl_c_do_processo_ecoa_o_modo_em_que_ele_estava(monkeypatch):
    """O call site REAL do SIGINT — o handler que `main` instala — não era exercitado
    por teste nenhum: trocar `args.delete` por `False` ali passava verde, e um
    `--apply --delete` interrompido voltava a sugerir o comando que ADOTA o item que
    o operador acabou de mandar apagar (o bug que `test_em_delete_…` fechou no texto,
    reaberto pelo argumento).

    Os dois modos rodam AQUI, com argv diferente, porque um único modo passaria
    verde com o `False` fixo.
    """
    import scripts.adotar_items_of_orfaos as script

    instalados: list[tuple] = []
    monkeypatch.setattr(script.signal, "signal", lambda sig, h: instalados.append((sig, h)))
    monkeypatch.setattr(script.db, "get_connections_by_item_id", lambda i: [])

    async def _nada(*a, **k):
        return None

    monkeypatch.setattr(script, "_executar", _nada)
    mortes: list[tuple] = []
    monkeypatch.setattr(script, "_abandona", lambda *a: mortes.append(a))

    for argv in (["adotar", "--item", "r6-del", "--apply", "--delete"],
                 ["adotar", "--item", "r6-ado", "--apply"]):
        monkeypatch.setattr(sys, "argv", argv)
        instalados.clear()
        script.main()
        # O `asyncio.run` instala o SIGINT DELE (`Runner.run`, o que levanta
        # KeyboardInterrupt) depois do do script — pegar o último SIGINT da lista
        # chamaria o do asyncio e derrubaria a sessão do pytest.
        do_script = [h for sig, h in instalados
                     if sig == signal.SIGINT and getattr(h, "__name__", "") == "_ao_sinal"]
        assert len(do_script) == 1, instalados
        do_script[0](signal.SIGINT, None)   # o Ctrl+C, pelo handler que o `main` instalou
        # e o SEGUNDO Ctrl+C não reentra no `print` do `_abandona`: já estamos saindo
        assert instalados[-1] == (signal.SIGINT, signal.SIG_IGN), instalados

    assert mortes == [("Ctrl+C", 130, True), ("Ctrl+C", 130, False)], mortes
