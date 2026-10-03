import Constants from "expo-constants";
import * as Device from "expo-device";
import { Platform } from "react-native";

/**
 * O User-Agent do app: `PigBankApp/<versão> (<modelo>; <iOS|Android> <versão>)`.
 *
 * O servidor guarda o UA do login e tira dele o rótulo da lista de sessões
 * (`core/sessions.py::device_label`). O formato é o de
 * `tests/fixtures/user_agents_app.json`, lido pelo pytest e pelo Jest.
 */
type Entrada = {
  versao: string;
  modelo: string | null;
  so: string;
  versaoSo: string | null;
};

/**
 * Só letras e dígitos ASCII, espaço e `. , _ + -` — a mesma classe do campo
 * modelo no `_UA_APP` do servidor —, espaços colapsados, no máximo 64. Não é
 * estética: o OkHttp do Android LANÇA com header não-ASCII, e aí TODA
 * requisição do app cairia — login inclusive.
 *
 * A versão do app passa por aqui também, mas o servidor exige `\S+` nela: não
 * pode ter espaço. Ela vem do `app.config.ts` (constante do build), então
 * quem garante isso é quem a edita.
 */
function limpar(texto: string | null): string {
  return (texto ?? "")
    .replace(/[^A-Za-z0-9 .,_+-]/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 64)
    .trim();
}

export function montarUserAgent({ versao, modelo, so, versaoSo }: Entrada): string {
  const ios = so === "ios";
  const aparelho = limpar(modelo) || (ios ? "iPhone" : "Android");
  return `PigBankApp/${limpar(versao) || "0"} (${aparelho}; ${ios ? "iOS" : "Android"} ${limpar(versaoSo)})`;
}

export const USER_AGENT = montarUserAgent({
  versao: Constants.expoConfig?.version ?? "0",
  modelo: Device.modelName,
  so: Platform.OS,
  versaoSo: Device.osVersion ?? String(Platform.Version),
});
