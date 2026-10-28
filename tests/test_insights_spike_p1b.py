"""PL-06 B1: o `category_spike` na regra P1-B (decisão Q1 = a do dono, 2026-10-28).

O mês corrente INTEIRO (o que já está lançado, fatura que fecha depois de hoje inclusive) contra o mês anterior
inteiro, os dois >= R$ 50, warning a partir de +30% e critical a partir de +60%, sem o portão de 40% do mês,
"sem categoria" fora, corte pelo histórico do plano. Postgres real; relógio em 08/10/2026 (`db.insights.now_tz`),
menos na conversa (caso 11), que roda no relógio real como o runner.

Controles negativos medidos à mão no PR (mutação no detector, um caso vermelho cada): portão de 40% → caso 1;
média dos 3 meses → caso 2; janela atual cortada em hoje → caso 4; sem piso do anterior → caso 5; sem a exclusão
de "sem categoria" → caso 6; sujeito pelo rótulo sanitizado → caso 8; sem corte de histórico → caso 9; emoji sem
`user_id` ou sem o `lower()` → caso 12; anterior sem o último dia → caso 13; `hoje` pelo relógio do sistema → caso 14;
anterior casado pelo rótulo e não pela `chave` → caso 15; sujeito sem `sujeito()` (nome com e-mail em claro) → caso 16.
Positivos (o caminho legítimo continua alertando): a 2ª metade do caso 5 (anterior = 50 alerta) e o Plus do caso 9.
O caso 3 é a regra nova não alertando onde a média alertaria (fica vermelho com a média dos 3 meses).
"""
from __future__ import annotations

from datetime import date, datetime

import pytest

import db
from _ia_stream_helpers import executar, openai_falso, rodada
from conftest import usuario_pagante
from core import ai_patterns
from core.services import comparacao_periodos as cp
from core.services.plan_service import plan_gate_ok
from db import insights
from test_insights_contrato import _usuario, llm  # noqa: F401
from tests._patrimonio_helpers import q
from tests.test_tipo_legado_na_tendencia import _mes_passado
from utils_date import _tz

AGORA = datetime(2026, 10, 8, 10, tzinfo=_tz())


def _relogio(monkeypatch, agora=AGORA):
    monkeypatch.setattr(insights, "now_tz", lambda: agora)


def _gasto(uid, valor, mes, dia=5, cat="mercado", ano=2026):
    q("insert into launches (user_id, tipo, valor, categoria, criado_em) values (%s, 'despesa', %s, %s, %s)",
      (uid, valor, cat, datetime(ano, mes, dia, 12, tzinfo=_tz())))


def _spikes(uid):
    return insights._detect_category_spike(uid)


def test_1_alta_de_30_no_dia_8_contra_o_mes_anterior_inteiro(monkeypatch):
    _relogio(monkeypatch)
    uid = usuario_pagante("pro")
    _gasto(uid, 400, 9)
    _gasto(uid, 520, 10)
    (it,) = _spikes(uid)
    e = it["explicacao"]
    assert (it["key"], it["severity"], it["title"]) == ("category_spike:mercado:2026-10", "warning",
                                                        "Mercado subiu 30% no mês")
    assert it["message"] == "R$ 520,00 até agora contra R$ 400,00 em setembro de 2026 inteiro. Vale conferir."
    assert (e["referencia"]["estatistica"], e["referencia"]["valor"]) == ("mes_anterior_inteiro", 400.0)
    assert e["referencia"]["periodo"]["inicio"] == "2026-09-01" and e["referencia"]["periodo"]["parcial"] is False
    assert (e["atual"], e["diferenca"], e["amostra"]) == ({"valor": 520.0}, {"valor": 120.0, "percentual": 30.0}, None)


def test_2_a_base_e_o_mes_anterior_e_nao_a_media(monkeypatch):
    _relogio(monkeypatch)
    uid = usuario_pagante("pro")
    for mes, v in ((7, 1000), (8, 1000), (9, 100), (10, 140)):
        _gasto(uid, v, mes)
    assert [i["title"] for i in _spikes(uid)] == ["Mercado subiu 40% no mês"]


def test_3_anterior_alto_nao_alerta_mesmo_com_media_baixa(monkeypatch):
    _relogio(monkeypatch, AGORA.replace(day=20))
    uid = usuario_pagante("pro")
    for mes, v in ((7, 100), (8, 100), (9, 600), (10, 400)):
        _gasto(uid, v, mes)
    assert _spikes(uid) == []


