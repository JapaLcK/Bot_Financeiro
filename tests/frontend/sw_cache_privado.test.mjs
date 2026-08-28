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

// ── A limpeza no logout ──────────────────────────────────────────────────
//
// Ela mora na PÁGINA, não no worker: o aparelho que tem cache privado é o
// controlado por um worker ANTIGO, que não escuta `message` — o postMessage
// cairia no vazio exatamente quando importa (Codex, #170).
//
// E mora em DOIS arquivos: `auth-refresh.js` (dashboard, home, settings,
// comecar) e `nav-auth.js` (as 12 páginas públicas, que NÃO carregam o
// auth-refresh). Os dois são dirigidos aqui — é o teste que compara a
// duplicação inevitável (§0.7).

/** Roda um JS de página num contexto falso e devolve o que ele apagou. */
function paginaComCache(arquivo, prepara) {
  const apagados = [];
  const ctx = {
    console,
    URL, Promise, Set, Date, JSON,
    caches: {
      keys: async () => ["pigbank-v8", "pigbank-v9"],
      delete: async (k) => { apagados.push(k); return true; },
    },
    localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
    location: { origin: ORIGEM, pathname: "/", href: ORIGEM, reload: () => {}, replace: () => {} },
    navigator: { serviceWorker: { controller: null } },
    document: {
      cookie: "", readyState: "complete",
      addEventListener: () => {}, querySelector: () => null, querySelectorAll: () => [],
      createElement: () => ({ style: {}, classList: { add() {}, remove() {} }, appendChild() {} }),
    },
    setTimeout, clearTimeout, fetch: async () => ({ ok: true, status: 200 }),
    addEventListener: () => {},
  };
  ctx.window = ctx;
  ctx.self = ctx;
  vm.createContext(ctx);
  if (prepara) prepara(ctx);
  vm.runInContext(readFileSync(join(dirname(fileURLToPath(import.meta.url)), "..", "..", "frontend", arquivo), "utf-8"), ctx);
  return { ctx, apagados };
}

test("auth-refresh apaga o Cache Storage num logout bem-sucedido", async () => {
  const { ctx, apagados } = paginaComCache("auth-refresh.js");

  await ctx.fetch("/auth/logout", { method: "POST" });
  await new Promise((r) => setTimeout(r, 0));

  assert.deepEqual(apagados.sort(), ["pigbank-v8", "pigbank-v9"],
                   "o logout nao limpou o cache do aparelho");
});

test("auth-refresh nao apaga nada em request comum nem em logout que falhou", async () => {
  const { ctx, apagados } = paginaComCache("auth-refresh.js");
  ctx.fetch = ctx.window.fetch;

  await ctx.window.fetch("/data/42");
  await new Promise((r) => setTimeout(r, 0));
  assert.deepEqual(apagados, [], "apagou o cache numa request qualquer");
});

test("nav-auth tambem limpa — as 12 paginas publicas nao carregam o auth-refresh", () => {
  // Comparação da duplicação (§0.7). O `doLogout` do nav-auth é interno ao
  // IIFE e depende de DOM para ser alcançado pelo clique; o que este caso
  // prende é que ele CHAMA a limpeza — se alguém tirar a chamada de um dos
  // dois arquivos, aqui fica vermelho.
  const dir = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "frontend");
  const nav = readFileSync(join(dir, "nav-auth.js"), "utf-8");
  const refresh = readFileSync(join(dir, "auth-refresh.js"), "utf-8");

  for (const [nome, fonte] of [["nav-auth.js", nav], ["auth-refresh.js", refresh]]) {
    assert.ok(/caches\s*\.\s*keys\s*\(/.test(fonte),
              `${nome} parou de apagar o Cache Storage no logout`);
    assert.ok(/caches\s*\.\s*delete\s*\(/.test(fonte), `${nome} nao apaga nada`);
  }

  // Dentro do CORPO do doLogout, não no arquivo inteiro. Um `test(/limpa.../)`
  // sobre o arquivo casa a própria DEFINIÇÃO (`function limpaCacheNoLogout()`)
  // e fica cego à chamada sumir — medido: tirando a chamada, aquela versão
  // seguia verde. É o teste que lê o texto do arquivo e afirma que um nome
  // existe, contra o qual o CLAUDE.md §3 avisa.
  const i = nav.indexOf("function doLogout()");
  assert.ok(i > 0, "doLogout sumiu do nav-auth.js");
  const corpo = nav.slice(i, nav.indexOf("\n  }", i));
  assert.ok(/limpaCacheNoLogout\s*\(\s*\)/.test(corpo),
            "nav-auth.js define a limpeza mas o doLogout nao a chama — sair pela landing deixa o cache privado intacto");
});

test("o worker NAO tem listener de message — a limpeza e' da pagina", () => {
  // Regressão do achado do Codex: um worker antigo nao escuta `message`, entao
  // depender dele deixaria o cache privado intacto justamente no aparelho que
  // ainda nao ativou a versao nova.
  const fonte = readFileSync(SW, "utf-8");
  assert.ok(!/addEventListener\(\s*["']message["']/.test(fonte),
            "a limpeza voltou para o worker: nao alcanca aparelho com worker antigo");
});
