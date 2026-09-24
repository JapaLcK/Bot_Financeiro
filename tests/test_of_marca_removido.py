"""Onda 4 / PR-D — a marca de remoção DELIBERADA no registry.

Disconnect e reset gravam, na MESMA transação do delete, uma linha
`origin='removed'` COM DONO no `open_finance_item_registry`. É ela que faz a 1ª
guarda de `_adota_item_orfao` (que já recusa qualquer origem com dono) recusar
uma reentrega de `item/created` de banco removido **sem linha `pluggy_item`** —
o `register_item` do POST falhou, ou a conexão é anterior ao registry. Sem a
marca os dois casos voltam, com dono e com sync agendado (provado por um teste
de ataque do Tester). Atomicidade e a barreira do lock moram no irmão
`tests/test_of_marca_removido_escrita.py`.

CONTROLES POSITIVOS do grupo (sem eles tudo aqui passaria num código que recusa
tudo): `test_reconexao_do_mesmo_dono_depois_da_marca_continua_funcionando` aqui,
e `test_of_webhook_adopt_guards.py::test_item_created_continua_adotando_o_dono_legitimo`
lá — RODAR OS DOIS ARQUIVOS JUNTOS em toda mutação.
"""

from __future__ import annotations

import os

import psycopg
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

os.environ.setdefault("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())

import db
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


def _linhas(item_id: str) -> list[dict]:
    """O registry INTEIRO daquele item, em ordem de `id`. `_registry` (o
    importado) devolve só `origin`+`user_id` e três testes o comparam por
    igualdade de dict — alargá-lo quebraria os três. Aqui a pergunta é a FORMA."""
    with get_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "select origin, user_id, status, last_event, removal_tracked "
                "from open_finance_item_registry where provider_item_id=%s order by id",
                (item_id,),
            )
            return [dict(r) for r in (cur.fetchall() or [])]


def _limpa_registry(item_id: str) -> None:
    """Só o rastro, sem tocar na conexão (o `_limpa_item` apaga as duas)."""
    with get_conn() as c:
        c.execute("delete from open_finance_item_registry where provider_item_id=%s",
                  (item_id,))
        c.commit()


def _conecta_pelo_widget(client, uid: int, item: str):
    return client.post(f"/open-finance/{uid}/pluggy-item",
                       json={"item": {"id": item}}, headers=_auth(client, uid))


def _reset_pela_rota(monkeypatch, uid: int):
    """POST /settings/reset com a limpeza remota da Pluggy inerte."""
    monkeypatch.setattr(of_routes, "create_pluggy_api_key", lambda: "api-key")
    monkeypatch.setattr(of_routes, "delete_pluggy_item",
                        lambda item_id, api_key=None: None)
    client = TestClient(dashboard.app)
    return client.post("/settings/reset", json={"password": SENHA},
                       headers=_auth_reset(client, uid))


def _sem_pluggy_item(monkeypatch):
    """`register_item` estoura SÓ para `origin='pluggy_item'`.

    É a falha do teste de ataque: a conexão commita e o rastro do navegador se
    perde. Depois do POST o dublê sai de cena (o webhook precisa do real)."""
    real = of_routes.register_item

    def _explode(*a, **kw):
        if kw.get("origin") == "pluggy_item":
            raise psycopg.OperationalError("escrita do rastro falhou")
        return real(*a, **kw)

    monkeypatch.setattr(of_routes, "register_item", _explode)
    return real


# ── grupo 1: a marca existe, nas duas portas ─────────────────────────────────

def test_disconnect_grava_a_marca_com_dono_e_a_porta(
        user_id, monkeypatch, eventos, webhook_pluggy):
    """DELETE /open-finance/{uid} deixa `removed` com dono, porta e status."""
    promote_to_pro(user_id)
    _mock_item(monkeypatch, user_id)
    item = "d-marca"
    try:
        client = TestClient(dashboard.app)
        assert _conecta_pelo_widget(client, user_id, item).status_code == 200
        c2 = TestClient(dashboard.app)
        assert c2.delete(f"/open-finance/{user_id}",
                         headers=_auth(c2, user_id)).status_code == 200
        linhas = _linhas(item)
        assert [r["origin"] for r in linhas] == ["pluggy_item", "removed"], linhas
        marca = linhas[-1]
        assert int(marca["user_id"]) == user_id, "marca sem dono não é vista pela guarda"
        assert marca["last_event"] == "disconnect"
        assert marca["status"] == "UPDATED", "o estado da conexão apagada some do rastro"
        assert marca["removal_tracked"] is True
    finally:
        db.disconnect_open_finance_connection(user_id)
        _limpa_item(item)


