"""Nome do usuário é CONTEÚDO, não marcação do WhatsApp (#146, fatia 1 de 2).

O WhatsApp NÃO tem caractere de escape: `\\*` não existe, entidade HTML aparece
literal na tela. E neutralizar o delimitador do usuário com um WORD JOINER
(U+2060) também não resolve — MEDIDO pelo dono no cliente real do WhatsApp,
`*a*<WJ>b*` renderiza `ab*`, idêntico ao que a `main` já mostra: o WJ cega a
ABERTURA, mas o FECHAMENTO olha o caractere ANTERIOR. Ou seja, era inerte
justamente quando o delimitador do usuário é igual ao do template — o `a*b`
dentro de `*{...}*`, que é o caso do título da issue.

O mecanismo é `wrap_wa_markup` (core/response_formatter.py): quando o texto do
usuário contém `*`, `~`, crase — ou um `_` que PODE parear (com não-palavra ou
borda de um dos lados) —, a mensagem sai SEM o embrulho de marcação. O `_` no
MEIO de palavra não entra: MEDIDO pelo dono no cliente real em 2026-09-04,
`meta_casa_nova` sai literal, sem itálico, enquanto `~x~` sai riscado mesmo
colado. Custo aceito: perde-se o negrito nos nomes que casam — e nesse mesmo
caso a formatação já estava quebrada antes, então não se perde nada que
funcione.

CONTROLE NEGATIVO — quatro mutações, todas medidas em 2026-09-04 contra os
`18 passed, 2 xfailed` deste arquivo com o conserto de pé. Nenhuma foi injetada
num caso que já estava vermelho:

  1. `wrap_wa_markup` embrulha sempre (`return f"{delim}{text}{delim}"`)
     → `9 failed, 9 passed`: caem os 9 testes de não-embrulho;
  2. `wrap_wa_markup` nunca embrulha (`return text`)
     → `9 failed, 9 passed`: caem os 9 controles positivos (partição limpa);
  3. `_MARCACAO_WA` volta ao filtro sem a fronteira do `_`
     → `1 failed, 17 passed`: só `test_categoria_legitima_mantem_o_negrito
     [meta_casa_nova]`, que é exatamente o que a precisão do `_` conserta;
  4. `core/handlers/bills.py:176/:297` voltam ao `*{paid.get('name')}*` literal
     → `2 failed, 16 passed`: os dois casos `luz *casa*` da porta digitada.

Os 2 `xfailed` são da #276 e ficam de fora dessas quatro de propósito: eles
afirmam o comportamento DESEJADO, não o atual. Que o `strict` está armado foi
medido no mesmo dia — virando a asserção de um deles para o estado de hoje, o
pytest devolve `[XPASS(strict)]` e `1 failed`. Ou seja: no dia em que a #276 for
consertada, este arquivo fica VERMELHO pedindo a remoção do marcador, e não
existe caminho em que o defeito seja lido como comportamento correto.

CONTROLE POSITIVO — o conserto RESTRINGE (passa a omitir o negrito), então
precisa provar que não engole o negrito de quem escreveu certo: `McDonald's`,
`café & pão`, `Cartão Nubank` (a mesma lista de tests/test_export_pdf_escape.py,
#145) e `meta_casa_nova` saem COM o par de asteriscos, e a pendência legada sai
com as crases. É a mutação 2 acima que prova que esse grupo mede alguma coisa.

CLASSE CEGA: a RENDERIZAÇÃO do cliente WhatsApp (Android/iOS) não é exercitável
aqui. O que se mede é a estrutura da STRING que sai da função real do handler —
se o embrulho de marcação está presente ou ausente. Isso NÃO é a tela: em pelo
menos um sítio (pending.py:90) ainda passa um `format_for_platform`
(core/handle_incoming.py:150) que remove pares de crase no WhatsApp.

ISSUE #276 — o helper decide POR ARGUMENTO, o WhatsApp pareia POR MENSAGEM.
Onde o template tem marcação PRÓPRIA além do embrulho, o `*` ímpar do usuário
casa com o do bot e o negrito vaza mesmo sem embrulho — medido pelo
`process_message` real em wa_runtime.py:805 (3 asteriscos) e :982/:984 (5). E há
um SEGUNDO mecanismo na mesma classe: bills.py:63 não tem marcação própria, mas
interpola o nome DUAS vezes, então o `*` do usuário pareia consigo mesmo (2
asteriscos). Fechar isso é mudança de desenho e fica fora deste PR — os dois
casos estão aqui como `xfail(strict=True)` afirmando o estado DESEJADO.

ESCOPO: os 7 sítios de `adapters/whatsapp/wa_runtime.py`, o
`pergunta_de_valor_sem_contexto` (core/handlers/bills.py:63, chamado de
wa_runtime.py:800), o `core/handlers/pending.py:90` e — porque é a MESMA
pergunta pela outra porta, com a string byte-idêntica — `core/handlers/
bills.py:176` e `:297`, que atendem quem DIGITA "paguei luz" em vez de tocar o
botão. Ficam FORA: `core/handlers/bills.py:169-170`, onde o nome cai DENTRO da
marcação do bot (`*paguei {nome} 132,50*`, forma que o helper não alcança por
construção), e os sítios restantes em ~20 arquivos.
"""
from __future__ import annotations

