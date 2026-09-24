"""`/.well-known/apple-app-site-association`: o arquivo que a Apple busca para
o app iOS salvar senha e código no app Senhas. A Apple não segue redirect e
exige `application/json`; o id do app espelha o `app/app.config.ts`."""

import re
from pathlib import Path

from fastapi.testclient import TestClient

import frontend.finance_bot_websocket_custom as dashboard
from frontend.routes.static_pages import _APPLE_APP_IDS

_CONFIG_DO_APP = Path(__file__).resolve().parent.parent / "app" / "app.config.ts"


def test_serve_o_arquivo_como_a_apple_exige():
    resp = TestClient(dashboard.app, follow_redirects=False).get("/.well-known/apple-app-site-association")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/json"
    assert "location" not in resp.headers
    assert resp.json() == {"webcredentials": {"apps": ["S849YDA49P.com.pigbankai.mobile"]}}
    assert len(resp.content) < 128 * 1024


def test_id_do_app_espelha_o_app_config():
    fonte = _CONFIG_DO_APP.read_text(encoding="utf-8")
    id_base = re.search(r'const ID_BASE = "([^"]+)"', fonte)
    team = re.search(r'appleTeamId: "([^"]+)"', fonte)
    assert id_base and team, "o formato do app.config.ts mudou: ajuste as buscas"
    assert _APPLE_APP_IDS == (f"{team.group(1)}.{id_base.group(1)}",)
