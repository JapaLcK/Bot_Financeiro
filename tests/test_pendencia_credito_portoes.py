"""PORTA 2 do abandono: a RESPOSTA NÃO RECONHECIDA num espaço enumerável.

O handler devolve `None` e o `route()` abandona — ou RE-PERGUNTA, conforme a
regra escrita em `core/handlers/credit.py`, acima do `_so_numero`: *manter a
pergunta viva arma um gatilho destrutivo?*. Aqui se mede a assimetria entre os
dois grupos, e os portões que allowlist nenhuma alcançaria (`tchau`,
`excluir cartao nubank` — todos out_of_scope/0.00).

O CATÁLOGO DE CONTROLES NEGATIVOS (A–M) do grupo inteiro mora em
`tests/_pendencia_credito_helpers.py`, uma vez só — inclusive os que derrubam
casos DESTE arquivo. Leia lá antes de mexer aqui.
"""
from __future__ import annotations

from datetime import date

import pytest

import db
from _pendencia_credito_helpers import (
    AVISO as _AVISO,
    AS_CINCO as _AS_CINCO,
    arma_card_setup as _arma_card_setup,
    arma_delete_card as _arma_delete_card,
    arma_installment as _arma_installment,
    arma_pay_bill_choice as _arma_pay_bill_choice,
    arma_set_primary as _arma_set_primary,
    cartao as _cartao,
    diga as _diga,
    escrituras as _escrituras,
    novo_uid as _uid,
)


#
# O que MUDA é a mensagem; o que NÃO muda é o dinheiro — nunca pagam. Fazê-las
# funcionar é feature, e feature não entra em PR de dinheiro.
_NAO_RECONHECIDAS = ["setembro/2026", "09/2026", "9/26", "a primeira", "a segunda"]


@pytest.mark.parametrize("resposta_do_user", _NAO_RECONHECIDAS)
def test_pay_bill_choice_nao_reconhecida_nunca_paga(resposta_do_user):
    """O invariante duro é o DINHEIRO: nenhuma delas pode pagar fatura.

    E o comportamento escolhido é RE-PERGUNTAR, não abandonar (a regra em
    `core/handlers/credit.py`, acima do `_so_numero`): aqui não há gatilho a
    armar — `sim` não é referência de fatura — e a lista numerada é exatamente
    o que ajuda quem tentou responder e errou a forma."""
    uid = _uid()
    _arma_pay_bill_choice(uid)
    saldo_antes = db.get_balance(uid)

    resposta = _diga(uid, resposta_do_user)

    assert db.list_open_bills(uid), f"{resposta_do_user!r} PAGOU: {resposta!r}"
    assert db.get_balance(uid) == saldo_antes, f"{resposta_do_user!r} mexeu no saldo"
    # A pergunta continua de pé, e o usuário recebe a lista de volta.
    assert _AVISO not in resposta, f"{resposta_do_user!r} abandonou: {resposta!r}"
    assert "não entendi qual fatura" in resposta.lower(), resposta
    pend = db.get_pending_action(uid)
    assert pend and pend["action_type"] == "pay_bill_choice", \
        f"{resposta_do_user!r}: a pendência não sobreviveu ({pend!r})"
def test_footgun_tres_turnos_nao_apaga_cartao():
    """O footgun de 3 turnos, com o cartão no nome: `excluir cartao nubank` →
    `saldo` → `sim`. O `sim` não pode apagar a exclusão de dois turnos atrás.

    Este é o guard anti-órfão do :588 chegando onde estava SOMBREADO."""
    uid = _uid()
    _cartao(uid)

    t1 = _diga(uid, "excluir cartao nubank")
    assert (db.get_pending_action(uid) or {}).get("action_type") == "credit_delete_card", t1

    t2 = _diga(uid, "saldo")
    assert _AVISO in t2, t2
    assert db.get_pending_action(uid) is None, "a confirmação de exclusão sobreviveu ao 'saldo'"

    t3 = _diga(uid, "sim")

    assert len(db.list_cards(uid)) == 1, f"o 'sim' apagou o cartão: {t3!r}"
    assert "excluído" not in t3.lower(), t3


