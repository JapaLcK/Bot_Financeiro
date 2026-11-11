"""PL-06 B6, PR 2, §19 (achados 2 e 3 do Tester r1): detector em falha e o corte de 6 depois do dedupe.

Achado 2: detector em falha é falha da geração (`generate_ai_insights(estrito=True)`): o dia é devolvido, nada é marcado
como enviado, a cobertura por outro canal fica, e a hora das 10h tenta de novo. Achado 3: o dedupe vale para a lista
inteira (`sem_corte=True`) e só então o e-mail leva até `LIMITE` novos; os de fora não são marcados e saem depois. A
pendência reenvia as chaves guardadas mesmo que fatos posteriores às 10h ocupem o topo do feed (§18).
"""
from __future__ import annotations

from datetime import timedelta

import pytest

import db.insights as dbi
from _insights_envio_apoio import avisos, envio, gasto, prefs  # noqa: F401 — `envio` é fixture
from core import ai_patterns
from core.services import insights_detectores as detectores
from core.services.insights_contrato import LIMITE
from db.bills import create_boleto
from test_insights_contrato import llm  # noqa: F401 — fixture
from test_insights_envio_cobertura import _numero, lembrete  # noqa: F401 — `lembrete` é fixture
from utils_date import now_tz


# ── Achado 2. Detector em falha não consome o dia ────────────────────────────────────────────────────────────

def _quebra(monkeypatch, detector):
    alvo = dbi if detector.startswith("_") else detectores
    real = getattr(alvo, detector)

    def quebra(u):
        raise RuntimeError("timeout")

    monkeypatch.setattr(alvo, detector, quebra)
    return lambda: monkeypatch.setattr(alvo, detector, real)


@pytest.mark.parametrize("detector", ["_detect_budget_warnings", "detect_bill_due"])
def test_a2_detector_cai_as_10h05_e_volta_as_10h20_envia_completo(envio, monkeypatch, detector):
    uid = envio.usuario()
    gasto(uid)
    create_boleto(uid, "Luz", 50, now_tz().date())  # D-0: critical, "vence hoje"
    volta = _quebra(monkeypatch, detector)
    assert envio.tique(10, 5)["falhas"] == 1
    assert envio.de(uid) == [] and prefs(uid)["d"] is None and avisos(uid) == {}, "o dia segue livre, nada marcado"
    volta()
    envio.tique(10, 20)
    (m,) = envio.de(uid)
    assert "Luz vence hoje" in m["text"] and "apertando" in m["text"]


def test_a2_detector_caido_a_hora_inteira_nao_envia_e_a_cobertura_fica(envio, lembrete, monkeypatch):
    uid = envio.usuario()
    gasto(uid)
    _numero(uid)
    hoje = now_tz().date()
    luz = create_boleto(uid, "Luz", 50, hoje + timedelta(days=3))["id"]
    assert lembrete(uid, hoje)
    _quebra(monkeypatch, "_detect_budget_warnings")
    for h, m in ((10, 5), (10, 30), (10, 59), (11, 5), (15, 5)):
        envio.tique(h, m)
    assert envio.de(uid) == [] and prefs(uid)["d"] is None
    assert avisos(uid) == {f"bill_due:{luz}:{hoje + timedelta(days=3)}": "warning"}, \
        "só a cobertura do WhatsApp é marcada; o orçamento não foi enviado"


# ── Achado 3. Dedupe antes do corte de 6 ──────────────────────────────────────────────────────────────────────

def _vencidas(uid, n=LIMITE):
    hoje = now_tz().date()
    for d in range(1, n + 1):
        create_boleto(uid, f"Velha {d}", 10, hoje - timedelta(days=d))


def test_a3_seis_ja_avisados_nao_seguram_o_novo(envio):
    uid = envio.usuario()
    _vencidas(uid)
    envio.tique()
    assert len(envio.de(uid)) == 1
    gasto(uid)  # warning novo, abaixo dos 6 critical no feed
    assert all(i["type"] == "bill_due" for i in ai_patterns.generate_ai_insights(uid, force=True)), "fora do top 6"
    envio.tique(dias=1)
    assert len(envio.de(uid)) == 2 and "apertando" in envio.de(uid)[1]["text"]


