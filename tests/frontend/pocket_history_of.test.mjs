/**
 * Caixinha do banco (Open Finance) na tela: o que NÃO pode aparecer.
 *
 * O histórico mostrava "Depositar / Sacar / Sacar tudo" em caixinha espelhada do
 * banco. Clicar só falhava no POST (`OF_POCKET_READONLY`, db/pockets.py) — erro
 * genérico depois do clique, num dinheiro que o Pig nem pode mover. A rota
 * `/pockets/{u}/{nome}/history` não devolvia `source`/`of_investment_id`, então o
 * front não tinha como saber.
 *
 * E o selo "via banco" existia só no card SEM meta: assim que o usuário punha uma
 * meta na caixinha do banco, o card perdia a origem (`_renderGoalCard`).
 *
 * Rodar:  node --test tests/frontend/pocket_history_of.test.mjs
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { chromium } from "playwright";
import { startServer } from "./_server.mjs";

const FRONTEND = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "frontend");
let ORIGIN, server, browser;

const json = (body) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const pocket = (extra) => ({
  ok: true,
  pocket: {
    id: 1, name: "Caixinha Nubank", balance: 500, target_amount: null, target_date: null,
    emoji: null, color: null, status: "active", description: null, interest_enabled: false,
    interest_rate: 1, interest_period: "cdi", interest_tax_profile: null,
    last_interest_date: null, source: null, of_investment_id: null, ...extra,
  },
  totals: { deposits: 0, withdrawals: 0, count: 0 },
  history: [],
});

before(async () => { ({ proc: server, origin: ORIGIN } = await startServer());
                     browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

async function abrirHistorico(viewport, payload) {
  const ctx = await browser.newContext({ viewport });
  const page = await ctx.newPage();
  await page.addInitScript(() => {
    window.Chart = function () { return { destroy() {}, update() {}, data: {}, options: {} }; };
    window.Chart.register = () => {};
    window.WebSocket = class { constructor() { this.readyState = 1; } send() {} close() {} };
  });
  await page.route("**/*", (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== ORIGIN) return route.abort();
    if (/\/history(\?|$)/.test(url.pathname)) return route.fulfill(json(payload));
    if (/\.[a-z0-9]+$/i.test(url.pathname)) return route.continue();
    return route.fulfill(json({}));
  });
  await page.route("**/static/auth-refresh.js", (route) =>
    route.fulfill({ status: 200, contentType: "application/javascript",
                    body: readFileSync(join(FRONTEND, "static", "auth-refresh.js"), "utf8") }));
  await page.goto(`${ORIGIN}/dashboard.html`);
  await page.waitForFunction(() => typeof window.openPocketHistory === "function");
  await page.evaluate(() => openPocketHistory("Caixinha Nubank"));
  await page.waitForFunction(() =>
    document.getElementById("pkt-hist-summary").style.display === "grid");
  const visivel = (id) => page.evaluate(
    (i) => getComputedStyle(document.getElementById(i)).display !== "none", id);
  const estado = { botoes: await visivel("pkt-move-actions"), aviso: await visivel("pkt-of-note"),
                   page };
  return estado;
}

for (const [rotulo, viewport] of [["desktop", { width: 1280, height: 800 }],
                                  ["mobile", { width: 390, height: 844 }]]) {
  test(`${rotulo}: caixinha do banco não oferece Depositar/Sacar`, async () => {
    const r = await abrirHistorico(viewport, pocket({ source: "open_finance", of_investment_id: 7 }));
    assert.equal(r.botoes, false, "Depositar/Sacar apareceu numa caixinha read-only");
    assert.equal(r.aviso, true, "faltou dizer que o dinheiro está no banco");
    // o aviso não pode estourar a largura da tela (o nome vem longo do banco)
    const larguras = await r.page.evaluate(() => {
      const n = document.getElementById("pkt-of-note");
      return { nota: n.getBoundingClientRect().width, tela: document.documentElement.clientWidth };
    });
    assert.ok(larguras.nota <= larguras.tela, `nota ${larguras.nota} > tela ${larguras.tela}`);
    await r.page.context().close();
  });

  test(`${rotulo}: caixinha normal continua com Depositar/Sacar`, async () => {
    const r = await abrirHistorico(viewport, pocket({}));   // controle positivo
    assert.equal(r.botoes, true, "a caixinha comum perdeu os botões");
    assert.equal(r.aviso, false);
    await r.page.context().close();
  });
}

test("card de META da caixinha do banco mantém o selo 'via banco'", async () => {
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  await page.route("**/*", (route) => route.request().url().startsWith(ORIGIN)
    ? route.continue() : route.abort());
  await page.setContent("<div id='x'></div>");
  await page.addScriptTag({ path: join(FRONTEND, "dashboard.js") }).catch(() => {});
  await sleep(50);
  const html = await page.evaluate(() => {
    const meta = { name: "Viagem", balance: 500, target_amount: 1000, pct_complete: 50,
                   source: "open_finance", of_investment_id: 7, interest_enabled: false,
                   days_left: null, indicator: "on_track" };
    return [_renderGoalCard(meta), _renderGoalCard({ ...meta, source: null, of_investment_id: null })];
  });
  assert.match(html[0], /via banco/, "meta vinda do banco perdeu o selo");
  assert.doesNotMatch(html[1], /via banco/, "meta comum ganhou selo que não é dela");
  await ctx.close();
});
