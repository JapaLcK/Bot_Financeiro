"""
core/services/billing_copy.py — o que o bot DIZ sobre o estado da assinatura.

UM assunto: as mensagens que `plano` e `cancelar` entregam a quem **não** tem um
plano pago vigente. São três estados, e confundi-los é caro — o bot é o canal
principal e a mensagem sai não-solicitada para quem acabou de ser cortado.

**Saiu de `core/services/billing_commands.py` no teto de 350 linhas
(`tests/test_max_lines_python.py`), e é separação por assunto**: lá mora o
ROTEAMENTO (quais frases disparam qual handler, quem cede a vez para a
pendência); aqui, a COPY. Mesmo movimento do `db/dunning.py` saindo de
`db/plans.py`.

**Os três estados, e por que nenhum serve pelos outros:**

| estado | verdade | o que NÃO se pode dizer |
|---|---|---|
| nunca escolheu plano | não há assinatura, nunca houve | — |
| **carência de inadimplência** | a assinatura está **VIVA na Stripe**, em retentativa; o acesso ainda vale | "não há assinatura a cancelar" |
| cortado (sem direito, sem carência) | não há assinatura nem acesso | "tá tudo de graça mesmo" |

A linha do meio é a que custou: uma versão anterior mandava a mensagem do
cortado para quem está na carência, e ela afirma **"não há assinatura a
cancelar"** para alguém cujo cartão está sendo retentado. É caminho de dinheiro
— a pessoa quer PARAR a cobrança e o bot diz que não há o que parar — e o
`_handle_cancelar` daquele momento também não entregava o link do portal
(roteava por `is_pro`, False na carência), então o beco durava os 7 dias da
carência inteiros.

O JULGAMENTO mora aqui junto com a copy, e de propósito: `estado_sem_plano_pago`
é a pergunta "qual das três?" e as constantes são as respostas. Separá-los em
dois módulos deixaria a chave (`"carencia"`) num arquivo e o texto no outro, que
é como uma delas fica sem par sem ninguém notar.
"""

from __future__ import annotations

# Cadastro sem plano escolhido é BARRADO pelo bot (`_paywall_gate`), e `plano` /
# `cancelar` são justamente os comandos que ele é levado a mandar.
SEM_PLANO = (
    "🐷 Sua conta ainda não escolheu um plano — por isso eu ainda não consigo "
    "anotar nada por aqui, e não há assinatura a cancelar.\n\n"
    "Escolhe um e eu já começo: manda {assinar} 🐷✨"
)

# O ex-assinante CORTADO: escolheu plano um dia, não tem direito vigente e não
# tem carência aberta. As copies que ele recebia ("Plano: Grátis · 30
# lançamentos por mês", "tá tudo de graça mesmo") descreviam um lugar onde dava
# pra ficar, e o corte o tirou.
#
# A afirmação "nem assinatura a cancelar" só é segura AQUI, e é por isso que a
# carência tem mensagem própria logo abaixo.
SEM_ACESSO = (
    "🐷 Sua conta está sem plano ativo no momento — o PigBank não tem mais "
    "versão gratuita, então não há plano Grátis pra onde voltar nem assinatura "
    "a cancelar.\n\n"
    "Pra voltar a usar, escolhe um plano: manda {assinar} 🐷✨"
)