def test_pay_bill_choice_nao_paga_com_pergunta_de_gasto():
    """`quanto gastei no nubank` é PERGUNTA, não escolha de fatura — mas
    `_find_card_name_in_text` casava "nubank" dentro dela e pagava R$ 300."""
    uid = _uid()
    _arma_pay_bill_choice(uid)
    saldo_antes = db.get_balance(uid)

    resposta = _diga(uid, "quanto gastei no nubank")

    assert db.list_open_bills(uid), f"a fatura foi paga: {resposta!r}"
    assert db.get_balance(uid) == saldo_antes, "o saldo mudou"
    assert _AVISO in resposta, resposta
def test_aviso_aparece_quando_abandona():
    """Metade negativa do par do aviso (D2): houve abandono → o usuário é
    avisado de que a pergunta que estava na tela morreu."""
    uid = _uid()
    _arma_card_setup(uid)

    resposta = _diga(uid, "saldo")

    assert resposta.startswith("🔕"), resposta
    assert _AVISO in resposta, resposta


# As SETE respostas que o `_is_delete` (`core/handlers/credit.py:677`) aceita no
# step `duplicate_card_name`. Duas delas a blacklist da rodada anterior comia, e
# o usuário perdia o cadastro do cartão no meio.

@pytest.mark.parametrize("step,frase,campo", [
    ("credit_limit_ask", "gastei 50 no mercado", "credit_limit"),
    ("closing_day", "gastei 10 no mercado", "closing_day"),
])
def test_portao_de_forma_nao_deixa_numero_de_frase_virar_valor(step, frase, campo):
    """`_parse_day` e `parse_money` fazem `search`, não `fullmatch`: achavam o
    número DENTRO da frase. `gastei 50 no mercado` virava limite de R$ 50,00.

    O conserto é o portão de FORMA (`_so_numero`) ANTES do parser — o
    `parse_money` não foi tocado.

    Estes RE-PERGUNTAM em vez de abandonar (a regra em `core/handlers/credit.py`
    acima do `_so_numero`): não há gatilho destrutivo a armar num step que só
    aceita número, e perder o cadastro do cartão no meio é chato."""
    uid = _uid()
    card_id = _cartao(uid)
    antes = db.list_cards(uid)[0][campo]
    db.set_pending_action(uid, "credit_card_setup", {
        "step": step, "card_name": "Nubank", "card_id": card_id,
        "closing_day": 10, "ask_primary": False, "credit_limit_asked": True,
    })

    resposta = _diga(uid, frase)

    assert db.list_cards(uid)[0][campo] == antes, \
        f"{step}: {frase!r} gravou {db.list_cards(uid)[0][campo]!r}: {resposta!r}"
    assert _AVISO not in resposta, f"{step}: abandonou em vez de re-perguntar: {resposta!r}"
    pend = db.get_pending_action(uid)
    assert pend and (pend["payload"] or {}).get("step") == step, \
        f"{step}: a pendência não sobreviveu ({pend!r})"


@pytest.mark.parametrize("frase", ["gastei 50 no mercado", "gastei 10 no mercado",
                                   "comprei uma tv por 50", "paguei 300 no mercado"])
def test_forma_com_residuo_semantico_continua_recusada(frase):
    """O outro lado do R3-2: afrouxar o critério não pode reabrir o buraco.
    Na `main` estas quatro gravavam limite de R$ 50, 10, 50 e 300."""
    uid = _uid()
    card_id = _cartao(uid)
    db.set_pending_action(uid, "credit_card_setup", {
        "step": "credit_limit_ask", "card_name": "Nubank", "card_id": card_id,
        "closing_day": 10, "ask_primary": False, "credit_limit_asked": True,
    })

    resposta = _diga(uid, frase)

    assert db.list_cards(uid)[0]["credit_limit"] is None, \
        f"{frase!r} gravou limite: {resposta!r}"


# --- R3-3: os dois portões que estavam com cobertura ZERO -----------------

