import { z } from "zod";

import { ErroDeApi, TEMPO_LIMITE_AUTH_MS, _esquecerRotacoes, chamar, comLimite } from "../api/client";
import {
  loginSchema,
  perfilSchema,
  respostaLoginSchema,
  type Perfil,
} from "../api/schemas/auth";
import {
  FalhaNoCofre,
  guardarCredenciaisSe,
  lerCredenciais,
  limparSessaoDe,
} from "../storage/secure";

/**
 * O resultado de uma tentativa de entrada.
 *
 * Conta com dois fatores NÃO devolve credencial no primeiro passo: devolve um
 * desafio. Tratar as duas respostas com um esquema só transformava o login
 * legítimo dessas contas em erro de contrato — e a pessoa via "resposta
 * inesperada" em vez da tela de código.
 */
/**
 * Ordem de INÍCIO das tentativas de entrada, não de término.
 *
 * Duas entradas podem se sobrepor: alguém erra a conta, corrige e envia de novo
 * antes de a primeira responder. Sem senha de chegada, o resultado aplicado é o
 * de quem TERMINA por último — e a pessoa acaba logada na conta que ela já tinha
 * abandonado, com a tela mostrando a outra. Com a senha, a tentativa velha
 * descobre que foi superada e descarta o próprio resultado.
 */
let ultimaTentativa = 0;

/** Esta entrada foi superada por outra mais nova; o resultado dela é descartado. */
export class EntradaSuperada extends ErroDeApi {
  constructor() {
    super(409, "Outra tentativa de entrada assumiu.");
    this.name = "EntradaSuperada";
  }
}

export type Entrada =
  | { fase: "pronta"; perfil: Perfil }
  | { fase: "mfa"; desafio: string; email: string };

/**
 * O `AbortController` da tentativa de entrada EM VOO agora, se houver.
 * `abandonarEntrada` usa isto para não deixar a requisição pendurada até o
 * tempo limite de 15s quando a pessoa desiste (Voltar) antes disso.
 */
let controladorEmVoo: AbortController | null = null;

/**
 * Envolve uma tentativa de entrada INTEIRA — requisição, decisão e gravação.
 *
 * Cobre os dois desfechos, e isso é o ponto. O erro de uma tentativa superada
 * também é ruído: a conta A responde 401 e a pessoa veria "senha incorreta"
 * enquanto a entrada da conta B, que ela pediu depois, estava indo bem.
 * Descartar o sucesso e mostrar a falha seria escolher o pior dos dois.
 *
 * E o invólucro vai até o FIM, não só até a resposta do servidor: uma falha de
 * gravação também precisa ser lida à luz de quem chegou depois. Manter a
 * conferência espalhada por cada ponto de saída foi exatamente como o ramo do
 * desafio ficou de fora antes, nesta mesma revisão — com um lugar só, não há
 * saída para esquecer.
 *
 * `executar` recebe o `sinal` desta tentativa (não chama `comLimite()`
 * sozinho): é o mesmo `AbortSignal` que `controladorEmVoo` referencia, para
 * que `abandonarEntrada()` consiga abortar a requisição de fora.
 */
async function tentativa<T>(
  executar: (vez: number, sinal: AbortSignal) => Promise<T>,
): Promise<T> {
  const minhaVez = ++ultimaTentativa;
  const controlador = new AbortController();
  controladorEmVoo = controlador;
  try {
    const r = await executar(minhaVez, comLimite(TEMPO_LIMITE_AUTH_MS, controlador));
    if (minhaVez !== ultimaTentativa) throw new EntradaSuperada();
    return r;
  } catch (e) {
    // `FalhaNoCofre` NUNCA vira `EntradaSuperada`. Ela diz que o estado da
    // sessão no aparelho ficou desconhecido, e traduzi-la em "outra tentativa
    // assumiu" trocaria um problema que precisa aparecer por uma corrida
    // silenciosa — a pessoa veria a tela seguir normalmente com o cofre
    // possivelmente guardando a sessão errada.
    if (e instanceof FalhaNoCofre) throw e;
    if (minhaVez !== ultimaTentativa) throw new EntradaSuperada();
    throw e;
  } finally {
    if (controladorEmVoo === controlador) controladorEmVoo = null;
  }
}

