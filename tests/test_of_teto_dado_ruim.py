"""Teto do "Atualizando…": dado ruim na âncora (`health.coletando_desde` /
`observed_at`) nunca derruba leitura. Irmão de `tests/test_of_teto_atualizando.py`.

O bloqueio: com um `::timestamptz` cru no select, UMA linha com texto ilegível
levantava `InvalidDatetimeFormat` na query inteira — e a da retentativa
(`list_connections_para_retentar`) é entre usuários: ninguém era retentado no
tique. Ilegível = sem âncora = sem teto (o card gira como antes).

CONTROLES (medidos em 2026-10-01, remeça se mexer no código):
  (m6) o ramo com sync de volta ao `::timestamptz` cru em `SQL_COLETA_ESTOURADA`
       → J (o banco levanta) — negativo.
  (m7–m9) tirar `aplica_teto_por_health` da retentativa, de `get_connections_by_item_id`
       ou do snapshot → J (B não estoura) e os de banco de `test_of_teto_atualizando.py`.
  (m10) `coletando_desde` lido com `or` ("" cai no `observed_at`) → os dois
       `test_vazio_nao_cai_no_observed_at*` (o `observed_at` deles tem 3 h).
  (m11) tirar `agora.tzinfo is None` da guarda → `test_agora_naive_nao_levanta`.
Positivos: o usuário B (dado perfeito) continua estourado e retentado; o texto
válido estoura em 120 min exatos e não em 119:59.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

import db
from conftest import _cleanup_user
from core.services.of_retentativa import classe_de_retentativa
from core.services.pluggy_health import connection_ui_state
from db.open_finance_state import (
    TETO_ATUALIZANDO_MIN, aplica_teto_por_health, list_connections_para_retentar)
from test_of_coleta_sem_fim import _conecta, _sql

AGORA = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
RUINS = {
    "vazio": "", "null": None, "lixo": "lixo", "dia_30_fev": "2026-02-30T00:00:00+00:00",
    "mes_13": "2026-13-01T00:00:00+00:00", "sem_hora": "2026-10-01",
    "sem_fuso": "2026-10-01T10:00:00", "numero": 123,
}


def _health(campo: str, valor, agora: datetime | None = None) -> dict:
    """Item em coleta sem produto; `valor` na âncora `campo`. Com `coletando_desde`
    ruim, o `observed_at` é `agora`: o null cai nele e também não estoura."""
    agora = (agora or datetime.now(timezone.utc)).isoformat()
    base = {"item_status": "UPDATING", "products": {}, "stale_products": []}
    if campo == "observed_at":
        return {**base, "observed_at": valor}
    return {**base, "observed_at": agora, "coletando_desde": valor}


# ── puro ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("campo", ["observed_at", "coletando_desde"])
@pytest.mark.parametrize("valor", list(RUINS.values()), ids=list(RUINS))
def test_ancora_ilegivel_nao_estoura(campo, valor):
    linha = {"coleta_estourada": None, "health": _health(campo, valor, AGORA)}
    assert aplica_teto_por_health(linha, AGORA)["coleta_estourada"] is False


@pytest.mark.parametrize("campo", ["observed_at", "coletando_desde"])
@pytest.mark.parametrize("idade, estourada", [
    (timedelta(minutes=TETO_ATUALIZANDO_MIN), True),
    (timedelta(minutes=TETO_ATUALIZANDO_MIN - 1, seconds=59), False),
], ids=["120min_exatos", "119min59s"])
def test_ancora_valida_continua_estourando(campo, idade, estourada):
    health = {"item_status": "UPDATING", campo: (AGORA - idade).isoformat()}
    linha = aplica_teto_por_health({"coleta_estourada": None, "health": health}, AGORA)
    assert linha["coleta_estourada"] is estourada


def test_vazio_nao_cai_no_observed_at():
    """O "" é dado ruim, não chave ausente: não herda o `observed_at` de 3 h atrás."""
    health = _health("coletando_desde", "", AGORA - timedelta(hours=3))
    linha = aplica_teto_por_health({"coleta_estourada": None, "health": health}, AGORA)
    assert linha["coleta_estourada"] is False


def test_agora_naive_nao_levanta():
    """`agora` sem fuso não se compara com a âncora: sem teto, sem TypeError."""
    health = _health("coletando_desde", (AGORA - timedelta(hours=3)).isoformat(), AGORA)
    linha = aplica_teto_por_health({"coleta_estourada": None, "health": health},
                                   AGORA.replace(tzinfo=None))
    assert linha["coleta_estourada"] is False


# ── J/K. banco: dois usuários, um com dado ruim ─────────────────────────────

@pytest.fixture()
def outro_id(user_id):
    uid = user_id + 1
    db.ensure_user(uid)
    yield uid
    _cleanup_user(uid)


def _sincronizada_em_coleta(uid: int, item: str, health: dict) -> int:
    conexao = _conecta(uid, item={"id": item, "status": "UPDATING",
                                  "connector": {"id": 612, "name": "Nubank"}})
    _sql("update open_finance_connections set status='UPDATING', reconnected_at=null, "
         "last_attempt_at=null, created_at = now() - interval '2 days', "
         "last_sync_at = now() - interval '1 day', health = %s::jsonb where id=%s",
         json.dumps(health), conexao["id"])
    return conexao["id"]


@pytest.mark.parametrize("campo", ["observed_at", "coletando_desde"])
@pytest.mark.parametrize("valor", list(RUINS.values()), ids=list(RUINS))
def test_dado_ruim_de_um_nao_derruba_ninguem(user_id, outro_id, campo, valor):
    _confere_dois_usuarios(user_id, outro_id, _health(campo, valor))


def test_vazio_nao_cai_no_observed_at_no_banco(user_id, outro_id):
    tres_horas = datetime.now(timezone.utc) - timedelta(hours=3)
    _confere_dois_usuarios(user_id, outro_id, _health("coletando_desde", "", tres_horas))


def _confere_dois_usuarios(user_id: int, outro_id: int, health_ruim: dict) -> None:
    id_ruim = _sincronizada_em_coleta(user_id, "item-ruim", health_ruim)
    tres_horas = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    id_bom = _sincronizada_em_coleta(outro_id, "item-bom", _health("coletando_desde", tres_horas))

    # (i) a retentativa entre usuários: B é retentado; A aparece, sem teto.
    linhas = {r["id"]: r for r in list_connections_para_retentar()}
    assert linhas[id_bom]["coleta_estourada"] is True
    assert classe_de_retentativa(linhas[id_bom]) == "coleta"
    assert linhas[id_ruim]["coleta_estourada"] is False

    # (ii)/K: o snapshot de A sai inteiro, o card segue "Atualizando…".
    [conexao] = db.get_open_finance_snapshot(user_id)["connections"]
    assert (conexao["provider_item_id"], conexao["ui"]["state"], conexao["ui"]["label"]) == (
        "item-ruim", "updating", "Atualizando…")
    assert "coleta_estourada" not in conexao

    # (iii) o toast/webhook.
    [linha] = db.get_connections_by_item_id("item-ruim")
    assert linha["coleta_estourada"] is False
    assert connection_ui_state(linha)["state"] == "updating"
