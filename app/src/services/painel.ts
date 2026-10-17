import { z } from "zod";
import { chamar, chamarFluxo, comLimite } from "@/api/client";
import * as S from "@/api/schemas/painel";
/** Cancelamento pai não reaproveita o timer de uma chamada anterior. */
export async function lerRecurso<T>(rota: string, schema: z.ZodType<T>, pai: AbortController) {
  const filho = new AbortController();
  const abortar = () => filho.abort();
  if (pai.signal.aborted) filho.abort();
  pai.signal.addEventListener("abort", abortar, { once: true });
  try { return await chamar(rota, schema, { sinal: comLimite(undefined, filho) }); }
  finally { pai.signal.removeEventListener("abort", abortar); }
}
export const salvarPerfil = (perfil: S.PerfilPainel, pai: AbortController) => chamar("/api/app/perfil", S.perfilPainelSchema, { metodo: "PUT", corpo: { perfil }, sinal: comLimite(undefined, pai) });
/** O turno do chat pode passar dos 15 s padrão (LLM + ferramentas no servidor). */
export const LIMITE_CHAT_MS = 60_000;
export const conversarFluxo = (message: string, pai: AbortController, aoEvento: (bruto: unknown) => void) => chamarFluxo("/ai/chat/stream", { metodo: "POST", corpo: { message, cards: S.CARDS_DA_TOOL }, sinal: comLimite(LIMITE_CHAT_MS, pai) }, aoEvento); // os 60 s valem para o total, chips incluídos
export const avaliarResposta = (id: number, nota: 1 | -1 | null, pai: AbortController) => chamar(`/ai/messages/${id}/feedback`, S.feedbackSchema, { metodo: "POST", corpo: { nota }, sinal: comLimite(undefined, pai) });
export const cancelarPendencia = (pai: AbortController) => chamar("/ai/pending", S.cancelamentoSchema, { metodo: "DELETE", sinal: comLimite(undefined, pai) });
export const marcarAssinatura = (chave: string, status: "assinatura" | "ignorar" | "nenhuma", pai: AbortController) => chamar("/api/app/assinaturas/marca", S.assinaturasSchema, { metodo: "POST", corpo: { chave, status }, sinal: comLimite(undefined, pai) });
export const responderReceita = (chave: string, resposta: "sim" | "nao" | "desfazer", pai: AbortController) => chamar("/api/app/previsao/receita", S.previsaoSchema, { metodo: "POST", corpo: { chave, resposta }, sinal: comLimite(undefined, pai) }); // o corpo é a Previsão do MENOR horizonte: só valida o contrato; quem manda é a releitura
