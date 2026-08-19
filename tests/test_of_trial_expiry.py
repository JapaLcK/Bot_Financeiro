"""Varredura de trial vencido no Open Finance (planos v2).

Cobre o serviço core/services/open_finance_trial_expiry.py + os helpers de DB:
  - flag PLANS_V2_ENABLED off → tick inerte (nada muda)
  - tier free (trial vencido, sem assinatura) → item deletado na Pluggy + PAUSED,
    SEM apagar os dados já importados (decisão de produto: dados ficam, sync para)
  - tier pago/trial ativo e allowlist de admin → intocados
  - falha no delete da Pluggy → NÃO pausa (retry no próximo tick)
  - PAUSED sai do refresh (list_pluggy_item_ids), barra o sync e não é
    sobrescrito por webhook atrasado (item/deleted)

⚠️ A varredura real varre a tabela inteira; nos testes o lister é SEMPRE
monkeypatchado pra devolver só as conexões do usuário de teste — o DB dos
testes é compartilhado e não se pausa conexão alheia.
"""

from datetime import date

import pytest

from core.services import open_finance_trial_expiry as ofte
from core.services import plan_service
from core.services.pluggy_sync import sync_pluggy_item
from db import (
    get_open_finance_snapshot,
    list_pluggy_connections_for_trial_sweep,
    list_pluggy_item_ids,
    pause_open_finance_connection,
    save_open_finance_sync,
    save_pluggy_open_finance_item,
    update_pluggy_open_finance_item_status,
)
from db.connection import get_conn


def _make_pluggy_connection(user_id: int, suffix: str = "a") -> dict:
    item_id = f"test-item-{user_id}-{suffix}"
    return save_pluggy_open_finance_item(
        user_id,
        {"id": item_id, "status": "UPDATED", "connector": {"id": 612, "name": "Nubank"}},
    )


def _seed_synced_data(connection_id: int) -> None:
    save_open_finance_sync(
        connection_id,
        [
            {
                "provider_account_id": f"acc-{connection_id}",
                "name": "Nubank Conta",
                "type": "BANK",
                "transactions": [
                    {
                        "provider_transaction_id": f"tx-{connection_id}-1",
                        "description": "Mercado",
                        "amount": "-50.00",
                        "transaction_date": date.today(),
                    }
                ],
            }
        ],
    )


def _connection_status(connection_id: int) -> str:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select status from open_finance_connections where id=%s", (connection_id,))
            return cur.fetchone()["status"]


@pytest.fixture()
def sweep_env(monkeypatch, user_id):
    """v2 ON + Pluggy mockada + lister escopado ao usuário de teste.

    Retorna (user_id, connection, deleted_items:list). O delete na Pluggy vira um
    recorder — a rede já é bloqueada no conftest, isto só torna o sucesso o default.
    """
    monkeypatch.setenv("PLANS_V2_ENABLED", "1")
    connection = _make_pluggy_connection(user_id)

    deleted: list[str] = []
    monkeypatch.setattr(ofte, "create_pluggy_api_key", lambda: "test-key")
    monkeypatch.setattr(ofte, "delete_pluggy_item", lambda item_id, key=None: deleted.append(item_id) or True)
    monkeypatch.setattr(
        ofte,
        "list_pluggy_connections_for_trial_sweep",
        lambda: [c for c in list_pluggy_connections_for_trial_sweep() if int(c["user_id"]) == user_id],
    )
    return user_id, connection, deleted


