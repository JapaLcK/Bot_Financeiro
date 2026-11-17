"""#1413 (mesma classe do #1260): conexões do pool por execução do dreno do Pix.

A reserva (`reservar_evento`) segura a conexão A pelo dreno inteiro. O `lock_efeito`
abria OUTRA (B) e a segurava durante o efeito e o HTTP dele, e as transitórias (C)
entravam por cima: 3 por dreno, 4 no UNDONE (duas travas aninhadas). Com 4 drenos ao
mesmo tempo, o pool de 8 esgotava (`PoolTimeout`). A trava agora vai em A.

Banco real, pelo `drenar_evento`; só o mundo externo é mockado.

CONTROLES:
  * negativo: `lock_efeito` de volta a `get_conn()` próprio (ignorando a reserva) deixa
    `test_no_maximo_duas_conexoes_por_dreno` (todos os caminhos menos o `d3`, que já
    usava 2) e `test_quatro_drenos_simultaneos_cabem_num_pool_de_cinco` vermelhos;
  * negativo: sem o `unlock` na conexão da reserva, `test_efeito_que_levanta_*` fica
    vermelho; sem fechar a reserva quando o `unlock` falha, `test_deadlock_*` fica vermelho
    (a trava sobra no pool), e relevantando o erro do `unlock` também (o `last_error` vira
    `InFailedSqlTransaction`); fechando sem relançar quando nada está em voo,
    `test_unlock_que_falha_*` fica vermelho; sem o `reset` do `ContextVar`,
    `test_contextvar_*` fica vermelho; com `with reserva` no lugar do `nullcontext`
    (commit em A),
    `test_reserva_segue_travada_*` fica vermelho;
  * positivo: D5-a e D5-b (`tests/test_pix_efeito_concorrente.py`) seguem verdes — a
    trava continua serializando o mesmo par e deixando pagamentos diferentes correrem.

CEGUEIRA: nenhum teste mede QUANDO a trava solta. Com `pg_advisory_xact_lock` sem `unlock`
(cada trava presa até o fim da reserva, e não do efeito), só
`test_unlock_que_falha_sem_erro_em_voo_sobe` fica vermelho, e por acaso: some o `unlock` que
ele precisa ver falhar, não a trava presa; threads de um processo só; o resíduo (o pool
dividido com as rotas, `DB_POOL_MAX_SYNC − N` para N drenos) não é testado aqui — quem fecha
é o teto de drenos simultâneos.
"""
from __future__ import annotations

import threading
import time
import uuid

import psycopg
import pytest

import core.services.pix_drain_effects as ef
from _billing_grants_helpers import conta, garantir_system_event_logs
from _dreno_pix_helpers import entregar, evento, mundo_externo, nova_cobranca
from core.services.pix_drain import drenar_evento
from db.connection import _get_pool, get_conn
from db.webhook_outbox import registrar_evento

UNDONE = "PAYMENT_RECEIVED_IN_CASH_UNDONE"


@pytest.fixture()
def externo(monkeypatch):
    garantir_system_event_logs()
    return mundo_externo(monkeypatch)


def _nova(uid):
    conta(uid, "free", None)
    return nova_cobranca(uid)


def _depois(tipo):
    def montar(uid):
        cob = _nova(uid)
        entregar(tipo, cob)
        return cob
    return montar


def _orfa(uid):
    cob = _nova(uid)
    with get_conn() as conn:
        conn.execute("update pix_charges set user_id = null where id = %s", (cob["id"],))
    return cob


# caminho: (montagem, evento medido, kwargs do `entregar`); todos fecham o evento medido.
CAMINHOS = {
    "received": (_nova, "PAYMENT_RECEIVED", {}),
    "refunded": (_depois("PAYMENT_RECEIVED"), "PAYMENT_REFUNDED", {}),
    "parcial": (_depois("PAYMENT_RECEIVED"), "PAYMENT_PARTIALLY_REFUNDED", {}),
    "valor_retido": (_nova, "PAYMENT_RECEIVED", {"valor": 0.01}),
    "orfao": (_orfa, "PAYMENT_RECEIVED", {}),
    "undone": (_depois("PAYMENT_RECEIVED"), UNDONE, {}),
    "d3": (_depois("PAYMENT_REFUNDED"), "PAYMENT_RECEIVED", {}),
}


