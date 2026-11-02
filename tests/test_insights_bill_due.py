"""PL-06 B2: o `bill_due` (família `vencimento`), conta a pagar pendente no feed de insights.

Decisões do dono (2026-10-28): info de D-7 a D-4, warning de D-3 a D-1, critical em D-0 e vencida de D+1 a D+7
(uma por recorrente), "(cerca de R$ 80,00)" no variável, primeiro no laço (P3), cache até a meia-noite (P4) e gate
`recurring_expenses` (T1). Postgres real; relógio do detector em 28/10/2026 10:00 (`insights_detectores.now_tz`),
menos na conversa (caso 13) e no teto (caso 12), que rodam no relógio real.

Controles negativos medidos à mão no PR (mutação numa cópia da árvore, um caso vermelho cada): `dias <= ALERTA` →
`dias < ALERTA` e `JANELA` 6 → caso 1; sem o corte inferior → caso 2; sem o `continue` da vencida repetida do
recorrente → caso 2; escada achatada e vencida sem `:vencida` → caso 3; número de dias dentro da chave da vencida
(`f"{id}:vencida:{-dias}"`) → casos 2 e 3 (a dispensa da vencida respeitada, P1); join sem `r.user_id` em
`ler_instancias` → caso 4; dia pelo UTC → caso 10; sem o gate T1 na geração E na leitura → caso 11; sem o filtro de
recurso na leitura, ou filtro depois do corte → caso 11b; sem ele no `motivo_vazio` → caso 11c; detector no fim do
laço → caso 12; ordem `(due_date, id)` dentro do tipo → caso 2b; sem o `reserva="Conta"` (ou reserva ignorada) → caso 8;
TTL fixo de 6 h no `ler_insights` → caso 14, na leitura da rota → caso 14b, no `motivo_vazio` → caso 14c.
Positivos: casos 3 (o dispensado não volta à toa), 5 (aparece antes de pagar), 6 (o Pro vê `acoes`), 11 (Pro legado vê),
11b (com o recurso, a conta aparece) e 11c (quem dispensou segue `dispensados`).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

import db
from _cards_apoio import _http
from _ia_stream_helpers import executar, openai_falso, rodada
from conftest import promote_to_pro, usuario_pagante
from core import ai_patterns
from core.handlers import forma_pagamento as fp
from core.services import insights_detectores as detectores
from db.bills import create_boleto, delete_boleto, ensure_bill_instance, list_bills
from db.recurring import create_recurring_expense
from test_insights_contrato import _usuario, llm  # noqa: F401
from test_api_app_sessao import completo
from test_insights_estado import _put
from tests._patrimonio_helpers import q
from utils_date import _tz, day_tz, now_tz

AGORA = datetime(2026, 10, 28, 10, tzinfo=_tz())
HOJE = AGORA.date()


def _relogio(monkeypatch, agora=AGORA):
    monkeypatch.setattr(detectores, "now_tz", lambda: agora)


def _contas(uid):
    """Os `bill_due` que o usuário recebe: recalcula (o relógio falso não mexe no TTL) e lê pela rota."""
    ai_patterns.generate_ai_insights(uid, force=True)
    r = _http(uid).get(f"/insights/{uid}/current")
    assert r.status_code == 200, r.text
    return [i for i in r.json()["insights"] if i["type"] == "bill_due"]


def _boleto(uid, dias, nome="Luz", valor=180, hoje=HOJE):
    return create_boleto(uid, nome, valor, hoje + timedelta(days=dias))["id"]


def _recorrente(uid, nome, valor, frequency="monthly"):
    return create_recurring_expense(uid, nome, valor, "casa", 10, "account", frequency=frequency,
                                    payment_mode="manual", variable_amount=True)["id"]


# ── 1. a escada antes do vencimento ─────────────────────────────────────────────

def test_1_escada_de_d7_a_d0_e_d8_fora(llm, monkeypatch):
    _relogio(monkeypatch)
    uid = _usuario("pro_max")
    ids = {d: _boleto(uid, d) for d in (8, 7, 4, 3, 1, 0)}
    vistos = {i["key"]: (i["severity"], i["title"]) for i in _contas(uid)}
    def k(d):
        return f"bill_due:{ids[d]}:{HOJE + timedelta(days=d)}"
    assert vistos == {
        k(7): ("info", "Luz vence em 7 dias (R$ 180,00)"), k(4): ("info", "Luz vence em 4 dias (R$ 180,00)"),
        k(3): ("warning", "Luz vence em 3 dias (R$ 180,00)"), k(1): ("warning", "Luz vence amanhã (R$ 180,00)"),
        k(0): ("critical", "Luz vence hoje (R$ 180,00)")}


def test_1b_mensagem_icone_e_explicacao(llm, monkeypatch):
    _relogio(monkeypatch)
    uid = _usuario("pro_max")
    _boleto(uid, 3)
    _boleto(uid, 0, "Internet", 99.9)
    hoje, tres = _contas(uid)
    assert hoje["message"] == "Vencimento hoje, 28/10. Se já pagou, marque como paga em Contas a pagar."
    assert tres["message"] == "Vencimento em 31/10. Se já pagou, marque como paga em Contas a pagar."
    assert (tres["icon"], tres["action_label"], tres["action_view"]) == ("🧾", "Ver contas a pagar", "fixed")
    e = tres["explicacao"]
    assert (e["tipo"], e["familia"], e["fonte"]["base"]) == ("bill_due", "vencimento", "contas_a_pagar")
    assert e["periodo"] == {"inicio": "2026-10-31", "fim": "2026-10-31", "parcial": False, "rotulo": "31/10/2026"}
    assert e["referencia"] == {"estatistica": "vencimento", "conta": "Luz", "vencimento": "2026-10-31",
                               "valor": 180.0, "variavel": False}
    assert (e["atual"], e["diferenca"], e["amostra"]) == ({"dias_para_vencer": 3}, None, None)


# ── 2. vencida: D+1 a D+7, uma por recorrente ───────────────────────────────────

def test_2_vencida_de_d1_a_d7_critical_e_uma_por_recorrente(llm, monkeypatch):
    _relogio(monkeypatch)
    uid = _usuario("pro_max")
    ids = {d: _boleto(uid, -d) for d in (1, 7, 8)}
    rid = _recorrente(uid, "Pão", 10, frequency="daily")
    for d in (2, 3):  # conta diária: duas vencidas na janela, só a mais recente aparece
        ensure_bill_instance(rid, uid, HOJE - timedelta(days=d), 10)
    vistos = [(i["key"], i["severity"], i["icon"], i["title"]) for i in _contas(uid)]
    (pao,) = [k for k, *_ in vistos if k.endswith(f":vencida:{HOJE - timedelta(days=2)}")]
    assert vistos == [  # da vencida mais recente à mais antiga
        (f"bill_due:{ids[1]}:vencida:{HOJE - timedelta(days=1)}", "critical", "⚠️", "Luz venceu ontem (R$ 180,00)"),
        (pao, "critical", "⚠️", "Pão venceu há 2 dias (cerca de R$ 10,00)"),
        (f"bill_due:{ids[7]}:vencida:{HOJE - timedelta(days=7)}", "critical", "⚠️", "Luz venceu há 7 dias (R$ 180,00)")]
    (it,) = [i for i in _contas(uid) if i["key"].startswith(f"bill_due:{ids[1]}:")]
    assert it["message"] == "Venceu em 27/10 e continua em aberto. Se já pagou, marque como paga em Contas a pagar."


def test_2b_a_de_hoje_vem_antes_das_vencidas_e_cabe_no_teto(llm, monkeypatch):
    """6 vencidas (D+1..D+6) e a de hoje, todas critical: o prazo de horas fica no feed, e em primeiro (P3)."""
    _relogio(monkeypatch)
    uid = _usuario("pro_max")
    for d in range(6, 0, -1):
        _boleto(uid, -d, f"Velha{d}")
    _boleto(uid, 0)
    assert [i["title"] for i in _contas(uid)] == ["Luz vence hoje (R$ 180,00)"] + [
        "Velha1 venceu ontem (R$ 180,00)"] + [f"Velha{d} venceu há {d} dias (R$ 180,00)" for d in range(2, 6)]


# ── 3. a D4 com o relógio andando ───────────────────────────────────────────────

def test_3_dispensa_volta_so_quando_a_faixa_piora(llm, monkeypatch):
    uid = _usuario("pro")  # Plus
    bid = _boleto(uid, 3)  # vence 31/10
    chave, vencida = f"bill_due:{bid}:2026-10-31", f"bill_due:{bid}:vencida:2026-10-31"

    def dia(d, m=10):
        _relogio(monkeypatch, AGORA.replace(month=m, day=d))
        return [(i["key"], i["severity"]) for i in _contas(uid)]

    def dispensa(k, sev):
        assert _put(uid, chave=k, dispensado=True, severidade=sev).status_code == 200

    assert dia(26) == [(chave, "info")]
    dispensa(chave, "info")
    assert dia(27) == []
    assert dia(28) == [(chave, "warning")]  # info → warning
    dispensa(chave, "warning")
    assert dia(29) == [] and dia(30) == []  # positivo: igual não ressuscita
    assert dia(31) == [(chave, "critical")]  # warning → critical
    dispensa(chave, "critical")
    assert dia(1, 11) == [(vencida, "critical")]  # fato novo: aparece com o D-0 dispensado
    dispensa(vencida, "critical")
    assert dia(2, 11) == [] and dia(7, 11) == []  # "há N dias" mudando não gera alerta novo


# ── 4. isolamento: a fonte reutilizada casa o recorrente do mesmo usuário ────────

def test_4_isolamento_entre_usuarios_e_recorrente_de_outro(llm, monkeypatch):
    _relogio(monkeypatch)
    a, b = _usuario("pro_max"), _usuario("pro_max")
    rid_a = _recorrente(a, "Segredo de A", 70)
    _boleto(b, 0, "Luz de B")
    q("insert into bill_instances (user_id, recurring_id, due_date, amount, status) values (%s, %s, %s, 50, 'pending')",
      (b, rid_a, HOJE))
    assert _contas(a) == []
    vistos = _contas(b)
    assert [i["title"] for i in vistos] == ["Luz de B vence hoje (R$ 180,00)", "Conta vence hoje (R$ 50,00)"]
    assert "Segredo" not in json.dumps(vistos, ensure_ascii=False)


# ── 5. paga ou apagada sai ──────────────────────────────────────────────────────

def test_5_conta_paga_ou_apagada_sai(llm, monkeypatch):
    _relogio(monkeypatch)
    uid = _usuario("pro_max")
    paga, apagada = _boleto(uid, 0), _boleto(uid, 0, "Internet", 99.9)
    assert len(_contas(uid)) == 2  # positivo: as duas aparecem antes
    assert fp.quitar(uid, paga, None, fp.DESCONHECIDA)[0] == "paga"
    assert delete_boleto(uid, apagada)
    assert _contas(uid) == []


# ── 6. valores e `acoes` por plano ──────────────────────────────────────────────

def test_6_valor_fixo_variavel_e_sem_valor_e_acoes_so_no_pro(llm, monkeypatch):
    _relogio(monkeypatch)
    pro, plus = _usuario("pro_max"), _usuario("pro")
    for uid in (pro, plus):
        _boleto(uid, 0)
        for nome, valor in (("Água", 80), ("Gás", 0)):
            ensure_bill_instance(_recorrente(uid, nome, valor), uid, HOJE, valor)
    itens = _contas(pro)
    assert [i["title"] for i in itens] == ["Luz vence hoje (R$ 180,00)", "Água vence hoje (cerca de R$ 80,00)",
                                           "Gás vence hoje (valor a confirmar)"]
    assert [i["acoes"][0]["valores"] for i in itens] == [{"conta_valor": 180.0}, {"conta_valor": 80.0}, {}]
    assert [i["explicacao"]["referencia"]["valor"] for i in itens] == [180.0, 80.0, None]
    pluses = _contas(plus)
    assert [i["title"] for i in pluses] == [i["title"] for i in itens] and not any("acoes" in i for i in pluses)


# ── 7 e 8. várias no mesmo dia; nomes ───────────────────────────────────────────

def test_7_mesmo_dia_um_item_por_conta_na_ordem_dos_ids(llm, monkeypatch):
    _relogio(monkeypatch)
    uid = _usuario("pro_max")
    ids = [_boleto(uid, 2, n) for n in ("Luz", "Internet", "Aluguel")]
    assert [i["key"] for i in _contas(uid)] == [f"bill_due:{i}:2026-10-30" for i in ids]


def test_8_nome_em_uma_linha_com_teto_e_cpf_fora_da_chave(llm, monkeypatch):
    _relogio(monkeypatch)
    uid = _usuario("pro_max")
    ids = [_boleto(uid, 5, n) for n in ("luz *Enel*\nCasa", "Aluguel 123.456.789-00", "x" * 60, "", "***", "outros")]
    marcado, cpf, longo, vazio, so_marcacao, outros = _contas(uid)
    assert marcado["title"] == "Luz Enel Casa vence em 5 dias (R$ 180,00)"
    assert "123.456.789-00" in cpf["title"] and cpf["key"] == f"bill_due:{ids[1]}:2026-11-02"  # o id, nunca o nome
    assert longo["title"] == "X" + "x" * 38 + "… vence em 5 dias (R$ 180,00)"
    assert vazio["title"] == "Boleto vence em 5 dias (R$ 180,00)"
    assert so_marcacao["title"] == "Conta vence em 5 dias (R$ 180,00)"  # não o "outros" do `nome()` vazio
    assert outros["title"] == "Outros vence em 5 dias (R$ 180,00)"  # o nome que o usuário deu fica


# ── 9 e 10. virada de mês/ano e fuso ────────────────────────────────────────────

def test_9_virada_de_mes_e_de_ano(llm, monkeypatch):
    uid = _usuario("pro_max")
    _boleto(uid, 0, hoje=datetime(2026, 11, 2).date())
    _relogio(monkeypatch)
    assert [(i["severity"], i["title"]) for i in _contas(uid)] == [("info", "Luz vence em 5 dias (R$ 180,00)")]
    _relogio(monkeypatch, AGORA.replace(month=11, day=4))
    assert [i["title"] for i in _contas(uid)] == ["Luz venceu há 2 dias (R$ 180,00)"]
    outro = _usuario("pro_max")
    _boleto(outro, 0, hoje=datetime(2027, 1, 1).date())
    _relogio(monkeypatch, AGORA.replace(month=12, day=29))
    assert [(i["severity"], i["title"]) for i in _contas(outro)] == [("warning", "Luz vence em 3 dias (R$ 180,00)")]


def test_10_o_dia_e_o_de_brasilia(llm, monkeypatch):
    uid = _usuario("pro_max")
    _boleto(uid, 3)  # 31/10
    _relogio(monkeypatch, datetime(2026, 10, 30, 23, 30, tzinfo=_tz()))  # já 31/10 em UTC
    assert [(i["severity"], i["title"]) for i in _contas(uid)] == [("warning", "Luz vence amanhã (R$ 180,00)")]
    _relogio(monkeypatch, datetime(2026, 10, 31, 0, 10, tzinfo=_tz()))
    assert [i["title"] for i in _contas(uid)] == ["Luz vence hoje (R$ 180,00)"]


# ── 11. modo legado: Grátis não vê aviso da agenda que não abre (T1) ─────────────

def test_11_legado_gratis_nao_ve_e_pro_legado_ve(llm, monkeypatch):
    monkeypatch.setenv("PLANS_V2_ENABLED", "0")
    _relogio(monkeypatch)
    gratis, pro = promote_to_pro(usuario_pagante("pro"), "free"), usuario_pagante("pro")
    for uid in (gratis, pro):
        _boleto(uid, 0)
    assert [i["type"] for i in ai_patterns.generate_ai_insights(gratis, force=True)] == []
    assert [i["type"] for i in ai_patterns.generate_ai_insights(pro, force=True)] == ["bill_due"]


def test_11b_quem_perde_o_recurso_com_o_cache_quente_nao_ve_e_o_teto_e_preenchido(llm, monkeypatch):
    """Legado: o Pro grava o cache com a conta de hoje e cai para Grátis dentro do TTL. A leitura (rota e card) tira o
    `bill_due` ANTES do corte em 6, sem recalcular: o 6º orçamento entra no lugar."""
    monkeypatch.setenv("PLANS_V2_ENABLED", "0")
    uid = completo(usuario_pagante("pro"))
    _seis_orcamentos_estourados(uid)
    create_boleto(uid, "Luz", 180, day_tz(now_tz()))
    ai_patterns.generate_ai_insights(uid, force=True)

    def feed():
        return [i["type"] for i in _http(uid).get(f"/insights/{uid}/current").json()["insights"]]
    assert feed() == ["bill_due"] + ["budget_warning"] * 5  # positivo: com o recurso, a conta aparece
    recalculos = []
    monkeypatch.setattr("db.compute_active_insights", lambda *a: recalculos.append(a) or ([], []))
    promote_to_pro(uid, "free")
    assert feed() == ["budget_warning"] * 6
    assert [i["type"] for i in ai_patterns.ler_insights(uid)] == ["budget_warning"] * 6  # o card `piggy`
    assert recalculos == []  # veio do cache


def test_11c_motivo_de_quem_perde_o_recurso_e_sem_destaques_e_nao_dispensados(llm, monkeypatch):
    """Legado, só a conta no cache: quem cai para Grátis recebe a lista vazia com `sem_destaques` (há dados, nada a
    mostrar), não `dispensados` (ele não dispensou nada). Positivo: quem dispensou de fato segue com `dispensados`."""
    monkeypatch.setenv("PLANS_V2_ENABLED", "0")
    caiu, dispensou = completo(usuario_pagante("pro")), completo(usuario_pagante("pro"))
    for uid in (caiu, dispensou):
        db.add_launch_and_update_balance(uid, "despesa", 30, "Padaria", None, categoria="mercado")
        create_boleto(uid, "Luz", 180, day_tz(now_tz()))
        ai_patterns.generate_ai_insights(uid, force=True)

    def feed(uid):
        r = _http(uid).get(f"/insights/{uid}/current").json()
        return [i["type"] for i in r["insights"]], r["motivo"]
    (it,) = ai_patterns.ler_insights(dispensou)
    assert _put(dispensou, chave=it["key"], dispensado=True, severidade=it["severity"]).status_code == 200
    monkeypatch.setattr("db.compute_active_insights", lambda *a: pytest.fail("recalculou"))
    promote_to_pro(caiu, "free")
    assert feed(caiu) == ([], "sem_destaques")
    assert feed(dispensou) == ([], "dispensados")


# ── 12. teto de 6: no empate de gravidade, a conta vem antes (P3) ─────────────────

def _seis_orcamentos_estourados(uid):
    for cat in ("mercado", "lazer", "transporte", "saude", "educacao", "restaurante"):
        db.upsert_budget(uid, cat, 100)
        db.add_launch_and_update_balance(uid, "despesa", 120, f"Loja {cat}", None, categoria=cat)


def test_12_conta_de_hoje_fica_no_feed_com_6_orcamentos_estourados(llm):
    uid = _usuario("pro_max")
    _seis_orcamentos_estourados(uid)
    create_boleto(uid, "Luz", 180, day_tz(now_tz()))
    feed = _http(uid).get(f"/insights/{uid}/current").json()["insights"]
    assert len(feed) == 6 and {i["severity"] for i in feed} == {"critical"}
    assert feed[0]["type"] == "bill_due" and [i["type"] for i in feed[1:]] == ["budget_warning"] * 5


# ── 13. a conversa: o card mostra a conta, "paguei a luz" a tira ──────────────────

def test_13_conversa_card_traz_a_conta_e_paguei_a_luz_a_tira(llm, monkeypatch):
    """Ponta a ponta pelo runner, no relógio real. O LLM é falso: isto NÃO prova a interpretação real de
    "paguei a luz", só que a quitação pelo chat tira o item do card no recálculo."""
    uid = _usuario("pro_max")
    create_boleto(uid, "Luz", 180, day_tz(now_tz()))
    ai_patterns.generate_ai_insights(uid)

    def card():
        openai_falso(monkeypatch, rodada(None, ("show_card", {"card": "piggy"})), rodada("Olha o que eu notei."))
        _, ev, _ = executar(uid, "o que você notou?")
        (bloco,) = [e["bloco"] for e in ev if e["tipo"] == "bloco"]
        return [i["title"] for i in bloco["abas"][0]["recursos"]["insights"]["insights"]]

    assert card() == ["Luz vence hoje (R$ 180,00)"]
    openai_falso(monkeypatch, rodada(None, ("mark_bill_paid", {"name": "luz"})), rodada("Pronto, marquei a luz."))
    r, _, _ = executar(uid, "paguei a luz")
    assert r.reply and list_bills(uid) == []  # pagou de verdade (pendentes vazias)
    ai_patterns.generate_ai_insights(uid, force=True)
    assert card() == []
    assert llm == []


# ── 14. o cache não atravessa a meia-noite (P4) ─────────────────────────────────

def test_14_cache_de_antes_da_meia_noite_e_miss(llm, monkeypatch):
    uid = _usuario("pro_max")
    _boleto(uid, 0, hoje=day_tz(now_tz()))
    ai_patterns.generate_ai_insights(uid, force=True)
    q("update ai_proactive_cache set generated_at = now() - interval '1 hour' where user_id = %s", (uid,))
    meio_dia = datetime(2026, 10, 28, 12, tzinfo=_tz())
    monkeypatch.setattr(ai_patterns, "now_tz", lambda: meio_dia)
    assert ai_patterns.ler_insights(uid) is not None  # positivo: dentro das 6 h e do mesmo dia, hit
    monkeypatch.setattr(ai_patterns, "now_tz", lambda: meio_dia.replace(hour=0, minute=30))
    assert ai_patterns.ler_insights(uid) is None  # gerado 1 h atrás = antes da meia-noite: miss


def _cache_de_antes_da_meia_noite(monkeypatch, uid, payload="{\"items\": []}"):
    """Cache vazio gravado 1 h antes de um relógio às 00:30 de Brasília: vale só se a leitura ignorar a meia-noite."""
    ai_patterns.generate_ai_insights(uid, force=True)
    q("update ai_proactive_cache set generated_at = now() - interval '1 hour', payload = %s::jsonb where user_id = %s",
      (payload, uid))
    monkeypatch.setattr(ai_patterns, "now_tz", lambda: datetime(2026, 10, 28, 0, 30, tzinfo=_tz()))


def test_14b_a_rota_regenera_o_cache_de_antes_da_meia_noite(llm, monkeypatch):
    uid = _usuario("pro_max")
    _boleto(uid, 0, hoje=day_tz(now_tz()))
    _cache_de_antes_da_meia_noite(monkeypatch, uid)
    r = _http(uid).get(f"/insights/{uid}/current").json()
    assert [i["title"] for i in r["insights"]] == ["Luz vence hoje (R$ 180,00)"]  # miss: recalculou
    q("update ai_proactive_cache set generated_at = now() - interval '1 hour', payload = '{\"items\": []}'::jsonb "
      "where user_id = %s", (uid,))
    monkeypatch.setattr(ai_patterns, "now_tz", lambda: datetime(2026, 10, 28, 12, tzinfo=_tz()))
    assert _http(uid).get(f"/insights/{uid}/current").json()["insights"] == []  # positivo: no mesmo dia é hit


def test_14c_motivo_nao_le_o_cache_de_antes_da_meia_noite(llm, monkeypatch):
    """Detector em falha não grava cache; o `motivo` não pode ler o vazio de ontem como "nada a apontar"."""
    import db.insights as detectores_db
    uid = _usuario("pro_max")
    _cache_de_antes_da_meia_noite(monkeypatch, uid)

    def quebra(*_a, **_k):
        raise RuntimeError("banco fora")
    monkeypatch.setattr(detectores_db, "get_budgets_status_for_month", quebra)
    r = _http(uid).get(f"/insights/{uid}/current").json()
    assert (r["insights"], r["motivo"]) == ([], None)
