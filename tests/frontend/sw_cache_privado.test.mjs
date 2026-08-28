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
    navigator: { serviceWorker: { controller: null, getRegistrations: async () => [] } },
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

test("auth-refresh AGUARDA o delete antes de devolver a resposta do logout", async () => {
  // Ordem, não só efeito. Quem chama o logout navega assim que o fetch resolve
  // (`location.replace`/`reload`), e navegação descarta o documento: uma
  // limpeza disparada e esquecida nao tem garantia de terminar, e o cache
  // privado sobrevive ao logout no aparelho compartilhado (Codex, #170).
  let liberaDelete;
  const bloqueado = new Promise((r) => { liberaDelete = r; });
  const apagados = [];

  const { ctx } = paginaComCache("auth-refresh.js", (c) => {
    c.caches.delete = async (k) => { await bloqueado; apagados.push(k); return true; };
  });

  let resolveu = false;
  const chamada = ctx.fetch("/auth/logout", { method: "POST" }).then(() => { resolveu = true; });

  await new Promise((r) => setTimeout(r, 10));
  assert.equal(resolveu, false,
               "o fetch do logout resolveu antes do delete: o chamador navega e a limpeza morre no meio");
  assert.deepEqual(apagados, [], "nada deveria ter sido apagado ainda");

  liberaDelete();
  await chamada;

  assert.equal(resolveu, true);
  assert.deepEqual(apagados.sort(), ["pigbank-v8", "pigbank-v9"]);
});

// ── O estado do DISPOSITIVO no fim de sessão ─────────────────────────────
//
// Enumeração, não remendo. Os quatro logouts limpavam subconjuntos diferentes
// e nenhum limpava tudo — sair pelo Ajustes não limpava nada:
//
//                   pigbank_menu_v1   pb_snap_*   pb_home_*
//   dashboard.js         limpa          limpa       DEIXA
//   home.html            DEIXA          limpa       limpa
//   settings.html        DEIXA          DEIXA       DEIXA
//   nav-auth.js          DEIXA          DEIXA       DEIXA
//
// `pigbank_menu_v1` guarda e-mail, nome e plano; `pb_home_<uid>` guarda
// snapshot, histórico, e-mail e nome.
//
// A regra é lista do que PRESERVAR, não do que apagar: chave nova derivada de
// conta é apagada por default (falha fechada, como a allowlist do worker).

const DERIVADO_DE_CONTA = {
  "pigbank_menu_v1": '{"email":"a@b.c","displayName":"Fulano","plan":"pro"}',
  "pb_home_42": '{"snapshot":{},"email":"a@b.c"}',
  "pb_snap_42_2026_08": '{"total":1234}',
  "pbNewsTab": "1",
  "_pigInsightsDismissed": "3",
  "pigbank_trial_banner_snooze": "1756400000000",
};
const PREFERENCIA_DO_APARELHO = {
  "pigbank_theme": "dark",
  "pigbank_hide_balance": "1",
  "pbFabPos": '{"side":"right","top":300}',
  "finbot_logout_at": "1756400000000",
};

function comStorageCheio(arquivo) {
  const local = new Map(Object.entries({ ...DERIVADO_DE_CONTA, ...PREFERENCIA_DO_APARELHO }));
  const sessao = new Map(Object.entries({ "pb_home_42": "x", "pb_snap_42_2026_08": "y", "pbSpa": "1" }));
  // Proxy, não snapshot: `Object.keys(storage)` tem que refletir o Map VIVO,
  // como o localStorage de verdade. Com `Object.fromEntries` as chaves ficam
  // congeladas na criação e o caso da chave nova fica cego — medido: ele
  // passava sem medir nada.
  const API_STORAGE = (m) => ({
    getItem: (key) => (m.has(key) ? m.get(key) : null),
    setItem: (key, v) => m.set(key, String(v)),
    removeItem: (key) => m.delete(key),
    key: (i) => [...m.keys()][i],
  });
  const mapa = (m) => new Proxy({}, {
    get: (_, k) => (k === "length" ? m.size : (API_STORAGE(m)[k] ?? m.get(k))),
    has: (_, k) => m.has(k),
    ownKeys: () => [...m.keys()],
    getOwnPropertyDescriptor: () => ({ enumerable: true, configurable: true }),
  });
  const { ctx } = paginaComCache(arquivo, (c) => {
    c.localStorage = mapa(local);
    c.sessionStorage = mapa(sessao);
  });
  return { ctx, local, sessao };
}

