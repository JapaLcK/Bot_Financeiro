"""Onda 5, PR-A: "Atualizado" só sobre leitura completa (R4 e R5).

Contrato e tabela de estados × eventos: `docs/open_finance_estados.md`.

Pelo caminho real: Pluggy mockada nas funções HTTP, estado no Postgres, tela
lida pela rota `GET /open-finance/{uid}` e o toast pelo `sync.items[]` do
`POST /refresh` (é o que `refreshVerdict`, em `frontend/settings.html`, lê).

CONTROLES, por caso (negativo = a mutação que deixa o caso vermelho por
asserção; medidos em 2026-09-27, remeça se mexer no código):

  (a) negativo: `resolve_connection_state` devolver `""` com `has_data` e
      `leitura_completa=False`; ou tirar o ramo `INVESTMENTS_READ_FAILED` do
      `out()` de `connection_ui_state` (vira `error_recoverable`).
  (b) negativo: o ramo `leitura_completa is None` devolver `""` com `has_data`
      (o comportamento de antes do PR-A).
  (c), (d) positivos: o caminho legítimo continua "Atualizado".
  (e) NÃO discrimina nada deste PR — ver o comentário do caso.
  (f), (g), (i) negativo: `observar_item` deixar de passar `versao_vista` ao
      `mark_sync_result` (sem o CAS; PR-C1 trocou o CAS por valor do motivo pelo
      da versão da linha, `tests/test_of_versao_da_linha.py`). (g) e (i) são o
      motivo que MUDOU no meio; (f) é o que o sync limpou no meio.
  positivo do CAS: `test_job_sem_corrida_limpa_no_accounts_com_espelho_cheio`.
  (h) negativo: tirar o `if sem_sync` do ramo `INVESTMENTS_READ_FAILED` do `out()`.
  (j) negativo: tirar o acréscimo de `_DETALHE_INVESTIMENTOS_FALTANDO` ao
      `_stale_detail` no ramo `partial` de `connection_ui_state`.
  (k) positivo: `partial` só da Pluggy continua com o `_stale_detail` puro.
  (l), (m), (o), (p) negativo: tirar o desvio para o default seguro
      (`if reason not in _REASONS_OK ...: return out("updated")`) do ramo
      `partial`; (m) sozinho: condicioná-lo a `not sem_sync`.
  (n) negativo: tirar o `not sem_sync` do acréscimo de investimentos no `partial`.
  (q) negativo: tirar o `"INVESTMENTS" not in stale` do mesmo acréscimo.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import core.services.pluggy_sync as ps
import db
import frontend.finance_bot_websocket_custom as dashboard
from conftest import promote_to_pro
from core.services.pluggy import PluggyApiError
from core.services.pluggy_health import connection_ui_state, derive_item_health
from db.connection import get_conn
from test_of_connection_state import (
    ITEM_SAUDAVEL, _auth, _conexao, _conta_pluggy, _linha, _mock_pluggy, _tx_pluggy)

DETALHE = "Investimentos não vieram nesta atualização"


def _429(*_a, **_kw):
    raise PluggyApiError("rate limit", status_code=429)


def _ui_pela_rota(uid: int, item: str = "item-g1") -> dict:
    client = TestClient(dashboard.app)
    resp = client.get(f"/open-finance/{uid}", headers=_auth(client, uid))
    assert resp.status_code == 200, resp.text
    return next(c["ui"] for c in resp.json()["connections"] if c["provider_item_id"] == item)


def _refresh(uid: int, monkeypatch) -> dict:
    """O botão Atualizar / o puxar: PATCH mockado, sem espera, e o sync real."""
    monkeypatch.setattr(ps, "update_pluggy_item", lambda i, k=None: {})
    monkeypatch.setattr(ps, "_hold_aggregate_emails", lambda uid, origem: None)
    client = TestClient(dashboard.app)
    resp = client.post(f"/open-finance/{uid}/refresh?wait=0", headers=_auth(client, uid))
    assert resp.status_code == 200, resp.text
    return resp.json()["sync"]


def _envelhece(item: str = "item-g1") -> None:
    """`health` velho: a linha fica elegível para o job de saúde."""
    with get_conn() as c:
        c.execute(
            "update open_finance_connections set health = jsonb_set(health, '{observed_at}', "
            "'\"2000-01-01T00:00:00+00:00\"') where provider_item_id=%s", (item,))
        c.commit()


def _tique_de_saude(item: str = "item-g1") -> None:
    """Envelhece o `health` e roda o job, que só faz `GET /items`."""
    _envelhece(item)
    ps.run_of_health_check()
    assert not _linha(item)["health"]["observed_at"].startswith("2000"), "o job não mediu a linha"


def _com_falha(uid: int, monkeypatch, qual: str) -> tuple[str, str]:
    """Conexão com contas espelhadas e uma falha de leitura NOSSA gravada pelo
    Atualizar. Devolve (motivo gravado, estado na tela)."""
    promote_to_pro(uid)
    _conexao(uid)
    _mock_pluggy(monkeypatch, item=ITEM_SAUDAVEL, contas=[_conta_pluggy()], txs=[_tx_pluggy()])
    assert ps.sync_pluggy_item("item-g1")["ok"] is True
    assert _ui_pela_rota(uid)["state"] == "updated"

    if qual == "contas":
        monkeypatch.setattr(ps, "list_pluggy_accounts", _429)
        esperado = ("read_failed", "error_recoverable")
    else:
        monkeypatch.setattr(ps, "list_pluggy_investments", _429)
        esperado = ("investments_read_failed", "partial")
    _refresh(uid, monkeypatch)
    assert (_linha()["status_reason"], _ui_pela_rota(uid)["state"]) == esperado
    # a Pluggy volta ao normal: o que vier depois não falha de novo
    _mock_pluggy(monkeypatch, item=ITEM_SAUDAVEL, contas=[_conta_pluggy()], txs=[_tx_pluggy()])
    return esperado


# (a) R4
def test_429_em_investimentos_com_contas_e_parcial_na_tela_e_no_refresh(user_id, monkeypatch):
    promote_to_pro(user_id)
    _conexao(user_id)
    _mock_pluggy(monkeypatch, item=ITEM_SAUDAVEL, contas=[_conta_pluggy()], txs=[_tx_pluggy()])
    monkeypatch.setattr(ps, "list_pluggy_investments", _429)

    sync = _refresh(user_id, monkeypatch)

    item = next(i for i in sync["items"] if i["item_id"] == "item-g1")
    assert (item["state"], item["label"], item["detail"]) == ("partial", "Parcial", DETALHE)
    assert sync["ok"] is False, "o toast não pode dizer 'Tudo em dia!'"
    ui = _ui_pela_rota(user_id)
    assert (ui["state"], ui["label"], ui["detail"]) == ("partial", "Parcial", DETALHE)
    assert _linha()["last_sync_at"] is not None, "as contas vieram: é sync, só não completo"


# (b) R5
@pytest.mark.parametrize("qual", ["contas", "investimentos"])
def test_tique_de_saude_nao_apaga_falha_de_leitura(user_id, monkeypatch, qual):
    motivo, estado = _com_falha(user_id, monkeypatch, qual)

    _tique_de_saude()

    assert _linha()["status_reason"] == motivo, "o job não leu nada: não pode limpar"
    assert _ui_pela_rota(user_id)["state"] == estado


# (c) positivo: só leitura completa limpa
@pytest.mark.parametrize("qual", ["contas", "investimentos"])
def test_sync_completo_depois_limpa_e_volta_a_atualizado(user_id, monkeypatch, qual):
    _com_falha(user_id, monkeypatch, qual)
    _tique_de_saude()

    assert ps.sync_pluggy_item("item-g1")["ok"] is True

    assert _linha()["status_reason"] is None
    ui = _ui_pela_rota(user_id)
    assert (ui["state"], ui["label"]) == ("updated", "Atualizado")


# (d) positivo: corretora completa
def test_corretora_so_com_investimentos_completa_e_atualizado(user_id, monkeypatch):
    promote_to_pro(user_id)
    _conexao(user_id, "item-corretora")
    _mock_pluggy(monkeypatch, item={**ITEM_SAUDAVEL, "id": "item-corretora"}, contas=[])
    monkeypatch.setattr(ps, "list_pluggy_investments", lambda i, k=None: [
        {"id": "inv-1", "name": "CDB Nu", "type": "FIXED_INCOME", "subtype": "CDB",
         "currencyCode": "BRL", "balance": "1500.00"}])

    sync = _refresh(user_id, monkeypatch)

    assert sync["ok"] is True
    assert [i["state"] for i in sync["items"]] == ["updated"]
    assert _ui_pela_rota(user_id, "item-corretora")["state"] == "updated"


# (e) O QUE ISTO MEDE: depois da falha de leitura e do tique de saúde de um
# usuário, a conexão já sincronizada de OUTRO usuário continua sem motivo e
# "Atualizado" pela rota dele. NÃO prova isolamento deste PR: todas as escritas
# alteradas aqui filtram pela conexão (`where id=%s`), e as asserções sobre o
# OUTRO usuário não ficaram vermelhas em nenhuma mutação medida — quando o caso
# fica vermelho, é nas asserções do PRIMEIRO usuário. É guarda de regressão
# contra uma escrita futura por `provider_item_id`/lote, não controle.
def test_falha_de_leitura_de_um_usuario_nao_toca_o_outro(user_id, monkeypatch):
    outro = user_id + 1
    db.ensure_user(outro)
    promote_to_pro(outro)
    _conexao(outro, "item-do-outro")
    _mock_pluggy(monkeypatch, item={**ITEM_SAUDAVEL, "id": "item-do-outro"},
                 contas=[_conta_pluggy("acc-outro")], txs=[_tx_pluggy("tx-outro")])
    assert ps.sync_pluggy_item("item-do-outro")["ok"] is True

    _com_falha(user_id, monkeypatch, "investimentos")
    _tique_de_saude()

    assert _linha("item-do-outro")["status_reason"] is None
    assert _ui_pela_rota(outro, "item-do-outro")["state"] == "updated"
    assert _ui_pela_rota(user_id)["state"] == "partial"


# ── Rodada 2: o job de saúde decide sobre o motivo que LEU ──────────────────
# `run_of_health_check` lista as linhas (com o motivo) e faz um `GET /items` por
# linha, sem lock. Um sync que termina nesse meio tempo muda o motivo; o job não
# pode gravar por cima uma decisão tomada sobre a leitura velha.

def _job_com_sync_no_meio(monkeypatch, no_meio) -> None:
    """Roda o job; no primeiro `GET /items` dele, `no_meio()` roda (outro processo)."""
    real_get = ps.get_pluggy_item
    feito: list = []

    def _get(item_id, api_key=None):
        if not feito:
            feito.append(True)
            no_meio()
        return real_get(item_id, api_key)

    monkeypatch.setattr(ps, "get_pluggy_item", _get)
    ps.run_of_health_check()


# (f) o sync completo limpou durante o job: o job não ressuscita a falha
@pytest.mark.parametrize("qual", ["contas", "investimentos"])
def test_job_nao_ressuscita_falha_limpa_por_sync_completo_no_meio(user_id, monkeypatch, qual):
    _com_falha(user_id, monkeypatch, qual)
    _envelhece()

    def sync_completo():
        assert ps.sync_pluggy_item("item-g1")["ok"] is True
        assert _linha()["status_reason"] is None

    _job_com_sync_no_meio(monkeypatch, sync_completo)

    assert _linha()["status_reason"] is None
    ui = _ui_pela_rota(user_id)
    assert (ui["state"], ui["label"]) == ("updated", "Atualizado")


# (g) um Atualizar com falha terminou durante o job: a falha nova sobrevive
@pytest.mark.parametrize("qual, motivo, estado", [
    ("contas", "read_failed", "error_recoverable"),
    ("investimentos", "investments_read_failed", "partial"),
])
def test_falha_gravada_durante_o_job_sobrevive(user_id, monkeypatch, qual, motivo, estado):
    promote_to_pro(user_id)
    _conexao(user_id)
    _mock_pluggy(monkeypatch, item=ITEM_SAUDAVEL, contas=[_conta_pluggy()], txs=[_tx_pluggy()])
    assert ps.sync_pluggy_item("item-g1")["ok"] is True
    assert _linha()["status_reason"] is None
    _envelhece()

    def atualizar_com_429():
        monkeypatch.setattr(ps, "list_pluggy_accounts" if qual == "contas"
                            else "list_pluggy_investments", _429)
        ps.sync_pluggy_user(user_id)          # o caminho do Atualizar (_sync_item_contido)
        assert _linha()["status_reason"] == motivo

    _job_com_sync_no_meio(monkeypatch, atualizar_com_429)

    assert _linha()["status_reason"] == motivo
    assert _ui_pela_rota(user_id)["state"] == estado


# positivo do CAS: sem corrida, o job ainda escreve (e limpa o que deve)
def test_job_sem_corrida_limpa_no_accounts_com_espelho_cheio(user_id, monkeypatch):
    promote_to_pro(user_id)
    conexao = _conexao(user_id)
    _mock_pluggy(monkeypatch, item=ITEM_SAUDAVEL, contas=[_conta_pluggy()], txs=[_tx_pluggy()])
    assert ps.sync_pluggy_item("item-g1")["ok"] is True
    with get_conn() as c:
        c.execute("update open_finance_connections set status_reason='no_accounts' where id=%s",
                  (conexao["id"],))
        c.commit()

    _tique_de_saude()

    assert _linha()["status_reason"] is None
    assert _ui_pela_rota(user_id)["state"] == "updated"


# (i) sync completo com contas terminou durante o job, com a linha listada
# em `no_accounts` e espelho vazio: o job não regrava "Sem dados" (B4 do Tester)
def test_job_nao_regrava_no_accounts_sobre_sync_com_contas_no_meio(user_id, monkeypatch):
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
        assert ps.sync_pluggy_item("item-g1")["ok"] is True
        assert _linha()["status_reason"] is None

    _job_com_sync_no_meio(monkeypatch, sync_com_contas)

    assert _linha()["status_reason"] is None
    assert _ui_pela_rota(user_id)["state"] == "updated"


# (h) leitura parcial de um sync que a reconexão desautorizou não vale
def test_parcial_anterior_a_autorizacao_atual_e_atualizando(user_id, monkeypatch):
    _com_falha(user_id, monkeypatch, "investimentos")
    # reconexão caindo entre a relectura e o carimbo: o sync grava motivo e
    # health, mas o `last_sync_at` fica ANTES do `reconnected_at`
    with get_conn() as c:
        c.execute("update open_finance_connections set reconnected_at = last_sync_at "
                  "+ interval '1 minute' where provider_item_id='item-g1'")
        c.commit()

    ui = _ui_pela_rota(user_id)
    assert (ui["state"], ui["label"], ui["detail"]) == (
        "updating", "Atualizando…", "Ainda não sincronizou")


# ── Rodada 4 (Codex, #692): Parcial da Pluggy E leitura parcial nossa ───────
# O ramo `partial` do `connection_ui_state` (produto atrasado na Pluggy ou
# `PARTIAL_SUCCESS`) devolvia só o `_stale_detail`, e o motivo nosso sumia.

ITEM_CARTAO_ATRASADO = {
    **ITEM_SAUDAVEL, "executionStatus": "PARTIAL_SUCCESS",
    "statusDetail": {**ITEM_SAUDAVEL["statusDetail"], "creditCards": {
        "isUpdated": False, "lastUpdatedAt": "2026-09-20T11:00:00.000Z", "warnings": []}},
}
CARTAO = "Cartão desatualizado desde 20/09"


# (j) as duas faltas aparecem, na tela e no que o toast lê
def test_parcial_da_pluggy_com_investimentos_falhando_nomeia_os_dois(user_id, monkeypatch):
    promote_to_pro(user_id)
    _conexao(user_id)
    _mock_pluggy(monkeypatch, item=ITEM_CARTAO_ATRASADO,
                 contas=[_conta_pluggy()], txs=[_tx_pluggy()])
    monkeypatch.setattr(ps, "list_pluggy_investments", _429)

    sync = _refresh(user_id, monkeypatch)

    esperado = ("partial", "Parcial", f"{CARTAO}; investimentos não vieram nesta atualização")
    item = next(i for i in sync["items"] if i["item_id"] == "item-g1")
    assert (item["state"], item["label"], item["detail"]) == esperado
    assert sync["ok"] is False
    ui = _ui_pela_rota(user_id)
    assert (ui["state"], ui["label"], ui["detail"]) == esperado


# (k) positivo: leitura completa, Parcial só da Pluggy — detalhe puro
def test_parcial_so_da_pluggy_mantem_o_detalhe_dela(user_id, monkeypatch):
    promote_to_pro(user_id)
    _conexao(user_id)
    _mock_pluggy(monkeypatch, item=ITEM_CARTAO_ATRASADO,
                 contas=[_conta_pluggy()], txs=[_tx_pluggy()])

    sync = _refresh(user_id, monkeypatch)

    item = next(i for i in sync["items"] if i["item_id"] == "item-g1")
    assert (item["state"], item["detail"]) == ("partial", CARTAO)
    assert _ui_pela_rota(user_id)["detail"] == CARTAO


# (l) Parcial da Pluggy e o Atualizar sem conseguir ler NADA (`read_failed`):
# "Atualizei o que deu" seria falso — é "Erro temporário", como em todo estado
# não terminal com `read_failed`.
def test_parcial_da_pluggy_com_contas_falhando_e_erro_temporario(user_id, monkeypatch):
    promote_to_pro(user_id)
    _conexao(user_id)
    _mock_pluggy(monkeypatch, item=ITEM_CARTAO_ATRASADO,
                 contas=[_conta_pluggy()], txs=[_tx_pluggy()])
    assert ps.sync_pluggy_item("item-g1")["ok"] is True
    assert _ui_pela_rota(user_id)["state"] == "partial"
    monkeypatch.setattr(ps, "list_pluggy_accounts", _429)

    sync = _refresh(user_id, monkeypatch)

    item = next(i for i in sync["items"] if i["item_id"] == "item-g1")
    assert (item["state"], item["reason"]) == ("error_recoverable", "read_failed")
    assert sync["ok"] is False
    assert _ui_pela_rota(user_id)["state"] == "error_recoverable"


# (m) o mesmo depois de reconectar (`sem_sync`): a reconexão zerou o motivo, e o
# `read_failed` foi gravado por um Atualizar posterior — vale. O run VELHO que
# falha depois da reconexão não grava (guarda de geração, PR-B1):
# `tests/test_of_marca_de_falha.py::test_c2_c3_lote_com_linha_velha_nao_desfaz_o_que_veio_depois`.
def test_read_failed_sobre_parcial_depois_de_reconectar_e_erro_temporario(user_id, monkeypatch):
    promote_to_pro(user_id)
    _conexao(user_id)
    _mock_pluggy(monkeypatch, item=ITEM_CARTAO_ATRASADO,
                 contas=[_conta_pluggy()], txs=[_tx_pluggy()])
    assert ps.sync_pluggy_item("item-g1")["ok"] is True
    db.save_pluggy_open_finance_item(
        user_id, {"id": "item-g1", "status": "UPDATED", "connector": {"id": 612, "name": "Nubank"}})
    ps.run_of_health_check()                    # health null → mede o item: cartão atrasado
    assert _linha()["health"]["stale_products"] == ["CREDIT"]
    monkeypatch.setattr(ps, "list_pluggy_accounts", _429)

    _refresh(user_id, monkeypatch)

    assert _linha()["status_reason"] == "read_failed"
    assert _ui_pela_rota(user_id)["state"] == "error_recoverable"



# (o) Parcial da Pluggy com ZERO espelhado (`no_accounts`): "Sem dados", como no
# verde — "Atualizei o que deu" seria falso.
def test_parcial_da_pluggy_sem_nada_espelhado_e_sem_dados(user_id, monkeypatch):
    promote_to_pro(user_id)
    _conexao(user_id)
    _mock_pluggy(monkeypatch, item=ITEM_CARTAO_ATRASADO, contas=[])

    sync = _refresh(user_id, monkeypatch)

    item = next(i for i in sync["items"] if i["item_id"] == "item-g1")
    assert (item["state"], item["reason"]) == ("no_accounts", "no_accounts")
    assert sync["ok"] is False
    assert _ui_pela_rota(user_id)["state"] == "no_accounts"


def _linha_parcial(**campos) -> dict:
    """Linha de `open_finance_connections` com o cartão atrasado na Pluggy."""
    from datetime import datetime, timezone
    base = {"status": "ACTIVE", "status_reason": "", "reconnected_at": None,
            "last_sync_at": datetime(2026, 9, 21, tzinfo=timezone.utc),
            "health": derive_item_health(ITEM_CARTAO_ATRASADO)}
    return base | campos


# (p) motivo que este arquivo não conhece: o default seguro do verde vale aqui
def test_motivo_desconhecido_no_parcial_e_erro_temporario():
    assert connection_ui_state(_linha_parcial(status_reason="motivo_novo"))["state"] == (
        "error_recoverable")
    assert connection_ui_state(_linha_parcial())["state"] == "partial", "positivo"


# (n) análogo do (h) no `partial`: leitura parcial de antes da autorização atual
# não é acrescentada ao detalhe da Pluggy
def test_parcial_da_pluggy_com_motivo_anterior_a_reconexao_nao_cita_investimentos():
    from datetime import timedelta
    linha = _linha_parcial(status_reason="investments_read_failed")
    linha["reconnected_at"] = linha["last_sync_at"] + timedelta(minutes=1)
    ui = connection_ui_state(linha)
    assert (ui["state"], ui["detail"]) == ("partial", CARTAO)


# (q) INVESTMENTS já atrasado na Pluggy: o detalhe dela já os nomeia; repetir
# "investimentos não vieram" seria redundante (e contraditório com warning)
def test_investimentos_atrasados_na_pluggy_e_falhando_nao_repete(user_id, monkeypatch):
    item = {**ITEM_SAUDAVEL, "executionStatus": "PARTIAL_SUCCESS",
            "statusDetail": {**ITEM_SAUDAVEL["statusDetail"], "investments": {
                "isUpdated": False, "lastUpdatedAt": "2026-09-20T11:00:00.000Z", "warnings": []}}}
    promote_to_pro(user_id)
    _conexao(user_id)
    _mock_pluggy(monkeypatch, item=item, contas=[_conta_pluggy()], txs=[_tx_pluggy()])
    monkeypatch.setattr(ps, "list_pluggy_investments", _429)

    _refresh(user_id, monkeypatch)

    assert _linha()["status_reason"] == "investments_read_failed"
    ui = _ui_pela_rota(user_id)
    assert (ui["state"], ui["detail"]) == ("partial", "Investimentos desatualizado desde 20/09")
