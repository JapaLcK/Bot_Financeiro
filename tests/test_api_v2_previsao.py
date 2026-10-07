"""Previsão v2 pelo endpoint montado: sessão e DB pytest_UUID reais."""
from datetime import timedelta
from decimal import Decimal as D

import psycopg
import pytest
from fastapi.testclient import TestClient

import db
import db_support
import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import sessao_de
from api.v2.previsao import Previsao
from conftest import promote_to_pro, usuario_pagante
from core.services import cashflow_snapshot, previsao_v2
from core.services.cashflow import _projection
from db.bills import create_boleto, ensure_bill_instance, mark_bill_paid
from db.recurring import create_recurring_expense
from tests._patrimonio_helpers import conexao, conta
from tests.test_cashflow_snapshot import q
from utils_date import now_tz

ROTA = "/api/v2/previsao"


@pytest.fixture
def uid(monkeypatch):
    u = usuario_pagante("pro_max")
    monkeypatch.setenv("DASHBOARD_V2_BETA_EMAILS", "")
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", str(u))
    monkeypatch.setenv("OF_CONSOLIDATED_BALANCE_ENABLED", "0")
    return u


def get(quem=None, *, bearer=False, **params):
    c = TestClient(dashboard.app)
    if quem is None:
        return c.get(ROTA, params=params)
    sessao = sessao_de(quem)
    if bearer:
        return c.get(ROTA, params=params, headers={"Authorization": f"Bearer {sessao['access']}"})
    c.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, sessao["dashboard"])
    return c.get(ROTA, params=params)


def ok(quem, **params):
    r = get(quem, **params)
    assert r.status_code == 200, r.text
    assert Previsao.model_validate(r.json()).model_dump(mode="json") == r.json()
    return r.json()


def recorrente(uid, nome, valor, offset=1, **kwargs):
    due = now_tz().date() + timedelta(days=offset)
    return create_recurring_expense(uid, nome, valor, "outros", due.day, "account",
                                   frequency="once", start_date=due, **kwargs)


@pytest.mark.parametrize("dias", [30, 60, 90])
def test_pro_horizonte_serie_marcos_e_precisao_das_fontes(uid, dias):
    q("update accounts set balance=10.005 where user_id=%s", (uid,))
    r = recorrente(uid, "Precisão", 1)
    q("update recurring_expenses set amount=2.675 where id=%s and user_id=%s", (r["id"], uid))
    corpo = ok(uid, dias=dias, bearer=True)
    assert corpo["base"]["saldo"] == "10.005"
    assert corpo["ancora"]["saldo"] == "10.00"
    assert len(corpo["trajetoria"]) == dias
    assert [m["dias"] for m in corpo["marcos"]] == [n for n in (30, 60, 90) if n <= dias]
    assert corpo["dias_permitidos"] == [30, 60, 90]
    assert corpo["capacidades"] == ["marcos", "trajetoria", "pior_dia", "compromissos"]
    assert corpo["periodo"]["inicio"] == corpo["trajetoria"][0]["data"]
    for m in corpo["marcos"]:
        assert m["saldo"] == corpo["trajetoria"][m["dias"] - 1]["saldo"] == "7.33"
    evento = corpo["compromissos"][0]["ocorrencias"][0]
    assert evento["valor"] == "2.675"
    assert corpo["trajetoria"][0]["compromissos"] == corpo["pior_dia"]["causas"] == [evento]
    assert corpo["pior_dia"]["data"] == corpo["trajetoria"][0]["data"]
    assert corpo["pior_dia"]["saldo"] == "7.33"
    assert corpo["cabe_nas_premissas"] is corpo["cobertura"]["inclui_estimativa_variavel"] is False


