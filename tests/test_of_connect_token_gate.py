"""
Testes das correções do ultrareview no fluxo Open Finance/Pluggy:

  - _ensure_of_access_allowed: barra o connect-token só quando o plano não dá OF
    nenhum (of_banks_max <= 0), sem travar planos com teto > 0 (reconexão).
  - PLUGGY_PRODUCTS default inclui INVESTMENTS (Caixinha via /investments).
  - PLUGGY_INCLUDE_SANDBOX default DESLIGADO (sandbox fora de produção).

Tudo com monkeypatch — sem tocar banco nem rede.
"""

import asyncio
import importlib

import pytest
from fastapi import HTTPException

import frontend.routes.open_finance as of
from core.services import plan_service


def _run(coro):
    return asyncio.run(coro)


# ─── _ensure_of_access_allowed (gate do connect-token) ───────────────────────

class TestConnectTokenGate:
    def test_blocks_when_plan_has_no_of(self, monkeypatch):
        monkeypatch.setattr(plan_service, "plans_v2_enabled", lambda: True)
        monkeypatch.setattr(plan_service, "get_user_limits", lambda uid: {"of_banks_max": 0})
        with pytest.raises(HTTPException) as ei:
            _run(of._ensure_of_access_allowed(1))
        assert ei.value.status_code == 402
        assert ei.value.detail["code"] == "OF_BANK_LIMIT"
        assert ei.value.detail["limit"] == 0

    def test_allows_paid_tier_at_limit(self, monkeypatch):
        # Teto > 0 NÃO é barrado aqui (reconexão precisa passar); a contagem é
        # cobrada no /pluggy-item, não na emissão do token.
        monkeypatch.setattr(plan_service, "plans_v2_enabled", lambda: True)
        monkeypatch.setattr(plan_service, "get_user_limits", lambda uid: {"of_banks_max": 2})
        _run(of._ensure_of_access_allowed(1))  # não levanta

    def test_allows_unlimited(self, monkeypatch):
        monkeypatch.setattr(plan_service, "plans_v2_enabled", lambda: True)
        monkeypatch.setattr(plan_service, "get_user_limits", lambda uid: {"of_banks_max": None})
        _run(of._ensure_of_access_allowed(1))  # não levanta

    def test_legacy_gate_off_allows(self, monkeypatch):
        # v1 legado com gate dormante: não barra nada.
        monkeypatch.setattr(plan_service, "plans_v2_enabled", lambda: False)
        monkeypatch.delenv("OF_BANK_LIMIT_ENABLED", raising=False)
        _run(of._ensure_of_access_allowed(1))  # não levanta

    def test_legacy_gate_on_blocks_free_zero_limit(self, monkeypatch):
        monkeypatch.setattr(plan_service, "plans_v2_enabled", lambda: False)
        monkeypatch.setenv("OF_BANK_LIMIT_ENABLED", "1")
        monkeypatch.setenv("OF_FREE_BANK_LIMIT", "0")
        monkeypatch.setattr(of, "is_pro", lambda uid: False)
        with pytest.raises(HTTPException) as ei:
            _run(of._ensure_of_access_allowed(1))
        assert ei.value.status_code == 402


# ─── Defaults de produtos / sandbox ──────────────────────────────────────────

class TestPluggyDefaults:
    def test_products_default_includes_investments(self, monkeypatch):
        import core.services.pluggy as pl

        captured = {}

        def fake_post(self, url, headers=None, json=None):  # noqa: A002
            captured["options"] = json["options"]

            class _R:
                status_code = 200

                def json(self_inner):
                    return {"accessToken": "tok"}

            return _R()

        monkeypatch.delenv("PLUGGY_PRODUCTS", raising=False)
        monkeypatch.setattr(pl, "create_pluggy_api_key", lambda: "k")
        monkeypatch.setattr(pl, "_raise_for_pluggy_response", lambda resp, msg: None)
        monkeypatch.setattr(pl.httpx.Client, "post", fake_post)

        pl.create_pluggy_connect_token(1, None)
        assert "INVESTMENTS" in captured["options"]["products"]

    def test_sandbox_default_off(self, monkeypatch):
        # Reimporta o módulo do router com a env limpa: default = desligado.
        monkeypatch.delenv("PLUGGY_INCLUDE_SANDBOX", raising=False)
        mod = importlib.reload(of)
        try:
            assert mod.PLUGGY_INCLUDE_SANDBOX is False
        finally:
            importlib.reload(of)  # restaura estado do módulo pros outros testes

    def test_sandbox_on_when_explicit(self, monkeypatch):
        monkeypatch.setenv("PLUGGY_INCLUDE_SANDBOX", "1")
        mod = importlib.reload(of)
        try:
            assert mod.PLUGGY_INCLUDE_SANDBOX is True
        finally:
            monkeypatch.delenv("PLUGGY_INCLUDE_SANDBOX", raising=False)
            importlib.reload(of)