def test_4_fatura_que_fecha_depois_de_hoje_conta_no_mes(monkeypatch):
    _relogio(monkeypatch)
    uid = usuario_pagante("pro")
    _gasto(uid, 300, 9)
    _gasto(uid, 200, 10, dia=3)
    cartao = db.create_card(uid, "Nubank", closing_day=20, due_day=27)
    db.add_credit_purchase(uid, cartao, 200, "mercado", "fecha dia 20", date(2026, 10, 5))
    assert q("select period_end from credit_bills where user_id = %s", (uid,))["period_end"] == date(2026, 10, 20)
    assert [i["title"] for i in _spikes(uid)] == ["Mercado subiu 33% no mês"]


def test_5_piso_de_50_no_anterior(monkeypatch):
    _relogio(monkeypatch)
    uid = usuario_pagante("pro")
    _gasto(uid, 12, 9, cat="farmácia")
    _gasto(uid, 80, 10, cat="farmácia")
    assert _spikes(uid) == []
    _gasto(uid, 38, 9, dia=20, cat="farmácia")  # setembro vai a 50: 80 × 50 = +60%
    assert [(i["title"], i["severity"]) for i in _spikes(uid)] == [("Farmácia subiu 60% no mês", "critical")]


def test_6_sem_categoria_fica_fora(monkeypatch):
    _relogio(monkeypatch)
    uid = usuario_pagante("pro")
    _gasto(uid, 100, 9, cat=None)
    _gasto(uid, 150, 10, cat=None)
    _gasto(uid, 150, 10, cat="")
    assert _spikes(uid) == []


def test_7_todas_as_categorias_sem_o_corte_em_10(monkeypatch):
    _relogio(monkeypatch)
    uid = usuario_pagante("pro")
    for n in range(12):
        _gasto(uid, 1000, 9, cat=f"caiu{n}")
        _gasto(uid, 60, 10, cat=f"caiu{n}")
    _gasto(uid, 100, 9, cat="lazer")
    _gasto(uid, 200, 10, cat="lazer")
    # a cena discrimina: o corte em 10 por |delta| da `comparar_mes` esconderia o lazer
    assert "lazer" not in [c["categoria"] for c in cp.comparar_mes(uid, 2026, 10, AGORA)["categorias"]]
    assert [i["key"] for i in _spikes(uid)] == ["category_spike:lazer:2026-10"]


def test_8_dispensa_feita_na_chave_da_main_continua_valendo(monkeypatch, llm):  # noqa: F811
    _relogio(monkeypatch)
    uid = _usuario("pro")  # Plus
    chave = "category_spike:mercado *bom*:2026-10"  # a da main: `cat_key` do nome cru, com o `*`
    q("insert into insight_estado (user_id, chave, tipo, severidade, dispensado_em, dispensado_severidade)"
      " values (%s, %s, 'category_spike', 'warning', now(), 'warning')", (uid, chave))
    _gasto(uid, 400, 9, cat="Mercado *bom*")
    _gasto(uid, 520, 10, cat="Mercado *bom*")
    assert [i["key"] for i in _spikes(uid)] == [chave]
    assert ai_patterns.generate_ai_insights(uid, force=True) == []
    _gasto(uid, 120, 10, dia=6, cat="Mercado *bom*")  # 640 × 400 = +60%: piorou, volta (D4)
    assert [(i["key"], i["severity"]) for i in ai_patterns.generate_ai_insights(uid, force=True)] == [
        (chave, "critical")]


def test_9_gratis_no_legado_nao_ve_e_o_plus_ve(monkeypatch, user_id, llm):  # noqa: F811
    _relogio(monkeypatch)
    plus = _usuario("pro")
    for uid in (user_id, plus):
        _gasto(uid, 400, 9)
        _gasto(uid, 520, 10)
    assert [i["type"] for i in ai_patterns.generate_ai_insights(plus, force=True)] == ["category_spike"]
    monkeypatch.setenv("PLANS_V2_ENABLED", "0")
    assert plan_gate_ok(user_id, "insights")  # o Grátis do legado tem o feed: quem o corta é o histórico (só o mês corrente)
    assert _spikes(user_id) == []


def test_10_isolamento_e_compra_de_a_na_fatura_de_b(monkeypatch):
    _relogio(monkeypatch)
    a, b = usuario_pagante("pro"), usuario_pagante("pro")
    for uid, outubro in ((a, 400), (b, 500)):
        _gasto(uid, 400, 9)
        _gasto(uid, outubro, 10)
    cartao_a = db.create_card(a, "Nubank", closing_day=20, due_day=27)
    cartao_b = db.create_card(b, "Nubank", closing_day=20, due_day=27)
    _, _, fatura_b = db.add_credit_purchase(b, cartao_b, 20, "mercado", "de B", date(2026, 10, 5))
    q("insert into credit_transactions (bill_id, user_id, card_id, valor, categoria, purchased_at)"
      " values (%s, %s, %s, 4321, 'mercado', %s)", (fatura_b, a, cartao_a, date(2026, 10, 5)))
    assert _spikes(a) == []
    assert [(i["title"], i["explicacao"]["atual"]["valor"]) for i in _spikes(b)] == [("Mercado subiu 30% no mês", 520.0)]


