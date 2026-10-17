/**
 * Gera `tests/fixtures/gastos_dia_casos.json`: a tabela dourada da conta do "Seu dia" (média, dia abrível, vizinho, pct e tom).
 * A saída é a do WEB (`webapp/src/dashboard/lib/gastos-dia.ts`, a autoridade, #1029); o jest do app (`gastosDia.ts`) lê a mesma tabela.
 * O teste web `tests/frontend/dashboard_v2_gastos_dia_topo.test.mjs` compara a tabela commitada com `gerar()`: mudou o web, regenere no mesmo PR.
 *
 * Rodar:  node scripts/gerar_gastos_dia_casos.mjs
 */
import { writeFileSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import { contraMedia, diaAbrivel, mediaDoPeriodo, vizinho } from "../webapp/src/dashboard/lib/gastos-dia.ts";

const ALVO = fileURLToPath(new URL("../tests/fixtures/gastos_dia_casos.json", import.meta.url));
const pad = (n) => String(n).padStart(2, "0");
const caso = (nome, mes, hoje, historico_desde, totais) => ({
  nome, entrada: { mes, hoje, historico_desde, dias: Object.entries(totais).map(([n, total]) => ({ dia: `${mes}-${pad(n)}`, total })) },
});
// Cada total é TEXTO decimal, como o servidor devolve (`Decimal` sem arredondar: "1.137" existe).
export const ENTRADAS = [
  caso("centavos primeiro erra: 1.040 e 1.137 (web −4% neutro; centavos −5% positivo)", "2026-10", "2026-10-02", null, { 1: "1.040", 2: "1.137" }),
  caso("decimal exato erra: 0.191 e 0.209 (web −5% por erro de double; exato −4%)", "2026-10", "2026-10-02", null, { 1: "0.191", 2: "0.209" }),
  caso("escalas diferentes no mesmo mês: 91.010 × 1.005", "2026-10", "2026-10-04", null, { 1: "91.010", 2: "1.005", 3: "2.000", 4: "7.5" }),
  caso("total com 6 casas", "2026-10", "2026-10-02", null, { 1: "12.345678", 2: "7.654322" }),
  caso("empate no meio, média 100: 95.500 cai abaixo de −4,5 (−5, gain) e 104.500 abaixo de +4,5", "2026-10", "2026-10-02", null, { 1: "95.500", 2: "104.500" }),
  caso("empate no meio, média 10: 9.550 cai acima de −4,5 (−4, neutro)", "2026-10", "2026-10-02", null, { 1: "9.550", 2: "10.450" }),
  caso("empate no meio, média 100: 114.500 cai acima de +14,5 (15, warn) e 85.500 abaixo de −14,5", "2026-10", "2026-10-02", null, { 1: "114.500", 2: "85.500" }),
  caso("mês corrente com corte do histórico, dia agendado e dia sem gasto", "2026-10", "2026-10-14", "2026-10-06", { 3: "50.00", 6: "115.00", 8: "180.00", 9: "0.000", 11: "0.004", 14: "50.00", 20: "40.00", 25: "0.00" }),
  caso("corte depois de hoje: nenhum dia vivido, sem média", "2026-10", "2026-10-14", "2026-10-20", { 5: "10.00", 22: "5.00" }),
  caso("mês passado inteiro (vai até o fim do mês)", "2026-09", "2026-10-14", null, { 1: "100.00", 15: "300.00", 30: "20.00" }),
  caso("fevereiro bissexto (29 dias)", "2028-02", "2028-03-05", null, { 1: "58.00", 28: "29.00", 29: "0.01" }),
  caso("pct −0: 99.9 contra média 100 (round dá −0; o `|| 0` o troca)", "2026-10", "2026-10-02", null, { 1: "99.900", 2: "100.100" }),
  caso("média 0 com dia presente (só zeros): sem pílula, nenhum dia vivido", "2026-10", "2026-10-02", null, { 1: "0.000", 2: "0.000" }),
  caso("mês sem nenhum gasto", "2026-10", "2026-10-14", null, {}),
];

/** A saída do web para uma entrada: média, nº de dias, a grade inteira (dia abrível e vizinhos) e pct/tom de cada dia com gasto. */
export function gerar({ entrada }) {
  const d = entrada;
  const { media, nDias, vividos } = mediaDoPeriodo(d);
  const [y, m] = d.mes.split("-").map(Number);
  const grade = Array.from({ length: new Date(y, m, 0).getDate() }, (_, i) => {
    const dia = `${d.mes}-${pad(i + 1)}`;
    return { dia, abrivel: diaAbrivel(dia, d), anterior: vizinho(dia, -1, d), proximo: vizinho(dia, 1, d) };
  });
  const dias = d.dias.map((g) => { const c = contraMedia(Number(g.total), media); return { dia: g.dia, pct: c?.pct ?? null, tom: c?.tom ?? null }; });
  return { media, nDias, vividos: vividos.length, grade, dias };
}

export const tabela = () => ENTRADAS.map((c) => ({ ...c, saida: gerar(c) }));

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  writeFileSync(ALVO, JSON.stringify(tabela(), null, 1) + "\n");
  console.log(`escrito: ${ALVO}`);
}
