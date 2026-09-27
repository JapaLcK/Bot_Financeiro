"""PR-E — o diagnóstico do operador lido depois de CONVERSAS reais pelas rotas.

Rotas de verdade (POST /pluggy-item, DELETE /open-finance, webhook) com banco
REAL; a Pluggy é dublada. A pergunta: o estado que o fluxo deixou no banco é o
que o operador vê, e o `--apagar` fecha a marca que o fluxo abriu.
"""
from __future__ import annotations

import asyncio

import psycopg
import pytest
from fastapi.testclient import TestClient

import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.open_finance as of_routes
from conftest import promote_to_pro
from core.services.pluggy import PluggyApiError
from db import open_finance_diagnostico as diag
from db.connection import get_conn
from scripts import of_itens_operador as op
from test_of_item_ownership import _auth, _webhook, eventos  # noqa: F401
from test_of_webhook_adopt_guards import (  # noqa: F401 — `webhook_pluggy` é fixture
    _existe_user,
    _limpa_item,
    _mock_item,
    webhook_pluggy,
)


@pytest.fixture(autouse=True)
def tabelas_admin():
    from core.admin_dashboard import ensure_admin_tables

    asyncio.run(ensure_admin_tables())


def _limpa(i: str) -> None:
    _limpa_item(i)
    with get_conn() as c:
        c.execute("delete from system_event_logs where details::text like %s", (f"%{i}%",))
        c.commit()


def test_rastro_perdido_desconectado_e_reentregue(user_id, monkeypatch, eventos,
                                                 webhook_pluggy):
    """POST com `register_item` quebrado → CONECTADO_SEM_RASTRO → DELETE →
    REMOVIDO → `item/created` reentregue não adota e o estado não muda."""
    promote_to_pro(user_id)   # no Grátis `of_banks_max=0` e o POST mede 402
    _mock_item(monkeypatch, user_id)
    item = "conv-sem-rastro"
    real = of_routes.register_item

    def _sem_pluggy_item(*a, **kw):
        if kw.get("origin") == "pluggy_item":
            raise psycopg.OperationalError("registry fora do ar")
        return real(*a, **kw)

    try:
        monkeypatch.setattr(of_routes, "register_item", _sem_pluggy_item)
        c1 = TestClient(dashboard.app)
        r = c1.post(f"/open-finance/{user_id}/pluggy-item",
                    json={"item": {"id": item}}, headers=_auth(c1, user_id))
        assert r.status_code == 200, r.text
        monkeypatch.setattr(of_routes, "register_item", real)
        assert diag.classifica_item(item) == diag.CONECTADO_SEM_RASTRO
        assert item in diag.conexoes_sem_rastro()

        monkeypatch.setattr(of_routes, "create_pluggy_api_key", lambda: "k")
        monkeypatch.setattr(of_routes, "delete_pluggy_item", lambda i, api_key=None: True)
        c2 = TestClient(dashboard.app)
        assert c2.delete(f"/open-finance/{user_id}",
                         headers=_auth(c2, user_id)).status_code == 200
        assert diag.classifica_item(item) == diag.REMOVIDO
        assert diag.listar_sem_conexao()[item] == diag.REMOVIDO

        webhook_pluggy.clear()   # o sync inicial do POST não é deste passo
        assert _webhook(TestClient(dashboard.app), "item/created", item).status_code == 200
        assert diag.classifica_item(item) == diag.REMOVIDO, "a reentrega readotou"
        assert webhook_pluggy == [], "sync agendado para banco removido"
    finally:
        _limpa(item)


def test_item_de_conta_inexistente_com_delete_falho_e_apagado_pelo_operador(
        monkeypatch, eventos, webhook_pluggy):
    """`item/created` de conta que não existe: o webhook tenta apagar, a Pluggy
    responde 503 → a marca aparece → `--apagar` → `operator_delete` → marca some."""
    fantasma, item = 987654321988, "conv-fantasma"
    _mock_item(monkeypatch, fantasma)
    monkeypatch.setattr(of_routes, "create_pluggy_api_key", lambda: "k")

    def _503(i, api_key=None):
        raise PluggyApiError("Pluggy 503", status_code=503)

    monkeypatch.setattr(of_routes, "delete_pluggy_item", _503)
    apagados: list[str] = []
    monkeypatch.setattr(op, "delete_pluggy_item", lambda i, api_key=None: apagados.append(i))
    try:
        assert not _existe_user(fantasma), "pré-condição"
        assert _webhook(TestClient(dashboard.app), "item/created", item).status_code == 200
        assert item in diag.itens_com_remocao_remota_falha()
        assert diag.classifica_item(item) == diag.NUNCA_ATRIBUIDO

        assert op.main(["--item", item, "--apagar", "--estado", diag.NUNCA_ATRIBUIDO,
                        "--apply"]) == 0
        assert apagados == [item]
        assert item not in diag.itens_com_remocao_remota_falha()
    finally:
        _limpa(item)
