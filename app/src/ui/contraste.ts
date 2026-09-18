import type { Paleta } from "@/ui/tokens";

/**
 * Contraste WCAG 2.x (relative luminance, fórmula do próprio W3C). Só hex de 6
 * dígitos: é tudo que os tokens usam, então um parser de CSS completo seria
 * escopo que ninguém pediu.
 */
function canal(c: number): number {
  const s = c / 255;
  return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
}

function luminancia(hex: string): number {
  const n = Number.parseInt(hex.replace("#", ""), 16);
  const r = (n >> 16) & 0xff;
  const g = (n >> 8) & 0xff;
  const b = n & 0xff;
  return 0.2126 * canal(r) + 0.7152 * canal(g) + 0.0722 * canal(b);
}

export function contraste(a: string, b: string): number {
  const l1 = luminancia(a);
  const l2 = luminancia(b);
  const [claraMax, claraMin] = l1 > l2 ? [l1, l2] : [l2, l1];
  return (claraMax + 0.05) / (claraMin + 0.05);
}

type Chave = keyof Paleta;

/** Um par a checar, e o mínimo AA que ele precisa alcançar. */
export interface ParDeContraste {
  primeiro: Chave;
  segundo: Chave;
  minimo: 4.5 | 3;
}

const TEXTO: Chave[] = ["ink", "inkMuted", "brandInk", "positive", "warning", "danger"];
const FUNDOS: Chave[] = ["bg", "surface", "surfaceRaised"];

/**
 * Todo par que o produto realmente sobrepõe (texto ou não-texto sobre fundo).
 * `brand` sobre `brandSoft` NÃO entra: é o par proibido do plano (2,96 —
 * reprova mesmo o teto de 3 de não-texto), documentado para nunca ser usado
 * como texto/ícone sobre aquele fundo.
 *
 * Pares NOVOS que o C1 passou a sobrepor e que também ficam de fora — não
 * por reprovar, mas por serem só DECORAÇÃO, nunca o único sinal de um
 * estado (nenhum comunica informação sozinho, então nenhum teto de
 * contraste se aplica). Medido com a própria `contraste()` acima, hex de
 * `tokens.ts`, 2026-09-17 — remeça se a paleta mudar:
 *
 * `border`×`surfaceRaised` = 1,25 no claro — o friso real do Card `raised`
 * (`Card.tsx`: fundo `surfaceRaised` quando `elevacao="raised"`, borda
 * `border`). No claro isso empata com `border`×`bg` (1,25) porque
 * `surfaceRaised === bg === "#FFFFFF"`. `border`×`surface` NÃO é um par do
 * produto: `border` só aparece como preenchimento sólido do `Skeleton`,
 * nunca como contorno sobre `surface`.
 *
 * `surfaceRaised`×`surface` = 1,08 no claro, 1,10 no escuro — o par real por
 * trás do indicador do `SegmentedControl` (fundo `surfaceRaised` sobre o
 * trilho `surface`), mesmo número que o comentário em
 * `SegmentedControl.tsx` já usa. Longe de qualquer teto de contraste, por
 * isso o indicador ganhou uma borda de `inkMuted` à parte para marcar o
 * selecionado.
 *
 * `surface`×`bg` = 1,08 no escuro — o halo do `EmptyState` (disco `surface`
 * atrás do sticker, só no tema escuro). Decorativo: o sticker é o único
 * sinal (nenhuma informação depende do halo sozinho), então nenhum teto de
 * contraste se aplica. Medido com a `contraste()` acima, hex de
 * `tokens.ts`, 2026-09-17 — remeça se a paleta mudar.
 */
export const PARES: ParDeContraste[] = [
  ...TEXTO.flatMap((primeiro) => FUNDOS.map((segundo) => ({ primeiro, segundo, minimo: 4.5 as const }))),
  ...(["ink", "inkMuted", "brandInk"] as Chave[]).map((primeiro) => ({
    primeiro,
    segundo: "brandSoft" as Chave,
    minimo: 4.5 as const,
  })),
  { primeiro: "onAcao", segundo: "acao", minimo: 4.5 },
  { primeiro: "onDanger", segundo: "danger", minimo: 4.5 },
  ...FUNDOS.map((segundo) => ({ primeiro: "brand" as Chave, segundo, minimo: 3 as const })),
  ...(["bg", "surface"] as Chave[]).map((segundo) => ({
    primeiro: "inkMuted" as Chave,
    segundo,
    minimo: 3 as const,
  })),
  ...FUNDOS.map((segundo) => ({ primeiro: "inkFaint" as Chave, segundo, minimo: 3 as const })),
];
