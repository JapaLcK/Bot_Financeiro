"""Onda 5, PR-C2 (grupo G2): o webhook da Pluggy OBSERVA em vez de gravar veredito.

Contrato: `docs/open_finance_estados.md` §1 itens 5/6/10, §2.2 (C12, C15, C16, E12).
Rota REAL (`POST /open-finance/pluggy/webhook?token=...`, por `httpx.ASGITransport`
no MESMO laço da tarefa de fundo), Postgres real, Pluggy mockada só em
`get_pluggy_item`/`create_pluggy_api_key` de `frontend/routes/of_observacao.py` (e nas
funções de leitura de `pluggy_sync`, para o sync de montagem). A tela vem de
`GET /open-finance/{uid}` (`_ui_das_duas`).

R3 = `item/error` rebaixava um item vivo a "Erro temporário" por tempo indefinido.
R8 = `item/created` atrasado trocava o par por UPDATING/sem motivo (verde falso COM
`health`) e o `raw` pelo envelope.

CONTROLES (medidos em 2026-10-05, SEMPRE em CÓPIA do arquivo de produção, restaurada
com `cmp`; remeça se mexer no código):
  negativo, sem `agenda_observacao` no webhook (o `item/error` não faz nada): R3(a),
      R3(b), R3(d), a conversa, a divergência e a derrota de versão ficam vermelhos;
  negativo, `status_by_event` com `item/created: UPDATING` de volta: os 3 casos
      de R8 vermelhos (par e `raw`);
  negativo, `item/error: ERROR` de volta no `status_by_event` (sem observação):
      o par vira ERROR/NULL — R3(a) e "motivo sobrevive" vermelhos;
  negativo, sem o semáforo (`async with _semaforo` removido): o teste de volume
      vê mais de 4 GET simultâneos;
  negativo, sem o coalescer (`if item_id in _INFLIGHT` removido em
      `agenda_observacao`): o teste dos 3 simultâneos vê 3 GET;
  negativo, `_pista` apaga o motivo (`status_reason=""`): os testes de pista
      vermelhos;
  negativo, 404 virando `observar_item(linha, None)` (grava `item_missing`):
      R3(c)[ACTIVE] vermelho;
  negativo, só uma tentativa (sem a releitura na derrota de versão): o teste de
      "1ª derrota relê" vermelho;
  negativo (rodada 3), sem `user_id=` nos dois logs de `of_observacao.py`: o
      diverge e o perdeu_corrida ficam vermelhos (dono na coluna, padrão do #541);
  negativo (rodada 7, Codex P2), lista de 2 valores (`ERROR`/`LOGIN_ERROR`) no lugar
      da regra do resolvedor: `OUTDATED`, `WAITING_USER_INPUT`, `INVALID_CREDENTIALS`
      e `WAITING_USER_ACTION` em `CONFIRMAM` ficam vermelhos (diverge falso);
  negativo (rodada 6, Codex P1), permit só em volta do GET (como antes): o teste de
      leituras da linha simultâneas (> 4) e o do lookup durante a rajada ficam
      vermelhos;
  negativo (rodada 5, Codex P2 #2), `_pista` relendo a linha (versão nova em vez da
      lida antes do GET): o "sync escreve no meio", a derrota dupla e a exceção com
      linha alterada ficam vermelhos;
  negativo (rodada 4), sem o `_tarefa` (try/except de topo): os 2 casos de exceção
      ficam vermelhos (sem log nem pista); `str(exc)` em `details`: o assert da
      MARCA fica vermelho; positivo: o caminho feliz não loga `of_observacao_falhou`;
  negativo (rodada 2), sem a checagem de `_TERMINAL` em `_observa`: os 2 casos
      PAUSED/DELETED vermelhos (2 GET e log falso); sem o `add_done_callback` que
      solta o slot: os 2 casos de exceção reprovam por asserção em `_drena`
      (teto de 10 s), sem travar;
  flaky corrigido: `_posta` em sequência drena `_INFLIGHT` entre POSTs (antes o 2º
      POST caía em `_DIRTY` conforme o timing); coalescer fica só em `simultaneos`;
  positivos (o caminho legítimo continua): a observação grava `health` com
      `last_updated_at`, `item/updated` segue agendando sync, `item/created` agenda
      sync, `item/created` de item desconhecido segue adotando
      (`test_of_webhook_adopt*.py`), `item/deleted` grava DELETED.
Sem `relogio_fixo` (a versão é o `updated_at` real).

O que estes testes NÃO veem: o que o `GET /items` da Pluggy REAL devolve logo depois
de um `item/error` (V6 do plano, medir na Onda 8 pelo `of_observacao_diverge`), nem a
latência real; nem duas réplicas (X7, limite documentado em §4 do doc).
"""

from __future__ import annotations

import asyncio
import json
import threading
import time

import httpx
import pytest

