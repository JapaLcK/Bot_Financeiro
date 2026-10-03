from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch

from zoneinfo import ZoneInfo

from core.reports.reports_daily import (
    build_weekly_report_text,
    build_monthly_report_text,
    build_weekly_report_summary,
    build_monthly_report_summary,
)

_TZ = ZoneInfo("America/Sao_Paulo")


def _cb(value):
    return {"manual": value, "open_finance_bank": 0, "of_bank_count": 0, "consolidated": value}


def test_build_weekly_report_text():
    with patch("core.reports.reports_daily.get_consolidated_balance", return_value=_cb(1000)), \
         patch("core.reports.reports_daily.get_launches_by_period", return_value=[{"id": 1}, {"id": 2}, {"id": 3}]), \
         patch("core.reports.reports_daily.get_summary_by_period", return_value={"despesa": 150.0, "receita": 20.0}):
        msg = build_weekly_report_text(123)

    assert "📊 *Resumo semanal do Bot Financeiro*" in msg
    assert "🏦 Saldo atual: R$ 1.000,00" in msg
    assert "📉 Gastos da semana: R$ 150,00" in msg
    assert "📈 Receitas da semana: R$ 20,00" in msg
    assert "📊 Lançamentos da semana: 3" in msg


def test_build_monthly_report_text():
    with patch("core.reports.reports_daily.get_consolidated_balance", return_value=_cb(1000)), \
         patch("core.reports.reports_daily.get_launches_by_period", return_value=[{"id": 1}]), \
         patch("db.resumo_mes.totais_do_mes", return_value={"saiu": Decimal("999.90"), "entrou": Decimal("500"),
                                                           "n_cartao": 2}):
        msg = build_monthly_report_text(123)

    assert "📊 *Resumo mensal do Bot Financeiro*" in msg
    assert "🏦 Saldo atual: R$ 1.000,00" in msg
    assert "📉 Gastos do mês: R$ 999,90" in msg
    assert "📈 Receitas do mês: R$ 500,00" in msg
    assert "📊 Lançamentos do mês: 3" in msg  # 1 de `launches` + 2 do cartão


def test_weekly_closed_usa_semana_anterior():
    # segunda-feira 2026-08-03 → semana fechada = 27/07 a 02/08
    fake_now = datetime(2026, 8, 3, 9, 0, tzinfo=_TZ)
    captured = {}

    def _fake_summary(user_id, start, end):
        captured["start"], captured["end"] = start, end
        return {"despesa": 0.0, "receita": 0.0}

    with patch("core.reports.reports_daily.now_tz", return_value=fake_now), \
         patch("core.reports.reports_daily.get_consolidated_balance", return_value=_cb(0)), \
         patch("core.reports.reports_daily.get_launches_by_period", return_value=[]), \
         patch("core.reports.reports_daily.get_summary_by_period", side_effect=_fake_summary):
        s = build_weekly_report_summary(123, closed=True)

    assert captured["start"] == date(2026, 7, 27)
    assert captured["end"] == date(2026, 8, 2)
    assert s["start"] == "27/07/2026" and s["end"] == "02/08/2026"


def test_monthly_closed_usa_mes_anterior():
    # dia 1 (01/08/2026) → mês fechado = 01/07 a 31/07
    fake_now = datetime(2026, 8, 1, 9, 0, tzinfo=_TZ)
    captured = {}

    def _fake_totais(user_id, dia):
        captured["dia"] = dia
        return {"saiu": Decimal(0), "entrou": Decimal(0), "n_cartao": 0}

    with patch("core.reports.reports_daily.now_tz", return_value=fake_now), \
         patch("core.reports.reports_daily.get_consolidated_balance", return_value=_cb(0)), \
         patch("core.reports.reports_daily.get_launches_by_period", return_value=[]), \
         patch("db.resumo_mes.totais_do_mes", side_effect=_fake_totais):
        s = build_monthly_report_summary(123, closed=True)

    # o mês-calendário de julho inteiro (`totais_do_mes` corta pelo dia 1).
    assert captured["dia"] == date(2026, 7, 1)
    assert s["start"] == "01/07/2026" and s["end"] == "31/07/2026"


