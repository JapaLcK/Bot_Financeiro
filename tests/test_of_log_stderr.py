"""Issue #657, PR A: os avisos locais do Open Finance vão para stderr, sem uid e
sem texto cru de exceção, e o rastro do teto estourado diz QUAL item.

1. `_log_com_teto` estourado imprime `motivo=` e `item_id=`. Antes o `motivo` só
   existia no `/pluggy-item`, e nenhum dos três casos da reconexão (409, lock
   ocupado, infra) dizia o item.
2. Os três avisos locais (`of_reconnect_lock_retry`, `of_reconnect_lock_timeout`,
   `of_item_registry_failed`) eram `logging.warning`, que o `_DashboardHandler`
   espelha com INSERT SÍNCRONO no event loop: com `system_event_logs` travada o
   prazo dobrava. Molde: `test_of_log_teto_e_status.py::
   test_teto_vale_com_o_espelho_do_logging_lento`.
3. O `print` do tick de refresh não leva o `claimed` (uid dos donos), e os
   prints de falha do `pluggy_sync` e do tick não levam `str(exc)`. O
   `db/connection.py` já redige o DETAIL das violações de constraint (classe 23),
   então a prova usa um erro real cuja MENSAGEM traz o texto do usuário
   (`22P02`, entrada inválida). Um caso por ponto, pela função que imprime.

CONTROLES (CLAUDE.md §3), cada um medido num caso verde:
  • tirar `item_id=` do print do `_log_com_teto` → os 3 casos do item 1 vermelhos;
  • voltar qualquer um dos três avisos para `logging.getLogger(__name__).warning`
    → o caso daquele aviso no item 2 vermelho;
  • voltar `{res}` no print do tick → `test_tick_do_refresh_nao_imprime_uid`;
  • voltar `{exc}` num print de falha → o caso daquele ponto em
    `test_print_de_falha_nao_leva_o_texto_da_excecao` vermelho.
CLASSE CEGA: aviso NOVO com `logging.warning` antes de um `_log_com_teto` — só
uma varredura pegaria (PR B da #657).
"""
from __future__ import annotations

import asyncio
import logging
import time
from types import SimpleNamespace

import httpx

import psycopg
import pytest
from fastapi.testclient import TestClient

import core.admin_dashboard as admin_dashboard
import core.observability as observability
import core.services.pluggy as pluggy_api
import core.services.piggy_agents as piggy_agents
import core.services.pluggy_sync as ps
import db
import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.open_finance as of_routes
from conftest import promote_to_pro
from core.services import plan_service
from core.services.pluggy import PluggyApiError
from core.services.pluggy_bills import list_pluggy_bills
from db.connection import get_conn
from test_of_item_ownership import _auth, eventos  # noqa: F401 — fixture
from test_of_connection_state import ITEM_SAUDAVEL, _conta_pluggy, _mock_pluggy, _tx_pluggy
from test_of_log_dono_coluna import HOST, _de, _host_fora, _levanta
from test_of_cartao_sinal import CARTAO
from test_of_refresh_schedule import _conexao
from test_of_retentativa import sem_catalogo
from test_of_webhook_adopt_guards import _limpa_item, _mock_item, webhook_pluggy  # noqa: F401

_INFRA = "sorry, too many clients already"


def _conflito(uid, *a, **k):
    raise of_routes._ConflitoReconexao(
        "x", "warning", "of_reconnect_aborted_state_gone", "Reconexão abortada",
        source="open_finance", user_id=uid, details={"item_id": "i657-409"})


def _ocupado(*a, **k):
    return None, False


def _infra(*a, **k):
    raise psycopg.errors.TooManyConnections(_INFRA)


