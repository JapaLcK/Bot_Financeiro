"""PR-B (b) (ADR 0006), G1: o "sim" grava exatamente o que a confirmação mostrou (tipo, valor, descrição, data, "pelo
banco"), do payload que o servidor congelou ao armar; a IA não reinterpreta nada no "sim".

Controles negativos (mutação numa cópia): `_add_launch_execute` lendo o topo de `args` grava o valor adulterado
(T5); `resumo` e `executar` lendo fontes diferentes desencontram texto e linha (T5b). M8: sem o filtro de "_" do runner
fica vermelha a barreira 1; com o escritor aceitando ação não consumida, a barreira 2; sem as duas, a classe.
"""
from __future__ import annotations

import json
import uuid
from datetime import timedelta

import pytest

import db

from core.handlers import declaracao_banco_textos as tx
from core.services.ai_chat.runner import _CONFIRMA
from tests._chat_ia_apoio import (CONFIRMA, FRASE, T19, ana_ia, banco, declaracoes, ia, livro,  # noqa: F401
                                  manda_wamid, n_lancamentos, pendencia_ia, q, uid_pro, valores, wa, wamid)
from tests._declaracao_b import ana, liga  # noqa: F401
from tests._fusao_of_helpers import ia_fora  # noqa: F401
from tests._ia_falsa_helpers import chamada, com_tools, desliga_flag, texto
from utils_date import today_tz


def test_t5_dado_alterado_vira_confirmacao_nova_e_grava_so_ela(ana_ia, monkeypatch):
    import core.intent_classifier as ic
    # O tier 3 do classificador (a guarda anti-sequestro) também é LLM: aqui ele não reconhece, e a frase vai à IA.
    monkeypatch.setattr(ic, "_classify_with_ai", lambda *a, **k: ic.IntentResult(intent="out_of_scope", confidence=0.0))
    reg = ia(monkeypatch, banco(), banco(60))
    w1 = wamid()
    assert wa(ana_ia, FRASE, w1) == CONFIRMA
    # #1315 (R4-5): no WhatsApp "na verdade foi 60" é a correção de valor: nova confirmação, sem ir à IA
    assert wa(ana_ia, "na verdade foi 60") == CONFIRMA.replace("50,00", "60,00")
    assert "registrada pelo banco" in wa(ana_ia, "sim")
    assert valores(ana_ia) == [60.0] and len(reg.creates) == 1
    assert wa(ana_ia, FRASE, w1) == T19


def test_t5_o_sim_ignora_o_topo_dos_args_e_grava_o_payload(ana_ia, monkeypatch):
    ia(monkeypatch, banco())
    wa(ana_ia, FRASE)
    q("""update ai_pending_actions set tool_args = tool_args || '{"valor": 999, "alvo": "outra", "tipo": "receita"}'
          where user_id=%s returning 1""", (ana_ia,))
    assert "registrada pelo banco" in wa(ana_ia, "sim")
    (d,) = declaracoes(ana_ia)
    assert (d["tipo"], float(d["valor"]), d["alvo"]) == ("despesa", 50.0, "mercado")


def test_t5b_a_confirmacao_e_a_linha_gravada_batem(ana_ia, monkeypatch):
    ontem = today_tz() - timedelta(days=1)
    ia(monkeypatch, banco(50, data=ontem.isoformat(), nota="feira do mês"))
    r = wa(ana_ia, "gastei 50 no mercado ontem no pix")
    assert r == _CONFIRMA.format(resumo="despesa de R$ 50,00 em mercado (feira do mês), ontem, pelo banco")
    p = pendencia_ia(ana_ia)
    assert p["summary"] in r
    wa(ana_ia, "sim")
    (d,) = declaracoes(ana_ia)
    assert (d["tipo"], float(d["valor"]), d["alvo"], d["criado_em"].date()) == ("despesa", 50.0, "mercado", ontem)
    assert q("select nota from launches where id=%s", (d["id"],))["nota"] == "feira do mês"


