import { z } from "zod";
// `/agents/*` (frontend/routes/agents.py): objeto padrão do zod, campos extras são descartados.
export const agentesSchema = z.object({
  catalog: z.array(z.object({ kind: z.string(), nome: z.string(), disponivel: z.boolean(), status: z.string().nullable(), energy_cost: z.number() })),
  can_chat: z.boolean().optional(), can_activate: z.boolean().optional(),
  energy_enabled: z.boolean(), energy_budget: z.number(), energy_used: z.number(),
});
export const respostaAgenteSchema = z.object({
  reply: z.string(), context: z.string().nullable(),
  redirects: z.array(z.object({ kind: z.string(), name: z.string(), question: z.string(), access: z.string(), needs_data: z.boolean().optional() })).optional(),
  usage: z.object({ used: z.number(), limit: z.number() }).optional(),
});
export const ativacaoSchema = z.object({ ok: z.boolean() });
