"""Onda 4 / PR-D — COMO a marca de remoção é escrita: mesma transação, sob o
lock, todas as linhas.

Irmão de `tests/test_of_marca_removido.py` (que prova que a marca existe e o que
ela fecha). O nome do arquivo é "escrita" e não "race" de propósito: dois dos
três grupos aqui não têm corrida nenhuma — o que junta os três é a pergunta
"onde a marca é gravada", que só a escrita responde.

  • ATOMICIDADE, as DUAS direções — a falha entre o delete e o commit não pode
    deixar marca sem delete (banco removido "para sempre" com a conexão viva),
    NEM delete sem marca (a ressurreição volta). Cada direção tem a mutação que
    a discrimina, escrita no caso; nenhum dos três casos sozinho pega as duas.
  • BARREIRA — a adoção pelo webhook que chega enquanto o disconnect segura o
    lock revalida SOB o lock, vê a marca e aborta (roteiro §10.3.5).
  • LOTE — o disconnect e o reset varrem TODOS os bancos do usuário, e cada um
    tem de sair marcado (a marca é um `executemany`).

CONTROLE POSITIVO do grupo: o disconnect e o reset do caminho feliz continuam
devolvendo 200 e apagando a conexão nos casos de lote — e os positivos do
arquivo irmão (a reconexão pelo widget) rodam junto.
"""

from __future__ import annotations

import asyncio
import os
import threading

import psycopg
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

