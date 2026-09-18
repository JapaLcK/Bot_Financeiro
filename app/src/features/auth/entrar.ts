import { ContratoInvalido, ErroDeApi } from "@/api/client";
import { entrar as entrarNoServidor, verificarMfa, EntradaSuperada } from "@/services/auth";
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
 */
let pendentes = 0;
let fila: Promise<void> = Promise.resolve();

function enfileirar(
  acao: () => Promise<EstadoEntrar | null>,
  aplicar: (e: EstadoEntrar) => void,
): Promise<void> {
  pendentes += 1;
  fila = fila.then(async () => {
    const proximo = await acao();
    pendentes -= 1;
    if (proximo) aplicar(proximo);
  });
  return fila;
}

/** Botão/auto-envio. Ignorado enquanto houver ação em andamento — é o que garante UMA requisição só quando o auto-envio do 6º dígito e o toque em "Verificar" coincidem. */
export function tocar(
  acao: () => Promise<EstadoEntrar | null>,
  aplicar: (e: EstadoEntrar) => void,
): Promise<void> {
  return pendentes > 0 ? fila : enfileirar(acao, aplicar);
}

/** Só para teste: zera a fila entre casos, como o `_resetTela` que este módulo substitui. */
export function _resetEntrar(): void {
  pendentes = 0;
  fila = Promise.resolve();
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
      // 400/404: o desafio MORREU no servidor (challenge expirado, ou
      // consumido por uma tentativa anterior — db/mfa.py:351 consome antes de
      // conferir o código, então mesmo um código ERRADO já queima o desafio).
      // Insistir em `M` reapresentaria um desafio morto; só o formulário, com
      // um login novo, dá um desafio vivo.
      if (e.status === 400 || e.status === 404) {
        const base = e.detalhe.trim() || GENERICO;
        return { fase: "formulario", aviso: `${base} Entre de novo.` };
      }
      // 429 e qualquer outro 4xx: o desafio continua vivo, a pessoa pode
      // tentar de novo sem refazer o login.
      return { fase: "mfa", desafio, email, modo, aviso: e.detalhe.trim() || GENERICO };
    }
    // Rede fora: mesma lógica — o desafio não foi tocado, tenta de novo aqui mesmo.
    return { fase: "mfa", desafio, email, modo, aviso: GENERICO };
  }
}

/** M → M: troca o modo, `aviso` e o campo (o campo é responsabilidade do componente, que observa `modo`). */
export function alternarModo(estado: EstadoMfa): EstadoEntrar {
  return {
    fase: "mfa",
    desafio: estado.desafio,
    email: estado.email,
    modo: estado.modo === "totp" ? "backup" : "totp",
  };
}

/** M → F: descarta o desafio localmente. Sem aviso; o e-mail do formulário não é tocado por este módulo (é estado da tela). */
export function voltar(): EstadoEntrar {
  return { fase: "formulario" };
}

/** X → F: só a chance de tentar de novo pelo formulário; nenhuma operação é refeita sozinha. */
export function tentarDeNovo(): EstadoEntrar {
  return { fase: "formulario" };
}
