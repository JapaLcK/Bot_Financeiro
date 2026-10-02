"""Onda 5, PR-B1: "Atualizando…" termina (R1, R1b e o processo que reinicia).

Contrato e tabela de estados × eventos: `docs/open_finance_estados.md`.

Pelo caminho real: webhook pela rota (`item/created`), o sync de fundo
(`_run_pluggy_sync_bg`) com a Pluggy mockada nas funções HTTP, o job de saúde,
e a tela lida pela rota `GET /open-finance/{uid}`. O prazo (D1) é medido contra
o `now()` do Postgres, como o prazo do dispositivo (`_envelhece_autorizacao`).

CONTROLES (medidos em 2026-09-30; remeça se mexer no código):
  (a)/(b) negativo: tirar o `marcar_leitura_falhou` do `_run_pluggy_sync_bg`
      deixa os casos vermelhos (a tela volta a "Atualizando…"); tirar o
      `not pendente` dos dois early-returns do ramo sem `health` também.
  (a2) a marca de falha (quem ela troca e contra qual linha):
      `tests/test_of_marca_de_falha.py`, com os controles dela.
  (b2) negativo: o `_UPDATING and sem_sync` do ramo com `health` devolver
      "updating" direto deixa o irmão vermelho; alargar para `_REASONS_OK`
      deixa o positivo (`no_accounts` em coleta) vermelho.
  (c) negativo: tirar o `coleta_vencida` do `out()` (ou do select) deixa os
      casos "fora" vermelhos. Os casos "dentro" são o positivo do prazo. A
      cláusula "sem sync" trocada por `last_sync_at is null` deixa a reconexão
      vermelha; por `coalesce(reconnected_at, created_at)`, o relógio atrasado.
  (d) positivo: sync bom depois da falha, e depois do prazo, dá "Atualizado".
  (e) a instrução de dispositivo vence o prazo e o motivo.
"""

from __future__ import annotations

import asyncio

import pytest

import core.services.pluggy_sync as ps
import db
import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.open_finance as of_routes
from conftest import promote_to_pro
from core.services.pluggy import PluggyApiError
from core.services.pluggy_health import connection_ui_state
from db.connection import get_conn
from db.open_finance_state import PRAZO_COLETA_MIN
from fastapi.testclient import TestClient
from test_of_connection_state import (
    ITEM_CAIXA_QR, ITEM_SAUDAVEL, _conta_pluggy, _envelhece_autorizacao, _linha,
    _mock_pluggy, _tx_pluggy)
from test_of_item_ownership import SEGREDO, _webhook, eventos  # noqa: F401
from test_of_leitura_incompleta import _ui_pela_rota

ITEM = "item-g1"
UPDATING_REMOTO = {**ITEM_SAUDAVEL, "status": "UPDATING", "executionStatus": "ACCOUNTS_IN_PROGRESS"}
VENCIDA = "Está demorando mais que o normal — atualize de novo"
ERRO = ("error_recoverable", "Erro temporário", "Tentaremos de novo automaticamente")


def _500(*_a, **_kw):
    raise PluggyApiError("boom", status_code=500)


def _conecta(uid: int, status: str = "UPDATING", item: dict | None = None) -> dict:
    """O upsert do `POST /pluggy-item` (a 1ª conexão: `last_sync_at` NULL)."""
    promote_to_pro(uid)
    return db.save_pluggy_open_finance_item(
        uid, item or {"id": ITEM, "status": status, "connector": {"id": 612, "name": "Nubank"}})


def _sync_de_fundo(monkeypatch, item: str = ITEM) -> None:
    monkeypatch.setattr(of_routes, "_backoff_sec", lambda _t: 0)
    asyncio.run(of_routes._run_pluggy_sync_bg(item))


def _ui_das_duas(uid: int, item: str = ITEM) -> dict:
    """A tela (snapshot) e o toast (`get_connections_by_item_id`, que o
    `_refresh_items_report` lê) são dois selects: os dois têm de concordar."""
    tela = _ui_pela_rota(uid, item)
    assert connection_ui_state(_linha(item)) == tela, "tela e toast divergiram"
    return tela


def _sql(query: str, *args) -> None:
    with get_conn() as c:
        c.execute(query, args)
        c.commit()