def test_plus_default_sem_detalhe_nem_trajectory(uid, monkeypatch):
    promote_to_pro(uid, "pro")
    recorrente(uid, "SEGREDO-COMPROMISSO", 7)
    def proibido(*args):
        pytest.fail("Plus não calcula trajetória")
    monkeypatch.setattr(previsao_v2, "_trajectory", proibido)
    c = ok(uid)
    assert c["dias"] == 30 and c["dias_permitidos"] == [30]
    assert c["capacidades"] == ["marcos"]
    assert all(c[k] is None for k in ("periodo", "ancora", "trajetoria", "pior_dia", "compromissos"))
    assert "SEGREDO-COMPROMISSO" not in str(c)
    assert all(set(m) == {"codigo", "direcao_do_erro"} for m in c["motivos"])


@pytest.mark.parametrize("entrada", ["", "31", "30.0", "-30", "NaN", "Inf", "999999999999", "abc"])
def test_query_recusada_antes_da_snapshot(uid, monkeypatch, entrada):
    monkeypatch.setattr(cashflow_snapshot, "ler", lambda *a: pytest.fail("leitura financeira"))
    r = get(uid, dias=entrada)
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
    assert "input" not in r.text


@pytest.mark.parametrize("plano,dias,codigo", [
    ("essencial", "30", "pro_required"), ("pro", "60", "forecast_horizon_not_allowed"),
    ("pro", "90", "forecast_horizon_not_allowed")])
def test_recurso_e_horizonte_recusam_antes_da_snapshot(uid, monkeypatch, plano, dias, codigo):
    promote_to_pro(uid, plano)
    monkeypatch.setattr(cashflow_snapshot, "ler", lambda *a: pytest.fail("leitura financeira"))
    r = get(uid, dias=dias)
    assert r.status_code == 403 and r.json()["error"]["code"] == codigo


@pytest.mark.parametrize("caso,status,codigo", [
    ("sem_sessao", 401, "unauthenticated"), ("fora_beta", 404, "dashboard_v2_disabled"),
    ("inativo", 402, "subscription_required"), ("exclusao", 403, "forbidden")])
def test_sessao_real_recusa_antes_da_snapshot(uid, monkeypatch, caso, status, codigo):
    if caso == "fora_beta":
        monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", "")
    elif caso == "inativo":
        q("update auth_accounts set plan='free' where user_id=%s", (uid,))
        db.mark_plan_selected(uid)
        db_support.invalidate_auth_user_cache(uid)
    elif caso == "exclusao":
        q("update auth_accounts set deletion_status='scheduled', deletion_scheduled_for=now() + interval '7 days' where user_id=%s", (uid,))
        db_support.invalidate_auth_user_cache(uid)
    monkeypatch.setattr(cashflow_snapshot, "ler", lambda *a: pytest.fail("leitura financeira"))
    r = get(None if caso == "sem_sessao" else uid)
    assert r.status_code == status and r.json()["error"]["code"] == codigo


def test_a_b_isolados_e_chaves_opacas_estaveis(uid, monkeypatch):
    a = uid
    b = usuario_pagante("pro_max")
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", f"{a},{b}")
    db.set_balance(a, D(100))
    db.set_balance(b, D(7))
    recorrente(a, "SEGREDO-A", 2)
    recorrente(b, "SOMENTE-B", 3)
    rb = ok(b, user_id=a, uid=a)
    ra = ok(a)
    assert rb["base"]["saldo"] == "7" and "SEGREDO-A" not in str(rb)
    assert ra["base"]["saldo"] == "100" and "SOMENTE-B" not in str(ra)
    evento = rb["compromissos"][0]["ocorrencias"][0]
    assert len(evento["chave"]) == 64 and ":" not in evento["chave"]
    assert evento["chave"] == ok(b)["compromissos"][0]["ocorrencias"][0]["chave"]
    assert evento["chave"] != ra["compromissos"][0]["ocorrencias"][0]["chave"]
    assert not {"origem_id", "efeito_quantificado", "payload", "raw"} & set(evento)