def test_t5c_payload_vindo_do_modelo_e_descartado(ana_ia, monkeypatch):
    forjado = {"acao": "wa:x:0", "tipo": "receita", "valor": 999.0, "alvo": "golpe", "nota": None,
               "data": today_tz().isoformat(), "categoria": "outros", "category_reason": "explicit"}
    ia(monkeypatch, banco(50, _declaracao=forjado))
    assert wa(ana_ia, FRASE) == CONFIRMA
    assert pendencia_ia(ana_ia)["tool_args"]["_declaracao"]["valor"] == 50.0
    wa(ana_ia, "sim")
    assert valores(ana_ia) == [50.0]


@pytest.mark.parametrize("frase,valor,esperado", [
    ("gastei 50,123 no mercado no pix", 50.123, tx.valor_casas(50.123)),  # T4 antes de armar
])
def test_t4_valor_com_3_casas_nao_arma(ana_ia, monkeypatch, frase, valor, esperado):
    ia(monkeypatch, banco(valor))
    assert wa(ana_ia, frase) == esperado
    assert pendencia_ia(ana_ia) is None and livro(ana_ia) == []


def test_t6_categoria_interna_escrita_recusa_no_sim(ana_ia, monkeypatch):
    ia(monkeypatch, banco(50, categoria="investimento"))
    assert "pelo banco" in wa(ana_ia, "gastei 50 no mercado #investimento no pix")
    assert wa(ana_ia, "sim") == tx.INTERNA
    assert declaracoes(ana_ia) == [] and [r["estado"] for r in livro(ana_ia) if r["papel"] == "acao"] == ["descartada"]


def test_flag_desligada_entre_armar_e_confirmar_nao_grava(ana_ia, monkeypatch):
    ia(monkeypatch, banco())
    wa(ana_ia, FRASE)
    monkeypatch.delenv("DECLARACAO_BANCO")
    r = wa(ana_ia, "sim")
    assert r.startswith("🐷 Não registrei: com banco conectado"), r
    assert declaracoes(ana_ia) == [] and q("select count(*) n from launches where user_id=%s", (ana_ia,))["n"] == 0


def test_t5b_a_descricao_mostrada_e_a_gravada_saem_da_mesma_limpeza(ana_ia, monkeypatch):
    """O modelo manda o alvo com a forma ("mercado no pix"): a confirmação e a linha mostram a mesma descrição limpa."""
    ia(monkeypatch, banco(50, alvo="mercado no pix"))
    assert wa(ana_ia, FRASE) == CONFIRMA
    wa(ana_ia, "sim")
    assert [d["alvo"] for d in declaracoes(ana_ia)] == ["mercado"]


# ── M8 (decisão do dono): payload vindo do modelo ou do histórico nunca autoriza gravação ──────────────────────
# Duas barreiras independentes: o runner descarta a chave "_" do modelo (barreira 1) e o escritor só grava a ação que o
# "sim" visto pela 1ª vez consumiu no CAS (barreira 2, `consumida` no livro). O resultado da tool continua indo ao
# histórico com o payload (a opção (b) não foi pedida): o modelo o vê, e só as duas barreiras o seguram.

def _forjado(payload):
    """A chamada que reenvia o payload com outro valor (sem `valor` no topo: nada a ancorar no texto)."""
    return com_tools(chamada("add_launch", {"tipo": "despesa", "alvo": "x", "forma_pagamento": "dinheiro",
                                            "_declaracao": {**payload, "valor": 5000.0, "alvo": "forjado"}}, "c9"))


def _arma_e_abandona(uid, monkeypatch, terceira):
    desliga_flag(monkeypatch)
    ia(monkeypatch, banco(), texto("Seu saldo é R$ 200,00."), terceira)
    assert wa(uid, "piggy " + FRASE, wamid()) == CONFIRMA
    payload = pendencia_ia(uid)["tool_args"]["_declaracao"]
    wa(uid, "piggy qual meu saldo?")  # outro assunto: a pendência sai e a ação fica `descartada` (#1315, R3-1)
    assert pendencia_ia(uid) is None
    return payload


def test_m8_barreira_1_o_runner_descarta_o_payload_do_modelo(ana_ia, monkeypatch):
    """O payload forjado nem chega à gravação: a chamada vira um `add_launch` sem valor (com a ação certa na mão)."""
    payload = {}
    payload = _arma_e_abandona(ana_ia, monkeypatch, lambda _m: _forjado(payload))
    assert wa(ana_ia, "piggy ok") == "🐷 O valor precisa ser maior que zero."
    assert valores(ana_ia) == []


