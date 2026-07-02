-- =====================================================================
-- Backfill: Pro vitalício (grandfather) para a base atual
-- >>> JÁ EXECUTADO EM PROD em 2026-06-16 (Lucas). Paywall ligado em seguida. <<<
-- (mantido no repo como referência/idempotente; não precisa rodar de novo)
-- =====================================================================
-- Contexto: o paywall (PAYWALL_ENABLED) passa a exigir assinatura ativa/trial
-- pra usar o app. Decisão de produto: TODA conta que já existe hoje ganha Pro
-- vitalício de brinde (plan='pro', sem expiração). Quem chegar depois paga.
--
-- Critério: grandfather quem NUNCA passou pelo Stripe (stripe_customer_id NULL).
-- Assim preservamos qualquer assinante/trial real (que tem customer) e cobrimos
-- todos os Free atuais — inclusive 'pro' expirados sem assinatura.
--
-- >>> ORDEM DE DEPLOY (NÃO INVERTER) <<<
--   1. Deploy do código (o gate vem DESLIGADO: PAYWALL_ENABLED ausente/false).
--   2. Rodar ESTE backfill em produção (DBeaver).
--   3. Conferir a contagem (query de verificação no fim).
--   4. Só então setar PAYWALL_ENABLED=true no ambiente (Railway).
-- Se inverter (ligar o flag antes do backfill), TODA a base atual fica
-- trancada fora no instante do deploy.
--
-- Idempotente: rodar de novo não muda nada além do que já foi promovido.
-- Reversível: para tirar o brinde de alguém, basta um UPDATE de volta.
-- =====================================================================

BEGIN;

-- Antes: quanto vamos promover (confira que bate com o esperado)
-- SELECT count(*) FROM auth_accounts
--  WHERE stripe_customer_id IS NULL
--    AND COALESCE(lower(plan), 'free') <> 'pro';

UPDATE auth_accounts
   SET plan                = 'pro',
       plan_expires_at     = NULL,            -- vitalício (is_pro trata NULL como Pro permanente)
       last_payment_status = 'grandfathered'  -- marcador pra distinguir de pagantes reais
 WHERE stripe_customer_id IS NULL
   AND COALESCE(lower(plan), 'free') <> 'pro';

COMMIT;

-- Verificação pós-backfill:
--   SELECT plan, last_payment_status, count(*)
--     FROM auth_accounts
--    GROUP BY plan, last_payment_status
--    ORDER BY count(*) DESC;
-- Esperado: a maioria em ('pro','grandfathered'); pagantes reais intactos
-- (plan='pro' com last_payment_status in ('trialing','active')).
