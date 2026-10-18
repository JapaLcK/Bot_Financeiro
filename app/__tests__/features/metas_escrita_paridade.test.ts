/// <reference types="node" />
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { CAMPOS_META, ERRO_CAMPO, MESES_MAX, MESES_MIN, NOME_MAX, NOME_RESERVA } from "@/features/painel/metasEscrita";
// `metasEscrita.ts` copia as regras do `MetaForm.tsx` e do `falhaDaEscrita` da web (§0.7): o app não importa o web, então este teste compara os dois
// (campos, códigos de erro, nome da reserva) e os limites com o servidor (`api/v2/metas.py`). As FRASES são do app (mínimo de texto): só as chaves se comparam.
const RAIZ = resolve(__dirname, "../../..");
const web = readFileSync(resolve(RAIZ, "webapp/src/dashboard/lib/metas-textos.ts"), "utf8");
const form = readFileSync(resolve(RAIZ, "webapp/src/dashboard/parts/MetaForm.tsx"), "utf8");
const servidor = readFileSync(resolve(RAIZ, "api/v2/metas.py"), "utf8");
const app = readFileSync(resolve(__dirname, "../../src/features/painel/metasEscrita.ts"), "utf8");
const bloco = (fonte: string, nome: string) => fonte.split(`export const ${nome}`)[1]!.split("\n};")[0]!;
const lista = (fonte: string) => [...fonte.matchAll(/"(\w+)"/g)].map((m) => m[1]);
/** O que o app diz de diferente do web (vazio = paridade); os parâmetros deixam o controle negativo mutá-los. */
function divergencias(campos: readonly string[], erroCampo: Record<string, string>, nomeReserva: string, limites: [number, number, number], codigos: string[]): string[] {
  const d: string[] = [];
  if (JSON.stringify(campos) !== JSON.stringify(lista(/CAMPOS_META = \[([^\]]*)\]/.exec(web)![1]!))) d.push("CAMPOS_META");
  if (JSON.stringify(Object.keys(erroCampo)) !== JSON.stringify([...bloco(web, "ERRO_CAMPO").matchAll(/^ {2}(\w+):/gm)].map((m) => m[1]))) d.push("chaves de ERRO_CAMPO");
  if (!form.includes(`const NOME_RESERVA = "${nomeReserva}"`)) d.push("NOME_RESERVA");
  if (!servidor.includes(`max_length=${limites[0]}`)) d.push("NOME_MAX"); if (!servidor.includes(`${limites[1]} <= n <= ${limites[2]}`)) d.push("meses");
  for (const c of codigos) if (!web.includes(`"${c}"`)) d.push(`código ${c} (web)`); else if (!servidor.includes(c) && !["password_required", "pro_required"].includes(c)) d.push(`código ${c} (servidor)`);
  return d;
}
const CODIGOS = ["reserva_ja_existe", "meta_nao_encontrada", "pro_required", "password_required"];
const usados = [...app.matchAll(/code === "(\w+)"/g)].map((m) => m[1]!);
it("campos, chaves de erro, nome da reserva, limites e códigos de erro são os do web e do servidor", () => {
  expect(divergencias(CAMPOS_META, ERRO_CAMPO, NOME_RESERVA, [NOME_MAX, MESES_MIN, MESES_MAX], CODIGOS)).toEqual([]);
  expect(usados.sort()).toEqual([...CODIGOS].sort()); // o app trata exatamente os códigos que o web trata
});
it("controle negativo: o comparador acusa campo trocado, chave a mais, nome diferente, limite e código desviados", () => {
  expect(divergencias([...CAMPOS_META].reverse(), ERRO_CAMPO, NOME_RESERVA, [NOME_MAX, MESES_MIN, MESES_MAX], CODIGOS)).toContain("CAMPOS_META");
  expect(divergencias(CAMPOS_META, { ...ERRO_CAMPO, extra: "x" }, NOME_RESERVA, [NOME_MAX, MESES_MIN, MESES_MAX], CODIGOS)).toContain("chaves de ERRO_CAMPO");
  expect(divergencias(CAMPOS_META, ERRO_CAMPO, "Reserva", [NOME_MAX, MESES_MIN, MESES_MAX], CODIGOS)).toContain("NOME_RESERVA");
  expect(divergencias(CAMPOS_META, ERRO_CAMPO, NOME_RESERVA, [80, 1, 36], CODIGOS)).toEqual(expect.arrayContaining(["NOME_MAX", "meses"]));
  expect(divergencias(CAMPOS_META, ERRO_CAMPO, NOME_RESERVA, [NOME_MAX, MESES_MIN, MESES_MAX], [...CODIGOS, "codigo_novo"])).toContain("código codigo_novo (web)");
});
