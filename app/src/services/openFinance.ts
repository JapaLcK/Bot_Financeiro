import Constants, { ExecutionEnvironment } from "expo-constants";

import { chamar, comLimite } from "@/api/client";
import { conexoesSchema, connectTokenSchema } from "@/api/schemas/openFinance";

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

export function pedirConnectToken(uid: number) {
  const scheme = schemeDoApp();
  return chamar(`/open-finance/${uid}/connect-token`, connectTokenSchema, {
    metodo: "POST",
    corpo: scheme ? { app_scheme: scheme } : {},
    sinal: comLimite(),
  });
}

export const conexoes = (uid: number) =>
  chamar(`/open-finance/${uid}`, conexoesSchema, { sinal: comLimite() });

/** O servidor só aproveita o `id`, e confere o dono na Pluggy (`clientUserId`). */
export const registrarItem = (uid: number, itemId: string) =>
  chamar(`/open-finance/${uid}/pluggy-item`, conexoesSchema, {
    metodo: "POST",
    corpo: { item: { id: itemId } },
    sinal: comLimite(),
  });
