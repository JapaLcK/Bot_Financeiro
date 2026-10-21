"""`POST /api/v2/simulador/habitos` (corte de hábito) e `/api/app/...`: Postgres real, relógio congelado (`dia_fixo`)."""
import json
import random
import re
from datetime import date, datetime, timedelta
from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import sessao_de
from api.v2 import simulador as rota
from api.v2.simulador import PREMISSAS_HABITOS_V2, SimuladorHabitos
from conftest import promote_to_pro, usuario_pagante
from core.services import previsao_v2
from core.services.cashflow_contract import FONTE_VARIAVEL
from core.services.decision_simulator import _curva, simular_corte
from core.services.previsao_variavel import cortar
from tests.test_api_v2_simulador import _contagens, cliente, dia_fixo, post as post_compra, uid  # noqa: F401 (fixtures)
from tests.test_cashflow_snapshot import q
from tests.test_previsao_variavel import gasto, historico, tres_janelas

ROTA = "/api/v2/simulador/habitos"
N_SORTEIOS = 40  # semente fixa; 300 no ataque original, 40 já cobre os ramos em ~1 s


def com_historico(u, dia, **categorias):
    historico(u, hoje=dia)
    for cat, v in categorias.items():
        tres_janelas(u, (v, v, v), categoria=cat, hoje=dia)


def corte(*itens):
    return {"cortes": [{"categoria": c, "percentual": p} for c, p in itens]}


def post(quem, corpo, rota_=ROTA, **kw):
    c, h = cliente(quem, **kw)
    return c.post(rota_, json=corpo, headers=h)


def ok(quem, corpo, **kw):
    r = post(quem, corpo, **kw)
    assert r.status_code == 200, r.text
    assert SimuladorHabitos.model_validate(r.json()).model_dump(mode="json") == r.json()
    return r.json()


def saldos(resumo):
    return [p["saldo"] for p in resumo["trajetoria"]]


@pytest.fixture
def espiao(monkeypatch):
    monkeypatch.setattr(rota, "ler_snapshot", lambda *a: pytest.fail("leitura financeira"))


# ── a conta ─────────────────────────────────────────────────────────────────

def test_delta_e_tres_vezes_a_economia_e_so_o_dia_a_dia_muda(uid, dia_fixo):
    com_historico(uid, dia_fixo, mercado=600, lazer=300)
    c = ok(uid, corte(("mercado", 50), ("lazer", 10)))
    assert c["despesa_mensal"] == "900.00" and c["economia_mensal"] == "330.00"
    assert [k["economia_mensal"] for k in c["cortes"]] == ["300.00", "30.00"]  # ordem da estimativa (maior primeiro)
    assert D(c["com_corte"]["delta_vs_atual"]) == 3 * D(c["economia_mensal"])
    a, k = c["atual"], c["com_corte"]
    assert len(a["trajetoria"]) == len(k["trajetoria"]) == 91 and a["trajetoria"][0] == k["trajetoria"][0]
    assert k["trajetoria"][0]["data"] == c["hoje"] == dia_fixo.isoformat()
    assert saldos(a)[1] != saldos(k)[1]  # o corte começa amanhã


def test_corte_total_equivale_a_previsao_sem_a_fonte_variavel_e_mantem_os_fixos(uid, dia_fixo):
    com_historico(uid, dia_fixo, mercado=600, lazer=300)
    snap = previsao_v2.ler_snapshot(uid, 90)
    sem = _curva(snap, [e for e in snap.ocorrencias if e.fonte != FONTE_VARIAVEL], 0)
    assert any(e.fonte != FONTE_VARIAVEL and e.nome == "Aluguel" for e in snap.ocorrencias)  # fixo presente
    k = ok(uid, corte(("mercado", 100), ("lazer", 100)))["com_corte"]
    assert saldos(k) == [f"{p['saldo']:.2f}" for p in sem["trajetoria"]]
    assert k["delta_vs_atual"] == "2700.00"


def test_zero_por_cento_nao_muda_nada(uid, dia_fixo):
    com_historico(uid, dia_fixo, mercado=600)
    c = ok(uid, corte(("mercado", 0)))
    assert c["economia_mensal"] == "0.00" and c["com_corte"]["delta_vs_atual"] == "0.00"
    assert saldos(c["com_corte"]) == saldos(c["atual"])


