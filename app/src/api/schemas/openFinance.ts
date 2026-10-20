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
  // https ou null; o host é fixado em `features/openFinance/conexoes/juntar.ts` antes de buscar. Servidor velho (sem o campo) e valor de forma estranha viram sem logo.
  logo_url: z.string().nullable().optional().catch(null),
  // Caminho do logo PRÓPRIO (`/brand/bancos/<ispb>.png`, mesma origem da API) ou null; a forma é conferida em `logoDoBanco` (juntar.ts). Ausente ou de tipo estranho = sem logo próprio.
  logo_proprio: z.string().nullable().optional().catch(null),
  // O id do conector da Pluggy (`pluggy_connector_id`): é por ele, e nunca pelo nome, que "Disponíveis para conectar" sabe o que já está conectado.
  institution_id: z.string().nullable().optional().catch(null),
  ui: z.object({ state: z.string(), label: z.string(), detail: z.string().nullable().optional() }),
});

/** `GET /open-finance/{uid}` e `POST /open-finance/{uid}/pluggy-item`: os dois devolvem `**snapshot`. */
export const conexoesSchema = z.object({ connections: z.array(conexaoSchema) });

export type Conexao = z.infer<typeof conexaoSchema>;

/**
 * `GET /open-finance/{uid}/connectors` (catálogo da Pluggy). Tolerante POR ITEM como o `bancosSchema`: item que não passa sai e os outros ficam; `connectors` ausente vira `[]`.
 * `logo` vem como "" quando o conector não tem; o host é fixado no render (`logoSeguro`), não aqui.
 */
const conectorSchema = z.object({ id: z.number().int().positive(), name: z.string().trim().min(1), logo: z.string().nullable().catch(null), logo_proprio: z.string().nullable().optional().catch(null) });
export const conectoresSchema = z.object({
  connectors: z.array(z.unknown()).catch([]).transform((itens) => itens.flatMap((i) => { const c = conectorSchema.safeParse(i); return c.success ? [c.data] : []; })),
});
export type Conector = z.output<typeof conectorSchema>;

/**
 * `POST /open-finance/{uid}/refresh` (`refresh_and_sync_pluggy_user`): `sync.items[]` é o veredito por item e `connections`, o snapshot de DEPOIS.
 * `sync.still_updating` fica de fora de propósito: é a soma dos itens (o app lê os itens). `sync.ok` é lido só para `items: []` com `ok: false` (a falha que não tem item para mostrar, #1100 D2);
 * é opcional para uma resposta sem o campo não virar contrato inválido.
 */
export const refreshSchema = conexoesSchema.extend({
  sync: z.object({ ok: z.boolean().optional(), items: z.array(z.object({
    item_id: z.string(), institution: z.string().nullable(), state: z.string(), label: z.string(),
    detail: z.string().nullable().optional(), reason: z.string().nullable().optional(), still_updating: z.boolean(),
  })) }),
});
export type Refresh = z.infer<typeof refreshSchema>;

export const onboardingBancarioSchema = z.object({
  ok: z.literal(true), completed: z.boolean(), completed_at: z.string().datetime({ offset: true }).nullable(),
}).refine((v) => v.completed === (v.completed_at !== null));

export const limiteBancarioSchema = z.object({
  ok: z.literal(true), of_banks_max: z.number().int().nullable(), em_uso: z.number().int().nonnegative(),
  pode_adicionar: z.boolean(), code: z.string().nullable(), message: z.string().nullable(),
});
export const desconectadoSchema = z.object({ ok: z.literal(true), deleted: z.number().int().nonnegative() });
