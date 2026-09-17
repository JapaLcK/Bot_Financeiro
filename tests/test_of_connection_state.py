"""G1 — o sync para de mentir que deu certo.

Fato medido em produção que originou o grupo: `GET /accounts?itemId=<item deletado>`
devolve **HTTP 200 com results:[]**; só `GET /items/{id}` devolve 404. Como
`save_open_finance_sync` carimbava `status='ACTIVE', last_sync_at=now()`
incondicionalmente, um item apagado no banco virava "Atualizado agora" — e um sync
posterior ressuscitava `DELETED → ACTIVE` e `ERROR → ACTIVE`.

CONTROLE NEGATIVO do grupo (MEDIDO): desligar o conserto — tirar o `get_pluggy_item`
e o `mark_sync_result` de `sync_pluggy_item` e devolver o `update ... status='ACTIVE',
last_sync_at=now()` incondicional a `save_open_finance_sync` — deixa 5 testes
vermelhos (404 não carimba, 404 não apaga, sem contas, ERROR→ACTIVE, tentativa ≠
sucesso). O de PAUSED segue verde: ele testa outra guarda.

CONTROLE POSITIVO: o caso 5 prova que o caminho legítimo continua funcionando —
`ERROR` volta a `ACTIVE` quando o item está saudável e o sync completa. Sem ele o
grupo passaria num código que recusasse tudo.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

os.environ.setdefault("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())

import db
import core.services.pluggy_sync as ps
import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.open_finance as of_routes
from core.services.pluggy import PluggyApiError
from core.services.pluggy_health import derive_item_health
from psycopg.types.json import Jsonb

from db.connection import get_conn
from utils_date import _tz

# Relógio fixo: nenhuma asserção pode depender do dia em que a suíte roda.
AGORA = datetime(2026, 8, 20, 12, 0, 0, tzinfo=_tz())
ANTES = datetime(2026, 8, 12, 9, 30, 0, tzinfo=_tz())

ITEM_SAUDAVEL = {
    "id": "item-g1",
    "status": "UPDATED",
    "executionStatus": "SUCCESS",
    "clientUserId": "1",
    "statusDetail": {
        "accounts": {"isUpdated": True, "lastUpdatedAt": "2026-08-20T11:00:00.000Z", "warnings": []},
        "creditCards": {"isUpdated": True, "lastUpdatedAt": "2026-08-20T11:00:00.000Z", "warnings": []},
    },
}


class _Relogio(datetime):
    @classmethod
    def now(cls, tz=None):
        return AGORA


@pytest.fixture()
def relogio_fixo(monkeypatch):
    monkeypatch.setattr(ps, "datetime", _Relogio)
    monkeypatch.setattr("db.open_finance_state.datetime", _Relogio)
    # `db.open_finance` também carimba hora nesta tabela (o `reconnected_at` do
    # upsert). Deixá-lo no relógio real fazia a reconexão nascer DEPOIS de um
    # sync com hora fixa, e a comparação `last_sync_at >= reconnected_at`
    # invertia — armadilha para quem viesse depois.
    monkeypatch.setattr("db.open_finance.datetime", _Relogio)


def _conexao(user_id: int, item_id: str = "item-g1", status: str = "UPDATED") -> dict:
    conn = db.save_pluggy_open_finance_item(
        user_id,
        {"id": item_id, "status": status, "connector": {"id": 612, "name": "Nubank"}},
    )
    _set_estado(conn["id"], status=status, last_sync_at=ANTES, last_attempt_at=ANTES)
    return conn


def _set_estado(connection_id: int, **campos) -> None:
    sets = ", ".join(f"{k}=%s" for k in campos)
    with get_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                f"update open_finance_connections set {sets} where id=%s",
                (*campos.values(), connection_id),
            )
        c.commit()


def _linha(item_id: str = "item-g1") -> dict:
    rows = db.get_connections_by_item_id(item_id)
    assert len(rows) == 1
    return rows[0]


def _avisadas(uid: int) -> set[str]:
    return {c["provider_item_id"] for c in db.list_connections_needing_reconnect(uid)}


def _espelho(connection_id: int) -> tuple[int, int]:
    with get_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "select count(*) as n from open_finance_accounts where connection_id=%s",
                (connection_id,),
            )
            contas = int(cur.fetchone()["n"])
            cur.execute(
                """
                select count(*) as n from open_finance_transactions t
                join open_finance_accounts a on a.id = t.account_id
                where a.connection_id=%s
                """,
                (connection_id,),
            )
            txs = int(cur.fetchone()["n"])
    return contas, txs


def _contas_espelhadas(connection_id: int) -> set[str]:
    """Os IDs, não a contagem: sobrescrita por snapshot velho troca CONTEÚDO."""
    with get_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "select provider_account_id from open_finance_accounts where connection_id=%s",
                (connection_id,),
            )
            return {r["provider_account_id"] for r in (cur.fetchall() or [])}


def _mock_pluggy(monkeypatch, *, item, contas=(), txs=()):
    """Mocka o mundo remoto. `item` pode ser um dict ou uma exceção a levantar."""
    def _get_item(item_id, api_key=None):
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(ps, "create_pluggy_api_key", lambda: "k")
    monkeypatch.setattr(ps, "get_pluggy_item", _get_item)
    monkeypatch.setattr(ps, "list_pluggy_accounts", lambda i, k=None: list(contas))
    monkeypatch.setattr(ps, "list_pluggy_transactions",
                        lambda acc, k=None, **kw: list(txs))
    monkeypatch.setattr(ps, "list_pluggy_investments", lambda i, k=None: [])


def _conta_pluggy(account_id="acc-g1"):
    return {"id": account_id, "name": "Conta", "type": "BANK", "currencyCode": "BRL",
            "balance": "1000.00"}


def _tx_pluggy(tx_id="tx-g1"):
    return {"id": tx_id, "description": "Mercado", "amount": "-50.00",
            "date": "2026-08-19T10:00:00.000-03:00"}


# ── 1. item 404 ───────────────────────────────────────────────────────────────

def test_item_404_nao_carimba_sucesso(user_id, monkeypatch, relogio_fixo):
    conexao = _conexao(user_id)
    # espelho com dado dentro: um item que sumiu não pode apagar histórico
    db.save_open_finance_sync(conexao["id"], [{
        "provider_account_id": "acc-g1", "name": "Conta", "type": "BANK",
        "currency": "BRL", "balance": 1000, "raw": {},
        "transactions": [{"provider_transaction_id": "tx-g1", "description": "Mercado",
                          "amount": -50, "transaction_date": ANTES.date(), "raw": {}}],
    }])
    _set_estado(conexao["id"], last_sync_at=ANTES)
    antes = _espelho(conexao["id"])

    _mock_pluggy(monkeypatch, item=PluggyApiError("nao existe", status_code=404))
    res = ps.sync_pluggy_item("item-g1")

    assert res["ok"] is False
    assert res["reason"] == "item_missing"
    linha = _linha()
    assert linha["status"] == "ERROR"
    assert linha["status_reason"] == "item_missing"
    assert linha["last_sync_at"] == ANTES, "last_sync_at é SUCESSO — não pode andar numa falha"
    assert _espelho(conexao["id"]) == antes, "espelho não pode ser tocado"


def test_item_404_nao_apaga_nada_nem_remoto(user_id, monkeypatch, relogio_fixo):
    conexao = _conexao(user_id)
    chamadas = []
    monkeypatch.setattr("core.services.pluggy.delete_pluggy_item",
                        lambda *a, **kw: chamadas.append(a))
    _mock_pluggy(monkeypatch, item=PluggyApiError("nao existe", status_code=404))

    res = ps.sync_pluggy_item("item-g1")

    assert res["reason"] == "item_missing"
    assert chamadas == [], "conexão perdida NÃO deleta item na Pluggy"
    assert len(db.get_connections_by_item_id("item-g1")) == 1, "a conexão local continua lá"
    assert db.get_open_finance_snapshot(user_id)["connections"], "nada foi removido"


# ── 3. item vivo, zero contas ────────────────────────────────────────────────

def test_item_vivo_sem_contas_nao_e_sucesso(user_id, monkeypatch, relogio_fixo):
    conexao = _conexao(user_id)
    _mock_pluggy(monkeypatch, item=ITEM_SAUDAVEL, contas=[])

    res = ps.sync_pluggy_item("item-g1")

    assert res["ok"] is False
    assert res["reason"] == "no_accounts"
    linha = _linha()
    assert linha["status_reason"] == "no_accounts"
    assert linha["last_sync_at"] == ANTES


# ── 4. DELETED não ressuscita ────────────────────────────────────────────────

def test_webhook_item_updated_nao_ressuscita_deleted(user_id, monkeypatch, relogio_fixo):
    conexao = _conexao(user_id)
    _set_estado(conexao["id"], status="DELETED")

    # o caminho do webhook: status_by_event não escreve mais ACTIVE…
    assert db.update_pluggy_open_finance_item_status("item-g1", "ACTIVE") == 0
    # …e o sync recusa antes de qualquer leitura remota (a rede está bloqueada
    # na suíte: se ele tentasse, o teste estouraria alto).
    res = ps.sync_pluggy_item("item-g1")

    assert res == {"ok": False, "reason": "connection_deleted", "item_id": "item-g1"}
    assert _linha()["status"] == "DELETED"


# ── 5. CONTROLE POSITIVO: ERROR → ACTIVE ─────────────────────────────────────

def test_error_volta_para_active_quando_item_esta_saudavel(user_id, monkeypatch, relogio_fixo):
    conexao = _conexao(user_id)
    _set_estado(conexao["id"], status="ERROR", status_reason="item_missing", last_sync_at=ANTES)
    _mock_pluggy(monkeypatch, item=ITEM_SAUDAVEL,
                 contas=[_conta_pluggy()], txs=[_tx_pluggy()])

    res = ps.sync_pluggy_item("item-g1")

    assert res["ok"] is True, res
    linha = _linha()
    assert linha["status"] == "ACTIVE"
    # Sucesso APAGA o motivo (linha F da tabela em pluggy_health): "ok" era um
    # segundo sentinela para a mesma coisa — o vazio já é o estado verde.
    assert linha["status_reason"] is None
    assert linha["last_sync_at"] == AGORA, "sucesso avança last_sync_at"
    assert linha["health"]["item_status"] == "UPDATED"
    assert _espelho(conexao["id"]) == (1, 1)


# ── 6. PAUSED continua terminal ──────────────────────────────────────────────

def test_paused_barra_o_sync(user_id, relogio_fixo):
    conexao = _conexao(user_id)
    _set_estado(conexao["id"], status="PAUSED")

    assert ps.sync_pluggy_item("item-g1") == {
        "ok": False, "reason": "connection_paused", "item_id": "item-g1",
    }


# ── 7. tentativa ≠ sucesso ───────────────────────────────────────────────────

def test_falha_avanca_tentativa_e_nao_sucesso(user_id, monkeypatch, relogio_fixo):
    conexao = _conexao(user_id)
    _set_estado(conexao["id"], last_attempt_at=ANTES, last_sync_at=ANTES)
    _mock_pluggy(monkeypatch, item=ITEM_SAUDAVEL, contas=[])

    ps.sync_pluggy_item("item-g1")

    linha = _linha()
    assert linha["last_attempt_at"] == AGORA, "tentamos: last_attempt_at anda"
    assert linha["last_sync_at"] == ANTES, "não deu certo: last_sync_at fica"


# ── 8. quem tira de DELETED é a reconexão explícita ──────────────────────────

def test_so_reconexao_explicita_sai_de_deleted(user_id, monkeypatch, relogio_fixo):
    conexao = _conexao(user_id)
    _set_estado(conexao["id"], status="DELETED")

    # webhook e sync não tiram
    db.update_pluggy_open_finance_item_status("item-g1", "ACTIVE")
    ps.sync_pluggy_item("item-g1")
    assert _linha()["status"] == "DELETED"

    # o usuário reconecta pelo widget → upsert com o item remoto
    db.save_pluggy_open_finance_item(
        user_id,
        {"id": "item-g1", "status": "UPDATED", "connector": {"id": 612, "name": "Nubank"}},
    )
    assert _linha()["status"] == "UPDATED"


# ── 9. item vivo SÓ com investimento (corretora) ─────────────────────────────
# Defeito medido: o early-return de `no_accounts` acontecia ANTES de
# `list_pluggy_investments`. Corretora (XP/Rico/BTG/Warren) devolve `/accounts`
# vazio porque a carteira vive em `/investments` — a conexão nunca espelhava
# nada e `last_sync_at` congelava para sempre.
# CONTROLE NEGATIVO: mover o `if not accounts: ... return` para antes da leitura
# de investimentos (como era) deixa este teste vermelho em três asserções.

def test_item_so_com_investimento_espelha_e_e_sucesso(user_id, monkeypatch, relogio_fixo):
    conexao = _conexao(user_id, "item-corretora")
    _mock_pluggy(monkeypatch, item={**ITEM_SAUDAVEL, "id": "item-corretora"}, contas=[])
    monkeypatch.setattr(ps, "list_pluggy_investments", lambda i, k=None: [
        {"id": "inv-1", "name": "CDB Nu", "type": "FIXED_INCOME", "subtype": "CDB",
         "currencyCode": "BRL", "balance": "1500.00"},
    ])

    res = ps.sync_pluggy_item("item-corretora")

    assert res["ok"] is True, res
    with get_conn() as c:
        with c.cursor() as cur:
            cur.execute("select count(*) as n from open_finance_investments where connection_id=%s",
                        (conexao["id"],))
            assert int(cur.fetchone()["n"]) == 1, "o investimento tinha que ter sido espelhado"
    linha = _linha("item-corretora")
    assert linha["last_sync_at"] == AGORA, "item que espelhou investimento sincronizou"
    assert linha["status_reason"] is None


def test_item_sem_conta_e_sem_investimento_continua_no_accounts(user_id, monkeypatch, relogio_fixo):
    """CONTROLE POSITIVO do conserto acima: quem não traz NADA continua não sendo
    sucesso — o conserto não pode transformar todo item vazio em verde."""
    _conexao(user_id, "item-vazio")
    _mock_pluggy(monkeypatch, item={**ITEM_SAUDAVEL, "id": "item-vazio"}, contas=[])

    res = ps.sync_pluggy_item("item-vazio")

    assert res["ok"] is False and res["reason"] == "no_accounts"
    assert _linha("item-vazio")["last_sync_at"] == ANTES


# ── 10. `no_accounts` não pode virar "Atualizado" na tela ────────────────────

def test_no_accounts_nunca_vira_estado_verde(user_id, monkeypatch, relogio_fixo):
    _conexao(user_id, "item-semconta")
    _mock_pluggy(monkeypatch, item={**ITEM_SAUDAVEL, "id": "item-semconta"}, contas=[])

    ps.sync_pluggy_item("item-semconta")

    conexoes = db.get_open_finance_snapshot(user_id)["connections"]
    ui = [c["ui"] for c in conexoes if c["provider_item_id"] == "item-semconta"][0]
    assert ui["state"] != "updated", "item que não espelhou nada não é 'Atualizado'"
    assert ui["state"] == "no_accounts"
    assert ui["label"] == "Sem dados"


# ── 11. conectar ≠ sincronizar ──────────────────────────────────────────────
# `save_pluggy_open_finance_item` carimbava last_sync_at=now(): a conexão nascia
# "Atualizado agora" e `user_synced_within` devolvia True sem sync nenhum.
# CONTROLE NEGATIVO: repor `last_sync_at = excluded.last_sync_at` no upsert deixa
# as duas primeiras asserções vermelhas.

def test_conectar_nao_carimba_sucesso(user_id):
    conexao = db.save_pluggy_open_finance_item(
        user_id, {"id": "item-novo", "status": "UPDATING",
                  "connector": {"id": 612, "name": "Nubank"}})

    assert conexao["last_sync_at"] is None, "conectar não é sincronizar"
    assert db.user_synced_within(user_id, 60) is False, \
        "sem sync, nada pode segurar o e-mail dos agentes"
    ui = db.get_open_finance_snapshot(user_id)["connections"][0]["ui"]
    assert ui["state"] == "updating", "recém-conectado é 'Atualizando…', não 'Atualizado'"


def test_reconexao_saindo_de_deleted_continua_funcionando(user_id, monkeypatch, relogio_fixo):
    """CONTROLE POSITIVO do teste acima: o upsert PODE (e deve) continuar mexendo
    no status — é ele que tira a conexão de DELETED quando o usuário reconecta.
    E um sync posterior é que carimba o sucesso."""
    conexao = _conexao(user_id, "item-volta")
    _set_estado(conexao["id"], status="DELETED", last_sync_at=None)

    db.save_pluggy_open_finance_item(
        user_id, {"id": "item-volta", "status": "UPDATED",
                  "connector": {"id": 612, "name": "Nubank"}})
    assert _linha("item-volta")["status"] == "UPDATED"

    _mock_pluggy(monkeypatch, item={**ITEM_SAUDAVEL, "id": "item-volta"},
                 contas=[_conta_pluggy("acc-volta")], txs=[_tx_pluggy("tx-volta")])
    assert ps.sync_pluggy_item("item-volta")["ok"] is True
    assert _linha("item-volta")["last_sync_at"] == AGORA


# ── 11b. ONDA 2: o job de saúde não pode pintar de verde o que nunca sincronizou
# Caminho medido, todo em código deste repositório: o upsert zera `health`, o job
# de saúde é elegível na hora (`health is null`), o `GET /items` volta saudável e
# `mark_sync_result(ok=None)` grava o health SEM tocar em `last_sync_at`. O ramo
# do health de `connection_ui_state` devolvia "updated" — a conexão nascia
# "Tudo em dia!" com "Última sync: pendente" na linha de baixo e zero contas
# espelhadas. Este teste é o par negativo+positivo da guarda: sem ela a 1ª metade
# fica vermelha; se ela recusasse tudo, a 2ª metade ficaria.

def test_recem_conectado_com_item_saudavel_nao_e_atualizado(user_id, monkeypatch, relogio_fixo):
    db.save_pluggy_open_finance_item(
        user_id, {"id": "item-fresco", "status": "UPDATED",
                  "connector": {"id": 612, "name": "Nubank"}})
    monkeypatch.setattr(ps, "create_pluggy_api_key", lambda: "k")
    monkeypatch.setattr(ps, "get_pluggy_item",
                        lambda i, k=None: {**ITEM_SAUDAVEL, "id": i})

    ps.run_of_health_check()

    linha = _linha("item-fresco")
    assert linha["health"], "o job mediu a saúde (é o que torna o bug alcançável)"
    assert linha["last_sync_at"] is None, "medir saúde não é sincronizar"
    ui = db.get_open_finance_snapshot(user_id)["connections"][0]["ui"]
    assert ui["state"] != "updated", "item vivo na Pluggy não é espelho nosso"
    assert ui["detail"] == "Ainda não sincronizou"

    # CONTROLE POSITIVO: o sync real é que libera o verde.
    _mock_pluggy(monkeypatch, item={**ITEM_SAUDAVEL, "id": "item-fresco"},
                 contas=[_conta_pluggy("acc-fresco")], txs=[_tx_pluggy("tx-fresco")])
    assert ps.sync_pluggy_item("item-fresco")["ok"] is True

    assert _linha("item-fresco")["last_sync_at"] == AGORA
    ui = db.get_open_finance_snapshot(user_id)["connections"][0]["ui"]
    assert ui["state"] == "updated", "sync concluído: agora sim"


def test_conexao_nova_sem_contas_mostra_sem_dados_e_nao_o_generico(user_id, monkeypatch, relogio_fixo):
    """O helper `_conexao()` carimba `last_sync_at=ANTES`, então TODO teste de
    `no_accounts` da Onda 1 nasce do lado da tabela onde a guarda desta onda não
    age — a categoria era inobservável pela suíte. Aqui a conexão é NOVA
    (last_sync_at NULL, que é o que `mark_sync_result(ok=False)` deixa) e o
    motivo concreto tem de sobreviver: "Sem dados", não "Ainda não sincronizou"."""
    db.save_pluggy_open_finance_item(
        user_id, {"id": "item-novo-vazio", "status": "UPDATED",
                  "connector": {"id": 612, "name": "Nubank"}})
    _mock_pluggy(monkeypatch, item={**ITEM_SAUDAVEL, "id": "item-novo-vazio"}, contas=[])

    res = ps.sync_pluggy_item("item-novo-vazio")

    assert res["ok"] is False and res["reason"] == "no_accounts"
    linha = _linha("item-novo-vazio")
    assert linha["last_sync_at"] is None, "sync sem espelho não é sucesso"
    assert linha["status_reason"] == "no_accounts"
    ui = db.get_open_finance_snapshot(user_id)["connections"][0]["ui"]
    assert ui["state"] == "no_accounts", "o motivo fala mais alto que a falta de sync"
    assert ui["label"] == "Sem dados"


def test_conexao_nova_com_leitura_pela_metade_continua_vermelha(user_id, monkeypatch, relogio_fixo):
    """Irmão do de cima para `read_failed` — a pílula dele é `error` (vermelha) em
    OF_PILL_CLASS, e virar `updating` a rebaixaria para âmbar numa conexão nova."""
    db.save_pluggy_open_finance_item(
        user_id, {"id": "item-novo-429", "status": "UPDATED",
                  "connector": {"id": 612, "name": "Nubank"}})
    _mock_pluggy(monkeypatch, item={**ITEM_SAUDAVEL, "id": "item-novo-429"}, contas=[])
    monkeypatch.setattr(ps, "list_pluggy_investments", lambda i, k=None: (_ for _ in ()).throw(
        PluggyApiError("rate limit", status_code=429)))

    assert ps.sync_pluggy_item("item-novo-429")["reason"] == "read_failed"

    linha = _linha("item-novo-429")
    assert linha["last_sync_at"] is None
    ui = db.get_open_finance_snapshot(user_id)["connections"][0]["ui"]
    assert ui["state"] == "error_recoverable"
    assert ui["detail"] == "Tentaremos de novo automaticamente"


def test_reconexao_nao_devolve_o_verde_sozinha(user_id, monkeypatch, relogio_fixo):
    """Reconectar zera `status_reason` e `health` (linha G) — o que ele NÃO pode
    fazer é devolver o veredito verde antes de um sync novo."""
    conexao = _conexao(user_id, "item-reconecta")
    _set_estado(conexao["id"], status="ERROR", status_reason="item_missing",
                last_sync_at=None)

    db.save_pluggy_open_finance_item(
        user_id, {"id": "item-reconecta", "status": "UPDATED",
                  "connector": {"id": 612, "name": "Nubank"}})
    monkeypatch.setattr(ps, "create_pluggy_api_key", lambda: "k")
    monkeypatch.setattr(ps, "get_pluggy_item",
                        lambda i, k=None: {**ITEM_SAUDAVEL, "id": i})
    ps.run_of_health_check()

    linha = _linha("item-reconecta")
    assert linha["last_sync_at"] is None, "reconectar não é sincronizar"
    ui = db.get_open_finance_snapshot(user_id)["connections"][0]["ui"]
    assert ui["state"] != "updated"
    assert ui["detail"] == "Ainda não sincronizou"


# ── 11c. RODADA CODEX (#162, P2): reconexão de quem JÁ tinha sincronizado ────
# O apontamento: o upsert preserva o `last_sync_at` velho de propósito, então uma
# guarda que só pergunta "existe last_sync_at?" aceita o carimbo PRÉ-reconexão —
# e o espelho velho volta à tela como "Atualizado" assim que o job de saúde mede
# o item novo como saudável, antes de a nova autorização ter sincronizado nada.
# O meu teste da rodada anterior escapava disso por acidente: ele zerava o
# `last_sync_at`, que é justamente o caso fácil.
# CONTROLE NEGATIVO: trocar `sem_sync` por `ultimo is None` em
# `connection_ui_state` deixa o 1º teste vermelho.

def test_reconexao_nao_reaproveita_o_sync_anterior(user_id, monkeypatch, relogio_fixo):
    _conexao(user_id, "item-religa")                    # nasce com last_sync_at=ANTES
    assert _linha("item-religa")["last_sync_at"] == ANTES

    db.save_pluggy_open_finance_item(
        user_id, {"id": "item-religa", "status": "UPDATED",
                  "connector": {"id": 612, "name": "Nubank"}})
    linha = _linha("item-religa")
    assert linha["last_sync_at"] == ANTES, "reconectar não pode MEXER no last_sync_at"
    assert linha["reconnected_at"] is not None, "mas tem que registrar a reconexão"

    monkeypatch.setattr(ps, "create_pluggy_api_key", lambda: "k")
    monkeypatch.setattr(ps, "get_pluggy_item", lambda i, k=None: {**ITEM_SAUDAVEL, "id": i})
    ps.run_of_health_check()

    ui = db.get_open_finance_snapshot(user_id)["connections"][0]["ui"]
    assert ui["state"] != "updated", "espelho de antes da reconexão não é 'Atualizado'"
    assert ui["detail"] == "Ainda não sincronizou"


@pytest.mark.parametrize("religado_em, rotulo", [(AGORA, "no mesmo instante"),
                                                 (ANTES, "estritamente antes")])
def test_sync_depois_da_reconexao_devolve_o_verde(user_id, monkeypatch, relogio_fixo,
                                                  religado_em, rotulo):
    """CONTROLE POSITIVO: sem ele a guarda podia recusar para sempre depois de
    qualquer reconexão — que é pior que o bug.

    Os dois lados da borda: com o relógio congelado o upsert e o sync carimbam o
    MESMO instante, que fixa o `<` (um `<=` no lugar dele ficaria vermelho); o
    segundo caso empurra a reconexão para trás e é a forma comum em produção —
    reconectou, sincronizou depois."""
    _conexao(user_id, "item-religa-ok")
    db.save_pluggy_open_finance_item(
        user_id, {"id": "item-religa-ok", "status": "UPDATED",
                  "connector": {"id": 612, "name": "Nubank"}})
    _set_estado(_linha("item-religa-ok")["id"], reconnected_at=religado_em)

    _mock_pluggy(monkeypatch, item={**ITEM_SAUDAVEL, "id": "item-religa-ok"},
                 contas=[_conta_pluggy("acc-religa")], txs=[_tx_pluggy("tx-religa")])
    assert ps.sync_pluggy_item("item-religa-ok")["ok"] is True

    linha = _linha("item-religa-ok")
    assert linha["last_sync_at"] == AGORA
    assert linha["last_sync_at"] >= linha["reconnected_at"], rotulo
    ui = db.get_open_finance_snapshot(user_id)["connections"][0]["ui"]
    assert ui["state"] == "updated", f"sincronizou depois de reconectar ({rotulo})"


@pytest.mark.parametrize("motivo, esperado, detalhe", [
    ("read_failed", "error_recoverable", "Tentaremos de novo automaticamente"),
    ("no_accounts", "no_accounts", "O banco não devolveu contas nem investimentos"),
])
def test_reconexao_com_sync_falho_mostra_o_motivo_e_nao_o_generico(
        user_id, relogio_fixo, motivo, esperado, detalhe):
    """O ramo SEM health, que a rodada anterior quebrou: `_sync_item_contido`
    (`pluggy_sync.py:574`) grava `mark_sync_result(ok=False, status=None,
    status_reason=...)` SEM passar health, então `coalesce(null, health)` deixa
    o health NULL e a conexão desce pelo ramo de baixo de `connection_ui_state`.

    Reconectou + sync falhou = o motivo tem que falar. Dizer "Atualizando…" ali
    é falso (ninguém está atualizando) e, no `read_failed`, rebaixa a pílula de
    `error` (vermelha) para `pending` (âmbar) — ver OF_PILL_CLASS em
    frontend/settings.html:2793.

    CONTROLE NEGATIVO: trocar o `ultimo is None` do fim de `connection_ui_state`
    por `sem_sync` deixa os dois casos vermelhos."""
    conexao = _conexao(user_id, f"item-religa-{motivo}")   # last_sync_at = ANTES
    db.save_pluggy_open_finance_item(
        user_id, {"id": f"item-religa-{motivo}", "status": "UPDATED",
                  "connector": {"id": 612, "name": "Nubank"}})

    db.mark_sync_result(conexao["id"], ok=False, status=None, status_reason=motivo)

    linha = _linha(f"item-religa-{motivo}")
    assert linha["health"] is None, "o handler do lote não mede saúde"
    assert linha["last_sync_at"] == ANTES and linha["reconnected_at"] == AGORA
    ui = db.get_open_finance_snapshot(user_id)["connections"][0]["ui"]
    assert ui["state"] == esperado, "o motivo tem que sobreviver à reconexão"
    assert ui["detail"] == detalhe


# ── 11d. RODADA CODEX 2 (#162, P2): reconexão NO MEIO de um sync ────────────
# A fase de leitura roda FORA do lock (`pluggy_sync.py:263-300`, transações
# paginadas por conta) e o carimbo de tentativa só vem DEPOIS dele (`:312`),
# então `last_attempt_at` não serve de início de corrida. Enumerando sync ×
# reconexão sobram quatro interposições, e só uma estava aberta:
#
#   R … início … fim   → fim > R, verde                        ok
#   início … fim … R   → fim < R, não-verde                    ok (commit d1550ed)
#   início … R … fim   → fim > R com dado da autorização VELHA  ← este teste
#   início … R … falha → sem carimbo, o motivo fala            ok
#
# CONTROLE NEGATIVO: tirar o `reconnected_at_visto` do `mark_sync_result` em
# `pluggy_sync.py` deixa o 1º teste vermelho.

def test_reconexao_no_meio_do_sync_nao_carimba_sucesso(user_id, monkeypatch, relogio_fixo):
    """O sync leu tudo sob a autorização antiga; o usuário reconectou enquanto
    ele lia. O run inteiro é descartado: nem espelho, nem carimbo.

    A versão anterior deste teste afirmava "o espelho FICA (o dado é real, só
    velho)". Estava errado, e a rodada 5 do Codex mostrou por quê — ver 11e."""
    conexao = _conexao(user_id, "item-corrida")
    _mock_pluggy(monkeypatch, item={**ITEM_SAUDAVEL, "id": "item-corrida"},
                 contas=[_conta_pluggy("acc-corrida")], txs=[_tx_pluggy("tx-corrida")])

    # A reconexão acontece DEPOIS de o sync ler a linha e no meio da leitura
    # remota — que é onde ela cabe na vida real (a leitura leva minutos).
    real = ps.list_pluggy_transactions
    def reconecta_no_meio(account_id, api_key=None, **kw):
        db.save_pluggy_open_finance_item(
            user_id, {"id": "item-corrida", "status": "UPDATED",
                      "connector": {"id": 612, "name": "Nubank"}})
        return real(account_id, api_key, **kw)
    monkeypatch.setattr(ps, "list_pluggy_transactions", reconecta_no_meio)

    res = ps.sync_pluggy_item("item-corrida")

    assert res["reason"] == "stale_authorization"
    linha = _linha("item-corrida")
    assert _espelho(conexao["id"]) == (0, 0), "run de geração velha não escreve espelho"
    assert linha["last_sync_at"] == ANTES, \
        "sync que começou antes da reconexão não carimba sucesso depois dela"
    ui = db.get_open_finance_snapshot(user_id)["connections"][0]["ui"]
    assert ui["state"] != "updated"