def test_reset_grava_a_marca_com_last_event_reset(user_id, monkeypatch):
    """O reset preserva o registry E acrescenta a marca (não substitui nada)."""
    _semeia(user_id)
    item = _item_de(user_id)
    try:
        assert [r["origin"] for r in _linhas(item)] == ["connect_token"], "pré-condição"
        assert _reset_pela_rota(monkeypatch, user_id).status_code == 200
        linhas = _linhas(item)
        assert [r["origin"] for r in linhas] == ["connect_token", "removed"], linhas
        assert linhas[-1]["last_event"] == "reset"
        assert int(linhas[-1]["user_id"]) == user_id
    finally:
        _limpa_item(item)


# ── grupo 2: o aceite — a reentrega não ressuscita ───────────────────────────

def test_item_created_reentregue_nao_ressuscita_banco_removido_sem_rastro_pluggy_item(
        user_id, monkeypatch, eventos, webhook_pluggy):
    """O caso do teste de ataque: `register_item` falhou, o rastro do navegador
    não existe, o usuário removeu o banco — e a Pluggy reentrega `item/created`.
    O status do POST NÃO é asserido de propósito (hoje 500, com a PR-A vira 200):
    a pré-condição verdadeira é a CONEXÃO ter sido criada."""
    promote_to_pro(user_id)
    item = "d-ress"
    _mock_item(monkeypatch, user_id)
    real = _sem_pluggy_item(monkeypatch)
    try:
        client = TestClient(dashboard.app, raise_server_exceptions=False)
        _conecta_pelo_widget(client, user_id, item)
        assert len(db.get_connections_by_item_id(item)) == 1, "pré-condição: conectou"
        monkeypatch.setattr(of_routes, "register_item", real)
        assert _registry(item) == [], "pré-condição: o rastro do navegador se perdeu"

        c2 = TestClient(dashboard.app)
        assert c2.delete(f"/open-finance/{user_id}",
                         headers=_auth(c2, user_id)).status_code == 200
        webhook_pluggy.clear()

        assert _webhook(TestClient(dashboard.app), "item/created", item).status_code == 200

        assert db.get_connections_by_item_id(item) == [], "banco REMOVIDO ressuscitou"
        assert webhook_pluggy == [], "e com sync, que re-importa o que o usuário apagou"
        skips = [e["details"] for e in eventos if e["event"] == "of_webhook_adopt_skipped"]
        assert [(s.get("motivo"), s.get("origens")) for s in skips] == \
            [("rastro_com_dono", ["removed"])], skips
    finally:
        db.disconnect_open_finance_connection(user_id)
        _limpa_item(item)


def test_conexao_legada_sem_rastro_nenhum_tambem_fica_removida(
        user_id, monkeypatch, eventos, webhook_pluggy):
    """A outra metade da classe: conexão ANTERIOR ao registry (nenhuma linha)."""
    promote_to_pro(user_id)
    item = "d-legado"
    _mock_item(monkeypatch, user_id)
    try:
        db.save_pluggy_open_finance_item(
            user_id, {"id": item, "status": "UPDATED",
                      "connector": {"id": 612, "name": "Nubank"}})
        assert _registry(item) == [], "pré-condição: conexão sem rastro"

        assert db.disconnect_open_finance_connection(user_id) == 1
        webhook_pluggy.clear()

        assert _webhook(TestClient(dashboard.app), "item/created", item).status_code == 200

        assert db.get_connections_by_item_id(item) == [], "banco LEGADO removido ressuscitou"
        assert webhook_pluggy == []
        # `webhook` (sem dono) é o rastro que a própria reentrega recusada deixa;
        # a marca é a linha COM DONO, e é ela que recusou.
        assert [r["origin"] for r in _registry(item)] == ["removed", "webhook"], _registry(item)
    finally:
        db.disconnect_open_finance_connection(user_id)
        _limpa_item(item)


