-- ============================================================================
-- PII — Fase 5 · PRECHECK (somente leitura, seguro rodar em prod)
-- ----------------------------------------------------------------------------
-- Objetivo: provar que TODA linha com PII em claro já tem o par cifrado (_enc)
-- populado. Enquanto qualquer contagem abaixo for > 0, o DROP (ver
-- pii_phase5_drop_columns.sql) PERDERIA DADO e NÃO PODE rodar.
--
-- Uso: rode no DBeaver (não no painel do Railway — ele engole erro).
-- Go/no-go: todas as linhas devem retornar faltando = 0.
-- ============================================================================

-- auth_accounts: email / phone_e164 / display_name
select 'auth_accounts.email'        as coluna, count(*) as faltando
  from auth_accounts where email is not null and email_enc is null
union all
select 'auth_accounts.phone_e164',  count(*)
  from auth_accounts where phone_e164 is not null and phone_enc is null
union all
select 'auth_accounts.display_name', count(*)
  from auth_accounts where display_name is not null and display_name_enc is null

-- auth_identities: email
union all
select 'auth_identities.email',     count(*)
  from auth_identities where email is not null and email_enc is null

-- auth_login_events: email (snapshot de auditoria)
union all
select 'auth_login_events.email',   count(*)
  from auth_login_events where email is not null and email_enc is null

-- data_export_tokens: delivered_to_email
union all
select 'data_export_tokens.delivered_to_email', count(*)
  from data_export_tokens where delivered_to_email is not null and delivered_to_email_enc is null

-- pending_google_signups: email / name_hint (linhas transitórias)
union all
select 'pending_google_signups.email', count(*)
  from pending_google_signups where email is not null and email_enc is null
union all
select 'pending_google_signups.name_hint', count(*)
  from pending_google_signups where name_hint is not null and name_hint_enc is null

-- email_verification_codes: email / phone_e164 (linhas de TTL curto)
union all
select 'email_verification_codes.email', count(*)
  from email_verification_codes where email is not null and email_enc is null
union all
select 'email_verification_codes.phone_e164', count(*)
  from email_verification_codes where phone_e164 is not null and phone_enc is null

order by faltando desc, coluna;