def test_sync_sem_reconexao_no_meio_carimba_normalmente(user_id, monkeypatch, relogio_fixo):
    """CONTROLE POSITIVO: a checagem otimista não pode recusar o caso comum —
    ninguém reconectou, o `reconnected_at` continua o mesmo (aqui, NULL), e o
    sucesso é carimbado. Sem isto, a guarda passaria num código que recusa tudo."""
    _conexao(user_id, "item-sem-corrida")
    assert _linha("item-sem-corrida")["reconnected_at"] is None
    _mock_pluggy(monkeypatch, item={**ITEM_SAUDAVEL, "id": "item-sem-corrida"},
                 contas=[_conta_pluggy("acc-sc")], txs=[_tx_pluggy("tx-sc")])

    assert ps.sync_pluggy_item("item-sem-corrida")["ok"] is True

    assert _linha("item-sem-corrida")["last_sync_at"] == AGORA
    ui = db.get_open_finance_snapshot(user_id)["connections"][0]["ui"]
    assert ui["state"] == "updated"


# ── 12. RODADA 3: a máquina de estados, evento por evento ───────────────────
# A tabela vive no topo de `core/services/pluggy_health.py`. Estes testes são a
# tabela executável — cada um é uma linha dela.
#
# CONTROLE NEGATIVO DO GRUPO (medido, ver relatório): trocar o
# `resolve_connection_state(...)` do sucesso por `status="ACTIVE",
# status_reason="ok"` fixo e o do 404-que-voltou por `status_reason=volta`
# (a versão da rodada 2) deixa 3 destes vermelhos.

