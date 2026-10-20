// O app JÁ INSTALADO não atualiza com o deploy: a resposta nova da /previsao (com `estimativa_variavel` e
// `pior_dia.despesa_variavel`) tem de continuar passando pelo `previsaoSchema` de hoje, sem perder o que ele lê.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { previsaoSchema } from "@/api/schemas/painel";

const PREV = JSON.parse(readFileSync(resolve(__dirname, "../../../tests/frontend/api_v2_respostas.json"), "utf8")).previsao as Record<string, any>;

it.each(Object.keys(PREV))("a fixture '%s' passa pelo schema atual do app", (nome) => {
  expect(previsaoSchema.safeParse(PREV[nome]).success).toBe(true);
});

it("com a estimativa o app atual lê os mesmos campos de sempre e ignora o novo", () => {
  const r = previsaoSchema.parse(PREV.plus30_com_estimativa);
  expect(r.marcos[0]!.saldo).toBe(-9267);
  expect(r.cobertura.inclui_estimativa_variavel).toBe(true);
  expect("estimativa_variavel" in r).toBe(false);
});
