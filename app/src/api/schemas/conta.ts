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

export type Contato = z.infer<typeof contatoSchema>;