def test_429_em_investimentos_nao_descarta_as_contas_ja_lidas(user_id, monkeypatch, relogio_fixo):
    """REGRESSÃO medida contra o HEAD: a leitura de `/investments` subiu para
    antes de qualquer escrita, então um 429 nela jogava fora contas e transações
    já lidas (até 60 requisições paginadas por conta). Fail-soft: o espelho das
    contas não pode custar isso."""
    conexao = _conexao(user_id)
    _mock_pluggy(monkeypatch, item=ITEM_SAUDAVEL,
                 contas=[_conta_pluggy()], txs=[_tx_pluggy()])
    monkeypatch.setattr(ps, "list_pluggy_investments", lambda i, k=None: (_ for _ in ()).throw(
        PluggyApiError("rate limit", status_code=429)))

    res = ps.sync_pluggy_item("item-g1")

    assert _espelho(conexao["id"]) == (1, 1), "contas e transações lidas TÊM que ser gravadas"
    assert res["ok"] is True
    assert res["investments_ok"] is False, "o sync tem que dizer que leu pela metade"
    assert _linha()["last_sync_at"] == AGORA


def test_leitura_incompleta_com_zero_contas_nao_vira_no_accounts(user_id, monkeypatch, relogio_fixo):
    """'não consegui ler' ≠ 'li e veio vazio' (linhas D × E da tabela). Sem esta
    distinção, um 429 em `/investments` acusaria o banco de não ter dado nenhum —
    e `no_accounts` manda a tela dizer "O banco não devolveu contas"."""
    _conexao(user_id, "item-429")
    _mock_pluggy(monkeypatch, item={**ITEM_SAUDAVEL, "id": "item-429"}, contas=[])
    monkeypatch.setattr(ps, "list_pluggy_investments", lambda i, k=None: (_ for _ in ()).throw(
        PluggyApiError("rate limit", status_code=429)))

    res = ps.sync_pluggy_item("item-429")

    assert res["reason"] == "read_failed"
    linha = _linha("item-429")
    assert linha["status_reason"] == "read_failed"
    assert linha["last_sync_at"] == ANTES, "leitura pela metade não é sucesso"
    from core.services.pluggy_health import connection_ui_state
    assert connection_ui_state(linha)["state"] == "error_recoverable"


def test_falha_lendo_contas_registra_a_tentativa(user_id, monkeypatch, relogio_fixo):
    """O carimbo de tentativa mora depois do lock; sem o `except` da fase de
    leitura, uma falha remota não deixava rastro nenhum na linha."""
    _conexao(user_id, "item-leitura")
    _mock_pluggy(monkeypatch, item={**ITEM_SAUDAVEL, "id": "item-leitura"})
    monkeypatch.setattr(ps, "list_pluggy_accounts", lambda i, k=None: (_ for _ in ()).throw(
        PluggyApiError("rate limit", status_code=429)))

    with pytest.raises(PluggyApiError):
        ps.sync_pluggy_item("item-leitura")

    linha = _linha("item-leitura")
    assert linha["last_attempt_at"] == AGORA, "falhar lendo É uma tentativa"
    assert linha["last_sync_at"] == ANTES, "e não é sucesso"


def test_reconectar_pelo_widget_limpa_motivo_e_saude(user_id, relogio_fixo):
    """Linha G: depois de refazer a conexão, a tela não pode continuar dizendo
    'Conexão perdida / Refaça a conexão' — nem herdar a saúde da conexão morta."""
    conexao = _conexao(user_id, "item-refeito")
    _set_estado(conexao["id"], status="ERROR", status_reason="item_missing")
    db.mark_sync_result(conexao["id"], ok=False, status="ERROR",
                        status_reason="item_missing",
                        health={"observed_at": AGORA.isoformat(), "item_status": "MISSING",
                                "execution_status": None, "products": {}, "stale_products": []})
    assert _ui("item-refeito")["state"] == "item_missing"

    db.save_pluggy_open_finance_item(
        user_id, {"id": "item-refeito", "status": "UPDATING",
                  "connector": {"id": 612, "name": "Nubank"}})

    linha = _linha("item-refeito")
    assert linha["status_reason"] is None
    assert linha["health"] is None, "saúde do item morto não vale para o item novo"
    assert _ui("item-refeito")["state"] == "updating"


def _ui(item_id: str) -> dict:
    from core.services.pluggy_health import connection_ui_state
    return connection_ui_state(_linha(item_id))


# ── 13. RODADA 4: o webhook grava o PAR, não só o `status` ──────────────────
# `update_pluggy_open_finance_item_status` escrevia o `status` sozinho: medido,
# `item/error` sobre ACTIVE/no_accounts produzia ERROR/no_accounts — par
# incoerente que a UI ainda mascarava de "Erro temporário". Ele não passa pelo
# `resolve_connection_state` (não observa o item, só repete a Pluggy), mas grava
# o MESMO par que as linhas B/C dariam: ERROR + motivo vazio.
# CONTROLE NEGATIVO (medido): tirar o `status_reason=null` do UPDATE deixa este
# teste vermelho no par.

def test_webhook_item_error_nao_deixa_par_incoerente(user_id, monkeypatch):
    import json

    from fastapi.testclient import TestClient

    import frontend.finance_bot_websocket_custom as dashboard

    conexao = _conexao(user_id, "item-webhook-erro")
    db.mark_sync_result(conexao["id"], ok=False, status="ACTIVE",
                        status_reason="no_accounts", at=None)
    monkeypatch.setenv("PLUGGY_WEBHOOK_SECRET", "test-webhook-secret")
    monkeypatch.setattr("frontend.routes.open_finance._schedule_pluggy_sync", lambda i: None)

    resp = TestClient(dashboard.app).post(
        "/open-finance/pluggy/webhook?token=test-webhook-secret",
        content=json.dumps({"event": "item/error", "itemId": "item-webhook-erro"}).encode(),
        headers={"Content-Type": "application/json"},
    )

    assert resp.status_code == 200, resp.text
    linha = _linha("item-webhook-erro")
    assert (linha["status"], linha["status_reason"]) == ("ERROR", None), linha
    assert _ui("item-webhook-erro")["state"] == "error_recoverable"


def test_webhook_item_error_atrasado_nao_apaga_item_missing(user_id, monkeypatch):
    """A exceção do par: `item/error` entregue com atraso (replay) NÃO pode rebaixar
    "Conexão perdida / Refaça a conexão" para "Erro temporário / Tentaremos de novo".
    CONTROLE NEGATIVO (medido): com `status_reason=null` cru no UPDATE, o par vira
    ('ERROR', None) e a UI vira `error_recoverable` — as duas asserções vermelhas."""
    import json

    from fastapi.testclient import TestClient

    import frontend.finance_bot_websocket_custom as dashboard

    conexao = _conexao(user_id, "item-webhook-sumido")
    db.mark_sync_result(conexao["id"], ok=False, status="ERROR",
                        status_reason="item_missing", at=None)
    monkeypatch.setenv("PLUGGY_WEBHOOK_SECRET", "test-webhook-secret")
    monkeypatch.setattr("frontend.routes.open_finance._schedule_pluggy_sync", lambda i: None)

    resp = TestClient(dashboard.app).post(
        "/open-finance/pluggy/webhook?token=test-webhook-secret",
        content=json.dumps({"event": "item/error", "itemId": "item-webhook-sumido"}).encode(),
        headers={"Content-Type": "application/json"},
    )

    assert resp.status_code == 200, resp.text
    linha = _linha("item-webhook-sumido")
    assert (linha["status"], linha["status_reason"]) == ("ERROR", "item_missing"), linha
    assert _ui("item-webhook-sumido")["state"] == "item_missing"


