"""Onda 5, PR-B1: a marca de falha de um sync (`read_failed`) e quem ela troca.

Tabela das células: `docs/open_finance_estados.md` §2.1. Os números nos nomes
dos testes (`c6`, `c9b`...) são as células de lá. Contrato: o mesmo doc, §1,
itens 2 e 8.

Duas gravações, cada uma com a autoridade da sua espécie:
  F  falha SEM observação (`marcar_leitura_falhou`): `read_failed` com `status`
     intocado, só se o par `(reconnected_at, last_sync_at)` é o lido no começo do
     run (`geracao_vista`) e o motivo ATUAL está em `MOTIVOS_QUE_A_FALHA_SUBSTITUI`
     (CAS por espécie). Nunca troca veredito (`no_accounts`, `item_missing`).
  O  a foto do `GET /items` do próprio run, quando ele falha DEPOIS dela
     (`sync_pluggy_item`): o par do resolvedor com `has_data=False`, só se ninguém
     observou o item desde o começo do run (`observacao_vista`) e, com o item vivo
     (`ACTIVE`), o motivo está em `MOTIVOS_QUE_A_FOTO_VIVA_SUBSTITUI` (a da F mais
     `item_missing`). Item em erro (`ERROR`) grava sem lista.

Onde o run falha: G = no `GET /items` (sem foto, só a F; `_bg_que_falha_depois_de`
troca o `sync_pluggy_item` inteiro por um stub); L = depois da foto (o
`sync_pluggy_item` REAL, com `/accounts` rodando o evento e levantando; sem isso
a O nunca roda). Sem `relogio_fixo`: ele congela o `datetime` de `pluggy_sync`, e
dois 404 sairiam com o mesmo `observed_at`.

CONTROLES (medidos em 2026-09-30; remeça se mexer no código):
  negativo, lista da F trocada pelo CAS por valor (`status_reason_visto`): c9,
      c9b e c9c vermelhos;
  negativo, `no_accounts` na lista: c6 e c18/c19 vermelhos;
  negativo, sem a O: c8 e c22 vermelhos;
  negativo, O sem `observacao_vista`: c20 vermelho; na O com `status=ERROR`
      (sem lista, só o `observed_at` a protege): c22_error_depois vermelho;
  negativo, a lista aplicada também à O com `status=ERROR`: c22 vermelho;
  negativo, sem o par na F (`geracao_vista` de `marcar_leitura_falhou`): c2 e c3
      vermelhos;
  negativo, sem o par na O: c3_L vermelho (1ª conexão, `health` NULL antes e
      depois da reconexão, então o `observed_at` não distingue);
  positivos (o caminho legítimo continua gravando): c1 (em
      `test_of_coleta_sem_fim.py`), c10, c12, c17, c26, c27 e c29.
"""

from __future__ import annotations

import pytest

import core.services.pluggy_sync as ps
import db
import frontend.routes.open_finance as of_routes
from core.services.pluggy import PluggyApiError
from db.connection import get_conn
from test_of_coleta_sem_fim import (
    ERRO, ITEM, _500, _conecta, _sql, _sync_de_fundo, _tique_de_saude, _ui_das_duas)
from test_of_connection_state import ITEM_SAUDAVEL, _conta_pluggy, _linha, _mock_pluggy, _tx_pluggy
from test_of_item_ownership import eventos, sem_indice_unico  # noqa: F401
from test_of_leitura_incompleta import _ui_pela_rota

_404 = PluggyApiError("not found", status_code=404)
SEM_DADOS = ("no_accounts", "Sem dados")
PERDIDA = ("item_missing", "Conexão perdida")


def _tela(uid: int) -> tuple[str, str, str]:
    ui = _ui_das_duas(uid)
    return ui["state"], ui["label"], ui["detail"]


def _bg_que_falha_depois_de(monkeypatch, no_meio) -> None:
    """G: o sync de fundo falha nas 3 tentativas sem chegar à Pluggy; `no_meio`
    roda antes da 3ª. Só exercita a F."""
    tentativas = {"n": 0}

    def sync_a(item_id, **_kw):
        tentativas["n"] += 1
        if tentativas["n"] == of_routes._SYNC_MAX_ATTEMPTS:
            no_meio()
        raise PluggyApiError("flap", status_code=503)

    monkeypatch.setattr(of_routes, "sync_pluggy_item", sync_a)
    _sync_de_fundo(monkeypatch)
    assert tentativas["n"] == of_routes._SYNC_MAX_ATTEMPTS


