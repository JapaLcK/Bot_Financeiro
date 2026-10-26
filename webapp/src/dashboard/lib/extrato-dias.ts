import type { Lancamento } from "./api-v2.gen";
import { decimal, isoDay, longDate, moneyText, weekday } from "./format.js";

// (tipo, origem, rótulo): a mesma lista do app (app/src/features/painel/extratoFiltros.tsx); tests/frontend/extrato_paridade.test.mjs compara as duas.
export const ATALHOS = [["", "", "Tudo"], ["entrada", "", "Entradas"], ["saida", "", "Saídas"], ["", "cartao", "Cartão"]] as const;

const ISO = /^\d{4}-\d{2}-\d{2}$/;
export const existe = (d: string) => ISO.test(d) && new Date(`${d}T12:00:00Z`).toISOString().slice(0, 10) === d; // "2026-02-30" passa na regex e não existe
// "Hoje · 19/10", "Ontem · 18/10", "Sex · 17/10" (outro ano: "/aa", menos em Hoje/Ontem, como no app). `hoje` é o dia de São Paulo (hojeNoFuso), nunca o do aparelho.
export function rotuloDia(dia: string, hoje: string): string {
  if (!existe(dia) || !ISO.test(hoje)) return longDate(isoDay(dia));
  const ontem = new Date(`${hoje}T12:00:00Z`);
  ontem.setUTCDate(ontem.getUTCDate() - 1);
  const dm = `${dia.slice(8)}/${dia.slice(5, 7)}`;
  if (dia === hoje) return `Hoje · ${dm}`;
  if (dia === ontem.toISOString().slice(0, 10)) return `Ontem · ${dm}`;
  return diaCurto(dia, hoje.slice(0, 4));
}
/** "Sex · 17/10"; de outro ano que `ano`: "Sex · 17/10/25". */
export function diaCurto(dia: string, ano: string): string {
  const sem = weekday(isoDay(dia));
  return `${sem[0].toUpperCase()}${sem.slice(1)} · ${dia.slice(8)}/${dia.slice(5, 7)}${dia.slice(0, 4) === ano ? "" : `/${dia.slice(2, 4)}`}`;
}

// Soma exata (BigInt) do que conta no total: fora interno, guardado e outra moeda. Nada conta → null (nunca "R$ 0,00").
function totalDecimal(itens: Lancamento[]): string | null {
  const contam = itens.flatMap((i) => {
    const d = i.interno || i.guardado || i.moeda !== "BRL" ? null : decimal(i.valor);
    return d ? [{ n: i.tipo === "entrada" ? (d.n < BigInt(0) ? -d.n : d.n) : -(d.n < BigInt(0) ? -d.n : d.n), escala: d.escala }] : [];
  });
  if (!contam.length) return null;
  const escala = Math.max(...contam.map((c) => c.escala));
  const soma = contam.reduce((s, c) => s + c.n * BigInt(10) ** BigInt(escala - c.escala), BigInt(0));
  const abs = (soma < BigInt(0) ? -soma : soma).toString().padStart(escala + 1, "0");
  return `${soma < BigInt(0) ? "-" : ""}${escala ? `${abs.slice(0, -escala)}.${abs.slice(-escala)}` : abs}`;
}

// Agrupa por dia (Map: ordem da 1ª aparição). O total só existe em grupo COMPLETO: o servidor ordena por dia desc,
// então só o último dia da página pode continuar na próxima (`completo` = não há próxima página).
export function diasDoExtrato(itens: Lancamento[], completo: boolean) {
  const grupos = new Map<string, Lancamento[]>();
  for (const item of itens) grupos.set(item.data, [...(grupos.get(item.data) ?? []), item]);
  const dias = [...grupos];
  return dias.map(([dia, linhas], i) => {
    const t = completo || i < dias.length - 1 ? totalDecimal(linhas) : null;
    const ganho = !!t && t[0] !== "-" && /[1-9]/.test(t);
    return { dia, itens: linhas, total: t && { texto: `${ganho ? "+" : ""}${moneyText(t)}`, ganho } };
  });
}
