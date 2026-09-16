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

export type Credenciais = z.infer<typeof credenciaisSchema>;
export type Perfil = z.infer<typeof perfilSchema>;