def _lote_que_falha_em_l(monkeypatch, uid: int, no_meio=lambda: None) -> None:
    """L: o lote do Atualizar (`_sync_item_contido`) com o `sync_pluggy_item`
    REAL. O `GET /items` responde (foto 1, pelo mock já instalado) e `/accounts`
    roda `no_meio()` e levanta 500. Chamadas a `/accounts` DENTRO do `no_meio`
    (um sync B) vão ao mock anterior."""
    linha = next(c for c in db.get_open_finance_snapshot(uid)["connections"]
                 if c["provider_item_id"] == ITEM)
    anterior = ps.list_pluggy_accounts
    dentro = {"sim": False}

    def contas(item_id, api_key=None):
        if dentro["sim"]:
            return anterior(item_id, api_key)
        dentro["sim"] = True
        try:
            no_meio()
        finally:
            dentro["sim"] = False
        raise PluggyApiError("boom", status_code=500)

    monkeypatch.setattr(ps, "list_pluggy_accounts", contas)
    assert ps._sync_item_contido(linha, uid)["reason"] == "read_failed"


def _run_que_falha(monkeypatch, uid: int, onde: str, no_meio=lambda: None) -> None:
    if onde == "G":
        _bg_que_falha_depois_de(monkeypatch, no_meio)
    else:
        _lote_que_falha_em_l(monkeypatch, uid, no_meio)


def _no_accounts(uid: int, monkeypatch) -> None:
    _conecta(uid, "UPDATED")
    _mock_pluggy(monkeypatch, item=ITEM_SAUDAVEL)
    assert ps.sync_pluggy_item(ITEM)["reason"] == "no_accounts"
    assert _tela(uid)[:2] == SEM_DADOS


def _item_missing_com_espelho(uid: int, monkeypatch) -> None:
    """Espelho cheio e um 404 depois: "Conexão perdida"."""
    _conecta(uid, "UPDATED")
    _mock_pluggy(monkeypatch, item=ITEM_SAUDAVEL, contas=[_conta_pluggy()], txs=[_tx_pluggy()])
    assert ps.sync_pluggy_item(ITEM)["ok"]
    _mock_pluggy(monkeypatch, item=_404, contas=[_conta_pluggy()], txs=[_tx_pluggy()])
    assert ps.sync_pluggy_item(ITEM)["reason"] == "item_missing"
    assert _tela(uid)[:2] == PERDIDA


# ── c2, c3, c14, c28: a marca só vale para o run que a produziu ──────────────

def test_c28_falha_de_item_com_dois_donos_nao_marca_ninguem(
        user_id, monkeypatch, eventos, sem_indice_unico):
    """Isolamento: o sync de fundo só tem o `item_id`. Com duas conexões para o
    mesmo item, a marca não escolhe dono (o `AmbiguousItemError` do sync e o da
    marca são o mesmo), e a O também não roda: a leitura do próprio sync levanta
    antes do `GET /items`. Com UM dono e o banco fora só na captura do sync de
    fundo, a O roda e grava na linha do dono (a F é que fica sem linha)."""
    outro = user_id + 1
    db.ensure_user(outro)
    try:
        for uid in (user_id, outro):
            db.save_pluggy_open_finance_item(
                uid, {"id": "item-2donos", "status": "UPDATING",
                      "connector": {"id": 612, "name": "Nubank"}})
        _mock_pluggy(monkeypatch, item=ITEM_SAUDAVEL)

        _sync_de_fundo(monkeypatch, "item-2donos")

        linhas = db.get_connections_by_item_id("item-2donos")
        assert [(r["status_reason"], r["health"]) for r in linhas] == [(None, None)] * 2
    finally:
        db.disconnect_open_finance_connection(outro)
        with get_conn() as c:
            c.execute("delete from users where id=%s", (outro,))
            c.commit()


