"""core/services/table_cleanup.py — a poda apaga o que deve e NÃO apaga o resto.

Isto APAGA LINHA EM PRODUÇÃO, então cada tabela tem os dois lados: uma linha
que TEM de cair e pelo menos uma que TEM de ficar (sessão viva, MFA em curso,
cadastro Google em curso).

Isolamento: toda linha entra com chave `uuid4` e toda asserção conta por essa
chave (`where token(_hash) = %s`), nunca `count(*)` da tabela. O motivo NÃO é
outra execução de suíte — cada execução cria o próprio database `pytest_<uuid>`
(`tests/conftest.py:146`). É que a poda é GLOBAL e roda no mesmo database dos
demais testes da MESMA execução: qualquer um deles pode ter deixado token,
challenge ou cadastro pendente nessas três tabelas, e `count(*)` os contaria.

Rodar:
  DATABASE_URL=... PYTHONPATH=. .venv/bin/python -m pytest tests/test_table_cleanup.py -q
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

from _billing_grants_helpers import garantir_system_event_logs
from core.services.table_cleanup import run_table_cleanup, run_table_cleanup_loop
from db.connection import get_conn


def _existe(tabela: str, coluna: str, chave: str) -> bool:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(f"select count(*) as n from {tabela} where {coluna} = %s", (chave,))
        return int(cur.fetchone()["n"]) == 1


def _eventos(marcador: str) -> list[tuple[str, str, str]]:
    """O que a poda gravou no painel — filtrado pela chave, nunca a tabela toda."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select level, event_type, source from system_event_logs"
            " where message like %s order by id",
            (f"%{marcador}%",),
        )
        return [(r["level"], r["event_type"], r["source"]) for r in cur.fetchall()]


def test_refresh_tokens_poda_expirado_e_preserva_sessao_viva(user_id):
    casos = {
        "A_revogado_31d": ("now() - interval '31 days'", "now() + interval '1 day'", False),
        "B_expirado_8d": (None, "now() - interval '8 days'", False),
        "C_revogado_29d": ("now() - interval '29 days'", "now() + interval '1 day'", True),
        "D_expirado_6d": (None, "now() - interval '6 days'", True),
        "E_sessao_viva": (None, "now() + interval '10 days'", True),
    }
    chaves = {nome: uuid.uuid4().hex for nome in casos}

    with get_conn() as conn, conn.cursor() as cur:
        for nome, (revoked, expires, _) in casos.items():
            cur.execute(
                f"""
                insert into auth_refresh_tokens
                    (token_hash, user_id, session_jti, expires_at, revoked_at)
                values (%s, %s, %s, {expires}, {revoked or 'null'})
                """,
                (chaves[nome], user_id, uuid.uuid4().hex),
            )
        conn.commit()

    run_table_cleanup()

    sobreviveu = {n: _existe("auth_refresh_tokens", "token_hash", chaves[n]) for n in casos}
    assert sobreviveu == {n: casos[n][2] for n in casos}


def test_mfa_challenges_poda_o_velho_e_preserva_quem_esta_no_meio_do_mfa(user_id):
    casos = {
        "expirou_2d": ("now() - interval '2 days'", False),
        "expirou_2h": ("now() - interval '2 hours'", True),
        "em_curso": ("now() + interval '5 minutes'", True),
    }
    chaves = {nome: uuid.uuid4().hex for nome in casos}

    with get_conn() as conn, conn.cursor() as cur:
        for nome, (expires, _) in casos.items():
            cur.execute(
                f"insert into mfa_login_challenges (token, user_id, expires_at)"
                f" values (%s, %s, {expires})",
                (chaves[nome], user_id),
            )
        conn.commit()

    run_table_cleanup()

    sobreviveu = {n: _existe("mfa_login_challenges", "token", chaves[n]) for n in casos}
    assert sobreviveu == {n: casos[n][1] for n in casos}


def test_pending_google_poda_o_expirado_e_preserva_cadastro_em_curso():
    casos = {
        "expirou_1min": ("now() - interval '1 minute'", False),
        "em_curso": ("now() + interval '10 minutes'", True),
    }
    chaves = {nome: uuid.uuid4().hex for nome in casos}

    with get_conn() as conn, conn.cursor() as cur:
        for nome, (expires, _) in casos.items():
            cur.execute(
                f"""
                insert into pending_google_signups
                    (token, provider, provider_sub, email, expires_at)
                values (%s, 'google', %s, %s, {expires})
                """,
                (chaves[nome], uuid.uuid4().hex, f"{chaves[nome]}@example.test"),
            )
        conn.commit()

    try:
        run_table_cleanup()
        sobreviveu = {n: _existe("pending_google_signups", "token", chaves[n]) for n in casos}
        assert sobreviveu == {n: casos[n][1] for n in casos}
    finally:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "delete from pending_google_signups where token = any(%s)",
                (list(chaves.values()),),
            )
            conn.commit()


