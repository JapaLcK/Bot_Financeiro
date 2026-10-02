"""A foto do patrimônio e o teto do "Atualizando…" (PR #744).

O teto muda o ESTADO (`updating` → `error_recoverable`), e `base.conexoes` da foto
grava o estado. `calcular` tem de aplicar as duas metades do teto como a tela
(`SQL_COLETA_ESTOURADA` sem sync, `aplica_teto_por_health` com sync), senão a foto
diz `updating` onde a tela diz erro.

CONTROLES (mutação numa cópia de `db/patrimonio.py`, restaurada e conferida com
`cmp`; medidos em 2026-10-01, remeça se mexer):
  tirar `aplica_teto_por_health` → `sincronizado_ha_3h` (persiste e cruza) e o isolamento
  tirar a coluna do select       → `sem_sync_ha_3h` (persiste e cruza)
  o `db/patrimonio.py` de antes  → os 5 acima
  tirar o `try` do parse em `aplica_teto_por_health` → `sincronizado_lixo`/`_vazio` (4)
  tirar o `where user_id=%s` do select das conexões → o isolamento
Positivos: coletando há 1 h (`updating`), `updated`, dado ruim na âncora.
"""
from __future__ import annotations

from datetime import date

import pytest
from psycopg.types.json import Jsonb

import db
from conftest import usuario_pagante
from db.patrimonio import gravar_foto
from tests._patrimonio_helpers import AGORA, conexao, foto, horas_atras, q


def _coletando(desde) -> dict:
    # Item em coleta sem produto (`coletando_sem_info`): o "Atualizando…" de quem já
    # sincronizou; a âncora do teto é `coletando_desde`.
    return {"item_status": "UPDATING", "execution_status": None, "products": {},
            "stale_products": [], "observed_at": AGORA.isoformat(), "coletando_desde": desde}


def _cria(uid, caso) -> int:
    if caso == "updated":
        return conexao(uid, f"item-{caso}-{uid}", sync=horas_atras(1))
    sem_sync = caso.startswith("sem_sync")
    c = conexao(uid, f"item-{caso}-{uid}", status="UPDATING" if sem_sync else "UPDATED",
                sync=None if sem_sync else horas_atras(1))
    horas = 3 if caso.endswith("3h") else 1
    health = None
    if not sem_sync:
        # `lixo`: sem `coletando_desde`, cai no `observed_at` ilegível; `vazio`: "".
        ruim = {"sincronizado_lixo": None, "sincronizado_vazio": ""}
        health = _coletando(ruim.get(caso, horas_atras(horas).isoformat()))
        if caso == "sincronizado_lixo":
            health["observed_at"] = "lixo"
        health = Jsonb(health)
    q("update open_finance_connections set created_at=%s, health=%s where id=%s",
      (horas_atras(horas), health, c))
    return c


ESPERADO = {
    "sem_sync_ha_3h": "error_recoverable",       # (a) âncora da autorização, no SQL
    "sincronizado_ha_3h": "error_recoverable",   # (b) âncora no health, em Python
    "sem_sync_ha_1h": "updating",                # (c) dentro do teto
    "sincronizado_ha_1h": "updating",
    "updated": "updated",
    "sincronizado_lixo": "updating",             # (d) dado ruim não levanta
    "sincronizado_vazio": "updating",
}


@pytest.mark.parametrize("caso", list(ESPERADO))
def test_foto_persiste_o_estado_da_tela(caso):
    uid = usuario_pagante()
    c = _cria(uid, caso)
    assert gravar_foto(uid, date(2026, 10, 1)) is True
    base = q("select base from patrimonio_fotos where user_id=%s", (uid,))["base"]
    assert base["conexoes"] == {str(c): ESPERADO[caso]}
    motivos = foto(uid)["motivos"]
    assert ("banco_desatualizado" in motivos) is (ESPERADO[caso] != "updated")


@pytest.mark.parametrize("caso", list(ESPERADO))
def test_foto_concorda_com_o_snapshot(caso):
    uid = usuario_pagante()
    c = _cria(uid, caso)
    tela = {x["id"]: x["ui"]["state"] for x in db.get_open_finance_snapshot(uid)["connections"]}
    assert foto(uid)["base"]["conexoes"][str(c)] == tela[c] == ESPERADO[caso]


def test_estado_de_outro_usuario_nao_entra_na_foto():
    a, b = usuario_pagante(), usuario_pagante()
    ca, cb = _cria(a, "updated"), _cria(b, "sincronizado_ha_3h")
    fa, fb = foto(a), foto(b)
    assert fa["base"]["conexoes"] == {str(ca): "updated"}
    assert "banco_desatualizado" not in fa["motivos"]
    assert fb["base"]["conexoes"] == {str(cb): "error_recoverable"}
