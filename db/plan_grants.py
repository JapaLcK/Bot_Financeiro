"""
db/plan_grants.py — escrita e leitura da tabela `plan_grants`.

O grant é o REGISTRO do direito de acesso. `auth_accounts.plan`/`plan_expires_at`
deixam de ser escritos à mão pelo webhook e viram projeção desta tabela
(core/services/billing_access.recompute_entitlement).

Plano: docs/plano_pix_anual_asaas.md §3.1, §5.1, §6 e §6.1.

Duas regras que valem para TODA escrita daqui:

  • **Toda escrita carrega `event_version`** (§6). É o que impede evento antigo
    de desfazer o estado atual quando o Stripe reentrega fora de ordem.
  • **Toda query filtra por `user_id`** (CLAUDE.md §0). A unique é
    `(source, external_ref)`, que é global; sem o filtro, um `external_ref`
    repetido entre contas vazaria direito de um usuário para outro.
"""

from __future__ import annotations

from .connection import get_conn

# Fontes válidas da coluna `source`. 'pix' e as tabelas do Asaas chegam no PR 1b.
GRANT_SOURCES = ("stripe", "pix", "legacy", "admin")

# §6 — evento com versão MENOR OU IGUAL nunca reescreve uma concessão, e
# NENHUM evento reescreve grant de outro dono.
#
# `plan_grants.user_id = excluded.user_id` é o isolamento do CLAUDE.md §0 dentro
# do upsert. A unique é `(source, external_ref)`, que é GLOBAL: sem esta linha,
# um upsert do usuário B com o `external_ref` do usuário A cai no `do update` e
# reescreve plano e validade da linha de A. O `where` do `on conflict` é o único
# lugar onde esse filtro cabe — o `insert` não tem cláusula `where`. Colisão
# entre donos devolve None (não aplicou), que é o resultado conservador certo:
# `external_ref` de `stripe` é id de assinatura, globalmente único, então
# colisão entre contas já é corrupção, não caso de negócio.
#
# O `>` estrito é metade da regra do §6.1 ("no empate, a revogação ganha"): a
# CONCESSÃO que empata é descartada aqui. A outra metade — a revogação que
# empata e APLICA — mora no `event_version <= %s` do `revoke_grant`, porque
# revogar é UPDATE, não upsert: este INSERT nasce sempre `status='active'`, e
# uma cláusula de desempate por `excluded.status='revoked'` seria inalcançável.
# Ela chegou a existir aqui: removê-la não deixou um único teste vermelho, que é
# a definição de código morto — e código morto num caminho de acesso pago é o
# que faz a próxima pessoa achar que a regra está coberta quando não está.
_GUARDA_VERSAO = """
     where plan_grants.user_id = excluded.user_id
       and excluded.event_version > plan_grants.event_version
"""

_SUPERSEDE_LEGACY = """
    update plan_grants
       set status = 'revoked', revoked_reason = 'superseded_by_stripe',
           revoked_at = now(), event_version = %s, updated_at = now()
     where user_id = %s and source = 'legacy' and status = 'active'
"""

# Marca de AUDITORIA do reparo da varredura (§4.1.1 D, célula 7): serve para
# alguém abrir a linha e saber quem mexeu por último. **Nenhuma decisão é tomada
# por ele** — nem aqui, nem em `billing_access`. A versão anterior o usava como
# cláusula da reclassificação abaixo, e isso era o R2-1: aquele ramo não
# comparava versão nenhuma, então depois de um reparo um `invoice.paid` de 2023
# deixava de ser obsoleto e autorizava escrita de acesso. Pior, o marcador só
# saía quando um evento aplicasse o upsert, então numa conta parada ele ficava
# indefinidamente. Marcador não é autorização.
REPARO_EVENT_ID = "reparo:varredura"

