"""
db/pix_effects.py — o registro de efeitos executados por pagamento do Asaas.

Uma tabela, `pix_payment_effects`: uma linha por par `(asaas_payment_id, effect)`,
que é o que impede o dreno de conceder acesso, mandar e-mail ou disparar o
`purchase` do GA4 duas vezes sobre o mesmo dinheiro.

Saiu de `db/webhook_outbox.py` no 1b-B, **movimento puro**: a outbox é a fila de
entrega do webhook, isto aqui é o livro-caixa do que já foi executado — dois
assuntos, duas tabelas, dois arquivos. A tabela é criada em `db/schema.py`.

Plano: docs/plano_pix_anual_asaas.md §3.4 e §8.2.

Quem chama é o dreno (`core/services/pix_drain.py`), e ele é o único —
`tests/test_pix_inerte.py::CHAMADORES_PERMITIDOS` mede isso.
"""

from __future__ import annotations

from contextlib import contextmanager

from .connection import get_conn


# Efeitos válidos do §3.4. A lista existe para o insert recusar typo — um
# `'grantt'` gravado seria um efeito que NUNCA é encontrado pela consulta e que
# portanto reexecuta para sempre.
EFEITOS = (
    "stripe_cancel", "grant", "ga4", "capi", "email", "revoke",
    "orphan_notified",
)


def efeito_registrado(asaas_payment_id: str, effect: str) -> bool:
    """O par `(asaas_payment_id, effect)` já rodou? (§3.4, correção nº 8b.)

    Chave pelo PAGAMENTO e não pelo evento: `PAYMENT_RECEIVED` reentregue com
    `event_id` NOVO — que a plataforma pode emitir — reexecutaria `ga4`, `capi`
    e `email` se a chave fosse o evento. Receita duplicada no GA4 e segundo
    Purchase na CAPI em cima do mesmo dinheiro.

    **`False` aqui não é permissão para executar: é a leitura de um instante.**
    Entre este `select` e o `registrar_efeito` que o segue, outra passada do
    dreno pode ter respondido `False` à mesma pergunta. Quem fecha essa janela é
    o DRENO, serializando por `(asaas_payment_id, effect)` — ver
    `registrar_efeito`.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select 1 from pix_payment_effects"
                " where asaas_payment_id = %s and effect = %s",
                (asaas_payment_id, effect),
            )
            return cur.fetchone() is not None


def registrar_efeito(asaas_payment_id: str, effect: str, event_id: str) -> bool:
    """Marca o efeito como executado. Devolve **False** se outra passada já o
    tinha registrado.

    O `on conflict do nothing` fecha a janela que o `efeito_registrado` sozinho
    deixa: entre a consulta e o registro cabe outra passada do dreno. A consulta
    evita o TRABALHO no caso comum; este insert evita a LINHA duplicada — as duas
    são necessárias e nenhuma substitui a outra.

    **O par consulta+insert protege a LINHA, e NÃO o trabalho externo.** Ele é
    idempotência de BOOKKEEPING: quando duas passadas concorrentes leem `False` e
    ambas executam, o e-mail já saiu duas vezes, o `purchase` do GA4 e o
    `Purchase` da CAPI já foram enviados duas vezes, e este insert só desempata
    depois — devolvendo `False` a uma delas para uma execução que já aconteceu.

    E o `for update skip locked` do dreno (1b-B) **não** serializa esse caso:
    dois eventos DISTINTOS do mesmo `payment.id` (um `PAYMENT_CONFIRMED` e um
    `PAYMENT_RECEIVED`, ou uma reentrega com `event_id` novo) travam linhas de
    OUTBOX diferentes, e nada em `pix_webhook_events` os põe em fila. A chave que
    precisa ser serializada é `(asaas_payment_id, effect)`, que não é a chave da
    linha travada.

    **Serializar por `(asaas_payment_id, effect)` é obrigação do DRENO**, e ela
    tem de segurar a consulta, a execução externa e o registro dentro do mesmo
    escopo — não só o insert. Sem isso, este módulo garante uma linha por par, e
    nada sobre quantas vezes o mundo lá fora foi tocado. Apontamento do Codex no
    #304; direções em avaliação no §17.1 do plano.

    `event_id` é forense: diz QUAL entrega executou o efeito. Nada é decidido
    por ele.
    """
    if effect not in EFEITOS:
        raise ValueError(f"efeito desconhecido: {effect!r}")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "insert into pix_payment_effects (asaas_payment_id, effect, event_id)"
                " values (%s, %s, %s)"
                " on conflict (asaas_payment_id, effect) do nothing"
                " returning effect",
                (asaas_payment_id, effect, event_id),
            )
            novo = cur.fetchone() is not None
        conn.commit()
    return novo


@contextmanager
def lock_efeito(asaas_payment_id: str, effect: str):
    """Serializa `(asaas_payment_id, effect)` entre passadas e entre processos,
    segurando **consulta + execução externa + registro** no mesmo escopo.

    É a resposta à pendência 5 do §17.1, e ela reusa o precedente que já existe
    no repositório: `pg_advisory_lock(hashtext(%s))`, o mesmo do
    `_billing_user_lock` (`frontend/finance_bot_websocket_custom.py:4093`).
    Nada de broker, nada de coluna nova, nada de chave de idempotência inventada.

    **A chave é o PAR, não o `event_id`.** Dois eventos DISTINTOS do mesmo
    `payment.id` (o `CONFIRMED` e o `RECEIVED`, ou uma reentrega com `event_id`
    novo) travam linhas de OUTBOX diferentes, então o `for update skip locked`
    de `reservar_evento` não os põe em fila. Sem isto os dois leem
    `efeito_registrado is False`, os dois executam, e saem dois e-mails, dois
    `purchase` no GA4 e dois `Purchase` na CAPI sobre o mesmo dinheiro — o
    `on conflict do nothing` de `registrar_efeito` só desempata DEPOIS.

    **Por efeito, e não por pagamento nem global**: o `email` de um pagamento
    não pode esperar o `ga4` de outro. Um lock mais grosso passa no teste de
    duplicata (D5-a) e mata a vazão de toda venda.

    **Quem pega isso é D5-b, e só depois de 2026-09-09.** Até então ele afirmava
    pegar e não pegava: media CONTADOR, e lock global **serializa sem duplicar**,
    então `"pix_effect:GLOBAL"` deixava os dois testes verdes — medido. Hoje ele
    mede SOBREPOSIÇÃO (`threading.Barrier(2)` dentro do efeito falso), que é a
    propriedade que a chave decide de fato. Se você afrouxar esta chave, é lá que
    fica vermelho.

    `ponytail:` teto — o lock morre com o processo, então a janela "executou e
    morreu antes de registrar" continua aberta. Ela é fechada do outro lado,
    por desenho e sem código novo: GA4 dedupe por `transaction_id`, a CAPI por
    `event_id` (§13.6), o e-mail por `recent_event_exists`, `grant` é criação
    única e `revoke` é idempotente. Se um efeito NOVO não tiver dedupe própria,
    é aqui que se lê que ele não está coberto.
    """
    chave = f"pix_effect:{asaas_payment_id}:{effect}"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select pg_advisory_lock(hashtext(%s))", (chave,))
            try:
                yield
            finally:
                # `unlock` no `finally` e não no fim do bloco: o lock é de
                # SESSÃO e a conexão volta para o pool viva — sem isto, uma
                # exceção no efeito deixaria a chave travada para o processo
                # inteiro, e o próximo evento daquele par nunca mais drenaria.
                cur.execute("select pg_advisory_unlock(hashtext(%s))", (chave,))