def test_reset_tambem_bloqueia_a_reentrega(user_id, monkeypatch, eventos, webhook_pluggy):
    """O gêmeo pelo RESET — cada porta tem teste próprio (CLAUDE.md §4: corrigir
    a classe, não a instância). O rastro que `_semeia` planta é APAGADO de
    propósito: com ele o item já tinha origem com dono (`connect_token`) e a
    guarda recusava antes da marca existir — medido, o caso passava na base."""
    _semeia(user_id)
    item = _item_de(user_id)
    _limpa_registry(item)
    _mock_item(monkeypatch, user_id)
    try:
        assert len(db.get_connections_by_item_id(item)) == 1, "pré-condição"
        assert _registry(item) == [], "pré-condição: conexão sem rastro com dono"
        assert _reset_pela_rota(monkeypatch, user_id).status_code == 200
        webhook_pluggy.clear()

        assert _webhook(TestClient(dashboard.app), "item/created", item).status_code == 200

        assert db.get_connections_by_item_id(item) == [], "banco ressuscitou depois do reset"
        assert webhook_pluggy == []
    finally:
        db.disconnect_open_finance_connection(user_id)
        _limpa_item(item)


# ── grupo 3: controles positivos ─────────────────────────────────────────────

def test_reconexao_do_mesmo_dono_depois_da_marca_continua_funcionando(
        user_id, monkeypatch, eventos, webhook_pluggy):
    """CONTROLE POSITIVO: a marca recusa a REENTREGA, não o usuário. Ele reconecta pelo
    widget e leva conexão, auditoria e sync — as QUATRO primeiras asserções (POST 200,
    1 conexão do dono, sync, auditoria), verdes mesmo num código que nunca grava marca.
    Só a última, a ordem `['pluggy_item','removed','pluggy_item']`, depende da marca por
    desenho: é a evidência da regra R, a ORDEM que a recuperação por operador (PR-E) usa."""
    promote_to_pro(user_id)
    from core.audit import AuditEvent, list_audit_events

    item = "d-reconecta"
    _mock_item(monkeypatch, user_id)
    try:
        client = TestClient(dashboard.app)
        assert _conecta_pelo_widget(client, user_id, item).status_code == 200
        c2 = TestClient(dashboard.app)
        assert c2.delete(f"/open-finance/{user_id}",
                         headers=_auth(c2, user_id)).status_code == 200
        webhook_pluggy.clear()

        c3 = TestClient(dashboard.app)
        assert _conecta_pelo_widget(c3, user_id, item).status_code == 200, "a marca travou o dono"

        linhas = db.get_connections_by_item_id(item)
        assert len(linhas) == 1 and int(linhas[0]["user_id"]) == user_id, linhas
        assert webhook_pluggy == [item], "reconexão sem sync não traz nada de volta"
        assert [r["origin"] for r in _registry(item)] == \
            ["pluggy_item", "removed", "pluggy_item"], _registry(item)
        conectados = [e for e in list_audit_events(user_id, limit=50)
                      if e["event"] == AuditEvent.OPEN_FINANCE_CONNECTED
                      and (e.get("details") or {}).get("item_id") == item]
        assert len(conectados) == 2, f"a reconexão sumiu de 'Atividade da conta': {conectados}"
    finally:
        db.disconnect_open_finance_connection(user_id)
        _limpa_item(item)


