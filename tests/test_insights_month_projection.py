"""PL-06 B3: o `month_projection` (família `projecao`), o mês que fecha no vermelho pelo pior caso do simulador.

Decisões do dono (2026-10-29): a regra do simulador (janela de conferência no lugar de `realizacao_passada_desconhecida`),
lida no último dia do mês e nunca no pior dia (P1); o dispensado volta só se a gravidade subir (P2); saldo de hoje >= 0
→ warning, < 0 → critical, os dois só com fechamento < 0 (P3); logo depois do `bill_due` no laço (P4); os textos da P6,
com a ressalva sobre Gastos fixos que nunca sai por tamanho; o chat pode discordar (P7, issue #1326). Postgres real;
relógio do detector em 20/10/2026 10:00 (`insights_detectores.now_tz`), menos na ordem (caso 18) e na conversa (19).

Emenda 1: só avisa quem já informou o saldo (`carteira_qualidade.saldo_informado`, lançamento interno `saldo_inicial`,
`ajuste` ou `ajuste_saldo`); casos A a H.

Controles negativos medidos à mão no PR (mutação numa cópia da árvore, os casos que ficam vermelhos): fechamento sem a
janela (`estruturais(snapshot.motivos)`) → 1, 2, 4, 5, 9, 10, 10b, 13–16, 19, C, D, D2, D3, G; `saldo <= 0` critical →
2; aviso só com saldo < 0, sem exigir fechamento < 0 → 4; `fecha > 0` → 5; somar a estimativa do dia a dia → 7;
`eventos_com_janela` sem a `conferencia` → 10 (e 8 do simulador, V3 e V4b entre eles); `JANELA_CONFERENCIA_DIAS` 4 →
1, 10 (e 3 do simulador, V4 entre eles); `hoje` em UTC → 11; sem o gate `forecast` na geração E na leitura → 14, 15 (só
na geração: verde, a leitura corta); sem o recurso na leitura → 15; gravidade única → 3, 12, 16, 17, 18, F, H[com];
depois do orçamento no laço → 18; o pior dia no lugar do fim do mês → 4, 9; sem a `NOTA` → 1, 3, 12; sem `impacto` →
13; `estruturais_com_janela` sem `JANELA_SUBSTITUI` → os mesmos do 1º item (e 16 do simulador); `premissa_da_janela`
None → 1, 10 (e 6 do simulador); sem a guarda `saldo_informado` → A, B, D4, E1, F, G, H[sem] (o E2 segue verde: o
import já fica calado por `carteira_nao_confirmada`); sem `"ajuste"` na lista → D, D3; sem `"ajuste_saldo"` → D2;
consulta sem `user_id = %s` → F; sem `and is_internal_movement` → D4; com `and tipo = 'receita'` → D3; a rota
`initial-balance` gravando outra categoria → C, G, H[com]; a rota `adjust-balance` gravando outra categoria → D, D3.
Positivos: 10b (sem o salário da janela a cena avisa), 13 (o Pro vê `impacto`), 14 (Pro legado vê), 15 (com o recurso
aparece), 16 (déficit maior com a mesma gravidade não ressuscita), C, D, D2, D3 (quem informou recebe).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal as D

import pytest

import core.handle_incoming as hi
import db
from _cards_apoio import _http
from _ia_stream_helpers import executar, openai_falso, rodada
from conftest import promote_to_pro, usuario_pagante
from core import ai_patterns
from core.services import cashflow, insights_detectores as detectores, veredito as ver
from core.services.cashflow_forecast import trajetoria_com_hoje
from core.services.cashflow_snapshot import carregar
from core.services.insights_contrato import montar_item, periodo_mes
from core.types import IncomingMessage
from db.accounts import delete_launch_and_rollback
from db.bills import create_boleto
from db.recurring import create_recurring_expense, update_recurring_expense
from db.recurring_income import create_recurring_income
from test_api_app_sessao import completo
from test_api_v2_perfil import cliente
from test_insights_contrato import _usuario, llm  # noqa: F401
from test_insights_estado import _put
from tests._patrimonio_helpers import conexao, conta, horas_atras
from utils_date import _tz, day_tz, now_tz
from utils_text import VALOR_MAX

HOJE = date(2026, 10, 20)
AGORA = datetime(2026, 10, 20, 10, tzinfo=_tz())
FIM = date(2026, 10, 31)
ANTIGO = datetime(2026, 4, 1, 12, tzinfo=_tz())  # fora das janelas da estimativa do dia a dia
AGOSTO = date(2026, 8, 1)
NOTA = "A Previsão mensal de Gastos fixos considera só recorrências e pode mostrar outro valor."


def _relogio(monkeypatch, agora=AGORA):
    monkeypatch.setattr(detectores, "now_tz", lambda: agora)


def _carteira(uid, valor):
    """Saldo da Carteira por lançamentos antigos (fora da estimativa): o saldo inicial de 1.000 que o onboarding grava
    (`saldo_inicial`, interno; sem ele o aviso não sai, emenda 1) e o que falta para `valor`."""
    db.add_launch_and_update_balance(uid, "receita", 1000, "setup", "Saldo inicial", "saldo_inicial", ANTIGO,
                                     is_internal_movement=True)
    if valor != 1000:
        db.add_launch_and_update_balance(uid, "despesa" if valor < 1000 else "receita", abs(1000 - valor), "Acerto",
                                         None, "outros", ANTIGO)


def _aluguel(uid, valor=1000, dia=25):
    return create_recurring_expense(uid, "Aluguel", valor, "moradia", dia, "account", start_date=AGOSTO)["id"]


def _salario(uid, valor=2000, dia=25):
    return create_recurring_income(uid, "Salário", valor, "salario", dia, start_date=AGOSTO)


def _mes(uid):
    """Os `month_projection` que o usuário recebe: recalcula e lê pela rota."""
    ai_patterns.generate_ai_insights(uid, force=True)
    r = _http(uid).get(f"/insights/{uid}/current")
    assert r.status_code == 200, r.text
    return [i for i in r.json()["insights"] if i["type"] == "month_projection"]


def _paridade(uid, hoje=HOJE):
    """O insight é o simulador lido no fim do mês (caso 8): mesma conclusão, mesmo número, mesmo silêncio."""
    fim = periodo_mes(hoje)["fim"]
    snap = carregar(uid, hoje, fim)
    fecha = cashflow.fechamento_pior_caso(snap, fim)
    v = ver.veredito(snap, [], 0, (fim - hoje).days)
    if fecha is None:
        assert v["estado"].startswith("dados_") and (cashflow.estruturais_com_janela(snap) or snap.base["saldo"] is None)
        return None
    base, pior = cashflow.pior_caso_do_risco(snap, cashflow.eventos_com_janela(snap))
    assert trajetoria_com_hoje(hoje, base, pior, (fim - hoje).days, 0)["trajectory"][-1]["saldo_projetado"] == fecha
    if fecha < 0:
        assert v["estado"] == "risco_identificado"
    return fecha


# ── 1 a 5: a regra (pior caso no fim do mês) e a gravidade ──────────────────────

def test_1_fixo_antigo_que_estoura_avisa_warning(llm, monkeypatch):
    _relogio(monkeypatch)
    uid = _usuario("pro_max")
    _carteira(uid, 300)
    _aluguel(uid)
    (it,) = _mes(uid)
    assert (it["key"], it["severity"], it["icon"]) == ("month_projection:saldo:2026-10", "warning", "📉")
    assert it["title"] == "Outubro pode fechar no vermelho"
    assert it["message"] == f"Previsão para 31/10: R$ 700,00 no negativo, sem contar os gastos do dia a dia. {NOTA}"
    assert (it["action_label"], it["action_view"]) == ("Ver gastos fixos", "fixed")
    e = it["explicacao"]
    assert (e["tipo"], e["familia"], e["fonte"]["base"], e["periodo"]["inicio"]) == (
        "month_projection", "projecao", "previsao", "2026-10-01")
    assert e["referencia"] == {"estatistica": "previsao_pior_caso", "data_alvo": "2026-10-31", "saldo_hoje_valor": 300.0,
                               "premissa": "Considerei que o previsto antes de 17/10 já aconteceu."}
    assert (e["atual"], e["diferenca"], e["amostra"]) == ({"valor": -700.0}, None, None)
    assert _paridade(uid) == D("-700.00")


def test_2_saldo_zero_e_warning(llm, monkeypatch):
    _relogio(monkeypatch)
    uid = _usuario("pro_max")
    _carteira(uid, 0)
    _aluguel(uid)
    assert [i["severity"] for i in _mes(uid)] == ["warning"]
    assert _paridade(uid) == D("-1000.00")


def test_3_saldo_negativo_e_critical(llm, monkeypatch):
    _relogio(monkeypatch)
    uid = _usuario("pro_max")
    _carteira(uid, -80)
    (it,) = _mes(uid)
    assert (it["severity"], it["title"]) == ("critical", "Seu saldo está no vermelho")
    assert it["message"] == ("Seu saldo já está R$ 80,00 no negativo. Previsão para 31/10: R$ 80,00 no negativo, sem "
                             f"contar os gastos do dia a dia. {NOTA}")
    assert _paridade(uid) == D("-80.00")


def test_4_saldo_negativo_que_fecha_positivo_nao_avisa(llm, monkeypatch):
    _relogio(monkeypatch)
    uid = _usuario("pro_max")
    _carteira(uid, -80)
    _salario(uid, 500, 28)
    assert _mes(uid) == []
    assert _paridade(uid) == D("420.00")


@pytest.mark.parametrize("aluguel,avisa", [(300, False), (D("300.01"), True)])
def test_5_fechamento_zero_nao_avisa_e_um_centavo_avisa(llm, monkeypatch, aluguel, avisa):
    _relogio(monkeypatch)
    uid = _usuario("pro_max")
    _carteira(uid, 300)
    _aluguel(uid, aluguel)
    assert [i["explicacao"]["atual"]["valor"] for i in _mes(uid)] == ([-0.01] if avisa else [])
    assert _paridade(uid) == (D("-0.01") if avisa else D("0.00"))


# ── 6 e 7: quem o simulador cala ────────────────────────────────────────────────

def test_6_com_banco_conectado_fica_calado(llm, monkeypatch):
    _relogio(monkeypatch)
    uid = _usuario("pro_max")
    _carteira(uid, 300)
    _aluguel(uid)
    conta(conexao(uid, "item-b3", sync=horas_atras(1)), "acc-b3", D("10"))
    assert _mes(uid) == []
    assert _paridade(uid) is None


def test_7_so_a_estimativa_do_dia_a_dia_nao_avisa(llm, monkeypatch):
    _relogio(monkeypatch)
    uid = _usuario("pro_max")
    _carteira(uid, 9100)
    for atras in (10, 40, 70):  # uma despesa de 3.000 por janela de 30 dias; a Carteira fica em 100
        db.add_launch_and_update_balance(uid, "despesa", 3000, "Mercado", None, "mercado",
                                         datetime.combine(HOJE - timedelta(days=atras), time(12), _tz()))
    snap = carregar(uid, HOJE, FIM)
    assert snap.estimativa_variavel is not None  # a estimativa existe e, somada, levaria a −2.900
    assert _mes(uid) == []
    assert _paridade(uid) == D("100.00")


# ── 9 e 10: o fim do mês, nunca o pior dia; a receita da janela ──────────────────

def test_9_mergulho_no_meio_do_mes_nao_avisa_e_o_simulador_sim(llm, monkeypatch):
    _relogio(monkeypatch)
    uid = _usuario("pro_max")
    _carteira(uid, 300)
    _aluguel(uid, 1000, 22)
    _salario(uid, 2000, 25)
    assert _mes(uid) == []
    snap = carregar(uid, HOJE, FIM)
    v = ver.veredito(snap, [], 0, (FIM - HOJE).days)
    assert (v["estado"], v["data"]) == ("risco_identificado", "2026-10-22")
    assert _paridade(uid) == D("1300.00")


def test_10_salario_na_janela_conta_a_favor_e_fora_dela_ja_aconteceu(llm, monkeypatch):
    """Receita fixa não conferida (decisão do dono): de D+1 a D+3 ela entra como se fosse vir e o alerta fica calado;
    no dia seguinte ao fim da janela vale "o previsto antes já aconteceu" (fica fora da conta), como no simulador."""
    uid = _usuario("pro_max")
    _carteira(uid, 300)
    _aluguel(uid)
    _salario(uid, 2000, 17)  # pago (ou não) em 17/10, ninguém conferiu
    _relogio(monkeypatch)  # 20/10: o 17 é o último dia da janela [17, 19]
    assert _mes(uid) == []
    assert _paridade(uid) == D("1300.00")
    snap = carregar(uid, HOJE, FIM)
    assert [e.nome for e in snap.conferencia] == ["Salário"]
    v = ver.veredito(snap, [], 0, (FIM - HOJE).days)
    assert (v["estado"], [b["codigo"] for b in v["bloqueios"]]) == ("dados_insuficientes", [ver.DUVIDA_RECENTE])
    _relogio(monkeypatch, AGORA.replace(day=21))  # 21/10: a janela é [18, 20]; o 17 "já aconteceu"
    (it,) = _mes(uid)
    assert (it["severity"], it["explicacao"]["atual"]["valor"]) == ("warning", -700.0)
    assert it["explicacao"]["referencia"]["premissa"] == "Considerei que o previsto antes de 18/10 já aconteceu."
    hoje = HOJE + timedelta(days=1)
    assert _paridade(uid, hoje) == D("-700.00")


def test_10b_sem_o_salario_a_mesma_cena_avisa(llm, monkeypatch):
    """Controle positivo do caso 10: é o salário da janela que cala o alerta."""
    _relogio(monkeypatch)
    uid = _usuario("pro_max")
    _carteira(uid, 300)
    _aluguel(uid)
    assert [i["explicacao"]["atual"]["valor"] for i in _mes(uid)] == [-700.0]


# ── 11: o mês é o de Brasília ───────────────────────────────────────────────────

def test_11_ultimo_dia_do_mes_a_noite_ainda_e_outubro(llm, monkeypatch):
    _relogio(monkeypatch, datetime(2026, 10, 31, 22, 30, tzinfo=_tz()))  # já 01/11 em UTC
    uid = _usuario("pro_max")
    _carteira(uid, -80)
    (it,) = _mes(uid)
    assert it["key"] == "month_projection:saldo:2026-10" and "Previsão para 31/10:" in it["message"]


# ── 12: a ressalva nunca é cortada ──────────────────────────────────────────────

def test_12_com_o_maior_valor_do_banco_o_corpo_cabe_e_termina_na_ressalva():
    per = periodo_mes(HOJE)
    maior = -(D(VALOR_MAX) - D("0.01"))  # numeric do banco: o maior saldo e o maior déficit gravaveis
    for saldo in (D("10"), maior):
        sev, title, message = detectores._textos_mes(per, maior, saldo)
        it = montar_item("month_projection", "saldo", per, title=title, message=message, severity=sev, icon="📉",
                         action_label=None, action_view=None, referencia={}, atual={})
        assert it["message"] == message and len(message) <= 240 and message.endswith("pode mostrar outro valor.")
    assert len(detectores._textos_mes(per, D("-230"), D("10"))[2]) == 166  # medido pelo dono
    assert len(detectores._textos_mes(per, D("-230"), D("-80"))[2]) == 206


# ── 13 a 16: plano, leitura e a D4 ──────────────────────────────────────────────

def test_13_impacto_so_no_pro_e_nada_de_pior_dia(llm, monkeypatch):
    _relogio(monkeypatch)
    pro, plus = _usuario("pro_max"), _usuario("pro")
    for uid in (pro, plus):
        _carteira(uid, 300)
        _aluguel(uid)
    (p,), (s,) = _mes(pro), _mes(plus)
    assert p["impacto"] == {"falta_valor": 700.0} and "impacto" not in s and "acoes" not in p
    for it in (p, s):
        texto = str(it)
        assert not any(k in texto for k in ("pior_dia", "worst_day", "trajetoria", "trajectory", "minimo_percurso"))


def test_14_essencial_e_gratis_legado_nao_veem_e_pro_legado_ve(llm, monkeypatch):
    _relogio(monkeypatch)
    essencial = _usuario("essencial")
    assert _http(essencial).get(f"/insights/{essencial}/current").status_code == 403
    monkeypatch.setenv("PLANS_V2_ENABLED", "0")
    gratis, pro = promote_to_pro(usuario_pagante("pro"), "free"), usuario_pagante("pro")
    for uid in (gratis, pro):
        _carteira(uid, 300)
        _aluguel(uid)
    assert [i["type"] for i in ai_patterns.generate_ai_insights(gratis, force=True)] == []
    assert [i["type"] for i in ai_patterns.generate_ai_insights(pro, force=True)] == ["month_projection"]


def test_15_quem_perde_o_forecast_com_o_cache_quente_nao_ve(llm, monkeypatch):
    _relogio(monkeypatch)
    monkeypatch.setenv("PLANS_V2_ENABLED", "0")
    uid = completo(usuario_pagante("pro"))
    _carteira(uid, 300)
    _aluguel(uid)
    ai_patterns.generate_ai_insights(uid, force=True)
    assert [i["type"] for i in ai_patterns.ler_insights(uid)] == ["month_projection"]  # positivo
    monkeypatch.setattr("db.compute_active_insights", lambda *a: pytest.fail("recalculou"))
    promote_to_pro(uid, "free")
    assert ai_patterns.ler_insights(uid) == []
    assert _http(uid).get(f"/insights/{uid}/current").json()["insights"] == []


def test_16_dispensa_volta_so_quando_vira_critical_e_mes_novo_e_fato_novo(llm, monkeypatch):
    _relogio(monkeypatch)
    uid = _usuario("pro")  # Plus
    _carteira(uid, 300)
    rid = _aluguel(uid)
    (it,) = _mes(uid)
    assert _put(uid, chave=it["key"], dispensado=True, severidade="warning").status_code == 200
    update_recurring_expense(uid, rid, amount=1500)
    assert _mes(uid) == []  # positivo: déficit maior com a mesma gravidade não ressuscita
    db.add_launch_and_update_balance(uid, "despesa", 310, "Acerto", None, "outros", ANTIGO)  # Carteira −10
    assert [(i["key"], i["severity"]) for i in _mes(uid)] == [("month_projection:saldo:2026-10", "critical")]
    _relogio(monkeypatch, datetime(2026, 11, 1, 10, tzinfo=_tz()))
    assert [i["key"] for i in _mes(uid)] == ["month_projection:saldo:2026-11"]


# ── 17 a 19: isolamento, ordem e conversa ───────────────────────────────────────

def test_17_o_aviso_de_um_usuario_nao_aparece_para_o_outro(llm, monkeypatch):
    _relogio(monkeypatch)
    a, b = _usuario("pro_max"), _usuario("pro_max")
    _carteira(a, -80)
    _carteira(b, 500)
    assert [i["severity"] for i in _mes(a)] == ["critical"] and _mes(b) == []
    assert "R$ 80,00" not in str(_http(b).get(f"/insights/{b}/current").json())


def test_18_ordem_conta_previsao_orcamento_no_mesmo_critical(llm):
    uid = _usuario("pro_max")
    _carteira(uid, 0)
    db.upsert_budget(uid, "mercado", 100)
    db.add_launch_and_update_balance(uid, "despesa", 120, "Mercado", None, categoria="mercado")  # Carteira −120
    create_boleto(uid, "Luz", 180, day_tz(now_tz()))
    feed = _http(uid).get(f"/insights/{uid}/current").json()["insights"]
    assert [(i["type"], i["severity"]) for i in feed] == [
        ("bill_due", "critical"), ("month_projection", "critical"), ("budget_warning", "critical")]


def test_19_conversa_card_traz_o_aviso_e_o_gasto_novo_piora_o_numero(llm, monkeypatch):
    """Ponta a ponta pelo runner, no relógio real. O LLM é falso: não prova a interpretação real das mensagens."""
    uid = _usuario("pro_max")
    per = periodo_mes(day_tz(now_tz()))
    _carteira(uid, 300)
    create_recurring_expense(uid, "Aluguel", 1000, "moradia", per["fim"].day, "account", start_date=AGOSTO)
    ai_patterns.generate_ai_insights(uid)
    mes = per["rotulo"].split(" de ")[0]
    openai_falso(monkeypatch, rodada(None, ("show_card", {"card": "piggy"})), rodada("Olha o que eu notei."))
    _, ev, _ = executar(uid, "o que você notou?")
    (bloco,) = [e["bloco"] for e in ev if e["tipo"] == "bloco"]
    assert [i["title"] for i in bloco["abas"][0]["recursos"]["insights"]["insights"]] == [
        f"{mes[:1].upper()}{mes[1:]} pode fechar no vermelho"]
    openai_falso(monkeypatch, rodada(None, ("add_launch", {"tipo": "despesa", "valor": 50, "alvo": "mercado",
                                                           "categoria": "mercado"})))
    r2, _, _ = executar(uid, "gastei 50 no mercado")
    assert "Despesa registrada" in r2.reply
    (it,) = [i for i in ai_patterns.generate_ai_insights(uid, force=True) if i["type"] == "month_projection"]
    assert "R$ 750,00 no negativo" in it["message"]
    assert llm == []


# ── Emenda 1: só avisa quem já informou o saldo (seção 12 do plano) ─────────────

def _diga(uid, texto):
    out = hi.handle_incoming(IncomingMessage(platform="whatsapp", user_id=uid, text=texto,
                                             message_id=uuid.uuid4().hex, attachments=[], external_id="", raw={}))
    return "\n".join(m.text for m in out)


def _post(uid, rota, corpo):
    """A rota real do painel, com sessão e CSRF."""
    c, h = cliente(uid)
    r = c.post(f"/account/{uid}/{rota}", json=corpo, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def test_a_usuario_novo_que_gasta_30_nao_recebe_saldo_no_vermelho(llm, monkeypatch):
    """O alarme falso do Tester: a Carteira nasce em 0 e o primeiro gasto não é "saldo no vermelho"."""
    _relogio(monkeypatch)
    uid = _usuario("pro")
    assert "30" in _diga(uid, "gastei 30 na padaria")
    assert db.get_balance(uid) == -30 and _mes(uid) == []


def test_b_usuario_novo_so_com_o_aluguel_nao_recebe_aviso(llm, monkeypatch):
    _relogio(monkeypatch)
    uid = _usuario("pro")
    _aluguel(uid)
    assert _mes(uid) == []


def test_c_saldo_inicial_pela_rota_do_onboarding_liga_o_aviso(llm, monkeypatch):
    _relogio(monkeypatch)
    uid = _usuario("pro")
    _post(uid, "initial-balance", {"amount": 300})
    _aluguel(uid)
    assert [(i["severity"], i["explicacao"]["atual"]["valor"]) for i in _mes(uid)] == [("warning", -700.0)]


def test_d_ajuste_de_saldo_pela_rota_do_painel_liga_o_aviso(llm, monkeypatch):
    _relogio(monkeypatch)
    uid = _usuario("pro")
    db.add_launch_and_update_balance(uid, "despesa", 30, "Padaria", None, "padaria", ANTIGO)
    assert _post(uid, "adjust-balance", {"target_balance": 300})["delta"] == 330
    _aluguel(uid)
    assert [(i["severity"], i["explicacao"]["atual"]["valor"]) for i in _mes(uid)] == [("warning", -700.0)]


def test_d2_categoria_ajuste_saldo_interna_conta_como_informado(llm, monkeypatch):
    """Trava a lista (`ajuste_saldo` em `CATEGORIAS_SALDO_INFORMADO`) gravando direto no banco, como os escritores gravam
    (interno). Não exercita o caminho do chat: nenhuma frase do `handle_incoming` dos testes produz essa categoria."""
    _relogio(monkeypatch)
    uid = _usuario("pro")
    db.add_launch_and_update_balance(uid, "receita", 300, "Ajuste", None, "ajuste_saldo", ANTIGO,
                                     is_internal_movement=True)
    _aluguel(uid)
    assert [i["severity"] for i in _mes(uid)] == ["warning"]


def test_d3_ajuste_para_baixo_pela_rota_tambem_conta(llm, monkeypatch):
    """O `adjust-balance` para baixo grava ("despesa", "ajuste", interno): a marca não depende do tipo."""
    from db.carteira_qualidade import saldo_informado
    _relogio(monkeypatch)
    uid = _usuario("pro")
    db.add_launch_and_update_balance(uid, "receita", 500, "Salário", None, "salario", ANTIGO)
    assert _post(uid, "adjust-balance", {"target_balance": 300})["delta"] == -200
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select tipo, categoria, is_internal_movement from launches where user_id = %s and categoria = 'ajuste'",
                    (uid,))
        assert [tuple(r.values()) for r in cur.fetchall()] == [("despesa", "ajuste", True)]
        assert saldo_informado(cur, uid) is True
    _aluguel(uid)
    assert [(i["severity"], i["explicacao"]["atual"]["valor"]) for i in _mes(uid)] == [("warning", -700.0)]


def test_d4_categoria_ajuste_do_proprio_usuario_nao_conta(llm, monkeypatch):
    """`#ajuste` numa despesa comum vira a categoria "ajuste" NÃO interna (o mesmo nome da do `adjust-balance`): não é
    saldo informado. Positivo: o ajuste pela rota (caso D) segue ligando."""
    _relogio(monkeypatch)
    uid = _usuario("pro")
    assert "50" in _diga(uid, "gastei 50 #ajuste saldo")
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select categoria, is_internal_movement from launches where user_id = %s", (uid,))
        assert [tuple(r.values()) for r in cur.fetchall()] == [("ajuste", False)]
    _aluguel(uid)
    assert _mes(uid) == []


def test_e1_saldo_inicial_zero_nao_deixa_marca_e_nao_avisa(llm, monkeypatch):
    """Limite declarado (P9): o onboarding com 0 não grava nada, então o "informei zero" não é detectável."""
    _relogio(monkeypatch)
    uid = _usuario("pro")
    assert _post(uid, "initial-balance", {"amount": 0})["launch_id"] is None
    db.add_launch_and_update_balance(uid, "despesa", 30, "Padaria", None, "padaria", ANTIGO)
    assert _mes(uid) == []


def test_e2_import_de_ofx_ja_fica_calado_pela_regra_do_simulador(llm, monkeypatch):
    """O `set_balance` do import não deixa rastro, e não precisa: o lançamento importado não é verificável na
    Carteira, então o snapshot leva `carteira_nao_confirmada` e o fechamento sai None antes da regra nova."""
    from ofx_import import import_ofx_bytes
    from test_category_normalization import _OFX_BANCO
    _relogio(monkeypatch)
    uid = _usuario("pro")
    import_ofx_bytes(uid, _OFX_BANCO.format(acct=uid % 100000).encode(), "extrato.ofx")
    _aluguel(uid)
    snap = carregar(uid, HOJE, FIM)
    assert db.get_balance(uid) == 100
    assert "carteira_nao_confirmada" in [m.codigo for m in snap.motivos]
    assert cashflow.fechamento_pior_caso(snap, FIM) is None and _mes(uid) == []


def test_f_a_marca_e_de_cada_usuario(llm, monkeypatch):
    _relogio(monkeypatch)
    a, b = _usuario("pro"), _usuario("pro")
    db.add_launch_and_update_balance(a, "despesa", 80, "Padaria", None, "padaria", ANTIGO)  # −80, nunca informou
    _carteira(b, -80)  # −80, informou 1.000 e gastou
    assert _mes(a) == [] and [i["severity"] for i in _mes(b)] == ["critical"]


def test_g_apagar_o_saldo_inicial_desliga_o_aviso(llm, monkeypatch):
    _relogio(monkeypatch)
    uid = _usuario("pro")
    lid = _post(uid, "initial-balance", {"amount": 300})["launch_id"]
    _aluguel(uid)
    assert len(_mes(uid)) == 1
    delete_launch_and_rollback(uid, lid)
    assert db.get_balance(uid) == 0  # o saldo voltou: o lançamento saiu de fato
    assert _mes(uid) == []


@pytest.mark.parametrize("informou", [True, False])
def test_h_conversa_quanto_eu_tenho_e_gastei_130(llm, informou):
    """Pelo `handle_incoming`, no relógio real (o NLP é o dos testes: não prova a interpretação real)."""
    uid = _usuario("pro")
    if informou:
        _post(uid, "initial-balance", {"amount": 100})
    assert _diga(uid, "quanto eu tenho?")
    assert "130" in _diga(uid, "gastei 130 no mercado")
    assert db.get_balance(uid) == (-30 if informou else -130)  # o 2º turno registrou nos dois
    titulos = [i["message"] for i in ai_patterns.generate_ai_insights(uid, force=True) if i["type"] == "month_projection"]
    assert [t.split(" Previsão")[0] for t in titulos] == (["Seu saldo já está R$ 30,00 no negativo."] if informou else [])
