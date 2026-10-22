"""PL-06 PR A1: contrato v1 dos insights com o texto dos detectores (sem LLM, D12) e plano na leitura do cache.

Postgres real; o `_call_llm` é trocado só para provar que ninguém o chama."""
import json
from datetime import date

import pytest

import db
from _cards_apoio import _http, mostrar
from _ia_stream_helpers import executar, linhas, openai_falso, rodada
from conftest import promote_to_pro, usuario_pagante
from core import ai_patterns
from db.pockets import update_pocket_meta
from test_api_app_sessao import completo


@pytest.fixture
def llm(monkeypatch):
    """Guarda "sem LLM" (D12): registra o `_call_llm` e o construtor do cliente `openai.OpenAI`, com a chave de
    API presente para nenhum caminho sair cedo. Os insights não podem chamar nenhum dos dois."""
    import openai
    chamadas = []
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    monkeypatch.setattr("core.ai_patterns._call_llm", lambda *a, **k: chamadas.append("_call_llm") or [])
    monkeypatch.setattr(openai, "OpenAI", lambda *a, **k: chamadas.append("OpenAI"))
    return chamadas


def _orcamento_apertando(uid, alvo="Mercado X", nota=None, categoria="mercado"):
    db.upsert_budget(uid, categoria, 100)
    db.add_launch_and_update_balance(uid, "despesa", 90, alvo, nota, categoria=categoria)


def _usuario(plano):
    return completo(usuario_pagante(plan=plano))


def _sem_pro(it):
    return not ({"acoes", "impacto"} & set(it))


# ── 1. sem LLM: o texto é o dos detectores ──────────────────────────────────────

def test_insights_saem_dos_detectores_sem_chamar_o_llm(llm):
    uid = _usuario("pro_max")
    _orcamento_apertando(uid)
    r = _http(uid).get(f"/insights/{uid}/current").json()
    ai_patterns.generate_ai_insights(uid, force=True)
    assert llm == []
    (it,) = r["insights"]
    assert (it["title"], it["message"]) == ("Mercado tá apertando",
                                            f"90% do orçamento de mercado e faltam {_dias_restantes()} dias.")
    e = it["explicacao"]
    assert it["key"] == f"budget_warning:mercado:{date.today():%Y-%m}" and "redacao" not in e
    assert (e["versao"], e["tipo"], e["familia"], e["fonte"]["base"]) == (1, "budget_warning", "desvio", "orcamentos")
    assert (e["referencia"]["valor"], e["atual"]["valor"], e["diferenca"]) == (
        100.0, 90.0, {"valor": -10.0, "percentual": -10.0})
    assert e["periodo"]["parcial"] is True and e["calculado_em"]


def test_titulo_mensagem_e_icone_tem_os_tetos_do_sanitizador_antigo():
    from core.services.insights_contrato import montar_item, periodo_mes
    it = montar_item("of_bill_increase", "x", periodo_mes(date(2026, 10, 1)), title="T" * 300, message="M" * 300,
                     severity="info", icon="📈📈📈📈📈", action_label=None, action_view=None, referencia={}, atual={})
    assert (len(it["title"]), len(it["message"]), len(it["icon"])) == (140, 240, 4)


# ── 1b. detector em falha não vira "nada a apontar" por 6 h ────────────────────

def test_detector_em_falha_nao_grava_o_cache_nem_diz_sem_destaques(llm, monkeypatch):
    import db.insights as detectores
    uid = _usuario("pro_max")
    _orcamento_apertando(uid)
    _, pid, _ = db.create_pocket(uid, "Viagem")
    update_pocket_meta(uid, pid, target_amount=10000, target_date=f"{date.today().year + 1}-01-01")

    def quebra(*_a, **_k):
        raise RuntimeError("banco fora")
    monkeypatch.setattr(detectores, "get_budgets_status_for_month", quebra)
    c = _http(uid)
    r = c.get(f"/insights/{uid}/current").json()
    assert [i["type"] for i in r["insights"]] == ["goal_behind"] and r["motivo"] is None  # o que funcionou sai
    assert ai_patterns.ler_insights(uid) is None  # nada no cache: o card `piggy` diz "ainda não está pronto"
    sem_meta = _usuario("pro_max")
    _orcamento_apertando(sem_meta)
    vazio = _http(sem_meta).get(f"/insights/{sem_meta}/current").json()
    assert (vazio["insights"], vazio["motivo"]) == ([], None)  # antes: ([], "sem_destaques") por 6 h
    monkeypatch.undo()  # a falha some: o próximo GET já mostra o fato
    r = _http(sem_meta).get(f"/insights/{sem_meta}/current").json()
    assert [i["type"] for i in r["insights"]] == ["budget_warning"] and r["motivo"] is None


def _dias_restantes():
    from db.insights import _days_in_month
    hoje = date.today()
    return max(0, _days_in_month(hoje) - hoje.day)