def test_item_removido_por_um_dono_nao_e_adotado_por_outro(
        user_id, monkeypatch, eventos, webhook_pluggy):
    """#349: quem decide posse é o `clientUserId` remoto. A marca de A não
    autoriza nem impede B — B continua recusado, e nada de A vaza no log."""
    promote_to_pro(user_id)
    item = "d-outro-dono"
    outro = user_id + 1
    db.ensure_user(outro)
    promote_to_pro(outro)
    _mock_item(monkeypatch, user_id)
    try:
        client = TestClient(dashboard.app)
        assert _conecta_pelo_widget(client, user_id, item).status_code == 200
        c2 = TestClient(dashboard.app)
        assert c2.delete(f"/open-finance/{user_id}",
                         headers=_auth(c2, user_id)).status_code == 200

        _mock_item(monkeypatch, outro)          # agora a Pluggy diz que o dono é B
        webhook_pluggy.clear()
        assert _webhook(TestClient(dashboard.app), "item/created", item).status_code == 200

        assert db.get_connections_by_item_id(item) == [], "o item foi para o outro dono"
        assert webhook_pluggy == []
        detalhes = [e["details"] for e in eventos if e["event"] == "of_webhook_adopt_skipped"]
        assert detalhes and all(str(user_id) not in str(d.values()) for d in detalhes), detalhes
    finally:
        for uid in (user_id, outro):
            db.disconnect_open_finance_connection(uid)
        with get_conn() as c:
            c.execute("delete from users where id=%s", (outro,))
            c.commit()
        _limpa_item(item)


# ── grupo 6: a coluna `removal_tracked` ──────────────────────────────────────

def test_linha_nova_nasce_marcada_e_a_legada_continua_sem_marca(
        user_id, monkeypatch, eventos, webhook_pluggy):
    """A coluna é a linha de corte no tempo: quem escreveu o rastro sabia marcar
    remoções? Rastro do fluxo real = sim; `insert` que não cita a coluna = não."""
    promote_to_pro(user_id)
    item = "d-coluna"
    _mock_item(monkeypatch, user_id)
    try:
        client = TestClient(dashboard.app)
        assert _conecta_pelo_widget(client, user_id, item).status_code == 200
        assert [r["removal_tracked"] for r in _linhas(item)] == [True]

        with get_conn() as c:
            c.execute("insert into open_finance_item_registry "
                      "(user_id, provider_item_id, origin) values (%s,%s,'webhook')",
                      (user_id, item))
            c.commit()
        assert [r["removal_tracked"] for r in _linhas(item)] == [True, False]
    finally:
        db.disconnect_open_finance_connection(user_id)
        _limpa_item(item)


# ── grupo 7: isolamento por usuário (CLAUDE.md §0) ───────────────────────────

def test_a_marca_e_do_dono_e_nao_toca_no_vizinho(
        user_id, monkeypatch, eventos, webhook_pluggy):
    """A desconecta; o rastro de B fica byte a byte igual."""
    promote_to_pro(user_id)
    item_a, item_b = "d-iso-a", "d-iso-b"
    vizinho = user_id + 1
    db.ensure_user(vizinho)
    promote_to_pro(vizinho)
    try:
        for uid, item in ((user_id, item_a), (vizinho, item_b)):
            _mock_item(monkeypatch, uid)
            client = TestClient(dashboard.app)
            assert _conecta_pelo_widget(client, uid, item).status_code == 200
        antes_b, origens_b = _linhas(item_b), db.item_registry_origins(item_b)

        c2 = TestClient(dashboard.app)
        assert c2.delete(f"/open-finance/{user_id}",
                         headers=_auth(c2, user_id)).status_code == 200

        assert [r["origin"] for r in _linhas(item_a)] == ["pluggy_item", "removed"]
        assert _linhas(item_b) == antes_b, "o disconnect de A mexeu no rastro de B"
        assert db.item_registry_origins(item_b) == origens_b
        with get_conn() as c:
            marcas = c.execute("select count(*) as n from open_finance_item_registry "
                               "where user_id=%s and origin='removed'", (vizinho,)).fetchone()
        assert marcas["n"] == 0, "marca de remoção gravada no vizinho"
        assert len(db.get_connections_by_item_id(item_b)) == 1, "o banco de B sumiu"
    finally:
        for uid in (user_id, vizinho):
            db.disconnect_open_finance_connection(uid)
        with get_conn() as c:
            c.execute("delete from users where id=%s", (vizinho,))
            c.commit()
        for i in (item_a, item_b):
            _limpa_item(i)
