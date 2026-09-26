"""Q40 — com banco conectado, lançamento manual é só dinheiro vivo.

Conversa inteira pelo `handle_incoming` (`manda()`), com estado real no banco:
o usuário tem Open Finance conectado (`_connect_fake_bank`), salvo nos casos
"sem OF", que provam a decisão A2 do dono (sem banco, tudo como antes).

Fonte da regra: `core/handlers/forma_pagamento.py`. Os números dos testes
seguem o critério de pronto do plano (A1–A14).
"""
from __future__ import annotations

import threading
import uuid

import pytest

import db
from tests._fusao_of_helpers import ia_fora, manda, uid_pro  # noqa: F401 (fixtures)
from tests.test_manual_launches_carteira_piggy import _connect_fake_bank, _importa_of_tx
from conftest import promote_to_pro
from utils_date import today_tz


def _q(sql, params):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        conn.commit()
    return row


def manuais(uid) -> int:
    return _q("select count(*) n from launches where user_id=%s "
              "and coalesce(source,'manual') <> 'open_finance'", (uid,))["n"]


def compras_credito(uid) -> int:
    return _q("select count(*) n from credit_transactions where user_id=%s", (uid,))["n"]


def carteira(uid) -> float:
    return float(db.get_consolidated_balance(uid)["manual"] or 0)


def pendencia(uid):
    p = db.get_pending_action(uid)
    return p["action_type"] if p else None


@pytest.fixture
def com_of(uid_pro):
    _connect_fake_bank(uid_pro)
    return uid_pro


@pytest.fixture
def sem_of():
    uid = int(uuid.uuid4().int % 1_000_000_000)
    db.ensure_user(uid)
    return promote_to_pro(uid)


# ── A1–A3: a pergunta, "pix" e "dinheiro" ───────────────────────────────────

def test_a1_gasto_sem_forma_pergunta_e_nao_grava(com_of, ia_fora):
    r = manda(com_of, "gastei 500")
    assert "dinheiro vivo" in r and "banco" in r, r
    assert manuais(com_of) == 0
    assert pendencia(com_of) == "payment_method_choice"


def test_a2_pix_nao_grava_e_diz_que_chega_pelo_open_finance(com_of, ia_fora):
    manda(com_of, "gastei 500 no mercado")
    r = manda(com_of, "pix")
    assert manuais(com_of) == 0
    assert "Open Finance" in r and "Não registrei" in r, r
    assert pendencia(com_of) is None


def test_a2_pix_lista_a_transacao_ja_importada(uid_pro, ia_fora):
    _importa_of_tx(uid_pro, today_tz(), "500.00", "PIX FULANO", f"tx-q40-{uid_pro}")
    antes = manuais(uid_pro)
    manda(uid_pro, "gastei 500 no mercado")
    r = manda(uid_pro, "pix")
    assert manuais(uid_pro) == antes
    assert "R$ 500,00" in r and "extrato" in r, r


def test_a3_dinheiro_pergunta_descricao_e_grava_uma_vez(com_of, ia_fora):
    antes = carteira(com_of)
    manda(com_of, "gastei 500")
    r = manda(com_of, "dinheiro")
    assert "Em que você gastou" in r, r
    assert manuais(com_of) == 0
    r = manda(com_of, "mercado")
    assert "registrada" in r, r
    assert manuais(com_of) == 1
    assert carteira(com_of) == pytest.approx(antes - 500)


# ── A4–A5: crédito × débito (Q2b) ───────────────────────────────────────────

def test_a4_cartao_sem_cartao_manual_nao_grava(com_of, ia_fora):
    r = manda(com_of, "gastei 50 no mercado no cartão")
    assert manuais(com_of) == 0 and compras_credito(com_of) == 0
    assert pendencia(com_of) is None
    assert "Open Finance" in r, r


def test_a4_cartao_manual_padrao_vai_para_a_fatura(com_of, ia_fora):
    card = db.create_card(com_of, "Manual", closing_day=10, due_day=17)
    db.set_default_card(com_of, card)
    antes = carteira(com_of)
    manda(com_of, "gastei 50 no mercado no cartão")
    assert compras_credito(com_of) == 1
    assert manuais(com_of) == 0
    assert carteira(com_of) == pytest.approx(antes)


