"""Veredito do simulador de compra (PR 3a): estado real no banco, pela rota `POST /api/v2/simulador`.

Só V2c e V9 são puros: o estado que provam não é alcançável com dado real (toda conta com banco
carrega `cobertura_bancaria_nao_comprovada`). A Carteira nasce de lançamento verificável: o saldo
sem lançamento a deixa "não confirmada" e tudo vira inconclusivo.
"""
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import sessao_de
from conftest import usuario_pagante
from core.services import carteira, previsao_v2, veredito as ver
from core.services.cashflow_contract import JANELA_CONFERENCIA_DIAS, Motivo, Ocorrencia, Snapshot
from core.services.decision_simulator import Simulacao, simulate
from db.bills import create_boleto
from db.recurring import create_recurring_expense
from db.recurring_income import create_recurring_income
from tests._patrimonio_helpers import conexao, conta, horas_atras
from tests.test_api_v2_simulador import ok
from tests.test_cashflow_snapshot import q

RAIZ = Path(__file__).parent.parent


def hoje() -> date:
    return previsao_v2.now_tz().date()


@pytest.fixture
def novo(monkeypatch):
    ids: list[str] = []

    def f(saldo=0):
        u = usuario_pagante("pro_max")
        ids.append(str(u))
        monkeypatch.setenv("DASHBOARD_V2_BETA_EMAILS", "")
        monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", ",".join(ids))
        if saldo:
            carteira.lancar(u, "receita", saldo, "Saldo", "Saldo inicial", "outros")
        return u
    return f


def gasto(u, nome, valor, dias, **kw):
    d = hoje() + timedelta(days=dias)
    return create_recurring_expense(u, nome, valor, "outros", d.day, "account", frequency="once", start_date=d, **kw)


def salario(u, valor, dias, nome="Salário"):
    """Receita mensal cujo dia do mês é o de hoje+dias, com início há 60 dias."""
    d = hoje() + timedelta(days=dias)
    return create_recurring_income(u, nome, valor, "outros", d.day, start_date=hoje() - timedelta(days=60))


def cen(nome="Compra", preco="100", **kw):
    return {"nome": nome, "preco": preco, **kw}


def rodar(u, *cenarios, reserva=None):
    corpo = {"cenarios": list(cenarios) or [cen()]}
    if reserva:
        corpo["reserva_minima"] = reserva
    r = ok(u, corpo)
    for v in (r["atual"]["veredito"], *(c["resumo"]["veredito"] for c in r["cenarios"])):
        if v:
            assert v["estado"] != "cabe_nas_premissas" and r["cabe_nas_premissas"] is False  # o "cabe" nunca sai
    return r


def v_atual(r):
    return r["atual"]["veredito"]


def v_cen(r, i=0):
    return r["cenarios"][i]["resumo"]["veredito"]


def codigos(v):
    return [b["codigo"] for b in v["bloqueios"]]


def iso(dias):
    return (hoje() + timedelta(days=dias)).isoformat()


# ── V1: o cenário muda o veredito; V1b: positivo ────────────────────────────

@pytest.mark.parametrize("preco,estado,valor", [("800", "risco_identificado", "-100.00"), ("600", "sem_risco_identificado", None)])
def test_v1_a_compra_decide_o_cenario_e_o_atual_nao_muda(novo, preco, estado, valor):
    u = novo(1000)
    gasto(u, "Aluguel", 300, 5)
    r = rodar(u, cen(preco=preco))
    assert v_atual(r) == {"estado": "sem_risco_identificado", "data": None, "valor": None, "bloqueios": [], "premissa": None}
    assert v_cen(r) == {"estado": estado, "data": iso(5) if valor else None, "valor": valor, "bloqueios": [], "premissa": None}


# ── V2: banco. Toda conta com banco é inconclusiva em 3a; 48 h é o limite do `banco_desatualizado` ──

@pytest.mark.parametrize("horas,velho", [(49, True), (47, False)])
def test_v2_banco_sempre_inconclusivo_e_48h_vem_da_fonte_unica(novo, horas, velho):
    u = novo(1000)
    c = conexao(u, "item-v2", sync=horas_atras(horas))
    conta(c, "acc-v2", D("100"))
    r = rodar(u, cen(preco="10"))
    for v in (v_atual(r), v_cen(r)):
        assert v["estado"] == "dados_insuficientes" and v["data"] is None and v["valor"] is None
        assert "cobertura_bancaria_nao_comprovada" in codigos(v)  # o limite declarado de 3a (PR 4a troca)
        assert ("banco_desatualizado" in codigos(v)) is velho
    if velho:
        b = next(b for b in v_atual(r)["bloqueios"] if b["codigo"] == "banco_desatualizado")
        assert "48 horas" in b["texto"] and b["itens"] == []


