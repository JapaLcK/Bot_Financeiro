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
import threading
import time
from datetime import date
from decimal import Decimal

import psycopg
import pytest

import db
import core.services.pluggy_sync as ps
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

    def _lock_ocupado(item_id):
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
    def _lock_espiao(item_id):
        ordem.append(("enter", item_id))
        try:
            yield True
        finally:
            ordem.append(("exit", item_id))

    monkeypatch.setattr(of_routes, "pluggy_item_lock", _lock_espiao)
    monkeypatch.setattr(of_routes, "save_pluggy_open_finance_item",
                        lambda uid, remote: ordem.append(("save", uid)) or {"id": 1})

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
    def _lock_ocupado(item_id):
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

    def _libera_na_segunda(uid, remote, item_id, tinha_conexao_propria=False):
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
                        lambda uid, remote, item_id, tinha_conexao_propria=False: (None, False))

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
