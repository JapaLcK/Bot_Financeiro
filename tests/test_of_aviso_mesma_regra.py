"""Onda 5, PR-D: o aviso "reconecte" é a TELA filtrada, e a instrução de
dispositivo/QR vence nos dois ramos (D4, D5; `docs/open_finance_estados.md`).

Caminho real: o estado nasce pela rota do webhook (pista com a releitura
falhando), pelo `sync_pluggy_item`, pelo job de saúde ou pelo upsert; a tela sai
pela ROTA `GET /open-finance/{uid}` (e o toast, pelo `_ui_das_duas`); o aviso sai
por `run_reconnect_notifications` real, com só as bordas trocadas e o
`send_template` espião. O prazo é contra o `now()` do Postgres
(`_envelhece_autorizacao`), nunca contra o relógio do Python.

CONTROLES (medidos em 2026-10-07; remeça se mexer no código):
  Negativo 1 (D4): devolver a `list_connections_needing_reconnect` o filtro por
      `status` → a_*, b_*, c_*, j_*, k e também f_*, h_*_61, i_*_61 (o filtro
      velho calava device vencido) vermelhos; d_*, e_*, g_*, *_55 e l verdes.
  Negativo 2 (D5, ramo com `health`): ignorar `device_na_janela` no ramo com
      `health` de `connection_ui_state` → f_*, h_*_61 e k vermelhos; g_*, h_*_55 verdes.
  Negativo 3 (ramo sem `health`): idem no ramo sem `health` → i_*_61 e k vermelhos.
  Negativo 4: tirar o `pop("device_na_janela")` do snapshot → k vermelho (chaves)
      e `test_of_connection_state.py::test_a_resposta_HTTP_nao_ganhou_chave_nova…`.
  Positivos: d, e (avisam), g, h_*_55, i_*_55 (device válido cala e instrui).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import core.services.pluggy_sync as ps
import db
import frontend.finance_bot_websocket_custom as dashboard
from _billing_grants_helpers import garantir_system_event_logs
from conftest import promote_to_pro
from core.services import open_finance_proactive as ofp
from core.services.pluggy import PluggyApiError
from core.services.pluggy_health import _DETALHE_ITEM_EM_ERRO, avisa_reconectar
from test_of_connection_state import (
    CHAVES_DA_CONEXAO, ITEM_CAIXA_QR, ITEM_SAUDAVEL, _auth, _conta_pluggy,
    _envelhece_autorizacao, _mock_pluggy, _tx_pluggy)
from test_of_webhook_observacao import (  # noqa: F401  (`_ambiente` é autouse)
    _ambiente, _ativa, _erro, _posta, _Remoto, _ui_das_duas)

AUTORIZE = "Autorize o acesso no app do banco"
REAUTORIZE = "Reautorize o banco"
TEMPORARIO = "Tentaremos de novo automaticamente"


@pytest.fixture(autouse=True)
def _event_logs():
    garantir_system_event_logs()


# ── como cada estado nasce (caminho de produção) ─────────────────────────────

def _pista(motivo=None):
    """E3 com a releitura falhando: a pista grava `status='ERROR'` (R2)."""
    def cria(uid, mp, item):
        _ativa(uid, mp, motivo, item=item)
        _Remoto(mp, PluggyApiError("boom", status_code=503))
        assert _posta([_erro(item)]) == [200]
    return cria


def _sync(remoto):
    """Sync real com o item da Pluggy em `remoto` (dict ou exceção)."""
    def cria(uid, mp, item):
        _ativa(uid, mp, item=item)
        alvo = remoto if isinstance(remoto, Exception) else {**remoto, "id": item}
        _mock_pluggy(mp, item=alvo, contas=[_conta_pluggy()], txs=[_tx_pluggy()])
        ps.sync_pluggy_item(item)
    return cria


def _upsert(remoto, minutos=None, tique=False):
    """`save_pluggy_open_finance_item`; `minutos` recua a autorização no banco;
    `tique` = o job de saúde mede o item (grava `health`)."""
    def cria(uid, mp, item):
        promote_to_pro(uid)
        payload = {**remoto, "id": item}
        conn = db.save_pluggy_open_finance_item(uid, payload)
        if minutos is not None:
            _envelhece_autorizacao(conn["id"], minutos)
        if tique:
            def _get(i, _k=None):
                if i != item:   # só este item responde: o job não mede as outras linhas
                    raise PluggyApiError("outro", status_code=503)
                return payload
            mp.setattr(ps, "create_pluggy_api_key", lambda: "k")
            mp.setattr(ps, "get_pluggy_item", _get)
            ps.run_of_health_check()
        assert (db.get_connections_by_item_id(item)[0]["health"] is not None) is tique
    return cria


INTER_QR = {"status": "WAITING_USER_ACTION", "executionStatus": "WAITING_USER_ACTION",
            "clientUserId": "1", "connector": {"id": 77, "name": "Inter"}}
INTER_QR_LOCAL = {"status": "WAITING_USER_ACTION", "clientUserId": "1",
                  "connector": {"id": 77, "name": "Inter"}}
SO_INVALID = {"executionStatus": "INVALID_CREDENTIALS", "clientUserId": "1",
              "connector": {"id": 612, "name": "Nubank"}}

# id → (como nasce, estado da tela, detalhe da tela, banco avisado ou None)
CASOS = {
    "a_pista": (_pista(), "error_recoverable", TEMPORARIO, None),
    "b_pista_no_accounts": (_pista("no_accounts"), "no_accounts", None, None),
    "b_pista_read_failed": (_pista("read_failed"), "error_recoverable", TEMPORARIO, None),
    "c_item_error": (_sync({**ITEM_SAUDAVEL, "status": "ERROR", "executionStatus": "ERROR"}),
                     "error_recoverable", _DETALHE_ITEM_EM_ERRO, None),
    "d_login_error": (_sync({**ITEM_SAUDAVEL, "status": "LOGIN_ERROR",
                             "executionStatus": "LOGIN_ERROR"}),
                      "needs_user_action", REAUTORIZE, "Nubank"),
    "e_item_missing": (_sync(PluggyApiError("nf", status_code=404)),
                       "item_missing", None, "Nubank"),
    "f_caixa_health_61": (_upsert(ITEM_CAIXA_QR, 61, tique=True),
                          "needs_user_action", REAUTORIZE, "Caixa"),
    "g_caixa_health_55": (_upsert(ITEM_CAIXA_QR, 55, tique=True),
                          "needs_user_action", AUTORIZE, None),
    "h_wua_health_61": (_upsert(INTER_QR, 61, tique=True), "needs_user_action", REAUTORIZE, "Inter"),
    "h_wua_health_55": (_upsert(INTER_QR, 55, tique=True), "needs_user_action", AUTORIZE, None),
    "i_wua_sem_health_61": (_upsert(INTER_QR_LOCAL, 61), "needs_user_action", REAUTORIZE, "Inter"),
    "i_wua_sem_health_55": (_upsert(INTER_QR_LOCAL, 55), "needs_user_action", AUTORIZE, None),
    "j_invalid_credentials": (_upsert(SO_INVALID), "needs_user_action", REAUTORIZE, "Nubank"),
}


# ── leitura: a rota e o aviso ────────────────────────────────────────────────

def _conexoes_pela_rota(uid: int) -> dict[str, dict]:
    client = TestClient(dashboard.app)
    resp = client.get(f"/open-finance/{uid}", headers=_auth(client, uid))
    assert resp.status_code == 200, resp.text
    return {c["provider_item_id"]: c for c in resp.json()["connections"]}


def _aviso(mp, uids: list[int]) -> dict[str, str]:
    """`run_reconnect_notifications` real; devolve destino → `banks` enviado."""
    enviados: dict[str, str] = {}

    def _send(to, name, **kw):
        enviados[to] = kw["named_body_params"][ofp.OF_RECONNECT_PARAM]
        return {"messages": [{"id": "wamid.1"}]}

    mp.setenv("OF_RECONNECT_TEMPLATE_NAME", "tpl")
    mp.setattr(ofp, "list_open_finance_user_ids", lambda: list(uids))
    mp.setattr(ofp, "filtrar_por_acesso", lambda ids: list(ids))
    mp.setattr(ofp, "_targets", lambda uid: [f"55{uid}"])
    mp.setattr("adapters.whatsapp.wa_client.send_template", _send)
    ofp.run_reconnect_notifications()
    return enviados


# ── a–j: um estado por vez ───────────────────────────────────────────────────

@pytest.mark.parametrize("caso", list(CASOS))
def test_tela_e_aviso_dizem_a_mesma_coisa(user_id, monkeypatch, caso):
    cria, estado, detalhe, banco = CASOS[caso]
    item = f"item-{caso}"
    cria(user_id, monkeypatch, item)

    ui = _ui_das_duas(user_id, item)   # rota (tela) == get_connections_by_item_id (toast)
    assert ui["state"] == estado, ui
    if detalhe is not None:
        assert ui["detail"] == detalhe, ui

    enviados = _aviso(monkeypatch, [user_id])
    assert enviados == ({f"55{user_id}": banco} if banco else {}), (caso, ui, enviados)


# ── k: invariante I1 em lote, e o corpo HTTP sem chave nova ──────────────────

def test_em_lote_o_aviso_e_exatamente_a_tela_filtrada(user_id, monkeypatch):
    # Os do job de saúde primeiro: cada tique só mede o próprio item, e os
    # estados sem `health` (i, j) precisam nascer depois do último tique.
    ordem = sorted(CASOS, key=lambda c: c[0] not in "fgh")
    for caso in ordem:
        CASOS[caso][0](user_id, monkeypatch, f"item-{caso}")

    conexoes = _conexoes_pela_rota(user_id)
    for caso in CASOS:
        _, estado, detalhe, _ = CASOS[caso]
        ui = conexoes[f"item-{caso}"]["ui"]
        assert ui["state"] == estado and detalhe in (None, ui["detail"]), (caso, ui)
    for c in conexoes.values():
        assert set(c) == CHAVES_DA_CONEXAO, f"chave nova no corpo HTTP: {set(c) ^ CHAVES_DA_CONEXAO}"

    pela_tela = {i for i, c in conexoes.items() if avisa_reconectar(c["ui"])}
    avisadas = {c["provider_item_id"] for c in db.list_connections_needing_reconnect(user_id)}
    esperadas = {f"item-{c}" for c, (*_, banco) in CASOS.items() if banco}
    assert avisadas == pela_tela == esperadas
    assert _aviso(monkeypatch, [user_id]) == {
        f"55{user_id}": ", ".join(sorted({CASOS[c][3] for c in CASOS if CASOS[c][3]}))}


# ── l: isolamento (§0 do CLAUDE.md) ──────────────────────────────────────────

def test_aviso_de_um_usuario_nao_vaza_para_outro(user_id, monkeypatch):
    outro = user_id + 1
    db.ensure_user(outro)   # o conftest apaga o órfão no fim do teste
    _ativa(outro, monkeypatch, item="item-do-outro")             # nada avisável
    CASOS["e_item_missing"][0](user_id, monkeypatch, "item-perdido")

    assert db.list_connections_needing_reconnect(outro) == []
    assert [c["provider_item_id"] for c in db.list_connections_needing_reconnect(user_id)] \
        == ["item-perdido"]
    assert _aviso(monkeypatch, [user_id, outro]) == {f"55{user_id}": "Nubank"}
