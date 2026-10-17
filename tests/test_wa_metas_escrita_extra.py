"""PR C4, passada do Tester: o que a regra `metas.escrita` NÃO pode pegar (MÉDIO 2 e N43), o desejo sem verbo (MÉDIO 3),
as cinco pendências de cartão (N44) e o aviso da pergunta cancelada. K = chave `dashboard_v2_enabled`; N = sem ela.
O pytest NÃO fala com o modelo (falso): prova roteamento, estado e texto.
Controles (injetados e restaurados): sem os termos novos de `_OUTRO_ASSUNTO` -> `test_outro_assunto_*`; sem `_DESEJO_META` ->
`test_desejo_*`; `_ESCRITA_META` aceitando o passado ("criei") -> `test_narracao_*`; `metas.escrita` fora do
`_ABANDONA_CREDITO` -> `test_cartao_*`; veredito sempre calado -> `test_aviso_*`.
"""
from __future__ import annotations

import pytest

import core.intent_classifier as ic
import core.intent_router as ir
import db
from conftest import usuario_pagante
from core.intent_classifier import IntentResult, classify
from tests._ia_falsa_helpers import chamada, com_tools, openai_falso, texto
from tests._pendencia_credito_helpers import AS_CINCO, AVISO, diga as diga_cartao, escrituras, novo_uid
from tests.test_ai_chat_metas import nova
from tests.test_ai_chat_metas_escrita import linhas
from tests.test_wa_minhas_metas import chave, diga, sem_pendencia, tier3  # noqa: F401  (fixtures)
from tests.test_wa_minhas_metas_extra import com_caixinha, saldo_de

# O que a `main` (5d8ca2ba, `allow_ai=False`) dizia e tem de continuar dizendo para K e N: lançamento, fatura, depósito,
# resgate e relatório que só CITAM uma meta não são escrita de meta.
OUTRO_ASSUNTO = {
    "faz o pagamento da fatura meta": ("credit.handle", 0.95), "registra um depósito de 100 na meta 2028": ("out_of_scope", 0.0),
    "faz um resgate de 100 da meta 2028": ("out_of_scope", 0.4), "registrei 100 de receita pra meta 2028": ("out_of_scope", 0.4),
    "apaga o lançamento 3 da meta": ("out_of_scope", 0.4), "faz um relatório das metas": ("out_of_scope", 0.4),
    "registra um pix de 100 na meta": ("out_of_scope", 0.0), "faz um pix pra meta": ("out_of_scope", 0.0),
}
# Narração, relato e negação (passado ou terceiro): nunca viram escrita de meta.
NARRACAO = {
    "minha amiga criou uma meta de 10 mil": ("out_of_scope", 0.0), "não quero criar meta nenhuma": ("out_of_scope", 0.0),
    "criei uma meta ontem": ("out_of_scope", 0.0), "apaguei a meta": ("out_of_scope", 0.0), "mudei a meta": ("out_of_scope", 0.0),
}
DESEJO = ["queria uma meta de 5000 pra viagem", "quero uma meta de 5000", "preciso de uma meta de 5000",
          "acho que preciso de uma meta de 5000", "gostaria de uma meta de 3000"]


def intent(frase, quem):
    r = classify(frase, user_id=quem, allow_ai=False)
    return r.intent, r.confidence


@pytest.mark.parametrize("frase,esperado", {**OUTRO_ASSUNTO, **NARRACAO}.items())
def test_outro_assunto_e_narracao_ficam_como_na_main_para_k_e_n(chave, frase, esperado):
    k, n = usuario_pagante(plan="pro_max"), usuario_pagante(plan="pro_max")
    chave(k)
    for quem in (None, n, k):
        assert intent(frase, quem) == esperado, (frase, quem)


def test_controle_sem_os_termos_novos_o_lancamento_vira_escrita(chave, monkeypatch):
    monkeypatch.setattr(ic, "_OUTRO_ASSUNTO", ic.re.compile(r"\b(?:caixinhas?|cartao|gastos?)\b"))
    k = usuario_pagante(plan="pro_max")
    chave(k)
    assert intent("registra um depósito de 100 na meta 2028", k) == ("metas.escrita", 1.0)


def test_controle_com_o_passado_a_narracao_vira_escrita(chave, monkeypatch):
    monkeypatch.setattr(ic, "_ESCRITA_META", ic.re.compile(r"^(?:cri\w*|apag\w*|mud\w*) "))
    k = usuario_pagante(plan="pro_max")
    chave(k)
    assert intent("criei uma meta ontem", k) == ("metas.escrita", 1.0)


