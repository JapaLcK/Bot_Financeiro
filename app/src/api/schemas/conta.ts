import { z } from "zod";

/**
 * `GET /settings/{uid}/security`, só o que o Perfil lê. `identities` traz o `external_id` (o telefone
 * decifrado de cada identidade): o Zod o descarta aqui, de propósito — só o `provider` entra no app.
 */
export const contatoSchema = z.object({
  email: z.string().nullable().optional(),
  display_name: z.string().nullable().optional(),
  phone: z.string().nullable().optional(),
  created_at: z.string().nullable().optional(),
  identities: z.array(z.object({ provider: z.string() })),
});

/** `POST /auth/link-code`: `""` quando o servidor não tem número de WhatsApp configurado. */
export const linkCodeSchema = z.object({ whatsapp_link: z.string() });

/**
 * `GET`/`PATCH /settings/{uid}/notifications`. Os booleanos são obrigatórios e sem default: um campo
 * ausente é `ContratoInvalido`, nunca "ligado" (ligar sem o usuário pedir é consentimento inventado).
 * `engagement_email_enabled` (legado, soma dos dois e-mails) o app não lê.
 */
const bool = z.boolean();
export const notificacoesSchema = z.object({
  email: z.string().nullable(),
  whatsapp_destination: z.string().nullable(),
  email_notifications_available: bool,
  whatsapp_updates_available: bool,
  tip_email_enabled: bool,
  insight_email_enabled: bool,
  whatsapp_updates_enabled: bool,
  daily_report_enabled: bool,
  daily_report_hour: z.number().int().min(0).max(23),
  daily_report_minute: z.number().int().min(0).max(59),
  weekly_report_enabled: bool,
  weekly_report_stored_enabled: bool,
  weekly_report_available: bool,
  monthly_report_enabled: bool,
});

export type Contato = z.infer<typeof contatoSchema>;
export type Notificacoes = z.infer<typeof notificacoesSchema>;