def test_a5_cartao_de_debito_com_of_nao_vira_credito(com_of, ia_fora):
    card = db.create_card(com_of, "Manual", closing_day=10, due_day=17)
    db.set_default_card(com_of, card)
    r = manda(com_of, "gastei 50 no cartão de débito")
    assert compras_credito(com_of) == 0 and manuais(com_of) == 0
    assert "Open Finance" in r, r


def test_a5_cartao_de_debito_sem_of_vai_para_a_carteira(sem_of, ia_fora):
    card = db.create_card(sem_of, "Manual", closing_day=10, due_day=17)
    db.set_default_card(sem_of, card)
    manda(sem_of, "gastei 50 no mercado no cartão de débito")
    assert compras_credito(sem_of) == 0
    assert manuais(sem_of) == 1


# ── A6: "em dinheiro" grava e sai do alvo ───────────────────────────────────

def test_a6_em_dinheiro_grava_com_o_mesmo_alvo_e_categoria(com_of, ia_fora, sem_of):
    manda(com_of, "gastei 50 no mercado em dinheiro")
    manda(sem_of, "gastei 50 no mercado")
    com = _q("select alvo, categoria from launches where user_id=%s", (com_of,))
    ref = _q("select alvo, categoria from launches where user_id=%s", (sem_of,))
    assert manuais(com_of) == 1
    assert com["alvo"] == "mercado" and com["categoria"] == ref["categoria"], (com, ref)


# ── A7–A8: respostas que não são forma ──────────────────────────────────────

@pytest.mark.parametrize("resposta", ["cancela", "não"])
def test_a7_cancelar_apaga_e_nao_grava(com_of, ia_fora, resposta):
    # "cancelar" exato é comando de assinatura (`billing_commands`), que roda
    # antes do route() com QUALQUER pendência determinística de pé — defeito
    # anterior a este PR, vale para toda pergunta do bot. Por isso a pergunta
    # repetida sugere "cancela".
    manda(com_of, "gastei 500 no mercado")
    r = manda(com_of, resposta)
    assert "não registrei" in r.lower(), r
    assert manuais(com_of) == 0 and pendencia(com_of) is None


@pytest.mark.parametrize("resposta", ["sim", "hmm"])
def test_a7_sim_e_hmm_mantem_a_pergunta(com_of, ia_fora, resposta):
    manda(com_of, "gastei 500 no mercado")
    r = manda(com_of, resposta)
    assert "dinheiro" in r and "banco" in r, r
    assert manuais(com_of) == 0
    assert pendencia(com_of) == "payment_method_choice"
    assert not ia_fora


def test_a8_segundo_gasto_cancela_com_aviso_e_pergunta_o_novo(com_of, ia_fora):
    manda(com_of, "gastei 500 no mercado")
    r = manda(com_of, "gastei 30 no uber")
    assert "Cancelei a pergunta anterior" in r, r
    assert "R$ 30,00" in r, r
    assert manuais(com_of) == 0
    p = db.get_pending_action(com_of)
    assert p["action_type"] == "payment_method_choice"
    assert p["payload"]["text"] == "gastei 30 no uber"


def test_a8_saldo_cancela_com_aviso(com_of, ia_fora):
    manda(com_of, "gastei 500 no mercado")
    r = manda(com_of, "saldo")
    assert "Cancelei a pergunta anterior" in r, r
    assert manuais(com_of) == 0 and pendencia(com_of) is None


# ── A9: estado deixado por outro fluxo ──────────────────────────────────────

def test_a9_oferta_de_recategorizar_e_desalojada(com_of, ia_fora):
    manda(com_of, "gastei 20 no mercado em dinheiro")
    assert pendencia(com_of) == "recategorize_launch_offer"
    manda(com_of, "gastei 500 no mercado")
    assert pendencia(com_of) == "payment_method_choice"
    assert manuais(com_of) == 1


def test_a9_outra_pergunta_viva_ganha_e_nada_e_gravado(com_of, ia_fora):
    # Uma pergunta que o route() não consome com "gastei 500 no mercado"
    # (divergência do plano: a `clarification` é resolvida/abandonada antes de
    # chegar ao add(); a oferta de gasto fixo é pergunta e fica de pé).
    db.set_pending_action(com_of, "confirm_recurring_offer", {
        "name": "Netflix", "amount": 44.9, "category": "assinaturas",
        "due_day": 5, "merchant_key": "netflix", "question": "Vira gasto fixo?"})
    r = manda(com_of, "gastei 500 no mercado")
    assert "outra pergunta" in r, r
    assert manuais(com_of) == 0
    assert pendencia(com_of) == "confirm_recurring_offer"