def _snap(saldo, motivos=()):
    return Snapshot(date(2026, 10, 6), datetime.now(timezone.utc), {"saldo": D(saldo)}, motivos=list(motivos))


def test_v2c_puro_47h_vira_nao_recomendo_e_desatualizado_pede_atualizar():
    v = ver.veredito(_snap("-10"), [], 0, 90)
    assert (v["estado"], v["data"], v["valor"], v["bloqueios"]) == ("risco_identificado", "2026-10-06", D("-10.00"), [])
    v = ver.veredito(_snap("-10", [Motivo("banco_desatualizado", "ambos")]), [], 0, 90)
    assert v["estado"] == "dados_desatualizados" and codigos(v) == ["banco_desatualizado"] and v["data"] is None
    v = ver.veredito(_snap("-10", [Motivo("saldo_ausente", "ambos")]), [], 0, 90)
    assert v["estado"] == "dados_insuficientes"
    assert ver.veredito(Snapshot(date(2026, 10, 6), datetime.now(timezone.utc), {"saldo": None}), [], 0, 90)["bloqueios"][0]["codigo"] == "saldo_indisponivel"


# ── V3/V3b/V4/V4b: a janela de 3 dias ───────────────────────────────────────

def _janela(novo, dias_pagamento, receita=1000):
    u = novo(100)
    salario(u, receita, dias_pagamento)
    gasto(u, "Conta grande", 500, 5)
    return rodar(u, cen(preco="50"))


@pytest.mark.parametrize("dias", [-2, -3])
def test_v3_receita_na_janela_vira_pergunta_quando_o_risco_depende_dela(novo, dias):
    v = v_cen(_janela(novo, dias))
    assert v["estado"] == "dados_insuficientes" and codigos(v) == [ver.DUVIDA_RECENTE]
    assert v["bloqueios"][0]["itens"] == [{"nome": "Salário", "data": iso(dias)}]
    assert v["premissa"] == f"Considerei que o previsto antes de {hoje() - timedelta(days=JANELA_CONFERENCIA_DIAS):%d/%m} já aconteceu."


def test_v4_fora_da_janela_e_risco(novo):
    v = v_cen(_janela(novo, -4))
    assert (v["estado"], v["valor"], v["bloqueios"]) == ("risco_identificado", "-450.00", [])
    assert v["premissa"].startswith("Considerei que o previsto antes de ")


def test_v4b_a_janela_nao_pergunta_quando_o_risco_sobrevive_sem_ela(novo):
    v = v_cen(_janela(novo, -2, receita=100))  # com a receita e sem ela, o dia +5 fica negativo
    assert (v["estado"], v["valor"], v["bloqueios"]) == ("risco_identificado", "-350.00", [])


# ── V5: o que só melhora a conta não bloqueia o "não recomendo" ──────────────

def test_v5_so_melhora_nao_bloqueia(novo):
    u = novo(1000)
    gasto(u, "Aluguel", 300, 5)
    salario(u, 100, 20, "Bônus")
    r = rodar(u, cen(preco="800"))
    assert {"gastos_variaveis_nao_estimados", "receita_nao_garantida"} <= {m["codigo"] for m in r["motivos"]}
    assert v_cen(r)["estado"] == "risco_identificado" and v_cen(r)["valor"] == "-100.00"


# ── V6: valor estimado é estrutural; sem a flag é V1 ─────────────────────────

def test_v6_valor_estimado_bloqueia(novo):
    u = novo(1000)
    gasto(u, "Luz", 300, 5, variable_amount=True)
    r = rodar(u, cen(preco="800"))
    for v in (v_atual(r), v_cen(r)):
        assert v["estado"] == "dados_insuficientes" and codigos(v) == ["valor_recorrente_estimado"]
        assert v["data"] is None and v["valor"] is None


# ── V7: a compra não é a causa ───────────────────────────────────────────────

def test_v7_atual_ja_negativo(novo):
    u = novo(100)
    gasto(u, "Conta grande", 500, 5)
    r = rodar(u, cen(preco="50"))
    assert (v_atual(r)["estado"], v_atual(r)["valor"]) == ("risco_identificado", "-400.00")
    assert (v_cen(r)["estado"], v_cen(r)["valor"]) == ("risco_identificado", "-450.00")
    assert D(v_cen(r)["valor"]) < D(v_atual(r)["valor"])


