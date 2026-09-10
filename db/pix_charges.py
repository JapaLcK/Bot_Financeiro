"""
db/pix_charges.py — escrita e leitura da tabela `pix_charges`.

A cobrança é o SNAPSHOT FINANCEIRO da venda Pix: preço, crédito e valor
congelam na criação; `access_starts_at` é decidido no PAGAMENTO (§7 do plano).

Plano: docs/plano_pix_anual_asaas.md §3.2, §10, §11 e §13.6.

**Deixou de ser inerte no 1b-B**: os chamadores são o checkout
(`core/services/pix_checkout.py`) e o dreno (`core/services/pix_drain.py`), e
entraram no MESMO PR de propósito — emitir cobrança sem já possuir o caminho que
recebe o pagamento é o que o corte do dono proíbe. Quem mede é a
`CHAMADORES_PERMITIDOS` de `tests/test_pix_inerte.py`, que virou allowlist.

Três regras que valem para TODA escrita daqui:

  • **Uma cobrança ativa por usuário é do BANCO** (`uniq_pix_charge_ativa`), e
    não de um `select`+`insert` em Python: duas requisições concorrentes seriam
    precificadas contra o mesmo crédito (§10).
  • **Transição é condicional** (`update … where status = <esperado> returning`).
    O `returning` vazio reúne "já estava lá" e "foi para outro terminal" — e
    **não** autoriza pular efeito nenhum (§8.2 C; ver `transicionar`). Ler o
    status e depois escrever seria a mesma corrida com mais linhas.
  • **Toda query do dado do usuário filtra por `user_id`** (CLAUDE.md §0), com
    **TRÊS exceções, e uma delas é ESCRITA**. Todas do dreno, todas nomeadas
    nas próprias funções:

    | função | leitura/escrita | chave |
    |---|---|---|
    | `buscar_por_external_reference` | leitura | `external_reference` (unique) |
    | `buscar_por_asaas_payment_id`   | leitura | `asaas_payment_id` (unique) |
    | `transicionar`                  | **ESCRITA** | `charge_id` |

    O motivo é o mesmo para as três: o dreno não tem usuário na requisição, e
    a cobrança pode legitimamente ter `user_id is null` (conta excluída, §13.4).

    **Mas a de escrita é chaveada por `charge_id`, que é um bigserial
    ENUMERÁVEL** — e isso não é detalhe de estilo. Ela é segura HOJE só
    porque o único chamador é o dreno, e o `charge_id` dele veio de uma busca
    pelo `asaas_payment_id`/`external_reference` do próprio evento assinado: não
    há entrada do usuário no caminho.

    **A armadilha, e o 1b-B a respeitou:** um endpoint "cancelar minha cobrança"
    que chame `transicionar(charge_id=<vindo da URL>)` é um IDOR — A cancela a
    cobrança de B trocando um número. O caminho do usuário passa por
    `buscar_por_public_token`/`buscar_ativa` (que FILTRAM por dono) e usa o `id`
    que elas devolveram, nunca um `charge_id` cru da requisição.

`attach_pagamento` e `gravar_stripe_period_end` moram em `db/pix_charges_saga.py`
desde o 1b-B, e a ressalva do `charge_id` enumerável vale igual para as duas.
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
    # As três de rastreio: sem elas o dreno manda a venda ao GA4 sem origem e o
    # Purchase da Meta sem `fbp`/`fbc` (§3.2). `qr_payload_enc` fica FORA (§13.6).
    "refunded_at, purged_at, ga_client_id, fbp, fbc"
)


def criar_cobranca(
    user_id: int,
    *,
    public_token: str,
    plan: str,
    plan_stored: str,
    price_cents: int,
    credit_cents: int,
    amount_cents: int,
    duration_days: int,
    stripe_subscription_id: str | None = None,
    stripe_period_end_at=None,
    rastreio: dict[str, str] | None = None,
) -> dict | None:
    """Cria a cobrança em `draft`. Devolve a linha, ou **`None`** quando o
    usuário JÁ tem uma cobrança ativa.

    `None` é a corrida perdida, não um erro: quem chama decide entre reaproveitar
    a cobrança existente e substituí-la (§10), e a substituição cancela no Asaas
    ANTES de criar a nova. O `on conflict do nothing` é sobre o índice PARCIAL
    `uniq_pix_charge_ativa`, então uma cobrança `paid`/`canceled`/`expired` do
    mesmo dono não bloqueia venda nova.

    **`external_reference` NÃO é parâmetro: é gerado aqui, como `pix:<id>`.**
    Ele era `str` livre, e o formato importa — o dreno só reconhece como NOSSO
    dinheiro o que casa `^pix:[0-9]+$` (§8.2 A, §11). Uma referência fora do
    formato faz o pagamento ser classificado como de terceiro e **descartado em
    silêncio**, que é o oposto do que a célula `orphan_unknown` existe para
    impedir. Os próprios testes deste repositório usavam `pix:<hex>` — ou seja,
    o parâmetro livre já tinha ensinado o formato errado (P1-3 do Codex).

    O `id` vem de um `nextval` ANTES do insert, e não de um `insert` seguido de
    `update`: a referência precisa existir na mesma linha que a cria, e um
    segundo comando deixaria uma janela em que a cobrança existe sem referência.
    A constraint `pix_charges_ref_formato` fecha o resto — nem esta função pode
    contorná-la.

    O `on conflict` cobre **só** o índice parcial. Repetir `public_token`
    levanta `UniqueViolation`, e isso é o certo: ele é `secrets.token_urlsafe(16)`,
    então repetição é corrupção, não caso de negócio — falhar alto é a única
    hora em que alguém vê. O chamador do 1b-B não deve envolver isto num
    `except` que devolva `None`: `None` significa "já existe cobrança ativa", e
    confundir as duas faria uma colisão de token virar "tente de novo".

    `stripe_period_end_at` é a ESTIMATIVA da migração (§8.2), gravada aqui com
    um valor que o checkout já leu — sem chamada extra ao Stripe. Ela é
    reconfirmada no efeito `stripe_cancel`, e é o que tira o `NULL` do caminho
    comum quando o fallback das 6 falhas precisar dela.

    `rastreio` são os identificadores de anúncio já sanitizados pelo endpoint
    (`ga_client_id`, `fbp`, `fbc`), mesma forma que `_billing_checkout_for_user`.
    Chave ausente vira `NULL` e é o normal (cookie bloqueado, visita orgânica).
    O dreno as lê em `ga4`/`capi`; a exclusão de conta as zera (§13.2).
    """
    rastreio = rastreio or {}
    with get_conn() as conn:
        with conn.cursor() as cur:
            # O id primeiro, para a referência nascer com ele na MESMA linha.
            cur.execute(
                "select nextval(pg_get_serial_sequence('pix_charges', 'id')) as id"
            )
            novo_id = int(cur.fetchone()["id"])
            cur.execute(
                "insert into pix_charges "
                " (id, user_id, external_reference, public_token, plan, plan_stored,"
                "  price_cents, credit_cents, amount_cents, duration_days,"
                "  stripe_subscription_id, stripe_period_end_at,"
                "  ga_client_id, fbp, fbc, status)"
                " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
                "         %s, %s, %s, 'draft')"
                # Inferência pelo índice PARCIAL: `uniq_pix_charge_ativa` é
                # `create unique index … where`, não uma constraint, então
                # `on conflict on constraint` não o alcança — o Postgres só casa
                # o índice se o predicado vier junto. O predicado é MONTADO de
                # `ESTADOS_ATIVOS` em vez de reescrito (CLAUDE.md §0.7).
                + _CONFLITO_INDICE_PARCIAL +
                f" returning {_COLUNAS}",
                (novo_id, int(user_id), f"pix:{novo_id}", public_token, plan, plan_stored,
                 int(price_cents), int(credit_cents), int(amount_cents),
                 int(duration_days), stripe_subscription_id,
                 stripe_period_end_at, rastreio.get("ga_client_id"),
                 rastreio.get("fbp"), rastreio.get("fbc")),
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

    ## `None` NÃO DECIDE EFEITO NENHUM — nem para pular, nem para rodar

    **`None` reúne duas situações OPOSTAS**, e é por isso que ele não serve de
    porteiro de coisa alguma:

      * **já está no destino** — a reentrega do mesmo evento. Os efeitos daquele
        evento podem não ter rodado, e têm de rodar;
      * **passou para um terminal que CONTRADIZ o evento** — a cobrança já é
        `refunded` e chega um `RECEIVED` atrasado. Aqui rodar `grant` é o erro.

    Os dois chegam aqui como o MESMO `None`, e quem escrever o dreno do 1b-B
    precisa ler isto antes de usar o retorno como condição.

    **O que autoriza pular um efeito por JÁ TER RODADO é o registro daquele
    efeito** (`pix_payment_effects`, par `(asaas_payment_id, effect)`), nunca o
    resultado desta transição — o §3.4 do plano já dizia isso enquanto o §8.2 C
    dizia o contrário. Vale porque aquela tabela **nunca é purgada** (não tem
    categoria no §13.1); se um dia ganhar retenção, esta garantia volta a ter furo.

    **O que autoriza pular um efeito por NÃO FAZER MAIS SENTIDO é PERGUNTA
    ABERTA, e ela é do 1b-B.** Não há resposta aqui de propósito: os cenários
    concretos (o `RECEIVED` atrasado numa cobrança já estornada, o parcial
    entregue fora de ordem, dois parciais somando 100%) estão medidos e
    registrados como **pendências obrigatórias do 1b-B** no §17.1 de
    `docs/plano_pix_anual_asaas.md`. Escolher aqui fixaria em contrato uma
    decisão que o dono não tomou.

    A regra `aplicou == False → efeitos = []` **existiu e foi removida** (P1-A,
    Codex no #304), porque era um ponto de perda de dinheiro: esta função
    **commita sozinha, antes dos efeitos**. Morrendo o processo — ou levantando
    um efeito — depois desse commit, o evento segue pendente na outbox; a
    retentativa chega aqui, recebe `None` porque a cobrança já é
    `paid`/`refunded`, e sob a regra antiga rodava **zero efeitos**. Cliente
    pago sem grant, ou cliente estornado com acesso para sempre.

    É o defeito do #298 um nível abaixo: lá o `UPDATE … WHERE status` perdia
    efeitos, e o conserto foi escrever o `processed_at` DEPOIS deles.

    Sem a condição no `where`, ler e depois escrever deixaria a janela entre as
    duas para o retry do Asaas — por isso ela fica.

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


def rezerar_rastreio_de_orfas() -> int:
    """A varredura diária do §13.2, e o outro lado do UPDATE de `db/privacy.py`.

    Aquele UPDATE (`:936`) não é a garantia: quem desfaz o vínculo é a FK
    `on delete set null`, e entre ele e o `delete from users` cabe um webhook que
    commite depois — a linha fica com `user_id` nulo e `purged_at` NUNCA escrito.
    Esta passada é quem alcança essas. `purged_at is null` a torna datável: sem
    ele o carimbo seria reescrito todo dia. Predicado DIFERENTE do da outbox de
    propósito — `pix_webhook_events` não tem `user_id` (§13.3).
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update pix_charges"
                "   set ga_client_id = null, fbp = null, fbc = null,"
                "       qr_payload_enc = null, asaas_customer_id = null,"
                "       purged_at = now()"
                " where user_id is null and purged_at is null"
            )
            rezeradas = cur.rowcount
        conn.commit()
    return rezeradas


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
