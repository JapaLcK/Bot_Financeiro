-- ============================================================================
-- PII — Fase 5 · DROP das colunas em claro   ⚠️ IRREVERSÍVEL ⚠️
-- ----------------------------------------------------------------------------
-- NÃO RODE ANTES DE:
--   1. pii_phase5_precheck.sql retornar faltando = 0 em TODAS as linhas.
--   2. A migração de CÓDIGO estar concluída e no ar (ver pii_phase5_README.md):
--      nenhum SELECT/INSERT/UPDATE pode mais tocar nas colunas em claro.
--      Hoje AINDA tocam — rodar isto agora QUEBRA os inserts de login/cadastro.
--   3. BACKUP verificado do banco (pg_dump) guardado fora do Railway.
--
-- Roda em transação: se algo destoar, dá ROLLBACK antes do COMMIT.
-- Deixado comentado de propósito — descomente só quando 1-3 estiverem OK.
-- ============================================================================

BEGIN;

-- -- auth_accounts
-- ALTER TABLE auth_accounts            DROP COLUMN IF EXISTS email;
-- ALTER TABLE auth_accounts            DROP COLUMN IF EXISTS phone_e164;
-- ALTER TABLE auth_accounts            DROP COLUMN IF EXISTS display_name;
--
-- -- auth_identities
-- ALTER TABLE auth_identities          DROP COLUMN IF EXISTS email;
--
-- -- auth_login_events
-- ALTER TABLE auth_login_events        DROP COLUMN IF EXISTS email;
--
-- -- data_export_tokens
-- ALTER TABLE data_export_tokens       DROP COLUMN IF EXISTS delivered_to_email;
--
-- -- pending_google_signups
-- ALTER TABLE pending_google_signups   DROP COLUMN IF EXISTS email;
-- ALTER TABLE pending_google_signups   DROP COLUMN IF EXISTS name_hint;
--
-- -- email_verification_codes
-- ALTER TABLE email_verification_codes DROP COLUMN IF EXISTS email;
-- ALTER TABLE email_verification_codes DROP COLUMN IF EXISTS phone_e164;

-- Confira o \d das tabelas AQUI antes de confirmar.
-- Se estiver tudo certo: COMMIT;  senão: ROLLBACK;
ROLLBACK;  -- padrão seguro: não commita nada. Troque por COMMIT conscientemente.
