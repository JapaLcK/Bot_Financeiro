"""Plano B do `logo_url`: conexão sem `raw.connector` (as antigas, ou depois de `item/deleted`)
pega o logo do catálogo de conectores da Pluggy, pelo id do conector (`institution_id`), em memória.

Ids reais da produção (consulta do dono, 2026-10-18): 611 BB, 612 Nubank (PAUSED), 651 PicPay,
775 Bradescard (PAUSED), todos numéricos.

CONTROLES (mutação, medida; remeça se mexer):
  negativos:
    tirar o fallback do catálogo (`_read_open_finance_connections`)  → C1, C2, C9
    tirar a guarda `provider == "pluggy"`                            → C6
    o `raw` perder para o catálogo (catálogo primeiro)               → C4
    tirar o `try` de `_aquece_catalogo`                              → C10
    `logo_do_conector` chamar a rede quando o cache está frio        → C3
    `logo_do_conector` respeitar o TTL                               → C3 (ts vencido)
    tirar o `boot` (create_task do lifespan)                         → C13
    `list_pluggy_connectors` direto no loop (sem `to_thread`)        → C14 (os dois)
    `include_sandbox=False` fixo no aquecimento                      → C14
  positivos: C2 (catálogo serve depois do item/deleted), C4 (raw), C8 (cada um vê o seu), C10.
"""
from __future__ import annotations

import asyncio
import threading

import pytest

import core.services.pluggy_catalogo as cat
import core.services.pluggy_sync as ps
import db
import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.of_retentativa as orq
import frontend.routes.open_finance as of_routes
from conftest import promote_to_pro
from core.services.pluggy import PluggyApiError
from db.connection import get_conn

L611 = "https://cdn.pluggy.ai/assets/connector-icons/611.svg"
L612 = "https://cdn.pluggy.ai/assets/connector-icons/612.svg"
L651 = "https://cdn.pluggy.ai/assets/connector-icons/651.svg"
L775 = "https://cdn.pluggy.ai/assets/connector-icons/775.svg"
CATALOGO = [{"id": 611, "imageUrl": L611}, {"id": 612, "imageUrl": L612},
            {"id": 651, "imageUrl": L651}, {"id": "775", "imageUrl": L775}]


@pytest.fixture()
def sem_rede(monkeypatch):
    """Qualquer ida à Pluggy pelo módulo do catálogo reprova o teste."""
    def _proibido(*a, **kw):
        raise AssertionError("o snapshot foi à rede da Pluggy")
    monkeypatch.setattr(cat, "_pluggy_get", _proibido)
    monkeypatch.setattr(cat, "create_pluggy_api_key", _proibido)


def _catalogo(monkeypatch, data, *, sandbox=False, ts=0.0):
    """Cache de processo trocado (e restaurado): `ts=0` = vencido há décadas."""
    monkeypatch.setattr(cat, "_CONNECTORS_CACHE", {sandbox: {"ts": ts, "data": data}})


def _conexao(uid, item="item-a", **item_extra):
    promote_to_pro(uid)
    db.save_pluggy_open_finance_item(uid, {"id": item, "status": "UPDATED", **item_extra})


def _logos(uid) -> list:
    return [c["logo_url"] for c in db.get_open_finance_snapshot(uid)["connections"]]


# ── o logo chega pelo catálogo ───────────────────────────────────────────────

@pytest.mark.parametrize("cid,esperado", [(611, L611), (612, L612), (651, L651), (775, L775)])
def test_c1_sem_connector_no_raw_o_catalogo_da_o_logo(user_id, monkeypatch, sem_rede, cid, esperado):
    _catalogo(monkeypatch, CATALOGO)          # id int no catálogo ("775": texto) × coluna sempre texto
    _conexao(user_id, connectorId=cid)
    assert _logos(user_id) == [esperado]