def test_a9_pergunta_de_valor_de_conta_e_abandonada(com_of, ia_fora):
    db.set_pending_action(com_of, "bill_amount_expected", {"bill_id": 1, "bill_name": "Luz"})
    r = manda(com_of, "gastei 500 no mercado")
    assert "dinheiro vivo" in r, r
    assert pendencia(com_of) == "payment_method_choice"
    assert manuais(com_of) == 0


# ── A10–A11: multi-lançamento e receita ─────────────────────────────────────

def test_a10_multi_uma_pergunta_so_e_dinheiro_grava_os_dois(com_of, ia_fora):
    r = manda(com_of, "gastei 300 no ifood e 150 na farmácia")
    assert r.count("dinheiro vivo") == 1, r
    assert manuais(com_of) == 0
    manda(com_of, "dinheiro")
    assert manuais(com_of) == 2


def test_a10_multi_pix_nao_grava(com_of, ia_fora):
    manda(com_of, "gastei 300 no ifood e 150 na farmácia")
    r = manda(com_of, "pix")
    assert manuais(com_of) == 0
    assert "Open Finance" in r, r


def test_a10_misto_nao_grava(com_of, ia_fora):
    r = manda(com_of, "gastei 300 no ifood em dinheiro e 150 na farmácia no pix")
    assert manuais(com_of) == 0
    assert "separado" in r, r
    assert pendencia(com_of) is None


def test_a11_receita_pix_nao_grava_e_dinheiro_grava(com_of, ia_fora):
    r = manda(com_of, "recebi 500")
    assert "chegaram" in r, r
    manda(com_of, "pix")
    assert manuais(com_of) == 0
    manda(com_of, "recebi 500")
    r = manda(com_of, "dinheiro")
    assert "Em que você recebeu" in r, r
    manda(com_of, "freela")
    row = _q("select tipo, valor from launches where user_id=%s", (com_of,))
    assert manuais(com_of) == 1 and row["tipo"] == "receita"


# ── A12: o que o usuário digita de verdade ──────────────────────────────────

@pytest.mark.parametrize("resposta,grava", [
    ("foi no pix", False), ("PIX!", False), ("no débito", False),
    ("cartão de crédito", False), ("banco", False),
    ("em espécie", True), ("especie", True), ("dinheiro vivo", True), ("vivo", True),
])
def test_a12_respostas_reais(com_of, ia_fora, resposta, grava):
    manda(com_of, "gastei 500 no mercado")
    r = manda(com_of, resposta)
    assert manuais(com_of) == (1 if grava else 0), r
    assert pendencia(com_of) != "payment_method_choice", r
    assert not ia_fora


# ── A13: sem OF, tudo como antes (positivo da A2) ───────────────────────────

def test_a13_sem_of_pergunta_descricao_e_nao_a_forma(sem_of, ia_fora):
    r = manda(sem_of, "gastei 500")
    assert "Em que você gastou" in r, r
    assert "banco" not in r
    assert pendencia(sem_of) == "clarification"


def test_a13_sem_of_pix_grava_na_carteira(sem_of, ia_fora):
    antes = carteira(sem_of)
    r = manda(sem_of, "gastei 50 no pix")
    assert "registrada" in r, r
    assert manuais(sem_of) == 1
    assert carteira(sem_of) == pytest.approx(antes - 50)


# ── A14: duas respostas simultâneas ─────────────────────────────────────────

