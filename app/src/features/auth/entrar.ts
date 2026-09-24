import { ContratoInvalido, ErroDeApi } from "@/api/client";
import { abandonarEntrada, entrar as entrarNoServidor, verificarMfa, EntradaSuperada } from "@/services/auth";
import { FalhaNoCofre } from "@/storage/secure";

/**
 * A máquina de estados da tela de Entrar, sem JSX e sem `react-native` — mesmo
 * desenho de `src/ui/inicio.ts` (Fase 1), que este módulo substitui: o Jest
 * exercita a lógica com os serviços reais, e a tela só desenha o estado.
 *
 * `modo` do desafio MFA nunca some sozinho: só troca via `alternarModo`.
 */
export type EstadoEntrar =
  | { fase: "formulario"; aviso?: string }
  | { fase: "enviando" }
  | { fase: "mfa"; desafio: string; email: string; modo: "totp" | "backup"; aviso?: string }
  | { fase: "verificando"; desafio: string; email: string; modo: "totp" | "backup" }
  | { fase: "erro-cofre" };

export type EstadoMfa = Extract<EstadoEntrar, { fase: "mfa" }>;
export type EstadoVerificando = Extract<EstadoEntrar, { fase: "verificando" }>;

const GENERICO = "Algo deu errado. Tente de novo.";

/** Mensagem da fase X (cofre do aparelho recusou ler ou gravar). */
export const MENSAGEM_ERRO_COFRE = "Não conseguimos abrir sua sessão neste aparelho. Tente de novo.";

/** Texto para a pessoa: nunca `TypeError`, rota ou JSON cru. Movido de `inicio.ts` (CLAUDE.md §0.1). */
export function textoDaFalha(e: unknown): string {
  // Antes do `ErroDeApi`, de quem é subclasse: o detalhe dele cita a rota.
  if (e instanceof ContratoInvalido) return GENERICO;
  if (e instanceof ErroDeApi) return e.detalhe.trim() || GENERICO;
  return GENERICO;
}

/**
 * Ação por vez, mesma guarda de `src/ui/inicio.ts` (que este módulo
 * substitui): um toque com ação em andamento (enviando ou verificando) é
 * ignorado. Sem MONTAGEM esperando na fila — o `SessaoProvider` monta uma vez
 * na raiz do app, então não há "tela remontada com ação em voo" para segurar
 * aqui; só o toque duplo continua precisando de guarda.
 *
 * `acao` pode devolver `null` (EntradaSuperada): "nada" na tabela de
 * estados×eventos — outra tentativa mais nova já decidiu o resultado, e
 * reaplicar aqui pisaria em cima dela.
 *
 * `emVoo` é a geração da ação em voo, ou `null` com a fila ociosa. Substitui
 * um contador (`pendentes`): como só cabe UMA ação por vez — a guarda de
 * `tocar()` logo abaixo impede a segunda —, a geração sozinha já identifica
 * qual ação está rodando, e é o que permite `abandonar()` (chamado por
 * `voltar()`) liberar A FILA JÁ, sem esperar essa ação terminar: sem isto,
 * "Voltar" só trocava a fase local, e a verificação abandonada continuava
 * contando como "em voo" até a própria requisição responder — um toque
 * legítimo logo depois (outra conta) era engolido em silêncio, com a tela
 * presa em "enviando"/"verificando" sem nenhuma requisição no ar.
 */
let emVoo: number | null = null;
let fila: Promise<void> = Promise.resolve();

/**
 * A "origem" corrente. `voltar()` e `alternarModo()` a abandonam (mudam a
 * fase por fora da fila, sem passar por `tocar()`) incrementando este
 * contador — é o "id da tentativa" da tabela de estados: uma ação que já
 * estava em voo quando a origem foi abandonada tem a SUA geração antiga, e o
 * resultado dela deixa de valer para `aplicar`, mesmo chegando depois (M/V
 * atrasado por 429/5xx não ressuscita o desafio depois de um Voltar).
 *
 * `autenticar()`, dentro de `acao()`, NÃO passa por aqui: a gravação da
 * credencial já aconteceu antes deste retorno (ver `enviar`/`verificar`
 * abaixo), e é o `ultimaTentativa` de `services/auth.ts` — não este contador
 * — quem decide se ELA vale, comparando com a tentativa de login mais nova.
 */
let geracao = 0;