def test_webhook_item_deleted_continua_terminal(user_id, monkeypatch):
    """CONTROLE POSITIVO: apagar o motivo junto não pode afrouxar o que já valia —
    o webhook continua escrevendo o status que a Pluggy disse, e PAUSED/DELETED
    continuam intocáveis."""
    conexao = _conexao(user_id, "item-webhook-del")
    assert db.update_pluggy_open_finance_item_status("item-webhook-del", "DELETED") == 1
    assert _linha("item-webhook-del")["status"] == "DELETED"

    _set_estado(conexao["id"], status_reason="item_missing")
    assert db.update_pluggy_open_finance_item_status("item-webhook-del", "ERROR") == 0
    linha = _linha("item-webhook-del")
    assert (linha["status"], linha["status_reason"]) == ("DELETED", "item_missing")


def test_sync_de_item_vazio_tira_o_error_e_diz_sem_dados(user_id, monkeypatch, relogio_fixo):
    """A ponta a ponta do defeito: conexão presa em ERROR/item_missing, item vivo,
    banco sem contas. O sync tem que tirar o ERROR e a tela dizer "Sem dados"."""
    conexao = _conexao(user_id, "item-preso")
    db.mark_sync_result(conexao["id"], ok=False, status="ERROR",
                        status_reason="item_missing", at=None)
    assert _ui("item-preso")["state"] == "item_missing"
    _mock_pluggy(monkeypatch, item={**ITEM_SAUDAVEL, "id": "item-preso"}, contas=[])

    res = ps.sync_pluggy_item("item-preso")

    linha = _linha("item-preso")
    assert res["ok"] is False and res["reason"] == "no_accounts"
    assert (linha["status"], linha["status_reason"]) == ("ACTIVE", "no_accounts"), linha
    assert linha["last_sync_at"] == ANTES, "espelho vazio continua não sendo sucesso"
    ui = _ui("item-preso")
    assert (ui["state"], ui["label"]) == ("no_accounts", "Sem dados")


# ── 11e. RODADA CODEX 3 (#162, P2): dois workers, o velho chega por último ───
# O apontamento: o `reconnected_at_visto` recusa só o CARIMBO do run de geração
# velha — as escritas do espelho acontecem TODAS antes dele, e já foram feitas
# quando o carimbo é recusado. Com duas réplicas (o deploy do Railway sobe a
# nova antes de derrubar a velha) a interposição é alcançável:
#
#   A começa … R (reconexão) … B começa … B escreve+carimba … A escreve
#
# B carimbou um `last_sync_at` legítimo, então `connection_ui_state` diz
# "Atualizado" — e A, chegando depois, sobrescreve contas, investimentos,
# `status` e `health` com o snapshot PRÉ-reconexão. Tela verde sobre espelho
# velho, que é o defeito que esta onda existe para tirar. O `_INFLIGHT` não
# cobre: é coalescing por PROCESSO.
#
# O conserto é reler o `reconnected_at` DENTRO do lock e abortar o run inteiro
# antes de qualquer escrita (`_sync_pluggy_item_confirmado`).
#
# CONTROLE NEGATIVO (medido): remover a relectura + o early-return de
# `stale_authorization` de `pluggy_sync.py` deixa 2 testes vermelhos — este e o
# `test_reconexao_no_meio_do_sync_nao_carimba_sucesso` de 11d.
# CONTROLE POSITIVO: `test_sync_sem_reconexao_no_meio_carimba_normalmente`
# (11d) prova que o caminho comum — ninguém reconectou — continua escrevendo e
# carimbando. Sem ele a guarda passaria num código que recusa todo sync.

def test_run_velho_nao_sobrescreve_o_espelho_do_run_novo(user_id, monkeypatch, relogio_fixo):
    """Worker A (pré-reconexão) chega no lock DEPOIS de o worker B ter
    reconectado, espelhado e carimbado. A tem de morrer sem escrever."""
    conexao = _conexao(user_id, "item-2workers")
    # A leu a linha com `reconnected_at` NULL e traz o snapshot VELHO.
    _mock_pluggy(monkeypatch, item={**ITEM_SAUDAVEL, "id": "item-2workers"},
                 contas=[_conta_pluggy("acc-VELHA")], txs=[_tx_pluggy("tx-VELHA")])

    real = ps.list_pluggy_transactions

    def reconecta_e_deixa_o_worker_b_terminar(account_id, api_key=None, **kw):
        # No meio da leitura remota de A: o usuário reconecta…
        db.save_pluggy_open_finance_item(
            user_id, {"id": "item-2workers", "status": "UPDATED",
                      "connector": {"id": 612, "name": "Nubank"}})
        nova = _linha("item-2workers")
        assert nova["reconnected_at"] == AGORA
        # …e o worker B, que começou DEPOIS dela, espelha e carimba primeiro.
        db.save_open_finance_sync(nova["id"], [{
            "provider_account_id": "acc-NOVA", "name": "Conta", "type": "BANK",
            "currency": "BRL", "balance": 2000, "raw": {},
            "transactions": [{"provider_transaction_id": "tx-NOVA",
                              "description": "Mercado", "amount": -10,
                              "transaction_date": AGORA.date(), "raw": {}}],
        }])
        db.mark_sync_result(nova["id"], ok=True, status="ACTIVE", status_reason="",
                            health=ps.derive_item_health(ITEM_SAUDAVEL),
                            reconnected_at_visto=nova["reconnected_at"])
        return real(account_id, api_key, **kw)

    monkeypatch.setattr(ps, "list_pluggy_transactions",
                        reconecta_e_deixa_o_worker_b_terminar)

    res = ps.sync_pluggy_item("item-2workers")

    # O espelho PRIMEIRO: é a afirmação forte, e é ela que o controle negativo
    # tem de derrubar. `ok is False` sozinho passaria num código que só recusa o
    # retorno depois de já ter escrito.
    assert _contas_espelhadas(conexao["id"]) == {"acc-NOVA"}, \
        "o snapshot pré-reconexão não pode voltar ao espelho"
    linha = _linha("item-2workers")
    assert linha["last_sync_at"] == AGORA, "o carimbo legítimo do worker B fica"
    assert res["ok"] is False and res["reason"] == "stale_authorization"
    ui = db.get_open_finance_snapshot(user_id)["connections"][0]["ui"]
    assert ui["state"] == "updated", "e ele está dizendo a verdade: o espelho é o novo"


# ── 11f. ONDA 3: a janela que a relectura NÃO fecha ─────────────────────────
# Desde que o run de geração velha morre na relectura dentro do lock (11e),
# NENHUM teste que passa por `sync_pluggy_item` chega mais ao
# `reconnected_at_visto` — o controle negativo de 11d ("tirar o
# `reconnected_at_visto` do call site") passou a não derrubar nada, ou seja, o
# parâmetro ficaria sem cobertura nenhuma. Ele não é redundante: a rota de
# reconexão (`/pluggy/item` → `save_pluggy_open_finance_item`) NÃO pega o
# `pluggy_item_lock`, então uma reconexão ainda cabe entre a relectura e o
# carimbo. Este teste ataca essa fresta direto no `mark_sync_result`.
#
# CONTROLE NEGATIVO: omitir o `reconnected_at_visto` na 1ª chamada deixa este
# teste vermelho. CONTROLE POSITIVO: a 2ª chamada, com o valor que de fato está
# no banco, carimba — sem ela a guarda passaria recusando todo carimbo.

def test_reconexao_entre_a_relectura_e_o_carimbo_ainda_e_recusada(user_id, relogio_fixo):
    conexao = _conexao(user_id, "item-janela")
    visto = _linha("item-janela")["reconnected_at"]
    assert visto is None, "a relectura de dentro do lock leu isto"

    # …e só DEPOIS dela o usuário reconecta, fora do lock.
    db.save_pluggy_open_finance_item(
        user_id, {"id": "item-janela", "status": "UPDATED",
                  "connector": {"id": 612, "name": "Nubank"}})
    assert _linha("item-janela")["reconnected_at"] == AGORA

    db.mark_sync_result(conexao["id"], ok=True, status="ACTIVE", status_reason="",
                        reconnected_at_visto=visto)
    assert _linha("item-janela")["last_sync_at"] == ANTES, \
        "carimbo com autorização velha não pode avançar o last_sync_at"

    db.mark_sync_result(conexao["id"], ok=True, status="ACTIVE", status_reason="",
                        reconnected_at_visto=AGORA)
    assert _linha("item-janela")["last_sync_at"] == AGORA, \
        "quem leu a autorização ATUAL carimba normalmente"


def test_reconexao_dentro_do_lock_ainda_recusa_o_carimbo(user_id, monkeypatch, relogio_fixo):
    """A mesma janela residual, agora pelo CAMINHO REAL — o call site em
    `pluggy_sync`, não o `mark_sync_result` na mão.

    Este teste existe por causa de uma medição: depois da relectura de 11e, a
    sabotagem "tirar o `reconnected_at_visto` do call site" passou a deixar ZERO
    vermelhos. O parâmetro continuava certo e continuava necessário, mas nada
    mais o exercitava — e parâmetro sem controle negativo é parâmetro que a
    próxima pessoa apaga achando que é resíduo.

    A reconexão é interposta DENTRO do lock (o `import_open_finance_launches`
    roda entre a escrita do espelho e o carimbo), que é exatamente a fresta que
    a relectura não fecha: a rota `/pluggy/item` não pega o `pluggy_item_lock`.

    CONTROLE NEGATIVO: tirar o `reconnected_at_visto` do call site deixa este
    teste vermelho — e volta a dar 1, como na Onda 2."""
    conexao = _conexao(user_id, "item-janela-lock")
    _mock_pluggy(monkeypatch, item={**ITEM_SAUDAVEL, "id": "item-janela-lock"},
                 contas=[_conta_pluggy("acc-jl")], txs=[_tx_pluggy("tx-jl")])

    real = ps.import_open_finance_launches

    def reconecta_dentro_do_lock(uid, cid, *a, **kw):
        db.save_pluggy_open_finance_item(
            user_id, {"id": "item-janela-lock", "status": "UPDATED",
                      "connector": {"id": 612, "name": "Nubank"}})
        return real(uid, cid, *a, **kw)

    monkeypatch.setattr(ps, "import_open_finance_launches", reconecta_dentro_do_lock)

    res = ps.sync_pluggy_item("item-janela-lock")

    assert res["ok"] is True, "passou pela relectura: na entrada do lock era a geração certa"
    linha = _linha("item-janela-lock")
    assert _espelho(conexao["id"]) == (1, 1), "o espelho FICA: o dado é real"
    assert linha["last_sync_at"] == ANTES, \
        "reconectaram entre a relectura e o carimbo — o carimbo não vale"
    ui = db.get_open_finance_snapshot(user_id)["connections"][0]["ui"]
    assert ui["state"] != "updated"


# ── Codex #166: quem espera QR não recebe "reconecte seu banco" ──────────────
# `WAITING_USER_ACTION` grava `status='ERROR'` como os outros de `_NEEDS_USER`,
# então caía em `list_connections_needing_reconnect` e receberia o template
# proativo de reconexão — quando a ação certa é autorizar o dispositivo / ler o
# QR no app do banco, antes do `userAction.expiresAt`. Mandar reconectar é
# empurrar a pessoa para o único caminho que faz PERDER a janela.
#
# Esta superfície estava ENUMERADA e eu tinha decidido não consertá-la ("fluxo
# dormente, template na Meta"). Dormente não é inexistente: quando ligarem o
# `OF_RECONNECT_TEMPLATE_NAME`, o aviso sai errado.
#
# São DOIS campos: `WAITING_USER_ACTION` chega como status de Item (Safra,
# Inter PF), e a Caixa chega como `"status": "OUTDATED"` +
# `"executionStatus": "USER_AUTHORIZATION_PENDING"` — o 2º achado do Codex do
# @hiago. Filtrar só o `item_status` deixava a Caixa recebendo "reconecte".
#
# CONTROLE NEGATIVO: tirar QUALQUER uma das duas condições da query deixa o caso
# correspondente (1º ou 2º) vermelho.
# CONTROLE POSITIVO: os casos 3 e 4 — `OUTDATED` sozinho e `LOGIN_ERROR`
# CONTINUAM sendo avisados. Sem o 3º, filtrar `OUTDATED` inteiro passaria, e o
# aviso sumiria para todo mundo que só precisa reautorizar.

@pytest.mark.parametrize("item_status, execution_status, deve_avisar", [
    ("WAITING_USER_ACTION", None, False),
    ("OUTDATED", "USER_AUTHORIZATION_PENDING", False),
    ("OUTDATED", "SUCCESS", True),
    ("LOGIN_ERROR", None, True),
])
def test_aviso_de_reconexao_pula_quem_espera_autorizacao_no_app(
        user_id, relogio_fixo, item_status, execution_status, deve_avisar):
    item = f"item-aviso-{item_status}-{execution_status}"
    conexao = _conexao(user_id, item)
    _set_estado(conexao["id"], status="ERROR",
                health=Jsonb({"item_status": item_status,
                              "execution_status": execution_status,
                              "products": {}, "stale_products": []}))

    avisadas = {c["provider_item_id"]
                for c in db.list_connections_needing_reconnect(user_id)}

    assert (item in avisadas) is deve_avisar, (
        f"{item_status}/{execution_status}: aviso proativo de reconexão "
        f"{'devia' if deve_avisar else 'NÃO devia'} sair")


