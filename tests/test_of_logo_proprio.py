"""Logo PRÓPRIO dos 10 bancos principais (arquivo em frontend/brand/bancos/<ispb>.png).

Precedência no cliente: próprio > `logo_url` da Pluggy > inicial. O servidor só ACRESCENTA `logo_proprio`
(caminho relativo ou null) ao snapshot e ao `/connectors`; `logo_url` e `logo` não mudam.

CONTROLES (mutação, medida; remeça se mexer):
  negativos:
    tirar `logo_proprio` de `_read_open_finance_connections`  → test_snapshot_*, test_cada_usuario_*
    apagar um PNG de frontend/brand/bancos/                   → test_todo_ispb_do_mapa_tem_arquivo_e_a_rota_serve
    tirar `logo_proprio` do `/connectors`                     → test_connectors_traz_a_chave
  positivos: 775/618 (fora da tabela) seguem com o `logo_url` da Pluggy; 611 e 219/619 casam.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import core.services.pluggy_catalogo as cat
import db
import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.open_finance as of_routes
from conftest import promote_to_pro
from db.connection import get_conn

L611 = "https://cdn.pluggy.ai/assets/connector-icons/611.svg"
L775 = "https://cdn.pluggy.ai/assets/connector-icons/775.svg"
CATALOGO = [{"id": 611, "imageUrl": L611}, {"id": "775", "imageUrl": L775}]


def _conexao(uid, connector_id, item="item-a"):
    promote_to_pro(uid)
    db.save_pluggy_open_finance_item(uid, {"id": item, "status": "UPDATED", "connectorId": connector_id})


def _con(uid):
    return db.get_open_finance_snapshot(uid)["connections"]


@pytest.mark.parametrize("cid,ispb", [
    ("601", "60701190"), ("603", "60746948"), ("611", "00000000"), ("608", "90400888"),
    ("619", "00360305"), ("219", "00360305"), ("823", "00416968"), ("215", "00416968"),
    ("626", "31872495"), ("612", "18236120"),
    ("675", "30306294"), ("614", "30306294"), ("214", "30306294"), ("606", "10573521"), ("206", "10573521"),
])
def test_id_conhecido_da_o_caminho(cid, ispb):
    assert cat.logo_proprio(cid) == f"/brand/bancos/{ispb}.png"


@pytest.mark.parametrize("a,b", [("219", "619"), ("215", "823"), ("206", "606"), ("214", "675"), ("614", "675")])
def test_variantes_do_mesmo_banco_dao_o_mesmo_arquivo(a, b):
    assert cat.logo_proprio(a) == cat.logo_proprio(b) is not None


@pytest.mark.parametrize("fora", ["775", "618", "218", "609", "664", "282", "230", "999999", "", " 611", "611 ", None, 611, 1.5, [], {}])
def test_id_fora_da_tabela_ou_nao_texto_e_none_sem_excecao(fora):
    assert cat.logo_proprio(fora) is None


def test_snapshot_traz_o_proprio_e_o_logo_url_da_pluggy_nao_muda(user_id, monkeypatch):
    monkeypatch.setattr(cat, "_CONNECTORS_CACHE", {False: {"ts": 0.0, "data": CATALOGO}})
    _conexao(user_id, 611)
    (c,) = _con(user_id)
    assert c["logo_proprio"] == "/brand/bancos/00000000.png" and c["logo_url"] == L611


def test_snapshot_fora_da_tabela_fica_null_e_o_logo_da_pluggy_segue(user_id, monkeypatch):
    """Controle positivo: a regra não recusa tudo. Bradescard (775) está fora de propósito."""
    monkeypatch.setattr(cat, "_CONNECTORS_CACHE", {False: {"ts": 0.0, "data": CATALOGO}})
    _conexao(user_id, 775)
    (c,) = _con(user_id)
    assert c["logo_proprio"] is None and c["logo_url"] == L775


def test_snapshot_mock_pluggy_e_null(user_id):
    db.create_mock_open_finance_connection(user_id)
    (c,) = _con(user_id)
    assert c["provider"] == "mock_pluggy" and c["logo_proprio"] is None


def test_provider_mock_com_id_do_mapa_e_null_e_pluggy_da_o_caminho(user_id):
    """Mede a condição `provider == "pluggy"`: mesma linha, mesmo institution_id=611, só o provider muda."""
    db.create_mock_open_finance_connection(user_id)
    (c,) = _con(user_id)
    for provider, esperado in (("mock_pluggy", None), ("pluggy", "/brand/bancos/00000000.png")):
        with get_conn() as cn:
            with cn.cursor() as cur:
                cur.execute("update open_finance_connections set institution_id='611', provider=%s where id=%s", (provider, c["id"]))
            cn.commit()
        assert _con(user_id)[0]["logo_proprio"] == esperado, provider


def test_caminho_do_mapa_casa_com_a_regex_do_cliente():
    """Paridade: a regex `LOGO_PROPRIO` do /painel (api.ts) tem de aceitar toda saída do servidor."""
    ts = (Path(__file__).parent.parent / "webapp/src/dashboard/conta/api.ts").read_text()
    literal = re.search(r"LOGO_PROPRIO\s*=\s*/(.*)/;", ts).group(1)
    for cid in cat._LOGO_PROPRIO_ISPB:
        assert re.search(literal, cat.logo_proprio(cid)), (cid, literal)


def test_cada_usuario_ve_so_o_seu(user_id):
    outro = user_id + 1
    db.ensure_user(outro)
    try:
        _conexao(user_id, 611, "item-a")
        _conexao(outro, 775, "item-b")
        assert [c["logo_proprio"] for c in _con(user_id)] == ["/brand/bancos/00000000.png"]
        assert [c["logo_proprio"] for c in _con(outro)] == [None]
    finally:
        from conftest import _cleanup_user
        _cleanup_user(outro)


def test_connectors_traz_a_chave(monkeypatch):
    catalogo = [{"id": 611, "name": "Banco do Brasil", "type": "PERSONAL_BANK", "imageUrl": L611},
                {"id": 775, "name": "Bradescard", "type": "PERSONAL_BANK", "imageUrl": L775}]
    monkeypatch.setattr(of_routes, "list_pluggy_connectors", lambda *a, **k: catalogo)
    monkeypatch.setattr(of_routes.shared, "authorize_dashboard_access", lambda *a, **k: None)
    out = asyncio.run(of_routes.open_finance_connectors_route(None, 1))
    por_id = {b["id"]: b for b in out["connectors"]}
    assert por_id[611]["logo_proprio"] == "/brand/bancos/00000000.png" and por_id[611]["logo"] == L611
    assert por_id[775]["logo_proprio"] is None and por_id[775]["logo"] == L775


def test_todo_ispb_do_mapa_tem_arquivo_e_a_rota_serve():
    client = TestClient(dashboard.app)
    ispbs = set(cat._LOGO_PROPRIO_ISPB.values())
    assert len(ispbs) == 10
    for ispb in sorted(ispbs):
        r = client.get(f"/brand/bancos/{ispb}.png")
        assert r.status_code == 200 and r.headers["content-type"] == "image/png", ispb
        assert r.content[:8] == b"\x89PNG\r\n\x1a\n" and "immutable" in r.headers["cache-control"], ispb
