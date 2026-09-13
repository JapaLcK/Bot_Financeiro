"""G5e — a ESPERA pelo sync do one-shot, e o prazo que a lidera (`ADOCAO_SYNC_ESPERA_SEC`).

Arquivo próprio (CLAUDE.md §0.5), cortado por assunto: aqui o PRAZO (quanto o
processo espera, e o que a env var pode valer); em `test_adotar_items_saida.py`, o
que o operador LÊ quando é abandonado e no dry-run. Nada nos dois toca banco (a
listagem é dublê) — `test_adotar_items_of_orfaos.py` cobre a ADOÇÃO com Postgres, e
`test_adotar_items_alvos.py` cobre em quem o script mexe.

CONTROLE POSITIVO do teto, que faltava ao grupo:
`test_sync_curto_dentro_do_prazo_e_aguardado_inteiro`. A mutação que expôs o buraco
foi apagar SÓ a linha `await asyncio.wait(...)` de `_executar`, mantendo o
`if any(not t.done())` → 17 passed, verde. A suíte não distinguia "espera até 120s"
de "espera 0s e abandona todo sync": o `webhook_pluggy` troca `_schedule_pluggy_sync`
por um `lambda` que só faz `append` (task nenhuma), e no único teste que criava task
de verdade o sync era de 30s contra prazo de 0,2s — SEMPRE estourava, com e sem a
espera.

As duas mutações da espera, MEDIDAS em 2026-09-10 nos quatro arquivos do script
(remeça antes de reusar, §2) — a versão anterior deste texto dizia que "nenhuma das
duas é pega pelos dois arquivos ao mesmo tempo", e isso era falso:
  • apagar `await asyncio.wait(...)` → 2 failed, e os DOIS são daqui
    (`…sync_curto…` e `…prazo_gigante…`); `test_adotar_items_saida.py` segue verde;
  • `timeout=_ESPERA_SYNC_SEC` → `timeout=None` → 2 failed, um de cada arquivo
    (`…nao_fica_preso_no_sync…` daqui, `…mensagem_e_a_mesma…` de lá), em 102s
    porque as duas esperas penduram nos 30s da task.
Os dois arquivos se somam, então; o que NENHUM dos dois pega é frase constante e
falsa na mensagem (o ponytail em `…mensagem_e_a_mesma…`).
"""

from __future__ import annotations

import asyncio
import importlib
import os
import subprocess
import sys
import time


def test_sync_curto_dentro_do_prazo_e_aguardado_inteiro(monkeypatch):
    """O caminho LEGÍTIMO: task curta, prazo folgado, ninguém é abandonado.

    Sem este caso o grupo passava num `_executar` que abandonasse TODO sync — que
    é pior que o bug, porque o extrato deixa de entrar em toda rodada.
    """
    import scripts.adotar_items_of_orfaos as script

    concluidos: list[str] = []

    async def _sync(item_id):
        await asyncio.sleep(0.3)
        concluidos.append(item_id)

    async def _adota(item_id, last_event=None):
        asyncio.create_task(_sync(item_id), name=f"pluggy_sync_{item_id}")
        return 7

    monkeypatch.setattr("frontend.routes.open_finance._adota_item_orfao", _adota)
    monkeypatch.setattr(script, "_ESPERA_SYNC_SEC", 10)
    saidas: list[tuple[str, int]] = []
    monkeypatch.setattr(script, "_abandona",
                        lambda motivo, cod, delete: saidas.append((motivo, cod)))

    t0 = time.perf_counter()
    # `wait_for` para a mutação "espera sem teto" virar falha com nome, não suíte
    # pendurada — 9s é menos que o prazo de 10s acima, então ele nunca é o normal.
    asyncio.run(asyncio.wait_for(
        script._executar(["s-curto"], apply=True, delete=False), timeout=9))
    gasto = time.perf_counter() - t0

    assert saidas == [], f"abandonou um sync que cabia no prazo: {saidas}"
    assert concluidos == ["s-curto"], "o sync foi cortado pela metade"
    # O teto separa "esperou o sync" de "dormiu o prazo inteiro"; o piso separa
    # "esperou" de "não esperou nada e o sync terminou por acaso depois".
    assert 0.3 <= gasto < 5, gasto


