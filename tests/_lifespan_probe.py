"""Sonda do lifespan: o `app` de VERDADE sobe e a tarefa de fundo CHAMA a função.

Extraída de `tests/test_table_cleanup.py` quando o segundo consumidor apareceu
(a purga de retenção do Pix, §13.2 + §13.3). Não é abstração especulada — os
dois medem a MESMA propriedade, que é o defeito que já custou a poda das três
tabelas: **a função existe e ninguém a chama**. E o preâmbulo de blindagem de
ambiente abaixo é justamente o que não pode divergir entre os dois (§0.7): ele
é o que impede as tarefas de fundo de subirem com o `.env` do disco na mão.

Ler o arquivo com `read_text()` procurando o nome da função NÃO mede isto
(CLAUDE.md §3): a corrotina de fundo é uma CLOSURE dentro do `lifespan` e não
tem como ser chamada de um teste. Por isso o processo é de verdade.

Sem prefixo `test_`: o pytest não coleta este arquivo.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

_FONTE = r'''
import os
_ENV_INICIAL = set(os.environ)   # antes de tudo; o diff no fim acusa o disco

# PRIMEIRA linha de projeto, antes de tudo: `config/env.py:95-97` faz
# `os.environ.setdefault` com o `.env` do DISCO, e `load_app_env` roda no IMPORT de
# `core.observability`, `frontend.routes.shared` e do monólito. Sem isto, as tarefas
# de fundo sobem com o `.env` inteiro na mão — não só as chaves de saída: medido no
# checkout principal, `load_app_env` acendeu 9 segredos (SMTP_PASSWORD,
# MFA_ENCRYPTION_KEY, WA_APP_SECRET, GOOGLE_CLIENT_SECRET…) que a env montada pelo
# processo pai nunca passou. `ROOT_DIR` é o único ponto por onde o disco entra
# (`config/env.py:32-39`); apontá-lo para um diretório vazio faz `merged = {}` e
# fecha a CATEGORIA, em vez de nomear chaves uma a uma.
# A ordem é o contrato: qualquer import de projeto ACIMA desta linha reabre o buraco.
import pathlib, tempfile
import config.env
config.env.ROOT_DIR = pathlib.Path(tempfile.mkdtemp())

import importlib, inspect, json, time

ALVOS = json.loads(ALVOS_JSON)
estado = {rotulo: False for rotulo in ALVOS}

def _substituto(original, rotulo):
    """Marcador do MESMO tipo do original.

    `iscoroutinefunction` decide porque as duas formas existem no lifespan:
    `run_table_cleanup_loop` é awaitada direto, e as varreduras do Pix são
    síncronas e entram por `asyncio.to_thread`. Um marcador `async` no lugar de
    uma função síncrona viraria uma corrotina nunca awaitada, e o `if n:` do
    laço leria o objeto como verdadeiro — verde pelo motivo errado.
    O `{}` de retorno é falsy nos dois usos (`if n:` e `any(d.values())`).
    """
    if inspect.iscoroutinefunction(original):
        async def _m(*a, **kw):
            estado[rotulo] = True
        return _m
    def _m(*a, **kw):
        estado[rotulo] = True
        return {}
    return _m

for rotulo, caminho in ALVOS.items():
    nome_modulo, _, atributo = caminho.partition(":")
    modulo = importlib.import_module(nome_modulo)
    setattr(modulo, atributo, _substituto(getattr(modulo, atributo), rotulo))

import frontend.finance_bot_websocket_custom as m
from fastapi.testclient import TestClient

with TestClient(m.app):          # lifespan de verdade, como no processo web
    for _ in range(80):          # o wrapper dorme 2s antes do import tardio
        if all(estado.values()):
            break
        time.sleep(0.25)

# Prova que o disco não entrou: `load_app_env` já rodou (o import do monólito o
# chama), então qualquer chave lida do `.env` estaria aqui. A lista é DERIVADA do
# ambiente — não há nomes de segredo a manter, então ela não envelhece junto com o
# `.env`. Os três excluídos são escritos pelo próprio `config/env.py`, com ou sem
# disco: `APP_ENV` (`:108`), `TZ` (import de `utils_date`) e `PGTZ`
# (`align_process_tz`, `:121`). Se um quarto aparecer, isto fica VERMELHO — que é
# a direção certa da falha, ao contrário de uma lista de nomes que passaria verde
# deixando o resto do `.env` vivo.
resultado = dict(estado)
resultado["do_disco"] = sorted(set(os.environ) - _ENV_INICIAL - {"APP_ENV", "TZ", "PGTZ"})
print("RESULTADO:" + json.dumps(resultado))
'''


def sondar(alvos: dict[str, str], timeout: int = 180) -> tuple[dict | None, str]:
    """Sobe o `app` com `RUN_BACKGROUND_TASKS=1` e diz quais dos `alvos` foram
    chamados. `alvos` é `{rótulo: "pacote.modulo:atributo"}`.

    Devolve `(resultado, diagnóstico)`. `resultado` é `None` quando o subprocesso
    não chegou ao fim — e aí o diagnóstico traz stdout/stderr.
    """
    # env MONTADA, não `{**os.environ}`: com `RUN_BACKGROUND_TASKS=1` este é o
    # único caminho do repositório que sobe as tarefas de fundo todas, e herdar o
    # ambiente entregava as credenciais de terceiros VIVAS a elas por ~6s.
    # Enxugar a env SOZINHO não protegia nada — o `.env` do disco repunha o que
    # faltasse. Quem fecha o disco é o `ROOT_DIR` do `_FONTE`; esta env é a outra
    # metade (o que o processo pai NÃO repassa). É o MÍNIMO medido para o boot
    # chegar ao lifespan: sem `JWT_SECRET` o processo aborta ("Refusing to start
    # with insecure default").
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": ".",
        "RUN_BACKGROUND_TASKS": "1",
        "DATABASE_URL": os.environ.get("DATABASE_URL", ""),
        "JWT_SECRET": os.environ.get("JWT_SECRET", ""),
    }
    fonte = f"ALVOS_JSON = {json.dumps(json.dumps(alvos))}\n" + _FONTE
    proc = subprocess.run(
        [sys.executable, "-c", fonte], cwd=RAIZ, env=env,
        capture_output=True, text=True, timeout=timeout,
    )
    linha = next((l for l in proc.stdout.splitlines()
                  if l.startswith("RESULTADO:")), None)
    diagnostico = f"{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
    if not linha:
        return None, diagnostico
    return json.loads(linha[len("RESULTADO:"):]), diagnostico
