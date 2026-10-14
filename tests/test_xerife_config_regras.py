"""PL-04 B: fronteira HTTP real, isolamento, regra recorrente e concorrência no Postgres."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event

import pytest

import db
from db.anomalias import alterar_regra_xerife, atualizar_config_xerife, listar_esperados_xerife
from core.services.xerife_config import RegraEsperado, XerifeConfig
from conftest import usuario_pagante, promote_to_pro
from tests.test_api_v2_perfil import cliente
from test_xerife_deteccao import _lanca, _eventos, _roda


@pytest.fixture
def uid():
    user = usuario_pagante()
    db.activate_agent(user, "xerife")
    return user


def _request(uid, method, path, body=None, *, path_uid=None, com_csrf=True):
    c, h = cliente(uid, com_csrf)
    return c.request(method, f"/agents/{path_uid or uid}/xerife/{path}", json=body, headers=h)


def _regra(**kw):
    return RegraEsperado(categoria="Alimentação", descricao="mercado", teto=500, **kw).model_dump(mode="json")


INVALIDAS = [
    {"multiplicador": True}, {"multiplicador": "2.5"}, {"multiplicador": 0},
    {"multiplicador": 11}, {"minimo": -1}, {"minimo": 1_000_001},
    {"minimo": None}, {"email_enabled": "false"}, {"email_enabled": 0},
    {"limites": {"lazer": "50"}}, {"limites": {"lazer": True}},
    {"limites": {"lazer": 0}}, {"limites": {" ": 100}},
    {"limites": {"Lazer": 50, "lazer": 100}}, {"desconhecido": 1},
    {"regras_esperado": []}, {"minimo": "abc"},
]


@pytest.mark.parametrize("config", INVALIDAS)
@pytest.mark.parametrize("path", ["activate", "config"])
def test_config_crua_invalida_nao_muda_estado(uid, config, path):
    before = db.get_agent(uid, "xerife")["config"]
    body = {"config": config} if path == "activate" else config
    response = _request(uid, "POST" if path == "activate" else "PATCH", path, body)
    assert response.status_code == 422
    assert db.get_agent(uid, "xerife")["config"] == before


@pytest.mark.parametrize("path", ["activate", "config", "regras"])
@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_nao_finitos_rejeitados_sem_500(uid, path, value):
    c, h = cliente(uid, True)
    h["content-type"] = "application/json"
    raw = '{"minimo":' + value + '}'
    if path == "activate":
        raw = '{"config":' + raw + '}'
    elif path == "regras":
        raw = '{"categoria":"lazer","descricao":"teste","teto":' + value + '}'
    response = c.request("PATCH" if path == "config" else "POST", f"/agents/{uid}/xerife/{path}",
                         content=raw, headers=h)
    assert response.status_code == 422


def test_config_persiste_sem_reativar_e_runner_aplica_sensibilidade(uid):
    for i in range(5):
        _lanca(uid, 100, dias=10 + i)
    _lanca(uid, 300, dias=0, horas=1)
    r = _request(uid, "PATCH", "config", {"multiplicador": 4, "minimo": 200, "email_enabled": False})
    assert r.status_code == 200 and r.json()["config"]["multiplicador"] == 4
    assert _roda(uid)["fired"] == 0
    assert _request(uid, "PATCH", "config", {"multiplicador": 2.5}).status_code == 200
    assert _roda(uid)["fired"] == 1
    db.pause_agent(uid, "xerife")
    _request(uid, "PATCH", "config", {"minimo": 50})
    assert db.get_agent(uid, "xerife")["status"] == "paused"
    cfg = _request(uid, "GET", "esperados").json()["config"]
    assert cfg == {"multiplicador": 2.5, "minimo": 50, "email_enabled": False, "limites": {}}


def test_get_config_legada_invalida_tem_defaults_seguros(uid):
    db.activate_agent(uid, "xerife", {"multiplicador": "lixo", "minimo": -3, "email_enabled": False})
    config = _request(uid, "GET", "esperados").json()["config"]
    assert config == {"multiplicador": 2.5, "minimo": 50, "email_enabled": False, "limites": {}}


def test_regra_normalizada_aplica_exato_teto_fim_e_isolamento(uid):
    outro = usuario_pagante()
    db.activate_agent(outro, "xerife")
    for u in [uid, outro]:
        for i in range(5):
            _lanca(u, 100, dias=10+i, alvo="normal")
    alvo = _lanca(uid, 450, dias=0, horas=1, alvo="  MERCADO\t Central  ")
    acima = _lanca(uid, 501, dias=0, horas=1, alvo="mercado central")
    diferente = _lanca(uid, 450, dias=0, horas=1, alvo="mercado central extra")
    dele = _lanca(outro, 450, dias=0, horas=1, alvo="mercado central")
    r = _request(uid, "POST", "regras", {"categoria": "  ALIMENTAÇÃO  ",
                  "descricao": " Mercado   Central ", "teto": 500})
    assert r.status_code == 200
    regra = r.json()["regra"]
    assert regra["categoria"] == "alimentação" and regra["descricao"] == "mercado central"
    assert _request(uid, "POST", "regras", {k:v for k,v in regra.items() if k != "id"}).json()["regra"]["id"] == regra["id"]
    assert {x["id"] for x in db.listar_candidatos_xerife(uid, datetime.now(timezone.utc))} == {acima, diferente}
    assert dele in {x["id"] for x in db.listar_candidatos_xerife(outro, datetime.now(timezone.utc))}
    assert alvo not in {x["payload"]["launch_id"] for x in _eventos(uid)}
    yesterday = (datetime.now().date() - timedelta(days=1)).isoformat()
    r2 = _request(uid, "POST", "regras", {"categoria":"alimentação", "descricao":"mercado central extra", "teto":500, "data_fim":yesterday})
    assert r2.status_code == 200
    assert diferente in {x["id"] for x in db.listar_candidatos_xerife(uid, datetime.now(timezone.utc))}


def test_regra_remove_da_media_e_desfazer_nao_reenvia_evento(uid):
    for i in range(5):
        _lanca(uid, 100, dias=10+i, alvo="normal")
    outlier = _lanca(uid, 1800, dias=20, alvo="mercado")
    atual = _lanca(uid, 480, dias=0, horas=1, alvo="normal")
    _roda(uid)
    assert _eventos(uid) == []
    rule = alterar_regra_xerife(uid, {**_regra(), "teto": 2000})
    _roda(uid)
    p = _eventos(uid)[0]["payload"]
    assert p["launch_id"] == atual and p["media"] == 100
    assert p["explicacao"]["amostra"]["esperados_fora"] == 1
    # Uma ocorrência nova durante a regra também fica com lápide ao desfazer.
    novo = _lanca(uid, 700, dias=0, horas=1, alvo="mercado")
    alterar_regra_xerife(uid, regra_id=rule["id"])
    _roda(uid)
    assert novo not in {x["payload"]["launch_id"] for x in _eventos(uid)}
    # A regra foi removida de verdade: sua referência volta à média.
    row = next(x for x in db.listar_candidatos_xerife(uid, datetime.now(timezone.utc)) if x["id"] == novo)
    assert row["n"] == 6 and row["esperados_fora"] == 0


def test_regra_tira_evento_da_fila_e_desfazer_nao_ressuscita(uid):
    for i in range(5):
        _lanca(uid, 100, dias=10+i, alvo="normal")
    lid = _lanca(uid, 480, dias=0, horas=1, alvo="mercado")
    _roda(uid)
    aid = db.get_agent(uid, "xerife")["id"]
    assert len(db.list_unemailed_events(aid)) == 1
    rule = alterar_regra_xerife(uid, _regra())
    assert not _eventos(uid) and not db.list_unemailed_events(aid)
    assert _request(uid, "DELETE", "regras/" + rule["id"]).status_code == 200
    _roda(uid)
    assert not _eventos(uid) and not db.list_unemailed_events(aid)
    with db.get_conn() as conn:
        row = conn.execute("select stale_at from agent_events where user_id = %s and dedupe_key = %s",
                           (uid, f"anomalia:{lid}")).fetchone()
        assert row["stale_at"] is not None


def test_listagem_individual_paginada_e_desfazer_nao_altera_outro(uid):
    outro = usuario_pagante()
    ids = [_lanca(uid, 100, dias=1, alvo="mercado") for _ in range(3)]
    for lid in ids:
        db.marcar_lancamento_esperado(uid, lid, True)
    dele = _lanca(outro, 100, dias=1)
    db.marcar_lancamento_esperado(outro, dele, True)
    first = _request(uid, "GET", "esperados?limit=2").json()
    second = _request(uid, "GET", "esperados?limit=2&offset=2").json()
    assert first["has_more"] and not second["has_more"]
    assert {r["id"] for r in first["lancamentos"] + second["lancamentos"]} == set(ids)
    lid = first["lancamentos"][0]["id"]
    assert _request(uid, "PUT", f"lancamentos/{lid}/esperado", {"esperado":False}).status_code == 200
    assert len(listar_esperados_xerife(uid)["lancamentos"]) == 2


@pytest.mark.parametrize("method,path,body", [
    ("GET", "esperados", None), ("PATCH", "config", {"minimo":100}),
    ("POST", "regras", {"categoria":"lazer","descricao":"a","teto":10}),
    ("DELETE", "regras/nao-existe", None),
])
def test_rotas_gate_sessao_plano_e_beta(uid, monkeypatch, method, path, body):
    outro = usuario_pagante()
    assert _request(outro, method, path, body, path_uid=uid).status_code == 403
    if method != "GET":
        assert _request(uid, method, path, body, com_csrf=False).status_code == 403
    promote_to_pro(uid, plan="essencial")
    assert _request(uid, method, path, body).status_code == 403
    monkeypatch.setenv("AGENTS_UI_ENABLED", "0")
    monkeypatch.setenv("AGENTS_BETA_EMAILS", "")
    assert _request(uid, method, path, body).status_code == 404


def test_regra_de_outro_usuario_nao_pode_ser_removida(uid):
    outro = usuario_pagante()
    db.activate_agent(outro, "xerife")
    rule = alterar_regra_xerife(outro, _regra())
    assert _request(uid, "DELETE", "regras/"+rule["id"]).status_code == 404
    assert listar_esperados_xerife(outro)["regras"] == [rule]


@pytest.mark.parametrize("patch", [{"categoria":" "}, {"descricao":""}, {"teto":True},
                                   {"teto":0}, {"data_fim":"2026-02-30"}, {"data_fim":123},
                                   {"categoria":1}, {"extra":1}])
def test_regra_invalida_nao_persistida(uid, patch):
    assert _request(uid, "POST", "regras", {**_regra(), **patch}).status_code == 422
    assert listar_esperados_xerife(uid)["regras"] == []


def test_row_lock_preserva_config_email_reativacao_e_regras(uid, monkeypatch):
    import db.anomalias as mod
    entered, release = Event(), Event()
    original = mod._lapides_regras
    def bloqueia(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)
    monkeypatch.setattr(mod, "_lapides_regras", bloqueia)
    with ThreadPoolExecutor(max_workers=4) as pool:
        creation = pool.submit(alterar_regra_xerife, uid, _regra())
        assert entered.wait(5)
        patch = pool.submit(atualizar_config_xerife, uid, {"minimo": 123})
        email = pool.submit(db.set_agent_email_enabled, uid, "xerife", False)
        activate = pool.submit(db.activate_agent, uid, "xerife", {"multiplicador":3})
        release.set()
        rule = creation.result(5)
        for result in (patch, email, activate):
            result.result(5)
    config = db.get_agent(uid, "xerife")["config"]
    assert config == {"minimo":123,"multiplicador":3,"email_enabled":False,"regras_esperado":[rule]}


def test_detector_com_snapshot_anterior_nao_vaza_alerta(uid, monkeypatch):
    import db.anomalias as mod
    entered, release = Event(), Event()
    original = mod._lapides_regras
    lid = _lanca(uid, 480, dias=0, horas=1, alvo="mercado")
    aid = db.get_agent(uid, "xerife")["id"]
    def bloqueia(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)
    monkeypatch.setattr(mod, "_lapides_regras", bloqueia)
    with ThreadPoolExecutor(max_workers=2) as pool:
        creation = pool.submit(alterar_regra_xerife, uid, _regra())
        assert entered.wait(5)
        insertion = pool.submit(db.record_agent_event, aid, uid, "xerife", f"anomalia:{lid}",
                                {"tipo":"anomalia","launch_id":lid})
        release.set()
        creation.result(5)
        insertion.result(5)
    assert not _eventos(uid) and not db.list_unemailed_events(aid)


def test_limite_50_regras_e_repeticao_idempotente(uid):
    from core.services.xerife_config import MAX_REGRAS
    for i in range(MAX_REGRAS):
        alterar_regra_xerife(uid, {**_regra(), "descricao":str(i)})
    assert alterar_regra_xerife(uid, {**_regra(), "descricao":"0"})
    assert _request(uid, "POST", "regras", _regra()).status_code == 422
    assert len(listar_esperados_xerife(uid)["regras"]) == MAX_REGRAS


def test_canal_feed_permanece_email_opt_out_opt_in_e_dedupe(uid, monkeypatch):
    from core.services import piggy_agents as pa, email_service
    enviados = []
    monkeypatch.setattr(email_service, "send_agent_report_email", lambda *a, **k: enviados.append(a) or True)
    for i in range(5):
        _lanca(uid, 100, dias=10+i, alvo="normal")
    _lanca(uid, 480, dias=0, horas=1, alvo="mercado")
    _request(uid, "PATCH", "config", {"email_enabled":False})
    _roda(uid)
    pa.run_agent_emails_once()
    assert len(_eventos(uid)) == 1 and not enviados
    _request(uid, "PATCH", "config", {"email_enabled":True})
    pa.run_agent_emails_once()
    pa.run_agent_emails_once(now=datetime.now(timezone.utc)+timedelta(hours=25))
    assert len([a for a in enviados if a[1] == uid]) == 1
    assert len(_eventos(uid)) == 1


def test_config_legada_nao_objeto_pode_ser_lida_e_reparada(uid):
    with db.get_conn() as conn:
        conn.execute("update agents set config = '[]'::jsonb where user_id = %s", (uid,))
        conn.commit()
    assert _request(uid, "GET", "esperados").json()["config"] == XerifeConfig().model_dump()
    assert _request(uid, "PATCH", "config", {"minimo":100}).json()["config"]["minimo"] == 100
    assert db.get_agent(uid, "xerife")["config"] == {"minimo":100}


def test_limites_do_html_correspondem_ao_contrato_pydantic():
    from html.parser import HTMLParser
    from pathlib import Path
    class Inputs(HTMLParser):
        def __init__(self):
            super().__init__()
            self.fields = {}
        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == "input" and attrs.get("name") in {"multiplicador","minimo","teto","categoria","descricao"}:
                self.fields[attrs["name"]] = attrs
    html = Inputs()
    html.feed((Path(__file__).parents[1]/"frontend/dashboard.html").read_text())
    for model, names in [(XerifeConfig, ["multiplicador","minimo"]), (RegraEsperado,["teto","categoria","descricao"])]:
        schema = model.model_json_schema()["properties"]
        for name in names:
            if "minimum" in schema[name]:
                assert float(html.fields[name]["min"]) == schema[name]["minimum"]
                assert float(html.fields[name]["max"]) == schema[name]["maximum"]
            else:
                assert int(html.fields[name]["maxlength"]) == schema[name]["maxLength"]


def test_teto_exato_e_data_fim_inclusiva_no_fuso_do_app(uid):
    from utils_date import _tz
    from datetime import date, time
    fim = date(2026, 10, 9)
    borda = datetime.combine(fim, time(23, 59, 59), tzinfo=_tz())
    ids = []
    with db.get_conn() as conn:
        for valor, quando in [(500,borda),(500,borda+timedelta(seconds=1)),(500.01,borda)]:
            ids.append(conn.execute("insert into launches (user_id,tipo,valor,categoria,alvo,criado_em) "
                                    "values (%s,'despesa',%s,'Alimentação','mercado',%s) returning id",
                                    (uid,valor,quando)).fetchone()["id"])
        conn.commit()
    alterar_regra_xerife(uid, _regra(data_fim=fim.isoformat()))
    restantes = {r["id"] for r in db.listar_candidatos_xerife(uid,borda+timedelta(hours=1))}
    assert restantes == set(ids[1:])
