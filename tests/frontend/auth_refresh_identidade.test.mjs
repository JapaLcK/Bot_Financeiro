/**
 * Contrato: depois de renovar a sessão, o `auth-refresh.js` só refaz a request
 * quando a conta continua a mesma.
 *
 * A página guarda a conta do primeiro `X-PB-Conta` de uma resposta 2xx e a manda
 * no `/auth/refresh` (`-` quando ainda não sabe). Refaz quando a conta renovada é
 * a da página, ou, sem conta conhecida, quando o servidor diz `confirmada`. Fora
 * disso: não refaz, apaga o estado do aparelho, encerra a sessão quando era
 * escrita ou a conta mudou, vai ao login com o aviso e a promessa de quem chamou
 * não resolve. O lado servidor está em `tests/test_sessao_divergente_refresh.py`;
 * aqui as respostas são mockadas.
 *
 * Rodar:  node --test tests/frontend/auth_refresh_identidade.test.mjs
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { startServer } from "./_server.mjs";
import { chromium } from "playwright";

let ORIGIN, server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer());
                     browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

const MARCA = 'Bearer realm="pigbank", error="invalid_token"';
const r401Auth = {
  status: 401, contentType: "application/json",
  headers: { "WWW-Authenticate": MARCA },
  body: JSON.stringify({ detail: "Token inválido ou expirado." }),
};
const r200 = (corpo, conta) => ({
  status: 200, contentType: "application/json", body: JSON.stringify(corpo),
  headers: conta == null ? {} : { "X-PB-Conta": String(conta) },
});
const renovou = (conta, confirmada) => r200({ ok: true, user_id: conta, confirmada }, conta);

/**
 * Página mínima com o `auth-refresh.js` real. `/me` responde 2xx com a conta
 * `conta` (quando dada) para a página aprendê-la; `/escrita` e `/leitura` dão
 * 401 na primeira vez e 200 depois. O `/login` responde 204: a navegação é
 * registrada e não acontece, então a página continua lá para ser inspecionada.
 */
async function abrir({ conta = null, servidor = conta, refresh, logoutTravado = false }) {
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  const chamadas = [];
  const headersDoRefresh = [];
  const tentativas = {};
  const registra = (route) => {
    const u = new URL(route.request().url());
    chamadas.push(`${route.request().method()} ${u.pathname}${u.search}`);
  };
  await page.route(`${ORIGIN}/__identidade.html`, (route) => route.fulfill({
    status: 200, contentType: "text/html",
    body: '<!doctype html><meta charset="utf-8"><title>identidade</title>'
        + '<script src="/static/auth-refresh.js"></script>',
  }));
  await page.route(`${ORIGIN}/me`, (route) => { registra(route); return route.fulfill(r200({}, conta)); });
  for (const caminho of ["/escrita", "/leitura", "/leitura2"]) {
    await page.route(`${ORIGIN}${caminho}`, (route) => {
      registra(route);
      tentativas[caminho] = (tentativas[caminho] || 0) + 1;
      return route.fulfill(tentativas[caminho] === 1 ? r401Auth : r200({ ok: "refeita" }, servidor));
    });
  }
  await page.route(`${ORIGIN}/outra`, (route) => route.fulfill(r200({}, 8)));
  await page.route(`${ORIGIN}/auth/refresh`, (route) => {
    registra(route);
    headersDoRefresh.push(route.request().headers()["x-pb-conta"]);
    return route.fulfill(refresh);
  });
  await page.route(`${ORIGIN}/auth/logout`, (route) => {
    registra(route);
    return logoutTravado ? undefined : route.fulfill(r200({ ok: true }));
  });
  await page.route((u) => u.pathname === "/login", (route) => { registra(route); return route.fulfill({ status: 204 }); });

  await page.goto(`${ORIGIN}/__identidade.html`);
  assert.equal(await page.evaluate(() => typeof window.pbCsrfHeaders), "function",
               "o auth-refresh.js não carregou");
  await page.evaluate(() => {
    localStorage.setItem("pigbank_theme", "dark");
    localStorage.setItem("pigbank_menu_v1", "dado-da-conta");
  });
  if (conta != null) await page.evaluate(() => fetch("/me"));
  return { page, ctx, chamadas, headersDoRefresh };
}

