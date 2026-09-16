import Constants from "expo-constants";
import { z } from "zod";

import { credenciaisSchema } from "./schemas/auth";
import { lerCredenciais, limparSe, trocarSe } from "../storage/secure";

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

/** Falha de renovação temporária: o servidor respondeu, mas mal. Dá para tentar de novo. */
export class RenovacaoIndisponivel extends ErroDeApi {
  constructor(status: number) {
    super(status, "Não conseguimos falar com o PigBank agora. Tente de novo.");
    this.name = "RenovacaoIndisponivel";
  }
}

/**
 * O resultado de uma renovação, e o MOTIVO quando ela não acontece.
 *
 * Um booleano não bastava: "a sessão acabou" e "o servidor está instável" levam
 * o usuário a telas opostas — a primeira pede login, a segunda pede paciência.
 * Colapsar as duas em `null` fazia um 500 do backend aparecer como "Entre para
 * continuar", que é mentir sobre o estado da conta.
 */
type Renovacao =
  | { ok: true; access: string; refresh: string }
  | { ok: false; motivo: "terminal" }
  | { ok: false; motivo: "transitorio"; status: number }
  | { ok: false; motivo: "sessao-trocou" };

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
 * problema: se o usuário sair e outra conta entrar entre o 401 e a renovação, a
 * promessa devolveria a credencial da conta NOVA para uma requisição que nasceu
 * na antiga — e num caminho de dinheiro isso escreve na conta errada.
 *
 * A conferência acontece DUAS vezes, antes e depois da ida ao servidor, e a
 * segunda é a que importa: a troca de conta pode acontecer com a requisição no
 * ar, e aí a gravação do resultado restauraria a sessão antiga por cima da
 * nova. Conferir só na entrada deixava essa janela aberta.
 */
let renovacaoEmVoo: { refresh: string; promessa: Promise<Renovacao> } | null =
  null;

/**
 * A última rotação bem-sucedida: o token consumido E o que nasceu dele.
 *
 * Existe para o 401 atrasado: duas requisições saem com o mesmo access token
 * expirado, a primeira renova e termina, e só então a segunda recebe o 401 dela.
 * Nesse instante `renovacaoEmVoo` já foi limpo e o cofre já tem o token novo, de
 * modo que a conferência de dono acusaria "a sessão trocou" — e a tela mandaria
 * o usuário para o login com a sessão perfeitamente viva.
 *
 * **Os dois lados são necessários, e guardar só o consumido abria uma brecha
 * pior que o problema.** Se a conta A renova, a conta B entra, e só então chega
 * o 401 atrasado da A, o token da A ainda casa com o consumido — e devolver a
 * credencial corrente entregaria a da B para repetir uma operação da A. Num
 * POST de dinheiro, escrita na conta errada.
 *
 * Com o par, a pergunta passa a ser a certa: "o cofre ainda contém exatamente o
 * sucessor daquele token?". Se contém, é a mesma sessão, só renovada por um
 * vizinho. Se não contém, alguém trocou de conta, e aí é fim de sessão mesmo.
 */
let ultimaRotacao: { consumido: string; sucessor: string } | null = null;