import pytest

import db
from adapters.whatsapp import wa_runtime
from core.handlers import bills as h_bills
from core.handlers import pending as h_pending
from core.response_formatter import format_for_platform

# A lista de nomes legítimos do #145 (tests/test_export_pdf_escape.py:105).
NOMES_LEGITIMOS = [("McDonald's", "mcdonald's"),
                   ("café & pão", "café & pão"),
                   ("Cartão Nubank", "cartão nubank")]


def _novo_launch(user_id: int) -> int:
    launch_id, _seq, _bal = db.add_launch_and_update_balance(
        user_id=user_id, tipo="despesa", valor=39.90,
        alvo="mercado", nota="mercado", categoria="outros",
    )
    return launch_id


# ─── wa_runtime:352 — categoria (a linha citada na issue) ────────────────────


@pytest.mark.parametrize("nome", [
    "a*b",            # delimitador IGUAL ao do template: o caso que o WJ não fechava
    "luz _extra_",     # underscore COM fronteira: pareia e italiza "extra"
    "conta_",          # termina em delimitador: com WJ o negrito do bot vazava
])
def test_categoria_com_marcacao_sai_sem_negrito(pro_user_id, nome):
    launch_id = _novo_launch(pro_user_id)

    msg = wa_runtime._apply_recategorize(pro_user_id, launch_id, nome)

    assert msg.endswith(f"atualizada para {nome}.")   # o nome inteiro, na tela
    assert f"*{nome}*" not in msg                     # e sem o embrulho


@pytest.mark.parametrize("nome,canon", NOMES_LEGITIMOS + [
    # MEDIDO pelo dono no cliente real em 2026-09-04: `meta_casa_nova` sai
    # LITERAL, sem itálico — underscore no MEIO de palavra não pareia. Não há
    # nada a neutralizar aqui, então o negrito do bot fica. Este caso já esteve
    # do lado errado da tabela, com o comentário afirmando que o WhatsApp
    # italizava "casa".
    ("meta_casa_nova", "meta_casa_nova"),
])
def test_categoria_legitima_mantem_o_negrito(pro_user_id, nome, canon):
    """CONTROLE POSITIVO: sem isto, um helper que nunca embrulha passaria."""
    launch_id = _novo_launch(pro_user_id)

    msg = wa_runtime._apply_recategorize(pro_user_id, launch_id, nome)

    assert msg.endswith(f"atualizada para *{canon}*.")


# ─── wa_runtime:822/1020 — nome da conta no caminho do dinheiro ─────────────


def test_conta_paga_com_asterisco_no_nome(monkeypatch):
    """Botão "✅ Já paguei" numa conta de valor fixo chamada `luz *casa*`."""
    replies: list[tuple[str, str]] = []
    _mock_wa_boot(monkeypatch, replies, uid=4242)
    monkeypatch.setattr(
        "db.bills.get_bill",
        lambda uid, bid: {"id": bid, "name": "luz *casa*", "status": "pending",
                          "variable_amount": False, "amount": 132.5},
    )
    monkeypatch.setattr(
        "db.bills.mark_bill_paid",
        lambda uid, bid: {"name": "luz *casa*", "paid_amount": 132.5},
    )

    wa_runtime.process_message(_botao(wa_runtime.WA_BILL_PAID_PREFIX + "41"))

    assert len(replies) == 1
    body = replies[0][1]
    assert "Conta paga: luz *casa* —" in body
    assert "*luz *casa**" not in body


def test_conta_paga_legitima_mantem_o_negrito(monkeypatch):
    """CONTROLE POSITIVO no caminho do dinheiro."""
    replies: list[tuple[str, str]] = []
    _mock_wa_boot(monkeypatch, replies, uid=4242)
    monkeypatch.setattr(
        "db.bills.get_bill",
        lambda uid, bid: {"id": bid, "name": "Cartão Nubank", "status": "pending",
                          "variable_amount": False, "amount": 132.5},
    )
    monkeypatch.setattr(
        "db.bills.mark_bill_paid",
        lambda uid, bid: {"name": "Cartão Nubank", "paid_amount": 132.5},
    )

    wa_runtime.process_message(_botao(wa_runtime.WA_BILL_PAID_PREFIX + "41"))

    assert "Conta paga: *Cartão Nubank* —" in replies[0][1]


