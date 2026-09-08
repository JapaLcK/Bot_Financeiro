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

from datetime import datetime, timedelta

import pytest

import db
import core.services.pluggy_sync as ps
from core.services.pluggy import PluggyApiError
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
# ALCANCE, que é onde este conserto PARA: ele cobre o AVISO PROATIVO e só. A TELA
# na mesma janela continua errada, porque `get_open_finance_snapshot` não
# seleciona `raw` — o `connection_ui_state` recebe a linha pronta e não tem como
# ver o `executionStatus`. Metade PENDENTE, de PR próprio; o motivo está no
# comentário do snapshot (`db/open_finance.py`). Se alguém escrever um teste de
# tela para esta janela esperando verde, é por não ter lido isto.
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

def test_reconexao_pelo_ramo_do_CONFLITO_cala_o_aviso_so_ate_o_health_voltar(
    user_id, relogio_fixo
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
