/**
 * PBOpenFinance.reconnect(itemId) — reconexão de banco já conectado, sem picker.
 *
 * O controle POSITIVO (picker sem itemId continua igual) é o A2 aqui e, inteiro,
 * of_connect_settings.test.mjs, que não foi editado.
 *
 * Controles negativos (desligue o conserto, o caso tem de ficar vermelho):
 *  - A1: tirar `item_id` do corpo do connect-token em openWidget().
 *  - A3: trocar o `itemId ? undefined : (...)` de selectedConnectorId por `(selected && selected.id) || undefined`.
 *  - A4: trocar o corpo do onClose por `conf("onClose", host)();` (sem esperar `salvando`).
 *  - A9/A9b (destroy mudo): tirar `geracao++;` do destroy() (ou fazer `vivo` devolver sempre true).
 *  - A10/A11 (uma conexão por vez): tirar `if (ocupado) return;` do connect() (A10); tirar
 *    `if (ocupado === sessao) ocupado = null;` do catch do connect (A11).
 *  - A13 (onClose sem argumento): no `fim` do onClose, trocar `function () {` por `function (r) {` e
 *    `conf("onClose", host)();` por `conf("onClose", host)(r);`.
 *  - A15 (onOpen isolado): tirar o try/catch em volta do `conf("onOpen", host)()` no fim de openWidget().
 *  - A16 (save libera a guarda): tirar o `liberar();` do `finally` do save (onSuccess).
 *  - A17 (ficha por sessão): trocar o corpo de `liberar` por `ocupado = null;`.
 *  - A18 (onConnectStart isolado): tirar o try/catch em volta do `conf("onConnectStart", host)()`.
 *  - A19 (init assíncrono): tirar a linha `conf("onClose", host)();   // o SDK não manda onClose...` do rejeitado do init
 *    (ou o bloco inteiro `if (aberto && typeof aberto.then === "function")`).
 *  - A21 (destroy no onConnectStart): tirar o `if (!vivo()) return;` logo depois do onConnectStart.
 *  - A22 (destroy no onError do init): trocar `if (vivo()) conf("onClose", host)();` por `conf("onClose", host)();`.
 *  - A23 (ocupado antes de validar): tirar o `if (ocupado) return false;` do topo de reconnect().
 *  - A24 (reconnect ocupado devolve false): trocar `if (ocupado) return false;` por `if (ocupado) return;` no topo de reconnect().
 *    O positivo é o mesmo caso: a 1ª chamada NÃO devolve `false`, e depois do onClose a seguinte também não.
 *  - A25 (widget velho): tirar `|| velha()` do `onClose` ou do `onError` de openWidget() (cada um deixa o A25 vermelho). O positivo é o A25b: sem outra conexão o onClose/onError do A chegam.
 *
 * Rodar: node --test tests/frontend/of_connect_reconectar.test.mjs
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { startServer } from "./_server.mjs";
import { chromium } from "playwright";

let ORIGIN, server, browser;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
/** Espera `cond()` ficar verdadeiro, com teto: sem ele um teste quebrado trava até o timeout do runner. */
async function waitFor(cond, ms = 5000) {
  const t0 = Date.now();
  while (!(await cond())) {
    if (Date.now() - t0 > ms) throw new Error("waitFor: tempo esgotado");
    await sleep(20);
  }
}
const json = (body, status = 200) =>
  ({ status, contentType: "application/json", body: JSON.stringify(body) });

