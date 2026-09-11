"""O gate do bot RESPONDE a isenção, em vez de deixar a mensagem seguir o fluxo.

Irmão do `test_paywall_gate_isencoes.py` (o que o gate deixa passar) e do
`test_paywall_gate_bot.py` (o veredito). Aqui se mede uma coisa só: quem está
barrado NUNCA cai no `handle_incoming` normal — nem pela porta da ajuda, nem
pela do billing.

A isenção antiga liberava a MENSAGEM (`return None`), e o `route()` resolve
PENDÊNCIAS antes de chegar no ramo `help` (`core/intent_router.py:519-575` ×
`:886`). Com um `installment_pending` vivo — cenário real: o usuário só-WhatsApp
começa `parcelei 500 em 5x`, NÃO é barrado (não tem cadastro web), depois se
cadastra e é auto-vinculado — `ajuda?` virava a DESCRIÇÃO da compra e registrava
as 5 parcelas. Barrado pelo gate, cobrado assim mesmo.

Enumerar as portas do `route()` para sempre não era possível; a correção é o
gate devolver a resposta ele mesmo (`h_help.answer_help` / `handle_billing_command`).

CONTROLE NEGATIVO DO GRUPO: no `_paywall_gate`, troque o ramo
    if ajuda in ("help", "help.tutorial"):
        return [OutgoingMessage(text=format_for_platform(...))]
de volta por `return None`. Medido nesta árvore: os NOVE `action_type` de
`_PENDENCIAS_VIVAS` ficam vermelhos, cada um pelo motivo anotado na tabela, e o
`test_barrado_com_parcelamento_pendente_nao_registra_parcela` junto. O da porta
do billing tem o controle negativo dele na própria docstring.

CONTROLE POSITIVO: `test_com_plano_*` — quem paga continua tendo a pendência
resolvida e a ajuda de sempre. É o risco desta correção.
"""
from __future__ import annotations

from datetime import date

import pytest

import db
from _paywall_gate_helpers import (  # noqa: F401  (v2_ligado é fixture autouse)
    barrado as _barrado,
    cadastro_novo as _cadastro_novo,
    com_plano as _com_plano,
    diga as _diga,
    v2_ligado,
)


_TABELAS_COM_USER_ID: list[str] = []


def _escrituras(uid: int) -> dict:
    """Quantas linhas este usuário tem em CADA tabela que tem `user_id`.

    Genérica de propósito: cada action_type da tabela abaixo escreveria numa
    tabela diferente (lançamento, transação de cartão, fatura, investimento,
    cartão, a própria pendência) e enumerar isso à mão é exatamente como se
    esquece uma. Não enxerga UPDATE — por isso o saldo e o payload da pendência
    são conferidos à parte.
    """
    with db.get_conn() as conn, conn.cursor() as cur:
        if not _TABELAS_COM_USER_ID:
            cur.execute(
                "select table_name from information_schema.columns "
                "where table_schema = 'public' and column_name = 'user_id'"
            )
            _TABELAS_COM_USER_ID.extend(sorted(r["table_name"] for r in cur.fetchall()))
        return {
            t: cur.execute(
                f'select count(*) as n from "{t}" where user_id = %s', (uid,)
            ).fetchone()["n"]
            for t in _TABELAS_COM_USER_ID
        }


def _cartao(uid: int) -> int:
    return db.create_card(uid, "Nubank", 10, 17)


def _arma_installment(uid: int) -> None:
    card_id = _cartao(uid)
    db.set_pending_action(uid, "installment_pending", {
        "valor": 500.0, "n": 5, "card_id": card_id, "card_name": "Nubank",
        "purchased_at": date.today().isoformat(), "categoria": "outros",
    })


def _arma_pay_bill_choice(uid: int) -> None:
    card_id = _cartao(uid)
    db.add_credit_purchase_installments(
        user_id=uid, card_id=card_id, valor_total=300.0, categoria="outros",
        nota="mercado", purchased_at=date.today(), installments=1,
    )
    bill_ids = [int(b["id"]) for b in db.list_open_bills(uid)]
    assert bill_ids, "setup do pay_bill_choice não gerou fatura em aberto"
    db.set_pending_action(uid, "pay_bill_choice", {"bill_ids": bill_ids, "amount": None})


def _arma_set_primary(uid: int) -> None:
    db.set_pending_action(uid, "credit_card_set_primary", {"card_id": _cartao(uid)})


def _arma_delete_card(uid: int) -> None:
    db.set_pending_action(uid, "credit_delete_card",
                          {"card_id": _cartao(uid), "card_name": "Nubank"})


def _arma_card_setup(uid: int) -> None:
    # "criar cartao" → o bot perguntou o NOME e está esperando.
    db.set_pending_action(uid, "credit_card_setup", {"step": "name"})


def _arma_bill_amount(uid: int) -> None:
    db.set_pending_action(uid, "bill_amount_expected",
                          {"bill_id": 1, "bill_name": "Luz"})


def _arma_multi_launch(uid: int) -> None:
    db.set_pending_action(uid, "multi_launch_values", {
        "queue": [{"tipo": "despesa", "desc": "aluguel"}], "platform": "whatsapp",
    })


