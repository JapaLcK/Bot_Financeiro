/**
 * O modal de receita fixa não pode converter uma receita legada em mensal
 * quando o primeiro salvar falha (issue #454, item 1).
 *
 * O `<select id="recurring-income-frequency">` só tem mensal/anual. Uma receita
 * antiga `once`/`weekly`/`daily` abre com o select em `""` (sem opção) e o PATCH
 * manda `frequency: ""`, que o backend lê como "não mudar"
 * (`db/recurring_income.py`, `update_recurring_income`). Se esse PATCH falha, o
 * `catch` de `saveRecurringIncome` reabre o modal com `{...payload, id}`, e o
 * `payload.frequency` é `""`. Com `rec.frequency || "monthly"` a reabertura
 * marcava "Mensal" e o segundo salvar convertia a receita, mesmo com o backend
 * consertado. O conserto é `??`: só `null`/`undefined` viram mensal.
 *
 * Controle NEGATIVO (medido): volte `rec.frequency ?? "monthly"` para
 * `rec.frequency || "monthly"` em `openRecurringIncomeEditModal`. O 1º caso fica
 * vermelho (reabre em "monthly" e o 2º PATCH manda "monthly").
 * Controle POSITIVO: o 2º e o 3º caso provam que anual e mensal continuam
 * reabrindo e sendo enviadas com a frequência certa; o 4º, que `null` e "Nova
 * receita" continuam abrindo em mensal.
 *
 * Como roda: mesma receita do edit_launch_patch_body.test.mjs. O `dashboard.js`
 * clássico é injetado numa origem de verdade (o `csrfHeaders` lê
 * `document.cookie`), o markup do modal é o que o próprio `dashboard.js` gera, e
 * o PATCH sai pelo `fetch` real e é atendido pelo `page.route`.
 *
 * Rodar:  node --test tests/frontend/receita_legada_modal.test.mjs
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { chromium } from "playwright";

const DASHBOARD_JS = join(
  dirname(fileURLToPath(import.meta.url)), "..", "..", "frontend", "dashboard.js",
);

/** IDs que o nível superior do dashboard.js acessa sem `?.`. */
const IDS = [
  "grid", "bgt-overlay", "bgt-input", "investment-detail-overlay",
  "investment-help-overlay", "edit-launch-overlay", "launch-overlay",
  "launch-valor", "pocket-overlay", "pocket-name", "pocket-history-overlay",
  "card-overlay", "card-name", "card-closing-day", "card-due-day",
  "bill-detail-overlay", "pay-bill-overlay", "pay-bill-receipt-overlay",
  "pay-bill-amount", "overview-heading", "launches-title", "launches-wrap",
  "charts-title", "charts-grid", "alert-banner", "last-update",
  "categories-distribution", "launch-success-toast",
];

let browser;
before(async () => { browser = await chromium.launch(); });
after(async () => { await browser?.close(); });

/** Página com o dashboard.js carregado. Todo PATCH vai para `patches`; o 1º
    responde 500, os seguintes 200. */
async function loadDashboardJs() {
  const page = await browser.newPage();
  const errs = [];
  const patches = [];
  page.on("pageerror", (e) => errs.push(String(e)));
  await page.route("https://pigbank.test/**", (route) => {
    const req = route.request();
    if (req.method() !== "PATCH") {
      return route.fulfill({
        contentType: "text/html",
        body: IDS.map((i) => `<div id="${i}"></div>`).join(""),
      });
    }
    patches.push({ url: req.url(), body: req.postDataJSON() });
    return patches.length === 1
      ? route.fulfill({ status: 500, contentType: "application/json", body: '{"detail":"falhou"}' })
      : route.fulfill({ status: 200, contentType: "application/json", body: '{"ok":true}' });
  });
  await page.goto("https://pigbank.test/dashboard");
  // O boot do dashboard.js não pode navegar: todo fetch fica pendente, menos o PATCH.
  await page.evaluate(() => {
    const real = window.fetch;
    window.fetch = (url, opts) => (opts?.method === "PATCH" ? real(url, opts) : new Promise(() => {}));
  });
  await page.addScriptTag({ path: DASHBOARD_JS });
  assert.deepEqual(errs, [], "dashboard.js não executou até o fim");
  return { page, patches, errs };
}

