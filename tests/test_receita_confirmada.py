"""P7, "detecta e você confirma" (2026-10-16): a receita do extrato é só CANDIDATA; entra na Previsão
a que o usuário confirmou (`POST /api/v2/previsao/receita`, marca em `subscription_marks` com a chave
`extrato#…`). Pelo endpoint montado (sessão, CSRF do pai, gate de plano) e pelo `ler`, com banco real."""
from datetime import date, timedelta
from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard
from _apoio_assinaturas import HOJE, conta, mensais, rp, semeia, tx
from _apoio_auth_app import csrf, sessao_de
from api.v2.previsao import Previsao
from conftest import usuario_pagante
from core.services.ai_chat.tools.bills import _check_cashflow
from core.services.assinaturas import listar_assinaturas
from core.services.cashflow_forecast import forecast_horizons
from db.of_recurring import marcar, marcas
from db.recurring_income import create_recurring_income
from tests.test_cashflow_snapshot import q
from tests.test_previsao_recorrencias_of import do_banco, ler_em

ROTA = "/api/v2/previsao"
DOC = "55566677788"
OUTRO = "11122233344"


@pytest.fixture
def novo(monkeypatch):
    ids = []

    def _novo(plano="pro_max"):
        ids.append(str(usuario_pagante(plano)))
        monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", ",".join(ids))
        return int(ids[-1])
    monkeypatch.setenv("DASHBOARD_V2_BETA_EMAILS", "")
    return _novo


def _cliente(uid):
    c = TestClient(dashboard.app)
    c.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, sessao_de(uid)["dashboard"])
    return c, csrf(c)


def ver(uid):
    c, _ = _cliente(uid)
    r = c.get(ROTA)
    assert r.status_code == 200, r.text
    assert Previsao.model_validate(r.json()).model_dump(mode="json") == r.json()
    return r.json()


def responder(uid, chave, resposta):
    c, h = _cliente(uid)
    return c.post(f"{ROTA}/receita", json={"chave": chave, "resposta": resposta}, headers=h)


def salario_do_dono(uid, doc=DOC, ultimo="4100"):
    """4000 por mês (o último, `ultimo`) do mesmo CPF, 6 meses, com dois Pix avulsos do mesmo CPF no meio."""
    u = date.today() - timedelta(days=10)
    while u.day > 28:
        u -= timedelta(days=1)
    sal = mensais("sal", ["4000"] * 5 + [ultimo], ultima=u, desc="EMPRESA X", pagador=doc)
    av = [tx(f"av{i}", "600", sal[i]["transaction_date"] - timedelta(days=14), desc="PIX", pagador=doc) for i in (1, 3)]
    semeia(uid, [conta("acc-sal", sal + av)], [])


def netflix_recente(uid):
    """Uma assinatura ativa de verdade (última cobrança há 5 dias), para a chave `netflix` existir em `/assinaturas`."""
    txs = mensais("nf", [-39.9] * 3, ultima=date.today() - timedelta(days=5), desc="NETFLIX.COM")
    semeia(uid, [conta("acc-cc", txs, tipo="CREDIT", numero="1234", nome="Cartão")], [rp("NETFLIX.COM", -39.9, txs)])


def na_conta(j):
    return [o for g in j["compromissos"] or [] for o in g["ocorrencias"] if o["tipo"] == "receita" and o["incluida_no_calculo"]]


def uma(j):
    (r,) = j["receitas_do_banco"]
    return r


def linhas(*uids):
    return q("select user_id, merchant_key, status from subscription_marks where user_id = any(%s)", (list(uids),))


# ── 1. pendente → Sim → Não → Desfazer ───────────────────────────────────────

def test_pendente_sim_nao_desfazer_pelo_endpoint(novo):
    a = novo()
    salario_do_dono(a)
    j = ver(a)
    r = uma(j)
    assert (r["nome"], D(r["valor"]), r["status"]) == ("EMPRESA X", D("4100"), "pendente") and r["chave"].startswith("extrato#")
    assert na_conta(j) == []  # candidata nunca é somada

    c = responder(a, r["chave"], "sim")
    assert c.status_code == 200, c.text
    assert uma(c.json())["status"] == "confirmada"
    assert {(D(o["valor"]), o["direcao"]) for o in na_conta(c.json())} == {(D("4100"), "entrada")}  # o valor do último elo
    assert len(na_conta(ver(a))) == 1  # a Previsão sem `dias` lê 30 dias: um ciclo

    n = responder(a, r["chave"], "nao").json()
    assert uma(n)["status"] == "negada" and na_conta(n) == []
    assert uma(ver(a))["status"] == "negada"  # negada não é pergunta de novo: segue na lista, com o status

    d = responder(a, r["chave"], "desfazer").json()
    assert uma(d)["status"] == "pendente" and na_conta(d) == [] and linhas(a) == []


