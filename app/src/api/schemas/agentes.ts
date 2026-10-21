import { z } from "zod";
// `/agents/*` (frontend/routes/agents.py): objeto padrão do zod, campos extras são descartados.
export const agentesSchema = z.object({
  catalog: z.array(z.object({ kind: z.string(), nome: z.string(), disponivel: z.boolean(), status: z.string().nullable(), energy_cost: z.number() })),
  can_chat: z.boolean().optional(), can_activate: z.boolean().optional(),
  energy_enabled: z.boolean(), energy_budget: z.number(), energy_used: z.number(),
});
/** O PADRAO_MES de `api/v2/resumo_mes.py` (`tests/test_agent_chat_card.py` prende a cópia): o mês entra na URL do GET. */
const MES = z.string().regex(/^[1-9][0-9]{3}-(0[1-9]|1[0-2])$/);
const motivos = z.array(z.string()).catch([]);
/**
 * O card abaixo da resposta (`core/services/agent_chat_card.py`; porta de `cardDe` em `webapp/src/dashboard/lib/agentes.ts`). O servidor só diz QUAL: os números vêm da rota do tipo.
 * Tipo desconhecido ou forma inválida = nenhum card (o `.catch(null)` de `respostaAgenteSchema`): a resposta de texto nunca se perde por causa do card.
 */
export const cardAgenteSchema = z.discriminatedUnion("tipo", [
  z.object({ tipo: z.literal("investido") }), z.object({ tipo: z.literal("renda_fixa") }), z.object({ tipo: z.literal("assinaturas") }), z.object({ tipo: z.literal("caixinhas") }),
  z.object({ tipo: z.literal("contas"), hoje: z.string().regex(/^\d{4}-\d{2}-\d{2}$/) }),
  z.object({ tipo: z.literal("orcamentos"), mes: MES }),
  z.object({ tipo: z.literal("resumo_mes"), mes: MES, motivos }),
  z.object({ tipo: z.literal("comparacao_meses"), meses: z.tuple([MES, MES]).refine(([a, b]) => a < b), motivos }), // o mais antigo primeiro: a diferença é novo − antigo
]);
export type CardAgente = z.output<typeof cardAgenteSchema>;
export const respostaAgenteSchema = z.object({
  reply: z.string(), context: z.string().nullable(),
  redirects: z.array(z.object({ kind: z.string(), name: z.string(), question: z.string(), access: z.string(), needs_data: z.boolean().optional() })).optional(),
  usage: z.object({ used: z.number(), limit: z.number() }).optional(),
  card: cardAgenteSchema.nullable().optional().catch(null),
});
export const ativacaoSchema = z.object({ ok: z.boolean() });
