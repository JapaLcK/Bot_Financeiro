/**
 * Dinheiro é sempre `centavos` (inteiro). Nenhuma função deste arquivo passa
 * por `Number()`, `parseFloat()`, `\d` ou `Intl` — Node e Hermes divergem no
 * separador (NBSP vs espaço comum) e no arredondamento de float, e um app que
 * mexe com saldo não pode herdar essa divergência.
 */

/** Maior valor de `AmountInput`: R$ 99.999.999,99 — 10 dígitos de centavos, todos 9. */
export const TETO_CENTAVOS = 9_999_999_999;

export interface Partes {
  negativo: boolean;
  /** Parte inteira COM separador de milhar (".") — ex. "1.234.567", "0". */
  inteiro: string;
  /** Sempre 2 dígitos — ex. "89", "05". */
  centavos: string;
}

/**
 * Quebra o módulo (sem sinal) em dígitos de reais e dois dígitos de centavos,
 * sem qualquer separador — usado por `partes` (que aplica o milhar) e por
 * `falado` (que não usa milhar nenhum). `padStart(3, "0")` garante pelo menos
 * "0XX": sem ele, `String(5)` daria "5" e não haveria dígito de centavo.
 */
function decompor(modulo: number): { reais: string; centavos: string } {
  const digitos = String(modulo).padStart(3, "0");
  return { reais: digitos.slice(0, -2), centavos: digitos.slice(-2) };
}

/** Agrupa de 3 em 3 a partir da direita. Só string, nunca `Intl.NumberFormat`. */
function comMilhar(digitosSemSinal: string): string {
  const grupos: string[] = [];
  let resto = digitosSemSinal;
  while (resto.length > 3) {
    grupos.unshift(resto.slice(-3));
    resto = resto.slice(0, -3);
  }
  grupos.unshift(resto);
  return grupos.join(".");
}

/**
 * `null` para tudo que não é um inteiro seguro (NaN, ±Infinity, 1.5, 2**53,
 * 1e21) — formatar qualquer um desses é mentir sobre o valor. SEM teto: um
 * saldo agregado pode passar de R$ 99.999.999,99 mesmo o `AmountInput` não
 * aceitando digitar isso. `-0` sai com `negativo: false` (o JS já resolve:
 * `-0 < 0` é `false`).
 */
export function partes(centavos: number): Partes | null {
  if (!Number.isSafeInteger(centavos)) return null;
  const { reais, centavos: centavosTxt } = decompor(Math.abs(centavos));
  return { negativo: centavos < 0, inteiro: comMilhar(reais), centavos: centavosTxt };
}

const ESPACOS = new Set([" ", "\t", "\n", "\r", " ", " ", " ", " "]);
const SINAIS = new Set(["+", "-", "−"]);
const PONTUACAO = new Set([".", ","]);
const PREFIXO = new Set(["R", "$"]);

/** Exportada para `AmountInput` classificar sufixo de colagem sem duplicar a regra. */
export function digitoAscii(caractere: string): boolean {
  return caractere >= "0" && caractere <= "9";
}

/**
 * Lista branca fechada: qualquer caractere fora dela rejeita a mudança
 * inteira (`null`), incluindo dígito não-ASCII (`١٢٣`, `１２３`) — usar `\d`
 * ou `/\p{Nd}/u` aceitaria esses porque eles SÃO dígito Unicode, só não são o
 * dígito 0-9 que o teclado `number-pad` produz.
 *
 * Sinal, ponto, vírgula, "R", "$" e espaço são aceitos e IGNORADOS: colar
 * "R$ 1.234,56" vira "123456" dígito a dígito (decisão do dono — colar não
 * interpreta separador decimal, só concatena o que é 0-9).
 */
export function digitosParaCentavos(texto: string): number | null {
  let digitos = "";
  for (const caractere of texto) {
    if (digitoAscii(caractere)) {
      digitos += caractere;
    } else if (
      !ESPACOS.has(caractere) &&
      !SINAIS.has(caractere) &&
      !PONTUACAO.has(caractere) &&
      !PREFIXO.has(caractere)
    ) {
      return null;
    }
  }

  // Sem zero à esquerda, mas nunca string vazia (precisa sobrar 1 char).
  let inicio = 0;
  while (inicio < digitos.length - 1 && digitos[inicio] === "0") inicio++;
  const semZeros = digitos.slice(inicio);

  // Corta ANTES do `parseInt`: é o que impede "9".repeat(100000) de virar um
  // número gigante só para ser descartado um passo depois.
  if (semZeros.length > 10) return null;
  if (semZeros.length === 0) return 0;

  const valor = parseInt(semZeros, 10);
  if (valor > TETO_CENTAVOS) return null;
  return valor;
}

function contarReal(quantidade: number): string {
  return quantidade === 1 ? "1 real" : `${quantidade} reais`;
}

function contarCentavo(quantidade: number): string {
  return quantidade === 1 ? "1 centavo" : `${quantidade} centavos`;
}

/**
 * Módulo falado em pt-BR, SEM separador de milhar ("1234567 reais", não
 * "1.234.567 reais" — quem lê em voz alta não lê pontos). Nunca leva sinal:
 * quem prefixa "menos "/"mais " é `faladoComSinal`, porque o sinal falado
 * depende do TIPO (decisão 2), não só do valor.
 */
export function falado(centavos: number): string | null {
  if (!Number.isSafeInteger(centavos)) return null;
  const modulo = Math.abs(centavos);
  if (modulo === 0) return "zero reais";

  const { reais: reaisTxt, centavos: centavosTxt } = decompor(modulo);
  const reais = parseInt(reaisTxt, 10);
  const restoCentavos = parseInt(centavosTxt, 10);

  if (reais === 0) return contarCentavo(restoCentavos);
  if (restoCentavos === 0) return contarReal(reais);
  return `${contarReal(reais)} e ${contarCentavo(restoCentavos)}`;
}

export type TipoValorFalado = "saldo" | "entrada" | "saida";

/**
 * Fonte única do texto "sem valor" falado (CLAUDE.md §0.7) — minúsculo
 * porque é a forma usada NO MEIO de uma frase (`AmountInput`, e aqui mesmo
 * dentro de `faladoComSinal`). Quem precisa dele como rótulo isolado, no
 * INÍCIO (`Money`), capitaliza a primeira letra no próprio ponto de uso —
 * o texto é o mesmo, só a posição na frase muda a capitalização.
 */
export const VALOR_INDISPONIVEL_FALADO = "valor indisponível";

/**
 * Fala do valor já com o sinal falado ("mais "/"menos ") aplicado — MESMA
 * regra do `Money` (decisão 2 do dono): `entrada`/`saida` falam o sinal do
 * TIPO, não o do número; só `saldo` segue o sinal do próprio valor. Zero e
 * valor inválido nunca levam sinal ("zero reais", "valor indisponível"), nunca
 * "menos zero reais"/"mais valor indisponível" — fonte única para `Money`
 * (rótulo visual) e `TransactionRow` (rótulo do container `accessible`), que
 * antes remontavam essa regra cada um a seu jeito e já tinham divergido.
 */
export function faladoComSinal(centavos: number, tipo: TipoValorFalado = "saldo"): string {
  const moduloFalado = falado(centavos);
  if (moduloFalado === null) return VALOR_INDISPONIVEL_FALADO;
  if (centavos === 0) return moduloFalado;

  if (tipo === "entrada") return `mais ${moduloFalado}`;
  if (tipo === "saida") return `menos ${moduloFalado}`;
  return centavos < 0 ? `menos ${moduloFalado}` : moduloFalado;
}