@pytest.mark.parametrize("salva, esperado, status", [
    (_conflito, "of_reconnect_aborted_state_gone motivo=None item_id=i657-409", 409),
    (_ocupado, "of_reconnect_lock_timeout motivo=lock_ocupado item_id=i657-409", 503),
    (_infra, "of_reconnect_lock_timeout motivo=TooManyConnections item_id=i657-409", 503),
], ids=["409", "lock_ocupado", "infra"])
def test_teto_estourado_diz_o_item_e_o_motivo(user_id, monkeypatch, capsys,
                                              salva, esperado, status):
    monkeypatch.setattr(of_routes, "_salva_item_sob_lock", salva)
    monkeypatch.setattr(of_routes, "_RECONNECT_DEADLINE_MS", 300)
    monkeypatch.setattr(of_routes, "_LOG_DIAG_TIMEOUT_S", 0.05)
    monkeypatch.setattr(of_routes, "_backoff_sec", lambda t: 0)

    async def _pendura(*a, **k):
        await asyncio.sleep(3)
    monkeypatch.setattr(of_routes, "log_system_event", _pendura)

    with pytest.raises(of_routes.HTTPException) as exc:
        asyncio.run(of_routes._grava_reconexao(user_id, {"id": "i"}, "i657-409"))
    assert exc.value.status_code == status
    err = capsys.readouterr().err
    assert f"log com teto estourado: {esperado}" in err, err
    assert str(user_id) not in err and _INFRA not in err, err


# ── 2. os três avisos locais não pagam o espelho síncrono ────────────────────

@pytest.fixture()
def espelho_lento(monkeypatch):
    """O `_DashboardHandler` de verdade no root, com o INSERT dublado por um sono
    (a tabela travada), e o `log_system_event` async voltando na hora: o único
    jeito de passar de 1s é um `logging.warning` no caminho."""
    async def _rapido(*a, **k):
        return None
    monkeypatch.setattr(of_routes, "log_system_event", _rapido)
    monkeypatch.setattr(observability, "log_system_event_sync",
                        lambda *a, **k: time.sleep(1.5))
    observability._configure_root_logger()
    assert any(isinstance(h, observability._DashboardHandler)
               for h in logging.getLogger().handlers), \
        "sem o _DashboardHandler no root o espelho não roda e o caso não mede nada"
    monkeypatch.setattr(of_routes, "_backoff_sec", lambda t: 0)


def test_aviso_do_retry_nao_paga_o_espelho(user_id, monkeypatch, espelho_lento, capsys):
    tentativas = []

    def _infra_depois_grava(*a, **k):
        tentativas.append(1)
        if len(tentativas) == 1:
            _infra()
        return {"id": 1, "provider_item_id": "i657-retry"}, True
    monkeypatch.setattr(of_routes, "_salva_item_sob_lock", _infra_depois_grava)

    t0 = time.monotonic()
    conexao = asyncio.run(of_routes._grava_reconexao(user_id, {"id": "i"}, "i657-retry"))
    dt = time.monotonic() - t0
    assert conexao["provider_item_id"] == "i657-retry" and len(tentativas) == 2
    assert dt < 1.0, f"o aviso do retry pagou o espelho síncrono: {dt:.2f}s"
    err = capsys.readouterr().err
    assert "of_reconnect_lock_retry item_id=i657-retry tentativa=1 motivo=TooManyConnections sqlstate=53300" in err, err


def test_aviso_do_timeout_nao_paga_o_espelho(user_id, monkeypatch, espelho_lento, capsys):
    # Uma tentativa só: o aviso do retry nem roda, sobra o do timeout.
    monkeypatch.setattr(of_routes, "_RECONNECT_LOCK_ATTEMPTS", 1)
    monkeypatch.setattr(of_routes, "_salva_item_sob_lock", _infra)

    t0 = time.monotonic()
    with pytest.raises(of_routes.HTTPException) as exc:
        asyncio.run(of_routes._grava_reconexao(user_id, {"id": "i"}, "i657-timeout"))
    dt = time.monotonic() - t0
    assert exc.value.status_code == 503
    assert dt < 1.0, f"o aviso do timeout pagou o espelho síncrono: {dt:.2f}s"
    err = capsys.readouterr().err
    assert "of_reconnect_lock_timeout item_id=i657-timeout motivo=TooManyConnections sqlstate=53300" in err, err
    assert _INFRA not in err, err


