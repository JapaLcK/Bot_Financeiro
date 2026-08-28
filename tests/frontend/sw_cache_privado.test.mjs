/**
 * Porteiro do cache do service worker: QUEM pode entrar no Cache Storage.
 *
 * UM invariante: só asset estático de mesma origem (mais o Chart.js do cdnjs,
 * que é público). Resposta autenticada NUNCA entra.
 *
 * Antes isto era uma blocklist de 15 prefixos, e blocklist falha ABERTO —
 * escapavam ~20 rotas GET autenticadas, todas passando por
 * `authorize_dashboard_access`: o histórico do chat da IA, o `balance` da
 * conta, os 7 endpoints de analytics (incluindo onde a pessoa gasta), cartões,
 * faturas, metas, recorrentes, previsão e comissões de afiliado. Iam para o
 * disco do aparelho indexadas por uma URL que carrega o `user_id`, e o ramo de
 * fallback as servia sempre que a rede falhasse.
 *
 * O teste enumera essas rotas por NOME. Uma allowlist frouxa demais (por
 * exemplo, aceitar `destination` vazio) fica vermelha aqui.
 *
 * Rodar:  npm run test:frontend
 *         (ou, um arquivo só: node --test tests/frontend/sw_cache_privado.test.mjs
 *          — `node --test tests/frontend/` NÃO funciona no Node 24 do Windows:
 *          ele trata o diretório como módulo e sai com MODULE_NOT_FOUND.)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import vm from "node:vm";

const SW = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "frontend", "service-worker.js");
const ORIGEM = "https://pigbankai.com";

/**
 * Roda o service-worker.js num contexto falso e devolve o que precisamos:
 * o `podeCachear`, os handlers registrados e o CACHE_NAME.
 *
 * O arquivo é carregado inteiro — no load ele só declara e registra
 * listeners, então nada dispara sozinho.
 */
function carregaSW() {
  const handlers = {};
  const ctx = {
    self: {
      location: { origin: ORIGEM },
      addEventListener: (nome, fn) => { handlers[nome] = fn; },
      skipWaiting: () => {},
      clients: { claim: () => {} },
      registration: { showNotification: () => {} },
    },
    caches: { open: async () => ({ addAll: async () => {}, put: async () => {} }),
              keys: async () => [], delete: async () => true, match: async () => null },
    fetch: async () => ({ ok: true, clone: () => ({}) }),
    URL,
    Response: class { constructor(b, i) { Object.assign(this, i || {}); } },
    Set,
    console,
  };
  ctx.self.self = ctx.self;
  vm.createContext(ctx);
  vm.runInContext(readFileSync(SW, "utf-8"), ctx);
  return { ctx, handlers };
}

const req = (destination = "") => ({ method: "GET", mode: "cors", destination });

/** `podeCachear` para um caminho da própria origem. */
function cacheia(caminho, destination = "") {
  const { ctx } = carregaSW();
  return ctx.podeCachear(req(destination), new URL(caminho, ORIGEM));
}

// As rotas GET autenticadas levantadas no inventário. Todas devolvem dado
// financeiro ou pessoal, e NENHUMA pode ser gravada no aparelho.
const ROTAS_PRIVADAS = [
  "/ai/messages",
  "/account/42/setup-status",
  "/analytics/42/kpis",
  "/analytics/42/evolution",
  "/analytics/42/categories",
  "/analytics/42/weekday-pattern",
  "/analytics/42/top-merchants",
  "/analytics/42/patterns",
  "/insights/42/current",
  "/cards/42/summary",
  "/bills/42",
  "/bills/42/7",
  "/installments/42/list",
  "/goals/42/status",
  "/pockets/42/viagem/history",
  "/recurring-bills/42",
  "/recurring-bills/42/projection",
  "/recurring-expenses/42",
  "/recurring-incomes/42",
  "/expenses/daily/42",
  "/forecast/42",
  "/categories/42",
  "/billing/subscription",
  "/api/affiliate/me",
  "/debug/ai/42/payload",
  // As que a blocklist antiga já cobria — continuam fora, agora por regra.
  "/data/42",
  "/history/42",
  "/budgets/42",
  "/open-finance/42",
  "/agents/42",
  "/auth/me",
];

test("nenhuma rota autenticada entra no cache", () => {
  for (const rota of ROTAS_PRIVADAS) {
    assert.equal(cacheia(rota), false, `${rota} foi para o cache do aparelho`);
  }
});

