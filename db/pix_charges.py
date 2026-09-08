"""
db/pix_charges.py — escrita e leitura da tabela `pix_charges`.

A cobrança é o SNAPSHOT FINANCEIRO da venda Pix: preço, crédito e valor
congelam na criação; `access_starts_at` é decidido no PAGAMENTO (§7 do plano).

Plano: docs/plano_pix_anual_asaas.md §3.2, §10, §11 e §13.6.

**Fatia INERTE (PR 1b-A): nenhum módulo de produção importa este arquivo.**
Os chamadores — checkout e dreno do webhook — são o PR 1b-B, e os dois entram
juntos de propósito: nenhuma versão intermediária pode emitir cobrança sem já
possuir o caminho que recebe o pagamento e concede o acesso.

Três regras que valem para TODA escrita daqui:

  • **Uma cobrança ativa por usuário é do BANCO** (`uniq_pix_charge_ativa`), não
    de um `select`+`insert` em Python. Duas requisições concorrentes seriam
    precificadas contra o mesmo crédito (§10).
  • **Transição é condicional** (`update … where status = <esperado> returning`).
    O `returning` vazio é a resposta a "reentrega ou corrida?", e é ele que
    zera a lista de efeitos no dreno (§8.2 C). Ler o status e depois escrever
    seria a mesma corrida com mais linhas.
  • **Toda query do dado do usuário filtra por `user_id`** (CLAUDE.md §0), com
    **QUATRO exceções, e duas delas são ESCRITA**. Todas do dreno, todas
    nomeadas nas próprias funções:

    | função | leitura/escrita | chave |
    |---|---|---|
    | `buscar_por_external_reference` | leitura | `external_reference` (unique) |
    | `buscar_por_asaas_payment_id`   | leitura | `asaas_payment_id` (unique) |
    | `transicionar`                  | **ESCRITA** | `charge_id` |
    | `attach_pagamento`              | **ESCRITA** | `charge_id` |

    O motivo é o mesmo para as quatro: o dreno não tem usuário na requisição, e
    a cobrança pode legitimamente ter `user_id is null` (conta excluída, §13.4).

    **Mas as duas de escrita são chaveadas por `charge_id`, que é um bigserial
    ENUMERÁVEL** — e isso não é detalhe de estilo. Elas são seguras HOJE só
    porque o único chamador é o dreno, e o `charge_id` dele veio de uma busca
    pelo `asaas_payment_id`/`external_reference` do próprio evento assinado: não
    há entrada do usuário no caminho.

    **Quem for escrever o 1b-B leia isto antes:** um endpoint "cancelar minha
    cobrança" que chame `transicionar(charge_id=<vindo da URL>)` é um IDOR — o
    usuário A cancela a cobrança de B trocando um número. O caminho do usuário
    passa por `buscar_por_public_token` (que FILTRA por dono) e usa o `id` que
    ela devolveu; nunca um `charge_id` cru da requisição.
"""

from __future__ import annotations

from .connection import get_conn

# Estados em que a cobrança ocupa o índice parcial de "ativa" (§3.2). Fonte
# única: o DDL de `db/schema.py` declara a MESMA lista, e o teste
# `test_estados_ativos_batem_com_o_indice_parcial` compara as duas
# (CLAUDE.md §0.7).
ESTADOS_ATIVOS = ("draft", "creating", "pending", "canceling")

_CONFLITO_INDICE_PARCIAL = (
    " on conflict (user_id) where status in ("
    + ", ".join(f"'{s}'" for s in ESTADOS_ATIVOS)
    + ") do nothing"
)

_COLUNAS = (
    "id, user_id, external_reference, asaas_payment_id, asaas_customer_id, "
    "plan, plan_stored, price_cents, credit_cents, amount_cents, currency, "
    "duration_days, stripe_subscription_id, stripe_cancel_scheduled_at, "
    "stripe_period_end_at, public_token, status, due_date, qr_expires_at, "
    "access_starts_at, access_expires_at, created_at, paid_at, canceled_at, "
    "refunded_at, purged_at"
)