def test_c1_conexao_pausada_tambem_recebe_o_logo(user_id, monkeypatch, sem_rede):
    _catalogo(monkeypatch, CATALOGO)
    _conexao(user_id, connectorId=612)
    with get_conn() as conn:
        conn.execute("update open_finance_connections set status='PAUSED' where user_id=%s", (user_id,))
        conn.commit()
    (c,) = db.get_open_finance_snapshot(user_id)["connections"]
    assert str(c["status"]).upper() == "PAUSED" and c["logo_url"] == L612


def test_c2_depois_do_item_deleted_o_logo_continua(user_id, monkeypatch, sem_rede):
    _catalogo(monkeypatch, CATALOGO)
    _conexao(user_id, connector={"id": 612, "imageUrl": "https://cdn.pluggy.ai/do-raw.svg"})
    assert _logos(user_id) == ["https://cdn.pluggy.ai/do-raw.svg"]          # fluxo 1: o raw
    db.update_pluggy_open_finance_item_status(
        "item-a", "DELETED", raw={"event": "item/deleted", "itemId": "item-a"})
    assert _logos(user_id) == [L612]                                         # fluxo 2: o catálogo
    _catalogo(monkeypatch, [])                                               # conector fora do catálogo
    assert _logos(user_id) == [None]


def test_c3_cache_frio_vira_none_sem_rede_e_vencido_ainda_serve(user_id, monkeypatch, sem_rede):
    _conexao(user_id, connectorId=612)
    monkeypatch.setattr(cat, "_CONNECTORS_CACHE", {})
    assert _logos(user_id) == [None]
    _catalogo(monkeypatch, CATALOGO, ts=1.0)                                 # TTL de 6 h vencido
    assert _logos(user_id) == [L612]


def test_c4_o_raw_vence_o_catalogo(user_id, monkeypatch, sem_rede):
    _catalogo(monkeypatch, CATALOGO)
    do_raw = "https://cdn.pluggy.ai/do-raw.svg"
    _conexao(user_id, connector={"id": 612, "imageUrl": do_raw})
    assert _logos(user_id) == [do_raw]


@pytest.mark.parametrize("ruim", ["javascript:alert(1)", "http://cdn.pluggy.ai/a.svg",
                                  "https://cdn.pluggy.ai/a b.svg", None, 123, ""])
def test_c5_catalogo_com_url_invalida_vira_none(user_id, monkeypatch, sem_rede, ruim):
    _catalogo(monkeypatch, [{"id": 612, "imageUrl": ruim}])
    _conexao(user_id, connectorId=612)
    assert _logos(user_id) == [None]


def test_c6_mock_pluggy_nao_casa_com_o_catalogo(user_id, monkeypatch, sem_rede):
    db.create_mock_open_finance_connection(user_id)
    (c,) = db.get_open_finance_snapshot(user_id)["connections"]
    assert c["provider"] == "mock_pluggy"
    _catalogo(monkeypatch, [{"id": c["institution_id"], "imageUrl": L612}, *CATALOGO])
    assert _logos(user_id) == [None]


def test_c7_sem_connector_nem_connector_id_fica_none(user_id, monkeypatch, sem_rede):
    _catalogo(monkeypatch, CATALOGO)
    _conexao(user_id)                                # institution_id = "pluggy"
    (c,) = db.get_open_finance_snapshot(user_id)["connections"]
    assert c["institution_id"] == "pluggy" and c["logo_url"] is None


def test_c8_cada_usuario_ve_so_o_seu(user_id, monkeypatch, sem_rede):
    outro = user_id + 1
    db.ensure_user(outro)
    try:
        _catalogo(monkeypatch, CATALOGO + [{"id": 999, "imageUrl": "https://cdn.pluggy.ai/999.svg"}])
        _conexao(user_id, "item-a", connectorId=612)
        _conexao(outro, "item-b", connectorId=999)
        assert _logos(user_id) == [L612]
        assert _logos(outro) == ["https://cdn.pluggy.ai/999.svg"]
    finally:
        from conftest import _cleanup_user
        _cleanup_user(outro)


def test_c9_catalogo_so_na_chave_sandbox_tambem_acha(user_id, monkeypatch, sem_rede):
    _catalogo(monkeypatch, CATALOGO, sandbox=True)
    _conexao(user_id, connectorId=612)
    assert _logos(user_id) == [L612]