class TestSweep:
    def test_flag_off_tick_inerte(self, sweep_env, monkeypatch):
        _, connection, deleted = sweep_env
        monkeypatch.setenv("PLANS_V2_ENABLED", "0")  # freio: default agora é LIGADO

        res = ofte.pause_expired_trial_connections()

        assert res["disabled"] is True
        assert res["paused"] == 0
        assert deleted == []
        assert _connection_status(connection["id"]) != "PAUSED"

    def test_free_expirado_pausa_e_preserva_dados(self, sweep_env):
        uid, connection, deleted = sweep_env
        _seed_synced_data(connection["id"])
        # user_id do fixture não tem auth_accounts nem trial → get_plan_tier real = 'free'
        assert plan_service.get_plan_tier(uid) == "free"

        res = ofte.pause_expired_trial_connections()

        assert res["paused"] == 1
        assert deleted == [connection["provider_item_id"]]
        assert _connection_status(connection["id"]) == "PAUSED"
        # Decisão de produto: dados importados FICAM visíveis, só o sync para.
        snapshot = get_open_finance_snapshot(uid)
        assert len(snapshot["accounts"]) == 1
        assert len(snapshot["transactions"]) == 1

    def test_tier_pago_ou_trial_ativo_nao_pausa(self, sweep_env, monkeypatch):
        _, connection, deleted = sweep_env
        monkeypatch.setattr(plan_service, "get_plan_tier", lambda uid: "plus")

        res = ofte.pause_expired_trial_connections()

        assert res["paused"] == 0
        assert deleted == []
        assert _connection_status(connection["id"]) != "PAUSED"

    def test_allowlist_admin_teste_e_pulada(self, sweep_env, monkeypatch):
        uid, connection, deleted = sweep_env
        monkeypatch.setattr(plan_service, "_ACCESS_ALLOWLIST", {uid})

        res = ofte.pause_expired_trial_connections()

        assert res["checked_users"] == 0
        assert res["paused"] == 0
        assert deleted == []
        assert _connection_status(connection["id"]) != "PAUSED"

    def test_falha_na_pluggy_nao_pausa_e_retenta(self, sweep_env, monkeypatch):
        _, connection, deleted = sweep_env

        def _boom(item_id, key=None):
            raise RuntimeError("pluggy fora do ar")

        monkeypatch.setattr(ofte, "delete_pluggy_item", _boom)
        res = ofte.pause_expired_trial_connections()
        assert res["paused"] == 0
        assert res["errors"] == 1
        # nunca marca PAUSED com o item ainda vivo na Pluggy (slot pago vazaria)
        assert _connection_status(connection["id"]) != "PAUSED"

        # próximo tick, Pluggy voltou → pausa
        monkeypatch.setattr(ofte, "delete_pluggy_item", lambda item_id, key=None: deleted.append(item_id) or True)
        res = ofte.pause_expired_trial_connections()
        assert res["paused"] == 1
        assert _connection_status(connection["id"]) == "PAUSED"


class TestPausedState:
    def test_lister_inclui_ativa_e_exclui_pausada(self, user_id):
        connection = _make_pluggy_connection(user_id)
        mine = [c for c in list_pluggy_connections_for_trial_sweep() if int(c["user_id"]) == user_id]
        assert [c["id"] for c in mine] == [connection["id"]]

        pause_open_finance_connection(connection["id"])
        mine = [c for c in list_pluggy_connections_for_trial_sweep() if int(c["user_id"]) == user_id]
        assert mine == []

    def test_paused_sai_do_refresh_periodico(self, user_id):
        connection = _make_pluggy_connection(user_id)
        assert list_pluggy_item_ids(user_id) == [connection["provider_item_id"]]

        pause_open_finance_connection(connection["id"])
        assert list_pluggy_item_ids(user_id) == []

    def test_sync_barra_conexao_pausada(self, user_id):
        connection = _make_pluggy_connection(user_id)
        pause_open_finance_connection(connection["id"])

        # Se a guarda falhar, o sync tenta a Pluggy real e o kill-switch de rede
        # do conftest estoura RuntimeError — o teste falha alto.
        res = sync_pluggy_item(connection["provider_item_id"])
        assert res == {
            "ok": False,
            "reason": "connection_paused",
            "item_id": connection["provider_item_id"],
        }

    def test_webhook_atrasado_nao_sobrescreve_paused(self, user_id):
        connection = _make_pluggy_connection(user_id)

        # antes de pausar, o webhook atualiza normal
        assert update_pluggy_open_finance_item_status(connection["provider_item_id"], "ACTIVE") == 1

        pause_open_finance_connection(connection["id"])
        # deletar o item na Pluggy dispara item/deleted — não pode reabrir o estado
        assert update_pluggy_open_finance_item_status(connection["provider_item_id"], "DELETED") == 0
        assert _connection_status(connection["id"]) == "PAUSED"
