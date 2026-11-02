"""PL-06 B4: o `positive_result` (família `evolucao_positiva`), a sobra do mês fechado maior que a do anterior.

Decisões do dono (2026-11-02): sobra = o "Entrou − Saiu" do Resumo do mês (`comparar_mes`), positiva E maior que a do
mês anterior (Q4); `info`, sem vaga reservada, último no laço (Q4b); dados suficientes = o plano enxerga os dois meses,
o anterior tem lançamento, o histórico do usuário começa até o dia 1 do mês anterior e o Resumo não avisa número
possivelmente incompleto (P1 c); o mês seguinte inteiro no feed (P2); nada a mais no Pro (P3); o Repórter chama de
sobra outra conta (P4, issue #1360); a frase da definição e nunca "guardou" (P5). Postgres real; relógio do detector em
05/10/2026 10:00 (`insights_detectores.now_tz`), menos no teto (caso 15) e na conversa (18).

Rodada 2 (P6–P9 do dono): banco parado no período cala (P6, com a data da P10); só a partir do dia
`SOBRA_DESDE_DIA` do mês seguinte (P7); algum lançamento nos primeiros `SOBRA_COBERTURA_DIAS` dias do mês base (P8).
Reduzem risco, não garantem completude.

Controles negativos medidos à mão no PR (mutação numa cópia da árvore, os casos que ficam vermelhos):
`sobra > ant` → `>=` → 2; sem `sobra > 0` → 4; ignorar `sem_base` → 5; ignorar `inicio_do_historico` → 12 (o detector
quebra em vez de ficar calado); sem a regra do início do histórico → 6[20/08]; `primeiro > ini_ant` → `>=` → 7[01/08];
ignorar os `motivos` do Resumo → 17[banco desatualizado]; `hoje` em UTC → 11; `_inicio_do_historico` sem `user_id` →
16, 24; primeiro no laço → 15b; `warning` em vez de `info` → 1, 14, 15b; "guardou" no texto → 1, 3, 9, 13; sem a frase
da definição → 1, 3, 13; o período com `parcial` do mês aberto → 1; `SOBRA_DESDE_DIA` 5 → 4 → 20[dia 4]; 5 → 6 →
20[dia 5] e todos os que avisam no dia 5; sem o dia mínimo → 20[dia 4], 21; `SOBRA_COBERTURA_DIAS` 7 → 8 → 23[dia 8],
24; sem a janela de cobertura → 22, 23[dia 8], 24; a janela sem `user_id` → 24, 25[cartão]; P10: sem o critério de
data (cala sempre) → 19[31/07 e 01/08 01:00 UTC]; `>=` → `>` no dia 1 → 19[01/08 00:00 e 03:00 UTC]; contra o mês
fechado em vez do anterior → 19[01/08, 15/08], 19c; a data em UTC → 19[31/07 23:59 e 01/08 01:00 UTC]; sem `user_id` →
19b; só PAUSED → 19[DELETED]; só DELETED → 19[PAUSED], 19c; `mark_sync_attempt` sem a guarda de terminal → 19d;
`financial_comparison` em outro nível que `insights` (v2 ou legado) → 26.
Positivos: 1 (sai), 3 (anterior negativo ou zerado), 7 (o histórico desde o dia 1), 12 (Plus vê), 14 (o mesmo mês não
ressuscita), 15[5 avisos] (entra em 6º), 17[banco em dia], 19[`updated_at` antes do período], 19b (banco em dia; outro
usuário), 19c (reconectou), 20[dia 5 00:00], 23[dia 7], 25 (cobertura só por transferência interna ou compra no
cartão).
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal as D

import pytest

import db
from _cards_apoio import _http
from _ia_stream_helpers import executar, openai_falso, rodada
from conftest import promote_to_pro, usuario_pagante
from core import ai_patterns
from core.services import insights_detectores as detectores
from test_insights_contrato import _orcamento_apertando, _usuario, llm  # noqa: F401
from test_insights_estado import _put
from tests._patrimonio_helpers import conexao, conta, horas_atras, q
from utils_date import _tz
from utils_text import VALOR_MAX

AGORA = datetime(2026, 10, 5, 10, tzinfo=_tz())
SOBRA_E = "Sobra é o que entrou menos o que saiu no mês, com o cartão pela fatura."


def _relogio(monkeypatch, agora=AGORA):
    monkeypatch.setattr(detectores, "now_tz", lambda: agora)


def _lanca(uid, tipo, valor, dia, categoria="outros", **kw):
    return db.add_launch_and_update_balance(uid, tipo, valor, "Loja", None, categoria,
                                            datetime.combine(dia, time(12), _tz()), **kw)[0]


def _mes(uid, ano, mes, entrou, saiu):
    if entrou:
        _lanca(uid, "receita", entrou, date(ano, mes, 5), "salario")
    if saiu:
        _lanca(uid, "despesa", saiu, date(ano, mes, 10), "mercado")


def _cena(uid, ago=(3000, 2480), set_=(3000, 2180), desde=date(2026, 7, 10)):
    """Usa o PigBank desde `desde` (um gasto de R$ 1,00 nesse dia), com agosto e setembro como pedido."""
    _lanca(uid, "despesa", 1, desde)
    if ago:
        _mes(uid, 2026, 8, *ago)
    _mes(uid, 2026, 9, *set_)


def _positivos(uid):
    ai_patterns.generate_ai_insights(uid, force=True)
    r = _http(uid).get(f"/insights/{uid}/current")
    assert r.status_code == 200, r.text
    return [i for i in r.json()["insights"] if i["type"] == "positive_result"]


# ── 1 a 7: a regra ──────────────────────────────────────────────────────────────

def test_1_setembro_fechou_com_sobra_maior_que_agosto(llm, monkeypatch):
    _relogio(monkeypatch)
    uid = _usuario("pro")
    _cena(uid)
    (it,) = _positivos(uid)
    assert (it["key"], it["severity"], it["icon"]) == ("positive_result:mes:2026-09", "info", "🎉")
    assert it["title"] == "Setembro fechou com R$ 820,00 de sobra"
    assert it["message"] == f"R$ 300,00 a mais que agosto. {SOBRA_E}"
    assert (it["action_label"], it["action_view"]) == ("Ver análises", "analytics")
    e = it["explicacao"]
    assert (e["familia"], e["fonte"]["base"], e["fonte"]["cartao"]) == ("evolucao_positiva", "lancamentos", "fatura")
    assert e["periodo"] == {"inicio": "2026-09-01", "fim": "2026-09-30", "parcial": False, "rotulo": "setembro de 2026"}
    assert e["referencia"] == {"estatistica": "mes_anterior_inteiro", "valor": 520.0, "periodo": {
        "inicio": "2026-08-01", "fim": "2026-08-31", "parcial": False, "rotulo": "agosto de 2026"}}
    assert (e["atual"], e["diferenca"]) == ({"valor": 820.0}, {"valor": 300.0, "percentual": 57.7})


def test_2_sobra_igual_a_do_anterior_nao_avisa(llm, monkeypatch):
    _relogio(monkeypatch)
    uid = _usuario("pro")
    _cena(uid, ago=(3000, 2180))
    assert _positivos(uid) == []


@pytest.mark.parametrize("ago,texto", [((1000, 1200), "Agosto tinha fechado com R$ 200,00 no negativo."),
                                       ((1000, 1000), "Agosto tinha fechado zerado.")])
def test_3_anterior_negativo_ou_zerado(llm, monkeypatch, ago, texto):
    _relogio(monkeypatch)
    uid = _usuario("pro")
    _cena(uid, ago=ago)
    assert [i["message"] for i in _positivos(uid)] == [f"{texto} {SOBRA_E}"]


def test_4_melhorou_mas_ainda_negativo_nao_avisa(llm, monkeypatch):
    _relogio(monkeypatch)
    uid = _usuario("pro")
    _cena(uid, ago=(1000, 1300), set_=(1000, 1100))  # −300 → −100
    assert _positivos(uid) == []


def test_5_mes_anterior_sem_lancamento_nao_avisa(llm, monkeypatch):
    _relogio(monkeypatch)
    uid = _usuario("pro")
    _cena(uid, ago=None)  # usa desde julho, mas agosto ficou sem entrada nem saída: `sem_base`
    # Só uma transferência interna no dia 3: cobre o começo do mês (P8), mas não entra na sobra.
    _lanca(uid, "despesa", 50, date(2026, 8, 3), "transferencia_interna", is_internal_movement=True)
    assert _positivos(uid) == []


@pytest.mark.parametrize("desde,aparece", [(date(2026, 8, 20), False), (date(2026, 8, 1), True)])
def test_6_e_7_o_mes_anterior_inteiro_no_historico(llm, monkeypatch, desde, aparece):
    """Quem entrou em 20/08 lançou 10 dias de agosto: comparar com setembro inteiro seria torto (P1)."""
    _relogio(monkeypatch)
    uid = _usuario("pro")
    _cena(uid, desde=desde)
    assert len(_positivos(uid)) == (1 if aparece else 0)


# ── 8 e 9: o que entra na sobra (a regra do Resumo do mês) ─────────────────────────

def test_8_cartao_pela_fatura_conta_uma_vez_e_o_pagamento_e_interno(llm, monkeypatch):
    _relogio(monkeypatch)
    uid = _usuario("pro")
    _cena(uid, set_=(3000, 1680))
    cartao = db.create_card(uid, "Nubank", closing_day=25, due_day=5)
    db.add_credit_purchase(uid, cartao, 500, "mercado", "compra", date(2026, 9, 10))  # fatura com fim em 25/09
    _lanca(uid, "despesa", 500, date(2026, 9, 5), "pagamento_fatura", is_internal_movement=True)
    assert [i["title"] for i in _positivos(uid)] == ["Setembro fechou com R$ 820,00 de sobra"]


def test_9_caixinha_nao_mexe_na_sobra_e_o_texto_nao_diz_guardou(llm, monkeypatch):
    _relogio(monkeypatch)
    uid = _usuario("pro")
    _cena(uid)
    db.create_pocket(uid, "Viagem")
    lid = db.pocket_deposit_from_account(uid, "Viagem", 300)[0]
    q("update launches set criado_em = %s where id = %s and user_id = %s",
      (datetime(2026, 9, 20, 12, tzinfo=_tz()), lid, uid))
    (it,) = _positivos(uid)
    assert it["title"] == "Setembro fechou com R$ 820,00 de sobra"
    assert not any(p in (it["title"] + it["message"]).lower() for p in ("guard", "poup", "econom"))


# ── 10 a 13: virada, fuso, plano e textos ─────────────────────────────────────────

def test_10_virada_de_ano(llm, monkeypatch):
    _relogio(monkeypatch, datetime(2027, 1, 5, 10, tzinfo=_tz()))
    uid = _usuario("pro")
    _lanca(uid, "despesa", 1, date(2026, 10, 10))
    _mes(uid, 2026, 11, 1000, 600)
    _mes(uid, 2026, 12, 1500, 600)
    (it,) = _positivos(uid)
    assert (it["key"], it["title"]) == ("positive_result:mes:2026-12", "Dezembro fechou com R$ 900,00 de sobra")
    assert it["message"].startswith("R$ 500,00 a mais que novembro.")


def test_11_ultimo_dia_do_mes_a_noite_ainda_e_setembro(llm, monkeypatch):
    """30/09 22:30 em Brasília já é 01/10 em UTC: setembro ainda está aberto e não sai item dele."""
    _relogio(monkeypatch, datetime(2026, 9, 30, 22, 30, tzinfo=_tz()))
    uid = _usuario("pro")
    _cena(uid, desde=date(2026, 8, 1))  # julho vazio: o "mês fechado" agosto não tem base
    assert _positivos(uid) == []


def test_12_sem_o_plano_nao_ve_e_plus_ve(llm, monkeypatch):
    _relogio(monkeypatch)
    essencial = _usuario("essencial")
    assert _http(essencial).get(f"/insights/{essencial}/current").status_code == 403
    plus = _usuario("pro")
    _cena(plus)
    assert len(_positivos(plus)) == 1  # positivo
    monkeypatch.setenv("PLANS_V2_ENABLED", "0")
    gratis = promote_to_pro(usuario_pagante("pro"), "free")  # legado: só o mês corrente
    _cena(gratis)
    itens, falhas = db.compute_active_insights(gratis)
    assert [i for i in itens if i["type"] == "positive_result"] == [] and falhas == []  # calado, não quebrado


@pytest.mark.parametrize("ant", [D("12345678.90"), D(0), -D("12345678.90")])
def test_13_textos_com_o_maior_valor_do_banco(monkeypatch, ant):
    import core.services.comparacao_periodos as cp
    import core.services.previsao_variavel as pv
    maior = D(VALOR_MAX) - D("0.01")
    monkeypatch.setattr(detectores, "now_tz", lambda: AGORA)
    monkeypatch.setattr(cp, "comparar_mes", lambda *_a, **_k: {
        "motivo": None, "motivos": [], "resultado": {"total_atual": maior, "total_anterior": ant},
        "anterior": {"inicio": date(2026, 8, 1)}})
    monkeypatch.setattr(pv, "_inicio_do_historico", lambda _cur, _uid, desde=date.min: max(desde, date(2026, 7, 1)))
    monkeypatch.setattr("db.contas_hoje.listar", lambda *_a, **_k: {"motivos": []})
    (it,) = detectores.detect_positive_result(1)
    assert len(it["title"]) <= 140 and len(it["message"]) <= 240 and it["message"].endswith(SOBRA_E)
    assert not any(p in (it["title"] + it["message"]).lower() for p in ("guard", "poup", "econom"))


# ── 14 a 17: D4, teto, isolamento e o Resumo incompleto ──────────────────────────────

def test_14_dispensado_nao_volta_no_mesmo_mes_e_outubro_e_fato_novo(llm, monkeypatch):
    _relogio(monkeypatch)
    uid = _usuario("pro")
    _cena(uid)
    (it,) = _positivos(uid)
    assert _put(uid, chave=it["key"], dispensado=True, severidade="info").status_code == 200
    _lanca(uid, "receita", 500, date(2026, 9, 20), "salario")  # a sobra de setembro sobe: o mesmo fato
    assert _positivos(uid) == []  # positivo: o mesmo mês não ressuscita
    _mes(uid, 2026, 10, 4000, 2000)
    _relogio(monkeypatch, datetime(2026, 11, 5, 10, tzinfo=_tz()))
    assert [i["key"] for i in _positivos(uid)] == ["positive_result:mes:2026-10"]


@pytest.mark.parametrize("avisos,aparece", [(6, False), (5, True)])
def test_15_so_ocupa_a_vaga_que_os_alertas_deixam(llm, monkeypatch, avisos, aparece):
    _relogio(monkeypatch)
    uid = _usuario("pro")
    _cena(uid)
    for i in range(avisos):
        _orcamento_apertando(uid, categoria=f"cat{i}")
    ai_patterns.generate_ai_insights(uid, force=True)
    tipos = [i["type"] for i in _http(uid).get(f"/insights/{uid}/current").json()["insights"]]
    assert len(tipos) == min(6, avisos + 1)
    assert ("positive_result" in tipos) is aparece and (not aparece or tipos[-1] == "positive_result")


def test_15b_no_empate_de_info_vem_depois_do_vencimento(llm, monkeypatch):
    """Q4b: último no laço. Uma conta que vence em 5 dias (info) vem antes da sobra (info)."""
    from db.bills import create_boleto
    _relogio(monkeypatch)
    uid = _usuario("pro")
    _cena(uid)
    create_boleto(uid, "Luz", 180, date(2026, 10, 10))
    ai_patterns.generate_ai_insights(uid, force=True)
    tipos = [i["type"] for i in _http(uid).get(f"/insights/{uid}/current").json()["insights"]]
    assert tipos == ["bill_due", "positive_result"]


def test_16_o_historico_e_a_sobra_sao_de_cada_usuario(llm, monkeypatch):
    _relogio(monkeypatch)
    a, b = _usuario("pro"), _usuario("pro")
    _cena(a)  # desde julho
    _lanca(b, "receita", 3000, date(2026, 8, 20), "salario")  # B começou em 20/08, com a mesma sobra
    _lanca(b, "despesa", 2480, date(2026, 8, 25), "mercado")
    _mes(b, 2026, 9, 3000, 2180)
    assert len(_positivos(a)) == 1 and _positivos(b) == []


@pytest.mark.parametrize("horas,aparece", [(72, False), (1, True)])
def test_17_resumo_com_banco_desatualizado_nao_celebra(llm, monkeypatch, horas, aparece):
    """P1 (c): com aviso de número possivelmente incompleto no Resumo (`banco_desatualizado`), nada."""
    _relogio(monkeypatch)
    uid = _usuario("pro")
    _cena(uid)
    conta(conexao(uid, f"item-b4-{horas}", sync=horas_atras(horas)), f"acc-b4-{horas}", D("10"))
    assert len(_positivos(uid)) == (1 if aparece else 0)


# ── 18: a conversa ────────────────────────────────────────────────────────────────

def test_18_conversa_card_traz_a_sobra_e_o_gasto_de_hoje_nao_mexe_nela(llm, monkeypatch):
    """Ponta a ponta pelo runner, no mês real (o fechado é o anterior); o relógio do detector vai ao dia
    `SOBRA_DESDE_DIA` deste mês (P7). O LLM é falso."""
    from utils_date import day_tz, now_tz
    agora = now_tz().replace(day=detectores.SOBRA_DESDE_DIA, hour=10)
    _relogio(monkeypatch, agora)
    uid = _usuario("pro")
    hoje = day_tz(agora)
    fechado = (hoje.replace(day=1) - timedelta(days=1)).replace(day=1)
    anterior = (fechado - timedelta(days=1)).replace(day=1)
    _lanca(uid, "despesa", 1, (anterior - timedelta(days=1)))
    _mes(uid, anterior.year, anterior.month, 3000, 2480)
    _mes(uid, fechado.year, fechado.month, 3000, 2180)
    ai_patterns.generate_ai_insights(uid)
    from core.services.comparacao_periodos import MESES
    titulo = f"{MESES[fechado.month - 1].capitalize()} fechou com R$ 820,00 de sobra"
    openai_falso(monkeypatch, rodada(None, ("show_card", {"card": "piggy"})), rodada("Olha o que eu notei."))
    _, ev, _ = executar(uid, "o que você notou?")
    (bloco,) = [e["bloco"] for e in ev if e["tipo"] == "bloco"]
    assert titulo in [i["title"] for i in bloco["abas"][0]["recursos"]["insights"]["insights"]]
    openai_falso(monkeypatch, rodada(None, ("add_launch", {"tipo": "despesa", "valor": 50, "alvo": "mercado",
                                                           "categoria": "mercado"})))
    r2, _, _ = executar(uid, "gastei 50 no mercado")
    assert "Despesa registrada" in r2.reply
    assert [i["title"] for i in ai_patterns.generate_ai_insights(uid, force=True)
            if i["type"] == "positive_result"] == [titulo]
    assert llm == []


# ── Rodada 2 (P6–P8 do dono): pausada, dia mínimo e cobertura do começo do mês base ───

def _of(uid, dia, valor, tipo="despesa"):
    q("insert into launches (user_id, tipo, valor, alvo, criado_em, posted_at, source) "
      "values (%s, %s, %s, 'BANCO', %s, %s, 'open_finance')", (uid, tipo, valor, datetime.combine(dia, time(12), _tz()), dia))


def _terminal(uid, status, updated_at):
    """Uma conexão terminal (`status`) do usuário com o `updated_at` (data aproximada da parada) dado."""
    cid = conexao(uid, f"it-p10-{status}-{uid}-{updated_at:%Y%m%d%H%M}", status=status, sync=horas_atras(1))
    conta(cid, f"acc-p10-{cid}", D("10"))
    q("update open_finance_connections set updated_at = %s where id = %s and user_id = %s", (updated_at, cid, uid))
    return cid


@pytest.mark.parametrize("status", ["PAUSED", "DELETED"])
@pytest.mark.parametrize("updated_at,aparece", [
    (datetime(2026, 7, 31, 23, 59, tzinfo=_tz()), True),    # antes do período comparado: não cala
    (datetime(2026, 8, 1, 0, 0, tzinfo=_tz()), False),      # exatamente no dia 1 do mês anterior
    (datetime(2026, 8, 15, 10, tzinfo=_tz()), False),       # durante o mês anterior
    (datetime(2026, 9, 15, 10, tzinfo=_tz()), False),       # durante o mês fechado
    (datetime(2026, 8, 1, 1, 0, tzinfo=timezone.utc), True),   # 31/07 no fuso do app
    (datetime(2026, 8, 1, 3, 0, tzinfo=timezone.utc), False),  # 01/08 00:00 no fuso do app
])
def test_19_conexao_terminal_com_updated_at_no_periodo_comparado_nao_celebra(llm, monkeypatch, status, updated_at,
                                                                             aparece):
    """P6 com a P10 e a P11 do dono: a conexão PAUSED ou DELETED cala só se o `updated_at` dela (data aproximada da
    parada) cai a partir do dia 1 do mês anterior ao fechado, no fuso do app. Com o `updated_at` antes disso (o trial
    que venceu meses atrás), não cala."""
    _relogio(monkeypatch)
    uid = _usuario("pro")
    _cena(uid)
    _terminal(uid, status, updated_at)
    assert len(_positivos(uid)) == (1 if aparece else 0)


def _updated_at(cid):
    return q("select updated_at from open_finance_connections where id = %s", (cid,))["updated_at"]


@pytest.mark.parametrize("status", ["PAUSED", "DELETED"])
def test_19d_tentativa_de_sync_nao_move_o_updated_at_da_conexao_terminal(llm, monkeypatch, status):
    """A premissa da P10: `mark_sync_attempt` não carimba `updated_at` de conexão terminal. A conexão passa a terminal
    pelo escritor real (pausa por plano ou webhook `item/deleted`) e o `updated_at` (data aproximada da parada) é posto
    no passado, antes do período comparado; depois a tentativa real de sync não pode trazer o `updated_at` para "agora"
    e calar o aviso. Positivo: numa conexão ativa a mesma tentativa carimba."""
    from db.open_finance import pause_open_finance_connection, update_pluggy_open_finance_item_status
    from db.open_finance_state import mark_sync_attempt
    _relogio(monkeypatch)
    uid = _usuario("pro")
    _cena(uid)
    item = f"it-p10-sync-{status}-{uid}"
    cid = conexao(uid, item, sync=horas_atras(1))
    conta(cid, f"acc-p10-sync-{cid}", D("10"))
    if status == "PAUSED":
        pause_open_finance_connection(cid)
    else:
        update_pluggy_open_finance_item_status(item, "DELETED")
    q("update open_finance_connections set updated_at = %s where id = %s", (datetime(2025, 1, 10, tzinfo=_tz()), cid))
    updated_at_terminal = _updated_at(cid)
    assert mark_sync_attempt(cid) == 0
    assert _updated_at(cid) == updated_at_terminal
    assert len(_positivos(uid)) == 1
    ativa = conexao(uid, f"it-p10-ativa-{uid}", sync=horas_atras(1))  # positivo: a tentativa carimba a conexão viva
    antes = _updated_at(ativa)
    assert mark_sync_attempt(ativa) == 1 and _updated_at(ativa) > antes


def test_19b_banco_em_dia_avisa_e_a_conexao_terminal_de_outro_usuario_nao_cala(llm, monkeypatch):
    _relogio(monkeypatch)
    a, b = _usuario("pro"), _usuario("pro")
    _cena(a)
    conta(conexao(a, f"it-ok-{a}", sync=horas_atras(1)), f"acc-ok-{a}", D("10"))  # positivo: banco em dia
    _terminal(b, "PAUSED", datetime(2026, 8, 15, 10, tzinfo=_tz()))
    assert len(_positivos(a)) == 1


def test_19c_reconectar_volta_a_avisar(llm, monkeypatch):
    """A conexão ficou PAUSED com o `updated_at` (data aproximada da parada) em agosto e o usuário reconectou (o mesmo
    item, pela rota do widget): não é mais terminal."""
    _relogio(monkeypatch)
    uid = _usuario("pro")
    _cena(uid)
    item = f"it-rec-{uid}"
    db.save_pluggy_open_finance_item(uid, {"id": item, "status": "UPDATED", "connector": {"id": 612, "name": "Nubank"}})
    q("update open_finance_connections set status = 'PAUSED', updated_at = %s where user_id = %s and provider_item_id = %s",
      (datetime(2026, 8, 15, 10, tzinfo=_tz()), uid, item))
    assert _positivos(uid) == []
    db.save_pluggy_open_finance_item(uid, {"id": item, "status": "UPDATED", "connector": {"id": 612, "name": "Nubank"}})
    assert len(_positivos(uid)) == 1


@pytest.mark.parametrize("agora,aparece", [(datetime(2026, 10, 4, 23, 59, tzinfo=_tz()), False),
                                           (datetime(2026, 10, 5, 0, 0, tzinfo=_tz()), True)])
def test_20_so_a_partir_do_dia_minimo_do_mes_seguinte(llm, monkeypatch, agora, aparece):
    _relogio(monkeypatch, agora)
    uid = _usuario("pro")
    _cena(uid)
    assert len(_positivos(uid)) == (1 if aparece else 0)


def test_21_banco_em_dia_no_dia_1_nao_avalia_setembro(llm, monkeypatch):
    """O test_b do Tester: no dia 1 o débito do dia 30 ainda pode chegar no sync seguinte. Nada até o dia mínimo."""
    _relogio(monkeypatch, datetime(2026, 10, 1, 10, tzinfo=_tz()))
    uid = _usuario("pro")
    _cena(uid)
    conta(conexao(uid, f"it-b-{uid}", sync=horas_atras(1)), f"acc-b-{uid}", D("10"))
    assert _positivos(uid) == []


def test_22_mes_base_que_o_banco_trouxe_pela_metade(llm, monkeypatch):
    """O test_c do Tester: um gasto manual em julho satisfaz a P1, mas o banco só trouxe agosto a partir do dia 20."""
    _relogio(monkeypatch)
    uid = _usuario("pro")
    _lanca(uid, "despesa", 1, date(2026, 7, 10))
    conta(conexao(uid, f"it-c-{uid}", sync=horas_atras(1)), f"acc-c-{uid}", D("10"))
    for d in range(20, 31):
        _of(uid, date(2026, 8, d), 100)
    _of(uid, date(2026, 9, 5), 3000, "receita")
    for d in range(1, 31):
        _of(uid, date(2026, 9, d), 70)
    assert _positivos(uid) == []


@pytest.mark.parametrize("dia,aparece", [(7, True), (8, False)])
def test_23_algum_lancamento_nos_primeiros_dias_do_mes_base(llm, monkeypatch, dia, aparece):
    _relogio(monkeypatch)
    uid = _usuario("pro")
    _lanca(uid, "despesa", 1, date(2026, 7, 10))
    _lanca(uid, "receita", 3000, date(2026, 8, dia), "salario")  # o único lançamento de agosto
    _lanca(uid, "despesa", 2480, date(2026, 8, 25), "mercado")
    _mes(uid, 2026, 9, 3000, 2180)
    assert len(_positivos(uid)) == (1 if aparece else 0)


def test_24_o_lancamento_de_outro_usuario_no_comeco_do_mes_nao_libera(llm, monkeypatch):
    _relogio(monkeypatch)
    a, b = _usuario("pro"), _usuario("pro")
    _lanca(a, "despesa", 1, date(2026, 7, 10))
    _lanca(a, "receita", 3000, date(2026, 8, 8), "salario")
    _lanca(a, "despesa", 2480, date(2026, 8, 25), "mercado")
    _mes(a, 2026, 9, 3000, 2180)
    _lanca(b, "despesa", 10, date(2026, 8, 3))  # B lançou no dia 3; A só no dia 8
    assert _positivos(a) == []


@pytest.mark.parametrize("como", ["transferencia_interna", "compra_no_cartao"])
def test_25_a_cobertura_do_comeco_do_mes_e_indicio_e_nao_garantia(llm, monkeypatch, como):
    """Positivo documentado (P8 é indício): o começo do mês base coberto só por uma transferência interna, ou só por
    uma compra no cartão, já libera o aviso."""
    _relogio(monkeypatch)
    uid = _usuario("pro")
    _lanca(uid, "despesa", 1, date(2026, 7, 10))
    _lanca(uid, "receita", 3000, date(2026, 8, 25), "salario")
    if como == "transferencia_interna":
        _lanca(uid, "despesa", 50, date(2026, 8, 3), "transferencia_interna", is_internal_movement=True)
        _lanca(uid, "despesa", 2480, date(2026, 8, 26), "mercado")
    else:
        cartao = db.create_card(uid, "Nubank", closing_day=25, due_day=5)
        db.add_credit_purchase(uid, cartao, 80, "mercado", "compra", date(2026, 8, 3))  # fatura com fim em 25/08
        _lanca(uid, "despesa", 2400, date(2026, 8, 26), "mercado")
    _mes(uid, 2026, 9, 3000, 2180)
    assert [i["message"].split(" Sobra é")[0] for i in _positivos(uid)] == ["R$ 300,00 a mais que agosto."]


@pytest.mark.parametrize("v2,pro", [(True, None), (False, False), (False, True)])
def test_26_financial_comparison_e_insights_liberam_os_mesmos_planos(monkeypatch, v2, pro):
    """`positive_result` (B4, lê `comparar_mes`, gate `financial_comparison`) e `category_spike` (B1, compara com o mês
    anterior) ficam FORA de `RECURSO_POR_TIPO` porque supõem que `financial_comparison` e `insights` liberam os mesmos
    planos em todo modo. Se isso mudar, o cache único passa a mostrar o item a quem perdeu o recurso, sem nada vermelho:
    este teste é o que fica vermelho. Fonte: `plan_service.FEATURE_MIN_TIER_V2` (v2) e `plan_gate_ok` (legado)."""
    from core.services import plan_service as ps
    msg = ("`financial_comparison` e `insights` divergiram: ponha `positive_result` e `category_spike` em "
           "`insights_contrato.RECURSO_POR_TIPO` com o recurso `financial_comparison`")
    if v2:
        assert ps.FEATURE_MIN_TIER_V2["financial_comparison"] == ps.FEATURE_MIN_TIER_V2["insights"], msg
        return
    monkeypatch.setattr(ps, "plans_v2_enabled", lambda: False)
    monkeypatch.setattr(ps, "is_pro", lambda _uid: pro)
    assert ps.plan_gate_ok(1, "financial_comparison") == ps.plan_gate_ok(1, "insights"), msg