# ── V8: isolamento ───────────────────────────────────────────────────────────

def test_v8_dado_de_outro_usuario_nao_entra(novo):
    a = novo(1000)
    gasto(a, "Aluguel", 300, 5)
    antes = rodar(a, cen(preco="600"))
    b = novo()
    db.set_balance(b, D("5"))  # Carteira não confirmada
    conta(conexao(b, "item-b", sync=horas_atras(100)), "acc-b", D("1"))
    create_boleto(b, "Boleto B", 10000, hoje() + timedelta(days=2))
    gasto(b, "Fixo B", 99999, 1, variable_amount=True)
    rodar(b, cen(preco="1"))
    depois = rodar(a, cen(preco="600"))
    assert v_atual(depois) == v_atual(antes) and v_cen(depois) == v_cen(antes)
    assert v_cen(depois)["estado"] == "sem_risco_identificado" and v_cen(depois)["bloqueios"] == []


# ── V9: sem bloqueio não vira "cabe" ─────────────────────────────────────────

def test_v9_sem_bloqueio_e_sem_risco_nunca_e_cabe():
    v = ver.veredito(_snap("1000000"), [], 0, 90)
    assert v["estado"] == "sem_risco_identificado" and v["bloqueios"] == []


# ── V10: parcelas têm data presumida ─────────────────────────────────────────

def test_v10_parcelas_com_data_presumida_perguntam_e_a_entrada_decide(novo):
    u = novo(1000)
    r = rodar(u, cen("Parcelado", "3000", parcelas=3), cen("À vista", "3000"))
    p = v_cen(r, 0)
    assert p["estado"] == "dados_insuficientes" and codigos(p) == ["data_parcela_presumida"]
    assert [i["nome"] for i in p["bloqueios"][0]["itens"]] == [f"Parcelado: parcela {k}/3" for k in (1, 2)]  # até o 1º dia abaixo; a 3ª cai depois
    assert (v_cen(r, 1)["estado"], v_cen(r, 1)["valor"]) == ("risco_identificado", "-2000.00")
    r = rodar(u, cen("Entrada", "1300", entrada="1100", parcelas=2))  # a entrada, de data conhecida, já fura
    assert (v_cen(r)["estado"], v_cen(r)["valor"], v_cen(r)["bloqueios"]) == ("risco_identificado", "-100.00", [])


def test_v10b_despesa_mensal_nova_tem_data_presumida(novo):
    u = novo(100)
    v = v_cen(rodar(u, cen(preco="10", despesa_mensal_nova="200")))
    assert v["estado"] == "dados_insuficientes" and codigos(v) == ["data_despesa_mensal_presumida"]


# ── V11: reserva ─────────────────────────────────────────────────────────────

def test_v11_sem_reserva_so_o_negativo_conta_e_com_reserva_abaixo_dela_tambem(novo):
    u = novo(1000)
    gasto(u, "Aluguel", 300, 5)
    assert v_cen(rodar(u, cen(preco="500")))["estado"] == "sem_risco_identificado"
    v = v_cen(rodar(u, cen(preco="500"), reserva="300"))
    assert (v["estado"], v["data"], v["valor"]) == ("risco_identificado", iso(5), "200.00")


# ── V12: boleto pendente pode já ter sido pago ───────────────────────────────

def test_v12_boleto_pendente_vira_pergunta(novo):
    u = novo(500)
    create_boleto(u, "Luz", 800, hoje() + timedelta(days=3))
    v = v_cen(rodar(u, cen(preco="10")))
    assert v["estado"] == "dados_insuficientes" and codigos(v) == ["realizacao_boleto_a_conferir"]
    assert v["bloqueios"][0]["itens"] == [{"nome": "Luz", "data": iso(3)}]


# ── V13/V14/V15: chave, legado, app ──────────────────────────────────────────

def test_v13_chave_desliga_so_o_veredito(novo, monkeypatch):
    u = novo(1000)
    gasto(u, "Aluguel", 300, 5)
    ligado = rodar(u, cen(preco="800"))
    monkeypatch.setenv("SIMULADOR_VEREDITO_ENABLED", "0")
    desligado = rodar(u, cen(preco="800"))
    assert v_atual(desligado) is None and v_cen(desligado) is None and v_cen(ligado)
    sem = lambda r: ({k: v for k, v in r["atual"].items() if k != "veredito"},
                     [{**c, "resumo": {k: v for k, v in c["resumo"].items() if k != "veredito"}} for c in r["cenarios"]])
    assert sem(desligado) == sem(ligado)


