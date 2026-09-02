"""G4 — concorrência real: threads de verdade, banco de verdade, caminho de verdade.

Fatos medidos em produção: a Pluggy manda `item/updated` e `transactions/created`
com 0–17s de intervalo, e cada evento criava uma task de sync sem lock nenhum —
10 `deadlock detected` e 1 violação de `uq_credit_tx_source_external` (o import de
cartão fazia SELECT-depois-INSERT, que é check-then-act).

CONTROLE NEGATIVO do grupo (os dois primeiros MEDIDOS nesta rodada):
  • desligar o `pluggy_item_lock` (fazer o contextmanager devolver True sem tomar
    lock) → `test_dois_syncs_do_mesmo_item_serializam_a_escrita` vermelho, na
    asserção de sobreposição das janelas de escrita;
  • devolver o lock para ANTES das leituras remotas (como era) →
    `test_lock_nao_e_segurado_durante_a_paginacao` vermelho;
  • tirar o `on conflict ... do nothing` do `add_imported_credit_purchase`
    → `test_duas_importacoes_do_mesmo_external_id...` vermelho (IntegrityError).
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from datetime import date
from decimal import Decimal

import psycopg
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

# Garante MFA_ENCRYPTION_KEY antes de importar o dashboard (que cacheia Fernet).
# No TOPO, como nos irmãos do repo (tests/test_mfa.py:18): dentro de um teste
# isto seria env global escrita no meio da suíte, e esta suíte tem dependência de
# ordem conhecida.
os.environ.setdefault("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())

import db
import core.services.pluggy_sync as ps
import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.open_finance as of_routes
from db.connection import get_conn

ITEM = {
    "id": "item-conc", "status": "UPDATED", "executionStatus": "SUCCESS",
    "statusDetail": {"accounts": {"isUpdated": True, "lastUpdatedAt": "2026-08-20T11:00:00Z"}},
}


def _conexao(user_id: int, item_id: str = "item-conc") -> dict:
    return db.save_pluggy_open_finance_item(
        user_id,
        {"id": item_id, "status": "UPDATED", "connector": {"id": 612, "name": "Nubank"}},
    )


def _espelho(connection_id: int) -> tuple[int, int]:
    with get_conn() as c:
        with c.cursor() as cur:
            cur.execute("select count(*) as n from open_finance_accounts where connection_id=%s",
                        (connection_id,))
            contas = int(cur.fetchone()["n"])
            cur.execute(
                "select count(*) as n from open_finance_transactions t "
                "join open_finance_accounts a on a.id=t.account_id where a.connection_id=%s",
                (connection_id,))
            txs = int(cur.fetchone()["n"])
    return contas, txs


# ── 21. dois syncs do mesmo item em paralelo ─────────────────────────────────
# O lock deixou de cercar o sync inteiro e passa a cercar SÓ a fase de escrita:
# segurar um advisory lock de SESSÃO durante `list_pluggy_transactions` (até 60
# requisições paginadas por conta) retinha uma conexão do pool por minutos —
# medido, com DB_POOL_MAX_SYNC=2 e 2 locks abertos um `select 1` estourava
# PoolTimeout em 30s, e em produção max_size=8 travaria o processo inteiro.
#
# CONTROLE NEGATIVO deste par: fazer `pluggy_item_lock` devolver True sem tomar
# lock nenhum → `test_dois_syncs...` vermelho (as janelas de escrita se
# sobrepõem). Devolver o lock para o começo do sync (como era) →
# `test_lock_nao_e_segurado_durante_a_paginacao` vermelho.

def _prova_lock_livre(item_id: str) -> bool:
    """De uma conexão de FORA: ninguém está segurando o lock deste item."""
    import os
    import psycopg
    from db.open_finance_state import _lock_key
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
        got = c.execute("select pg_try_advisory_lock(hashtext(%s)) as g",
                        (_lock_key(item_id),)).fetchone()[0]
        if got:
            c.execute("select pg_advisory_unlock(hashtext(%s))", (_lock_key(item_id),))
        return bool(got)


def test_dois_syncs_do_mesmo_item_serializam_a_escrita(user_id, monkeypatch):
    conexao = _conexao(user_id)
    janelas: list[tuple[float, float]] = []
    real_save = db.save_open_finance_sync

    def _save_lento(connection_id, accounts):
        inicio = time.monotonic()
        try:
            return real_save(connection_id, accounts)
        finally:
            time.sleep(0.4)          # segura a fase de ESCRITA
            janelas.append((inicio, time.monotonic()))

    monkeypatch.setattr(ps, "save_open_finance_sync", _save_lento)
    monkeypatch.setattr(ps, "create_pluggy_api_key", lambda: "k")
    monkeypatch.setattr(ps, "get_pluggy_item", lambda i, k=None: ITEM)
    monkeypatch.setattr(ps, "list_pluggy_accounts", lambda i, k=None: [
        {"id": "acc-conc", "name": "Conta", "type": "BANK", "currencyCode": "BRL",
         "balance": "10.00"}])
    monkeypatch.setattr(ps, "list_pluggy_transactions",
                        lambda acc, k=None, **kw: [{"id": "tx-conc", "description": "M",
                                                    "amount": "-1.00", "date": "2026-08-19"}])
    monkeypatch.setattr(ps, "list_pluggy_investments", lambda i, k=None: [])

    barreira = threading.Barrier(2, timeout=10)
    resultados: list[dict] = []
    erros: list[BaseException] = []

    def _roda():
        try:
            barreira.wait()
            resultados.append(ps.sync_pluggy_item("item-conc"))
        except BaseException as exc:   # noqa: BLE001 — o teste decide o que fazer
            erros.append(exc)

    threads = [threading.Thread(target=_roda) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert erros == [], f"nenhuma exceção era esperada: {erros}"
    assert len(janelas) == 2, f"as duas escritas tinham que acontecer: {janelas}"
    (a_ini, a_fim), (b_ini, b_fim) = sorted(janelas)
    assert b_ini >= a_fim - 0.01, (
        f"as escritas se sobrepuseram — o lock não serializou: {janelas}")
    assert all(r.get("ok") for r in resultados), resultados
    assert _espelho(conexao["id"]) == (1, 1), "espelho gravado uma vez só"
    assert _prova_lock_livre("item-conc"), "o lock tem que ser solto no fim"


def test_lock_nao_e_segurado_durante_a_paginacao(user_id, monkeypatch):
    """A leitura remota é read-only contra a Pluggy e não pode reter nada.

    Durante a paginação: (1) ninguém segura o advisory lock do item e (2) o pool
    continua atendendo — que é o que estourava PoolTimeout.
    """
    _conexao(user_id, "item-pag")
    medidas: list[tuple[bool, bool]] = []

    def _txs(account_id, api_key=None, **kw):
        pool_ok = False
        try:
            with get_conn() as c:
                pool_ok = c.execute("select 1 as x").fetchone()["x"] == 1
        except Exception:
            pool_ok = False
        medidas.append((_prova_lock_livre("item-pag"), pool_ok))
        return []

    monkeypatch.setattr(ps, "create_pluggy_api_key", lambda: "k")
    monkeypatch.setattr(ps, "get_pluggy_item", lambda i, k=None: {**ITEM, "id": "item-pag"})
    monkeypatch.setattr(ps, "list_pluggy_accounts", lambda i, k=None: [
        {"id": "acc-pag", "name": "Conta", "type": "BANK", "currencyCode": "BRL",
         "balance": "10.00"}])
    monkeypatch.setattr(ps, "list_pluggy_transactions", _txs)
    monkeypatch.setattr(ps, "list_pluggy_investments", lambda i, k=None: [])

    ps.sync_pluggy_item("item-pag")

    assert medidas, "a paginação tinha que ter sido exercitada"
    for lock_livre, pool_ok in medidas:
        assert lock_livre, "o lock estava tomado DURANTE a leitura remota"
        assert pool_ok, "o pool não respondeu durante a leitura remota"


# ── 22. evento durante o sync → UMA re-execução ──────────────────────────────

def test_evento_durante_o_sync_gera_exatamente_uma_reexecucao(monkeypatch):
    execucoes: list[str] = []
    liberado = asyncio.Event()

    async def _sync_lento(item_id):
        execucoes.append(item_id)
        if len(execucoes) == 1:
            await liberado.wait()
        return None

    monkeypatch.setattr(of_routes, "_run_pluggy_sync_bg", _sync_lento)
    of_routes._INFLIGHT.clear()
    of_routes._DIRTY.clear()

    async def _cenario():
        of_routes._schedule_pluggy_sync("item-x")   # 1º evento: roda
        await asyncio.sleep(0)
        of_routes._schedule_pluggy_sync("item-x")   # chega durante o sync → dirty
        of_routes._schedule_pluggy_sync("item-x")   # e mais um → continua 1 bit
        liberado.set()
        for _ in range(10):
            await asyncio.sleep(0)
        return list(execucoes)

    feitas = asyncio.run(_cenario())

    assert feitas == ["item-x", "item-x"], f"esperava 1 execução + 1 re-execução: {feitas}"
    assert of_routes._INFLIGHT == {}
    assert of_routes._DIRTY == set()


# ── 23. duas importações do mesmo external_id ────────────────────────────────

def test_duas_importacoes_do_mesmo_external_id_somam_a_fatura_uma_vez(user_id):
    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    compra = date(2026, 8, 5)
    barreira = threading.Barrier(2, timeout=10)
    saidas: list[tuple] = []
    erros: list[BaseException] = []

    def _importa():
        try:
            barreira.wait()
            saidas.append(db.add_imported_credit_purchase(
                user_id, card_id, "-100.00", "mercado", compra, "ext-dup",
            ))
        except BaseException as exc:  # noqa: BLE001
            erros.append(exc)

    threads = [threading.Thread(target=_importa) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert erros == [], f"a corrida não pode estourar: {erros}"
    with get_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "select count(*) as n from credit_transactions "
                "where user_id=%s and source='open_finance' and external_id='ext-dup'",
                (user_id,))
            assert int(cur.fetchone()["n"]) == 1, "duplicou a transação de cartão"
            cur.execute(
                "select total from credit_bills where user_id=%s and card_id=%s",
                (user_id, card_id))
            totais = [Decimal(str(r["total"])) for r in cur.fetchall()]
    assert totais == [Decimal("100.00")], f"a fatura somou {totais} (deveria somar uma vez só)"


# ── 24. retry só para erro de concorrência ───────────────────────────────────

def test_retry_dispara_em_deadlock_e_nao_em_erro_de_programacao(monkeypatch):
    eventos: list[tuple] = []

    async def _log(level, event_type, message, **kw):
        eventos.append((level, event_type))

    async def _sleep_rapido(_s):
        return None

    monkeypatch.setattr(of_routes, "log_system_event", _log)
    monkeypatch.setattr(of_routes.asyncio, "sleep", _sleep_rapido)

    tentativas = []

    def _sempre_deadlock(item_id):
        tentativas.append(item_id)
        raise psycopg.errors.DeadlockDetected("deadlock detected")

    monkeypatch.setattr(of_routes, "sync_pluggy_item", _sempre_deadlock)
    asyncio.run(of_routes._run_pluggy_sync_bg("item-dead"))

    assert len(tentativas) == 3, f"máximo de 3 tentativas: {len(tentativas)}"
    retries = [e for e in eventos if e[1] == "pluggy_sync_retry"]
    assert len(retries) == 2, f"um log por RETENTATIVA (2 esperas para 3 tentativas): {eventos}"
    assert ("error", "pluggy_sync_failed") in eventos

    # erro de programação não é retentado — repetir 3x é o mesmo bug 3x
    eventos.clear()
    tentativas.clear()

    def _erro_bobo(item_id):
        tentativas.append(item_id)
        raise ValueError("bug de verdade")

    monkeypatch.setattr(of_routes, "sync_pluggy_item", _erro_bobo)
    asyncio.run(of_routes._run_pluggy_sync_bg("item-bug"))

    assert len(tentativas) == 1
    assert [e for e in eventos if e[1] == "pluggy_sync_retry"] == []


# ── ONDA 2 / Codex #162 (4º P2): `sync_in_progress` travava indefinidamente ──
# `sync_pluggy_item` devolve `sync_in_progress` como DICT, não como exceção, e o
# loop de `_run_pluggy_sync_bg` só retentava exceção `_retryable` — então a
# tarefa encerrava em silêncio e ninguém mais sincronizava aquele item.
#
# Por que dói: o run de geração velha segura o `pluggy_item_lock` enquanto
# escreve; o sync que a RECONEXÃO agendou bate no lock, recebe
# `sync_in_progress` e desiste. Com `OF_REFRESH_ENABLED` off (o default) nada
# mais roda sozinho — o espelho velho fica indefinidamente e a tela só sai do
# âmbar se o usuário tocar "Atualizar" ou puxar a tela.

def test_sync_in_progress_e_retentado(user_id, monkeypatch):
    """CONTROLE NEGATIVO: com o `break` incondicional de volta, `tentativas`
    vira 1 e o `pluggy_sync_retry` some — o travamento indefinido volta."""
    eventos = []

    async def _log(level, event_type, *a, **kw):
        eventos.append((level, event_type))

    async def _sleep_rapido(_s):
        return None

    monkeypatch.setattr(of_routes, "log_system_event", _log)
    monkeypatch.setattr(of_routes.asyncio, "sleep", _sleep_rapido)

    tentativas = []

    def _lock_ocupado(item_id, *, budget_ms=None):
        tentativas.append(item_id)
        return {"ok": False, "reason": "sync_in_progress", "item_id": item_id}

    monkeypatch.setattr(of_routes, "sync_pluggy_item", _lock_ocupado)
    asyncio.run(of_routes._run_pluggy_sync_bg("item-ocupado"))

    assert len(tentativas) == 3, f"o lock ocupado tem que ser retentado: {tentativas}"
    assert len([e for e in eventos if e[1] == "pluggy_sync_retry"]) == 2


def test_sync_in_progress_que_libera_para_de_retentar(user_id, monkeypatch):
    """CONTROLE POSITIVO: o retry existe para ALCANÇAR o sync, não para gastar
    três tentativas sempre. Liberou o lock na 2ª, a 3ª não acontece."""
    async def _log(level, event_type, *a, **kw):
        return None

    async def _sleep_rapido(_s):
        return None

    monkeypatch.setattr(of_routes, "log_system_event", _log)
    monkeypatch.setattr(of_routes.asyncio, "sleep", _sleep_rapido)

    tentativas = []

    def _libera_na_segunda(item_id):
        tentativas.append(item_id)
        if len(tentativas) == 1:
            return {"ok": False, "reason": "sync_in_progress", "item_id": item_id}
        return {"ok": True, "item_id": item_id, "user_id": None}

    monkeypatch.setattr(of_routes, "sync_pluggy_item", _libera_na_segunda)
    asyncio.run(of_routes._run_pluggy_sync_bg("item-libera"))

    assert len(tentativas) == 2, "parou assim que conseguiu sincronizar"


def test_stale_authorization_nao_vira_laco(user_id, monkeypatch):
    """`stale_authorization` significa "alguém mais novo assumiu", e quem assumiu
    já agendou o próprio sync. Retentar seria correr atrás de trabalho que já tem
    dono — e num par de runs que se atropelam, laço. UMA tentativa e pronto."""
    eventos = []

    async def _log(level, event_type, *a, **kw):
        eventos.append((level, event_type))

    async def _sleep_rapido(_s):
        return None

    monkeypatch.setattr(of_routes, "log_system_event", _log)
    monkeypatch.setattr(of_routes.asyncio, "sleep", _sleep_rapido)

    tentativas = []

    def _geracao_velha(item_id):
        tentativas.append(item_id)
        return {"ok": False, "reason": "stale_authorization", "item_id": item_id}

    monkeypatch.setattr(of_routes, "sync_pluggy_item", _geracao_velha)
    asyncio.run(of_routes._run_pluggy_sync_bg("item-velho"))

    assert len(tentativas) == 1, f"stale_authorization não se retenta: {tentativas}"
    assert [e for e in eventos if e[1] == "pluggy_sync_retry"] == []


# ── ONDA 2 / Codex #162 (5º achado, P1): a reconexão participa do lock ──────
# A relectura da geração não é atômica com as escritas que vêm DEPOIS dela. Uma
# reconexão caindo nessa fresta deixava o run de geração velha gravar o espelho
# E rodar `import_open_finance_launches`/`import_open_finance_credit`, que criam
# LANÇAMENTO e COMPRA DE CARTÃO. O carimbo era recusado, mas nenhum sync
# posterior remove lançamento — o upsert só acrescenta. Transação fantasma de
# uma autorização que não vale mais, sobrevivendo à recuperação.
#
# Pegar o mesmo lock na reconexão fecha a fresta na origem.

def test_reconexao_grava_dentro_do_lock_de_escrita(user_id, monkeypatch):
    """O que se mede é a ORDEM: o `save` tem que acontecer entre o enter e o exit
    do lock. Sem isso o teste passaria com o lock pego e solto ao lado da escrita.

    CONTROLE NEGATIVO: chamar `save_pluggy_open_finance_item` direto (como antes)
    deixa o evento "save" fora do par enter/exit e este teste vermelho."""
    from contextlib import contextmanager

    ordem = []

    @contextmanager
    def _lock_espiao(item_id, *, budget_ms=None):
        ordem.append(("enter", item_id))
        try:
            yield True
        finally:
            ordem.append(("exit", item_id))

    monkeypatch.setattr(of_routes, "pluggy_item_lock", _lock_espiao)
    monkeypatch.setattr(of_routes, "save_pluggy_open_finance_item",
                        lambda uid, remote, **kw: ordem.append(("save", uid)) or {"id": 1})

    conexao, sob_lock = of_routes._salva_item_sob_lock(user_id, {"id": "item-lk"}, "item-lk")

    assert sob_lock is True
    assert ordem == [("enter", "item-lk"), ("save", user_id), ("exit", "item-lk")], ordem
    assert conexao == {"id": 1}


def test_sem_lock_nao_grava(user_id, monkeypatch):
    """Sem o lock, NÃO grava. A 1ª versão gravava assim mesmo e só logava — e é
    justamente a escrita que o lock existe para serializar (Codex #162, P1): se o
    teto estourou é porque um sync ESTÁ escrevendo, já passou pela checagem de
    geração, e vai importar lançamento da autorização velha.

    CONTROLE NEGATIVO: devolver `save_pluggy_open_finance_item(...)` no ramo sem
    lock deixa este teste vermelho."""
    from contextlib import contextmanager

    salvou = []

    @contextmanager
    def _lock_ocupado(item_id, *, budget_ms=None):
        yield False

    monkeypatch.setattr(of_routes, "pluggy_item_lock", _lock_ocupado)
    monkeypatch.setattr(of_routes, "save_pluggy_open_finance_item",
                        lambda uid, remote: salvou.append(uid) or {"id": 2})

    conexao, sob_lock = of_routes._salva_item_sob_lock(user_id, {"id": "item-lk2"}, "item-lk2")

    assert (conexao, sob_lock) == (None, False)
    assert salvou == [], "sem lock não se escreve nada"


def test_reconexao_retenta_o_lock_antes_de_desistir(user_id, monkeypatch):
    """A terceira opção: nem gravar sem lock, nem recusar de primeira. A janela
    do lock é só a fase de escrita de um sync, então ela passa em segundos.

    CONTROLE POSITIVO do grupo: sem o retry, um lock ocupado por um instante
    viraria 503 na cara do usuário."""
    tentativas = []

    async def _log(level, event_type, *a, **kw):
        return None

    async def _sleep_rapido(_s):
        return None

    monkeypatch.setattr(of_routes, "log_system_event", _log)
    monkeypatch.setattr(of_routes.asyncio, "sleep", _sleep_rapido)

    def _libera_na_segunda(uid, remote, item_id, budget_ms=None):
        tentativas.append(item_id)
        if len(tentativas) == 1:
            return None, False
        return {"id": 7}, True

    monkeypatch.setattr(of_routes, "_salva_item_sob_lock", _libera_na_segunda)

    conexao = asyncio.run(of_routes._grava_reconexao(user_id, {"id": "i"}, "item-retry"))

    assert conexao == {"id": 7}
    assert len(tentativas) == 2, "esperou a vez em vez de desistir ou gravar solto"


def test_lock_ocupado_ate_o_fim_recusa_com_503(user_id, monkeypatch):
    """Esgotadas as tentativas, RECUSA — não grava solto. 503 é recuperável: o
    item continua na Pluggy e o mesmo POST reaproveita. Lançamento fantasma não
    seria recuperável, e é essa a troca.

    CONTROLE NEGATIVO: voltar a gravar sem lock no fim deixa este teste vermelho."""
    from fastapi import HTTPException

    eventos = []

    async def _log(level, event_type, *a, **kw):
        eventos.append((level, event_type))

    async def _sleep_rapido(_s):
        return None

    monkeypatch.setattr(of_routes, "log_system_event", _log)
    monkeypatch.setattr(of_routes.asyncio, "sleep", _sleep_rapido)
    monkeypatch.setattr(of_routes, "_salva_item_sob_lock",
                        lambda uid, remote, item_id, budget_ms=None: (None, False))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(of_routes._grava_reconexao(user_id, {"id": "i"}, "item-preso"))

    assert exc.value.status_code == 503
    assert "tente de novo" in str(exc.value.detail).lower()
    assert ("error", "of_reconnect_lock_timeout") in eventos


# ── RODADA 3: o lock não pode esperar para sempre nem abrir conexão sem teto ──

def test_lock_wait_zero_nao_significa_espera_infinita(user_id, monkeypatch):
    """`lock_timeout='0ms'` no Postgres significa *desligado*, não *não espere*.
    Medido antes: `400ms` desistia em 0,48s; `0` seguia bloqueado depois de 3s,
    com a thread segurando a conexão dedicada."""
    _conexao(user_id, "item-wait0")
    monkeypatch.setenv("OF_SYNC_LOCK_WAIT_MS", "0")

    resultado = {}

    def _segundo():
        t0 = time.monotonic()
        with db.pluggy_item_lock("item-wait0") as got:
            resultado["got"] = got
            resultado["s"] = time.monotonic() - t0

    with db.pluggy_item_lock("item-wait0") as primeiro:
        assert primeiro is True
        t = threading.Thread(target=_segundo)
        t.start()
        t.join(timeout=5)

    assert not t.is_alive(), "esperou para sempre"
    assert resultado["got"] is False
    assert resultado["s"] < 2, f"desistiu em {resultado['s']:.2f}s"


def test_teto_de_conexoes_dedicadas(user_id, monkeypatch):
    """Medido antes: 30 syncs concorrentes = 30 backends extras fora do pool
    (antes=1, DURANTE=31). Quem não pega vaga volta False — mesmo desfecho de
    perder o lock, e o chamador já sabe reportar `sync_in_progress`."""
    import db.open_finance_state as ofs

    monkeypatch.setenv("OF_SYNC_LOCK_MAX_CONN", "2")
    monkeypatch.setenv("OF_SYNC_LOCK_WAIT_MS", "200")
    ofs._lock_slots.cache_clear()
    try:
        antes = _backends()
        segurando = threading.Event()
        soltar = threading.Event()
        pegos: list[bool] = []

        def _segura(item):
            with db.pluggy_item_lock(item) as got:
                pegos.append(got)
                segurando.set()
                soltar.wait(timeout=5)

        ts = [threading.Thread(target=_segura, args=(f"item-teto-{n}",)) for n in range(5)]
        for t in ts:
            t.start()
        segurando.wait(timeout=5)
        time.sleep(0.6)          # tempo de todo mundo tentar e os excedentes desistirem
        durante = _backends()
        soltar.set()
        for t in ts:
            t.join(timeout=5)

        assert durante - antes <= 2, f"teto furado: {durante - antes} conexões extras"
        assert pegos.count(False) >= 3, f"quem não pegou vaga tem que voltar False: {pegos}"
        assert pegos.count(True) >= 1, "CONTROLE POSITIVO: alguém tem que conseguir trabalhar"
    finally:
        ofs._lock_slots.cache_clear()


def _backends() -> int:
    with get_conn() as c:
        with c.cursor() as cur:
            cur.execute("select count(*) as n from pg_stat_activity"
                        " where datname = current_database()")
            return int(cur.fetchone()["n"])


def test_429_da_pluggy_e_retentado_pelo_webhook():
    """`_retryable` devolvia False para todo `PluggyApiError`: um 429 no meio do
    sync não era retentado, e a cota de leitura já tinha sido gasta. 404 fica de
    fora de propósito — é `item_missing`, e repetir não ressuscita item."""
    from core.services.pluggy import PluggyApiError

    assert of_routes._retryable(PluggyApiError("rate", status_code=429)) is True
    assert of_routes._retryable(PluggyApiError("gateway", status_code=502)) is True
    assert of_routes._retryable(PluggyApiError("sumiu", status_code=404)) is False
    assert of_routes._retryable(PluggyApiError("chave errada", status_code=401)) is False
    assert of_routes._retryable(ValueError("bug")) is False


# ── ONDA 3 (Codex #166, P2): a reconexão inteira cabe num prazo só ──────────
# O `_grava_reconexao` retenta o lock, e cada `pluggy_item_lock` reiniciava o
# relógio: até `OF_SYNC_LOCK_WAIT_MS` esperando vaga no semáforo MAIS o mesmo
# tanto no advisory lock, vezes 2 tentativas, mais backoff. Sob contenção o POST
# `/pluggy/item` passava de um minuto e o proxy derrubava antes — com o
# agravante de a gravação poder acontecer DEPOIS do timeout do cliente, ou seja,
# erro na cara do usuário num fluxo que deu certo.
#
# Agora o prazo é único e a tentativa seguinte recebe só o que sobrou.
#
# CONTROLES NEGATIVOS, os três medidos:
#   • tirar o `budget_ms=restante_ms` do call site → 2 vermelhos
#     (`test_prazo_nao_reinicia...` e `test_lock_livre...`);
#   • tirar a checagem `restante_ms < 1` do laço → 1
#     (`test_prazo_estourado_nem_tenta_de_novo`);
#   • tirar o repasse do orçamento dentro do `pluggy_item_lock` (usar
#     `_lock_wait_ms()` no `lock_timeout` mesmo com `budget_ms`) → 1
#     (`test_lock_reparte_o_orcamento...`).
# CONTROLE POSITIVO: `test_lock_livre_grava_na_primeira...` prova que o prazo não
# atrapalha o caminho comum — sem ele, um deadline pequeno demais passaria
# despercebido recusando tudo.

class _RelogioFake:
    """Relógio controlável: o teste decide quanto tempo cada etapa 'gasta'."""

    def __init__(self):
        self.agora = 1000.0

    def monotonic(self):
        return self.agora

    def anda(self, segundos):
        self.agora += segundos


def test_prazo_nao_reinicia_a_cada_tentativa(user_id, monkeypatch):
    """Cada tentativa recebe o RESTANTE, não o teto cheio de novo."""
    relogio = _RelogioFake()
    monkeypatch.setattr(of_routes.time, "monotonic", relogio.monotonic)
    monkeypatch.setattr(of_routes, "_RECONNECT_DEADLINE_MS", 20000)

    orcamentos = []

    async def _log(*a, **kw):
        return None

    async def _dorme(s):
        relogio.anda(s)          # o backoff também sai do mesmo bolso

    monkeypatch.setattr(of_routes, "log_system_event", _log)
    monkeypatch.setattr(of_routes.asyncio, "sleep", _dorme)

    inicio = relogio.agora

    def _ocupado(uid, remote, item_id, budget_ms=None):
        orcamentos.append((budget_ms, relogio.agora - inicio))
        # Gasta METADE do que recebeu: se gastasse tudo não haveria 2ª tentativa
        # (e é assim mesmo — `test_prazo_estourado_nem_tenta_de_novo` cobre esse
        # caso). Aqui o que se quer medir é que a 2ª recebe o RESTO.
        relogio.anda(budget_ms / 2000.0)
        return None, False

    monkeypatch.setattr(of_routes, "_salva_item_sob_lock", _ocupado)

    with pytest.raises(of_routes.HTTPException) as exc:
        asyncio.run(of_routes._grava_reconexao(user_id, {"id": "i"}, "item-prazo"))

    assert exc.value.status_code == 503
    assert len(orcamentos) == 2, orcamentos
    # O invariante: cada tentativa recebe o que RESTA do prazo dividido pelas
    # tentativas que ainda cabem. Dar o prazo inteiro à primeira parecia certo e
    # matava o retry — sob contenção real ela esperava tudo e a segunda nunca
    # acontecia. Somar os orçamentos não faz sentido: o 2º é o resto do 1º.
    for i, (orcamento, decorrido) in enumerate(orcamentos):
        folga = 20000 - int(decorrido * 1000)
        esperado = folga // (2 - i)
        # ±2ms: os dois lados truncam float em ponto diferente. A tolerância é
        # de arredondamento, não de comportamento — o relógio reiniciando daria
        # 20000 cravado na 2ª, que está a 10s de distância.
        assert abs(orcamento - esperado) <= 2, (
            f"tentativa {i + 1} recebeu {orcamento}ms com {decorrido:.3f}s já "
            f"gastos (folga {folga}, esperado {esperado}) — o relógio reiniciou")
    assert orcamentos[0][0] < 20000, \
        "a 1ª tentativa NÃO pode levar o prazo inteiro: sobra zero para a 2ª"
    assert relogio.agora - inicio <= 20.0, \
        f"a operação inteira estourou o prazo: {relogio.agora - inicio:.3f}s"


def test_backoff_nao_dorme_por_cima_do_que_o_log_gastou(user_id, monkeypatch):
    """A folga do backoff é recontada DEPOIS do `log_system_event` do retry.

    Aquele log abre conexão async NOVA e faz INSERT SEM `statement_timeout`,
    DENTRO da janela do prazo — é o maior componente do que sobra dela. A conta e
    as constantes ficam num lugar só, no comentário de `_prazo_reconexao_ms`
    (frontend/routes/open_finance.py). Não se repete número solto aqui, porque a
    versão anterior deste arquivo dizia "~82s" enquanto o comentário dizia
    "70,4s" e nenhum dos dois tinha receita.

    O log passou a ter teto próprio (`_log_com_teto`, `_LOG_DIAG_TIMEOUT_S`) —
    mas teto não é o mesmo que custo zero: ele ainda pode comer 2s do prazo, e
    quem prova que o sono não é dimensionado por uma folga já consumida é este
    teste. O teto é assunto do `test_log_do_diagnostico_nao_fura_o_prazo`; aqui
    o `_log` gasta 10s do relógio FAKE, que o `asyncio.wait_for` não enxerga.

    CONTROLE NEGATIVO: medir a `folga` ANTES do log (como era) manda dormir o
    backoff cheio (0,375–0,625s) tendo 0,2s de prazo — vermelho aqui."""
    relogio = _RelogioFake()
    monkeypatch.setattr(of_routes.time, "monotonic", relogio.monotonic)
    monkeypatch.setattr(of_routes, "_RECONNECT_DEADLINE_MS", 20000)

    sonos = []

    async def _log(*a, **kw):
        relogio.anda(10.0)               # o log custa 10s do prazo

    async def _dorme(s):
        sonos.append(s)
        relogio.anda(s)

    def _ocupado(uid, remote, item_id, budget_ms=None):
        relogio.anda(9.8)
        return None, False

    monkeypatch.setattr(of_routes, "log_system_event", _log)
    monkeypatch.setattr(of_routes.asyncio, "sleep", _dorme)
    monkeypatch.setattr(of_routes, "_salva_item_sob_lock", _ocupado)

    with pytest.raises(of_routes.HTTPException):
        asyncio.run(of_routes._grava_reconexao(user_id, {"id": "i"}, "item-log"))

    # 20,0 − 9,8 (tentativa) − 10,0 (log) = 0,2s de prazo real quando o sono é
    # decidido. O backoff da 1ª tentativa vale 0,375–0,625s, então o `min` só
    # pode devolver a folga.
    assert sonos and sonos[0] <= 0.21, \
        f"dormiu {sonos[0]:.3f}s com 0,200s de prazo — a folga foi medida antes do log"


def test_prazo_estourado_nem_tenta_de_novo(user_id, monkeypatch):
    """Sem folga, não há segunda tentativa — nem uma 'rapidinha'."""
    relogio = _RelogioFake()
    monkeypatch.setattr(of_routes.time, "monotonic", relogio.monotonic)
    monkeypatch.setattr(of_routes, "_RECONNECT_DEADLINE_MS", 20000)

    chamadas = []

    async def _log(*a, **kw):
        return None

    async def _dorme(s):
        relogio.anda(s)

    monkeypatch.setattr(of_routes, "log_system_event", _log)
    monkeypatch.setattr(of_routes.asyncio, "sleep", _dorme)

    def _come_tudo(uid, remote, item_id, budget_ms=None):
        chamadas.append(budget_ms)
        relogio.anda(30.0)               # estourou o prazo sozinha
        return None, False

    monkeypatch.setattr(of_routes, "_salva_item_sob_lock", _come_tudo)

    with pytest.raises(of_routes.HTTPException):
        asyncio.run(of_routes._grava_reconexao(user_id, {"id": "i"}, "item-estourado"))

    assert len(chamadas) == 1, f"tentou de novo com o prazo vencido: {chamadas}"


def test_lock_livre_grava_na_primeira_sem_esperar_nada(user_id, monkeypatch):
    """CONTROLE POSITIVO: com o lock livre nada muda — grava de primeira, sem
    503, e a tentativa recebe o prazo cheio."""
    relogio = _RelogioFake()
    monkeypatch.setattr(of_routes.time, "monotonic", relogio.monotonic)
    monkeypatch.setattr(of_routes, "_RECONNECT_DEADLINE_MS", 20000)

    vistos = []

    def _livre(uid, remote, item_id, budget_ms=None):
        vistos.append(budget_ms)
        return {"id": 42}, True

    monkeypatch.setattr(of_routes, "_salva_item_sob_lock", _livre)

    conexao = asyncio.run(of_routes._grava_reconexao(user_id, {"id": "i"}, "item-livre"))

    assert conexao == {"id": 42}
    # Metade do prazo, porque a outra metade fica reservada para a 2ª tentativa.
    # O caminho comum não espera nada — o lock está livre e ele volta na hora.
    assert vistos == [10000]


def test_lock_reparte_o_orcamento_entre_a_vaga_e_o_advisory(monkeypatch):
    """O `budget_ms` é o teto TOTAL da aquisição: o que a vaga do semáforo
    consumiu sai do `lock_timeout` do Postgres.

    Sem isso, "teto total" seria teto POR ETAPA e uma chamada custaria 2× — que
    é metade da razão de o POST passar de um minuto."""
    from db import open_finance_state as ofs

    relogio = _RelogioFake()
    executados = []

    class _ConnFake:
        def execute(self, sql, params=None):
            executados.append((sql, params))

        def close(self):
            pass

    esperas = []

    class _VagaLenta:
        def acquire(self, timeout=None):
            esperas.append(timeout)
            relogio.anda(4.0)            # a vaga custou 4s do orçamento
            return True

        def release(self):
            pass

    monkeypatch.setattr(ofs.time, "monotonic", relogio.monotonic)
    monkeypatch.setattr(ofs, "_lock_slots", lambda: _VagaLenta())
    monkeypatch.setattr(ofs.psycopg, "connect", lambda *a, **kw: _ConnFake())
    monkeypatch.setenv("DATABASE_URL", "postgresql://x/y")

    with ofs.pluggy_item_lock("item-orc", budget_ms=10000) as got:
        assert got is True

    timeouts = [p[0] for sql, p in executados if p and "lock_timeout" in sql]
    assert timeouts == ["6000ms"], \
        f"o advisory lock devia herdar o que sobrou (10000-4000), veio {timeouts}"
    # A VAGA também obedece o orçamento — é metade do bug reportado ("até 15s de
    # vaga MAIS 15s de advisory"). Sem esta linha, trocar o `teto_ms` do
    # `acquire` por `_lock_wait_ms()` passava verde (medido).
    assert esperas == [10.0], \
        f"a vaga do semáforo tem de esperar o orçamento, não o teto fixo: {esperas}"


def test_lock_ocupado_de_verdade_ainda_retenta_e_loga(user_id, monkeypatch):
    """O caminho COMPOSTO — prazo × `pluggy_item_lock` real × Postgres real.

    Os outros quatro testes desta seção trocam `_salva_item_sob_lock` por stub
    ou mockam o `psycopg.connect`, e por isso nenhum deles viu o defeito que o
    ataque achou: dando o prazo INTEIRO à primeira tentativa, ela esperava tudo
    no `pg_advisory_lock`, voltava sem folga, e a segunda nunca acontecia —
    `_RECONNECT_LOCK_ATTEMPTS` virava código morto e o `of_reconnect_lock_retry`
    nunca mais era emitido. Ops perde justamente o sinal que separa "ficou
    ocupado mas recuperou" de "desistiu".

    Aqui o lock é segurado por OUTRA conexão, de verdade.

    CONTROLE NEGATIVO: dar o prazo inteiro à 1ª tentativa (tirar a divisão por
    `_RECONNECT_LOCK_ATTEMPTS - tentativa + 1`) deixa este teste vermelho com
    1 tentativa e zero eventos de retry.
    CONTROLE POSITIVO: `test_lock_livre_grava_na_primeira_sem_esperar_nada`
    prova que o caminho comum não paga nada por isto."""
    import os

    from fastapi import HTTPException

    from db.open_finance_state import _lock_key

    item_id = f"item-lock-real-{user_id}"
    eventos = []
    orcamentos = []

    async def _log(level, event_type, *a, **kw):
        eventos.append(event_type)

    monkeypatch.setattr(of_routes, "log_system_event", _log)
    # Prazo curto de propósito: o teste não pode custar 20s. 2s é o piso que
    # ainda deixa a 2ª tentativa acontecer — o backoff (`_backoff_sec`, 375–625ms
    # na 1ª) sai do mesmo bolso, então com 600ms ele comia a metade restante e o
    # teste media o contrário do que quer (medido: 1 tentativa, orçamento 299).
    monkeypatch.setattr(of_routes, "_RECONNECT_DEADLINE_MS", 2000)

    real = of_routes._salva_item_sob_lock

    def _espiao(uid, remote, iid, budget_ms=None):
        orcamentos.append(budget_ms)
        return real(uid, remote, iid, budget_ms)

    monkeypatch.setattr(of_routes, "_salva_item_sob_lock", _espiao)

    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as segurador:
        segurador.execute("select pg_advisory_lock(hashtext(%s))", (_lock_key(item_id),))
        try:
            with pytest.raises(HTTPException) as exc:
                asyncio.run(of_routes._grava_reconexao(user_id, {"id": item_id}, item_id))
        finally:
            segurador.execute("select pg_advisory_unlock(hashtext(%s))",
                              (_lock_key(item_id),))

    assert exc.value.status_code == 503
    assert len(orcamentos) == 2, \
        f"a 2ª tentativa não aconteceu — a 1ª comeu o prazo: {orcamentos}"
    assert "of_reconnect_lock_retry" in eventos, \
        f"o warning de retry sumiu do log: {eventos}"
    assert eventos[-1] == "of_reconnect_lock_timeout"
    assert _prova_lock_livre(item_id), "o lock ficou preso depois do 503"


@pytest.mark.parametrize("valor, esperado, porque", [
    ("0", 20000, "`0` é como se escreve 'desligado' — não pode virar 503 em tudo"),
    ("-5", 20000, "negativo idem"),
    ("500", 20000, "abaixo de 1s não há reconexão possível: só o connect leva mais"),
    ("abc", 20000, "lixo cai no default, como no resto do repo"),
    ("", 20000, "vazio idem"),
    ("30000", 30000, "CONTROLE POSITIVO: valor legítimo é obedecido"),
])
def test_prazo_de_reconexao_recusa_config_sem_sentido(monkeypatch, valor, esperado, porque):
    """A primeira versão usava `max(1, ...)`, e aí `OF_RECONNECT_DEADLINE_MS=0`
    derrubava toda reconexão com um log que culpava o lock.

    CONTROLE NEGATIVO: voltar para `max(1, _env_int(...))` deixa os três
    primeiros casos vermelhos."""
    monkeypatch.setenv("OF_RECONNECT_DEADLINE_MS", valor)
    assert of_routes._prazo_reconexao_ms() == esperado, porque


def test_escrita_da_reconexao_herda_o_que_sobrou_do_prazo(user_id, monkeypatch):
    """O orçamento não para no lock: o que sobra vai para a ESCRITA.

    Sem isto, `save_pluggy_open_finance_item` esperava o pool (até
    `DB_CONNECT_TIMEOUT`, 30s) FORA do prazo prometido — e podia commitar depois
    de o cliente ter desistido (Codex #166, P2).

    CONTROLE NEGATIVO: chamar `save_pluggy_open_finance_item(user_id, remote)`
    sem o `budget_ms` deixa este teste vermelho.
    CONTROLE POSITIVO: o caso `budget_ms=None` prova que quem não pede prazo
    (o sync, os scripts) continua sem nenhum."""
    from contextlib import contextmanager

    recebidos = []
    relogio = _RelogioFake()

    @contextmanager
    def _lock_que_demora(item_id, *, budget_ms=None):
        if budget_ms is not None:
            relogio.anda(3.0)          # o lock comeu 3s do orçamento
        yield True

    monkeypatch.setattr(of_routes.time, "monotonic", relogio.monotonic)
    monkeypatch.setattr(of_routes, "pluggy_item_lock", _lock_que_demora)
    monkeypatch.setattr(of_routes, "save_pluggy_open_finance_item",
                        lambda uid, remote, **kw: recebidos.append(kw.get("budget_ms")) or {"id": 9})

    of_routes._salva_item_sob_lock(user_id, {"id": "i"}, "item-esc", 10000)
    of_routes._salva_item_sob_lock(user_id, {"id": "i"}, "item-esc", None)

    assert recebidos[0] == 7000, \
        f"a escrita devia herdar 10000-3000, veio {recebidos[0]}"
    assert recebidos[1] is None, "sem prazo pedido, a escrita não ganha teto"


def test_gravar_reconexao_usa_UMA_conexao_do_pool(user_id, monkeypatch):
    """`ensure_user` abria a própria conexão antes do upsert: duas aquisições,
    cada uma podendo esperar `DB_CONNECT_TIMEOUT` fora do prazo. Agora é
    `ensure_user_tx` na MESMA transação — metade das esperas, e o par virou
    atômico.

    CONTROLE NEGATIVO: voltar a chamar `ensure_user(user_id)` antes do `with`
    deixa a contagem em 2."""
    import db.open_finance as ofdb
    import db.users as usersdb

    aberturas = []
    real = ofdb.get_conn

    def _espiao(timeout=None):
        aberturas.append(timeout)
        return real(timeout=timeout)

    # Os DOIS módulos: `db/users.py` faz `from .connection import get_conn`, e o
    # nome já está ligado lá — espionar só o `db.open_finance` deixava o
    # `ensure_user` invisível, e o controle negativo passava verde (medido).
    monkeypatch.setattr(ofdb, "get_conn", _espiao)
    monkeypatch.setattr(usersdb, "get_conn", _espiao)

    ofdb.save_pluggy_open_finance_item(
        user_id, {"id": f"item-1conn-{user_id}", "status": "UPDATED",
                  "connector": {"id": 612, "name": "Nubank"}},
        budget_ms=5000)

    assert len(aberturas) == 1, f"a escrita pegou {len(aberturas)} conexões do pool"
    assert aberturas == [5.0], f"o prazo tem de chegar ao pool: {aberturas}"


# ── Codex #166, 3 apontamentos de uma vez: etapa sequencial não pode ganhar o
# orçamento de novo ────────────────────────────────────────────────────────────
# Mesma classe, três lugares. Um teto "total" que cada etapa reinicia não é teto:
# duas etapas em sequência somam o dobro. Eu tinha enumerado as esperas da ROTA e
# não as de DENTRO de cada função — o `connect` antes do advisory, e o pool antes
# da query.

def test_advisory_lock_desconta_o_tempo_do_connect(monkeypatch):
    """O `lock_timeout` sai do que sobrou DEPOIS do connect, não do que havia
    antes dele.

    CONTROLE NEGATIVO: não recontar (usar o `restante_ms` pré-connect) devolve
    `9000ms` em vez de `6000ms` — a soma das duas etapas quase dobra a cota."""
    from db import open_finance_state as ofs

    relogio = _RelogioFake()
    executados = []

    class _ConnFake:
        def execute(self, sql, params=None):
            executados.append((sql, params))

        def close(self):
            pass

    class _Vaga:
        def acquire(self, timeout=None):
            relogio.anda(1.0)            # a vaga levou 1s
            return True

        def release(self):
            pass

    def _connect_lento(*a, **kw):
        relogio.anda(3.0)                # o connect levou mais 3s
        return _ConnFake()

    monkeypatch.setattr(ofs.time, "monotonic", relogio.monotonic)
    monkeypatch.setattr(ofs, "_lock_slots", lambda: _Vaga())
    monkeypatch.setattr(ofs.psycopg, "connect", _connect_lento)
    monkeypatch.setenv("DATABASE_URL", "postgresql://x/y")

    with ofs.pluggy_item_lock("item-reconta", budget_ms=10000) as got:
        assert got is True

    timeouts = [p[0] for sql, p in executados if p and "lock_timeout" in sql]
    assert timeouts == ["6000ms"], \
        f"10000 − 1000 (vaga) − 3000 (connect) = 6000; veio {timeouts}"


def test_connect_que_come_o_orcamento_nao_tenta_o_advisory(monkeypatch):
    """Se o connect esgota a cota, não sobra lock para pedir — e pedir com
    `0ms` seria espera INFINITA no Postgres."""
    from db import open_finance_state as ofs

    relogio = _RelogioFake()
    executados = []
    fechou = []

    class _ConnFake:
        def execute(self, sql, params=None):
            executados.append(sql)

        def close(self):
            fechou.append(True)

    vagas = 0

    class _Vaga:
        def acquire(self, timeout=None):
            return True

        def release(self):
            nonlocal vagas
            vagas += 1

    def _connect_lentissimo(*a, **kw):
        relogio.anda(9.0)
        return _ConnFake()

    monkeypatch.setattr(ofs.time, "monotonic", relogio.monotonic)
    monkeypatch.setattr(ofs, "_lock_slots", lambda: _Vaga())
    monkeypatch.setattr(ofs.psycopg, "connect", _connect_lentissimo)
    monkeypatch.setenv("DATABASE_URL", "postgresql://x/y")

    with ofs.pluggy_item_lock("item-sem-tempo", budget_ms=5000) as got:
        assert got is False

    assert executados == [], f"não podia nem configurar o lock: {executados}"
    # UMA vez. A primeira versão fechava à mão E deixava o `finally` repetir —
    # o `close` duplo é inócuo, mas o `Semaphore.release()` que vinha junto
    # AUMENTA o contador e afrouxa o teto de conexões dedicadas para sempre.
    assert fechou == [True], f"a conexão foi fechada {len(fechou)}× : {fechou}"
    assert vagas == 1, f"o semáforo foi liberado {vagas}× — release duplo vaza vaga"


def test_statement_timeout_desconta_a_espera_do_pool(user_id, monkeypatch):
    """O `statement_timeout` sai do que sobrou DEPOIS do pool.

    CONTROLE NEGATIVO: não recontar manda `5000ms` em vez de `3000ms` — as duas
    esperas somariam quase o dobro da cota."""
    from contextlib import contextmanager

    import db.open_finance as ofdb
    import db.users as usersdb

    relogio = _RelogioFake()
    real = ofdb.get_conn
    vistos = []
    ordem = []

    class _CursorEspiao:
        def __init__(self, cur):
            self._cur = cur

        def execute(self, sql, params=None):
            if params and "statement_timeout" in str(sql):
                vistos.append(params[0])
                ordem.append("TETO")
            else:
                ordem.append(" ".join(str(sql).split())[:40])
            return self._cur.execute(sql, params)

        def __getattr__(self, nome):
            return getattr(self._cur, nome)

        def __enter__(self):
            self._cur.__enter__()
            return self

        def __exit__(self, *a):
            return self._cur.__exit__(*a)

    class _ConnEspiao:
        def __init__(self, conn):
            self._conn = conn

        def cursor(self, *a, **kw):
            return _CursorEspiao(self._conn.cursor(*a, **kw))

        def __getattr__(self, nome):
            return getattr(self._conn, nome)

    @contextmanager
    def _pool_lento(timeout=None):
        relogio.anda(2.0)                # o pool levou 2s do orçamento
        with real() as conn:
            yield _ConnEspiao(conn)

    monkeypatch.setattr(ofdb, "monotonic", relogio.monotonic)
    monkeypatch.setattr(ofdb, "get_conn", _pool_lento)
    monkeypatch.setattr(usersdb, "get_conn", _pool_lento)

    ofdb.save_pluggy_open_finance_item(
        user_id, {"id": f"item-stmt-{user_id}", "status": "UPDATED",
                  "connector": {"id": 612, "name": "Nubank"}},
        budget_ms=5000)

    # O `statement_timeout` do Postgres vale por STATEMENT, não pela transação.
    # A invariante é INTERCALAÇÃO, não contagem: TODO statement de verdade tem
    # de vir logo depois de um reajuste. Asserção por número (`len == 2`) dava
    # verde numa versão que reajustava só 2 dos 3 statements, porque os dois
    # inserts do `ensure_user_tx` moram em `db/users.py` e o reajuste daqui não
    # os alcançava (Codex do @hiago no #166).
    #
    # CONTROLE NEGATIVO: trocar o `_CursorComTeto` por chamadas soltas de
    # reajuste deixa `insert into accounts` sem "TETO" antes — vermelho aqui.
    reais = [(i, sql) for i, sql in enumerate(ordem) if sql != "TETO"]
    sem_teto = [sql for i, sql in reais if i == 0 or ordem[i - 1] != "TETO"]
    assert not sem_teto, \
        f"statement sem reajuste imediatamente antes: {sem_teto}\nordem: {ordem}"
    assert len(reais) == 3, \
        f"esperado users+accounts+upsert; veio {[s for _, s in reais]}"
    assert set(vistos) == {"3000ms"}, \
        f"5000 − 2000 (espera do pool) = 3000; veio {vistos}"


@pytest.mark.parametrize("erro", [
    "pool",
    "query",
    "connect",
    "commit",
])
def test_teto_da_escrita_estourado_vira_503_e_nao_500(user_id, monkeypatch, erro):
    """"Não deu tempo" tem UM desfecho, por qualquer porta: 503 recuperável.

    As quatro portas são a mesma categoria — `psycopg.OperationalError` — e as
    duas últimas escapavam como 500 porque o `try` antigo cobria só a chamada do
    `save_`, e não o `with pluggy_item_lock(...)` acima dela:

      • `pool`    — `PoolTimeout`, a vaga do pool não veio;
      • `query`   — `QueryCanceled`, o `statement_timeout` cortou a escrita;
      • `connect` — `ConnectionTimeout` do `psycopg.connect` DEDICADO do
        `pluggy_item_lock` (verificado à mão: com o `try` antigo, 500);
      • `commit`  — o commit estoura como `OperationalError`; ele fica fora do
        `statement_timeout` por limitação do Postgres (ver `_CursorComTeto`),
        então o desfecho é a única coisa que dá para garantir.

    O `except` mora no `_grava_reconexao`, em volta do `await to_thread(...)` —
    não dentro do `_salva_item_sob_lock`. É lá que existe o `log_system_event`
    para dizer QUAL foi o erro (ver
    `test_log_diz_QUAL_falha_e_nao_so_lock_ocupado`).

    CONTROLE NEGATIVO (medido): restaurar
    `except (PoolTimeout, psycopg.errors.QueryCanceled)` no lugar antigo (dentro
    do `_salva_item_sob_lock`, só em volta do `save_`) → `connect` e `commit`
    vermelhos, subindo como `ConnectionTimeout`/`OperationalError` em vez de
    503; `pool` e `query` seguem verdes, que é o que os torna incapazes de
    discriminar sozinhos.
    CONTROLE POSITIVO: `test_erro_de_bug_na_escrita_nao_vira_503` — o `except`
    NÃO pode transformar tudo em 503."""
    from contextlib import contextmanager

    import psycopg
    from fastapi import HTTPException
    from psycopg_pool import PoolTimeout

    from db.open_finance_state import _lock_key  # noqa: F401  (documenta a origem)

    @contextmanager
    def _lock(item_id, *, budget_ms=None):
        if erro == "connect":
            # O `psycopg.connect` da conexão dedicada estourando o
            # `connect_timeout` — dentro do `with`, fora do `try` antigo.
            raise psycopg.errors.ConnectionTimeout("connect timeout")
        yield True

    _ERROS = {
        "pool": lambda: PoolTimeout("pool cheio"),
        "query": lambda: psycopg.errors.QueryCanceled("statement timeout"),
        "commit": lambda: psycopg.OperationalError("commit não voltou"),
    }

    def _estoura(uid, remote, **kw):
        raise _ERROS[erro]()

    async def _log(*a, **kw):
        return None

    async def _dorme(_s):
        return None

    monkeypatch.setattr(of_routes, "pluggy_item_lock", _lock)
    monkeypatch.setattr(of_routes, "save_pluggy_open_finance_item", _estoura)
    monkeypatch.setattr(of_routes, "log_system_event", _log)
    monkeypatch.setattr(of_routes.asyncio, "sleep", _dorme)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(of_routes._grava_reconexao(user_id, {"id": "i"}, "item-503"))

    assert exc.value.status_code == 503
    assert "tente de novo" in str(exc.value.detail).lower()


@pytest.mark.parametrize("erro, esperado", [
    (lambda: ValueError("meta ou caixinha inválida"), 400),
    (lambda: psycopg.errors.UniqueViolation("uq_of_conn_provider_item"), 500),
    (lambda: psycopg.ProgrammingError('column "x" does not exist'), 500),
])
def test_erro_de_bug_na_escrita_nao_vira_503(user_id, monkeypatch, erro, esperado):
    """CONTROLE POSITIVO do grupo: o `except` da categoria não pode engolir BUG.

    Sem esta prova, o grupo acima passaria num código que transforma tudo em 503
    — pior que o defeito, porque o usuário retentaria para sempre um erro que
    nunca vai passar, e o log de erro sumiria.

    A fronteira é a hierarquia do psycopg, medida: `UniqueViolation`,
    `ProgrammingError` e `ValueError` NÃO são `psycopg.OperationalError`. Por
    isso continuam subindo — `ValueError` vira o 400 da rota
    (open_finance.py:855) e os outros dois o 500 de sempre.

    CONTROLE NEGATIVO: trocar o `except psycopg.OperationalError` do
    `_grava_reconexao` por `except psycopg.Error` → os dois casos de psycopg
    viram 503 aqui (o de `ValueError` continua 400, e é por isso que ele sozinho
    não discrimina)."""
    def _estoura(uid, remote, **kw):
        raise erro()

    async def _log(*a, **kw):
        return None

    monkeypatch.setattr(
        of_routes, "get_pluggy_item",
        lambda item_id, api_key=None: {
            "id": item_id, "status": "UPDATED", "clientUserId": str(user_id),
            "connector": {"id": 612, "name": "Nubank"}})
    monkeypatch.setattr(of_routes, "save_pluggy_open_finance_item", _estoura)
    monkeypatch.setattr(of_routes, "log_system_event", _log)
    monkeypatch.setattr(of_routes, "_schedule_pluggy_sync", lambda item_id: None)

    client = TestClient(dashboard.app, raise_server_exceptions=False)
    client.cookies.set(dashboard.AUTH_COOKIE_NAME, dashboard._make_jwt(user_id, "of@t.com"))
    client.cookies.set(dashboard.DASHBOARD_COOKIE_NAME,
                       dashboard.make_dashboard_token(user_id, hours=1))
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, "test-csrf-token")

    resp = client.post(
        f"/open-finance/{user_id}/pluggy-item",
        json={"item": {"id": f"item-bug-{user_id}"}},
        headers={dashboard.CSRF_HEADER_NAME: "test-csrf-token",
                 "Content-Type": "application/json"},
    )

    assert resp.status_code == esperado, resp.text


def test_conexao_dedicada_leva_statement_timeout_so_com_orcamento(monkeypatch):
    """A conexão dedicada do lock ganha `statement_timeout` — e ele é RECONTADO
    depois do connect, igual ao `lock_timeout`.

    O `options` do connect é parâmetro de STARTUP: só existe antes do connect e
    carrega o `restante_ms` PRÉ-connect. Sozinho, ele repetia o erro que a
    recontagem do `lock_timeout` já tinha consertado uma linha abaixo — connect
    que come quase a cota deixava a etapa seguinte com o orçamento INTEIRO de
    teto, e a etapa somava quase o DOBRO da cota. Por isso o `set_config`
    reescreve os dois com o valor recontado.

    O relógio anda de propósito (vaga 1s + connect 3s numa cota de 10s): com o
    `psycopg.connect` de custo ZERO da versão anterior deste teste, o valor
    pré-connect e o recontado eram o MESMO número e o teste não discriminava
    nada. Mesmo padrão do irmão `test_advisory_lock_desconta_o_tempo_do_connect` (:995).

    CONTROLE NEGATIVO: tirar o `set_config('statement_timeout', ...)` (deixando
    só o do `options`) → vermelho na asserção dos `9000ms` vs `6000ms`.
    CONTROLE POSITIVO na terceira parte: SEM `budget_ms` (o caminho do sync, que
    não tem cliente HTTP esperando) nada muda — nem `options`, nem
    `connect_timeout`, nem `statement_timeout` no `set_config`."""
    from db import open_finance_state as ofs

    relogio = _RelogioFake()
    kwargs: list[dict] = []
    executados: list[tuple] = []

    class _ConnFake:
        def execute(self, sql, params=None):
            executados.append((sql, params))

        def close(self):
            pass

    class _Vaga:
        def acquire(self, timeout=None):
            relogio.anda(1.0)            # a vaga levou 1s
            return True

        def release(self):
            pass

    def _connect_lento(*a, **kw):
        kwargs.append(kw)
        relogio.anda(3.0)                # o connect levou mais 3s
        return _ConnFake()

    monkeypatch.setattr(ofs.time, "monotonic", relogio.monotonic)
    monkeypatch.setattr(ofs, "_lock_slots", lambda: _Vaga())
    monkeypatch.setattr(ofs.psycopg, "connect", _connect_lento)
    monkeypatch.setenv("DATABASE_URL", "postgresql://x/y")

    with ofs.pluggy_item_lock("item-opts", budget_ms=10000) as got:
        assert got is True

    # 10000 − 1000 (vaga) = 9000 no instante do connect. É o que o `options`
    # PODE saber, e o `connect_timeout` arredonda para segundos inteiros.
    assert kwargs[0].get("options") == "-c statement_timeout=9000ms", kwargs[0]
    assert kwargs[0].get("connect_timeout") == 9, kwargs[0]

    # 10000 − 1000 (vaga) − 3000 (connect) = 6000 depois do connect. Os DOIS
    # tetos têm de valer isto — 9000 aqui é o bug que este teste existe para
    # pegar (a etapa somaria 9000 + 3000 numa cota de 10000).
    cfg = [(sql, p) for sql, p in executados if p and "set_config" in sql]
    assert len(cfg) == 1, executados
    sql, params = cfg[0]
    assert "lock_timeout" in sql and "statement_timeout" in sql, sql
    assert list(params) == ["6000ms", "6000ms"], params

    executados.clear()
    with ofs.pluggy_item_lock("item-sem-orc") as got:
        assert got is True
    assert "options" not in kwargs[1] and "connect_timeout" not in kwargs[1], \
        f"o caminho do sync não pode mudar: {kwargs[1]}"
    assert all("statement_timeout" not in sql for sql, _ in executados), \
        f"o SQL do sync tem de ficar byte a byte o de antes: {executados}"


@pytest.mark.parametrize("modo, erro_esperado, msg_esperada", [
    ("infra", "TooManyConnections", "TooManyConnections"),
    ("lock", None, "lock do item ocupado"),
])
def test_log_diz_QUAL_falha_e_nao_so_lock_ocupado(
        user_id, monkeypatch, modo, erro_esperado, msg_esperada):
    """503 tem DUAS causas e os logs têm de separá-las.

    Engolir `psycopg.OperationalError` dentro do `_salva_item_sob_lock` devolvia
    `(None, False)`, que o `_grava_reconexao` só sabe ler como "lock ocupado":
    DiskFull, InvalidPassword, TooManyConnections, AdminShutdown — a categoria
    inteira — saía com o MESMO texto, e o tipo não aparecia nem no `details`. É o
    diagnóstico falso que o docstring do `_prazo_reconexao_ms` já registrou uma
    vez.

    CONTROLE NEGATIVO (o que este teste realmente discrimina): apagar a
    atribuição `causa = f"{type(exc).__name__}: {exc}"` do `except` do
    `_grava_reconexao` → o caso `infra` fica vermelho nos dois logs (volta a
    dizer "lock do item ocupado" com `erro: None`). "Voltar a capturar dentro do
    `_salva_item_sob_lock`" NÃO é executável aqui: este teste stuba
    `_salva_item_sob_lock` inteiro (abaixo), então o `except` de lá nem existe no
    caminho — quem cobre a porta de dentro é
    `test_teto_da_escrita_estourado_vira_503_e_nao_500`.
    CONTROLE POSITIVO: o caso `lock` — lock genuinamente ocupado continua
    dizendo "lock do item ocupado" e `erro=None`, senão o conserto seria só
    trocar uma mentira por outra.

    Cobre também o campo do log (achado 4): o `restante_ms` do retry era medido
    ANTES do `log_system_event` que gasta prazo, e chegava ao banco vencido
    (`10000` valendo `-20000`). O nome agora diz o que o número é.
    CONTROLE NEGATIVO: manter a chave `restante_ms` → vermelho nos dois casos."""
    eventos = []

    async def _log(level, event_type, msg, **kw):
        eventos.append((event_type, msg, kw.get("details") or {}))

    async def _dorme(_s):
        return None

    def _falha(uid, remote, item_id, budget_ms=None):
        if modo == "infra":
            raise psycopg.errors.TooManyConnections("sorry, too many clients already")
        return None, False

    monkeypatch.setattr(of_routes, "log_system_event", _log)
    monkeypatch.setattr(of_routes.asyncio, "sleep", _dorme)
    monkeypatch.setattr(of_routes, "_salva_item_sob_lock", _falha)

    with pytest.raises(of_routes.HTTPException) as exc:
        asyncio.run(of_routes._grava_reconexao(user_id, {"id": "i"}, "item-diag"))

    assert exc.value.status_code == 503
    assert [e for e, _, _ in eventos] == [
        "of_reconnect_lock_retry", "of_reconnect_lock_timeout"], eventos

    for evento, msg, det in eventos:
        assert msg_esperada in msg, f"{evento}: {msg}"
        if erro_esperado is None:
            assert det.get("erro") is None, f"{evento}: {det}"
        else:
            assert (det.get("erro") or "").startswith(erro_esperado + ":"), \
                f"{evento} não identifica o erro: {det}"
            assert "lock do item ocupado" not in msg, \
                f"{evento} mente sobre a causa: {msg}"

    retry = eventos[0][2]
    assert "restante_ms" not in retry, \
        f"o número vencido não pode voltar com o nome antigo: {retry}"
    assert "restante_ms_antes_do_log" in retry, retry


def test_causa_e_a_da_ultima_tentativa(user_id, monkeypatch):
    """Infra na 1ª tentativa + lock ocupado na 2ª: o log FINAL diz "lock", não a
    infra — e a infra fica registrada no log do retry.

    É o comportamento documentado no `except` do `_grava_reconexao` ("`causa` é a
    da ÚLTIMA tentativa, de propósito"), e sem este teste era só comentário. O
    preço aparece aqui: quem lê SÓ o `of_reconnect_lock_timeout` não vê o
    `TooManyConnections` que aconteceu antes.

    CONTROLE NEGATIVO: tirar o `causa = None` do caminho de sucesso do `try` (a
    linha logo depois do `to_thread`) → a infra da 1ª tentativa gruda e o log
    final passa a acusar `TooManyConnections`, vermelho na última asserção."""
    eventos = []
    tentativas = []

    async def _log(level, event_type, msg, **kw):
        eventos.append((event_type, msg, kw.get("details") or {}))

    async def _dorme(_s):
        return None

    def _falha(uid, remote, item_id, budget_ms=None):
        tentativas.append(item_id)
        if len(tentativas) == 1:
            raise psycopg.errors.TooManyConnections("sorry, too many clients already")
        return None, False          # 2ª: lock genuinamente ocupado

    monkeypatch.setattr(of_routes, "log_system_event", _log)
    monkeypatch.setattr(of_routes.asyncio, "sleep", _dorme)
    monkeypatch.setattr(of_routes, "_salva_item_sob_lock", _falha)

    with pytest.raises(of_routes.HTTPException) as exc:
        asyncio.run(of_routes._grava_reconexao(user_id, {"id": "i"}, "item-mix"))

    assert exc.value.status_code == 503
    assert len(tentativas) == 2, tentativas

    retry, final = eventos[0], eventos[1]
    assert "TooManyConnections" in (retry[2].get("erro") or ""), retry
    assert final[2].get("erro") is None, \
        f"a causa da 2ª tentativa é lock ocupado, não a infra da 1ª: {final}"
    assert "lock do item ocupado" in final[1], final


def test_causa_sobrevive_ao_log_system_event_que_nao_grava(user_id, monkeypatch, caplog):
    """O canal que diagnostica o 503 não pode depender do banco que caiu.

    `log_system_event` (core/admin_dashboard.py:190-201) abre conexão NOVA para
    gravar e engole TODA exceção com um `print` que não carrega nem `message` nem
    `details`. Na família "o banco recusa conexão" — `TooManyConnections`,
    `DiskFull`, `AdminShutdown`, `InvalidPassword` — a conexão do log é
    EXATAMENTE o que está quebrado, então `system_event_logs` fica vazio e a
    `causa` sumiria. Que é a família que o `test_log_diz_QUAL_falha_...` usa como
    exemplar: lá o `_log` stub SEMPRE grava, então ele prova composição, não
    gravação. Aqui o stub modela o desfecho real (engole e não grava nada).

    CONTROLE NEGATIVO: apagar o `logging.getLogger(__name__).warning(...)` do
    `_grava_reconexao` → vermelho na asserção do `caplog`, porque não sobra canal
    nenhum com a causa.
    CONTROLE POSITIVO: o 503 continua saindo com o log mudo — o diagnóstico não
    pode virar um segundo modo de falha."""
    import logging as _logging

    async def _log_que_engole(*a, **kw):
        # Desfecho de produção sob banco fora: a exceção morre dentro da
        # `log_system_event` (except Exception + print), o fluxo segue, e
        # `system_event_logs` NÃO recebe linha nenhuma.
        return None

    async def _dorme(_s):
        return None

    def _falha(uid, remote, item_id, budget_ms=None):
        raise psycopg.errors.TooManyConnections("sorry, too many clients already")

    monkeypatch.setattr(of_routes, "log_system_event", _log_que_engole)
    monkeypatch.setattr(of_routes.asyncio, "sleep", _dorme)
    monkeypatch.setattr(of_routes, "_salva_item_sob_lock", _falha)

    with caplog.at_level(_logging.WARNING, logger="frontend.routes.open_finance"):
        with pytest.raises(of_routes.HTTPException) as exc:
            asyncio.run(of_routes._grava_reconexao(user_id, {"id": "i"}, "item-mudo"))

    assert exc.value.status_code == 503          # CONTROLE POSITIVO

    texto = "\n".join(r.getMessage() for r in caplog.records)
    assert "item-mudo" in texto and "TooManyConnections" in texto, \
        f"a causa sumiu junto com o banco: {texto!r}"


def test_infra_na_1a_que_some_na_2a_ainda_deixa_rastro(user_id, monkeypatch, caplog):
    """O caso que o log FINAL não cobre, porque ele não acontece.

    Infra na 1ª tentativa + sucesso na 2ª → a rota devolve 200 e o
    `of_reconnect_lock_timeout` NUNCA roda. O único portador da causa da 1ª é o
    `of_reconnect_lock_retry` — e é justamente ele que, na família "o banco
    recusa conexão", o `log_system_event` não consegue gravar. Sem o canal local
    no ramo do retry, um `TooManyConnections` que some na tentativa seguinte
    desaparece por completo: o usuário recebe 200 e ops nunca soube que o banco
    piscou.

    CONTROLE NEGATIVO: apagar o `logging.getLogger(__name__).warning(...)` do
    ramo do retry (o `if tentativa < _RECONNECT_LOCK_ATTEMPTS`) → vermelho aqui,
    e o `test_causa_sobrevive_ao_log_system_event_que_nao_grava` continua VERDE,
    porque lá as duas tentativas falham e o log final salva o diagnóstico.
    CONTROLE POSITIVO: o 200 continua saindo — o rastro não pode custar o
    caminho de sucesso."""
    import logging as _logging

    async def _log_que_engole(*a, **kw):
        return None

    async def _dorme(_s):
        return None

    tentativas = {"n": 0}

    def _falha_depois_grava(uid, remote, item_id, budget_ms=None):
        tentativas["n"] += 1
        if tentativas["n"] == 1:
            raise psycopg.errors.TooManyConnections("sorry, too many clients already")
        # `_salva_item_sob_lock` devolve (connection, sob_lock) — a 2ª entra.
        return {"id": 1, "provider_item_id": item_id}, True

    monkeypatch.setattr(of_routes, "log_system_event", _log_que_engole)
    monkeypatch.setattr(of_routes.asyncio, "sleep", _dorme)
    monkeypatch.setattr(of_routes, "_salva_item_sob_lock", _falha_depois_grava)

    with caplog.at_level(_logging.WARNING, logger="frontend.routes.open_finance"):
        conexao = asyncio.run(
            of_routes._grava_reconexao(user_id, {"id": "i"}, "item-piscou"))

    assert conexao["provider_item_id"] == "item-piscou"   # CONTROLE POSITIVO
    assert tentativas["n"] == 2, "a 2ª tentativa tem de ter acontecido"

    texto = "\n".join(r.getMessage() for r in caplog.records)
    assert "item-piscou" in texto and "TooManyConnections" in texto, \
        f"a infra da 1ª tentativa sumiu sem deixar rastro: {texto!r}"


def test_log_do_diagnostico_nao_fura_o_prazo(user_id, monkeypatch, caplog):
    """Banco que aceita a conexão do log e trava no INSERT não pendura o 503.

    `log_system_event` (`core/admin_dashboard.py:180`) não tem
    `statement_timeout`: o `connect_timeout` limita o handshake e nada limita o
    INSERT nem o commit. Sem o `_log_com_teto`, os DOIS logs
    (`of_reconnect_lock_retry` e `of_reconnect_lock_timeout`) eram aguardados
    síncronos, e o teto anunciado de 20s continuava ILIMITADO exatamente sob
    sobrecarga do banco — que é quando o teto importa.

    Relógio REAL de propósito: o `asyncio.wait_for` mede pelo loop, então um
    `_RelogioFake` não exercitaria nada. Por isso os números são pequenos —
    prazo de 0,3s e teto de log de 0,05s.

    CONTROLE NEGATIVO: tirar o `asyncio.wait_for` do `_log_com_teto` → os dois
    logs dormem 5s cada e a asserção de tempo fica vermelha (o teste passa a
    levar ~10s).
    CONTROLE POSITIVO: o 503 continua saindo E o `caplog` continua com a causa —
    o `logging.warning` local roda ANTES do log com teto, então o diagnóstico
    sobrevive ao log que estourou. Sem isso, limitar o log teria trocado
    lentidão por cegueira."""
    import logging as _logging

    monkeypatch.setattr(of_routes, "_RECONNECT_DEADLINE_MS", 300)
    monkeypatch.setattr(of_routes, "_LOG_DIAG_TIMEOUT_S", 0.05)

    logs = []

    async def _log_travado(*a, **kw):
        logs.append(a[1] if len(a) > 1 else None)
        await asyncio.sleep(5)          # INSERT que não volta

    def _falha(uid, remote, item_id, budget_ms=None):
        raise psycopg.errors.TooManyConnections("sorry, too many clients already")

    monkeypatch.setattr(of_routes, "log_system_event", _log_travado)
    monkeypatch.setattr(of_routes, "_salva_item_sob_lock", _falha)

    t0 = time.monotonic()
    with caplog.at_level(_logging.WARNING, logger="frontend.routes.open_finance"):
        with pytest.raises(of_routes.HTTPException) as exc:
            asyncio.run(of_routes._grava_reconexao(user_id, {"id": "i"}, "item-log-preso"))
    gasto = time.monotonic() - t0

    assert exc.value.status_code == 503                       # CONTROLE POSITIVO
    assert logs == ["of_reconnect_lock_retry", "of_reconnect_lock_timeout"], \
        f"os dois logs têm de ser TENTADOS, não pulados: {logs}"
    # 0,3s de prazo + 0,05s do log final. Folga de 3× para máquina carregada;
    # sem o `wait_for` seriam ~10s, então a margem não come o sinal.
    assert gasto < 1.0, f"o 503 levou {gasto:.2f}s — os logs furaram o prazo"

    texto = "\n".join(r.getMessage() for r in caplog.records)
    assert "item-log-preso" in texto and "TooManyConnections" in texto, \
        f"o log estourou e levou a causa junto: {texto!r}"
