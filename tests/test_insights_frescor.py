"""PL-06 D13: depois de uma escrita financeira, os insights recalculam (em vez de servir até 6 h de cache velho).

O ouvinte do `pb_escrita` (`api/v2/eventos.escutar_banco`) grava o marcador `insights_mudou` do dono antes de avisar o
SSE (`eventos._Marcador` → `ai_patterns.invalidar_insights`); a leitura só aceita cache cuja geração começou depois dele. Postgres real e
ouvinte real (`ouvindo`/`barreira`); a escrita roda numa thread, como o WhatsApp.

Controles negativos (mutação medida no PR, numa cópia da árvore): `invalidar_insights` no-op → T1, T2, T6, T8; gravar
o cache com `now()` do fim → T3; card `piggy` de volta ao `ler_insights` → T4; a geração fazendo um UPDATE em
`launches` → T5. Positivos: cache reaproveitado sem escrita e com escrita do vizinho (T2); o caminho com o banco bom
marca (T7). O ouvinte com o marcador emperrado: `test_insights_frescor_ouvinte.py`.
"""
import asyncio
import os
import time
from datetime import date, timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import sessao_de
from api.v2 import eventos
from _cards_apoio import _http
from _ia_stream_helpers import executar, openai_falso, rodada
from conftest import promote_to_pro, usuario_pagante
from core import ai_patterns
from db.bills import create_boleto
from db.pockets import update_pocket_meta
from db.recurring import create_recurring_expense
from db.recurring_income import create_recurring_income
from test_api_app_sessao import completo
from test_insights_contrato import _orcamento_apertando, _usuario, llm  # noqa: F401
from test_insights_estado import MERCADO, _linhas, _put
from tests._fusao_of_helpers import ia_fora, manda  # noqa: F401 (fixture)
from tests.test_api_v2_eventos_escrita import _sql, barreira, ouvindo


def _marcador(uid):
    r = _sql("select generated_at from ai_proactive_cache where user_id = %s and kind = 'insights_mudou'", uid)
    return r[0]["generated_at"] if r else None


def _feed(uid):
    r = _http(uid).get(f"/insights/{uid}/current")
    assert r.status_code == 200, r.text
    return [(i["type"], i["severity"]) for i in r.json()["insights"]]


def _titulos_v2(uid):
    c = TestClient(dashboard.app)
    c.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, sessao_de(uid)["dashboard"])
    r = c.get("/api/v2/insights")
    assert r.status_code == 200, r.text
    return [i["title"] for i in r.json()["insights"]]


@pytest.fixture
def geracoes(monkeypatch):
    """Os uids de cada `compute_active_insights` (a geração de verdade, sem o cache)."""
    real, quem = db.compute_active_insights, []
    monkeypatch.setattr("db.compute_active_insights", lambda uid: quem.append(uid) or real(uid))
    return quem


# ── T1. A conversa: gasto pelo WhatsApp, e o próximo pedido (sem force) já mostra ─────────────────────────────

@pytest.mark.parametrize("texto, primeiro", [
    ("gastei 20 no mercado", "legado"),
    ("gastei 18,90 no açaí do mercado e 15 na padaria", "v2"),
    ("paguei 30 no mercado", "legado"),
    ("Gastei R$ 20,50 no Mercado", "v2"),
    ("comprei 20 de pão no supermercado", "legado"),
])
def test_t1_gasto_na_conversa_e_o_proximo_pedido_ja_recalcula(ia_fora, llm, monkeypatch, texto, primeiro):
    uid = _usuario("pro")
    monkeypatch.setenv("DASHBOARD_V2_BETA_EMAILS", "")
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", str(uid))
    manda(uid, "gastei 85 no mercado")
    (cat,) = {r["categoria"] for r in _sql("select categoria from launches where user_id = %s", uid)}
    db.upsert_budget(uid, cat, 100)  # 85 %: warning
    assert _feed(uid) == [("budget_warning", "warning")]
    assert _titulos_v2(uid) == [f"{cat.capitalize()} tá apertando"]  # do cache

    async def cena():
        async with ouvindo(monkeypatch) as avisados:
            await asyncio.to_thread(manda, uid, texto)
            await barreira(avisados)
            if primeiro == "v2":  # o 1º pedido depois da escrita é o que discrimina
                return await asyncio.to_thread(_titulos_v2, uid), await asyncio.to_thread(_feed, uid)
            feed = await asyncio.to_thread(_feed, uid)
            return await asyncio.to_thread(_titulos_v2, uid), feed

    titulos, feed = asyncio.run(cena())
    assert not ia_fora, f"a mensagem caiu na IA: {ia_fora}"
    assert feed == [("budget_warning", "critical")]
    assert titulos == [f"{cat.capitalize()} estourou o orçamento"]
    assert llm == []


