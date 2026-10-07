/// <reference types="node" />
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { decimalParaCentavos, numeroLegadoParaCentavos, somarCentavos, percentualEmCentavos } from "@/features/painel/decimal";
import { PERFIS, PADRAO, sanitizarLayout, chaveLayout } from "@/features/painel/catalogo";
import * as S from "@/api/schemas/painel";
import fixture from "./painel.fixture.json";
it.each([["10.005", 1001], ["-10.005", -1001], ["0.004999", 0], ["0.005", 1], ["1.999", 200], ["12", 1200], ["0.01", 1]])("Decimal %s arredonda HALF_UP sem float", (valor, esperado) => expect(decimalParaCentavos(String(valor))).toBe(esperado));
it.each(["NaN", "Infinity", "1e3", " 1.00", "+2", "2,22", "9007199254740992.00"])("Decimal inválido %s não vira zero", (v) => expect(() => decimalParaCentavos(v)).toThrow());
it.each([[500.005, 50001], [-10.005, -1001], [1.123, 112], [1.005, 101], [1e-7, 0], [-5e-3, -1], [1.23456e3, 123456], [12.3, 1230]])("number legado %s quantiza HALF_UP, inclusive notação científica", (valor, esperado) => expect(numeroLegadoParaCentavos(valor)).toBe(esperado));
it.each([Infinity, -Infinity, NaN, 1e21, Number.MAX_VALUE, Number.MAX_SAFE_INTEGER])("number legado inválido ou fora do limite %s é recusado", (valor) => expect(() => numeroLegadoParaCentavos(valor)).toThrow());
it("total de centavos que não cabe continua recusado", () => expect(() => somarCentavos([Number.MAX_SAFE_INTEGER, 1])).toThrow());
it("presets preservam paridade com a fonte oficial web", () => {
 const web = readFileSync(resolve(__dirname, "../../../webapp/src/dashboard/lib/profiles.js"), "utf8");
 for (const p of PERFIS.filter((p) => p.id !== "padrao")) { const bloco = web.split(`id: "${p.id}"`)[1]!.split("preset: [")[1]!.split("]")[0]!; expect(bloco.match(/"([a-z]+)"/g)!.map((s) => s.slice(1,-1))).toEqual(p.preset); }
 const board = readFileSync(resolve(__dirname, "../../../webapp/src/dashboard/parts/Board.tsx"), "utf8"); const bloco = board.split("const DEFAULT:")[1]!.split("const EXTRA:")[0]!; expect([...bloco.matchAll(/id: "([a-z]+)"/g)].map((m) => m[1])).toEqual(PADRAO);
});
it("organização aceita todos ocultos, remove IDs desconhecidos/repetidos e isola usuário/perfil", () => { expect(sanitizarLayout([], "padrao")).toEqual([]); expect(sanitizarLayout(["contas", "bogus", "contas", "metas"], "padrao")).toEqual(["contas", "metas"]); expect(chaveLayout(1, "padrao")).not.toBe(chaveLayout(2, "padrao")); expect(chaveLayout(1, "padrao")).not.toBe(chaveLayout(1, "investir")); });
it("contratos reais transformam todos os valores monetários na fronteira", () => { expect(S.contasSchema.parse(fixture["/api/app/contas"]).total).toBe(940025); expect(S.detalhesMesSchema.parse(fixture["/api/app/mes-detalhes"]).guardado.liquido).toBe(120000); expect(S.previsaoSchema.parse(fixture["/api/app/previsao"]).base.saldo).toBe(940025); expect(S.patrimonioSchema.parse(fixture["/api/app/patrimonio"]).total).toBe(2600025); expect(S.rendimentoSchema.parse(fixture["/api/app/rendimento"]).itens[0]!.taxa).toBe("100"); });

it("cortes 0/100 e centavo fracionário são exatos e reversíveis sem floats", () => { expect(percentualEmCentavos(1001, 0)).toBe(0); expect(percentualEmCentavos(1001, 100)).toBe(1001); expect(percentualEmCentavos(5, 10)).toBe(1); expect(percentualEmCentavos(Number.MAX_SAFE_INTEGER, 100)).toBe(Number.MAX_SAFE_INTEGER); });