def _tique_de_saude(monkeypatch, item_remoto: dict) -> None:
    """O job de saúde mede o item (grava `health`) sem sincronizar nada. Envelhece
    o `health` antes, para a linha ser elegível mesmo já tendo sido medida."""
    _sql("update open_finance_connections set health = jsonb_set(health, '{observed_at}', "
         "'\"2000-01-01T00:00:00+00:00\"') where provider_item_id=%s and health is not null", ITEM)
    monkeypatch.setattr(ps, "create_pluggy_api_key", lambda: "k")
    def _get_item(_i, _k=None):
        if isinstance(item_remoto, Exception):
            raise item_remoto
        return item_remoto

    monkeypatch.setattr(ps, "get_pluggy_item", _get_item)
    ps.run_of_health_check()
    assert _linha()["health"], "o job não mediu a linha"


# ── (a) R1 e (b) R1b: o sync de fundo que falha até o fim ────────────────────
# `webhook`: `item/created` grava `UPDATING` (o caso do plano).
# `upsert_updated`: o `POST /pluggy-item` com o item já `UPDATED` na Pluggy, que
# cai no `ultimo is None` do ramo sem `health` — a mesma classe, outro return.
# `accounts` (R1): `GET /items` ok, 500 em `/accounts`; a foto do próprio run é
# gravada (`ACTIVE` + `health`, a O de `tests/test_of_marca_de_falha.py`).
# `items` (R1b): 500 no `GET /items` do próprio sync, sem foto: o `health` fica
# NULL e o `status` local é o do webhook ou do upsert.

@pytest.mark.parametrize("entrada", ["webhook", "upsert_updated"])
@pytest.mark.parametrize("falha", ["accounts", "items"])
def test_sync_de_fundo_que_falha_nao_deixa_atualizando_sem_fim(
        user_id, monkeypatch, eventos, entrada, falha):
    _conecta(user_id, "UPDATING" if entrada == "webhook" else "UPDATED")
    if entrada == "webhook":
        agendados: list[str] = []
        monkeypatch.setenv("PLUGGY_WEBHOOK_SECRET", SEGREDO)
        monkeypatch.setattr(of_routes, "_schedule_pluggy_sync", agendados.append)
        assert _webhook(TestClient(dashboard.app), "item/created", ITEM).status_code == 200
        assert agendados == [ITEM], "o webhook tinha de agendar o sync de fundo"
        assert _linha()["status"] == "UPDATING"
    assert _ui_das_duas(user_id)["state"] == "updating", "recém-conectada: coleta legítima"

    _mock_pluggy(monkeypatch, item=ITEM_SAUDAVEL)
    monkeypatch.setattr(ps, "list_pluggy_accounts" if falha == "accounts" else "get_pluggy_item",
                        _500)
    _sync_de_fundo(monkeypatch)

    ui = _ui_das_duas(user_id)
    assert (ui["state"], ui["label"], ui["detail"]) == ERRO, (
        "a falha registrada fala antes do 'Atualizando…'")
    linha = _linha()
    assert (linha["last_sync_at"], linha["status_reason"]) == (None, "read_failed")
    if falha == "accounts":
        assert (linha["status"], linha["health"]["item_status"]) == ("ACTIVE", "UPDATED")
    else:
        assert linha["health"] is None
    assert any(e["event"] == "pluggy_sync_failed" for e in eventos), eventos


def test_R1_com_o_job_de_saude_medindo_depois_continua_sem_girar(user_id, monkeypatch, eventos):
    """O cenário inteiro do R1: a falha de fundo e depois os tiques de saúde, que
    gravam `health` sem sincronizar. O ramo com `health` também deixa o motivo
    falar (o default seguro do `out()`)."""
    _conecta(user_id)
    _mock_pluggy(monkeypatch, item=ITEM_SAUDAVEL)
    monkeypatch.setattr(ps, "list_pluggy_accounts", _500)
    _sync_de_fundo(monkeypatch)

    _tique_de_saude(monkeypatch, ITEM_SAUDAVEL)

    assert _linha()["status_reason"] == "read_failed", "o job não leu nada: não limpa"
    assert _ui_das_duas(user_id)["state"] == "error_recoverable"


# ── (b2) o irmão no ramo com `health`: item ainda coletando na Pluggy ─────────