def test_arredondamento_por_categoria_soma_o_total_sem_menos_zero(uid, dia_fixo):
    com_historico(uid, dia_fixo, mercado="10.05", lazer="20.10", saude="30.15", barato="0.50")
    c = ok(uid, corte(("mercado", 33), ("lazer", 33), ("saude", 33), ("barato", 5)))
    por = {k["chave"]: k["economia_mensal"] for k in c["cortes"]}
    assert por == {"saude": "9.95", "lazer": "6.63", "mercado": "3.32", "barato": "0.02"}
    assert sum(map(D, por.values())) == D(c["economia_mensal"]) == D("19.92")
    assert "-0.00" not in str(c)


def test_cortar_pura_so_as_categorias_enviadas_na_ordem_da_estimativa():
    est = {"categorias": [{"chave": "a", "nome": "A", "despesa_mensal": D("10.00")},
                          {"chave": "b", "nome": "B", "despesa_mensal": D("5.00")}]}
    linhas, total = cortar(est, {"b": 50})
    assert [l["chave"] for l in linhas] == ["b"] and total == D("2.50")


def test_atual_do_habito_e_o_atual_do_simulador_de_compra(uid, dia_fixo):
    com_historico(uid, dia_fixo, mercado=600)
    h = ok(uid, corte(("mercado", 20)))["atual"]
    r = post_compra(uid, {"reserva_minima": "0", "cenarios": [{"nome": "x", "preco": "1"}]})
    assert r.status_code == 200 and r.json()["atual"] == h


# ── isolamento ──────────────────────────────────────────────────────────────

def test_categoria_valida_contra_a_estimativa_do_proprio_uid(uid, dia_fixo, monkeypatch):
    com_historico(uid, dia_fixo, mercado=600, lazer=300)
    outro = usuario_pagante("pro_max")
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", f"{uid},{outro}")
    db.set_balance(outro, D("1000"))
    com_historico(outro, dia_fixo, mercado=100)
    assert ok(outro, corte(("mercado", 50)))["economia_mensal"] == "50.00"  # a de B, não a de A (300.00)
    r = post(outro, corte(("lazer", 50)))  # só existe para A
    assert r.status_code == 422 and r.json()["error"]["details"][0]["type"] == "categoria_fora_da_estimativa"
    assert ok(uid, corte(("lazer", 50)))["economia_mensal"] == "150.00"
    assert post(uid, {**corte(("mercado", 10)), "user_id": outro}).status_code == 422


# ── 422 ─────────────────────────────────────────────────────────────────────

def det(r):
    assert r.status_code == 422, r.text
    return r.json()["error"]["details"][0]


def test_categoria_fora_da_estimativa_e_loc_do_indice(uid, dia_fixo):
    com_historico(uid, dia_fixo, mercado=600)
    d = det(post(uid, corte(("mercado", 10), ("SEGREDO-123", 10))))
    assert d["type"] == "categoria_fora_da_estimativa" and d["loc"] == ["body", "cortes", 1, "categoria"]
    assert "SEGREDO" not in post(uid, corte(("SEGREDO-123", 10))).text


def test_repetidas_422_antes_da_leitura(uid, espiao):
    d = det(post(uid, corte(("mercado", 10), ("mercado", 20))))
    assert d["type"] == "categorias_repetidas"


def test_sem_estimativa_422_e_repetidas(uid, dia_fixo):
    assert det(post(uid, corte(("mercado", 10))))["type"] == "categoria_fora_da_estimativa"  # estimativa null
    com_historico(uid, dia_fixo, mercado=600)
    d = det(post(uid, corte(("mercado", 10), ("mercado", 20))))
    assert d["type"] == "categorias_repetidas" and d["loc"] == ["body", "cortes"]


@pytest.mark.parametrize("corpo", [
    corte(("mercado", 101)), corte(("mercado", -1)), corte(("mercado", 30.5)), corte(("mercado", "30")),
    corte(("mercado", True)), {"cortes": []}, {"cortes": [{"categoria": "m", "percentual": 1}] * 51},
    corte(("", 10)), {"cortes": [{"categoria": "m", "percentual": 1, "x": 1}]}, {}])
def test_corpo_invalido_422_sem_eco_e_antes_da_leitura(uid, espiao, corpo):
    r = post(uid, corpo)
    assert r.status_code == 422 and '"input"' not in r.text


def test_categoria_sem_teto_de_tamanho_e_sem_eco(uid, dia_fixo):
    """Nome legado de 200 caracteres (coluna text sem teto) tem de poder ser cortado; um que a estimativa não
    tem é 422 próprio, sem eco."""
    nome = "x" * 200
    com_historico(uid, dia_fixo, **{nome: 600})
    r = ok(uid, corte((nome, 50)))
    assert D(r["economia_mensal"]) == D("300.00") and r["cortes"][0]["chave"] == nome
    r = post(uid, corte(("y" * 121, 10)))
    assert r.status_code == 422 and det(r)["type"] == "categoria_fora_da_estimativa" and "y" * 121 not in r.text


