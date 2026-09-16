import Constants from "expo-constants";
import { z } from "zod";

import { credenciaisSchema } from "./schemas/auth";
import {
  guardarCredenciais,
  lerCredenciais,
  limparCredenciais,
} from "../storage/secure";

/** Header que faz o servidor entregar token no corpo e NENHUM cookie. */
const HEADER_CLIENTE = "X-PigBank-Client";
const CLIENTE = "app";

export class ErroDeApi extends Error {
  constructor(
    readonly status: number,
    readonly detalhe: string,
    readonly corpo?: unknown,
  ) {
    super(detalhe);
    this.name = "ErroDeApi";
  }
}

/** Sessão acabou: quem chamou tem de mandar o usuário para o login. */
export class SessaoExpirada extends ErroDeApi {
  constructor() {
    super(401, "Sua sessão expirou. Entre de novo.");
    this.name = "SessaoExpirada";
  }
}

/** Resposta com forma diferente da esperada — contrato quebrou, não é rede. */
export class ContratoInvalido extends ErroDeApi {
  constructor(readonly rota: string, causa: z.ZodError) {
    super(200, `Resposta inesperada de ${rota}`, causa.issues);
    this.name = "ContratoInvalido";
  }
}

function baseUrl(): string {
  const url = Constants.expoConfig?.extra?.apiUrl;
  if (typeof url !== "string" || !url) {
    throw new Error("apiUrl ausente em app.config.ts — confira o .env");
  }
  return url.replace(/\/+$/, "");
}

/**
 * Renovação DEDUPLICADA, e amarrada ao token que a originou.
 *
 * Sem a deduplicação, uma tela que dispara seis requisições ao abrir e toma
 * seis 401 dispara seis refresh. Cinco apresentam um token já consumido, e o
 * backend trata reapresentação como ROUBO: revoga tudo do usuário. O sintoma
 * seria logout aleatório ao abrir o app, com a causa no cliente e não no
 * servidor. É o mesmo raciocínio do `auth-refresh.js` do site.
 *
 * E a amarração ao token existe porque a deduplicação global tinha um segundo
 * problema: se o usuário sair e outra conta entrar entre o 401 e a renovação,
 * a promessa devolveria a credencial da conta NOVA para uma requisição que
 * nasceu na antiga — e num caminho de dinheiro isso escreve na conta errada.
 */
let renovacaoEmVoo: { refresh: string; promessa: Promise<string | null> } | null =
  null;