/** Abre a edição de `rec`, salva (PATCH 500), lê o modal reaberto, dispensa o
    alerta e salva de novo (PATCH 200). */
const falhaEReabre = (page, rec) => page.evaluate(async (r) => {
  window.showToast = () => {};
  window.loadRecurringIncomeView = () => {};
  window.sendRefresh = () => {};
  const freq = () => document.getElementById("recurring-income-frequency").value;
  const aberto = (id) => document.getElementById(id)?.classList.contains("open");

  openRecurringIncomeEditModal(r);
  const aoAbrir = freq();
  const primeiro = saveRecurringIncome();
  for (let i = 0; i < 500 && !aberto("generic-confirm-overlay"); i++) {
    await new Promise((res) => setTimeout(res, 10));
  }
  const reaberto = {
    modal: aberto("recurring-income-edit-overlay"),
    alerta: aberto("generic-confirm-overlay"),
    freq: freq(),
    mes: document.getElementById("recurring-income-month").value,
  };
  document.getElementById("generic-confirm-ok").click();
  await primeiro;
  await saveRecurringIncome();
  return { aoAbrir, reaberto };
}, rec);

const BASE = {
  id: 7, name: "Bônus", amount: 5000, category: "salário", pay_day: 10,
  start_date: "2026-10-10", pay_month: null, is_primary: false, notes: null,
};

test("receita legada 'once': a reabertura depois do erro mantém a frequência vazia", async () => {
  const { page, patches, errs } = await loadDashboardJs();
  const r = await falhaEReabre(page, { ...BASE, frequency: "once" });

  assert.equal(r.aoAbrir, "", "o select abriu com uma opção marcada");
  assert.ok(r.reaberto.modal && r.reaberto.alerta, "o modal não reabriu com o alerta de erro");
  assert.equal(r.reaberto.freq, "", "a reabertura marcou uma frequência que a receita não tem");
  assert.equal(patches.length, 2);
  assert.match(patches[0].url, /\/recurring-incomes\/\d+\/7$/);
  assert.deepEqual(patches.map((p) => [p.body.frequency, p.body.pay_month]), [["", null], ["", null]]);
  assert.equal(patches[1].body.name, "Bônus");
  assert.deepEqual(errs, []);
  await page.close();
});

test("receita anual (mês 10): reabre anual no mês e o PATCH seguinte manda 'annual' (controle positivo)", async () => {
  const { page, patches, errs } = await loadDashboardJs();
  const r = await falhaEReabre(page, { ...BASE, frequency: "annual", pay_month: 10 });

  assert.equal(r.aoAbrir, "annual");
  assert.deepEqual(r.reaberto, { modal: true, alerta: true, freq: "annual", mes: "10" });
  assert.deepEqual(patches.map((p) => [p.body.frequency, p.body.pay_month]), [["annual", 10], ["annual", 10]]);
  assert.deepEqual(errs, []);
  await page.close();
});

test("receita mensal: reabre mensal e o PATCH seguinte manda 'monthly' (controle positivo)", async () => {
  const { page, patches, errs } = await loadDashboardJs();
  const r = await falhaEReabre(page, { ...BASE, frequency: "monthly" });

  assert.equal(r.aoAbrir, "monthly");
  assert.equal(r.reaberto.freq, "monthly");
  assert.deepEqual(patches.map((p) => [p.body.frequency, p.body.pay_month]), [["monthly", null], ["monthly", null]]);
  assert.deepEqual(errs, []);
  await page.close();
});

test("sem frequência (null) e 'Nova receita' continuam abrindo em mensal (controle positivo)", async () => {
  const { page, errs } = await loadDashboardJs();
  const abre = (rec) => page.evaluate((r) => {
    openRecurringIncomeEditModal(r);
    return document.getElementById("recurring-income-frequency").value;
  }, rec);

  assert.equal(await abre({ ...BASE, frequency: null }), "monthly");
  assert.equal(await abre(null), "monthly");
  assert.deepEqual(errs, []);
  await page.close();
});
