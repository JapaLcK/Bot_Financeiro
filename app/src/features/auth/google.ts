import * as Crypto from "expo-crypto";
import * as WebBrowser from "expo-web-browser";

import { ErroDeApi, baseUrl } from "@/api/client";
import { GENERICO, textoSocial, type EstadoEntrar } from "@/features/auth/entrar";
import { EntradaSuperada, entrarComGoogle, pendenteGoogle } from "@/services/auth";
import { FalhaNoCofre } from "@/storage/secure";

/**
 * "Continuar com Google" na rota Entrar — a fase G de `EstadoEntrar`, e a
 * entrada em C. C e K são comuns com a Apple (`cadastroSocial.ts`).
 * Sem JSX, como `entrar.ts`: o Jest exercita as transições com os serviços reais.
 *
 * O retorno do Google chega SÓ pelo `openAuthSessionAsync`, que devolve a URL
 * a quem abriu. Não existe rota `auth` no expo-router, e `+native-intent.ts` é uma
 * allowlist (só a volta do Open Finance passa): um `pigbank://auth?code=` vindo de
 * fora não faz nada, e é isso que fecha o login forçado por link (CSRF de login).
 * Medido SÓ no iOS: a defesa "não existe rota auth" não tem
 * medição no Android, onde o retorno do Custom Tab vem pelo `Linking` (#604).
 *
 * PKCE (#604): cada toque gera um `code_verifier` que vive só na memória DESTA
 * chamada (nunca em módulo, cofre ou disco), e segue em `cadastro-social`
 * (`verificador`) até o `complete-signup`, morrendo com o estado.
 *
 * O que isso NÃO garante sozinho. No Android a folha aberta aceita qualquer
 * `pigbank://auth?code=…` que chegue pelo `Linking` do `expo-web-browser`
 * (`WebBrowser.js` ~:350, `event.url.startsWith(returnUrl)`); o filtro do
 * roteador (`+native-intent.ts`) não alcança isso. A injeção de código é fechada
 * NO SERVIDOR: o backend do PR-2 só troca o código se `code_challenge =
 * S256(verifier)` (igualdade estrita); sem ele no ar, o verifier não protege
 * nada. E um app malicioso que registre o scheme `pigbank` e inicie o PRÓPRIO
 * fluxo (com o próprio verifier) NÃO é coberto: a defesa estrutural é trocar o
 * scheme por App Links https (follow-up da #604).
 */
const RETORNO = "pigbank://auth";

/** Os únicos `?erro=` do callback (`auth_google_callback`). `undefined` = volta sem aviso. */
const TEXTO_DO_ERRO = {
  cancelado: undefined,
  falha: "Não deu para entrar com o Google. Tente de novo.",
  email_nao_verificado: "Seu e-mail no Google ainda não foi verificado. Verifique no Google e tente de novo.",
  conta_em_exclusao: "Esta conta está agendada para exclusão.",
} as const;

export const CADASTRO_GOOGLE_EXPIRADO = "Seu cadastro pelo Google expirou. Tente de novo.";

export type RetornoGoogle =
  | { tipo: "code"; valor: string }
  | { tipo: "onboarding"; valor: string }
  | { tipo: "erro"; valor: keyof typeof TEXTO_DO_ERRO }
  | { tipo: "invalido" };

/**
 * Lê a URL de volta. Scheme e host EXATOS, e exatamente UM parâmetro conhecido
 * com valor não vazio; qualquer outra coisa é `invalido`. À mão, sem `URL`: o
 * polyfill do Hermes não tem prova aqui de que separa host e caminho como o
 * navegador (`pigbank://auth.x`, `pigbank://auth/x`). O fragmento é descartado
 * porque o 302 do callback herda o `#` da URL de entrada (RFC 7231 §7.1.2) e o
 * `ASWebAuthenticationSession` o devolve junto (Origem: #613).
 */