def criar_cobranca(
    user_id: int,
    *,
    external_reference: str,
    public_token: str,
    plan: str,
    plan_stored: str,
    price_cents: int,
    credit_cents: int,
    amount_cents: int,
    duration_days: int,
    stripe_subscription_id: str | None = None,
    stripe_period_end_at=None,
) -> dict | None:
    """Cria a cobrança em `draft`. Devolve a linha, ou **`None`** quando o
    usuário JÁ tem uma cobrança ativa.

    `None` é a corrida perdida, não um erro: quem chama decide entre reaproveitar
    a cobrança existente e substituí-la (§10), e a substituição cancela no Asaas
    ANTES de criar a nova. O `on conflict do nothing` é sobre o índice PARCIAL
    `uniq_pix_charge_ativa`, então uma cobrança `paid`/`canceled`/`expired` do
    mesmo dono não bloqueia venda nova.

    O `on conflict` cobre **só** o índice parcial. Repetir `external_reference`
    ou `public_token` levanta `UniqueViolation`, e isso é o certo: os dois são
    gerados por nós (`pix:<id>` e `secrets.token_urlsafe(16)`), então repetição
    é corrupção, não caso de negócio — falhar alto é a única hora em que alguém
    vê. O chamador do 1b-B não deve envolver isto num `except` que devolva
    `None`: `None` significa "já existe cobrança ativa", e confundir as duas
    faria uma colisão de token virar "tente de novo mais tarde".

    `stripe_period_end_at` é a ESTIMATIVA da migração (§8.2), gravada aqui com
    um valor que o checkout já leu — sem chamada extra ao Stripe. Ela é
    reconfirmada no efeito `stripe_cancel`, e é o que tira o `NULL` do caminho
    comum quando o fallback das 6 falhas precisar dela.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "insert into pix_charges "
                " (user_id, external_reference, public_token, plan, plan_stored,"
                "  price_cents, credit_cents, amount_cents, duration_days,"
                "  stripe_subscription_id, stripe_period_end_at, status)"
                " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'draft')"
                # Inferência pelo índice PARCIAL: `uniq_pix_charge_ativa` é
                # `create unique index … where`, não uma constraint, então
                # `on conflict on constraint` não o alcança — o Postgres só casa
                # o índice se o predicado vier junto. O predicado é MONTADO de
                # `ESTADOS_ATIVOS` em vez de reescrito (CLAUDE.md §0.7).
                + _CONFLITO_INDICE_PARCIAL +
                f" returning {_COLUNAS}",
                (int(user_id), external_reference, public_token, plan, plan_stored,
                 int(price_cents), int(credit_cents), int(amount_cents),
                 int(duration_days), stripe_subscription_id,
                 stripe_period_end_at),
            )
            row = cur.fetchone()
        conn.commit()
    return dict(row) if row else None


def transicionar(
    charge_id: int,
    *,
    de: str | tuple[str, ...],
    para: str,
    asaas_payment_id: str | None = None,
    access_starts_at=None,
    access_expires_at=None,
    apagar_qr: bool = False,
) -> dict | None:
    """Transição CONDICIONAL da máquina de estados (§11). Devolve a linha nova
    quando a transição aplicou; **`None`** quando o status atual não é `de`.

    `None` é o que o dreno lê como "reentrega ou já decidido por outro caminho",
    e é ele que zera a lista de efeitos (§8.2 C) — reentrega do mesmo
    `PAYMENT_RECEIVED` não reexecuta nada. Sem a condição no `where`, ler e
    depois escrever deixaria a janela entre as duas para o retry do Asaas.

    `de` aceita tupla porque a MESMA célula do §11 tem vários estados de origem:
    `RECEIVED` concede a partir de `pending`, `canceling`, `canceled` e
    `expired` (pagamento tardio válido até 60 dias).

    `apagar_qr` zera `qr_payload_enc` no destino terminal (§13.6): depois de
    `paid`/`canceled`/`expired` confirmado não há uso legítimo do instrumento
    de pagamento, e o que escapar é limpo pela varredura.

    **Sem `user_id` no `where`, de propósito** (§0): quem chama é o dreno do
    webhook, que não tem usuário na requisição e trata justamente a cobrança
    cujo `user_id` pode ser NULL (conta excluída, §13.4). O `charge_id` já veio
    de uma busca pelo `asaas_payment_id`/`external_reference` do próprio evento.

    **A segurança disto é do CHAMADOR, não desta função.** `charge_id` é
    bigserial enumerável: chamada com um id vindo de URL, ela transiciona a
    cobrança de qualquer um. Ver a tabela das quatro exceções no topo do módulo.
    """
    estados = (de,) if isinstance(de, str) else tuple(de)
    # Os carimbos de data são posicionais por status, e não parâmetros soltos:
    # marcar `paid_at` numa transição para `canceled` seria mentira gravada.
    carimbo = {
        "paid": ", paid_at = now()",
        "paid_orphan": ", paid_at = now()",
        "canceled": ", canceled_at = now()",
        "refunded": ", refunded_at = now()",
        "refunded_partial": ", refunded_at = now()",
    }.get(para, "")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update pix_charges"
                "   set status = %s,"
                "       asaas_payment_id = coalesce(%s, asaas_payment_id),"
                "       access_starts_at = coalesce(%s, access_starts_at),"
                "       access_expires_at = coalesce(%s, access_expires_at),"
                "       qr_payload_enc = case when %s then null else qr_payload_enc end"
                + carimbo
                + " where id = %s and status = any(%s)"
                f" returning {_COLUNAS}",
                (para, asaas_payment_id, access_starts_at, access_expires_at,
                 bool(apagar_qr), int(charge_id), list(estados)),
            )
            row = cur.fetchone()
        conn.commit()
    return dict(row) if row else None


def attach_pagamento(charge_id: int, asaas_payment_id: str, *, qr_payload_enc: str | None = None,
                     due_date=None, qr_expires_at=None) -> bool:
    """Grava o id remoto na cobrança `creating` e a leva a `pending`. Devolve
    True se aplicou.

    É a saída do estado AMBÍGUO do §10: o POST ao Asaas pode ter efetivado com a
    resposta perdida, então a varredura reconcilia por `externalReference` e
    chama isto. `where asaas_payment_id is null` faz o attach ser idempotente
    sem `select` antes — duas passadas da varredura não sobrescrevem o id que a
    primeira gravou, e a segunda devolve False em vez de mentir.

    `qr_payload_enc` chega **já cifrado** por quem chama (§13.6): este módulo não
    decide política de PII, e o QR nunca aparece em log, em `details` de
    auditoria nem em mensagem de erro.

    Sem `user_id` no `where` pelo mesmo motivo do `transicionar` — e com a mesma
    ressalva: `charge_id` é enumerável, então a segurança é do chamador.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update pix_charges"
                "   set asaas_payment_id = %s, status = 'pending',"
                "       qr_payload_enc = coalesce(%s, qr_payload_enc),"
                "       due_date = coalesce(%s, due_date),"
                "       qr_expires_at = coalesce(%s, qr_expires_at)"
                " where id = %s and status in ('creating', 'draft')"
                "   and asaas_payment_id is null"
                " returning id",
                (asaas_payment_id, qr_payload_enc, due_date, qr_expires_at,
                 int(charge_id)),
            )
            aplicou = cur.fetchone() is not None
        conn.commit()
    return aplicou


