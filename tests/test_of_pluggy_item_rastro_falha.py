"""G5d — o `POST /pluggy-item` não devolve 5xx quando só o RASTRO falha.

A conexão é gravada por `_grava_reconexao` e COMMITA; o `register_item` que grava
o rastro `pluggy_item` no registry vem depois. Sem `try`, um `OperationalError`
dele subia: 500 com o banco já conectado, SEM `OPEN_FINANCE_CONNECTED` e SEM sync
inicial — o widget mostrava erro, o banco estava lá e a carteira ficava vazia até
o próximo refresh. Os outros dois emissores de `of_item_registry_failed` (emissão
do connect token e o webhook) já tratavam; esta porta era a que faltava.

O QUE ESTE GRUPO NÃO COBRE:
  * o logger e a auditoria são SUBSTITUÍDOS — o INSERT real de
    `system_event_logs` e de `audit_events` não roda aqui. A auditoria mockada
    guarda só o `uid`, e de `log_system_event` não se verifica `level` nem
    `message`: só o nome do evento, o `user_id` e os `details` (o `source` entra
    de carona — o `bind` da fixture reprova o kwarg errado, ninguém lê o valor).
    A gravação de verdade é assunto de `core/admin_dashboard.py` e `core/audit.py`;
  * a conexão, o registry e a marca de remoção são banco REAL (Postgres);
  * nada aqui exercita a Pluggy (o `get_pluggy_item` é dublê) nem o sync (o
    `_schedule_pluggy_sync` só anota o id).

QUAL ASSERÇÃO DISCRIMINA cada mutação (medido, seção "Prova" do relato):
  * `register_item` sem `try` (a HEAD) → caso 1 vermelho no STATUS (500 ≠ 200);
  * `try` ALARGADO até englobar o `_grava_reconexao` → caso 3 vermelho no
    EVENTO, não no status: o `except` engole o 503, o `connection` fica não
    ligado e o `UnboundLocalError` que sai fala da variável `connection` — o
    middleware de erro (`core/admin_dashboard.py`) devolve 503 para qualquer
    exceção cuja mensagem contenha "connection", então o status COINCIDE com o
    esperado. Quem pega é `of_item_registry_failed` aparecendo num caminho que
    nunca chegou a registrar nada;
  * trocar `source=` por um kwarg inexistente → SÓ o caso 1 vermelho, e no
    STATUS (`assert 500 == 200`): o `TypeError` da fixture `eventos` LOCAL
    (abaixo) sobe pelo `except` e vira 500 — ele aparece no log do servidor, não
    na linha `E` do pytest. Os casos 2 e 3 nunca entram no `except`, então ficam
    verdes;
  * remover `"motivo": type(exc).__name__` dos `details` → caso 1 vermelho no
    `KeyError: 'motivo'`. `motivo`+`sqlstate` em vez do `str(exc)[:200]` dos irmãos
    é decisão de LGPD: com o dono na COLUNA os `details` saem inteiros na exportação
    do titular, e o texto cru do psycopg traz host e porta (ver o comentário na rota);
  * remover `user_id=session_uid` → caso 1 vermelho (`None != <uid>`), e o 4 com
    ele: é o campo que dá à purga de LGPD alcance sobre o evento;
  * trocar `_log_com_teto` por `log_system_event` cru → NADA fica vermelho aqui. O
    dublê responde na hora, então o teto que impede o 200 PENDURADO (o `except`
    dispara quando o Postgres acabou de falhar) é invisível para este grupo — quem
    mede isso é `tests/test_of_concurrency.py`, com um log que pendura de verdade.

FORA DO QUE O `try` SALVA: só a falha ISOLADA do registry. `get_open_finance_snapshot`,
duas linhas adiante na rota, é leitura SEM `try` — numa queda geral do Postgres a
resposta volta a ser 5xx com a conexão commitada, que é o próprio desfecho que o
caso 1 descreve. Consertar isso é outra decisão, não está nesta PR.
"""

from __future__ import annotations

import inspect

import psycopg
import pytest
from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.open_finance as of_routes
from core.audit import AuditEvent
from test_of_item_ownership import _auth
from test_of_webhook_adopt_guards import (  # noqa: F401 — `webhook_pluggy` é fixture
    _limpa_item,
    _mock_item,
    _registry,
    webhook_pluggy,
)