/** Dispara o fetch e devolve o estado da promessa depois de `ms`. */
async function dispara(page, url, init, ms = 600) {
  await page.evaluate(([u, i]) => {
    window.__estado = window.__estado || {};
    window.__estado[u] = "pendente";
    fetch(u, i).then((r) => { window.__estado[u] = `resolveu ${r.status}`; },
                     () => { window.__estado[u] = "rejeitou"; });
  }, [url, init || {}]);
  await page.waitForTimeout(ms);
  return page.evaluate((u) => window.__estado[u], url);
}

const storage = (page) => page.evaluate(() => ({
  tema: localStorage.getItem("pigbank_theme"), menu: localStorage.getItem("pigbank_menu_v1"),
}));
const navegou = (chamadas) => chamadas.filter((c) => c.startsWith("GET /login"));
const POST = { method: "POST", body: "{}" };

test("F1: conta conhecida e igual à renovada — a escrita é refeita", async () => {
  const { page, ctx, chamadas, headersDoRefresh } = await abrir({ conta: 7, refresh: renovou(7, true) });
  assert.equal(await dispara(page, "/escrita", POST), "resolveu 200");
  assert.deepEqual(headersDoRefresh, ["7"]);
  assert.equal(chamadas.filter((c) => c === "POST /escrita").length, 2, JSON.stringify(chamadas));
  assert.deepEqual(navegou(chamadas), []);
  await ctx.close();
});

test("F2: conta conhecida e diferente da renovada — nada é refeito, sai com aviso", async () => {
  const { page, ctx, chamadas } = await abrir({ conta: 7, refresh: renovou(8, true) });
  assert.equal(await dispara(page, "/escrita", POST), "pendente");
  assert.equal(chamadas.filter((c) => c === "POST /escrita").length, 1, JSON.stringify(chamadas));
  assert.ok(chamadas.includes("POST /auth/logout"), JSON.stringify(chamadas));
  assert.deepEqual(navegou(chamadas), ["GET /login?next=%2F__identidade.html&aviso=acao-nao-enviada"]);
  assert.deepEqual(await storage(page), { tema: "dark", menu: null });
  await ctx.close();
});

test("F3: refresh recusado por conta divergente — navega e não refaz", async () => {
  const { page, ctx, chamadas } = await abrir({
    conta: 7,
    refresh: { status: 401, contentType: "application/json", body: JSON.stringify({ detail: "sessao_divergente" }) },
  });
  assert.equal(await dispara(page, "/escrita", POST), "pendente");
  assert.equal(chamadas.filter((c) => c === "POST /escrita").length, 1, JSON.stringify(chamadas));
  assert.deepEqual(navegou(chamadas), ["GET /login?next=%2F__identidade.html&aviso=acao-nao-enviada"]);
  await ctx.close();
});

test("F3b: refresh 401 de outro motivo segue como antes — o 401 chega a quem chamou", async () => {
  const { page, ctx, chamadas } = await abrir({
    conta: 7,
    refresh: { status: 401, contentType: "application/json", body: JSON.stringify({ detail: "invalid_refresh_token" }) },
  });
  assert.equal(await dispara(page, "/escrita", POST), "resolveu 401");
  assert.deepEqual(navegou(chamadas), []);
  await ctx.close();
});

test("F2b: conta conhecida e refresh sem `user_id` — não refaz, vai ao login sem sair nem aviso", async () => {
  const { page, ctx, chamadas } = await abrir({ conta: 7, refresh: r200({ ok: true }) });
  assert.equal(await dispara(page, "/leitura"), "pendente");
  assert.ok(!chamadas.includes("POST /auth/logout"), JSON.stringify(chamadas));
  assert.deepEqual(navegou(chamadas), ["GET /login?next=%2F__identidade.html"]);
  await ctx.close();
});

