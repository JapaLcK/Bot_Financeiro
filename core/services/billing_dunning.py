"""
core/services/billing_dunning.py — vocabulário da inadimplência de cartão.

Este módulo NÃO tira acesso de ninguém, e isso continua verdade DEPOIS do corte
do Grátis: quem está com a cobrança do cartão falhada e o plano vigente continua
com o produto exatamente como antes. O que existe aqui é a mecânica de cobrança
— a lista de status que descreve "cartão em atraso" e a janela de 7 dias — mais
`carencia_aberta`, que agora CONCEDE acesso (nunca subtrai) pelo lado direito do
OR de `plan_service.tem_direito_hoje`.

**São TRÊS os consumidores da janela, não dois** (a enumeração anterior parava
em dois e envelheceu no PR do aviso de corte):

  1. o lembrete de pagamento (`core/services/payment_reminder.py`);
  2. a dedupe do e-mail de falha no webhook;
  3. o **aviso de fim do Grátis e, desde o PR A, o próprio GATE DE ACESSO** —
     `carencia_aberta` (abaixo) é o lado direito do OR de
     `core.services.plan_service.tem_direito_hoje`, que `has_app_access`
     consulta e que a população de `scripts/aviso_fim_do_gratis.py` nega no
     `where`. São os mesmos três consumidores; o terceiro é que ganhou peso.

**A DIREÇÃO DO OR importa e é o que segura as células 18, 29 e 30 de
`docs/dunning_estados_eventos.md` fora daquele trabalho**: a autoridade é o
direito pago; o relógio só CONCEDE tempo a quem já o perdeu, nunca subtrai. Lê
isto antes de escrever qualquer gate — lido como autoridade, o status de
cobrança bloquearia cliente pagante por um ciclo inteiro de retentativa.

A regra de acesso CHEGOU (PR A, #274/#354): `has_app_access` consulta
`tem_direito_hoje`, e quem não tem direito pago nem carência aberta perde o
produto. O que NÃO mudou é de quem é a autoridade — o par
(`plan`, `plan_expires_at`), nunca o status de cobrança.

**A copy do lembrete do 6º dia continua PROIBIDA de prometer corte**
(`email_service.send_payment_reminder_email`): ele sai dentro da carência, com
o acesso ainda de pé, e prometer perda que não veio naquele dia é mentira na
mesma medida que a antiga. Quem PODE falar em perda de acesso são o e-mail de
falha e o de cancelamento, que foram reescritos junto.

**A INVARIANTE**: relógio (`auth_accounts.past_due_since`) não nulo só existe
em conta cujo `last_payment_status` está em `PAST_DUE_PAYMENT_STATUSES`. Quem a
mantém é ESTRUTURAL e mora na escrita: `db_support.set_payment_status_impl`
zera o relógio no MESMO UPDATE quando o status vai para fora da lista, e
`core/admin_dashboard.set_account_plan` faz o mesmo no SQL cru dele. Ler a
docstring de `set_payment_status_impl` antes de mexer em qualquer um dos lados.

**Uma escrita a mantém pela ORDEM, e não pelo `CASE`**: a perna terminal do
`customer.subscription.deleted` grava `unpaid` (que está DENTRO da lista, então
o `CASE` PRESERVA) e só então chama `db.dunning.encerrar_ciclo_de_atraso`.
Invertida, o clear viraria no-op e o par ficaria órfão — ver a célula 32.

Órfão (relógio com status fora da lista) NÃO é dado morto: enquanto ele
existir, o próximo `invoice.payment_failed` devolve o status para a lista, o
`claim_past_due_since` vê `rowcount 0` e o relógio do ciclo novo fica preso na
data velha — a conta já nasce fora da janela do lembrete e o lembrete daquele
ciclo nunca sai. Coberta por teste nos dois writers em
`tests/test_billing_dunning.py`, e na sequência real do webhook (grant Pix
vigente → `recompute_entitlement`) por `test_T4_*` de
`tests/test_billing_dunning_webhook.py`.

Import ZERO de propósito: são só constantes, e `core/admin_dashboard`,
`db/dunning.py`, `db_support.py`, `core/services/payment_reminder.py` e o
webhook importam daqui. Quem trouxer uma função para cá importa
`db`/`plan_service` DENTRO dela — mesmo padrão de `trial_downsell.py` e
`billing_access.py`.
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
# acesso: o DIA em que o lembrete de pagamento começa a valer
# (`payment_reminder`, janela abrindo em `DUNNING_GRACE_DAYS - 1`) e a janela de
# dedupe do e-mail de falha de pagamento no webhook.
DUNNING_GRACE_DAYS = 7

# ── A JANELA DO LEMBRETE, e a invariante que amarra os dois números ──────────
#
# `db.dunning.list_payment_reminder_candidates` aceita a conta enquanto a idade
# de `past_due_since` estiver em
# `[DUNNING_GRACE_DAYS - 1, DUNNING_GRACE_DAYS - 1 + PAYMENT_REMINDER_WINDOW_DAYS)`,
# e `payment_reminder` deduplica o envio por `PAYMENT_REMINDER_DEDUPE_DAYS` em
# `system_event_logs`.
#
# **INVARIANTE: PAYMENT_REMINDER_WINDOW_DAYS < PAYMENT_REMINDER_DEDUPE_DAYS.**
# Ela é o que garante UM lembrete por ciclo: a conta fica elegível por
# WINDOW_DAYS, então o tick seguinte a encontra de novo e só a dedupe o cala.
# Largura ≥ dedupe volta a permitir dois lembretes no mesmo ciclo. Amarrada por
# `tests/test_billing_dunning.py::test_invariante_janela_menor_que_dedupe` —
# quem mexer num dos dois números vê o outro por causa dele.
#
# Por que 3 e não 1: a cadência real do tick é MAIOR que 24 h (o
# `asyncio.sleep` de `run_engagement_loop` só começa DEPOIS de todo o trabalho
# de engajamento, trial, nudge e lembrete) e restart/falha operacional atrasam
# muito mais — um deploy no meio do tick sumia com o lembrete de quem estava
# numa janela de exatamente 24 h. Com 3 dias, um tick INTEIRO perdido ainda
# entrega. Medido pelo Tester na janela de 1 dia: 0,00 % de perda com tick de
# 2 s, 0,03 % com 30 s, 0,14 % com 120 s — a simulação não modelava restart, e
# é ele que eleva o caso.
PAYMENT_REMINDER_WINDOW_DAYS = 3
PAYMENT_REMINDER_DEDUPE_DAYS = 6.0


def carencia_aberta(past_due_since, last_payment_status, agora) -> bool:
    """A carência de `DUNNING_GRACE_DAYS` deste ciclo ainda está correndo?

    Os três termos são o par relógio × status da INVARIANTE mais a idade:
    relógio carimbado, `last_payment_status` em `PAST_DUE_PAYMENT_STATUSES`, e
    MENOS de `DUNNING_GRACE_DAYS` desde o carimbo. Qualquer outra combinação é
    False.

    **Ela só CONCEDE tempo, nunca tira acesso.** Quem chama a usa no lado
    DIREITO de um OR cujo lado esquerdo é o direito pago
    (`plan_service.tem_direito_hoje`): False aqui não bloqueia ninguém que
    tenha plano vigente. É isso que impede o status de cobrança de virar
    autoridade sobre o acesso — lido como autoridade, ele bloquearia cliente
    pagante por um ciclo inteiro de retentativa (célula 29 de
    `docs/dunning_estados_eventos.md`).

    Mora aqui porque `DUNNING_GRACE_DAYS` mora aqui (§0.7): o 7 não se repete
    em `plan_service`. O módulo continua com import ZERO no topo — o `datetime`
    entra DENTRO da função, o padrão que a docstring do módulo prescreve.

    Normalização IDÊNTICA à do SQL irmão (`db/dunning.py`,
    `lower(coalesce(last_payment_status, ''))`): `lower()` e nada mais. Sem
    `strip()`, de propósito — o que o SQL não apara, o Python também não pode
    aparar, ou os dois lados discordam em `' past_due '`.

    Naive vira UTC nos dois lados: `past_due_since` é `timestamptz` e chega
    aware do banco, mas script e teste podem passar naive, e misturar naive com
    aware levanta TypeError no meio de uma decisão de acesso.
    """
    if past_due_since is None:
        return False
    if (last_payment_status or "").lower() not in PAST_DUE_PAYMENT_STATUSES:
        return False
    from datetime import timedelta, timezone
    if past_due_since.tzinfo is None:
        past_due_since = past_due_since.replace(tzinfo=timezone.utc)
    if agora.tzinfo is None:
        agora = agora.replace(tzinfo=timezone.utc)
    return agora - past_due_since < timedelta(days=DUNNING_GRACE_DAYS)