def test_aviso_do_registry_vai_ao_stderr_sem_espelho_e_sem_uid(
        user_id, monkeypatch, eventos, webhook_pluggy, capsys):
    """O espelho do `_DashboardHandler` real anota E dorme 1,5s (tabela travada):
    um `logging.warning` no caminho aparece na lista e estoura o 1s. O dono fica
    na coluna do evento do `_log_com_teto` (o `eventos` volta na hora)."""
    espelhados: list[dict] = []

    def _espelho_lento(level, **kw):
        espelhados.append(kw)
        time.sleep(1.5)
    monkeypatch.setattr(observability, "log_system_event_sync", _espelho_lento)
    observability._configure_root_logger()
    assert any(isinstance(h, observability._DashboardHandler)
               for h in logging.getLogger().handlers), \
        "sem o _DashboardHandler no root o espelho não roda e o caso não mede nada"
    promote_to_pro(user_id)
    _mock_item(monkeypatch, user_id)
    monkeypatch.setattr(of_routes, "register_item", _levanta(psycopg.OperationalError(HOST)))
    client = TestClient(dashboard.app)
    headers = _auth(client, user_id)
    try:
        t0 = time.monotonic()
        r = client.post(f"/open-finance/{user_id}/pluggy-item",
                        json={"item": {"id": "i657-reg"}}, headers=headers)
        dt = time.monotonic() - t0
        assert r.status_code == 200, r.text
        assert dt < 1.0, f"o aviso do registry pagou o espelho síncrono: {dt:.2f}s"
        assert not [e for e in espelhados if "of_item_registry_failed" in e.get("message", "")], \
            espelhados
        err = capsys.readouterr().err
        assert "of_item_registry_failed item_id=i657-reg motivo=OperationalError" in err, err
        assert str(user_id) not in err, err
        _host_fora(err)
        assert _de(eventos, "of_item_registry_failed")[0]["user_id"] == user_id, eventos
    finally:
        db.disconnect_open_finance_connection(user_id)
        _limpa_item("i657-reg")


# ── 3. prints do tick de refresh e de falha ───────────────────────────────────

def _uma_volta_do_tick(monkeypatch, tiques: int = 1) -> None:
    """Roda o `_open_finance_refresh` de verdade por `tiques` voltas e corta na
    espera seguinte. O PATCH periódico só roda do 2º tique em diante."""
    async def _log(*a, **kw):
        return None
    monkeypatch.setattr(admin_dashboard, "log_system_event", _log)
    monkeypatch.setattr(dashboard, "_aquece_catalogo", sem_catalogo)

    async def _sem_retentativa(**kw):   # varreria as conexões de outros testes
        return {}
    monkeypatch.setattr("frontend.routes.of_retentativa.retentar_leituras", _sem_retentativa)
    voltas = []
    sleep_real = asyncio.sleep

    async def _uma_volta(segundos):
        voltas.append(segundos)
        if len(voltas) > tiques:
            raise asyncio.CancelledError
        await sleep_real(0)

    async def _corre():
        monkeypatch.setattr(asyncio, "sleep", _uma_volta)
        with pytest.raises(asyncio.CancelledError):
            await dashboard._open_finance_refresh()
    asyncio.run(_corre())


def test_tick_do_refresh_nao_imprime_uid(user_id, monkeypatch, capsys):
    """Tick real, uma volta, no molde de `test_of_refresh_claimed_sem_uid.py`."""
    _conexao(user_id, "i657-tick")
    monkeypatch.setenv("OF_REFRESH_ENABLED", "1")
    monkeypatch.setenv("OF_REFRESH_JITTER_PCT", "0")
    monkeypatch.setattr(ps, "update_pluggy_item", lambda item, key=None: True)
    monkeypatch.setattr(ps, "_hold_aggregate_emails", lambda uid, origem: None)
    monkeypatch.setattr(ps, "run_of_health_check", lambda **kw: {})
    real = ps.request_pluggy_refresh
    monkeypatch.setattr(ps, "request_pluggy_refresh", lambda **kw: real(**kw, user_id=user_id))
    _uma_volta_do_tick(monkeypatch, tiques=2)
    linhas = [ln for ln in capsys.readouterr().out.splitlines() if "[open_finance_refresh]" in ln]
    assert linhas == ["[open_finance_refresh] triggered=1 total=1 origin=periodic failures=[]"], linhas
    assert str(user_id) not in linhas[0], linhas


