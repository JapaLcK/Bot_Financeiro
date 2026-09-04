// Portão de tamanho: o teto de 350 linhas tem de valer para TODO arquivo de
// frontend/ — inclusive os basenames que a regra copiada `quality/max-lines`
// isentava sozinha (index/constants/types/dto/enum/vo, `*.config.*`, e os
// diretórios fixtures/mocks/generated). Ver `isCheckableSourceFile` em
// eslint-rules/utils.cjs. Por isso o eslint.config.mjs usa a NATIVA `max-lines`.
//
// Nada de arquivo em disco: `lintText` resolve a config pelo caminho virtual,
// então não sobra sonda esquecida em frontend/ (e nem rota nova no backend).
import { test } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { ESLint } from "eslint";

const cwd = fileURLToPath(new URL("../..", import.meta.url));
const eslint = new ESLint({ cwd });
const LONGO = "x\n".repeat(500);
// 351 linhas: uma acima do teto. É o que prende o número 350 — com a sonda de
// 500 linhas, subir o teto para qualquer valor até 499 passava despercebido.
const UMA_ACIMA = "x\n".repeat(350) + "x";

async function maxLinesReports(filePath, texto = LONGO) {
  const [res] = await eslint.lintText(texto, { filePath });
  return res.messages.filter((m) => m.ruleId === "max-lines");
}

test("basename isento pela regra copiada continua reprovando: era o furo", async () => {
  const msgs = await maxLinesReports("frontend/constants.js");
  assert.equal(msgs.length, 1);
  assert.equal(msgs[0].severity, 2);
});

test("arquivo comum de frontend/ reprova em error", async () => {
  const msgs = await maxLinesReports("frontend/nome_que_ninguem_isenta.js");
  assert.equal(msgs.length, 1);
  assert.equal(msgs[0].severity, 2);
});

test("legado da lista de escape não é reportado (o portão não reprova tudo)", async () => {
  assert.deepEqual(await maxLinesReports("frontend/dashboard.js"), []);
});

test("teste de frontend fica em warn, não error", async () => {
  const msgs = await maxLinesReports("tests/frontend/qualquer.test.mjs");
  assert.equal(msgs.length, 1);
  assert.equal(msgs[0].severity, 1);
});

test("351 linhas já reprovam: o teto guardado é 350, não 'algum teto'", async () => {
  const msgs = await maxLinesReports(
    "frontend/nome_que_ninguem_isenta.js",
    UMA_ACIMA,
  );
  assert.equal(msgs.length, 1);
  assert.equal(msgs[0].severity, 2);
});
