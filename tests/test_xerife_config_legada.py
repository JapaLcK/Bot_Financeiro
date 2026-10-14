"""Config antiga e arbitrária recupera defaults iguais na tela e no detector."""
import json

import pytest

import db
from db.anomalias import alterar_regra_xerife
from core.services.xerife_config import XerifeConfig
from conftest import usuario_pagante
from test_xerife_deteccao import _lanca, _eventos, _roda
from test_xerife_config_regras import _request, _regra


@pytest.fixture
def uid():
    user = usuario_pagante()
    db.activate_agent(user, "xerife")
    for i in range(5):
        _lanca(user, 100, dias=10+i, alvo="normal")
    _lanca(user, 480, dias=0, horas=1, alvo="mercado")
    return user


def _config(uid, config):
    with db.get_conn() as conn:
        conn.execute("update agents set config = %s::jsonb where user_id = %s and kind = 'xerife'",
                     (json.dumps(config),uid))
        conn.commit()


@pytest.mark.parametrize("value", [True, "abc", "nan", "inf", -2, 0, 100])
def test_default_mostrado_e_usado_quando_limiar_legado_invalido(uid, value):
    _config(uid, {"multiplicador":value})
    cfg = _request(uid,"GET","esperados").json()["config"]
    assert cfg == XerifeConfig().model_dump()
    assert _roda(uid)["fired"] == 1
    assert _eventos(uid)[0]["payload"]["explicacao"]["referencia"]["limiar_multiplicador"] == cfg["multiplicador"]


@pytest.mark.parametrize("raw", [None, 2, "abc", {}, ["abc"], [None], [{}],
    [{"id":"x", "categoria":"alimentação", "descricao":"mercado", "teto":"abc"}],
    [{"id":None, "categoria":"alimentação", "descricao":"mercado", "teto":500}],
    [{"id":"x", "categoria":"alimentação", "descricao":"mercado", "teto":500, "data_fim":"nunca"}],
])
def test_regras_legadas_invalidas_nao_quebram_leitura_deteccao_ou_crud(uid, raw):
    _config(uid, {"regras_esperado":raw})
    r = _request(uid,"GET","esperados")
    assert r.status_code == 200 and r.json()["regras"] == []
    assert _roda(uid)["fired"] == 1
    rule = alterar_regra_xerife(uid, _regra())
    assert not _eventos(uid)
    assert _request(uid,"GET","esperados").json()["regras"] == [rule]
    assert alterar_regra_xerife(uid, regra_id=rule["id"]) == rule
    assert _request(uid,"GET","esperados").json()["regras"] == []


@pytest.mark.parametrize("value", [0, "false", None, False])
def test_email_legado_usa_mesma_preferencia_efetiva_da_tela(uid, monkeypatch, value):
    from core.services import piggy_agents as pa, email_service
    enviados = []
    monkeypatch.setattr(email_service, "send_agent_report_email", lambda *a, **k: enviados.append(a) or True)
    _config(uid, {"email_enabled":value})
    cfg = _request(uid,"GET","esperados").json()["config"]
    assert _roda(uid)["fired"] == 1
    pa.run_agent_emails_once()
    assert bool([a for a in enviados if a[1] == uid]) is cfg["email_enabled"]