def test_c2_run_velho_que_falha_nao_desfaz_sync_novo_bom(user_id, monkeypatch, eventos):
    """Achado 1 do Tester, invertido: sem o par, a tela saía de "Atualizado" para
    "Erro temporário" até o próximo sync completo."""
    _conecta(user_id, "UPDATED")
    _mock_pluggy(monkeypatch, item=ITEM_SAUDAVEL, contas=[_conta_pluggy()], txs=[_tx_pluggy()])

    def sync_b_bom():
        assert ps.sync_pluggy_item(ITEM)["ok"]
        assert _ui_pela_rota(user_id)["state"] == "updated"

    _bg_que_falha_depois_de(monkeypatch, sync_b_bom)

    assert _linha()["status_reason"] is None
    assert _ui_das_duas(user_id)["state"] == "updated"


def test_c3_reconexao_no_meio_nao_herda_a_falha_do_run_velho(user_id, monkeypatch, eventos):
    _conecta(user_id)

    def reconecta():
        db.save_pluggy_open_finance_item(
            user_id, {"id": ITEM, "status": "UPDATED", "connector": {"id": 612, "name": "Nubank"}})

    _bg_que_falha_depois_de(monkeypatch, reconecta)

    linha = _linha()
    assert linha["reconnected_at"] is not None and linha["status_reason"] is None
    ui = _ui_das_duas(user_id)
    assert (ui["state"], ui["detail"]) == ("updating", "Ainda não sincronizou")


def test_c3_L_reconexao_no_meio_nao_recebe_a_foto_do_run_velho(user_id, monkeypatch, eventos):
    """1ª conexão (`health` NULL), reconexão no meio e falha em `/accounts`: a
    reconexão zera o `health`, então o `observed_at` é NULL antes e depois e só o
    par recusa a foto da autorização antiga."""
    _conecta(user_id, "UPDATED")
    _mock_pluggy(monkeypatch, item=ITEM_SAUDAVEL)

    _lote_que_falha_em_l(monkeypatch, user_id, lambda: db.save_pluggy_open_finance_item(
        user_id, {"id": ITEM, "status": "UPDATED", "connector": {"id": 612, "name": "Nubank"}}))

    linha = _linha()
    assert (linha["status_reason"], linha["health"]) == (None, None)
    assert _ui_das_duas(user_id)["state"] == "updating"


def test_c14_item_readotado_no_meio_nao_recebe_a_marca(user_id, monkeypatch, eventos):
    """A marca vai pelo `id` que o run leu ao começar, não pelo `item_id` relido:
    conexão removida e o mesmo item gravado de novo (outra linha) no meio."""
    antiga = _conecta(user_id)

    def readota():
        db.disconnect_open_finance_connection(user_id)
        db.save_pluggy_open_finance_item(
            user_id, {"id": ITEM, "status": "UPDATING", "connector": {"id": 612, "name": "Nubank"}})

    _bg_que_falha_depois_de(monkeypatch, readota)

    nova = _linha()
    assert nova["id"] != antiga["id"] and nova["status_reason"] is None


@pytest.mark.parametrize("terminal", ["PAUSED", "DELETED"])
def test_c15_terminal_no_meio_nao_recebe_a_marca(user_id, monkeypatch, eventos, terminal):
    _conecta(user_id, "UPDATED")

    _bg_que_falha_depois_de(monkeypatch, lambda: _sql(
        "update open_finance_connections set status=%s where provider_item_id=%s", terminal, ITEM))

    assert (_linha()["status"], _linha()["status_reason"]) == (terminal, None)