def test_a14_duas_respostas_dinheiro_gravam_uma_vez(com_of, ia_fora):
    manda(com_of, "gastei 500 no mercado")
    barreira = threading.Barrier(2)
    erros: list[BaseException] = []

    def _responde():
        try:
            barreira.wait()
            manda(com_of, "dinheiro")
        except BaseException as e:  # noqa: BLE001 — o assert abaixo mostra
            erros.append(e)

    ts = [threading.Thread(target=_responde) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not erros, erros
    assert manuais(com_of) == 1


# ── isolamento: o extrato de B nunca aparece para A ─────────────────────────

def test_extrato_de_outro_usuario_nao_aparece(com_of, uid_pro, ia_fora, sem_of):
    outro = sem_of
    _importa_of_tx(outro, today_tz(), "500.00", "PIX DO OUTRO", f"tx-q40-b-{outro}")
    from db.open_finance import buscar_no_extrato
    assert buscar_no_extrato(com_of, "despesa", 500) == []
    assert len(buscar_no_extrato(outro, "despesa", 500)) == 1


# ── outros caminhos que gravam: foto de recibo, fila de valores, Discord ─────

def _recibo(uid):
    db.set_pending_action(uid, "confirm_media_launch", {
        "tipo": "despesa", "valor": 42.5, "alvo": "padaria", "categoria": "alimentação",
        "data": today_tz().isoformat(), "platform": "whatsapp"})


def test_foto_de_recibo_com_of_pergunta_e_dinheiro_grava_a_previa(com_of, ia_fora):
    _recibo(com_of)
    r = manda(com_of, "sim")
    assert "R$ 42,50" in r and "dinheiro vivo" in r, r
    assert manuais(com_of) == 0
    manda(com_of, "dinheiro")
    row = _q("select alvo, categoria, valor from launches where user_id=%s", (com_of,))
    assert manuais(com_of) == 1
    assert (row["alvo"], row["categoria"], float(row["valor"])) == ("padaria", "alimentação", 42.5)


def test_foto_de_recibo_com_of_pix_nao_grava(com_of, ia_fora):
    _recibo(com_of)
    manda(com_of, "sim")
    r = manda(com_of, "pix")
    assert manuais(com_of) == 0 and "Open Finance" in r, r


def test_foto_de_recibo_sem_of_grava_como_antes(sem_of, ia_fora):
    _recibo(sem_of)
    r = manda(sem_of, "sim")
    assert "Lançamento registrado" in r, r
    assert manuais(sem_of) == 1


def test_fila_de_valores_de_antes_do_deploy_nao_grava_sem_forma(com_of, ia_fora):
    db.set_pending_action(com_of, "multi_launch_values", {
        "queue": [{"tipo": "despesa", "desc": "aluguel"}], "platform": "whatsapp"})
    r = manda(com_of, "1200")
    assert "Não registrei" in r and "aluguel" in r, r
    assert manuais(com_of) == 0 and pendencia(com_of) is None


def test_entrada_rapida_do_discord(com_of, sem_of):
    from core.services.quick_entry import handle_quick_entry
    assert "Não registrei" in handle_quick_entry(com_of, "gastei 50 no mercado").text
    assert "Open Finance" in handle_quick_entry(com_of, "gastei 50 no pix").text
    assert manuais(com_of) == 0
    handle_quick_entry(com_of, "gastei 50 no mercado em dinheiro")
    handle_quick_entry(sem_of, "gastei 50 no pix")
    assert manuais(com_of) == 1 and manuais(sem_of) == 1
    assert handle_quick_entry(com_of, "oi, tudo bem?") is None


# ── Áudio com vários lançamentos: uma pergunta só, como no texto (A10) ──────
# Antes do conserto, cada pedaço armava a própria pergunta e a fila de valores
# do pedaço sem valor apagava a do pedaço com valor: "dinheiro" caía no fora de
# escopo e os dois lançamentos sumiam sem mensagem.

class _Audio:
    data = b"x"
    filename = "audio.ogg"
    content_type = "audio/ogg"


def fala(monkeypatch, uid: int, frase: str) -> str:
    import core.handle_incoming as hi
    from core.types import IncomingMessage
    monkeypatch.setattr(hi, "transcribe_audio", lambda data, fn: frase)
    out = hi.handle_incoming(IncomingMessage(
        platform="whatsapp", user_id=uid, text="", message_id="1",
        attachments=[_Audio()], external_id=str(uid), raw={}))
    return out[0].text


def test_audio_multi_com_of_dinheiro_grava_o_que_tem_valor_e_segue_a_fila(
        com_of, ia_fora, monkeypatch):
    antes = carteira(com_of)
    r = fala(monkeypatch, com_of, "gastei 50 no mercado e paguei o aluguel")
    assert r.count("dinheiro vivo") == 1, r
    assert manuais(com_of) == 0 and pendencia(com_of) == "payment_method_choice"
    r = manda(com_of, "dinheiro")
    assert manuais(com_of) == 1 and carteira(com_of) == antes - 50, r
    assert "aluguel" in r and pendencia(com_of) == "multi_launch_values", r
    manda(com_of, "1500")
    assert manuais(com_of) == 2 and carteira(com_of) == antes - 1550


def test_audio_multi_com_of_pix_nao_grava_e_avisa(com_of, ia_fora, monkeypatch):
    fala(monkeypatch, com_of, "gastei 50 no mercado e paguei o aluguel")
    r = manda(com_of, "pix")
    assert "Não registrei" in r and "Open Finance" in r, r
    assert manuais(com_of) == 0 and pendencia(com_of) is None


def test_audio_multi_com_of_em_dinheiro_vale_para_o_audio_inteiro(com_of, ia_fora, monkeypatch):
    r = fala(monkeypatch, com_of, "gastei 50 no mercado em dinheiro e paguei o aluguel")
    assert manuais(com_of) == 1 and "aluguel" in r, r
    manda(com_of, "1500")
    assert manuais(com_of) == 2


def test_audio_multi_sem_of_como_antes(sem_of, ia_fora, monkeypatch):
    r = fala(monkeypatch, sem_of, "gastei 50 no mercado e paguei o aluguel")
    assert "dinheiro vivo" not in r and "aluguel" in r, r
    assert manuais(sem_of) == 1 and pendencia(sem_of) == "multi_launch_values"


# ── áudio com pedaços de fluxos diferentes (caixinha, conta, gasto) ─────────
# Cada pedaço segue no SEU fluxo; a forma é perguntada uma vez para o áudio.

def _deposito_viagem(uid) -> float:
    row = _q("select balance from pockets where user_id=%s and name='viagem'", (uid,))
    return float(row["balance"]) if row else 0.0


def _despesas(uid) -> list[tuple[float, str]]:
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select valor, alvo from launches where user_id=%s and tipo='despesa' "
                    "and coalesce(source,'manual') <> 'open_finance' order by id", (uid,))
        return [(float(r["valor"]), r["alvo"]) for r in cur.fetchall()]