# A exclusão de device/QR vale para o `where` INTEIRO, inclusive para a perna do
# consentimento vencendo — e isso é DELIBERADO, não descuido. Eu tinha
# restringido à perna de erro, argumentando que são janelas diferentes (~30 min
# contra 7 dias); o Manager derrubou com dois fatos medidos:
#
#   • `run_reconnect_notifications` manda UM template só, com o nome do banco,
#     para toda linha devolvida — não existe "aviso de renovação" separado. Uma
#     conexão que passasse pela perna do consentimento receberia exatamente o
#     "reconecte seu banco" que este filtro existe para evitar;
#   • a perna do consentimento é morta para `provider='pluggy'`: o upsert grava
#     `consent_expires_at = None` e o ramo de conflito não toca a coluna.
#
# Este teste prende a decisão. Se alguém restringir o filtro à perna de erro
# "consertando" o que parece um efeito colateral, ele fica vermelho.
#
# LIMITE HONESTO: o estado abaixo é montado com UPDATE cru e HOJE é inalcançável
# em produção (nenhum escritor põe `consent_expires_at` numa linha 'pluggy').
# Ele guarda a decisão, não um caminho vivo.

def test_espera_de_dispositivo_nao_recebe_o_aviso_nem_pela_perna_do_consentimento(
        user_id, relogio_fixo):
    item = "item-consent-device"
    conexao = _conexao(user_id, item)
    _set_estado(conexao["id"], status="ACTIVE",
                consent_expires_at=datetime.now(_tz()) + timedelta(days=2),
                health=Jsonb({"item_status": "OUTDATED",
                              "execution_status": "USER_AUTHORIZATION_PENDING",
                              "products": {}, "stale_products": []}))

    avisadas = {c["provider_item_id"]
                for c in db.list_connections_needing_reconnect(user_id)}

    assert item not in avisadas, (
        "o template é UM só e diz 'reconecte seu banco': deixar passar pela "
        "perna do consentimento entrega a instrução que faz perder a janela")


# ── Codex #166 (rodada 4): a janela em que o `health` ainda é NULL ────────────
# O filtro acima olhava só o `health`, e a reconexão o ZERA (`health = null` no
# ramo de conflito do upsert; numa conexão NOVA ele já nasce NULL). Quem escreve
# de volta é o sync de fundo. No meio dos dois, a Caixa — `status: OUTDATED` +
# `executionStatus: USER_AUTHORIZATION_PENDING` — casava com a cláusula de erro
# pelo `OUTDATED`, os dois predicados avaliavam contra `''`, e um tique do aviso
# proativo mandava "reconecte seu banco": a instrução que faz PERDER a janela do
# QR. O `raw` JÁ estava persistido (`Jsonb(item)`), então dava para fechar sem
# tocar na máquina de estados.
#
# Este teste NÃO monta o estado com UPDATE cru de propósito: ele passa pelo
# `save_pluggy_open_finance_item`, que é o caminho de produção que abre a janela.
#
# ALCANCE: este teste cobre o AVISO PROATIVO. A TELA na mesma janela era a metade
# PENDENTE — `get_open_finance_snapshot` não selecionava nada do `raw`, então o
# `connection_ui_state` recebia a linha pronta e mandava "Reautorize o banco" no
# minuto do QR. Fechada: o snapshot passou a selecionar o DERIVADO
# (`SQL_EXECUTION_STATUS`, escalar; o `raw` inteiro continua sem sair do
# Postgres), e o grupo "a TELA na janela do device/QR", no fim deste arquivo,
# prende as duas superfícies com a MESMA condição e o MESMO prazo.
#
# CONTROLE NEGATIVO: tirar o `raw->>'executionStatus'` da query → o caso da Caixa
# fica vermelho (volta a ser avisado).
# CONTROLE POSITIVO: o `LOGIN_ERROR` com `health` NULL CONTINUA sendo avisado —
# sem ele, um fallback que casasse demais teria calado o aviso inteiro e o teste
# passaria mesmo assim.
# CONTROLE do `case when health is null`: o 3º caso. Com `coalesce` puro
# (`health->>…, raw->>…`), o `health` observado DEPOIS (usuário autorizou, virou
# LOGIN_ERROR, sem `execution_status`) cairia no `raw` VELHO — `mark_sync_result`
# não toca em `raw` — e calaria o aviso PARA SEMPRE. Fica vermelho sem o `case`.

def test_aviso_pula_o_QR_na_janela_em_que_o_health_ainda_e_null(user_id, relogio_fixo):
    db.save_pluggy_open_finance_item(user_id, {
        "id": "item-null-caixa", "status": "OUTDATED",
        "executionStatus": "USER_AUTHORIZATION_PENDING",
        "connector": {"id": 219, "name": "Caixa"}})
    db.save_pluggy_open_finance_item(user_id, {
        "id": "item-null-login", "status": "LOGIN_ERROR",
        "connector": {"id": 612, "name": "Nubank"}})
    db.save_pluggy_open_finance_item(user_id, {
        "id": "item-null-mfa", "status": "WAITING_USER_INPUT",
        "connector": {"id": 612, "name": "Nubank"}})
    # Mesmo `raw` da Caixa, mas o sync JÁ observou: o usuário autorizou o
    # dispositivo e o que sobrou foi credencial. Aqui o `raw` é passado.
    autorizou = db.save_pluggy_open_finance_item(user_id, {
        "id": "item-autorizou", "status": "OUTDATED",
        "executionStatus": "USER_AUTHORIZATION_PENDING",
        "connector": {"id": 219, "name": "Caixa"}})
    _set_estado(autorizou["id"], status="ERROR",
                health=Jsonb({"item_status": "LOGIN_ERROR", "execution_status": None,
                              "products": {}, "stale_products": []}))

    avisadas = {c["provider_item_id"]
                for c in db.list_connections_needing_reconnect(user_id)}

    assert "item-null-caixa" not in avisadas, \
        "health NULL + raw da Caixa: 'reconecte seu banco' faz perder a janela do QR"
    assert "item-null-login" in avisadas, \
        "CONTROLE POSITIVO: erro comum com health NULL continua sendo avisado"
    assert "item-null-mfa" in avisadas, \
        "CONTROLE POSITIVO: MFA pendente com health NULL continua sendo avisado"
    assert "item-autorizou" in avisadas, \
        "o `raw` só vale enquanto o `health` é NULL — senão o aviso morre para sempre"


# O teste acima entra pelo INSERT do upsert (item que nunca existiu). A reconexão
# de PRODUÇÃO entra pelo CONFLITO — é o único ramo que executa `health = null` +
# `raw = excluded.raw` + `reconnected_at`, e é ele que ABRE a janela. Cobrir só o
# INSERT deixava sem teste o caminho que importa (Codex #166, rodada 5).
#
# CONTROLE NEGATIVO (medido, ver o relato): tirar o `raw->>'executionStatus'` da
# query → o passo 2 fica vermelho. Tirar o `health = null` do ramo de conflito →
# o passo 2 também (o `health` bom sobrevive e não há por que consultar o `raw`).
# CONTROLE do `case when health is null`: o passo 3 — com `coalesce` puro o `raw`
# VELHO calaria o aviso para sempre, porque `mark_sync_result` não toca em `raw`.
#
# SEM `relogio_fixo`, e isto é load-bearing desde que o `case` ganhou o PRAZO
# (`SQL_RAW_AINDA_VALE`): o prazo é avaliado contra o `now()` do POSTGRES, e o
# `relogio_fixo` só falsifica o relógio do PYTHON — ele carimbaria o
# `reconnected_at` desta reconexão em 2026-08-20 (medido: 26 dias atrás do
# `now()` do banco na data desta sessão), que é FORA da janela, e o passo 2 leria
# como "aviso volta" um caso que quer dizer "acabei de reconectar". Nenhuma
# asserção deste teste depende de data fixa. O prazo vencido tem teste próprio
# (`test_passado_o_prazo_a_tela_e_o_aviso_voltam_a_mandar_reautorizar`), e ali o
# recuo é feito no BANCO (`_envelhece_autorizacao`), não no Python.

def test_reconexao_pelo_ramo_do_CONFLITO_cala_o_aviso_so_ate_o_health_voltar(
    user_id
):
    # 1) conexão que JÁ existia, saudável e com `health` medido
    conexao = db.save_pluggy_open_finance_item(user_id, {
        "id": "item-conflito", "status": "ACTIVE", "executionStatus": "SUCCESS",
        "connector": {"id": 219, "name": "Caixa"}})
    db.mark_sync_result(
        conexao["id"], ok=True, status="ACTIVE", status_reason="",
        health={"item_status": "UPDATED", "execution_status": "SUCCESS",
                "products": {}, "stale_products": []})
    assert _linha("item-conflito")["health"] is not None

    # 2) o usuário reconecta: MESMO provider_item_id → ramo do CONFLITO
    db.save_pluggy_open_finance_item(user_id, {
        "id": "item-conflito", "status": "OUTDATED",
        "executionStatus": "USER_AUTHORIZATION_PENDING",
        "connector": {"id": 219, "name": "Caixa"}})
    linha = _linha("item-conflito")
    assert linha["health"] is None, "o ramo do conflito tem de ZERAR o health"
    assert "item-conflito" not in _avisadas(user_id), (
        "health NULL + raw da Caixa: 'reconecte seu banco' faz perder a janela do "
        "QR — e este raw só está aqui se o conflito trocou `raw = excluded.raw`")

    # 3) o job de saúde observa DEPOIS: autorizou o QR, sobrou credencial
    db.mark_sync_result(
        linha["id"], ok=None, status="ERROR", status_reason="login_error",
        health={"item_status": "LOGIN_ERROR", "execution_status": None,
                "products": {}, "stale_products": []})
    assert "item-conflito" in _avisadas(user_id), (
        "o `raw` da reconexão continua VELHO (mark_sync_result não o toca): sem o "
        "`case when health is null` o aviso morria para sempre")


# ─────────────────────────────────────────────────────────────────────────────
# A TELA na janela do device/QR — a metade que faltava do Codex #166
#
# O DEFEITO, medido nos dois ramos do `connection_ui_state` com o MESMO item cru
# da Caixa (`status: OUTDATED` + `executionStatus: USER_AUTHORIZATION_PENDING`):
#
#   COM health -> detail = 'Autorize o acesso no app do banco'
#   SEM health -> detail = 'Reautorize o banco'
#
# Mesmo estado, mesmo rótulo ("Ação necessária"), instrução OPOSTA. O ramo sem
# health é o que o upsert produz — `save_pluggy_open_finance_item` grava
# `status = item['status'] or item['executionStatus']` (→ `OUTDATED`) e
# `health = null` —, ou seja, é o caminho de TODA conexão recém-gravada, inclusive
# o `POST /pluggy-item`, que monta o snapshot ANTES do sync de fundo. E
# "Reautorize o banco" no minuto do QR é a instrução que faz PERDER a janela.
#
# O CONSERTO: o snapshot passou a selecionar um DERIVADO calculado no Postgres
# (`SQL_EXECUTION_STATUS`, em `db/open_finance_state.py`) — só o ESCALAR viaja, o
# `raw` inteiro nunca sai do banco (ele carrega `clientUserId`) —, válido só
# enquanto `health is null` E a autorização atual couber em
# `JANELA_DEVICE_AUTH_MIN`. Ele é removido do dict (`pop`) antes da serialização:
# o corpo HTTP fica idêntico em chaves ao de antes.
#
# NENHUM teste deste grupo monta `health` ou linha na mão: todos entram por
# `save_pluggy_open_finance_item` (o caminho de produção) e saem pelo
# `get_open_finance_snapshot` ou pela ROTA. Montar `health` à mão não cobriria o
# caminho que quebrava.
#
# O grupo tem METADES INDEPENDENTES, e cada uma tem o seu controle negativo —
# uma injeção só não discrimina todas. As QUATRO foram MEDIDAS, não deduzidas.
#
# Os VERMELHOS vão por NODE ID, e não por apelido ("caso 7", "a perna de +1
# min"): apelido deixa de bater no dia em que um `parametrize` é renomeado, e o
# node id é a única parte que sobrevive. Sem `N passed` aqui, de propósito
# (`docs/controles_declarados.md`) — contagem envelhece em silêncio a cada teste
# novo do arquivo; o que prende é o nome. Nos ids com parâmetro, o miolo vai
# abreviado (`…`): o prefixo basta para o `-k`.
#
#   (A) desligar o DETALHE: reverter `connection_ui_state` para
#       `_detalhe_de_acao(status)` (um argumento só). Vermelhos:
#         test_caixa_sem_health_manda_autorizar_o_dispositivo_e_nao_reautorizar
#         test_rota_do_snapshot_entrega_a_instrucao_de_dispositivo
#         test_POST_pluggy_item_ja_nasce_com_a_instrucao_certa
#         test_dentro_do_prazo_o_aviso_continua_calado
#         test_o_piso_do_prazo_vale_60_minutos[55-…-False]
#         test_carimbo_no_futuro_nao_reabre_o_silencio_permanente[1-…-False]
#         test_a_folga_do_teto_vale_ate_5_minutos_exatos[299-…] e [300-…]
#         test_as_celulas_que_mudaram_na_varredura_ficam_na_instrucao_de_dispositivo
#           — os SETE params
#       O call site é UM SÓ: o do ramo SEM `health` (`_detalhe_de_acao(status)`).
#       O ramo COM `health` já passava os dois argumentos na `main` — injetar lá
#       também derruba `test_caixa_com_health_medido_diz_a_MESMA_coisa_que_sem_health`,
#       que este PR não mudou, e a injeção passa a acusar código alheio.
#       Verdes: casos 4, 5, 6, 7, a perna de 61 min do 8a, a de +10 dias do 8b, a
#       de 5m30s do 8c e o do vazamento.
#       O caso 7 fica VERDE de propósito e isso NÃO é buraco: ele afirma
#       "Reautorize o banco", que é justamente o que o código quebrado devolve.
#       Quem o discrimina é a injeção (B).
#   (B) desligar o PISO do prazo: trocar
#       `coalesce(reconnected_at, created_at) > now() - make_interval(mins => %s)`
#       por uma tautologia que consome o mesmo `%s` (`(%s::int is not null)`).
#       Vermelhos:
#         test_passado_o_prazo_a_tela_e_o_aviso_voltam_a_mandar_reautorizar
#         test_o_piso_do_prazo_vale_60_minutos[61-…-True]
#       Esta injeção prova que o piso EXISTE, e não que ele vale 60 — quem prende
#       o VALOR é o caso 8a, e a prova dele é mutar a CONSTANTE, não o predicado
#       (`JANELA_DEVICE_AUTH_MIN` em 1 → a perna de 55 vermelha; em 1440 → a de
#       61). As duas pernas do 8a estão nas duas listas por isso.
#   (B') desligar o TETO do prazo: ALARGAR o literal, de `interval '5 minutes'`
#       para `interval '10 years'`. Vermelhos:
#         test_carimbo_no_futuro_nao_reabre_o_silencio_permanente[14400-…-True]
#         test_a_folga_do_teto_vale_ate_5_minutos_exatos[330-…-True]
#       e as pernas de +1 min, 4m59s e 5m00s seguem VERDES, que é o que prova a
#       folga ser decisão medida e não número solto.
#   (C) desligar o derivado em `get_connections_by_item_id`: `case when %s::int
#       is null then null end as execution_status`, que consome o mesmo `%s`.
#       Vermelho:
#         test_caixa_sem_health_manda_autorizar_o_dispositivo_e_nao_reautorizar
#       — e é a 2ª asserção dele, a do TOAST, que passa por
#       `_refresh_items_report`; a 1ª, a do snapshot, segue verde. É o que prova
#       serem DOIS selects.
#
# NENHUMA das quatro APAGA texto, e isso não é estilo. Apagar uma das condições
# do `SQL_RAW_AINDA_VALE` deixa um `and` pendurado no fim da string — e, no piso e
# no (C), ainda tira o `%s` sem tirar o parâmetro. O SQL não compila, o `psycopg`
# derruba o ARQUIVO INTEIRO: caem junto os controles POSITIVOS e as pernas que a
# própria injeção declara VERDES, e quem seguisse o texto concluiria o OPOSTO do
# que ele afirma. Esta era exatamente a redação anterior da (B'), e o eixo dela
# foi trocado por isso. É a família que o `docs/controles_declarados.md` nomeia:
# "o remédio é trocar o eixo da injeção: ALARGUE em vez de apagar".
#
# CONTROLE POSITIVO: casos 4, 5 e 6 — o caminho legítimo de "Reautorize o banco"
# (e o de "Atualizando…") continua funcionando. Sem eles, o grupo passaria num
# código que mandasse "Autorize o acesso no app do banco" para todo mundo, que é
# pior que o bug. Eles ficam verdes nas QUATRO injeções.

