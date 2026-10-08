"""PL-05: resumo semanal completo (Plus/Pro): resultado, maior categoria, comparação com a
semana equivalente e atualização dos bancos. Dia, horário, claim e gate de plano do job NÃO mudam.

Grupos (A a F do plano):
  A  puros: janelas, variação, texto (borda do banco trocada por fake)
  B  banco real: totais, contagem, maior categoria, isolamento, cartão fora, claim liberado
  C  job do WhatsApp ponta a ponta (plano antes do claim; build que explode devolve o claim)
  D  conversa (`handle_incoming`)
  E  guarda do template homologado (duas cópias da mesma regra)
  F  Discord

Controles NEGATIVOS medidos (sem o conserto, vermelho):
  - contagem `len(launches)` no lugar do filtro  -> B `test_totais_...` e o diferencial
  - `include_card` ignorado (cartão sempre somado) -> B `test_cartao_...`
  - sem a guarda de base zero em `_variacao`      -> A `test_variacao_...`
  - `claim and plan_gate_ok` (ordem trocada)      -> C passo 1
  - sem o try/release no tick                     -> C passo 5
  Rodada 2 (cada um medido): bancos/comparação sem try, Discord sem try, sem teto de tentativas,
  release sem try, arredondamento duplo, zero negativo, categoria crua, release sem `user_id`,
  sem a guarda `despesa > 0`, resultado com pct, bancos reais (nunca/deletada).
  Rodada 4: maior categoria, cartões e semana anterior sem try (o template segue saindo).
"""
from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest

import core.reports.reports_daily as rd
import core.reports.weekly as wk
import db
from conftest import promote_to_pro, usuario_pagante
from tests._patrimonio_helpers import q

_TZ = ZoneInfo("America/Sao_Paulo")
D = Decimal
SEGUNDA = datetime(2026, 10, 5, 10, 0, tzinfo=_TZ)   # semana fechada: 28/09 a 04/10; anterior 21/09 a 27/09
SEMANA = (date(2026, 9, 28), date(2026, 10, 4))
ANTERIOR = (date(2026, 9, 21), date(2026, 9, 27))
LEGADAS = {"start", "end", "saldo", "gastos", "receita", "lancamentos"}


# ── A. puros ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("hoje,closed,periodo,anterior", [
    (date(2026, 10, 5), True, (date(2026, 9, 28), date(2026, 10, 4)), (date(2026, 9, 21), date(2026, 9, 27))),
    (date(2026, 10, 7), True, (date(2026, 9, 28), date(2026, 10, 4)), (date(2026, 9, 21), date(2026, 9, 27))),
    (date(2026, 10, 5), False, (date(2026, 10, 5), date(2026, 10, 5)), (date(2026, 9, 28), date(2026, 9, 28))),
    (date(2026, 10, 8), False, (date(2026, 10, 5), date(2026, 10, 8)), (date(2026, 9, 28), date(2026, 10, 1))),
    (date(2026, 10, 11), False, (date(2026, 10, 5), date(2026, 10, 11)), (date(2026, 9, 28), date(2026, 10, 4))),
    # virada de ano
    (date(2027, 1, 4), True, (date(2026, 12, 28), date(2027, 1, 3)), (date(2026, 12, 21), date(2026, 12, 27))),
    (date(2027, 1, 7), False, (date(2027, 1, 4), date(2027, 1, 7)), (date(2026, 12, 28), date(2026, 12, 31))),
])
def test_semana_equivalente(hoje, closed, periodo, anterior):
    p, a = wk._semana_equivalente(hoje, closed)
    assert (p, a) == (periodo, anterior)
    assert (p[1] - p[0]) == (a[1] - a[0])   # n dias contra n dias


def test_variacao_base_zero_e_sinais():
    # base zero: sem pct (nada de divisão por zero nem "+100%" inventado)
    assert wk._variacao(D("50"), D("0")) == {"anterior": D("0"), "delta": D("50"), "pct": None}
    assert wk._variacao(D("0"), D("0"))["pct"] is None
    # atual zero com base real: -100% é verdade e fica
    assert wk._variacao(D("0"), D("80"))["pct"] == D("-100.0")
    # positivo: base real dá o percentual correto, HALF_UP em 1 casa
    assert wk._variacao(D("112"), D("100"))["pct"] == D("12.0")
    assert wk._variacao(D("100"), D("930.40"))["pct"] == D("-89.3")
    # resultado: sem pct (base negativa/trocando de sinal engana)
    v = wk._variacao(D("-30"), D("40"), com_pct=False)
    assert v == {"anterior": D("40"), "delta": D("-70")}