@pytest.mark.parametrize("resposta,despesas", [
    ("dinheiro", [(30.0, "uber")]), ("pix", []),
])
def test_audio_multi_com_of_caixinha_e_gasto_nada_some(com_of, ia_fora, monkeypatch,
                                                        resposta, despesas):
    manda(com_of, "criar caixinha viagem")
    r = fala(monkeypatch, com_of, "guardei 100 na caixinha viagem e gastei 30 no uber")
    assert "dinheiro vivo" in r and "não encontrada" not in r, r
    assert _deposito_viagem(com_of) == 0 and _despesas(com_of) == []
    assert pendencia(com_of) == "payment_method_choice"
    r = manda(com_of, resposta)
    assert _deposito_viagem(com_of) == 100.0, r
    assert _despesas(com_of) == despesas, r


@pytest.mark.parametrize("resposta,despesas", [
    ("dinheiro", [(50.0, "mercado")]), ("banco", []),
])
def test_audio_multi_com_of_conta_nao_leva_o_valor_do_outro_pedaco(com_of, ia_fora, monkeypatch,
                                                                   resposta, despesas):
    from tests.test_conta_paga_forma import B, conta_fixa
    conta = conta_fixa(com_of, valor=90.0)
    fala(monkeypatch, com_of, "paguei a luz e gastei 50 no mercado")
    assert _despesas(com_of) == [] and pendencia(com_of) == "payment_method_choice"
    r = manda(com_of, resposta)
    luz = B.get_bill(com_of, conta["id"])
    assert luz["status"] == "pending" and luz["paid_amount"] is None, (r, luz)
    assert _despesas(com_of) == despesas, r


def _of_cai(monkeypatch):
    """A consulta do Open Finance falha (a de `regra_ativa` e a de `quitar`)."""
    def _cai(_uid):
        raise RuntimeError("DB caiu")
    monkeypatch.setattr(db, "has_open_finance_connections", _cai)


def test_audio_multi_com_regra_ativa_com_erro_pergunta_e_nao_perde(sem_of, ia_fora, monkeypatch):
    _of_cai(monkeypatch)
    r = fala(monkeypatch, sem_of, "gastei 300 no ifood e 150 na farmacia")
    assert "dinheiro vivo" in r and "erro" not in r.lower(), r
    assert manuais(sem_of) == 0
    manda(sem_of, "dinheiro")
    assert sorted(v for v, _ in _despesas(sem_of)) == [150.0, 300.0]


