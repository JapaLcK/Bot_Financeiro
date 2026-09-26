import * as WebBrowser from "expo-web-browser";

import { ErroDeApi, baseUrl } from "@/api/client";
import { GENERICO, textoDaFalha, type EstadoEntrar } from "@/features/auth/entrar";
import { EntradaSuperada, completarCadastroGoogle, entrarComGoogle, pendenteGoogle } from "@/services/auth";
import { FalhaNoCofre } from "@/storage/secure";

/**
 * "Continuar com Google" na rota Entrar — as fases G, C e K de `EstadoEntrar`.
 * Sem JSX, como `entrar.ts`: o Jest exercita as transições com os serviços reais.
 *
 * O retorno do Google chega SÓ pelo `openAuthSessionAsync` (a
 * `ASWebAuthenticationSession` devolve a URL a quem abriu). Não existe rota
 * `auth` no expo-router: um `pigbank://auth?code=` vindo de fora não faz nada,
 * e é isso que fecha o login forçado por link (CSRF de login).
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
export const ERRO_COFRE_GOOGLE =
  "Sua conta foi criada, mas não conseguimos abrir a sessão neste aparelho. Entre com o Google de novo.";

export type RetornoGoogle =
  | { tipo: "code"; valor: string }
  | { tipo: "onboarding"; valor: string }
  | { tipo: "erro"; valor: keyof typeof TEXTO_DO_ERRO }
  | { tipo: "invalido" };

/**
 * Lê a URL de volta. Scheme e host EXATOS, e exatamente UM parâmetro conhecido
 * com valor não vazio; qualquer outra coisa é `invalido`. À mão, sem `URL`: o
 * polyfill do Hermes não tem prova aqui de que separa host e caminho como o
 * navegador (`pigbank://auth.x`, `pigbank://auth/x`).
 */
export function lerRetorno(url: string): RetornoGoogle {
  if (!url.startsWith(`${RETORNO}?`)) return { tipo: "invalido" };
  const par = url.slice(RETORNO.length + 1);
  const igual = par.indexOf("=");
  if (igual < 1 || par.includes("&") || par.includes("#")) return { tipo: "invalido" };
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

/** 5xx, rede, tempo limite e contrato quebrado: texto genérico. O resto, o `detail` do servidor. */
const textoGoogle = (e: unknown) => (e instanceof ErroDeApi && e.status >= 500 ? GENERICO : textoDaFalha(e));

/**
 * F → G → resultado. NUNCA devolve `null`: G tem todos os botões desativados,
 * e `null` (o "nada" de `enfileirar`) prenderia a tela nela.
 */
export async function continuarComGoogle(autenticar: () => void): Promise<EstadoEntrar> {
  let retorno: RetornoGoogle;
  try {
    const r = await WebBrowser.openAuthSessionAsync(`${baseUrl()}/auth/google/start?app=2`, RETORNO);
    // cancel, dismiss, locked: a pessoa fechou a folha (inclusive na landing de state vencido).
    if (r.type !== "success") return { fase: "formulario" };
    retorno = lerRetorno(r.url);
  } catch {
    return { fase: "formulario", aviso: GENERICO };
  }
  if (retorno.tipo === "erro") return { fase: "formulario", aviso: TEXTO_DO_ERRO[retorno.valor] };
  if (retorno.tipo === "onboarding") return abrirCadastro(retorno.valor);
  if (retorno.tipo === "invalido") return { fase: "formulario", aviso: GENERICO };

  try {
    const r = await entrarComGoogle(retorno.valor);
    if (r.fase === "mfa") return { fase: "mfa", desafio: r.desafio, email: r.email, modo: "totp" };
    autenticar();
    // Não chega a renderizar: o `Stack.Protected` troca a rota com `autenticar()`.
    return { fase: "google" };
  } catch (e) {
    if (e instanceof EntradaSuperada) return { fase: "formulario" };
    if (e instanceof FalhaNoCofre) return { fase: "erro-cofre" };
    return { fase: "formulario", aviso: textoGoogle(e) };
  }
}

/** G → C: busca o e-mail e o nome sugerido do pré-cadastro. */
async function abrirCadastro(token: string): Promise<EstadoEntrar> {
  try {
    const p = await pendenteGoogle(token);
    return { fase: "google-cadastro", token, email: p.email, nome: p.name_hint };
  } catch (e) {
    const expirou = e instanceof ErroDeApi && e.status === 404;
    return { fase: "formulario", aviso: expirou ? CADASTRO_GOOGLE_EXPIRADO : GENERICO };
  }
}

/**
 * K → resultado. O 400 do `complete-signup` só distingue o cadastro vencido
 * pelo texto (`consume_pending_google_signup`, `db/google_auth.py`): esse volta
 * ao formulário, porque o token morreu; os de nome/telefone ficam em C.
 *
 * ponytail: casa pelo começo do `detail`, sem `code` no corpo. Se o texto do
 * servidor mudar, o vencido cai em C com o aviso certo e a pessoa volta pelo
 * Voltar. Ganha um `code` no backend quando isso incomodar.
 */
export async function criarContaComGoogle(
  c: { token: string; email: string; nome: string },
  telefone: string,
  autenticar: () => void,
): Promise<EstadoEntrar> {
  const voltaAoC = (aviso?: string): EstadoEntrar => ({ fase: "google-cadastro", ...c, aviso });
  try {
    await completarCadastroGoogle(c.token, c.nome.trim(), telefone.replace(/\D+/g, ""));
    autenticar();
    return { fase: "google-criando", token: c.token, email: c.email };
  } catch (e) {
    if (e instanceof EntradaSuperada) return voltaAoC();
    if (e instanceof FalhaNoCofre) return { fase: "erro-cofre", mensagem: ERRO_COFRE_GOOGLE };
    if (e instanceof ErroDeApi && e.status === 400 && e.detalhe.startsWith("Cadastro expirado")) {
      return { fase: "formulario", aviso: e.detalhe };
    }
    return voltaAoC(textoGoogle(e));
  }
}