# ── 2. plano na leitura: downgrade com cache quente ─────────────────────────────

def test_pro_ve_acoes_e_quem_cai_para_plus_nao_ve_sem_recalcular(llm, monkeypatch):
    uid = _usuario("pro_max")
    _orcamento_apertando(uid)
    c = _http(uid)
    (it,) = c.get(f"/insights/{uid}/current").json()["insights"]
    # positivo: o Pro recebe o impacto e a ação calculados em código
    assert it["impacto"] == {"tipo": "restante", "valor": 10.0}
    assert it["acoes"][0]["valores"] == {"orcamento_valor": 100.0, "gasto_valor": 90.0}
    recalculos = []
    monkeypatch.setattr("db.compute_active_insights", lambda *a: recalculos.append(a) or ([], []))
    promote_to_pro(uid, "pro")  # Plus, sem limpar o cache de 6 h
    plus = c.get(f"/insights/{uid}/current").json()
    assert plus["insights"] and all(_sem_pro(i) for i in plus["insights"])
    assert plus["insights"][0]["explicacao"] == it["explicacao"]  # o Plus segue com a explicação
    r, (bloco,), _ = mostrar(uid, card="piggy")
    assert all(_sem_pro(i) for i in r["abas"][0]["insights"])
    assert all(_sem_pro(i) for i in bloco["abas"][0]["recursos"]["insights"]["insights"])
    assert recalculos == [] and llm == []  # veio do cache


def test_o_insight_de_um_usuario_nao_aparece_para_o_outro(llm):
    a, b = _usuario("pro_max"), _usuario("pro_max")
    _orcamento_apertando(a)
    _orcamento_apertando(b, categoria="segredob")
    feed_a = _http(a).get(f"/insights/{a}/current").json()
    r, (bloco,), _ = mostrar(a, card="piggy")
    assert [i["key"] for i in feed_a["insights"]] == [f"budget_warning:mercado:{date.today():%Y-%m}"]
    assert "segredob" not in json.dumps([feed_a, r, bloco], ensure_ascii=False)


def test_rota_de_debug_aplica_a_mesma_projecao_por_plano(llm, monkeypatch):
    monkeypatch.setenv("DEBUG_AI_ROUTES", "1")
    uid = _usuario("pro")  # Plus
    _orcamento_apertando(uid)

    def payload():
        r = _http(uid).get(f"/debug/ai/{uid}/payload", params={"kind": "insights"})
        assert r.status_code == 200, r.text
        assert r.json()["payload"]["falhas"] == []
        return r.json()["payload"]["itens"]
    assert payload() and all(_sem_pro(i) for i in payload())
    promote_to_pro(uid, "pro_max")  # positivo: o Pro vê
    assert all("acoes" in i for i in payload())


# ── 3. conta do OF: chave pelo mês da cobrança ─────────────────────────────────

def _conta_of(uid, dia, valor):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("insert into launches (user_id, tipo, valor, alvo, criado_em, posted_at, source) "
                    "values (%s, 'despesa', %s, 'Enel Luz', %s, %s, 'open_finance')", (uid, valor, dia, dia))
        conn.commit()


def _conta_of_no_feed(uid):
    (it,) = [i for i in ai_patterns.generate_ai_insights(uid, force=True) if i["type"] == "of_bill_increase"]
    return it