def _fake(monkeypatch, *, now=SEGUNDA, comparar=True, corte=None, cartao=False, saldo=2310.0,
          bancos=("sem_banco", None, 0), top=(("Mercado", 320.0),),
          janelas=None):
    """Troca só a borda do banco. `janelas`: {(inicio, fim): (receita, despesa, n)}."""
    janelas = janelas or {SEMANA: (1200.0, 830.40, 14), ANTERIOR: (1200.0, 930.40, 10)}
    monkeypatch.setattr(wk, "now_tz", lambda: now)
    monkeypatch.setattr(rd, "get_consolidated_balance",
                        lambda uid: {"manual": saldo, "consolidated": saldo})
    monkeypatch.setattr(wk, "get_summary_by_period", lambda uid, i, f: (
        lambda w: {"receita": w[0], "despesa": w[1]})(janelas.get((i, f), (0.0, 0.0, 0))))

    def launches(uid, i, f):
        n = janelas.get((i, f), (0, 0, 0))[2]
        # as linhas que NÃO entram nos totais (interna, aporte) não podem ser contadas
        return ([{"tipo": "despesa", "is_internal_movement": False}] * n
                + [{"tipo": "despesa", "is_internal_movement": True},
                   {"tipo": "aporte_investimento", "is_internal_movement": False}])
    monkeypatch.setattr(wk, "get_launches_by_period", launches)
    monkeypatch.setattr(wk, "get_top_expense_categories",
                        lambda uid, i, f, limit=5, by_bill_month=False, include_card=True:
                        [{"categoria": c, "total": t} for c, t in top])
    monkeypatch.setattr(wk, "list_cards", lambda uid: [{"id": 1}] if cartao else [])
    monkeypatch.setattr(wk, "_bancos", lambda uid, agora: bancos)
    monkeypatch.setattr(wk, "finish_report", lambda uid, lines: "\n".join(lines))
    monkeypatch.setattr("core.services.plan_service.plan_gate_ok", lambda uid, f: comparar)
    monkeypatch.setattr("core.services.plan_service.history_earliest_date", lambda uid, now=None: corte)


def test_texto_plus_fechado_completo(monkeypatch):
    _fake(monkeypatch, cartao=True, bancos=("atualizado", datetime(2026, 10, 5, 8, 12, tzinfo=_TZ), 0))
    assert wk.build_weekly_report_text(1, closed=True) == "\n".join([
        "📊 *Resumo semanal do Bot Financeiro*",
        "📅 Semana de 28/09 a 04/10/2026 (seg a dom)",
        "🔄 Bancos atualizados em 05/10 às 08:12",
        "",
        "📈 Receitas: R$ 1.200,00",
        "📉 Despesas: R$ 830,40",
        "✅ Resultado da semana: +R$ 369,60 (receitas - despesas)",
        "🏷️ Maior categoria: Mercado, R$ 320,00 (39% das despesas)",
        "↔️ Contra 21/09 a 27/09: despesas -11%, receitas +0%, resultado +R$ 100,00",
        "🧾 Lançamentos: 14",
        "",
        "🏦 Saldo atual nas contas: R$ 2.310,00 (é o que você tem hoje, não o resultado da semana)",
        "ℹ️ Compras no cartão ficam na fatura e não entram neste resumo.",
    ])


def test_essencial_ve_resultado_e_categoria_mas_nao_a_comparacao(monkeypatch):
    _fake(monkeypatch, comparar=False)
    d = wk.build_weekly_report_data(1, closed=True)
    assert d["comparacao"] is None and d["comparacao_motivo"] == "plano"
    txt = wk.build_weekly_report_text(1, closed=True)
    assert "Resultado da semana: +R$ 369,60" in txt and "Maior categoria: Mercado" in txt
    assert "↔️" not in txt and "Contra " not in txt
    assert "Compras no cartão" not in txt          # sem cartão cadastrado, sem o aviso


def test_comparacao_sem_base_nao_compara(monkeypatch):
    _fake(monkeypatch, janelas={SEMANA: (1200.0, 830.40, 14), ANTERIOR: (0.0, 0.0, 0)})
    d = wk.build_weekly_report_data(1, closed=True)
    assert d["comparacao"] is None and d["comparacao_motivo"] == "sem_base"
    assert "↔️ Sem lançamentos na semana anterior para comparar." in wk.build_weekly_report_text(1, closed=True)


def test_comparacao_base_real_com_despesa_anterior_zerada_mostra_valor_nunca_percentual(monkeypatch):
    _fake(monkeypatch, janelas={SEMANA: (1200.0, 830.40, 14), ANTERIOR: (1200.0, 0.0, 3)})
    d = wk.build_weekly_report_data(1, closed=True)
    assert d["comparacao"]["despesa"]["pct"] is None
    txt = wk.build_weekly_report_text(1, closed=True)
    assert "despesas novas (R$ 830,40)" in txt and "receitas +0%" in txt
    assert not re.search(r"\b(nan|inf|none)\b", txt.lower())


def test_comparacao_fora_do_historico_do_plano(monkeypatch):
    _fake(monkeypatch, corte=date(2026, 9, 25))     # corta a semana anterior
    d = wk.build_weekly_report_data(1, closed=True)
    assert d["comparacao"] is None and d["comparacao_motivo"] == "inicio_do_historico"
    assert "fora do histórico do seu plano" in wk.build_weekly_report_text(1, closed=True)


