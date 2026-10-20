// O app JÁ INSTALADO não atualiza com o deploy: a resposta da /previsao com ou sem `estimativa_variavel` e `pior_dia.despesa_variavel`
// tem de passar pelo `previsaoSchema`; o campo novo é lido em centavos e, ausente ou malformado, vira null sem derrubar o resto.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { previsaoSchema } from "@/api/schemas/painel";

const PREV = JSON.parse(readFileSync(resolve(__dirname, "../../../tests/frontend/api_v2_respostas.json"), "utf8")).previsao as Record<string, any>;

it.each(Object.keys(PREV))("a fixture '%s' passa pelo schema do app", (nome) => {
  expect(previsaoSchema.safeParse(PREV[nome]).success).toBe(true);
});

it("com a estimativa o app lê o mensal e as categorias em centavos, na ordem do servidor", () => {
  const r = previsaoSchema.parse(PREV.pro90_com_estimativa);
  expect(r.estimativa_variavel).toEqual({ despesa_mensal: 33000, categorias: [
    { chave: "mercado", nome: "Mercado", despesa_mensal: 18000 }, { chave: "transporte", nome: "Transporte", despesa_mensal: 9000 }, { chave: "lazer", nome: "Lazer", despesa_mensal: 6000 },
  ] });
  expect(r.pior_dia!.despesa_variavel).toBe(6600);
  expect(r.cobertura.inclui_estimativa_variavel).toBe(true);
});

it("sem a estimativa (null ou servidor antigo) é null, e o resto segue lido", () => {
  const { estimativa_variavel: _e, ...antigo } = PREV.plus30_com_estimativa;
  for (const corpo of [{ ...PREV.plus30_com_estimativa, estimativa_variavel: null }, antigo]) {
    const r = previsaoSchema.parse(corpo);
    expect(r.estimativa_variavel).toBeNull(); expect(r.marcos[0]!.saldo).toBe(-9267);
  }
  const { despesa_variavel: _d, ...piorAntigo } = PREV.pro90_com_estimativa.pior_dia;
  expect(previsaoSchema.parse({ ...PREV.pro90_com_estimativa, pior_dia: piorAntigo }).pior_dia!.despesa_variavel).toBeNull();
});

it("valor malformado ('1E-7') vira null só no campo: a previsão, o pior dia e as outras categorias seguem", () => {
  const ruim = structuredClone(PREV.pro90_com_estimativa);
  ruim.estimativa_variavel.despesa_mensal = "1E-7"; ruim.pior_dia.despesa_variavel = "1E-7";
  const r = previsaoSchema.safeParse(ruim);
  expect(r.success).toBe(true);
  expect(r.data!.estimativa_variavel).toBeNull(); expect(r.data!.pior_dia!.despesa_variavel).toBeNull();
  expect(r.data!.pior_dia!.causas).toHaveLength(1); expect(r.data!.marcos).toHaveLength(3);
});