def test_snapshot_unica_read_only_bancos_v2_e_rollback(uid, monkeypatch):
    conta(conexao(uid, f"previsao-{uid}"), "provider-SEGREDO", "10.005")
    chamadas = []
    original = cashflow_snapshot.ler
    def ler(cur, user, hoje, ate, agora, bancos):
        assert user == uid and bancos is True and ate - hoje == timedelta(days=30)
        cur.execute("show transaction_isolation")
        assert cur.fetchone()["transaction_isolation"] == "repeatable read"
        cur.execute("show transaction_read_only")
        assert cur.fetchone()["transaction_read_only"] == "on"
        cur.execute("select current_database() as nome")
        assert cur.fetchone()["nome"].startswith("pytest_")
        s = original(cur, user, hoje, ate, agora, bancos)
        chamadas.append(s)
        return s
    monkeypatch.setattr(cashflow_snapshot, "ler", ler)
    c = ok(uid)
    assert len(chamadas) == 1
    assert c["base"]["saldo"] == "10.005" and c["base"]["origem"] == "consolidated"
    assert D(c["base"]["saldo"]) == D(TestClient(dashboard.app).get("/api/v2/contas", headers={
        "Authorization": f"Bearer {sessao_de(uid)['access']}"}).json()["total"])
    assert c["marcos"][0]["saldo"] == str(_projection(chamadas[0].hoje, chamadas[0].base,
        chamadas[0].ocorrencias, chamadas[0].hoje + timedelta(days=30))["projetado"])
    assert "provider-SEGREDO" not in str(c)


def test_snapshot_repeatable_read_com_pagamento_concorrente(uid, monkeypatch):
    from db import contas_hoje
    db.add_launch_and_update_balance(uid, "receita", 100, "Espécie", None)
    b = create_boleto(uid, "Antecipada", 20, now_tz().date() + timedelta(days=1))
    original = contas_hoje.listar
    def intercalar(cur, user, **kwargs):
        d = original(cur, user, **kwargs)
        mark_bill_paid(user, b["id"], 20, metodo="carteira")
        return d
    monkeypatch.setattr(contas_hoje, "listar", intercalar)
    c = ok(uid)
    assert c["base"]["saldo"] == "100" and c["marcos"][0]["saldo"] == "80.00"
    assert c["compromissos"][0]["ocorrencias"][0]["realizacao"] == "a_conferir"
    monkeypatch.setattr(contas_hoje, "listar", original)
    depois = ok(uid)
    assert D(depois["base"]["saldo"]) == D(80) and depois["compromissos"] == []
    assert all(p["compromissos"] == [] for p in depois["trajetoria"])
    assert depois["pior_dia"]["causas"] == []


def test_get_nao_escreve_repara_expira_ou_emite_sse(uid, monkeypatch):
    from api.v2 import eventos
    from db.schema import TABELAS_QUE_AVISAM
    b = create_boleto(uid, "Vencida", 2, now_tz().date() - timedelta(days=1))
    db.set_pending_action(uid, "bill_amount_expected", {"bill_id": b["id"]}, minutes=-1)
    def foto():
        linhas = {}
        for t, dono in TABELAS_QUE_AVISAM.items():
            filtro = "t.user_id=%s"
            if dono == "conexao":
                filtro = "t.connection_id in (select id from open_finance_connections where user_id=%s)"
            elif dono == "conta":
                filtro = "t.account_id in (select a.id from open_finance_accounts a join open_finance_connections c on c.id=a.connection_id where c.user_id=%s)"
            linhas[t] = q(f"select to_jsonb(t) as linha from {t} t where {filtro} order by to_jsonb(t)::text", (uid,))
        return linhas
    antes = foto()
    pendente = q("select * from pending_actions where user_id=%s", (uid,))
    monkeypatch.setattr(eventos, "avisar", lambda *a: pytest.fail("GET emitiu SSE"))
    c = ok(uid)
    assert foto() == antes
    assert q("select * from pending_actions where user_id=%s", (uid,)) == pendente
    assert c["compromissos"][0]["ocorrencias"][0]["data"] < c["hoje"]