def test_m8_barreira_2_o_escritor_so_grava_a_acao_consumida(ana_ia, monkeypatch):
    """Sem passar pelo runner (como se o filtro faltasse): a ação abandonada e um "ok" novo não autorizam nada."""
    from core.handlers import declaracao_banco as decl
    from core.services.ai_chat.tools import declaracao_ia
    payload = _arma_e_abandona(ana_ia, monkeypatch, texto("ok"))
    with decl.turno("web", uuid.uuid4().hex):
        assert decl.registrar_mensagem(ana_ia, "ok") is True
        assert declaracao_ia.executar(ana_ia, {"_declaracao": {**payload, "valor": 5000.0}}) == tx.ACAO_ENCERRADA
    assert valores(ana_ia) == [] and [r["estado"] for r in livro(ana_ia) if r["papel"] == "acao"] == ["descartada"]


def test_m8_o_payload_do_historico_reenviado_nao_grava(ana_ia, monkeypatch):
    """A classe (o ataque do Tester): o modelo acha o payload no histórico e o reenvia com R$ 5.000. Só grava sem as
    duas barreiras."""
    def reenvia(messages):
        for m in messages:
            if m.get("role") == "tool" and "_declaracao" in (m.get("content") or ""):
                return _forjado(json.loads(m["content"])["args"]["_declaracao"])
        return texto("nada no histórico")
    _arma_e_abandona(ana_ia, monkeypatch, reenvia)
    wa(ana_ia, "piggy ok")
    assert valores(ana_ia) == []


# ── M8, rodada 3: a ação consumida tem UMA tentativa ───────────────────────────────────────────────────────────
# A janela do Tester: o escritor recusa depois do consumo (T6, T9) e o MESMO "sim" é reentregue com um payload forjado.

FALHAS = ["t6", "t9", "erro"]


def _consumida_sem_gravar(uid, monkeypatch, falha, *depois):
    """A 1ª entrega do "sim" consome a ação e a gravação não acontece: T6 (categoria interna), T9 (a consulta do banco
    cai no `preparar`) ou erro (exceção no escritor, rollback). `depois`: rodadas do modelo para a reentrega."""
    from core.services.ai_chat.system_prompt import ERROR_MSG
    ia(monkeypatch, banco(50, categoria="investimento") if falha == "t6" else banco(), *depois)
    wa(uid, ("gastei 50 no mercado #investimento no pix" if falha == "t6" else FRASE))
    payload, s1 = pendencia_ia(uid)["tool_args"]["_declaracao"], wamid()
    alvo, nome = (db, "has_open_finance_connections") if falha == "t9" else (db, "add_launch_and_update_balance")
    original = getattr(alvo, nome)

    def cai(*_a, **_k):
        raise RuntimeError("DB caiu")
    if falha != "t6":
        monkeypatch.setattr(alvo, nome, cai)
    esperado = {"t6": tx.INTERNA, "t9": tx.SEM_GARANTIA, "erro": ERROR_MSG}[falha]
    assert wa(uid, "sim", s1) == esperado
    monkeypatch.setattr(alvo, nome, original)
    assert declaracoes(uid) == []
    return payload, s1


@pytest.mark.parametrize("falha", FALHAS)
def test_m8_a1_o_mesmo_sim_com_payload_forjado_depois_da_falha_nao_grava(ana_ia, monkeypatch, falha):
    """Barreira 2 sozinha (a chamada direta pula o filtro): a tentativa que falhou encerrou a ação."""
    from core.handlers import declaracao_banco as decl
    from core.services.ai_chat.tools import declaracao_ia
    payload, s1 = _consumida_sem_gravar(ana_ia, monkeypatch, falha)
    forjado = {**payload, "valor": 5000.0, "alvo": "forjado", "categoria": "outros", "category_reason": "default"}
    with decl.turno("wa", s1):
        decl.registrar_mensagem(ana_ia, "sim")
        assert declaracao_ia.executar(ana_ia, {"_declaracao": forjado}) == tx.ACAO_ENCERRADA
    assert valores(ana_ia) == [] and [r["estado"] for r in livro(ana_ia) if r["papel"] == "acao"] == ["descartada"]


