"""PR-A2c (ADR 0006): UMA VEZ por declaração, e a corrida com o sync e com outro processo (G3). Postgres real,
threads com barreira: `_ordered_pair` só solta o segundo quando o primeiro segura o mutex do usuário e o segundo está
parado no lock (corrida de verdade, não duas chamadas em sequência). Dois PROCESSOS reais não são exercidos aqui.

Controles NEGATIVOS (numa CÓPIA; `db/bank_lembrete.py` e `core/services/declaracao_lembrete.py`):
- tirar `dec.lembrete_em is null` do `_VIVA` (vale para a listagem E para o claim) -> VERMELHO
  `test_dois_ticks_seguidos_mandam_uma_vez`, `test_dois_ticks_em_paralelo_mandam_uma_vez`;
- reservar uma a uma (`claim_declaracao_lembretes(uid, [id])` num laço) -> VERMELHO
  `test_ticks_concorrentes_nao_dividem_a_mensagem`;
- tirar `_lock_user` do claim -> VERMELHO `test_casada_entre_a_listagem_e_a_reserva_nao_manda_nada`
  (a reserva passa por cima do sync que ainda não commitou);
- tirar `dec.matched_transaction_id is null` do `_VIVA` -> VERMELHO o mesmo teste (a reserva vê o par que o sync
  commitou e manda assim mesmo);
- trocar "reservar, depois enviar" por "enviar, depois reservar" -> VERMELHO `test_dois_ticks_em_paralelo_mandam_uma_vez`.
- mandar dentro de uma transação com `_lock_user` aberto -> VERMELHO `test_envio_fora_de_transacao_e_de_lock`.
- tirar o `_lock_user` do `release` -> VERMELHO `test_soltar_nao_cruza_a_ordem_de_lock_do_sync` (DeadlockDetected:
  o UPDATE em lote do release e o sync pegam d1 e d2 em ordem oposta).
Positivos: `test_dois_ticks_seguidos_mandam_uma_vez` (a 1ª volta envia), `test_casada_depois_da_reserva_...`.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import psycopg
import requests

import core.services.declaracao_lembrete as dl
import db
import db.bank_lembrete as bl
import db.bank_movements as movements
from tests._lembrete_declaracao import (  # noqa: F401 (_event_logs é fixture autouse)
    FONE_A, _event_logs, agora, armar, corpos, declara_ha, dono, lembretes, para, reservadas)
from tests._espera_lock import _esperar_backend_travado
from tests._patrimonio_helpers import q
from tests._retrato_declaracao import retrato
from tests.test_bank_movements import _bank
from tests.test_bank_movements_concurrency import _captura, _ordered_pair
from tests.test_declaracao_fluxo_reconcilia import sincroniza, tx


def test_dois_ticks_seguidos_mandam_uma_vez(user_id, monkeypatch):
    ag = agora()
    posts = armar(monkeypatch)
    dono(user_id, FONE_A, ag)
    declara_ha(user_id, ag, 8, alvo="um")
    declara_ha(user_id, ag, 9, alvo="dois")

    assert dl.notify_declaracoes_whatsapp_once(now=ag) == 1  # a 1ª volta envia as duas numa mensagem
    assert dl.notify_declaracoes_whatsapp_once(now=agora(13)) == 0  # "reinício": nova chamada, estado só do banco
    assert dl.notify_declaracoes_whatsapp_once(now=agora(14)) == 0
    [corpo] = corpos(posts, FONE_A)
    assert "gasto um," in corpo and "gasto dois," in corpo and reservadas(user_id) == 2


def test_a_rolagem_de_dias_nao_reabre(user_id, monkeypatch):
    """Lembrada no dia 8, no dia 9, 30, 400 a declaração continua a mesma: nada de novo."""
    ag = agora()
    posts = armar(monkeypatch)
    dono(user_id, FONE_A, ag)
    declara_ha(user_id, ag, 8)
    assert dl.notify_declaracoes_whatsapp_once(now=ag) == 1
    for dias in (1, 22, 400):
        q("update auth_accounts set last_activity_at=%s where user_id=%s", (ag + timedelta(days=dias), user_id))
        assert dl.notify_declaracoes_whatsapp_once(now=ag + timedelta(days=dias)) == 0
    assert len(para(posts, FONE_A)) == 1


def _lento(monkeypatch, espera=0.3):
    """Quem reserva algo segura a vez antes de seguir: com o claim em lote isso não abre brecha; com o claim um
    por um o segundo processo pega a 2ª declaração no meio e a mensagem sai partida."""
    real = bl.claim_declaracao_lembretes

    def lento(uid, ids, limite):
        reservadas_ = real(uid, ids, limite)
        if reservadas_:
            time.sleep(espera)
        return reservadas_

    monkeypatch.setattr(bl, "claim_declaracao_lembretes", lento)


def test_ticks_concorrentes_nao_dividem_a_mensagem(user_id, monkeypatch):
    ag = agora()
    posts = armar(monkeypatch)
    dono(user_id, FONE_A, ag)
    declara_ha(user_id, ag, 8, alvo="luz")
    declara_ha(user_id, ag, 9, alvo="agua")
    _lento(monkeypatch)

    with ThreadPoolExecutor(2) as pool:
        for f in [pool.submit(dl.notify_declaracoes_whatsapp_once, now=ag) for _ in range(2)]:
            f.result()

    [corpo] = corpos(posts, FONE_A)
    assert "gasto luz," in corpo and "gasto agua," in corpo and reservadas(user_id) == 2


def test_dois_ticks_em_paralelo_mandam_uma_vez(user_id, monkeypatch):
    """Os dois leem a MESMA lista (barreira depois da listagem) e disputam a reserva: só um manda."""
    ag = agora()
    posts = armar(monkeypatch, atraso=0.2)
    dono(user_id, FONE_A, ag)
    declara_ha(user_id, ag, 8)
    barreira = threading.Barrier(2, timeout=5)
    real = bl.list_declaracao_lembretes_devidos

    def lista_e_espera(*a, **k):
        linhas = real(*a, **k)
        barreira.wait()
        return linhas

    monkeypatch.setattr(bl, "list_declaracao_lembretes_devidos", lista_e_espera)
    with ThreadPoolExecutor(2) as pool:
        enviados = [f.result() for f in [pool.submit(dl.notify_declaracoes_whatsapp_once, now=ag) for _ in range(2)]]

    assert sorted(enviados) == [0, 1] and len(para(posts, FONE_A)) == 1 and reservadas(user_id) == 1


def _declarada_e_extrato(user_id, ag):
    cid, _ = _bank(user_id)
    lid = declara_ha(user_id, ag, 8)
    return lid, (lambda: sincroniza(cid, user_id, tx(days=-8)))  # o sync liga o par sozinho (candidato único)


def test_casada_entre_a_listagem_e_a_reserva_nao_manda_nada(user_id, monkeypatch):
    """#7: o sync segura o mutex e liga o par enquanto o tick, que já listou a declaração viva, espera na reserva.
    Quando ele entra, a declaração está casada: o claim devolve vazio, nenhuma mensagem, nenhuma reserva."""
    ag = agora()
    posts = armar(monkeypatch)
    dono(user_id, FONE_A, ag)
    lid, sync = _declarada_e_extrato(user_id, ag)
    tick = _captura(lambda: dl.notify_declaracoes_whatsapp_once(now=ag))

    a, b = _ordered_pair(monkeypatch, _captura(sync), tick, esperar_travado=True)

    assert not isinstance(a, Exception) and b == 0, (a, b)
    assert q("select matched_transaction_id m from bank_movement_declarations where launch_id=%s", (lid,))["m"]
    assert para(posts, FONE_A) == [] and reservadas(user_id) == 0


def test_casada_depois_da_reserva_a_mensagem_sai_e_o_vinculo_nao_muda(user_id, monkeypatch):
    """#8: a reserva segura o mutex; o sync espera, liga o par depois. A mensagem reflete o estado da reserva
    (limite aceito: segundos de defasagem) e o lembrete não desfaz nem muda o vínculo."""
    ag = agora()
    posts = armar(monkeypatch)
    dono(user_id, FONE_A, ag)
    lid, sync = _declarada_e_extrato(user_id, ag)
    tick = _captura(lambda: dl.notify_declaracoes_whatsapp_once(now=ag))

    a, b = _ordered_pair(monkeypatch, tick, _captura(sync), esperar_travado=True)

    assert a == 1 and not isinstance(b, Exception), (a, b)
    assert len(para(posts, FONE_A)) == 1 and lembretes(user_id)[lid] is not None
    estado = q("select matched_transaction_id m, confirmation_method c from bank_movement_declarations "
               "where launch_id=%s", (lid,))
    assert estado["m"] and estado["c"] == "automatic"
    assert retrato(user_id)["C"] == 0  # Carteira intocada


def test_a_reserva_de_um_usuario_nao_alcanca_o_outro(user_id, monkeypatch):
    """I6: o claim de A com o id da declaração de B (id errado, ou forjado) não reserva a de B."""
    import db
    from conftest import _cleanup_user
    ag = agora()
    armar(monkeypatch)
    outro = user_id + 1
    try:
        db.ensure_user(outro)
        a = declara_ha(user_id, ag, 8, alvo="do a")
        b = declara_ha(outro, ag, 8, alvo="do b")

        assert [r["launch_id"] for r in bl.claim_declaracao_lembretes(user_id, [a, b], ag)] == [a]
        assert lembretes(outro) == {b: None}
        bl.release_declaracao_lembretes(outro, [a, b])  # soltar tampouco cruza: A segue reservada
        assert lembretes(user_id)[a] is not None
    finally:
        _cleanup_user(outro)


def test_envio_fora_de_transacao_e_de_lock(user_id, monkeypatch):
    """Durante o POST à Meta nenhuma conexão fica `idle in transaction` e o mutex do usuário está livre: o claim
    commitou antes do envio (uma Meta lenta não pode segurar a linha de `accounts`)."""
    ag = agora()
    posts = armar(monkeypatch)
    dono(user_id, FONE_A, ag)
    declara_ha(user_id, ag, 8)
    visto = {}
    boa = requests.post  # o `_post` do `armar`

    def _post(url, **kw):
        with db.get_conn() as c, c.cursor() as cur:
            cur.execute("""select count(*) n from pg_stat_activity where datname = current_database()
                            and state like 'idle in transaction%%' and pid <> pg_backend_pid()""")
            visto["idle_tx"] = cur.fetchone()["n"]
            try:
                cur.execute("select 1 from accounts where user_id = %s for update nowait", (user_id,))
                visto["mutex"] = "livre"
            except psycopg.errors.LockNotAvailable:
                visto["mutex"] = "preso"
        return boa(url, **kw)

    monkeypatch.setattr("requests.post", _post)

    assert dl.notify_declaracoes_whatsapp_once(now=ag) == 1 and len(para(posts, FONE_A)) == 1
    assert visto == {"idle_tx": 0, "mutex": "livre"}


def test_soltar_nao_cruza_a_ordem_de_lock_do_sync(user_id, monkeypatch):
    """Deadlock real: d1 (id menor) é o gasto mais NOVO, d2 (id maior) o mais ANTIGO. O sync liga d2 primeiro e segura
    a linha; o `release` (UPDATE em lote, ids ascendentes) pegaria d1 e esperaria d2, e o sync então quer d1. O
    `_lock_user` do release o põe na fila do mutex: ele só mexe nas linhas depois do commit do sync."""
    ag = agora()
    dono(user_id, FONE_A, ag)
    cid, _ = _bank(user_id)
    d1 = declara_ha(user_id, ag, 8, valor=50)
    d2 = declara_ha(user_id, ag, 9, valor=70)
    assert d1 < d2 and len(bl.claim_declaracao_lembretes(user_id, [d1, d2], ag)) == 2
    ligou, soltando, real = threading.Event(), threading.Event(), movements._bind

    def bind(cur, uid, declaration, tx_, method):
        real(cur, uid, declaration, tx_, method)
        if not ligou.is_set():
            assert declaration["launch_id"] == d2  # a mais antiga é a primeira da fila do sync
            ligou.set()
            assert soltando.wait(5)
            assert _esperar_backend_travado(5)  # o release está parado (no mutex ou na linha de d2)

    def solta():
        assert ligou.wait(5)
        soltando.set()
        bl.release_declaracao_lembretes(user_id, [d1, d2])

    monkeypatch.setattr(movements, "_bind", bind)
    with ThreadPoolExecutor(2) as pool:
        a = pool.submit(_captura(lambda: sincroniza(cid, user_id, tx(-50, "t1", -8), tx(-70, "t2", -9))))
        b = pool.submit(_captura(solta))
        a, b = a.result(timeout=20), b.result(timeout=20)

    assert not isinstance(a, Exception) and not isinstance(b, Exception), (a, b)
    assert all(q("select matched_transaction_id m from bank_movement_declarations where launch_id=%s", (i,))["m"]
               for i in (d1, d2))
    assert reservadas(user_id) == 0  # e a soltura valeu