def test_intervalo_zero_desliga_o_loop_sem_podar(monkeypatch):
    """Kill switch: a corrotina RETORNA e não chama a poda uma vez sequer."""
    import core.services.table_cleanup as tc

    chamadas: list[int] = []
    monkeypatch.setenv("TABLE_CLEANUP_INTERVAL_HOURS", "0")
    monkeypatch.setattr(tc, "run_table_cleanup", lambda **kw: chamadas.append(1))

    asyncio.run(asyncio.wait_for(run_table_cleanup_loop(), timeout=5))

    assert chamadas == []


# T5 — a tarefa SOBE no lifespan. É o teste que prende o bug original: as três
# funções de poda existiam e ninguém as chamava. Ler o arquivo com `read_text()`
# procurando o nome da função não mede nada (CLAUDE.md §3), então isto sobe um
# processo com o `app` de verdade e vê a corrotina ser executada. O loop real é
# trocado por um marcador ANTES do import do app — o wrapper do lifespan faz
# import tardio, que resolve o atributo do módulo na hora da chamada.
_SUBPROCESSO = r'''
import os
_ENV_INICIAL = set(os.environ)   # antes de tudo; o diff no fim acusa o disco

# PRIMEIRA linha de projeto, antes de tudo: `config/env.py:95-97` faz
# `os.environ.setdefault` com o `.env` do DISCO, e `load_app_env` roda no IMPORT de
# `core.observability`, `frontend.routes.shared` e do monólito. Sem isto, as 17
# tarefas de fundo sobem com o `.env` inteiro na mão — não só as chaves de saída:
# medido no checkout principal, `load_app_env` acendeu 9 segredos (SMTP_PASSWORD,
# MFA_ENCRYPTION_KEY, WA_APP_SECRET, GOOGLE_CLIENT_SECRET…) que a env montada
# abaixo nunca passou. `ROOT_DIR` é o único ponto por onde o disco entra
# (`config/env.py:32-39`); apontá-lo para um diretório vazio faz `merged = {}` e
# fecha a CATEGORIA, em vez de nomear chaves uma a uma.
# A ordem é o contrato: qualquer import de projeto ACIMA desta linha reabre o buraco.
import pathlib, tempfile
import config.env
config.env.ROOT_DIR = pathlib.Path(tempfile.mkdtemp())

import json, time
import core.services.table_cleanup as tc

estado = {"iniciou": False}

async def _marcador():
    estado["iniciou"] = True

tc.run_table_cleanup_loop = _marcador

import frontend.finance_bot_websocket_custom as m
from fastapi.testclient import TestClient

with TestClient(m.app):          # lifespan de verdade, como no processo web
    for _ in range(80):          # o wrapper dorme 2s antes do import tardio
        if estado["iniciou"]:
            break
        time.sleep(0.25)

# Prova que o disco não entrou: `load_app_env` já rodou (o import do monólito o
# chama), então qualquer chave lida do `.env` estaria aqui. A lista é DERIVADA do
# ambiente — não há nomes de segredo a manter, então ela não envelhece junto com o
# `.env`. Os três excluídos são escritos pelo próprio `config/env.py`, com ou sem
# disco: `APP_ENV` (`:108`), `TZ` (import de `utils_date`) e `PGTZ`
# (`align_process_tz`, `:121`). Se um quarto aparecer, isto fica VERMELHO — que é
# a direção certa da falha, ao contrário da lista de 5 nomes que estava aqui e
# passava verde deixando o resto do `.env` vivo.
estado["do_disco"] = sorted(set(os.environ) - _ENV_INICIAL - {"APP_ENV", "TZ", "PGTZ"})
print("RESULTADO:" + json.dumps(estado))
'''


def test_processo_do_app_liga_a_poda_no_lifespan():
    """CONTRATO: um processo que serve o `app` com RUN_BACKGROUND_TASKS=1
    executa o loop de poda. Sem o `create_task`, `{"iniciou": false}`."""
    raiz = Path(__file__).resolve().parent.parent
    # env MONTADA, não `{**os.environ}`: com `RUN_BACKGROUND_TASKS=1` este é o
    # único teste do repositório que sobe as 17 tarefas de fundo, e herdar o
    # ambiente entregava as credenciais de terceiros VIVAS a elas por ~6s.
    # Enxugar a env SOZINHO não protegia nada — o `.env` do disco repunha o que
    # faltasse. Quem fecha o disco é o `ROOT_DIR` do `_SUBPROCESSO` acima; esta
    # env é a outra metade (o que o processo pai NÃO repassa).
    # É o MÍNIMO medido para o boot chegar ao lifespan: sem `JWT_SECRET` o
    # processo aborta ("Refusing to start with insecure default"); com estas
    # quatro chaves ele sobe e imprime RESULTADO (medido: `iniciou: true`).
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": ".",
        "RUN_BACKGROUND_TASKS": "1",
        "DATABASE_URL": os.environ.get("DATABASE_URL", ""),
        "JWT_SECRET": os.environ.get("JWT_SECRET", ""),
    }
    proc = subprocess.run(
        [sys.executable, "-c", _SUBPROCESSO], cwd=raiz, env=env,
        capture_output=True, text=True, timeout=180,
    )
    linha = next((l for l in proc.stdout.splitlines()
                  if l.startswith("RESULTADO:")), None)
    assert linha, f"subprocesso não chegou ao fim:\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
    assert json.loads(linha[len("RESULTADO:"):]) == {
        "iniciou": True, "do_disco": []
    }, (
        "a poda existe e ninguém a chama (lifespan sem a tarefa de fundo), OU "
        "o `.env` do disco entrou e as 17 tarefas de fundo subiram com "
        "credencial de terceiro viva.\n"
        f"{proc.stderr[-2000:]}"
    )