# ─── wa_runtime:805 — pergunta de valor de conta variável ───────────────────


def _pergunta_de_valor(monkeypatch, nome: str) -> str:
    """Roda o `process_message` real até a pergunta de valor (wa_runtime.py:805)."""
    replies: list[tuple[str, str]] = []
    _mock_wa_boot(monkeypatch, replies, uid=4242)
    monkeypatch.setattr(
        "db.bills.get_bill",
        lambda uid, bid: {"id": bid, "name": nome, "status": "pending",
                          "variable_amount": True, "amount": None},
    )
    monkeypatch.setattr("adapters.whatsapp.wa_runtime.claim_pending_action",
                        lambda *a, **k: True)

    wa_runtime.process_message(_botao(wa_runtime.WA_BILL_PAID_PREFIX + "41"))

    assert len(replies) == 1
    return replies[0][1]


def test_pergunta_de_valor_com_asterisco_no_nome(monkeypatch):
    """O que ESTE PR fecha neste sítio: o embrulho sai.

    `conta_de_luz` estava aqui e era a única entrada da lista que ESCONDIA o
    vazamento da #276: sem asterisco nenhum, o total da mensagem ficava par de
    graça. Com `a*b` o sítio é de fato exercitado.
    """
    body = _pergunta_de_valor(monkeypatch, "a*b")

    assert "Quanto veio a conta de a*b este mês?" in body
    assert "*a*b*" not in body


@pytest.mark.xfail(strict=True, reason="issue #276: o WhatsApp pareia por MENSAGEM, "
                                       "e wrap_wa_markup decide por argumento")
def test_pergunta_de_valor_nao_deixa_asterisco_impar(monkeypatch):
    """O que a #276 fecha: delimitador PAREADO na mensagem inteira.

    Medido em 2026-09-04, o `process_message` real devolve:

        'Quanto veio a conta de a*b este mês?\nÉ só mandar o valor. Ex: *132,50*'

    TRÊS asteriscos: o embrulho saiu, mas o `*` do usuário pareia com o
    `*132,50*` do TEMPLATE e o negrito vaza na tela mesmo assim. A asserção
    abaixo é o estado DESEJADO, não o atual — `strict` de propósito: quando a
    #276 for consertada isto vira XPASS e a mensagem pede a remoção do marcador,
    em vez de parecer regressão.
    """
    body = _pergunta_de_valor(monkeypatch, "a*b")

    assert body.count("*") % 2 == 0


# ─── bills.py:63 — o irmão de wa_runtime.py:800, mesma variável ────────────


def test_pergunta_sem_contexto_com_underscore_no_nome(monkeypatch):
    """O `claim` perdeu a linha: o texto vem de core/handlers/bills.py e
    interpola o nome DUAS vezes."""
    monkeypatch.setattr(db, "get_pending_action", lambda uid: {})

    msg = h_bills.pergunta_de_valor_sem_contexto(9, "luz _extra_")

    assert msg.count("luz _extra_") == 2
    assert "*luz _extra_*" not in msg
    assert msg.count("*") == 0   # o template não tem marcação própria


@pytest.mark.xfail(strict=True, reason="issue #276: o WhatsApp pareia por MENSAGEM, "
                                       "e wrap_wa_markup decide por argumento")
def test_pergunta_sem_contexto_nao_deixa_asterisco_impar(monkeypatch):
    """A #276 por OUTRO mecanismo: aqui o template não tem marcação própria,
    mas interpola o nome DUAS vezes — o `*` do usuário pareia consigo mesmo
    através da mensagem. Medido em 2026-09-04: 2 asteriscos, e o que fica em
    negrito é `b tem valor variável... Me responde ela primeiro; a a`.
    """
    monkeypatch.setattr(db, "get_pending_action", lambda uid: {})

    msg = h_bills.pergunta_de_valor_sem_contexto(9, "a*b")

    assert msg.count("*") == 0


def test_pergunta_sem_contexto_legitima_mantem_o_negrito(monkeypatch):
    """CONTROLE POSITIVO."""
    monkeypatch.setattr(db, "get_pending_action", lambda uid: {})

    msg = h_bills.pergunta_de_valor_sem_contexto(9, "Cartão Nubank")

    assert msg.count("*Cartão Nubank*") == 2


# ─── bills.py:176/:297 — a porta GÊMEA de wa_runtime.py:822/1020 ───────────
#
# String byte-idêntica, mesma ação do mesmo usuário: quem toca o BOTÃO cai no
# wa_runtime, quem DIGITA "paguei luz" cai aqui. Sem estas duas linhas, metade
# dos usuários recebia a versão quebrada.


def _conta(nome: str, **extra) -> dict:
    return {"id": 41, "name": nome, "status": "pending",
            "variable_amount": False, "amount": 132.5, **extra}