_ALHEIO = 987654321657


def _erro_real() -> psycopg.errors.InvalidTextRepresentation:
    """A exceção do Postgres de verdade, com o texto do usuário (`<alheio>`) na mensagem."""
    with get_conn() as c:
        try:
            c.execute("select %s::int", (f"x{_ALHEIO}",))
        except psycopg.errors.InvalidTextRepresentation as e:
            c.rollback()
            return e
    raise AssertionError("pré-condição: o cast tinha de recusar")


def _hold(mp, uid, erro):
    mp.setattr(db, "hold_agent_emails", _levanta(erro))
    ps._hold_aggregate_emails(_ALHEIO, "refresh_all")


# A linha que o lote lê no começo do run (`marcar_leitura_falhou` usa as datas).
_CONEXAO_LOTE = {"provider_item_id": "i657-lote", "id": 1,
                 "reconnected_at": None, "last_sync_at": None}


def _lote(mp, uid, erro):
    mp.setattr(ps, "sync_pluggy_item", _levanta(erro))
    mp.setattr(ps, "mark_sync_result", lambda *a, **k: None)
    ps._sync_item_contido(_CONEXAO_LOTE, _ALHEIO)


def _mark(mp, uid, erro):
    mp.setattr(ps, "sync_pluggy_item", _levanta(RuntimeError("x")))
    mp.setattr(ps, "mark_sync_result", _levanta(erro))
    ps._sync_item_contido(_CONEXAO_LOTE, _ALHEIO)


def _hook_user(mp, uid, erro):
    mp.setattr(ps, "get_open_finance_snapshot", lambda u: {"connections": [
        {"provider": "pluggy", "provider_item_id": "i657-u", "id": 1}]})
    mp.setattr(ps, "_hold_aggregate_emails", lambda u, o: None)
    mp.setattr(ps, "_sync_item_contido", lambda c, u: {"ok": True})
    mp.setattr(piggy_agents, "run_faria_limer_once", _levanta(erro))
    mp.setattr(piggy_agents, "run_barao_once", lambda **kw: {})
    ps.sync_pluggy_user(_ALHEIO)


def _sync_real(mp, uid, item_id):
    """Sync real de um item com uma conta e uma transação; só a Pluggy é dublada."""
    _conexao(uid, item_id)
    _mock_pluggy(mp, item={**ITEM_SAUDAVEL, "id": item_id},
                 contas=[_conta_pluggy()], txs=[_tx_pluggy()])
    ps.sync_pluggy_item(item_id)


def _caixinha(mp, uid, erro):
    mp.setattr(plan_service, "require_min_tier", _levanta(erro))
    _sync_real(mp, uid, "i657-caixinha")


def _agentes(mp, uid, erro):
    mp.setattr(ps, "import_open_finance_launches", lambda *a, **k: {"inserted": 1})
    mp.setattr(piggy_agents, "run_agents_for_user", _levanta(erro))
    _sync_real(mp, uid, "i657-agentes")


def _leitura_que_falha(mp, uid, item_id, funcao, erro, contas=None):
    """Sync real em que uma leitura fail-soft da Pluggy levanta `erro`."""
    _conexao(uid, item_id)
    _mock_pluggy(mp, item={**ITEM_SAUDAVEL, "id": item_id},
                 contas=contas or [_conta_pluggy()], txs=[_tx_pluggy()])
    mp.setattr(ps, "list_pluggy_recurring_payments", lambda i, k=None: [])
    mp.setattr(ps, "list_pluggy_bills", lambda *a, **k: [])
    if erro is not None:
        mp.setattr(ps, funcao, _levanta(erro))
    ps.sync_pluggy_item(item_id)