def test_parcial_resultado_negativo_e_sem_despesa(monkeypatch):
    qua = datetime(2026, 10, 7, 9, 0, tzinfo=_TZ)
    _fake(monkeypatch, now=qua, top=(), janelas={
        (date(2026, 10, 5), date(2026, 10, 7)): (100.0, 0.0, 1),
        (date(2026, 9, 28), date(2026, 9, 30)): (0.0, 40.0, 2)})
    d = wk.build_weekly_report_data(1)
    assert d["periodo"]["parcial"] is True and d["maior_categoria"] is None
    txt = wk.build_weekly_report_text(1)
    assert "📅 Semana em andamento: 05/10 a 07/10/2026" in txt
    assert "Maior categoria" not in txt            # sem despesa, sem categoria (e sem "None")
    assert not re.search(r"\b(nan|inf|none)\b", txt.lower())
    assert "despesas -100%" in txt and "receitas novas (R$ 100,00)" in txt

    _fake(monkeypatch, top=(("Lazer", 50.0),), janelas={SEMANA: (10.0, 50.0, 2), ANTERIOR: (0.0, 5.0, 1)})
    assert "Resultado da semana: -R$ 40,00" in wk.build_weekly_report_text(1, closed=True)


@pytest.mark.parametrize("bancos,linha", [
    (("sem_banco", None, 0), None),
    (("nunca_sincronizado", None, 1), "🔄 Bancos conectados ainda não sincronizaram"),
    (("desatualizado", datetime(2026, 10, 1, 7, 5, tzinfo=_TZ), 0),
     "🔄 Bancos desatualizados: última atualização em 01/10 às 07:05\n"),
    (("desatualizado", datetime(2026, 10, 1, 7, 5, tzinfo=_TZ), 2),
     "🔄 Bancos desatualizados: última atualização em 01/10 às 07:05 e 2 banco(s) ainda não sincronizado(s)\n"),
])
def test_atualizacao_dos_bancos(monkeypatch, bancos, linha):
    _fake(monkeypatch, bancos=bancos)
    txt = wk.build_weekly_report_text(1, closed=True)
    assert (linha in txt + "\n") if linha else ("🔄" not in txt)


def test_chaves_legadas_intactas_e_valores_de_uma_linha(monkeypatch):
    _fake(monkeypatch, cartao=True, bancos=("atualizado", datetime(2026, 10, 5, 8, 12, tzinfo=_TZ), 0))
    s = wk.build_weekly_report_summary(1, closed=True)
    assert LEGADAS <= set(s)
    assert (s["start"], s["end"], s["saldo"], s["gastos"], s["receita"], s["lancamentos"]) == (
        "28/09/2026", "04/10/2026", "R$ 2.310,00", "R$ 830,40", "R$ 1.200,00", "14")
    assert all(isinstance(v, str) and "\n" not in v for v in s.values())   # Meta recusa "\n" em variável nomeada
    d = wk.build_weekly_report_data(1, closed=True)
    assert d["cartao_incluido"] is False and isinstance(d["receita"], Decimal)
    assert d["n_lancamentos"] == 14      # interna e aporte da janela não entram


# ── E. guarda do template homologado ────────────────────────────────────────

@pytest.mark.parametrize("kind", ["weekly", "monthly"])
def test_template_homologado_mesmas_variaveis(kind):
    from adapters.whatsapp.wa_app import _periodic_template_named_body_params
    tpl = json.loads((Path(__file__).parent.parent / "docs/whatsapp_report_templates.json").read_text())[kind]
    body = next(c for c in tpl["components"] if c["type"] == "BODY")
    homologadas = {p["param_name"] for p in body["example"]["body_text_named_params"]}
    resumo = {"start": "a", "end": "b", "saldo": "c", "gastos": "d", "receita": "e", "lancamentos": "f"}
    assert set(_periodic_template_named_body_params(resumo)) == homologadas == set(re.findall(r"{{(\w+)}}", body["text"]))


# ── B. banco real ───────────────────────────────────────────────────────────

def _lanc(uid, tipo, valor, quando, categoria=None, interno=False):
    # naive de propósito: o corte do relatório é data ingênua no fuso da sessão (limite herdado do Q18)
    q("""insert into launches (user_id, tipo, valor, categoria, criado_em, is_internal_movement)
         values (%s, %s, %s, %s, %s, %s) returning id""", (uid, tipo, valor, categoria, quando, interno))


def _semear(uid):
    _lanc(uid, "receita", 1000, datetime(2026, 9, 28, 0, 0))                    # seg 00:00 (limite)
    _lanc(uid, "entrada", 200, datetime(2026, 10, 4, 23, 59))                   # dom 23:59, tipo legado
    _lanc(uid, "despesa", 300, datetime(2026, 9, 30, 12), "mercado")
    _lanc(uid, "saida", 20, datetime(2026, 9, 30, 13), "mercado")               # legado, mesma categoria
    _lanc(uid, "despesa", 100, datetime(2026, 10, 1, 12), "lazer")
    _lanc(uid, "despesa", 500, datetime(2026, 10, 1, 12), "transferência", interno=True)
    _lanc(uid, "despesa", 700, datetime(2026, 10, 2, 12), "fatura", interno=True)   # pagamento de fatura
    _lanc(uid, "aporte_investimento", 50, datetime(2026, 10, 2, 12), "investimento")
    _lanc(uid, "despesa", 999, datetime(2026, 10, 5, 0, 0), "fora")             # segunda seguinte 00:00: fora
    _lanc(uid, "despesa", 200, datetime(2026, 9, 27, 23, 59), "mercado")        # semana anterior (limite)
    _lanc(uid, "receita", 1000, datetime(2026, 9, 21, 0, 0))                    # semana anterior (limite)


