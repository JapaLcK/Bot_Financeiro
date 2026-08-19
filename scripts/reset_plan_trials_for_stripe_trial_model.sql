-- =====================================================================
-- Reset de elegibilidade de trial — virada pro modelo de trial via Stripe
-- >>> ONE-TIME. Rodar UMA vez em prod (DBeaver), no deploy da mudança. <<<
-- >>> NÃO colocar no schema.py: rodaria a cada restart e zeraria os trials. <<<
-- =====================================================================
-- Contexto (2026-08-06): o trial deixou de ser "sem cartão ancorado no
-- telefone" e passou a ser 30 dias do PLANO ESCOLHIDO, via Stripe COM CARTÃO
-- (subscription trialing → cobra no dia 31). Regra mantida: 1 trial por
-- telefone na vida (tabela plan_trials, keyed por phone_hash).
--
-- PROBLEMA que este script resolve: no modelo ANTIGO, TODA conta já ganhou um
-- trial de telefone no cadastro (auto-claim) → o phone_hash de todo mundo já
-- está em plan_trials. Sem reset, a base atual seria considerada INELEGÍVEL no
-- novo checkout (is_trial_eligible_for_user = false) e pagaria a 1ª fatura na
-- hora, sem os 30 dias grátis.
--
-- DECISÃO DE PRODUTO (Lucas, 2026-08-06): dar à base atual um começo limpo —
-- todo mundo volta a ser elegível ao trial de 30 dias do novo modelo.
--
-- O que faz:
--   1. Remove só registros do modelo antigo (model_version=1). Claims criados
--      pelo webhook novo usam model_version=2 e sobrevivem à migração.
--   2. Limpa trial_started_at / trial_downsell_sent_at das contas que NÃO estão
--      num trial Stripe vivo. Sem isso, o guard "não rejuvenesce trial queimado"
--      de claim_trial_for_user deixaria o trial_started_at velho (data do
--      cadastro) travado, e o banner "faltam X dias" mostraria 0 quando a pessoa
--      assinasse o novo trial. (O tier fica correto de qualquer jeito — vem de
--      plan_expires_at da assinatura; isto é só o contador da UI/e-mails.)
--      Preserva quem está com last_payment_status='trialing' (trial Stripe em
--      andamento, cujo contador ainda faz sentido).
--
-- >>> ORDEM DE DEPLOY <<<
--   1. Deploy do código do novo modelo de trial. O init_db adiciona
--      model_version=1 aos registros legados; claims novos gravam versão 2.
--   2. Rodar ESTE script em produção (DBeaver). Pode haver tráfego normal:
--      registros versão 2 não são removidos.
--   3. Conferir as queries de verificação no fim.
-- Se rodar ANTES do deploy do código novo, o checkout antigo (que lia os dias
-- restantes do trial de telefone) passaria a dar 30 dias cheios pra quem estava
-- no meio do trial — inofensivo, mas rode na ordem por garantia.
--
-- Idempotente: execuções posteriores não removem claims do modelo 2.
-- Irreversível para os registros legados removidos. Confira as contagens ANTES.
-- =====================================================================

BEGIN;

-- Antes: quantos telefones têm trial registrado (o que será apagado)
--   SELECT count(*) AS trials_registrados FROM plan_trials;
-- Antes: quantas contas têm trial_started_at que será limpo
--   SELECT count(*) AS contas_com_trial_started
--     FROM auth_accounts
--    WHERE trial_started_at IS NOT NULL
--      AND COALESCE(last_payment_status, '') IS DISTINCT FROM 'trialing';

-- Compatibilidade caso o script seja aberto antes do primeiro restart do app.
ALTER TABLE plan_trials
  ADD COLUMN IF NOT EXISTS model_version smallint NOT NULL DEFAULT 1;

-- 1) Zera apenas a trava do modelo antigo. Checkouts concluídos depois do
-- deploy gravam model_version=2 e não perdem a proteção contra segundo trial.
DELETE FROM plan_trials WHERE model_version = 1;
ALTER TABLE plan_trials ALTER COLUMN model_version SET DEFAULT 2;

-- 2) Limpa o âncora de trial das contas que não estão num trial Stripe vivo,
--    pra o novo trial (quando assinarem) ancorar na data certa.
UPDATE auth_accounts
   SET trial_started_at      = NULL,
       trial_downsell_sent_at = NULL
 WHERE (trial_started_at IS NOT NULL OR trial_downsell_sent_at IS NOT NULL)
   AND COALESCE(last_payment_status, '') IS DISTINCT FROM 'trialing';

COMMIT;

-- Verificação pós-reset:
--   SELECT model_version, count(*) FROM plan_trials GROUP BY model_version;
--     -- esperado: nenhuma versão 1; versão 2 pode existir se houve checkout
--   SELECT count(*) AS ainda_com_started
--     FROM auth_accounts
--    WHERE trial_started_at IS NOT NULL;                                 -- esperado: só os 'trialing' vivos
--   SELECT last_payment_status, count(*)
--     FROM auth_accounts
--    WHERE trial_started_at IS NOT NULL
--    GROUP BY last_payment_status;                                       -- esperado: só 'trialing'
