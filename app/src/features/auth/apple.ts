import * as AppleAuthentication from "expo-apple-authentication";
import * as Crypto from "expo-crypto";

import { EntradaSuperada, entrarComApple } from "@/services/auth";
import { FalhaNoCofre } from "@/storage/secure";

import { textoSocial, type EstadoEntrar } from "./entrar";

/**
 * "Continuar com a Apple" na rota Entrar — a fase A de `EstadoEntrar` (tabela
 * A1–A14 do plano do item 7). Sem JSX, como `google.ts`: o Jest exercita as
 * transições com os serviços reais.
 *
 * O retorno da Apple chega SÓ pela promessa do `signInAsync`: não há URL de
 * volta nem rota para forjar.
 */

export const FALHA_APPLE = "Não deu para entrar com a Apple. Tente de novo.";

/**
 * F → A → resultado. NUNCA devolve `null` nem rejeita: A deixa os outros
 * botões desativados, e o "nada" de `enfileirar` prenderia a tela nela.
 *
 * Nonce: o cru (novo a cada toque) vai ao servidor; à Apple vai o SHA-256 dele
 * em hex minúsculo, que ela devolve dentro do identity token. Um token vazado
 * sem o cru não entra.
 */
export async function continuarComApple(autenticar: () => void): Promise<EstadoEntrar> {
  const nonce = Crypto.randomUUID();
  let token: string;
  let nome: string | null;
  try {
    const credencial = await AppleAuthentication.signInAsync({
      requestedScopes: [AppleAuthentication.AppleAuthenticationScope.FULL_NAME, AppleAuthentication.AppleAuthenticationScope.EMAIL],
      nonce: await Crypto.digestStringAsync(Crypto.CryptoDigestAlgorithm.SHA256, nonce),
    });
    if (!credencial.identityToken) return { fase: "formulario", aviso: FALHA_APPLE };
    token = credencial.identityToken;
    // A Apple manda o nome só na 1ª autorização; nas outras vem vazio. O corte
    // é o `max_length` do `AppleExchangeBody`: um 422 ali perderia o nome de vez.
    // Por code point, como o Pydantic conta: `.slice` em UTF-16 partiria um emoji.
    const n = credencial.fullName;
    nome = n && (n.givenName || n.familyName) ? Array.from(AppleAuthentication.formatFullName(n)).slice(0, 100).join("") : null;
  } catch (e) {
    // Fechou a folha: volta sem aviso. Qualquer outro código (sem ID Apple no
    // aparelho, FAILED, UNKNOWN...) é falha.
    if ((e as { code?: unknown } | null)?.code === "ERR_REQUEST_CANCELED") return { fase: "formulario" };
    return { fase: "formulario", aviso: FALHA_APPLE };
  }

  try {
    const r = await entrarComApple(token, nonce, nome);
    if (r.fase === "mfa") return { fase: "mfa", desafio: r.desafio, email: r.email, modo: "totp" };
    if (r.fase === "cadastro") {
      return { fase: "cadastro-social", provedor: "apple", token: r.token, email: r.email, nome: r.nome };
    }
    autenticar();
    // Não chega a renderizar: o `Stack.Protected` troca a rota com `autenticar()`.
    return { fase: "apple" };
  } catch (e) {
    if (e instanceof EntradaSuperada) return { fase: "formulario" };
    if (e instanceof FalhaNoCofre) return { fase: "erro-cofre" };
    return { fase: "formulario", aviso: textoSocial(e) };
  }
}