def _data(uid, closed=True, now=SEGUNDA):
    with patch.object(wk, "now_tz", return_value=now):
        return wk.build_weekly_report_data(uid, closed=closed)


def test_totais_contagem_maior_categoria_e_isolamento_entre_usuarios():
    a, b, vazio = usuario_pagante("plus"), usuario_pagante("plus"), usuario_pagante("plus")
    _semear(a)
    _lanc(b, "despesa", 5000, datetime(2026, 9, 30, 12), "mercado")             # OUTRO usuário, mesma janela
    _lanc(b, "receita", 9999, datetime(2026, 9, 29, 12))

    d = _data(a)
    assert (d["receita"], d["despesa"], d["resultado"]) == (D("1200.00"), D("420.00"), D("780.00"))
    assert d["n_lancamentos"] == 5                      # interna, fatura, aporte e a de fora não contam
    assert d["maior_categoria"] == {"categoria": "mercado", "total": D("320.00"), "pct_da_despesa": D("76.2")}
    c = d["comparacao"]
    assert (c["despesa"]["pct"], c["receita"]["pct"], c["resultado"]["delta"]) == (D("110.0"), D("20.0"), D("-20.00"))
    assert d["comparacao_motivo"] is None and d["bancos"] == "sem_banco"

    db_ = _data(b)                                       # nada de A vaza para B (e vice-versa)
    assert (db_["receita"], db_["despesa"], db_["n_lancamentos"]) == (D("9999.00"), D("5000.00"), 2)
    assert db_["comparacao_motivo"] == "sem_base"
    v = _data(vazio)
    assert (v["receita"], v["despesa"], v["n_lancamentos"], v["maior_categoria"]) == (D("0.00"), D("0.00"), 0, None)


def test_diferencial_semanal_bate_com_a_regra_unica_do_mes_sem_cartao():
    """§0.7: para quem não tem cartão, o semanal é a mesma conta do Entrou/Saiu (`TOTAIS_SQL`)."""
    from db import resumo_mes
    uid = usuario_pagante("plus")
    _semear(uid)
    d = _data(uid)
    with db.get_conn() as conn, conn.cursor() as cur:
        t = resumo_mes.totais(cur, uid, SEMANA[0], date(2026, 10, 5))
    assert (d["receita"], d["despesa"], d["n_lancamentos"]) == (t["entrou"], t["saiu"], t["n"])


def test_cartao_nao_entra_no_semanal_nem_na_maior_categoria():
    uid = usuario_pagante("plus")
    _lanc(uid, "despesa", 120, datetime(2026, 9, 30, 12), "mercado")
    card = db.create_card(uid, "Nubank", closing_day=10, due_day=17)
    db.add_credit_purchase(uid, card, 900, "eletronicos", "tv", date(2026, 9, 30))
    d = _data(uid)
    assert d["despesa"] == D("120.00") and d["cartao_incluido"] is False and d["tem_cartao"] is True
    assert d["maior_categoria"]["categoria"] == "mercado"      # sem include_card=False seria "eletronicos"
    with patch.object(wk, "now_tz", return_value=SEGUNDA):
        assert "Compras no cartão ficam na fatura" in wk.build_weekly_report_text(uid, closed=True)
    # positivo: o default de get_top_expense_categories continua somando o cartão (2 chamadores de produção)
    todos = db.get_top_expense_categories(uid, *SEMANA)
    assert {c["categoria"] for c in todos} == {"mercado", "eletronicos"}
    sem = db.get_top_expense_categories(uid, *SEMANA, include_card=False)
    assert [c["categoria"] for c in sem] == ["mercado"]


def test_release_so_devolve_o_proprio_claim():
    uid = usuario_pagante("plus")
    seg, outra = date(2026, 10, 5), date(2026, 10, 12)
    assert db.claim_weekly_report_send(uid, seg) is True
    assert db.claim_weekly_report_send(uid, seg) is False        # dedup do claim
    db.release_weekly_report_claim(uid, outra)                   # data diferente: não pisa
    assert db.claim_weekly_report_send(uid, seg) is False
    db.release_weekly_report_claim(uid, seg)
    assert db.claim_weekly_report_send(uid, seg) is True         # positivo: devolvido, dá para reservar de novo


# ── C. job do WhatsApp ponta a ponta ────────────────────────────────────────

def _prefs_semanal(uid):
    return q("select last_weekly_sent_date from daily_report_prefs where user_id=%s", (uid,))["last_weekly_sent_date"]


