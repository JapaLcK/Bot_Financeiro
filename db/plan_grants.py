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

# §6 — evento com versão MENOR OU IGUAL nunca reescreve uma concessão.
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
     where excluded.event_version > plan_grants.event_version
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
    external_ref: str | None,
    reason: str,
    event_version: int,
    last_event_id: str | None = None,
) -> bool:
    """Revoga grant(s) do usuário. Devolve True se revogou alguma linha.

    **`external_ref=None` revoga TODOS os grants ativos daquela `source`** — é o
    que o `customer.subscription.deleted` usa. O motivo é concreto: o objeto do
    evento nem sempre traz o id da assinatura, e amarrar a revogação a ele
    deixava o grant vivo; a projeção seguinte então RESSUSCITAVA o plano por
    cima do `free` que o webhook acabara de escrever — regressão contra o
    comportamento de hoje, pega por
    `tests/test_billing_webhook_lifecycle.py::test_checkout_completed_fecha_o_gate_de_escolha`.
    A conta tem no máximo uma assinatura viva (`_billing_checkout_for_user`
    recusa a segunda), então "todas as ativas" é a mesma coisa que "a dela".

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
                " where user_id = %s and source = %s"
                "   and (%s::text is null or external_ref = %s)"
                "   and status <> 'revoked'"
                "   and event_version <= %s"
                " returning id",
                (reason, int(event_version), last_event_id,
                 int(user_id), source, external_ref, external_ref, int(event_version)),
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


def revoke_all_active_grants(user_id: int, reason: str, event_version: int) -> int:
    """Revoga TODOS os grants ativos do usuário. Usado pelo reparo manual do
    admin ao descer para `free` (§9 da auditoria / §15): sem isto, o grant que
    sustentava o acesso reapareceria na projeção seguinte.

    Sem guarda de versão de propósito: é ordem humana, não evento de gateway.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update plan_grants"
                "   set status = 'revoked', revoked_reason = %s, revoked_at = now(),"
                "       event_version = %s, updated_at = now()"
                " where user_id = %s and status = 'active'",
                (reason, int(event_version), int(user_id)),
            )
            n = cur.rowcount
        conn.commit()
    return int(n)


def list_grants(user_id: int, only_active: bool = False) -> list[dict]:
    """Grants do usuário, mais novos por último (`starts_at`).

    `only_active=False` de propósito no uso da projeção: a diferença entre
    "usuário sem NENHUM grant" (desconhecimento → não escreve) e "usuário com
    grants, todos vencidos/revogados" (informação → rebaixa) é o que torna o PR
    reversível (§4.1). Contar só os ativos apagaria essa diferença.
    """
    sql = "select * from plan_grants where user_id = %s"
    if only_active:
        sql += " and status = 'active'"
    sql += " order by starts_at asc, id asc"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (int(user_id),))
            return [dict(r) for r in cur.fetchall()]


def users_com_grant_na_janela(desde) -> list[int]:
    """user_ids cujo grant ativo COMEÇOU ou TERMINOU desde `desde` (§4.3).

    É a lista que o loop de re-projeção precisa: grant futuro que virou vigente
    e grant vigente que venceu são as duas únicas transições que acontecem sem
    nenhum evento externo. Indexada por (user_id, status, starts_at); devolve
    zero linhas quase sempre.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select distinct user_id from plan_grants"
                " where status = 'active'"
                "   and (starts_at between %s and now() or ends_at between %s and now())",
                (desde, desde),
            )
            return [int(r["user_id"]) for r in cur.fetchall()]