def test_m8_positivo_t9_e_o_gasto_mandado_de_novo_grava_uma_vez(ana_ia, monkeypatch):
    """§1 do CLAUDE.md: a ação que falhou não prende o usuário; o T9 manda mandar de novo, e isso grava uma vez."""
    w1 = wamid()
    ia(monkeypatch, banco())
    wa(ana_ia, FRASE, w1)
    def cai(_uid):
        raise RuntimeError("DB caiu")
    monkeypatch.setattr(db, "has_open_finance_connections", cai)
    assert wa(ana_ia, "sim") == tx.SEM_GARANTIA
    monkeypatch.undo()  # o banco volta (e a flag e a IA primeiro com ele: religadas abaixo)
    monkeypatch.setenv("DECLARACAO_BANCO", "1")
    monkeypatch.setenv("WA_IA_PRIMEIRO", "1")
    ia(monkeypatch, banco())
    assert wa(ana_ia, FRASE) == CONFIRMA
    assert "registrada pelo banco" in wa(ana_ia, "sim")
    assert valores(ana_ia) == [50.0] and wa(ana_ia, FRASE, w1) == T19


@pytest.mark.parametrize("falha", FALHAS)
def test_m8_a2_o_mesmo_sim_reentregue_com_o_modelo_reenviando_o_historico_nao_grava(ana_ia, monkeypatch, falha):
    """O caminho real do Tester: depois da falha, o MESMO "sim" chega de novo e o modelo reenvia o payload do histórico
    com R$ 5.000. Com o filtro, a chave "_" nem passa; sem ele (mutação F), a ação encerrada segura sozinha."""
    def reenvia(messages):
        for m in messages:
            if m.get("role") == "tool" and "_declaracao" in (m.get("content") or ""):
                p = json.loads(m["content"])["args"]["_declaracao"]
                return com_tools(chamada("add_launch", {"tipo": "despesa", "alvo": "x", "forma_pagamento": "dinheiro",
                                                        "_declaracao": {**p, "valor": 5000.0, "categoria": "outros",
                                                                        "category_reason": "default"}}, "c9"))
        return texto("nada")
    _, s1 = _consumida_sem_gravar(ana_ia, monkeypatch, falha, reenvia)
    wa(ana_ia, "sim", s1)
    assert valores(ana_ia) == []


# ── M8, rodada 4: o escritor só grava o payload congelado ao armar (`payload_hash`) ──────────────────────────────
# A janela do Tester r3: a ação fica `consumida` sem gravação (processo que morre, ou o banco caindo no escritor E no
# `descarta` do `finally`) e o MESMO "sim" volta com o payload do histórico trocado. O que fecha é o hash, não a
# tentativa única.

def _consumida_com_falha_dupla(uid, monkeypatch, *depois):
    from db import declaracao_livro as lv
    ia(monkeypatch, banco(), *depois)
    assert wa(uid, FRASE) == CONFIRMA
    payload, s1 = pendencia_ia(uid)["tool_args"]["_declaracao"], wamid()

    def cai(*_a, **_k):
        raise RuntimeError("DB caiu")
    o1, o2 = db.add_launch_and_update_balance, lv.descarta
    monkeypatch.setattr(db, "add_launch_and_update_balance", cai)
    monkeypatch.setattr(lv, "descarta", cai)
    wa(uid, "sim", s1)
    monkeypatch.setattr(db, "add_launch_and_update_balance", o1)
    monkeypatch.setattr(lv, "descarta", o2)
    assert [r["estado"] for r in livro(uid) if r["papel"] == "acao"] == ["consumida"]
    return payload, s1


def test_m8_hash_o_mesmo_sim_com_payload_trocado_e_acao_consumida_nao_grava(ana_ia, monkeypatch):
    """Barreira do hash isolada (a chamada direta pula o filtro, e a falha dupla pula a tentativa única)."""
    from core.handlers import declaracao_banco as decl
    from core.services.ai_chat.tools import declaracao_ia
    payload, s1 = _consumida_com_falha_dupla(ana_ia, monkeypatch)
    with decl.turno("wa", s1):
        decl.registrar_mensagem(ana_ia, "sim")
        r = declaracao_ia.executar(ana_ia, {"_declaracao": {**payload, "valor": 5000.0, "alvo": "forjado"}})
    assert r == tx.ACAO_ENCERRADA and valores(ana_ia) == []