test("F4: conta desconhecida, leitura, não confirmada — não refaz, vai ao login sem sair", async () => {
  const { page, ctx, chamadas, headersDoRefresh } = await abrir({ refresh: renovou(7, false) });
  assert.equal(await dispara(page, "/leitura"), "pendente");
  assert.deepEqual(headersDoRefresh, ["-"]);
  assert.equal(chamadas.filter((c) => c === "GET /leitura").length, 1, JSON.stringify(chamadas));
  assert.ok(!chamadas.includes("POST /auth/logout"), JSON.stringify(chamadas));
  assert.deepEqual(navegou(chamadas), ["GET /login?next=%2F__identidade.html"]);
  await ctx.close();
});

test("F5: conta desconhecida, leitura, confirmada — refaz e aprende a conta na resposta refeita", async () => {
  const { page, ctx, chamadas, headersDoRefresh } = await abrir({ servidor: 7, refresh: renovou(7, true) });
  assert.equal(await dispara(page, "/leitura"), "resolveu 200");
  assert.equal(chamadas.filter((c) => c === "GET /leitura").length, 2, JSON.stringify(chamadas));
  assert.deepEqual(navegou(chamadas), []);
  assert.equal(await dispara(page, "/leitura2"), "resolveu 200");
  assert.deepEqual(headersDoRefresh, ["-", "7"]);
  await ctx.close();
});

test("F6: conta desconhecida, escrita, confirmada — refaz", async () => {
  const { page, ctx, chamadas } = await abrir({ refresh: renovou(7, true) });
  assert.equal(await dispara(page, "/escrita", POST), "resolveu 200");
  assert.equal(chamadas.filter((c) => c === "POST /escrita").length, 2, JSON.stringify(chamadas));
  await ctx.close();
});

test("F7: dois 401 em paralelo — um refresh, um logout, uma navegação", async () => {
  const { page, ctx, chamadas } = await abrir({ conta: 7, refresh: renovou(8, true) });
  await page.evaluate(() => { fetch("/leitura"); fetch("/escrita", { method: "POST", body: "{}" }); });
  await page.waitForTimeout(800);
  const conta = (c) => chamadas.filter((x) => x === c).length;
  assert.equal(conta("POST /auth/refresh"), 1, JSON.stringify(chamadas));
  assert.equal(conta("POST /auth/logout"), 1, JSON.stringify(chamadas));
  assert.deepEqual(navegou(chamadas), ["GET /login?next=%2F__identidade.html&aviso=acao-nao-enviada"]);
  await ctx.close();
});

test("F9: a conta guardada é a PRIMEIRA — uma 2xx de outra conta depois não a troca", async () => {
  const { page, ctx, chamadas } = await abrir({ conta: 7, refresh: renovou(8, true) });
  await page.evaluate(() => fetch("/outra"));
  assert.equal(await dispara(page, "/leitura"), "pendente");
  assert.equal(chamadas.filter((c) => c === "GET /leitura").length, 1, JSON.stringify(chamadas));
  assert.ok(chamadas.includes("POST /auth/logout"), JSON.stringify(chamadas));
  assert.deepEqual(navegou(chamadas), ["GET /login?next=%2F__identidade.html&aviso=sessao-trocada"]);
  await ctx.close();
});

test("F10a: leitura, conta conhecida e diferente da renovada — encerra a sessão, aviso sessao-trocada", async () => {
  const { page, ctx, chamadas } = await abrir({ conta: 7, refresh: renovou(8, true) });
  assert.equal(await dispara(page, "/leitura"), "pendente");
  assert.equal(chamadas.filter((c) => c === "GET /leitura").length, 1, JSON.stringify(chamadas));
  assert.ok(chamadas.includes("POST /auth/logout"), JSON.stringify(chamadas));
  assert.deepEqual(navegou(chamadas), ["GET /login?next=%2F__identidade.html&aviso=sessao-trocada"]);
  await ctx.close();
});

