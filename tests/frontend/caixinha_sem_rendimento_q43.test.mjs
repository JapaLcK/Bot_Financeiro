/**
 * Q43: caixinha manual não promete mais rendimento simulado.
 *
 * O backend (PR #623) já grava `interest_enabled=false` e ignora `true`, mas a tela
 * ainda mostrava "Rende 110% do CDI · simulado" em caixinha legada e oferecia o
 * checkbox "Rendimento?" nos modais de caixinha e de meta. Aqui: a tela só diz
 * "Sem rendimento" (manual), "Saldo atualizado pelo banco" (Open Finance ativo) ou o
 * aviso de reativar (banco congelado no Grátis), e os formulários não mandam mais
 * `interest_*`.
 *
 * Rodar:  node --test tests/frontend/caixinha_sem_rendimento_q43.test.mjs
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

// Caixinha manual LEGADA: nasceu com juro ligado antes do #623.
const LEGADO = { id: 1, name: "Viagem", balance: 500, description: null, status: "active",
                 interest_enabled: true, interest_rate: 1.1, source: null, of_investment_id: null };
const META = { target_amount: 1000, pct_complete: 50, days_left: null, indicator: "on_track" };

before(async () => { ({ proc: server, origin: ORIGIN } = await startServer());
                     browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

async function abrirDashboard(histPocket = LEGADO) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await ctx.newPage();
  const enviados = [];
  await page.addInitScript(() => {
    window.Chart = function () { return { destroy() {}, update() {}, data: {}, options: {} }; };
    window.Chart.register = () => {};
    window.WebSocket = class { constructor() { this.readyState = 1; } send() {} close() {} };
  });
  await page.route("**/*", (route) => {
    const req = route.request();
    const url = new URL(req.url());
    if (url.origin !== ORIGIN) return route.abort();
    if (/\/history(\?|$)/.test(url.pathname)) {
      return route.fulfill(json({ ok: true, pocket: histPocket, history: [],
                                  totals: { deposits: 0, withdrawals: 0, count: 0 } }));
    }
    if (req.method() === "POST" && /\/pockets\/[^/]+$/.test(url.pathname)) {
      enviados.push({ metodo: "POST", body: JSON.parse(req.postData()) });
      return route.fulfill(json({ ok: true, created: true, pocket: { id: 9, name: "Carro" } }));
    }
    if (req.method() === "PATCH" && /\/meta$/.test(url.pathname)) {
      enviados.push({ metodo: "PATCH", body: JSON.parse(req.postData()) });
      return route.fulfill(json({ ok: true, pocket: {} }));
    }
    if (/\.[a-z0-9]+$/i.test(url.pathname)) return route.continue();
    return route.fulfill(json({}));
  });
  await page.route("**/static/auth-refresh.js", (route) =>
    route.fulfill({ status: 200, contentType: "application/javascript",
                    body: readFileSync(join(FRONTEND, "static", "auth-refresh.js"), "utf8") }));
  await page.goto(`${ORIGIN}/dashboard.html`);
  await page.waitForFunction(() => typeof window.openPocketHistory === "function");
  return { page, enviados };
}

const chavesInterest = (body) => Object.keys(body).filter((k) => k.startsWith("interest_"));

test("caixinha manual legada (110% do CDI) mostra 'Sem rendimento' no card, na meta e no histórico", async () => {
  const { page } = await abrirDashboard();
  const cards = await page.evaluate(([p, m]) =>
    [_renderPocketOnlyCard(p), _renderGoalCard({ ...p, ...m })], [LEGADO, META]);
  await page.evaluate(() => openPocketHistory("Viagem"));
  await page.waitForFunction(() => document.getElementById("pkt-hist-summary").style.display === "grid");
  const sub = await page.textContent("#pkt-hist-sub");
  for (const [onde, txt] of [["card sem meta", cards[0]], ["card de meta", cards[1]], ["histórico", sub]]) {
    assert.doesNotMatch(txt, /CDI|Rende/, `${onde} ainda promete rendimento`);
    assert.match(txt, /Sem rendimento/, `${onde} não diz 'Sem rendimento'`);
  }
  await page.context().close();
});

test("meta do banco diz 'Saldo atualizado pelo banco'; meta manual diz 'Sem rendimento'", async () => {
  const { page } = await abrirDashboard();
  const [banco, manual] = await page.evaluate(([p, m]) => [
    _renderGoalCard({ ...p, ...m, source: "open_finance", of_investment_id: 7, interest_enabled: false }),
    _renderGoalCard({ ...p, ...m, interest_enabled: false }),
  ], [LEGADO, META]);
  assert.match(banco, /Saldo atualizado pelo banco/);
  assert.doesNotMatch(banco, /Sem rendimento/);
  assert.match(manual, /Sem rendimento/);
  await page.context().close();
});