@pytest.mark.parametrize("no_meio", ["sync_bom", "reconexao"])
def test_c2_c3_lote_com_linha_velha_nao_desfaz_o_que_veio_depois(user_id, monkeypatch, no_meio):
    """O outro chamador: `_sync_item_contido` recebe a linha do snapshot lido no
    começo do lote. Um sync bom ou uma reconexão depois disso não são desfeitos
    pela falha (célula 3 no lote: `test_of_leitura_incompleta.py`, caso (m))."""
    _conecta(user_id, "UPDATED")
    linha_do_lote = next(c for c in db.get_open_finance_snapshot(user_id)["connections"]
                         if c["provider_item_id"] == ITEM)
    _mock_pluggy(monkeypatch, item=ITEM_SAUDAVEL, contas=[_conta_pluggy()], txs=[_tx_pluggy()])
    if no_meio == "sync_bom":
        assert ps.sync_pluggy_item(ITEM)["ok"]
        esperado = "updated"
    else:
        db.save_pluggy_open_finance_item(
            user_id, {"id": ITEM, "status": "UPDATED", "connector": {"id": 612, "name": "Nubank"}})
        esperado = "updating"
    monkeypatch.setattr(ps, "get_pluggy_item", _500)

    assert ps._sync_item_contido(linha_do_lote, user_id)["reason"] == "read_failed"

    assert _linha()["status_reason"] is None
    assert _ui_das_duas(user_id)["state"] == esperado


def test_c23_sync_parcial_no_meio_fica_parcial(user_id, monkeypatch, eventos):
    _conecta(user_id, "UPDATED")
    _mock_pluggy(monkeypatch, item=ITEM_SAUDAVEL, contas=[_conta_pluggy()], txs=[_tx_pluggy()])
    monkeypatch.setattr(ps, "list_pluggy_investments", _500)

    _bg_que_falha_depois_de(monkeypatch, lambda: ps.sync_pluggy_item(ITEM))

    assert _linha()["status_reason"] == "investments_read_failed"
    assert _ui_das_duas(user_id)["state"] == "partial"


# ── c4, c5, c6, c11, c20: veredito mais novo que o run vence ─────────────────

@pytest.mark.parametrize("remoto, estado", [
    (ITEM_SAUDAVEL, "no_accounts"),   # c4: leu zero contas
    (_404, "item_missing"),           # c5: viu o item sumir
], ids=["c4_no_accounts", "c5_item_missing"])
def test_c4_c5_run_velho_nao_apaga_veredito_mais_novo(
        user_id, monkeypatch, eventos, remoto, estado):
    """O sync B do meio termina SEM sucesso (`ok=False`), então o par não muda;
    quem protege o veredito é a lista da F (ele não está nela)."""
    _conecta(user_id, "UPDATED")
    _mock_pluggy(monkeypatch, item=remoto)

    def sync_b_sem_sucesso():
        assert ps.sync_pluggy_item(ITEM)["reason"] == estado

    _bg_que_falha_depois_de(monkeypatch, sync_b_sem_sucesso)

    assert _linha()["status_reason"] == estado
    assert _ui_das_duas(user_id)["state"] == estado


@pytest.mark.parametrize("onde", ["G", "L"])
def test_c6_no_accounts_observado_de_novo_no_meio_fica_sem_dados(user_id, monkeypatch, eventos, onde):
    """M1 do Manager (ABA): o CAS por valor via o mesmo `no_accounts` antes e
    depois e deixava a falha velha apagar a observação nova."""
    _no_accounts(user_id, monkeypatch)

    def sync_b_le_zero_de_novo():
        assert ps.sync_pluggy_item(ITEM)["reason"] == "no_accounts"

    _run_que_falha(monkeypatch, user_id, onde, sync_b_le_zero_de_novo)

    assert _linha()["status_reason"] == "no_accounts"
    assert _tela(user_id)[:2] == SEM_DADOS


@pytest.mark.parametrize("onde", ["G", "L"])
def test_c11_job_ve_404_no_meio_fica_conexao_perdida(user_id, monkeypatch, eventos, onde):
    _conecta(user_id, "UPDATED")
    _mock_pluggy(monkeypatch, item=ITEM_SAUDAVEL)

    _run_que_falha(monkeypatch, user_id, onde, lambda: _tique_de_saude(monkeypatch, _404))

    assert (_linha()["status"], _linha()["status_reason"]) == ("ERROR", "item_missing")
    assert _tela(user_id)[:2] == PERDIDA


