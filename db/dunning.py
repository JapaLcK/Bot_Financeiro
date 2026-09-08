"""
db/dunning.py — o relógio da inadimplência de cartão (`auth_accounts.past_due_since`).

Camada de banco de UM assunto: a PRIMEIRA falha de cobrança do ciclo. O
vocabulário (lista de status, janela, largura) mora em
`core/services/billing_dunning`; quem lê o funil é
`core/services/payment_reminder`; quem escreve são os ramos de cobrança do
webhook do Stripe. **NADA de acesso depende desta coluna.**

Saiu de `db/plans.py` (que estava em 350/350, o teto de
`tests/test_max_lines_python.py`) quando o predicado de status entrou no
`claim`. É separação por assunto, não por tamanho: `plans.py` é trial e escada
de planos, e estas três funções são cobrança. Importe daqui — não há
re-export em `db/plans.py`, de propósito (§0.7).
"""

from __future__ import annotations

import logging

from .connection import get_conn

logger = logging.getLogger(__name__)


def claim_past_due_since(user_id: int) -> bool:
    """Carimba o início da inadimplência. True se foi ESTA chamada que carimbou.

    A idempotência é SQL, não Python (decisão do dono): UMA instrução com
    `past_due_since is null` no `where`, sem read-modify-write. A Stripe manda
    um `invoice.payment_failed` por smart retry, e reentrega o mesmo evento em
    cima de 5xx — o relógio NÃO pode reiniciar em nenhum dos dois casos.

    **O `where` também exige o status ATUAL em `PAST_DUE_PAYMENT_STATUSES`, e
    isso é a INVARIANTE mantida na escrita, não código defensivo.** Sem o
    predicado, o carimbo era incondicional e criava o órfão que a invariante
    declara impossível: `invoice.payment_failed` e `invoice.paid` são duas
    requisições, cada uma com o próprio `set_payment_status`, e há um `await`
    entre o `set_payment_status(past_due)` do ramo falho e este UPDATE (ele roda
    em `asyncio.to_thread`). No intervalo, o ramo pago escreve `active` e zera o
    relógio — e o UPDATE incondicional o repunha com o status já fora da lista.
    O guard de `Subscription.retrieve` do ramo falho NÃO fecha isso: ele roda
    ANTES do `set_payment_status`, então a corrida entre os nossos dois handlers
    continua aberta depois dele. A normalização é a mesma de
    `list_payment_reminder_candidates` (`lower(coalesce(...))`) e a lista vem da
    constante, nunca de literal (§0.7).

    O `rowcount` sai de graça e diz se foi esta chamada que abriu o ciclo (um
    `coalesce` no `set` seria idempotente também, mas o `RETURNING` veria o
    valor novo e não diria isso). **NÃO o use como SUPRESSOR de e-mail**: ele já
    foi a chave do "seu pagamento falhou" e o carimbo COMMITA antes do envio,
    então SMTP fora do ar na 1ª entrega calava o ciclo inteiro (medido: 1ª
    entrega + 3 reentregas da Stripe = 0 e-mails). Quem suprime é o `_fire_email`
    do webhook, que grava a chave DEPOIS de o envio confirmar; ele usa este
    `rowcount` só para AMPLIAR (ciclo novo → manda mesmo dentro da janela de
    dedupe), nunca para calar.

    Com o predicado novo, `rowcount 0` passa a ter DOIS significados — "já
    carimbado" e "status não elegível" — e o uso no webhook continua correto
    justamente porque ele só AMPLIA: os dois casos caem em
    `dedup_days=DUNNING_GRACE_DAYS`, que é a janela normal, e não em "não
    mande". No caso novo (a corrida acima) isso é o comportamento desejado por
    si: quem acabou de pagar não precisa de um "sua cobrança falhou" com
    `dedup_days=0`.
    """
    from core.services.billing_dunning import PAST_DUE_PAYMENT_STATUSES
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update auth_accounts set past_due_since = now()"
                " where user_id = %s and past_due_since is null"
                "   and lower(coalesce(last_payment_status, '')) = any(%s)",
                (int(user_id), list(PAST_DUE_PAYMENT_STATUSES)),
            )
            carimbou = cur.rowcount == 1
        conn.commit()
    from db_support import invalidate_auth_user_cache
    invalidate_auth_user_cache(user_id)
    return carimbou


def clear_past_due_since(user_id: int) -> None:
    """Zera o relógio: pagou, cancelou ou a assinatura morreu — ciclo fechado.

    INCONDICIONAL de propósito: quem decide se o evento tem autoridade para
    fechar o ciclo é o CHAMADOR, e nos ramos `checkout.session.completed` e
    `invoice.paid` isso é o retorno de `_materializar_assinatura` (False =
    evento velho, recusado pela guarda de versão de `upsert_grant`). Chamar isto
    sem olhar aquele retorno era zerar o relógio por ordem de um evento que já
    tinha sido descartado para o ACESSO.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update auth_accounts set past_due_since = null where user_id = %s",
                (int(user_id),),
            )
        conn.commit()
    from db_support import invalidate_auth_user_cache
    invalidate_auth_user_cache(user_id)


def list_payment_reminder_candidates(grace_days: int = 7) -> list[dict]:
    """Contas do LEMBRETE DE PAGAMENTO: cartão em atraso, a partir do 6º dia.

    Janela `[grace_days - 1, grace_days - 1 + PAYMENT_REMINDER_WINDOW_DAYS)` de
    idade de `past_due_since`. A largura é constante nomeada e não `1` porque a
    cadência real do tick é MAIOR que 24 h: o `sleep` de
    `run_engagement_loop` só começa depois de todo o trabalho de engajamento,
    trial, nudge e lembrete, e um restart ou uma falha operacional atrasam
    muito mais que isso. Com janela de exatamente 24 h, um tick antes de a
    conta entrar + o tick seguinte depois de ela sair = o único lembrete do
    ciclo nunca sai. A largura tem TETO: veja a invariante em
    `core/services/billing_dunning` (largura < janela de dedupe), que é o que
    impede o mesmo ciclo de receber dois lembretes.

    O funil grosso é SQL (status, relógio, e-mail, opt-out); o filtro fino que
    precisa de Python (allowlist, grant pix/admin) é do chamador, como em
    `list_trial_downsell_candidates`.
    """
    from core.services.billing_dunning import (
        PAST_DUE_PAYMENT_STATUSES,
        PAYMENT_REMINDER_WINDOW_DAYS,
    )
    inicio = int(grace_days) - 1
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select user_id, email, email_enc
                from auth_accounts
                where past_due_since is not null
                  and lower(coalesce(last_payment_status, '')) = any(%s)
                  and coalesce(engagement_opt_out, false) = false
                  and email is not null and email <> ''
                  and past_due_since <= now() - make_interval(days => %s)
                  and past_due_since >  now() - make_interval(days => %s)
                """,
                (list(PAST_DUE_PAYMENT_STATUSES),
                 inicio, inicio + PAYMENT_REMINDER_WINDOW_DAYS),
            )
            return [dict(r) for r in cur.fetchall() or []]