def test_v14_saida_legada_nao_tem_veredito(novo):
    u = novo(1000)
    r = simulate(u, Simulacao.model_validate({"cenarios": [{"nome": "x", "preco": 100.0}]}))
    assert "veredito" not in r["atual"] and "veredito" not in r["cenarios"][0]["resumo"]


def test_v15_rota_do_app_devolve_o_mesmo_veredito(novo):
    u = novo(1000)
    gasto(u, "Aluguel", 300, 5)
    q("update auth_accounts set open_finance_onboarding_completed_at=now() where user_id=%s", (u,))
    c = TestClient(dashboard.app)
    c.headers["Authorization"] = f"Bearer {sessao_de(u)['access']}"
    r = c.post("/api/app/simulador", json={"cenarios": [cen(preco="800")]})
    assert r.status_code == 200, r.text
    assert r.json()["cenarios"][0]["resumo"]["veredito"] == v_cen(rodar(u, cen(preco="800")))


# ── V16: todo código que a snapshot emite tem texto ──────────────────────────

def test_v16_todo_codigo_emitido_tem_texto_e_o_desconhecido_nao_sai_cru():
    from db.contas_hoje import MOTIVOS_CARTEIRA, MOTIVOS_CONTA
    codigos_ = set(MOTIVOS_CARTEIRA + MOTIVOS_CONTA) | {ver.DUVIDA_RECENTE, "saldo_indisponivel"}
    for arq, padrao in (("core/services/cashflow_snapshot.py", r"_motivo\(\s*s,\s*'([a-z_]+)'"),
                        ("core/services/previsao_recorrencias.py", r"_motivo\(\s*s,\s*'([a-z_]+)'"),
                        ("core/services/decision_simulator.py", r"Motivo\('([a-z_]+)'")):
        achados = set(re.findall(padrao, (RAIZ / arq).read_text(encoding="utf-8")))
        assert achados, arq
        codigos_ |= achados
    assert not codigos_ - set(ver.TEXTO), sorted(codigos_ - set(ver.TEXTO))
    assert ver._texto("codigo_que_ninguem_emite") == ver.GENERICO
    assert "{" not in ver._texto("banco_desatualizado") and "48" in ver._texto("recorrencias_banco_nao_lidas")


# ── T1: a janela de 3 dias (laço de `cashflow_snapshot`) só pergunta o que ainda não tem resposta ──

def _fixo_na_janela(u, valor=800, **kw):
    r = gasto(u, "Fixo da janela", valor, -2, **kw)
    return r["id"] if isinstance(r, dict) else r


def _instancia(u, rid, status):
    """`paid` = pago de verdade: lançamento da Carteira que o boleto aponta (só `status` não prova a saída)."""
    lid = carteira.lancar(u, "despesa", 800, "Fixo da janela", "Fixo da janela", "outros")["launch_id"] if status == "paid" else None
    q("insert into bill_instances (recurring_id,user_id,due_date,amount,status,launch_id,paid_amount) values (%s,%s,%s,800,%s,%s,%s)",
      (rid, u, hoje() - timedelta(days=2), status, lid, 800 if lid else None))


def test_t1a_instancia_do_ciclo_pendente_nao_duplica_a_pergunta(novo):
    u = novo(100)
    _instancia(u, _fixo_na_janela(u), "pending")
    v = v_cen(rodar(u, cen(preco="10")))
    assert v["estado"] == "dados_insuficientes" and codigos(v) == ["realizacao_boleto_a_conferir"]  # sem `realizacao_recente_a_conferir`
    assert [i["data"] for i in v["bloqueios"][0]["itens"]] == [iso(-2)]


def test_t1a_instancia_do_ciclo_paga_nao_vira_pergunta(novo):
    u = novo(1000)
    _instancia(u, _fixo_na_janela(u), "paid")
    v = v_cen(rodar(u, cen(preco="10")))
    assert v["estado"] == "sem_risco_identificado", v


def test_t1b_fixo_no_cartao_na_janela_nao_vira_item(novo):
    u = novo(100)
    card = db.create_card(u, "Cartão", 10, 10)
    d = hoje() - timedelta(days=2)
    create_recurring_expense(u, "Cartão fixo", 800, "outros", d.day, "credit_card", card_id=card, frequency="once", start_date=d)
    assert v_cen(rodar(u, cen(preco="10")))["estado"] == "sem_risco_identificado"