def buscar_por_public_token(user_id: int, public_token: str) -> dict | None:
    """A leitura do POLL (`GET /billing/pix/{token}`), e por isso ela filtra por
    `user_id` (CLAUDE.md §0).

    `public_token` é o único identificador que sai do servidor (§13.6): ele vai
    para a URL do poll, para o `sid` do `/home?upgrade=success`, para o
    `transaction_id` do GA4 e para o `event_id` da CAPI — e o `sid` é lido pelo
    pixel da Meta (`frontend/home.html:2151`). O `asaas_payment_id` nunca sai
    daqui; buscar por ele nesta função tem de dar 404 (caso 42b do §16).

    O filtro por dono não é redundante com a unique do token: token é `unsafe`
    do ponto de vista do handler — ele chega na URL, então vem do cliente. Sem
    `user_id`, um token vazado devolve a cobrança (plano, valores, datas) de
    outra pessoa.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"select {_COLUNAS} from pix_charges"
                " where user_id = %s and public_token = %s",
                (int(user_id), public_token),
            )
            row = cur.fetchone()
    return dict(row) if row else None


def buscar_por_external_reference(external_reference: str) -> dict | None:
    """Leitura do DRENO e da varredura — **sem `user_id`, e é o certo**.

    O webhook do Asaas não traz usuário e a cobrança pode ter `user_id is null`
    (conta excluída, §13.4): exigir dono aqui transformaria o pagamento órfão em
    "cobrança inexistente" e o dinheiro viraria `orphan_unknown` sem linha.
    `external_reference` é gerado por nós (`pix:<id>`) e é `unique`, então não há
    entrada de usuário escolhendo o que é lido.
    """
    return _buscar_por("external_reference", external_reference)


def buscar_por_asaas_payment_id(asaas_payment_id: str) -> dict | None:
    """Leitura do DRENO — sem `user_id` pelo mesmo motivo de
    `buscar_por_external_reference`. É a busca PRIMÁRIA do §8.2: o `payment.id`
    é estável entre eventos irmãos do mesmo pagamento."""
    return _buscar_por("asaas_payment_id", asaas_payment_id)


def _buscar_por(coluna: str, valor: str) -> dict | None:
    # `coluna` NUNCA vem de fora: só as duas funções acima chamam, com literal.
    assert coluna in ("external_reference", "asaas_payment_id")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"select {_COLUNAS} from pix_charges where {coluna} = %s",
                (valor,),
            )
            row = cur.fetchone()
    return dict(row) if row else None