@pytest.mark.parametrize("saldo", [None, "0"])
def test_indisponivel_e_zero_sao_distintos_uid(uid, saldo):
    q("update accounts set balance=%s::numeric where user_id=%s", ("NaN" if saldo is None else saldo, uid))
    recorrente(uid, "Conhecido", 2)
    c = ok(uid)
    assert c["base"]["saldo"] == saldo
    assert bool(c["compromissos"])
    assert c["compromissos"][0]["ocorrencias"][0]["incluida_no_calculo"] is True
    assert (c["estado"] == "indisponivel") is (saldo is None)
    assert (c["pior_dia"] is None) is (saldo is None)
    assert all((p["saldo"] is None) is (saldo is None) for p in c["trajetoria"])


def test_homonimos_desconhecidos_estimados_excluidos_e_ciclo_dedup(uid):
    due = now_tz().date() + timedelta(days=1)
    r = recorrente(uid, "Homônimo", 10)
    ensure_bill_instance(r["id"], uid, due, 12)
    recorrente(uid, "Homônimo", 4, variable_amount=True)
    desconhecido = recorrente(uid, "Sem valor/data", 0, variable_amount=True)
    q("update recurring_expenses set frequency='estranha' where id=%s and user_id=%s", (desconhecido["id"], uid))
    card = db.create_card(uid, "Cartão", due.day, due.day)
    create_recurring_expense(uid, "No cartão", 7, "outros", due.day, "credit_card", card_id=card,
                             frequency="once", start_date=due)
    c = ok(uid)
    eventos = [e for g in c["compromissos"] for e in g["ocorrencias"]]
    assert len(eventos) == 4
    assert len([g for g in c["compromissos"] if g["nome"] == "Homônimo"]) == 2
    sem = next(e for e in eventos if e["nome"] == "Sem valor/data")
    assert sem["valor"] is sem["data"] is None and sem["qualidade_valor"] == "desconhecido"
    excluido = next(e for e in eventos if e["nome"] == "No cartão")
    assert excluido["incluida_no_calculo"] is False and excluido["valor"] == "7"
    assert c["marcos"][0]["saldo"] == "-16.00"
    assert c["compromissos"][-1]["primeira_data"] is None


def test_erro_db_estrutural_vira_envelope_503(uid, monkeypatch):
    def falhar(*args):
        raise psycopg.OperationalError("connection refused")
    monkeypatch.setattr(cashflow_snapshot, "ler", falhar)
    r = get(uid)
    assert r.status_code == 503 and r.json()["error"]["code"] == "service_unavailable"


@pytest.mark.parametrize("caso", ["fresca", "outra_moeda", "conexao_pausada", "saldo_ausente_no_sync",
                                  "nan", "sync_velho_49h", "moeda_presumida", "conciliacao_pendente"])
def test_recorte_bancario_preserva_cobertura_e_diferenca_explicada(uid, caso):
    from tests.test_api_v2_contas import CASOS
    db.set_balance(uid, D(100))
    montar, total, *_ = CASOS[caso]
    montar(uid)
    c = ok(uid)
    esperado = "100" if caso == "moeda_presumida" else total
    assert D(c["base"]["saldo"]) == D(esperado)
    assert c["base"]["bancos_excluidos"] is False
    if caso == "moeda_presumida":
        assert "moeda_presumida" in c["base"]["motivos"] and D(total) != D(esperado)
    if caso == "conciliacao_pendente":
        assert "conciliacao_a_conferir" in [m["codigo"] for m in c["motivos"]]


