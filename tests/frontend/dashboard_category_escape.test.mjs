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

/* ──────────────────────────────────────────────────────────────────────
 * Varredura por CONSTRUTO (não por nome de variável).
 *
 * O caso da pílula (acima) foi achado procurando `${...categoria...}`. Isso
 * deixou passar 6 irmãos, porque o dado entrava por `JSON.stringify(obj)` ou
 * por uma variável com outro nome (`safeCatJson`, `safeRecJson`, `nameSafe`).
 * O construto é sempre o mesmo: **texto do usuário dentro de um atributo
 * `on*=` de HTML**, com escape artesanal que cobre só metade dos caracteres.
 *
 * O que cada um fazia antes:
 *   dashboard.js:890  `onclick='…openCardEditModal(${JSON.stringify(c)})'`    sem escape
 *   dashboard.js:891  `onclick="…(${JSON.stringify(n).replace(/"/g,'&quot;')})"` só `"`
 *   dashboard.js:1418 `onclick='…openInstAnticipateModal(${JSON.stringify(g.name)}…)'` sem escape
 *   dashboard.js:2178 `JSON.stringify(b).replace(/'/g, "&apos;")`             só `'`
 *   dashboard.js:3459 `JSON.stringify(r).replace(/"/g, "&quot;")`             só `"`
 *   dashboard.js:4189 `escapeHtmlSafe(name).replace(/'/g, "\\'")`             ordem invertida
 *   dashboard.js:4705 `JSON.stringify(r).replace(/"/g, "&quot;")`             só `"`
 *
 * O `&` é o furo comum: nenhum deles escapava. Um nome com `&quot;` LITERAL
 * (que é como o texto chega quando alguém escreve HTML no WhatsApp) é
 * decodificado pelo parser DEPOIS do escape e vira `"` cru dentro do JSON —
 * SyntaxError na compilação do handler, linha inclicável. O `4189` é a mesma
 * doença por outra porta: escapar HTML ANTES de escapar a string JS faz o
 * `&#39;` voltar a ser `'` dentro de `'…'` e fechar o literal.
 *
 * VENENO cobre os três de uma vez: entidade literal, `&` solto e apóstrofo.
 * ────────────────────────────────────────────────────────────────────── */

const VENENO = `pao &quot;quente&quot; & mcdonald's`;

/** Renderiza `window[fn](arg)`, injeta no DOM, clica em `sel` e devolve o que
    o handler recebeu. `got === "NADA"` = handler não rodou (atributo quebrado). */
const clicar = (page, fn, arg, sel, handler) => page.evaluate(([fn, arg, sel, handler]) => {
  window.__got = "NADA";
  window[handler] = (...a) => { window.__got = a; };
  const host = document.createElement("div");
  host.innerHTML = window[fn](arg);
  document.body.appendChild(host);
  const el = host.querySelector(sel);
  let erro = null;
  try { el.click(); } catch (e) { erro = String(e); }
  return { achou: !!el, onclick: el?.getAttribute("onclick"), got: window.__got, erro };
}, [fn, arg, sel, handler]);

/** Um fixture por renderer: [rótulo, função, objeto, seletor, handler, ler o nome]. */
const casos = (nome) => [
  ["_renderBudgetRow (:2181, aspas simples)",
   "_renderBudgetRow",
   { categoria: nome, spent: 30, budget: 100, pct: 30, remaining: 70, status: "verde", color: "#22c55e", emoji: "🍞" },
   ".bar-row", "openBudgetEditModal", (a) => a[0]?.categoria],

  ["_renderCardItem editar (:890, aspas simples)",
   "_renderCardItem",
   { id: 7, name: nome, color: "purple", credit_limit: 1000, credit_used: 100, closing_day: 5, due_day: 12 },
   ".cc-detail-actions .mock-cta.outline", "openCardEditModal", (a) => a[0]?.name],

  ["_renderCardItem excluir (:891, aspas DUPLAS)",
   "_renderCardItem",
   { id: 7, name: nome, color: "purple", credit_limit: 1000, credit_used: 100, closing_day: 5, due_day: 12 },
   ".cc-detail-actions .inst-delete-btn", "openCardDeleteModal", (a) => a[1]],

  ["_renderInstallmentItem antecipar (:1418, aspas simples)",
   "_renderInstallmentItem",
   { group_id: "g1", name: nome, categoria: "mercado", total: 300, paid_amount: 100, remaining_amount: 200,
     installments_total: 3, n_paid: 1, n_pending: 2, valor_parcela: 100, next_due_date: "2026-09-10",
     purchased_at: "2026-07-10",
     parcelas: [
       { installment_no: 1, valor: 100, due_date: "2026-07-10", is_paid: true },
       { installment_no: 2, valor: 100, due_date: "2026-08-10", is_next: true },
       { installment_no: 3, valor: 100, due_date: "2026-09-10" },
     ] },
   ".inst-detail-body .mock-cta.outline", "openInstAnticipateModal", (a) => a[1]],

  ["_renderRecurringRow (:3459, aspas DUPLAS)",
   "_renderRecurringRow",
   { id: 3, name: nome, amount: 50, frequency: "monthly", pay_day: 10, payment_type: "debit" },
   ".tx-row", "openRecurringEditModal", (a) => a[0]?.name],

  ["_renderRecurringIncomeRow (:4705, aspas DUPLAS)",
   "_renderRecurringIncomeRow",
   { id: 4, name: nome, amount: 900, frequency: "monthly", pay_day: 5, is_primary: true },
   ".tx-row", "openRecurringIncomeEditModal", (a) => a[0]?.name],

  ["_renderBillRow apagar (:4189, string JS dentro do atributo)",
   "_renderBillRow",
   { id: 9, name: nome, amount: 80, due_date: "2026-09-15" },
   'button[title="Apagar"]', "deleteBoleto", (a) => a[1]],
];

for (const [rotulo, fn, obj, sel, handler, ler] of casos(VENENO)) {
  test(`${rotulo}: nome com & e apóstrofo não quebra o handler`, async () => {
    const page = await loadDashboardJs();
    const r = await clicar(page, fn, obj, sel, handler);
    assert.equal(r.achou, true, `seletor ${sel} não achou nada`);
    assert.equal(r.erro, null);
    assert.notEqual(r.got, "NADA", `handler não rodou; onclick saiu: ${r.onclick}`);
    assert.equal(ler(r.got), VENENO, "o handler recebeu o nome corrompido");
    await page.close();
  });
}

// Controle positivo do grupo: o caminho legítimo (nome sem nada especial)
// continua entregando o objeto inteiro ao handler.
for (const [rotulo, fn, obj, sel, handler, ler] of casos("padaria do ze")) {
  test(`${rotulo}: nome comum continua funcionando (controle positivo)`, async () => {
    const page = await loadDashboardJs();
    const r = await clicar(page, fn, obj, sel, handler);
    assert.equal(r.erro, null);
    assert.equal(ler(r.got), "padaria do ze");
    await page.close();
  });
}