def _investimentos(mp, uid, erro):
    _leitura_que_falha(mp, uid, "i657-inv", "list_pluggy_investments", erro)


def _recorrencias(mp, uid, erro):
    _leitura_que_falha(mp, uid, "i657-rec", "list_pluggy_recurring_payments", erro)


def _faturas(mp, uid, erro):
    _leitura_que_falha(mp, uid, "i657-fat", "list_pluggy_bills", erro, contas=[CARTAO])


def _faturas_gravacao(mp, uid, erro):
    mp.setattr(ps, "salvar_faturas_do_banco", _levanta(erro))
    _leitura_que_falha(mp, uid, "i657-fgr", "list_pluggy_bills", None, contas=[CARTAO])


def _foto(mp, uid, erro):
    """O run falha depois do `GET /items`, e gravar a foto dele também falha."""
    _conexao(uid, "i657-foto")
    _mock_pluggy(mp, item={**ITEM_SAUDAVEL, "id": "i657-foto"})
    mp.setattr(ps, "_sync_pluggy_item_confirmado", _levanta(RuntimeError("x")))
    mp.setattr(ps, "mark_sync_result", _levanta(erro))
    with pytest.raises(RuntimeError):
        ps.sync_pluggy_item("i657-foto")


def _saude(mp, uid, erro):
    mp.setattr(ps, "list_connections_for_health_check",
               lambda **kw: [{"provider_item_id": "i657-saude"}])
    mp.setattr(ps, "create_pluggy_api_key", lambda: "k")
    mp.setattr(ps, "get_pluggy_item", _levanta(erro))
    ps.run_of_health_check()


def _tick(mp, uid, erro):
    mp.setattr(ps, "run_of_health_check", _levanta(erro))
    _uma_volta_do_tick(mp)


@pytest.mark.parametrize("roda, prefixo", [
    (_hold, "[pluggy_sync] hold agregados (refresh_all): "),
    (_lote, "[pluggy_sync] item i657-lote falhou no lote: "),
    (_mark, "[pluggy_sync] mark_sync_result falhou (i657-lote): "),
    (_hook_user, "[pluggy_sync] faria_limer hook (user): "),
    (_caixinha, "[pluggy_sync] caixinha auto-import: "),
    (_agentes, "[pluggy_sync] agents hook: "),
    (_tick, "[open_finance_refresh] erro: "),
    (_investimentos, "[pluggy_sync] investimentos indisponíveis item=i657-inv "),
    (_recorrencias, "[pluggy_sync] recorrências indisponíveis item=i657-rec "),
    (_faturas, "[pluggy_sync] faturas indisponíveis item=i657-fat "),
    (_faturas_gravacao, "[pluggy_sync] faturas não gravadas item=i657-fgr "),
    (_foto, "[pluggy_sync] foto do run que falhou não gravada (i657-foto): "),
    (_saude, "[pluggy_sync] health check falhou item=i657-saude: "),
], ids=["hold_aggregate_emails", "sync_item_contido", "mark_sync_result", "hook_user",
        "caixinha", "agents_hook", "tick_erro", "investimentos", "recorrencias", "faturas",
        "faturas_gravacao", "foto_do_run", "health_check"])
def test_print_de_falha_nao_leva_o_texto_da_excecao(user_id, monkeypatch, capsys,
                                                     roda, prefixo):
    erro = _erro_real()
    assert str(_ALHEIO) in str(erro), "pré-condição: o texto cru traz o dado do usuário"
    roda(monkeypatch, user_id, erro)
    saida = "".join(capsys.readouterr())
    assert f"{prefixo}motivo=InvalidTextRepresentation sqlstate=22P02" in saida, saida
    assert str(_ALHEIO) not in saida, saida