test("fim de sessao apaga TUDO que e' derivado de conta", async () => {
  const { ctx, local, sessao } = comStorageCheio("auth-refresh.js");

  await ctx.fetch("/auth/logout", { method: "POST" });
  await new Promise((r) => setTimeout(r, 0));

  for (const k of Object.keys(DERIVADO_DE_CONTA)) {
    assert.equal(local.has(k), false, `${k} sobreviveu ao logout — e' derivado de conta`);
  }
  assert.equal(sessao.has("pb_home_42"), false, "pb_home_ sobreviveu na sessao");
  assert.equal(sessao.has("pb_snap_42_2026_08"), false, "pb_snap_ sobreviveu na sessao");
});

test("preferencia do aparelho SOBREVIVE — apagar o tema seria hostil", async () => {
  const { ctx, local, sessao } = comStorageCheio("auth-refresh.js");

  await ctx.fetch("/auth/logout", { method: "POST" });
  await new Promise((r) => setTimeout(r, 0));

  for (const k of Object.keys(PREFERENCIA_DO_APARELHO)) {
    assert.equal(local.get(k), PREFERENCIA_DO_APARELHO[k],
                 `${k} foi apagado — e' preferencia do aparelho, nao da conta`);
  }
  // `finbot_logout_at` e' MECANISMO: outras abas escutam o storage event dele
  // para se deslogarem juntas. Apaga-lo quebraria o logout entre abas.
  assert.equal(local.has("finbot_logout_at"), true);
  assert.equal(sessao.has("pbSpa"), true);
});

test("chave NOVA derivada de conta e' apagada por default (falha fechada)", async () => {
  const { ctx, local } = comStorageCheio("auth-refresh.js");
  local.set("pigbank_alguma_coisa_nova_v2", "dado do usuario");

  await ctx.fetch("/auth/logout", { method: "POST" });
  await new Promise((r) => setTimeout(r, 0));

  assert.equal(local.has("pigbank_alguma_coisa_nova_v2"), false,
               "chave nao listada sobreviveu: a regra virou lista do que apagar e falha aberta");
});

test("desregistra o worker ANTES de apagar, nao depois", async () => {
  // Ordem. Um worker ANTIGO ainda no controle tem `cache.put` assincrono no
  // handler de fetch dele: uma request em voo noutra aba recriava o cache
  // DEPOIS do delete (Codex, #170). Desregistrar primeiro nao mata o worker
  // que ja controla os clientes abertos, mas garante que nenhum load futuro
  // pegue o velho.
  const ordem = [];
  let liberaUnregister;
  const bloqueado = new Promise((r) => { liberaUnregister = r; });

  const { ctx } = paginaComCache("auth-refresh.js", (c) => {
    c.navigator.serviceWorker.getRegistrations = async () => [{
      unregister: async () => { await bloqueado; ordem.push("unregister"); return true; },
    }];
    c.caches.delete = async (k) => { ordem.push("delete:" + k); return true; };
  });

  const chamada = ctx.fetch("/auth/logout", { method: "POST" });
  await new Promise((r) => setTimeout(r, 10));

  assert.deepEqual(ordem, [], "apagou o cache antes de desregistrar o worker");

  liberaUnregister();
  await chamada;

  assert.equal(ordem[0], "unregister", `esperado unregister primeiro, veio ${ordem[0]}`);
  assert.ok(ordem.slice(1).every((o) => o.startsWith("delete:")), ordem);
});

test("sem service worker no navegador, a limpeza segue e apaga o cache", async () => {
  // Controle: `getRegistrations` ausente (Safari antigo, contexto inseguro) nao
  // pode abortar a limpeza inteira.
  const apagados = [];
  const { ctx } = paginaComCache("auth-refresh.js", (c) => {
    c.navigator.serviceWorker = undefined;
    c.caches.delete = async (k) => { apagados.push(k); return true; };
  });

  await ctx.fetch("/auth/logout", { method: "POST" });

  assert.deepEqual(apagados.sort(), ["pigbank-v8", "pigbank-v9"],
                   "sem SW a limpeza de cache foi abortada junto");
});

test("nav-auth preserva a mesma lista que o auth-refresh", () => {
  // §0.7: duplicacao inevitavel entre publico e logado, entao um teste compara.
  const dir = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "frontend");
  const lista = (fonte, marca) => {
    const i = fonte.indexOf(marca);
    assert.ok(i > 0, `${marca} sumiu`);
    const bloco = fonte.slice(i, fonte.indexOf("]", i));
    return new Set(bloco.match(/"([^"]+)"/g).map((s) => s.slice(1, -1)));
  };
  const a = lista(readFileSync(join(dir, "auth-refresh.js"), "utf-8"), "_PRESERVA = new Set([");
  const b = lista(readFileSync(join(dir, "nav-auth.js"), "utf-8"), "PRESERVA = [");
  assert.deepEqual([...a].sort(), [...b].sort(),
                   "as duas listas divergiram: um logout preserva o que o outro apaga");
});