before(async () => { ({ proc: server, origin: ORIGIN } = await startServer());
                     browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

const HTML = `<!doctype html><meta charset="utf-8"><title>t</title><script src="/static/auth-refresh.js"></script>
<script src="/open-finance-connect.js"></script>`;

/**
 * Página mínima com o módulo REAL e um widget falso. `posts` registra os POSTs
 * do módulo; `ev` (no window) registra a ordem dos callbacks do host.
 */
async function abrir({ token = () => json({ accessToken: "tok-123" }),
                       item = () => json({ ok: true }) } = {}) {
  const ctx = await browser.newContext();
  await ctx.addCookies([{ name: "csrf_token", value: "csrf-1", url: ORIGIN }]);
  const page = await ctx.newPage();
  const posts = [];
  await page.route("**/*", (route) => {
    const u = new URL(route.request().url());
    if (u.origin !== ORIGIN) return route.abort();
    if (/\.[a-z0-9]+$/i.test(u.pathname)) return route.continue();
    return route.fulfill(json({}));
  });
  await page.route("**/__t.html", (r) => r.fulfill({ status: 200, contentType: "text/html", body: HTML }));
  await page.route("**/auth/validate", (r) => r.fulfill(json({ user_id: 1 })));
  await page.route("**/open-finance/1/connectors", (r) =>
    r.fulfill(json({ connectors: [{ id: 612, name: "Nubank", color: "820ad1", logo: "", inv: true }] })));
  await page.route("**/open-finance/1", (r) => r.fulfill(json({ connections: [] })));
  await page.route("**/auth/me", (r) => r.fulfill(json({ of_banks_max: 5 })));
  await page.route("**/open-finance/1/connect-token", async (r) => {
    posts.push(["token", r.request().headers()["x-csrf-token"], r.request().postData()]);
    return r.fulfill(await token());
  });
  await page.route("**/open-finance/1/pluggy-item", async (r) => {
    posts.push(["item", r.request().headers()["x-csrf-token"], r.request().postData()]);
    return r.fulfill(await item());
  });
  await page.addInitScript(() => {
    window.PluggyConnect = function (o) {
      window.__w = o; window.__n = (window.__n || 0) + 1; this.init = function () {};
    };
  });
  await page.goto(`${ORIGIN}/__t.html`);
  await page.evaluate(() => {
    window.__ev = [];
    window.PBOpenFinance.init({
      apiBase: "", userId: 1,
      onOpen: () => window.__ev.push("open"),
      onConnected: () => window.__ev.push("connected"),
      onError: (m, s) => window.__ev.push(["error", m, s]),
      // Com argumento vira ["close", ...args]: é como o teste vê um onClose que vaza o valor do .then.
      onClose: (...a) => window.__ev.push(a.length ? ["close", ...a] : "close"),
    });
  });
  page.__ctx = ctx; page.__posts = posts;
  return page;
}

const ev = (page) => page.evaluate(() => window.__ev);
const widgetPronto = (page) => page.waitForFunction(() => !!window.__w);
const opts = (page) => page.evaluate(() => {
  const o = window.__w;
  return { keys: Object.keys(o).filter((k) => o[k] !== undefined), updateItem: o.updateItem,
           sel: o.selectedConnectorId, tem: "updateItem" in o };
});

async function escolherNubank(page) {
  await page.evaluate(() => window.PBOpenFinance.open());
  await page.click('#bankpick-list .bank-row[data-name="Nubank"]');
  await page.click("#bankpick-go");
  await widgetPronto(page);
}

test("A1 reconnect: item_id no corpo, updateItem, sem selectedConnectorId, onOpen 1x", async () => {
  const page = await abrir();
  await page.evaluate(() => window.PBOpenFinance.reconnect("item-11"));
  await widgetPronto(page);

  assert.deepEqual(page.__posts, [["token", "csrf-1", JSON.stringify({ item_id: "item-11" })]]);
  const o = await opts(page);
  assert.equal(o.updateItem, "item-11");
  assert.equal(o.sel, undefined);
  assert.deepEqual(await ev(page), ["open"]);
  await page.__ctx.close();
});

test("A2 picker sem itemId: corpo {} e sem updateItem (positivo)", async () => {
  const page = await abrir();
  await escolherNubank(page);

  assert.deepEqual(page.__posts, [["token", "csrf-1", "{}"]]);
  const o = await opts(page);
  assert.equal(o.tem, false, "updateItem não pode nem existir sem itemId");
  assert.equal(o.sel, 612);
  await page.__ctx.close();
});

test("A3 reconectar depois de escolher no picker não vaza o banco escolhido", async () => {
  const page = await abrir();
  await escolherNubank(page);
  // O widget do picker fecha de verdade (onClose) antes de reconectar: uma conexão por vez.
  await page.evaluate(() => { window.__w.onClose(); window.__w = null; window.PBOpenFinance.close(); });
  await page.evaluate(() => window.PBOpenFinance.reconnect("item-11"));
  await widgetPronto(page);

  const o = await opts(page);
  assert.equal(o.sel, undefined, "selectedConnectorId vazou do picker pra reconexão");
  assert.equal(o.updateItem, "item-11");
  await page.__ctx.close();
});

test("A4 onClose da reconexão espera o /pluggy-item; o picker não espera", async () => {
  let liberar;
  const presa = new Promise((r) => { liberar = r; });
  const page = await abrir({ item: async () => { await presa; return json({ ok: true }); } });

  await page.evaluate(() => window.PBOpenFinance.reconnect("item-11"));
  await widgetPronto(page);
  await page.evaluate(() => {
    window.__w.onSuccess({ item: { id: "item-11" } });   // não esperado: o save fica preso
    window.__w.onClose();
  });
  await sleep(300);
  assert.deepEqual(await ev(page), ["open"], "onClose chegou antes de o save terminar");
  liberar();
  await page.waitForFunction(() => window.__ev.length === 3);
  assert.deepEqual(await ev(page), ["open", "connected", "close"]);

  // Picker: ordem de sempre, onClose na hora.
  await page.evaluate(() => { window.__ev.length = 0; window.__w = null; });
  const presa2 = new Promise((r) => { liberar = r; });
  await page.unroute("**/open-finance/1/pluggy-item");
  await page.route("**/open-finance/1/pluggy-item", async (r) => { await presa2; r.fulfill(json({ ok: true })); });
  await escolherNubank(page);
  await page.evaluate(() => {
    window.__w.onSuccess({ item: { id: "x" } });
    window.__w.onClose();
  });
  await page.waitForFunction(() => window.__ev.includes("close"));
  assert.deepEqual(await ev(page), ["open", "close"]);
  liberar();
  await page.waitForFunction(() => window.__ev.length === 3);
  assert.deepEqual(await ev(page), ["open", "close", "connected"]);
  await page.__ctx.close();
});

test("A5 erro do servidor chega com mensagem e status; widget não é construído", async () => {
  const msg = "Não achamos esse banco nas suas conexões.";
  const page = await abrir({
    token: () => json({ detail: { code: "OF_ITEM_NAO_ENCONTRADO", message: msg } }, 404),
  });
  await page.evaluate(() => window.PBOpenFinance.reconnect("item-11"));
  await page.waitForFunction(() => window.__ev.length > 0);
  assert.deepEqual(await ev(page), [["error", msg, 404]]);

  await page.unroute("**/open-finance/1/connect-token");
  await page.route("**/open-finance/1/connect-token", (r) => r.fulfill(json({ detail: "x" }, 401)));
  await page.evaluate(() => { window.__ev.length = 0; window.PBOpenFinance.reconnect("item-11"); });
  await page.waitForFunction(() => window.__ev.length > 0);
  assert.deepEqual(await ev(page), [["error", "x", 401]]);
  assert.equal(await page.evaluate(() => window.__n || 0), 0);
  await page.__ctx.close();
});

test("A6 destroy() com connect-token em voo na reconexão: widget nunca abre", async () => {
  let liberar;
  const presa = new Promise((r) => { liberar = r; });
  const page = await abrir({ token: async () => { await presa; return json({ accessToken: "t" }); } });
  // Sem await da promessa do reconnect: ela só resolve depois do connect-token, que está preso.
  await page.evaluate(() => { window.PBOpenFinance.reconnect("item-11"); });
  await waitFor(() => page.__posts.length >= 1);
  await page.evaluate(() => window.PBOpenFinance.destroy());
  liberar();
  await sleep(400);
  assert.equal(await page.evaluate(() => window.__n || 0), 0);
  assert.deepEqual(await ev(page), []);
  await page.__ctx.close();
});

test("A7 itemId vazio ou não-string: onError e nenhum pedido", async () => {
  const page = await abrir();
  await page.evaluate(() => { window.PBOpenFinance.reconnect(""); window.PBOpenFinance.reconnect(5); });
  await sleep(300);
  const e = await ev(page);
  assert.equal(e.length, 2);
  assert.ok(e.every((x) => x[0] === "error"));
  assert.deepEqual(page.__posts, []);
  assert.equal(await page.evaluate(() => window.__n || 0), 0);
  await page.__ctx.close();
});

/** Reconexão com o widget já aberto; devolve a página. */
async function reconectada(opcoes) {
  const page = await abrir(opcoes);
  await page.evaluate(() => window.PBOpenFinance.reconnect("item-11"));
  await widgetPronto(page);
  return page;
}
const tipos = async (page) => (await ev(page)).map((x) => (Array.isArray(x) ? x[0] : x));

test("A8 save /pluggy-item 402: onError(msg, 402) e SÓ DEPOIS o close", async () => {
  const msg = "Seu plano conecta até 1 banco.";
  const page = await reconectada({
    item: () => json({ detail: { code: "OF_BANK_LIMIT", message: msg } }, 402),
  });
  await page.evaluate(() => { window.__w.onSuccess({ item: { id: "item-11" } }); window.__w.onClose(); });
  await waitFor(async () => (await ev(page)).length >= 3);
  assert.deepEqual(await ev(page), ["open", ["error", msg, 402], "close"]);
  await page.__ctx.close();
});

test("A9 destroy() com o save em voo: nenhum evento depois do destroy", async () => {
  let liberar;
  const presa = new Promise((r) => { liberar = r; });
  const page = await reconectada({ item: async () => { await presa; return json({ ok: true }); } });
  await page.evaluate(() => { window.__w.onSuccess({ item: { id: "item-11" } }); window.__w.onClose(); });
  await waitFor(() => page.__posts.length >= 2);   // token + item: o save está em voo
  await page.evaluate(() => window.PBOpenFinance.destroy());
  liberar();
  await sleep(400);
  assert.deepEqual(await ev(page), ["open"]);
  await page.__ctx.close();
});

test("A9b destroy() antes do onSuccess: success, error e close do widget não chegam ao host", async () => {
  const page = await reconectada();
  await page.evaluate(() => {
    window.PBOpenFinance.destroy();
    window.__w.onSuccess({ item: { id: "item-11" } });
    window.__w.onError({ x: 1 });
    window.__w.onClose();
  });
  await sleep(400);
  assert.deepEqual(await ev(page), ["open"]);
  assert.deepEqual(page.__posts.map((p) => p[0]), ["token"], "o save não pode nem ser pedido");
  await page.__ctx.close();
});

test("A10 reconnect duas vezes: 1 connect-token, 1 widget, 1 open; onClose libera a próxima", async () => {
  const page = await abrir();
  await page.evaluate(() => { window.PBOpenFinance.reconnect("item-11"); window.PBOpenFinance.reconnect("item-11"); });
  await widgetPronto(page);
  await sleep(300);
  assert.equal(page.__posts.length, 1);
  assert.equal(await page.evaluate(() => window.__n), 1);
  assert.deepEqual(await ev(page), ["open"]);

  // open() do picker também fica mudo enquanto a conexão está em curso.
  await page.evaluate(() => window.PBOpenFinance.open());
  assert.equal(await page.locator("#bankpick-overlay.open").count(), 0);

  // Positivo: depois do onClose a guarda foi liberada e reconectar funciona.
  await page.evaluate(() => window.__w.onClose());
  await page.evaluate(() => window.PBOpenFinance.reconnect("item-11"));
  await waitFor(() => page.evaluate(() => window.__n === 2));
  await page.__ctx.close();
});

test("A11 erro do connect-token libera a guarda: reconectar de novo funciona", async () => {
  let n = 0;
  const page = await abrir({
    token: () => (n++ === 0 ? json({ detail: "fora do ar" }, 500) : json({ accessToken: "t" })),
  });
  await page.evaluate(() => window.PBOpenFinance.reconnect("item-11"));
  await waitFor(async () => (await ev(page)).length >= 1);
  assert.deepEqual(await ev(page), [["error", "fora do ar", 500]]);
  await page.evaluate(() => window.PBOpenFinance.reconnect("item-11"));
  await widgetPronto(page);
  assert.deepEqual(await tipos(page), ["error", "open"]);
  await page.__ctx.close();
});

test("A12 onSuccess sem item.id: onError e depois close; nada é salvo", async () => {
  const page = await reconectada();
  await page.evaluate(() => { window.__w.onSuccess({}); window.__w.onClose(); });
  await waitFor(async () => (await ev(page)).length >= 3);
  assert.deepEqual(await tipos(page), ["open", "error", "close"]);
  assert.deepEqual(page.__posts.map((p) => p[0]), ["token"]);
  await page.__ctx.close();
});

test("A13 onClose sem onSuccess: só close, sem argumento", async () => {
  const page = await reconectada();
  await page.evaluate(() => window.__w.onClose());
  await waitFor(async () => (await ev(page)).length >= 2);
  await sleep(200);
  assert.deepEqual(await ev(page), ["open", "close"]);   // com argumento viraria ["close", ...]
  await page.__ctx.close();
});

test("A14 erro do SDK seguido de onClose: error antes de close", async () => {
  const page = await reconectada();
  await page.evaluate(() => { window.__w.onError({ x: 1 }); window.__w.onClose(); });
  await waitFor(async () => (await ev(page)).length >= 3);
  assert.deepEqual(await tipos(page), ["open", "error", "close"]);
  await page.__ctx.close();
});

test("A20 erro do SDK SEM onClose depois: a guarda é liberada e o próximo reconnect abre", async () => {
  // A Pluggy não garante onClose depois de onError (Codex, PR #1139). Negativo: tirar o
  // `liberar();` do onError do widget em openWidget() deixa o 2º reconnect mudo.
  const page = await reconectada();
  await page.evaluate(() => { window.__w.onError({ x: 1 }); });
  await waitFor(async () => (await ev(page)).length >= 2);
  await page.evaluate(() => { window.PBOpenFinance.reconnect("item-11"); });
  await waitFor(() => page.evaluate(() => window.__n === 2));
  assert.deepEqual(await tipos(page), ["open", "error", "open"]);
  await page.__ctx.close();
});

test("A15 onOpen do host que lança não vira onError", async () => {
  const page = await abrir();
  await page.evaluate(() => {
    window.PBOpenFinance.init({
      apiBase: "", userId: 1,
      onOpen: () => { window.__ev.push("open"); throw new Error("boom"); },
      onError: (m) => window.__ev.push(["error", m]),
    });
    window.PBOpenFinance.reconnect("item-11");
  });
  await widgetPronto(page);
  await sleep(300);
  assert.deepEqual(await ev(page), ["open"]);
  await page.__ctx.close();
});

test("A16 onSuccess + save terminam sem onClose: a próxima reconnect abre", async () => {
  const page = await reconectada();
  await page.evaluate(() => window.__w.onSuccess({ item: { id: "item-11" } }));
  await waitFor(async () => (await ev(page)).includes("connected"));
  await sleep(100);
  await page.evaluate(() => { window.PBOpenFinance.reconnect("item-11"); });
  await waitFor(() => page.evaluate(() => window.__n === 2));
  await page.__ctx.close();
});

test("A17 ficha por sessão: save velho que termina depois do destroy não solta a guarda da nova", async () => {
  let liberar;
  const presa = new Promise((r) => { liberar = r; });
  const page = await reconectada({ item: async () => { await presa; return json({ ok: true }); } });
  // Chaves: sem elas o evaluate espera a promessa do save, que está presa.
  await page.evaluate(() => { window.__w.onSuccess({ item: { id: "item-11" } }); });
  await waitFor(() => page.__posts.length >= 2);   // o save da sessão A está em voo
  // Na MESMA volta: a sessão B toma a guarda antes de o finally da A rodar.
  await page.evaluate(() => { window.PBOpenFinance.destroy(); window.PBOpenFinance.reconnect("item-22"); });
  await waitFor(() => page.evaluate(() => window.__n === 2));
  liberar();
  await sleep(300);
  await page.evaluate(() => { window.PBOpenFinance.reconnect("item-33"); });
  await sleep(300);
  assert.equal(await page.evaluate(() => window.__n), 2, "o finally da A soltou a guarda da B");
  assert.equal(page.__posts.filter((p) => p[0] === "token").length, 2);
  await page.__ctx.close();
});

test("A18 onConnectStart do host que lança não prende a guarda", async () => {
  const page = await abrir();
  await page.evaluate(() => {
    window.PBOpenFinance.init({ apiBase: "", userId: 1,
      onConnectStart: () => { throw new Error("boom"); } });
    window.PBOpenFinance.reconnect("item-11");
  });
  await waitFor(() => page.evaluate(() => window.__n === 1));
  // O onClose da reconexão libera a guarda numa microtarefa (espera o save): reconectar em outro evaluate.
  await page.evaluate(() => { window.__w.onClose(); });
  await page.evaluate(() => {
    window.PBOpenFinance.init({ apiBase: "", userId: 1, onOpen: () => window.__ev.push("open") });
    window.PBOpenFinance.reconnect("item-11");
  });
  await waitFor(() => page.evaluate(() => window.__n === 2));
  await page.__ctx.close();
});

test("A19 init() assíncrono que rejeita: open, error, close (nessa ordem) e a próxima reconnect funciona", async () => {
  const page = await abrir();
  await page.evaluate(() => {
    window.PluggyConnect = function (o) {
      window.__w = o; window.__n = (window.__n || 0) + 1;
      this.init = function () { return Promise.reject(new Error("iframe falhou")); };
    };
    window.PBOpenFinance.reconnect("item-11");
  });
  await waitFor(async () => (await ev(page)).length >= 3);
  const e = await ev(page);
  assert.equal(e.length, 3);
  assert.deepEqual([e[0], e[1][0], e[2]], ["open", "error", "close"]);
  await page.evaluate(() => {
    window.PluggyConnect = function (o) { window.__w = o; window.__n++; this.init = function () {}; };
    window.PBOpenFinance.reconnect("item-11");
  });
  await waitFor(() => page.evaluate(() => window.__n === 2));
  await page.__ctx.close();
});

test("A21 onConnectStart que chama destroy(): nada abre e nenhum callback dispara", async () => {
  const page = await abrir();
  await page.evaluate(() => {
    window.PBOpenFinance.init({ apiBase: "", userId: 1,
      onConnectStart: () => window.PBOpenFinance.destroy(),
      onOpen: () => window.__ev.push("open"),
      onError: (m) => window.__ev.push(["error", m]),
      onClose: () => window.__ev.push("close") });
    window.PBOpenFinance.reconnect("item-11");
  });
  await sleep(300);
  assert.equal(page.__posts.filter((p) => p[0] === "token").length, 0);
  assert.equal(await page.evaluate(() => window.__n ?? 0), 0);
  assert.deepEqual(await ev(page), []);
  await page.__ctx.close();
});

test("A22 init que rejeita e o onError do host chama destroy(): sem onClose depois", async () => {
  const page = await abrir();
  await page.evaluate(() => {
    window.PluggyConnect = function (o) {
      window.__w = o; window.__n = (window.__n || 0) + 1;
      this.init = function () { return Promise.reject(new Error("iframe falhou")); };
    };
    window.PBOpenFinance.init({ apiBase: "", userId: 1,
      onOpen: () => window.__ev.push("open"),
      onError: () => { window.__ev.push("error"); window.PBOpenFinance.destroy(); },
      onClose: () => window.__ev.push("close") });
    window.PBOpenFinance.reconnect("item-11");
  });
  await waitFor(async () => (await ev(page)).length >= 2);
  await sleep(200);
  assert.deepEqual(await ev(page), ["open", "error"]);
  await page.__ctx.close();
});

test("A23 reconnect com id inválido durante uma conexão em curso: ignorado em silêncio", async () => {
  const page = await abrir();
  await page.evaluate(() => window.PBOpenFinance.reconnect("item-11"));
  await widgetPronto(page);
  await page.evaluate(() => { window.PBOpenFinance.reconnect(""); window.PBOpenFinance.reconnect(null); });
  await sleep(200);
  assert.deepEqual(await ev(page), ["open"]);
  assert.equal(await page.evaluate(() => window.__n), 1);
  await page.__ctx.close();
});

test("A24 reconnect com conexão em curso devolve false (sem callback nem pedido); a 1ª e a seguinte ao onClose não", async () => {
  const page = await abrir();
  const r1 = await page.evaluate(() => window.PBOpenFinance.reconnect("item-11"));
  await widgetPronto(page);
  const r2 = await page.evaluate(() => window.PBOpenFinance.reconnect("item-11"));
  await sleep(200);
  assert.equal(r1, undefined); // o evaluate espera a promessa do connect (resolve undefined): o que importa é que NÃO é false
  assert.equal(r2, false);
  assert.equal(page.__posts.length, 1);
  assert.deepEqual(await ev(page), ["open"]);
  await page.evaluate(() => window.__w.onClose());
  const r3 = await page.evaluate(() => window.PBOpenFinance.reconnect("item-11"));
  assert.notEqual(r3, false);
  await page.__ctx.close();
});

/** A reconecta e conclui (save), B começa: devolve a página com `window.__wA` = o widget de A e `window.__w` = o de B. */
async function aDepoisB() {
  const page = await abrir();
  await page.evaluate(() => window.PBOpenFinance.reconnect("item-11"));
  await widgetPronto(page);
  await page.evaluate(() => { window.__wA = window.__w; window.__w.onSuccess({ item: { id: "item-11" } }); });
  await waitFor(async () => (await ev(page)).includes("connected"));
  await page.evaluate(() => { window.__w = null; window.PBOpenFinance.reconnect("item-12"); }); // a guarda foi solta pelo save
  await widgetPronto(page);
  return page;
}

test("A25 o onClose e o onError TARDIOS do widget A, com a conexão B em curso, NÃO chegam ao host; o de B chega", async () => {
  const page = await aDepoisB();
  assert.deepEqual(await tipos(page), ["open", "connected", "open"]);
  await page.evaluate(() => { window.__wA.onError({ message: "tardio" }); window.__wA.onClose(); });
  await sleep(300);
  assert.deepEqual(await tipos(page), ["open", "connected", "open"]);
  await page.evaluate(() => window.__w.onClose());
  await waitFor(async () => (await ev(page)).includes("close"));
  assert.deepEqual(await tipos(page), ["open", "connected", "open", "close"]);
  await page.__ctx.close();
});

test("A25b sem outra conexão em curso o onClose e o onError de A chegam ao host (o fim normal)", async () => {
  const page = await reconectada();
  await page.evaluate(() => { window.__w.onSuccess({ item: { id: "item-11" } }); });
  await waitFor(async () => (await ev(page)).includes("connected"));
  await page.evaluate(() => { window.__w.onClose(); });
  await waitFor(async () => (await ev(page)).includes("close"));
  await page.evaluate(() => { window.__w.onError({ message: "x" }); });
  await waitFor(async () => (await tipos(page)).includes("error"));
  assert.deepEqual(await tipos(page), ["open", "connected", "close", "error"]);
  await page.__ctx.close();
});