def _arma_investment_pick(uid: int) -> None:
    db.set_pending_action(uid, "investment_pick", {
        "amount": 300.0, "text": "aportar 300", "nomes": ["Tesouro Selic"],
    })


def _arma_funding_source(uid: int) -> None:
    db.set_pending_action(uid, "funding_source_choice", {
        "amount": 300.0,
        "retomar": {"intent": "investments.deposit", "nome": "Tesouro Selic"},
        "fontes": [{"kind": "pocket", "of_account_id": None,
                    "label": "Reserva", "balance": 1000.0}],
    })


# A CATEGORIA, não a instância (§2 do CLAUDE.md). Varredura do
# `core/intent_router.py`: todo `action_type` resolvido ANTES do ramo `help`
# (:886). O comentário de cada linha é o que ele fazia com `ajuda?` na árvore
# SEM a correção — medido, não deduzido.
_PENDENCIAS_VIVAS = [
    # :519 — resolve_bill_amount abandona a pergunta: pending_actions 1 → 0.
    ("bill_amount_expected", _arma_bill_amount),
    # :532 — resolve_multi_launch_value abandona a fila: pending_actions 1 → 0.
    ("multi_launch_values", _arma_multi_launch),
    # :539 — o conjunto do h_credit.resolve_pending. Na época deste arquivo ele
    # NÃO tinha escotilha de abandono: "Quando fecha a fatura do cartão
    # *ajuda?*?" virava o NOME do cartão. Hoje tem (ver
    # `tests/test_pendencia_credito_abandono.py`), mas isso não muda o que ESTE
    # arquivo mede: para quem é BARRADO o gate responde antes do `route()`, e a
    # escotilha nem chega a rodar — a pendência tem de sobreviver intacta.
    ("credit_card_setup", _arma_card_setup),
    # "Responda *sim* para tornar *Nubank* o principal..." — sequestra a ajuda.
    ("credit_card_set_primary", _arma_set_primary),
    # "Responda *sim* para excluir *Nubank*..." — idem.
    ("credit_delete_card", _arma_delete_card),
    # ← O P1: "✅ Parcelamento Registrado! Descrição: ajuda? — 5x de R$ 100,00".
    ("installment_pending", _arma_installment),
    # "❓ Não entendi qual fatura..." — sequestra a ajuda.
    ("pay_bill_choice", _arma_pay_bill_choice),
    # :551 e :563 — TÊM escotilha de abandono, e é ela que morde: `ajuda?` é
    # "outro comando claro", então a pergunta é APAGADA (pending_actions 1 → 0).
    ("investment_pick", _arma_investment_pick),
    ("funding_source_choice", _arma_funding_source),
]


@pytest.mark.parametrize("action_type,armar", _PENDENCIAS_VIVAS,
                         ids=[t for t, _ in _PENDENCIAS_VIVAS])
def test_barrado_com_pendencia_viva_pede_ajuda_e_nada_acontece(action_type, armar):
    """Barrado + pendência viva + `ajuda?` → recebe a AJUDA, e o estado dele
    fica exatamente como estava (nenhuma linha nova, saldo intacto, pendência
    nem resolvida nem abandonada)."""
    uid = _cadastro_novo()
    armar(uid)
    antes = _escrituras(uid)
    pendencia_antes = db.get_pending_action(uid)
    assert pendencia_antes and pendencia_antes["action_type"] == action_type

    resposta = _diga(uid, "ajuda?")

    assert "comece aqui" in resposta.lower(), f"{action_type}: {resposta!r}"
    assert db.list_launches(uid) == [], f"{action_type} registrou lançamento"
    assert db.get_balance(uid) == 0, f"{action_type} mexeu no saldo"
    assert _escrituras(uid) == antes, f"{action_type} escreveu no banco"
    pendencia_depois = db.get_pending_action(uid)
    assert pendencia_depois is not None, f"{action_type}: a pendência foi consumida"
    assert pendencia_depois["action_type"] == action_type
    assert pendencia_depois["payload"] == pendencia_antes["payload"], \
        f"{action_type}: o payload da pendência foi alterado"


def test_barrado_com_parcelamento_pendente_nao_registra_parcela():
    """O P1 isolado, com o dinheiro no nome: `ajuda?` não pode virar a descrição
    da compra. Sem a correção, `list_installment_groups` volta com 1 grupo de 5
    parcelas cuja descrição é "ajuda?"."""
    uid = _cadastro_novo()
    _arma_installment(uid)

    resposta = _diga(uid, "ajuda?")

    assert "comece aqui" in resposta.lower(), resposta
    assert db.list_installment_groups(uid) == [], "registrou o parcelamento"
    assert db.list_launches(uid) == []
    assert db.get_balance(uid) == 0
    assert (db.get_pending_action(uid) or {}).get("action_type") == "installment_pending"


