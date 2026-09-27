"""Issue #541, ponta a ponta: o log de Open Finance com dono na coluna sai na
exportação do titular SEM texto cru de exceção, e some na exclusão da conta.

Tudo REAL a partir do webhook: `item/created` de um usuário Pro, a adoção
chamando `register_item` que cai com um `OperationalError` cujo texto traz host
e porta (o que o psycopg de verdade põe na mensagem), o `log_system_event` real
gravando em `system_event_logs`, e depois `build_user_export_zip` e
`delete_user_data` (`db/privacy.py`) lendo a COLUNA — que é a única coisa que
os dois enxergam.

CONTROLES (CLAUDE.md §3), injetados no caso verde:
  • tirar `user_id=dono` do `except` genérico de `_adota_item_orfao` → vermelho
    nas duas pontas (a linha não sai na exportação e sobrevive à exclusão);
  • voltar `"error": str(exc)[:200]` com a coluna preenchida → vermelho (host na
    exportação).
O positivo da FK (conta apagada no meio: coluna NULL, linha preservada) mora em
`tests/test_of_webhook_adopt.py::test_conta_apagada_no_meio_da_adocao_nao_ressuscita`.
"""
from __future__ import annotations

import asyncio
import io
import json
import uuid
import zipfile

import psycopg

import db.privacy as privacy
import frontend.routes.open_finance as of_routes
from conftest import promote_to_pro
from core.admin_dashboard import ensure_admin_tables
from db.connection import get_conn
from test_account_deletion_adocao_corrida import _limpa_item, _skips, _webhook_de_item_criado
from test_of_log_dono_coluna import HOST


def _marca() -> int:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select coalesce(max(id), 0) as m from system_event_logs")
        m = int(cur.fetchone()["m"])
        conn.commit()
    return m


def test_skip_da_adocao_sai_na_exportacao_sem_host_e_some_na_exclusao(user_id, monkeypatch):
    asyncio.run(ensure_admin_tables())  # `system_event_logs` não vem de db/schema.py
    promote_to_pro(user_id)

    def _registry_fora(*a, **k):
        raise psycopg.OperationalError(HOST)
    monkeypatch.setattr(of_routes, "register_item", _registry_fora)
    item = f"i541-e2e-{uuid.uuid4().hex[:10]}"  # SEM o uid: a busca abaixo é por ele
    marca = _marca()
    try:
        assert _webhook_de_item_criado(monkeypatch, user_id, item).status_code == 200
        assert [s["user_id"] for s in _skips(item)] == [user_id], _skips(item)

        with zipfile.ZipFile(io.BytesIO(privacy.build_user_export_zip(user_id))) as zf:
            bruto = zf.read("dados.json").decode()
        eventos = json.loads(bruto)["dados"]["eventos_sistema"]
        meus = [e for e in eventos if e["event_type"] == "of_webhook_adopt_skipped"
                and item in json.dumps(e["details"])]
        assert len(meus) == 1, f"o skip do titular não saiu na exportação: {eventos}"
        assert meus[0]["details"]["motivo"] == "OperationalError", meus
        for pedaco in ("10.9.8.7", "db.interno"):
            assert pedaco not in bruto, f"texto cru da exceção na exportação: {pedaco}"
        assert "5432" not in meus[0]["message"] + json.dumps(meus[0]["details"]), meus

        privacy.delete_user_data(user_id)

        assert _skips(item) == [], "o skip do titular sobreviveu à exclusão da conta"
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "select event_type, message from system_event_logs where id > %s "
                "and (message like %s or details::text like %s)",
                (marca, f"%{user_id}%", f"%{user_id}%"),
            )
            restos = cur.fetchall()
            conn.commit()
        assert restos == [], f"uid do titular sobrou em log depois da exclusão: {restos}"
    finally:
        _limpa_item(item)
