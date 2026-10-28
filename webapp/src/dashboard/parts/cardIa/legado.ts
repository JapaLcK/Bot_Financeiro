import { money, moneyText } from "../../lib/format.js";

// As peças dos cards de rota legada (caixinhas, fatura, parcelas, renda, piggy): o servidor os manda como dict de float, sem o Pydantic da /api/v2. Por isso o
// contrato de cada um confere TODO número que o card desenha (`num`): "R$ NaN" e "∞" nunca chegam à tela. `nomeLegivel`, `indicadorMeta` e `dataPtBr` são cópia de
// app/src/features/painel/catalogo.ts (os dois projetos npm não se importam; tests/frontend/card_ia_paridade.test.mjs executa as duas versões).
export const num = (x: unknown): x is number => typeof x === "number" && Number.isFinite(x);
export const numOuNull = (x: unknown) => x === null || num(x);
export const textoOuNull = (x: unknown) => x === null || typeof x === "string";
export const lista = (x: unknown, ok: (i: unknown) => boolean) => Array.isArray(x) && x.every(ok);
/** O dinheiro que o card confere: `num` no chat (float legado) ou `texto` no Resumo (a /api/v2: texto decimal que `moneyText` lê; "NaN", "∞" e texto torto nunca passam). O chat segue só com float. */
export type Dinheiro = (x: unknown) => boolean;
export const texto: Dinheiro = (x) => typeof x === "string" && moneyText(x) !== null;
/** Dinheiro já conferido por `num` ou `texto`; `null` é "Não informado", nunca R$ 0,00. */
export const reais = (n: number | string | null) => (n === null ? "Não informado" : typeof n === "string" ? moneyText(n)! : money(n));

export function dataPtBr(dia: string | null) {
  if (!dia || !/^\d{4}-\d{2}-\d{2}$/.test(dia)) return "Data não informada";
  return `${dia.slice(8)}/${dia.slice(5, 7)}/${dia.slice(0, 4)}`;
}
export function indicadorMeta(indicador: string) {
  const rotulos: Record<string, string> = { no_target: "Sem objetivo definido", achieved: "Objetivo alcançado", on_track: "No ritmo para alcançar", ahead: "Adiantada em relação ao objetivo", behind: "Abaixo do ritmo necessário", tight: "Prazo apertado", active: "Meta em andamento", no_deadline: "Sem prazo definido" };
  return rotulos[indicador] ?? "Ritmo da meta a conferir";
}
export function nomeLegivel(nome: string) {
  return /^[a-z0-9]+([-_][a-z0-9]+)+$/.test(nome) ? nome.split(/[-_]/).map((p) => p[0]!.toUpperCase() + p.slice(1)).join(" ") : nome;
}