@pytest.fixture()
def eventos(monkeypatch):
    """Captura os `log_system_event` PRESA À ASSINATURA REAL do logger.

    Deliberadamente NÃO é a fixture importável de `test_of_item_ownership.py`:
    aquela é `async def _log(level, event_type, message, **kw)` e aceita QUALQUER
    kwarg, então trocar `source=` por um nome inexistente na rota passava verde
    no teste e dava 500 em produção (o `log_system_event` de verdade só tem
    `source`, `user_id` e `details`). O `bind` levanta `TypeError` no mesmo lugar
    em que o real levantaria.
    """
    assinatura = inspect.signature(of_routes.log_system_event)
    capturados: list[dict] = []

    async def _log(*args, **kw):
        amarrado = assinatura.bind(*args, **kw)
        amarrado.apply_defaults()
        capturados.append(dict(amarrado.arguments))

    monkeypatch.setattr(of_routes, "log_system_event", _log)
    return capturados


@pytest.fixture()
def auditados(monkeypatch):
    """Só o uid de cada `OPEN_FINANCE_CONNECTED` — sem tocar `audit_events`."""
    vistos: list[int] = []

    def _grava(uid, evento, **kw):
        if evento == AuditEvent.OPEN_FINANCE_CONNECTED:
            vistos.append(int(uid))

    monkeypatch.setattr(of_routes, "record_audit_event", _grava)
    return vistos


def _post(client: TestClient, user_id: int, item_id: str):
    return client.post(f"/open-finance/{user_id}/pluggy-item",
                       json={"item": {"id": item_id}}, headers=_auth(client, user_id))


def _falhas(eventos: list[dict]) -> list[dict]:
    return [e for e in eventos if e["event_type"] == "of_item_registry_failed"]


# ── 1. o rastro cai, a conexão já está commitada ─────────────────────────────

def test_registry_fora_do_ar_nao_vira_500_com_a_conexao_gravada(
        user_id, monkeypatch, eventos, webhook_pluggy, auditados):
    """Medido na HEAD, sem o `try`: 500, conexão de pé, zero auditoria, zero sync."""
    _mock_item(monkeypatch, user_id)
    monkeypatch.setattr(of_routes, "register_item", lambda *a, **k: (_ for _ in ()).throw(
        psycopg.OperationalError("registry fora do ar")))
    client = TestClient(dashboard.app)
    try:
        r = _post(client, user_id, "pia-falha")

        assert r.status_code == 200, f"{r.status_code}: a conexão JÁ está no banco"
        linhas = db.get_connections_by_item_id("pia-falha")
        assert len(linhas) == 1 and int(linhas[0]["user_id"]) == user_id, linhas
        assert auditados == [user_id], f"conexão sem OPEN_FINANCE_CONNECTED: {auditados}"
        assert webhook_pluggy == ["pia-falha"], f"sync inicial não agendado: {webhook_pluggy}"

        falhas = _falhas(eventos)
        assert len(falhas) == 1, eventos
        assert falhas[0]["details"]["item_id"] == "pia-falha", falhas
        assert falhas[0]["details"]["origin"] == "pluggy_item_route", falhas
        # Tipo, não texto: o `str(exc)` do psycopg é o que NÃO pode ir para um
        # `details` com dono na coluna (exportação LGPD). O `sqlstate` é `None`
        # num `OperationalError` construído à mão, então a chave é o que se exige,
        # não o valor.
        assert falhas[0]["details"]["motivo"] == "OperationalError", falhas
        assert "sqlstate" in falhas[0]["details"], falhas
        assert "registry fora do ar" not in str(falhas[0]["details"]), (
            f"texto cru da exceção nos `details` de um evento exportável: {falhas}")
        assert falhas[0]["user_id"] == user_id, (
            "evento sem dono: com a coluna NULL nem o delete da exclusão de conta "
            f"(db/privacy.py:881) nem o cascade da FK alcançam a linha — {falhas}")
    finally:
        db.disconnect_open_finance_connection(user_id)
        _limpa_item("pia-falha")


# ── 2. CONTROLE POSITIVO: o caminho normal continua fazendo tudo ─────────────

