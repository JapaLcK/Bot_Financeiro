-- ============================================================================
-- PII — Fase 5 · EXPAND: afrouxar NOT NULL das colunas em claro
-- ----------------------------------------------------------------------------
-- Rode ANTES da migração de código que para de escrever as colunas em claro
-- (passo 2 → passo 3 do pii_phase5_README.md). Sem isto, os INSERT sem a
-- coluna em claro estouram NOT NULL enquanto a coluna ainda existe (o drop só
-- vem depois, em pii_phase5_drop_columns.sql).
--
-- Reversível (não apaga dado): pra desfazer, `... SET NOT NULL` de novo — mas
-- só depois de garantir que não há linhas com NULL nessas colunas.
-- Seguro rodar em prod. Idempotente (DROP NOT NULL em coluna já nullable é no-op).
-- ============================================================================

BEGIN;

ALTER TABLE auth_accounts            ALTER COLUMN email DROP NOT NULL;
ALTER TABLE pending_google_signups   ALTER COLUMN email DROP NOT NULL;
ALTER TABLE email_verification_codes ALTER COLUMN email DROP NOT NULL;

COMMIT;