def test_irmao_com_health_updating_falha_registrada_fala(user_id, monkeypatch, eventos):
    """Achado do Tester: 1ª conexão, o sync de fundo falha, e o job de saúde mede
    o item AINDA em `UPDATING` na Pluggy. Contrato item 8: a falha fala."""
    _conecta(user_id)
    _mock_pluggy(monkeypatch, item=ITEM_SAUDAVEL)
    monkeypatch.setattr(ps, "list_pluggy_accounts", _500)
    _sync_de_fundo(monkeypatch)
    _tique_de_saude(monkeypatch, UPDATING_REMOTO)

    linha = _linha()
    assert (linha["status_reason"], linha["health"]["item_status"]) == ("read_failed", "UPDATING")
    ui = _ui_das_duas(user_id)
    assert (ui["state"], ui["label"], ui["detail"]) == ERRO


def test_com_health_updating_no_accounts_e_parcial_sem_sync_seguem_atualizando(user_id, monkeypatch):
    """Positivo do irmão: só falha de LEITURA fala. `no_accounts` numa 1ª coleta
    em curso (a Pluggy ainda sem contas) e `investments_read_failed` sem sync
    (regra do PR-A) continuam "Atualizando…"."""
    _conecta(user_id)
    _mock_pluggy(monkeypatch, item=UPDATING_REMOTO)
    assert ps.sync_pluggy_item(ITEM)["reason"] == "no_accounts"
    assert (_linha()["last_sync_at"], _linha()["status_reason"]) == (None, "no_accounts")
    assert _ui_das_duas(user_id)["state"] == "updating"

    _sql("update open_finance_connections set status_reason='investments_read_failed' "
         "where provider_item_id=%s", ITEM)
    assert _ui_das_duas(user_id)["state"] == "updating"


# ── (c) o prazo da coleta (D1): sem sync desde a autorização atual ───────────
# Quatro jeitos de estar "Atualizando…" sem sync, todos sem marca de falha (o
# processo que reiniciou no meio do sync não deixa marca nenhuma):
#   sem health, `status` local UPDATING (webhook) ou UPDATED (upsert);
#   com health do job de saúde, item UPDATING (coleta longa) ou UPDATED.
# Bordas: 29 min dentro; 30 min fora (o `<=` do SQL; o relógio só anda para
# fora durante o teste); 1 min no futuro dentro (desvio normal de relógio);
# 10 dias no futuro fora (o teto de +5 min, como no prazo do dispositivo).

_ESTADOS = {
    "sem_health_updating": ("UPDATING", None),
    "sem_health_updated": ("UPDATED", None),
    "health_updating": ("UPDATING", {**ITEM_SAUDAVEL, "status": "UPDATING"}),
    "health_updated": ("UPDATING", ITEM_SAUDAVEL),
}
_BORDAS = [
    (PRAZO_COLETA_MIN - 1, False),
    (PRAZO_COLETA_MIN, True),
    (-1, False),
    (-10 * 24 * 60, True),
]


@pytest.mark.parametrize("estado", list(_ESTADOS))
@pytest.mark.parametrize("minutos, vencida", _BORDAS,
                         ids=["dentro_29min", "fora_30min", "futuro_1min", "futuro_10dias"])
def test_prazo_da_coleta_troca_o_detalhe_e_mantem_a_pilula(
        user_id, monkeypatch, estado, minutos, vencida):
    status, item_remoto = _ESTADOS[estado]
    conexao = _conecta(user_id, status)
    if item_remoto:
        _tique_de_saude(monkeypatch, item_remoto)
    _envelhece_autorizacao(conexao["id"], minutos)

    ui = _ui_das_duas(user_id)

    assert (ui["state"], ui["label"]) == ("updating", "Atualizando…"), "mesma pílula"
    if vencida:
        assert ui["detail"] == VENCIDA
    else:
        assert ui["detail"] != VENCIDA, "coleta legítima dentro do prazo"


def test_o_prazo_e_o_da_decisao_do_dono():
    """D1 (2026-09-27): 30 min desde a autorização atual. Mudar o número é
    decisão de produto, não refactor (as bordas acima o leem da constante)."""
    assert PRAZO_COLETA_MIN == 30