# T6 — o CORPO do laço. T1/T2/T3 chamam `run_table_cleanup()` direto e T4 põe
# intervalo 0 (o `while` nem entra): trocar o corpo por `pass` deixava tudo
# verde. Aqui o laço roda LIGADO, com a espera curto-circuitada.
def test_laco_ligado_executa_a_poda(monkeypatch):
    """Com intervalo válido, uma volta do laço chama a poda de fato.

    Mutação: corpo do `while` → `pass` deixa `chamadas` vazia (e o laço nunca
    encerra, porque quem zera a env é a própria poda) → vermelho."""
    import core.services.table_cleanup as tc

    chamadas: list[dict] = []
    espera_real = asyncio.sleep
    monkeypatch.setenv("TABLE_CLEANUP_INTERVAL_HOURS", "24")

    def _poda(**kw):
        chamadas.append(kw)
        os.environ["TABLE_CLEANUP_INTERVAL_HOURS"] = "0"  # 2ª volta encerra
        return {"removed": {"auth_refresh_tokens": 1}, "errors": [], "dry_run": False}

    monkeypatch.setattr(tc, "run_table_cleanup", _poda)
    monkeypatch.setattr(tc, "log_event", lambda *a, **k: None)

    async def _sem_esperar(_segundos):
        await espera_real(0)  # cede o loop sem os 120s do boot nem as 24h

    monkeypatch.setattr(asyncio, "sleep", _sem_esperar)

    asyncio.run(asyncio.wait_for(run_table_cleanup_loop(), timeout=5))

    assert chamadas == [{}], "o corpo do laço não executou a poda"


# T7 — o ALERTA. T6 (acima) desliga o `log_event` de propósito, e era o único
# teste a rodar o corpo do laço: apagar o `await asyncio.to_thread(log_event, …)`
# deixava a suíte inteira verde. Aqui ele fica LIGADO e a asserção é a linha em
# `system_event_logs` — que é de onde o painel lê.
def test_falha_da_poda_grava_alerta_de_error_no_painel(monkeypatch):
    """Poda que volta com `errors` vira linha de nível `error` no painel.

    Mutação: apagar o bloco `await asyncio.to_thread(log_event, …)` do laço →
    `depois == []` → vermelho."""
    import core.services.table_cleanup as tc

    garantir_system_event_logs()  # a tabela nasce no startup, não no init_db
    marcador = uuid.uuid4().hex   # a tabela é global: a busca é pela chave
    espera_real = asyncio.sleep
    monkeypatch.setenv("TABLE_CLEANUP_INTERVAL_HOURS", "24")

    def _poda(**kw):
        os.environ["TABLE_CLEANUP_INTERVAL_HOURS"] = "0"  # 2ª volta encerra
        return {"removed": {}, "errors": [{"table": marcador, "error": "boom"}],
                "dry_run": False}

    monkeypatch.setattr(tc, "run_table_cleanup", _poda)  # `log_event` NÃO se desliga

    async def _sem_esperar(_segundos):
        await espera_real(0)

    monkeypatch.setattr(asyncio, "sleep", _sem_esperar)

    assert _eventos(marcador) == []
    try:
        asyncio.run(asyncio.wait_for(run_table_cleanup_loop(), timeout=10))

        assert _eventos(marcador) == [
            ("error", "cleanup_job", "core.services.table_cleanup")
        ], "a poda falhou e o painel não ficou sabendo"
    finally:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "delete from system_event_logs where message like %s", (f"%{marcador}%",)
            )
            conn.commit()


def test_dry_run_nao_apaga_nada(user_id):
    """A única válvula do operador: `--dry-run` LISTA e não apaga.

    Mutação: `if dry_run:` → `if False:` apaga o token abaixo → vermelho."""
    chave = uuid.uuid4().hex
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            insert into auth_refresh_tokens
                (token_hash, user_id, session_jti, expires_at)
            values (%s, %s, %s, now() - interval '8 days')
            """,
            (chave, user_id, uuid.uuid4().hex),
        )
        conn.commit()

    resumo = run_table_cleanup(dry_run=True)

    assert _existe("auth_refresh_tokens", "token_hash", chave), (
        "dry-run APAGOU linha que a poda de verdade levaria"
    )
    assert resumo == {"removed": {}, "errors": [], "dry_run": True}
