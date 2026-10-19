/// <reference types="node" />
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { ERRO_CAMPO, TEXTO_CODIGO, VAZIO, corpoDe } from "@/features/painel/simuladorCompra";
import v2 from "../../../tests/frontend/api_v2_respostas.json";
// `simuladorCompra.ts` copia os campos e os códigos do `SimuladorCompra.tsx` da web (§0.7): o app não importa o web, então este teste compara as CHAVES (campos, códigos de erro, corpo do POST)
// com o web, com o contrato gerado (`api-v2.gen.ts`) e com a fixture dos códigos do servidor (já presa ao Python por `tests/test_api_v2_contrato.py`). As FRASES são do app (mínimo de texto): não se comparam.
const RAIZ = resolve(__dirname, "../../..");
const web = readFileSync(resolve(RAIZ, "webapp/src/dashboard/widgets/SimuladorCompra.tsx"), "utf8");
const gen = readFileSync(resolve(RAIZ, "webapp/src/dashboard/lib/api-v2.gen.ts"), "utf8");
const bloco = (nome: string) => web.split(`const ${nome}`)[1]!.split("\n};")[0]!;
const chaves = (b: string) => [...b.matchAll(/(?:^|,|\{)\s*(\w+):\s/g)].map((m) => m[1]!).sort();
const tipo = (nome: string) => [...gen.split(`export type ${nome} = {`)[1]!.split("};")[0]!.matchAll(/(\w+)\??:/g)].map((m) => m[1]!).sort();
const CODIGOS_422 = (v2 as unknown as { simulador: { codigos_422: string[] } }).simulador.codigos_422;
const COMPLETO = { nome: "A", preco: 100, entrada: 1, custos: 1, despesa: 1, parcelas: "2", juros: "1", data: "2026-11-05" };

/** O que o app diz de diferente do web, do contrato e da fixture (vazio = paridade); os parâmetros deixam o controle negativo mutá-los. */
function divergencias(erroCampo: Record<string, string>, textoCodigo: Record<string, string>, corpo: { cenarios: object[] } & Record<string, unknown>): string[] {
  const d: string[] = [], cen = tipo("CenarioIn"), topo = tipo("SimuladorIn");
  if (JSON.stringify(Object.keys(erroCampo).sort()) !== JSON.stringify(chaves(bloco("ERRO_CAMPO")))) d.push("chaves de ERRO_CAMPO (web)");
  if (JSON.stringify(Object.keys(erroCampo).filter((k) => k !== "reserva_minima").sort()) !== JSON.stringify(cen)) d.push("chaves de ERRO_CAMPO (CenarioIn)");
  if (!erroCampo.reserva_minima) d.push("reserva_minima");
  if (JSON.stringify(Object.keys(textoCodigo).sort()) !== JSON.stringify(chaves(bloco("TEXTO_CODIGO")))) d.push("chaves de TEXTO_CODIGO (web)");
  if (JSON.stringify([...Object.keys(textoCodigo), "valor_invalido"].sort()) !== JSON.stringify([...CODIGOS_422].sort())) d.push("chaves de TEXTO_CODIGO (fixture do servidor)");
  for (const k of Object.keys(corpo)) if (!topo.includes(k)) d.push(`corpo.${k} fora do SimuladorIn`);
  for (const c of corpo.cenarios) for (const k of Object.keys(c)) if (!cen.includes(k)) d.push(`cenario.${k} fora do CenarioIn`);
  return d;
}
it("campos de erro, códigos de erro e chaves do corpo do POST são os do web, do contrato gerado e do servidor", () => {
  expect(tipo("CenarioIn")).toHaveLength(8); expect(chaves(bloco("TEXTO_CODIGO"))).toHaveLength(9); // o leitor acha algo: sem isso, "vazio = vazio" passaria
  expect(divergencias(ERRO_CAMPO, TEXTO_CODIGO, corpoDe(100, [COMPLETO]))).toEqual([]);
  expect(Object.keys(corpoDe(100, [COMPLETO]).cenarios[0]!).sort()).toEqual(tipo("CenarioIn")); // o cenário completo usa TODAS as chaves do contrato
  expect(Object.keys(corpoDe(100, [{ ...VAZIO, preco: 1 }]).cenarios[0]!).sort()).toEqual(["nome", "preco"]);
});
it("controle negativo: o comparador acusa chave a mais, chave a menos e chave de corpo inventada", () => {
  const { nome: _n, ...semNome } = ERRO_CAMPO, { dinheiro_casas: _d, ...semDinheiro } = TEXTO_CODIGO, c = corpoDe(100, [COMPLETO]);
  expect(divergencias({ ...ERRO_CAMPO, extra: "x" }, TEXTO_CODIGO, c)).toEqual(expect.arrayContaining(["chaves de ERRO_CAMPO (web)", "chaves de ERRO_CAMPO (CenarioIn)"]));
  expect(divergencias(semNome, TEXTO_CODIGO, c)).toEqual(expect.arrayContaining(["chaves de ERRO_CAMPO (web)"]));
  expect(divergencias(ERRO_CAMPO, { ...TEXTO_CODIGO, novo_codigo: "x" }, c)).toEqual(expect.arrayContaining(["chaves de TEXTO_CODIGO (web)", "chaves de TEXTO_CODIGO (fixture do servidor)"]));
  expect(divergencias(ERRO_CAMPO, semDinheiro, c)).toEqual(expect.arrayContaining(["chaves de TEXTO_CODIGO (web)", "chaves de TEXTO_CODIGO (fixture do servidor)"]));
  expect(divergencias(ERRO_CAMPO, TEXTO_CODIGO, { ...c, inventada: 1 })).toContain("corpo.inventada fora do SimuladorIn");
  expect(divergencias(ERRO_CAMPO, TEXTO_CODIGO, { cenarios: [{ ...c.cenarios[0]!, parcelas_total: 1 }] })).toContain("cenario.parcelas_total fora do CenarioIn");
});