/**
 * Abandona a tentativa de entrada em voo (chamada por "Voltar" na tela de
 * MFA — `features/auth/entrar.ts`): avança `ultimaTentativa`, então a
 * resposta atrasada dela (se chegar) vira `EntradaSuperada` — sem gravar
 * credencial (o `guardarCredenciaisSe` de `entrar`/`verificarMfa` já cobre
 * essa corrida) — e aborta a requisição em voo, para ela não ficar pendurada
 * até os 15s de `comLimite`.
 *
 * Sem tentativa em voo, é inofensivo: só avança o contador (o que o próximo
 * login/verify já faria sozinho) e não há controlador para abortar.
 */
export function abandonarEntrada(): void {
  ultimaTentativa += 1;
  controladorEmVoo?.abort();
}

export async function entrar(email: string, senha: string): Promise<Entrada> {
  return tentativa(async (minhaVez, sinal): Promise<Entrada> => {
    const r = await chamar("/auth/login", respostaLoginSchema, {
      metodo: "POST",
      corpo: { email, password: senha },
      semAuth: true,
      sinal,
    });
    if ("mfa_required" in r) {
      return { fase: "mfa", desafio: r.mfa_challenge, email: r.email };
    }
    return { fase: "pronta", perfil: await gravarSessao(minhaVez, r) };
  });
}

/**
 * O final comum de quem recebe credencial (`entrar`, `verificarMfa`,
 * `confirmarCadastro`), sempre DENTRO de `tentativa()`. A conferência acontece
 * DENTRO da gravação, não antes: entre um passo e o outro caberia uma entrada
 * mais nova, e o aparelho ficaria logado nesta enquanto a tela mostra a outra.
 */
async function gravarSessao(minhaVez: number, r: z.infer<typeof loginSchema>): Promise<Perfil> {
  const gravou = await guardarCredenciaisSe(
    () => minhaVez === ultimaTentativa,
    { access: r.access_token, refresh: r.refresh_token },
  );
  if (!gravou) throw new EntradaSuperada();
  _esquecerRotacoes();
  return { user_id: r.user_id, email: r.email, plan: r.plan };
}

/** Completa a entrada de quem tem dois fatores. `backup` usa código de reserva. */
export async function verificarMfa(
  desafio: string,
  codigo: string,
  backup = false,
): Promise<Perfil> {
  return tentativa(async (minhaVez, sinal) => {
    const r = await chamar("/auth/mfa/verify-login", loginSchema, {
      metodo: "POST",
      corpo: { challenge: desafio, code: codigo, use_backup: backup },
      semAuth: true,
      sinal,
    });
    return gravarSessao(minhaVez, r);
  });
}

/**
 * Pede o código de confirmação do cadastro. Não grava credencial, então fica
 * FORA de `tentativa()` — mesmo desenho do Esqueci a senha. Objeto e não
 * posicional: são quatro strings, e trocar duas delas passaria no TS.
 */
export async function cadastrar(dados: { email: string; senha: string; nome: string; telefone: string }): Promise<void> {
  await chamar("/auth/register", z.unknown(), {
    metodo: "POST",
    corpo: { email: dados.email, password: dados.senha, name: dados.nome, phone: dados.telefone },
    semAuth: true,
    sinal: comLimite(),
  });
}

/**
 * Confirma o código e recebe a sessão da conta nova. Passa por `tentativa()`
 * como o login e o MFA: um `entrar()` de outra conta começado depois vence,
 * e este resultado vira `EntradaSuperada` sem tocar no cofre.
 */
export async function confirmarCadastro(email: string, codigo: string): Promise<Perfil> {
  return tentativa(async (minhaVez, sinal) => {
    const r = await chamar("/auth/verify-email", loginSchema, {
      metodo: "POST",
      corpo: { email, code: codigo },
      semAuth: true,
      sinal,
    });
    return gravarSessao(minhaVez, r);
  });
}

export async function perfil(): Promise<Perfil> {
  return chamar("/auth/me", perfilSchema, { sinal: comLimite() });
}

export async function temSessao(): Promise<boolean> {
  return (await lerCredenciais()) !== null;
}