# NÃO É EVENTO VELHO: o `returning` do upsert vem vazio em situações que não
# têm nada a ver umas com as outras, e classificar pelo RESULTADO do upsert
# confunde as três. A classificação certa é por COMPARAÇÃO DE VERSÃO, contra a
# linha que existe:
#
#   • `event_version <= %s` — empate NÃO é evento velho. `event["created"]` da
#     Stripe tem precisão de SEGUNDOS (§6.1 chama o empate de caso real), então
#     empatam tanto a REENTREGA do mesmo evento (retry do 5xx) quanto dois
#     eventos IRMÃOS do mesmo segundo — `checkout.session.completed` e
#     `invoice.paid` de uma compra imediata. Comparar `last_event_id` aqui, como
#     esta query fazia, tratava o irmão como evento velho e deixava a conta
#     `trialing` com a fatura paga;
#     O REPARO não precisa de cláusula aqui, e não pode ter uma: ele não
#     escreve `event_version` (invariante 3 do §4.1.1 D), então nunca bloqueia
#     evento nenhum. A cláusula que existia (`or last_event_id = REPARO_...`)
#     era o R2-1 — ver o comentário do `REPARO_EVENT_ID` acima;
#   • `status = 'active'` — sem ele a reclassificação RESSUSCITARIA grant
#     revogado: `revoke_grant` preserva o `last_event_id`
#     (`coalesce(%s, last_event_id)`) e, no empate do §6.1, carimba a MESMA
#     versão da concessão. É a invariante 1 do §4.1.1 D pela porta dos fundos;
#   • `user_id` no filtro (CLAUDE.md §0): a unique é `(source, external_ref)`,
#     GLOBAL — sem ele a colisão entre donos devolveria o id do OUTRO usuário,
#     que é exatamente o caso que a guarda do upsert recusa.
_NAO_E_EVENTO_VELHO = """
    select id from plan_grants
     where user_id = %s and source = %s and external_ref = %s
       and status = 'active'
       and event_version <= %s
"""


def upsert_grant(
    user_id: int,
    source: str,
    external_ref: str,
    plan_stored: str,
    starts_at,
    ends_at,
    event_version: int,
    last_event_id: str | None = None,
) -> int | None:
    """Cria ou estende um grant. Devolve o `id` quando o grant EXISTE com este
    evento; `None` quando o evento **não se aplica**.

    O contrato do retorno é o que o §4.1.2 consome, e ele NÃO é "o upsert
    aplicou?": o `returning` vem vazio em situações sem relação umas com as
    outras. A pergunta certa é **"um evento MAIS NOVO já decidiu este grant?"**,
    respondida por comparação de versão contra a linha existente:

    | situação                             | retorno | por quê |
    |--------------------------------------|---------|---------|
    | aplicou (criou ou estendeu)          | `id`    | `returning` do upsert |
    | **reentrega do MESMO evento**        | `id`    | versão IGUAL não é mais nova |
    | **evento IRMÃO do mesmo segundo**    | `id`    | idem — `checkout` + `invoice.paid` empatam (§6.1) |
    | evento VELHO (versão estritamente menor) | `None` | outro evento decidiu |
    | grant REVOGADO, outro dono           | `None`  | nunca foi para aplicar |

    `None` significa **"não aplicável"**, e nunca "falhou": quem recebe `None`
    não deve reescrever acesso (§4.1.1 A), e quem recebe `id` pode seguir com os
    efeitos do evento mesmo que esta não seja a primeira entrega dele — é
    exatamente o que faz o 5xx da Stripe ser retryable em vez de virar evento
    obsoleto.

    O grant `source='pix'` (`do nothing`) passa a devolver o `id` do grant ATIVO
    existente sempre que nenhum evento mais novo o superou. Não há chamador
    hoje; **o consumidor é o PR 1b**, e é para ele que este parágrafo existe.

    `source='pix'` é **criação única** (§6): depois de criado, um grant Pix só
    muda por operação explícita — `revoke_grant` (estorno/chargeback) ou a
    antecipação do §4.4. A janela é decidida uma vez, no pagamento; não existe
    caso legítimo de "evento posterior recalcula a janela do mesmo pagamento".

    Supersessão do `legacy` (§5.1): todo upsert APLICADO de grant `stripe`
    revoga, na MESMA transação, o `legacy` do mesmo usuário. Evento velho
    bloqueado pela guarda não supersede — o `returning` vem vazio. Isso fecha os
    três caminhos que a v4 deixava abertos: grant `stripe` que ENCURTA, de tier
    MENOR, e assinatura que lapsa sem `deleted` (`unpaid`).
    """
    if source not in GRANT_SOURCES:
        raise ValueError(f"source inválido: {source!r}")

    conflito = (
        "on conflict (source, external_ref) do nothing"
        if source == "pix"
        else (
            "on conflict (source, external_ref) do update"
            "   set plan_stored = excluded.plan_stored,"
            "       starts_at = excluded.starts_at,"
            "       ends_at = excluded.ends_at,"
            "       status = excluded.status,"
            "       event_version = excluded.event_version,"
            "       last_event_id = excluded.last_event_id,"
            "       revoked_reason = null, revoked_at = null,"
            "       updated_at = now()"
            + _GUARDA_VERSAO
        )
    )

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "insert into plan_grants (user_id, source, external_ref, plan_stored,"
                "                         starts_at, ends_at, status, event_version, last_event_id)"
                " values (%s, %s, %s, %s, %s, %s, 'active', %s, %s) "
                + conflito
                + " returning id",
                (int(user_id), source, external_ref, plan_stored,
                 starts_at, ends_at, int(event_version), last_event_id),
            )
            row = cur.fetchone()
            # A supersessão fica AQUI, amarrada ao `returning` do upsert, e NÃO
            # à reclassificação abaixo: `_SUPERSEDE_LEGACY` não tem guarda de
            # versão, então rodá-la na reentrega revogaria o `legacy` com uma
            # `event_version` MENOR que a do grant vigente (caso 14 do §16).
            if row and source == "stripe":
                cur.execute(_SUPERSEDE_LEGACY, (int(event_version), int(user_id)))
            if row is None:
                cur.execute(_NAO_E_EVENTO_VELHO,
                            (int(user_id), source, external_ref,
                             int(event_version)))
                row = cur.fetchone()
        conn.commit()
    return int(row["id"]) if row else None