# ── gate ────────────────────────────────────────────────────────────────────

def test_sem_sessao_401(uid, espiao):
    assert post(None, corte(("mercado", 10))).status_code == 401


@pytest.mark.parametrize("plano", ["essencial", "pro"])  # 'pro' gravado = tier Plus
def test_abaixo_do_pro_403_antes_da_leitura_mesmo_com_corpo_invalido(uid, espiao, plano):
    promote_to_pro(uid, plano)
    for corpo in (corte(("mercado", 10)), {"cortes": []}, {"user_id": 1}):
        r = post(uid, corpo)
        assert r.status_code == 403 and r.json()["error"]["code"] == "pro_required"


def test_csrf_403(uid, espiao):
    assert post(uid, corte(("mercado", 10)), com_csrf=False).status_code == 403


def test_app_mesmo_contrato_e_onboarding_obrigatorio(uid, dia_fixo):
    com_historico(uid, dia_fixo, mercado=600)
    corpo = corte(("mercado", 10))
    c = TestClient(dashboard.app)
    c.headers["Authorization"] = f"Bearer {sessao_de(uid)['access']}"
    r = c.post("/api/app/simulador/habitos", json=corpo)
    assert r.status_code == 403 and r.json()["error"]["code"] == "open_finance_onboarding_required"
    q("update auth_accounts set open_finance_onboarding_completed_at=now() where user_id=%s", (uid,))
    r = c.post("/api/app/simulador/habitos", json=corpo)
    assert r.status_code == 200, r.text
    sem_hora = lambda d: {k: v for k, v in d.items() if k not in ("calculado_em", "valido_ate")}
    assert sem_hora(r.json()) == sem_hora(ok(uid, corpo))


# ── não grava, premissas ────────────────────────────────────────────────────

def test_nao_grava_nada(uid, dia_fixo):
    com_historico(uid, dia_fixo, mercado=600)
    c, h = cliente(uid)
    antes = _contagens(uid)
    assert c.post(ROTA, json=corte(("mercado", 10)), headers=h).status_code == 200
    assert _contagens(uid) == antes
    gasto(uid, 5, 1, hoje=dia_fixo)  # controle: a medição enxerga escrita
    assert _contagens(uid) != antes


def test_premissas_da_pessoa_sem_nome_de_campo(uid, dia_fixo):
    com_historico(uid, dia_fixo, mercado=600)
    p = ok(uid, corte(("mercado", 10)))["premissas"]
    assert p[-len(PREMISSAS_HABITOS_V2):] == PREMISSAS_HABITOS_V2
    assert not [t for t in p if re.search(r"\b[a-z]+_[a-z_]+\b", t)]


# ── card `simulador` do chat ────────────────────────────────────────────────

def test_card_do_chat_traz_a_previsao_e_mantem_o_mes(uid, dia_fixo):
    from core.services.ai_chat.cards import montar
    com_historico(uid, dia_fixo, mercado=600, lazer=300)
    q("update auth_accounts set open_finance_onboarding_completed_at=now() where user_id=%s", (uid,))
    bloco, visao = montar("simulador", uid, {"meses": [], "dias": None})
    rec = bloco["abas"][0]["recursos"]
    assert {"detalhes", "previsao"} <= set(rec) and rec["previsao"]["estimativa_variavel"]["despesa_mensal"] == "900.00"
    # a visão do modelo é a mediana (o que o card desenha), nunca o mês nem a previsão inteira
    v = visao["abas"][0]
    assert set(v) == {"mes", "gasto_do_dia_a_dia"} and v["gasto_do_dia_a_dia"]["despesa_mensal"] == 900.0
    assert {(c["nome"], c["despesa_mensal"]) for c in v["gasto_do_dia_a_dia"]["categorias"]} == {("mercado", 600.0), ("lazer", 300.0)}
    from core.services.ai_guard import check
    nao = lambda ev: [c for c in check("Você gasta cerca de R$ 900,00 por mês.", [ev]) if not c.supported]  # noqa: E731
    assert nao(json.dumps(visao)) == []  # sustentado; controle negativo logo abaixo
    mutada = json.dumps(visao).replace("despesa_mensal", "mensal")
    assert nao(mutada)  # chave não monetária: não sustentado


