/// <reference types="node" />
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { DIRECAO, ESTADO, GASTOS_DIA_A_DIA, MOTIVO, MOTIVO_PATRIMONIO, ou } from "@/features/painel/previsaoMotivos";
// `previsaoMotivos.ts` copia o web (§0.7): o app não importa o web, então este teste compara com o fonte dele e com os códigos do Python.
const RAIZ = resolve(__dirname, "../../..");
const ler = (p: string) => readFileSync(resolve(RAIZ, p), "utf8");
const selos = ler("webapp/src/dashboard/parts/Selos.tsx"), previsao = ler("webapp/src/dashboard/widgets/Previsao.tsx");
const py = ler("core/services/cashflow_snapshot.py") + ler("core/services/previsao_recorrencias.py");
const contas = ler("db/contas_hoje.py"), tupla = (n: string) => [...contas.match(new RegExp(`${n} = \\(([^)]*)\\)`))![1]!.matchAll(/"([a-z_]+)"/g)].map((m) => m[1]!);
const excluidos = [...py.match(/if code not in \(([^)]*)\)/)![1]!.matchAll(/'([a-z_]+)'/g)].map((m) => m[1]!);
const pares = (t: string) => Object.fromEntries([...t.matchAll(/^ {2}(\w+): "([^"]*)"/gm)].map((m) => [m[1]!, m[2]!]));
/** O que o app diz de diferente do web/Python (lista vazia = paridade); parâmetros para o controle negativo poder mutá-los. */
function divergencias(motivo: Record<string, string>, direcao: Record<string, string>, estado: Record<string, string>): string[] {
  const d: string[] = [], webMotivo = pares(selos.split("const MOTIVO")[1]!.split("\n};")[0]!), webDirecao = pares(selos.split("export const DIRECAO")[1]!.split("\n};")[0]!);
  const codigos = new Set([...py.matchAll(/_motivos?\(\w+, '([a-z_]+)'/g)].map((m) => m[1]!).concat(tupla("MOTIVOS_CARTEIRA"), tupla("MOTIVOS_CONTA")).filter((c) => !excluidos.includes(c)));
  if (JSON.stringify(Object.keys(motivo).sort()) !== JSON.stringify([...codigos].sort())) d.push("códigos do Python");
  for (const [k, v] of Object.entries(motivo)) if (v !== webMotivo[k]) d.push(`motivo ${k}`);
  if (JSON.stringify(direcao) !== JSON.stringify(webDirecao)) d.push("direção");
  for (const [k, v] of Object.entries(estado)) if (!previsao.includes(`${k}: "${v}"`)) d.push(`estado ${k}`);
  return d;
}
it("a leitura do fonte achou as exclusões e as tuplas (zero = falha alto)", () => {
  expect(excluidos.sort()).toEqual(["conciliacao_pendente", "movimentos_pendentes"]); expect(tupla("MOTIVOS_CARTEIRA").length + tupla("MOTIVOS_CONTA").length).toBe(10);
});
it("motivos, direções e estados do app são os do web, e os códigos são os do Python", () => {
  expect(Object.keys(MOTIVO).length).toBeGreaterThan(30); // a regex achou a seção, não uma lista vazia
  expect(divergencias(MOTIVO, DIRECAO, ESTADO)).toEqual([]);
});
it("controle negativo: o comparador acusa rótulo mudado, código removido, direção e estado desviados", () => {
  expect(divergencias({ ...MOTIVO, bancos_excluidos: "x" }, DIRECAO, ESTADO)).toContain("motivo bancos_excluidos");
  const { bancos_excluidos: _a, ...resto } = MOTIVO; expect(divergencias(resto, DIRECAO, ESTADO)).toContain("códigos do Python");
  const { banco_desatualizado: _b, ...semBanco } = MOTIVO; expect(divergencias(semBanco, DIRECAO, ESTADO)).toContain("códigos do Python");
  expect(divergencias(MOTIVO, { ...DIRECAO, ambos: "x" }, ESTADO)).toContain("direção");
  expect(divergencias(MOTIVO, DIRECAO, { ...ESTADO, a_conferir: "x" })).toContain("estado a_conferir");
});
it("MOTIVO_PATRIMONIO (Progresso ao milhão) é o do web", () => {
  const web = pares(selos.split("const MOTIVO")[1]!.split("\n};")[0]!);
  for (const [k, v] of Object.entries(MOTIVO_PATRIMONIO)) expect(web[k]).toBe(v);
  expect(Object.keys(MOTIVO_PATRIMONIO)).toEqual(["manual_e_banco", "caixinha_espelhada_fora", "conciliacao_pendente", "movimentos_pendentes"]);
});
/** Códigos de `db/patrimonio.py` sem rótulo no app (`sem_banco_conectado` vem da rota e troca o corpo da tela, não vira selo). */
const semRotulo = (rotulo: Record<string, string>) => emitidos.filter((c) => !(c in rotulo));
const emitidos = [...ler("db/patrimonio.py").split("motivos = [")[1]!.split("if sim]")[0]!.matchAll(/^ {8}\("([a-z_]+)",/gm)].map((m) => m[1]!);
it("todo código que o patrimônio emite tem rótulo no app (a categoria, não só os dois conhecidos)", () => {
  expect(emitidos.length).toBeGreaterThanOrEqual(10); // a regex achou o bloco
  expect(semRotulo({ ...MOTIVO, ...MOTIVO_PATRIMONIO })).toEqual([]);
  const { conciliacao_pendente: _c, ...sem } = MOTIVO_PATRIMONIO; // controle negativo
  expect(semRotulo({ ...MOTIVO, ...sem })).toEqual(["conciliacao_pendente"]);
});
it("só chave própria: 'constructor' e '__proto__' saem crus", () => {
  expect(ou(MOTIVO, "constructor")).toBe("constructor"); expect(ou(MOTIVO, "__proto__")).toBe("__proto__"); expect(ou(MOTIVO, "bancos_excluidos")).toBe("bancos fora do cálculo");
});
it("o rótulo 'Gastos do dia a dia' é o mesmo literal nos dois lados (e o controle acusa o desvio)", () => {
  const web = (t: string) => t.includes(`GASTOS_DIA_A_DIA = "${GASTOS_DIA_A_DIA}"`);
  expect(GASTOS_DIA_A_DIA).toBe("Gastos do dia a dia"); expect(web(previsao)).toBe(true);
  expect(web(previsao.replace("Gastos do dia a dia", "Gastos variáveis"))).toBe(false);
});