def esticar_grant(user_id: int, source: str, external_ref: str, ends_at) -> bool:
    """Move o `ends_at` de UM grant ATIVO para frente. É o reparo da célula 7
    (§4.1.1 D) — e é `UPDATE`, não `upsert_grant`, de propósito.

    **O reparo NÃO escreve `event_version`** (invariante 3): aquela coluna é
    marca d'água de EVENTO, e o reparo não é evento. Ele lê o Stripe numa
    varredura e corrige uma DATA que ficou para trás.

    O upsert obrigava o contrário: para passar pela guarda
    `excluded.event_version > plan_grants.event_version` ele precisava carimbar
    `epoch(now())`, e desse carimbo saíram dois defeitos medidos — um
    `subscription.deleted` atrasado deixava de revogar (`revoke_grant` exige
    `event_version <= %s`), e o remendo que compensava isso na reclassificação
    aceitava evento de qualquer idade (R2-1).

    Este `UPDATE` é **estritamente mais estreito** que o upsert, e é por isso
    que ele FECHA a célula 7 em vez de abrir:

      • não tem `insert`: não pode recriar grant que sumiu;
      • `status = 'active'` no `where` faz a **invariante 1** (o reparo nunca
        ressuscita) virar condição de BANCO, em vez de precondição em Python a
        três linhas de distância;
      • `plan_stored` e `starts_at` não estão no `set`: a **invariante 2** (o
        reparo nunca escolhe tier) vira estrutural;
      • `ends_at < %s` faz "só estica, nunca encurta" ser condição do próprio
        `UPDATE`, em vez de depender de a célula 6 ter cortado antes.

    `last_event_id` recebe o `REPARO_EVENT_ID` só como AUDITORIA — ninguém
    decide nada por ele.

    Consequência declarada: o reparo deixa de disparar a supersessão do
    `legacy`, que era efeito colateral do `upsert_grant` de `source='stripe'`.
    É o certo pelo §5.1 — quem mata o `legacy` é o primeiro EVENTO real. O que
    torna isso SEGURO hoje não é a direção do erro: é que o `legacy` só existe para quem NÃO
    tem grant nenhum: o `RESYNC_LEGACY_GRANTS_SQL` (`db/schema.py`) filtra por
    `and not exists (select 1 from plan_grants g where g.user_id = a.user_id)`.
    Então `legacy` ativo nunca coexiste com `stripe` ativo, e não há o que
    supersedir na hora do reparo. **Se alguém relaxar aquele `not exists`, isto
    acorda**: um `legacy` do backfill carrega o `plan_expires_at` antigo, que
    pode ir MUITO além do que o Stripe cobra — o Tester mediu 270 dias de acesso
    a mais. A direção do erro NÃO é conservadora; quem segura é o `not exists`.

    Devolve True se alguma linha foi esticada.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update plan_grants"
                "   set ends_at = %s, last_event_id = %s, updated_at = now()"
                " where user_id = %s and source = %s and external_ref = %s"
                "   and status = 'active' and ends_at < %s"
                " returning id",
                (ends_at, REPARO_EVENT_ID, int(user_id), source, external_ref,
                 ends_at),
            )
            aplicou = cur.fetchone() is not None
        conn.commit()
    return aplicou


def revoke_grant(
    user_id: int,
    source: str,
    external_ref: str,
    reason: str,
    event_version: int,
    last_event_id: str | None = None,
) -> bool:
    """Revoga UM grant, identificado por `(user_id, source, external_ref)`.

    **Revoga só o que o evento nomeia.** Existiu aqui um `external_ref=None` que
    varria todos os grants ativos da `source`; foi apagado por não ter caso
    alcançável que o justificasse — o objeto `Subscription` do Stripe sempre
    traz `id`, e o único lugar onde ele faltava era uma fixture sintética de
    teste. O caminho amplo custava dinheiro: com duas assinaturas vivas, o
    `deleted` da ANTIGA revogava junto a NOVA já paga e rebaixava quem estava
    em dia. Caminho sem prova, num fluxo de acesso pago, é o que faz a próxima
    pessoa achar que a regra está coberta quando não está.

    **`event_version <= %s`, e não `<`: é aqui que mora o §6.1.**
    `event["created"]` do Stripe tem precisão de SEGUNDOS, então `invoice.paid` e
    `customer.subscription.deleted` podem empatar; com `<` o segundo a chegar
    seria descartado em silêncio e o acesso passaria a depender da ordem de
    entrega. Com `<=` o estado final é o MESMO nas duas ordens:

      paid → deleted: o deleted empata, revoga             → revoked
      deleted → paid: o paid empata, o `>` do upsert corta → revoked

    É também o erro conservador certo: negar acesso indevidamente é visível e
    reparável pelo grant `admin` (§15); conceder é irreversível na direção que
    dói (produto entregue, dinheiro não).

    ponytail: teto conhecido — dois eventos de MESMA semântica no mesmo segundo
    (duas concessões com `ends_at` diferentes) mantêm a primeira. Não é caso
    conhecido; se aparecer, o upgrade é ordenar por `(created, id)`.
    Teto 2: o empate só converge quando a linha do grant JÁ EXISTE. Se o
    `deleted` chega antes de qualquer grant, não há o que revogar e o `paid`
    seguinte concede — que é exatamente o comportamento de antes deste PR
    (`update_user_plan` do ramo pago era o último a escrever), então não é
    regressão; fechá-lo exigiria gravar lápide de grant em todo cancelamento.

    Revogar grant `stripe` revoga também o `legacy` do mesmo usuário (§5.1) —
    senão o legado sustentaria acesso que o Stripe já disse que acabou.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update plan_grants"
                "   set status = 'revoked', revoked_reason = %s, revoked_at = now(),"
                "       event_version = %s, last_event_id = coalesce(%s, last_event_id),"
                "       updated_at = now()"
                " where user_id = %s and source = %s and external_ref = %s"
                "   and status <> 'revoked'"
                "   and event_version <= %s"
                " returning id",
                (reason, int(event_version), last_event_id,
                 int(user_id), source, external_ref, int(event_version)),
            )
            aplicou = cur.fetchone() is not None
            if aplicou and source == "stripe":
                cur.execute(
                    "update plan_grants"
                    "   set status = 'revoked', revoked_reason = %s, revoked_at = now(),"
                    "       event_version = %s, updated_at = now()"
                    " where user_id = %s and source = 'legacy' and status = 'active'",
                    (reason, int(event_version), int(user_id)),
                )
        conn.commit()
    return aplicou