# ── T2. Positivo e isolamento: sem escrita (ou com escrita do vizinho) o cache vale ───────────────────────────

def test_t2_cache_reaproveitado_e_escrita_do_vizinho_nao_invalida(monkeypatch, geracoes):
    a, b = usuario_pagante("pro"), usuario_pagante("pro")

    async def cena():
        async with ouvindo(monkeypatch) as avisados:
            await asyncio.to_thread(ai_patterns.generate_ai_insights, a)
            await asyncio.to_thread(ai_patterns.generate_ai_insights, a)
            sem_escrita = geracoes.count(a)
            await asyncio.to_thread(db.add_launch_and_update_balance, b, "despesa", 10, "Mercado", None)
            await barreira(avisados)
            await asyncio.to_thread(ai_patterns.generate_ai_insights, a)
            vizinho = geracoes.count(a), _marcador(a), _marcador(b)
            await asyncio.to_thread(db.add_launch_and_update_balance, a, "despesa", 10, "Mercado", None)
            await barreira(avisados)
            await asyncio.to_thread(ai_patterns.generate_ai_insights, a)
            return sem_escrita, vizinho, geracoes.count(a), _marcador(a)

    sem_escrita, (depois_de_b, marca_a, marca_b), depois_de_a, marca_a_final = asyncio.run(cena())
    assert sem_escrita == 1  # positivo: a 2ª leitura veio do cache
    assert marca_b is not None  # o ouvinte marcou B: a medida abaixo não é vazia
    assert (depois_de_b, marca_a) == (1, None)  # a escrita de B não toca no cache nem no marcador de A
    assert (depois_de_a, marca_a_final is not None) == (2, True)


# ── T3. Geração em voo quando a escrita comita: o cache dela nasce velho ──────────────────────────────────────

def test_t3_geracao_iniciada_antes_da_marca_e_descartada(monkeypatch):
    uid = usuario_pagante("pro")
    _orcamento_apertando(uid)  # 90 %: warning
    real, chamadas = db.compute_active_insights, []

    def intercala(u):
        r = real(u)  # o que a geração viu: ainda 90 %
        chamadas.append(u)
        if len(chamadas) == 1:
            db.add_launch_and_update_balance(uid, "despesa", 20, "Mercado X", None, categoria="mercado")  # 110 %
            fim = time.monotonic() + 10
            while _marcador(uid) is None:  # o ouvinte marca ANTES de a geração gravar o cache
                assert time.monotonic() < fim, "o marcador não chegou"
                time.sleep(0.01)
        return r

    monkeypatch.setattr("db.compute_active_insights", intercala)

    async def cena():
        async with ouvindo(monkeypatch) as avisados:
            em_voo = await asyncio.to_thread(ai_patterns.generate_ai_insights, uid)
            await barreira(avisados)
            return em_voo, await asyncio.to_thread(ai_patterns.generate_ai_insights, uid)

    em_voo, depois = asyncio.run(cena())
    assert [i["severity"] for i in em_voo] == ["warning"]  # a geração em voo não viu o gasto
    assert [i["severity"] for i in depois] == ["critical"]
    assert len(chamadas) == 2


# ── T4. Card `piggy` no chat: gera na hora, inclusive na mesma rodada do gasto ────────────────────────────────

def _card(ev):
    (bloco,) = [e["bloco"] for e in ev if e["tipo"] == "bloco"]
    return [(i["type"], i["severity"]) for i in bloco["abas"][0]["recursos"]["insights"]["insights"]]


