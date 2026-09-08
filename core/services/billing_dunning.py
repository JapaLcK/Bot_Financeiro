"""
core/services/billing_dunning.py — vocabulário da inadimplência de cartão.

Este módulo NÃO tira acesso de ninguém. Quem está com a cobrança do cartão
falhada continua com o plano e o bot exatamente como antes; o que existe aqui é
a mecânica de cobrança: a lista de status que descreve "cartão em atraso" e a
janela de 7 dias usada pelo lembrete de pagamento
(`core/services/payment_reminder.py`) e pela dedupe do e-mail de falha no
webhook. A regra de acesso é assunto de outro PR — não escreva aqui, nem em
mensagem, e-mail ou docstring, nada que prometa perda de acesso.

**A INVARIANTE**: relógio (`auth_accounts.past_due_since`) não nulo só existe
em conta cujo `last_payment_status` está em `PAST_DUE_PAYMENT_STATUSES`. Quem a
mantém é ESTRUTURAL e mora na escrita: `db_support.set_payment_status_impl`
zera o relógio no MESMO UPDATE quando o status vai para fora da lista, e
`core/admin_dashboard.set_account_plan` faz o mesmo no SQL cru dele. Ler a
docstring de `set_payment_status_impl` antes de mexer em qualquer um dos lados.

Órfão (relógio com status fora da lista) NÃO é dado morto: enquanto ele
existir, o próximo `invoice.payment_failed` devolve o status para a lista, o
`claim_past_due_since` vê `rowcount 0` e o relógio do ciclo novo fica preso na
data velha — a conta já nasce fora da janela `[6d, 7d)` e o lembrete de
pagamento daquele ciclo nunca sai. Coberta por teste nos dois writers em
`tests/test_billing_dunning.py`, e na sequência real do webhook (grant Pix
vigente → `recompute_entitlement`) por `test_T4_*` de
`tests/test_billing_dunning_webhook.py`.

Import ZERO de propósito: são duas constantes, e `core/admin_dashboard`,
`db/plans.py`, `db_support.py` e o webhook importam daqui. Quem trouxer uma
função para cá importa `db`/`plan_service` DENTRO dela — mesmo padrão de
`trial_downsell.py` e `billing_access.py`.
"""

# Status crus de `last_payment_status` (o webhook grava o do Stripe sem
# traduzir, `db_support.set_payment_status`) que descrevem uma cobrança de
# cartão EM ATRASO com a assinatura ainda VIVA lá: 'unpaid' é dunning e
# 'incomplete' é 3DS pendente — nenhum dos dois é terminal (os terminais são
# 'canceled' e 'incomplete_expired').
#
# FONTE ÚNICA da lista (§0.7). Ela nasceu em `core/admin_dashboard.py`, que
# decide com ela o rótulo do painel e o gate de `/trial-reset`; mora aqui
# porque `admin_dashboard` importa fastapi/bcrypt/jwt/slowapi e é caro de
# importar (meça antes de reusar o argumento:
# `python3 -X importtime -c "import core.admin_dashboard" 2>&1 | tail -1`).
# O admin passou a importar daqui, e `_LIVE_PAYMENT_STATUSES` continua se
# compondo dela. NÃO crie uma quarta lista.
PAST_DUE_PAYMENT_STATUSES = ("past_due", "unpaid", "incomplete")

# A janela da inadimplência: 7 dias contados de `auth_accounts.past_due_since`.
# Constante de módulo, não env — é regra de produto (decisão do dono), e não
# parâmetro de rollout. Neste PR ela decide DUAS coisas e nenhuma delas é
# acesso: o dia do lembrete de pagamento (`payment_reminder`, janela
# `[DUNNING_GRACE_DAYS - 1, DUNNING_GRACE_DAYS)`) e a janela de dedupe do
# e-mail de falha de pagamento no webhook.
DUNNING_GRACE_DAYS = 7