def test_pendentes_vem_primeiro_depois_por_valor(novo):
    a = novo()
    salario_do_dono(a)
    u = date.today() - timedelta(days=8)
    while u.day > 28:
        u -= timedelta(days=1)
    semeia(a, [conta("acc-2", mensais("o", ["900"] * 3, ultima=u, desc="OUTRA", pagador=OUTRO))], [])
    grande, pequena = ver(a)["receitas_do_banco"]
    assert (D(grande["valor"]), D(pequena["valor"])) == (D("4100"), D("900"))
    responder(a, grande["chave"], "nao")
    assert [r["status"] for r in ver(a)["receitas_do_banco"]] == ["pendente", "negada"]


# ── 2. isolamento ────────────────────────────────────────────────────────────

def test_b_nao_responde_a_chave_de_a_e_nada_e_gravado(novo):
    a, b = novo(), novo()
    salario_do_dono(a)
    salario_do_dono(b, doc=OUTRO)
    chave_de_a = uma(ver(a))["chave"]
    r = responder(b, chave_de_a, "sim")
    assert r.status_code == 404 and r.json()["error"]["code"]
    assert linhas(a, b) == []
    assert uma(ver(b))["status"] == "pendente"


def test_mesmo_pagador_marcas_independentes_entre_usuarios(novo):
    a, b = novo(), novo()
    salario_do_dono(a)
    salario_do_dono(b)  # o mesmo CPF => o mesmo hash
    chave = uma(ver(a))["chave"]
    assert chave == uma(ver(b))["chave"]
    assert responder(a, chave, "sim").status_code == 200
    assert (uma(ver(a))["status"], uma(ver(b))["status"]) == ("confirmada", "pendente")
    assert na_conta(ver(b)) == []
    assert [x["user_id"] for x in linhas(a, b)] == [a]


# ── 3. chave inventada, e as duas marcas não se misturam ─────────────────────

@pytest.mark.parametrize("chave", ["extrato#" + "0" * 16, "netflix", "", "x" * 300])
def test_chave_inventada_ou_de_assinatura_e_404(novo, chave):
    a = novo()
    salario_do_dono(a)
    netflix_recente(a)
    assert responder(a, chave, "sim").status_code == 404
    assert linhas(a) == []


def test_resposta_fora_da_lista_e_422(novo):
    a = novo()
    salario_do_dono(a)
    assert responder(a, uma(ver(a))["chave"], "talvez").status_code == 422


def test_chave_de_receita_nao_e_aceita_na_marca_de_assinatura(novo):
    a = novo()
    salario_do_dono(a)
    c, h = _cliente(a)
    r = c.post("/api/v2/assinaturas/marca", json={"chave": uma(ver(a))["chave"], "status": "ignorar"}, headers=h)
    assert r.status_code == 404 and linhas(a) == []


def test_sem_csrf_e_403_e_nada_e_gravado(novo):
    a = novo()
    salario_do_dono(a)
    c, _ = _cliente(a)
    r = c.post(f"{ROTA}/receita", json={"chave": uma(ver(a))["chave"], "resposta": "sim"}, headers={})
    assert r.status_code == 403 and linhas(a) == []


# ── 4. confirmada acompanha o extrato ────────────────────────────────────────

def test_aumento_de_10pct_segue_confirmada_com_o_valor_novo(user_id):
    semeia(user_id, [conta("acc", mensais("s", ["4000", "4000", "4400"], desc="EMPRESA X", pagador=DOC))], [])
    chave = ler_em(user_id).receitas_do_banco[0]["chave"]
    marcar(user_id, chave, "assinatura")
    s = ler_em(user_id)
    assert [(r["status"], r["valor"]) for r in s.receitas_do_banco] == [("confirmada", D("4400"))]
    assert {e.valor for e in s.ocorrencias if e.fonte == "recorrencia_banco" and e.incluida} == {D("4400")}


