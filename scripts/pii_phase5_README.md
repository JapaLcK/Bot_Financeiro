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

## Sequência segura (nesta ordem)

1. **Backfill 100%.** Rode `migrate_pii_to_encrypted.py` até o fim e confirme
   com `pii_phase5_precheck.sql` → todas as contagens `faltando = 0`.
2. **Migração de código** (PR próprio, com testes):
   - Parar de **escrever** as colunas em claro (tirar `email`/`phone_e164`/
     `display_name`/`name_hint`/`delivered_to_email` dos INSERT/UPDATE).
   - Trocar toda **leitura** em claro por decrypt do `_enc`
     (`decrypt_pii(...)`), removendo os `row.get("email")` de fallback.
   - `email_verification_codes` tem TTL curto — pode ser o primeiro a limpar.
3. **Deploy** dessa migração e observar 1–2 dias (cadastro, login, export,
   exclusão de conta, painel admin).
4. **Backup** verificado (`pg_dump`) guardado fora do Railway.
5. **Precheck de novo** → `faltando = 0`.
6. **Drop**: descomentar `pii_phase5_drop_columns.sql`, rodar no DBeaver dentro
   da transação, conferir o `\d`, e só então `COMMIT`.

## Arquivos

- `pii_phase5_precheck.sql` — go/no-go, somente leitura, seguro em prod.
- `pii_phase5_drop_columns.sql` — o DROP, guardado (default `ROLLBACK`).

## O que eu preciso de você

O passo 2 (migração de código) é a parte grande e é onde eu preciso do seu
"vai" pra abrir o PR. O drop em si (passo 6) você roda à mão, com o backup na
mão — eu não executo operação irreversível em prod.
