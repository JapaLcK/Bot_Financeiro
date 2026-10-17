"""PR C4 (Metas): escrita de meta no WhatsApp. K = chave `dashboard_v2_enabled`, N = sem ela. Roteamento e
proteção são determinísticos; quem escreve é a IA (modelo FALSO, `tests/_ia_falsa_helpers.py`): o pytest NÃO
prova que o modelo separa "cria" de conversa (harness real). Conversas pelo `handle_incoming`, banco real.
Controles (injetados e restaurados): `_pede_escrita_de_meta` sempre False -> `test_k_*` e as conversas;
`metas.escrita` fora do `ABANDONA` -> `test_conversa_d_*` (caixinha cai de R$ 300 para R$ 100); sem
`or intent == "metas.escrita"` no 5b -> `test_conversa_b_*`; sem `_metas_ligadas` no gate -> `test_n_*`;
sem a checagem de nome repetido -> `test_conversa_b_repetir_*`.
"""
from __future__ import annotations

import pytest

import core.intent_router as ir
import db
from conftest import usuario_pagante
from core.intent_classifier import IntentResult, classify
from core.services.ai_chat.tools import metas_escrita as me
from tests._ia_falsa_helpers import chamada, com_tools, lancamentos, liga_flag, openai_falso, texto
from tests.test_ai_chat_metas_escrita import linhas
from tests.test_wa_minhas_metas import chave, com_meta, diga, sem_pendencia, tier3  # noqa: F401  (fixtures)
from tests.test_wa_minhas_metas_extra import com_caixinha, saldo_de
from tests.test_ai_chat_metas import nova

K_ESCRITA = [
    "apaga a meta Casa nova", "cria uma meta de 5000 pra viagem", "muda a meta da casa pra 6000",
    "aumenta a meta da viagem pra 8000", "deleta a meta 2028", "quero criar uma meta", "Excluir a meta Viagem!",
    "renomeia a meta viagem pra férias", "nova meta de 3000", "faz uma meta de 3000 pro curso",
    "registra uma meta de 2000", "pode fazer uma meta de 800?", "criar meta viagem", "me ajuda a criar uma meta",
]
# Fora da regra, para K e N, com o que a `main` (5d8ca2ba) dizia (`allow_ai=False`): outro assunto, depósito,
# saque, leitura, e as palavras que a regra deixa de fora de propósito.
FORA = {
    "excluir caixinha metas": ("pockets.delete", 0.95), "criar caixinha reserva": ("pockets.create", 0.95),
    "guardei 100 na meta 2028": ("out_of_scope", 0.0), "saquei 200 da reserva de emergência": ("funds.withdraw", 0.95),
    "meta de gastos 500 no mercado": ("out_of_scope", 0.4), "reserva": ("out_of_scope", 0.0),
    "criar caixinha meta 2028": ("pockets.create", 0.95), "minhas metas": ("out_of_scope", 0.0),
    "gastei 50 no mercado": ("launches.add", 0.95), "cria minha reserva de emergência": ("out_of_scope", 0.0),
    "e se eu criasse uma meta de 5000?": ("out_of_scope", 0.0),
}


def intent(frase, quem):
    r = classify(frase, user_id=quem, allow_ai=False)
    return r.intent, r.confidence


@pytest.mark.parametrize("frase", K_ESCRITA)
def test_k_frases_de_escrita_de_meta(chave, frase):
    k = usuario_pagante(plan="pro_max")
    chave(k)
    assert intent(frase, k) == ("metas.escrita", 1.0)


@pytest.mark.parametrize("frase", K_ESCRITA)
def test_n_as_mesmas_frases_dao_o_que_davam_antes(chave, frase):
    n = usuario_pagante(plan="pro_max")
    chave()
    sem = intent(frase, None)  # sem `user_id` a regra nem existe
    assert sem[0] != "metas.escrita" and intent(frase, n) == sem


@pytest.mark.parametrize("frase,esperado", FORA.items())
def test_o_resto_do_corpus_fica_como_na_main_para_k_e_n(chave, frase, esperado):
    k, n = usuario_pagante(plan="pro_max"), usuario_pagante(plan="pro_max")
    chave(k)
    for quem in (None, n, k):
        got = intent(frase, quem)
        if frase == "minhas metas" and quem == k:
            assert got == ("metas.list", 1.0)  # C2: leitura, não escrita
        else:
            assert got == esperado, (frase, quem)


def test_falha_da_chave_vira_o_comportamento_de_antes(chave, monkeypatch):
    from core.services import metas_conversa as mc
    k = usuario_pagante(plan="pro_max")
    chave(k)
    monkeypatch.setattr(mc, "metas_ligadas", lambda uid: (_ for _ in ()).throw(RuntimeError("banco fora")))
    assert intent("apaga a meta Casa nova", k) == ("out_of_scope", 0.0)