@pytest.mark.parametrize("limite", ["virada", "frescor", "pendencia", "ia_fronteira"])
def test_instante_unico_validez_virada_ano_frescor_e_ttl(uid, monkeypatch, limite):
    from datetime import datetime
    from psycopg.types.json import Jsonb
    from db.ai_chat import PENDING_TTL_MINUTES
    from utils_date import _tz
    agora = datetime(2026, 12, 31, 23, 55, tzinfo=_tz())
    monkeypatch.setattr(previsao_v2, "now_tz", lambda: agora)
    esperado = agora.replace(hour=0, minute=0) + timedelta(days=1)
    if limite == "frescor":
        esperado = agora + timedelta(minutes=2)
        cid = conexao(uid, f"validade-{uid}", sync=esperado - timedelta(hours=48))
        conta(cid, "saldo", 1)
    elif limite == "pendencia":
        esperado = agora + timedelta(minutes=1)
        q("insert into pending_actions (user_id,action_type,payload,expires_at) values (%s,'bill_amount_expected',%s,%s)",
          (uid, Jsonb({"nome": "SEGREDO"}), esperado))
    elif limite == "ia_fronteira":
        esperado = agora
        q("insert into ai_pending_actions (user_id,tool_name,tool_args,summary,created_at) values (%s,'create_bill',%s,'Teste',%s)",
          (uid, Jsonb({"nome": "SEGREDO"}), agora - timedelta(minutes=PENDING_TTL_MINUTES)))
    c = ok(uid)
    assert datetime.fromisoformat(c["calculado_em"]) == agora
    assert datetime.fromisoformat(c["valido_ate"]) == esperado
    assert c["hoje"] == "2026-12-31" and c["trajetoria"][0]["data"] == "2027-01-01"
    assert c["cobertura"]["janela_conferencia_inicio"] is None
    assert "SEGREDO" not in str(c)


def test_faturas_cartoes_ciclos_distintos_restante_exato_e_sem_reparo(uid):
    from tests.test_cashflow_prazo_fatura import fatura, observadas
    hoje = now_tz().date()
    card1, card2 = (db.create_card(uid, f"Cartão {n}", 10, 17) for n in range(2))
    fatura(uid, card1, hoje, "open", "5.005", "2.330")
    fatura(uid, card1, hoje + timedelta(days=30), "closed", 4)
    fatura(uid, card2, hoje, "paid", -1)
    antes = observadas(uid)
    c = ok(uid, dias=90)
    gs = [g for g in c["compromissos"] if g["fonte"] == "fatura"]
    assert len(gs) == 3 and len({g["chave"] for g in gs}) == 3
    es = [e for g in gs for e in g["ocorrencias"]]
    assert "2.675" in [e["valor"] for e in es]
    assert None in [e["valor"] for e in es]
    assert len({e["ciclo"] for e in es}) == 2
    assert observadas(uid) == antes


def test_sem_truncamento_de_grupos_dentro_do_horizonte(uid):
    primeiro = recorrente(uid, "Todos os compromissos", 1)
    q("""insert into recurring_expenses
        (user_id,name,amount,category,due_day,payment_type,is_active,start_date,frequency)
        select user_id,name,amount,category,due_day,payment_type,is_active,start_date,frequency
        from recurring_expenses cross join generate_series(1,100)
        where user_id=%s and id=%s""", (uid, primeiro["id"]))
    recorrente(uid, "FORA DO HORIZONTE", 999, offset=31)
    c = ok(uid, dias=30)
    assert len(c["compromissos"]) == len(c["trajetoria"][0]["compromissos"]) == 101
    assert c["marcos"][0]["saldo"] == "-101.00"
    assert "FORA DO HORIZONTE" not in str(c)


def test_snapshot_read_only_recusa_escrita_e_nao_oculta_falha(uid, monkeypatch):
    original = cashflow_snapshot.ler
    recusas = []
    def tentar_escrever(cur, *args):
        snapshot = original(cur, *args)
        try:
            cur.execute("update accounts set balance=123 where user_id=%s", (uid,))
        except psycopg.errors.ReadOnlySqlTransaction:
            recusas.append(True)
            raise
        return snapshot
    monkeypatch.setattr(cashflow_snapshot, "ler", tentar_escrever)
    r = get(uid)
    assert r.status_code == 500, r.text
    assert r.json()["error"]["code"] == "internal_error" and recusas == [True]
    assert q("select balance from accounts where user_id=%s", (uid,))[0]["balance"] == D(0)



