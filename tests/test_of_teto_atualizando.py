"""Fase 4 do app, PR 2: teto do "Atualizando…".

Passado `TETO_ATUALIZANDO_MIN`, o `updating` de `connection_ui_state` sai como
`error_recoverable` com `_DETALHE_COLETA_ESTOURADA` (decisão do dono: sem estado
novo). Quem decide é o derivado `coleta_estourada` (`db/open_finance_state.py`):
sem sync, no `now()` do Postgres; com sync, `aplica_teto_por_health` lê a âncora
`health.coletando_desde`, herdada entre fotos por `mesclar_health_em_coleta`.
Dado ruim nessa âncora: `tests/test_of_teto_dado_ruim.py`. Tabela:
`docs/open_finance_estados.md`.

CONTROLES (mutação numa cópia, restaurada e conferida com `cmp`; medidos em
2026-10-01, remeça se mexer no código):
  (m1) tirar o `if coleta_estourada` do `out()`         → A, C, D, E, I e o isolamento
  (m2) tirar a herança do `coletando_desde`             → B (as 3 de herdar) e D
  (m3) tirar o remapeamento em `of_retentativa.py`      → G, em `tests/test_of_retentativa.py`
  (m4) `<=` virar `<` na borda do teto                  → C (120 min exatos, os dois)
       `>` virar `>=` no teto do futuro                 → C (+5 min exatos, os dois)
  (m5) o teto agir também sobre `needs_user_action`     → A (dispositivo, login_error), F (3 h)
       o teto antes das transformações do `out()`       → A (3 casos), C, D, E, I
       o teto sobre qualquer estado                     → A (os 11 não-updating), F
  tirar `SQL_COLETA_ESTOURADA` do snapshot ou de `_COLUNAS_DA_CONEXAO` → C, D, E
Positivos: os `updating` com `coleta_estourada=False`, a D1 (30–120 min), as
saídas (E) e o dispositivo dentro e fora da janela (F).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

import core.services.pluggy_sync as ps
import db
from core.services.pluggy_health import (
    JANELA_DEVICE_AUTH_MIN, connection_ui_state, mesclar_health_em_coleta)
from db.connection import get_conn
from db.open_finance_state import (
    PRAZO_COLETA_MIN, SQL_COLETA_ESTOURADA, TETO_ATUALIZANDO_MIN, aplica_teto_por_health,
    list_connections_para_retentar)
from test_of_coleta_sem_fim import (
    ITEM, UPDATING_REMOTO, VENCIDA, _conecta, _sql, _sync_de_fundo, _tique_de_saude,
    _ui_das_duas)
from test_of_connection_state import (
    ITEM_CAIXA_QR, ITEM_SAUDAVEL, _conta_pluggy, _envelhece_autorizacao, _linha, _mock_pluggy,
    _tx_pluggy)

TETO = ("error_recoverable", "Erro temporário",
        "O banco está demorando — atualize de novo mais tarde")
AGORA = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
# Item em coleta sem `statusDetail`: `derive_item_health` grava `products` vazio.
SEM_DETALHE = {"id": ITEM, "status": "UPDATING", "executionStatus": "ACCOUNTS_IN_PROGRESS"}


def _trio(ui: dict) -> tuple:
    return ui["state"], ui["label"], ui["detail"]


# ── H. a constante ──────────────────────────────────────────────────────────

def test_o_teto_e_o_da_decisao_do_dono():
    assert TETO_ATUALIZANDO_MIN == 120
    assert TETO_ATUALIZANDO_MIN > JANELA_DEVICE_AUTH_MIN + 5, "o QR inteiro cabe antes"
    assert TETO_ATUALIZANDO_MIN > PRAZO_COLETA_MIN, "a faixa da D1 continua existindo"


# ── A. puro: o teto só age sobre `updating`, no fim do `out()` ──────────────

_SAUDAVEL = {"observed_at": AGORA.isoformat(), "item_status": "UPDATED",
             "execution_status": "SUCCESS", "stale_products": [],
             "products": {"BANK": {"updated": True, "last_updated_at": AGORA.isoformat(),
                                   "warnings": []}}}
_PARCIAL = {**_SAUDAVEL, "stale_products": ["CREDIT"], "products": {
    **_SAUDAVEL["products"],
    "CREDIT": {"updated": False, "last_updated_at": "2026-09-12T00:00:00Z", "warnings": []}}}
_COLETANDO = {"observed_at": AGORA.isoformat(), "item_status": "UPDATING",
              "execution_status": None, "products": {}, "stale_products": []}

# Todo jeito de chegar a `updating`, um por return/transformação.
_UPDATING = {
    "local_updating_sem_health": {"status": "UPDATING"},
    "nunca_sincronizou_sem_health": {"status": "ACTIVE"},
    "saudavel_sem_sync": {"status": "ACTIVE", "health": _SAUDAVEL},
    "coletando_sem_sync": {"status": "ACTIVE", "health": {**_SAUDAVEL, "item_status": "UPDATING"}},
    "coletando_sem_info_sincronizado": {"status": "ACTIVE", "health": _COLETANDO,
                                        "last_sync_at": AGORA},
    "parcial_nosso_sem_sync": {"status": "ACTIVE", "status_reason": "investments_read_failed",
                               "health": _SAUDAVEL},
    "local_updating_sincronizado": {"status": "UPDATING", "last_sync_at": AGORA},
}

_OUTROS = {
    "dispositivo_sem_health": ({"status": "OUTDATED",
                                "execution_status": "USER_AUTHORIZATION_PENDING"},
                               "needs_user_action"),
    "login_error": ({"status": "ERROR", "health": {**_SAUDAVEL, "item_status": "LOGIN_ERROR"}},
                    "needs_user_action"),
    "item_em_error": ({"status": "ERROR", "health": {**_SAUDAVEL, "item_status": "ERROR"},
                       "last_sync_at": AGORA}, "error_recoverable"),
    "erro_do_webhook": ({"status": "ERROR", "last_sync_at": AGORA}, "error_recoverable"),
    "read_failed_sem_sync": ({"status": "ACTIVE", "status_reason": "read_failed"},
                             "error_recoverable"),
    "sem_dados": ({"status": "ERROR", "status_reason": "no_accounts", "health": _SAUDAVEL,
                   "last_sync_at": AGORA}, "no_accounts"),
    "parcial": ({"status": "ACTIVE", "health": _PARCIAL, "last_sync_at": AGORA}, "partial"),
    "atualizado": ({"status": "ACTIVE", "health": _SAUDAVEL, "last_sync_at": AGORA}, "updated"),
    "item_missing": ({"status": "ERROR", "status_reason": "item_missing"}, "item_missing"),
    "pausado": ({"status": "PAUSED"}, "paused"),
    "removido": ({"status": "DELETED"}, "removed"),
}


@pytest.mark.parametrize("caso", list(_UPDATING))
def test_updating_estourado_vira_erro_temporario(caso):
    linha = _UPDATING[caso]
    assert connection_ui_state(linha)["state"] == "updating", "positivo: sem o teto, gira"
    assert _trio(connection_ui_state({**linha, "coleta_estourada": True})) == TETO
    # O teto vence a D1 (as duas valem juntas depois de 120 min sem sync).
    assert _trio(connection_ui_state(
        {**linha, "coleta_estourada": True, "coleta_vencida": True})) == TETO


@pytest.mark.parametrize("caso", list(_UPDATING))
def test_D1_continua_entre_o_prazo_e_o_teto(caso):
    ui = connection_ui_state({**_UPDATING[caso], "coleta_vencida": True,
                              "coleta_estourada": False})
    assert (ui["state"], ui["label"], ui["detail"]) == ("updating", "Atualizando…", VENCIDA)


@pytest.mark.parametrize("caso", list(_OUTROS))
def test_o_teto_nao_mexe_em_quem_nao_esta_atualizando(caso):
    linha, esperado = _OUTROS[caso]
    base = connection_ui_state(linha)
    assert base["state"] == esperado
    assert connection_ui_state({**linha, "coleta_estourada": True,
                                "coleta_vencida": True}) == base


# ── B. puro: `coletando_desde` na mescla ────────────────────────────────────

def _foto(item_status: str, observed_at: str, **extra) -> dict:
    return {"observed_at": observed_at, "item_status": item_status, "execution_status": None,
            "products": {}, "stale_products": [], **extra}


_T0, _T1, _T2 = "2026-10-01T09:00:00+00:00", "2026-10-01T10:00:00+00:00", "2026-10-01T12:00:00+00:00"


@pytest.mark.parametrize("anterior, desde", [
    (None, _T2),
    (_foto("UPDATING", _T1, coletando_desde=_T0), _T0),
    (_foto("CREATED", _T1, coletando_desde=_T0), _T0),
    (_foto("UPDATING", _T1), _T1),                       # legado, sem a chave
    (_foto("UPDATED", _T1, coletando_desde=_T0), _T2),
    (_foto("ERROR", _T1, coletando_desde=_T0), _T2),
    (_foto("MISSING", _T1, coletando_desde=_T0), _T2),
    (_foto("LOGIN_ERROR", _T1, coletando_desde=_T0), _T2),
    (_foto("WAITING_USER_ACTION", _T1, coletando_desde=_T0), _T2),
], ids=["sem_anterior", "herda", "herda_created", "legado_herda_observed_at", "updated_zera",
        "error_zera", "missing_zera", "login_error_zera", "dispositivo_zera"])
def test_coletando_desde_na_mescla(anterior, desde):
    novo = _foto("UPDATING", _T2)
    copia_novo, copia_anterior = dict(novo), (dict(anterior) if anterior else None)

    assert mesclar_health_em_coleta(anterior, novo)["coletando_desde"] == desde

    assert (novo, anterior) == (copia_novo, copia_anterior), "pura: não muta"


def test_foto_final_nao_ganha_coletando_desde():
    anterior = _foto("UPDATING", _T1, coletando_desde=_T0)
    assert "coletando_desde" not in mesclar_health_em_coleta(anterior, _foto("UPDATED", _T2))


# ── C. banco: as bordas, pelos selects reais ────────────────────────────────

def _ui_dos_tres(uid: int, conexao_id: int) -> dict:
    """Tela (snapshot), toast (`get_connections_by_item_id`) e a listagem da
    retentativa: três selects, um estado."""
    tela = _ui_das_duas(uid)
    [linha] = list_connections_para_retentar(id=conexao_id)
    assert connection_ui_state(linha) == tela, "a retentativa leu outro estado"
    return tela


def _sincronizada_coletando(uid: int, minutos: int, *, legado: bool = False) -> dict:
    """U4: sincronizou, e o item segue em coleta sem produto há `minutos`."""
    conexao = _conecta(uid)
    desde = ("'coletando_desde', (now() - make_interval(mins => %s))::text, "
             "'observed_at', now()::text") if not legado else (
             "'observed_at', (now() - make_interval(mins => %s))::text")
    _sql("update open_finance_connections set status='ACTIVE', reconnected_at=null, "
         "created_at = now() - interval '2 days', last_sync_at = now() - interval '1 day', "
         "health = jsonb_build_object('item_status', 'UPDATING', 'products', '{}'::jsonb, "
         f"'stale_products', '[]'::jsonb, {desde}) where id=%s", minutos, conexao["id"])
    return conexao


@pytest.mark.parametrize("minutos, esperado", [
    (TETO_ATUALIZANDO_MIN - 1, ("updating", "Atualizando…", VENCIDA)),
    (TETO_ATUALIZANDO_MIN, TETO),
    (-1, ("updating", "Atualizando…", "Ainda não sincronizou")),
    (-10 * 24 * 60, TETO),
], ids=["119min_D1", "120min_teto", "futuro_1min", "futuro_10dias"])
def test_sem_sync_pela_autorizacao(user_id, minutos, esperado):
    conexao = _conecta(user_id, "UPDATED")
    _envelhece_autorizacao(conexao["id"], minutos)
    assert _trio(_ui_dos_tres(user_id, conexao["id"])) == esperado


@pytest.mark.parametrize("minutos, legado, teto", [
    (TETO_ATUALIZANDO_MIN - 1, False, False),
    (TETO_ATUALIZANDO_MIN, False, True),
    (180, True, True),
    (0, True, False),
], ids=["119min", "120min_teto", "legado_observed_3h", "legado_observed_agora"])
def test_sincronizada_em_coleta_pelo_coletando_desde(user_id, minutos, legado, teto):
    conexao = _sincronizada_coletando(user_id, minutos, legado=legado)
    ui = _ui_dos_tres(user_id, conexao["id"])
    assert _trio(ui) == (TETO if teto else ("updating", "Atualizando…", None))


def _estourada_na_mesma_transacao(conexao_id: int, sets: str, *args) -> bool:
    """A borda exata: o `now()` é constante na transação, então o carimbo e a
    leitura veem o MESMO instante (fora dela o relógio anda e a borda some). O
    ramo em Python recebe esse mesmo `now()` como `agora`."""
    with get_conn() as c:
        c.execute(f"update open_finance_connections set {sets} where id=%s", (*args, conexao_id))
        linha = c.execute(f"select health, now() as agora, {SQL_COLETA_ESTOURADA} "
                          "from open_finance_connections where id=%s", (conexao_id,)).fetchone()
        c.rollback()
    return aplica_teto_por_health(dict(linha), linha["agora"])["coleta_estourada"]


_AUTORIZACAO = ("last_sync_at = null, reconnected_at = null, "
                "created_at = now() - make_interval(mins => %s, secs => %s)")
_COLETANDO_DESDE = ("last_sync_at = now() - interval '1 day', reconnected_at = null, "
                    "created_at = now() - interval '2 days', health = jsonb_build_object("
                    "'item_status', 'UPDATING', 'observed_at', now()::text, 'coletando_desde', "
                    "(now() - make_interval(mins => %s, secs => %s))::text)")


@pytest.mark.parametrize("sets", [_AUTORIZACAO, _COLETANDO_DESDE], ids=["autorizacao", "coletando"])
@pytest.mark.parametrize("mins, secs, estourada", [
    (TETO_ATUALIZANDO_MIN, 0, True),
    (TETO_ATUALIZANDO_MIN - 1, 59, False),
    (-5, 0, False),
    (-5, -1, True),
], ids=["120min_exatos", "119min59s", "futuro_5min_exatos", "futuro_5min01s"])
def test_borda_exata_do_teto(user_id, sets, mins, secs, estourada):
    conexao = _conecta(user_id)
    assert _estourada_na_mesma_transacao(conexao["id"], sets, mins, secs) is estourada


# ── D. a ida e volta: foto nova não reinicia o relógio ──────────────────────

def test_job_de_saude_e_sync_com_o_item_em_coleta_nao_tiram_do_teto(user_id, monkeypatch):
    _sincronizada_coletando(user_id, 180)
    assert _trio(_ui_das_duas(user_id)) == TETO

    _tique_de_saude(monkeypatch, SEM_DETALHE)
    assert _linha()["health"]["observed_at"] > "2001", "o job regravou a foto"
    assert _trio(_ui_das_duas(user_id)) == TETO, "a foto do job reiniciou o relógio"

    _mock_pluggy(monkeypatch, item=SEM_DETALHE, contas=[_conta_pluggy()], txs=[_tx_pluggy()])
    _sync_de_fundo(monkeypatch)
    linha = _linha()
    assert (linha["status_reason"], linha["health"]["item_status"]) == (None, "UPDATING")
    assert _trio(_ui_das_duas(user_id)) == TETO, "o sync reiniciou o relógio"


# ── E. as saídas do teto ────────────────────────────────────────────────────

@pytest.mark.parametrize("como", ["sincronizada", "sem_sync"])
def test_sync_com_o_item_pronto_tira_do_teto(user_id, monkeypatch, como):
    if como == "sincronizada":
        _sincronizada_coletando(user_id, 180)
    else:
        _envelhece_autorizacao(_conecta(user_id)["id"], 180)
    assert _trio(_ui_das_duas(user_id)) == TETO

    _mock_pluggy(monkeypatch, item=ITEM_SAUDAVEL, contas=[_conta_pluggy()], txs=[_tx_pluggy()])
    _sync_de_fundo(monkeypatch)

    assert _trio(_ui_das_duas(user_id))[:2] == ("updated", "Atualizado")


@pytest.mark.parametrize("como", ["sincronizada", "sem_sync"])
def test_reconexao_reinicia_o_relogio(user_id, como):
    if como == "sincronizada":
        _sincronizada_coletando(user_id, 180)
    else:
        _envelhece_autorizacao(_conecta(user_id)["id"], 180)
    assert _trio(_ui_das_duas(user_id)) == TETO

    _conecta(user_id)   # o widget de novo: o ramo do conflito do upsert

    assert _ui_das_duas(user_id)["state"] == "updating"


# ── F. a instrução de dispositivo nunca vira teto ───────────────────────────

@pytest.mark.parametrize("minutos, detalhe", [
    (45, "Autorize o acesso no app do banco"),
    (180, "Reautorize o banco"),
])
def test_caixa_esperando_o_dispositivo_nao_vira_teto(user_id, minutos, detalhe):
    conexao = _conecta(user_id, item=ITEM_CAIXA_QR)
    _envelhece_autorizacao(conexao["id"], minutos)
    ui = _ui_das_duas(user_id, ITEM_CAIXA_QR["id"])
    assert (ui["state"], ui["detail"]) == ("needs_user_action", detalhe)


def test_updating_com_execution_status_de_dispositivo_ainda_nao_e_teto(user_id):
    """61 min: fora da janela do QR, dentro do teto — D1, não "Erro temporário"."""
    conexao = _conecta(user_id, item={**ITEM_CAIXA_QR, "id": ITEM, "status": "UPDATING"})
    _envelhece_autorizacao(conexao["id"], JANELA_DEVICE_AUTH_MIN + 1)
    assert _trio(_ui_das_duas(user_id)) == ("updating", "Atualizando…", VENCIDA)


# ── I. o toast do Atualizar, pelo relatório real ────────────────────────────

def test_relatorio_do_refresh_entrega_o_teto(user_id):
    _sincronizada_coletando(user_id, 180)
    outro = db.save_pluggy_open_finance_item(
        user_id, {"id": "item-coletando", "status": "UPDATING",
                  "connector": {"id": 612, "name": "Nubank"}})
    assert outro["id"]

    itens = {i["item_id"]: i for i in ps._refresh_items_report(
        user_id, [ITEM, "item-coletando"], {}, {}, {}, set())}

    assert (itens[ITEM]["state"], itens[ITEM]["label"], itens[ITEM]["detail"]) == TETO
    # O outro segue coletando (neutro no toast); quem decide que o erro vence é o
    # `refreshVerdict` (`tests/frontend/of_refresh_ui.test.mjs`, Codex #455).
    assert itens["item-coletando"]["state"] == "updating"


# ── isolamento: o derivado é da linha do próprio usuário ────────────────────

def test_o_teto_de_um_usuario_nao_vaza_para_o_outro(user_id):
    outro = user_id + 1
    db.ensure_user(outro)
    _sincronizada_coletando(user_id, 180)
    _conecta(outro, item={"id": "item-do-outro", "status": "UPDATING",
                          "connector": {"id": 612, "name": "Nubank"}})

    assert _trio(_ui_das_duas(user_id)) == TETO
    [so_dele] = db.get_open_finance_snapshot(outro)["connections"]
    assert so_dele["provider_item_id"] == "item-do-outro"
    assert so_dele["ui"]["state"] == "updating"