import core.services.pluggy_sync as ps
import db
import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.of_observacao as obs
import frontend.routes.open_finance as of_routes
from core.services.of_retentativa import classe_de_retentativa
from core.services.pluggy import PluggyApiError
from core.services.pluggy_health import resolve_connection_state
from db.connection import get_conn
from test_of_coleta_sem_fim import ITEM, _conecta, _sql, _sync_de_fundo, _ui_das_duas
from test_of_connection_state import (
    ITEM_SAUDAVEL, _avisadas, _conta_pluggy, _linha, _mock_pluggy, _tx_pluggy)
from test_of_item_ownership import SEGREDO, sem_indice_unico  # noqa: F401

LOGIN_ERROR = {**ITEM_SAUDAVEL, "status": "LOGIN_ERROR", "executionStatus": "INVALID_CREDENTIALS"}
ERRO_REAL = {**ITEM_SAUDAVEL, "status": "ERROR", "executionStatus": "ERROR"}
ATUALIZANDO = {**ITEM_SAUDAVEL, "status": "UPDATING", "executionStatus": "ACCOUNTS_IN_PROGRESS"}
ATUALIZADO = ("updated", "Atualizado")
ACAO = ("Ação necessária", "Reautorize o banco")
ERRO_TEMP = "Erro temporário"      # + detalhe: o texto da tela não é o que se mede aqui
ERRO_DO_BANCO = "O banco teve um erro"
MOTIVOS = ["read_failed", "investments_read_failed", "no_accounts"]