@pytest.mark.parametrize("resposta,despesas", [
    ("dinheiro", [150.0, 300.0]), ("banco", []),
])
def test_audio_multi_regra_ativa_com_erro_na_resposta_nao_perde(com_of, ia_fora, monkeypatch,
                                                               resposta, despesas):
    fala(monkeypatch, com_of, "gastei 300 no ifood e 150 na farmacia")
    assert pendencia(com_of) == "payment_method_choice"
    _of_cai(monkeypatch)
    r = manda(com_of, resposta)
    assert "erro" not in r.lower(), r
    assert sorted(v for v, _ in _despesas(com_of)) == despesas, r
    if resposta == "banco":
        assert "Não registrei" in r and "Open Finance" in r, r


def test_conta_pelo_banco_com_erro_na_consulta_nao_paga_sem_debito(sem_of, ia_fora, monkeypatch):
    # Sem banco conectado, "no pix" paga na Carteira. Com a consulta caída,
    # `regra_ativa` diz "tem banco"; marcar paga sem débito seria gasto sumido.
    from tests.test_conta_paga_forma import B, conta_fixa
    conta = conta_fixa(sem_of, valor=90.0)
    _of_cai(monkeypatch)
    r = manda(sem_of, "paguei a luz no pix")
    luz = B.get_bill(sem_of, conta["id"])
    assert luz["status"] == "pending" and luz["paid_amount"] is None, (r, luz)


def test_audio_multi_com_of_forma_antes_do_valor(com_of, ia_fora, monkeypatch):
    # O único pedaço que precisa da forma é o SEM valor: a forma vem antes
    # do "quanto foi o aluguel?" (Q4a).
    manda(com_of, "criar caixinha viagem")
    r = fala(monkeypatch, com_of, "guardei 100 na caixinha viagem e paguei o aluguel")
    assert "dinheiro vivo" in r and "Faltou o valor" not in r, r
    assert pendencia(com_of) == "payment_method_choice"
    assert _deposito_viagem(com_of) == 0
    r = manda(com_of, "dinheiro")
    assert _deposito_viagem(com_of) == 100.0 and "Faltou o valor de *aluguel*" in r, r


def test_audio_multi_com_of_misto_nao_grava(com_of, ia_fora, monkeypatch):
    r = fala(monkeypatch, com_of, "gastei 50 no mercado em dinheiro e 30 no uber no pix")
    assert "Mandou dinheiro e banco" in r, r
    assert manuais(com_of) == 0 and pendencia(com_of) is None


def test_audio_multi_com_of_pergunta_viva_nao_grava_nada(com_of, ia_fora, monkeypatch):
    from tests.test_bill_amount_pending import _monta_conta_variavel
    _monta_conta_variavel(com_of)
    manda(com_of, "paguei a luz em dinheiro")
    assert pendencia(com_of) == "bill_amount_expected"
    r = fala(monkeypatch, com_of, "gastei 50 no mercado e paguei o aluguel")
    assert "outra pergunta minha esperando" in r, r
    assert manuais(com_of) == 0 and pendencia(com_of) == "bill_amount_expected"


def test_audio_multi_com_of_fila_nao_apaga_a_pergunta_de_outro_pedaco(com_of, ia_fora, monkeypatch):
    fala(monkeypatch, com_of, "gastei 50 e paguei o aluguel")
    r = manda(com_of, "dinheiro")
    assert "Em que você gastou" in r and "Não registrei *aluguel*" in r, r
    assert pendencia(com_of) == "clarification"
    manda(com_of, "mercado")
    assert _despesas(com_of) == [(50.0, "mercado")]


def test_audio_multi_sem_of_caixinha_e_gasto_como_antes(sem_of, ia_fora, monkeypatch):
    r = fala(monkeypatch, sem_of, "guardei 100 na caixinha viagem e gastei 30 no uber")
    assert "dinheiro vivo" not in r, r
    assert _despesas(sem_of) == [(30.0, "uber")]