def test_mensal_total_contagem_e_rotulo_no_mes_inteiro():
    """Banco real. Lançamento com data futura dentro do mês conta (decisão do dono, Q18):
    total, contagem e rótulo na MESMA janela, o mês inteiro. Antes o total já somava o mês
    e a contagem e o "a dd/mm" paravam em hoje. O fechado (abril → março) dá o mesmo."""
    from conftest import usuario_pagante
    from tests._patrimonio_helpers import q

    uid = usuario_pagante()
    for dia, valor in ((5, 10), (25, 30)):  # o 25 é futuro para o relógio do dia 10
        q("""insert into launches (user_id, tipo, valor, criado_em)
             values (%s, 'despesa', %s, %s) returning id""",
          (uid, valor, datetime(2026, 3, dia, 12, tzinfo=_TZ)))
    esperado = ("01/03/2026", "31/03/2026", "R$ 40,00", "2")
    for agora, closed in ((datetime(2026, 3, 10, 9, tzinfo=_TZ), False),
                          (datetime(2026, 4, 1, 9, tzinfo=_TZ), True)):
        with patch("core.reports.reports_daily.now_tz", return_value=agora):
            s = build_monthly_report_summary(uid, closed=closed)
        assert (s["start"], s["end"], s["gastos"], s["lancamentos"]) == esperado, (closed, s)


def test_mensal_conta_as_compras_no_cartao_do_total():
    """Banco real, relógio congelado. A contagem é a de `launches` (todas, como antes) mais
    as compras no cartão que entram no Gastos (`TOTAIS_SQL`: sem estorno, fatura que fecha no
    mês). Antes só com cartão saía "Gastos R$ 230,00 / Lançamentos 0". Controle NEGATIVO
    medido: a contagem antiga (`len(launches)`) deixa vermelho. Março aberto e fechado."""
    import db
    from conftest import usuario_pagante
    from tests._patrimonio_helpers import q

    def lanc(uid, valor, interno=False):
        q("""insert into launches (user_id, tipo, valor, criado_em, is_internal_movement)
             values (%s, 'despesa', %s, %s, %s) returning id""",
          (uid, valor, datetime(2026, 3, 5, 12, tzinfo=_TZ), interno))

    def cartao(uid):
        c = db.create_card(uid, "Nubank", closing_day=10, due_day=17)
        db.add_credit_purchase(uid, c, 230, "mercado", "vista", date(2026, 3, 5))
        db.add_credit_purchase(uid, c, 999, "fora", "fatura de abril", date(2026, 3, 15))
        db.add_credit_refund(uid, c, 25, "mercado", "estorno", date(2026, 3, 6))

    so_cartao, misto, so_launches, vazio = (usuario_pagante() for _ in range(4))
    cartao(so_cartao)
    cartao(misto)
    lanc(misto, 10)
    lanc(so_launches, 10)
    lanc(so_launches, 500, interno=True)  # positivo: a contagem de `launches` não muda
    esperado = {so_cartao: ("R$ 230,00", "1"), misto: ("R$ 240,00", "2"),
                so_launches: ("R$ 10,00", "2"), vazio: ("R$ 0,00", "0")}
    for agora, closed in ((datetime(2026, 3, 10, 9, tzinfo=_TZ), False),
                          (datetime(2026, 4, 1, 9, tzinfo=_TZ), True)):
        for uid, (gastos, n) in esperado.items():
            with patch("core.reports.reports_daily.now_tz", return_value=agora):
                s = build_monthly_report_summary(uid, closed=closed)
            assert (s["gastos"], s["lancamentos"]) == (gastos, n), (closed, uid, s)


def test_classifier_routes_weekly_and_monthly():
    from core.intent_classifier import _try_exact, _try_alias, _normalize

    for text in ("resumo semanal", "resumo da semana", "quero o relatorio semanal"):
        norm = _normalize(text)
        res = _try_exact(norm) or _try_alias(norm, text)
        assert res is not None and res.intent == "report.weekly", text

    for text in ("resumo mensal", "resumo do mes", "quero o relatorio mensal"):
        norm = _normalize(text)
        res = _try_exact(norm) or _try_alias(norm, text)
        assert res is not None and res.intent == "report.monthly", text
