/**
 * Dedup de voo do loadExpenseChart (gráfico "evolução de gastos").
 *
 * Na abertura quente o render() roda 2× (snapshot da sessão + WebSocket) e
 * cada render dispara loadExpenseChart(_expensePeriod) ⇒ 2 fetches idênticos
 * a /expenses/daily. O conserto: mesma janela em voo devolve a promise em
 * curso; período diferente ou nada em voo segue no fetch novo.
 *
 * Rodar:  npm run test:frontend
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { chromium } from "playwright";

const DASHBOARD_JS = join(
  dirname(fileURLToPath(import.meta.url)), "..", "..", "frontend", "dashboard.js",
);

const IDS = [
  "grid", "bgt-overlay", "bgt-input", "investment-detail-overlay",
  "investment-help-overlay", "edit-launch-overlay", "launch-overlay",
  "launch-valor", "pocket-overlay", "pocket-name", "pocket-history-overlay",
  "card-overlay", "card-name", "card-closing-day", "card-due-day",
  "bill-detail-overlay", "pay-bill-overlay", "pay-bill-receipt-overlay",
  "pay-bill-amount", "overview-heading", "launches-title", "launches-wrap",
  "charts-title", "charts-grid", "alert-banner", "last-update",
  "categories-distribution",
];

let browser;
before(async () => { browser = await chromium.launch(); });
after(async () => { await browser?.close(); });

async function bootPage() {
  const page = await browser.newPage();
  const errs = [];
  page.on("pageerror", (e) => errs.push(String(e)));
  await page.setContent(IDS.map((i) => `<div id="${i}"></div>`).join(""));
  await page.evaluate(() => { window.fetch = () => new Promise(() => {}); });
  await page.addScriptTag({ path: DASHBOARD_JS });
  assert.deepEqual(errs, [], "dashboard.js não executou até o fim");
  await page.evaluate(() => {
    // fetch contável e CONTROLÁVEL: só resolve quando o teste mandar.
    window._fetches = [];
    window.fetch = (url) => new Promise((resolve) => {
      window._fetches.push({ url, resolve });
    });
    window._resolveAll = () => {
      for (const f of window._fetches.splice(0)) {
        f.resolve({ ok: true, json: async () => ({ data: [] }) });
      }
    };
    window.buildExpenseChart = () => {}; // Chart.js fora do DOM mínimo
  });
  return page;
}

test("2 renders na mesma abertura ⇒ 1 request a /expenses/daily; período novo ⇒ nova", async () => {
  const page = await bootPage();

  // Abertura quente: dois render() chamam loadExpenseChart no mesmo período.
  const emVoo = await page.evaluate(() => {
    loadExpenseChart(30);
    loadExpenseChart(30);
    return window._fetches.map((f) => f.url);
  });
  assert.equal(emVoo.length, 1, `esperava 1 fetch em voo, veio ${emVoo.length}: ${emVoo}`);
  assert.match(emVoo[0], /\/expenses\/daily\/.*days=30/);

  // Troca de período COM voo pendente: dispara nova (semântica preservada).
  const aposTroca = await page.evaluate(() => {
    loadExpenseChart(7);
    return window._fetches.length;
  });
  assert.equal(aposTroca, 2, "trocar de período tinha que disparar novo fetch");

  // Voo encerrado ⇒ próxima chamada do MESMO período volta a buscar (updates).
  const aposResolver = await page.evaluate(async () => {
    window._resolveAll();
    await new Promise((r) => setTimeout(r, 0)); // deixa os finally rodarem
    loadExpenseChart(7);
    return window._fetches.length;
  });
  assert.equal(aposResolver, 1, "update posterior (nada em voo) tinha que buscar de novo");

  await page.close();
});