@pytest.mark.parametrize("texto", ["talvez", "depois", "tchau"])
def test_reminder_opt_in_recusa_o_que_nao_e_sim_nem_nao(texto):
    """MUTANTE: apague o `if not _is_yes(answer) and not _is_no(answer): return
    None` do step `reminder_opt_in` e este teste morre.

    Sem o portão, QUALQUER texto caía no `update_card_reminder_settings(
    enabled=False)` logo abaixo e o fluxo AVANÇAVA para a pergunta do limite —
    ou seja, `talvez` desligava o lembrete do cartão e passava de step."""
    uid = _uid()
    card_id = _cartao(uid)
    db.set_pending_action(uid, "credit_card_setup", {
        "step": "reminder_opt_in", "card_name": "Nubank", "card_id": card_id,
        "closing_day": 10, "ask_primary": False,
    })

    resposta = _diga(uid, texto)

    assert _AVISO in resposta, f"{texto!r} não abandonou: {resposta!r}"
    assert "limite de crédito" not in resposta.lower(), \
        f"{texto!r} avançou de step: {resposta!r}"
    assert db.get_pending_action(uid) is None, \
        f"{texto!r}: a pendência não foi abandonada"


@pytest.mark.parametrize("texto", ["talvez", "depois", "tchau"])
def test_set_primary_step_recusa_o_que_nao_e_sim_nem_nao(texto):
    """MUTANTE: apague o `if not _is_yes(answer) and not _is_no(answer): return
    None` do step `set_primary` e este teste morre.

    Sem o portão, qualquer texto caía no "Mantive o cartão principal atual" e
    consumia a pergunta — `tchau` respondia uma pergunta que o usuário não
    respondeu."""
    uid = _uid()
    card_id = _cartao(uid)
    db.set_pending_action(uid, "credit_card_setup", {
        "step": "set_primary", "card_name": "Nubank", "card_id": card_id,
        "closing_day": 10,
    })

    resposta = _diga(uid, texto)

    assert _AVISO in resposta, f"{texto!r} não abandonou: {resposta!r}"
    assert "mantive" not in resposta.lower(), \
        f"{texto!r} respondeu a pergunta sozinho: {resposta!r}"


# --- N1: o step `choose` segue a regra escrita (não arma gatilho → re-pergunta)

def test_set_primary_choose_re_pergunta_em_vez_de_abandonar():
    """N1. O `choose` não arma gatilho — o turno seguinte só é lido como NOME
    de cartão, e `sim` ali vira `get_card_id_by_name("sim")` → None. Pela regra
    escrita em `core/handlers/credit.py`, isso é RE-PERGUNTA.

    Antes desta rodada ele abandonava, e era o que fazia `Itaú` escrito sem
    acento matar o fluxo em vez de pedir de novo."""
    uid = _uid()
    _cartao(uid)
    db.set_pending_action(uid, "credit_card_set_primary", {"step": "choose"})

    resposta = _diga(uid, "cartao que nao existe")

    assert _AVISO not in resposta, f"abandonou: {resposta!r}"
    assert "não encontrei esse cartão" in resposta.lower(), resposta
    pend = db.get_pending_action(uid)
    assert pend and pend["action_type"] == "credit_card_set_primary", \
        f"a pendência não sobreviveu: {pend!r}"


# P2-2 (Codex no #323), o lado do ATAQUE. Aparar o sufixo do nome do cartão
# abriu uma ponta nova, e as duas pontas NÃO correm o mesmo risco: no começo o
# perigo é o comando vir antes do nome (`excluir cartao nubank`), no fim é ele
# vir depois. Por isso `_CORTESIA_FINAL` é um conjunto pequeno de cortesia e
# não a lista de filler — com o filler, `nubank excluir` casaria `nubank` e a
# pergunta "qual fatura?" pagaria R$ 300 com um comando de EXCLUIR.
@pytest.mark.parametrize("frase", ["nubank excluir", "nubank apagar",
                                   "nubank deletar", "nubank remover",
                                   "nubank saldo", "nubank fatura"])
def test_pay_bill_choice_nao_paga_com_comando_depois_do_nome(frase):
    uid = _uid()
    _arma_pay_bill_choice(uid)

    resposta = _diga(uid, frase)

    assert db.list_open_bills(uid), f"{frase!r} PAGOU a fatura: {resposta!r}"
    assert float(db.get_balance(uid)) > -300, \
        f"{frase!r} debitou a fatura: {resposta!r}"