# ── a conversa: handle_incoming, banco real, modelo falso ─────────────────────────────────────────

def pend_ia(uid):
    return db.ai_get_pending_action(uid)


def test_conversa_a_apagar_pendente_e_gasto_em_seguida_abandona_a_pendencia(chave, tier3, monkeypatch):
    k = com_meta(chave, "Casa nova")
    openai_falso(monkeypatch, com_tools(chamada("delete_meta", {"nome": "Casa nova"})),
                 texto("🐷 Apagar a meta *Casa nova*? Responde *sim* ou *não*."))
    (r0,) = diga(k, "apaga a meta Casa nova")
    assert "Apagar a meta" in r0 and pend_ia(k)["tool_name"] == "delete_meta"
    openai_falso(monkeypatch, texto("🐷 Sim pra quê?"))
    r1, r2 = diga(k, "gastei 50 no mercado", "dinheiro")
    assert "dinheiro vivo ou" in r1 and pend_ia(k) is None  # a pendência da IA foi abandonada
    assert lancamentos(k) == [{"tipo": "despesa", "valor": 50.0}]
    diga(k, "sim")
    assert [m["nome"] for m in linhas(k)] == ["Casa nova"]  # o "sim" depois não apaga nada


def test_conversa_a_apagar_sim_e_nao_pelo_whatsapp(chave, tier3, monkeypatch):
    k = com_meta(chave, "Casa nova")
    pede = lambda: openai_falso(monkeypatch, com_tools(chamada("delete_meta", {"nome": "casa nova"})), texto("🐷 Confirma?"))  # noqa: E731
    pede()
    diga(k, "apaga a meta Casa nova")
    assert diga(k, "não") == ["👍 Beleza, não fiz nada."] and len(linhas(k)) == 1
    pede()
    diga(k, "apaga a meta Casa nova")
    assert diga(k, "sim") == ['✅ Meta "Casa nova" apagada. O dinheiro no banco não mudou.'] and linhas(k) == []


def criar_viagem(monkeypatch):
    return openai_falso(monkeypatch, com_tools(chamada("create_meta", {"nome": "Viagem", "tipo": "meta", "alvo": 5000})))


def test_conversa_b_cria_na_hora_lista_e_o_sim_solto_nao_cria_outra(chave, tier3, monkeypatch):
    k = usuario_pagante(plan="pro_max")
    chave(k)
    criar_viagem(monkeypatch)
    (r,) = diga(k, "cria uma meta de 5000 pra viagem")
    assert 'Criei a meta "Viagem": juntar R$ 5.000,00' in r and 'me diga "apaga a meta Viagem"' in r
    assert len(linhas(k)) == 1 and pend_ia(k) is None
    openai_falso(monkeypatch)  # a listagem é determinística: se for à IA, falha
    assert "Viagem" in diga(k, "minhas metas")[0]
    openai_falso(monkeypatch, texto("🐷 Sim pra quê?"))
    diga(k, "sim")
    assert len(linhas(k)) == 1


def test_conversa_b_repetir_a_mesma_mensagem_recusa_pelo_nome(chave, tier3, monkeypatch):
    k = usuario_pagante(plan="pro_max")
    chave(k)
    for _ in range(2):
        criar_viagem(monkeypatch)
        (r,) = diga(k, "cria uma meta de 5000 pra viagem")
    assert r.startswith('🐷 Você já tem a meta "Viagem" (alvo R$ 5.000,00).') and len(linhas(k)) == 1


def test_conversa_b_controle_sem_a_checagem_de_nome_a_repeticao_cria_duas(chave, tier3, monkeypatch):
    monkeypatch.setattr(me, "_iguais", lambda metas, nome: [])
    k = usuario_pagante(plan="pro_max")
    chave(k)
    for _ in range(2):
        criar_viagem(monkeypatch)
        diga(k, "cria uma meta de 5000 pra viagem")
    assert len(linhas(k)) == 2


def test_conversa_d_caixinha_casa_pendente_e_apagar_a_meta_casa_nova_nao_move_dinheiro(chave, tier3, monkeypatch):
    k = com_caixinha(chave, "Casa")
    nova(k, "Casa nova", "5000", "300")
    openai_falso(monkeypatch)  # com pendência determinística aberta a IA nem é chamada
    r0, r1 = diga(k, "saquei 200", "apaga a meta Casa nova")
    assert "retirar de qual" in r0 and "Pra criar, mudar ou apagar meta" in r1 and sem_pendencia(k)
    assert saldo_de(k, "Casa") == 300.0 and [m["nome"] for m in linhas(k)] == ["Casa nova"]