@pytest.mark.parametrize("frase", DESEJO)
def test_desejo_sem_verbo_e_escrita_so_para_k(chave, frase):
    k, n = usuario_pagante(plan="pro_max"), usuario_pagante(plan="pro_max")
    chave(k)
    assert intent(frase, k) == ("metas.escrita", 1.0)
    assert intent(frase, n) == intent(frase, None) == ("out_of_scope", 0.0)


def test_desejo_flag_0_vai_a_ia_pergunta_e_o_sim_cria_a_meta_e_nao_a_caixinha(chave, tier3, monkeypatch):
    """Na `main` o tier 3 devolvia `pockets.create` 0.95 e criava a CAIXINHA "viagem" sem perguntar."""
    tier3["fala"] = IntentResult(intent="pockets.create", confidence=0.95, entities={"name": "viagem"})
    k = usuario_pagante(plan="pro_max")
    chave(k)
    openai_falso(monkeypatch, com_tools(chamada("create_meta", {"nome": "Viagem", "tipo": "meta", "alvo": 5000})))
    (r,) = diga(k, "queria uma meta de 5000 pra viagem")
    assert "Só confirmando" in r and linhas(k) == [] and db.list_pockets(k) == []
    openai_falso(monkeypatch)
    (r,) = diga(k, "sim")
    assert r.startswith('✅ Criei a meta "Viagem"') and len(linhas(k)) == 1 and db.list_pockets(k) == []


def test_controle_sem_o_desejo_o_tier3_cria_a_caixinha_como_na_main(chave, tier3, monkeypatch):
    monkeypatch.setattr(ic, "_DESEJO_META", ic.re.compile(r"^$"))
    tier3["fala"] = IntentResult(intent="pockets.create", confidence=0.95, entities={"name": "viagem"})
    k = usuario_pagante(plan="pro_max")
    chave(k)
    openai_falso(monkeypatch)
    diga(k, "queria uma meta de 5000 pra viagem")
    assert [p["name"] for p in db.list_pockets(k)] == ["viagem"] and linhas(k) == []


@pytest.mark.parametrize("pendencia,arma", AS_CINCO)
def test_cartao_pendencia_aberta_e_apagar_meta_abandona_com_aviso_e_nada_e_pago(chave, tier3, pendencia, arma):
    """N44: as cinco pendências de cartão aceitam QUALQUER texto; a frase de escrita de meta as abandona com o aviso."""
    k = novo_uid()
    arma(k)  # comprar no cartão à mão é recusado depois da chave: arma antes
    chave(k)
    nova(k, "Viagem A", "1000", "300")
    antes = escrituras(k)
    r = diga_cartao(k, "apaga a meta Viagem A")
    assert r.startswith("🔕 " + AVISO) and "Pra criar, mudar ou apagar meta" in r and sem_pendencia(k), (pendencia, r)
    depois = escrituras(k)
    assert [m["nome"] for m in linhas(k)] == ["Viagem A"]
    assert {t: v for t, v in depois.items() if t != "pending_actions"} == {t: v for t, v in antes.items() if t != "pending_actions"}


def test_controle_sem_metas_escrita_no_abandona_credito_a_pendencia_de_cartao_engole(chave, tier3, monkeypatch):
    monkeypatch.setattr(ir, "_ABANDONA_CREDITO", ir._ABANDONA_CREDITO - {"metas.escrita"})
    k = novo_uid()
    dict(AS_CINCO)["installment_pending"](k)
    chave(k)
    nova(k, "Viagem A", "1000", "300")
    r = diga_cartao(k, "apaga a meta Viagem A")
    assert AVISO not in r  # a pergunta do cartão tratou a frase como resposta


def test_aviso_a_pergunta_de_caixinha_cancelada_e_anunciada(chave, tier3, monkeypatch):
    k = com_caixinha(chave, "Casa")
    openai_falso(monkeypatch)
    r0, r1 = diga(k, "saquei 200", "apaga a meta Casa nova")
    assert r1 == "🔕 Cancelei a pergunta anterior.\n\n" + ir._ESCRITA_DE_META_MSG and saldo_de(k, "Casa") == 300.0


def test_aviso_so_aparece_quando_havia_pergunta(chave, tier3, monkeypatch):
    import core.services.plan_service as ps
    monkeypatch.setattr(ps, "ai_chat_allowed", lambda uid: False)
    k = usuario_pagante(plan="pro_max")
    chave(k)
    assert diga(k, "apaga a meta Casa nova") == [ir._ESCRITA_DE_META_MSG]
