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


def _decode_action(code: str) -> str:
    return {
        "a": "NO ACTION",
        "r": "RESTRICT",
        "c": "CASCADE",
        "n": "SET NULL",
        "d": "SET DEFAULT",
    }.get(code, code)