@pytest.mark.parametrize("nome,esperado", [
    ("luz *casa*", "Conta paga: luz *casa* —"),      # marcação do usuário: sem embrulho
    ("Cartão Nubank", "Conta paga: *Cartão Nubank* —"),  # CONTROLE POSITIVO
])
def test_conta_paga_digitando_bills_176(monkeypatch, nome, esperado):
    """`paguei ...` no texto — bills.py:176, o irmão de wa_runtime.py:822."""
    monkeypatch.setattr("db.bills.list_bills", lambda uid, include_paid=False: [_conta(nome)])
    monkeypatch.setattr("db.bills.mark_bill_paid",
                        lambda uid, bid, amount=None: {"name": nome, "paid_amount": 132.5})

    msg = h_bills.try_pay_from_text(9, "paguei")

    assert esperado in msg


@pytest.mark.parametrize("nome,esperado", [
    ("luz *casa*", "Conta paga: luz *casa* —"),
    ("Cartão Nubank", "Conta paga: *Cartão Nubank* —"),  # CONTROLE POSITIVO
])
def test_conta_paga_respondendo_o_valor_bills_297(monkeypatch, nome, esperado):
    """Resposta só com o valor — bills.py:297, o irmão de wa_runtime.py:1020."""
    monkeypatch.setattr(db, "consume_pending_action", lambda uid, p: True)
    monkeypatch.setattr("db.bills.mark_bill_paid",
                        lambda uid, bid, amount: {"name": nome, "paid_amount": amount})
    pend = {"action_type": "bill_amount_expected",
            "payload": {"bill_id": 41, "bill_name": nome}}

    msg = h_bills.resolve_bill_amount(9, "132,50", pend)

    assert esperado in msg


# ─── core/handlers/pending.py:90 — a mensagem CRUA do usuário, em crase ────


def _pendencia_legada(monkeypatch, texto: str) -> str:
    pend = {"action_type": "confirm_media_launch", "payload": {"text": texto}}
    monkeypatch.setattr(db, "get_pending_action", lambda uid: pend)
    monkeypatch.setattr(db, "consume_pending_action", lambda uid, p: True)
    monkeypatch.setattr("core.services.quick_entry.handle_quick_entry",
                        lambda uid, text: None)
    return h_pending.resolve_delete(9, confirmed=True)


def test_pendencia_legada_com_crase_na_mensagem_crua(monkeypatch):
    """Medido na `main`: com ``paguei a `luz` ``, o `format_for_platform`
    (que remove crase no WhatsApp) pareia errado e sobram DUAS crases soltas
    na tela — `Tente: paguei a luz``."""
    bruto = _pendencia_legada(monkeypatch, "paguei a `luz`")

    assert bruto.endswith("Tente: paguei a `luz`")
    assert format_for_platform(bruto, "whatsapp").count("`") == 0


def test_pendencia_legada_legitima_mantem_a_crase(monkeypatch):
    """CONTROLE POSITIVO: a string CRUA do handler continua saindo em crase.

    Não é a tela: depois daqui ainda roda o `format_for_platform`
    (core/handle_incoming.py:150), que remove os pares de crase no WhatsApp.
    O que se prova é que o embrulho do helper não sumiu — o teste acima é que
    mede a tela, passando pelo `format_for_platform`.
    """
    bruto = _pendencia_legada(monkeypatch, "gastei 50 no mercado")

    assert bruto.endswith("Tente: `gastei 50 no mercado`")


# ─── plumbing do process_message (só monkeypatch de borda) ──────────────────


def _botao(interactive_id: str):
    from adapters.whatsapp.wa_parse import InboundMessage

    return InboundMessage(
        wa_id="5511988887777", text="", timestamp="123", attachments=[],
        raw={"id": "wamid." + interactive_id, "type": "interactive",
             "interactive": {"type": "button_reply",
                             "button_reply": {"id": interactive_id, "title": "✅ Já paguei"}}},
    )


def _mock_wa_boot(monkeypatch, replies: list, *, uid: int) -> None:
    monkeypatch.setattr(wa_runtime, "get_or_create_canonical_user",
                        lambda provider, external_id: uid)
    monkeypatch.setattr(wa_runtime, "attempt_whatsapp_phone_link",
                        lambda wa_id, current_user_id=None: {"status": "linked", "user_id": uid})
    monkeypatch.setattr(wa_runtime, "log_system_event_sync", lambda *a, **k: None)
    monkeypatch.setattr(wa_runtime, "send_typing_indicator", lambda *a, **k: None)
    monkeypatch.setattr(wa_runtime, "_seen_recent", lambda message_id: False)
    monkeypatch.setattr(wa_runtime, "_send_reply",
                        lambda to, body: replies.append((to, body)))
