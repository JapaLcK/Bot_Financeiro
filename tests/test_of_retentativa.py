"""Onda 5, PR-B2: a retentativa automática pelo tique de saúde (D3 = A).

Contrato e tabelas: `docs/open_finance_estados.md` §2.2.

Pela porta real: estado no Postgres, a listagem de verdade, o orquestrador
(`retentar_leituras`) agendando pelo `_schedule_pluggy_sync` e aguardando o
`_run_pluggy_sync_bg` real, a Pluggy mockada nas funções HTTP com CONTADOR por
item, e a tela pela rota `GET /open-finance/{uid}`. Prazo e cooldown são
montados contra o `now()` do Postgres, sem `relogio_fixo`.

As duas listagens do tique (saúde e retentativa) são globais, e a suíte roda em
paralelo: a fixture `amb` filtra o RESULTADO das queries reais pelos usuários
do teste. Sem isso o tique deste arquivo sincronizaria linha de outro teste com
a Pluggy daqui, e linha de outro teste tomaria vaga do K.

CONTROLES, medidos em 2026-09-30 (mutação numa cópia do arquivo de produção,
restaurada e conferida com `cmp`; remeça se mexer no código). Cada linha é a
mutação e o que ficou vermelho:

  negativos, elegibilidade (`core/services/of_retentativa.py`, listagem):
    tirar a exclusão de E13 (`item_status == ERROR`)   → E13 (as duas células)
    tirar a guarda de estado (`elif estado not in …`)  → E3, E10, E11
    não exigir a Pluggy à frente (`if True:`)          → E7, E9, E15, E17 e R1/R5/R6
    âncora `last_sync_at` no lugar de `last_attempt_at` → E8 (coleta vencida), E9
    tirar o `coleta_vencida` do ramo `updating`        → E6, E24
    tirar o cooldown do SQL                            → E18
    tirar o "dono único" do SQL                        → E19
    predicado aceitando data sem fuso / com `>=`       → os casos do predicado
  negativos, tique (`frontend/routes/of_retentativa.py` e o laço):
    tirar a chamada do laço (`_open_finance_refresh`)  → R1, R5, R6
    ignorar `OF_HEALTH_CHECK_ENABLED`                  → desligado desliga tudo
    tirar o filtro de acesso                           → sem direito de uso
    tirar o corte K                                    → teto K, duas passadas
    429 não parar                                      → 429 para o tique
    sucesso não zerar a conta                          → disjuntor FFOFFX
    neutro contar como falha                           → disjuntor FFNX, reconexão
    `stale_authorization` fora dos neutros             → reconexão no meio
    tirar o prazo do tique                             → prazo
    tirar a rechecagem por `id`                        → Atualizar no meio
    não passar `expected_user_id`                      → readoção, F de outro dono
    a F ignorar o dono (`_run_pluggy_sync_bg`)         → F de outro dono
    (rodada 2) o orquestrador voltar a carimbar        → 5xx em `no_accounts`, coalescido
    (rodada 2) o resto dos controles: `tests/test_of_retentativa_tique.py`
  positivos: E1, E2, E5, E8, E12, E16 e E17 com a Pluggy à frente selecionados;
  conexão em dia com 0 chamadas; com K=0 a saúde ainda faz o `GET /items`;
  sem inserção o hook de agentes não roda.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone

import pytest
from psycopg.types.json import Jsonb

import core.services.pluggy_sync as ps
import db
import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.open_finance as of_routes
from conftest import promote_to_pro
from core.services.pluggy import PluggyApiError
from db.connection import get_conn
from test_of_connection_state import ITEM_SAUDAVEL, _conta_pluggy, _tx_pluggy
from test_of_item_ownership import eventos, sem_indice_unico  # noqa: F401
from test_of_leitura_incompleta import _ui_pela_rota

VENCIDA = "Está demorando mais que o normal — atualize de novo"


def _iso(horas_atras: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=horas_atras)).isoformat()


def _health(item_status: str = "UPDATED", *, horas: float = 3.0, stale=()) -> dict:
    """Foto do item. `horas` = idade do `last_updated_at` dos produtos; a linha
    padrão tentou há 2 h, então `horas < 2` é "a Pluggy à frente"."""
    produtos = {"BANK": {"updated": True, "last_updated_at": _iso(horas), "warnings": []}}
    for p in stale:
        produtos[p] = {"updated": False, "last_updated_at": _iso(horas), "warnings": []}
    return {"observed_at": datetime.now(timezone.utc).isoformat(), "item_status": item_status,
            "execution_status": "SUCCESS", "products": produtos, "stale_products": list(stale)}


# Linha "velha e tentada": autorizada há 3 h, última tentativa e último sync há 2 h.
_SQL_PADRAO = {"created_at": "now() - interval '3 hours'",
               "last_attempt_at": "now() - interval '2 hours'",
               "last_sync_at": "now() - interval '2 hours'",
               "reconnected_at": "null"}


def _nova(uid: int, *, status: str = "ACTIVE", reason: str | None = None, health=...,
          provider: str = "pluggy", raw: dict | None = None, item: str | None = None,
          **sql) -> dict:
    item = item or f"ret-{uuid.uuid4().hex[:12]}"
    conn = db.save_pluggy_open_finance_item(
        uid, {"id": item, "status": "UPDATED", "connector": {"id": 612, "name": "Nubank"}})
    saude = _health() if health is ... else health
    sets = ", ".join(f"{k} = {v}" for k, v in {**_SQL_PADRAO, **sql}.items())
    with get_conn() as c:
        c.execute(
            f"update open_finance_connections set status=%s, status_reason=%s, health=%s, "
            f"provider=%s, raw=coalesce(%s, raw), {sets} where id=%s",
            (status, reason, Jsonb(saude) if saude else None, provider,
             Jsonb(raw) if raw else None, conn["id"]))
        c.commit()
    return {"id": conn["id"], "item": item, "uid": uid}


def _linha(conexao_id: int) -> dict | None:
    with get_conn() as c:
        return c.execute("select * from open_finance_connections where id=%s",
                         (conexao_id,)).fetchone()


# ── 1. Predicado puro ────────────────────────────────────────────────────────

_T = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("datas, esperado", [
    (["2026-09-30T13:00:00Z"], True),
    (["2026-09-30T13:00:00.000Z"], True),
    (["2026-09-30T10:00:00-03:00"], True),           # 13:00 UTC
    (["2026-09-30T12:00:00Z"], False),               # igual: `>` estrito
    (["2026-09-30T11:00:00Z"], False),
    (["2026-09-30T13:00:00"], False),                # sem fuso: ignorada
    (["2026-09-30"], False),                         # só data: sem fuso
    (["lixo"], False),
    ([None], False),
    ([7], False),
    (["lixo", "2026-09-30T13:00:00Z"], True),        # a válida conta, a inválida não
    (["2026-09-30T13:00:00", "2026-09-30T11:00:00Z"], False),
])
def test_pluggy_tem_dado_depois_de(datas, esperado):
    from core.services.pluggy_health import pluggy_tem_dado_depois_de
    produtos = {f"P{i}": {"last_updated_at": d} for i, d in enumerate(datas)}
    assert pluggy_tem_dado_depois_de({"products": produtos}, _T) is esperado


@pytest.mark.parametrize("health, instante", [
    (None, _T), ({"products": {"BANK": {"last_updated_at": "2026-10-01T00:00:00Z"}}}, None),
    ({}, _T), ({"products": None}, _T), ({"products": {"BANK": {}}}, _T),
    ({"products": {"BANK": "x"}}, _T), ("x", _T),
    ({"products": {"BANK": {"last_updated_at": "2026-10-01T00:00:00Z"}}}, datetime(2026, 9, 30)),
])
def test_pluggy_tem_dado_depois_de_sem_base_e_falso(health, instante):
    from core.services.pluggy_health import pluggy_tem_dado_depois_de
    assert pluggy_tem_dado_depois_de(health, instante) is False


# ── 2. Elegibilidade E1–E24 (listagem real + classificador) ─────────────────
# Cada célula grava o estado no banco e pergunta à listagem e ao classificador.
# `lambda` porque as datas da Pluggy são relativas ao momento do teste.

CELULAS = {
    "E1_read_failed_sem_health": (lambda: dict(reason="read_failed", health=None), "leitura"),
    "E1_read_failed_item_saudavel": (lambda: dict(reason="read_failed"), "leitura"),
    "E2_investments_read_failed": (lambda: dict(reason="investments_read_failed"), "leitura"),
    "E3_read_failed_needs_user": (
        lambda: dict(reason="read_failed", health=_health("LOGIN_ERROR", horas=1)), None),
    "E4_dispositivo_na_janela": (
        lambda: dict(status="OUTDATED", health=None,
                     raw={"executionStatus": "USER_AUTHORIZATION_PENDING"},
                     created_at="now() - interval '10 minutes'",
                     last_sync_at="null", last_attempt_at="null"), None),
    "E5_coleta_vencida_sem_health": (
        lambda: dict(status="UPDATING", health=None, last_sync_at="null",
                     last_attempt_at="null"), "coleta"),
    "E5_coleta_vencida_com_health": (
        lambda: dict(status="UPDATING", health=_health("UPDATING"), last_sync_at="null",
                     last_attempt_at="null"), "coleta"),
    "E6_coleta_no_prazo": (
        lambda: dict(status="UPDATING", health=None, last_sync_at="null", last_attempt_at="null",
                     created_at="now() - interval '5 minutes'"), None),
    "E7_coleta_vencida_no_accounts": (
        lambda: dict(reason="no_accounts", health=_health("UPDATING"), last_sync_at="null"), None),
    "E8_no_accounts_pluggy_a_frente": (
        lambda: dict(reason="no_accounts", health=_health(horas=1)), "pluggy_a_frente"),
    "E8_coleta_vencida_no_accounts_pluggy_a_frente": (
        lambda: dict(reason="no_accounts", health=_health("UPDATING", horas=1),
                     last_sync_at="null"), "pluggy_a_frente"),
    # A Pluggy coletou DEPOIS do último sync bom e ANTES da última tentativa:
    # é a célula que separa a âncora `last_attempt_at` da `last_sync_at`.
    "E9_no_accounts_sem_pluggy_a_frente": (
        lambda: dict(reason="no_accounts", health=_health(horas=3),
                     last_sync_at="now() - interval '5 hours'"), None),
    "E10_item_missing": (
        lambda: dict(status="ERROR", reason="item_missing", health=_health(horas=1)), None),
    "E11_needs_user": (lambda: dict(status="ERROR", health=_health("LOGIN_ERROR", horas=1)), None),
    "E12_error_do_webhook_item_saudavel": (lambda: dict(status="ERROR"), "pista_de_erro"),
    "E12_error_do_webhook_sem_health": (lambda: dict(status="ERROR", health=None),
                                        "pista_de_erro"),
    "E13_item_em_error_na_pluggy": (lambda: dict(status="ERROR", health=_health("ERROR")), None),
    "E13_read_failed_item_em_error": (
        lambda: dict(reason="read_failed", health=_health("ERROR")), None),
    "E14_paused": (lambda: dict(status="PAUSED", reason="read_failed"), None),
    "E14_deleted": (lambda: dict(status="DELETED", reason="read_failed"), None),
    "E15_em_dia": (lambda: dict(), None),
    "E16_pluggy_a_frente": (lambda: dict(health=_health(horas=1)), "pluggy_a_frente"),
    "E17_parcial_da_pluggy": (lambda: dict(health=_health(stale=("CREDIT",))), None),
    "E17_parcial_da_pluggy_a_frente": (
        lambda: dict(health=_health(horas=1, stale=("CREDIT",))), "pluggy_a_frente"),
    "E18_cooldown": (
        lambda: dict(reason="read_failed", last_attempt_at="now() - interval '10 minutes'"), None),
    "E21_mock_pluggy": (lambda: dict(reason="read_failed", provider="mock_pluggy"), None),
    "E23_motivo_desconhecido": (lambda: dict(reason="motivo_que_ninguem_conhece"), None),
    "E24_reconectado_no_prazo": (
        lambda: dict(status="UPDATING", health=None,
                     reconnected_at="now() - interval '5 minutes'"), None),
}


def _selecionadas() -> dict[int, str]:
    from core.services.of_retentativa import elegiveis
    from db.open_finance_state import list_connections_para_retentar
    return {r["id"]: classe for r, classe in elegiveis(list_connections_para_retentar())}


@pytest.mark.parametrize("celula", sorted(CELULAS))
def test_elegibilidade_por_celula(user_id, celula):
    estado, esperado = CELULAS[celula]
    c = _nova(user_id, **estado())
    assert _selecionadas().get(c["id"]) == esperado


def test_E19_item_com_dois_donos_fica_fora(user_id, sem_indice_unico):  # noqa: F811
    outro = user_id + 1
    db.ensure_user(outro)
    a = _nova(user_id, reason="read_failed")
    b = _nova(outro, reason="read_failed", item=a["item"])
    selecionadas = _selecionadas()
    assert a["id"] not in selecionadas and b["id"] not in selecionadas


def test_rechecagem_por_id_devolve_so_a_linha(user_id):
    from db.open_finance_state import list_connections_para_retentar
    a, _b = _nova(user_id, reason="read_failed"), _nova(user_id, reason="read_failed")
    assert [r["id"] for r in list_connections_para_retentar(id=a["id"])] == [a["id"]]


# ── 3–5. O tique ─────────────────────────────────────────────────────────────

class _Pluggy:
    """A Pluggy mockada nas funções HTTP, com contador de chamadas por item."""

    def __init__(self, monkeypatch):
        self.chamadas: Counter = Counter()
        self.contas: Counter = Counter()
        self.falha: dict[str, Exception] = {}
        self.com_transacao = False
        self.durante_contas = None
        monkeypatch.setattr(ps, "create_pluggy_api_key", lambda: "k")
        monkeypatch.setattr(ps, "get_pluggy_item", self._item)
        monkeypatch.setattr(ps, "list_pluggy_accounts", self._lista_contas)
        monkeypatch.setattr(ps, "list_pluggy_transactions", self._txs)
        monkeypatch.setattr(ps, "list_pluggy_investments", self._inv)

    def _item(self, item_id, _k=None):
        self.chamadas[item_id] += 1
        if item_id in self.falha:
            raise self.falha[item_id]
        return {**ITEM_SAUDAVEL, "id": item_id}

    def _lista_contas(self, item_id, _k=None):
        self.chamadas[item_id] += 1
        self.contas[item_id] += 1
        if self.durante_contas:
            self.durante_contas(item_id)
        return [_conta_pluggy(f"acc-{item_id}")]

    def _txs(self, account_id, _k=None, **_kw):
        return [_tx_pluggy(f"tx-{account_id}")] if self.com_transacao else []

    def _inv(self, item_id, _k=None):
        self.chamadas[item_id] += 1
        return []


@pytest.fixture()
def amb(user_id, monkeypatch, eventos):  # noqa: F811
    import frontend.routes.of_retentativa as orq
    from db.open_finance_state import list_connections_para_retentar

    promote_to_pro(user_id)
    meus = {user_id}
    real_saude = ps.list_connections_for_health_check
    monkeypatch.setattr(ps, "list_connections_for_health_check",
                        lambda **kw: [r for r in real_saude(**kw) if r["user_id"] in meus])
    ganchos: dict[int, object] = {}

    def _lista(**kw):
        """`ganchos[id]` roda ANTES da rechecagem daquela linha; `ganchos[("depois",
        id)]`, DEPOIS dela (entre a rechecagem e o agendamento)."""
        if kw.get("id") in ganchos:
            ganchos.pop(kw["id"])()
        linhas = [r for r in list_connections_para_retentar(**kw) if r["user_id"] in meus]
        if ("depois", kw.get("id")) in ganchos:
            ganchos.pop(("depois", kw["id"]))()
        return linhas

    monkeypatch.setattr(orq, "list_connections_para_retentar", _lista)
    monkeypatch.setattr(of_routes, "_backoff_sec", lambda _t: 0)
    of_routes._INFLIGHT.clear()
    of_routes._DIRTY.clear()
    orq._TENTADOS.clear()      # memória do processo: um caso não herda a fila do outro

    avisos: Counter = Counter()

    async def _broadcast(uid, _msg):
        avisos[uid] += 1

    monkeypatch.setattr(dashboard.manager, "broadcast_to_user", _broadcast)
    agentes: list[int] = []
    monkeypatch.setattr("core.services.piggy_agents.run_agents_for_user",
                        lambda uid, trigger=None: agentes.append(uid))

    class A:
        pass

    a = A()
    a.pluggy, a.meus, a.eventos, a.avisos, a.agentes = _Pluggy(monkeypatch), meus, eventos, avisos, agentes
    a.ganchos, a.orq = ganchos, orq
    yield a
    of_routes._INFLIGHT.clear()
    of_routes._DIRTY.clear()
    orq._TENTADOS.clear()


def _retenta(amb, prazo_sec: float = 3600) -> dict:
    asyncio.run(amb.orq.retentar_leituras(prazo_sec=prazo_sec))
    ticks = [e for e in amb.eventos if e["event"] == "of_retry_tick"]
    assert ticks, "o tique não registrou `of_retry_tick`"
    return ticks[-1]


def _um_tique(monkeypatch, tiques: int = 1) -> None:
    """`tiques` voltas do laço de verdade (`_open_finance_refresh`): os `sleep` do 1º
    tique (`_PRIMEIRO_TIQUE_SEC`) e do intervalo passam, o seguinte encerra o laço. O PATCH
    periódico só roda do 2º tique em diante (`tiques=2`)."""
    real = asyncio.sleep
    voltas = {"n": 0}

    async def _sleep(sec, *a, **kw):
        if sec in (4321, dashboard._PRIMEIRO_TIQUE_SEC):
            voltas["n"] += 1
            if voltas["n"] > tiques:
                raise asyncio.CancelledError
            return None
        return await real(sec, *a, **kw)

    monkeypatch.setenv("OF_REFRESH_INTERVAL_SEC", "4321")
    monkeypatch.setattr(asyncio, "sleep", _sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(dashboard._open_finance_refresh())
    monkeypatch.setattr(asyncio, "sleep", real)


@pytest.mark.parametrize("cenario", ["R1_sync_de_fundo_morto", "R5_read_failed",
                                     "R6_webhook_perdido"])
def test_o_tique_recupera_quem_ficou_para_tras(user_id, amb, monkeypatch, cenario):
    c = {"R1_sync_de_fundo_morto": lambda: _nova(user_id, status="UPDATING", health=None,
                                                 last_sync_at="null", last_attempt_at="null"),
         "R5_read_failed": lambda: _nova(user_id, reason="read_failed"),
         "R6_webhook_perdido": lambda: _nova(user_id, health=_health(horas=1))}[cenario]()
    em_dia = _nova(user_id)
    antes = _ui_pela_rota(user_id, c["item"])
    assert (antes["state"], antes["detail"]) == {
        "R1_sync_de_fundo_morto": ("updating", VENCIDA),
        "R5_read_failed": ("error_recoverable", "Tentaremos de novo automaticamente"),
        "R6_webhook_perdido": ("updated", None)}[cenario]

    _um_tique(monkeypatch)

    assert _ui_pela_rota(user_id, c["item"])["state"] == "updated"
    assert amb.pluggy.contas[c["item"]] == 1, "o tique tinha de reler a Pluggy"
    linha = _linha(c["id"])
    assert linha["last_sync_at"] > datetime.now(timezone.utc) - timedelta(minutes=5)
    assert linha["status_reason"] is None
    assert amb.pluggy.chamadas[em_dia["item"]] == 0, "conexão em dia não gasta chamada"


def test_teto_k_por_tique(user_id, amb, monkeypatch):
    monkeypatch.setenv("OF_RETRY_MAX_PER_TICK", "3")
    conexoes = [_nova(user_id, reason="read_failed") for _ in range(8)]
    tick = _retenta(amb)
    lidos = [c["item"] for c in conexoes if amb.pluggy.chamadas[c["item"]]]
    assert lidos == [c["item"] for c in conexoes[:3]]
    assert tick["details"]["tentados"] == 3 and tick["details"]["elegiveis"] == 8
    assert "user_id" not in tick["details"] and not tick.get("user_id")


def test_mais_antiga_primeiro_e_duas_passadas_sem_repetir(user_id, amb, monkeypatch):
    monkeypatch.setenv("OF_RETRY_MAX_PER_TICK", "2")
    # A ordem de criação (`id`) é DIFERENTE da ordem de idade: só `order by id` falharia.
    idades = ["'3 hours'", "'4 hours'", None, "'5 hours'"]
    conexoes = [_nova(user_id, reason="read_failed",
                      last_attempt_at=f"now() - interval {i}" if i else "null") for i in idades]
    for c in conexoes:   # a leitura falha sempre: a linha continua elegível
        amb.pluggy.falha[c["item"]] = PluggyApiError("boom", status_code=500)
    _retenta(amb)
    primeira = {c["item"] for c in conexoes if amb.pluggy.chamadas[c["item"]]}
    assert primeira == {conexoes[2]["item"], conexoes[3]["item"]}      # nunca tentada e 5 h
    amb.pluggy.chamadas.clear()
    _retenta(amb)
    segunda = {c["item"] for c in conexoes if amb.pluggy.chamadas[c["item"]]}
    assert segunda == {conexoes[1]["item"], conexoes[0]["item"]}       # 4 h e 3 h


def test_429_para_o_tique(user_id, amb):
    conexoes = [_nova(user_id, reason="read_failed") for _ in range(3)]
    amb.pluggy.falha[conexoes[0]["item"]] = PluggyApiError("rate", status_code=429)
    tick = _retenta(amb)
    assert [amb.pluggy.chamadas[c["item"]] for c in conexoes[1:]] == [0, 0]
    assert tick["details"]["interrompido"] == "429" and tick["level"] == "warning"


def _reconecta(uid: int, item: str) -> None:
    db.save_pluggy_open_finance_item(
        uid, {"id": item, "status": "UPDATING", "connector": {"id": 612, "name": "Nubank"}})


@pytest.mark.parametrize("sequencia, tentados", [
    ("FFFX", 3),          # 3 falhas seguidas param: o 4º não é tentado
    ("FFOFFX", 6),        # um sucesso no meio reinicia a conta
    ("FFNX", 4),          # neutro (reconexão no meio: stale_authorization) não conta
])
def test_disjuntor_de_falhas_seguidas(user_id, amb, sequencia, tentados):
    conexoes = [_nova(user_id, reason="read_failed") for _ in sequencia]
    neutros = {c["item"] for letra, c in zip(sequencia, conexoes) if letra == "N"}
    for letra, c in zip(sequencia, conexoes):
        if letra == "F":
            amb.pluggy.falha[c["item"]] = PluggyApiError("boom", status_code=503)
    amb.pluggy.durante_contas = lambda item: _reconecta(user_id, item) if item in neutros else None
    tick = _retenta(amb)
    tocados = [c["item"] for c in conexoes if amb.pluggy.chamadas[c["item"]]]
    assert tocados == [c["item"] for c in conexoes[:tentados]]
    assert tick["details"]["interrompido"] == ("falhas_seguidas" if tentados < len(sequencia)
                                               else None)


def test_coalescido_marca_dirty_e_nao_cria_tarefa(user_id, amb):
    c = _nova(user_id, reason="read_failed")
    of_routes._INFLIGHT[c["item"]] = object()
    tick = _retenta(amb)
    assert amb.pluggy.chamadas[c["item"]] == 0 and c["item"] in of_routes._DIRTY
    assert tick["details"]["coalescidos"] == 1


def test_health_check_desligado_desliga_tudo(user_id, amb, monkeypatch):
    monkeypatch.setenv("OF_HEALTH_CHECK_ENABLED", "0")
    c = _nova(user_id, reason="read_failed", health=None)
    _um_tique(monkeypatch)
    assert amb.pluggy.chamadas[c["item"]] == 0


def test_k_zero_desliga_so_a_retentativa(user_id, amb, monkeypatch):
    monkeypatch.setenv("OF_RETRY_MAX_PER_TICK", "0")
    c = _nova(user_id, reason="read_failed", health=None)
    _um_tique(monkeypatch)
    assert amb.pluggy.chamadas[c["item"]] == 1, "a saúde roda (um GET /items)"
    assert amb.pluggy.contas[c["item"]] == 0, "a retentativa não"


def test_prazo_do_tique_nao_comeca_item_novo(user_id, amb, monkeypatch):
    conexoes = [_nova(user_id, reason="read_failed") for _ in range(2)]
    relogio = iter([0.0, 0.0, 0.0, 10_000.0])   # início, 1º item (começo e depois da rechecagem), 2º item
    monkeypatch.setattr(amb.orq, "_relogio", lambda: next(relogio))
    tick = _retenta(amb, prazo_sec=60)
    assert [amb.pluggy.contas[c["item"]] for c in conexoes] == [1, 0]
    assert tick["details"]["interrompido"] == "prazo" and tick["details"]["tentados"] == 1


def test_sem_direito_de_uso_nao_entra(user_id, amb, monkeypatch):
    monkeypatch.setenv("PLANS_V2_ENABLED", "1")
    monkeypatch.setenv("ACCESS_GATE_ENABLED", "1")
    cortado = user_id + 1
    db.ensure_user(cortado)
    amb.meus.add(cortado)
    com, sem = _nova(user_id, reason="read_failed"), _nova(cortado, reason="read_failed")
    _retenta(amb)
    assert (amb.pluggy.contas[com["item"]], amb.pluggy.chamadas[sem["item"]]) == (1, 0)


def _envelhece(*ids, horas: float = 6.0) -> None:
    """Passa `horas` de RELÓGIO para as conexões: tentativa, sync E as datas dos
    produtos da Pluggy no `health` (todas absolutas, então todas recuam juntas)."""
    with get_conn() as c:
        c.execute("update open_finance_connections set "
                  "last_attempt_at = last_attempt_at - interval '1 hour' * %s, "
                  "last_sync_at = last_sync_at - interval '1 hour' * %s "
                  "where id = any(%s)", (horas, horas, list(ids)))
        for i in ids:
            h = c.execute("select health from open_finance_connections where id=%s",
                          (i,)).fetchone()["health"]
            if h:
                for p in (h.get("products") or {}).values():
                    if p.get("last_updated_at"):
                        p["last_updated_at"] = (datetime.fromisoformat(p["last_updated_at"])
                                                - timedelta(hours=horas)).isoformat()
                c.execute("update open_finance_connections set health=%s where id=%s",
                          (Jsonb(h), i))
        c.commit()


def test_no_accounts_a_frente_com_5xx_passageiro_volta_a_ser_retentado(user_id, amb):
    """O orquestrador NÃO carimba a tentativa (rodada 2 do PR-B2). `no_accounts` com a
    Pluggy à frente e o `GET /items` dando 5xx não grava nada na linha (a marca de
    falha não troca veredito): se o orquestrador carimbasse, a âncora
    (`last_attempt_at`) passaria do dado da Pluggy e a linha não voltaria até a
    Pluggy coletar de novo. Passam 6 h; o tique seguinte tem de tentar de novo.
    O custo aceito da escolha (a linha fica na frente da fila) está em
    `docs/open_finance_estados.md` §2.2."""
    c = _nova(user_id, reason="no_accounts", health=_health(horas=1))
    antes = _linha(c["id"])["last_attempt_at"]
    amb.pluggy.falha[c["item"]] = PluggyApiError("boom", status_code=500)
    _retenta(amb)
    assert amb.pluggy.chamadas[c["item"]] == 3, "as 3 tentativas do sync de fundo"
    linha = _linha(c["id"])
    assert (linha["status_reason"], linha["last_attempt_at"]) == ("no_accounts", antes)
    _envelhece(c["id"])
    assert _selecionadas().get(c["id"]) == "pluggy_a_frente"


def test_coalescido_nao_gasta_a_janela_da_tentativa(user_id, amb):
    """Item em voo (coalescido em `_DIRTY`): ninguém o tentou, e o `last_attempt_at`
    e a origem ficam como estavam."""
    c = _nova(user_id, reason="read_failed")
    antes = _linha(c["id"])
    of_routes._INFLIGHT[c["item"]] = object()
    _retenta(amb)
    depois = _linha(c["id"])
    assert depois["last_attempt_at"] == antes["last_attempt_at"]
    assert depois["last_refresh_origin"] == antes["last_refresh_origin"]


# ── 4. Coordenação ───────────────────────────────────────────────────────────

def test_readocao_entre_a_listagem_e_o_run_nao_grava_em_ninguem(user_id, amb):
    c = _nova(user_id, reason="read_failed")
    outro = user_id + 1
    db.ensure_user(outro)
    amb.meus.add(outro)
    nova: dict = {}

    def _readota():
        with get_conn() as conn:
            conn.execute("delete from open_finance_connections where id=%s", (c["id"],))
            conn.commit()
        nova.update(_nova(outro, reason="read_failed", item=c["item"]))
        nova["antes"] = _linha(nova["id"])

    amb.ganchos[("depois", c["id"])] = _readota
    _retenta(amb)
    assert amb.pluggy.chamadas[c["item"]] == 0, "a recusa vem antes de qualquer leitura"
    assert _linha(c["id"]) is None
    assert _linha(nova["id"]) == nova["antes"], "a linha do outro dono ficou intocada"


def test_falha_do_run_nao_marca_a_linha_de_outro_dono(user_id, amb, monkeypatch):
    """Readoção antes da captura do sync de fundo e a leitura do próprio sync
    levantando (banco fora): a marca de falha (F) iria para a linha capturada,
    que é do OUTRO dono."""
    c = _nova(user_id, reason="read_failed")
    outro = user_id + 1
    db.ensure_user(outro)
    amb.meus.add(outro)
    nova: dict = {}

    def _readota():
        with get_conn() as conn:
            conn.execute("delete from open_finance_connections where id=%s", (c["id"],))
            conn.commit()
        nova.update(_nova(outro, item=c["item"]))
        nova["antes"] = _linha(nova["id"])

    def _banco_fora(*_a, **_kw):
        raise RuntimeError("banco fora")

    monkeypatch.setattr(ps, "get_open_finance_connection_by_item_id", _banco_fora)
    amb.ganchos[("depois", c["id"])] = _readota
    _retenta(amb)
    assert _linha(nova["id"]) == nova["antes"]


def test_reconexao_no_meio_recusa_o_run_e_nao_toca_a_linha_nova(user_id, amb):
    c = _nova(user_id, reason="read_failed")
    amb.pluggy.durante_contas = lambda item: _reconecta(user_id, item)
    tick = _retenta(amb)
    linha = _linha(c["id"])
    assert linha["reconnected_at"] is not None and linha["health"] is None
    assert linha["status_reason"] is None and linha["last_sync_at"] < linha["reconnected_at"]
    assert tick["details"]["neutros"] == 1


def test_atualizar_entre_a_listagem_e_a_vez_do_item_poupa_a_chamada(user_id, amb):
    a, b = _nova(user_id, reason="read_failed"), _nova(user_id, reason="read_failed")

    def _atualizou():
        with get_conn() as conn:
            conn.execute("update open_finance_connections set status_reason=null, "
                         "last_sync_at=now(), last_attempt_at=now() where id=%s", (b["id"],))
            conn.commit()

    amb.ganchos[b["id"]] = _atualizou
    _retenta(amb)
    assert (amb.pluggy.contas[a["item"]], amb.pluggy.chamadas[b["item"]]) == (1, 0)


# ── 5. Efeitos ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("com_transacao", [True, False])
def test_um_aviso_por_item_e_agentes_so_com_insercao(user_id, amb, com_transacao):
    conexoes = [_nova(user_id, reason="read_failed") for _ in range(2)]
    amb.pluggy.com_transacao = com_transacao
    _retenta(amb)
    assert amb.avisos[user_id] == len(conexoes)
    assert amb.agentes == ([user_id] * len(conexoes) if com_transacao else [])