test("rota autenticada continua fora mesmo com querystring", () => {
  assert.equal(cacheia("/categories/42?include_archived=true"), false);
  assert.equal(cacheia("/history/42?mes=2026-08"), false);
});

test("asset estático continua sendo cacheado", () => {
  for (const asset of ["/dashboard.js", "/dashboard.css", "/brand/icon-192.png",
                       "/phosphor.css", "/manifest.json", "/fonts/inter.woff2",
                       "/favicon.png", "/modals.js"]) {
    assert.equal(cacheia(asset), true, `${asset} deixou de ser cacheado`);
  }
});

test("app-mode volta a ser cacheável — o prefixo /app da lista antiga o pegava por acidente", () => {
  assert.equal(cacheia("/app-mode.css"), true);
  assert.equal(cacheia("/app-mode.js"), true);
});

test("o Chart.js do cdnjs é público e continua cacheado", () => {
  const { ctx } = carregaSW();
  const url = new URL("https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js");
  assert.equal(ctx.podeCachear(req("script"), url), true);
});

test("outra origem qualquer não entra", () => {
  const { ctx } = carregaSW();
  for (const u of ["https://api.stripe.com/v1/x", "https://cdn.pluggy.ai/connect.js"]) {
    assert.equal(ctx.podeCachear(req("script"), new URL(u)), false, u);
  }
});

test("asset sem extensão entra pelo destination declarado pelo navegador", () => {
  assert.equal(cacheia("/brand/logo", "image"), true);
  // ...e uma rota de API com destination vazio (o que um fetch() produz) não.
  assert.equal(cacheia("/analytics/42/kpis", ""), false);
});

test("o CDN saiu do PRECACHE — addAll rejeita inteiro se um item falhar", () => {
  const fonte = readFileSync(SW, "utf-8");
  const bloco = fonte.slice(fonte.indexOf("const PRECACHE"), fonte.indexOf("]", fonte.indexOf("const PRECACHE")));
  assert.ok(!bloco.includes("cdnjs"), "o CDN voltou pro PRECACHE: a queda dele impede a instalação do worker");
});

test("CACHE_NAME subiu — é o que apaga do aparelho o que a versão anterior guardou", () => {
  // Lido do FONTE: `const` no `vm.runInContext` fica no escopo léxico e não
  // vira propriedade do contexto (a `function podeCachear` vira, por ser
  // declaração de função). Sem o bump, a allowlist só impede gravação nova e o
  // dado privado que o v8 guardou continua no aparelho.
  const fonte = readFileSync(SW, "utf-8");
  const m = fonte.match(/const CACHE_NAME = "(pigbank-v\d+)"/);
  assert.ok(m, "CACHE_NAME sumiu ou mudou de forma");
  assert.ok(Number(m[1].replace("pigbank-v", "")) >= 9,
            `CACHE_NAME precisa passar de v8 para apagar o cache antigo, veio ${m[1]}`);
});

test("o activate apaga todo cache de nome diferente", async () => {
  const apagados = [];
  const { ctx, handlers } = carregaSW();
  ctx.caches.keys = async () => ["pigbank-v7", "pigbank-v8", "pigbank-v9"];
  ctx.caches.delete = async (k) => { apagados.push(k); return true; };

  let pendente;
  await handlers.activate({ waitUntil: (p) => { pendente = p; } });
  await pendente;

  assert.deepEqual(apagados.sort(), ["pigbank-v7", "pigbank-v8"]);
});

test("a mensagem de logout apaga o Cache Storage inteiro", async () => {
  const apagados = [];
  const { ctx, handlers } = carregaSW();
  ctx.caches.keys = async () => ["pigbank-v9"];
  ctx.caches.delete = async (k) => { apagados.push(k); return true; };

  let pendente;
  await handlers.message({ data: { type: "pb-logout" }, waitUntil: (p) => { pendente = p; } });
  await pendente;

  assert.deepEqual(apagados, ["pigbank-v9"]);
});

test("mensagem de outro tipo não apaga nada", async () => {
  const apagados = [];
  const { ctx, handlers } = carregaSW();
  ctx.caches.delete = async (k) => { apagados.push(k); return true; };

  await handlers.message({ data: { type: "outra-coisa" }, waitUntil: () => {} });
  await handlers.message({ data: null, waitUntil: () => {} });

  assert.deepEqual(apagados, []);
});
