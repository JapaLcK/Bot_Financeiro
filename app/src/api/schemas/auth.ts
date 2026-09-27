import { z } from "zod";

/**
 * Contratos validados na FRONTEIRA. O backend é um monólito com ~198 rotas sem
 * versionamento, e o app publicado na loja não se atualiza junto com o deploy —
 * então uma resposta que mudou de forma tem de virar erro nomeado aqui, e não
 * `undefined` vazando por três telas até alguém ver um crash sem causa.
 */
export const credenciaisSchema = z.object({
  access_token: z.string().min(1),
  refresh_token: z.string().min(1),
  dashboard_token: z.string().min(1),
  expires_in: z.number().int().positive(),
});

export const loginSchema = credenciaisSchema.extend({
  user_id: z.number().int(),
  email: z.string(),
  plan: z.string().optional(),
  dashboard_url: z.string().optional(),
});

/** `/auth/me` devolve a linha inteira da conta; aqui só o que a Fase 1 usa. */
export const perfilSchema = z.object({
  user_id: z.number().int(),
  email: z.string().nullable().optional(),
  display_name: z.string().nullable().optional(),
  plan: z.string().nullable().optional(),
  app_access: z.boolean().optional(),
});

/**
 * O primeiro passo do login de quem tem dois fatores: NÃO vem credencial.
 *
 * `frontend/finance_bot_websocket_custom.py` devolve `mfa_required` com um
 * desafio, e o cliente precisa completar em `/auth/mfa/verify-login`. Sem
 * modelar isso, todo login com MFA virava erro de contrato.
 */
export const desafioMfaSchema = z.object({
  mfa_required: z.literal(true),
  mfa_challenge: z.string().min(1),
  email: z.string(),
});

/** O que `/auth/login` pode devolver com 200: credencial OU desafio. */
export const respostaLoginSchema = z.union([desafioMfaSchema, loginSchema]);

/** `GET /auth/google/pending/{token}`: o pré-cadastro de quem entrou pelo Google sem conta. */
export const pendenteGoogleSchema = z.object({
  email: z.string(),
  name_hint: z.string(),
});

export type Credenciais = z.infer<typeof credenciaisSchema>;
export type Perfil = z.infer<typeof perfilSchema>;

/** `/auth/mfa/status` (`db/mfa.py`, `get_mfa_status`). */
export const mfaStatusSchema = z.object({
  enabled: z.boolean(),
  has_pending_secret: z.boolean(),
  backup_codes_remaining: z.number().int(),
});

/** `/auth/mfa/setup`: o QR vem pronto, como SVG em data URI. */
export const mfaSetupSchema = z.object({
  secret: z.string().min(1),
  uri: z.string().startsWith("otpauth://"),
  qr_code: z.string().startsWith("data:image/svg+xml;base64,"),
});

/** `/auth/mfa/enable` (que manda também `ok`) e `/auth/mfa/regenerate-backup-codes`. */
export const codigosBackupSchema = z.object({
  backup_codes: z.array(z.string()).min(1),
});

export type MfaStatus = z.infer<typeof mfaStatusSchema>;
export type MfaSetup = z.infer<typeof mfaSetupSchema>;
