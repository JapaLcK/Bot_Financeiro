"""
db/pix_pausa.py — a pausa da concessão do Pix (#1574).

Enquanto ela está ativa, nenhum pagamento Pix concede acesso nem comissão. Serve para drenar
estornos antigos sem que um pagamento reenviado restaure o que o estorno tirou. O roteiro de
ativar, confirmar e retomar está em `docs/CLAUDE.md`, "Pausa da concessão do Pix".

**Mora no banco, e não numa flag do código**, porque tem de valer também para o processo da
versão anterior, que continua vivo durante a sobreposição do deploy e não lê flag nova. Pausa
ativa = a linha única de `pix_pausa_concessao` com `retomada_em` nulo. Três gatilhos `before`, com `WHEN`
estreito, recusam com o SQLSTATE `SQLSTATE_PAUSA` só:
- a transição de `pix_charges` para `paid`/`paid_orphan`;
- o grant `source='pix'` nascendo ou voltando a `active` (dois gatilhos: o `WHEN` de insert não cita `old`);
- o insert de comissão `pix:%`.
Estorno, revogação, Stripe e admin passam. O código novo nem lista nem reserva `PAYMENT_RECEIVED`/
`CONFIRMED` com a pausa ativa (`pix_pausa_retem`, em `db/webhook_outbox.py`): o evento fica aberto, sem
tentativa, e não ocupa o lote da varredura. Recusa do gatilho que ainda chegue ao código novo (dreno
reservado antes da ativação, baixa desfeita que reconcede) vira `FALHA_PAUSA`, sem contar tentativa; o
estorno parcial de cobrança ainda não paga também espera assim (`pix_drain`, #1600). A
versão antiga bate no gatilho e conta uma tentativa por passada, só enquanto ela viver.

O evento que a pausa segura não se perde na purga da outbox (`pix_pausa_guarda`): enquanto a pausa está
ativa, e por mais `RETENCAO_OUTBOX_DIAS` depois da retomada, a compra aberta e o evento aberto recusado
pelo gatilho (`last_error = FALHA_PAUSA`) ficam com o payload. Por isso a retomada grava `retomada_em`
em vez de apagar a linha (o prazo da purga recomeça dela), e gatilhos recusam o `delete` e o `truncate`.
"""
from __future__ import annotations

from .connection import get_conn

SQLSTATE_PAUSA = "PBPAU"
FALHA_PAUSA = "PausaPix"  # o `last_error` da recusa; `registrar_falha` não conta tentativa com ele
AVISO_APOS_MIN = 30  # decisão do dono: avisa o admin, sem retomada automática
AVISO_A_CADA_H, AVISO_NO_FIM_H = 6, 1  # depois do 1º aviso; de hora em hora no último dia antes dos 7 dias
EVENTOS_DE_COMPRA = ("PAYMENT_RECEIVED", "PAYMENT_CONFIRMED")  # os que o dreno leva aos efeitos de compra

# (nome, tabela, evento, condição). O roteiro confere estes 4 nomes em `pg_trigger`.
GATILHOS = (
    ("trg_pix_pausa_cobranca", "pix_charges", "update",
     "new.status in ('paid', 'paid_orphan') and old.status is distinct from new.status"),
    ("trg_pix_pausa_grant_novo", "plan_grants", "insert",
     "new.source = 'pix' and new.status = 'active'"),
    ("trg_pix_pausa_grant", "plan_grants", "update",
     "new.source = 'pix' and new.status = 'active' and old.status is distinct from 'active'"),
    ("trg_pix_pausa_comissao", "affiliate_commissions", "insert",
     "new.stripe_invoice_id like 'pix:%'"),
)



def _trava(tentativas: int, mensagem: str) -> str:
    """As três tabelas juntas, sem entrar na fila (`nowait` em laço, 50 ms entre tentativas): esperar na
    fila prenderia atrás do pedido toda escrita nova (webhook, checkout, saque)."""
    return f"""
            for tentativa in 1..{tentativas} loop
              begin
                lock table pix_charges, plan_grants, affiliate_commissions in share row exclusive mode nowait;
                exit;
              exception when lock_not_available then
                if tentativa = {tentativas} then
                  raise exception '{mensagem}' using errcode = 'lock_not_available';
                end if;
                perform pg_sleep(0.05);
              end;
            end loop;"""


# Só cria o gatilho que falta: `drop` + `create` a cada boot pediria lock nas três tabelas em todo
# deploy. `create trigger` pede `share row exclusive`, que espera escrita em voo; a trava é `_trava`, como a
# migração de afiliados em `db/schema.py`. Sem as tabelas livres em ~8 s o boot para, com erro claro:
# subir sem a pausa no banco não é opção.
# `tests/test_pix_ddl_sem_escrita.py` aceita este texto exato (cria gatilho, não grava linha).
INSTALAR_GATILHOS = """
        do $$ begin
          if (select count(*) from pg_trigger where tgname in ({nomes})) < {n} then{trava}
          end if;{criar}
        end $$
        """.format(
    nomes=", ".join(f"'{nome}'" for nome, *_ in GATILHOS),
    n=len(GATILHOS),
    trava=_trava(160, "pausa do Pix: tabelas ocupadas em 160 tentativas; o boot para aqui"),
    criar="".join(f"""
          if not exists (select 1 from pg_trigger where tgname = '{nome}') then
            create trigger {nome} before {evento} on {tabela} for each row
              when ({quando}) execute function pix_pausa_recusa();
          end if;""" for nome, tabela, evento, quando in GATILHOS),
)