def test_com_plano_a_pendencia_continua_sendo_resolvida():
    """CONTROLE POSITIVO da mudança: quem NÃO é barrado segue no fluxo de
    sempre. `route()` continua resolvendo a pendência do cartão — o gate não
    pode ter mudado nada para quem paga.

    É o risco desta correção: se o gate tivesse virado atalho para todo mundo,
    este parcelamento não seria registrado.

    O veículo era `ajuda?`, e a asserção antiga cobrava que ele registrasse o
    parcelamento com a descrição "ajuda?". Isso NÃO foi afrouxado: aquilo era o
    bug — a escotilha de abandono das cinco pendências de cartão
    (`core/intent_router.py`, `tests/test_pendencia_credito_abandono.py`) fez
    `ajuda?` passar a abandonar a pergunta em vez de virar a descrição da
    compra. O propósito do teste é o mesmo; o veículo virou uma resposta
    LEGÍTIMA, que é o que sempre se quis provar aqui."""
    uid = _com_plano()
    _arma_installment(uid)

    resposta = _diga(uid, "tv samsung")

    assert not _barrado(resposta), resposta
    assert "parcelamento registrado" in resposta.lower(), resposta
    grupos = db.list_installment_groups(uid)
    assert len(grupos) == 1, f"o parcelamento de quem paga sumiu: {grupos!r}"
    with db.get_conn() as conn, conn.cursor() as cur:
        notas = cur.execute(
            "select nota from credit_transactions where user_id = %s", (uid,)
        ).fetchall()
    assert len(notas) == 5, f"esperava 5 parcelas, veio {len(notas)}"
    assert all("tv samsung" in (r["nota"] or "").lower() for r in notas), notas


def test_com_plano_a_mensagem_passa_pelo_route_e_nao_pelo_atalho_do_gate():
    """O OUTRO propósito que vivia misturado no teste acima: para quem paga, o
    gate não pode responder no lugar do `route()`.

    O discriminador é o `_AVISO_PERGUNTA_CANCELADA`: ele só é produzido dentro
    do `route()` (`core/intent_router.py`), nunca pelo gate. Se o gate tivesse
    virado atalho para todo mundo, o pagante receberia a MESMA ajuda seca do
    barrado — sem aviso e com a pendência intacta, exatamente como o
    `test_barrado_com_pendencia_viva_pede_ajuda_e_nada_acontece` mede acima.

    A expectativa antiga desta metade ("`ajuda?` de quem paga registrava o
    parcelamento") era o bug, não o contrato: `ajuda?` classifica `help`, que
    está no `_ABANDONA_CREDITO` (`core/intent_router.py`), então agora ABANDONA
    a pergunta — avisando, que é o ponto. Uma DESCRIÇÃO de compra não está no
    conjunto e continua sendo resolvida: é o teste acima."""
    uid = _com_plano()
    _arma_installment(uid)

    resposta = _diga(uid, "ajuda?")

    assert not _barrado(resposta), resposta
    assert "cancelei a pergunta anterior" in resposta.lower(), \
        f"não passou pelo route(): {resposta!r}"
    assert "comece aqui" in resposta.lower(), resposta
    # Abandonada, não resolvida: nada de parcelamento com descrição "ajuda?".
    assert db.get_pending_action(uid) is None, "a pendência sobreviveu"
    assert db.list_installment_groups(uid) == [], "registrou o parcelamento"
    assert db.list_launches(uid) == []


@pytest.mark.parametrize("texto,esperado", [
    ("ajuda", "comece aqui"),
    ("ajuda?", "comece aqui"),
    ("/ajuda ofx", "comece aqui"),
    ("tutorial", "tutorial"),
])
def test_com_plano_a_ajuda_continua_igual(texto, esperado):
    """O outro lado do controle positivo: a extração do `answer_help` não pode
    ter mudado a ajuda de quem não é barrado (é o MESMO texto, do mesmo
    `h_help`)."""
    uid = _com_plano()

    resposta = _diga(uid, texto)

    assert not _barrado(resposta), resposta
    assert esperado in resposta.lower(), resposta


def test_barrado_manda_cancelar_com_ai_pending_recebe_a_mensagem_do_gate():
    """A porta do billing. `handle_billing_command` CEDE A VEZ (devolve None)
    quando há uma ação pendente da IA — e "cancelar" puro é justamente o texto
    que resolveria uma confirmação de delete. Se o gate deixasse a mensagem
    seguir nesse caso, o buraco da ajuda voltaria pela porta do billing.

    CONTROLE NEGATIVO: troque o `if resposta is not None:` do `_paywall_gate`
    por `return None` incondicional e este teste fica vermelho.
    """
    uid = _cadastro_novo()
    db.ai_set_pending_action(uid, "delete_launch", {"launch_id": 1}, "apagar #1")
    db.set_pending_action(uid, "installment_pending", {
        "valor": 500.0, "n": 5, "card_id": _cartao(uid), "card_name": "Nubank",
        "purchased_at": date.today().isoformat(), "categoria": "outros",
    })

    resposta = _diga(uid, "cancelar")

    assert _barrado(resposta), f"o billing cedeu a vez e a mensagem seguiu: {resposta!r}"
    assert db.list_installment_groups(uid) == []
    assert (db.get_pending_action(uid) or {}).get("action_type") == "installment_pending"