// Banco congelado (Grátis): o saldo NÃO atualiza mais. O rótulo sai de uma função só
// (`_pocketYieldLabel`) nos três lugares; antes o card de meta e o histórico diziam
// "Saldo atualizado pelo banco" numa caixinha que não atualiza.
const BANCO = { ...LEGADO, name: "Nubank", interest_enabled: false, source: "open_finance", of_investment_id: 7 };
const STALE = /Reative seu banco \(plano pago\) pra o saldo voltar a atualizar/;

test("banco congelado: card de meta e histórico pedem pra reativar, não dizem 'atualizado'", async () => {
  const congelada = { ...BANCO, of_plan_active: false };
  const { page } = await abrirDashboard(congelada);
  const meta = await page.evaluate(([p, m]) => _renderGoalCard({ ...p, ...m }), [congelada, META]);
  await page.evaluate(() => openPocketHistory("Nubank"));
  await page.waitForFunction(() => document.getElementById("pkt-hist-summary").style.display === "grid");
  const sub = await page.textContent("#pkt-hist-sub");
  for (const [onde, txt] of [["card de meta", meta], ["histórico", sub]]) {
    assert.match(txt, STALE, `${onde} não pede pra reativar o banco`);
    assert.doesNotMatch(txt, /Saldo atualizado pelo banco/, `${onde} diz que o saldo congelado atualiza`);
  }
  await page.context().close();
});

test("banco ativo: card sem meta diz o mesmo 'Saldo atualizado pelo banco' do card de meta", async () => {
  const { page } = await abrirDashboard();
  const ativa = { ...BANCO, of_plan_active: true };
  const [semMeta, meta] = await page.evaluate(([p, m]) =>
    [_renderPocketOnlyCard(p), _renderGoalCard({ ...p, ...m })], [ativa, META]);
  for (const [onde, txt] of [["card sem meta", semMeta], ["card de meta", meta]]) {
    assert.match(txt, /Saldo atualizado pelo banco/, `${onde} mudou o texto do banco`);
    assert.doesNotMatch(txt, /corretora|Reative/, `${onde} saiu do texto escolhido`);
  }
  await page.context().close();
});

test("Nova caixinha: sem checkbox de rendimento, e o POST cria sem interest_*", async () => {
  const { page, enviados } = await abrirDashboard();
  await page.evaluate(() => openPocketModal());
  assert.equal(await page.locator("#pocket-interest-enabled").count(), 0, "o checkbox de rendimento voltou");
  await page.fill("#pocket-name", "Carro");
  await page.evaluate(() => submitPocket());
  await page.waitForFunction(() =>
    /Caixinha "Carro" criada/.test(document.getElementById("launch-success-toast").textContent));
  assert.equal(enviados.length, 1);
  assert.deepEqual(chavesInterest(enviados[0].body), []);
  assert.equal(enviados[0].body.name, "Carro");
  await page.context().close();
});

test("meta: criar e editar não mandam interest_*, e a edição leva o nome novo", async () => {
  const { page, enviados } = await abrirDashboard();
  await page.evaluate(() => openGoalEditModal(null));
  assert.equal(await page.locator("#goal-interest-enabled").count(), 0, "o checkbox de rendimento voltou na meta");
  await page.fill("#goal-name", "Carro");
  await page.evaluate(() => saveGoal());
  await page.waitForFunction(() => document.getElementById("toast").textContent.includes("Meta criada"));
  assert.deepEqual(enviados.map((e) => e.metodo), ["POST", "PATCH"]);
  for (const e of enviados) assert.deepEqual(chavesInterest(e.body), [], `${e.metodo} levou interest_*`);

  enviados.length = 0;
  await page.evaluate((p) => openGoalEditModal(p), LEGADO);
  await page.fill("#goal-name", "Viagem 2027");
  await page.evaluate(() => saveGoal());
  await page.waitForFunction(() => document.getElementById("toast").textContent.includes("Meta atualizada"));
  assert.deepEqual(enviados.map((e) => e.metodo), ["PATCH"]);
  assert.equal(enviados[0].body.name, "Viagem 2027");
  assert.deepEqual(chavesInterest(enviados[0].body), []);
  await page.context().close();
});