def list_grants(user_id: int) -> list[dict]:
    """TODOS os grants do usuário, revogados e vencidos inclusive, mais novos
    por último (`starts_at`).

    Devolver os revogados não é desleixo: a diferença entre "usuário sem NENHUM
    grant" (desconhecimento → a projeção não escreve) e "usuário com grants,
    todos vencidos ou revogados" (informação → rebaixa) é o que torna este PR
    reversível (§4.1). Um filtro de `status` aqui apagaria essa diferença.
    """
    sql = ("select * from plan_grants where user_id = %s"
           " order by starts_at asc, id asc")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (int(user_id),))
            return [dict(r) for r in cur.fetchall()]


def users_com_grant_na_janela(desde) -> list[int]:
    """user_ids a reprojetar (§4.3).

    `desde` = instante: só quem teve grant ativo COMEÇANDO ou TERMINANDO desde
    então. São as duas únicas transições de acesso que acontecem sem nenhum
    evento externo. Indexada, devolve zero linhas quase sempre — é a passada de
    60 s.

    **`desde=None` = TODOS os usuários com grant ativo** — a varredura diária.
    Ela é AUTO-CURATIVA de propósito: a passada por janela só conserta o que
    transicionou DENTRO da janela, então um processo fora do ar por mais tempo
    que a janela deixava o `status='active'` e o `plan` velhos para sempre. Sem
    janela não existe "mais tempo que a janela".

    ponytail: o custo é um `recompute` por usuário com grant ativo **por dia E
    por boot** — a primeira volta do loop também roda sem janela, e no Railway
    todo deploy é um boot, então dias de deploy pagam mais de uma vez. O pior
    caso mede o número de PAGANTES. Se doer, o upgrade é comparar projeção ×
    grants em SQL e reprojetar só quem divergir.
    """
    sql = "select distinct user_id from plan_grants where status = 'active'"
    params: tuple = ()
    if desde is not None:
        sql += " and (starts_at between %s and now() or ends_at between %s and now())"
        params = (desde, desde)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return [int(r["user_id"]) for r in cur.fetchall()]
