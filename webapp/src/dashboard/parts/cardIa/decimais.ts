import { decimal } from "../../lib/format.js";
import { menos } from "../CardResumoMes";

// Soma exata (decimal em texto, sem float) e a parte de um valor no total em %, inteira, HALF_UP afastando do zero: a regra de `percentualDe` do app
// (app/src/features/painel/decimal.ts) e do `periodo` do servidor; tests/fixtures/percentual_categorias.json é a tabela dos três (tests/frontend/card_ia_paridade.test.mjs).
const neg = (v: string) => (v.startsWith("-") ? v.slice(1) : `-${v}`);
export const soma = (vs: string[]) => vs.reduce((a, v) => menos(a, neg(v)), "0");
export function pctDe(valor: string, total: string) {
  const a = decimal(valor)!, t = decimal(total)!, e = Math.max(a.escala, t.escala), esc = (x: { n: bigint; escala: number }) => x.n * BigInt(10) ** BigInt(e - x.escala);
  const n = esc(a) * BigInt(100), d = esc(t), an = n < BigInt(0) ? -n : n, ad = d < BigInt(0) ? -d : d;
  if (ad === BigInt(0)) return 0;
  const q = (BigInt(2) * an + ad) / (BigInt(2) * ad);
  return Number((n < BigInt(0)) !== (d < BigInt(0)) ? -q : q);
}
