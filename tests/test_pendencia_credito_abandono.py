"""PORTA 1 do abandono: o comando CONHECIDO (`abandona_pergunta_de_credito`).

A allowlist de intent decide antes de o handler ver a mensagem. Aqui se mede o
que ela pega — `saldo`, saudação, comando de leitura — e o que ela custa.
Os portões do `core/handlers/credit.py` (porta 2) estão em
`test_pendencia_credito_portoes.py`; as respostas legítimas, em
`test_pendencia_credito_respostas_legitimas.py`.

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


# ===========================================================================
# NEGATIVOS — injetados onde discriminam: todos estes casos são VERDES com a
# correção e VERMELHOS sem ela (lista na docstring do módulo).
# ===========================================================================

def _delta(uid: int, texto: str) -> dict:
    antes = _escrituras(uid)
    _diga(uid, texto)
    depois = _escrituras(uid)
    return {k: depois[k] - antes[k] for k in antes}
@pytest.mark.parametrize("action_type,armar", _AS_CINCO,
                         ids=[t for t, _ in _AS_CINCO])
def test_saldo_abandona_sem_escrever(action_type, armar):
    """`saldo` em cima de qualquer uma das cinco: a pendência é abandonada, a
    resposta é a do COMANDO, e a pendência não escreve NADA.

    A referência é um GÊMEO no mesmo estado e SEM a pergunta armada, não o
    zero: o próprio `saldo` grava por conta dele (medido: com um cartão na
    conta ele materializa a fatura em aberto, `credit_bills` 0 → 1). Comparar
    contra zero cobraria da pendência uma escrita que é do comando.
    """
    gemeo = _uid()
    armar(gemeo)
    db.clear_pending_action(gemeo)
    esperado = _delta(gemeo, "saldo")

    uid = _uid()
    armar(uid)
    assert _escrituras(uid)["pending_actions"] == 1
    antes = _escrituras(uid)

    resposta = _diga(uid, "saldo")

    # A resposta do `saldo` não contém a palavra "saldo" — ela abre com o
    # extrato da conta. É o texto do handler, não o nome do comando.
    assert "conta corrente" in resposta.lower(), \
        f"{action_type}: não respondeu o comando: {resposta!r}"
    assert _AVISO in resposta, f"{action_type}: abandonou sem avisar: {resposta!r}"
    depois = _escrituras(uid)
    assert depois["pending_actions"] == 0, f"{action_type}: a pendência sobreviveu"
    obtido = {k: depois[k] - antes[k] for k in antes}
    # Única diferença legítima contra o gêmeo: a pendência sumindo (0 → -1).
    assert obtido == {**esperado, "pending_actions": -1}, \
        f"{action_type} escreveu além do comando: " \
        f"{[(k, esperado[k], obtido[k]) for k in obtido if obtido[k] != esperado[k] and k != 'pending_actions']}"
# ===========================================================================
# PR A — a segunda porta de abandono: RESPOSTA NÃO RECONHECIDA num espaço de
# resposta ENUMERÁVEL. A allowlist de intent sozinha não alcança esta classe:
# `quanto tenho na fatura do nubank`, `excluir cartao nubank` e `tchau` são
# out_of_scope/0.00 e nenhum conjunto de intents os pegaria.
# ===========================================================================

# Frases que casavam o nome do cartão DENTRO delas (`_find_card_name_in_text`) e
# pagavam a fatura sozinhas. Todas medidas: `excluir cartao nubank` PAGAVA
# R$ 300 — o comando de EXCLUIR virava PAGAMENTO.
_FRASES_QUE_PAGAVAM = [
    "quanto tenho na fatura do nubank",
    "quando vence o nubank",
    "excluir cartao nubank",
    "quanto gastei no nubank",
    "meu nubank fecha quando",
    "qual o limite do nubank",
    "gastei 50 no nubank",
    "oi", "bom dia", "tchau", "obrigado",
]
@pytest.mark.parametrize("frase", _FRASES_QUE_PAGAVAM)
def test_pay_bill_choice_nao_paga_com_frase_que_contem_o_cartao(frase):
    """Pagamento é UPDATE: `_escrituras` (contagem de linhas) é cega a ele.
    Por isso o invariante é `list_open_bills` + `get_balance`."""
    uid = _uid()
    _arma_pay_bill_choice(uid)

    resposta = _diga(uid, frase)

    assert db.list_open_bills(uid), f"{frase!r} PAGOU a fatura: {resposta!r}"
    # O débito da fatura é R$ 300. `gastei 50 no nubank` é comando de verdade e
    # registra R$ 50 depois de abandonar — por isso o corte é o valor da FATURA,
    # não "saldo intacto".
    assert float(db.get_balance(uid)) > -300, \
        f"{frase!r} debitou a fatura: {resposta!r}"
@pytest.mark.parametrize("meio", ["oi", "bom dia", "boa noite", "tchau",
                                  "obrigado", "saldo", "ajuda"])
def test_footgun_tres_turnos_com_qualquer_meio(meio):
    """`excluir cartao nubank` → <meio> → `sim`. O `sim` não pode apagar a
    exclusão de dois turnos atrás, seja qual for o meio.

    `oi`/`bom dia` só são pegos pelo `greeting` na allowlist; `tchau` e
    `obrigado` são out_of_scope/0.00 e só caem pela segunda porta (o
    `_resolve_delete_card` devolvendo `None`). Por isso os dois grupos estão
    no mesmo teste — cada um prova uma metade."""
    uid = _uid()
    _cartao(uid)

    _diga(uid, "excluir cartao nubank")
    assert (db.get_pending_action(uid) or {}).get("action_type") == "credit_delete_card"

    _diga(uid, meio)
    assert db.get_pending_action(uid) is None, \
        f"a confirmação de exclusão sobreviveu a {meio!r}"

    t3 = _diga(uid, "sim")

    assert len(db.list_cards(uid)) == 1, f"o 'sim' apagou o cartão após {meio!r}: {t3!r}"


@pytest.mark.parametrize("saudacao", ["oi", "olá", "bom dia", "boa tarde", "boa noite"])
def test_saudacao_nao_vira_descricao_de_parcelamento(saudacao):
    """`bom dia` virava "✅ Parcelamento Registrado! Descrição: bom dia",
    R$ 500. Pego pelo `greeting` no `_ABANDONA_CREDITO`."""
    uid = _uid()
    _arma_installment(uid)

    resposta = _diga(uid, saudacao)

    assert db.list_installment_groups(uid) == [], \
        f"{saudacao!r} registrou o parcelamento: {resposta!r}"
    assert _AVISO in resposta, resposta


# --- POSITIVOS: qual resposta legítima o portão RECUSA? (nenhuma) ----------
# `report.monthly` estava dentro e `report.weekly` fora; `pockets.list` dentro e
# `investments.list` fora. Medido: `desligar resumo semanal` respondendo "o que
# você comprou?" gravava 5 parcelas de R$ 500.
_COMANDOS_QUE_HERDARAM_A_AUSENCIA = [
    "resumo semanal", "relatorio semanal", "meus investimentos", "investimentos",
    "desfazer", "cdi", "ver cdi", "desligar resumo semanal",
    "ligar resumo semanal", "desligar resumo mensal", "desligar report diario",
    "parar emails", "reativar emails",
]


@pytest.mark.parametrize("comando", _COMANDOS_QUE_HERDARAM_A_AUSENCIA)
def test_comando_de_leitura_abandona_em_vez_de_virar_parcelamento(comando):
    """Todo comando de leitura/configuração abandona — não só os que alguém
    lembrou de listar."""
    uid = _uid()
    _arma_installment(uid)

    resposta = _diga(uid, comando)

    assert db.list_installment_groups(uid) == [], \
        f"{comando!r} virou parcelamento: {resposta!r}"
    assert _AVISO in resposta, f"{comando!r} não abandonou: {resposta!r}"


# M4: `greeting` NÃO é mundo fechado — 4 regexes com `re.search` ancorados só no
# começo, então casam a saudação SEGUIDA de outra coisa. Estas cinco são
# respostas legítimas e mesmo assim abandonam. Custo REAL e aceito: a direção é
# fail-safe. O teste existe para o custo ser VISÍVEL — se alguém consertar a
# saudação, ele morre e a decisão volta para a mesa em vez de mudar sozinha.
_SAUDACAO_COM_RESPOSTA_LEGITIMA = ["opa 5000", "opa pode ser dia 10",
                                   "oi, a do nubank", "hey nubank",
                                   "e ai comprei uma tv"]


@pytest.mark.parametrize("frase", _SAUDACAO_COM_RESPOSTA_LEGITIMA)
def test_saudacao_seguida_de_resposta_legitima_abandona_sem_escrever(frase):
    """O custo do `greeting` na allowlist, fixado. O invariante que importa é
    que a direção seja fail-safe: abandona COM AVISO e NÃO ESCREVE."""
    uid = _uid()
    _arma_installment(uid)

    resposta = _diga(uid, frase)

    assert _AVISO in resposta, f"{frase!r}: {resposta!r}"
    assert db.list_installment_groups(uid) == [], f"{frase!r} escreveu: {resposta!r}"
    assert db.list_launches(uid) == [], f"{frase!r} escreveu: {resposta!r}"