def test_card_do_chat_trunca_o_nome_da_categoria_na_visao(uid, dia_fixo):
    from core.services.ai_chat.cards import montar
    com_historico(uid, dia_fixo, **{"x" * 200: 600})
    q("update auth_accounts set open_finance_onboarding_completed_at=now() where user_id=%s", (uid,))
    _, visao = montar("simulador", uid, {"meses": [], "dias": None})
    assert [len(c["nome"]) for c in visao["abas"][0]["gasto_do_dia_a_dia"]["categorias"]] == [60]


def test_card_do_chat_sem_historico_visao_nula(uid, dia_fixo):
    from core.services.ai_chat.cards import montar
    q("update auth_accounts set open_finance_onboarding_completed_at=now() where user_id=%s", (uid,))
    bloco, visao = montar("simulador", uid, {"meses": [], "dias": None})
    assert visao["abas"][0]["gasto_do_dia_a_dia"] is None and "previsao" in bloco["abas"][0]["recursos"]


# ── ataque (provado pelo Tester) ────────────────────────────────────────────

def test_prop_delta_sorteios_com_semente_fixa(uid, dia_fixo):
    com_historico(uid, dia_fixo, a="123.45", b="77.77", c="0.01", d="999.99", e="3.33")
    snap = previsao_v2.ler_snapshot(uid, 90)
    chaves, rnd = [c["chave"] for c in snap.estimativa_variavel["categorias"]], random.Random(1)
    for _ in range(N_SORTEIOS):
        cortes = {k: rnd.randint(0, 100) for k in rnd.sample(chaves, rnd.randint(1, 5))}
        r = simular_corte(snap, cortes)
        a, k = r["atual"]["trajetoria"], r["com_corte"]["trajetoria"]
        assert r["com_corte"]["delta_vs_atual"] == r["economia_mensal"] * 3 and k[0] == a[0], cortes
        assert all(y["saldo"] >= x["saldo"] for x, y in zip(a, k)), cortes  # nunca pior


@pytest.mark.parametrize("dia", [date(2026, 2, 28), date(2028, 2, 29), date(2026, 12, 31), date(2027, 1, 31)])
def test_datas_de_borda_com_relogio_em_2359(uid, monkeypatch, dia):
    agora = datetime(dia.year, dia.month, dia.day, 23, 59, tzinfo=previsao_v2.now_tz().tzinfo)
    monkeypatch.setattr(previsao_v2, "now_tz", lambda: agora)
    com_historico(uid, dia, mercado=100)
    c = ok(uid, corte(("mercado", 33)))
    assert D(c["com_corte"]["delta_vs_atual"]) == 3 * D(c["economia_mensal"])
    assert c["hoje"] == dia.isoformat() and len(c["atual"]["trajetoria"]) == 91


@pytest.mark.parametrize("cat", ["MERCADO", "Mercado", " mercado", "mercado ", "mercado\u0000", "merçado", "m" * 120,
                                 "\U0001F600", "' or 1=1 --", "mercado\n"])
def test_categoria_fora_da_chave_422_sem_eco(uid, dia_fixo, cat):
    com_historico(uid, dia_fixo, mercado=100)
    r = post(uid, corte((cat, 10)))
    assert r.status_code == 422 and cat not in r.text and "1=1" not in r.text


@pytest.mark.parametrize("corpo", [
    None, [], "x", 5, {"cortes": None}, {"cortes": {"categoria": "mercado", "percentual": 10}},
    {"cortes": [[{"categoria": "mercado", "percentual": 10}]]}, {"cortes": [None]}, {"cortes": ["mercado"]},
    {"cortes": [{"categoria": None, "percentual": 10}]}, {"cortes": [{"categoria": 5, "percentual": 10}]},
    {"cortes": [{"categoria": "mercado", "percentual": None}]}, {"cortes": [{"categoria": "mercado", "percentual": [10]}]},
    {"cortes": [{"categoria": "mercado"}]}, {"cortes": [{"percentual": 10}]},
    {"cortes": [{"categoria": "mercado", "percentual": 10**30}]}, {"cortes": [{"categoria": "mercado", "percentual": 1e2}]},
    {"cortes": [{"categoria": "mercado", "percentual": 10, "user_id": 1}]},
    {"cortes": [{"categoria": "mercado", "percentual": 10}], "SEGREDO_X": "SEGREDO_Y"}])
def test_corpos_hostis_422_sem_eco(uid, dia_fixo, corpo):
    com_historico(uid, dia_fixo, mercado=100)
    c, h = cliente(uid)
    r = c.post(ROTA, content=json.dumps(corpo), headers={**h, "content-type": "application/json"})
    assert r.status_code == 422 and "SEGREDO_Y" not in r.text
