"""Onda 5, PR-C1: CAS pela VERSÃO da linha (`updated_at`) na observação do item.

Contrato: `docs/open_finance_estados.md` §2.1 ("Conserto de classe") e o plano
`.time-dev/of-onda5-c/plano-r1.md` §4/§5. `observar_item` (core/services/pluggy_sync.py)
grava o que o `GET /items` disse; `mark_sync_result(versao_vista=<updated_at lido>)`
só grava se a linha ainda tem aquela versão. Antes era o VALOR do motivo
(`status_reason_visto`), que não via quem mudava a linha e mantinha o motivo.

Pelo caminho real: Postgres real, Pluggy mockada nas funções HTTP, tela pela rota
`GET /open-finance/{uid}`. O job lê a linha, faz o `GET /items` (aqui, o "meio") e só
então grava; o que acontece no meio roda dentro do próprio `get_pluggy_item`.

NUNCA `relogio_fixo` aqui: ele congela o `datetime` de `db.open_finance_state` e os
dois `updated_at` ficam IGUAIS, então o CAS não distingue nada e o teste mediria o
relógio falso. Relógio real; a ida e volta e os bumps usam `updated_at` explícito
via SQL.

CONTROLES (medidos em CÓPIA e restaurados com `cmp`, 2026-10-03; remeça se mexer):
  negativo M1, tirar a cláusula `updated_at is not distinct from` do UPDATE de
      `mark_sync_result`: X1, X2, X4, X5, `test_motivo_mudou_no_meio_segue_descartado`
      e a ida e volta (versão errada gravando) ficam vermelhos.
  negativo M2, a versão trocada pelo VALOR do motivo (cláusula `status_reason is not
      distinct from` e `observar_item` passando `linha["status_reason"]`, o que o job
      fazia): X1, X2, X4 e X5 vermelhos (motivo igual); o caso do motivo que mudou
      segue verde, é o que o CAS por valor já pegava. A ida e volta também fica
      vermelha, mas por erro de tipo (datetime contra texto): artefato da mutação.
  negativo M3, `observar_item(None)` sem `versao_vista` (o 404 sem CAS de antes): X4 e
      X5 vermelhos, X1 e X2 verdes.
  negativo M4, `updated_at` fora do `mark_sync_attempt`: `test_cada_escritor_bumpa_a_versao`
      vermelho só no parâmetro dele, e a guarda estrutural também.
  negativo M5, a mesma cláusula fora do UPDATE: `test_x6_...` (webhook `item/error`
      no meio do GET do job, motivo igual) fica vermelho junto com X1, X2, X4 e X5.
  positivo: `test_sem_concorrencia_o_job_grava_health_e_par` (sem corrida o job grava
      health + par e `perdeu == 0`) e o caso de motivo que mudou (descartado). Sem o
      positivo o grupo passaria num `observar_item` que nunca grava.

CLASSE QUE `test_todo_update_de_estado_da_conexao_bumpa_updated_at` NÃO PEGA: um
UPDATE que não tem `updated_at` e cuja PRIMEIRA coluna do SET é da allowlist (ela só
olha essa, `\\bset\\s+(\\w+)`), então um que comece por `next_refresh_at` e também
escreva `status` passaria sem bump; SQL
montado dinamicamente (f-string que compõe o `update`), escrita fora de db/, core/
e frontend/ (ex.: scripts/), e o upsert `insert ... on conflict do update` (que só o
caso parametrizado de `save_pluggy_open_finance_item` cobre). E nenhum teste deste
arquivo vê a latência real de um `GET /items` nem o comportamento real da Pluggy.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

import core.services.pluggy_sync as ps
import db
from conftest import promote_to_pro
from core.services.pluggy import PluggyApiError
from db.connection import get_conn
from test_of_connection_state import (
    ITEM_SAUDAVEL, _conexao, _conta_pluggy, _linha, _mock_pluggy, _set_estado, _tx_pluggy)
from test_of_leitura_incompleta import _envelhece, _ui_pela_rota

LOGIN_ERROR = {**ITEM_SAUDAVEL, "status": "LOGIN_ERROR", "executionStatus": "ERROR"}
RAIZ = Path(__file__).resolve().parent.parent


def _pronta(user_id: int, monkeypatch) -> dict:
    """Conexão sincronizada (ACTIVE, com `health` e espelho) e `health` envelhecido:
    elegível para o job de saúde."""
    promote_to_pro(user_id)
    conexao = _conexao(user_id)
    _mock_pluggy(monkeypatch, item=ITEM_SAUDAVEL, contas=[_conta_pluggy()], txs=[_tx_pluggy()])
    assert ps.sync_pluggy_item("item-g1")["ok"] is True
    _envelhece()
    return conexao


def _job_com_no_meio(monkeypatch, no_meio, resposta_do_job) -> dict:
    """Roda o job. No `GET /items` dele roda `no_meio()` (outro processo) e devolve
    `resposta_do_job` (a leitura que, nesse instante, já está velha). `no_meio`
    enxerga a Pluggy como estava mockada antes."""
    mundo = ps.get_pluggy_item
    feito: list = []

    def _get(item_id, api_key=None):
        if not feito:
            feito.append(True)
            monkeypatch.setattr(ps, "get_pluggy_item", mundo)
            no_meio()
        if isinstance(resposta_do_job, Exception):
            raise resposta_do_job
        return resposta_do_job

    monkeypatch.setattr(ps, "get_pluggy_item", _get)
    return ps.run_of_health_check()


def _sync_ok() -> None:
    assert ps.sync_pluggy_item("item-g1")["ok"] is True


# X1: sync ok no meio, motivo IGUAL antes e depois (None): o CAS por valor não via
def test_x1_sync_ok_no_meio_com_motivo_igual_nao_e_sobrescrito(user_id, monkeypatch):
    _pronta(user_id, monkeypatch)
    assert _linha()["status_reason"] is None

    res = _job_com_no_meio(monkeypatch, _sync_ok, LOGIN_ERROR)

    assert res["perdeu"] == 1
    linha = _linha()
    assert linha["status"] == "ACTIVE", "o job gravou a leitura velha por cima do sync"
    assert linha["health"]["item_status"] == "UPDATED"
    assert _ui_pela_rota(user_id)["state"] == "updated"


# X2: reconexão no meio (zera health, motivo NULL antes e depois)
def test_x2_reconexao_no_meio_nao_recebe_o_health_da_autorizacao_velha(user_id, monkeypatch):
    _pronta(user_id, monkeypatch)

    def reconecta():
        db.save_pluggy_open_finance_item(
            user_id, {"id": "item-g1", "status": "UPDATING",
                      "connector": {"id": 612, "name": "Nubank"}})
        assert _linha()["health"] is None

    res = _job_com_no_meio(monkeypatch, reconecta, ITEM_SAUDAVEL)

    assert res["perdeu"] == 1
    assert _linha()["health"] is None, "o job devolveu o health da autorização antiga"


# X4: lote de 404 do job × sync ok no meio: "Conexão perdida" com item vivo
def test_x4_404_do_job_nao_vence_sync_ok_no_meio(user_id, monkeypatch):
    _pronta(user_id, monkeypatch)

    res = _job_com_no_meio(monkeypatch, _sync_ok, PluggyApiError("x", status_code=404))

    assert res["perdeu"] == 1
    linha = _linha()
    assert (linha["status"], linha["status_reason"]) == ("ACTIVE", None)
    assert _ui_pela_rota(user_id)["state"] == "updated"


# X5: 404 do SYNC (W4) × reconexão do mesmo item no meio do GET
def test_x5_404_do_sync_nao_vence_reconexao_no_meio(user_id, monkeypatch):
    _pronta(user_id, monkeypatch)

    def get_404_com_reconexao(item_id, api_key=None):
        db.save_pluggy_open_finance_item(
            user_id, {"id": "item-g1", "status": "UPDATING",
                      "connector": {"id": 612, "name": "Nubank"}})
        raise PluggyApiError("x", status_code=404)

    monkeypatch.setattr(ps, "get_pluggy_item", get_404_com_reconexao)

    res = ps.sync_pluggy_item("item-g1")

    assert res["reason"] == "item_missing", "o retorno ao chamador não muda"
    linha = _linha()
    assert linha["status_reason"] != "item_missing", "item_missing por cima da autorização nova"
    assert linha["health"] is None


# o caso do PR-A, por comportamento: o motivo mudou no meio e o job não regrava
def test_motivo_mudou_no_meio_segue_descartado(user_id, monkeypatch):
    promote_to_pro(user_id)
    _conexao(user_id)
    _mock_pluggy(monkeypatch, item=ITEM_SAUDAVEL, contas=[])
    ps.sync_pluggy_item("item-g1")
    assert _linha()["status_reason"] == "no_accounts"
    _envelhece()

    def sync_com_contas():
        monkeypatch.setattr(ps, "list_pluggy_accounts", lambda i, k=None: [_conta_pluggy()])
        monkeypatch.setattr(ps, "list_pluggy_transactions",
                            lambda acc, k=None, **kw: [_tx_pluggy()])
        _sync_ok()
        assert _linha()["status_reason"] is None

    res = _job_com_no_meio(monkeypatch, sync_com_contas, ITEM_SAUDAVEL)

    assert res["perdeu"] == 1
    assert _linha()["status_reason"] is None
    assert _ui_pela_rota(user_id)["state"] == "updated"


# X6: o webhook `item/error` de hoje (veredito ERROR) no meio do GET do job, motivo igual
def test_x6_webhook_item_error_no_meio_nao_e_sobrescrito_pelo_job(user_id, monkeypatch):
    _pronta(user_id, monkeypatch)
    assert _linha()["status_reason"] is None

    def webhook():
        assert db.update_pluggy_open_finance_item_status("item-g1", "ERROR") == 1

    res = _job_com_no_meio(monkeypatch, webhook, ITEM_SAUDAVEL)

    assert res["perdeu"] == 1
    assert _linha()["status"] == "ERROR", "o job gravou a leitura velha por cima do webhook"


# POSITIVO: sem concorrência o job grava health + par normalmente
def test_sem_concorrencia_o_job_grava_health_e_par(user_id, monkeypatch):
    _pronta(user_id, monkeypatch)
    _mock_pluggy(monkeypatch, item=LOGIN_ERROR)

    res = ps.run_of_health_check()

    assert (res["checked"], res["perdeu"]) == (1, 0)
    linha = _linha()
    assert linha["status"] == "ERROR"
    assert linha["health"]["item_status"] == "LOGIN_ERROR"
    assert not linha["health"]["observed_at"].startswith("2000")


# ida e volta EXATA do `updated_at` (microssegundo): leu, devolveu, 1 linha
def test_ida_e_volta_exata_do_updated_at(user_id):
    conexao = _conexao(user_id)
    _set_estado(conexao["id"], updated_at=datetime(2026, 9, 1, 10, 0, 0, 123457, tzinfo=timezone.utc))

    pela_listagem = db.list_connections_for_health_check(older_than_sec=1, limit=50)
    visto = next(r for r in pela_listagem if r["id"] == conexao["id"])["updated_at"]
    assert visto == db.get_linha_para_observar(conexao["id"], user_id)["updated_at"] == _linha()["updated_at"]
    assert visto.microsecond == 123457

    assert db.mark_sync_result(conexao["id"], ok=None, versao_vista=visto.replace(microsecond=123458)) == 0
    assert db.mark_sync_result(conexao["id"], ok=None, versao_vista=visto) == 1


def test_get_linha_para_observar_filtra_o_usuario(user_id):
    conexao = _conexao(user_id)

    assert db.get_linha_para_observar(conexao["id"], user_id)["has_data"] is False
    assert db.get_linha_para_observar(conexao["id"], user_id + 1) is None


# todo escritor de estado bumpa a versão, senão o CAS não o enxerga
ESCRITORES = {
    "save_pluggy_open_finance_item": lambda uid, c: db.save_pluggy_open_finance_item(
        uid, {"id": "item-g1", "status": "UPDATED", "connector": {"id": 612, "name": "Nubank"}}),
    "mark_sync_attempt": lambda uid, c: db.mark_sync_attempt(c["id"]),
    "mark_sync_result": lambda uid, c: db.mark_sync_result(c["id"], ok=None, status="ACTIVE"),
    "update_pluggy_open_finance_item_status": lambda uid, c: db.update_pluggy_open_finance_item_status(
        "item-g1", "UPDATING"),
    "pause_open_finance_connection": lambda uid, c: db.pause_open_finance_connection(c["id"]),
}


@pytest.mark.parametrize("escritor", sorted(ESCRITORES))
def test_cada_escritor_bumpa_a_versao(user_id, escritor):
    conexao = _conexao(user_id)
    velho = datetime(2000, 1, 1, tzinfo=timezone.utc)
    _set_estado(conexao["id"], updated_at=velho)

    ESCRITORES[escritor](user_id, conexao)

    assert _linha()["updated_at"] > velho, f"{escritor} não bumpou updated_at"


# UPDATEs de open_finance_connections que NÃO escrevem estado (status, motivo,
# health, sync) e por isso não precisam bumpar: claims de refresh e recorrências.
SEM_ESTADO = ("next_refresh_at", "last_refresh_requested_at", "recurring_")


def test_todo_update_de_estado_da_conexao_bumpa_updated_at():
    achados = 0
    for pasta in ("db", "core", "frontend"):
        for arq in (RAIZ / pasta).rglob("*.py"):
            src = arq.read_text()
            for m in re.finditer(r"update\s+open_finance_connections\b", src, re.I):
                achados += 1
                corte = re.search(r"\bwhere\b", src[m.end():], re.I)
                bloco = src[m.end(): m.end() + (corte.start() if corte else 800)]
                sets = re.sub(r"--[^\n]*", "", bloco)   # comentário SQL não conta
                if "updated_at" in sets:
                    continue
                coluna = re.search(r"\bset\s+(\w+)", sets, re.I).group(1)
                assert coluna.startswith(SEM_ESTADO), (
                    f"{arq.relative_to(RAIZ)}: UPDATE de estado sem updated_at ({coluna})")
    assert achados >= 6, "a varredura não achou os UPDATEs conhecidos: o regex quebrou"