def test_t4_card_piggy_mostra_o_gasto_do_turno_anterior(monkeypatch, llm):
    uid = _usuario("pro")
    _orcamento_apertando(uid)  # 90 %
    openai_falso(monkeypatch, rodada(None, ("show_card", {"card": "piggy"})), rodada("Olha o que eu notei."))
    _, ev, _ = executar(uid, "o que você notou?")
    assert _card(ev) == [("budget_warning", "warning")]
    openai_falso(monkeypatch, rodada(None, ("add_launch", {"tipo": "despesa", "valor": 20, "alvo": "mercado",
                                                           "categoria": "mercado"})))
    r, _, _ = executar(uid, "gastei 20 no mercado")
    assert "Despesa registrada" in r.reply
    openai_falso(monkeypatch, rodada(None, ("show_card", {"card": "piggy"})), rodada("Olha o que eu notei."))
    _, ev, _ = executar(uid, "e agora?")
    assert _card(ev) == [("budget_warning", "critical")]
    assert llm == []


def test_t4b_card_piggy_na_mesma_rodada_do_gasto(monkeypatch, llm):
    uid = _usuario("pro")
    _orcamento_apertando(uid)
    ai_patterns.generate_ai_insights(uid)  # cache quente com o warning: o card não pode servi-lo
    openai_falso(monkeypatch, rodada(None, ("add_launch", {"tipo": "despesa", "valor": 20, "alvo": "mercado",
                                                           "categoria": "mercado"}), ("show_card", {"card": "piggy"})))
    r, ev, _ = executar(uid, "gastei 20 no mercado, como estão minhas dicas?")
    assert "Despesa registrada" in r.reply
    assert _card(ev) == [("budget_warning", "critical")]
    assert llm == []


# ── T5. Gerar não avisa ninguém (a guarda do laço aviso → refetch → geração → aviso) ──────────────────────────

def test_t5_gerar_e_marcar_nao_avisam(monkeypatch):
    uid = _usuario("pro_max")
    hoje = date.today()
    db.add_launch_and_update_balance(uid, "receita", 1000, "setup", "Saldo inicial", "saldo_inicial",
                                     is_internal_movement=True)  # saldo informado
    _orcamento_apertando(uid)
    create_boleto(uid, "Luz", 180, hoje + timedelta(days=1))
    create_recurring_expense(uid, "Aluguel", 1000, "moradia", 25, "account")
    create_recurring_income(uid, "Salário", 2000, "salario", 25)
    _, pid, _ = db.create_pocket(uid, "Viagem")
    update_pocket_meta(uid, pid, target_amount=10000, target_date=f"{hoje.year + 1}-01-01")

    async def cena():
        async with ouvindo(monkeypatch) as avisados:
            itens = await asyncio.to_thread(ai_patterns.generate_ai_insights, uid, force=True)
            await barreira(avisados)
            gerar = avisados.count(uid)
            async with await psycopg.AsyncConnection.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
                await ai_patterns.invalidar_insights(c, [uid])
            await barreira(avisados)
            marcar = avisados.count(uid) - gerar
            await asyncio.to_thread(db.add_launch_and_update_balance, uid, "despesa", 1, "x", None)
            await barreira(avisados)
            return itens, gerar, marcar, avisados.count(uid) - gerar - marcar

    itens, gerar, marcar, escrita = asyncio.run(cena())
    assert {"budget_warning", "bill_due", "goal_behind"} <= {i["type"] for i in itens}, itens  # geração com dado
    assert (gerar, marcar) == (0, 0)
    assert escrita >= 1  # positivo: o ouvinte vê este usuário


# ── T6. A dispensa sobrevive à invalidação; piorar ainda traz de volta (D4), agora sem esperar 6 h ────────────

def test_t6_dispensa_sobrevive_e_piora_reaparece(monkeypatch, llm):
    uid = _usuario("pro")
    _orcamento_apertando(uid)  # 90 %
    assert [i["key"] for i in _http(uid).get(f"/insights/{uid}/current").json()["insights"]] == [MERCADO]
    assert _put(uid, chave=MERCADO, dispensado=True, severidade="warning").status_code == 200

    async def cena():
        async with ouvindo(monkeypatch) as avisados:
            await asyncio.to_thread(db.add_launch_and_update_balance, uid, "despesa", 5, "Mercado X", None,
                                    categoria="mercado")  # 95 %: continua warning
            await barreira(avisados)
            leve = await asyncio.to_thread(_feed, uid), _marcador(uid)
            await asyncio.to_thread(db.add_launch_and_update_balance, uid, "despesa", 10, "Mercado X", None,
                                    categoria="mercado")  # 105 %
            await barreira(avisados)
            return leve, await asyncio.to_thread(_feed, uid)

    (leve, marca), piorou = asyncio.run(cena())
    assert marca is not None  # o gasto leve invalidou o cache
    assert leve == []
    assert _linhas(uid)[0]["dispensado_em"] is not None
    assert piorou == [("budget_warning", "critical")]
    assert llm == []