def test_a3_pendencia_nao_sai_do_corte_por_fato_posterior_as_10h(envio):
    uid = envio.usuario()
    gasto(uid)
    envio.recusar.add(envio.de_email(uid))
    envio.tique()
    envio.recusar.clear()
    _vencidas(uid)  # depois do corte: 6 critical tomam o topo do feed
    envio.tique(11, 5)
    (m,) = envio.de(uid)
    assert "apertando" in m["text"] and "Velha" not in m["text"]


def _contas_no(m):
    return {l.split(" (")[0] for l in m["text"].splitlines() if l.startswith(("Velha ", "Hoje "))}


def test_a3_oito_novos_vao_seis_e_os_dois_de_fora_saem_amanha(envio):
    uid = envio.usuario()
    _vencidas(uid, 7)
    create_boleto(uid, "Hoje", 10, now_tz().date())  # o 8º critical
    envio.tique()
    (m,) = envio.de(uid)
    assert len(_contas_no(m)) == LIMITE and len(avisos(uid)) == LIMITE, "6 no e-mail, só os 6 marcados"
    envio.tique(dias=1)
    primeiro, segundo = (_contas_no(e) for e in envio.de(uid))
    assert len(segundo) == 2 and not primeiro & segundo and len(avisos(uid)) == 8


def test_a3_corte_respeita_a_prioridade(envio):
    """6 critical e 2 warning novos: os 6 do e-mail são os critical; os warning saem no dia seguinte."""
    uid = envio.usuario()
    _vencidas(uid)
    create_boleto(uid, "Breve", 10, now_tz().date() + timedelta(days=2))  # warning
    gasto(uid)  # warning
    envio.tique()
    (m,) = envio.de(uid)
    assert "Breve" not in m["text"] and "apertando" not in m["text"]
    assert set(avisos(uid).values()) == {"critical"} and len(avisos(uid)) == LIMITE
    envio.tique(dias=1)
    assert "Breve" in envio.de(uid)[1]["text"] and "apertando" in envio.de(uid)[1]["text"]


def test_a3_aviso_fora_dos_6_do_painel_vai_com_titulo_e_mensagem_completos(envio):
    """O e-mail se basta: o aviso que não aparece no painel (fora do top 6) chega com o título e a mensagem do feed."""
    import html
    import re
    uid = envio.usuario()
    _vencidas(uid)
    envio.tique()
    gasto(uid)
    (novo,) = [i for i in ai_patterns.generate_ai_insights(uid, force=True, sem_corte=True)
               if i["type"] == "budget_warning"]
    assert novo["key"] not in {i["key"] for i in ai_patterns.generate_ai_insights(uid, force=True)}, "fora do painel"
    envio.tique(dias=1)
    m = envio.de(uid)[1]
    assert re.findall(r"<p><strong>(.*?)</strong><br/>(.*?)</p>", m["html"]) == [
        (html.escape(novo["title"]), html.escape(novo["message"]))]
    assert f"{novo['title']}\n{novo['message']}" in m["text"]


def test_a3_feed_rota_e_card_seguem_com_o_corte_de_6(monkeypatch, llm):
    """O `sem_corte` é só do envio: a rota, a leitura do cache e o card do chat seguem cortando em `LIMITE`."""
    from _cards_apoio import _http
    from _ia_stream_helpers import executar, openai_falso, rodada
    from test_insights_contrato import _usuario
    from test_insights_frescor import _card
    uid = _usuario("pro")
    _vencidas(uid, 7)
    create_boleto(uid, "Hoje", 10, now_tz().date())
    assert len(ai_patterns.generate_ai_insights(uid, force=True, sem_corte=True)) == 8
    r = _http(uid).get(f"/insights/{uid}/current")
    assert r.status_code == 200 and len(r.json()["insights"]) == LIMITE
    assert len(ai_patterns.ler_insights(uid)) == LIMITE
    openai_falso(monkeypatch, rodada(None, ("show_card", {"card": "piggy"})), rodada("Olha o que eu notei."))
    _, ev, _ = executar(uid, "o que você notou?")
    assert len(_card(ev)) == LIMITE
