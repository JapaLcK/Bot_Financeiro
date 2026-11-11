"""PL-06 B6, D1: perder o direito a `insights` retira a adesão ao aviso por e-mail.

Gatilho no banco porque o direito tem escritores demais para conferir em Python (webhook, Pix,
família, admin com SQL cru, varredura) e um que nasça amanhã também passa por aqui. Ele só roda para
quem aderiu. `insight_aviso_coberto_ate` guarda até quando o direito está garantido (nula com adesão =
vitalício), ou, depois de uma perda, o INSTANTE da perda. Desenho em `.time-dev/pl06/plano-b6.md` §13 (b):

**O gatilho trata toda perda como expiração natural** (§12): a adesão dura até `MARGEM_DIREITO` (1 h)
depois do instante da perda, sem envio (o portão exige o direito real). Quem retira na hora são os caminhos
EXPLÍCITOS, por `db.reports.retirar_adesao_sem_direito` (cancelamento, contestação, estorno total, admin,
troca de preço para um tier sem `insights`, sair da família). O gatilho NÃO infere downgrade pelo estado (§15
do plano): Plus que vence sobre um Essencial é perda natural, com a margem. Escritor esquecido cai no default
seguro: preserva por até 1 h, sem envio.

1. sem adesão na linha nova: zera a marca (também no INSERT: sem adesão, marca nula);
2. aderindo agora, inclusive linha que nasce com adesão (INSERT): marca = cobertura; sem cobertura em dia, não adere;
4. a adesão antiga já estava morta (`pb_insight_adesao_viva` falso: `now() > marca + margem`; 60 min exatos
   ficam): retira, mesmo que esta escrita devolva o plano. É o que faz o restabelecimento tardio exigir nova
   adesão SEM depender de uma escrita no meio. A mesma função diz à tela e à exportação se a adesão está viva;
5. escrita só de status ou relógio, com status vivo ou de atraso: a marca não encolhe (o webhook grava o
   status ANTES do plano em `_materializar_assinatura`);
6. sem cobertura: a marca vira o instante da perda, `least(marca antiga, now())`. A varredura que grava
   `free` às 10h01 de um Pix vencido às 10h00 deixa a marca em 10h00, não no seu próprio horário;
7. o resto: além da margem, retira; dentro, a marca fica com a mais antiga (`least`): ela nunca avança para
   o passado, então escritas a cada <1 h não encadeiam a margem.

`insight_aviso_coberto_ate` está no `update of`: escrita direta nela vira o valor calculado (todo ramo a
sobrescreve a partir da marca ANTIGA). Retirar grava `insight_aviso_retirada` com o motivo em
`audit_events`, na mesma transação. A cobertura é um dialeto SQL de `plan_gate_ok(uid, "insights") or
carencia_aberta(...)`; a concordância é medida em `tests/test_insights_adesao_direito.py`. Com o freio
`PLANS_V2_ENABLED=0` o gatilho segue por tier (limite declarado, issue #1481).
"""
from __future__ import annotations

import re


def _arr(valores) -> str:
    """Lista de constantes do código como array SQL. Só aceita identificadores simples."""
    assert all(re.fullmatch(r"[a-z_]+", v) for v in valores), valores
    return "array[" + ", ".join(f"'{v}'" for v in valores) + "]::text[]"