# CARÊNCIA: `plan_expires_at` já passou (tier `free`) mas o relógio de
# inadimplência está aberto, então `tem_direito_hoje` ainda concede acesso pelo
# lado direito do OR. A assinatura EXISTE e está em retentativa na Stripe.
#
# Não promete data de fim de propósito: quem decide quando a retentativa acaba é
# a Stripe, e `DUNNING_GRACE_DAYS` é a janela do NOSSO lembrete, não a dela.
#
# E não promete "seu acesso continua", que era o que ela dizia. Medido
# 2026-09-11 para a conta em carência: `has_app_access` é True, mas
# `get_plan_tier` já é `"free"` e `get_user_limits` devolve
# `{launches_month_max: 30, of_banks_max: 0, agents_max: 0,
#   history_current_month_only: True, pockets_max: 1}` — ela perde Open
# Finance, agentes e histórico no mesmo instante. A frase antiga prometia o que
# o produto não entrega, e no 31º lançamento a pessoa ouvia o limite nomeando o
# plano que as outras copies declaram inexistente.
#
# Os LIMITES não foram tocados aqui de propósito: `get_plan_tier` é de outro PR
# (a dívida está em `docs/dunning_estados_eventos.md`). O que este PR conserta é
# a copy parar de prometer o que ela não entrega.
COBRANCA_EM_ATRASO = (
    "🐷 A cobrança da sua assinatura não passou e a operadora está tentando de "
    "novo — eu continuo anotando por aqui enquanto isso, mas com os recursos "
    "reduzidos: 30 lançamentos no mês, histórico só do mês atual, e sem Open "
    "Finance nem agentes até a cobrança entrar.\n\n"
    "Pra atualizar o cartão ou encerrar a assinatura: manda {cancelar}"
)


def estado_sem_plano_pago(user_id: int, user: dict | None = None) -> str:
    """Em qual dos TRÊS estados sem-plano-pago esta conta está?

    `"sem_plano"` | `"carencia"` | `"sem_acesso"` — as chaves de
    `core.services.billing_copy`. Uma fonte só, porque `plano` e `cancelar`
    precisam do MESMO julgamento e antes cada um fazia o seu (o `cancelar`
    roteava por `is_pro`, que é False para Essencial e para a carência).

    **O predicado da carência é `carencia_aberta`, e NÃO `has_app_access`.** Uma
    versão anterior deduzia a carência de "ainda tem acesso", com o argumento de
    que, descartado o plano pago vigente, o acesso só podia vir do lado direito
    do OR de `tem_direito_hoje`. O argumento é falso todas as vezes em que
    `has_app_access` devolve True por CURTO-CIRCUITO, sem chegar ao OR:

    | curto-circuito | o que o cortado ouvia |
    |---|---|
    | `ACCESS_GATE_ENABLED=0` (freio do corte) | a copy da carência, e `cancelar` entregava o portal de uma assinatura que não existe |
    | `PLANS_V2_ENABLED=0` (freio da escada) | idem |
    | exceção (soluço de banco) | idem, pelo `except` daqui |

    O freio de emergência é a alavanca que se puxa às 3 da manhã se o corte der
    errado, e era exatamente aí que a copy passava a mentir para a base INTEIRA.
    Reusar um gate cujo contrato inclui "devolve True quando o freio está
    puxado" para responder uma pergunta sobre o RELÓGIO era reuso da função
    errada: `carencia_aberta` responde só o relógio, e não tem freio nenhum.

    `user` é buscado uma vez e serve às duas pernas — em produção o cache de 10 s
    do `get_auth_user` esconderia a diferença, mas se o TTL expirar entre elas a
    copy sai de dois julgamentos distintos.

    Import defensivo pelo mesmo motivo do `_handle_plano`: testes (e deploys sem
    a escada v2) mockam plan_service só com `is_pro`.
    """
    try:
        from core.services.plan_service import needs_plan_selection
    except ImportError:
        return "sem_acesso"
    # `sem_plano` ganha de `carencia`: carência com `plan_selected_at` NULL ouve a
    # copy de quem não escolheu. Alcançabilidade ~nula — `mark_plan_selected` roda
    # no `checkout.session.completed` E no `invoice.paid`, então quem tem
    # assinatura na Stripe já tem a marca. Decisão registrada, não esquecimento.
    if needs_plan_selection(user_id, user):
        return "sem_plano"
    try:
        from datetime import datetime, timezone

        from core.services.billing_dunning import carencia_aberta
        if user is None:
            from db import get_auth_user
            user = get_auth_user(int(user_id))
        aberta = bool(user) and carencia_aberta(
            user.get("past_due_since"),
            user.get("last_payment_status"),
            datetime.now(timezone.utc),
        )
        return "carencia" if aberta else "sem_acesso"
    except Exception:
        # "Não sei" NÃO vira "não tem" numa copy: `sem_acesso` afirmaria que não
        # há assinatura a cancelar. `carencia` só oferece o portal, que não
        # fecha porta nenhuma.
        return "carencia"