@pytest.fixture()
def job(monkeypatch):
    """Tick real (claim, gate de plano, builder e prefs reais); só o envio, o relógio e a lista
    de destinatários são trocados. Devolve (tick, envios)."""
    from adapters.whatsapp import wa_app as wa
    envios = []
    monkeypatch.setattr(wa, "now_tz", lambda: SEGUNDA)
    monkeypatch.setattr(wk, "now_tz", lambda: SEGUNDA)
    monkeypatch.setattr(wa, "_periodic_template_config", lambda kind: {"name": "resumo_semanal", "language_code": "pt_BR"})
    monkeypatch.setattr(wa, "_runtime_instance_details", lambda: {"pid": 1, "hostname": "t"})
    monkeypatch.setattr(wa, "list_identities_by_user",
                        lambda uid: [{"provider": "whatsapp", "external_id": "5511999999999"}])
    monkeypatch.setattr(wa, "log_system_event_sync", lambda *a, **kw: None)
    monkeypatch.setattr(wa, "send_template", lambda to, name, **kw: envios.append((to, kw["named_body_params"])))

    monkeypatch.setattr(wa, "_WEEKLY_BUILD_FALHAS", {})

    def tick(*uids):
        monkeypatch.setattr(wa, "list_users_with_weekly_report_enabled", lambda: list(uids))
        monkeypatch.setattr(wa, "list_users_with_monthly_report_enabled", lambda: [])
        wa._periodic_report_tick()
    return SimpleNamespace(tick=tick, envios=envios, wa=wa, mp=monkeypatch)


def test_job_downgrade_nao_consome_claim_e_plus_envia_uma_vez(job):
    uid = usuario_pagante("essencial")
    _semear(uid)
    db.set_weekly_report_enabled(uid, True)

    job.tick(uid)                                   # 1. Essencial com preferência antiga: nada, claim intacto
    assert job.envios == [] and _prefs_semanal(uid) is None

    promote_to_pro(uid, "pro")                      # 2. sobe para Plus no mesmo dia (E1 do roteiro)
    job.tick(uid)
    assert len(job.envios) == 1 and _prefs_semanal(uid) == date(2026, 10, 5)

    job.tick(uid)                                   # 3. dedup do claim
    assert len(job.envios) == 1

    to, params = job.envios[0]                      # 4. as 5 variáveis homologadas, valores da semana
    assert params == {"periodo": "28/09/2026 a 04/10/2026", "saldo": "R$ 0,00", "gastos": "R$ 420,00",
                      "receita": "R$ 1.200,00", "lancamentos": "5"}


def test_job_build_que_explode_devolve_o_claim_e_o_proximo_ciclo_envia(job):
    uid = usuario_pagante("plus")
    _semear(uid)
    db.set_weekly_report_enabled(uid, True)
    real, chamadas = job.wa.build_weekly_report_summary, []

    def explode_na_primeira(*a, **kw):
        chamadas.append(1)
        if len(chamadas) == 1:
            raise RuntimeError("banco fora do ar")
        return real(*a, **kw)
    job.mp.setattr(job.wa, "build_weekly_report_summary", explode_na_primeira)

    job.tick(uid)
    assert job.envios == [] and _prefs_semanal(uid) is None      # semana devolvida, não perdida
    job.tick(uid)
    assert len(job.envios) == 1 and _prefs_semanal(uid) == date(2026, 10, 5)


# ── D. conversa ─────────────────────────────────────────────────────────────

def _diga(uid, texto):
    import core.handle_incoming as hi
    from core.types import IncomingMessage
    out = hi.handle_incoming(IncomingMessage(platform="whatsapp", user_id=uid, text=texto,
                                             message_id=uuid.uuid4().hex, attachments=[], external_id="", raw={}))
    return "\n".join(m.text for m in out)


def test_conversa_gasto_e_depois_resumo_semanal_plus():
    uid = usuario_pagante("plus")
    assert "50" in _diga(uid, "gastei 50 no mercado")
    txt = _diga(uid, "resumo semanal")
    assert "Resumo semanal do Bot Financeiro" in txt, txt
    assert "Despesas: R$ 50,00" in txt and "Resultado da semana: -R$ 50,00" in txt, txt
    assert "Maior categoria:" in txt and "(100% das despesas)" in txt, txt
    assert "Saldo atual nas contas" in txt


def test_conversa_essencial_responde_o_resumo_mas_nao_liga_o_automatico():
    uid = usuario_pagante("essencial")
    _diga(uid, "gastei 50 no mercado")
    assert "disponível nos planos Plus e Pro" in _diga(uid, "ligar resumo semanal")
    txt = _diga(uid, "resumo semanal")
    assert "Despesas: R$ 50,00" in txt and "Maior categoria:" in txt, txt
    assert "↔️" not in txt                                   # D4: sem a linha de comparação


# ── F. Discord ──────────────────────────────────────────────────────────────