def test_inclusao_variavel_sem_valor_com_data_e_obrigacoes_validas(uid):
    db.set_balance(uid, D(100))
    recorrente(uid, "Variável desconhecida", 0, variable_amount=True)
    recorrente(uid, "Estimativa válida", 7, variable_amount=True)
    recorrente(uid, "Hoje", 3, offset=0)
    create_boleto(uid, "Vencida", 2, now_tz().date() - timedelta(days=1))
    c = ok(uid)
    es = {e["nome"]: e for g in c["compromissos"] for e in g["ocorrencias"]}
    sem = es["Variável desconhecida"]
    assert sem["data"] == c["trajetoria"][0]["data"]
    assert sem["valor"] is None and sem["qualidade_valor"] == "desconhecido"
    assert sem["incluida_no_calculo"] is False
    assert {m["codigo"] for m in sem["motivos"]} == {"valor_recorrente_desconhecido"}
    estimada = es["Estimativa válida"]
    assert estimada["valor"] == "7" and estimada["qualidade_valor"] == "estimado"
    assert estimada["realizacao"] == "prevista" and estimada["incluida_no_calculo"] is True
    for nome in ("Hoje", "Vencida"):
        assert es[nome]["realizacao"] == "a_conferir" and es[nome]["incluida_no_calculo"] is True
        assert es[nome]["data"] <= c["hoje"]
    assert c["ancora"]["saldo"] == "95.00" and c["marcos"][0]["saldo"] == "88.00"
    assert c["trajetoria"][0]["saldo"] == c["pior_dia"]["saldo"] == "88.00"
    for colecao in (c["trajetoria"][0]["compromissos"], c["pior_dia"]["causas"]):
        assert {e["chave"] for e in colecao} == {sem["chave"], estimada["chave"]}
        assert next(e for e in colecao if e["chave"] == sem["chave"]) == sem
        assert next(e for e in colecao if e["chave"] == estimada["chave"]) == estimada


@pytest.mark.parametrize("fonte,valor,motivo", [
    ("instancia", "0", "valor_boleto_desconhecido"),
    ("instancia", "NaN", "valor_boleto_desconhecido"),
    ("receita_recorrente", "0", "valor_recorrente_desconhecido"),
    ("receita_recorrente", "NaN", "valor_recorrente_desconhecido"),
    ("fatura", "-1", "valor_fatura_a_conferir"),
])
def test_inclusao_irmaos_sem_valor_utilizavel_com_data(uid, monkeypatch, fonte, valor, motivo):
    from db.recurring_income import create_recurring_income
    from tests.test_cashflow_prazo_fatura import fatura
    due = now_tz().date() + timedelta(days=1)
    if fonte == "instancia":
        b = create_boleto(uid, "Irmão desconhecido", 1, due)
        q("update bill_instances set amount=%s::numeric where id=%s and user_id=%s", (valor, b["id"], uid))
    elif fonte == "receita_recorrente":
        r = create_recurring_income(uid, "Irmão desconhecido", 1, "salário", due.day, start_date=due)
        if valor == "0":
            from db import recurring_income
            original = recurring_income.ler_receitas
            def sem_valor(cur, user):
                rows = original(cur, user)
                assert user == uid and len(rows) == 1 and rows[0]["id"] == r["id"]
                # Writer e CHECK recusam zero; fixture na fronteira do leitor puro.
                return [{**row, "amount": D(0)} for row in rows]
            monkeypatch.setattr(recurring_income, "ler_receitas", sem_valor)
        else:
            q("update recurring_incomes set amount=%s::numeric where id=%s and user_id=%s", (valor, r["id"], uid))
    else:
        card = db.create_card(uid, "Irmão desconhecido", due.day, due.day)
        fatura(uid, card, due, "open", valor)
        from psycopg.types.json import Jsonb
        aid = conta(conexao(uid, f"inclusao-fatura-{uid}"), "cartao", 0)
        q("update open_finance_accounts set type='CREDIT', raw=%s where id=%s", (Jsonb({
            "currencyCode": "BRL", "creditData": {"balanceCloseDate": due.isoformat(),
                                                      "balanceDueDate": due.isoformat()}}), aid))
        q("update credit_cards set open_finance_account_id=%s where id=%s and user_id=%s", (aid, card, uid))
    recorrente(uid, "Saída válida", 2)
    c = ok(uid)
    e = next(e for g in c["compromissos"] for e in g["ocorrencias"] if e["fonte"] == fonte)
    assert e["data"] == due.isoformat() and e["valor"] is None
    assert e["qualidade_valor"] == "desconhecido" and e["incluida_no_calculo"] is False
    assert e["qualidade_data"] == "conhecida"
    assert motivo in {m["codigo"] for m in e["motivos"]}
    assert c["marcos"][0]["saldo"] == "-2.00"
    assert next(item for item in c["trajetoria"][0]["compromissos"] if item["chave"] == e["chave"]) == e
    if fonte == "receita_recorrente":
        assert all(item["chave"] != e["chave"] for item in c["pior_dia"]["causas"])
    else:
        assert next(item for item in c["pior_dia"]["causas"] if item["chave"] == e["chave"]) == e


