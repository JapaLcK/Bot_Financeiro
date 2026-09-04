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
    """Cria ou estende um grant. Devolve o `id` quando APLICOU, None quando a
    guarda de versão bloqueou (evento velho) ou quando o grant `pix` já existia.

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
            if row and source == "stripe":
                cur.execute(_SUPERSEDE_LEGACY, (int(event_version), int(user_id)))
        conn.commit()
    return int(row["id"]) if row else None


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