@pytest.mark.parametrize("caminho", list(CAMINHOS))
def test_no_maximo_duas_conexoes_por_dreno(caminho, user_id, externo, niveis_de_conexao):
    montar, tipo, kw = CAMINHOS[caminho]
    cob = montar(user_id)
    del niveis_de_conexao[:]
    eid = entregar(tipo, cob, **kw)
    assert evento(eid)["processed_at"] is not None, evento(eid)["last_error"]
    assert max(niveis_de_conexao) + 1 <= 2, f"{max(niveis_de_conexao) + 1} conexões ao mesmo tempo"


def _enfileirar(cob, tipo="PAYMENT_RECEIVED"):
    eid = f"evt_{uuid.uuid4().hex[:16]}"
    registrar_evento(eid, tipo, {"id": eid, "event": tipo, "payment": {
        "id": cob["asaas_payment_id"], "externalReference": cob["external_reference"],
        "value": cob["amount_cents"] / 100, "status": "RECEIVED"}}, int(time.time()))
    return eid


def test_quatro_drenos_simultaneos_cabem_num_pool_de_cinco(user_id, externo, monkeypatch, pool_de):
    """Os 4 se encontram DENTRO do efeito: cada um segura só a reserva ali (antes, reserva + trava)."""
    from conftest import _cleanup_user  # type: ignore[attr-defined]
    from db import ensure_user

    outros = [int(uuid.uuid4().int % 10_000_000_000) for _ in range(3)]
    for uid in outros:
        ensure_user(uid)
    try:
        cobs = [_nova(uid) for uid in (user_id, *outros)]
        eventos = [_enfileirar(c) for c in cobs]
        encontro, cruzaram, ga4 = threading.Barrier(4, timeout=10), [], ef.EXECUTORES["ga4"]

        def _ga4(cobranca, evt):
            encontro.wait()
            cruzaram.append(cobranca["asaas_payment_id"])
            ga4(cobranca, evt)

        monkeypatch.setitem(ef.EXECUTORES, "ga4", _ga4)
        pool_de(5)
        erros = []

        def _roda(eid):
            try:
                drenar_evento(eid)
            except BaseException as exc:  # noqa: BLE001 — o teste decide
                erros.append(exc)

        fios = [threading.Thread(target=_roda, args=(e,)) for e in eventos]
        for f in fios:
            f.start()
        for f in fios:
            f.join(timeout=60)
        abertos = [(e, evento(e)["last_error"]) for e in eventos if evento(e)["processed_at"] is None]
        assert not erros and not abertos, (erros, abertos)
        assert len(set(cruzaram)) == 4
    finally:
        for uid in outros:
            _cleanup_user(uid)


def test_efeito_que_levanta_nao_deixa_trava_no_pool(user_id, externo, monkeypatch):
    """A trava vive na sessão da reserva, que volta viva ao pool: o `unlock` tem de rodar."""
    cob = _nova(user_id)

    def _quebra(cobranca, evt):
        raise RuntimeError("ga4 fora do ar")

    monkeypatch.setitem(ef.EXECUTORES, "ga4", _quebra)
    eid = entregar("PAYMENT_RECEIVED", cob)
    assert evento(eid)["processed_at"] is None and evento(eid)["last_error"] == "RuntimeError"
    with psycopg.connect(_get_pool().conninfo, autocommit=True) as fora:
        for efeito in ("stripe_cancel", "grant", "ebook", "ga4"):
            chave = f"pix_effect:{cob['asaas_payment_id']}:{efeito}"
            assert fora.execute("select pg_try_advisory_lock(hashtext(%s))", (chave,)).fetchone()[0], efeito
            fora.execute("select pg_advisory_unlock(hashtext(%s))", (chave,))


def test_reserva_segue_travada_depois_das_travas_de_efeito(user_id, externo, monkeypatch):
    """Em A só rodam lock e unlock: um `commit` ali soltaria o `for update` e outra passada
    reservaria o MESMO evento no meio do dreno. No `ga4`, 3 travas já abriram e fecharam em A."""
    from db.webhook_outbox import reservar_evento

    cob = _nova(user_id)
    eid = _enfileirar(cob)
    vista = []

    def _ga4(cobranca, evt):
        def outra_passada():
            with reservar_evento(eid) as linha:
                vista.append(linha)
        fio = threading.Thread(target=outra_passada)
        fio.start()
        fio.join(timeout=10)

    monkeypatch.setitem(ef.EXECUTORES, "ga4", _ga4)
    drenar_evento(eid)
    assert vista == [None], "a reserva foi solta no meio do dreno"
    assert evento(eid)["processed_at"] is not None