def test_audio_multi_com_of_conta_com_valor_proprio_nao_cruza(com_of, ia_fora, monkeypatch):
    from tests.test_conta_paga_forma import B, conta_fixa
    conta = conta_fixa(com_of, valor=90.0)
    fala(monkeypatch, com_of, "paguei a luz 90 e gastei 50 no mercado")
    manda(com_of, "dinheiro")
    luz = B.get_bill(com_of, conta["id"])
    assert luz["status"] == "paid" and float(luz["paid_amount"]) == 90.0, luz
    assert sorted(_despesas(com_of)) == [(50.0, "mercado"), (90.0, "conta:Luz")]


def test_audio_multi_com_of_duas_respostas_gravam_uma_vez(com_of, ia_fora, monkeypatch):
    fala(monkeypatch, com_of, "gastei 300 no ifood e 150 na farmacia")
    barreira, erros = threading.Barrier(2), []

    def _responde():
        try:
            barreira.wait()
            manda(com_of, "dinheiro")
        except BaseException as e:  # noqa: BLE001
            erros.append(e)
    ts = [threading.Thread(target=_responde) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not erros, erros
    assert sorted(v for v, _ in _despesas(com_of)) == [150.0, 300.0]


def test_audio_multi_com_of_tres_pedacos_uma_pergunta(com_of, ia_fora, monkeypatch):
    r = fala(monkeypatch, com_of, "gastei 100 no mercado, recebi 200 de freela e gastei 30 no uber")
    assert r.count("dinheiro vivo") == 1, r
    manda(com_of, "dinheiro")
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select tipo, valor from launches where user_id=%s order by id", (com_of,))
        linhas = [(x["tipo"], float(x["valor"])) for x in cur.fetchall()]
    assert linhas == [("despesa", 100.0), ("receita", 200.0), ("despesa", 30.0)], linhas


def test_audio_multi_com_of_consulta_espera_a_resposta(com_of, ia_fora, monkeypatch):
    fala(monkeypatch, com_of, "qual meu saldo e gastei 30 no uber")
    assert manuais(com_of) == 0
    r = manda(com_of, "dinheiro")
    assert "Saldo total" in r, r
    assert _despesas(com_of) == [(30.0, "uber")]


def test_audio_multi_com_of_cartao_manual_vale_para_o_audio_inteiro(com_of, ia_fora, monkeypatch):
    # A forma é da transcrição inteira: "cartão de crédito" decide banco, a
    # compra vai para a fatura do cartão manual e o outro pedaço é recusado
    # com aviso, nunca gravado em silêncio.
    db.set_default_card(com_of, db.create_card(com_of, "Manual", closing_day=10, due_day=17))
    r = fala(monkeypatch, com_of, "comprei 50 no mercado no cartao de credito e gastei 30 no uber")
    assert "Compra no Crédito Registrada" in r and "Não registrei" in r, r
    assert compras_credito(com_of) == 1 and _despesas(com_of) == []


@pytest.mark.xfail(strict=True, reason=(
    "Defeito anterior a este PR (medido na base 36052c9, sem pergunta de forma): "
    "no mesmo áudio, o gasto de outro pedaço arma uma pendência por cima de "
    "`funding_source_choice` e o depósito/aporte nunca acontece. Conserto em PR próprio."))
@pytest.mark.parametrize("prepara,frase", [
    (lambda uid: manda(uid, "criar caixinha viagem"),
     "guardei 100 na caixinha viagem e gastei 30 no uber"),
    (lambda uid: db.create_investment(uid, "CDB Nubank", 1.0, "monthly"),
     "investi 200 no CDB Nubank e gastei 30 no uber"),
], ids=["caixinha", "investimento"])
def test_audio_multi_de_onde_sai_nao_e_apagada_pelo_outro_pedaco(com_of, ia_fora, monkeypatch,
                                                                prepara, frase):
    with db.get_conn() as conn, conn.cursor() as cur:  # Carteira e banco cobrem: pergunta a origem
        cur.execute("update accounts set balance = balance + 500 where user_id=%s", (com_of,))
        conn.commit()
    prepara(com_of)
    fala(monkeypatch, com_of, frase)
    r = manda(com_of, "dinheiro")
    assert "De onde sai" in r, r
    assert pendencia(com_of) == "funding_source_choice"
    manda(com_of, "1")
    guardado = _q("select coalesce((select sum(balance) from pockets where user_id=%s), 0)"
                  " + coalesce((select sum(balance) from investments where user_id=%s), 0) t",
                  (com_of, com_of))["t"]
    assert float(guardado) > 0