def ddl() -> list[str]:
    from core.audit import AuditEvent
    from core.services.billing_dunning import DUNNING_GRACE_DAYS, PAST_DUE_PAYMENT_STATUSES
    from core.services.insights_notificacao import MARGEM_DIREITO, planos_com_insights

    margem = int(MARGEM_DIREITO.total_seconds())
    vivos = _arr(("active", "trialing", *PAST_DUE_PAYMENT_STATUSES))
    retira = f"""
            new.insight_aviso_aderiu_em := null;
            new.insight_aviso_coberto_ate := null;
            insert into audit_events (user_id, event, details)
            values (new.user_id, '{AuditEvent.INSIGHT_AVISO_RETIRADA}', jsonb_build_object('motivo', motivo));
            return new;"""
    return [
        f"""
        create or replace function pb_insight_cobertura(
          p_plan text, p_expira timestamptz, p_status text, p_desde timestamptz
        ) returns timestamptz language sql stable as $$
          select greatest(
            case when lower(coalesce(p_plan, '')) = any({_arr(planos_com_insights())})
                 then coalesce(p_expira, 'infinity'::timestamptz) end,
            case when p_desde is not null
                  and lower(coalesce(p_status, '')) = any({_arr(PAST_DUE_PAYMENT_STATUSES)})
                 then p_desde + interval '{int(DUNNING_GRACE_DAYS)} days' end)
        $$
        """,
        # O critério ÚNICO de "adesão viva": gatilho (passo 4), tela (GET e flag do 402) e exportação.
        f"""
        create or replace function pb_insight_adesao_viva(p_aderiu timestamptz, p_marca timestamptz)
        returns boolean language sql stable as $$
          select p_aderiu is not null and (p_marca is null or now() <= p_marca + interval '{margem} seconds')
        $$
        """,
        f"""
        create or replace function pb_insight_aviso_direito() returns trigger language plpgsql as $$
        declare
          c timestamptz := pb_insight_cobertura(new.plan, new.plan_expires_at,
                                                new.last_payment_status, new.past_due_since);
          margem constant interval := interval '{margem} seconds';
          aderindo boolean;
          motivo constant text := 'direito';
        begin
          if new.insight_aviso_aderiu_em is null then
            new.insight_aviso_coberto_ate := null;
            return new;
          end if;
          if tg_op = 'INSERT' then
            aderindo := true;
          else
            aderindo := old.insight_aviso_aderiu_em is null;
          end if;
          if aderindo then
            if c is null or c < now() then
              new.insight_aviso_aderiu_em := null;
              new.insight_aviso_coberto_ate := null;
            else
              new.insight_aviso_coberto_ate := nullif(c, 'infinity'::timestamptz);
            end if;
            return new;
          end if;
          if not pb_insight_adesao_viva(old.insight_aviso_aderiu_em, old.insight_aviso_coberto_ate) then{retira}
          end if;
          if new.plan is not distinct from old.plan
             and new.plan_expires_at is not distinct from old.plan_expires_at
             and lower(coalesce(new.last_payment_status, '')) = any({vivos}) then
            c := greatest(c, coalesce(old.insight_aviso_coberto_ate, 'infinity'::timestamptz));
          end if;
          if c is null then
            c := least(old.insight_aviso_coberto_ate, now());
          end if;
          if now() > c + margem then{retira}
          elsif c < now() then
            new.insight_aviso_coberto_ate := least(c, old.insight_aviso_coberto_ate);
          else
            new.insight_aviso_coberto_ate := nullif(c, 'infinity'::timestamptz);
          end if;
          return new;
        end;
        $$
        """,
        "drop trigger if exists trg_insight_aviso_direito on auth_accounts",
        "drop trigger if exists trg_insight_aviso_direito_insert on auth_accounts",
        # Linha que NASCE com adesão também passa pela regra (passo 2): sem isto, marca nula = vitalício.
        # Gatilho à parte porque o WHEN de INSERT não pode citar OLD.
        """
        create trigger trg_insight_aviso_direito_insert
          before insert on auth_accounts
          for each row
          when (new.insight_aviso_aderiu_em is not null or new.insight_aviso_coberto_ate is not null)
          execute function pb_insight_aviso_direito()
        """,
        # A marca entra na lista e no WHEN: escrita direta nela, com ou sem adesão, vira o valor calculado.
        """
        create trigger trg_insight_aviso_direito
          before update of plan, plan_expires_at, last_payment_status, past_due_since,
                           insight_aviso_aderiu_em, insight_aviso_coberto_ate
          on auth_accounts
          for each row
          when (old.insight_aviso_aderiu_em is not null or new.insight_aviso_aderiu_em is not null
                or new.insight_aviso_coberto_ate is not null)
          execute function pb_insight_aviso_direito()
        """,
    ]