os.environ.setdefault("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())

import db
import db.bank_movements as bank_movements
import db.open_finance_state as of_state
import db.privacy as privacy
import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.open_finance as of_routes
from conftest import promote_to_pro
from db.connection import get_conn
from test_account_reset import SENHA, _item_de, _semeia
from test_account_reset import _auth as _auth_reset
from test_account_reset import _zera_rate_limit  # noqa: F401 — autouse: limiter
from test_of_item_ownership import _auth, _webhook, eventos  # noqa: F401
from test_of_webhook_adopt_guards import (  # noqa: F401
    _limpa_item,
    _mock_item,
    _registry,
    webhook_pluggy,
)


def _marcas(uid: int) -> list[dict]:
    """As marcas daquele dono, em ordem de gravação. Devolve as LINHAS e não a
    contagem porque o grupo do lote pergunta QUAIS items saíram marcados."""
    with get_conn() as c:
        with c.cursor() as cur:
            cur.execute("select provider_item_id, last_event from open_finance_item_registry "
                        "where user_id=%s and origin='removed' order by id", (uid,))
            return [dict(r) for r in (cur.fetchall() or [])]


def _conn_crua(uid: int, item: str) -> None:
    """Conexão direto no banco: o teto do plano (Plus 2, Pro 5) e o widget não
    entram na pergunta deste grupo, que é quantas linhas o delete marca."""
    with get_conn() as c:
        c.execute("insert into open_finance_connections (user_id, provider, provider_item_id, "
                  "status, institution_id, institution_name) "
                  "values (%s,'pluggy',%s,'UPDATED','612','Nubank')", (uid, item))
        c.commit()


def _pluggy_inerte(monkeypatch) -> None:
    """Limpeza remota da Pluggy sem rede (o mesmo par do reset)."""
    monkeypatch.setattr(of_routes, "create_pluggy_api_key", lambda: "api-key")
    monkeypatch.setattr(of_routes, "delete_pluggy_item",
                        lambda item_id, api_key=None: None)


# ── grupo 4: atomicidade ─────────────────────────────────────────────────────

def test_falha_no_meio_do_disconnect_nao_deixa_marca_sem_delete(
        user_id, monkeypatch, eventos, webhook_pluggy):
    """`reconcile_bank_movements` estoura DEPOIS do delete e da marca, antes do
    commit: a transação inteira volta — conexão viva, nenhuma marca.

    MEDE UMA DIREÇÃO SÓ: marca que SOBREVIVE ao rollback do delete (banco
    removido para sempre com a conexão viva). A direção contrária — delete que
    commita e marca que vira uma segunda transação — este caso NÃO mede: com a
    chamada da marca movida para depois do `conn.commit()`, a falha acontece
    antes de a marca existir e ele fica verde. Medido: essa mutação sobrevivia ao
    grupo inteiro, disconnect e reset. Quem a discrimina é
    `test_marca_que_estoura_derruba_o_delete_junto`, logo abaixo.

    O dublê mira SÓ a chamada do disconnect: `reconcile_bank_movements` também
    roda na CONEXÃO (medido — a 1ª chamada do teste vinha do POST, e a rota ainda
    retentava o erro), então ele só entra em cena depois que o banco existe. O
    import é local, então o patch pega em `db.bank_movements`."""
    promote_to_pro(user_id)
    item = "dr-atomico"
    _mock_item(monkeypatch, user_id)
    real = bank_movements.reconcile_bank_movements
    chamadas: list[int] = []

    def _estoura(cur, uid, *a, **kw):
        chamadas.append(uid)
        raise psycopg.OperationalError("falha entre a marca e o commit")

    try:
        client = TestClient(dashboard.app)
        assert client.post(f"/open-finance/{user_id}/pluggy-item",
                           json={"item": {"id": item}},
                           headers=_auth(client, user_id)).status_code == 200

        monkeypatch.setattr(bank_movements, "reconcile_bank_movements", _estoura)
        c2 = TestClient(dashboard.app, raise_server_exceptions=False)
        c2.delete(f"/open-finance/{user_id}", headers=_auth(c2, user_id))

        assert chamadas == [user_id], "o dublê não pegou a chamada do disconnect"
        assert len(db.get_connections_by_item_id(item)) == 1, \
            "o delete commitou apesar da falha"
        assert _marcas(user_id) == [], \
            "marca sem delete: o banco fica removido para sempre com a conexão viva"
        assert [r["origin"] for r in _registry(item)] == ["pluggy_item"], _registry(item)
    finally:
        monkeypatch.setattr(bank_movements, "reconcile_bank_movements", real)
        db.disconnect_open_finance_connection(user_id)
        _limpa_item(item)


def test_falha_no_meio_do_reset_nao_deixa_marca_sem_delete(user_id, monkeypatch):
    """Mesma ideia no reset, no `_table_exists` do delete SEGUINTE ao bloco das
    conexões (o padrão de `test_account_reset::test_deadlock_no_reset...`):
    503, conexão de pé, nenhuma marca.

    Mesma direção do caso anterior: o que este caso discrimina é a marca gravada
    em transação PRÓPRIA (fica vermelho tanto com a chamada antes quanto depois
    do commit do delete, porque nas duas ela commita sozinha e sobrevive ao
    rollback). Ele nada diz sobre a porta do disconnect."""
    _semeia(user_id)
    item = _item_de(user_id)
    real = privacy._table_exists

    def _estoura(cur, table):
        if table == "credit_transactions":
            raise psycopg.errors.DeadlockDetected("deadlock detected")
        return real(cur, table)

    _pluggy_inerte(monkeypatch)
    monkeypatch.setattr(privacy, "_table_exists", _estoura)
    try:
        client = TestClient(dashboard.app)
        resp = client.post("/settings/reset", json={"password": SENHA},
                           headers=_auth_reset(client, user_id))

        assert resp.status_code == 503, resp.text
        assert len(db.get_connections_by_item_id(item)) == 1, "o reset commitou pela metade"
        assert _marcas(user_id) == [], "marca de remoção sobreviveu ao rollback do reset"
    finally:
        monkeypatch.setattr(privacy, "_table_exists", real)
        db.disconnect_open_finance_connection(user_id)
        _limpa_item(item)


def test_marca_que_estoura_derruba_o_delete_junto(
        user_id, monkeypatch, eventos, webhook_pluggy):
    """A OUTRA direção: a marca falha → o DELETE volta atrás, a conexão fica.

    Os dois casos acima medem "marca sem delete". Este mede "delete sem marca",
    que é o estrago pior (o banco some e a reentrega de `item/created` o
    ressuscita). O dublê estoura DENTRO de `mark_items_removed`; como a chamada
    está na transação do delete, o `with get_conn()` desfaz tudo e a conexão
    continua de pé.

    MUTAÇÃO que o deixa VERMELHO (e que sobrevive a todo o resto do grupo):
    mover a chamada da marca para DEPOIS do `conn.commit()` de
    `disconnect_open_finance_connection`, com cursor próprio — aí o delete já
    commitou quando o dublê estoura e a asserção da conexão cai com 0 linhas.

    `mark_items_removed` é importada LOCALMENTE nos dois chamadores
    (`db/open_finance.py`, `db/privacy.py`), então patchar o módulo pega.
    """
    promote_to_pro(user_id)
    item = "dr-marca-estoura"
    _mock_item(monkeypatch, user_id)
    real = of_state.mark_items_removed
    chamadas: list[int] = []

    def _estoura(cur, uid, linhas, *, last_event):
        chamadas.append(uid)
        raise psycopg.OperationalError("falha ao gravar a marca")

    try:
        client = TestClient(dashboard.app)
        assert client.post(f"/open-finance/{user_id}/pluggy-item",
                           json={"item": {"id": item}},
                           headers=_auth(client, user_id)).status_code == 200

        monkeypatch.setattr(of_state, "mark_items_removed", _estoura)
        c2 = TestClient(dashboard.app, raise_server_exceptions=False)
        c2.delete(f"/open-finance/{user_id}", headers=_auth(c2, user_id))

        assert chamadas == [user_id], "o dublê não pegou a chamada do disconnect"
        assert len(db.get_connections_by_item_id(item)) == 1, \
            "delete commitado sem marca: o banco sumiu e a reentrega o ressuscita"
        assert _marcas(user_id) == [], "a marca estourou, não podia existir"
        assert [r["origin"] for r in _registry(item)] == ["pluggy_item"], _registry(item)
    finally:
        monkeypatch.setattr(of_state, "mark_items_removed", real)
        db.disconnect_open_finance_connection(user_id)
        _limpa_item(item)


# ── grupo 5: a barreira do lock ──────────────────────────────────────────────

def test_adocao_que_espera_o_lock_ve_a_marca_e_aborta(
        user_id, monkeypatch, eventos, webhook_pluggy):
    """A intercalação que a PR tem de fechar (roteiro §10.3.5):

    1. A (disconnect) apaga a conexão e grava a marca — e PARA antes do commit;
    2. B (`item/created` reentregue, item sem linha `pluggy_item`) lê o registry
       e ainda não vê a marca (não commitou): decide adotar e vai pedir o lock;
    3. A commita e solta;
    4. B revalida SOB o lock, vê `removed`, levanta 409 e apaga a própria
       reivindicação.

    Desfecho medido: 0 conexões, registry final só com a marca, um
    `of_webhook_adopt_skipped`. Se B não chegar a bloquear no lock (item fora da
    enumeração de A), o desfecho que importa é o mesmo — o delete de A varre
    também a linha de B —, e o relato diz qual das duas ocorreu."""
    promote_to_pro(user_id)
    item = "dr-barreira"
    _mock_item(monkeypatch, user_id)

    parado = threading.Event()
    liberar = threading.Event()
    real = bank_movements.reconcile_bank_movements

    def _segura(cur, uid, *a, **kw):
        out = real(cur, uid, *a, **kw)
        if not parado.is_set():
            parado.set()                 # delete e marca feitos, commit pendente
            assert liberar.wait(10), "B nunca chegou ao lock"
        return out

    try:
        db.save_pluggy_open_finance_item(              # legado: sem `pluggy_item`
            user_id, {"id": item, "status": "UPDATED",
                      "connector": {"id": 612, "name": "Nubank"}})
        assert _registry(item) == [], "pré-condição: conexão sem rastro"
        monkeypatch.setattr(bank_movements, "reconcile_bank_movements", _segura)

        fim: list[object] = []

        def _disconnect():
            c = TestClient(dashboard.app, raise_server_exceptions=False)
            fim.append(c.delete(f"/open-finance/{user_id}",
                                headers=_auth(c, user_id)).status_code)

        t = threading.Thread(target=_disconnect)
        t.start()
        assert parado.wait(10), "o disconnect não chegou ao ponto de barreira"

        # B decide adotar AGORA (a marca ainda não commitou) e vai pedir o lock.
        adotado: list[object] = []

        def _adota():
            adotado.append(asyncio.run(of_routes._adota_item_orfao(item, "item/created")))

        tb = threading.Thread(target=_adota)
        tb.start()
        tb.join(3)                      # se bloquear no lock, segue preso aqui
        bloqueou = tb.is_alive()
        liberar.set()
        tb.join(10)
        t.join(10)

        print(f"[barreira] B bloqueou no lock: {bloqueou}; disconnect={fim}; "
              f"registry={_registry(item)}")
        assert fim == [200], fim
        assert adotado == [None], f"a adoção venceu a marca: {adotado}"
        assert db.get_connections_by_item_id(item) == [], "banco removido ressuscitou"
        assert [r["origin"] for r in _registry(item)] == ["removed"], _registry(item)
        assert webhook_pluggy == [], "sync agendado re-importa o que o usuário apagou"
        skips = [e["details"] for e in eventos if e["event"] == "of_webhook_adopt_skipped"]
        assert len(skips) == 1, skips
    finally:
        monkeypatch.setattr(bank_movements, "reconcile_bank_movements", real)
        liberar.set()
        db.disconnect_open_finance_connection(user_id)
        _limpa_item(item)


# ── grupo 6: o lote — N conexões, N marcas ───────────────────────────────────

def test_disconnect_de_dois_bancos_marca_os_dois(user_id, monkeypatch):
    """A rota NUNCA passa `connection_id` (`frontend/routes/open_finance.py:2088`):
    um disconnect varre todos os bancos do usuário — o Plus permite 2 e o Pro 5.

    A marca é um `executemany` sobre as linhas do `returning`. Se ela virar um
    `execute` só da primeira, o 2º banco sai SEM marca e volta ressuscitável
    pela reentrega de `item/created` — e o resto do grupo continua verde (todos
    os outros casos têm uma conexão só). É este caso que prende o lote.
    """
    promote_to_pro(user_id)
    a, b = "d-lote-a", "d-lote-b"
    _pluggy_inerte(monkeypatch)
    try:
        _conn_crua(user_id, a)
        _conn_crua(user_id, b)
        c = TestClient(dashboard.app)
        assert c.delete(f"/open-finance/{user_id}",
                        headers=_auth(c, user_id)).status_code == 200
        marcas = _marcas(user_id)
        assert sorted(m["provider_item_id"] for m in marcas) == [a, b], marcas
        assert [m["last_event"] for m in marcas] == ["disconnect"] * 2, marcas
    finally:
        db.disconnect_open_finance_connection(user_id)
        for i in (a, b):
            _limpa_item(i)


def test_reset_de_dois_bancos_marca_os_dois(user_id, monkeypatch):
    """O gêmeo pela outra porta: o reset apaga as conexões todas de uma vez, e a
    marca de cada uma sai com `last_event='reset'`. As duas conexões são a que o
    `_semeia` planta e uma segunda, crua."""
    _semeia(user_id)
    a, b = _item_de(user_id), "d-lote-reset-b"
    _pluggy_inerte(monkeypatch)
    try:
        _conn_crua(user_id, b)
        c = TestClient(dashboard.app)
        assert c.post("/settings/reset", json={"password": SENHA},
                      headers=_auth_reset(c, user_id)).status_code == 200
        marcas = _marcas(user_id)
        assert sorted(m["provider_item_id"] for m in marcas) == sorted([a, b]), marcas
        assert [m["last_event"] for m in marcas] == ["reset"] * 2, marcas
    finally:
        db.disconnect_open_finance_connection(user_id)
        for i in (a, b):
            _limpa_item(i)
