"""PL-06 B5: o `subscription_increase` (família `assinatura`), o reajuste de assinatura detectada pela Pluggy.

Decisões do dono (2026-10-30): reajuste nos últimos 60 dias, absorvendo a conta do OF do mesmo comerciante (Q5);
warning a partir de 10% (P1); só depois de o preço antigo se repetir em duas cobranças seguidas (P2); queda não avisa
(P3); só `servicos` (P4); a conta do OF some só onde o reajuste sai, e volta depois da janela (P5, issue #1336); se a
consulta de assinaturas falha, a conta do OF continua saindo (fail-open). Postgres real, dados pelos helpers de
`_apoio_assinaturas` e lançamentos do OF gravados direto; datas relativas ao dia real (o detector da conta do OF lê o
relógio do sistema), com o relógio do detector novo adiantado só na D4 (caso 17).

Controles negativos medidos à mão no PR (mutação numa cópia da árvore, os casos que ficam vermelhos): sem a absorção
no `projetar` → 2, 14, 15, 17, 18, 21, 23b; a conta do OF sem `comerciante_id` → 2, 14, 15, 17, 18, 23b; absorver só
pelo comerciante, sem o valor novo → 21, 23b, 23c, 24, 25; comparar o valor novo em reais inteiros → 23c; a chave sem
o sufixo → 1, 5[60], 17, 23, 23b, 29; o sufixo pelo valor NOVO em centavos → 1, 5[60], 17, 23, 27, 29; o sufixo pelo
id da 1ª cobrança nova → 1, 5[60], 11, 17, 20, 23, 28, 29; a dedupe por comerciante e dia → 23, 23b; janela 60 → 59 →
5[60]; queda avisando → 6[queda]; o detector lendo `servicos` em vez de `reajustes_estaveis`, ou a estabilidade com
uma cobrança só → 7, 8, 22; a estabilidade pelo par (chave, reajuste_em) e não pela cadeia → 22; a estabilidade
também como campo do item → 20 e a guarda `test_assinaturas.py::test_netflix_no_cartao_sai_em_servicos_com_o_meio`;
`outras` estáveis também → 9; limiar 10 → 11 → 12[10%]; sem `status == "ativa"` → 26[cancelada]; sem `quando <=
hoje` → 26[futuro]; sem o gate na geração E na leitura → 14, 15, 21 (só na geração: verde, a leitura corta); sem o
tipo em `RECURSO_POR_TIPO` → 15, 21; absorver antes do filtro de recurso → 15, 21; absorver depois da dispensa → 17,
21; a conta do OF consultando as assinaturas sem fallback → 16; `_SQL_OCORRENCIAS` sem `c.user_id = %s` → 18, 29; sem
`impacto` → 13.
Positivos: 1 (o reajuste sai), 3 (a padaria segue), 13 (o Pro vê `impacto`), 14 (Pro legado vê), 15 (com o recurso
aparece), 17 (o mesmo reajuste dispensado não volta), 21 e 2 (a absorção do mesmo fato segue valendo).
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal as D

import pytest

import db
from _apoio_assinaturas import conta, rp, semeia, tx
from _cards_apoio import _http
from _ia_stream_helpers import executar, openai_falso, rodada
from conftest import promote_to_pro, usuario_pagante
from core import ai_patterns
from core.services import insights_detectores as detectores
from core.services.insights_contrato import comerciante_id, montar_item, periodo_dia, projetar
from db.of_recurring import marcar, salvar_recorrencias
from test_api_app_sessao import completo
from test_insights_contrato import _usuario, llm  # noqa: F401
from test_insights_estado import _put
from utils_date import day_tz, now_tz
from utils_text import VALOR_MAX

HOJE = day_tz(now_tz())
NETFLIX = comerciante_id("netflix")
PADRAO = (105, 75, 45, 15)  # dias atrás de cada cobrança: intervalo de 30 dias, mensal


def _cobrancas(prefixo, valores, atras=PADRAO, *, desc="NETFLIX.COM", nome="Netflix"):
    m = {"merchant": {"name": nome}} if nome else {}
    return [tx(f"{prefixo}-{i}", -D(str(v)), HOJE - timedelta(days=d), desc=desc, **m)
            for i, (v, d) in enumerate(zip(valores, atras))]


def _of(uid, alvo, dia, valor):
    """A cobrança como o sync do OF a grava em `launches` (o detector da conta do OF lê daqui)."""
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("insert into launches (user_id, tipo, valor, alvo, criado_em, posted_at, source) "
                    "values (%s, 'despesa', %s, %s, %s, %s, 'open_finance')", (uid, valor, alvo, dia, dia))
        conn.commit()


def _assinatura(uid, valores, atras=PADRAO, *, desc="NETFLIX.COM", nome="Netflix", of=False, alvo=None, prefixo="nf",
                tipo="CREDIT"):
    txs = _cobrancas(prefixo, valores, atras, desc=desc, nome=nome)
    cid = semeia(uid, [conta(f"acc-{prefixo}", txs, tipo=tipo)], [rp(desc, -float(valores[-1]), txs)])
    if of:
        for t in txs:
            _of(uid, alvo or desc, t["transaction_date"], -t["amount"])
    return cid, txs


def _feed(uid):
    ai_patterns.generate_ai_insights(uid, force=True)
    r = _http(uid).get(f"/insights/{uid}/current")
    assert r.status_code == 200, r.text
    return r.json()["insights"]


def _tipos(itens):
    return [(i["type"], i["title"]) for i in itens if i["type"] in ("subscription_increase", "of_bill_increase")]


def _reajuste_dia(atras=15):
    return (HOJE - timedelta(days=atras)).isoformat()


# ── 1 a 4: o reajuste e a absorção da conta do OF ───────────────────────────────

def test_1_netflix_subiu_13_por_cento(llm):
    uid = _usuario("pro")
    _assinatura(uid, (39.9, 39.9, 39.9, 44.9))
    (it,) = [i for i in _feed(uid) if i["type"] == "subscription_increase"]
    assert (it["key"], it["severity"], it["title"]) == (
        f"subscription_increase:netflix:{_reajuste_dia()}:3990", "warning", "Netflix subiu 13%")
    dia = HOJE - timedelta(days=15)
    assert it["message"] == (f"De R$ 39,90 para R$ 44,90 por mês, cobrado assim desde {dia:%d/%m}. "
                             "Detectado pelo Open Finance.")
    assert (it["action_label"], it["action_view"], "acoes" in it) == (None, None, False)
    e = it["explicacao"]
    assert (e["familia"], e["fonte"]["base"], e["fonte"]["origem"]) == (
        "assinatura", "open_finance", "assinaturas detectadas pelo Open Finance")
    assert e["referencia"] == {"estatistica": "valor_anterior", "assinatura": "Netflix", "valor": 39.9,
                               "frequencia": "monthly", "comerciante_id": NETFLIX}
    assert (e["atual"], e["diferenca"]) == ({"valor": 44.9, "data": _reajuste_dia()},
                                           {"valor": 5.0, "percentual": 12.5})


def test_2_e_3_absorve_a_conta_do_of_da_netflix_e_deixa_a_padaria(llm):
    """Pelo caminho real: a assinatura debitada em CONTA (cartão vai para `credit_transactions`, sem conta do OF) e os
    lançamentos pelo import do OF (`import_open_finance_launches`)."""
    from db.open_finance import import_open_finance_launches
    uid = _usuario("pro")
    _assinatura(uid, (39.9, 39.9, 39.9, 49.9), tipo="BANK")
    assert import_open_finance_launches(uid)
    for v, d in ((20, 75), (20, 45), (26, 15)):  # padaria +30%, sem Recurring Payments
        _of(uid, "Padaria do Zé", HOJE - timedelta(days=d), v)
    assert _tipos(_feed(uid)) == [("subscription_increase", "Netflix subiu 25%"),
                                  ("of_bill_increase", "Padaria do Zé subiu 30%")]
    cache = ai_patterns._get_cached(uid, ai_patterns._cache_kind("insights"), 3600)
    assert ("of_bill_increase", "NETFLIX.COM subiu 25%") in _tipos(cache)  # registrado e em cache, só não mostrado


def test_4_descricao_diferente_no_grupo_e_na_transacao_mostra_os_dois(llm):
    """Limite: o casamento é pelo `merchant_key`; "PG *NETFLIX ENTRETENIMENTO" não é "netflix". Erra para a
    duplicidade, nunca para o aviso perdido."""
    uid = _usuario("pro")
    _assinatura(uid, (39.9, 39.9, 39.9, 49.9), of=True, alvo="PG *NETFLIX ENTRETENIMENTO")
    assert [t for t, _ in _tipos(_feed(uid))] == ["subscription_increase", "of_bill_increase"]


# ── 5 a 10: a regra ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("atras,aparece", [(61, False), (60, True)])
def test_5_janela_de_60_dias(llm, atras, aparece):
    uid = _usuario("pro")
    _assinatura(uid, (39.9, 39.9, 39.9, 44.9, 44.9), (atras + 90, atras + 60, atras + 30, atras, atras - 30))
    assert [i["key"] for i in _feed(uid) if i["type"] == "subscription_increase"] == (
        [f"subscription_increase:netflix:{_reajuste_dia(atras)}:3990"] if aparece else [])


@pytest.mark.parametrize("valores", [
    (44.9, 44.9, 44.9, 39.9),        # 6: queda (P3)
    (110.45, 112.30, 109.80, 113.10),  # 7: cobrança em dólar que varia todo mês (P2)
])
def test_6_e_7_queda_e_preco_que_varia_nao_avisam(llm, valores):
    uid = _usuario("pro")
    _assinatura(uid, valores)
    assert [i for i in _feed(uid) if i["type"] == "subscription_increase"] == []


def test_8_preco_antigo_numa_cobranca_so_nao_avisa(llm):
    """Aceito pelo dono (P2): sem duas cobranças no preço antigo, não há reajuste a apontar."""
    uid = _usuario("pro")
    _assinatura(uid, (39.9, 44.9), (45, 15))
    assert [i for i in _feed(uid) if i["type"] == "subscription_increase"] == []


def test_9_conta_de_luz_em_outras_segue_na_regra_da_conta_do_of(llm):
    uid = _usuario("pro")
    _assinatura(uid, (100, 100, 100, 140), desc="ENEL DISTRIBUICAO", nome=None, of=True, prefixo="luz")
    from core.services.assinaturas import listar_assinaturas
    a = listar_assinaturas(uid, HOJE)
    assert [x["chave"] for x in a["outras"]] == ["enel distribuicao"] and a["servicos"] == []
    assert [t for t, _ in _tipos(_feed(uid))] == ["of_bill_increase"]


def test_10_assinatura_ignorada_nao_avisa_e_a_conta_do_of_continua(llm):
    uid = _usuario("pro")
    _assinatura(uid, (39.9, 39.9, 39.9, 49.9), of=True)
    marcar(uid, "netflix", "ignorar")
    assert [t for t, _ in _tipos(_feed(uid))] == ["of_bill_increase"]


# ── 11 a 13: textos, gravidade e Pro ─────────────────────────────────────────────

def test_11_com_o_maior_valor_do_banco_nada_e_cortado(monkeypatch):
    import core.services.assinaturas as assinaturas
    maior = D(VALOR_MAX) - D("0.01")
    item = {"chave": "netflix", "nome": "N" * 60, "valor": maior, "valor_anterior": maior / 2,
            "reajuste_em": HOJE.isoformat(), "status": "ativa"}
    monkeypatch.setattr(assinaturas, "listar_assinaturas", lambda *_a: {
        "servicos": [item], "reajustes_estaveis": [item]})
    monkeypatch.setattr("core.services.plan_service.plan_gate_ok", lambda *_a: True)
    (it,) = detectores.detect_subscription_increase(1)
    assert it["title"].endswith(" subiu 100%") and len(it["title"]) <= 140
    assert it["message"].endswith("Detectado pelo Open Finance.") and len(it["message"]) <= 240


@pytest.mark.parametrize("novo,severidade,titulo", [(109, "info", "Netflix subiu 9%"),
                                                    (110, "warning", "Netflix subiu 10%")])
def test_12_alta_de_9_e_info_e_de_10_e_warning(llm, novo, severidade, titulo):
    uid = _usuario("pro")
    _assinatura(uid, (100, 100, 100, novo))
    assert [(i["severity"], i["title"]) for i in _feed(uid) if i["type"] == "subscription_increase"] == [
        (severidade, titulo)]


def test_13_impacto_anual_so_no_pro(llm):
    pro, plus = _usuario("pro_max"), _usuario("pro")
    for uid in (pro, plus):
        _assinatura(uid, (39.9, 39.9, 39.9, 44.9))
    (p,) = [i for i in _feed(pro) if i["type"] == "subscription_increase"]
    (s,) = [i for i in _feed(plus) if i["type"] == "subscription_increase"]
    assert p["impacto"] == {"por_ano_valor": 60.0} and "impacto" not in s


# ── 14 a 17: plano, cache, falha e a D4 ──────────────────────────────────────────

def test_14_sem_o_recurso_nao_ha_reajuste_e_a_conta_do_of_aparece(llm, monkeypatch):
    essencial = _usuario("essencial")
    assert _http(essencial).get(f"/insights/{essencial}/current").status_code == 403
    monkeypatch.setenv("PLANS_V2_ENABLED", "0")
    gratis, pro = promote_to_pro(usuario_pagante("pro"), "free"), usuario_pagante("pro")
    for uid in (gratis, pro):
        _assinatura(uid, (39.9, 39.9, 39.9, 49.9), of=True)
    assert [i["type"] for i in ai_patterns.generate_ai_insights(gratis, force=True)
            if i["type"] in ("subscription_increase", "of_bill_increase")] == ["of_bill_increase"]
    assert [i["type"] for i in ai_patterns.generate_ai_insights(pro, force=True)
            if i["type"] in ("subscription_increase", "of_bill_increase")] == ["subscription_increase"]


def test_15_quem_perde_o_recurso_com_o_cache_quente_volta_a_ver_a_conta_do_of(llm, monkeypatch):
    monkeypatch.setenv("PLANS_V2_ENABLED", "0")
    uid = completo(usuario_pagante("pro"))
    _assinatura(uid, (39.9, 39.9, 39.9, 49.9), of=True)
    ai_patterns.generate_ai_insights(uid, force=True)
    assert [t for t, _ in _tipos(ai_patterns.ler_insights(uid))] == ["subscription_increase"]  # positivo
    monkeypatch.setattr("db.compute_active_insights", lambda *a: pytest.fail("recalculou"))
    promote_to_pro(uid, "free")
    assert [t for t, _ in _tipos(ai_patterns.ler_insights(uid))] == ["of_bill_increase"]
    assert [t for t, _ in _tipos(_http(uid).get(f"/insights/{uid}/current").json()["insights"])] == [
        "of_bill_increase"]


def test_16_assinaturas_fora_do_ar_a_conta_do_of_continua(llm, monkeypatch):
    import core.services.assinaturas as assinaturas
    uid = _usuario("pro")
    _assinatura(uid, (39.9, 39.9, 39.9, 49.9), of=True)

    def quebra(*_a, **_k):
        raise RuntimeError("banco fora")
    monkeypatch.setattr(assinaturas, "listar_assinaturas", quebra)
    itens, falhas = db.compute_active_insights(uid)
    assert falhas == ["detect_subscription_increase"]
    assert [t for t, _ in _tipos(itens)] == ["of_bill_increase"]
    r = _http(uid).get(f"/insights/{uid}/current").json()
    assert [t for t, _ in _tipos(r["insights"])] == ["of_bill_increase"] and r["motivo"] is None
    assert ai_patterns.ler_insights(uid) is None  # detector em falha não grava o cache


def test_17_dispensar_o_reajuste_nao_ressuscita_a_conta_e_reajuste_novo_aparece(llm, monkeypatch):
    uid = _usuario("pro")
    cid, txs = _assinatura(uid, (39.9, 39.9, 39.9, 49.9), of=True)
    (it,) = [i for i in _feed(uid) if i["type"] == "subscription_increase"]
    assert _put(uid, chave=it["key"], dispensado=True, severidade=it["severity"]).status_code == 200
    assert _tipos(_feed(uid)) == []  # nem o reajuste nem a conta do OF do mesmo fato
    novos = txs + [tx("nf-4", D("-49.9"), HOJE + timedelta(days=15), desc="NETFLIX.COM", merchant={"name": "Netflix"}),
                   tx("nf-5", D("-59.9"), HOJE + timedelta(days=45), desc="NETFLIX.COM", merchant={"name": "Netflix"})]
    db.save_open_finance_sync(cid, [conta("acc-nf", novos, tipo="CREDIT")])
    salvar_recorrencias(cid, [rp("NETFLIX.COM", -59.9, novos)])
    monkeypatch.setattr(detectores, "now_tz", lambda: now_tz() + timedelta(days=50))
    assert [i["key"] for i in _feed(uid) if i["type"] == "subscription_increase"] == [
        f"subscription_increase:netflix:{HOJE + timedelta(days=45)}:4990"]


# ── 18 a 21: isolamento, conversa, a rota de assinaturas e a ordem do `projetar` ──

def test_18_o_reajuste_de_um_usuario_nao_absorve_nem_aparece_para_o_outro(llm):
    a, b = _usuario("pro"), _usuario("pro")
    _assinatura(a, (39.9, 39.9, 39.9, 39.9))  # mesmos ids de transação, sem reajuste
    for v, d in ((39.9, 75), (39.9, 45), (49.9, 15)):
        _of(a, "NETFLIX.COM", HOJE - timedelta(days=d), v)
    _assinatura(b, (39.9, 39.9, 39.9, 49.9), of=True)
    assert [t for t, _ in _tipos(_feed(a))] == ["of_bill_increase"]
    assert [t for t, _ in _tipos(_feed(b))] == ["subscription_increase"]


def test_19_conversa_card_traz_o_reajuste_e_o_turno_seguinte_segue(llm, monkeypatch):
    """Ponta a ponta pelo runner. O LLM é falso: não prova a interpretação real das mensagens."""
    uid = _usuario("pro")
    _assinatura(uid, (39.9, 39.9, 39.9, 44.9))
    ai_patterns.generate_ai_insights(uid)
    openai_falso(monkeypatch, rodada(None, ("show_card", {"card": "piggy"})), rodada("Olha o que eu notei."))
    _, ev, _ = executar(uid, "o que você notou?")
    (bloco,) = [e["bloco"] for e in ev if e["tipo"] == "bloco"]
    assert "Netflix subiu 13%" in [i["title"] for i in bloco["abas"][0]["recursos"]["insights"]["insights"]]
    # Com banco conectado o gasto pede a forma; "em dinheiro" a responde (Q40), como no chat de verdade.
    openai_falso(monkeypatch, rodada(None, ("add_launch", {"tipo": "despesa", "valor": 50, "alvo": "mercado",
                                                           "categoria": "mercado", "forma_pagamento": "dinheiro"})))
    r2, _, _ = executar(uid, "gastei 50 em dinheiro no mercado")
    assert "Despesa registrada" in r2.reply and llm == []


def test_20_o_par_estavel_nao_vaza_para_a_rota_nem_para_o_detetive(monkeypatch):
    """`reajustes_estaveis` é chave de topo de `listar_assinaturas` com os próprios itens, fora deles (a guarda de
    `test_assinaturas.py` exige item = `Assinatura.model_fields`) e fora do modelo da rota."""
    from api.v2.assinaturas import Assinatura
    from core.services.agent_chat_data import _snapshot
    from core.services.assinaturas import listar_assinaturas
    from test_api_v2_assinaturas import _get
    uid = usuario_pagante("pro")
    monkeypatch.setenv("DASHBOARD_V2_BETA_EMAILS", "")
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", str(uid))
    _assinatura(uid, (39.9, 39.9, 39.9, 44.9))
    lista = listar_assinaturas(uid, HOJE)
    (estavel,) = lista["reajustes_estaveis"]
    assert estavel is lista["servicos"][0] and estavel["reajuste_em"] == _reajuste_dia()  # positivo: o item é marcado
    assert all(set(it) == set(Assinatura.model_fields) for it in lista["servicos"] + lista["outras"])
    r = _get(uid)
    assert r.status_code == 200, r.text
    assert set(r.json()) == {"servicos", "outras", "ignoradas", "total_mensal", "total_anual"}
    (s,) = r.json()["servicos"]
    assert set(s) == set(Assinatura.model_fields) and (s["valor_anterior"], s["reajuste_em"]) == ("39.9", _reajuste_dia())
    snap = _snapshot(uid, "detetive")
    assert "reajustes_estaveis" not in json.dumps(snap, default=str)
    assert all(set(it) == set(Assinatura.model_fields) for it in snap["recorrencias"])


def _sintetico(tipo, comerciante, key, novo=D("49.9")):
    return {**montar_item(tipo, key, periodo_dia(HOJE), title=key, message="", severity="warning", icon="📈",
                          action_label=None, action_view=None, referencia={"comerciante_id": comerciante},
                          atual={"valor": novo}),
            "key": key}


def test_21_projetar_filtra_o_recurso_absorve_e_so_depois_dispensa():
    reajuste = _sintetico("subscription_increase", NETFLIX, "r")
    of_netflix, of_outro = _sintetico("of_bill_increase", NETFLIX, "o1"), _sintetico("of_bill_increase", "x", "o2")
    itens = [reajuste, of_netflix, of_outro]

    def chaves(**kw):
        return [i["key"] for i in projetar(itens, acoes_ok=False, **kw)]
    assert chaves(recursos_ok={"subscriptions": True}) == ["r", "o2"]
    assert chaves(recursos_ok={}) == ["o1", "o2"]  # sem o recurso, a conta do OF volta
    assert chaves(recursos_ok={"subscriptions": True}, dispensadas={"r": ("warning", None)}) == ["o2"]
    of_outro_valor = _sintetico("of_bill_increase", NETFLIX, "o3", D("59.9"))  # mesmo comerciante, outro valor novo
    assert [i["key"] for i in projetar([reajuste, of_outro_valor], acoes_ok=False,
                                       recursos_ok={"subscriptions": True})] == ["r", "o3"]


# ── Rodada 2: a cadeia certa, o mesmo fato e as bordas da regra ────────────────────

def _apple(uid, music_valores=(21.9, 25.9, 31.9), music_atras=(75, 45, 15), of_music=False):
    """iCloud (9,90 ×3 → 14,90) e Apple Music no mesmo `merchant_key` ("apple com bill"), reajuste no mesmo dia."""
    icloud = _cobrancas("ic", (9.9, 9.9, 9.9, 14.9), desc="APPLE.COM/BILL", nome="Apple")
    music = _cobrancas("am", music_valores, music_atras, desc="APPLE.COM/BILL", nome="Apple")
    semeia(uid, [conta("acc-ap", icloud + music, tipo="CREDIT")],
           [rp("APPLE.COM/BILL", -14.9, icloud), rp("APPLE.COM/BILL", -float(music_valores[-1]), music)])
    if of_music:
        for t in music:
            _of(uid, "APPLE.COM/BILL", t["transaction_date"], -t["amount"])


def test_22_a_estabilidade_e_da_cadeia_e_nao_da_chave(llm):
    """O Apple Music instável não herda a estabilidade do iCloud: sai só o reajuste do iCloud (+51%)."""
    uid = _usuario("pro")
    _apple(uid)
    assert [(i["title"], i["explicacao"]["atual"]["valor"]) for i in _feed(uid)
            if i["type"] == "subscription_increase"] == [("Apple subiu 51%", 14.9)]


def test_23_dois_servicos_do_comerciante_no_mesmo_dia_sao_dois_avisos(llm):
    """P6 (b): iCloud 9,90 → 14,90 e Apple Music 21,90 → 25,90, os dois estáveis e no mesmo dia: dois itens, chaves
    distintas pelo preço antigo (P7), e dispensar um não esconde o outro."""
    uid = _usuario("pro")
    _apple(uid, music_valores=(21.9, 21.9, 21.9, 25.9), music_atras=PADRAO)
    dia = _reajuste_dia()
    itens = {i["key"]: i for i in _feed(uid) if i["type"] == "subscription_increase"}
    assert set(itens) == {f"subscription_increase:apple com bill:{dia}:990",
                          f"subscription_increase:apple com bill:{dia}:2190"}
    icloud = itens[f"subscription_increase:apple com bill:{dia}:990"]
    assert _put(uid, chave=icloud["key"], dispensado=True, severidade=icloud["severity"]).status_code == 200
    assert [i["key"] for i in _feed(uid) if i["type"] == "subscription_increase"] == [
        f"subscription_increase:apple com bill:{dia}:2190"]


def test_23b_a_conta_do_of_so_some_com_o_reajuste_do_mesmo_valor(llm):
    """Com os dois reajustes da Apple no feed, a conta do OF que subiu para 25,90 é absorvida (o Apple Music) e a que
    subiu para 31,90 (um terceiro valor) sai."""
    uid = _usuario("pro")
    _apple(uid, music_valores=(21.9, 21.9, 21.9, 25.9), music_atras=PADRAO)
    for v, d in ((21.9, 75), (21.9, 45), (25.9, 15)):
        _of(uid, "APPLE.COM/BILL", HOJE - timedelta(days=d), v)
    assert [t for t, _ in _tipos(_feed(uid))] == ["subscription_increase", "subscription_increase"]
    outro = _usuario("pro")
    _apple(outro, music_valores=(21.9, 21.9, 21.9, 25.9), music_atras=PADRAO)
    for v, d in ((21.9, 75), (21.9, 45), (31.9, 15)):
        _of(outro, "APPLE.COM/BILL", HOJE - timedelta(days=d), v)
    assert sorted(t for t, _ in _tipos(_feed(outro))) == ["of_bill_increase", "subscription_increase",
                                                          "subscription_increase"]


def test_23c_valor_novo_diferente_so_nos_centavos_nao_e_o_mesmo_fato(llm):
    """R6: a assinatura foi a 49,90 e a conta do OF a 49,50: comparação em centavos, saem os dois."""
    uid = _usuario("pro")
    _assinatura(uid, (39.9, 39.9, 39.9, 49.9))
    for v, d in ((39.9, 75), (39.9, 45), (49.5, 15)):
        _of(uid, "NETFLIX.COM", HOJE - timedelta(days=d), v)
    assert sorted(t for t, _ in _tipos(_feed(uid))) == ["of_bill_increase", "subscription_increase"]


def test_24_a_conta_do_of_de_outro_servico_do_comerciante_nao_e_absorvida(llm):
    """O iCloud subiu (14,90) e a conta do OF que subiu é a do Apple Music (31,90): são fatos diferentes, saem os dois."""
    uid = _usuario("pro")
    icloud = _cobrancas("ic", (9.9, 9.9, 9.9, 14.9), desc="APPLE.COM/BILL", nome="Apple")
    semeia(uid, [conta("acc-ap", icloud, tipo="CREDIT")], [rp("APPLE.COM/BILL", -14.9, icloud)])
    for v, d in ((25.9, 75), (25.9, 45), (31.9, 15)):
        _of(uid, "APPLE.COM/BILL", HOJE - timedelta(days=d), v)
    assert [t for t, _ in _tipos(_feed(uid))] == ["subscription_increase", "of_bill_increase"]


def test_25_aumento_que_o_recurring_payments_ainda_nao_viu_nao_e_absorvido(llm):
    """O grupo da Pluggy parou em 44,90; o extrato do OF já traz 59,90. Nem dispensar o reajuste esconde a conta."""
    uid = _usuario("pro")
    _assinatura(uid, (39.9, 39.9, 44.9, 44.9), (110, 80, 50, 20))
    for v, d in ((39.9, 110), (39.9, 80), (44.9, 50), (44.9, 20), (59.9, 5)):
        _of(uid, "NETFLIX.COM", HOJE - timedelta(days=d), v)
    itens = _feed(uid)
    (sub,) = [i for i in itens if i["type"] == "subscription_increase"]
    (of,) = [i for i in itens if i["type"] == "of_bill_increase"]
    assert (sub["explicacao"]["atual"]["valor"], of["explicacao"]["atual"]["valor"]) == (44.9, 59.9)
    assert _put(uid, chave=sub["key"], dispensado=True, severidade=sub["severity"]).status_code == 200
    assert [t for t, _ in _tipos(_feed(uid))] == ["of_bill_increase"]


@pytest.mark.parametrize("valores,atras", [
    ((39.9, 39.9, 39.9, 44.9), (150, 120, 90, 55)),  # T1: reajuste há 55 dias, última cobrança há mais de 40: cancelada
    ((39.9, 39.9, 39.9, 44.9), (75, 45, 15, -5)),    # T2: a cobrança no valor novo tem data futura
])
def test_26_assinatura_cancelada_ou_reajuste_futuro_nao_avisam(llm, valores, atras):
    uid = _usuario("pro")
    _assinatura(uid, valores, atras)
    assert [i for i in _feed(uid) if i["type"] == "subscription_increase"] == []


# ── P7 (b): a chave pelo preço antigo, que a Pluggy não mexe ao corrigir a cobrança nova ───

def _dispensa_netflix(uid):
    cid, txs = _assinatura(uid, (39.9, 39.9, 39.9, 44.9))
    (it,) = [i for i in _feed(uid) if i["type"] == "subscription_increase"]
    assert _put(uid, chave=it["key"], dispensado=True, severidade=it["severity"]).status_code == 200
    assert [i for i in _feed(uid) if i["type"] == "subscription_increase"] == []
    return cid, txs, it["key"]


def _ressincroniza(cid, txs):
    """O caminho real: o sync grava as transações e a Pluggy reenvia o grupo do Recurring Payments."""
    db.save_open_finance_sync(cid, [conta("acc-nf", txs, tipo="CREDIT")])
    salvar_recorrencias(cid, [rp("NETFLIX.COM", float(txs[-1]["amount"]), txs)])


def _chaves_no_cache(uid):
    ai_patterns.generate_ai_insights(uid, force=True)
    cache = ai_patterns._get_cached(uid, ai_patterns._cache_kind("insights"), 3600)
    return [i["key"] for i in cache if i["type"] == "subscription_increase"]


def test_27_pluggy_corrige_os_centavos_da_cobranca_nova_depois_da_dispensa(llm):
    """O test_g do Tester: a MESMA transação volta com 44,89. A chave é a mesma e o dispensado não volta."""
    from core.services.assinaturas import listar_assinaturas
    uid = _usuario("pro")
    cid, txs, chave = _dispensa_netflix(uid)
    _ressincroniza(cid, txs[:-1] + [{**txs[-1], "amount": D("-44.89")}])
    assert [a["valor"] for a in listar_assinaturas(uid, HOJE)["servicos"]] == [D("44.89")]  # a correção chegou
    assert _chaves_no_cache(uid) == [chave]
    assert [i for i in _feed(uid) if i["type"] == "subscription_increase"] == []


def test_28_cobranca_nova_relancada_com_outro_id_depois_da_dispensa(llm):
    """Pendente → lançada: mesma data e valor, `provider_transaction_id` novo, e a antiga some do grupo da Pluggy."""
    uid = _usuario("pro")
    cid, txs, chave = _dispensa_netflix(uid)
    nova = {**txs[-1], "provider_transaction_id": "nf-3-lancada", "raw": {**txs[-1]["raw"], "id": "nf-3-lancada"}}
    _ressincroniza(cid, txs[:-1] + [nova])
    assert _chaves_no_cache(uid) == [chave]
    assert [i for i in _feed(uid) if i["type"] == "subscription_increase"] == []


def test_29_limites_aceitos_pelo_dono_na_p7(llm):
    """Não são defeitos: são os limites da P7 (b) do dono, afirmados aqui para que mudá-los seja uma decisão.
    (1) Mesmo comerciante, mesmo dia e MESMO preço antigo, preços novos diferentes: um aviso só.
    (2) A Pluggy corrigir uma cobrança ANTIGA muda a chave: o dispensado volta uma vez."""
    uid = _usuario("pro")
    a = _cobrancas("ia", (9.9, 9.9, 9.9, 14.9), desc="APPLE.COM/BILL", nome="Apple")
    b = _cobrancas("ib", (9.9, 9.9, 9.9, 19.9), desc="APPLE.COM/BILL", nome="Apple")
    semeia(uid, [conta("acc-ap", a + b, tipo="CREDIT")], [rp("APPLE.COM/BILL", -14.9, a), rp("APPLE.COM/BILL", -19.9, b)])
    assert [i["key"] for i in _feed(uid) if i["type"] == "subscription_increase"] == [
        f"subscription_increase:apple com bill:{_reajuste_dia()}:990"]
    outro = _usuario("pro")
    cid, txs, chave = _dispensa_netflix(outro)
    _ressincroniza(cid, [{**txs[0], "amount": D("-39.89")}, {**txs[1], "amount": D("-39.89")},
                         {**txs[2], "amount": D("-39.89")}, txs[3]])
    assert [i["key"] for i in _feed(outro) if i["type"] == "subscription_increase"] == [
        chave.replace(":3990", ":3989")]  # o preço antigo virou 39,89: outra chave, o aviso volta
