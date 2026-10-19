/// <reference types="node" />
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { telefoneBR } from "@/features/conta/contato";
// `contato.ts` copia `telefoneBR` de `webapp/src/dashboard/conta/api.ts` (§0.7): o app não importa o web, então este teste
// extrai a função do fonte do web, executa-a e compara com a cópia numa tabela.
const web = readFileSync(resolve(__dirname, "../../../webapp/src/dashboard/conta/api.ts"), "utf8");
const trecho = /export function telefoneBR\(valor: string \| null\) \{[\s\S]*?\n\}/.exec(web)![0]!;
const doWeb = new Function(`${trecho.replace("export function telefoneBR(valor: string | null)", "function telefoneBR(valor)")}; return telefoneBR;`)() as (v: string | null) => string;

const TABELA: (string | null)[] = ["1198765432", "11987654321", "+5511987654321", "5511987654321", "551198765432", "(11) 98765-4321", "abc", "123", "", null, "+1 415 555 0100"];

/** O que a cópia do app diz de diferente do web (lista vazia = paridade); a cópia entra como parâmetro para o controle negativo poder mutá-la. */
const divergencias = (copia: (v: string | null) => string) => TABELA.filter((v) => copia(v) !== doWeb(v));

it("telefoneBR do app dá o mesmo que o do web em 10, 11 e 13 dígitos, lixo, vazio e nulo", () => {
  expect(doWeb("11987654321")).toBe("(11) 98765-4321"); // âncora: o extrator pegou a função certa
  expect(divergencias(telefoneBR)).toEqual([]);
});

it("controle negativo: o comparador acusa uma cópia que mudou", () => {
  expect(divergencias((v) => (v === null ? "x" : telefoneBR(v)))).toEqual([null]);
  expect(divergencias((v) => telefoneBR(v).replace("-", " "))).toEqual(expect.arrayContaining(["11987654321", "1198765432"]));
});
