/**
 * Loader compartilhado do `dashboard.js` para os testes de frontend.
 *
 * Nasceu dentro do `dashboard_category_escape.test.mjs` e saiu de lá quando um
 * segundo teste (`sobrou_zero_neutro.test.mjs`) precisou do MESMO arranjo: o
 * `dashboard.js` é script CLÁSSICO de dez mil linhas e só executa até o fim se
 * a página já tiver os `id` que o nível superior dele acessa sem `?.` — senão
 * um throw no topo aborta o arquivo e o teste mede meia medição. Duas cópias
 * dessa lista de IDs seriam duas listas divergindo no primeiro `getElementById`
 * novo (CLAUDE.md §0.7).
 *
 * Não é `*.test.mjs` de propósito: o `node --test tests/frontend/*.test.mjs`
 * não deve tentar rodá-lo como suíte.
 */
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { chromium } from "playwright";
import assert from "node:assert/strict";

export const DASHBOARD_JS = join(
  dirname(fileURLToPath(import.meta.url)), "..", "..", "frontend", "dashboard.js",
);
// A dashboard.html carrega /launch-type-labels.js ANTES do dashboard.js (é lá
// que `LAUNCH_TYPE_LABELS` mora). Injetar aqui é FIDELIDADE à página real, não
// necessidade: desde a guarda `typeof` do dashboard.js o arquivo ausente só
// degrada o rótulo — sem a injeção o `dashboard_category_escape.test.mjs`
// inteiro passa igual, medido em 10/09/2026 com `if (false)` no lugar do
// `if (!semMapa)`. É o caso `semMapa` daquele arquivo que exerce a ausência de
// propósito. (Remedir antes de reusar o resultado.)
export const LABELS_JS = DASHBOARD_JS.replace("dashboard.js", "launch-type-labels.js");

/** IDs que o nível superior do dashboard.js acessa sem `?.` (grep:
    `^document.getElementById("…").`) mais os que o `render()` toca. */
export const IDS = [
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
export const abrirBrowser = async () => (browser ||= await chromium.launch());
export const fecharBrowser = async () => { await browser?.close(); browser = undefined; };
export const novaPagina = () => browser.newPage();

/** `semMapa`: não injeta o /launch-type-labels.js — simula o 404/blip dele. */
export async function loadDashboardJs({ semMapa = false } = {}) {
  const page = await browser.newPage();
  const errs = [];
  page.__errs = errs;
  page.on("pageerror", (e) => errs.push(String(e)));
  await page.setContent(IDS.map((i) => `<div id="${i}"></div>`).join(""));
  await page.evaluate(() => { window.fetch = () => new Promise(() => {}); });
  if (!semMapa) await page.addScriptTag({ path: LABELS_JS });
  await page.addScriptTag({ path: DASHBOARD_JS });
  assert.deepEqual(errs, [], "dashboard.js não executou até o fim");
  return page;
}