function enfileirar(
  minhaGeracao: number,
  acao: () => Promise<EstadoEntrar | null>,
  aplicar: (e: EstadoEntrar) => void,
): Promise<void> {
  emVoo = minhaGeracao;
  // `acao()` roda NA HORA, não dentro de uma continuação (`fila.then(async
  // () => acao())`): como só cabe UMA ação em voo por vez, não há fila de
  // verdade para esperar, e adiar a chamada abria uma janela real — "Voltar"
  // no MESMO lote do toque (I-A/I-B) rodava ANTES desta continuação, e o
  // `AbortController` que `services/auth.ts` cria só existe depois que a
  // cadeia síncrona de `acao()` chega até o `fetch()`. Chamando na hora, essa
  // cadeia (verificar → verificarMfa → tentativa → cria o controlador →
  // chama a rede) já rodou quando `abandonarEntrada()` tenta abortar.
  fila = acao().then((proximo) => {
    // Só libera SE ainda for esta ação: `abandonar()` já pode ter zerado
    // `emVoo` (ou uma ação mais nova já pode estar rodando) antes desta
    // promise terminar — não pisa em cima de nenhum dos dois casos.
    if (emVoo === minhaGeracao) emVoo = null;
    if (proximo && geracao === minhaGeracao) aplicar(proximo);
  });
  return fila;
}

/** Botão/auto-envio. Ignorado enquanto houver ação em andamento — é o que garante UMA requisição só quando o auto-envio do 6º dígito e o toque em "Verificar" coincidem. */
export function tocar(
  acao: () => Promise<EstadoEntrar | null>,
  aplicar: (e: EstadoEntrar) => void,
): Promise<void> {
  return emVoo !== null ? fila : enfileirar(geracao, acao, aplicar);
}

/** Só para teste: zera a fila entre casos, como o `_resetTela` que este módulo substitui. */
export function _resetEntrar(): void {
  emVoo = null;
  fila = Promise.resolve();
  geracao = 0;
}

/**
 * F/E → resultado. `autenticar` é chamado assim que a credencial é gravada,
 * INDEPENDENTE do `aplicar` chegar a rodar depois: a gravação já aconteceu
 * dentro de `entrarNoServidor` (services/auth.ts) antes deste retorno, e o
 * `autenticar()` só espelha isso no `SessaoProvider` — mesmo que a tela tenha
 * saído do ar no meio da espera (o `Stack.Protected` troca de rota sozinho
 * quando a sessão vira "autenticado", e a promise desta função não é
 * cancelada pelo desmonte).
 */
export async function enviar(
  email: string,
  senha: string,
  autenticar: () => void,
): Promise<EstadoEntrar | null> {
  try {
    const r = await entrarNoServidor(email.trim(), senha);
    if (r.fase === "mfa") return { fase: "mfa", desafio: r.desafio, email: r.email, modo: "totp" };
    autenticar();
    // Não chega a renderizar: o guard do `_layout` troca a tela assim que
    // `autenticar()` roda, antes do próximo commit desta.
    return { fase: "enviando" };
  } catch (e) {
    // Outra tentativa mais nova já decidiu (ver comentário de `enfileirar`).
    if (e instanceof EntradaSuperada) return null;
    if (e instanceof FalhaNoCofre) return { fase: "erro-cofre" };
    return { fase: "formulario", aviso: textoDaFalha(e) };
  }
}

/**
 * M/V → resultado. O desafio MFA que chega DEPOIS de a tela ter sido
 * abandonada (Voltar, ou navegação para longe) é descartado: `voltar()` já
 * trocou a fase para "formulario" antes de qualquer resposta desta chegar, e
 * quem chama `aplicar` não tem mais nada em `M`/`V` para atualizar. O
 * resultado positivo (autenticação) segue o mesmo raciocínio de `enviar`.
 *
 * A tabela da falha (`frontend/finance_bot_websocket_custom.py`,
 * `auth_mfa_verify_login`, e `db/mfa.py`). O desafio aceita 5 tentativas:
 * `reserve_login_challenge_attempt` gasta uma ANTES de conferir o código, e o
 * código errado com tentativas sobrando deixa o desafio vivo.
 *
 * | falha                                              | desafio   | resultado |
 * |----------------------------------------------------|-----------|-----------|
 * | 400 `mfa_code_invalid` (código errado, sobram tentativas) | vivo | M     |
 * | 400 `mfa_challenge_expired` (expirado, 5ª tentativa, corrida) | morto | F |
 * | 400 sem `code` ou com `code` desconhecido          | —         | F         |
 * | 404 (usuário sumiu)                                | consumido | F         |
 * | 5xx                                                | ambíguo   | F         |
 * | tempo limite (abort do comLimite, 15s)             | ambíguo   | F         |
 * | 429 por IP (antes da reserva)                      | vivo      | M         |
 * | 429 por conta (depois da reserva: a tentativa conta) | vivo, salvo se era a 5ª | M |
 * | erro de rede sem resposta (fetch rejeita antes de chegar) | ambíguo | M  |
 *
 * 5xx e tempo limite vão ao formulário porque o caso é ambíguo: voltar à
 * senha custa redigitá-la e nunca deixa a pessoa presa num desafio morto.
 */