def test_discord_manda_o_texto_rico(monkeypatch):
    uid = usuario_pagante("plus")
    _semear(uid)
    monkeypatch.setattr(wk, "now_tz", lambda: SEGUNDA)
    monkeypatch.setattr(rd, "now_tz", lambda: SEGUNDA)
    monkeypatch.setattr(rd, "list_users_with_weekly_report_enabled", lambda: [uid])
    monkeypatch.setattr(rd, "list_users_with_monthly_report_enabled", lambda: [])
    monkeypatch.setattr(rd, "list_identities_by_user", lambda u: [{"provider": "discord", "external_id": "100"}])
    send = AsyncMock()
    bot = SimpleNamespace(fetch_user=AsyncMock(return_value=SimpleNamespace(send=send)))
    asyncio.run(rd._periodic_reports_discord.coro(bot))
    (texto,), _ = send.await_args
    assert "Resultado da semana: +R$ 780,00" in texto and "Maior categoria: mercado" in texto
    assert "Contra 21/09 a 27/09" in texto


# ── Rodada 2: dado extra não derruba o envio, teto de retentativa, arredondamento, apresentação ─────

def test_job_bancos_ou_comparacao_fora_do_ar_nao_derrubam_o_template(job):
    uid = usuario_pagante("plus")
    _semear(uid)
    db.set_weekly_report_enabled(uid, True)

    def quebra(*a, **kw):
        raise RuntimeError("falha de dado extra")
    job.mp.setattr(wk, "_bancos", quebra)
    job.mp.setattr("core.services.plan_service.history_earliest_date", quebra)

    job.tick(uid)
    assert len(job.envios) == 1 and _prefs_semanal(uid) == date(2026, 10, 5)
    assert job.envios[0][1] == {"periodo": "28/09/2026 a 04/10/2026", "saldo": "R$ 0,00", "gastos": "R$ 420,00",
                                "receita": "R$ 1.200,00", "lancamentos": "5"}
    txt = wk.build_weekly_report_text(uid, closed=True)           # texto livre: sem a linha de bancos
    assert "🔄" not in txt and "Comparação com a semana anterior indisponível no momento." in txt
    assert "Despesas: R$ 420,00" in txt
    d = wk.build_weekly_report_data(uid, closed=True)
    assert (d["bancos"], d["atualizado_em"], d["comparacao"], d["comparacao_motivo"]) == (None, None, None, "indisponivel")


def test_job_build_sempre_falhando_para_no_teto_de_tentativas(job):
    uid = usuario_pagante("plus")
    db.set_weekly_report_enabled(uid, True)
    builds = []

    def sempre_falha(*a, **kw):
        builds.append(1)
        raise RuntimeError("boom")
    job.mp.setattr(job.wa, "build_weekly_report_summary", sempre_falha)

    for _ in range(14):
        job.tick(uid)
    assert len(builds) == 10 and job.envios == []                  # 10 builds, depois 0 (nada de 2880/dia)
    assert _prefs_semanal(uid) == date(2026, 10, 5)               # na última a semana fica perdida de propósito
    assert job.wa._WEEKLY_BUILD_FALHAS == {(uid, date(2026, 10, 5)): 10}


def test_contador_de_falhas_limpa_semanas_antigas():
    from adapters.whatsapp import wa_app as wa
    with patch.dict(wa._WEEKLY_BUILD_FALHAS, {(1, date(2026, 9, 28)): 2}, clear=True):
        assert wa._contar_falha_do_build_semanal(1, date(2026, 10, 5)) == 1
        assert wa._WEEKLY_BUILD_FALHAS == {(1, date(2026, 10, 5)): 1}


def test_job_release_que_explode_nao_pula_os_outros_usuarios(job):
    a, b = usuario_pagante("plus"), usuario_pagante("plus")
    for u in (a, b):
        db.set_weekly_report_enabled(u, True)
    builds = []

    def falha(uid, **kw):
        builds.append(uid)
        raise RuntimeError("boom")

    def release_quebrado(*args):
        raise RuntimeError("banco caiu")
    job.mp.setattr(job.wa, "build_weekly_report_summary", falha)
    job.mp.setattr(job.wa, "release_weekly_report_claim", release_quebrado)

    job.tick(a, b)                                                 # não pode levantar nem parar no primeiro
    assert sorted(builds) == sorted([a, b])


def test_discord_build_que_falha_para_um_usuario_nao_derruba_os_outros(monkeypatch):
    u1, u2 = usuario_pagante("plus"), usuario_pagante("plus")
    monkeypatch.setattr(rd, "now_tz", lambda: SEGUNDA)
    monkeypatch.setattr(rd, "list_users_with_weekly_report_enabled", lambda: [u1, u2])
    monkeypatch.setattr(rd, "list_users_with_monthly_report_enabled", lambda: [])
    monkeypatch.setattr(rd, "list_identities_by_user", lambda u: [{"provider": "discord", "external_id": str(u)}])

    def build(uid, closed=False):
        if uid == u1:
            raise RuntimeError("boom")
        return f"resumo {uid}"
    monkeypatch.setattr(rd, "build_weekly_report_text", build)
    enviados = []
    bot = SimpleNamespace(fetch_user=AsyncMock(side_effect=lambda i: SimpleNamespace(
        send=AsyncMock(side_effect=lambda m: enviados.append((int(i), m))))))
    asyncio.run(rd._periodic_reports_discord.coro(bot))
    assert enviados == [(u2, f"resumo {u2}")]


