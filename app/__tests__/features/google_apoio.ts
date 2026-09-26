import * as WebBrowser from "expo-web-browser";

import { credencialDe, resposta, rotear, type Rota } from "./auth_apoio";

/** Apoio dos testes do "Continuar com Google" (`entrar_google*`, `completar_cadastro_google*`, `google_deep_link`). */

export const abrirFolha = jest.mocked(WebBrowser.openAuthSessionAsync);

/** O que a `ASWebAuthenticationSession` devolve: a URL de volta, ou a folha fechada. */
export function voltaDoGoogle(url: string | { type: "cancel" | "dismiss" | "locked" }) {
  abrirFolha.mockReset();
  abrirFolha.mockResolvedValue((typeof url === "string" ? { type: "success", url } : url) as WebBrowser.WebBrowserAuthSessionResult);
}

export const PENDENTE = { email: "bia@gmail.com", name_hint: "Bia Souza" };
export const CODIGO_INVALIDO = {
  detail: "Não deu para concluir a entrada com o Google. Tente de novo.",
  code: "google_code_invalid",
};

/** Backend falso: `code-ana` troca pela conta Ana; `gso_bia` é o pré-cadastro da Bia. */
export function rotasGoogle(extra: Record<string, Rota> = {}) {
  rotear({
    "/auth/google/exchange": (o) =>
      (JSON.parse(String(o.body)) as { code: string }).code === "code-ana"
        ? resposta(200, credencialDe("ana@x.com"))
        : resposta(400, CODIGO_INVALIDO),
    "/auth/google/pending/gso_bia": () => resposta(200, PENDENTE),
    "/auth/google/complete-signup": () => resposta(200, credencialDe("bia@gmail.com")),
    ...extra,
  });
}
