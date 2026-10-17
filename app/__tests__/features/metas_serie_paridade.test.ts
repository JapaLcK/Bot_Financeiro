/// <reference types="node" />
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { serieDaMeta } from "@/features/painel/metasChat";
import { ALFA_WASH_SERIE, SERIE_META } from "@/ui/tokens";
// A cor de série das pílulas de meta copia `--serie-1..4` de `webapp/src/dashboard/styles/metas.css` e a regra do `id` de `MetaPilula.tsx` (§0.7):
// o app não importa o web, então este teste compara os dois. Só o ESCURO tem par na web; o claro é preso por propriedade em `ui/contraste.test.ts`.
const RAIZ = resolve(__dirname, "../../..");
const css = readFileSync(resolve(RAIZ, "webapp/src/dashboard/styles/metas.css"), "utf8");
const pilula = readFileSync(resolve(RAIZ, "webapp/src/dashboard/parts/MetaPilula.tsx"), "utf8");
interface Serie { n: number; hex: string; rgb: number[]; alfa: number }
/** As séries do CSS da web. Falha ALTO se não achar exatamente 4 (1..4): lista vazia compararia verde com nada. */
function lerWeb(fonte: string): Serie[] {
  const re = /--serie-(\d):\s*(#[0-9a-f]{6});\s*--serie-\1-wash:\s*rgba\((\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\)/gi;
  const s = [...fonte.matchAll(re)].map((m) => ({ n: Number(m[1]), hex: m[2]!.toUpperCase(), rgb: [m[3], m[4], m[5]].map(Number), alfa: Number(m[6]) }));
  if (s.length !== 4 || s.some((x, i) => x.n !== i + 1)) throw new Error(`metas.css: esperava --serie-1..4 com -wash, achei ${s.map((x) => x.n).join(",") || "nenhuma"}`);
  return s;
}
/** O mapa `.pilula[data-serie="N"] { --cor: var(--serie-K); --cor-wash: var(--serie-W-wash) }` do web: [N, K, W]. Falha ALTO se não achar exatamente os 4 (sem ele, trocar duas séries no CSS passaria). */
function lerMapa(fonte: string): number[][] {
  const re = /\.pilula\[data-serie="(\d)"\]\s*\{\s*--cor:\s*var\(--serie-(\d)\);\s*--cor-wash:\s*var\(--serie-(\d)-wash\);\s*\}/g;
  const m = [...fonte.matchAll(re)].map((x) => [Number(x[1]), Number(x[2]), Number(x[3])] as number[]);
  if (m.length !== 4 || m.some((x, i) => x[0] !== i + 1)) throw new Error(`metas.css: esperava o mapa .pilula[data-serie=1..4], achei ${m.map((x) => x[0]).join(",") || "nenhum"}`);
  return m;
}
/** O N de `Math.abs(m.id) % N` no `data-serie` do web. Falha ALTO se a fórmula mudou de forma. */
function lerModulo(fonte: string): number {
  const m = /data-serie=\{reserva \? undefined : \(Math\.abs\(m\.id\) % (\d+)\) \+ 1\}/.exec(fonte);
  if (!m) throw new Error("MetaPilula.tsx: a fórmula de data-serie mudou");
  return Number(m[1]);
}
/** O que o escuro do app diz de diferente do web (lista vazia = paridade); `escuro`/`alfa` entram como parâmetro para o controle negativo poder mutá-los. */
function divergencias(escuro: readonly string[], alfa: number, fonte = css): string[] {
  const d: string[] = [], web = lerWeb(fonte);
  web.forEach((w, i) => {
    if (escuro[i]?.toUpperCase() !== w.hex) d.push(`hex ${w.n}`);
    if (escuro[i] && w.rgb.join() !== [1, 3, 5].map((k) => Number.parseInt(escuro[i]!.slice(k, k + 2), 16)).join()) d.push(`rgb do wash ${w.n}`);
    if (w.alfa !== alfa) d.push(`alfa ${w.n}`);
  });
  if (escuro.length !== web.length) d.push("quantidade");
  for (const [n, k, w] of lerMapa(fonte)) if (n !== k || n !== w) d.push(`mapa ${n}`); // data-serie="N" tem de apontar para --serie-N (cor e wash)
  return d;
}
it("o escuro do app tem os 4 hex, a ordem, o RGB do wash e o alfa 0,12 do web", () => expect(divergencias(SERIE_META.dark, ALFA_WASH_SERIE)).toEqual([]));
it("a regra do id é a do web: módulo igual ao nº de cores e a tabela de MetaPilula (ids 2,3,4,5 -> séries 3,4,1,2 no web)", () => {
  expect(lerModulo(pilula)).toBe(SERIE_META.dark.length);
  expect([2, 3, 4, 5].map(serieDaMeta)).toEqual([2, 3, 0, 1]); // o web: data-serie "3","4","1","2" (índice + 1)
  expect(serieDaMeta(-3)).toBe(3); expect(serieDaMeta(0)).toBe(0); // Math.abs: id negativo não vira índice negativo
});
it("controle negativo: o comparador acusa hex trocado, ordem trocada, wash com outro RGB e alfa mudado", () => {
  const d = [...SERIE_META.dark];
  expect(divergencias([d[0]!, d[1]!, d[2]!, "#F0ABFD"], ALFA_WASH_SERIE)).toContain("hex 4");
  expect(divergencias([d[1]!, d[0]!, d[2]!, d[3]!], ALFA_WASH_SERIE)).toEqual(expect.arrayContaining(["hex 1", "hex 2"]));
  expect(divergencias(d, 0.14)).toContain("alfa 1");
  expect(divergencias(d.slice(0, 3), ALFA_WASH_SERIE)).toContain("quantidade");
  expect(divergencias(d, ALFA_WASH_SERIE, css.replace("rgba(34, 211, 238", "rgba(34, 211, 239"))).toContain("rgb do wash 1");
});
it("controle negativo: leitor falha alto se o CSS perde o -wash, a série 4 ou o formato; e se a fórmula do id vira % 5", () => {
  expect(() => lerWeb("")).toThrow(/achei nenhuma/);
  expect(() => lerWeb(css.replaceAll("-wash:", "-washed:"))).toThrow();
  expect(() => lerWeb(css.replace(/--serie-4:[^\n]*\n/, ""))).toThrow(/1,2,3/);
  expect(() => lerWeb(css.replaceAll("rgba(", "color-mix(in srgb, "))).toThrow();
  expect(() => lerModulo("")).toThrow(/mudou/);
  expect(lerModulo(pilula.replace("% 4)", "% 5)"))).toBe(5); // o N lido muda, e o teste da regra acima compara com SERIE_META.dark.length
});
it("controle negativo: o mapa data-serie -> --serie trocado é acusado, e o leitor falha alto se o mapa some", () => {
  const troca = css.replace('[data-serie="1"] { --cor: var(--serie-1);', '[data-serie="1"] { --cor: var(--serie-2);'), semWash = css.replace('--cor-wash: var(--serie-3-wash); }', '--cor-wash: var(--serie-4-wash); }');
  expect(troca).not.toBe(css); expect(semWash).not.toBe(css); // o replace achou o texto: senão o controle seria no-op
  expect(divergencias(SERIE_META.dark, ALFA_WASH_SERIE, troca)).toContain("mapa 1");
  expect(divergencias(SERIE_META.dark, ALFA_WASH_SERIE, semWash)).toContain("mapa 3");
  const semMapa = css.replaceAll(/\.pilula\[data-serie="\d"\][^\n]*\n/g, ""); expect(semMapa).not.toBe(css);
  expect(() => lerMapa(semMapa)).toThrow(/nenhum/);
  expect(() => lerMapa(css.replace(/\.pilula\[data-serie="4"\][^\n]*\n/, ""))).toThrow(/1,2,3/);
});
