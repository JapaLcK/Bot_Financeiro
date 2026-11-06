"""PR-B (b) (ADR 0006): a declaração "pelo banco" pelo chat IA no WhatsApp, pelo `handle_incoming` com banco real.

P1 (IA primeiro, `WA_IA_PRIMEIRO`), P2 (prefixo "piggy", sem IA primeiro) e P4 (áudio). O "sim" grava; a reentrega se
vincula à ação pelo wamid (nunca pelo conteúdo) e é respondida antes da IA: `len(ia.creates)` mede que o modelo não foi
chamado. Controles negativos (mutação numa cópia, tabela em `.time-dev/prb-chat/`): sem o passo 1 da `reentrega` a IA é
chamada de novo (T1, T2, T11); sem o passo 2 o "sim" velho consome a pendência nova (T4, T10); sem o registro do "sim" no
`handle_incoming` (T13) ou depois da transcrição (T14) a mensagem já vista parece nova e grava.
"""
from __future__ import annotations

import threading

import db
from core.handlers import declaracao_banco_textos as tx
from tests._chat_ia_apoio import (CONFIRMA, FRASE, T19, ana_ia, banco, carteira, declaracoes, ia, livro,  # noqa: F401
                                  pendencia_ia, q, t3, uid_pro, valores, wa, wamid)
from tests._declaracao_b import fala
from tests._ia_falsa_helpers import desliga_flag, texto


def _arma(uid, monkeypatch, *rodadas, frase=FRASE):
    reg = ia(monkeypatch, *(rodadas or (banco(),)))
    w = wamid()
    assert wa(uid, frase, w) == CONFIRMA
    return reg, w


def test_t1_reentrega_antes_do_sim_repete_a_confirmacao_sem_ia(ana_ia, monkeypatch):
    reg, w1 = _arma(ana_ia, monkeypatch)
    antes = pendencia_ia(ana_ia)["created_at"]
    assert wa(ana_ia, FRASE, w1) == CONFIRMA
    assert len(reg.creates) == 1 and pendencia_ia(ana_ia)["created_at"] == antes
    assert "registrada pelo banco" in wa(ana_ia, "sim")
    assert valores(ana_ia) == [50.0] and carteira(ana_ia) == 200.0


def test_t2_reentrega_depois_do_sim_e_t3(ana_ia, monkeypatch):
    reg, w1 = _arma(ana_ia, monkeypatch)
    wa(ana_ia, "sim")
    assert wa(ana_ia, FRASE, w1) == t3(1)
    assert valores(ana_ia) == [50.0] and len(reg.creates) == 1 and pendencia_ia(ana_ia) is None