/**
 * Sair: apaga o que está no aparelho e SÓ ENTÃO avisa o servidor, esperando a
 * resposta dele por no máximo `TEMPO_LIMITE_AUTH_MS`.
 *
 * A limpeza vem PRIMEIRO e não depende da rede (#433): um logout feito no
 * metrô, com o fetch do Android sem timeout, podia ficar pendurado para sempre
 * — e com a limpeza depois da resposta, a credencial continuava no keychain
 * enquanto isso, e o próximo a abrir o app entraria na conta de quem achou que
 * tinha saído.
 *
 * A revogação é esperada (#458), com tempo limite: disparada e esquecida, a
 * tela ia para Entrar na hora, e o app podia ir para o fundo ou ser fechado
 * com a requisição ainda no ar — a sessão ficava viva no servidor sem ninguém
 * saber. Esperar mantém a tela em
 * "carregando" até a resposta ou o tempo limite, com o cofre já vazio. A falha
 * da revogação (401/403, 5xx/429, rede fora, tempo limite) é engolida: não há
 * nada que a pessoa possa fazer com ela, e o aparelho já saiu.
 *
 * Resíduo aceito (decisão do dono): se o app morre ou é suspenso durante a
 * espera, ou se o servidor devolve 5xx/429 ou a rede está fora, a sessão pode
 * continuar viva no servidor até o refresh expirar (14 dias). Não há
 * retentativa. O inverso também: se o cofre recusa apagar, o `finally` pede a
 * revogação mesmo assim, e a credencial, morta se ela vingar, fica no cofre.
 * O provider segue autenticado, com o erro na tela; tocar Sair de novo refaz
 * a saída.
 *
 * A requisição fala pela sessão que INICIOU a saída, e a limpeza identifica
 * essa sessão pelo `jti`, não pelo refresh token. Os dois detalhes vêm do mesmo
 * lugar: entre a captura e o fim há tempo de rede, e nesse tempo ou outra conta
 * entra (e não pode ser deslogada por engano) ou esta mesma sessão é renovada
 * por outra tela (e não pode escapar da saída com um refresh novo).
 */
export async function sair(): Promise<void> {
  // Toda entrada em voo fica inválida: sair é a intenção mais recente, e um
  // login que terminasse depois gravaria credencial numa sessão que o usuário
  // acabou de encerrar.
  ultimaTentativa += 1;
  const daSaida = await lerCredenciais();
  // Sem sessão capturada não há logout a fazer, e TENTAR é pior que não fazer:
  // a requisição releria o cofre e poderia sair autenticada por uma conta que
  // entrou depois. Saída duplicada ou tardia cai exatamente aqui.
  if (!daSaida) return;
  try {
    // Esquece a linhagem SÓ se a sessão apagada era mesmo a desta saída. Se
    // outra conta assumiu o cofre no meio, a linhagem já é dela, e apagá-la
    // faria as renovações em voo daquela conta virarem fim de sessão.
    //
    // ponytail: esta guarda NÃO tem teste que a discrimine, e a ausência é
    // declarada em vez de escondida. Para alcançá-la, a outra conta precisa
    // entrar E rotacionar dentro da janela da limpeza do cofre — entrar
    // sozinho já zera a linhagem, então só a rotação seguinte a recria. Montar
    // isso exigiria reentrância no dublê de rede, e o teste ficaria medindo o
    // andaime. A guarda fica porque é correta e custa uma condição; quem for
    // mexer aqui não deve confiar em vermelho para perceber que a quebrou.
    if (await limparSessaoDe(daSaida.access, daSaida.refresh)) {
      _esquecerRotacoes();
    }
  } finally {
    // Esperada, mas com tempo limite: um `fetch` pendurado não prende a tela
    // além de `TEMPO_LIMITE_AUTH_MS`. O `.catch` engole a falha da revogação —
    // o cofre já está limpo, e `sair()` só rejeita por falha do cofre.
    await chamar("/auth/logout", perfilSchema.partial(), {
      metodo: "POST",
      sinal: comLimite(),
      // O REFRESH token como credencial, não o access. O servidor revoga a
      // sessão por qualquer um dos dois, mas o access pode estar expirado — e é
      // o caso mais comum de todos, um app parado por mais de quinze minutos.
      // Com um access vencido ele não decodifica nada e não revoga nada, e o
      // logout voltaria 200 sem ter encerrado sessão nenhuma. O refresh dura
      // catorze dias e resolve a sessão sozinho.
      credencial: { access: daSaida.refresh, refresh: daSaida.refresh },
    }).catch(() => undefined);
  }
}