def test_12_icone_e_o_emoji_da_categoria_do_proprio_usuario(monkeypatch):
    _relogio(monkeypatch)
    a, b = usuario_pagante("pro"), usuario_pagante("pro")
    for uid in (a, b):
        for mes, v in ((8, 400), (9, 400), (10, 520)):
            _gasto(uid, v, mes, cat="Mercado")  # rótulo com maiúscula; o catálogo guarda minúsculo
    q("insert into user_categories (user_id, name, emoji) values (%s, 'mercado', '🍕')", (b,))
    assert [i["icon"] for i in _spikes(a)] == ["📈"]
    assert [i["icon"] for i in _spikes(b)] == ["🍕"]


def test_13_o_ultimo_dia_do_mes_anterior_conta(monkeypatch):
    _relogio(monkeypatch, datetime(2026, 11, 1, 0, 30, tzinfo=_tz()))
    uid = usuario_pagante("pro")
    q("insert into launches (user_id, tipo, valor, categoria, criado_em) values (%s, 'despesa', 100, 'mercado', %s)",
      (uid, datetime(2026, 10, 31, 23, 50, tzinfo=_tz())))
    _gasto(uid, 140, 11, dia=1)
    assert [i["title"] for i in _spikes(uid)] == ["Mercado subiu 40% no mês"]


def test_14_janela_e_chave_saem_do_relogio_do_app(monkeypatch):
    _relogio(monkeypatch, datetime(2025, 3, 10, 10, tzinfo=_tz()))  # outro mês e outro ano que o relógio real
    uid = usuario_pagante("pro")
    _gasto(uid, 400, 2, ano=2025)
    _gasto(uid, 520, 3, ano=2025)
    (it,) = _spikes(uid)
    e = it["explicacao"]
    assert (it["key"], e["periodo"]["inicio"], e["referencia"]["periodo"]["inicio"]) == (
        "category_spike:mercado:2025-03", "2025-03-01", "2025-02-01")
    assert it["message"] == "R$ 520,00 até agora contra R$ 400,00 em fevereiro de 2025 inteiro. Vale conferir."


def test_15_o_anterior_casa_pela_chave_e_nao_pela_grafia(monkeypatch):
    _relogio(monkeypatch)
    uid = usuario_pagante("pro")
    _gasto(uid, 400, 9, cat="Farmácia")
    _gasto(uid, 520, 10, cat="farmacia")
    assert [i["key"] for i in _spikes(uid)] == ["category_spike:farmacia:2026-10"]


def test_16_categoria_com_email_vira_hash_na_chave(monkeypatch):
    _relogio(monkeypatch)
    uid = usuario_pagante("pro")
    _gasto(uid, 400, 9, cat="joao@gmail.com")
    _gasto(uid, 520, 10, cat="joao@gmail.com")
    (it,) = _spikes(uid)
    assert it["key"].startswith("category_spike:") and it["key"].endswith(":2026-10")
    assert "joao" not in it["key"] and "gmail" not in it["key"], it["key"]


def test_11_conversa_card_piggy_e_depois_um_gasto(monkeypatch, llm):  # noqa: F811
    uid = _usuario("pro")
    hoje = date.today()
    ant = _mes_passado(5)
    _gasto(uid, 400, ant.month, ano=ant.year)
    _gasto(uid, 520, hoje.month, dia=hoje.day, ano=hoje.year)
    ai_patterns.generate_ai_insights(uid)  # aquece o cache que o card lê
    openai_falso(monkeypatch, rodada(None, ("show_card", {"card": "piggy"})), rodada("Olha o que eu notei."))
    r, ev, _ = executar(uid, "o que você notou?")
    assert r.reply == "Olha o que eu notei."
    (bloco,) = [e["bloco"] for e in ev if e["tipo"] == "bloco"]
    (it,) = bloco["abas"][0]["recursos"]["insights"]["insights"]
    rot = f"{cp.MESES[_mes_passado(1).month - 1]} de {_mes_passado(1).year}"
    assert it["message"] == f"R$ 520,00 até agora contra R$ 400,00 em {rot} inteiro. Vale conferir."
    openai_falso(monkeypatch, rodada(None, ("add_launch", {"tipo": "despesa", "valor": 50, "alvo": "mercado",
                                                           "categoria": "mercado"})))
    r2, ev2, _ = executar(uid, "gastei 50 no mercado")
    assert "Despesa registrada" in r2.reply and not [e for e in ev2 if e["tipo"] == "bloco"]
    (depois,) = ai_patterns.generate_ai_insights(uid, force=True)  # o lançamento do 2º turno entrou no mês
    assert (depois["key"], depois["explicacao"]["atual"]["valor"]) == (it["key"], 570.0)