def ddl() -> list[str]:
    return [
        # Uma linha só: `id` é sempre `true`. `avisada_em` é do aviso de pausa longa.
        """
        create table if not exists pix_pausa_concessao (
          id boolean primary key default true check (id),
          ativa_desde timestamptz not null default now(),
          motivo text,
          avisada_em timestamptz,
          retomada_em timestamptz
        )
        """,
        """
        create or replace function pix_pausa_ativa() returns boolean language sql stable as $$
          select exists (select 1 from pix_pausa_concessao where retomada_em is null)
        $$
        """,
        # O predicado ÚNICO da lista e da reserva da outbox (`db/webhook_outbox.py`).
        f"""
        create or replace function pix_pausa_retem(p_tipo text) returns boolean language sql stable as $$
          select p_tipo in ({", ".join(f"'{t}'" for t in EVENTOS_DE_COMPRA)}) and pix_pausa_ativa()
        $$
        """,
        # O evento aberto que a purga da outbox não zera (compra, ou recusado pelo gatilho): pausa ativa, ou
        # retomada há menos de `p_dias`.
        f"""
        create or replace function pix_pausa_guarda(p_tipo text, p_erro text, p_dias int) returns boolean
        language sql stable as $$
          select (p_tipo in ({", ".join(f"'{t}'" for t in EVENTOS_DE_COMPRA)}) or p_erro is not distinct from '{FALHA_PAUSA}') and exists (
            select 1 from pix_pausa_concessao
             where retomada_em is null or retomada_em > now() - make_interval(days => p_dias))
        $$
        """,
        # Ativar pela função, não pelo `insert` cru: a trava espera a escrita que já passou pelo gatilho
        # (sem pausa) terminar, então quando ela volta nada mais concede. `ativa_desde` é o fim da espera.
        f"""
        create or replace function pix_pausa_ativar(p_motivo text) returns timestamptz language plpgsql as $$
        begin{_trava(100, "pausa do Pix: tabelas ocupadas por 5 s; rode de novo")}
          insert into pix_pausa_concessao (motivo, ativa_desde) values (p_motivo, clock_timestamp())
            on conflict (id) do update
              set motivo = excluded.motivo, ativa_desde = excluded.ativa_desde, avisada_em = null, retomada_em = null
              where pix_pausa_concessao.retomada_em is not null;
          return (select ativa_desde from pix_pausa_concessao);
        end
        $$
        """,
        f"""
        create or replace function pix_pausa_recusa() returns trigger language plpgsql as $$
        begin
          if pix_pausa_ativa() then
            raise exception 'pausa da concessão do Pix ativa: escrita em % recusada', tg_table_name
              using errcode = '{SQLSTATE_PAUSA}';
          end if;
          return new;
        end
        $$
        """,
        INSTALAR_GATILHOS,
        # O `delete` (o roteiro antigo) ou o `truncate` da linha desligariam a guarda da purga: recusados.
        # Retomar é o `update`.
        """
        create or replace function pix_pausa_sem_delete() returns trigger language plpgsql as $$
        begin
          raise exception 'pausa do Pix: não apague a linha; retome com update pix_pausa_concessao set retomada_em = now()';
        end
        $$
        """,
        """
        do $$ begin
          if not exists (select 1 from pg_trigger where tgname = 'trg_pausa_pix_sem_delete') then
            create trigger trg_pausa_pix_sem_delete before delete on pix_pausa_concessao
              for each statement execute function pix_pausa_sem_delete();
          end if;
          if not exists (select 1 from pg_trigger where tgname = 'trg_pausa_pix_sem_truncate') then
            create trigger trg_pausa_pix_sem_truncate before truncate on pix_pausa_concessao
              for each statement execute function pix_pausa_sem_delete();
          end if;
        end $$
        """,
    ]


def pausa_ativa() -> bool:
    with get_conn() as conn:
        return bool(conn.execute("select pix_pausa_ativa() as a").fetchone()["a"])


def falha_de(exc: Exception) -> tuple[str, str | None]:
    """A falha que o dreno registra: a recusa da pausa vira `FALHA_PAUSA`; o resto, classe e código."""
    if getattr(exc, "sqlstate", None) == SQLSTATE_PAUSA:
        return (FALHA_PAUSA, None)
    return (type(exc).__name__, getattr(exc, "code", None))


def avisar_se_esquecida(enviar, dias: int) -> None:
    """Pausa ativa há `AVISO_APOS_MIN`: chama `enviar(linha)` no 1º aviso e depois a cada `AVISO_A_CADA_H`,
    ou `AVISO_NO_FIM_H` a partir de um dia antes de a pausa completar `dias`.

    Claim numa transação curta (o `update` é atômico: com vários processos, um só ganha a janela) e o HTTP
    depois do commit, sem trava na linha: ativar e retomar não esperam o canal. Se `enviar` não devolver
    verdadeiro, a marca volta ao valor anterior (só se ninguém a trocou no meio) e a passada seguinte tenta
    de novo."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "update pix_pausa_concessao p set avisada_em = clock_timestamp()"
            "  from (select avisada_em from pix_pausa_concessao) antes"
            " where p.retomada_em is null and p.ativa_desde <= now() - make_interval(mins => %s)"
            "   and (p.avisada_em is null or p.avisada_em <= now() - make_interval(hours =>"
            "        case when p.ativa_desde <= now() - make_interval(days => %s - 1) then %s else %s end))"
            " returning p.ativa_desde, p.motivo, p.avisada_em as marca, antes.avisada_em as antes",
            (AVISO_APOS_MIN, dias, AVISO_NO_FIM_H, AVISO_A_CADA_H),
        )
        linha = cur.fetchone()
        conn.commit()
    if not linha:
        return
    enviado = False
    try:
        enviado = enviar(linha)
    finally:
        if not enviado:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("update pix_pausa_concessao set avisada_em = %s where avisada_em = %s",
                            (linha["antes"], linha["marca"]))
                conn.commit()
