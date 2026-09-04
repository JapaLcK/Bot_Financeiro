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

// A versão que este arquivo assume ser o cache ATUAL. Um número só, num lugar
// só: bumpar o CACHE_NAME sem mexer aqui derruba o teste da declaração, logo
// abaixo. Antes havia um piso (`>= 9`) que precisava ser levantado à mão e
// deixava de significar "não regrediu" no bump seguinte — número fixo que
// envelhece em silêncio é o que o CLAUDE.md §2 manda não escrever.
const VERSAO_ATUAL = 9;

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

test("CACHE_NAME tem UMA declaração e é a versão que este arquivo assume", () => {
  // Lido do FONTE: `const` no `vm.runInContext` fica no escopo léxico e não
  // vira propriedade do contexto (a `function podeCachear` vira, por ser
  // declaração de função).
  //
  // ANCORADA em início de linha, e `matchAll` no lugar de `.match()`: sem as
  // duas coisas a regex casava declaração COMENTADA (`// const CACHE_NAME =
  // "pigbank-v8";`) e, como `.match()` devolve a PRIMEIRA ocorrência, uma linha
  // morta acima da viva fazia o teste ler o valor errado sem reclamar.
  //
  // A tolerância de espaçamento é a MESMA do gate de CI (`.github/workflows/
  // tests.yml`), para os dois não divergirem em silêncio. Sobra de propósito uma
  // diferença: aqui o esquema `pigbank-vN` é exigido, lá não — lá exigir esquema
  // travaria o repositório no dia em que o esquema mudasse.
  //
  // Que a versão AVANCE quem prova é aquele gate, a cada PR, contra a base de
  // verdade. Aqui só se prova que ela é UMA, legível, e a que os outros testes
  // deste arquivo assumem.
  const fonte = readFileSync(SW, "utf-8");
  const achados = [...fonte.matchAll(/^[ \t]*const[ \t]+CACHE_NAME[ \t]*=[ \t]*"(pigbank-v\d+)"/gm)];
  assert.equal(achados.length, 1,
               `esperava UMA declaração 'const CACHE_NAME = "pigbank-vN"' em início de linha no service-worker.js, achei ${achados.length}`);
  assert.equal(achados[0][1], `pigbank-v${VERSAO_ATUAL}`,
               `o CACHE_NAME é ${achados[0][1]} e este arquivo assume pigbank-v${VERSAO_ATUAL}: bumpou o worker, atualize o VERSAO_ATUAL junto`);
});

test("o activate apaga todo cache de nome diferente", async () => {
  const apagados = [];
  const { ctx, handlers } = carregaSW();
  // Os nomes antigos saem do VERSAO_ATUAL, não fixos: com "pigbank-v7" e
  // "pigbank-v8" escritos à mão ao lado de um atual derivado, VERSAO_ATUAL em 7
  // ou 8 COLIDIA — o cache atual aparecia duas vezes na lista, o activate não o
  // apagava, e o teste ficava vermelho sem ter nada a ver com o que ele mede.
  const ANTIGOS = [`pigbank-v${VERSAO_ATUAL - 2}`, `pigbank-v${VERSAO_ATUAL - 1}`];
  ctx.caches.keys = async () => [...ANTIGOS, `pigbank-v${VERSAO_ATUAL}`];
  ctx.caches.delete = async (k) => { apagados.push(k); return true; };

  let pendente;
  await handlers.activate({ waitUntil: (p) => { pendente = p; } });
  await pendente;

  assert.deepEqual(apagados.sort(), [...ANTIGOS].sort());
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
    // `host` nao e' decoracao: `_isOwnApi` compara POR ELE. Sem o campo, toda
    // URL absoluta batia contra `undefined` e o interceptor tratava a PROPRIA
    // origem como terceiro — o harness ficava cego a metade do `_isOwnApi`.
    location: { origin: ORIGEM, host: new URL(ORIGEM).host, pathname: "/", href: ORIGEM,
                reload: () => {}, replace: () => {} },
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
  const { ctx, apagados } = paginaComCache("static/auth-refresh.js");

  await ctx.fetch("/auth/logout", { method: "POST" });
  await new Promise((r) => setTimeout(r, 0));

  assert.deepEqual(apagados.sort(), ["pigbank-v8", "pigbank-v9"],
                   "o logout nao limpou o cache do aparelho");
});