DETALHE_DISPOSITIVO = "Autorize o acesso no app do banco"
DETALHE_REAUTORIZA = "Reautorize o banco"

# Item cru da Caixa como a Pluggy o devolve na espera de autorização de
# dispositivo: `status` OUTDATED com o `executionStatus` ao lado (nunca o
# contrário — varredura das 183 páginas da doc, ver `pluggy_health.py`).
ITEM_CAIXA_QR = {
    "id": "item-tela-caixa", "status": "OUTDATED",
    "executionStatus": "USER_AUTHORIZATION_PENDING",
    "clientUserId": "1",
    "connector": {"id": 219, "name": "Caixa"},
}

# Chaves que o corpo HTTP entrega HOJE por conexão: o select de
# `get_open_finance_snapshot` + o `ui` montado no laço. O derivado NÃO está aqui,
# e é isso que o `pop` garante.
CHAVES_DA_CONEXAO = {
    "id", "provider", "provider_item_id", "status", "institution_name",
    "last_sync_at", "last_attempt_at", "status_reason", "health",
    "reconnected_at", "ui",
}


def _auth(client: TestClient, uid: int, email: str = "of@t.com") -> dict:
    client.cookies.set(dashboard.AUTH_COOKIE_NAME, dashboard._make_jwt(uid, email))
    client.cookies.set(dashboard.DASHBOARD_COOKIE_NAME,
                       dashboard.make_dashboard_token(uid, hours=1))
    token = "test-csrf-token"
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, token)
    return {dashboard.CSRF_HEADER_NAME: token, "Content-Type": "application/json"}


def _ui_da_tela(uid: int, item_id: str) -> dict:
    """O `ui` que a TELA recebe, pelo SNAPSHOT.

    Distinto do `_ui(item_id)` lá de cima de propósito: aquele passa por
    `get_connections_by_item_id` (o caminho INTERNO, que o toast do /refresh
    usa), este por `get_open_finance_snapshot` (o caminho da TELA). São dois
    selects diferentes, e o defeito vivia em um deles.
    """
    conexoes = db.get_open_finance_snapshot(uid)["connections"]
    linha = next(c for c in conexoes if c["provider_item_id"] == item_id)
    return linha["ui"]


def _envelhece_autorizacao(connection_id: int, minutos: int = 0, segundos: int = 0) -> None:
    """Recua a autorização NO BANCO (valor negativo = avança para o FUTURO).

    Tem de ser em SQL: o prazo é avaliado contra o `now()` do POSTGRES, então
    recuar pelo relógio do Python (o `relogio_fixo` deste arquivo) não mexeria na
    conta — foi o que fez um teste verde deste arquivo ficar vermelho quando o
    prazo entrou, porque o `relogio_fixo` carimbava `reconnected_at` 26 dias atrás.
    Recua as DUAS pontas da âncora `coalesce(reconnected_at, created_at)`.

    Os SEGUNDOS existem para a fronteira do teto (4m59s / 5m00s / 5m30s): em
    minutos ela não é expressável, e fazer a conta em Python traria de volta o
    relógio errado. É o mesmo `make_interval`, com o segundo argumento.

    ORÇAMENTO: o carimbo é relativo ao `now()` DESTE update e a condição é
    reavaliada contra um `now()` POSTERIOR. Enquanto o teste roda, portanto, o
    carimbo anda para DENTRO do teto e para FORA do piso — quem escrever caso
    novo aqui olha a tabela do 8c antes de escolher a margem.
    """
    with get_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                """
                update open_finance_connections
                   set created_at = now() - make_interval(mins => %s, secs => %s),
                       reconnected_at = now() - make_interval(mins => %s, secs => %s)
                 where id = %s
                """,
                (minutos, segundos, minutos, segundos, connection_id),
            )
        c.commit()


# ── caso 1: o defeito. Caixa recém-gravada, `health` ausente ─────────────────

def test_caixa_sem_health_manda_autorizar_o_dispositivo_e_nao_reautorizar(user_id):
    db.save_pluggy_open_finance_item(user_id, ITEM_CAIXA_QR)

    ui = _ui_da_tela(user_id, "item-tela-caixa")

    assert ui["state"] == "needs_user_action"
    assert ui["detail"] == DETALHE_DISPOSITIVO, (
        "com `health` NULL a tela media 'Reautorize o banco' — a instrução que "
        "faz PERDER a janela do QR, no minuto em que ela está aberta")

    # A CLASSE, não a instância (§2): "Reautorize o banco" saía por DUAS
    # superfícies, e consertar só a tela deixaria o TOAST do /refresh mandando o
    # oposto. Este é o SEGUNDO select (`get_connections_by_item_id`), e ele
    # também precisa do derivado.
    #
    # Pelo CAMINHO DO TOAST, não pelo `connection_ui_state` chamado direto: a
    # versão anterior desta asserção era `_ui(...)`, que monta o estado à mão a
    # partir de `_linha(...)` — o padrão que o CLAUDE.md §3 nomeia como teste que
    # não passa pelo caminho alterado. Quem monta o toast é `_refresh_items_report`,
    # e é ele que roda aqui: os dicionários vazios são o resultado do PATCH, dos
    # motivos e da espera, que este caso não exercita.
    rel = ps._refresh_items_report(
        user_id, ["item-tela-caixa"], {"item-tela-caixa": "Caixa"}, {}, {}, set())

    assert [r["detail"] for r in rel] == [DETALHE_DISPOSITIVO], (
        "o toast do /refresh lê outro select: sem o derivado lá também, ele "
        f"continuaria mandando reautorizar na janela do QR — {rel}")


# ── caso 2: o outro ramo. Não é controle negativo: é a prova de convergência ──

def test_caixa_com_health_medido_diz_a_MESMA_coisa_que_sem_health(user_id):
    conexao = db.save_pluggy_open_finance_item(user_id, ITEM_CAIXA_QR)
    # O job de saúde observa o MESMO item cru e grava o que o `derive_item_health`
    # produzir — o par de produção, não um `health` montado à mão.
    db.mark_sync_result(conexao["id"], ok=None, status="ERROR", status_reason="",
                        health=derive_item_health(ITEM_CAIXA_QR))

    ui = _ui_da_tela(user_id, "item-tela-caixa")

    assert ui["detail"] == DETALHE_DISPOSITIVO, (
        "os dois ramos do connection_ui_state têm de convergir: era isso que o "
        "defeito quebrava (mesmo estado, instrução oposta)")


# ── caso 3: ponta a ponta pela ROTA — é a PRIMEIRA tela ──────────────────────

def test_rota_do_snapshot_entrega_a_instrucao_de_dispositivo(user_id):
    db.save_pluggy_open_finance_item(user_id, ITEM_CAIXA_QR)
    client = TestClient(dashboard.app)

    resp = client.get(f"/open-finance/{user_id}", headers=_auth(client, user_id))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["connections"][0]["ui"]["detail"] == DETALHE_DISPOSITIVO


def test_POST_pluggy_item_ja_nasce_com_a_instrucao_certa(user_id, monkeypatch):
    # O `POST /pluggy-item` monta o snapshot ANTES de o sync de fundo escrever
    # saúde: é exatamente a janela em que `health` é NULL, e é a PRIMEIRA tela
    # que a pessoa vê depois de fechar o widget.
    remoto = {**ITEM_CAIXA_QR, "id": "item-tela-post", "clientUserId": str(user_id)}
    monkeypatch.setattr(of_routes, "get_pluggy_item",
                        lambda item_id, api_key=None: remoto)
    monkeypatch.setattr(of_routes, "_schedule_pluggy_sync", lambda item_id: None)
    client = TestClient(dashboard.app)

    resp = client.post(f"/open-finance/{user_id}/pluggy-item",
                       json={"item": {"id": "item-tela-post"}},
                       headers=_auth(client, user_id))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["connections"][0]["ui"]["detail"] == DETALHE_DISPOSITIVO


# ── casos 4 e 5: CONTROLES POSITIVOS — "Reautorize o banco" continua vivo ────

def test_login_error_sem_health_continua_mandando_reautorizar(user_id):
    db.save_pluggy_open_finance_item(user_id, {
        "id": "item-tela-login", "status": "LOGIN_ERROR",
        "connector": {"id": 612, "name": "Nubank"}})

    ui = _ui_da_tela(user_id, "item-tela-login")

    assert ui["state"] == "needs_user_action"
    assert ui["detail"] == DETALHE_REAUTORIZA, (
        "CONTROLE POSITIVO: o caso MAJORITÁRIO de `_NEEDS_USER` não pode virar "
        "instrução de dispositivo — senão o conserto recusa tudo e acerta nada")


def test_outdated_SEM_executionStatus_continua_mandando_reautorizar(user_id):
    # `OUTDATED` cai nos DOIS lados: sozinho é reautorização, acompanhado do
    # `USER_AUTHORIZATION_PENDING` é dispositivo. É o par que discrimina.
    db.save_pluggy_open_finance_item(user_id, {
        "id": "item-tela-outdated", "status": "OUTDATED",
        "connector": {"id": 612, "name": "Nubank"}})

    ui = _ui_da_tela(user_id, "item-tela-outdated")

    assert ui["detail"] == DETALHE_REAUTORIZA, (
        "CONTROLE POSITIVO: sem o segundo campo, `OUTDATED` é reautorização")


# ── caso 6: a ORDEM da máquina de estados não mudou ──────────────────────────

def test_updating_com_executionStatus_de_dispositivo_continua_atualizando(user_id):
    # `_UPDATING` é testado ANTES de `_NEEDS_USER` no `connection_ui_state`, e
    # essa ordem foi medida (60 combinações). O derivado não pode furá-la.
    db.save_pluggy_open_finance_item(user_id, {
        "id": "item-tela-updating", "status": "UPDATING",
        "executionStatus": "USER_AUTHORIZATION_PENDING",
        "connector": {"id": 219, "name": "Caixa"}})

    ui = _ui_da_tela(user_id, "item-tela-updating")

    assert ui["state"] == "updating", "a ordem `_UPDATING` antes de `_NEEDS_USER` é a de sempre"
    assert ui["detail"] != DETALHE_DISPOSITIVO


# ── casos 7 e 8: o PRAZO. O `raw` é congelado; a instrução não pode ser ──────

