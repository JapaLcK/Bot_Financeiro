import Constants, { ExecutionEnvironment } from "expo-constants";

import { chamar, comLimite } from "@/api/client";
import { conexoesSchema, connectTokenSchema, onboardingBancarioSchema, limiteBancarioSchema, desconectadoSchema } from "@/api/schemas/openFinance";

/**
 * As rotas de Open Finance de `frontend/routes/open_finance.py`. O `uid` vem
 * SEMPRE de `perfil()` (quem chama garante); o servidor ainda confere a sessão.
 */

/**
 * O scheme deste binário, para a Pluggy devolver o usuário a ELE depois do
 * OAuth do banco. Nenhuma lista aqui: o que vale é o que o `app.config.ts`
 * registrou, e `tests/test_app_espelhos.py` compara com a do servidor.
 *
 * No Expo Go não manda: o `pigbank-*://` não é dele, e o iOS não teria para
 * onde voltar (mesmo idioma de `features/bloqueio/tampa.ts`).
 */
function schemeDoApp(): string | null {
  if (Constants.executionEnvironment === ExecutionEnvironment.StoreClient) return null;
  const scheme = Constants.expoConfig?.scheme;
  return typeof scheme === "string" && scheme ? scheme : null;
}

export function pedirConnectToken(uid: number, itemId?: string, tentativaId?: string) {
  const scheme = schemeDoApp();
  return chamar(`/open-finance/${uid}/connect-token`, connectTokenSchema, {
    metodo: "POST",
    corpo: { ...(scheme ? { app_scheme: scheme, ...(tentativaId ? { attempt_id: tentativaId } : {}) } : {}), ...(itemId ? { item_id: itemId } : {}) },
    sinal: comLimite(),
  });
}

/** Timeout pertence à chamada, não à janela inteira de polling. */
async function comCancelamento<T>(pai: AbortController | undefined, operacao: (sinal: AbortSignal) => Promise<T>): Promise<T> {
  const filho = new AbortController();
  const abortar = () => filho.abort();
  if (pai?.signal.aborted) filho.abort();
  pai?.signal.addEventListener("abort", abortar, { once: true });
  try { return await operacao(comLimite(undefined, filho)); }
  finally { pai?.signal.removeEventListener("abort", abortar); }
}

export const conexoes = (uid: number, controlador?: AbortController) =>
  comCancelamento(controlador, (sinal) => chamar(`/open-finance/${uid}`, conexoesSchema, { sinal }));

/** O servidor só aproveita o `id`, e confere o dono na Pluggy (`clientUserId`). */
export const registrarItem = (uid: number, itemId: string, controlador?: AbortController) =>
  comCancelamento(controlador, (sinal) => chamar(`/open-finance/${uid}/pluggy-item`, conexoesSchema, {
    metodo: "POST",
    corpo: { item: { id: itemId } },
    sinal,
  }));

export const onboardingBancario = () => chamar("/onboarding/open-finance", onboardingBancarioSchema, { sinal: comLimite() });
export const limiteBancario = (uid: number) => chamar(`/open-finance/${uid}/limite`, limiteBancarioSchema, { sinal: comLimite() });
export const desconectarBanco = (uid: number, id: number) => chamar(`/open-finance/${uid}/connections/${id}`, desconectadoSchema, { metodo: "DELETE", sinal: comLimite() });