def test_deadlock_no_lock_aninhado_nao_deixa_trava_no_pool(user_id, externo, monkeypatch):
    """UNDONE aninha `grant` → `afiliado` em A. Uma sessão de fora (X) segura `afiliado` e, com A
    esperando, pede `grant`: o Postgres acusa deadlock EM A (esperou primeiro) e aborta a
    transação dela. O `unlock` do `grant` falha ali; sem fechar A, a trava ficava na sessão
    ociosa do pool e X esperava para sempre. A causa real tem de chegar ao `last_error`."""
    cob = _depois("PAYMENT_RECEIVED")(user_id)
    pid = cob["asaas_payment_id"]
    no_grant = threading.Event()
    revoke = ef.EXECUTORES["revoke"]

    def _revoke(cobranca, evt):
        revoke(cobranca, evt)
        no_grant.set()

    monkeypatch.setitem(ef.EXECUTORES, "revoke", _revoke)
    with psycopg.connect(_get_pool().conninfo, autocommit=True) as x:
        # X só procura deadlock depois de 5 s: A (1 s, o padrão) é sempre quem detecta e cai.
        x.execute("set lock_timeout = '8s'; set deadlock_timeout = '5s'")
        x.execute("select pg_advisory_lock(hashtext(%s))", (f"pix_effect:{pid}:afiliado",))
        eid = _enfileirar(cob, UNDONE)
        fio = threading.Thread(target=drenar_evento, args=(eid,))
        fio.start()
        assert no_grant.wait(15)
        # A já espera ESTE `afiliado` antes de X pedir o `grant` (senão X pega o `grant` livre).
        # Sem filtrar pela chave, a espera de outro teste (`-n 2`) soltava X cedo demais.
        for _ in range(100):
            if x.execute("select count(*) from pg_locks where locktype = 'advisory' and not granted"
                         " and objsubid = 1"
                         " and classid::bigint = (hashtext(%s)::bigint >> 32) & 4294967295"
                         " and objid::bigint = hashtext(%s)::bigint & 4294967295",
                         (f"pix_effect:{pid}:afiliado",) * 2).fetchone()[0]:
                break
            time.sleep(0.05)
        else:
            raise AssertionError("A nunca chegou a esperar o `afiliado`")
        x.execute("select pg_advisory_lock(hashtext(%s))", (f"pix_effect:{pid}:grant",))
        fio.join(30)
        x.execute("select pg_advisory_unlock_all()")
    ev = evento(eid)
    assert ev["processed_at"] is None and ev["attempts"] == 1
    assert ev["last_error"] == "DeadlockDetected"
    drenar_evento(eid)  # a retentativa acha as travas livres e conclui
    assert evento(eid)["processed_at"] is not None, evento(eid)["last_error"]


def test_contextvar_da_reserva_volta_a_none(user_id, externo):
    from db.pix_effects import conexao_da_reserva
    from db.webhook_outbox import reservar_evento

    eid = _enfileirar(_nova(user_id))
    drenar_evento(eid)
    assert conexao_da_reserva.get() is None
    with pytest.raises(RuntimeError):
        with reservar_evento(_enfileirar(_nova(user_id))):
            raise RuntimeError("dreno caiu")
    assert conexao_da_reserva.get() is None


def test_unlock_que_falha_sem_erro_em_voo_sobe(user_id):
    """A sessão da reserva cai DENTRO de uma trava cujo corpo termina bem: o `unlock` falha e o
    erro tem de subir. Engolido, o dreno seguiria sem a reserva (o `for update` morreu junto)."""
    from db.pix_effects import conexao_da_reserva, lock_efeito
    from db.webhook_outbox import reservar_evento

    with reservar_evento(_enfileirar(_nova(user_id))):
        a = conexao_da_reserva.get()
        with psycopg.connect(_get_pool().conninfo, autocommit=True) as fora:
            with pytest.raises(psycopg.OperationalError):
                with lock_efeito(f"pay_{uuid.uuid4().hex[:12]}", "grant"):
                    fora.execute("select pg_terminate_backend(%s)", (a.info.backend_pid,))
                    for _ in range(100):
                        if not fora.execute("select 1 from pg_stat_activity where pid = %s",
                                            (a.info.backend_pid,)).fetchone():
                            break
                        time.sleep(0.05)
        assert a.closed
