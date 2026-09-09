"""O ESPELHO, parte 2: CONFIRMAÇÕES e os steps do cadastro de cartão.

As 8 respostas do `_is_yes`, as 7 do `_is_no`, as 7 do `_is_delete`, o nome no
step `choose`, e as formas numéricas que `_parse_day`/`parse_money` entendem
("todo dia 10", "cinco mil", "5000 pilas"). Irmão do
`test_pendencia_credito_respostas_legitimas.py`.

O CATÁLOGO DE CONTROLES NEGATIVOS (A–M) do grupo inteiro mora em
`tests/_pendencia_credito_helpers.py`, uma vez só. Leia lá antes de mexer aqui.
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


def test_card_setup_name_aceita_nome_de_cartao():
    """step=`name` + "nubank" → segue para a pergunta do dia de fechamento."""
    uid = _uid()
    _arma_card_setup(uid)

    resposta = _diga(uid, "nubank")

    assert "fecha a fatura" in resposta.lower(), resposta
    assert _AVISO not in resposta, resposta
    pend = db.get_pending_action(uid)
    assert pend and pend["action_type"] == "credit_card_setup"
    assert pend["payload"].get("step") == "closing_day", pend["payload"]


def test_delete_card_sim_apaga():
    uid = _uid()
    _arma_delete_card(uid)

    resposta = _diga(uid, "sim")

    assert "excluído com sucesso" in resposta.lower(), resposta
    assert db.list_cards(uid) == [], "o cartão não foi excluído"


def test_delete_card_nao_mantem():
    """`confirm.no` fica FORA da escotilha de propósito: "não"/"cancelar"
    precisam continuar chegando no handler para CANCELAR a pergunta."""
    uid = _uid()
    _arma_delete_card(uid)

    resposta = _diga(uid, "não")

    assert "mantive" in resposta.lower(), resposta
    assert len(db.list_cards(uid)) == 1, "o cartão sumiu num 'não'"
    assert db.get_pending_action(uid) is None


def test_set_primary_sim_vira_principal():
    uid = _uid()
    _arma_set_primary(uid)

    resposta = _diga(uid, "sim")

    assert "principal" in resposta.lower(), resposta
    cartoes = db.list_cards(uid)
    assert len(cartoes) == 1 and cartoes[0]["is_default"], cartoes


def test_aviso_nao_aparece_quando_responde():
    """Metade positiva do par do aviso: quem responde a pergunta não vê aviso
    nenhum. Sem esta, um aviso colado em TODA resposta passaria."""
    uid = _uid()
    _arma_installment(uid)

    assert _AVISO not in _diga(uid, "tv samsung")
_RESPOSTAS_IS_DELETE = ["excluir", "excluir cartao", "excluir cartão",
                        "deletar", "apagar", "remover", "delete"]


@pytest.mark.parametrize("resposta_do_user", _RESPOSTAS_IS_DELETE)
def test_duplicate_card_name_aceita_as_sete_do_is_delete(resposta_do_user):
    """step=`duplicate_card_name`: as sete são RESPOSTA, não comando. Todas têm
    de chegar no handler e pedir a confirmação da exclusão."""
    uid = _uid()
    card_id = _cartao(uid)
    db.set_pending_action(uid, "credit_card_setup", {
        "step": "duplicate_card_name", "card_name": "Nubank",
        "existing_card_name": "Nubank", "existing_card_id": card_id,
    })

    resposta = _diga(uid, resposta_do_user)

    assert _AVISO not in resposta, \
        f"{resposta_do_user!r} foi abandonada: {resposta!r}"
    assert "tem certeza" in resposta.lower(), f"{resposta_do_user!r}: {resposta!r}"
    pend = db.get_pending_action(uid)
    assert pend and pend["payload"].get("step") == "confirm_delete_existing_card", pend
@pytest.mark.parametrize("sim", ["sim", "s", "yes", "y", "quero", "claro", "ok", "pode"])
def test_delete_card_as_oito_do_is_yes_apagam(sim):
    uid = _uid()
    _arma_delete_card(uid)

    resposta = _diga(uid, sim)

    assert db.list_cards(uid) == [], f"{sim!r} não apagou: {resposta!r}"


# `cancelar` fica FORA: ele é interceptado pelo `handle_billing_command` antes
# do `route()` e nunca chega no handler do cartão. Medido igual nas duas
# colunas (main × branch) — comportamento anterior a este PR.
@pytest.mark.parametrize("nao", ["nao", "não", "n", "no", "cancela",
                                 "agora nao", "agora não"])
def test_delete_card_as_sete_do_is_no_mantem(nao):
    uid = _uid()
    _arma_delete_card(uid)

    resposta = _diga(uid, nao)

    assert len(db.list_cards(uid)) == 1, f"{nao!r} apagou o cartão: {resposta!r}"
    assert "mantive" in resposta.lower(), resposta
    assert db.get_pending_action(uid) is None


@pytest.mark.parametrize("resposta_do_user", ["nubank", "o nubank", "meu nubank"])
def test_set_primary_choose_aceita_o_nome(resposta_do_user):
    uid = _uid()
    _cartao(uid)
    db.set_pending_action(uid, "credit_card_set_primary", {"step": "choose"})

    resposta = _diga(uid, resposta_do_user)

    assert _AVISO not in resposta, f"{resposta_do_user!r} foi abandonada: {resposta!r}"
    assert "principal" in resposta.lower(), resposta


# Os steps de NÚMERO e de SIM/NÃO do `credit_card_setup`: a resposta legítima
# passa. `(step, resposta, o que tem de aparecer)`.
_STEPS_LEGITIMOS = [
    ("closing_day", "10", "quando vence"),
    ("closing_day", "dia 10", "quando vence"),
    ("reminder_opt_in", "sim", "quantos dias"),
    ("reminder_opt_in", "nao", "limite de crédito"),
    ("reminder_days", "3", "limite de crédito"),
    ("credit_limit_ask", "5000", "registrado com sucesso"),
    ("credit_limit_ask", "R$ 5.000,00", "registrado com sucesso"),
    ("credit_limit_ask", "nao", "registrado com sucesso"),
]


@pytest.mark.parametrize("step,resposta_do_user,esperado", _STEPS_LEGITIMOS,
                         ids=[f"{s}-{r}" for s, r, _ in _STEPS_LEGITIMOS])
def test_steps_restritos_aceitam_a_resposta_legitima(step, resposta_do_user, esperado):
    uid = _uid()
    card_id = _cartao(uid)
    db.set_pending_action(uid, "credit_card_setup", {
        "step": step, "card_name": "Nubank", "card_id": card_id,
        "closing_day": 10, "ask_primary": False,
    })

    resposta = _diga(uid, resposta_do_user)

    assert _AVISO not in resposta, \
        f"{step}/{resposta_do_user!r} foi abandonada: {resposta!r}"
    assert esperado in resposta.lower(), f"{step}/{resposta_do_user!r}: {resposta!r}"


# --- o "de brinde" do portão de forma -------------------------------------
_FORMA_LEGITIMA = [
    ("closing_day", "todo dia 10", "quando vence"),
    ("closing_day", "fecha dia 10", "quando vence"),
    ("closing_day", "vence dia 8", "quando vence"),
    ("closing_day", "no dia 10", "quando vence"),
    ("closing_day", "5 de cada mes", "quando vence"),
    ("credit_limit_ask", "5 mil", "registrado com sucesso"),
    ("credit_limit_ask", "cinco mil", "registrado com sucesso"),
    ("credit_limit_ask", "10 mil", "registrado com sucesso"),
    ("credit_limit_ask", "limite 5000", "registrado com sucesso"),
    ("credit_limit_ask", "e 5000", "registrado com sucesso"),
    ("credit_limit_ask", "1.500,50", "registrado com sucesso"),
    ("reminder_days", "3 dias", "registrado com sucesso"),
    ("reminder_days", "uns 3", "registrado com sucesso"),
    ("reminder_days", "3 dias antes", "registrado com sucesso"),
]


@pytest.mark.parametrize("step,resposta_do_user,esperado", _FORMA_LEGITIMA,
                         ids=[f"{s}-{r}" for s, r, _ in _FORMA_LEGITIMA])
def test_forma_legitima_passa_pelo_portao(step, resposta_do_user, esperado):
    """`_so_numero` não pode ser mais estrito que o parser que ele guarda.

    `parse_money` entende `cinco mil`; `_parse_day` entende `todo dia 10`. O
    portão derrubava os dois antes de o parser ver."""
    uid = _uid()
    card_id = _cartao(uid)
    db.set_pending_action(uid, "credit_card_setup", {
        "step": step, "card_name": "Nubank", "card_id": card_id,
        "closing_day": 10, "ask_primary": False, "credit_limit_asked": True,
    })

    resposta = _diga(uid, resposta_do_user)

    assert _AVISO not in resposta, \
        f"{step}/{resposta_do_user!r} foi abandonada: {resposta!r}"
    assert "me diga" not in resposta.lower(), \
        f"{step}/{resposta_do_user!r} foi recusada: {resposta!r}"
    assert esperado in resposta.lower(), f"{step}/{resposta_do_user!r}: {resposta!r}"
# ===========================================================================
# Rodada 7 — o que a prosa afirmava e a medição não sustentava.
# ===========================================================================

def test_fala_e_moeda_nao_divergiu_do_memory_stop_tokens():
    """§0.7: `_FALA_E_MOEDA` é SELEÇÃO da lista canônica, não uma segunda lista.

    O reuso literal não serve (`MEMORY_STOP_TOKENS` tem `dashboard`, `eu`,
    `meu` — `dashboard 5000` viraria limite de cartão), então o que se garante
    aqui é o outro lado: todo token da seleção continua EXISTINDO na origem.
    Renomeie `pila`→`pilas` lá e este teste morre, em vez de o portão passar a
    recusar `5000 pila` em silêncio — que foi exatamente como nasceram as quatro
    divergências desta rodada."""
    from utils_text import MEMORY_STOP_TOKENS
    from core.handlers.credit import _FALA_E_MOEDA

    assert _FALA_E_MOEDA, "a seleção ficou vazia — a origem mudou de nome?"
    assert _FALA_E_MOEDA <= MEMORY_STOP_TOKENS
    # As quatro que estavam divergentes, nomeadas: se alguma sumir da origem, a
    # seleção encolhe em silêncio e o `<=` acima continuaria verde.
    for token in ("pila", "pilas", "conto", "contos", "mango", "mangos",
                  "acho", "centavos", "reais"):
        assert token in _FALA_E_MOEDA, f"{token!r} caiu fora da seleção"


@pytest.mark.parametrize("resposta_do_user", [
    "5000 pila", "5000 pilas", "5000 contos", "5000 mangos",
    "acho que 5000", "5 mil reais e 50 centavos",
])
def test_so_numero_aceita_o_vocabulario_canonico_de_fala(resposta_do_user):
    """As quatro divergências do M5, viradas em teste. O `parse_money` lê 5000
    em todas — era o portão que derrubava antes de ele ver."""
    uid = _uid()
    card_id = _cartao(uid)
    db.set_pending_action(uid, "credit_card_setup", {
        "step": "credit_limit_ask", "card_name": "Nubank", "card_id": card_id,
        "closing_day": 10, "ask_primary": False, "credit_limit_asked": True,
    })

    resposta = _diga(uid, resposta_do_user)

    assert "me diga" not in resposta.lower(), \
        f"{resposta_do_user!r} foi recusada: {resposta!r}"
    assert db.list_cards(uid)[0]["credit_limit"] is not None, \
        f"{resposta_do_user!r} não gravou o limite: {resposta!r}"


# M2: as duas assimetrias que critério nenhum sustentava, e os irmãos delas.