def test_inclusao_instancia_valor_positivo_sem_data(uid, monkeypatch):
    from db import bills
    b = create_boleto(uid, "Sem data", 5, now_tz().date() + timedelta(days=1))
    original = bills.ler_instancias
    def sem_data(cur, user):
        rows = original(cur, user)
        assert user == uid and len(rows) == 1 and rows[0]["id"] == b["id"]
        # NOT NULL no DB atual: representar a row incompleta na fronteira do leitor.
        return [{**row, "due_date": None} for row in rows]
    monkeypatch.setattr(bills, "ler_instancias", sem_data)
    c = ok(uid)
    e = c["compromissos"][0]["ocorrencias"][0]
    assert e["valor"] == "5" and e["data"] is None
    assert e["incluida_no_calculo"] is False and e["realizacao"] == "a_conferir"
    assert "data_boleto_desconhecida" in {m["codigo"] for m in e["motivos"]}
    assert c["ancora"]["saldo"] == c["marcos"][0]["saldo"] == "0.00"
    assert all(p["compromissos"] == [] for p in c["trajetoria"])
    assert c["pior_dia"]["causas"] == []


def test_inclusao_fatura_presumida_e_cartao_excluido(uid):
    from tests.test_cashflow_prazo_fatura import fatura
    due = now_tz().date() + timedelta(days=1)
    card = db.create_card(uid, "Fatura presumida", due.day, due.day)
    fatura(uid, card, due, "open", 11)
    create_recurring_expense(uid, "Excluída no cartão", 7, "outros", due.day, "credit_card", card_id=card,
                             frequency="once", start_date=due)
    c = ok(uid)
    es = {e["nome"]: e for g in c["compromissos"] for e in g["ocorrencias"]}
    e = es["Fatura presumida"]
    assert e["data"] == due.isoformat() and e["qualidade_data"] == "presumida"
    assert e["valor"] == "11" and e["incluida_no_calculo"] is True
    assert e["realizacao"] == "a_conferir"
    excluida = es["Excluída no cartão"]
    assert excluida["data"] == due.isoformat() and excluida["valor"] == "7"
    assert excluida["incluida_no_calculo"] is False
    assert "incorporacao_cartao_nao_comprovada" in {m["codigo"] for m in excluida["motivos"]}
    assert c["marcos"][0]["saldo"] == "-11.00"
    assert c["trajetoria"][0]["compromissos"] == c["pior_dia"]["causas"] == [e]


def test_inclusao_zero_decimal_utilizavel_no_contrato():
    from core.services.cashflow_contract import Ocorrencia
    e = Ocorrencia("instancia", 1, "zero", now_tz().date(), "boleto", "Zero", D(0), "saida")
    assert previsao_v2._ocorrencia(1, e)["incluida_no_calculo"] is True
