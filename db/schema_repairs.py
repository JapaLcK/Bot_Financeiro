"""
db/schema_repairs.py — Migrations idempotentes pra corrigir inconsistências
históricas entre o que `schema.py` declara e o que está realmente em produção.

`create table if not exists` não atualiza definições de tabelas que já existem.
Quando uma FK foi adicionada ou alterada no schema.py *depois* que a tabela já
existia em prod, a constraint nova nunca é aplicada — a tabela continua com a
definição original. O sintoma é silencioso: deletes que deveriam cascatear não
cascateiam, e dados órfãos aparecem.

Cada função neste módulo é idempotente: detecta o gap entre o estado real e o
desejado, e só executa ALTER se houver diferença.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# Tabelas cujo FK em users(id) deve ser ON DELETE SET NULL em vez de CASCADE.
# São logs de auditoria que precisam sobreviver à deleção do user (LGPD permite
# manter eventos com user_id nulo para investigação de incidentes).
_USER_FK_SET_NULL_TABLES: frozenset[str] = frozenset({
    "auth_login_events",
    # Trava de 1 trial por telefone, na vida. A linha é keyed por phone_hash e
    # PRECISA sobreviver à deleção da conta — cascatear devolveria 15 dias novos
    # a cada conta recriada com o mesmo número. Estar nesta lista não é detalhe:
    # sem ela, o `repair_user_fk_cascades` abaixo converteria a FK nova para
    # CASCADE na primeira subida e apagaria a trava em silêncio.
    "plan_trials",
    # Telemetria do funil de checkout: contamos sessões (session_id), não só
    # pessoas vivas — a conversão histórica não pode encolher quando uma conta
    # é excluída. O evento sobrevive anonimizado (user_id nulo).
    "checkout_funnel_events",
    # Registro de compliance de acesso a PII: "alguém leu tal campo de tal
    # titular, em tal data, com tal propósito" (purpose/actor/field). A coluna
    # da FK é `subject_user_id` — o TITULAR cujo dado foi lido, não o autor do
    # acesso —, então CASCADE apagaria a trilha justamente de quem pediu
    # exclusão, destruindo a prova de que o acesso foi legítimo. O
    # `db/privacy.py` não apaga esta tabela (nem no reset, nem no
    # delete_user_data): a linha sobrevive anonimizada, com subject_user_id
    # nulo.
    "pii_access_log",
})


def repair_user_fk_cascades(cur) -> list[dict]:
    """
    Garante que toda FK referenciando `users(id)` tenha o `on delete` correto.

    Tabelas listadas em _USER_FK_SET_NULL_TABLES viram `set null`; o resto vira
    `cascade`. Retorna a lista de constraints alteradas (vazia se nada mudou).
    """
    cur.execute(
        """
        select conrelid::regclass::text as table_name,
               conname,
               confdeltype,
               (select string_agg(quote_ident(attname), ', ' order by k.ord)
                  from unnest(conkey) with ordinality k(attnum, ord)
                  join pg_attribute a on a.attrelid = conrelid
                                     and a.attnum = k.attnum) as cols_sql
          from pg_constraint
         where contype = 'f'
           and confrelid = 'users'::regclass
        """
    )
    rows = cur.fetchall() or []

    changes: list[dict] = []
    for r in rows:
        table_name = r["table_name"]
        conname = r["conname"]
        cur_code = r["confdeltype"]
        cols_sql = r["cols_sql"]

        target_code = "n" if table_name in _USER_FK_SET_NULL_TABLES else "c"
        if cur_code == target_code:
            continue

        target_clause = "set null" if target_code == "n" else "cascade"
        logger.info(
            "[schema_repairs] alterando FK %s.%s (%s): %s -> %s",
            table_name, conname, cols_sql,
            _decode_action(cur_code), target_clause.upper(),
        )
        cur.execute(
            f'alter table {table_name} '
            f'drop constraint "{conname}", '
            f'add constraint "{conname}" '
            f"foreign key ({cols_sql}) references users(id) on delete {target_clause}"
        )
        changes.append({
            "table": table_name,
            "constraint": conname,
            "from": _decode_action(cur_code),
            "to": target_clause.upper(),
        })

    return changes


def ensure_plan_trials_user_fk(cur) -> bool:
    """Cria a FK `plan_trials.user_id -> users(id) on delete set null`, se faltar.

    O `repair_user_fk_cascades` só AJUSTA FK existente — ele varre
    `pg_constraint`, então uma tabela sem FK nenhuma passa despercebida. A
    `plan_trials` nasceu sem FK de propósito, para sobreviver à deleção da
    conta; o efeito colateral era o `user_id` da conta apagada ficar na linha
    para sempre. `set null` dá as duas coisas: a linha sobrevive e o vínculo
    some, e quem garante é o banco.

    Pelo banco, e não por um UPDATE no job de exclusão, porque o UPDATE tem
    corrida: um `claim_trial_for_user` (webhook do checkout) que insira DEPOIS
    do UPDATE e commite depois grava o `user_id` de uma conta já apagada, e a
    varredura pós-commit nunca revisita `plan_trials`. Apontado pelo Codex no
    PR #152.

    Idempotente. Devolve True se criou a constraint.
    """
    cur.execute(
        """
        select convalidated from pg_constraint
         where contype = 'f'
           and conrelid = 'plan_trials'::regclass
           and confrelid = 'users'::regclass
        """
    )
    atual = cur.fetchone()
    if atual and atual["convalidated"]:
        return False

    criou = False
    if not atual:
        # NOT VALID primeiro, e a ordem é o conserto: ele instala a constraint
        # sem varrer as linhas existentes, mas JÁ a aplica a toda escrita nova.
        # Fazer a limpeza antes do ALTER (a versão anterior) deixava um vão: o
        # `_run_ddl` roda em autocommit, então limpeza e ALTER são transações
        # separadas, e um `claim_trial_for_user` do container velho — que ainda
        # atende webhooks durante o deploy — podia inserir um órfão NOVO no meio
        # e derrubar a validação do ALTER, abortando o init da instância. Depois
        # do NOT VALID, escrita nova não consegue mais criar órfão.
        logger.info("[schema_repairs] criando FK plan_trials.user_id -> users(id) ON DELETE SET NULL (NOT VALID)")
        cur.execute(
            "alter table plan_trials "
            "add constraint plan_trials_user_id_fkey "
            "foreign key (user_id) references users(id) on delete set null "
            "not valid"
        )
        criou = True

    # Só as linhas de contas apagadas ANTES da constraint existir. Depois do
    # NOT VALID nenhuma nova aparece, então esta limpeza converge.
    cur.execute(
        """
        update plan_trials set user_id = null
         where user_id is not null
           and not exists (select 1 from users u where u.id = plan_trials.user_id)
        """
    )
    # Separado do ADD de propósito: se o processo morrer entre os dois, a
    # constraint fica NOT VALID — ainda protegendo escrita nova — e a subida
    # seguinte cai aqui pelo `convalidated` e termina o trabalho.
    logger.info("[schema_repairs] validando FK plan_trials_user_id_fkey")
    cur.execute("alter table plan_trials validate constraint plan_trials_user_id_fkey")
    return criou


def _decode_action(code: str) -> str:
    return {
        "a": "NO ACTION",
        "r": "RESTRICT",
        "c": "CASCADE",
        "n": "SET NULL",
        "d": "SET DEFAULT",
    }.get(code, code)
