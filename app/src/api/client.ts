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

/**
 * A sessão que fez o pedido não é mais a do aparelho.
 *
 * Distinta de `SessaoExpirada` de propósito: aquela quer dizer "entre de novo",
 * esta quer dizer "esta tela era de outra conta". Colapsar as duas faria uma
 * troca de conta bem-sucedida empurrar a pessoa para o login, logo depois de
 * ela ter entrado.
 */
export class RequisicaoSuperada extends ErroDeApi {
  constructor() {
    super(409, "Esta tela era de outra conta.");
    this.name = "RequisicaoSuperada";
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
 * A CADEIA recente de rotações: os tokens já consumidos e o que está em uso.
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
 * Com a cadeia, a pergunta passa a ser a certa: "este token pertence à mesma
 * linhagem do que está guardado agora?". Se pertence, é a mesma sessão, só
 * renovada por um vizinho. Se não, alguém trocou de conta, e é fim de sessão.
 *
 * **E é uma CADEIA, não um par.** Uma requisição atrasada pode ficar parada
 * enquanto a sessão roda duas ou três vezes (`rt0 → rt1 → rt2`), e um par só
 * lembraria do último salto — a mais atrasada de todas seria tratada como conta
 * trocada, justamente a que mais precisa da tolerância. O teto de oito mantém
 * isso limitado: memória de sessão, não registro histórico.
 */
const TETO_HISTORICO = 8;
let rotacoes: { consumidos: string[]; cabeca: string } | null = null;

/**
 * Registra que `consumido` virou `sucessor`, mantendo a cadeia recente.
 *
 * A cadeia só CRESCE quando o token consumido era a cabeça dela — ou seja,
 * quando é o mesmo fio. Qualquer outra coisa começa uma cadeia nova, e isso é o
 * que impede a linhagem de atravessar contas: sem a verificação, `A0 → A1`
 * seguido de `B0 → B1` deixaria `[A0, B0]` apontando para `B1`, e uma
 * requisição atrasada da conta A receberia a credencial da B.
 */
function anotarRotacao(consumido: string, sucessor: string): void {
  const mesmoFio = rotacoes?.cabeca === consumido;
  const consumidos = mesmoFio
    ? [...rotacoes!.consumidos, consumido]
    : [consumido];
  rotacoes = {
    consumidos: consumidos.slice(-TETO_HISTORICO),
    cabeca: sucessor,
  };
}

/** Esquece a linhagem. Chamado quando a sessão é substituída de fora. */
export function _esquecerRotacoes(): void {
  rotacoes = null;
}

/** `refresh` pertence à cadeia que termina no que está guardado agora? */
function daMesmaCadeia(refresh: string, guardado: string): boolean {
  return rotacoes?.cabeca === guardado && rotacoes.consumidos.includes(refresh);
}

async function renovar(refreshDeOrigem: string): Promise<Renovacao> {
  if (renovacaoEmVoo?.refresh === refreshDeOrigem) return renovacaoEmVoo.promessa;

  const promessa = (async (): Promise<Renovacao> => {
    let resposta: Response;
    try {
      const antes = await lerCredenciais();
      if (!antes || antes.refresh !== refreshDeOrigem) {
        // Esta MESMA sessão já foi renovada por um vizinho? Só então não há
        // erro. A conferência é pelos DOIS lados: o token de origem tem de ser
        // o que foi consumido, E o cofre tem de conter exatamente o sucessor
        // dele. Sem a segunda metade, uma troca de conta depois da rotação
        // devolveria a credencial da conta nova.
        if (antes && daMesmaCadeia(refreshDeOrigem, antes.refresh)) {
          return { ok: true, access: antes.access, refresh: antes.refresh };
        }
        return { ok: false, motivo: "sessao-trocou" };
      }

      resposta = await fetch(`${baseUrl()}/auth/refresh`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${refreshDeOrigem}`,
          [HEADER_CLIENTE]: CLIENTE,
          "Content-Type": "application/json",
        },
        credentials: "omit",
      });
    } catch {
      // ANTES da resposta. Falha de REDE não apaga a sessão: o token pode estar
      // perfeitamente vivo e o usuário só estar no elevador.
      //
      // ponytail: fica o caso ambíguo — a resposta pode ter se perdido DEPOIS
      // de o servidor rotacionar, e aí a renovação seguinte reapresenta um
      // token gasto, que o servidor trata como roubo. Decisão do dono: manter
      // assim e medir com app em produção. A mitigação certa é do lado do
      // servidor (janela de graça na rotação), não um sinalizador no cliente
      // que pode ficar preso e deslogar quem está bem.
      return { ok: false, motivo: "transitorio", status: 0 };
    }

    // ── Daqui para baixo o servidor JÁ RESPONDEU, e isso muda tudo. ─────────
    // Antes da resposta, falha é ambiguidade e preservar é o certo. Depois de
    // um 2xx, o token de origem está COMPROVADAMENTE consumido: qualquer erro
    // daqui — corpo truncado, esquema quebrado, keychain recusando — não pode
    // virar "tente de novo", porque a próxima tentativa reapresenta um token
    // gasto e o servidor revoga TUDO do usuário, em todos os aparelhos.
    //
    // Esta separação é o conserto da CLASSE. Havia um `try` só em volta de tudo
    // e ele colapsava os dois mundos em "transitório" — e a revisão foi achando
    // uma instância de cada vez: gravação, esquema, limpeza.

    // Só o 401 PROVA que a sessão acabou. Um 429 ou um 500 é incidente
    // passageiro do servidor, e tratá-lo como fim de sessão transformaria dois
    // minutos de instabilidade em logout de todo mundo.
    if (resposta.status === 401) {
      // Limpeza é o melhor esforço e NÃO muda o veredito: se o keychain
      // recusar, a sessão continua provadamente morta, e virar "erro
      // temporário" mostraria a tela errada guardando credencial inútil.
      await limparSe(refreshDeOrigem).catch(() => undefined);
      return { ok: false, motivo: "terminal" };
    }
    if (!resposta.ok) {
      return { ok: false, motivo: "transitorio", status: resposta.status };
    }

    try {
      const novas = credenciaisSchema.parse(await resposta.json());
      // Compara-e-troca. A conta pode ter trocado com a requisição no ar, e
      // conferir numa chamada para gravar na seguinte deixa exatamente a janela
      // em que a outra conta cabe — o resultado seria a sessão antiga
      // restaurada por cima da nova.
      const trocou = await trocarSe(refreshDeOrigem, {
        access: novas.access_token,
        refresh: novas.refresh_token,
      });
      if (!trocou) return { ok: false, motivo: "sessao-trocou" };
      anotarRotacao(refreshDeOrigem, novas.refresh_token);
      return {
        ok: true,
        access: novas.access_token,
        refresh: novas.refresh_token,
      };
    } catch {
      // Corpo truncado, esquema quebrado ou gravação recusada — tanto faz qual:
      // o token de origem está gasto e não pode continuar no cofre.
      await limparSe(refreshDeOrigem).catch(() => undefined);
      return { ok: false, motivo: "terminal" };
    }
  })();

  renovacaoEmVoo = { refresh: refreshDeOrigem, promessa };
  try {
    return await promessa;
  } finally {
    if (renovacaoEmVoo?.refresh === refreshDeOrigem) renovacaoEmVoo = null;
  }
}

/**
 * Tempo máximo de uma chamada de auth. Sem ele, um `fetch` que nunca responde
 * nem falha (Android sem timeout — mesmo caso real do `sair()` de
 * services/auth.ts) trava para sempre a fila de "ação por vez" do login: a
 * tentativa antiga nunca SE RESOLVE, então nenhuma nova consegue começar.
 */
export const TEMPO_LIMITE_AUTH_MS = 15_000;

/**
 * `AbortSignal` que aborta sozinho depois de `ms` — para passar em `sinal`.
 *
 * `controlador` é opcional: quem precisa abortar a requisição de FORA (uma
 * tentativa de entrada abandonada por "Voltar" — `services/auth.ts`,
 * `abandonarEntrada`) passa o próprio `AbortController` para guardar a
 * referência; sem uso externo, um novo é criado por chamada, como antes.
 */
export function comLimite(
  ms: number = TEMPO_LIMITE_AUTH_MS,
  controlador: AbortController = new AbortController(),
): AbortSignal {
  // `unref` (Node/Jest) tira o cronômetro da contagem que mantém o processo
  // vivo — a requisição normal resolve muito antes dos 15s, e sem isto cada
  // teste deixava um timer pendurado até o fim do prazo. Não existe em
  // Hermes (RN real); `?.()` não quebra lá, só não faz nada.
  const cronometro = setTimeout(() => controlador.abort(), ms) as unknown as { unref?: () => void };
  cronometro.unref?.();
  return controlador.signal;
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
  /**
   * Não renova em 401, e não trata o 401 como fim de sessão.
   *
   * Existe para as rotas que usam 401 para uma credencial SECUNDÁRIA — a senha
   * numa configuração de dois fatores, por exemplo. Ali o 401 quer dizer "esse
   * dado está errado", não "sua sessão acabou", e renovar não resolve nada:
   * mandaria o usuário para a tela de entrada por ter digitado a senha errada
   * num formulário que já estava autenticado.
   *
   * Nenhuma rota da Fase 1 é assim; o sinalizador existe para que a primeira
   * que for tenha um caminho certo em vez de descobrir o problema em produção.
   */
  semRenovar?: boolean;
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
 * O corpo de uma chamada: manda, renova uma vez em 401, valida a forma da
 * resposta. Não faz a conferência de dono — isso é do invólucro `chamar`.
 */
async function executar<T>(
  rota: string,
  schema: z.ZodType<T>,
  opcoes: Opcoes,
  guardadas: { access: string; refresh: string } | null,
): Promise<T> {
  let resposta = await enviar(rota, opcoes, guardadas?.access ?? null);

  if (
    resposta.status === 401 &&
    !opcoes.semAuth &&
    !opcoes.credencial &&
    !opcoes.semRenovar
  ) {
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
      // Melhor esforço, como as outras duas limpezas: uma falha do keychain
      // não pode engolir o `SessaoExpirada` e deixar a tela sem veredito.
      await limparSe(renovada.refresh).catch(() => undefined);
      throw new SessaoExpirada();
    }
  }

  if (!resposta.ok) {
    // O corpo é lido UMA vez: num `Response` real a segunda leitura rejeita, e
    // o `code` do corpo (ex.: `mfa_code_invalid`) sumiria. 5xx não é lido
    // (ver `mensagemDeErro`).
    const corpo = resposta.status >= 500 ? undefined : await resposta.json().catch(() => undefined);
    throw new ErroDeApi(resposta.status, mensagemDeErro(resposta.status, corpo), corpo);
  }

  const bruto = await resposta.json().catch(() => null);
  const conferido = schema.safeParse(bruto);
  if (!conferido.success) throw new ContratoInvalido(rota, conferido.error);
  return conferido.data;
}

/**
 * Uma chamada à API: `executar` por dentro, com UMA conferência de dono no
 * fim — depois da última leitura do corpo, e não antes do status.
 *
 * A pergunta é: a sessão que fez o pedido ainda é a que está no cofre? Vale
 * para os DOIS desfechos de `executar`. Entregar dado da conta A depois de a
 * conta B assumir renderiza saldo, transação e nome de outra pessoa na tela da
 * conta nova — num app financeiro isso é vazamento entre contas, mesmo sendo o
 * próprio aparelho. E entregar o ERRO da conta A não é melhor: a pessoa veria
 * "não foi possível" sobre uma operação que ela não pediu nesta sessão.
 *
 * A conferência fica DEPOIS de `executar` retornar ou lançar, porque é aí que
 * a última leitura do corpo já aconteceu — e dali até o `return`/`throw` não
 * sobra nenhum `await` em que a conta pudesse trocar de novo sem ser vista.
 *
 * E ela é pela CONDIÇÃO DO COFRE, não pelo `motivo` de `SessaoExpirada`: o
 * mesmo erro quer dizer coisas diferentes dependendo de quem está lá agora. Com
 * o cofre vazio, a conta que pediu saiu e ninguém entrou — `SessaoExpirada`
 * fica, porque ali o login é a tela certa. Com outra linhagem no cofre, uma
 * conta nova assumiu — o erro é `RequisicaoSuperada`, não fim de sessão dela.
 *
 * A conferência é para requisição AUTENTICADA: rota pública não tem sessão a
 * trair. E credencial fixa (logout) também não, porque ali o fim da sessão é
 * o objetivo.
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
  // rota pública não tem sessão a trair; credencial fixa (logout) quer o fim dela
  if (!guardadas || opcoes.credencial) {
    return executar(rota, schema, opcoes, guardadas);
  }
  let valor: T;
  try {
    valor = await executar(rota, schema, opcoes, guardadas);
  } catch (e) {
    // Falha ao LER o cofre aqui (keychain recusou): só relança `e` se ele for
    // `SessaoExpirada` — esse erro não carrega nada da conta A, é o motivo
    // real e é o próprio caso que essa checagem existe para cobrir. Qualquer
    // outro `e` pode carregar corpo/status da A, então aqui é fail-closed: a
    // falha de leitura, o comportamento de antes desta checagem existir.
    let trocouDeConta: boolean;
    try {
      trocouDeConta = await superada(guardadas.refresh, e instanceof SessaoExpirada);
    } catch (falhaLeitura) {
      if (e instanceof SessaoExpirada) throw e;
      throw falhaLeitura;
    }
    if (trocouDeConta) throw new RequisicaoSuperada();
    throw e;
  }
  if (await superada(guardadas.refresh, false)) throw new RequisicaoSuperada();
  return valor;
}

/** O cofre não é mais desta linhagem? No fim de sessão, cofre vazio é o próprio fim, não troca. */
async function superada(refresh: string, fimDeSessao: boolean): Promise<boolean> {
  const agora = await lerCredenciais();
  if (!agora) return !fimDeSessao;
  return agora.refresh !== refresh && !daMesmaCadeia(refresh, agora.refresh);
}

/**
 * Mensagem para o usuário, nunca o texto cru do servidor.
 *
 * O backend devolve `{"detail": ...}` em vários formatos — string, objeto com
 * `error`/`message`, lista de erros do Pydantic. Mostrar o JSON na tela é um
 * defeito que o produto já viveu no site (o modal que exibia `{"detail":...}`).
 */
function mensagemDeErro(status: number, corpo: unknown): string {
  // 5xx ANTES de olhar o corpo, e não depois: em erro de servidor o `detail`
  // carrega a exceção crua (`psycopg.OperationalError`, um traceback), e a
  // ordem inversa entregava isso à tela do usuário. Foi o teste de 500 que
  // pegou — a versão anterior confiava no `detail` primeiro.
  if (status >= 500) {
    return "Tivemos um problema aqui. Tente de novo em instantes.";
  }
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
  rotacoes = null;
}
