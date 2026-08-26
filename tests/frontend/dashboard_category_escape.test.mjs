/**
 * Nome de categoria escrito pelo usuário × HTML do dashboard.
 *
 * O backend passou a PRESERVAR a grafia digitada (acento, apóstrofo,
 * pontuação) em `user_categories` — `McDonald's` vira categoria de verdade.
 * O render tem que aguentar isso:
 *
 *  1. `_renderCategoryPill` monta `onclick='openCategoryEditModal({...})'` com
 *     aspas SIMPLES. `JSON.stringify` não escapa `'`, então um apóstrofo no
 *     nome FECHA o atributo no meio: o resto do JSON vira atributo solto, o
 *     handler sai truncado e o clique estoura SyntaxError — a categoria fica
 *     impossível de renomear/arquivar. O conserto é `escapeHtmlSafe` por fora
 *     do JSON, igual `dashboard.js:1454` já faz nos parcelamentos.
 *  2. O card "Categorias (mês)" da visão geral injetava `c.categoria` cru em
 *     texto, em `data-num="cat_…"` (atributo de aspas DUPLAS) e num
 *     `onclick="openBudget('…')"` que só escapava `'`. A fonte é
 *     `get_top_expense_categories`, que soma `launches` + `credit_transactions`
 *     — e `launches.categoria` recebe texto de terceiro (a tool da IA).
 *
 * Como roda: o `dashboard.js` é script CLÁSSICO de 10 mil linhas. Ele é
 * injetado inteiro numa página com o DOM mínimo que o topo dele exige (os
 * `getElementById(...).addEventListener` de nível superior estouram sem os
 * elementos, e um throw ali abortaria o resto do arquivo). O `fetch` vira uma
 * promessa que nunca resolve pra que o IIFE de boot não navegue nem apague o
 * body. Zero `pageerror` no carregamento é a prova de que o arquivo executou
 * até o fim — sem isso, os `const` do topo ficariam em TDZ e o teste mediria
 * um arquivo pela metade.
 *
 * Rodar:  npm run test:frontend
 *         (ou só este: node --test tests/frontend/dashboard_category_escape.test.mjs)
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { chromium } from "playwright";

const DASHBOARD_JS = join(
  dirname(fileURLToPath(import.meta.url)), "..", "..", "frontend", "dashboard.js",
);

/** IDs que o nível superior do dashboard.js acessa sem `?.` (grep:
    `^document.getElementById("…").`) mais os que o `render()` toca. */
const IDS = [
  "grid", "bgt-overlay", "bgt-input", "investment-detail-overlay",
  "investment-help-overlay", "edit-launch-overlay", "launch-overlay",
  "launch-valor", "pocket-overlay", "pocket-name", "pocket-history-overlay",
  "card-overlay", "card-name", "card-closing-day", "card-due-day",
  "bill-detail-overlay", "pay-bill-overlay", "pay-bill-receipt-overlay",
  "pay-bill-amount", "overview-heading", "launches-title", "launches-wrap",
  "charts-title", "charts-grid", "alert-banner", "last-update",
];

let browser;
before(async () => { browser = await chromium.launch(); });
after(async () => { await browser?.close(); });

async function loadDashboardJs() {
  const page = await browser.newPage();
  const errs = [];
  page.on("pageerror", (e) => errs.push(String(e)));
  await page.setContent(IDS.map((i) => `<div id="${i}"></div>`).join(""));
  await page.evaluate(() => { window.fetch = () => new Promise(() => {}); });
  await page.addScriptTag({ path: DASHBOARD_JS });
  assert.deepEqual(errs, [], "dashboard.js não executou até o fim");
  return page;
}

/** Renderiza uma pílula, injeta no documento e clica nela. */
const clicarPilula = (page, cat) => page.evaluate((c) => {
  window.__got = "NADA";
  window.openCategoryEditModal = (arg) => { window.__got = arg; };
  const host = document.createElement("div");
  host.innerHTML = _renderCategoryPill(c);
  document.body.appendChild(host);
  const pill = host.querySelector(".cat-pill");
  let erro = null;
  try { pill.click(); } catch (e) { erro = String(e); }
  return {
    attrs: [...pill.attributes].map((a) => a.name),
    onclick: pill.getAttribute("onclick"),
    scripts: host.querySelectorAll("script").length,
    nome: pill.querySelector(".cat-name").textContent.trim(),
    got: window.__got,
    erro,
  };
}, cat);

