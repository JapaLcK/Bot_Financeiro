/**
 * "Próxima fatura" no card expandido do dashboard.
 *
 * A rota `/api/cards` devolvia `next_bill: {total: 0.0}` fixo (TODO Sprint 2
 * que nunca veio) e o `_renderCardItem` renderizava a linha sem condicional:
 * TODO usuário que expandia um cartão lia "Próxima fatura R$ 0,00", com
 * qualquer saldo. As duas pontas foram apagadas.
 *
 * Carregamento igual ao `dashboard_category_escape.test.mjs`: o `dashboard.js`
 * é script clássico, injetado inteiro num DOM mínimo, com `fetch` que nunca
 * resolve pro IIFE de boot não navegar.
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

const CARD = {
  id: 7, name: "Nubank", color: "purple", closing_day: 10, due_day: 17,
  credit_limit: 5000, credit_used: 1200,
  open_bill: { id: 3, total: 1200, paid_amount: 0, due_amount: 1200, period_end: "2026-09-10" },
};

async function metaDoCartao() {
  const page = await browser.newPage();
  const errs = [];
  page.on("pageerror", (e) => errs.push(String(e)));
  await page.setContent(IDS.map((i) => `<div id="${i}"></div>`).join(""));
  await page.evaluate(() => { window.fetch = () => new Promise(() => {}); });
  await page.addScriptTag({ path: DASHBOARD_JS });
  assert.deepEqual(errs, [], "dashboard.js não executou até o fim");

  const labels = await page.evaluate((c) => {
    const host = document.createElement("div");
    host.innerHTML = _renderCardItem(c, 0);
    document.body.appendChild(host);
    return [...host.querySelectorAll(".cc-meta .row .label")].map((e) => e.textContent.trim());
  }, CARD);
  await page.close();
  return labels;
}

test("cartão expandido não mostra mais 'Próxima fatura'", async () => {
  const labels = await metaDoCartao();
  assert.equal(labels.includes("Próxima fatura"), false,
    "a linha fixa em R$ 0,00 voltou ao render");
  // Controle positivo: o resto do bloco continua de pé — sem isto o teste
  // passaria num render que apagou o `cc-meta` inteiro.
  assert.deepEqual(labels,
    ["Fatura aberta", "Limite", "Melhor dia", "Fecha em", "Vence em"]);
});
