import { z } from "zod";

/** `POST /open-finance/{uid}/connect-token` (frontend/routes/open_finance.py). */
export const connectTokenSchema = z.object({
  ok: z.literal(true),
  accessToken: z.string().min(1),
  // Só dev/staging (`PLUGGY_INCLUDE_SANDBOX`): liga o conector Sandbox no widget, como o site.
  includeSandbox: z.boolean().optional(),
});

/**
 * Uma conexão do snapshot (`get_open_finance_snapshot`, db/open_finance.py).
 * `ui` é o estado exibível decidido por `connection_ui_state`
 * (core/services/pluggy_health.py): o app só desenha, nunca deriva.
 */
const conexaoSchema = z.object({
  provider_item_id: z.string().nullable(),
  institution_name: z.string().nullable(),
  ui: z.object({ state: z.string(), label: z.string(), detail: z.string().nullable().optional() }),
});

/** `GET /open-finance/{uid}` e `POST /open-finance/{uid}/pluggy-item`: os dois devolvem `**snapshot`. */
export const conexoesSchema = z.object({ connections: z.array(conexaoSchema) });

export type Conexao = z.infer<typeof conexaoSchema>;
