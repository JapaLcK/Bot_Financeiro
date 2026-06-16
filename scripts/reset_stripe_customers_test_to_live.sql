-- =====================================================================
-- Reset dos stripe_customer_id ao virar test → live
-- =====================================================================
-- Contexto: na fase de teste (sandbox), o checkout cria um Customer no Stripe
-- e salva o id (cus_...) em auth_accounts.stripe_customer_id. Esses customers
-- existem SÓ no modo teste. Ao trocar a STRIPE_SECRET_KEY pra sk_live_*, o
-- código (billing_create_checkout) REUSA o customer salvo — e o Stripe Live
-- não encontra um customer de teste, então o Session.create quebra com
-- "No such customer" e o front mostra "Não foi possível iniciar o checkout".
--
-- Fix: zerar os customer_id pra forçar a criação de um Customer novo no Live
-- no próximo checkout. Seguro porque ninguém pagou de verdade ainda (era tudo
-- sandbox) — não apaga conta nem dado, só a referência ao Stripe.
--
-- Rode UMA vez, em produção, DEPOIS de trocar a secret key pra sk_live_*.
-- Idempotente: rodar de novo não muda nada (quem já assinou no Live e tem um
-- customer válido também é zerado — então só rode antes de existir assinante
-- Live real; nesta virada inicial isso é verdade).
-- =====================================================================

UPDATE auth_accounts
   SET stripe_customer_id = NULL
 WHERE stripe_customer_id IS NOT NULL;

-- Verificação: deve voltar 0.
--   SELECT count(*) FROM auth_accounts WHERE stripe_customer_id IS NOT NULL;