export async function verificar(
  desafio: string,
  email: string,
  modo: "totp" | "backup",
  codigoDigitado: string,
  autenticar: () => void,
): Promise<EstadoEntrar | null> {
  // O servidor só apara as PONTAS; um código colado ("123 456") ou digitado
  // com espaço no meio nunca bateria sem isto.
  const codigo = codigoDigitado.replace(/\s+/g, "");
  try {
    await verificarMfa(desafio, codigo, modo === "backup");
    autenticar();
    return { fase: "verificando", desafio, email, modo };
  } catch (e) {
    if (e instanceof EntradaSuperada) return null;
    if (e instanceof FalhaNoCofre) return { fase: "erro-cofre" };
    // Contrato quebrado (200 de forma errada): o desafio já foi consumido do
    // lado do servidor mesmo num 200 malformado, então insistir em `M` só
    // repetiria o mesmo contrato quebrado. Volta ao formulário.
    if (e instanceof ContratoInvalido) return { fase: "formulario", aviso: GENERICO };
    if (e instanceof ErroDeApi) {
      // Código errado com tentativas sobrando: o desafio continua vivo, a
      // pessoa tenta outro código sem redigitar a senha. O `code` vem no topo
      // do corpo, ao lado do `detail`.
      const codigoDoErro = (e.corpo as { code?: unknown } | null | undefined)?.code;
      if (e.status === 400 && codigoDoErro === "mfa_code_invalid") {
        return { fase: "mfa", desafio, email, modo, aviso: e.detalhe.trim() || GENERICO };
      }
      // Qualquer outro 400 (`mfa_challenge_expired`, ou sem `code`) e 404: o
      // desafio morreu (ou o usuário sumiu); só um login novo dá um desafio
      // vivo. 5xx: ambíguo — ver a tabela acima.
      if (e.status === 400 || e.status === 404 || e.status >= 500) {
        const base = e.detalhe.trim() || GENERICO;
        return { fase: "formulario", aviso: `${base} Entre de novo.` };
      }
      // 429 e qualquer outro 4xx: o desafio continua vivo, a pessoa pode
      // tentar de novo sem refazer o login.
      return { fase: "mfa", desafio, email, modo, aviso: e.detalhe.trim() || GENERICO };
    }
    // Tempo limite do `comLimite` (15s): a requisição provavelmente CHEGOU ao
    // servidor, e o que ela fez com o desafio é ambíguo — mesmo raciocínio do
    // 5xx (ver a tabela acima). Qualquer outra rejeição sem resposta (rede
    // fora antes de sair do aparelho) fica em M.
    if (typeof e === "object" && e !== null && "name" in e && e.name === "AbortError") {
      return { fase: "formulario", aviso: `${GENERICO} Entre de novo.` };
    }
    // Rede fora, sem resposta e sem abort: ambíguo, o desafio pode estar
    // vivo — tenta de novo aqui mesmo.
    return { fase: "mfa", desafio, email, modo, aviso: GENERICO };
  }
}

/**
 * M → M: troca o modo, `aviso` e o campo (o campo é responsabilidade do
 * componente, que observa `modo`). O botão que chama isto fica desativado
 * enquanto uma verificação está em voo, então na prática não há o que
 * abandonar aqui — mas a geração sobe do mesmo jeito: defesa contra qualquer
 * chamada que escape dessa guarda (ex.: um toque que já estava em trânsito
 * quando o botão desativou).
 */
export function alternarModo(estado: EstadoMfa): EstadoEntrar {
  geracao += 1;
  return {
    fase: "mfa",
    desafio: estado.desafio,
    email: estado.email,
    modo: estado.modo === "totp" ? "backup" : "totp",
  };
}

/**
 * M → F: descarta o desafio localmente. Sem aviso; o e-mail do formulário não
 * é tocado por este módulo (é estado da tela).
 *
 * Sobe a geração: uma verificação que ainda estava em voo quando a pessoa
 * voltou (429/5xx demora a responder) é da geração ANTERIOR — o resultado
 * dela chega depois e `enfileirar` descarta, em vez de ressuscitar a tela de
 * código com o desafio que a pessoa já abandonou.
 *
 * E abandona a ação em voo (se houver): libera `emVoo` JÁ — não espera a
 * verificação responder — e chama `abandonarEntrada()` (services/auth.ts),
 * que avança `ultimaTentativa` (a resposta atrasada vira `EntradaSuperada`,
 * sem gravar credencial) e aborta a requisição em rede. Sem isto, um toque
 * legítimo logo após "Voltar" (outra conta) era engolido pela guarda de
 * `tocar()` até a verificação abandonada terminar sozinha — na prática, até
 * o tempo limite de 15s (I-A/I-B).
 */
export function voltar(): EstadoEntrar {
  geracao += 1;
  emVoo = null;
  abandonarEntrada();
  return { fase: "formulario" };
}

/** X → F: só a chance de tentar de novo pelo formulário; nenhuma operação é refeita sozinha. */
export function tentarDeNovo(): EstadoEntrar {
  return { fase: "formulario" };
}