def _mes_menos(d, n):
    m = d.month - 1 - n
    return date(d.year + m // 12, m % 12 + 1, 10)


def test_conta_do_open_finance_chave_pelo_mes_da_cobranca(llm):
    uid = _usuario("pro_max")
    hoje = date.today()
    _conta_of(uid, _mes_menos(hoje, 2), 100)
    _conta_of(uid, _mes_menos(hoje, 1), 130)
    it = _conta_of_no_feed(uid)
    mes_passado = f"{_mes_menos(hoje, 1):%Y-%m}"
    e = it["explicacao"]
    assert it["key"] == f"of_bill_increase:enel luz:{mes_passado}" and it["action_view"] is None
    assert (e["referencia"]["valor"], e["referencia"]["meses"], e["atual"]) == (
        100.0, 1, {"valor": 130.0, "data": _mes_menos(hoje, 1).isoformat()})
    assert e["diferenca"] == {"valor": 30.0, "percentual": 30.0} and e["fonte"]["base"] == "open_finance"
    assert _sem_pro(it)  # sem frequência conhecida, sem impacto anual (D8)
    # outra cobrança no MESMO mês, com outro valor: o mesmo fato (a piora a A2 compara pela severidade)
    _conta_of(uid, _mes_menos(hoje, 1).replace(day=20), 160)
    assert _conta_of_no_feed(uid)["key"] == it["key"]
    # cobrança num mês novo: fato novo
    _conta_of(uid, hoje, 200)
    assert _conta_of_no_feed(uid)["key"] == f"of_bill_increase:enel luz:{hoje:%Y-%m}"


def test_a_nota_da_conta_do_of_nao_entra_no_insight(llm):
    """O detector do OF lê texto livre (`alvo or nota`): com `alvo`, a nota (que pode ter dado pessoal) fica fora."""
    uid = _usuario("pro_max")
    hoje = date.today()
    with db.get_conn() as conn, conn.cursor() as cur:
        for dia, valor in ((_mes_menos(hoje, 1), 100), (hoje, 130)):
            cur.execute("insert into launches (user_id, tipo, valor, alvo, nota, criado_em, posted_at, source) "
                        "values (%s, 'despesa', %s, 'Enel Luz', 'pix 11 98888-7777 SEGREDO', %s, %s, "
                        "'open_finance')", (uid, valor, dia, dia))
        conn.commit()
    it = _conta_of_no_feed(uid)
    texto = json.dumps(it, ensure_ascii=False)
    assert it["title"].startswith("Enel Luz") and "98888" not in texto and "SEGREDO" not in texto


def test_nome_da_caixinha_sai_em_uma_linha_sem_marcacao(llm):
    uid = _usuario("pro_max")
    _, pid, _ = db.create_pocket(uid, "Viagem *Maria*​")
    update_pocket_meta(uid, pid, target_amount=10000, target_date=f"{date.today().year + 1}-01-01")
    (meta,) = [i for i in ai_patterns.generate_ai_insights(uid, force=True) if i["type"] == "goal_behind"]
    assert meta["explicacao"]["referencia"]["caixinha"] == "Viagem Maria"


# ── 4. lista vazia com motivo explícito ─────────────────────────────────────────

def test_sem_dados_e_sem_destaques(llm):
    vazio = _usuario("pro")
    r = _http(vazio).get(f"/insights/{vazio}/current").json()
    assert (r["insights"], r["motivo"]) == ([], "sem_dados")
    calmo = _usuario("pro")
    db.add_launch_and_update_balance(calmo, "despesa", 30, "Padaria", None, categoria="padaria")
    r = _http(calmo).get(f"/insights/{calmo}/current").json()
    assert (r["insights"], r["motivo"]) == ([], "sem_destaques")
    # positivo: com fato, `motivo` nulo
    _orcamento_apertando(calmo)
    r = _http(calmo).get(f"/insights/{calmo}/current").json()  # o cache vazio vale 6 h: sem fato novo
    assert r["insights"] == []
    ai_patterns.generate_ai_insights(calmo, force=True)
    r = _http(calmo).get(f"/insights/{calmo}/current").json()
    assert r["insights"] and r["motivo"] is None and llm == []


# ── 5. a conversa: Plus pede o card, conversa de outro assunto, cai para Essencial ─

def test_conversa_plus_ve_o_card_sem_acoes_e_essencial_e_recusado(monkeypatch, llm):
    uid = _usuario("pro_max")
    _orcamento_apertando(uid)
    ai_patterns.generate_ai_insights(uid)  # aquece como Pro: o cache tem `acoes`
    promote_to_pro(uid, "pro")
    openai_falso(monkeypatch, rodada(None, ("show_card", {"card": "piggy"})), rodada("Olha o que eu notei."))
    r, ev, _ = executar(uid, "o que você notou?")
    assert r.reply == "Olha o que eu notei."
    (bloco,) = [e["bloco"] for e in ev if e["tipo"] == "bloco"]
    itens = bloco["abas"][0]["recursos"]["insights"]["insights"]
    assert [i["type"] for i in itens] == ["budget_warning"] and all(_sem_pro(i) for i in itens)
    openai_falso(monkeypatch, rodada(None, ("add_launch", {"tipo": "despesa", "valor": 50, "alvo": "mercado",
                                                           "categoria": "mercado"})))
    r2, ev2, _ = executar(uid, "gastei 50 no mercado")  # a tool responde direto, com o próprio texto
    assert "Despesa registrada" in r2.reply and not [e for e in ev2 if e["tipo"] == "bloco"]
    # o lançamento mudou o estado de verdade: o mesmo fato, agora estourado (o cache de 6 h segue com o antigo)
    assert ai_patterns.ler_insights(uid)[0]["severity"] == "warning"
    (depois,) = ai_patterns.generate_ai_insights(uid, force=True)
    assert (depois["key"], depois["severity"]) == (itens[0]["key"], "critical")
    promote_to_pro(uid, "essencial")
    openai_falso(monkeypatch, rodada(None, ("show_card", {"card": "piggy"})), rodada("Esse card é do Plus."))
    _, ev3, _ = executar(uid, "e agora, o que você notou?")
    assert not [e for e in ev3 if e["tipo"] == "bloco"]
    assert json.loads([x for x in linhas(uid) if x["role"] == "tool"][-1]["content"])["error"] == "pro_required"
    assert llm == []