const CAT = { id: 2, emoji: "🍔", color: "#FF2D8E", usage_count: 3, is_system: false };

test("pílula com apóstrofo: atributo íntegro e clique abre o modal", async () => {
  const page = await loadDashboardJs();
  const r = await clicarPilula(page, { ...CAT, name: "McDonald's" });

  // O bug deixava o JSON vazando pra fora do onclick como atributos soltos.
  assert.deepEqual(r.attrs, ["class", "style", "onclick"]);
  assert.equal(r.erro, null);
  assert.equal(r.got?.name, "McDonald's", "o modal recebeu o nome errado");
  assert.equal(r.got?.id, 2);
  assert.match(r.nome, /McDonald's$/);
  await page.close();
});

test("pílula sem apóstrofo continua funcionando (controle positivo)", async () => {
  const page = await loadDashboardJs();
  const r = await clicarPilula(page, { ...CAT, name: "padaria do ze" });
  assert.equal(r.erro, null);
  assert.equal(r.got?.name, "padaria do ze");
  await page.close();
});

test("pílula com <script> no nome: vira texto, não vira nó", async () => {
  const page = await loadDashboardJs();
  const nome = "<script>window.__pwned=1</" + "script>";
  const r = await clicarPilula(page, { ...CAT, name: nome });
  assert.equal(r.scripts, 0, "<script> virou nó dentro da pílula");
  assert.match(r.nome, /<script>window\.__pwned=1<\/script>$/);
  assert.equal(await page.evaluate(() => !!window.__pwned), false);
  await page.close();
});

/** Roda o `render(d)` real com um mês mínimo e devolve o que saiu no #grid.
    O tail do render (investimentos, gráficos) estoura com o DOM reduzido —
    depois de o #grid já estar montado, por isso o try/catch. A asserção de
    que o card de categorias existe é o que prova que chegamos lá. */
const renderOverview = (page, categorias) => page.evaluate((cats) => {
  window.__pwned = false;
  window.__budget = "NADA";
  window.openBudget = (c) => { window.__budget = c; };
  try {
    render({
      year: 2026, month: 8, balance: 0, monthly_income: 0, monthly_expense: 0,
      expense_categories: cats.map((c) => ({ categoria: c, total: 10 })),
      launches: [], pockets: [], investments: [], credit_cards: [], budgets: {},
    });
  } catch { /* tail do render, fora do que este teste mede */ }
  const grid = document.getElementById("grid");
  const btns = [...grid.querySelectorAll(".bgt-btn")];
  let erro = null;
  try { btns[0]?.click(); } catch (e) { erro = String(e); }
  return {
    labels: [...grid.querySelectorAll(".cat-lbl")].map((e) => e.textContent),
    nums: [...grid.querySelectorAll(".cat-val[data-num]")].map((e) => e.dataset.num),
    scripts: grid.querySelectorAll("script").length,
    pwned: !!window.__pwned,
    budget: window.__budget,
    erro,
  };
}, categorias);

test("visão geral: <script> na categoria vira texto, não vira nó", async () => {
  const page = await loadDashboardJs();
  const nome = "<script>window.__pwned=1</" + "script>";
  const r = await renderOverview(page, [nome]);

  assert.equal(r.labels.length, 1, "o card de categorias não foi renderizado");
  assert.equal(r.scripts, 0, "<script> virou nó no card de categorias");
  assert.equal(r.pwned, false);
  assert.equal(r.labels[0], nome, "o nome não chegou íntegro na tela");
  // data-num é a chave da animação de contador: tem que continuar sendo o
  // nome CRU depois do escape (dataset lê o atributo já decodificado).
  assert.deepEqual(r.nums, [`cat_${nome}`]);
  await page.close();
});

test("visão geral: apóstrofo não quebra o botão de orçamento", async () => {
  const page = await loadDashboardJs();
  const r = await renderOverview(page, ["mcdonald's"]);
  assert.equal(r.erro, null);
  assert.equal(r.budget, "mcdonald's");
  await page.close();
});

test("visão geral: categoria com aspas duplas não escapa do data-num", async () => {
  const page = await loadDashboardJs();
  const r = await renderOverview(page, ['pao " quente']);
  assert.deepEqual(r.nums, ['cat_pao " quente']);
  assert.equal(r.budget, 'pao " quente');
  await page.close();
});