# ── infraestrutura ───────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    """Segredo do webhook, semáforo novo (o do módulo se amarra ao 1º laço que
    disputa), sync de fundo gravado em vez de executado, e logs capturados."""
    monkeypatch.setenv("PLUGGY_WEBHOOK_SECRET", SEGREDO)
    monkeypatch.setattr(obs, "_semaforo", asyncio.Semaphore(obs._MAX_SIMULTANEAS))
    syncs: list[str] = []

    async def _sync_falso(item_id, **_kw):
        syncs.append(item_id)

    monkeypatch.setattr(of_routes, "_run_pluggy_sync_bg", _sync_falso)
    logs: list[dict] = []

    async def _log(level, event_type, message, **kw):
        logs.append({"level": level, "event": event_type, **kw})

    monkeypatch.setattr(of_routes, "log_system_event", _log)
    monkeypatch.setattr(obs, "log_system_event", _log)
    of_routes._INFLIGHT.clear()
    of_routes._DIRTY.clear()
    yield {"syncs": syncs, "logs": logs}
    of_routes._INFLIGHT.clear()
    of_routes._DIRTY.clear()


@pytest.fixture()
def syncs(_ambiente):
    return _ambiente["syncs"]


@pytest.fixture()
def logs(_ambiente):
    return _ambiente["logs"]


class _Remoto:
    """A Pluggy do ponto de vista da observação. `item` é um dict, uma exceção, ou
    uma função `(n_da_chamada) -> dict | exceção`. Conta as chamadas e o pico de
    chamadas simultâneas (`atraso` segura cada GET para elas se sobreporem)."""

    def __init__(self, monkeypatch, item, atraso: float = 0.0):
        self.item, self.atraso = item, atraso
        self.chamadas = 0
        self.ids: list[str] = []
        self.pico = 0
        self._agora = 0
        self._lock = threading.Lock()
        monkeypatch.setattr(obs, "create_pluggy_api_key", lambda: "k")
        monkeypatch.setattr(obs, "get_pluggy_item", self._get)

    def _get(self, item_id, api_key=None):
        with self._lock:
            self.chamadas += 1
            n = self.chamadas
            self.ids.append(item_id)
            self._agora += 1
            self.pico = max(self.pico, self._agora)
        try:
            time.sleep(self.atraso)
            item = self.item(n) if callable(self.item) else self.item
            if isinstance(item, Exception):
                raise item
            return item
        finally:
            with self._lock:
                self._agora -= 1


def _envia(c, url, corpo):
    # `send` e não `.post`: o conftest bloqueia o verbo (rede externa), não o
    # transporte ASGI, que é in-process.
    return c.send(c.build_request("POST", url, content=json.dumps(corpo).encode(),
                                  headers={"Content-Type": "application/json"}))


_PRAZO_DRENAGEM_S = 10


async def _drena() -> None:
    """Espera a tarefa de fundo soltar o slot de `_INFLIGHT` (e a rodada suja rodar).
    Com teto: slot que nunca solta reprova por asserção em vez de travar a suíte."""
    fim = time.monotonic() + _PRAZO_DRENAGEM_S
    while of_routes._INFLIGHT:
        if time.monotonic() > fim:
            pytest.fail(f"_INFLIGHT não esvaziou em {_PRAZO_DRENAGEM_S}s: {list(of_routes._INFLIGHT)}")
        await asyncio.gather(*list(of_routes._INFLIGHT.values()), return_exceptions=True)
        await asyncio.sleep(0)


def _posta(corpos, *, token: str | None = SEGREDO, simultaneos: bool = False) -> list[int]:
    """Posta pela rota real e espera a tarefa de fundo (e a rodada suja) acabar.
    Em sequência, DRENA entre os POSTs: o webhook responde 200 antes de a tarefa
    acabar, então sem isso o 2º evento caía em `_DIRTY` conforme o timing (flaky).
    `simultaneos=True` é o caso de coalescer, de propósito sem drenar."""
    url = "/open-finance/pluggy/webhook" + (f"?token={token}" if token else "")

    async def _go():
        transport = httpx.ASGITransport(app=dashboard.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            if simultaneos:
                respostas = await asyncio.gather(*[_envia(c, url, b) for b in corpos])
            else:
                respostas = []
                for b in corpos:
                    respostas.append(await _envia(c, url, b))
                    await _drena()
            await _drena()
            return [r.status_code for r in respostas]

    return asyncio.run(_go())


def _erro(item: str = ITEM) -> dict:
    return {"event": "item/error", "itemId": item}


def _estado(item: str = ITEM) -> dict:
    with get_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "select status, status_reason, raw, health, updated_at, last_sync_at "
                "from open_finance_connections where provider_item_id=%s", (item,))
            return dict(cur.fetchone())


def _par(item: str = ITEM) -> tuple:
    e = _estado(item)
    return e["status"], e["status_reason"]


def _tela(uid: int, item: str = ITEM) -> tuple[str, str, str]:
    ui = _ui_das_duas(uid, item)
    return ui["state"], ui["label"], ui["detail"]


def _ativa(uid: int, monkeypatch, motivo: str | None = None, item: str = ITEM) -> dict:
    """Conexão que já sincronizou (com `health` e espelho): o "Atualizado" de R3/R8.
    `motivo`: o veredito de leitura que o sync deixou na linha."""
    _conecta(uid, "UPDATED", {"id": item, "status": "UPDATED",
                              "connector": {"id": 612, "name": "Nubank"}})
    contas = [] if motivo == "no_accounts" else [_conta_pluggy()]
    _mock_pluggy(monkeypatch, item={**ITEM_SAUDAVEL, "id": item}, contas=contas,
                 txs=[] if motivo == "no_accounts" else [_tx_pluggy()])
    r = ps.sync_pluggy_item(item)
    assert r["ok"] or r["reason"] == "no_accounts", r
    if motivo in ("read_failed", "investments_read_failed"):
        db.mark_sync_result(_linha(item)["id"], ok=False, status="ACTIVE", status_reason=motivo)
    assert _estado(item)["health"] is not None
    return _estado(item)


# ── R3: item/error sobre item vivo ───────────────────────────────────────────

def test_r3a_item_error_com_item_vivo_na_pluggy_fica_atualizado(user_id, monkeypatch):
    _ativa(user_id, monkeypatch)
    assert _tela(user_id)[0] == ATUALIZADO[0]
    remoto = _Remoto(monkeypatch, ITEM_SAUDAVEL)

    assert _posta([_erro()]) == [200]

    assert remoto.chamadas == 1
    status, motivo = _par()
    assert status == "ACTIVE" and not motivo
    assert _tela(user_id)[1] == ATUALIZADO[1]
    assert _avisadas(user_id) == set()


def test_r3b_login_error_na_releitura_pede_reautorizar(user_id, monkeypatch):
    _ativa(user_id, monkeypatch)
    _Remoto(monkeypatch, LOGIN_ERROR)

    _posta([_erro()])

    _, label, detail = _tela(user_id)
    assert (label, detail) == ACAO
    assert _estado()["health"]["item_status"] == "LOGIN_ERROR"


def test_r3d_erro_real_na_releitura_e_erro_temporario(user_id, monkeypatch):
    _ativa(user_id, monkeypatch)
    _Remoto(monkeypatch, ERRO_REAL)

    _posta([_erro()])

    _, label, detail = _tela(user_id)
    assert label == ERRO_TEMP and detail.startswith(ERRO_DO_BANCO), (label, detail)


def test_r3c_404_sobre_item_missing_mantem(user_id, monkeypatch):
    _ativa(user_id, monkeypatch)
    _mock_pluggy(monkeypatch, item=PluggyApiError("nf", status_code=404),
                 contas=[_conta_pluggy()], txs=[_tx_pluggy()])
    assert ps.sync_pluggy_item(ITEM)["reason"] == "item_missing"
    antes = _estado()
    _Remoto(monkeypatch, PluggyApiError("nf", status_code=404))

    _posta([_erro()])

    assert _par() == ("ERROR", "item_missing")
    assert _estado()["health"] == antes["health"]
    assert "Conexão perdida" in _tela(user_id)[1]


def test_r3c_404_sobre_active_grava_pista_e_nao_item_missing(user_id, monkeypatch):
    antes = _ativa(user_id, monkeypatch, "read_failed")
    _Remoto(monkeypatch, PluggyApiError("nf", status_code=404))

    _posta([_erro()])

    depois = _estado()
    assert (depois["status"], depois["status_reason"]) == ("ERROR", "read_failed")
    assert depois["raw"] == antes["raw"] and depois["health"] == antes["health"]


# ── R8: item/created não grava veredito nem raw ──────────────────────────────

@pytest.mark.parametrize("motivo", MOTIVOS)
def test_r8_item_created_nao_toca_par_nem_raw_e_agenda_sync(user_id, monkeypatch, syncs, motivo):
    antes = _ativa(user_id, monkeypatch, motivo)
    tela_antes = _tela(user_id)
    remoto = _Remoto(monkeypatch, ITEM_SAUDAVEL)

    assert _posta([{"event": "item/created", "itemId": ITEM}]) == [200]

    assert _estado() == antes   # par, raw, health, last_sync_at E updated_at
    assert _tela(user_id) == tela_antes
    assert remoto.chamadas == 0       # `item/created` não observa (d1)
    assert syncs == [ITEM]            # o sync continua agendado


def test_item_updated_continua_agendando_sync(user_id, monkeypatch, syncs):
    _ativa(user_id, monkeypatch)
    _posta([{"event": "item/updated", "itemId": ITEM}])
    assert syncs == [ITEM]


def test_item_deleted_continua_gravando_deleted(user_id, monkeypatch):
    _ativa(user_id, monkeypatch)
    _posta([{"event": "item/deleted", "itemId": ITEM}])
    assert _estado()["status"] == "DELETED"


# ── motivo de leitura sobrevive; a observação grava health ───────────────────

@pytest.mark.parametrize("motivo", MOTIVOS)
def test_motivo_sobrevive_a_item_error_com_releitura_saudavel(user_id, monkeypatch, motivo):
    _ativa(user_id, monkeypatch, motivo)
    _Remoto(monkeypatch, ITEM_SAUDAVEL)

    _posta([_erro()])

    assert _par() == ("ACTIVE", motivo)


def test_observacao_grava_health_com_last_updated_at_e_preserva_raw(user_id, monkeypatch):
    antes = _ativa(user_id, monkeypatch)
    _Remoto(monkeypatch, {**ITEM_SAUDAVEL, "lastUpdatedAt": "2026-09-01T10:00:00.000Z"})

    _posta([_erro()])

    depois = _estado()
    assert depois["health"]["last_updated_at"] is not None
    assert depois["health"]["observed_at"] != antes["health"]["observed_at"]
    assert depois["raw"] == antes["raw"]          # d4: o envelope nunca vira `raw`
    assert depois["last_sync_at"] == antes["last_sync_at"]   # observação não é sync


# ── pista quando a releitura não confirma ────────────────────────────────────

@pytest.mark.parametrize("falha", [
    PluggyApiError("rate", status_code=429),
    PluggyApiError("boom", status_code=503),
    TimeoutError("timeout"),
], ids=["429", "5xx", "timeout"])
def test_pista_quando_o_get_falha_preserva_motivo_e_marca_para_retentar(user_id, monkeypatch, falha):
    antes = _ativa(user_id, monkeypatch, "read_failed")
    remoto = _Remoto(monkeypatch, falha)

    _posta([_erro()])

    assert remoto.chamadas == 1      # 429 não retenta
    depois = _estado()
    assert (depois["status"], depois["status_reason"]) == ("ERROR", "read_failed")
    assert depois["health"] == antes["health"] and depois["raw"] == antes["raw"]
    assert _tela(user_id)[1] == ERRO_TEMP


def test_pista_sem_motivo_e_classe_pista_de_erro(user_id, monkeypatch):
    _ativa(user_id, monkeypatch)
    _Remoto(monkeypatch, PluggyApiError("boom", status_code=503))

    _posta([_erro()])

    assert _par()[0] == "ERROR"
    assert _tela(user_id)[1] == ERRO_TEMP
    assert classe_de_retentativa(_linha()) == "pista_de_erro"


# ── idempotência, coalescer, ordem ───────────────────────────────────────────

def test_mesmo_item_error_tres_vezes_sequenciais_mesmo_estado(user_id, monkeypatch):
    _ativa(user_id, monkeypatch, "read_failed")
    remoto = _Remoto(monkeypatch, ITEM_SAUDAVEL)

    _posta([_erro()])
    uma = _par(), _tela(user_id)
    _posta([_erro(), _erro()])

    assert (_par(), _tela(user_id)) == uma
    assert remoto.chamadas == 3


def test_tres_simultaneos_com_get_lento_fazem_um_get_e_uma_rodada_suja(user_id, monkeypatch, syncs):
    _ativa(user_id, monkeypatch)
    remoto = _Remoto(monkeypatch, ITEM_SAUDAVEL, atraso=0.3)

    assert _posta([_erro()] * 3, simultaneos=True) == [200, 200, 200]

    assert remoto.chamadas == 1
    assert syncs == [ITEM]      # a rodada suja: UM sync (que relê), não três


def test_item_error_depois_da_reconexao_fica_atualizando(user_id, monkeypatch):
    """Fora de ordem: a reconexão zerou o `health` (sem sync). O `item/error` velho
    chega; a Pluggy diz que o item está atualizando: tela "Atualizando", não ERROR."""
    _conecta(user_id, "UPDATING")
    assert _estado()["health"] is None
    _Remoto(monkeypatch, ATUALIZANDO)

    _posta([_erro()])

    assert _par()[0] != "ERROR"
    assert _tela(user_id)[1] == "Atualizando…"


def test_sync_em_voo_nao_faz_get_e_marca_sujo(user_id, monkeypatch, syncs):
    _ativa(user_id, monkeypatch)
    remoto = _Remoto(monkeypatch, ITEM_SAUDAVEL)

    async def _go():
        solta = asyncio.Event()

        async def _sync_lento(item_id, **_kw):
            syncs.append(item_id)
            await solta.wait()

        monkeypatch.setattr(of_routes, "_run_pluggy_sync_bg", _sync_lento)
        of_routes._schedule_pluggy_sync(ITEM)             # o sync em voo
        transport = httpx.ASGITransport(app=dashboard.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            r = await _envia(c, f"/open-finance/pluggy/webhook?token={SEGREDO}", _erro())
        assert r.status_code == 200
        marcado = ITEM in of_routes._DIRTY
        solta.set()
        await _drena()
        return marcado

    assert asyncio.run(_go()) is True
    assert remoto.chamadas == 0
    assert syncs == [ITEM, ITEM]        # o em voo e a rodada suja


# ── versão da linha: derrota relê; duas derrotas viram pista ─────────────────

def test_derrota_de_versao_no_primeiro_get_rele_e_grava_a_verdade(user_id, monkeypatch, logs):
    _ativa(user_id, monkeypatch)
    cid = _linha()["id"]

    def _item(n):
        if n == 1:
            db.mark_sync_attempt(cid)   # outro escritor mexe na linha durante o GET
        return LOGIN_ERROR

    remoto = _Remoto(monkeypatch, _item)

    _posta([_erro()])

    assert remoto.chamadas == 2
    assert _tela(user_id)[1:] == ACAO
    assert not [e for e in logs if e["event"] == "of_observacao_perdeu_corrida"]


def test_duas_derrotas_de_versao_logam_e_a_pista_nao_regrava(user_id, monkeypatch, logs):
    """Codex P2 #2: a pista usa a versão lida ANTES do GET; depois de duas derrotas ela
    está velha, então grava 0 linhas e o escritor que ganhou fica (só o log registra)."""
    antes = _ativa(user_id, monkeypatch, "read_failed")
    cid = _linha()["id"]

    def _item(n):
        db.mark_sync_attempt(cid)
        return ITEM_SAUDAVEL

    remoto = _Remoto(monkeypatch, _item)

    _posta([_erro()])

    assert remoto.chamadas == 2
    assert _par() == ("ACTIVE", "read_failed")      # Codex P2 #2: era ERROR (pista relia a versão)
    assert _estado()["health"] == antes["health"]
    perdeu = [e for e in logs if e["event"] == "of_observacao_perdeu_corrida"]
    assert len(perdeu) == 1 and perdeu[0]["details"] == {"item_id": ITEM}
    assert perdeu[0]["user_id"] == user_id          # dono na coluna (#541)


# ── divergência (b1) ─────────────────────────────────────────────────────────

def test_webhook_diz_erro_e_releitura_vive_grava_e_loga_diverge(user_id, monkeypatch, logs):
    _ativa(user_id, monkeypatch)
    _Remoto(monkeypatch, ITEM_SAUDAVEL)

    _posta([_erro()])

    diverge = [e for e in logs if e["event"] == "of_observacao_diverge"]
    assert len(diverge) == 1
    assert diverge[0]["details"] == {
        "item_id": ITEM, "status_evento": "ERROR", "status_releitura": "UPDATED"}
    # Padrão do #541: o dono vai na COLUNA (exportação e exclusão de conta a enxergam),
    # nunca em `details`.
    assert diverge[0]["user_id"] == user_id and "user_id" not in diverge[0]["details"]


# Codex P2: "item em erro" é a regra do `resolve_connection_state` (`_NEEDS_USER` ou
# `ERROR`; `WAITING_USER_ACTION` é o `ITEM_STATUS_AUTORIZA_DISPOSITIVO`, que está em
# `_NEEDS_USER`). Lista literal de propósito: um teste que importasse a regra
# concordaria com qualquer mudança nela.
CONFIRMAM = ["ERROR", "LOGIN_ERROR", "WAITING_USER_INPUT", "INVALID_CREDENTIALS",
             "OUTDATED", "WAITING_USER_ACTION"]
VIVOS = ["UPDATED", "UPDATING", "CREATED"]


@pytest.mark.parametrize("status", CONFIRMAM)
def test_releitura_que_confirma_o_erro_nao_loga_diverge(user_id, monkeypatch, logs, status):
    _ativa(user_id, monkeypatch)
    _Remoto(monkeypatch, {**ITEM_SAUDAVEL, "status": status})

    _posta([_erro()])

    assert not [e for e in logs if e["event"] == "of_observacao_diverge"]
    e = _estado()   # o par gravado é o que o resolvedor produz para esse status
    assert (e["status"], e["status_reason"] or "") == resolve_connection_state(
        health=e["health"], has_data=True, reason_atual="")
    assert e["status"] == "ERROR"


@pytest.mark.parametrize("status", VIVOS)
def test_releitura_viva_loga_diverge_com_o_status_lido(user_id, monkeypatch, logs, status):
    _ativa(user_id, monkeypatch)
    _Remoto(monkeypatch, {**ITEM_SAUDAVEL, "status": status})

    _posta([_erro()])

    diverge = [e for e in logs if e["event"] == "of_observacao_diverge"]
    assert [d["details"]["status_releitura"] for d in diverge] == [status]
    assert _estado()["status"] == "ACTIVE"


# ── isolamento por usuário e posse ───────────────────────────────────────────

def test_item_de_a_nao_altera_a_linha_de_b(user_id, monkeypatch):
    outro = user_id + 1
    db.ensure_user(outro)    # a limpeza de órfãos do conftest o leva embora
    _ativa(user_id, monkeypatch)
    _ativa(outro, monkeypatch, item="item-do-b")
    b_antes = _estado("item-do-b")
    _Remoto(monkeypatch, LOGIN_ERROR)

    _posta([_erro()])

    assert _estado("item-do-b") == b_antes
    assert _tela(user_id)[1:] == ACAO


def test_item_com_dois_donos_nao_le_nem_escreve(user_id, monkeypatch, logs, sem_indice_unico):
    outro = user_id + 1
    db.ensure_user(outro)
    for uid in (user_id, outro):
        _conecta(uid, "UPDATED")
    antes = [dict(r) for r in _linhas_do_item()]
    remoto = _Remoto(monkeypatch, LOGIN_ERROR)

    _posta([_erro()])

    assert remoto.chamadas == 0
    assert [dict(r) for r in _linhas_do_item()] == antes
    assert [e["event"] for e in logs].count("of_item_owner_conflict") == 1


def _linhas_do_item():
    with get_conn() as c:
        with c.cursor() as cur:
            cur.execute("select id, status, status_reason, raw, health, updated_at "
                        "from open_finance_connections where provider_item_id=%s order by id", (ITEM,))
            return cur.fetchall()


def test_get_linha_para_observar_com_user_errado_devolve_none(user_id, monkeypatch):
    _ativa(user_id, monkeypatch)
    cid = _linha()["id"]
    assert db.get_linha_para_observar(cid, user_id)["id"] == cid
    assert db.get_linha_para_observar(cid, user_id + 12345) is None


def test_item_desconhecido_nao_le_a_pluggy(monkeypatch):
    remoto = _Remoto(monkeypatch, LOGIN_ERROR)
    assert _posta([_erro("item-que-ninguem-tem")]) == [200]
    assert remoto.chamadas == 0


# ── autenticação: nada chega à Pluggy ────────────────────────────────────────

def test_sem_token_da_401_e_nao_le_a_pluggy(user_id, monkeypatch):
    _ativa(user_id, monkeypatch)
    remoto = _Remoto(monkeypatch, LOGIN_ERROR)
    assert _posta([_erro()], token=None) == [401]
    assert _posta([_erro()], token="errado") == [401]
    assert remoto.chamadas == 0 and _tela(user_id)[0] == ATUALIZADO[0]


def test_sem_secret_da_503_e_nao_le_a_pluggy(user_id, monkeypatch):
    _ativa(user_id, monkeypatch)
    remoto = _Remoto(monkeypatch, LOGIN_ERROR)
    monkeypatch.delenv("PLUGGY_WEBHOOK_SECRET")
    assert _posta([_erro()]) == [503]
    assert remoto.chamadas == 0


# ── volume ───────────────────────────────────────────────────────────────────

class _LeituraDaLinha:
    """`get_linha_para_observar` instrumentada: lenta e com pico de leituras de banco
    simultâneas (cada uma ocupa uma thread do executor padrão)."""

    def __init__(self, monkeypatch, atraso: float):
        self.real, self.atraso = obs.get_linha_para_observar, atraso
        self.pico, self._agora, self._lock = 0, 0, threading.Lock()
        monkeypatch.setattr(obs, "get_linha_para_observar", self)

    def __call__(self, *a, **k):
        with self._lock:
            self._agora += 1
            self.pico = max(self.pico, self._agora)
        try:
            time.sleep(self.atraso)
            return self.real(*a, **k)
        finally:
            with self._lock:
                self._agora -= 1


def _cria_itens(uid: int, n: int) -> list[str]:
    itens = [f"item-rajada-{i}" for i in range(n)]
    for it in itens:
        _conecta(uid, "UPDATED", {"id": it, "status": "UPDATED",
                                  "connector": {"id": 612, "name": "Nubank"}})
    return itens


def test_rajada_de_itens_diferentes_nunca_passa_do_semaforo(user_id, monkeypatch):
    """Codex P1: o teto de 4 cobre a observação INTEIRA, não só o GET: leituras da
    linha (banco, no executor) também nunca passam de 4 simultâneas."""
    itens = _cria_itens(user_id, 40)
    remoto = _Remoto(monkeypatch, ITEM_SAUDAVEL, atraso=0.02)
    leituras = _LeituraDaLinha(monkeypatch, atraso=0.02)

    _posta([_erro(it) for it in itens], simultaneos=True)

    assert sorted(remoto.ids) == sorted(itens)     # todos observados
    assert remoto.pico == obs._MAX_SIMULTANEAS     # usou o teto e não passou dele
    assert leituras.pico <= obs._MAX_SIMULTANEAS, leituras.pico


def test_rajada_nao_enfileira_o_outro_trabalho_do_executor(user_id, monkeypatch):
    """Codex P1: durante a rajada, o lookup de posse do webhook (um `to_thread` de
    OUTRO trabalho no mesmo executor) responde sem esperar a fila das 40 leituras."""
    itens = _cria_itens(user_id, 40)
    _Remoto(monkeypatch, ITEM_SAUDAVEL, atraso=0.02)
    _LeituraDaLinha(monkeypatch, atraso=0.25)
    medido = {}

    async def _go():
        transport = httpx.ASGITransport(app=dashboard.app)
        url = f"/open-finance/pluggy/webhook?token={SEGREDO}"
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            rajada = asyncio.ensure_future(
                asyncio.gather(*[_envia(c, url, _erro(it)) for it in itens]))
            while len(of_routes._INFLIGHT) < len(itens):     # as 40 tarefas já existem
                await asyncio.sleep(0.01)
            t0 = time.monotonic()
            await asyncio.to_thread(db.get_connections_by_item_id, itens[0])   # o lookup
            medido["lookup"] = time.monotonic() - t0
            medido["pendentes"] = len(of_routes._INFLIGHT)
            await rajada
            await _drena()

    asyncio.run(_go())

    assert medido["pendentes"] > 20                  # o lookup rodou NO MEIO da rajada
    assert medido["lookup"] < 0.2, medido


# ── a conversa (CLAUDE.md §3): item/error, tela, e um sync depois ────────────

def test_conversa_item_error_tela_e_sync_real(user_id, monkeypatch, syncs):
    _ativa(user_id, monkeypatch, "read_failed")
    _Remoto(monkeypatch, LOGIN_ERROR)

    _posta([_erro()])
    assert _tela(user_id)[1:] == ACAO
    assert _avisadas(user_id) == {ITEM}          # o aviso sai: a conexão exige ação

    # O usuário reautoriza: o item volta saudável e o sync REAL fecha o estado.
    _mock_pluggy(monkeypatch, item=ITEM_SAUDAVEL, contas=[_conta_pluggy()], txs=[_tx_pluggy()])
    assert ps.sync_pluggy_item(ITEM)["ok"]

    assert _tela(user_id)[0] == ATUALIZADO[0]
    assert _avisadas(user_id) == set()


# ── rodada 2: terminal, slot solto, pista sobre outros motivos ───────────────

@pytest.mark.parametrize("terminal", ["PAUSED", "DELETED"])
def test_item_error_em_conexao_terminal_nao_le_nem_escreve(user_id, monkeypatch, logs, terminal):
    _ativa(user_id, monkeypatch)
    _sql("update open_finance_connections set status=%s where provider_item_id=%s", terminal, ITEM)
    antes = _estado()
    remoto = _Remoto(monkeypatch, LOGIN_ERROR)

    assert _posta([_erro()]) == [200]

    assert remoto.chamadas == 0
    assert _estado() == antes
    assert not [e for e in logs if e["event"] == "of_observacao_perdeu_corrida"]


MARCA = "SENHA-no-erro"


@pytest.mark.parametrize("onde", ["observar_item", "get_linha"])
def test_excecao_na_tarefa_solta_o_slot_loga_e_tenta_a_pista(user_id, monkeypatch, logs, onde):
    antes = _ativa(user_id, monkeypatch, "read_failed")
    _Remoto(monkeypatch, ITEM_SAUDAVEL)
    real = obs.get_linha_para_observar
    chamadas = {"n": 0}

    def _boom(*a, **k):
        raise RuntimeError(f"connection to server at db.interno failed {MARCA}")

    if onde == "observar_item":
        monkeypatch.setattr(obs, "observar_item", _boom)
    else:
        def _linha_que_cai_so_na_1a(*a, **k):
            chamadas["n"] += 1
            if chamadas["n"] == 1:
                _boom()
            return real(*a, **k)
        monkeypatch.setattr(obs, "get_linha_para_observar", _linha_que_cai_so_na_1a)

    assert _posta([_erro()]) == [200]    # `_posta` reprova se o slot não soltar

    assert of_routes._INFLIGHT == {} and of_routes._DIRTY == set()
    falhou = [e for e in logs if e["event"] == "of_observacao_falhou"]
    assert len(falhou) == 1
    assert falhou[0]["user_id"] == user_id                       # dono na coluna (#541)
    assert falhou[0]["details"] == {"item_id": ITEM, "tipo_do_erro": "RuntimeError"}
    assert MARCA not in json.dumps(falhou[0], default=str)        # nunca o texto da exceção
    depois = _estado()
    if onde == "get_linha":
        # Codex P2 #2: sem versão lida não há CAS: não grava pista, fica só o log.
        assert (depois["status"], depois["status_reason"]) == ("ACTIVE", "read_failed")
    else:   # versão conhecida e linha inalterada: a pista, com o motivo intacto
        assert (depois["status"], depois["status_reason"]) == ("ERROR", "read_failed")
    assert depois["health"] == antes["health"] and depois["raw"] == antes["raw"]


def test_excecao_inesperada_com_linha_alterada_no_meio_nao_grava_pista(user_id, monkeypatch):
    _ativa(user_id, monkeypatch, "read_failed")
    _Remoto(monkeypatch, ITEM_SAUDAVEL)
    cid = _linha()["id"]

    def _bump_e_boom(*a, **k):
        db.mark_sync_attempt(cid)      # outro escritor mexe na linha no meio
        raise RuntimeError("boom")

    monkeypatch.setattr(obs, "observar_item", _bump_e_boom)

    _posta([_erro()])

    assert _par() == ("ACTIVE", "read_failed")


def test_get_falha_e_sync_saudavel_escreve_no_meio_a_pista_nao_sobrescreve(user_id, monkeypatch):
    """Codex P2 #2: o GET falha, mas durante ele um sync saudável termina e escreve a
    linha. A pista (versão lida antes do GET) perde o CAS: a linha fica como o sync
    deixou, e nunca ERROR por cima de um ACTIVE confirmado."""
    _ativa(user_id, monkeypatch)
    cid = _linha()["id"]
    deixado = {}

    def _item(n):
        db.mark_sync_result(cid, ok=True, status="ACTIVE", status_reason="")
        deixado.update(_estado())
        return PluggyApiError("boom", status_code=503)

    _Remoto(monkeypatch, _item)

    _posta([_erro()])

    assert _estado() == deixado and _par()[0] == "ACTIVE"


def test_excecao_na_tarefa_com_log_e_banco_fora_nao_propaga_e_solta_o_slot(user_id, monkeypatch):
    _ativa(user_id, monkeypatch)
    _Remoto(monkeypatch, ITEM_SAUDAVEL)

    def _boom(*a, **k):
        raise RuntimeError("banco fora")

    async def _log_boom(*a, **k):
        raise RuntimeError("log fora")

    monkeypatch.setattr(obs, "get_linha_para_observar", _boom)   # a pista também cai
    monkeypatch.setattr(obs, "log_system_event", _log_boom)

    assert _posta([_erro()]) == [200]

    assert of_routes._INFLIGHT == {}


def test_caminho_feliz_nao_loga_falha(user_id, monkeypatch, logs):
    _ativa(user_id, monkeypatch)
    _Remoto(monkeypatch, ITEM_SAUDAVEL)
    _posta([_erro()])
    assert not [e for e in logs if e["event"] == "of_observacao_falhou"]


def test_limite_da_decisao_a_pista_grava_error_sobre_no_accounts(user_id, monkeypatch):
    """CARACTERIZAÇÃO (limite conhecido da decisão (a)): a pista grava `ERROR` sobre
    QUALQUER motivo. `ACTIVE/no_accounts` vira `ERROR/no_accounts`: a tela segue
    "Sem dados" e a retentativa não pega (classe None)."""
    _ativa(user_id, monkeypatch, "no_accounts")
    _Remoto(monkeypatch, PluggyApiError("boom", status_code=503))

    _posta([_erro()])

    assert _par() == ("ERROR", "no_accounts")
    assert _tela(user_id)[1] == "Sem dados"
    assert classe_de_retentativa(_linha()) is None


def test_limite_da_decisao_a_pista_grava_error_sobre_investments_read_failed(user_id, monkeypatch):
    """CARACTERIZAÇÃO (mesmo limite): `ACTIVE/investments_read_failed` vira
    `ERROR/investments_read_failed`: a tela passa de "Parcial" a "Erro temporário"
    e a retentativa pega (classe `leitura`). Pior caso: só quando o GET falha."""
    _ativa(user_id, monkeypatch, "investments_read_failed")
    assert _tela(user_id)[1] == "Dados parciais"
    _Remoto(monkeypatch, PluggyApiError("boom", status_code=503))

    _posta([_erro()])

    assert _par() == ("ERROR", "investments_read_failed")
    assert _tela(user_id)[1] == ERRO_TEMP
    assert classe_de_retentativa(_linha()) == "leitura"
