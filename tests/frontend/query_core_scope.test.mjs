/**
 * `useMutation({ scope })` com duas mutations na fila: a segunda tem de rodar.
 *
 * O build do dashboard (vite 8 + rolldown, `target: safari14`) rebaixa os campos
 * privados `#x` do @tanstack/query-core e, no `MutationCache.runNext`, emitia
 * `D(I,this).bind(this).get(t)` — `TypeError` em runtime, engolido num `finally`:
 * a segunda mutation do mesmo scope ficava `isPaused` para sempre.
 *
 * Builda em memória, com a config REAL do dashboard, uma entrada mínima que usa o
 * query-core, e roda o resultado. Depende de `webapp/node_modules` (o CI faz `npm ci`).
 * Os dois últimos casos olham o artefato commitado: o gate do CI garante que ele é o
 * que esse build produz.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const RAIZ = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const WEBAPP = join(RAIZ, "webapp");
const { build } = createRequire(join(WEBAPP, "package.json"))("vite");

const ENTRADA = "\0scope-entrada";
const FONTE = `
import { QueryClient } from "@tanstack/query-core";
const qc = new QueryClient();
const cache = qc.getMutationCache();
const ordem = [];
let solta;
const porta = new Promise((r) => { solta = r; });
const m1 = cache.build(qc, { scope: { id: "guia" }, mutationFn: async () => { await porta; ordem.push(1); } });
const m2 = cache.build(qc, { scope: { id: "guia" }, mutationFn: async () => { ordem.push(2); } });
const p1 = m1.execute(undefined);
const p2 = m2.execute(undefined);
globalThis.__scope = (async () => {
  await Promise.resolve();
  const pausadaAntes = m2.state.isPaused;
  solta();
  await p1;
  const fim = await Promise.race([p2.then(() => "rodou"), new Promise((r) => setTimeout(() => r("presa"), 300))]);
  return { pausadaAntes, fim, ordem };
})();
`;

async function buildaEntrada() {
  const saida = await build({
    root: WEBAPP,
    configFile: join(WEBAPP, "vite.dashboard.config.ts"),
    logLevel: "silent",
    plugins: [{
      name: "scope-entrada",
      resolveId: (id) => (id === ENTRADA ? ENTRADA : null),
      load: (id) => (id === ENTRADA ? FONTE : null),
    }],
    build: { write: false, minify: true, rollupOptions: { input: ENTRADA } },
  });
  const arquivos = (Array.isArray(saida) ? saida : [saida]).flatMap((s) => s.output);
  return arquivos.find((o) => o.type === "chunk").code;
}

test("scope: a 2ª mutation da fila roda quando a 1ª termina", async () => {
  vm.runInThisContext(await buildaEntrada());
  const r = await globalThis.__scope;
  assert.equal(r.pausadaAntes, true, "a 2ª deveria esperar a 1ª (senão o teste não exerce a fila)");
  assert.equal(r.fim, "rodou", "a 2ª ficou isPaused para sempre");
  assert.deepEqual(r.ordem, [1, 2]);
});

test("artefato commitado do dashboard não tem o runNext quebrado", () => {
  const js = readFileSync(join(RAIZ, "frontend", "dashboard-app.js"), "utf8");
  assert.match(js, /runNext\(/, "o dashboard deixou de embutir o MutationCache: revise este teste");
  assert.ok(!/\)\.bind\(this\)\.get\(/.test(js), "runNext emitido com .bind(this) depois do helper de campo privado");
});