def test_passado_o_prazo_a_tela_e_o_aviso_voltam_a_mandar_reautorizar(user_id):
    # `mark_sync_result` não toca em `raw` e `mark_sync_attempt` empurra
    # `updated_at`/`last_attempt_at` sem que o `raw` mude. Sem prazo, uma linha
    # que nunca mais fosse medida ficaria para sempre mandando ler um QR morto —
    # e o aviso proativo, calado para sempre.
    #
    # IMPORT LOCAL, e NÃO mova para o topo do arquivo: a constante não existe no
    # código ANTIGO, e no topo o `ImportError` derruba a COLETA do arquivo
    # inteiro — a coluna antiga do `scripts/coluna_dupla.py` vira um `<error>`
    # sem nenhuma asserção vista, e o gate rebaixa a prova a FRACA. Aqui dentro,
    # a coluna antiga roda os corpos e vermelha pelo motivo certo. É o mesmo
    # adiamento que a PRODUÇÃO faz (`janela_device_auth_min`,
    # `db/open_finance_state.py`), lá por mão única de pacote, aqui por isto.
    from core.services.pluggy_health import JANELA_DEVICE_AUTH_MIN
    conexao = db.save_pluggy_open_finance_item(user_id, ITEM_CAIXA_QR)
    _envelhece_autorizacao(conexao["id"], JANELA_DEVICE_AUTH_MIN + 5)

    ui = _ui_da_tela(user_id, "item-tela-caixa")

    assert ui["detail"] == DETALHE_REAUTORIZA, (
        "vencido o prazo, a ação certa é reautorizar: errar curto custa uma "
        "instrução conservadora, errar longo manda esperar um QR morto")
    assert "item-tela-caixa" in _avisadas(user_id), (
        "o silêncio do aviso proativo tem de ser LIMITADO — antes do prazo ele "
        "durava até o próximo tique de saúde (default 6 h), ou mais")


def test_dentro_do_prazo_o_aviso_continua_calado(user_id):
    # CONTROLE do prazo pelo outro lado, e preserva o conserto do #166: um prazo
    # curto demais (ou uma âncora errada) reabriria o aviso dentro da janela.
    from core.services.pluggy_health import JANELA_DEVICE_AUTH_MIN  # local: ver caso 7
    conexao = db.save_pluggy_open_finance_item(user_id, ITEM_CAIXA_QR)
    _envelhece_autorizacao(conexao["id"], JANELA_DEVICE_AUTH_MIN - 5)

    assert _ui_da_tela(user_id, "item-tela-caixa")["detail"] == DETALHE_DISPOSITIVO
    assert "item-tela-caixa" not in _avisadas(user_id), (
        "dentro da janela, 'reconecte seu banco' é o que faz PERDER o QR (#166)")


# ── caso 8a: a FRONTEIRA do piso, nos 60 minutos ────────────────────────────
#
# Os casos 7 e 8 escrevem a idade como `JANELA_DEVICE_AUTH_MIN ± 5`: eles são
# DERIVADOS da constante e por isso não prendem o VALOR dela, só a existência do
# piso. Medido: com a constante em 1, 5, 1440 ou 525600 os dois seguem VERDES —
# e nenhum outro teste da árvore usa a constante. É a patologia que o
# `docs/controles_declarados.md` nomeia ("se o caso do teste se escreve em função
# da constante, ele não pode ser o único caso"), e o remédio já estava aplicado
# no TETO (o 8c, em segundos absolutos) e faltava no piso.
#
# A assimetria é o contrário do risco: um piso curto demais mata a instrução
# CERTA com o QR ainda aberto (com a constante em 5, ela morre 5 min depois de
# conectar) — o bug do #166 de volta, sem uma linha vermelha.
#
# Estes dois casos prendem o 60 pelas duas pontas, em minutos ABSOLUTOS (medido:
# com a constante em 1, 5 ou 15 a perna de 55 fica vermelha; em 1440 ou 525600,
# a de 61).
#
# A perna de DENTRO é 55, e não 59, pelo motivo do 8c: ela é a outra metade da
# classe de veredito que anda com o tempo (a tabela está lá). A 59 tinha 60 s de
# orçamento entre o `update` e a leitura; a 55 tem 300 s. O pino perdido é o de
# 56–59 min, onde nenhuma mutação declarada vive.

@pytest.mark.parametrize("minutos_de_idade,detalhe,avisado", [
    (55, DETALHE_DISPOSITIVO, False),
    (61, DETALHE_REAUTORIZA, True),
])
def test_o_piso_do_prazo_vale_60_minutos(
    user_id, minutos_de_idade, detalhe, avisado
):
    conexao = db.save_pluggy_open_finance_item(user_id, ITEM_CAIXA_QR)
    _envelhece_autorizacao(conexao["id"], minutos_de_idade)

    assert _ui_da_tela(user_id, "item-tela-caixa")["detail"] == detalhe, (
        f"carimbo com {minutos_de_idade} min de idade: o piso é de 60 min. Um "
        "piso mais curto tira a instrução certa de quem ainda tem QR aberto; um "
        "mais longo manda esperar um QR morto")
    assert ("item-tela-caixa" in _avisadas(user_id)) is avisado, (
        "o piso vale nas DUAS superfícies: a tela e o aviso proativo leem a "
        "MESMA condição, por dois selects diferentes")


# ── caso 8b: o OUTRO LADO do intervalo — carimbo no FUTURO ───────────────────
#
# O prazo dos casos 7 e 8 tinha teto nenhum, e o grupo era ESTRUTURALMENTE CEGO a
# isso: movendo o `AGORA` do arquivo de 2026 para 2027, NENHUM teste dele virava
# — não havia caso com carimbo no futuro.
#
# Por que o carimbo pode estar no futuro: as duas pontas da comparação vêm de
# RELÓGIOS DIFERENTES. `reconnected_at` é o `datetime.now(_tz())` do PYTHON
# (`save_pluggy_open_finance_item`); o `now()` do `SQL_RAW_AINDA_VALE` é do
# POSTGRES. Sem teto, cada segundo de adiantamento do app estende a janela um
# segundo, e um relógio grosseiramente errado a torna PERMANENTE — medido com
# `now() + 10 days`: `{'state': 'needs_user_action', 'detail': 'Autorize o acesso
# no app do banco'}` na tela e `avisadas -> set()`, os dois PARA SEMPRE, que é
# exatamente a falha que o prazo existe para fechar.
#
# OS DOIS CASOS SÃO UM PAR, e a folga de 5 min do teto é o que os separa:
#   • +1 min (app adiantado, desvio NORMAL entre app e banco) → o conserto
#     legítimo CONTINUA valendo. É o controle POSITIVO do teto: com `<= now()`
#     puro, um app 2 s adiantado matava o conserto na RECONEXÃO recém-gravada
#     (só nela: no primeiro INSERT a âncora é o `created_at`, `default now()` do
#     Postgres, que nunca está no futuro — ver `db/open_finance_state.py`);
#   • +10 dias → fora da folga, o derivado morre e a instrução volta a
#     "Reautorize o banco", com o aviso proativo de volta.

@pytest.mark.parametrize("minutos_no_futuro,detalhe,avisado", [
    (1, DETALHE_DISPOSITIVO, False),
    (10 * 24 * 60, DETALHE_REAUTORIZA, True),
])
def test_carimbo_no_futuro_nao_reabre_o_silencio_permanente(
    user_id, minutos_no_futuro, detalhe, avisado
):
    conexao = db.save_pluggy_open_finance_item(user_id, ITEM_CAIXA_QR)
    # Minuto NEGATIVO em `_envelhece_autorizacao` = `now() - (-n)` = futuro. O
    # recuo/avanço é no BANCO pelo mesmo motivo de sempre: o prazo é avaliado
    # contra o `now()` do Postgres.
    _envelhece_autorizacao(conexao["id"], -minutos_no_futuro)

    assert _ui_da_tela(user_id, "item-tela-caixa")["detail"] == detalhe
    assert ("item-tela-caixa" in _avisadas(user_id)) is avisado, (
        "sem o TETO do intervalo, um carimbo no futuro cala o aviso proativo e "
        "prende a tela na instrução de dispositivo — para sempre")


# ── caso 8c: a FRONTEIRA do teto, nos 5 minutos exatos ──────────────────────
#
# O par do 8b (+1 min / +10 dias) prende só o intervalo ABERTO `[1 min, 10 dias)`:
# medido mutando o literal do teto e rodando o arquivo, `'1 minute'`, `'1 hour'`,
# `'5 hours'` e `'9 days'` passavam TODOS verdes. Um `'5 hours'` — 60× mais frouxo
# — devolveria a supressão permanente para qualquer app com o relógio errado em
# HORAS, e nada ficava vermelho. Fronteira sem teste é a próxima regressão.
#
# Estes três casos prendem o número: 4m59s e 5m00s DENTRO (o `<=` é inclusivo),
# 5m30s FORA. Só um teto na faixa [5m00s, 5m30s) deixa as três verdes — medido,
# `'10 seconds'`, `'1 minute'`, `'1 hour'`, `'5 hours'`, `'9 days'` e `'10 years'`
# ficam TODOS vermelhos.
#
# Em SEGUNDOS pelo `make_interval` do helper: a aritmética de minuto não expressa
# a borda, e fazê-la em Python traria de volta o relógio que o teto existe para
# descartar.
#
# ── A CLASSE do veredito que ANDA COM O TEMPO (Codex #428, P2) ───────────────
#
# `_envelhece_autorizacao` grava o carimbo relativo ao `now()` do UPDATE; o
# predicado é reavaliado contra o `now()` das consultas seguintes. Passado Δ, o
# `now() + 5 min` do teto SOBE e o `now() - 60 min` do piso também: carimbo de
# FUTURO anda para DENTRO, carimbo de PASSADO anda para FORA. Só duas das quatro
# combinações podem virar de veredito, e o ORÇAMENTO é a distância à borda:
#
#   futuro  esperado FORA   → vira DENTRO  → 8c, a perna de +5m30s .... 30 s
#                                          → 8b, a perna de +10 dias .. ~10 dias
#   passado esperado DENTRO → vira FORA    → 8a, a perna de −55 min ... 300 s
#                                          → caso 8 (`JANELA−5`) ...... 300 s
#                                          → todo caso sem envelhecer . ~60 min
#   futuro  esperado DENTRO (+1 min, 4m59s, 5m00s) ............. não vira nunca
#   passado esperado FORA   (−61 min, caso 7 em −65 min) ....... não vira nunca
#
# Só a perna do teto estava em 1 s, e só ela era risco real — as outras entradas
# que andam já tinham orçamento em minutos ou dias. A do piso entrou no conserto
# (59 → 55) por ser o MESMO defeito, não por flakear: 60 s já era folgado.
#
# ponytail: as duas pernas que podiam virar foram ALARGADAS — +5m01s virou
# +5m30s (1 s de orçamento → 30 s) e −59 min virou −55 min (60 s → 300 s). NÃO se
# congelou o `now()` do Postgres: o teste existe para medir a passagem do tempo
# contra o relógio do BANCO, e um relógio fixo trocaria o risco de flake por um
# caso que deixa de exercer o mecanismo (CLAUDE.md §3).
#
# Δ MEDIDO em 2026-09-16, 3 rodadas, envolvendo `_envelhece_autorizacao` num
# espião de `time.monotonic()` e comparando com o fim do caso (limite SUPERIOR do
# Δ que conta, já que a última consulta vem antes): máximo de 3,0 ms em qualquer
# caso do arquivo, 1,9 ms na perna de +5m30s e 2,8 ms na de −55 min. Folga real da
# perna mais apertada: 30 s − 1,9 ms, ~15.000× o Δ observado (antes, ~500×).
# NÚMERO DATADO (CLAUDE.md §2): remeça o espião antes de reusar — máquina mais
# lenta, Postgres remoto ou runner carregado mudam a conta, não a conclusão.
#
# O preço é o pino: um teto entre 5m00s e 5m29s passa verde (antes, entre 5m00s e
# 5m00s). Se um dia um teto nessa faixa importar, o conserto é um caso a MAIS em
# 5m01s tolerante a flake, não estreitar esta perna de volta.

@pytest.mark.parametrize("segundos_no_futuro,detalhe,avisado", [
    (4 * 60 + 59, DETALHE_DISPOSITIVO, False),
    (5 * 60, DETALHE_DISPOSITIVO, False),
    (5 * 60 + 30, DETALHE_REAUTORIZA, True),
])
def test_a_folga_do_teto_vale_ate_5_minutos_exatos(
    user_id, segundos_no_futuro, detalhe, avisado
):
    conexao = db.save_pluggy_open_finance_item(user_id, ITEM_CAIXA_QR)
    _envelhece_autorizacao(conexao["id"], segundos=-segundos_no_futuro)

    assert _ui_da_tela(user_id, "item-tela-caixa")["detail"] == detalhe, (
        f"carimbo em `now() + {segundos_no_futuro} s`: a folga do teto é de 5 min "
        "EXATOS, inclusiva — um teto mais frouxo devolve a supressão permanente "
        "para relógio errado em horas")
    assert ("item-tela-caixa" in _avisadas(user_id)) is avisado, (
        "a fronteira tem de valer nas DUAS superfícies: a tela e o aviso "
        "proativo leem a MESMA condição, por dois selects diferentes")


# ── caso 9: isolamento por user_id (CLAUDE.md §0, regra dura) ────────────────

def test_o_derivado_nao_vaza_a_conexao_de_um_usuario_para_outro(user_id):
    outro = user_id + 1
    db.ensure_user(outro)
    try:
        db.save_pluggy_open_finance_item(user_id, ITEM_CAIXA_QR)
        db.save_pluggy_open_finance_item(outro, {
            "id": "item-tela-do-outro", "status": "LOGIN_ERROR",
            "connector": {"id": 612, "name": "Nubank"}})

        # 1) o snapshot de B não enxerga a Caixa de A
        itens_de_b = {c["provider_item_id"]
                      for c in db.get_open_finance_snapshot(outro)["connections"]}
        assert itens_de_b == {"item-tela-do-outro"}, itens_de_b

        # 2) e a ROTA de A, autenticada como B, nem responde
        client = TestClient(dashboard.app)
        resp = client.get(f"/open-finance/{user_id}",
                          headers=_auth(client, outro, "outro@t.com"))
        assert resp.status_code == 403, resp.text
    finally:
        db.disconnect_open_finance_connection(outro)
        with get_conn() as c:
            c.execute("delete from users where id=%s", (outro,))
            c.commit()