def test_c20_job_ve_404_de_novo_depois_da_foto_fica_conexao_perdida(user_id, monkeypatch, eventos):
    """A observação mais nova vence a foto do run: o job vê o 404 DEPOIS da foto 1
    (item vivo). Sem `observacao_vista`, a O gravava a foto mais velha por cima."""
    _item_missing_com_espelho(user_id, monkeypatch)
    _mock_pluggy(monkeypatch, item=ITEM_SAUDAVEL)
    antes = _linha()["health"]["observed_at"]

    _lote_que_falha_em_l(monkeypatch, user_id, lambda: _tique_de_saude(monkeypatch, _404))

    linha = _linha()
    assert (linha["status"], linha["status_reason"]) == ("ERROR", "item_missing")
    assert linha["health"]["item_status"] == "MISSING" and linha["health"]["observed_at"] != antes
    assert _tela(user_id)[:2] == PERDIDA


# ── c7, c8: `item_missing` e a foto do próprio run ───────────────────────────

def test_c7_falha_no_get_nao_troca_item_missing(user_id, monkeypatch, eventos):
    """Sem foto não há observação do item vivo (contrato §1 item 2): a F não
    troca "Refaça a conexão" por "Tentaremos de novo"."""
    _conecta(user_id, "UPDATED")
    _mock_pluggy(monkeypatch, item=_404)
    assert ps.sync_pluggy_item(ITEM)["reason"] == "item_missing"

    _bg_que_falha_depois_de(monkeypatch, lambda: None)

    assert (_linha()["status"], _linha()["status_reason"]) == ("ERROR", "item_missing")
    assert _ui_das_duas(user_id)["state"] == "item_missing"


@pytest.mark.parametrize("via", ["lote", "bg"])
def test_c8_run_que_ve_o_item_vivo_e_falha_depois_troca_para_erro_temporario(
        user_id, monkeypatch, eventos, via):
    """DECISÃO 2 = A: o `GET /items` do próprio run responde 200 e `/accounts`
    falha. A foto prova o item vivo; o usuário não refaz uma conexão que existe."""
    _item_missing_com_espelho(user_id, monkeypatch)
    _mock_pluggy(monkeypatch, item=ITEM_SAUDAVEL)

    if via == "lote":
        _lote_que_falha_em_l(monkeypatch, user_id)
    else:
        monkeypatch.setattr(ps, "list_pluggy_accounts", _500)
        _sync_de_fundo(monkeypatch)

    linha = _linha()
    assert (linha["status"], linha["status_reason"], linha["health"]["item_status"]) == (
        "ACTIVE", "read_failed", "UPDATED")
    assert _tela(user_id) == ERRO


# ── c9, c9b, c9c: o job LIMPOU o motivo no meio (item vivo) ──────────────────

def test_c9_job_tira_item_missing_no_meio_e_a_falha_grava(user_id, monkeypatch, eventos):
    """M2 do Manager: o CAS por valor recusava e a tela ficava verde sobre uma
    leitura que falhou."""
    _item_missing_com_espelho(user_id, monkeypatch)

    _bg_que_falha_depois_de(monkeypatch, lambda: _tique_de_saude(monkeypatch, ITEM_SAUDAVEL))

    assert _linha()["status_reason"] == "read_failed"
    assert _tela(user_id) == ERRO


def test_c9b_job_tira_item_missing_depois_da_foto_e_a_falha_grava(user_id, monkeypatch, eventos):
    _item_missing_com_espelho(user_id, monkeypatch)
    _mock_pluggy(monkeypatch, item=ITEM_SAUDAVEL)

    _lote_que_falha_em_l(monkeypatch, user_id, lambda: _tique_de_saude(monkeypatch, ITEM_SAUDAVEL))

    assert _linha()["status_reason"] == "read_failed"
    assert _tela(user_id) == ERRO


def test_c9c_job_tira_no_accounts_com_espelho_cheio_e_a_falha_grava(user_id, monkeypatch, eventos):
    """`no_accounts` com espelho cheio: um sync bom e depois um que leu zero. O job
    o tira (`has_data`); a falha do run que começou antes grava."""
    _conecta(user_id, "UPDATED")
    _mock_pluggy(monkeypatch, item=ITEM_SAUDAVEL, contas=[_conta_pluggy()], txs=[_tx_pluggy()])
    assert ps.sync_pluggy_item(ITEM)["ok"]
    _mock_pluggy(monkeypatch, item=ITEM_SAUDAVEL)
    assert ps.sync_pluggy_item(ITEM)["reason"] == "no_accounts"

    _bg_que_falha_depois_de(monkeypatch, lambda: _tique_de_saude(monkeypatch, ITEM_SAUDAVEL))

    assert _linha()["status_reason"] == "read_failed"
    assert _tela(user_id) == ERRO


