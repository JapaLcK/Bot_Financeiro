# PII — Fase 5: remover as colunas em claro

**Status:** preparado, **não executado**. O drop é irreversível e hoje está
**bloqueado por código**, não só pelo seu aval.

## Por que não é só "dropar as colunas"

As colunas cifradas (`email_enc`, `phone_enc`, etc.) já convivem com as em
claro (`email`, `phone_e164`, ...). Mas o código **ainda lê e escreve as em
claro**. Exemplos confirmados em 2026-08-10:

- **Escrita:** `core/admin_dashboard.py` (`log_auth_login_event` insere `email`
  + `email_enc`), `db/google_auth.py` (`auth_accounts`, `auth_identities`,
  `pending_google_signups`), `db/privacy.py` (`data_export_tokens`).
- **Leitura (fallback):** `db/privacy.py:56/474` (`row.get("email")`),
  `core/audit.py:264`, `core/admin_dashboard.py:328/333`.

Se as colunas caírem agora, esses INSERTs quebram (referenciam coluna
inexistente) e os fallbacks de leitura viram `NULL`. **Cadastro e login
param.** Por isso a ordem importa.

Isto é um **expand/contract**: primeiro afrouxa o schema, depois muda o código,
por último remove as colunas. Pular o expand quebra o cadastro.

1. **Backfill 100%.** Rode `migrate_pii_to_encrypted.py` até o fim e confirme
   com `pii_phase5_precheck.sql` → todas as contagens `faltando = 0`.
2. **EXPAND: afrouxar `NOT NULL`** (rode `pii_phase5_expand_nullable.sql` **antes**
   do passo 3). Hoje são `NOT NULL`: `auth_accounts.email`,
   `pending_google_signups.email`, `email_verification_codes.email`. Se você
   fizer o passo 3 (inserts sem a coluna em claro) com elas ainda `NOT NULL`,
   **todo cadastro quebra com constraint violation** — as colunas só somem no
   passo 6. `DROP NOT NULL` é reversível e não apaga dado.
3. **Migração de código** (PR próprio, com testes):
   - Parar de **escrever** as colunas em claro (tirar `email`/`phone_e164`/
     `display_name`/`name_hint`/`delivered_to_email` dos INSERT/UPDATE).
   - Trocar toda **leitura** em claro por decrypt do `_enc`
     (`decrypt_pii(...)`), removendo os `row.get("email")` de fallback.
   - `email_verification_codes` tem TTL curto — pode ser o primeiro a limpar.
4. **Deploy** dessa migração e observar 1–2 dias (cadastro, login, export,
   exclusão de conta, painel admin).
5. **Backup** verificado (`pg_dump`) guardado fora do Railway.
6. **Precheck de novo** → `faltando = 0`.
7. **CONTRACT (drop)**: descomentar `pii_phase5_drop_columns.sql`, rodar no
   DBeaver dentro da transação, conferir o `\d`, e só então `COMMIT`.

## Arquivos

- `pii_phase5_precheck.sql` — go/no-go, somente leitura, seguro em prod.
- `pii_phase5_expand_nullable.sql` — EXPAND: `DROP NOT NULL` (passo 2, reversível).
- `pii_phase5_drop_columns.sql` — CONTRACT: o DROP, guardado (default `ROLLBACK`).

## O que eu preciso de você

O passo 2 (migração de código) é a parte grande e é onde eu preciso do seu
"vai" pra abrir o PR. O drop em si (passo 6) você roda à mão, com o backup na
mão — eu não executo operação irreversível em prod.