def test_script_nao_fica_preso_no_sync_e_diz_o_que_ficou_pendente(monkeypatch, capsys):
    """A SEGUNDA METADE da queixa do dono ("e como que eu paro isso?").

    Este PR faz mais adoções AGENDAREM sync, e a espera daqui não tinha prazo:
    um script que voltava ao prompt em 0,0s passou a durar o que o sync durar —
    sem teto (`PLUGGY_TIMEOUT` × páginas × contas × tentativas = dezenas de
    minutos) e sem Ctrl+C.

    O que ESTE teste mede: a espera respeita `_ESPERA_SYNC_SEC` e chama
    `_abandona` com o motivo e o código de saída. Com a mutação (voltar ao
    `gather` sem timeout) ele fica VERMELHO pelo `wait_for` do próprio teste —
    que existe para a mutação virar falha com nome em vez de pendurar a suíte.

    O que ele NÃO mede, e foi medido FORA da suíte (o relato tem os números):
    a task de sync aqui é `asyncio.sleep`, cancelável; a de produção é
    `asyncio.to_thread`, e é ela que prende o `asyncio.run` no encerramento. Só
    um processo de verdade com SIGINT prova o `os._exit` — por isso `_abandona`
    é dublê aqui: chamá-lo de verdade mataria o pytest.
    """
    import scripts.adotar_items_of_orfaos as script

    async def _adota_lento(item_id, last_event=None):
        asyncio.create_task(asyncio.sleep(30), name=f"pluggy_sync_{item_id}")
        return 7

    monkeypatch.setattr("frontend.routes.open_finance._adota_item_orfao", _adota_lento)
    monkeypatch.setattr(script, "_ESPERA_SYNC_SEC", 0.2)
    saidas: list[tuple[str, int]] = []
    abandona = script._abandona   # o de verdade, para o 2º bloco
    monkeypatch.setattr(script, "_abandona",
                        lambda motivo, cod, delete: saidas.append((motivo, cod)))

    asyncio.run(asyncio.wait_for(
        script._executar(["s-lento"], apply=True, delete=False), timeout=10))

    assert saidas == [("prazo de 0.2s estourado", 1)], saidas

    # E o que o operador lê ao ser abandonado: a verdade sobre o sync (ele NÃO
    # continua no servidor). Chamado FORA do loop de propósito — é o Ctrl+C que
    # chega antes do `asyncio.run`; o que a mensagem diz em cada ponto de morte é
    # do arquivo irmão (`test_adotar_items_saida.py`).
    monkeypatch.setattr(script.os, "_exit", lambda c: print(f"[exit {c}]"))
    abandona("Ctrl+C", 130, False)
    texto = capsys.readouterr().out
    assert "não continua" in texto.lower() and "[exit 130]" in texto, texto


def test_prazo_gigante_nao_troca_o_resumo_por_traceback(monkeypatch):
    """`ADOCAO_SYNC_ESPERA_SEC=1` + 400 zeros passa pelo `_env_int` (é int válido,
    ao contrário de `1e400`) e estourava `OverflowError` DENTRO do `asyncio.wait`:
    o operador recebia traceback em vez do resumo, e o comentário do módulo
    afirmava que "o `int` o recusa pelo mesmo caminho do lixo".

    `importlib.reload` e não `monkeypatch.setattr(script, "_ESPERA_SYNC_SEC", …)`:
    o conserto É a expressão de módulo, e forçar a constante pela porta dos fundos
    passaria verde com e sem ele.
    """
    import scripts.adotar_items_of_orfaos as script

    async def _adota(item_id, last_event=None):
        asyncio.create_task(asyncio.sleep(0), name=f"pluggy_sync_{item_id}")
        return 7

    monkeypatch.setenv("ADOCAO_SYNC_ESPERA_SEC", "1" + "0" * 400)
    # Este era o ÚNICO teste que chamava `_executar` sem dublar nem `_abandona` nem
    # `os._exit`, e sobrevivia só porque o `asyncio.wait` devolve a tempo. Com a
    # mutação que o cabeçalho deste arquivo prescreve (apagar o `await
    # asyncio.wait(...)`), o `_abandona` rodava de verdade e o `os._exit` MATAVA o
    # pytest: rc=1, zero byte, sem summary — e tudo o que viria depois sumia sem
    # relatório. Dublado, a mutação vira falha com nome no assert abaixo.
    saiu: list[int] = []
    monkeypatch.setattr(script.os, "_exit", saiu.append)
    try:
        script = importlib.reload(script)
        assert script._ESPERA_SYNC_SEC <= 24 * 60 * 60, script._ESPERA_SYNC_SEC
        monkeypatch.setattr("frontend.routes.open_finance._adota_item_orfao", _adota)
        asyncio.run(script._executar(["g-igante"], apply=True, delete=False))
        assert saiu == [], f"abandonou um sync que já tinha terminado: {saiu}"
    finally:
        monkeypatch.undo()
        importlib.reload(script)


