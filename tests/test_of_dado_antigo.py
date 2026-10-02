"""Onda 5, PR-B3: dado antigo na tela (D2 = Pluggy à frente, D7 = "dados de dd/mm").

Contrato e tabela de estados: `docs/open_finance_estados.md` §1 item 7, §3.

Pelo caminho real: Postgres real, Pluggy mockada nas funções HTTP, a tela lida
pela rota `GET /open-finance/{uid}`, o toast pelo `sync.items[]` do
`POST /refresh` e o job de saúde pelo `run_of_health_check` (que só faz
`GET /items`). Sem `relogio_fixo`: `last_sync_at` vai por SQL (`now() - interval`)
e as datas da Pluggy são relativas ao relógio real, em UTC.

CONTROLES, medidos em 2026-10-01 (mutação numa CÓPIA de
`core/services/pluggy_health.py`, restaurada e conferida com `cmp`; remeça se
mexer no código). Cada linha é a mutação e o que ficou vermelho:

  D2 negativo: `pluggy_a_frente` sempre False (desliga o elif da D2)
      → R6 nos dois sabores, toast da D2, patrimônio, isolamento, Atualizar-limpa
        e a pré-condição (a tela ainda é "Parcial") de cada caso de precedência.
  D2 negativo de Y: tirar `last_updated_at` do item no `derive_item_health`
      → só R6 "so_item" (e o toast/patrimônio/isolamento, que usam esse sabor).
  D2 positivo: empate, 1 h atrás, data ilegível/sem fuso/ausente → "Atualizado".
      Mutação "partial sempre que há health" (`pluggy_a_frente = True`) os
      deixa vermelhos: prova que o positivo discrimina conserto restritivo demais.
  D2 ordem: mover o elif da D2 para antes do `coletando_sem_info` → esse caso
      vermelho; para antes do motivo de leitura (primeiro `if` do `out()`) →
      read_failed, investments_read_failed e no_accounts vermelhos. Tirar o
      `not sem_sync` da âncora → só o "sem_sync" da D7 vermelho; mover o elif da
      D2 para antes do `sem_sync` → só `coletando_sem_info` vermelho (a D2 passa
      na frente dele também). A guarda `not sem_sync` da âncora e a ordem do elif
      protegem a D2 do `sem_sync` em redundância; só a da âncora protege a D7.
  Rodada 2 (Tester):
  intervalo: tirar `OverflowError` do `except` de `data_da_pluggy` (ou o
      `astimezone` de validação) → todo o grupo `test_data_fora_do_intervalo_*`
      vermelho (500 na rota, no /refresh e no patrimônio).
  futuro: tirar o `if data <= limite` → `test_data_no_futuro_*` vermelho (tela
      "Parcial" para sempre; retentativa elegível).
  corrida do relógio: tirar o `and data_pluggy` do elif da D2 →
      `test_relogio_lido_duas_vezes_nao_derruba_a_tela` vermelho (AttributeError);
      o positivo é o R6 normal, que segue Parcial.
  positivos: "descartar tudo" (`data_da_pluggy` devolvendo None) → R6, C1 e o
      caso de +2 min dentro da tolerância vermelhos.
  D7 negativo: `dados_de` sempre None → C1 e limiar de 24 h+1 min vermelhos;
      trocar `day_tz` por fatia da string (`_dm`) → caso de fuso vermelho;
      `>` por `>=` no limiar → o caso das 24 h exatas vermelho.
  D7 positivo: sync normal (data minutos antes) → None.
  Contrato de `ui`: `test_ui_tem_so_as_chaves_do_contrato` prende a chave nova.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import core.services.pluggy_sync as ps
import db
from conftest import promote_to_pro
from core.services.of_retentativa import classe_de_retentativa
from db.connection import get_conn
from tests._patrimonio_helpers import foto
from test_of_connection_state import (
    ITEM_SAUDAVEL, _conexao, _conta_pluggy, _linha, _mock_pluggy, _tx_pluggy)
from test_of_leitura_incompleta import _refresh, _tique_de_saude, _ui_pela_rota
from utils_date import _tz

ITEM = "item-g1"
FRENTE = "— atualize para trazer"


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _dd_mm(dt: datetime) -> str:
    return dt.astimezone(_tz()).strftime("%d/%m")


def _item(lu, *, produtos: bool = False, status: str = "UPDATED", exec_status: str = "SUCCESS",
          credito_atrasado: bool = False) -> dict:
    """Item da Pluggy. `lu` é o `lastUpdatedAt` do ITEM (o caso real: SUCCESS sem
    statusDetail); `produtos=True` manda também o statusDetail com a mesma data."""
    item = {k: v for k, v in ITEM_SAUDAVEL.items() if k != "statusDetail"}
    item.update(status=status, executionStatus=exec_status,
                lastUpdatedAt=lu if isinstance(lu, str) or lu is None else _z(lu))
    detalhe = {}
    if produtos:
        detalhe["accounts"] = {"isUpdated": True, "lastUpdatedAt": item["lastUpdatedAt"],
                               "warnings": []}
    if credito_atrasado:
        detalhe["creditCards"] = {"isUpdated": False, "lastUpdatedAt": item["lastUpdatedAt"],
                                  "warnings": []}
    if detalhe:
        item["statusDetail"] = detalhe
    return item


def _pluggy(monkeypatch, item) -> None:
    _mock_pluggy(monkeypatch, item=item, contas=[_conta_pluggy()], txs=[_tx_pluggy()])


def _sync_ha(horas: float, *, item: str = ITEM) -> None:
    """Põe o último sync (e a tentativa, e a autorização) `horas` atrás, por SQL."""
    with get_conn() as c:
        c.execute(
            "update open_finance_connections set last_sync_at = now() - %s, "
            "last_attempt_at = now() - %s, reconnected_at = null, "
            "created_at = now() - interval '60 days' where provider_item_id = %s",
            (timedelta(hours=horas), timedelta(hours=horas), item))
        c.commit()


def _monta(uid: int, monkeypatch, item: dict, *, sync_ha: float = 240) -> None:
    """Conexão com contas espelhadas (sync real), sync recuado por SQL e o job de
    saúde medindo `item` — o caminho que grava o `health` de verdade."""
    promote_to_pro(uid)
    _conexao(uid)
    _pluggy(monkeypatch, ITEM_SAUDAVEL)
    assert ps.sync_pluggy_item(ITEM)["ok"] is True
    _sync_ha(sync_ha)
    _pluggy(monkeypatch, item)
    _tique_de_saude()
    assert _linha()["health"]["item_status"] == item["status"]


def _estado(uid: int) -> tuple[str, str | None]:
    ui = _ui_pela_rota(uid)
    return ui["state"], ui["detail"]


# ── D2 ────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("sabor", ["com_status_detail", "so_item"])
def test_R6_pluggy_a_frente_do_sync_e_parcial_ambar(user_id, monkeypatch, sabor):
    """R6: sync de 10 dias atrás, a Pluggy coletou há 1 h. O sabor `so_item` é o
    caso real (SUCCESS sem statusDetail): a data só existe no `lastUpdatedAt` do item."""
    data = _agora() - timedelta(hours=1)
    if sabor == "com_status_detail":
        item = _item(None, produtos=True)
        item["statusDetail"]["accounts"]["lastUpdatedAt"] = _z(data)
    else:
        item = _item(data)
    _monta(user_id, monkeypatch, item)

    ui = _ui_pela_rota(user_id)

    assert (ui["state"], ui["label"]) == ("partial", "Parcial")
    assert ui["detail"] == f"O banco já tem dados de {_dd_mm(data)} {FRENTE}"
    assert ui["dados_de"] is None, "à frente é a D2; a D7 é o outro lado"


@pytest.mark.parametrize("delta", [timedelta(0), timedelta(hours=-1)], ids=["empate", "1h_atras"])
def test_positivo_empate_e_dado_atras_continuam_atualizado(user_id, monkeypatch, delta):
    _monta(user_id, monkeypatch, _item(_agora()))        # só para existir a linha
    ls = _linha()["last_sync_at"]
    _pluggy(monkeypatch, _item((ls + delta).isoformat()))   # microssegundos: o empate é exato
    _tique_de_saude()

    assert _estado(user_id) == ("updated", None)


@pytest.mark.parametrize("lu", ["lixo", "2099-01-01T00:00:00", None],
                         ids=["ilegivel", "sem_fuso", "ausente"])
def test_sem_prova_nao_ha_a_frente(user_id, monkeypatch, lu):
    _monta(user_id, monkeypatch, _item(lu))

    ui = _ui_pela_rota(user_id)

    assert (ui["state"], ui["detail"], ui["dados_de"]) == ("updated", None, None)


def test_last_sync_at_sem_fuso_nao_estoura(monkeypatch):
    """Guarda do predicado e da âncora: instante sem fuso = sem D2/D7, sem exceção."""
    from core.services.pluggy_health import connection_ui_state, derive_item_health
    health = derive_item_health(_item(_agora()))
    ui = connection_ui_state({"status": "UPDATED", "health": health,
                              "last_sync_at": datetime(2020, 1, 1)})
    assert (ui["state"], ui["dados_de"]) == ("updated", None)


def _sql(sql: str, *params) -> None:
    with get_conn() as c:
        c.execute(sql, params)
        c.commit()


def _pluggy_e_tique(monkeypatch, item: dict) -> None:
    _pluggy(monkeypatch, item)
    _tique_de_saude()


# nome -> (preparo depois do tique, o que a tela tem de dizer com a Pluggy à frente)
PRECEDENCIA = {
    "read_failed": (lambda mp: _sql("update open_finance_connections set status_reason='read_failed'"
                                 " where provider_item_id=%s", ITEM),
                    lambda s, d: s == "error_recoverable"),
    "investments_read_failed": (
        lambda mp: _sql("update open_finance_connections set status_reason='investments_read_failed'"
                     " where provider_item_id=%s", ITEM),
        lambda s, d: (s, d) == ("partial", "Investimentos não vieram nesta atualização")),
    "no_accounts": (lambda mp: _sql("update open_finance_connections set status_reason='no_accounts'"
                                 " where provider_item_id=%s", ITEM),
                    lambda s, d: s == "no_accounts"),
    "item_missing": (lambda mp: _sql("update open_finance_connections set status_reason='item_missing'"
                                  " where provider_item_id=%s", ITEM),
                     lambda s, d: s == "item_missing"),
    "coletando_sem_info": (
        lambda mp: _pluggy_e_tique(mp, _item(_agora() - timedelta(hours=1), status="UPDATING",
                                      exec_status="UPDATING")),
        lambda s, d: (s, d) == ("updating", None)),
    "reconexao_sem_sync": (
        lambda mp: _sql("update open_finance_connections set reconnected_at = now()"
                     " where provider_item_id=%s", ITEM),
        lambda s, d: (s, d) == ("updating", "Ainda não sincronizou")),
    "reconexao_coleta_vencida": (
        lambda mp: _sql("update open_finance_connections set reconnected_at = now() - interval '3 hours'"
                     " where provider_item_id=%s", ITEM),
        lambda s, d: (s, d) == ("updating", "Está demorando mais que o normal — atualize de novo")),
}


@pytest.mark.parametrize("nome", sorted(PRECEDENCIA))
def test_a_d2_so_substitui_o_verde(user_id, monkeypatch, nome):
    """Com a Pluggy à frente, motivo de leitura, reconexão sem sync e coleta vencida
    continuam falando: a D2 só troca o "Atualizado"."""
    _monta(user_id, monkeypatch, _item(_agora() - timedelta(hours=1)))
    assert _estado(user_id)[0] == "partial", "pré-condição: sem o preparo, é a D2"
    preparo, esperado = PRECEDENCIA[nome]

    preparo(monkeypatch)

    estado, detalhe = _estado(user_id)
    assert esperado(estado, detalhe), (nome, estado, detalhe)
    assert FRENTE not in (detalhe or "")


@pytest.mark.parametrize("item_kw, esperado", [
    ({"status": "LOGIN_ERROR", "exec_status": "ERROR"}, "needs_user_action"),
    ({"status": "ERROR", "exec_status": "ERROR"}, "error_recoverable"),
    ({"credito_atrasado": True, "exec_status": "PARTIAL_SUCCESS"}, "partial"),
], ids=["login_error", "item_error_E13", "partial_da_pluggy"])
def test_a_d2_nao_fala_sobre_item_com_problema(user_id, monkeypatch, item_kw, esperado):
    _monta(user_id, monkeypatch, _item(_agora() - timedelta(hours=1), **item_kw))

    estado, detalhe = _estado(user_id)

    assert estado == esperado and FRENTE not in (detalhe or "")
    if esperado == "partial":
        assert "desatualizado" in detalhe, "P1: o partial da Pluggy mantém o detalhe dele"


def test_atualizar_limpa_a_d2(user_id, monkeypatch):
    _monta(user_id, monkeypatch, _item(_agora() - timedelta(hours=1)))
    assert _estado(user_id)[0] == "partial"

    sync = _refresh(user_id, monkeypatch)

    assert sync["items"][0]["state"] == "updated"
    assert _estado(user_id) == ("updated", None)


def test_toast_da_d2_quando_o_sync_do_atualizar_nao_carimbou(user_id, monkeypatch):
    """Lock do item ocupado: o Atualizar devolve `sync_in_progress`, o last_sync_at
    não anda e o item do relatório é o partial da D2 (o texto vai no `detail`)."""
    data = _agora() - timedelta(hours=1)
    _monta(user_id, monkeypatch, _item(data))
    monkeypatch.setenv("OF_SYNC_LOCK_WAIT_MS", "50")

    with db.pluggy_item_lock(ITEM) as locked:
        assert locked
        sync = _refresh(user_id, monkeypatch)

    item = sync["items"][0]
    assert (item["state"], item["detail"]) == (
        "partial", f"O banco já tem dados de {_dd_mm(data)} {FRENTE}")
    assert sync["ok"] is False


def test_retentativa_continua_lendo_a_linha_a_frente(user_id, monkeypatch):
    _monta(user_id, monkeypatch, _item(_agora() - timedelta(hours=1)))
    assert classe_de_retentativa(_linha()) == "pluggy_a_frente"


def test_retentativa_nao_le_a_linha_com_dado_atras(user_id, monkeypatch):
    _monta(user_id, monkeypatch, _item(_agora() - timedelta(hours=1)), sync_ha=1)
    # recuo o dado para antes do sync e da tentativa (ambos há 1 h)
    _pluggy(monkeypatch, _item(_agora() - timedelta(hours=3)))
    _tique_de_saude()
    assert classe_de_retentativa(_linha()) is None


def test_patrimonio_aceita_banco_desatualizado_com_a_pluggy_a_frente(user_id, monkeypatch):
    """(c4) aceito: o dado do banco que somamos é mais velho que o da Pluggy."""
    _monta(user_id, monkeypatch, _item(_agora() - timedelta(minutes=10)), sync_ha=1)
    assert _estado(user_id)[0] == "partial"
    assert "banco_desatualizado" in foto(user_id)["motivos"]

    _pluggy(monkeypatch, _item(_agora() - timedelta(hours=3)))
    _tique_de_saude()
    assert _estado(user_id)[0] == "updated"
    assert "banco_desatualizado" not in foto(user_id)["motivos"]


def test_isolamento_entre_usuarios(user_id, monkeypatch):
    outro = user_id + 1
    db.ensure_user(outro)
    try:
        # A: em dia (sem item à frente). B: R6, em outro item.
        promote_to_pro(user_id)
        promote_to_pro(outro)
        _conexao(user_id, "item-a")
        _conexao(outro, "item-b")
        _pluggy(monkeypatch, ITEM_SAUDAVEL)
        for item in ("item-a", "item-b"):
            assert ps.sync_pluggy_item(item)["ok"] is True
        _sql("update open_finance_connections set last_sync_at = now() - interval '10 days',"
             " reconnected_at = null where provider_item_id = 'item-b'")
        _mock_pluggy(monkeypatch, item=_item(_agora() - timedelta(hours=1)),
                     contas=[_conta_pluggy()], txs=[_tx_pluggy()])
        _tique_de_saude("item-a")
        _tique_de_saude("item-b")
        _sql("update open_finance_connections set last_sync_at = now() where provider_item_id = 'item-a'")

        assert _ui_pela_rota(user_id, "item-a")["state"] == "updated"
        assert _ui_pela_rota(outro, "item-b")["state"] == "partial"
        assert [i["item_id"] for i in _refresh(user_id, monkeypatch)["items"]] == ["item-a"]
    finally:
        from conftest import _cleanup_user
        _cleanup_user(outro)


# ── D7 ────────────────────────────────────────────────────────────────────────

def test_C1_login_error_com_data_antiga_mostra_dados_de(user_id, monkeypatch):
    """C1: o sync real leu a Pluggy agora e o banco está parado há 7 dias."""
    data = _agora() - timedelta(days=7)
    promote_to_pro(user_id)
    _conexao(user_id)
    _pluggy(monkeypatch, _item(data, status="LOGIN_ERROR", exec_status="ERROR"))
    ps.sync_pluggy_item(ITEM)
    _sync_ha(1)
    _tique_de_saude()

    ui = _ui_pela_rota(user_id)

    assert ui["state"] == "needs_user_action"
    assert ui["dados_de"] == _dd_mm(data)


def test_limiar_de_24_horas_e_estrito(user_id, monkeypatch):
    _monta(user_id, monkeypatch, _item(_agora() - timedelta(minutes=5)), sync_ha=1)
    ls = _linha()["last_sync_at"]
    # positivo: sync normal (dado minutos antes) -> nada
    assert _ui_pela_rota(user_id)["dados_de"] is None
    for atraso, esperado in ((timedelta(hours=24), None),
                             (timedelta(hours=24, minutes=1), _dd_mm(ls - timedelta(hours=24, minutes=1)))):
        _pluggy(monkeypatch, _item((ls - atraso).isoformat()))
        _tique_de_saude()
        assert _ui_pela_rota(user_id)["dados_de"] == esperado, atraso


def test_dados_de_usa_o_fuso_do_app(user_id, monkeypatch):
    """01:30Z é 22:30 do dia ANTERIOR em America/Sao_Paulo; fatiar a string erraria."""
    data = (_agora() - timedelta(days=5)).replace(hour=1, minute=30, second=0, microsecond=0)
    assert _dd_mm(data) != data.strftime("%d/%m")
    _monta(user_id, monkeypatch, _item(data), sync_ha=1)

    assert _ui_pela_rota(user_id)["dados_de"] == _dd_mm(data)


@pytest.mark.parametrize("quando", ["sem_sync", "sem_health", "last_sync_nulo"])
def test_sem_ancora_ou_sem_dado_nao_ha_sufixo(user_id, monkeypatch, quando):
    _monta(user_id, monkeypatch, _item(_agora() - timedelta(days=20)), sync_ha=1)
    assert _ui_pela_rota(user_id)["dados_de"] is not None, "pré-condição"
    sql = {"sem_sync": "reconnected_at = now()", "sem_health": "health = null",
           "last_sync_nulo": "last_sync_at = null"}[quando]

    _sql(f"update open_finance_connections set {sql} where provider_item_id=%s", ITEM)

    assert _ui_pela_rota(user_id)["dados_de"] is None


def test_ui_tem_so_as_chaves_do_contrato(user_id, monkeypatch):
    """Mudança consciente de contrato: `ui` ganha `dados_de` (e só ela)."""
    _monta(user_id, monkeypatch, _item(_agora()), sync_ha=1)
    assert set(_ui_pela_rota(user_id)) == {"state", "label", "detail", "stale_products", "dados_de"}


# ── Rodada 2: data fora do intervalo e data no futuro ─────────────────────────

FORA_DO_INTERVALO = ["0001-01-01T00:00:00Z", "9999-12-31T23:59:59-05:00"]


@pytest.mark.parametrize("bruta", FORA_DO_INTERVALO)
def test_data_fora_do_intervalo_e_ignorada_na_funcao(bruta):
    from core.services.pluggy_health import (
        connection_ui_state, data_da_pluggy, derive_item_health, pluggy_tem_dado_depois_de)
    health = derive_item_health(_item(bruta, produtos=True))
    ls = _agora() - timedelta(days=10)

    assert data_da_pluggy(health) is None
    assert pluggy_tem_dado_depois_de(health, ls) is False
    ui = connection_ui_state({"status": "UPDATED", "health": health, "last_sync_at": ls})
    assert (ui["state"], ui["dados_de"]) == ("updated", None)


@pytest.mark.parametrize("bruta", FORA_DO_INTERVALO)
def test_data_fora_do_intervalo_nao_derruba_rota_refresh_nem_patrimonio(user_id, monkeypatch, bruta):
    _monta(user_id, monkeypatch, _item(bruta), sync_ha=1)

    ui = _ui_pela_rota(user_id)                       # GET: 200
    assert (ui["state"], ui["dados_de"]) == ("updated", None)
    assert _refresh(user_id, monkeypatch)["items"]    # POST /refresh: 200
    assert "motivos" in foto(user_id)                 # patrimônio não levanta


def test_data_no_futuro_alem_da_tolerancia_e_descartada(user_id, monkeypatch):
    """+2 dias (relógio adiantado, ou `lastUpdatedAt` = fim previsto): sem prova de
    dado novo. Sem o descarte o card ficava Parcial para sempre e a retentativa
    relia a cada tique."""
    from core.services.of_retentativa import elegiveis
    from db.open_finance_state import list_connections_para_retentar
    _monta(user_id, monkeypatch, _item(_agora() + timedelta(days=2)))

    assert _estado(user_id) == ("updated", None)
    linha = _linha()
    assert linha["id"] not in {r["id"] for r, _c in elegiveis(list_connections_para_retentar())}
    assert classe_de_retentativa(linha) is None


def test_positivo_data_dentro_da_tolerancia_ainda_vale(user_id, monkeypatch):
    """+2 min está dentro dos 5 min de tolerância: continua sendo a Pluggy à frente."""
    _monta(user_id, monkeypatch, _item(_agora() + timedelta(minutes=2)))

    assert _estado(user_id)[0] == "partial"
    assert classe_de_retentativa(_linha()) == "pluggy_a_frente"


def test_caracteriza_skew_de_0_a_5_min_ainda_e_a_frente(user_id, monkeypatch):
    """CARACTERIZAÇÃO do limite conhecido, não aprovação: `lastUpdatedAt` = sync + 3
    min (skew do relógio da Pluggy dentro da tolerância) ainda é "à frente": tela
    Parcial com o texto da D2 e classe `pluggy_a_frente`. Decisão pendente do dono:
    se mandar exigir `data > âncora + tolerância`, troque as asserções deste caso
    (a regra mora em `pluggy_tem_dado_depois_de`; não mude só a tela)."""
    _monta(user_id, monkeypatch, _item(_agora()), sync_ha=1)
    ls = _linha()["last_sync_at"]
    data = ls + timedelta(minutes=3)
    _pluggy(monkeypatch, _item(data.isoformat()))
    _tique_de_saude()

    ui = _ui_pela_rota(user_id)

    assert (ui["state"], ui["detail"]) == (
        "partial", f"O banco já tem dados de {_dd_mm(data)} {FRENTE}")
    assert classe_de_retentativa(_linha()) == "pluggy_a_frente"


def test_relogio_lido_duas_vezes_nao_derruba_a_tela(monkeypatch):
    """`data_da_pluggy` (direto) e o predicado leem o relógio em separado. Se a
    1ª leitura descarta a data (futuro > 5 min) e a 2ª, 1 min depois, a aceita, o
    predicado diz "à frente" com `data_pluggy` None: a D2 não pode estourar."""
    import core.services.pluggy_health as ph
    real = datetime.now(timezone.utc)
    chamadas = []

    class Relogio(datetime):
        @classmethod
        def now(cls, tz=None):
            chamadas.append(1)
            return real + timedelta(minutes=1 if len(chamadas) > 1 else 0)

    monkeypatch.setattr(ph, "datetime", Relogio)
    health = {"item_status": "UPDATED", "products": {},
              "last_updated_at": (real + timedelta(minutes=5, seconds=30)).isoformat()}

    ui = ph.connection_ui_state({"status": "UPDATED", "health": health,
                                 "last_sync_at": real - timedelta(days=10)})

    assert len(chamadas) == 2, "pré-condição: o relógio foi lido duas vezes"
    assert ui["state"] == "updated" and ui["dados_de"] is None