# ── T7. Marcador em falha: o ouvinte segue de pé e o aviso chega ──────────────────────────────────────────────

def test_t7_falha_do_marcador_nao_derruba_a_escuta(monkeypatch, capsys):
    uid = usuario_pagante("pro")
    real = ai_patterns.invalidar_insights
    monkeypatch.setattr(eventos, "PAUSA_MARCADOR_S", 0.2)

    async def quebrado(*a, **k):
        raise RuntimeError("banco recusou")

    async def cena():
        async with ouvindo(monkeypatch) as avisados:
            monkeypatch.setattr(ai_patterns, "invalidar_insights", quebrado)
            for _ in range(2):
                await asyncio.to_thread(db.add_launch_and_update_balance, uid, "despesa", 1, "x", None)
                await barreira(avisados)
            com_falha = avisados.count(uid), _marcador(uid)
            monkeypatch.setattr(ai_patterns, "invalidar_insights", real)
            await asyncio.sleep(0.3)  # passa a pausa
            await asyncio.to_thread(db.add_launch_and_update_balance, uid, "despesa", 1, "x", None)
            await barreira(avisados)
            return com_falha, avisados.count(uid), _marcador(uid)

    (avisos_com_falha, marca_com_falha), avisos, marca = asyncio.run(cena())
    err = capsys.readouterr().err
    assert "[eventos] marcador de insights falhou: RuntimeError" in err  # a falha aconteceu mesmo
    assert (avisos_com_falha, marca_com_falha) == (2, None)  # as duas escritas chegaram ao stream, sem marca
    assert "LISTEN pb_escrita caiu" not in err
    assert avisos == 3 and marca is not None  # positivo: com o banco bom, marca


# ── T8. Troca de plano (o trigger de plano avisa): o recurso novo aparece sem esperar 6 h ────────────────────

def test_t8_subir_de_plano_mostra_a_conta_a_vencer(monkeypatch, llm):
    monkeypatch.setenv("PLANS_V2_ENABLED", "0")  # o Grátis legado tem insights e não tem gastos fixos (B2, T1)
    uid = completo(promote_to_pro(usuario_pagante("pro"), "free"))
    create_boleto(uid, "Luz", 180, date.today() + timedelta(days=1))
    assert _feed(uid) == []  # Grátis (legado): sem gastos fixos, sem `bill_due`; o cache guarda a lista sem ele

    async def cena():
        async with ouvindo(monkeypatch) as avisados:
            await asyncio.to_thread(promote_to_pro, uid, "pro")
            await barreira(avisados)
            return await asyncio.to_thread(_feed, uid)

    assert asyncio.run(cena()) == [("bill_due", "warning")]
    assert llm == []


# ── T9. Card `piggy` com detector em falha e cache anterior válido: o `motivo` não vem do cache velho ─────────

def test_t9_card_com_detector_em_falha_nao_diz_dispensados(monkeypatch, llm):
    from _cards_apoio import mostrar
    uid = _usuario("pro")
    _orcamento_apertando(uid)  # 90 %: warning, nada dispensado
    assert ai_patterns.generate_ai_insights(uid)  # cache válido com o warning

    import db.insights as dbi
    real = dbi._detect_budget_warnings

    def quebra(u):
        raise RuntimeError("banco piscou")
    monkeypatch.setattr(dbi, "_detect_budget_warnings", quebra)
    _, (bloco,), _ = mostrar(uid, card="piggy")
    corpo = bloco["abas"][0]["recursos"]["insights"]
    assert (corpo["insights"], corpo["motivo"]) == ([], None)  # um detector falhou: não é "dispensados"
    assert ai_patterns.ler_insights(uid) is None  # a lista incompleta não deixa cache para trás
    monkeypatch.setattr(dbi, "_detect_budget_warnings", real)
    assert [i["type"] for i in ai_patterns.generate_ai_insights(uid)] == ["budget_warning"]  # positivo
    assert llm == []