async function renovar(refreshDeOrigem: string): Promise<Renovacao> {
  if (renovacaoEmVoo?.refresh === refreshDeOrigem) return renovacaoEmVoo.promessa;

  const promessa = (async (): Promise<Renovacao> => {
    try {
      const antes = await lerCredenciais();
      if (!antes || antes.refresh !== refreshDeOrigem) {
        // Esta MESMA sessão já foi renovada por um vizinho? Só então não há
        // erro. A conferência é pelos DOIS lados: o token de origem tem de ser
        // o que foi consumido, E o cofre tem de conter exatamente o sucessor
        // dele. Sem a segunda metade, uma troca de conta depois da rotação
        // devolveria a credencial da conta nova.
        if (
          antes &&
          ultimaRotacao?.consumido === refreshDeOrigem &&
          ultimaRotacao.sucessor === antes.refresh
        ) {
          return { ok: true, access: antes.access, refresh: antes.refresh };
        }
        return { ok: false, motivo: "sessao-trocou" };
      }

      const resposta = await fetch(`${baseUrl()}/auth/refresh`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${refreshDeOrigem}`,
          [HEADER_CLIENTE]: CLIENTE,
          "Content-Type": "application/json",
        },
        credentials: "omit",
      });

      // Só o 401 PROVA que a sessão acabou. Um 429 ou um 500 é incidente
      // passageiro do servidor, e tratá-lo como fim de sessão transformaria
      // dois minutos de instabilidade em logout de todo mundo.
      if (resposta.status === 401) {
        // Apaga só se ainda for a MESMA sessão, e a comparação tem de ser
        // ATÔMICA com a escrita: se outra conta entrou enquanto isto estava no
        // ar, apagar levaria a sessão dela junto.
        await limparSe(refreshDeOrigem);
        return { ok: false, motivo: "terminal" };
      }
      if (!resposta.ok) {
        return { ok: false, motivo: "transitorio", status: resposta.status };
      }

      const novas = credenciaisSchema.parse(await resposta.json());
      // Daqui em diante o token de origem está COMPROVADAMENTE consumido: o
      // servidor respondeu 200 e rotacionou. Isso separa este caso do caso
      // ambíguo lá do `catch` — e a diferença muda o que é seguro fazer.
      //
      // Se a gravação falhar (keychain recusando), deixar o token velho no
      // cofre garantiria o replay na renovação seguinte, e o servidor trata
      // replay como roubo: revoga tudo do usuário, em todos os aparelhos.
      // Apagar troca isso por um login a mais neste aparelho. Entre perder a
      // sessão aqui e perder em todos, a escolha não é difícil.
      //
      // Compara-e-troca. A conta pode ter trocado com a requisição no ar, e
      // conferir numa chamada para gravar na seguinte deixa exatamente a janela
      // em que a outra conta cabe — o resultado seria a sessão antiga
      // restaurada por cima da nova.
      let trocou: boolean;
      try {
        trocou = await trocarSe(refreshDeOrigem, {
          access: novas.access_token,
          refresh: novas.refresh_token,
        });
      } catch {
        await limparSe(refreshDeOrigem).catch(() => undefined);
        return { ok: false, motivo: "terminal" };
      }
      if (!trocou) return { ok: false, motivo: "sessao-trocou" };
      ultimaRotacao = {
        consumido: refreshDeOrigem,
        sucessor: novas.refresh_token,
      };
      return {
        ok: true,
        access: novas.access_token,
        refresh: novas.refresh_token,
      };
    } catch {
      // Falha de REDE não apaga a sessão: o token pode estar perfeitamente vivo
      // e o usuário só estar no elevador. Quem apaga é o 401 acima, que é
      // resposta do servidor.
      //
      // ponytail: fica o caso ambíguo — a resposta pode ter se perdido DEPOIS
      // de o servidor rotacionar, e aí a renovação seguinte reapresenta um
      // token gasto, que o servidor trata como roubo. Decisão do dono: manter
      // assim e medir com app em produção. A mitigação certa é do lado do
      // servidor (janela de graça na rotação), não um sinalizador no cliente
      // que pode ficar preso e deslogar quem está bem.
      return { ok: false, motivo: "transitorio", status: 0 };
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
  /**
   * Credencial FIXA, em vez da que estiver no cofre na hora.
   *
   * É o que o logout precisa: ele captura a sessão que iniciou a saída, e entre
   * essa captura e a leitura interna daqui outra conta pode entrar — aí a
   * requisição sairia autenticada como ela e revogaria a sessão de quem acabou
   * de chegar. Com a credencial fixa, a requisição fala pela sessão que a
   * originou, e só por ela.
   *
   * Quem passa isto também abre mão da renovação: não faz sentido renovar uma
   * sessão que se está encerrando.
   */
  credencial?: { access: string; refresh: string };
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
  const guardadas = opcoes.semAuth
    ? null
    : (opcoes.credencial ?? (await lerCredenciais()));
  let resposta = await enviar(rota, opcoes, guardadas?.access ?? null);

  if (resposta.status === 401 && !opcoes.semAuth && !opcoes.credencial) {
    // Sem credencial de origem não há o que renovar — e renovar com a de outro
    // dono é justamente o que a amarração impede.
    if (!guardadas) throw new SessaoExpirada();
    const renovada = await renovar(guardadas.refresh);
    if (!renovada.ok) {
      // Instabilidade do servidor NÃO é fim de sessão. A tela que trata
      // `SessaoExpirada` manda o usuário para o login; mandá-lo para lá por
      // causa de um 500 é mentir sobre o estado da conta dele.
      if (renovada.motivo === "transitorio") {
        throw new RenovacaoIndisponivel(renovada.status);
      }
      throw new SessaoExpirada();
    }
    resposta = await enviar(rota, opcoes, renovada.access);
    if (resposta.status === 401) {
      // Renovou e AINDA assim tomou 401: a sessão morreu entre as duas
      // requisições (revogada noutro aparelho, logout, troca de senha). É
      // terminal, e a credencial recém-guardada tem de sair do keychain junto —
      // senão `temSessao()` segue dizendo que sim, o app abre como logado e
      // tenta renovar de novo a cada início, sem nunca chegar à tela de entrada.
      //
      // E só apaga se ainda for a mesma sessão: outra conta pode ter entrado
      // enquanto isto estava no ar, e apagar levaria a sessão dela junto.
      await limparSe(renovada.refresh);
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

/** Só para teste: zera o estado de renovação entre casos. */
export function _resetRenovacao(): void {
  renovacaoEmVoo = null;
  ultimaRotacao = null;
}
