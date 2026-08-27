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


def test_sync_depois_da_reconexao_devolve_o_verde(user_id, monkeypatch, relogio_fixo):
    """CONTROLE POSITIVO: sem ele a guarda podia recusar para sempre depois de
    qualquer reconexão — que é pior que o bug."""
    _conexao(user_id, "item-religa-ok")
    db.save_pluggy_open_finance_item(
        user_id, {"id": "item-religa-ok", "status": "UPDATED",
                  "connector": {"id": 612, "name": "Nubank"}})

    _mock_pluggy(monkeypatch, item={**ITEM_SAUDAVEL, "id": "item-religa-ok"},
                 contas=[_conta_pluggy("acc-religa")], txs=[_tx_pluggy("tx-religa")])
    assert ps.sync_pluggy_item("item-religa-ok")["ok"] is True

    linha = _linha("item-religa-ok")
    assert linha["last_sync_at"] == AGORA
    assert linha["last_sync_at"] >= linha["reconnected_at"], "o sync é POSTERIOR à reconexão"
    ui = db.get_open_finance_snapshot(user_id)["connections"][0]["ui"]
    assert ui["state"] == "updated", "sincronizou depois de reconectar: verde de novo"


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