def test_pagador_para_sai_da_conta_e_volta_sem_nova_pergunta(user_id):
    semeia(user_id, [conta("acc", mensais("s", ["4000"] * 3, desc="EMPRESA X", pagador=DOC))], [])  # último: 05/09
    marcar(user_id, ler_em(user_id).receitas_do_banco[0]["chave"], "assinatura")
    parou = ler_em(user_id, hoje=date(2026, 10, 20))  # 45 dias sem crédito
    ocs = [e for e in parou.ocorrencias if e.fonte == "recorrencia_banco"]
    assert ocs and not any(e.incluida for e in ocs)
    assert parou.receitas_do_banco[0]["status"] == "confirmada"
    semeia(user_id, [conta("acc-2", [tx("volta", "4000", date(2026, 10, 5), desc="EMPRESA X", pagador=DOC)])], [])
    voltou = ler_em(user_id, hoje=date(2026, 10, 10))
    assert voltou.receitas_do_banco[0]["status"] == "confirmada"
    assert any(e.incluida for e in voltou.ocorrencias if e.fonte == "recorrencia_banco")


# ── 5. plano e quem não tem banco ────────────────────────────────────────────

def test_plus_recebe_a_lista_sem_os_detalhes_e_pode_responder(novo):
    p = novo("pro")  # 'pro' gravado = Plus no v2
    salario_do_dono(p)
    j = ver(p)
    assert j["compromissos"] is None and j["capacidades"] == ["marcos"] and uma(j)["status"] == "pendente"
    c = responder(p, uma(j)["chave"], "sim")
    assert c.status_code == 200 and c.json()["compromissos"] is None and uma(c.json())["status"] == "confirmada"


def test_plano_sem_forecast_e_403_e_nada_e_gravado(novo):
    e = novo("essencial")
    salario_do_dono(e)
    r = responder(e, "qualquer", "sim")
    assert r.status_code == 403 and r.json()["error"]["code"] == "pro_required"
    assert marcas(e) == {}


def test_sem_banco_a_lista_e_vazia_e_a_receita_manual_segue(user_id):
    db.set_balance(user_id, D(1000))
    create_recurring_income(user_id, "Trabalho", 5000, "salario", 5, start_date=date(2026, 10, 1))
    s = ler_em(user_id)
    assert s.receitas_do_banco == [] and [e for e in s.ocorrencias if e.fonte == "receita_recorrente"]


# ── 6. as duas marcas não se tocam ───────────────────────────────────────────

def test_marca_de_receita_e_de_assinatura_sao_independentes(novo):
    a = novo()
    salario_do_dono(a)
    netflix_recente(a)
    antes = listar_assinaturas(a, date.today())
    assert antes["servicos"]
    chave = uma(ver(a))["chave"]
    responder(a, chave, "nao")
    assert listar_assinaturas(a, date.today()) == antes
    marcar(a, "netflix", "ignorar")
    assert uma(ver(a))["status"] == "negada"
    marcar(a, "netflix", "nenhuma")
    assert marcas(a) == {chave: ("ignorar", False)}


# ── 7. WhatsApp, IA e forecast leem a mesma conta ────────────────────────────

def test_forecast_e_ia_so_somam_a_confirmada(novo, monkeypatch):
    monkeypatch.delenv("OF_CONSOLIDATED_BALANCE_ENABLED", raising=False)
    a = novo()
    db.set_balance(a, D(1000))
    salario_do_dono(a)

    def olhar():
        fc = forecast_horizons(a, (30, 60, 90))["horizons"]
        return [D(str(fc[k]["projetado"])) for k in ("30", "60", "90")], D(str(_check_cashflow(a, {"days": 90})["projetado"]))

    (f30, f60, f90), ia = olhar()
    assert (f30, f60, f90, ia) == (D(1000),) * 4  # candidata nunca é somada
    responder(a, uma(ver(a))["chave"], "sim")
    (g30, g60, g90), ia = olhar()
    assert g30 > D(1000) and g90 > g60 > g30 and ia == g90


def _visao_hero(uid, monkeypatch):
    from core.services.ai_chat import cards
    monkeypatch.setattr(cards, "exigir_acesso_do_app", lambda u: None)
    bloco, visao = cards.montar("hero", uid, {"dias": None})
    return bloco, visao["abas"][0]


def test_visao_do_modelo_no_hero_nao_leva_a_candidata(novo, monkeypatch):
    a = novo()
    db.set_balance(a, D(1000))
    salario_do_dono(a)
    bloco, v = _visao_hero(a, monkeypatch)
    assert "receitas_do_banco" not in v and "EMPRESA" not in str(v) and "4100" not in str(v)
    assert v["marcos"] and v["base"]  # controle positivo: o resto da Previsão segue na visão
    assert uma(bloco["abas"][0]["recursos"]["previsao"])["status"] == "pendente"  # o app (bloco) segue vendo a pergunta
    negada = uma(ver(a))["chave"]
    responder(a, negada, "nao")
    assert "receitas_do_banco" not in _visao_hero(a, monkeypatch)[1]