def _roda_no_repo(codigo_ou_args, **env):
    """Roda código (ou `-m modulo`) neste repositório, em processo NOVO.

    `VAR=None` APAGA a variável em vez de defini-la: o teste do flush precisa de
    um stdout block-buffered, e um `PYTHONUNBUFFERED` herdado do pytest o deixaria
    verde com e sem o conserto.
    """
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cmd = ([sys.executable, *codigo_ou_args] if isinstance(codigo_ou_args, list)
           else [sys.executable, "-c", codigo_ou_args])
    ambiente = {**os.environ, "PYTHONPATH": raiz, **env}
    return subprocess.run(cmd, cwd=raiz, capture_output=True, text=True, timeout=180,
                          env={k: v for k, v in ambiente.items() if v is not None})


def test_env_var_com_lixo_nao_derruba_o_help():
    """`ADOCAO_SYNC_ESPERA_SEC` era a única env var do repo que matava o processo
    NO IMPORT: `float("")`/`float("abc")` levantava `ValueError` antes do `main`,
    derrubando junto o `--help` e o dry-run — caminhos que nem chegam perto da
    espera.

    Subprocesso de propósito: o defeito era no import do MÓDULO, e importar em
    processo já quente (a suíte importa este módulo em outros testes) não o
    reproduz.
    """
    p = _roda_no_repo(["-m", "scripts.adotar_items_of_orfaos", "--help"],
                      ADOCAO_SYNC_ESPERA_SEC="abc")
    assert p.returncode == 0, f"--help morreu com lixo na env var:\n{p.stderr}"
    assert "--apply" in p.stdout, p.stdout


def test_prazo_lido_da_env_var_lixo_cai_no_default_e_negativo_vira_zero():
    """A TABELA da env var, lida da CONSTANTE do módulo e não de uma cópia da
    expressão aqui (§0.7: cópia da regra passa verde com o código errado).

    `0` é escolha legítima do operador ("adota e me devolve o prompt") e é
    HONRADO; negativo se comporta como 0 no `asyncio.wait` de qualquer jeito, e o
    clamp existe para a mensagem não imprimir "prazo de -5s estourado". `1e400`
    virava `inf` com o `float` — prazo que nunca estoura é a ausência de prazo —,
    e o int de 401 dígitos (que o `int` ACEITA) é o mesmo problema pela outra
    porta: aqui ele tem de virar o teto de 24h, não `OverflowError` no `asyncio.wait`.
    """
    p = _roda_no_repo(
        "import os, importlib\n"
        "import scripts.adotar_items_of_orfaos as s\n"
        "for v in ['', 'abc', '  ', '1e400', '0.5', '0', '-5', '7', '1'+'0'*400]:\n"
        "    os.environ['ADOCAO_SYNC_ESPERA_SEC'] = v\n"
        "    print(repr(v), importlib.reload(s)._ESPERA_SYNC_SEC)\n")
    assert p.returncode == 0, p.stderr
    lido = dict(linha.rsplit(" ", 1) for linha in p.stdout.strip().splitlines())
    assert lido == {"''": "120", "'abc'": "120", "'  '": "120", "'1e400'": "120",
                    "'0.5'": "120", "'0'": "0", "'-5'": "0", "'7'": "7",
                    repr("1" + "0" * 400): "86400"}, p.stdout