# ── caso 10: `raw` trocado pelo ENVELOPE do webhook ──────────────────────────

def test_raw_do_webhook_nao_vira_instrucao_de_dispositivo(user_id):
    # `update_pluggy_open_finance_item_status` grava em `raw` o ENVELOPE do
    # evento, não o item: ele não carrega `executionStatus`. O derivado tem de
    # virar NULL — nunca uma instrução de dispositivo tirada de um envelope.
    db.save_pluggy_open_finance_item(user_id, ITEM_CAIXA_QR)
    db.update_pluggy_open_finance_item_status(
        "item-tela-caixa", "ERROR",
        {"event": "item/error", "itemId": "item-tela-caixa",
         "id": "evt-1", "triggeredBy": "SYNC"})

    linha = _linha("item-tela-caixa")
    assert linha["execution_status"] is None, (
        "envelope de webhook não é item: `raw->>'executionStatus'` não existe ali")

    ui = _ui_da_tela(user_id, "item-tela-caixa")
    assert ui["detail"] != DETALHE_DISPOSITIVO
    assert ui["state"] in ("error_recoverable", "no_accounts"), ui


# ── O VAZAMENTO: o derivado não pode virar contrato público ──────────────────
#
# Guarda ESTRUTURAL, sobre a RESPOSTA HTTP e não sobre o dict Python: o derivado
# nasce do `raw`, e o `raw` da Pluggy carrega `clientUserId` e `statusDetail`. A
# varredura do corpo serializado INTEIRO pega o vazamento por qualquer caminho —
# não só pela chave que alguém lembrou de proibir.
#
# CONTROLE NEGATIVO (medido, ver o relato): acrescentar `raw` ao select de
# `get_open_finance_snapshot` sem o `pop` deixa as DUAS asserções vermelhas.

@pytest.mark.parametrize("via", ["get", "post"])
def test_a_resposta_HTTP_nao_ganhou_chave_nova_nem_vazou_o_raw(user_id, monkeypatch, via):
    remoto = {**ITEM_CAIXA_QR, "id": "item-vaza", "clientUserId": str(user_id)}
    client = TestClient(dashboard.app)
    headers = _auth(client, user_id)

    if via == "get":
        db.save_pluggy_open_finance_item(user_id, remoto)
        resp = client.get(f"/open-finance/{user_id}", headers=headers)
    else:
        monkeypatch.setattr(of_routes, "get_pluggy_item",
                            lambda item_id, api_key=None: remoto)
        monkeypatch.setattr(of_routes, "_schedule_pluggy_sync", lambda item_id: None)
        resp = client.post(f"/open-finance/{user_id}/pluggy-item",
                           json={"item": {"id": "item-vaza"}}, headers=headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    conexao = next(c for c in body["connections"] if c["provider_item_id"] == "item-vaza")

    assert set(conexao.keys()) == CHAVES_DA_CONEXAO, (
        "o corpo HTTP tem de ficar IDÊNTICO em chaves ao de antes do derivado: "
        f"sobrou {set(conexao.keys()) - CHAVES_DA_CONEXAO}, "
        f"faltou {CHAVES_DA_CONEXAO - set(conexao.keys())}")

    corpo = json.dumps(body)
    assert "clientUserId" not in corpo, "o `raw` carrega o id do cliente na Pluggy"
    assert "statusDetail" not in corpo, "e o detalhe por produto, que a tela não usa"


# ── AS CÉLULAS QUE MUDARAM, medidas em DUAS COLUNAS ─────────────────────────
#
# Varredura combinatória pelo caminho de produção, base × branch: 12 `status` × 7
# `executionStatus` × {com health, sem health} = 168 células. 160 IDÊNTICAS — a
# ordem da máquina de estados não mudou, agora MEDIDO e não lido. As 8 que
# mudaram são as de baixo, todas com `health` NULL, todas de "Reautorize o banco"
# para "Autorize o acesso no app do banco".
#
# Foram 6 na primeira varredura: a lista de `status` dela não trazia
# `INVALID_CREDENTIALS`, que está em `_NEEDS_USER` e é LOAD-BEARING no módulo. Ele
# entrou depois e trouxe as DUAS últimas células. Estão aqui pelo mesmo argumento
# que as de caixa minúscula — inalcançáveis em produção, presas assim mesmo —, e
# deixar de fora justo essas duas seria cobertura decidida pelo acaso de qual
# `status` entrou na varredura de quem.
#
# Este teste prende as 7 que NENHUM outro caso deste grupo segura (a 8ª, OUTDATED
# + `USER_AUTHORIZATION_PENDING` em maiúscula, é o caso 1). Elas não são efeito
# colateral: as DIAGONAIS (`LOGIN_ERROR`, `WAITING_USER_INPUT`,
# `INVALID_CREDENTIALS` ao lado do `executionStatus` de dispositivo) são as que a
# docstring de `_detalhe_de_acao` enumera como benignas — "`_NEEDS_USER` +
# `execution_status` de device/QR" —, e passar a mostrar a instrução de
# dispositivo nelas é CONVERGIR com o ramo que tem `health`, que já fazia isso. O
# que faltava era teste: mudança medida e não presa é mudança que volta sozinha.
#
# ALCANCE das duas de `INVALID_CREDENTIALS`: nenhum, pelo mesmo mecanismo das de
# caixa minúscula. `INVALID_CREDENTIALS` é `executionStatus`, nunca `status` de
# item, e o upsert grava `status = item['status'] or item['executionStatus']` —
# então um `status` local `INVALID_CREDENTIALS` implica `raw->>'executionStatus'`
# IGUAL a `INVALID_CREDENTIALS`, nunca o de dispositivo. O par das duas colunas
# não sai do caminho de produção.
#
# A CAIXA (minúscula) é a outra metade. O derivado passa por `upper()`, então um
# `executionStatus` em minúscula no `raw` agora casa onde antes não casava — hoje
# INALCANÇÁVEL pelo caminho de produção (a Pluggy manda em maiúscula), e a razão
# de ser assim está no comentário do `SQL_EXECUTION_STATUS`
# (`db/open_finance_state.py`). Presa aqui porque foi a varredura que a achou.
#
# CONTROLE: as 160 células inalteradas não cabem num teste, mas os casos 4, 5 e 6
# deste grupo são três delas (LOGIN_ERROR e OUTDATED sem `executionStatus`,
# UPDATING com ele) e continuam exigindo o comportamento de antes.

@pytest.mark.parametrize("status,execution_status", [
    ("LOGIN_ERROR", "USER_AUTHORIZATION_PENDING"),
    ("WAITING_USER_INPUT", "USER_AUTHORIZATION_PENDING"),
    ("INVALID_CREDENTIALS", "USER_AUTHORIZATION_PENDING"),
    ("OUTDATED", "user_authorization_pending"),
    ("LOGIN_ERROR", "user_authorization_pending"),
    ("WAITING_USER_INPUT", "user_authorization_pending"),
    ("INVALID_CREDENTIALS", "user_authorization_pending"),
])
def test_as_celulas_que_mudaram_na_varredura_ficam_na_instrucao_de_dispositivo(
    user_id, status, execution_status
):
    db.save_pluggy_open_finance_item(user_id, {
        "id": "item-celula", "status": status,
        "executionStatus": execution_status,
        "connector": {"id": 219, "name": "Caixa"}})

    ui = _ui_da_tela(user_id, "item-celula")

    assert ui["state"] == "needs_user_action", ui
    assert ui["detail"] == DETALHE_DISPOSITIVO, (
        f"célula ({status}, {execution_status}) com `health` NULL: o ramo com "
        "health já mandava autorizar o dispositivo, e é com ele que este aqui "
        f"converge — veio {ui['detail']!r}")


# ── mesclar_health_em_coleta via mark_sync_result — banco real (issue #444) ──
# `mark_sync_result` (`db/open_finance_state.py`) chama `mesclar_health_em_coleta`
# antes de gravar, quando a foto NOVA está em coleta. Diferente do teste da
# função pura (`tests/test_of_health.py`), este passa pela ESCRITA de verdade,
# DUAS vezes seguidas — é o caminho real (sync completo, depois job de saúde
# no meio de uma coleta nova).
#
# CONTROLE NEGATIVO do grupo: tirar a chamada a `mesclar_health_em_coleta` de
# `mark_sync_result` deixa os dois primeiros vermelhos (o `health = coalesce`
# do UPDATE sobrescreve `health` com a foto pobre inteira). Medido à mão antes
# deste commit.

_CREDIT_ATRASADO_12_08 = {
    "isUpdated": False, "lastUpdatedAt": "2026-08-12T03:10:00.000Z", "warnings": [],
}


def test_health_parcial_sobrevive_a_uma_2a_foto_updating(user_id):
    """(1) sync completo com CREDIT atrasado desde 12/08. (2) job de saúde no
    meio de uma coleta nova, que só mede `accounts`. O cartão continua
    aparecendo — a mescla preserva o que a 2ª foto não mediu."""
    conexao = _conexao(user_id, "item-444-integra-1")
    health1 = derive_item_health({
        "status": "UPDATED",
        "statusDetail": {"accounts": {"isUpdated": True, "lastUpdatedAt": "2026-08-20T11:00:00Z",
                                      "warnings": []},
                         "creditCards": _CREDIT_ATRASADO_12_08}})
    db.mark_sync_result(conexao["id"], ok=True, status="ACTIVE", status_reason="", health=health1)

    health2 = derive_item_health({"status": "UPDATING",
                                  "statusDetail": {"accounts": {"isUpdated": True}}})
    db.mark_sync_result(conexao["id"], ok=None, status="ACTIVE", status_reason="", health=health2)

    ui = _ui("item-444-integra-1")
    assert ui["state"] == "partial", ui
    assert "12/08" in ui["detail"], ui["detail"]


def test_health_parcial_sobrevive_a_uma_2a_foto_updating_sem_statusdetail(user_id):
    """Mesmo caso, mas a 2ª foto não traz `statusDetail` NENHUM (item entrou em
    UPDATING sem a Pluggy ter devolvido nada ainda) — mescla do mesmo jeito."""
    conexao = _conexao(user_id, "item-444-integra-2")
    health1 = derive_item_health({
        "status": "UPDATED",
        "statusDetail": {"accounts": {"isUpdated": True, "lastUpdatedAt": "2026-08-20T11:00:00Z",
                                      "warnings": []},
                         "creditCards": _CREDIT_ATRASADO_12_08}})
    db.mark_sync_result(conexao["id"], ok=True, status="ACTIVE", status_reason="", health=health1)

    health2 = derive_item_health({"status": "UPDATING"})
    db.mark_sync_result(conexao["id"], ok=None, status="ACTIVE", status_reason="", health=health2)

    ui = _ui("item-444-integra-2")
    assert ui["state"] == "partial", ui
    assert "12/08" in ui["detail"], ui["detail"]


def test_2a_foto_updated_sem_credit_vira_updated_CONTROLE_POSITIVO(user_id):
    """POSITIVO: a 2ª foto é FINAL (UPDATED), não UPDATING — a mescla não se
    aplica a foto final, e um `statusDetail` só com `accounts` em dia é o que
    manda: "updated". Isto prova que o conserto só age em coleta, não trava a
    tela em "partial" para sempre."""
    conexao = _conexao(user_id, "item-444-integra-3")
    health1 = derive_item_health({
        "status": "UPDATED",
        "statusDetail": {"accounts": {"isUpdated": True, "lastUpdatedAt": "2026-08-20T11:00:00Z",
                                      "warnings": []},
                         "creditCards": _CREDIT_ATRASADO_12_08}})
    db.mark_sync_result(conexao["id"], ok=True, status="ACTIVE", status_reason="", health=health1)

    health2 = derive_item_health({"status": "UPDATED",
                                  "statusDetail": {"accounts": {"isUpdated": True}}})
    db.mark_sync_result(conexao["id"], ok=True, status="ACTIVE", status_reason="", health=health2)

    ui = _ui("item-444-integra-3")
    assert ui["state"] == "updated", ui


def test_updating_seguido_de_foto_final_limpa_o_cartao_CONTROLE_POSITIVO(user_id):
    """POSITIVO: UPDATING intermediário preserva o cartão atrasado (mescla), mas
    a foto FINAL que resolve o cartão (CREDIT atualizado) some com o "Parcial" —
    a mescla não gruda o motivo velho para sempre."""
    conexao = _conexao(user_id, "item-444-integra-4")
    health1 = derive_item_health({
        "status": "UPDATED",
        "statusDetail": {"accounts": {"isUpdated": True, "lastUpdatedAt": "2026-08-20T11:00:00Z",
                                      "warnings": []},
                         "creditCards": _CREDIT_ATRASADO_12_08}})
    db.mark_sync_result(conexao["id"], ok=True, status="ACTIVE", status_reason="", health=health1)

    health2 = derive_item_health({"status": "UPDATING",
                                  "statusDetail": {"accounts": {"isUpdated": True}}})
    db.mark_sync_result(conexao["id"], ok=None, status="ACTIVE", status_reason="", health=health2)
    assert _ui("item-444-integra-4")["state"] == "partial", "checagem intermediária"

    health3 = derive_item_health({
        "status": "UPDATED",
        "statusDetail": {"accounts": {"isUpdated": True, "lastUpdatedAt": "2026-08-20T15:00:00Z",
                                      "warnings": []},
                         "creditCards": {"isUpdated": True, "lastUpdatedAt": "2026-08-20T15:00:00Z",
                                        "warnings": []}}})
    db.mark_sync_result(conexao["id"], ok=True, status="ACTIVE", status_reason="", health=health3)

    ui = _ui("item-444-integra-4")
    assert ui["state"] == "updated", ui
