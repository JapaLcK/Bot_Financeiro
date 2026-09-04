// Portão de tamanho: guarda o que o `5527f13` conquistou.
//
// A regra `quality/max-lines` vem copiada de um toolkit TypeScript, onde ela se
// isentava sozinha por basename (`index`, `constants`, `types`, `dto`, `enum`,
// `vo`, `*.config.*`, `*.stories.*`) e por diretório (`generated/`, `locales/`,
// `fixtures/`, `mocks/`, `dist/`…). Aqui isso era buraco: `frontend/index.js` é
// nome plausível num repo que já tem `frontend/index.html`, e passava com 404
// linhas sem uma linha vermelha. As isenções foram encolhidas em
// `eslint-rules/utils.cjs` para a única que este repo tem de verdade — os testes.
//
// Este arquivo existe para que ninguém as traga de volta sem perceber, e para
// prender o NÚMERO: com uma sonda de 500 linhas, subir o teto para qualquer
// valor até 499 passaria despercebido.
//
// Nada é escrito em disco: `lintText` resolve a config pelo caminho virtual,
// então não sobra sonda esquecida em `frontend/` — e nem exige a rota que todo
// arquivo novo de `frontend/` precisa em `frontend/routes/static_pages.py`.
//
// Rodar:  npm run test:frontend
//         (ou só este: node --test tests/frontend/eslint_max_lines_gate.test.mjs)
import { test } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { ESLint } from "eslint";

const cwd = fileURLToPath(new URL("../..", import.meta.url));
const eslint = new ESLint({ cwd });

const LONGO = "x\n".repeat(500);
// 351 linhas de conteúdo: uma acima do teto. É o que prende o 350.
const UMA_ACIMA = "x\n".repeat(350) + "x";

async function reports(filePath, texto = LONGO) {
  const [res] = await eslint.lintText(texto, { filePath });
  return res.messages.filter((m) => m.ruleId === "quality/max-lines");
}

test("basename que o toolkit isentava reprova: as isenções não podem voltar", async () => {
  for (const nome of ["constants.js", "index.js", "types.js", "pb.config.js"]) {
    const msgs = await reports(`frontend/${nome}`);
    assert.equal(msgs.length, 1, `frontend/${nome} passou silencioso`);
    assert.equal(msgs[0].severity, 2, `frontend/${nome} não está em error`);
  }
});

test("diretório que o toolkit isentava reprova igual", async () => {
  for (const caminho of ["frontend/mocks/m.js", "frontend/fixtures/f.js"]) {
    const msgs = await reports(caminho);
    assert.equal(msgs.length, 1, `${caminho} passou silencioso`);
  }
});

test("arquivo comum de frontend/ reprova em error", async () => {
  const msgs = await reports("frontend/nome_que_ninguem_isenta.js");
  assert.equal(msgs.length, 1);
  assert.equal(msgs[0].severity, 2);
});

test("legado da lista de escape não é reportado (o portão não reprova tudo)", async () => {
  assert.deepEqual(await reports("frontend/dashboard.js"), []);
});

test("teste de frontend fica em warn, não error", async () => {
  const msgs = await reports("tests/frontend/qualquer.test.mjs");
  assert.equal(msgs.length, 1);
  assert.equal(msgs[0].severity, 1);
});

test("351 linhas já reprovam: o teto guardado é 350, não 'algum teto'", async () => {
  const msgs = await reports("frontend/nome_que_ninguem_isenta.js", UMA_ACIMA);
  assert.equal(msgs.length, 1);
  assert.equal(msgs[0].severity, 2);
});

test("350 linhas com \\n final passam limpo: o que prende a CONTAGEM", async () => {
  // Simétrico do caso acima, e o único que enxerga o off-by-one: `sourceCode.lines`
  // traz um elemento vazio depois do \n final. Sem o desconto do core-rules.cjs este
  // arquivo reprova ("351 lines | max 350") enquanto o `wc -l` diz 350 — e baixar o
  // teto de 350 também deixa de passar despercebido.
  assert.deepEqual(
    await reports("frontend/nome_que_ninguem_isenta.js", "x\n".repeat(350)),
    [],
  );
});