def test_c12_o_catalogo_nunca_casa_por_nome(user_id, monkeypatch, sem_rede):
    _catalogo(monkeypatch, [{"id": 1, "name": "Nubank", "imageUrl": L612}])
    _conexao(user_id, connectorId=612, connector={"name": "Nubank"})
    assert _logos(user_id) == [None]


# ── o aquecimento do tique e do boot ─────────────────────────────────────────

def _tique(monkeypatch, ordem: list):
    monkeypatch.setattr(ps, "run_of_health_check", lambda **kw: ordem.append("saude") or {})

    async def _retentativa(**kw):
        ordem.append("retentativa")
        return {}
    monkeypatch.setattr(orq, "retentar_leituras", _retentativa)
    asyncio.run(dashboard._open_finance_tick(interval=10, com_patch=False))


def test_c10_o_tique_aquece_com_a_chave_do_modal_antes_da_saude(monkeypatch):
    ordem: list = []

    def _lista(api_key=None, *, include_sandbox=False):
        ordem.append(("catalogo", api_key, include_sandbox))
        return []
    monkeypatch.setattr(cat, "list_pluggy_connectors", _lista)
    _tique(monkeypatch, ordem)
    assert ordem == [("catalogo", None, of_routes.PLUGGY_INCLUDE_SANDBOX), "saude", "retentativa"]


def test_c10_aquecimento_que_levanta_nao_segura_saude_nem_retentativa(monkeypatch, capsys):
    def _cai(api_key=None, *, include_sandbox=False):
        raise PluggyApiError("segredo-do-usuario-4242", status_code=502)
    monkeypatch.setattr(cat, "list_pluggy_connectors", _cai)
    ordem: list = []
    _tique(monkeypatch, ordem)
    assert ordem == ["saude", "retentativa"]
    err = capsys.readouterr().err
    assert "PluggyApiError" in err and "segredo-do-usuario-4242" not in err


@pytest.mark.parametrize("sandbox", [True, False])
def test_c14_aquecimento_usa_a_constante_do_modal_e_fora_do_loop(monkeypatch, sandbox):
    """`_aquece_catalogo` (a mesma função do tique e da tarefa do boot): a chamada à Pluggy é bloqueante
    (até ~40 s), então roda em THREAD, nunca no loop; e `include_sandbox` é o `PLUGGY_INCLUDE_SANDBOX`
    lido na hora (não um valor fixo)."""
    monkeypatch.setattr(of_routes, "PLUGGY_INCLUDE_SANDBOX", sandbox)
    visto: dict = {}

    def _lista(api_key=None, *, include_sandbox=False):
        visto.update(thread=threading.get_ident(), sandbox=include_sandbox)
        return []
    monkeypatch.setattr(cat, "list_pluggy_connectors", _lista)
    asyncio.run(dashboard._aquece_catalogo())
    assert visto["sandbox"] is sandbox
    assert visto["thread"] != threading.get_ident(), "o aquecimento bloqueou o event loop"


def test_c14_o_tique_tambem_aquece_fora_do_loop_e_com_a_constante(monkeypatch):
    monkeypatch.setattr(of_routes, "PLUGGY_INCLUDE_SANDBOX", True)
    visto: dict = {}

    def _lista(api_key=None, *, include_sandbox=False):
        visto.update(thread=threading.get_ident(), sandbox=include_sandbox)
        return []
    monkeypatch.setattr(cat, "list_pluggy_connectors", _lista)
    _tique(monkeypatch, [])
    assert visto["sandbox"] is True and visto["thread"] != threading.get_ident()


def test_c13_o_boot_aquece_o_catalogo_uma_vez():
    from _lifespan_probe import sondar
    resultado, diag = sondar({"catalogo": "core.services.pluggy_catalogo:list_pluggy_connectors"})
    assert resultado, f"subprocesso não chegou ao fim:\n{diag}"
    assert resultado["catalogo"] is True, diag