def test_release_nao_zera_o_claim_de_outro_usuario():
    a, b = usuario_pagante("plus"), usuario_pagante("plus")
    seg = date(2026, 10, 5)
    assert db.claim_weekly_report_send(a, seg) and db.claim_weekly_report_send(b, seg)
    db.release_weekly_report_claim(a, seg)
    assert _prefs_semanal(a) is None and _prefs_semanal(b) == seg   # sem `user_id` no WHERE, B também zera


# ── _bancos sem fake: conexões reais semeadas ──

def _banco(uid, item, status="UPDATED", sync="agora", tentativa=None, contas=1):
    from tests import _patrimonio_helpers as h
    sync = h.AGORA if sync == "agora" else sync
    cid = h.conexao(uid, item, status=status, sync=sync, tentativa=tentativa)
    for i in range(contas):
        h.conta(cid, f"acc-{item}-{i}", 100)
    return sync


def test_bancos_reais_cada_estado():
    from datetime import timedelta, timezone
    from tests import _patrimonio_helpers as h
    agora = datetime.now(timezone.utc)
    casos = {}
    casos["sem"] = usuario_pagante("plus")
    casos["atualizado"] = u = usuario_pagante("plus")
    s_atual = _banco(u, f"a{u}")
    casos["velho"] = u = usuario_pagante("plus")
    s_velho = _banco(u, f"v{u}", sync=h.AGORA - timedelta(hours=72))
    casos["nunca"] = u = usuario_pagante("plus")
    _banco(u, f"n{u}", sync=None)
    casos["login"] = u = usuario_pagante("plus")
    s_login = _banco(u, f"l{u}", status="LOGIN_ERROR", sync=h.AGORA - timedelta(hours=1))
    casos["deleted"] = u = usuario_pagante("plus")
    _banco(u, f"d{u}", status="DELETED")
    casos["paused"] = u = usuario_pagante("plus")
    _banco(u, f"p{u}", status="PAUSED")
    casos["misto"] = u = usuario_pagante("plus")
    s_misto = _banco(u, f"m1{u}")
    _banco(u, f"m2{u}", sync=None)
    casos["deleted_e_ok"] = u = usuario_pagante("plus")
    s_ok = _banco(u, f"o1{u}")
    _banco(u, f"o2{u}", status="DELETED")

    casos["tres_contas_nunca"] = u = usuario_pagante("plus")      # 1 conexão com 3 contas = 1 banco
    s_tres = _banco(u, f"t1{u}")
    _banco(u, f"t2{u}", sync=None, contas=3)

    got = {k: wk._bancos(uid, agora) for k, uid in casos.items()}
    assert got["tres_contas_nunca"] == ("desatualizado", s_tres, 1)
    assert got["sem"] == ("sem_banco", None, 0)
    assert got["atualizado"] == ("atualizado", s_atual, 0)
    assert got["velho"] == ("desatualizado", s_velho, 0)
    assert got["nunca"] == ("nunca_sincronizado", None, 1)
    assert got["login"] == ("desatualizado", s_login, 0)           # conexão com erro continua viva, só velha
    assert got["deleted"] == ("sem_banco", None, 0) and got["paused"] == ("sem_banco", None, 0)
    assert got["misto"] == ("desatualizado", s_misto, 1)           # um sincronizou, um nunca
    assert got["deleted_e_ok"] == ("atualizado", s_ok, 0)          # a apagada não conta nem derruba o estado


# ── arredondamento, apresentação e guardas ──

def test_percentual_arredonda_uma_vez_do_valor_cheio(monkeypatch):
    # 10000 -> 11146 é +11,46%: em 1 casa (11,5) e depois inteiro daria 12; do valor cheio dá 11
    _fake(monkeypatch, top=(("Outros", 100.0),),
          janelas={SEMANA: (0.0, 11146.0, 2), ANTERIOR: (0.0, 10000.0, 1)})
    d = wk.build_weekly_report_data(1, closed=True)
    assert d["comparacao"]["despesa"]["pct"] == D("11.5")          # o payload guarda 1 casa
    assert "despesas +11%" in wk.build_weekly_report_text(1, closed=True)

    _fake(monkeypatch, top=(("Outros", 100.0),), janelas={SEMANA: (0.0, 260.0, 2), ANTERIOR: (0.0, 1.0, 1)})
    assert "(38% das despesas)" in wk.build_weekly_report_text(1, closed=True)   # 38,46%, não 38,5 -> 39


def test_zero_negativo_nao_sai_com_sinal():
    from core.reports.formatting import _fmt_sinal
    assert _fmt_sinal(D("-0.00")) == "R$ 0,00" and _fmt_sinal(D("0.00")) == "R$ 0,00"
    assert _fmt_sinal(D("-0.01")) == "-R$ 0,01" and _fmt_sinal(D("5")) == "+R$ 5,00"


@pytest.mark.parametrize("nome,esperado", [
    ("a\nb", "a b"),
    ("a\t\tb\r\n  c", "a b c"),
    ("*_~`bold* 🍕 açaí", "bold 🍕 açaí"),
    ("***", "outros"),
])
def test_maior_categoria_sempre_em_uma_linha(monkeypatch, nome, esperado):
    _fake(monkeypatch, top=((nome, 320.0),))
    m = wk.build_weekly_report_summary(1, closed=True)["maior_categoria"]
    assert m.startswith(f"{esperado}, R$ 320,00") and "\n" not in m