def test_caminho_normal_grava_rastro_audita_e_agenda_o_sync(
        user_id, monkeypatch, eventos, webhook_pluggy, auditados):
    """Sem este caso, o de cima passaria num código que nunca registra rastro."""
    _mock_item(monkeypatch, user_id)
    client = TestClient(dashboard.app)
    try:
        r = _post(client, user_id, "pia-ok")

        assert r.status_code == 200, r.text
        assert _registry("pia-ok") == [{"origin": "pluggy_item", "user_id": user_id}], \
            _registry("pia-ok")
        assert auditados == [user_id], auditados
        assert webhook_pluggy == ["pia-ok"], webhook_pluggy
        assert _falhas(eventos) == [], "o caminho feliz rolou pelo `except`"
    finally:
        db.disconnect_open_finance_connection(user_id)
        _limpa_item("pia-ok")


# ── 3. a escrita da CONEXÃO falhando continua sendo 503 seco ─────────────────

def test_conexao_que_nao_grava_nao_deixa_rastro_auditoria_nem_sync(
        user_id, monkeypatch, eventos, webhook_pluggy, auditados):
    """O `try` é SÓ do `register_item`: o 503 de `_grava_reconexao` continua subindo.

    A falha entra pelo `save_pluggy_open_finance_item` (a mesma
    `psycopg.OperationalError` da infra que o `except` da retentativa trata), para
    passar pelo laço do prazo de verdade em vez de dublar `_grava_reconexao`.

    Este é o caso que fica vermelho se alguém alargar o `try` — e o que discrimina
    é `_falhas(eventos) == []`, não o status: ver o docstring do módulo.
    """
    _mock_item(monkeypatch, user_id)
    monkeypatch.setattr(of_routes, "save_pluggy_open_finance_item",
                        lambda *a, **k: (_ for _ in ()).throw(
                            psycopg.OperationalError("banco fora do ar")))
    client = TestClient(dashboard.app)
    try:
        r = _post(client, user_id, "pia-503")

        assert r.status_code == 503, f"{r.status_code}: escrita da conexão que falha é 503"
        assert db.get_connections_by_item_id("pia-503") == [], "nada podia ter sido gravado"
        assert _registry("pia-503") == [], "rastro de uma conexão que não existe"
        assert auditados == [], f"auditou conexão que não nasceu: {auditados}"
        assert webhook_pluggy == [], f"sync de banco não conectado: {webhook_pluggy}"
        assert _falhas(eventos) == [], (
            "`of_item_registry_failed` num caminho que nunca chegou ao `register_item` "
            "— é o `try` englobando o `_grava_reconexao`")
    finally:
        db.disconnect_open_finance_connection(user_id)
        _limpa_item("pia-503")


# ── 4. o IRMÃO da mesma classe: o rastro do connect token ────────────────────

def test_falha_no_rastro_do_connect_token_tambem_nasce_com_dono(
        user_id, monkeypatch, eventos):
    """Mesma classe, mesmo conserto (CLAUDE.md §2): o dono vai na COLUNA.

    O `try` deste lado já existia — 200 aqui não é novidade e não é o que se mede.
    O que faltava era o `user_id=`, e sem ele o evento fica órfão exatamente como
    o do `/pluggy-item`. O terceiro emissor (webhook) fica de fora de propósito:
    lá não há dono conhecido para pôr na coluna.

    A asserção do token NÃO DISCRIMINA esta mudança: o `register_item` dublado nunca
    recebe o token, então ela passa com e sem o `user_id=`. Fica como guarda de
    regressão para o dia em que alguém puser o retorno da Pluggy nos `details`.
    """
    monkeypatch.setattr(of_routes, "create_pluggy_connect_token",
                        lambda uid, webhook_url=None: {"accessToken": "tok-de-teste"})
    monkeypatch.setattr(of_routes, "register_item", lambda *a, **k: (_ for _ in ()).throw(
        psycopg.OperationalError("registry fora do ar")))
    client = TestClient(dashboard.app)

    r = client.post(f"/open-finance/{user_id}/connect-token", headers=_auth(client, user_id))

    assert r.status_code == 200, r.text
    falhas = _falhas(eventos)
    assert len(falhas) == 1, eventos
    assert falhas[0]["user_id"] == user_id, falhas
    assert "tok-de-teste" not in str(falhas[0]["details"]), (
        f"o token bruto NUNCA pode ir para o log: {falhas}")