def test_visao_do_modelo_no_hero_soma_a_receita_so_depois_do_sim(novo, monkeypatch):
    a = novo()
    db.set_balance(a, D(1000))
    salario_do_dono(a)
    antes = [m["saldo"] for m in _visao_hero(a, monkeypatch)[1]["marcos"]]
    responder(a, uma(ver(a))["chave"], "sim")
    v = _visao_hero(a, monkeypatch)[1]
    depois = [m["saldo"] for m in v["marcos"]]
    assert "receitas_do_banco" not in v and D(str(depois[-1])) > D(str(antes[-1]))


def test_visao_do_modelo_no_hero_lista_so_as_confirmadas(novo, monkeypatch):
    a = novo()
    db.set_balance(a, D(1000))
    salario_do_dono(a)
    na_visao = lambda: _visao_hero(a, monkeypatch)[1]
    v = na_visao()
    assert v["receitas_confirmadas"] == [] and "receitas_do_banco" not in v  # pendente: lista vazia, não ausente
    r = uma(ver(a))
    responder(a, r["chave"], "sim")
    v = na_visao()
    (x,) = v["receitas_confirmadas"]
    assert x == {"nome": r["nome"], "valor": float(D(r["valor"])), "dia": r["dia"]} and isinstance(x["dia"], int)
    assert r["chave"] not in str(v) and "receitas_do_banco" not in v
    responder(a, r["chave"], "desfazer")
    assert na_visao()["receitas_confirmadas"] == []
    responder(a, r["chave"], "nao")
    assert na_visao()["receitas_confirmadas"] == []


def test_visao_hero_so_a_confirmada_e_nome_cortado_em_60():
    from core.services.ai_chat.cards import visao_hero
    rec = lambda n, st: {"chave": f"extrato#{n}", "nome": n, "valor": 10.5, "dia": 5, "status": st}
    longo = "N" * 80
    v = visao_hero({"previsao": {"marcos": [1], "receitas_do_banco": [
        rec("PEND", "pendente"), rec(longo, "confirmada"), rec("NEG", "negada")]}})
    assert v["receitas_confirmadas"] == [{"nome": "N" * 60, "valor": 10.5, "dia": 5}]
    assert v["marcos"] == [1] and "receitas_do_banco" not in v and "extrato#" not in str(v)


@pytest.mark.parametrize("motivo", ["_interrompida", "_fora_da_base"])
def test_confirmada_interrompida_segue_listada_mas_nao_entra_na_conta(novo, monkeypatch, motivo):
    """Comportamento aceito: o pagador parou (+40 dias) ou a conta saiu da base; a receita fica na lista
    para o Desfazer e contribui 0."""
    from core.services import previsao_recorrencias
    a = novo()
    db.set_balance(a, D(1000))
    salario_do_dono(a)
    responder(a, uma(ver(a))["chave"], "sim")
    antes = _visao_hero(a, monkeypatch)[1]
    assert len(antes["receitas_confirmadas"]) == 1 and len(na_conta(ver(a))) == 1
    assert D(str(antes["marcos"][-1]["saldo"])) > D(1000)  # sem a interrupção ela soma
    monkeypatch.setattr(previsao_recorrencias, motivo, lambda *a: True)
    depois = _visao_hero(a, monkeypatch)[1]
    assert [r["nome"] for r in depois["receitas_confirmadas"]] == ["EMPRESA X"]
    assert na_conta(ver(a)) == [] and all(D(str(m["saldo"])) == D(1000) for m in depois["marcos"])  # contribui R$ 0


# ── 8. só é pergunta a candidata que o Sim mudaria ───────────────────────────

def test_candidata_parada_ha_120_dias_nao_e_pergunta(user_id):
    ult = HOJE - timedelta(days=120)
    semeia(user_id, [conta("acc", mensais("s", ["4000"] * 3, ultima=ult, desc="EMPRESA X", pagador=DOC))], [])
    assert ler_em(user_id).receitas_do_banco == []


def test_candidata_de_conta_fora_da_base_nao_e_pergunta(user_id):
    semeia(user_id, [conta("acc", mensais("s", ["4000"] * 3, desc="EMPRESA X", pagador=DOC), saldo=None)], [])
    assert ler_em(user_id).receitas_do_banco == []


def test_candidata_ativa_na_base_segue_pergunta_e_o_sim_entra_na_conta(user_id):
    semeia(user_id, [conta("acc", mensais("s", ["4000"] * 3, desc="EMPRESA X", pagador=DOC))], [])
    (r,) = ler_em(user_id).receitas_do_banco
    assert r["status"] == "pendente"
    marcar(user_id, r["chave"], "assinatura")
    assert any(e.incluida for e in do_banco(ler_em(user_id)))