test("F10b: leitura, refresh recusado por conta divergente — encerra a sessão, aviso sessao-trocada", async () => {
  const { page, ctx, chamadas } = await abrir({
    conta: 7,
    refresh: { status: 401, contentType: "application/json", body: JSON.stringify({ detail: "sessao_divergente" }) },
  });
  assert.equal(await dispara(page, "/leitura"), "pendente");
  assert.equal(chamadas.filter((c) => c === "GET /leitura").length, 1, JSON.stringify(chamadas));
  assert.ok(chamadas.includes("POST /auth/logout"), JSON.stringify(chamadas));
  assert.deepEqual(navegou(chamadas), ["GET /login?next=%2F__identidade.html&aviso=sessao-trocada"]);
  await ctx.close();
});

test("F11: logout que não responde — a tela vai ao login em até ~3 s", async () => {
  const { page, ctx, chamadas } = await abrir({ conta: 7, refresh: renovou(8, true), logoutTravado: true });
  assert.equal(await dispara(page, "/leitura", undefined, 1000), "pendente");
  assert.deepEqual(navegou(chamadas), []);
  await page.waitForTimeout(5000);
  assert.deepEqual(navegou(chamadas), ["GET /login?next=%2F__identidade.html&aviso=sessao-trocada"]);
  await ctx.close();
});

// ── login.html ───────────────────────────────────────────────────────────────

const TELAS = { desktop: { width: 1440, height: 900 }, mobile: { width: 390, height: 844 } };
const AVISOS = {
  "acao-nao-enviada": "A conta desta sessão mudou e a sua última ação não foi enviada. Entre de novo.",
  "sessao-trocada": "A conta desta sessão mudou. Entre de novo.",
};

async function abreLogin(tela, busca) {
  const page = await browser.newPage({ viewport: TELAS[tela] });
  const refresh = [];
  let validates = 0;
  await page.route("**/auth/validate", (route) => { validates++; return route.fulfill({ status: 401, body: "{}" }); });
  await page.route("**/auth/refresh", (route) => {
    refresh.push(route.request().headers()["x-pb-conta"]);
    return route.fulfill({ status: 401, body: "{}" });
  });
  await page.goto(`${ORIGIN}/login.html${busca}`);
  await page.locator("#form-login").waitFor({ state: "visible" });
  await page.waitForTimeout(300);
  return { page, refresh, validates: () => validates };
}

for (const tela of Object.keys(TELAS)) {
  test(`F8 ${tela}: a checagem de sessão do login manda a conta desconhecida`, async () => {
    const { page, refresh } = await abreLogin(tela, "");
    assert.deepEqual(refresh, ["-"]);
    assert.equal(await page.locator("#login-error").isVisible(), false);
    await page.close();
  });

  for (const [aviso, texto] of Object.entries(AVISOS)) {
    test(`F8 ${tela}: ?aviso=${aviso} aparece e o login não pula`, async () => {
      const { page, validates } = await abreLogin(tela, `?next=/home&aviso=${aviso}`);
      assert.equal(await page.locator("#login-error").textContent(), texto);
      assert.equal(await page.locator("#login-error").isVisible(), true);
      assert.equal(validates(), 0);
      assert.equal(await page.evaluate(() =>
        document.documentElement.scrollWidth <= document.documentElement.clientWidth), true);
      await page.close();
    });
  }
}

for (const aviso of ["%3Cb%3Ex%3C%2Fb%3E", "constructor", "toString", "__proto__"]) {
  test(`F8: aviso desconhecido ${aviso} é ignorado`, async () => {
    const { page, validates } = await abreLogin("desktop", `?aviso=${aviso}`);
    assert.equal(await page.locator("#login-error").isVisible(), false);
    assert.equal(validates(), 1);
    await page.close();
  });
}