def test_t3_o_mesmo_sim_em_duas_threads_grava_uma_vez(ana_ia, monkeypatch):
    _arma(ana_ia, monkeypatch)
    s1, barreira, saidas = wamid(), threading.Barrier(2), []

    def manda():
        barreira.wait()
        saidas.append(wa(ana_ia, "sim", s1))
    ts = [threading.Thread(target=manda) for _ in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert valores(ana_ia) == [50.0], saidas
    assert sum("registrada pelo banco" in s for s in saidas) == 1, saidas


def test_t3b_dois_sim_diferentes_em_duas_threads_gravam_uma_vez(ana_ia, monkeypatch):
    _arma(ana_ia, monkeypatch)
    barreira, saidas = threading.Barrier(2), []

    def manda():
        barreira.wait()
        saidas.append(wa(ana_ia, "sim"))
    ts = [threading.Thread(target=manda) for _ in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert valores(ana_ia) == [50.0], saidas
    assert sum("registrada pelo banco" in s for s in saidas) == 1, saidas


def test_t4_sim_repetido_com_pendencia_nova_e_t3_e_nao_a_consome(ana_ia, monkeypatch):
    reg, _ = _arma(ana_ia, monkeypatch, banco(), banco(30, alvo="padaria"))
    s1 = wamid()
    wa(ana_ia, "sim", s1)
    assert wa(ana_ia, "gastei 30 na padaria no pix") == CONFIRMA.replace("R$ 50,00 em mercado", "R$ 30,00 em padaria")
    nova = pendencia_ia(ana_ia)["created_at"]
    assert wa(ana_ia, "sim", s1) == t3(1)
    assert pendencia_ia(ana_ia)["created_at"] == nova and valores(ana_ia) == [50.0] and len(reg.creates) == 2
    assert "registrada pelo banco" in wa(ana_ia, "sim")  # o "sim" novo confirma a nova
    assert valores(ana_ia) == [50.0, 30.0]


def test_t6_duas_acoes_com_o_mesmo_texto_gravam_duas(ana_ia, monkeypatch):
    ia(monkeypatch, banco(), banco())
    for _ in range(2):
        assert wa(ana_ia, FRASE) == CONFIRMA
        assert "registrada pelo banco" in wa(ana_ia, "sim")
    assert valores(ana_ia) == [50.0, 50.0] and carteira(ana_ia) == 200.0


def test_t7_cartao_e_t8_antes_da_confirmacao(ana_ia, monkeypatch):
    desliga_flag(monkeypatch)  # P2: o prefixo leva à IA o que o roteador trataria como compra no cartão
    reg = ia(monkeypatch, banco())
    r = wa(ana_ia, "piggy gastei 50 no mercado no cartão")
    assert r.startswith(tx.CARTAO), r
    assert pendencia_ia(ana_ia) is None and livro(ana_ia) == [] and declaracoes(ana_ia) == [] and len(reg.creates) == 1


def test_t8_sem_wamid_t18_e_o_sim_sem_id_nao_consome(ana_ia, monkeypatch):
    reg = ia(monkeypatch, banco(), banco())
    assert wa(ana_ia, FRASE, "1") == tx.SEM_ID  # o harness e o simulador: sem wamid
    assert pendencia_ia(ana_ia) is None and livro(ana_ia) == []
    assert wa(ana_ia, FRASE) == CONFIRMA
    antes = pendencia_ia(ana_ia)["created_at"]
    assert wa(ana_ia, "sim", "1") == tx.SEM_ID
    assert pendencia_ia(ana_ia)["created_at"] == antes and declaracoes(ana_ia) == []
    assert "registrada pelo banco" in wa(ana_ia, "sim") and len(reg.creates) == 2


def test_t10_declaracao_apagada_e_sim_velho_com_outra_pendencia_viva(ana_ia, monkeypatch):
    """(a) do dono: o livro sobrevive ao apagar; o "sim" velho reentregue responde T3 e não toca a pendência nova."""
    reg, _ = _arma(ana_ia, monkeypatch, banco(), banco(30, alvo="padaria"))
    s1 = wamid()
    wa(ana_ia, "sim", s1)
    (d,) = declaracoes(ana_ia)
    assert db.delete_launch_and_rollback(ana_ia, d["id"]) is None  # None = apagou
    assert declaracoes(ana_ia) == []
    wa(ana_ia, "gastei 30 na padaria no pix")
    viva = pendencia_ia(ana_ia)["created_at"]
    assert wa(ana_ia, "sim", s1) == t3(1)
    assert pendencia_ia(ana_ia)["created_at"] == viva and declaracoes(ana_ia) == [] and len(reg.creates) == 2
    assert [(r["papel"], r["estado"], r["ligado"], r["user_seq"]) for r in livro(ana_ia)] == [
        ("acao", "confirmada", False, 1), ("sim", None, True, None), ("acao", "armada", False, None)]


def test_t11_reentrega_depois_do_nao_e_t19(ana_ia, monkeypatch):
    """(b) do dono: depois do "não" a mensagem original reentregue não arma nada nem chama a IA."""
    reg, w1 = _arma(ana_ia, monkeypatch)
    assert wa(ana_ia, "não") == "👍 Beleza, não fiz nada."
    assert wa(ana_ia, FRASE, w1) == T19
    assert pendencia_ia(ana_ia) is None and declaracoes(ana_ia) == [] and len(reg.creates) == 1


def test_t11_reentrega_depois_de_outro_assunto_e_t19(ana_ia, monkeypatch):
    reg, w1 = _arma(ana_ia, monkeypatch, banco(), texto("Seu saldo é R$ 200,00."))
    wa(ana_ia, "saldo")  # a guarda anti-sequestro abandona a pendência; a IA primeiro responde o saldo
    assert pendencia_ia(ana_ia) is None
    assert wa(ana_ia, FRASE, w1) == T19
    assert pendencia_ia(ana_ia) is None and declaracoes(ana_ia) == [] and len(reg.creates) == 2


def test_t11_reentrega_depois_dos_10_minutos_e_t19(ana_ia, monkeypatch):
    reg, w1 = _arma(ana_ia, monkeypatch)
    q("update ai_pending_actions set created_at = now() - interval '11 minutes' where user_id=%s returning 1", (ana_ia,))
    assert wa(ana_ia, FRASE, w1) == T19
    assert pendencia_ia(ana_ia) is None and declaracoes(ana_ia) == [] and len(reg.creates) == 1


def test_t13_sim_que_respondeu_o_roteador_nao_confirma_a_declaracao(ana_ia, monkeypatch):
    db.set_pending_action(ana_ia, "confirm_media_launch", {  # o recibo lido por OCR: o "sim" é do roteador
        "tipo": "despesa", "valor": 20.0, "alvo": "farmacia", "categoria": "saude", "data": None,
        "platform": "whatsapp", "text": "gastei 20,00 farmacia"})
    s0 = wamid()
    wa(ana_ia, "sim", s0)
    assert "registrada" in wa(ana_ia, "dinheiro") and carteira(ana_ia) == 180.0
    reg, _ = _arma(ana_ia, monkeypatch)
    viva = pendencia_ia(ana_ia)["created_at"]
    assert wa(ana_ia, "sim", s0) == tx.SIM_JA_RECEBIDO  # rodadas 7 e 8: o "sim" visto não consome a viva
    assert pendencia_ia(ana_ia)["created_at"] == viva and declaracoes(ana_ia) == [] and len(reg.creates) == 1


def test_t14_sim_falado_reentregue_nao_confirma(ana_ia, monkeypatch):
    """O áudio "sim" visto pelo roteador e reentregue quando a pergunta da IA está aberta (iria à IA)."""
    s0 = wamid()
    fala(monkeypatch, ana_ia, "sim", s0)
    reg, _ = _arma(ana_ia, monkeypatch)
    db.ai_append_message(ana_ia, "assistant", "Quer ver o extrato também?")  # pergunta aberta: o áudio vai à IA
    viva = pendencia_ia(ana_ia)["created_at"]
    assert tx.SIM_JA_RECEBIDO in "\n".join(fala(monkeypatch, ana_ia, "sim", s0))  # rodadas 7 e 8
    assert pendencia_ia(ana_ia)["created_at"] == viva and declaracoes(ana_ia) == [] and len(reg.creates) == 1


def test_t15_dois_assuntos_o_sim_nao_grava_o_mercado(ana_ia, monkeypatch):
    """CLAUDE.md §3: a IA arma o mercado (P2), a padaria vai ao roteador (abandona) e o "sim" não grava o mercado."""
    desliga_flag(monkeypatch)
    reg = ia(monkeypatch, banco())
    w1 = wamid()
    assert wa(ana_ia, "piggy " + FRASE, w1) == CONFIRMA
    assert "registrada pelo banco" in wa(ana_ia, "gastei 30 na padaria no pix")
    wa(ana_ia, "sim")
    assert valores(ana_ia) == [30.0] and len(reg.creates) == 1
    assert wa(ana_ia, "piggy " + FRASE, w1) == T19


def test_p4_audio_que_arma_e_reentregue_repete_a_confirmacao(ana_ia, monkeypatch):
    reg = ia(monkeypatch, banco())
    db.ai_append_message(ana_ia, "assistant", "Quanto você gastou e como pagou?")  # o áudio responde à IA
    a1 = wamid()
    assert CONFIRMA in "\n".join(fala(monkeypatch, ana_ia, FRASE, a1))
    assert CONFIRMA in "\n".join(fala(monkeypatch, ana_ia, FRASE, a1))
    assert len(reg.creates) == 1
    assert "registrada pelo banco" in wa(ana_ia, "sim") and valores(ana_ia) == [50.0]
    assert t3(1) in "\n".join(fala(monkeypatch, ana_ia, FRASE, a1))


def test_m21_reentrega_da_abandonada_com_outra_declaracao_viva_e_t19(ana_ia, monkeypatch):
    """Passo 1: "armada e viva" é a pendência DESSA ação. W1 abandonada por W2: a reentrega de W1 dá T19, não a
    confirmação de W2 (que um "sim" depois confirmaria)."""
    reg, w1 = _arma(ana_ia, monkeypatch, banco(), banco(30, alvo="padaria"))
    import core.intent_classifier as ic  # a frase nova vai à IA (o tier 3 do classificador também é LLM)
    monkeypatch.setattr(ic, "_classify_with_ai", lambda *a, **k: ic.IntentResult(intent="out_of_scope", confidence=0.0))
    wa(ana_ia, "na verdade foi 30 na padaria no pix")
    viva = pendencia_ia(ana_ia)
    assert viva["tool_args"]["_declaracao"]["alvo"] == "padaria"
    assert wa(ana_ia, FRASE, w1) == T19
    assert pendencia_ia(ana_ia)["created_at"] == viva["created_at"] and len(reg.creates) == 2


def test_4a_nao_reentregue_nao_cancela_a_pendencia_nova(ana_ia, monkeypatch):
    """O "não" segue a regra do "sim": só uma mensagem vista agora pela 1ª vez consome a declaração."""
    _arma(ana_ia, monkeypatch, banco(), banco(30, alvo="padaria"))
    n1 = wamid()
    assert wa(ana_ia, "não", n1) == "👍 Beleza, não fiz nada."
    wa(ana_ia, "gastei 30 na padaria no pix")
    viva = pendencia_ia(ana_ia)
    assert wa(ana_ia, "não", n1) == CONFIRMA.replace("R$ 50,00 em mercado", "R$ 30,00 em padaria")
    assert pendencia_ia(ana_ia)["created_at"] == viva["created_at"]
    assert "registrada pelo banco" in wa(ana_ia, "sim") and valores(ana_ia) == [30.0]