# ── c18, c19: DECISÃO 1 = A, `no_accounts` sem evento fica "Sem dados" ───────

@pytest.mark.parametrize("onde", ["L", "G"], ids=["c18_L", "c19_G"])
def test_c18_c19_falha_sobre_no_accounts_mantem_sem_dados(user_id, monkeypatch, eventos, onde):
    """A falha de hoje não sabe nada sobre as contas; o veredito de ontem é a
    última coisa que se sabe. Em L a foto também não é gravada."""
    _no_accounts(user_id, monkeypatch)
    antes = _linha()["health"]

    _run_que_falha(monkeypatch, user_id, onde)

    linha = _linha()
    assert (linha["status_reason"], linha["health"]) == ("no_accounts", antes)
    assert _tela(user_id)[:2] == SEM_DADOS


# ── c22: a foto do run diz que o item precisa do usuário ─────────────────────

def test_c22_foto_login_error_no_run_que_falha_manda_reautorizar(user_id, monkeypatch, eventos):
    """Sem a O a linha ficava com a foto de ontem (item saudável) e "Erro
    temporário": a instrução errada."""
    _no_accounts(user_id, monkeypatch)
    _mock_pluggy(monkeypatch, item={**ITEM_SAUDAVEL, "status": "LOGIN_ERROR",
                                    "executionStatus": "ERROR"})

    _lote_que_falha_em_l(monkeypatch, user_id)

    linha = _linha()
    assert (linha["status"], linha["health"]["item_status"]) == ("ERROR", "LOGIN_ERROR")
    assert _tela(user_id) == ("needs_user_action", "Ação necessária", "Reautorize o banco")


def test_c22_error_depois_foto_login_error_nao_apaga_no_accounts_mais_novo(
        user_id, monkeypatch, eventos):
    """A O com `status=ERROR` grava sem lista; quem a recusa é o `observed_at`.
    A foto 1 diz `LOGIN_ERROR`, e um sync B, com o item já saudável, lê zero
    contas no meio: o veredito mais novo fica."""
    _conecta(user_id, "UPDATED")
    _mock_pluggy(monkeypatch, item={**ITEM_SAUDAVEL, "status": "LOGIN_ERROR",
                                    "executionStatus": "ERROR"})

    def sync_b_le_zero_com_item_saudavel():
        monkeypatch.setattr(ps, "get_pluggy_item", lambda i, k=None: ITEM_SAUDAVEL)
        assert ps.sync_pluggy_item(ITEM)["reason"] == "no_accounts"

    _lote_que_falha_em_l(monkeypatch, user_id, sync_b_le_zero_com_item_saudavel)

    linha = _linha()
    assert (linha["status"], linha["status_reason"], linha["health"]["item_status"]) == (
        "ACTIVE", "no_accounts", "UPDATED")
    assert _tela(user_id)[:2] == SEM_DADOS


# ── positivos: sem evento que o contradiga, a falha grava ────────────────────

@pytest.mark.parametrize("onde", ["G", "L"])
def test_c10_job_mantem_sem_motivo_no_meio_e_a_falha_grava(user_id, monkeypatch, eventos, onde):
    _conecta(user_id, "UPDATED")
    _mock_pluggy(monkeypatch, item=ITEM_SAUDAVEL)

    _run_que_falha(monkeypatch, user_id, onde, lambda: _tique_de_saude(monkeypatch, ITEM_SAUDAVEL))

    assert _linha()["status_reason"] == "read_failed"
    assert _tela(user_id) == ERRO