def test_maior_categoria_gigante_e_cortada(monkeypatch):
    _fake(monkeypatch, top=(("x" * 6000, 320.0),))
    m = wk.build_weekly_report_summary(1, closed=True)["maior_categoria"]
    assert m.startswith("x" * 39 + "…, R$ 320,00") and len(m) < 80


def test_sem_despesa_nao_ha_maior_categoria_mesmo_com_categoria_no_banco(monkeypatch):
    _fake(monkeypatch, top=(("Mercado", 5.0),), janelas={SEMANA: (100.0, 0.0, 1), ANTERIOR: (0.0, 0.0, 0)})
    assert wk.build_weekly_report_data(1, closed=True)["maior_categoria"] is None


def test_resultado_da_comparacao_nao_tem_percentual(monkeypatch):
    _fake(monkeypatch)
    c = wk.build_weekly_report_data(1, closed=True)["comparacao"]
    assert "pct" not in c["resultado"] and "pct" in c["despesa"] and "pct" in c["receita"]


def test_segunda_feira_parcial_nao_diz_de_x_a_x(monkeypatch):
    _fake(monkeypatch, now=SEGUNDA, janelas={(date(2026, 10, 5), date(2026, 10, 5)): (0.0, 10.0, 1)})
    assert "📅 Segunda-feira, 05/10/2026 (parcial)" in wk.build_weekly_report_text(1)


# ── Rodada 3 ──

@pytest.mark.parametrize("nome,esperado", [
    ("​", "outros"),                       # só ZWSP
    ("﻿‮", "outros"),                 # BOM + RLO
    ("ab‮c‏d​e", "abcde"),       # RLO, RLM e ZWSP no meio não ficam
    ("a‍b", "ab"),                         # ZWJ sai também (emoji composto quebra: cosmético)
    ("a\x00b\x07c", "abc"),
])
def test_categoria_sem_caracteres_de_controle_e_formato(nome, esperado):
    assert wk._categoria_em_uma_linha(nome) == esperado


@pytest.mark.parametrize("parte,base,esperado", [
    ("0.5", "100", "1"), ("1.5", "100", "2"), ("2.5", "100", "3"), ("-0.5", "100", "-1"),
])
def test_pct_meio_exato_arredonda_para_cima(parte, base, esperado):
    assert wk._pct(D(parte), D(base), 0) == D(esperado)        # HALF_EVEN daria 0, 2, 2, -0
    assert wk._pct(D("0.05"), D("100"), 1) == D("0.1")


def test_gate_da_comparacao_que_levanta_falha_fechado(monkeypatch):
    """Se não dá para saber o plano, o build cai (o job devolve o claim); nunca concede a linha Plus."""
    _fake(monkeypatch, comparar=True)

    def quebra(uid, feature):
        raise RuntimeError("plano indisponível")
    monkeypatch.setattr("core.services.plan_service.plan_gate_ok", quebra)
    with pytest.raises(RuntimeError):
        wk.build_weekly_report_data(1, closed=True)


# ── Rodada 4: mais campos extras que não derrubam o template ──

@pytest.mark.parametrize("extra", ["maior_categoria", "cartoes", "semana_anterior"])
def test_job_campo_extra_que_falha_nao_derruba_o_template(job, extra):
    uid = usuario_pagante("plus")
    _semear(uid)
    db.set_weekly_report_enabled(uid, True)

    def quebra(*a, **kw):
        raise RuntimeError("falha de dado extra")
    if extra == "maior_categoria":
        job.mp.setattr(wk, "get_top_expense_categories", quebra)
    elif extra == "cartoes":
        job.mp.setattr(wk, "list_cards", quebra)
    else:                                   # só a janela da semana anterior; a atual segue essencial
        real = wk.get_summary_by_period
        job.mp.setattr(wk, "get_summary_by_period",
                       lambda u, i, f: quebra() if (i, f) == ANTERIOR else real(u, i, f))

    job.tick(uid)
    assert len(job.envios) == 1 and _prefs_semanal(uid) == date(2026, 10, 5)
    assert job.envios[0][1]["gastos"] == "R$ 420,00" and job.envios[0][1]["lancamentos"] == "5"
    d = wk.build_weekly_report_data(uid, closed=True)
    assert d["despesa"] == D("420.00")
    assert {"maior_categoria": d["maior_categoria"] is None, "cartoes": d["tem_cartao"] is False,
            "semana_anterior": d["comparacao_motivo"] == "indisponivel"}[extra]


def test_job_semana_atual_que_falha_continua_derrubando_o_build(job):
    """A semana ATUAL é o dado essencial: sem ela não há resumo (o tick devolve o claim)."""
    uid = usuario_pagante("plus")
    db.set_weekly_report_enabled(uid, True)

    def quebra(*a, **kw):
        raise RuntimeError("sem totais")
    job.mp.setattr(wk, "get_summary_by_period", quebra)
    job.tick(uid)
    assert job.envios == [] and _prefs_semanal(uid) is None