@pytest.mark.parametrize("valor", ["0"])  # NULL é inalcançável: recurring_expenses.amount é NOT NULL
def test_t1c_fixo_sem_valor_ou_zerado_na_janela_nao_vira_item(novo, valor):
    u = novo(500)
    rid = _fixo_na_janela(u)
    q(f"update recurring_expenses set amount={valor} where id=%s and user_id=%s", (rid, u))
    create_boleto(u, "Luz", 800, hoje() + timedelta(days=3))  # a pergunta do boleto existe; a do fixo não pode somar-se
    v = v_cen(rodar(u, cen(preco="10")))
    assert v["estado"] == "dados_insuficientes" and ver.DUVIDA_RECENTE not in codigos(v) and codigos(v)[0] == "realizacao_boleto_a_conferir"


# ── T2: fronteira de `< limite` ──────────────────────────────────────────────

@pytest.mark.parametrize("gasto_,reserva", [(200, "700"), (900, None)])  # pior dia == reserva; pior dia == 0
def test_t2_pior_dia_exatamente_no_limite_nao_e_risco(novo, gasto_, reserva):
    u = novo(1000)
    gasto(u, "Aluguel", gasto_, 5)
    assert v_cen(rodar(u, cen(preco="100"), reserva=reserva))["estado"] == "sem_risco_identificado"


def test_t2_no_limite_pela_alternativa_tambem_nao_pergunta(novo):
    u = novo(1000)
    create_boleto(u, "Boleto", 500, hoje() + timedelta(days=3))
    gasto(u, "Aluguel", 499, 5)
    v = v_cen(rodar(u, cen(preco="1")))  # com a dúvida, o pior dia é exatamente 0
    assert v["estado"] == "sem_risco_identificado" and v["bloqueios"] == []


# ── T3: os itens da pergunta vão só até o 1º dia abaixo do limite ────────────

def test_t3_duvida_depois_do_primeiro_dia_abaixo_nao_entra_nos_itens(novo):
    u = novo(500)
    create_boleto(u, "Luz", 800, hoje() + timedelta(days=3))
    create_boleto(u, "Depois", 10, hoje() + timedelta(days=40))
    v = v_cen(rodar(u, cen(preco="10")))
    assert v["bloqueios"][0]["itens"] == [{"nome": "Luz", "data": iso(3)}]


# ── T4: ajuste de conciliação/declaração (puro: com banco, `cobertura_bancaria_nao_comprovada` bloqueia antes) ──

def _ocorrencia(valor, dias):
    return Ocorrencia("gasto_recorrente", 1, "c", hoje() + timedelta(days=dias), "gasto_fixo", "Fixo",
                      D(valor), "saida")


@pytest.mark.parametrize("codigo", ["conciliacao_a_conferir", "declaracao_bancaria_a_conferir"])
def test_t4a_ajuste_a_favor_que_tira_o_risco_vira_bloqueio(codigo):
    s = Snapshot(hoje(), datetime.now(timezone.utc), {"saldo": D(0)}, [_ocorrencia(50, 2)],
                 [Motivo(codigo, "so_melhora", 7, efeito_quantificado=D(100))])
    v = ver.veredito(s, [], 0, 90)  # no pior caso 0+100-50 >= 0; só com a dúvida contra (sem o ajuste) o dia +2 fica em -50
    assert v["estado"] == "dados_desatualizados" and codigos(v) == [codigo]


def test_t4b_ajuste_contra_nao_entra_no_pior_caso_do_risco():
    s = Snapshot(hoje(), datetime.now(timezone.utc), {"saldo": D(0)}, [],
                 [Motivo("conciliacao_a_conferir", "so_piora", 7, efeito_quantificado=D(-100))])
    assert ver.veredito(s, [], 0, 90)["estado"] == "sem_risco_identificado"


# ── T5: chave de emergência ──────────────────────────────────────────────────

@pytest.mark.parametrize("valor,esperado", [("0", False), ("false", False), ("no", False), ("off", False),
                                            ("OFF", False), (" False ", False), ("1", True), (None, True)])
def test_t5_so_zero_false_no_off_desligam(monkeypatch, valor, esperado):
    monkeypatch.delenv("SIMULADOR_VEREDITO_ENABLED", raising=False)
    if valor is not None:
        monkeypatch.setenv("SIMULADOR_VEREDITO_ENABLED", valor)
    assert ver.ligado() is esperado