async function renovar(refreshDeOrigem: string): Promise<string | null> {
  // Deduplicação POR TOKEN, não global. Uma renovação em voo só serve a quem
  // partiu do MESMO refresh: se o usuário sair e outra conta entrar no meio, a
  // promessa da conta A não pode entregar o token da B a uma requisição da A —
  // e num caminho de dinheiro isso escreveria na conta errada.
  if (renovacaoEmVoo?.refresh === refreshDeOrigem) return renovacaoEmVoo.promessa;

  const promessa = (async () => {
    try {
      const guardadas = await lerCredenciais();
      // A sessão trocou por baixo (logout, outra conta): não renova nada, e
      // sobretudo não devolve a credencial de outro dono para uma requisição
      // que nasceu nesta.
      if (!guardadas || guardadas.refresh !== refreshDeOrigem) return null;

      const resposta = await fetch(`${baseUrl()}/auth/refresh`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${guardadas.refresh}`,
          [HEADER_CLIENTE]: CLIENTE,
          "Content-Type": "application/json",
        },
        credentials: "omit",
      });

      // Só o 401 PROVA que a sessão acabou. Um 429 ou um 500 é incidente
      // passageiro do servidor, e apagar a credencial ali transformaria dois
      // minutos de instabilidade em logout definitivo de todo mundo.
      if (resposta.status === 401) {
        await limparCredenciais();
        return null;
      }
      if (!resposta.ok) return null;

      const novas = credenciaisSchema.parse(await resposta.json());
      await guardarCredenciais({
        access: novas.access_token,
        refresh: novas.refresh_token,
      });
      return novas.access_token;
    } catch {
      // Falha de REDE não apaga a sessão: o token pode estar perfeitamente vivo
      // e o usuário só estar no elevador. Quem apaga é o 401 acima, que é
      // resposta do servidor.
      //
      // ponytail: fica o caso ambíguo — a resposta pode ter se perdido DEPOIS
      // de o servidor rotacionar, e aí a renovação seguinte reapresenta um
      // token gasto, que o servidor trata como roubo. A decisão do dono é
      // manter assim e medir com app em produção; a mitigação certa é do lado
      // do servidor (janela de graça na rotação), não um sinalizador no cliente
      // que pode ficar presente e deslogar quem está bem.
      return null;
    }
  })();

  renovacaoEmVoo = { refresh: refreshDeOrigem, promessa };
  try {
    return await promessa;
  } finally {
    if (renovacaoEmVoo?.refresh === refreshDeOrigem) renovacaoEmVoo = null;
  }
}

type Opcoes = {
  metodo?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  corpo?: unknown;
  /** Rotas públicas (config do app, por exemplo) não mandam credencial. */
  semAuth?: boolean;
  sinal?: AbortSignal;
};

async function enviar(rota: string, opcoes: Opcoes, access: string | null) {
  const metodo = opcoes.metodo ?? "GET";
  const cabecalhos: Record<string, string> = { [HEADER_CLIENTE]: CLIENTE };
  if (access) cabecalhos["Authorization"] = `Bearer ${access}`;
  // Toda ESCRITA declara JSON, inclusive a que não tem corpo (logout). É a 2ª
  // condição da isenção de CSRF do servidor: um `<form>` cross-site só emite
  // `urlencoded`, `multipart` ou `text/plain`, então exigir JSON fecha a porta
  // pela qual um navegador atacaria. Escrita sem o cabeçalho toma 403.
  if (metodo !== "GET") cabecalhos["Content-Type"] = "application/json";
  return fetch(`${baseUrl()}${rota}`, {
    method: metodo,
    headers: cabecalhos,
    body: opcoes.corpo === undefined ? undefined : JSON.stringify(opcoes.corpo),
    signal: opcoes.sinal,
    // Sem cookie, sempre. O fetch do React Native tem cookie jar ligado por
    // padrão, e um cookie guardado sem querer vira credencial ambiente — o
    // servidor então volta a exigir o par cookie+header do CSRF, que o app não
    // tem, e a escrita seguinte falha com 403.
    credentials: "omit",
  });
}

/**
 * Uma chamada à API: manda, renova uma vez em 401, valida a forma da resposta.
 *
 * `schema` não é opcional de propósito — ver `ContratoInvalido`.
 */
export async function chamar<T>(
  rota: string,
  schema: z.ZodType<T>,
  opcoes: Opcoes = {},
): Promise<T> {
  const guardadas = opcoes.semAuth ? null : await lerCredenciais();
  let resposta = await enviar(rota, opcoes, guardadas?.access ?? null);

  if (resposta.status === 401 && !opcoes.semAuth) {
    // Sem credencial de origem não há o que renovar — e renovar com a de outro
    // dono é justamente o que a amarração abaixo impede.
    if (!guardadas) throw new SessaoExpirada();
    const novoAccess = await renovar(guardadas.refresh);
    if (!novoAccess) throw new SessaoExpirada();
    resposta = await enviar(rota, opcoes, novoAccess);
    if (resposta.status === 401) {
      // Renovou e AINDA assim tomou 401: a sessão morreu entre as duas
      // requisições (revogada noutro aparelho, logout, troca de senha). É
      // terminal, e a credencial recém-guardada tem de sair do keychain junto —
      // senão `temSessao()` segue dizendo que sim, o app abre como logado e
      // tenta renovar de novo a cada início, sem nunca chegar à tela de entrada.
      await limparCredenciais();
      throw new SessaoExpirada();
    }
  }

  if (!resposta.ok) {
    throw new ErroDeApi(resposta.status, await mensagemDeErro(resposta));
  }

  const bruto = await resposta.json().catch(() => null);
  const conferido = schema.safeParse(bruto);
  if (!conferido.success) throw new ContratoInvalido(rota, conferido.error);
  return conferido.data;
}

/**
 * Mensagem para o usuário, nunca o texto cru do servidor.
 *
 * O backend devolve `{"detail": ...}` em vários formatos — string, objeto com
 * `error`/`message`, lista de erros do Pydantic. Mostrar o JSON na tela é um
 * defeito que o produto já viveu no site (o modal que exibia `{"detail":...}`).
 */
async function mensagemDeErro(resposta: Response): Promise<string> {
  // 5xx ANTES de olhar o corpo, e não depois: em erro de servidor o `detail`
  // carrega a exceção crua (`psycopg.OperationalError`, um traceback), e a
  // ordem inversa entregava isso à tela do usuário. Foi o teste de 500 que
  // pegou — a versão anterior confiava no `detail` primeiro.
  if (resposta.status >= 500) {
    return "Tivemos um problema aqui. Tente de novo em instantes.";
  }
  const corpo = await resposta.json().catch(() => null);
  const detalhe = (corpo as { detail?: unknown } | null)?.detail;
  if (typeof detalhe === "string") return detalhe;
  if (detalhe && typeof detalhe === "object") {
    const d = detalhe as { message?: unknown; error?: unknown };
    if (typeof d.message === "string") return d.message;
    if (typeof d.error === "string") return d.error;
  }
  return "Não foi possível completar a ação.";
}

/** Só para teste: zera a renovação em voo entre casos. */
export function _resetRenovacao(): void {
  renovacaoEmVoo = null;
}