// Os dois caminhos INTERNOS do interceptor. Ele produz resposta em tres
// pontos (request inicial, refresh interno do _doRefresh, retry pos-refresh) e
// a limpeza rodava so' no primeiro: as respostas internas saiam por baixo dela
// (Codex, #170). Nenhum dos casos acima os alcanca.

/** fetch falso que responde por caminho, e conta as chamadas. */
function fetchPorRota(mapa) {
  const chamadas = [];
  return {
    chamadas,
    fn: async (input, init) => {
      const url = typeof input === "string" ? input : (input && input.url) || "";
      const caminho = new URL(url, ORIGEM).pathname;
      chamadas.push({ caminho, method: (init && init.method) || "GET" });
      const r = mapa[caminho];
      const status = typeof r === "function" ? r(chamadas) : r;
      return { ok: status >= 200 && status < 300, status };
    },
  };
}

test("refresh interno que falha (401) limpa — deslogue involuntario", async () => {
  const apagados = [];
  const falso = fetchPorRota({ "/data/42": 401, "/auth/refresh": 401 });
  const { ctx } = paginaComCache("auth-refresh.js", (c) => {
    c.fetch = falso.fn;
    c.caches.delete = async (k) => { apagados.push(k); return true; };
  });

  const resp = await ctx.window.fetch("/data/42");

  assert.ok(falso.chamadas.some((c) => c.caminho === "/auth/refresh"),
            "o interceptor nem tentou renovar — o teste nao mede o caminho certo");
  assert.equal(resp.status, 401);
  assert.deepEqual(apagados.sort(), ["pigbank-v8", "pigbank-v9"],
                   "refresh 401 e' fim de sessao e nao limpou o cache");
});

test("retry pos-refresh que encerra a sessao limpa", async () => {
  // DELETE /auth/account leva 401, o refresh renova, o retry da' certo — e o
  // retry e' que encerra a sessao. Ele saia por baixo da limpeza.
  const apagados = [];
  let tentativas = 0;
  const falso = fetchPorRota({
    "/auth/account": () => (++tentativas === 1 ? 401 : 200),
    "/auth/refresh": 200,
  });
  const { ctx } = paginaComCache("auth-refresh.js", (c) => {
    c.fetch = falso.fn;
    c.caches.delete = async (k) => { apagados.push(k); return true; };
  });

  const resp = await ctx.window.fetch("/auth/account", { method: "DELETE" });

  assert.equal(tentativas, 2, "o retry nao aconteceu — o teste nao mede o caminho certo");
  assert.equal(resp.ok, true);
  assert.deepEqual(apagados.sort(), ["pigbank-v8", "pigbank-v9"],
                   "o retry encerrou a sessao e nao limpou o cache");
});

test("request comum que renova e da' certo NAO limpa", async () => {
  // Controle positivo do par acima: renovar sessao nao e' encerrar sessao.
  const apagados = [];
  let tentativas = 0;
  const falso = fetchPorRota({
    "/data/42": () => (++tentativas === 1 ? 401 : 200),
    "/auth/refresh": 200,
  });
  const { ctx } = paginaComCache("auth-refresh.js", (c) => {
    c.fetch = falso.fn;
    c.caches.delete = async (k) => { apagados.push(k); return true; };
  });

  const resp = await ctx.window.fetch("/data/42");

  assert.equal(tentativas, 2);
  assert.equal(resp.ok, true);
  assert.deepEqual(apagados, [], "apagou o cache num refresh bem-sucedido");
});

test("nav-auth so' recarrega DEPOIS de apagar", async () => {
  // Mesmo invariante do caso acima, no outro dono. Aqui o `doLogout` nao e'
  // exposto pelo IIFE, entao a ordem e' prendida na FORMA: o `location.reload`
  // tem que estar DENTRO da continuacao da limpeza, nao ao lado da chamada.
  const dir = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "frontend");
  const nav = readFileSync(join(dir, "nav-auth.js"), "utf-8");
  const i = nav.indexOf("function doLogout()");
  const corpo = nav.slice(i, nav.indexOf("\n  }", i));

  const chamada = corpo.indexOf("limpaCacheNoLogout()");
  const reload = corpo.indexOf("location.reload()");
  assert.ok(chamada > 0 && reload > 0, "doLogout perdeu a limpeza ou o reload");
  assert.ok(/limpaCacheNoLogout\(\)\s*\.then\(/.test(corpo),
            "o reload nao espera a limpeza: navegacao descarta o documento e o delete morre no meio");
  assert.ok(chamada < reload, "a limpeza tem que vir antes do reload");
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