test("auth-refresh nao apaga nada numa request comum", async () => {
  // O titulo antigo prometia "nem em logout que falhou" e o corpo so' chamava
  // `/data/42` — nunca houve logout aqui. O logout que falha tem os seus casos
  // proprios no fim do arquivo, e o comportamento e' o OPOSTO: ele limpa.
  const { ctx, apagados } = paginaComCache("static/auth-refresh.js");
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

  const { ctx } = paginaComCache("static/auth-refresh.js", (c) => {
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
  // MECANISMO como o logout_at: carimbo do último "Recomeçar do zero". Se o
  // wipe do logout o apagasse, a cadeia reset → logout → relogin na MESMA aba
  // reabria o flash de snapshot pré-reset (a aba nunca limpou o sessionStorage
  // e o marker que o descartaria teria sumido).
  "finbot_reset_at": "1756400000001",
};

function comStorageCheio(arquivo, prepara) {
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
    if (prepara) prepara(c);
  });
  return { ctx, local, sessao };
}

test("fim de sessao apaga TUDO que e' derivado de conta", async () => {
  const { ctx, local, sessao } = comStorageCheio("static/auth-refresh.js");

  await ctx.fetch("/auth/logout", { method: "POST" });
  await new Promise((r) => setTimeout(r, 0));

  for (const k of Object.keys(DERIVADO_DE_CONTA)) {
    assert.equal(local.has(k), false, `${k} sobreviveu ao logout — e' derivado de conta`);
  }
  assert.equal(sessao.has("pb_home_42"), false, "pb_home_ sobreviveu na sessao");
  assert.equal(sessao.has("pb_snap_42_2026_08"), false, "pb_snap_ sobreviveu na sessao");
});

test("preferencia do aparelho SOBREVIVE — apagar o tema seria hostil", async () => {
  const { ctx, local, sessao } = comStorageCheio("static/auth-refresh.js");

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
  const { ctx, local } = comStorageCheio("static/auth-refresh.js");
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

  const { ctx } = paginaComCache("static/auth-refresh.js", (c) => {
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
  const { ctx } = paginaComCache("static/auth-refresh.js", (c) => {
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
  const a = lista(readFileSync(join(dir, "static", "auth-refresh.js"), "utf-8"), "_PRESERVA = new Set([");
  const b = lista(readFileSync(join(dir, "nav-auth.js"), "utf-8"), "PRESERVA = [");
  assert.deepEqual([...a].sort(), [...b].sort(),
                   "as duas listas divergiram: um logout preserva o que o outro apaga");
});

// Os dois caminhos INTERNOS do interceptor. Ele produz resposta em tres
// pontos (request inicial, refresh interno do _doRefresh, retry pos-refresh) e
// a limpeza rodava so' no primeiro: as respostas internas saiam por baixo dela
// (Codex, #170). Nenhum dos casos acima os alcanca.

/** fetch falso que responde por caminho, e conta as chamadas.
 *
 * `autenticacao` lista os caminhos cujo 401 sai com `WWW-Authenticate` — a marca
 * de FAMÍLIA que o interceptor passou a exigir para renovar (#176). 401 sem ela é
 * de aplicação (senha errada, chave inválida) e não dispara refresh nenhum.
 *
 * O `headers` não é enfeite de fidelidade: um duplo sem ele rebentava com
 * `TypeError: Cannot read properties of undefined` na linha nova do interceptor,
 * porque `Response.headers` sempre existe no navegador e aqui não existia.
 */
function fetchPorRota(mapa, autenticacao = []) {
  const chamadas = [];
  const marcados = new Set(autenticacao);
  return {
    chamadas,
    fn: async (input, init) => {
      const url = typeof input === "string" ? input : (input && input.url) || "";
      const caminho = new URL(url, ORIGEM).pathname;
      chamadas.push({ caminho, method: (init && init.method) || "GET" });
      const r = mapa[caminho];
      const status = typeof r === "function" ? r(chamadas) : r;
      const marca = status === 401 && marcados.has(caminho)
        ? { "WWW-Authenticate": 'Bearer realm="pigbank", error="invalid_token"' } : {};
      return { ok: status >= 200 && status < 300, status, headers: new Headers(marca) };
    },
  };
}

test("refresh interno que falha (401) limpa — deslogue involuntario", async () => {
  const apagados = [];
  const falso = fetchPorRota({ "/data/42": 401, "/auth/refresh": 401 }, ["/data/42"]);
  const { ctx } = paginaComCache("static/auth-refresh.js", (c) => {
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

// Os DOIS ramos de falha do /auth/refresh. O status dessa rota responde "a
// sessao acabou?", nao classifica o erro: 401 e' refresh invalidado, ou
// ausente sem access valido; 400 e' ausente com a sessao de pe. O caso acima
// cobre o 401; os tres abaixo cobrem o 400 e o teto de renovacoes (#173).

test("refresh interno que devolve 400 NAO limpa — a sessao esta' de pe", async () => {
  // O predicado do #173: 400 no /auth/refresh e' "refresh ausente com o access
  // token de pe", nao fim de sessao — trata-lo como fim apagava o aparelho.
  //
  // O GATILHO mudou com o #176 e o caso trocou de rota por isso: antes quem
  // chegava aqui era a senha errada do MFA, que hoje nao renova mais (401 de
  // aplicacao, sem WWW-Authenticate — ver `auth_refresh_gatilho.test.mjs`).
  // Quem ainda alcanca este ramo e' o 401 de AUTENTICACAO, que renova e pode
  // receber 400. O invariante medido e' o mesmo; so' a porta de entrada mudou.
  const apagados = [];
  const falso = fetchPorRota({ "/data/42": 401, "/auth/refresh": 400 }, ["/data/42"]);
  const { ctx } = paginaComCache("static/auth-refresh.js", (c) => {
    c.fetch = falso.fn;
    c.caches.delete = async (k) => { apagados.push(k); return true; };
  });

  const resp = await ctx.window.fetch("/data/42");

  assert.ok(falso.chamadas.some((c) => c.caminho === "/auth/refresh"),
            "o interceptor nem tentou renovar — o teste nao mede o caminho certo");
  assert.equal(resp.status, 401, "o 401 tem que chegar ao chamador");
  assert.deepEqual(apagados, [], "apagou o Cache Storage com a sessao viva");
});

test("401 de autenticacao com refresh ausente: o estado fica E a chamada seguinte funciona", async () => {
  // O cenario inteiro, ponta a ponta: nao basta nao apagar, a sessao tem que
  // seguir utilizavel depois. Sem a segunda metade o caso passaria num
  // interceptor que engoliu a sessao de outro jeito.
  //
  // A marca de familia no `/auth/mfa/setup` NAO e' enfeite: sem ela o #176
  // devolve o 401 antes de renovar, o `"/auth/refresh": 400` do mapa vira letra
  // morta e o caso deixa de medir a metade que importa (o 400 nao apagando o
  // aparelho). Medido: com a mutacao da #173 o caso ficava VERDE sem a marca.
  // Aqui o 401 e' access token vencido ao abrir o setup do MFA, nao senha
  // errada — essa e' 401 de aplicacao e vive no `auth_refresh_gatilho.test.mjs`.
  const apagados = [];
  const falso = fetchPorRota({
    "/auth/mfa/setup": 401, "/auth/refresh": 400, "/data/42": 200,
  }, ["/auth/mfa/setup"]);
  const { ctx, local, sessao } = comStorageCheio("static/auth-refresh.js", (c) => {
    c.fetch = falso.fn;
    c.caches.delete = async (k) => { apagados.push(k); return true; };
  });

  const erro = await ctx.window.fetch("/auth/mfa/setup", { method: "POST" });
  assert.ok(falso.chamadas.some((c) => c.caminho === "/auth/refresh"),
            "o interceptor nem tentou renovar — o teste nao mede o caminho certo");
  assert.equal(erro.status, 401);

  for (const k of Object.keys(DERIVADO_DE_CONTA)) {
    assert.equal(local.has(k), true, `${k} foi apagado com a sessao viva`);
  }
  assert.equal(sessao.has("pb_home_42"), true, "pb_home_ foi apagado da sessao");
  assert.deepEqual(apagados, [], "apagou o Cache Storage com a sessao viva");

  const ok = await ctx.window.fetch("/data/42");
  assert.equal(ok.ok, true, "a chamada autenticada seguinte parou de funcionar");
  assert.deepEqual(apagados, [], "a chamada seguinte apagou o Cache Storage");
});

test("uma renovacao por 401 interceptado, e nenhum retry quando ela falha", async () => {
  // O teto do 401 que AINDA renova (o de autenticacao — desde o #176 o de
  // aplicacao nao entra mais aqui): nao ha recursao, e' uma renovacao so', e a
  // request original so' e' refeita quando a renovacao da' certo.
  const falso = fetchPorRota({ "/data/42": 401, "/auth/refresh": 400 }, ["/data/42"]);
  const { ctx } = paginaComCache("static/auth-refresh.js", (c) => { c.fetch = falso.fn; });

  await ctx.window.fetch("/data/42");

  const refresh = falso.chamadas.filter((c) => c.caminho === "/auth/refresh");
  const original = falso.chamadas.filter((c) => c.caminho === "/data/42");
  assert.equal(refresh.length, 1, `esperado 1 refresh, vieram ${refresh.length}`);
  assert.equal(original.length, 1, "a request original foi refeita com o refresh falhando");
});

test("retry pos-refresh que encerra a sessao limpa", async () => {
  // DELETE /auth/account com o ACCESS TOKEN vencido (401 de autenticacao, o que
  // leva WWW-Authenticate — a senha errada da mesma rota e' 401 de aplicacao e
  // desde o #176 nao renova). O refresh renova, o retry da' certo — e o retry e'
  // que encerra a sessao. Ele saia por baixo da limpeza.
  const apagados = [];
  let tentativas = 0;
  const falso = fetchPorRota({
    "/auth/account": () => (++tentativas === 1 ? 401 : 200),
    "/auth/refresh": 200,
  }, ["/auth/account"]);
  const { ctx } = paginaComCache("static/auth-refresh.js", (c) => {
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
  }, ["/data/42"]);
  const { ctx } = paginaComCache("static/auth-refresh.js", (c) => {
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
  const refresh = readFileSync(join(dir, "static", "auth-refresh.js"), "utf-8");

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

// ── Logout SEM RESPOSTA: o fetch rejeita (offline, DNS, captive portal) ──
//
// O quarto caminho do interceptor, e o unico que nao produzia resposta: o
// `await _origFetch(...)` estourava ANTES do `_comLimpeza`, entao nada era
// apagado. Alcancavel de verdade — modo aviao / wifi de aeroporto + "Sair" no
// Ajustes —, e o pior dos cinco donos de logout: `logoutSettings`
// (settings.html) nao tem limpeza local nenhuma, grava o `finbot_logout_at`
// ANTES do fetch (as outras abas caem para /?logout=1) e navega no `.finally`
// mesmo com a rejeicao. O logout PARECIA ter dado certo com tudo no aparelho.
//
// O par de controles: apagar sem resposta vale so' para o /auth/logout. Nas
// outras duas rotas do mapa a rejeicao e' rede fora com a sessao DE PE, e
// limpar ali seria pior que o bug.

/** fetch falso que rejeita como o navegador rejeita: TypeError, sem resposta. */
const fetchOffline = () => async () => { throw new TypeError("Failed to fetch"); };

test("logout OFFLINE limpa o aparelho — e a rejeicao ainda chega ao chamador", async () => {
  const apagados = [];
  const { ctx, local, sessao } = comStorageCheio("static/auth-refresh.js", (c) => {
    c.fetch = fetchOffline();
    c.caches.delete = async (k) => { apagados.push(k); return true; };
  });

  await assert.rejects(() => ctx.window.fetch("/auth/logout", { method: "POST" }),
                       TypeError,
                       "a rejeicao foi engolida: o chamador perde o erro de rede");

  for (const k of Object.keys(DERIVADO_DE_CONTA)) {
    assert.equal(local.has(k), false, `${k} sobreviveu ao logout offline`);
  }
  assert.equal(sessao.has("pb_home_42"), false, "pb_home_ sobreviveu na sessao");
  assert.equal(sessao.has("pb_snap_42_2026_08"), false, "pb_snap_ sobreviveu na sessao");
  assert.deepEqual(apagados.sort(), ["pigbank-v8", "pigbank-v9"],
                   "o Cache Storage sobreviveu ao logout offline");
  for (const k of Object.keys(PREFERENCIA_DO_APARELHO)) {
    assert.equal(local.get(k), PREFERENCIA_DO_APARELHO[k],
                 `${k} foi apagado — e' preferencia do aparelho, nao da conta`);
  }
});

test("offline em rota comum NAO limpa — o interceptor renova e a sessao segue", async () => {
  // Controle positivo: o caminho legitimo (perder a rede com a sessao viva)
  // tem que continuar funcionando. Sem ele, apagar em TODA rejeicao passaria
  // nos casos acima destruindo o aparelho de quem so' entrou no metro.
  const apagados = [];
  const { ctx, local } = comStorageCheio("static/auth-refresh.js", (c) => {
    c.fetch = fetchOffline();
    c.caches.delete = async (k) => { apagados.push(k); return true; };
  });

  await assert.rejects(() => ctx.window.fetch("/data/42"), TypeError);

  assert.deepEqual(apagados, [], "apagou o Cache Storage num GET que so' perdeu a rede");
  for (const k of Object.keys(DERIVADO_DE_CONTA)) {
    assert.equal(local.has(k), true, `${k} foi apagado com a sessao viva`);
  }
});

test("offline no DELETE /auth/account NAO limpa — a exclusao nao aconteceu", async () => {
  // O chamador (settings.html, ~:2605) mostra o toast e NAO navega nessa
  // falha: o usuario continua logado na propria pagina. Apagar o aparelho dele
  // ali seria pior que o bug que este PR conserta.
  const apagados = [];
  const { ctx, local } = comStorageCheio("static/auth-refresh.js", (c) => {
    c.fetch = fetchOffline();
    c.caches.delete = async (k) => { apagados.push(k); return true; };
  });

  await assert.rejects(() => ctx.window.fetch("/auth/account", { method: "DELETE" }), TypeError);

  assert.deepEqual(apagados, [], "apagou o Cache Storage de quem continua logado");
  for (const k of Object.keys(DERIVADO_DE_CONTA)) {
    assert.equal(local.has(k), true, `${k} foi apagado numa exclusao que falhou`);
  }
});

test("offline no /auth/refresh chamado DIRETO nao limpa nem estoura no predicado", async () => {
  // O refresh do `_doRefresh` e' interno e nao passa pelo wrapper, entao o
  // predicado dele so' e' alcancado quando alguem chama a rota direto. Sem o
  // `!!resp &&`, `resp.status` num `resp` null trocaria o TypeError de rede do
  // navegador por um TypeError de leitura de null — erro diferente chegando ao
  // chamador, no meio da limpeza.
  const apagados = [];
  const { ctx, local } = comStorageCheio("static/auth-refresh.js", (c) => {
    c.fetch = fetchOffline();
    c.caches.delete = async (k) => { apagados.push(k); return true; };
  });

  await assert.rejects(() => ctx.window.fetch("/auth/refresh", { method: "POST" }),
                       (e) => e instanceof TypeError && /Failed to fetch/.test(e.message),
                       "o erro que chegou ao chamador nao e' o da rede");

  assert.deepEqual(apagados, [], "apagou o Cache Storage com a sessao de pe");
  for (const k of Object.keys(DERIVADO_DE_CONTA)) {
    assert.equal(local.has(k), true, `${k} foi apagado por um refresh sem rede`);
  }
});

// ── Logout que RESPONDE ERRO: a outra metade da mesma classe ─────────────
//
// O primeiro corte deste PR prendia a limpeza ao `resp.ok` e so' abria excecao
// para a rejeicao. Era consertar a instancia: o argumento que justifica limpar
// sem resposta ("o chamador navega para a tela deslogada de qualquer jeito")
// nao depende de HAVER resposta — vale igual para 403, 429 e 500.
//
// E o 403 nao precisa de modo aviao: o cookie `csrf_token` dura 24h
// (CSRF_COOKIE_MAX_AGE) e so' e' reemitido em metodo SEGURO quando falta, entao
// uma aba deixada aberta mais de um dia manda o POST sem o header e leva 403 do
// `csrf_middleware` — antes da rota, sem o backend limpar cookie nenhum. O 429
// vem do `@limiter.limit("30/minute")` da propria rota.

for (const status of [403, 429, 500]) {
  test(`logout que responde ${status} limpa — o chamador navega igual`, async () => {
    const apagados = [];
    const { ctx, local, sessao } = comStorageCheio("static/auth-refresh.js", (c) => {
      c.fetch = async () => ({ ok: false, status });
      c.caches.delete = async (k) => { apagados.push(k); return true; };
    });

    const resp = await ctx.window.fetch("/auth/logout", { method: "POST" });

    assert.equal(resp.status, status, "o status tem que chegar ao chamador");
    assert.deepEqual(apagados.sort(), ["pigbank-v8", "pigbank-v9"],
                     `logout ${status} deixou o Cache Storage no aparelho`);
    for (const k of Object.keys(DERIVADO_DE_CONTA)) {
      assert.equal(local.has(k), false, `${k} sobreviveu a um logout ${status}`);
    }
    assert.equal(sessao.has("pb_home_42"), false, "pb_home_ sobreviveu na sessao");
  });
}

test("URL protocolo-relativa de terceiro NAO e' a nossa origem", async () => {
  // `_isOwnApi` aceitava qualquer string comecando com "/", inclusive `//host/`,
  // ANTES de tentar parsear — e ai `_caminho()` resolvia
  // `//cdn-de-terceiro/auth/logout` para o pathname `/auth/logout`. Com a
  // limpeza agora disparando tambem sem resposta, e fetch cross-origin sem CORS
  // SEMPRE rejeitando, bastaria uma dessas URLs para apagar o aparelho.
  const apagados = [];
  const repassadas = [];
  const { ctx, local } = comStorageCheio("static/auth-refresh.js", (c) => {
    c.fetch = async (input) => { repassadas.push(input); throw new TypeError("Failed to fetch"); };
    c.caches.delete = async (k) => { apagados.push(k); return true; };
  });

  await assert.rejects(() => ctx.window.fetch("//cdn-de-terceiro.example.com/auth/logout",
                                              { method: "POST" }), TypeError);

  assert.deepEqual(repassadas, ["//cdn-de-terceiro.example.com/auth/logout"],
                   "a request nem chegou ao fetch original — o teste nao mede o caminho certo");
  assert.deepEqual(apagados, [], "um host de terceiro apagou o Cache Storage deste aparelho");
  for (const k of Object.keys(DERIVADO_DE_CONTA)) {
    assert.equal(local.has(k), true, `${k} foi apagado por uma URL de terceiro`);
  }
});

test("caminho absoluto da PROPRIA origem continua sendo interceptado", async () => {
  // Controle positivo do caso acima: a guarda do `//` nao pode derrubar a
  // deteccao por HOST, que e' o que faz `https://pigbankai.com/auth/logout`
  // (URL absoluta, mesma origem) continuar passando pela limpeza.
  const apagados = [];
  const { ctx } = comStorageCheio("static/auth-refresh.js", (c) => {
    c.fetch = async () => ({ ok: true, status: 200 });
    c.caches.delete = async (k) => { apagados.push(k); return true; };
  });

  await ctx.window.fetch(`${ORIGEM}/auth/logout`, { method: "POST" });

  assert.deepEqual(apagados.sort(), ["pigbank-v8", "pigbank-v9"],
                   "a guarda do // derrubou a interceptacao da propria origem");
});

// ── Storage BLOQUEADO: o getter que lanca antes de qualquer try ─────────
//
// `window.localStorage` nao e' um campo, e' um GETTER, e ele LANCA
// `SecurityError` quando o site esta' com dados bloqueados (Chrome) ou o
// WKWebView nega storage. O `try` do `apagaStorage` protegia o `forEach`, mas a
// avaliacao do ARGUMENTO acontecia fora dele — entao o erro subia por
// `_limpaEstadoDoDispositivo` e derrubava a limpeza inteira ANTES do Cache
// Storage e do service worker, que sao justamente o residuo que ela existe para
// remover. De quebra, o chamador recebia `SecurityError` no lugar do erro real.

/**
 * `paginaComCache` com um dos storages lancando no ACESSO, como o navegador.
 *
 * O getter TEM que morar num `window` separado do global do vm: definido no
 * proprio global, o Node engole a excecao e devolve `undefined` — medido, e foi
 * assim que a primeira versao destes tres casos passou sem medir nada.
 */
function comStorageBloqueado(qual, prepara) {
  const apagados = [];
  const { ctx } = paginaComCache("static/auth-refresh.js", (c) => {
    const win = Object.create(c);
    Object.defineProperty(win, qual, {
      configurable: true,
      get() { const e = new Error("Access is denied for this document."); e.name = "SecurityError"; throw e; },
    });
    c.window = win;
    c.caches.delete = async (k) => { apagados.push(k); return true; };
    if (prepara) prepara(c);
  });
  return { ctx, apagados };
}

for (const qual of ["localStorage", "sessionStorage"]) {
  test(`${qual} bloqueado nao derruba a limpeza do cache no logout`, async () => {
    const { ctx, apagados } = comStorageBloqueado(qual);

    const resp = await ctx.window.fetch("/auth/logout", { method: "POST" });

    assert.equal(resp.ok, true, "o logout nem devolveu resposta ao chamador");
    assert.deepEqual(apagados.sort(), ["pigbank-v8", "pigbank-v9"],
                     `com ${qual} bloqueado o Cache Storage ficou no aparelho`);
  });
}

test("storage bloqueado no logout OFFLINE: limpa o cache E devolve o erro DA REDE", async () => {
  // Os dois danos de uma vez. Sem o conserto o chamador recebia o SecurityError
  // do getter em vez do TypeError da rede, e nada era apagado.
  const desregistrados = [];
  const { ctx, apagados } = comStorageBloqueado("localStorage", (c) => {
    c.fetch = async () => { throw new TypeError("Failed to fetch"); };
    c.navigator.serviceWorker.getRegistrations = async () => [
      { unregister: async () => { desregistrados.push(1); return true; } },
    ];
  });

  await assert.rejects(() => ctx.window.fetch("/auth/logout", { method: "POST" }),
                       (e) => e instanceof TypeError && /Failed to fetch/.test(e.message),
                       "o chamador recebeu o erro do storage, nao o da rede");

  assert.deepEqual(apagados.sort(), ["pigbank-v8", "pigbank-v9"], "o cache ficou no aparelho");
  assert.equal(desregistrados.length, 1, "o service worker nao foi desregistrado");
});

test("nav-auth tem a mesma correcao — o Sair das publicas parava de recarregar", () => {
  // §0.7 de novo: o `apagaStorage` do nav-auth e' a copia do outro e tinha o
  // mesmo `try` no lugar errado. La o dano e' pior: o `.finally` do `doLogout`
  // rejeitava e o `location.reload()` NUNCA rodava — o botao "Sair" das 12
  // paginas publicas nao fazia nada visivel. O `doLogout` e' interno ao IIFE e
  // so' e' alcancado por clique, entao o que se prende aqui e' a FORMA: nenhum
  // dos dois pode avaliar o getter fora do try.
  const dir = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "frontend");
  for (const [nome, caminho] of [["nav-auth.js", ["nav-auth.js"]],
                                 ["auth-refresh.js", ["static", "auth-refresh.js"]]]) {
    const fonte = readFileSync(join(dir, ...caminho), "utf-8");
    assert.ok(!/apagaStorage\(\s*window\./i.test(fonte),
              `${nome} volta a avaliar o getter de storage FORA do try: com dados do site bloqueados a limpeza inteira morre antes do Cache Storage`);
    assert.ok(/pagaStorage\("localStorage"\)/.test(fonte) && /pagaStorage\("sessionStorage"\)/.test(fonte),
              `${nome} parou de limpar um dos dois storages`);
  }
});

test("URL com barra invertida tambem aponta para outro host", async () => {
  // `//host/x` era o caso obvio; `/\host/x` resolve para o MESMO lugar porque a
  // WHATWG normaliza `\` como `/`, e nao comeca com `//`. Consertar so' o
  // primeiro era consertar a instancia: por esta ainda dava para apagar o
  // aparelho de quem carregasse a URL. O conserto foi tirar o fast-path e
  // deixar a comparacao por HOST decidir.
  for (const url of ["//evil.example.com/auth/logout",
                     "/\\evil.example.com/auth/logout",
                     "/\\/evil.example.com/auth/logout"]) {
    const apagados = [];
    const { ctx, local } = comStorageCheio("static/auth-refresh.js", (c) => {
      c.fetch = async () => { throw new TypeError("Failed to fetch"); };
      c.caches.delete = async (k) => { apagados.push(k); return true; };
    });

    await assert.rejects(() => ctx.window.fetch(url, { method: "POST" }), TypeError);

    assert.deepEqual(apagados, [], `${url} apagou o Cache Storage deste aparelho`);
    assert.equal(local.has("pigbank_menu_v1"), true, `${url} apagou o menu deste aparelho`);
  }
});

test("caminho relativo comum continua sendo interceptado sem o fast-path", async () => {
  // Controle positivo de tirar o fast-path: `/auth/logout` cru e' a forma que
  // TODOS os cinco donos de logout usam, e ela nao pode deixar de ser nossa.
  const apagados = [];
  const { ctx } = comStorageCheio("static/auth-refresh.js", (c) => {
    c.fetch = async () => ({ ok: true, status: 200 });
    c.caches.delete = async (k) => { apagados.push(k); return true; };
  });

  await ctx.window.fetch("/auth/logout", { method: "POST" });

  assert.deepEqual(apagados.sort(), ["pigbank-v8", "pigbank-v9"],
                   "tirar o fast-path derrubou a interceptacao do caminho relativo");
});

// ── O METODO importa quando o predicado e' incondicional ────────────────
//
// `_caminho()` guarda so' o pathname, entao com o logout limpando em qualquer
// resposta um GET no mesmo caminho tambem limpava. O backend so' define
// `POST /auth/logout`, entao um GET leva 405 — e, ao contrario dos cinco donos
// de logout, ele NAO navega para lugar nenhum: o usuario fica na pagina, com a
// sessao viva no servidor e o aparelho apagado. Achado do Codex no #230.
//
// Os outros dois nao precisam da checagem: o status ja os prende, e um 405 nao
// e' `ok` nem 401.

for (const metodo of ["GET", "HEAD", "PUT"]) {
  test(`${metodo} em /auth/logout NAO limpa — 405 e o usuario fica na pagina`, async () => {
    const apagados = [];
    const { ctx, local, sessao } = comStorageCheio("static/auth-refresh.js", (c) => {
      c.fetch = async () => ({ ok: false, status: 405 });
      c.caches.delete = async (k) => { apagados.push(k); return true; };
    });

    const opcoes = metodo === "GET" ? undefined : { method: metodo };
    const resp = await ctx.window.fetch("/auth/logout", opcoes);

    assert.equal(resp.status, 405);
    assert.deepEqual(apagados, [], `${metodo} apagou o Cache Storage de quem continua logado`);
    for (const k of Object.keys(DERIVADO_DE_CONTA)) {
      assert.equal(local.has(k), true, `${k} foi apagado por um ${metodo} que levou 405`);
    }
    assert.equal(sessao.has("pb_home_42"), true, "pb_home_ foi apagado da sessao");
  });
}

test("o metodo sai do objeto Request quando nao vem no init", async () => {
  // `fetch(new Request(url, {method}))` nao passa `init`. Sem ler o `.method` do
  // objeto o metodo cairia para o default GET e o logout legitimo pararia de
  // limpar — regressao pior que o bug que a checagem conserta.
  const apagados = [];
  const { ctx } = comStorageCheio("static/auth-refresh.js", (c) => {
    c.fetch = async () => ({ ok: true, status: 200 });
    c.caches.delete = async (k) => { apagados.push(k); return true; };
  });

  await ctx.window.fetch({ url: "/auth/logout", method: "POST" });

  assert.deepEqual(apagados.sort(), ["pigbank-v8", "pigbank-v9"],
                   "o metodo do objeto Request foi ignorado e o logout parou de limpar");
});

test("metodo em minusculo ainda e' POST — `fetch(url, {method:\"post\"})` e' valido", async () => {
  // O navegador aceita o metodo em qualquer caixa. Sem normalizar, um chamador
  // que escrevesse "post" faria o logout parar de limpar em silencio —
  // regressao pior que o 405 que a checagem de metodo conserta.
  const apagados = [];
  const { ctx, local } = comStorageCheio("static/auth-refresh.js", (c) => {
    c.fetch = async () => ({ ok: true, status: 200 });
    c.caches.delete = async (k) => { apagados.push(k); return true; };
  });

  await ctx.window.fetch("/auth/logout", { method: "post" });

  assert.deepEqual(apagados.sort(), ["pigbank-v8", "pigbank-v9"],
                   "o logout com o metodo em minusculo parou de limpar o cache");
  assert.equal(local.has("pigbank_menu_v1"), false, "o menu sobreviveu ao logout");
});

test("mesmo host, esquema diferente NAO e' a nossa origem", async () => {
  // `host` ignora o esquema. Numa pagina HTTPS o `http://mesmo-host/auth/logout`
  // passava por nosso; o navegador recusa por conteudo misto, a request REJEITA,
  // e a rejeicao vira fim de sessao — o aparelho apagado por um logout que nunca
  // saiu do navegador. Achado do Codex no #230.
  const apagados = [];
  const repassadas = [];
  const { ctx, local } = comStorageCheio("static/auth-refresh.js", (c) => {
    c.fetch = async (input) => { repassadas.push(input); throw new TypeError("Failed to fetch"); };
    c.caches.delete = async (k) => { apagados.push(k); return true; };
  });

  const inseguro = `${ORIGEM.replace("https:", "http:")}/auth/logout`;
  await assert.rejects(() => ctx.window.fetch(inseguro, { method: "POST" }), TypeError);

  assert.deepEqual(repassadas, [inseguro],
                   "a request nem chegou ao fetch original — o teste nao mede o caminho certo");
  assert.deepEqual(apagados, [], "conteudo misto apagou o Cache Storage deste aparelho");
  assert.equal(local.has("pigbank_menu_v1"), true, "conteudo misto apagou o menu deste aparelho");
});