@pytest.mark.parametrize("roda, prefixo", [
    (_investimentos, "[pluggy_sync] investimentos indisponíveis item=i657-inv "),
    (_recorrencias, "[pluggy_sync] recorrências indisponíveis item=i657-rec "),
    (_faturas, "[pluggy_sync] faturas indisponíveis item=i657-fat "),
    (_saude, "[pluggy_sync] health check falhou item=i657-saude: "),
], ids=["investimentos", "recorrencias", "faturas", "health_check"])
def test_falha_da_pluggy_imprime_status_e_code(user_id, monkeypatch, capsys, roda, prefixo):
    """Sem `str(exc)`, o HTTP e o `code` (já filtrado pelo `safe_code`) são o que
    separa limite da Pluggy de contrato diferente (`docs/plano-so-open-finance.md`)."""
    roda(monkeypatch, user_id, PluggyApiError("x", status_code=429, code="RATE_LIMIT"))
    saida = "".join(capsys.readouterr())
    assert (f"{prefixo}motivo=PluggyApiError sqlstate=None status=429 code=RATE_LIMIT"
            in saida), saida


# ── 4. o texto da `PluggyApiError` entra no print; o corpo da resposta, nunca ──
# O texto entra porque é todo nosso e diz qual regra do leitor recusou (o operador
# o procura em `docs/plano-so-open-finance.md`). Isso só vale enquanto o
# `_raise_for_pluggy_response` não puser o corpo da resposta na mensagem, e o
# corpo de erro da Pluggy traz dado do titular. O caso 22P02 do grupo acima prova
# que o texto de qualquer OUTRA exceção continua fora.

_CORPO_COM_PII = {"code": "INVALID_PARAM", "message": "Fulano de Tal Sobrenome",
                  "email": "fulano657@exemplo.com", "cpf": "123.456.789-01"}
_PII = ("Fulano de Tal", "fulano657@exemplo.com", "123.456.789-01")


def _pluggy_http(mp, status: int, corpo: dict) -> None:
    """Todo `httpx.Client` de `core/services/pluggy.py` responde `status` + `corpo`
    pelo `MockTransport`: `_pluggy_get` e `_raise_for_pluggy_response` são os reais."""
    real = httpx.Client
    resposta = httpx.MockTransport(lambda req: httpx.Response(status, json=corpo))
    mp.setattr(pluggy_api, "httpx",
               SimpleNamespace(Client=lambda **kw: real(transport=resposta, **kw)))


def test_pluggy_api_error_nunca_leva_o_corpo_da_resposta(monkeypatch):
    _pluggy_http(monkeypatch, 400, _CORPO_COM_PII)
    with pytest.raises(PluggyApiError) as exc:
        list_pluggy_bills("acc-657", "k")
    texto = str(exc.value)
    assert "HTTP 400" in texto and "code=INVALID_PARAM" in texto, texto
    for pii in _PII:
        assert pii not in texto, texto


@pytest.mark.parametrize("status, corpo, esperado", [
    (200, {"results": [], "totalPages": None},
     "status=None code=None texto=Leitura de /bills incompleta: metadata de paginação incoerente."),
    (400, _CORPO_COM_PII,
     "status=400 code=INVALID_PARAM texto=Falha ao consultar /bills na Pluggy: "
     "Pluggy retornou HTTP 400 (code=INVALID_PARAM)"),
], ids=["recusa_do_leitor", "http_com_pii_no_corpo"])
def test_print_de_faturas_traz_o_texto_da_pluggy_api_error(user_id, monkeypatch, capsys,
                                                          status, corpo, esperado):
    _conexao(user_id, "i657-fat-http")
    _mock_pluggy(monkeypatch, item={**ITEM_SAUDAVEL, "id": "i657-fat-http"},
                 contas=[CARTAO], txs=[_tx_pluggy()])
    monkeypatch.setattr(ps, "list_pluggy_recurring_payments", lambda i, k=None: [])
    _pluggy_http(monkeypatch, status, corpo)
    ps.sync_pluggy_item("i657-fat-http")
    saida = "".join(capsys.readouterr())
    assert (f"[pluggy_sync] faturas indisponíveis item=i657-fat-http "
            f"motivo=PluggyApiError sqlstate=None {esperado}") in saida, saida
    for pii in _PII:
        assert pii not in saida, saida
