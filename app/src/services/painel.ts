import { z } from "zod";
import { chamar, comLimite } from "@/api/client";
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
export const enviarMensagem = (message: string, pai: AbortController) => chamar("/ai/chat", S.respostaChatSchema, { metodo: "POST", corpo: { message }, sinal: comLimite(undefined, pai) });
export const marcarAssinatura = (chave: string, status: "assinatura" | "ignorar" | "nenhuma", pai: AbortController) => chamar("/api/app/assinaturas/marca", S.assinaturasSchema, { metodo: "POST", corpo: { chave, status }, sinal: comLimite(undefined, pai) });