def test_conversa_d_cria_uma_meta_pra_casa_com_pendencia_nao_cria_nem_move(chave, tier3, monkeypatch):
    k = com_caixinha(chave, "Casa")
    openai_falso(monkeypatch)
    r0, r1 = diga(k, "saquei 200", "cria uma meta de 5000 pra casa")
    assert "Pra criar, mudar ou apagar meta" in r1 and saldo_de(k, "Casa") == 300.0 and linhas(k) == []


def test_conversa_d_faz_e_registra_uma_meta_pra_casa_tambem_abandonam_sem_sacar(chave, tier3, monkeypatch):
    k = com_caixinha(chave, "Casa")
    openai_falso(monkeypatch)
    for frase in ("faz uma meta de 5000 pra casa", "registra uma meta pra casa"):
        r0, r1 = diga(k, "saquei 200", frase)
        assert "Pra criar, mudar ou apagar meta" in r1 and saldo_de(k, "Casa") == 300.0, frase
    assert linhas(k) == []


def test_conversa_d_controle_sem_o_abandono_a_contencao_saca_da_caixinha(chave, tier3, monkeypatch):
    monkeypatch.setattr(ir, "ABANDONA", ir.ABANDONA - {"metas.escrita"})
    k = com_caixinha(chave, "Casa")
    nova(k, "Casa nova", "5000", "300")
    openai_falso(monkeypatch)
    diga(k, "saquei 200", "apaga a meta Casa nova")
    assert saldo_de(k, "Casa") == 100.0  # é o saque que o `ABANDONA` impede


def test_conversa_d_positivo_o_nome_exato_ainda_resolve_o_saque(chave, tier3, monkeypatch):
    k = com_caixinha(chave, "Casa")
    nova(k, "Casa nova", "5000", "300")
    diga(k, "saquei 200", "Casa")
    assert saldo_de(k, "Casa") == 100.0


def test_conversa_g_sem_ia_vale_o_texto_fixo_e_nada_e_tocado(chave, tier3, monkeypatch):
    import core.services.plan_service as ps
    monkeypatch.setattr(ps, "ai_chat_allowed", lambda uid: False)
    k = com_caixinha(chave, "Casa")
    nova(k, "Casa nova", "5000", "300")
    openai_falso(monkeypatch)
    (r,) = diga(k, "apaga a meta Casa nova")
    assert r == ir._ESCRITA_DE_META_MSG and saldo_de(k, "Casa") == 300.0 and len(linhas(k)) == 1


def test_conversa_h_com_a_flag_a_ia_cria_uma_e_o_roteador_nao_roda_depois(chave, tier3, monkeypatch):
    liga_flag(monkeypatch)
    k = usuario_pagante(plan="pro_max")
    chave(k)
    criar_viagem(monkeypatch)
    (r,) = diga(k, "cria uma meta de 5000 pra viagem")
    assert r.startswith('✅ Criei a meta "Viagem"') and ir._ESCRITA_DE_META_MSG not in r and len(linhas(k)) == 1


def test_conversa_h_pendencia_velha_da_ia_e_abandonada_e_o_sim_depois_nao_executa_a_velha(chave, tier3, monkeypatch):
    liga_flag(monkeypatch)
    k = com_meta(chave, "Casa nova")
    assert db.ai_set_pending_action(k, "delete_meta", {"nome": "Casa nova"}, 'apagar a meta "Casa nova"')
    criar_viagem(monkeypatch)
    (r,) = diga(k, "cria uma meta de 5000 pra viagem")
    assert r.startswith("✅ Criei a meta") and pend_ia(k) is None
    openai_falso(monkeypatch, texto("🐷 Sim pra quê?"))
    diga(k, "sim")
    assert sorted(m["nome"] for m in linhas(k)) == ["Casa nova", "Viagem"]


def test_n_com_a_mesma_frase_nunca_cria_nem_arma(chave, tier3, monkeypatch):
    """N imitando o histórico: o modelo emite `create_meta`; o gate do `execute` recusa e nada é gravado."""
    n = usuario_pagante(plan="pro_max")
    chave()
    openai_falso(monkeypatch, com_tools(chamada("create_meta", {"nome": "Viagem", "tipo": "meta", "alvo": 5000})))
    (r,) = diga(n, "cria uma meta de 5000 pra viagem")
    assert me.SEM_METAS in r and linhas(n) == [] and pend_ia(n) is None


def test_n_se_o_tier3_inventar_metas_escrita_vale_nao_entendi_e_nada_e_tocado(chave, tier3, monkeypatch):
    import core.services.plan_service as ps
    monkeypatch.setattr(ps, "ai_chat_allowed", lambda uid: False)
    n = usuario_pagante(plan="pro_max")
    chave()
    tier3["fala"] = IntentResult(intent="metas.escrita", confidence=1.0)
    (r,) = diga(n, "faz aquilo da viagem")
    assert r == ir.NOT_UNDERSTOOD_MSG and linhas(n) == []