def test_m8_hash_falha_dupla_e_o_mesmo_sim_com_o_modelo_reenviando_nao_grava(ana_ia, monkeypatch):
    """O caminho real (o 3º caso do Tester r3): sem o filtro (mutação F), só o hash segura."""
    def reenvia(messages):
        for m in messages:
            if m.get("role") == "tool" and "_declaracao" in (m.get("content") or ""):
                p = json.loads(m["content"])["args"]["_declaracao"]
                return com_tools(chamada("add_launch", {"tipo": "despesa", "alvo": "x", "forma_pagamento": "dinheiro",
                                                        "_declaracao": {**p, "valor": 5000.0, "alvo": "forjado"}}, "c9"))
        return texto("nada")
    _, s1 = _consumida_com_falha_dupla(ana_ia, monkeypatch, reenvia)
    wa(ana_ia, "sim", s1)
    assert valores(ana_ia) == []


@pytest.mark.parametrize("frase,args,valor", [
    ("gastei 12,90 no mercado no pix", {"valor": 12.9}, 12.9),
    ("paguei 1500 de aluguel no pix", {"valor": 1500, "alvo": "aluguel"}, 1500.0),
    ("gastei 50 na padaria são joão ontem no pix",
     {"valor": 50, "alvo": "padaria são joão", "nota": "pão de açúcar", "data": "ONTEM"}, 50.0),
])
def test_m8_hash_o_caminho_legitimo_nunca_da_falso_diferente(ana_ia, monkeypatch, frase, args, valor):
    """Positivo da canonicalização: o payload volta do jsonb da pendência e o hash bate (valor com e sem centavos,
    acento, data)."""
    if args.get("data") == "ONTEM":
        args = {**args, "data": (today_tz() - timedelta(days=1)).isoformat()}
    ia(monkeypatch, banco(**args))
    assert wa(ana_ia, frase).startswith("🐷 Só confirmando")
    assert "registrada pelo banco" in wa(ana_ia, "sim")
    assert valores(ana_ia) == [valor]


def test_tentativa_unica_o_payload_identico_depois_do_t9_nao_grava(ana_ia, monkeypatch):
    """§1 do CLAUDE.md: o usuário leu "não registrei, manda de novo"; o MESMO payload reaparecendo pelo mesmo "sim" não
    grava por baixo, senão o gasto mandado de novo dobra. Quem segura é a marca do turno (outro turno nunca grava) e,
    quando a gravação falhou sem derrubar o banco, também a tentativa única (a ação fica `descartada`)."""
    from core.handlers import declaracao_banco as decl
    from core.services.ai_chat.tools import declaracao_ia
    payload, s1 = _consumida_sem_gravar(ana_ia, monkeypatch, "t9")
    with decl.turno("wa", s1):
        decl.registrar_mensagem(ana_ia, "sim")
        assert declaracao_ia.executar(ana_ia, {"_declaracao": payload}) == tx.ACAO_ENCERRADA
    assert valores(ana_ia) == []


# ── L-b20: a IA não passa pelo leitor estrito; o roteador recusa a mesma frase ──────────────────────────────────────

FRASE_K = "gastei 1,5k no mercado pelo banco"


@pytest.mark.parametrize("valor, mostrado", [(1500, "R$ 1.500,00"), (1.5, "R$ 1,50")])
def test_l_b20_a_confirmacao_mostra_o_valor_do_modelo_e_o_sim_grava_ele(ana_ia, monkeypatch, valor, mostrado):
    """O que o modelo propõe é o que a confirmação mostra e o que o "sim" grava, uma vez. O modelo é FALSO: o teste fixa o
    valor proposto e não demonstra como o modelo real interpreta "1,5k"."""
    ia(monkeypatch, banco(valor))
    assert f"despesa de {mostrado} em mercado" in wa(ana_ia, FRASE_K, wamid())
    assert mostrado in wa(ana_ia, "sim", wamid())
    assert valores(ana_ia) == [float(valor)]


def test_l_b20_o_roteador_do_whatsapp_recusa_a_mesma_frase_com_t10(ana, liga):  # noqa: F811
    assert manda_wamid(ana, FRASE_K) == [tx.valor_abreviado("1,5k")]
    assert declaracoes(ana) == [] and n_lancamentos(ana) == 0
