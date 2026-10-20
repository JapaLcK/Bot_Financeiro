import { chamar, comLimite } from "@/api/client";
import { agentesSchema, ativacaoSchema, respostaAgenteSchema } from "@/api/schemas/agentes";
import { LIMITE_CHAT_MS, lerRecurso } from "./painel";
// Rotas de frontend/routes/agents.py. O uid vem sempre do dono da sessão (`dono.current`), nunca de texto digitado.
export const lerAgentes = (uid: number, pai: AbortController) => lerRecurso(`/agents/${uid}`, agentesSchema, pai);
export const ativarAgente = (uid: number, kind: string, pai: AbortController) => chamar(`/agents/${uid}/${kind}/activate`, ativacaoSchema, { metodo: "POST", corpo: {}, sinal: comLimite(undefined, pai) });
export const conversarComAgente = (uid: number, kind: string, message: string, context: string | null, pai: AbortController, encaminhada?: { needs_data: boolean }) => chamar(`/agents/${uid}/${kind}/chat`, respostaAgenteSchema, { metodo: "POST", corpo: { message, context, ...(encaminhada && { forwarded: true, needs_data: encaminhada.needs_data }) }, sinal: comLimite(LIMITE_CHAT_MS, pai) });