@pytest.mark.parametrize("remoto", [None, UPDATING_REMOTO], ids=["sem_health", "health_updating"])
def test_reconexao_sem_sync_depois_vence_o_prazo(user_id, monkeypatch, remoto):
    """Achado do Tester: sincronizada ANTES da reconexão, reconectada há 31 min e
    sem sync desde. `last_sync_at` não nulo, mas anterior à âncora."""
    conexao = _conecta(user_id, "UPDATED")
    if remoto:
        _tique_de_saude(monkeypatch, remoto)
    _sql("update open_finance_connections set created_at = now() - interval '3 days', "
         "last_sync_at = now() - interval '2 days', reconnected_at = now() - interval '31 minutes' "
         "where id=%s", conexao["id"])

    ui = _ui_das_duas(user_id)
    assert (ui["state"], ui["detail"]) == ("updating", VENCIDA)


def test_relogio_do_app_atrasado_nao_vence_conexao_sincronizada(user_id, monkeypatch):
    """Achado do Tester, invertido: `last_sync_at` é relógio do Python e
    `created_at` do Postgres. App 1 s atrasado no 1º sync: o "sem sync" do SQL
    tem de ser o mesmo do `sem_sync` do Python (só `reconnected_at`)."""
    conexao = _conecta(user_id, "UPDATED")
    _tique_de_saude(monkeypatch, {"id": ITEM, "status": "UPDATING",
                                  "executionStatus": "ACCOUNTS_IN_PROGRESS"})
    _sql("update open_finance_connections set created_at = now() - interval '2 days', "
         "last_sync_at = now() - interval '2 days' - interval '1 second' where id=%s",
         conexao["id"])

    ui = _ui_das_duas(user_id)
    assert ui["state"] == "updating" and ui["detail"] != VENCIDA, ui


def test_reconexao_recente_reabre_o_prazo_mesmo_com_conexao_antiga(user_id, monkeypatch):
    """A âncora é a autorização ATUAL: conexão criada há dias e reconectada agora
    está dentro do prazo; o `last_sync_at` de antes da reconexão não conta."""
    conexao = _conecta(user_id, "UPDATED")
    _envelhece_autorizacao(conexao["id"], 3 * 24 * 60)
    with get_conn() as c:
        c.execute("update open_finance_connections set last_sync_at = now() - interval '3 days', "
                  "reconnected_at = now() where id=%s", (conexao["id"],))
        c.commit()

    assert _ui_das_duas(user_id)["detail"] == "Ainda não sincronizou"


# ── (d) positivos: o sync bom encerra os dois ────────────────────────────────

@pytest.mark.parametrize("vencida", [False, True], ids=["dentro_do_prazo", "fora_do_prazo"])
def test_sync_bom_depois_da_falha_da_atualizado(user_id, monkeypatch, eventos, vencida):
    conexao = _conecta(user_id)
    _mock_pluggy(monkeypatch, item=ITEM_SAUDAVEL)
    monkeypatch.setattr(ps, "list_pluggy_accounts", _500)
    _sync_de_fundo(monkeypatch)
    if vencida:
        _envelhece_autorizacao(conexao["id"], PRAZO_COLETA_MIN + 5)

    _mock_pluggy(monkeypatch, item=ITEM_SAUDAVEL, contas=[_conta_pluggy()], txs=[_tx_pluggy()])
    _sync_de_fundo(monkeypatch)

    assert _linha()["status_reason"] is None
    ui = _ui_das_duas(user_id)
    assert (ui["state"], ui["label"]) == ("updated", "Atualizado")


# ── (e) a instrução de dispositivo vence o prazo e o motivo ──────────────────
# Caixa: `status` OUTDATED + `executionStatus` USER_AUTHORIZATION_PENDING, sem
# `health`. 45 min: fora do prazo da coleta (30) e dentro da janela do
# dispositivo (60). O sync de fundo falha (500 no `GET /items`) e marca o motivo.

def test_instrucao_de_dispositivo_vence_prazo_e_motivo(user_id, monkeypatch, eventos):
    conexao = _conecta(user_id, item=ITEM_CAIXA_QR)
    _mock_pluggy(monkeypatch, item=PluggyApiError("boom", status_code=500))
    _sync_de_fundo(monkeypatch, ITEM_CAIXA_QR["id"])
    assert _linha(ITEM_CAIXA_QR["id"])["status_reason"] == "read_failed"
    _envelhece_autorizacao(conexao["id"], 45)

    ui = _ui_das_duas(user_id, ITEM_CAIXA_QR["id"])

    assert (ui["state"], ui["detail"]) == ("needs_user_action",
                                           "Autorize o acesso no app do banco")
