import { z } from "zod";

/** `POST /open-finance/{uid}/connect-token` (frontend/routes/open_finance.py). */
export const connectTokenSchema = z.object({
  ok: z.literal(true),
  accessToken: z.string().min(1),
  // Só dev/staging (`PLUGGY_INCLUDE_SANDBOX`): liga o conector Sandbox no widget, como o site.
  includeSandbox: z.boolean().optional(),
  // A lista que foi neste token (`pluggy_products()`, §0.7); o widget a recebe como `products`.
  // Inválido, `null` ou `[]` viram `undefined`: a lib poria "products=null" ou "products=" na URL,
  // e um campo estranho não pode impedir a conexão (sem ele o widget fica como antes).
  products: z.array(z.string().min(1)).min(1).optional().catch(undefined),
});

/**
 * Uma conexão do snapshot (`get_open_finance_snapshot`, db/open_finance.py).
 * `ui` é o estado exibível decidido por `connection_ui_state`
 * (core/services/pluggy_health.py): o app só desenha, nunca deriva.
 */
const conexaoSchema = z.object({
  id: z.number().int().positive(),
  status: z.string(),
  status_reason: z.string().nullable(),
  last_sync_at: z.string().datetime({ offset: true }).nullable(),
  reconnected_at: z.string().datetime({ offset: true }).nullable(),
  provider_item_id: z.string().nullable(),
  institution_name: z.string().nullable(),
  ui: z.object({ state: z.string(), label: z.string(), detail: z.string().nullable().optional() }),
});

/** `GET /open-finance/{uid}` e `POST /open-finance/{uid}/pluggy-item`: os dois devolvem `**snapshot`. */
export const conexoesSchema = z.object({ connections: z.array(conexaoSchema) });

export type Conexao = z.infer<typeof conexaoSchema>;

export const onboardingBancarioSchema = z.object({
  ok: z.literal(true), completed: z.boolean(), completed_at: z.string().datetime({ offset: true }).nullable(),
}).refine((v) => v.completed === (v.completed_at !== null));

export const limiteBancarioSchema = z.object({
  ok: z.literal(true), of_banks_max: z.number().int().nullable(), em_uso: z.number().int().nonnegative(),
  pode_adicionar: z.boolean(), code: z.string().nullable(), message: z.string().nullable(),
});
export const desconectadoSchema = z.object({ ok: z.literal(true), deleted: z.number().int().nonnegative() });
