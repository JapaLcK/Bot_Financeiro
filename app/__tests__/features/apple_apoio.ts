import * as AppleAuthentication from "expo-apple-authentication";

import { credencialDe, resposta, type Rota } from "./auth_apoio";
import { rotasGoogle } from "./google_apoio";

/** Apoio dos testes do "Continuar com a Apple" (`apple`, `entrar_apple_tela`, `cadastro_social_tela`). */

export const folhaApple = jest.mocked(AppleAuthentication.signInAsync);

/** O que a folha da Apple devolve quando a pessoa confirma. */
export function credencialApple(
  extra: Partial<AppleAuthentication.AppleAuthenticationCredential> = {},
): AppleAuthentication.AppleAuthenticationCredential {
  return {
    user: "001234.apple",
    state: null,
    fullName: null,
    email: null,
    realUserStatus: 1,
    identityToken: "id-token-ana",
    authorizationCode: "c",
    ...extra,
  };
}

/** A folha confirma com `credencialApple(extra)`. */
export function voltaDaApple(extra: Partial<AppleAuthentication.AppleAuthenticationCredential> = {}) {
  folhaApple.mockReset();
  folhaApple.mockResolvedValue(credencialApple(extra));
}

/** A folha rejeita com o `code` do expo (`ERR_REQUEST_CANCELED`, `ERR_REQUEST_FAILED`...). */
export function falhaDaApple(code: string) {
  folhaApple.mockReset();
  folhaApple.mockRejectedValue(Object.assign(new Error(code), { code }));
}

/** Relay (P6): o e-mail que a Apple cria para quem oculta o próprio. */
export const PENDENTE_APPLE = {
  signup_required: true,
  signup_token: "gso_leo",
  email: "x1y2@privaterelay.appleid.com",
  name_hint: "Leo Lima",
};

/** Backend falso: `id-token-ana` entra como Ana; `id-token-leo` é conta nova (relay). As rotas do Google seguem. */
export function rotasApple(extra: Record<string, Rota> = {}) {
  rotasGoogle({
    "/auth/apple/exchange": (o) => {
      const token = (JSON.parse(String(o.body)) as { identity_token: string }).identity_token;
      if (token === "id-token-ana") return resposta(200, credencialDe("ana@x.com"));
      if (token === "id-token-leo") return resposta(200, PENDENTE_APPLE);
      return resposta(400, { detail: "Não deu para entrar com a Apple. Tente de novo.", code: "apple_token_invalid" });
    },
    "/auth/apple/complete-signup": () => resposta(200, credencialDe("leo@x.com")),
    ...extra,
  });
}