def test_confirmada_e_negada_seguem_listadas_mesmo_interrompidas_ou_fora_da_base(user_id):
    ult = HOJE - timedelta(days=120)
    semeia(user_id, [conta("acc", mensais("s", ["4000"] * 3, ultima=ult, desc="EMPRESA X", pagador=DOC))], [])
    chave = ler_em(user_id, hoje=ult + timedelta(days=5)).receitas_do_banco[0]["chave"]
    for marca, status in (("assinatura", "confirmada"), ("ignorar", "negada")):
        marcar(user_id, chave, marca)
        assert [r["status"] for r in ler_em(user_id).receitas_do_banco] == [status]


@pytest.mark.parametrize("hoje,listada", [(date(2026, 10, 5), False),   # credito hoje: o proximo cai a 31 dias, fora dos 30 do Plus
                                           (date(2026, 10, 6), True)])    # no dia seguinte o proximo cai a 30 dias
def test_pendente_so_e_pergunta_se_o_sim_incluir_ocorrencia_em_30_dias(user_id, hoje, listada):
    semeia(user_id, [conta("acc", mensais("s", ["4000"] * 3, ultima=date(2026, 10, 5), desc="EMPRESA X", pagador=DOC))], [])
    for dias in (30, 90):  # a lista nao depende do horizonte pedido
        assert [r["status"] for r in ler_em(user_id, hoje=hoje, dias=dias).receitas_do_banco] == (["pendente"] if listada else [])
    chave = ler_em(user_id, hoje=date(2026, 10, 6)).receitas_do_banco[0]["chave"]
    marcar(user_id, chave, "assinatura")  # confirmada/negada ficam sempre, para desfazer
    assert len(ler_em(user_id, hoje=hoje, dias=30).receitas_do_banco) == 1
    assert any(e.incluida for e in do_banco(ler_em(user_id, hoje=hoje, dias=30))) == listada


@pytest.mark.parametrize("atras,listada", [(40, True), (41, False)])  # fronteira de DIAS_ATIVA (_interrompida)
def test_pendente_na_fronteira_de_dias_ativa(user_id, atras, listada):
    semeia(user_id, [conta("acc", mensais("s", ["4000"] * 3, ultima=HOJE - timedelta(days=atras), desc="EMPRESA X", pagador=DOC))], [])
    assert bool(ler_em(user_id).receitas_do_banco) == listada


def test_desfazer_nao_apaga_marca_de_assinatura(novo):
    a = novo()
    chaves = ("netflix", "xextrato#abc", "EXTRATO#x")  # marcas do proprio usuario que NAO sao receita
    for k in chaves:
        marcar(a, k, "ignorar")
    for k in (*chaves, "extrato#", "extrato#netflix"):
        assert responder(a, k, "desfazer").status_code == 404
    assert marcas(a) == {k: ("ignorar", False) for k in chaves}


# ── 9. desfazer a marca de uma receita que deixou de ser detectada ───────────

def _confirma_e_perde_a_deteccao(a):
    u = date.today() - timedelta(days=35)
    semeia(a, [conta("acc-sal", mensais("sal", ["4000"] * 3, ultima=u, desc="EMPRESA X", pagador=DOC))], [])
    chave = uma(ver(a))["chave"]
    assert responder(a, chave, "sim").status_code == 200
    # Pix avulso do mesmo CPF 28 dias depois do último elo: a cadeia some da lista
    semeia(a, [conta("acc-av", [tx("avx", "100", u + timedelta(days=28), desc="PIX", pagador=DOC)])], [])
    assert ver(a)["receitas_do_banco"] == [] and len(linhas(a)) == 1
    return chave


def test_desfazer_marca_orfa_do_proprio_usuario_funciona(novo):
    a = novo()
    chave = _confirma_e_perde_a_deteccao(a)
    assert responder(a, chave, "sim").status_code == 404 and len(linhas(a)) == 1  # sim/nao seguem exigindo a lista
    r = responder(a, chave, "desfazer")
    assert r.status_code == 200 and linhas(a) == []


def test_desfazer_chave_de_outro_usuario_ou_sem_marca_segue_404(novo):
    a, b = novo(), novo()
    chave = _confirma_e_perde_a_deteccao(a)
    assert responder(b, chave, "desfazer").status_code == 404  # b nao tem marca nessa chave
    assert responder(b, "extrato#" + "0" * 16, "desfazer").status_code == 404
    assert [x["user_id"] for x in linhas(a, b)] == [a]  # a marca de a ficou intacta