export function lerRetorno(url: string): RetornoGoogle {
  const semFragmento = url.split("#", 1)[0] ?? "";
  if (!semFragmento.startsWith(`${RETORNO}?`)) return { tipo: "invalido" };
  const par = semFragmento.slice(RETORNO.length + 1);
  const igual = par.indexOf("=");
  if (igual < 1 || par.includes("&")) return { tipo: "invalido" };
  const chave = par.slice(0, igual);
  let valor: string;
  try {
    valor = decodeURIComponent(par.slice(igual + 1));
  } catch {
    return { tipo: "invalido" };
  }
  if (!valor) return { tipo: "invalido" };
  if (chave === "code" || chave === "onboarding") return { tipo: chave, valor };
  const erro = Object.keys(TEXTO_DO_ERRO).find((k) => k === valor) as keyof typeof TEXTO_DO_ERRO | undefined;
  if (chave === "erro" && erro) return { tipo: "erro", valor: erro };
  return { tipo: "invalido" };
}

/** RFC 7636 §4.1: 32 bytes aleatórios em hex = 64 caracteres [0-9a-f], 256 bits. */
function novoVerificador(): string {
  const bytes = Crypto.getRandomBytes(32);
  // Sem entropia (vazio) o verifier seria "" e o RFC 7636 §4.1 pede 43+ caracteres: falha, não segue.
  if (bytes.length !== 32) throw new Error("sem entropia");
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

/** RFC 7636 §4.2 (S256): base64url sem padding do SHA-256 do verifier. */
export async function desafioDe(verificador: string): Promise<string> {
  const b64 = await Crypto.digestStringAsync(Crypto.CryptoDigestAlgorithm.SHA256, verificador, {
    encoding: Crypto.CryptoEncoding.BASE64,
  });
  return b64.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/**
 * F → G → resultado. NUNCA devolve `null`: G tem todos os botões desativados,
 * e `null` (o "nada" de `enfileirar`) prenderia a tela nela.
 */
export async function continuarComGoogle(autenticar: () => void): Promise<EstadoEntrar> {
  let retorno: RetornoGoogle;
  let verificador = "";
  try {
    verificador = novoVerificador();
    const desafio = await desafioDe(verificador);
    const r = await WebBrowser.openAuthSessionAsync(`${baseUrl()}/auth/google/start?app=2&code_challenge=${desafio}`, RETORNO);
    // cancel, dismiss, locked: a pessoa fechou a folha (inclusive na landing de state vencido).
    if (r.type !== "success") return { fase: "formulario" };
    retorno = lerRetorno(r.url);
  } catch {
    return { fase: "formulario", aviso: GENERICO };
  }
  if (retorno.tipo === "erro") return { fase: "formulario", aviso: TEXTO_DO_ERRO[retorno.valor] };
  if (retorno.tipo === "onboarding") return abrirCadastro(retorno.valor, verificador);
  if (retorno.tipo === "invalido") return { fase: "formulario", aviso: GENERICO };

  try {
    const r = await entrarComGoogle(retorno.valor, verificador);
    if (r.fase === "mfa") return { fase: "mfa", desafio: r.desafio, email: r.email, modo: "totp" };
    autenticar();
    // Não chega a renderizar: o `Stack.Protected` troca a rota com `autenticar()`.
    return { fase: "google" };
  } catch (e) {
    if (e instanceof EntradaSuperada) return { fase: "formulario" };
    if (e instanceof FalhaNoCofre) return { fase: "erro-cofre" };
    return { fase: "formulario", aviso: textoSocial(e) };
  }
}

/** G → C: busca o e-mail e o nome sugerido do pré-cadastro. */
async function abrirCadastro(token: string, verificador: string): Promise<EstadoEntrar> {
  try {
    const p = await pendenteGoogle(token);
    return { fase: "cadastro-social", provedor: "google", token, email: p.email, nome: p.name_hint, verificador };
  } catch (e) {
    const expirou = e instanceof ErroDeApi && e.status === 404;
    return { fase: "formulario", aviso: expirou ? CADASTRO_GOOGLE_EXPIRADO : GENERICO };
  }
}