@pytest.mark.parametrize("onde", ["G", "L"])
@pytest.mark.parametrize("motivo", ["read_failed", "investments_read_failed"], ids=["c12", "c17"])
def test_c12_c17_falha_de_leitura_anterior_vira_read_failed(user_id, monkeypatch, eventos, onde, motivo):
    _conecta(user_id, "UPDATED")
    _mock_pluggy(monkeypatch, item=ITEM_SAUDAVEL)
    _sql("update open_finance_connections set status_reason=%s where provider_item_id=%s",
         motivo, ITEM)

    _run_que_falha(monkeypatch, user_id, onde)

    assert _linha()["status_reason"] == "read_failed"
    assert _tela(user_id) == ERRO


@pytest.mark.parametrize("onde, status", [("G", "ERROR"), ("L", "ACTIVE")], ids=["c13_G", "c13b_L"])
def test_c13_webhook_item_error_no_meio(user_id, monkeypatch, eventos, onde, status):
    """c13b (registrado em `decisoes.md`): em L a foto, mais velha que a pista do
    webhook, troca `ERROR` por `ACTIVE`. A tela é a mesma."""
    _conecta(user_id, "UPDATED")
    _mock_pluggy(monkeypatch, item=ITEM_SAUDAVEL)

    _run_que_falha(monkeypatch, user_id, onde,
                   lambda: db.update_pluggy_open_finance_item_status(ITEM, "ERROR"))

    assert (_linha()["status"], _linha()["status_reason"]) == (status, "read_failed")
    assert _tela(user_id) == ERRO


@pytest.mark.parametrize("onde", ["G", "L"])
def test_c16_webhook_item_created_no_meio(user_id, monkeypatch, eventos, onde):
    _conecta(user_id, "UPDATED")
    _mock_pluggy(monkeypatch, item=ITEM_SAUDAVEL)

    _run_que_falha(monkeypatch, user_id, onde,
                   lambda: db.update_pluggy_open_finance_item_status(ITEM, "UPDATING"))

    assert _linha()["status_reason"] == "read_failed"
    assert _tela(user_id) == ERRO


def test_c26_falha_propria_com_last_attempt_carimbado_ainda_marca(user_id, monkeypatch, eventos):
    """O próprio run carimba `last_attempt_at` (`mark_sync_attempt`) antes de
    falhar: isso não mexe no par nem no `observed_at`."""
    _conecta(user_id)
    _mock_pluggy(monkeypatch, item=ITEM_SAUDAVEL, contas=[_conta_pluggy()])
    monkeypatch.setattr(ps, "list_pluggy_transactions", _500)

    _sync_de_fundo(monkeypatch)

    linha = _linha()
    assert linha["last_attempt_at"] is not None, "o run devia ter carimbado a tentativa"
    assert linha["status_reason"] == "read_failed"


def test_c27_lote_sem_corrida_marca_pela_linha_do_snapshot(user_id, monkeypatch):
    """M3 do Manager: a linha do snapshot (tipos reais) casa com o CAS."""
    _conecta(user_id, "UPDATED")
    _mock_pluggy(monkeypatch, item=ITEM_SAUDAVEL)

    _lote_que_falha_em_l(monkeypatch, user_id)

    assert _linha()["status_reason"] == "read_failed"
    assert _tela(user_id) == ERRO


def test_c29_bg_falha_em_l_e_depois_em_g(user_id, monkeypatch, eventos):
    """A O da 1ª tentativa grava a foto; a F final encontra `read_failed` e
    regrava o mesmo valor."""
    _conecta(user_id)
    fotos = {"n": 0}

    def get_item(item_id, api_key=None):
        fotos["n"] += 1
        if fotos["n"] > 1:
            raise PluggyApiError("flap", status_code=503)
        return ITEM_SAUDAVEL

    _mock_pluggy(monkeypatch, item=ITEM_SAUDAVEL)
    monkeypatch.setattr(ps, "get_pluggy_item", get_item)
    monkeypatch.setattr(ps, "list_pluggy_accounts", _500)

    _sync_de_fundo(monkeypatch)

    linha = _linha()
    assert fotos["n"] == of_routes._SYNC_MAX_ATTEMPTS
    assert (linha["status"], linha["status_reason"], linha["health"]["item_status"]) == (
        "ACTIVE", "read_failed", "UPDATED")
    assert _tela(user_id) == ERRO
