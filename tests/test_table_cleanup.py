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
import os
import uuid

from _billing_grants_helpers import garantir_system_event_logs
from _lifespan_probe import sondar
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
# processo com o `app` de verdade e vê a corrotina ser executada.
#
# O harness mora em `tests/_lifespan_probe.py` desde que o segundo consumidor
# apareceu (a purga de retenção do Pix): a blindagem de ambiente é o que não
# podia virar duas cópias (§0.7).
def test_processo_do_app_liga_a_poda_no_lifespan():
    """CONTRATO: um processo que serve o `app` com RUN_BACKGROUND_TASKS=1
    executa o loop de poda. Sem o `create_task`, `{"poda": false}`."""
    resultado, diagnostico = sondar(
        {"poda": "core.services.table_cleanup:run_table_cleanup_loop"})
    assert resultado, f"subprocesso não chegou ao fim:\n{diagnostico}"
    assert resultado == {"poda": True, "do_disco": []}, (
        "a poda existe e ninguém a chama (lifespan sem a tarefa de fundo), OU "
        "o `.env` do disco entrou e as tarefas de fundo subiram com "
        f"credencial de terceiro viva.\n{diagnostico}"
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
