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
  "categories-distribution",
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

/* ══════════════════════════════════════════════════════════════════════
 * Ver os lançamentos de uma categoria (dois onclick NOVOS).
 *
 * 1. `.bar-row` da Distribuição do mês → `openCategoryLaunches(nome, janela)`.
 *    `m.categoria` vem de `lastData.expense_categories`, que é alimentado por
 *    `get_top_expense_categories` (launches + credit_transactions) — texto de
 *    terceiro, a tool da IA escreve ali. Mesmo veneno da varredura acima.
 * 2. Botão "Ver lançamentos" do modal de edição → mesma função, sem janela.
 *
 * As LINHAS da lista de lançamentos passam ÍNDICE no onclick, não o objeto:
 * nenhum texto do usuário entra no atributo. O que elas precisam provar é
 * outra coisa — que Editar/Excluir só aparece onde apagar é seguro.
 * ══════════════════════════════════════════════════════════════════════ */

/** Renderiza a Distribuição do mês com as categorias dadas e clica na 1ª linha. */
const clicarDistribuicao = (page, cats, tecla) => page.evaluate(([cats, tecla]) => {
  window.__got = "NADA";
  window.openCategoryLaunches = (...a) => { window.__got = a; };
  lastData = {
    year: 2026, month: 2,
    expense_categories: cats.map((c) => ({ categoria: c, total: 10, count: 1 })),
  };
  _renderCategoriesDistribution([]);
  const row = document.getElementById("categories-distribution").querySelector(".bar-row");
  let erro = null;
  try {
    if (tecla) row.dispatchEvent(new KeyboardEvent("keydown", { key: tecla, bubbles: true }));
    else row.click();
  } catch (e) { erro = String(e); }
  return {
    achou: !!row,
    role: row?.getAttribute("role"),
    tabindex: row?.getAttribute("tabindex"),
    onclick: row?.getAttribute("onclick"),
    scripts: document.getElementById("categories-distribution").querySelectorAll("script").length,
    got: window.__got,
    erro,
  };
}, [cats, tecla || null]);

test("distribuição: clique na linha abre os lançamentos com a janela do mês", async () => {
  const page = await loadDashboardJs();
  const r = await clicarDistribuicao(page, ["mercado"]);
  assert.equal(r.achou, true, "a linha da distribuição não foi renderizada");
  assert.equal(r.erro, null);
  assert.equal(r.role, "button");
  assert.equal(r.tabindex, "0");
  assert.notEqual(r.got, "NADA", `onclick não rodou; saiu: ${r.onclick}`);
  assert.equal(r.got[0], "mercado");
  // fevereiro de 2026 tem 28 dias — o último dia sai de getDate(), não de
  // toISOString (que converteria pro fuso e podia voltar um dia).
  // tipo/includeInternal: a barra soma só despesa e ignora movimento interno.
  // Sem os dois, a lista abria contradizendo o número que foi clicado.
  assert.deepEqual(r.got[1], {
    from: "2026-02-01", to: "2026-02-28", tipo: "despesa", includeInternal: false,
  });
  await page.close();
});

test("distribuição: Enter na linha faz o mesmo que o clique", async () => {
  const page = await loadDashboardJs();
  const r = await clicarDistribuicao(page, ["mercado"], "Enter");
  assert.equal(r.erro, null);
  assert.equal(r.got[0], "mercado");
  await page.close();
});

test("distribuição: nome com & e apóstrofo não quebra o handler", async () => {
  const page = await loadDashboardJs();
  const r = await clicarDistribuicao(page, [VENENO]);
  assert.equal(r.erro, null);
  assert.notEqual(r.got, "NADA", `onclick não rodou; saiu: ${r.onclick}`);
  assert.equal(r.got[0], VENENO, "o handler recebeu o nome corrompido");
  await page.close();
});

test("distribuição: <script> na categoria vira texto, não vira nó", async () => {
  const page = await loadDashboardJs();
  const nome = "<script>window.__pwned=1</" + "script>";
  const r = await clicarDistribuicao(page, [nome]);
  assert.equal(r.scripts, 0, "<script> virou nó na distribuição");
  assert.equal(await page.evaluate(() => !!window.__pwned), false);
  assert.equal(r.got[0], nome);
  await page.close();
});

/** Abre o modal de edição com `cat` e clica em "Ver lançamentos". */
const verLancamentos = (page, cat) => page.evaluate((c) => {
  window.__got = "NADA";
  window.openCategoryLaunches = (...a) => { window.__got = a; };
  openCategoryEditModal(c);
  const row = document.getElementById("cat-edit-launches-row");
  const visivel = row.style.display !== "none";
  let erro = null;
  try { row.querySelector("button").click(); } catch (e) { erro = String(e); }
  return {
    visivel,
    editAberto: document.getElementById("cat-edit-overlay").classList.contains("open"),
    got: window.__got,
    erro,
  };
}, cat);

test('modal de edição: "Ver lançamentos" fecha o de edição e abre a lista', async () => {
  const page = await loadDashboardJs();
  const r = await verLancamentos(page, { ...CAT, name: VENENO });
  assert.equal(r.visivel, true, 'o botão "Ver lançamentos" não apareceu na edição');
  assert.equal(r.erro, null);
  assert.deepEqual(r.got, [VENENO, { backToEdit: true }]);
  // Um overlay por vez: todo .overlay é z-index:800 e cada ESC document-level
  // fecha o SEU — com dois abertos, um ESC fecharia os dois.
  assert.equal(r.editAberto, false, "o modal de edição ficou aberto por baixo");
  await page.close();
});

test('modal de edição: "Nova categoria" não mostra "Ver lançamentos"', async () => {
  const page = await loadDashboardJs();
  const r = await page.evaluate(() => {
    openCategoryEditModal(null);
    return document.getElementById("cat-edit-launches-row").style.display;
  });
  assert.equal(r, "none", "categoria nova não tem lançamento pra listar");
  await page.close();
});

/* ── A lista em si: quem pode ser apagado e quem não pode ────────────────
 * O `id` nulo na perna do crédito (db/accounts.py) é o que impede o delete de
 * uma compra de cartão de ir pro endpoint de launches e apagar OUTRA linha —
 * `editable = l.id != null && !l.is_internal_movement` (dashboard.js).
 * Aqui a prova é de ponta: resposta da rota → adaptador → detalhe na tela. */

/* CONTRATO: as chaves destes objetos são as MESMAS que
 * `list_launches_by_category` (db/accounts.py) devolve e a rota
 * (frontend/routes/categories.py) repassa. Duas listas escritas à mão, e antes
 * ninguém as comparava: renomear `has_time` na rota deixava os 51 testes daqui
 * verdes e a tela quebrada. `tests/test_routes_categories.py::
 * test_chaves_da_rota_batem_com_a_fixture_do_frontend` compara as duas — mesmo
 * remédio de `tests/test_phosphor_subset.py` para os ícones (§0.7 do CLAUDE.md).
 * Mexeu numa lista, o teste cobra a outra. */
const LINHAS = [
  { tipo: "despesa", valor: 90, categoria: "saúde", descricao: "farmacia",
    nota: "farmacia", alvo: null, data: "2026-02-10",
    criado_em: "2026-02-10T09:00:00-03:00", posted_at: "2026-02-10",
    has_time: false, fonte: "credito", user_seq: null,
    id: null, is_internal_movement: false },
  { tipo: "despesa", valor: 50, categoria: "saúde", descricao: "consulta",
    nota: "consulta", alvo: null, data: "2026-02-09",
    criado_em: "2026-02-09T09:00:00-03:00", posted_at: "2026-02-09",
    has_time: true, fonte: "launches", user_seq: 7,
    id: 4242, is_internal_movement: false },
  { tipo: "despesa", valor: 700, categoria: "saúde", descricao: "pagamento da fatura",
    nota: "pagamento da fatura", alvo: null, data: "2026-02-08",
    criado_em: "2026-02-08T09:00:00-03:00", posted_at: "2026-02-08",
    has_time: true, fonte: "launches", user_seq: 8,
    id: 4243, is_internal_movement: true },
];

/** Abre a lista com a resposta acima mockada e clica na linha `idx`. */
const abrirLista = (page, idx) => page.evaluate(async (idx) => {
  window.fetch = () => Promise.resolve({
    ok: true,
    json: () => Promise.resolve({
      ok: true,
      launches: window.__LINHAS,
      resumo: { n_total: 312, despesa: 840, receita: 0 },
    }),
  });
  await openCategoryLaunches("saúde", {});
  const listaAberta = document.getElementById("cat-launches-overlay").classList.contains("open");
  const linhas = [...document.querySelectorAll("#cl-list .bar-row")];
  const foot = document.getElementById("cl-foot").textContent;
  linhas[idx].click();
  const det = document.getElementById("launch-detail-overlay");
  return {
    listaAberta,
    nLinhas: linhas.length,
    foot,
    listaEscondida: !document.getElementById("cat-launches-overlay").classList.contains("open"),
    detalheAberto: det.classList.contains("open"),
    desc: document.getElementById("ld-desc").textContent,
    editar: document.getElementById("ld-edit").style.display,
    excluir: document.getElementById("ld-delete").style.display,
  };
}, idx);

const comLinhas = async (page) => {
  await page.evaluate((l) => { window.__LINHAS = l; }, LINHAS);
  return page;
};

test("lista: compra de cartão abre em leitura (sem Editar/Excluir)", async () => {
  const page = await comLinhas(await loadDashboardJs());
  const r = await abrirLista(page, 0);
  assert.equal(r.listaAberta, true);
  assert.equal(r.nLinhas, 3);
  // sem paginação: o rodapé conta o total REAL, não o que coube na tela
  assert.match(r.foot, /Mostrando 3 de 312 lançamentos/);
  assert.equal(r.detalheAberto, true);
  assert.equal(r.listaEscondida, true, "os dois overlays ficaram abertos juntos");
  assert.equal(r.desc, "farmacia");
  assert.equal(r.editar, "none", "cartão não tem id em launches — Editar apagaria outra linha");
  assert.equal(r.excluir, "none");
  await page.close();
});

test("lista: linha comum de launches abre COM Editar e Excluir (controle positivo)", async () => {
  const page = await comLinhas(await loadDashboardJs());
  const r = await abrirLista(page, 1);
  assert.equal(r.desc, "consulta");
  assert.equal(r.editar, "");
  assert.equal(r.excluir, "");
  await page.close();
});

test("lista: movimentação interna abre em leitura", async () => {
  const page = await comLinhas(await loadDashboardJs());
  const r = await abrirLista(page, 2);
  assert.equal(r.desc, "pagamento da fatura");
  assert.equal(r.editar, "none");
  assert.equal(r.excluir, "none");
  await page.close();
});

test("lista: um ESC fecha só a lista e devolve o usuário ao modal de edição", async () => {
  const page = await comLinhas(await loadDashboardJs());
  const r = await page.evaluate(async () => {
    window.fetch = () => Promise.resolve({
      ok: true,
      json: () => Promise.resolve({ ok: true, launches: window.__LINHAS, resumo: { n_total: 3, despesa: 840, receita: 0 } }),
    });
    openCategoryEditModal({ id: 2, name: "saúde", emoji: "🍔", color: "#FF2D8E", is_system: false });
    document.getElementById("cat-edit-launches-row").querySelector("button").click();
    await new Promise((r) => setTimeout(r, 0));
    const listaAberta = document.getElementById("cat-launches-overlay").classList.contains("open");
    const editDurante = document.getElementById("cat-edit-overlay").classList.contains("open");
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    return {
      listaAberta, editDurante,
      listaDepois: document.getElementById("cat-launches-overlay").classList.contains("open"),
      editDepois: document.getElementById("cat-edit-overlay").classList.contains("open"),
    };
  });
  assert.equal(r.listaAberta, true, "a lista não abriu");
  assert.equal(r.editDurante, false, "o modal de edição ficou empilhado embaixo");
  assert.equal(r.listaDepois, false, "o ESC não fechou a lista");
  assert.equal(r.editDepois, true, "o ESC não devolveu o usuário ao modal de edição");
  await page.close();
});

test("lista: fechar o detalhe reabre a lista de onde veio", async () => {
  const page = await comLinhas(await loadDashboardJs());
  const r = await page.evaluate(async () => {
    window.fetch = () => Promise.resolve({
      ok: true,
      json: () => Promise.resolve({ ok: true, launches: window.__LINHAS, resumo: { n_total: 3, despesa: 840, receita: 0 } }),
    });
    await openCategoryLaunches("saúde", {});
    document.querySelectorAll("#cl-list .bar-row")[0].click();
    document.getElementById("ld-close").click();
    await new Promise((r) => setTimeout(r, 0));
    return {
      detalhe: document.getElementById("launch-detail-overlay").classList.contains("open"),
      lista: document.getElementById("cat-launches-overlay").classList.contains("open"),
      nLinhas: document.querySelectorAll("#cl-list .bar-row").length,
    };
  });
  assert.equal(r.detalhe, false);
  assert.equal(r.lista, true, "fechar o detalhe deixou o usuário sem lista nenhuma");
  assert.equal(r.nLinhas, 3);
  await page.close();
});

/* ══════════════════════════════════════════════════════════════════════
 * Rodada 2 — o que o ataque do Tester achou e este grupo trava:
 *
 *  A1. Editar pela lista gravava o ALVO por cima da NOTA. A rota passou a
 *      devolver `nota`/`alvo` crus e o adaptador parou de fabricar
 *      `nota = descricao` (que é o alvo quando existe).
 *  A2. Duas aberturas seguidas: a resposta da PRIMEIRA chegava por último e
 *      pintava as linhas dela sob o título da segunda — clicar numa linha abria
 *      o detalhe (com Excluir) de um lançamento de outra categoria.
 *  A4. Erro da API virava JSON cru na tela.
 *  A5. "Data e hora" abria vazio pela lista e preenchido pela Visão Geral.
 *  A7/A8. Estado (`_catLaunchesCtx`, foco guardado) sobrevivendo ao fechamento.
 *
 * O editor de lançamento é o markup REAL de dashboard.html (recortado do
 * arquivo, não copiado): copiar deixaria o teste verde depois de o markup mudar.
 * ══════════════════════════════════════════════════════════════════════ */

import { readFileSync } from "node:fs";

const DASHBOARD_HTML = join(
  dirname(fileURLToPath(import.meta.url)), "..", "..", "frontend", "dashboard.html",
);

/** Recorta o bloco de nível superior `<div … id="X">…</div>` do dashboard.html. */
function blocoDoHtml(id) {
  const linhas = readFileSync(DASHBOARD_HTML, "utf8").split("\n");
  const ini = linhas.findIndex((l) => l.startsWith("<div") && l.includes(`id="${id}"`));
  assert.ok(ini >= 0, `bloco #${id} sumiu do dashboard.html`);
  const fim = linhas.findIndex((l, i) => i > ini && l === "</div>");
  assert.ok(fim > ini, `bloco #${id} não fecha na coluna 0`);
  return linhas.slice(ini, fim + 1).join("\n");
}

const EDIT_LAUNCH_HTML = blocoDoHtml("edit-launch-overlay");

const DASHBOARD_CSS = join(
  dirname(fileURLToPath(import.meta.url)), "..", "..", "frontend", "dashboard.css",
);

/** Como loadDashboardJs, mas com o modal de edição de lançamento DE VERDADE. */
async function loadComEditor() {
  const page = await browser.newPage();
  const errs = [];
  page.on("pageerror", (e) => errs.push(String(e)));
  await page.setContent(
    IDS.filter((i) => i !== "edit-launch-overlay").map((i) => `<div id="${i}"></div>`).join("")
    + EDIT_LAUNCH_HTML,
  );
  await page.evaluate(() => { window.fetch = () => new Promise(() => {}); });
  await page.addScriptTag({ path: DASHBOARD_JS });
  assert.deepEqual(errs, [], "dashboard.js não executou até o fim");
  return page;
}

/** Uma linha da rota, com os dois campos que o editor lê. */
const linhaReal = (o) => ({
  tipo: "despesa", valor: 12, categoria: "mercado", descricao: "Padaria do Ze",
  nota: "pao integral", alvo: "Padaria do Ze", data: "2026-02-10",
  criado_em: "2026-02-10T09:00:00-03:00", fonte: "launches", user_seq: 3,
  id: 4242, is_internal_movement: false, ...o,
});

/** Abre a lista com `linhas`, clica na 1ª e manda Editar. Devolve o editor. */
const editarPelaLista = (page, linhas) => page.evaluate(async (linhas) => {
  window.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({
    ok: true, launches: linhas, resumo: { n_total: linhas.length, despesa: 12, receita: 0 },
  }) });
  await openCategoryLaunches("mercado", {});
  document.querySelectorAll("#cl-list .bar-row")[0].click();
  document.getElementById("ld-edit").click();
  return {
    aberto: document.getElementById("edit-launch-overlay").classList.contains("open"),
    nota: document.getElementById("edit-launch-nota").value,
    data: document.getElementById("edit-launch-data").value,
    ctx: _catLaunchesCtx,
    foco: _catLaunchesReturnFocus,
  };
}, linhas);

test("A1: editar pela lista pré-preenche a NOTA real, nunca o alvo", async () => {
  // Forma exata de recurring_charger.py / db/bills.py / db/cards.py: alvo E nota
  // preenchidos e DIFERENTES. `descricao` (o rótulo) é o alvo — salvar com ele
  // no campo Descrição gravava o alvo por cima da nota, sem o usuário ver.
  const page = await loadComEditor();
  const r = await editarPelaLista(page, [linhaReal({
    descricao: "recorrente:Netflix", alvo: "recorrente:Netflix",
    nota: "Cobrança automática · Netflix",
  })]);
  assert.equal(r.aberto, true, "o editor não abriu");
  assert.equal(r.nota, "Cobrança automática · Netflix",
    `o editor abriu com Nota="${r.nota}" — salvar assim grava o alvo por cima da nota real`);
  await page.close();
});

test("A5: o campo Data e hora abre preenchido (igual à Visão Geral)", async () => {
  const page = await loadComEditor();
  const r = await editarPelaLista(page, [linhaReal({})]);
  assert.equal(r.data, "2026-02-10T09:00",
    `Data abriu "${r.data}" — a lista mandava só o campo data (um date) e o input ficava vazio`);
  await page.close();
});

test("A6: sem alvo e sem nota, o editor abre VAZIO (não salva um travessão)", async () => {
  const page = await loadComEditor();
  const r = await editarPelaLista(page, [linhaReal({ descricao: "—", nota: null, alvo: null })]);
  assert.equal(r.nota, "", `o editor abriu com Nota="${r.nota}"`);
  await page.close();
});

/* ══ C2. Editar um lançamento SEM categoria não pode categorizá-lo ═════════
   A lista fabricava `coalesce(categoria,'outros')` (rótulo de mensagem de
   WhatsApp) e o editor mandava esse texto de volta no PATCH: mexer só na data
   ou na nota GRAVAVA "outros" numa transação que o usuário nunca categorizou.
   Mesma classe do A1 (o `nota` fabricado a partir de `descricao`) — o campo vai
   CRU e a tela decide como exibir.

   E o remédio não é só o SQL: com `categoria` nula o `<select>` cai sozinho na
   PRIMEIRA opção ("alimentação"), que é PIOR que "outros". Por isso a opção
   "— sem categoria —" (value "") e a omissão da chave no corpo do PATCH.

   Controle NEGATIVO: volte `if (categoria) reqBody.categoria = categoria;` para
   `const reqBody = { categoria, nota };` — o 1º teste fica vermelho.
   Controle POSITIVO: o 2º teste (lançamento COM categoria continua mandando a
   dele). */

/** Edita pela lista, troca a data e devolve o CORPO do PATCH que saiu. */
const salvarPelaLista = (page, linha) => page.evaluate(async (linha) => {
  // `document.cookie` é inacessível em about:blank (o setContent do harness) e
  // o `csrfHeaders` real estoura ANTES do fetch. Só o cookie é falso; o
  // csrfHeaders e o submitEditLaunch continuam os de produção.
  window.getCookie = () => "tok";
  // o toast de sucesso é um elemento real do dashboard.html que o DOM mínimo
  // do harness não tem — sem ele o `catch` do submit vira um erro fantasma
  document.body.insertAdjacentHTML("beforeend", '<div id="launch-success-toast"></div>');
  window.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({
    ok: true, launches: [linha], resumo: { n_total: 1, despesa: 12, receita: 0 },
  }) });
  await openCategoryLaunches("sem categoria", {});
  document.querySelectorAll("#cl-list .bar-row")[0].click();
  document.getElementById("ld-edit").click();
  const selecionado = document.getElementById("edit-launch-categoria").value;
  const rotulos = [...document.getElementById("edit-launch-categoria").options]
    .map((o) => o.text);
  // mexe SÓ na data — o campo que não tem nada a ver com categoria
  document.getElementById("edit-launch-data").value = "2026-02-11T09:00";

  let corpo = null;
  window.fetch = (u, o) => {
    corpo = JSON.parse(o.body);
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true }) });
  };
  await submitEditLaunch();
  return {
    selecionado, rotulos, corpo,
    erro: document.getElementById("edit-launch-error").textContent,
  };
}, linha);

test("C2: editar só a data de um lançamento SEM categoria não grava categoria",
  async () => {
    const page = await loadComEditor();
    const r = await salvarPelaLista(page, linhaReal({ categoria: null }));
    assert.equal(r.erro, "", `o editor barrou o salvamento: "${r.erro}"`);
    assert.ok(r.corpo, "o PATCH não chegou a sair");
    assert.equal("categoria" in r.corpo, false,
      `o PATCH levou categoria=${JSON.stringify(r.corpo.categoria)} numa transação `
      + "que estava sem categoria");
    assert.equal(r.corpo.criado_em?.slice(0, 10), "2026-02-11", r.corpo);
    assert.equal(r.selecionado, "", `o <select> abriu em "${r.selecionado}"`);
    assert.ok(r.rotulos.includes("— sem categoria —"), r.rotulos.join(" | "));
    await page.close();
  });

test("C2 controle: lançamento COM categoria continua mandando a dele", async () => {
  const page = await loadComEditor();
  const r = await salvarPelaLista(page, linhaReal({ categoria: "mercado" }));
  assert.equal(r.erro, "", `o editor barrou o salvamento: "${r.erro}"`);
  assert.equal(r.corpo.categoria, "mercado", r.corpo);
  assert.equal(r.rotulos.includes("— sem categoria —"), false,
    "a opção de vazio apareceu num lançamento que TEM categoria");
  await page.close();
});

test("A8: depois de Editar, o ctx e o foco guardado da lista morrem", async () => {
  // `_catLaunchesCtx != null` significa "existe lista pra onde voltar". Depois
  // de Editar não existe: quem fecha o editor volta pro dashboard.
  const page = await loadComEditor();
  const r = await editarPelaLista(page, [linhaReal({})]);
  assert.equal(r.ctx, null, `_catLaunchesCtx ficou de pé: ${JSON.stringify(r.ctx)}`);
  assert.equal(r.foco, null, "_catLaunchesReturnFocus ficou de pé");
  await page.close();
});

test("A2: resposta da categoria ANTERIOR não pinta a lista da atual", async () => {
  const page = await loadDashboardJs();
  const r = await page.evaluate(async () => {
    const pend = [];
    window.fetch = (url) => new Promise((res) => { pend.push({ url, res }); });
    const resp = (rows) => ({ ok: true, json: () => Promise.resolve({
      ok: true, launches: rows, resumo: { n_total: rows.length, despesa: 1, receita: 0 },
    }) });
    const linha = (o) => ({ tipo: "despesa", valor: 5, categoria: "x", nota: "n",
      alvo: null, descricao: "d", data: "2026-02-10", criado_em: "2026-02-10T09:00:00-03:00",
      fonte: "launches", user_seq: 1, id: 1, is_internal_movement: false, ...o });
    // clica em "mercado" e, antes de carregar, clica em "saúde"
    openCategoryLaunches("mercado", { from: "2026-02-01", to: "2026-02-28" });
    openCategoryLaunches("saúde", { from: "2026-02-01", to: "2026-02-28" });
    // a rede devolve saúde primeiro e mercado DEPOIS
    pend[1].res(resp([linha({ nota: "REMEDIO", categoria: "saúde", id: 11 })]));
    await new Promise((r) => setTimeout(r, 20));
    pend[0].res(resp([linha({ nota: "FEIRA", categoria: "mercado", id: 22, valor: 900 })]));
    await new Promise((r) => setTimeout(r, 20));
    document.querySelectorAll("#cl-list .bar-row")[0].click();
    return {
      titulo: document.getElementById("cl-title").textContent,
      linhas: [...document.querySelectorAll("#cl-list .bar-row .name")].map((e) => e.textContent),
      detalhe: document.getElementById("ld-desc").textContent,
      ctx: _catLaunchesCtx && _catLaunchesCtx.nome,
      pedidos: pend.length,
    };
  });
  assert.equal(r.pedidos, 2, "o segundo clique não chegou a pedir nada");
  assert.equal(r.titulo, "Lançamentos em saúde");
  assert.deepEqual(r.linhas, ["REMEDIO"],
    `título diz "${r.titulo}" e a lista mostra ${JSON.stringify(r.linhas)}`);
  assert.equal(r.detalhe, "REMEDIO",
    "o clique na linha abriu o detalhe (com Excluir) de outra categoria");
  assert.equal(r.ctx, "saúde");
  await page.close();
});

test("A7: ESC durante o carregamento — a resposta atrasada não repovoa nada", async () => {
  const page = await loadDashboardJs();
  const r = await page.evaluate(async () => {
    let solta;
    window.fetch = () => new Promise((res) => { solta = res; });
    openCategoryLaunches("mercado", {});
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    const fechouNaHora = !document.getElementById("cat-launches-overlay").classList.contains("open");
    solta({ ok: true, json: () => Promise.resolve({ ok: true, launches: [
      { tipo: "despesa", valor: 1, categoria: "mercado", descricao: "TARDIO", nota: "TARDIO",
        alvo: null, data: "2026-02-10", criado_em: "2026-02-10T09:00:00-03:00",
        fonte: "launches", user_seq: 1, id: 1, is_internal_movement: false },
    ], resumo: { n_total: 1, despesa: 1, receita: 0 } }) });
    await new Promise((r) => setTimeout(r, 20));
    return {
      fechouNaHora,
      linhas: document.querySelectorAll("#cl-list .bar-row").length,
      ctx: _catLaunchesCtx,
      rows: _catLaunchesRows.length,
    };
  });
  assert.equal(r.fechouNaHora, true);
  assert.equal(r.linhas, 0, "a resposta atrasada renderizou dentro de um modal já fechado");
  assert.equal(r.ctx, null);
  assert.equal(r.rows, 0, "_catLaunchesRows ficou populado com o modal fechado");
  await page.close();
});

test("A4: erro da API não vira JSON cru na tela", async () => {
  const page = await loadDashboardJs();
  const txt = await page.evaluate(async () => {
    // 402 do gate de plano: {"detail":{"error":"subscription_required"}}.
    window.fetch = () => Promise.resolve({
      ok: false, status: 402,
      json: () => Promise.resolve({ detail: { error: "subscription_required" } }),
      text: () => Promise.resolve('{"detail":{"error":"subscription_required"}}'),
    });
    await openCategoryLaunches("mercado", {});
    return document.getElementById("cl-list").textContent;
  });
  assert.ok(!/detail|subscription_required|[{}[\]]|object Object/.test(txt),
    `mensagem crua na UI: ${JSON.stringify(txt)}`);
  assert.match(txt, /402/, "a mensagem tem que dizer QUE erro foi");
  await page.close();
});

test("erro com detail string mostra a mensagem do servidor", async () => {
  // Controle positivo do A4: o conserto não pode ter engolido a mensagem boa.
  const page = await loadDashboardJs();
  const txt = await page.evaluate(async () => {
    window.fetch = () => Promise.resolve({
      ok: false, status: 400,
      json: () => Promise.resolve({ detail: "Janela inválida: 'from' é depois de 'to'." }),
    });
    await openCategoryLaunches("mercado", {});
    return document.getElementById("cl-list").textContent;
  });
  assert.match(txt, /Janela inválida/);
  await page.close();
});

test("estado vazio diz o que fazer (mandar o gasto pelo WhatsApp)", async () => {
  const page = await loadDashboardJs();
  // A CSS entra aqui de propósito: sem ela, `hidden` esconderia o resumo pela
  // regra de UA e o teste passaria sem medir nada — é `.cl-sum{display:flex}`
  // que precisa ser vencida.
  await page.addStyleTag({ path: DASHBOARD_CSS });
  const r = await page.evaluate(async () => {
    window.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({
      ok: true, launches: [], resumo: { n_total: 0, despesa: 0, receita: 0 } }) });
    await openCategoryLaunches("padaria", {});
    return {
      txt: document.getElementById("cl-list").textContent,
      sticker: document.querySelector("#cl-list img")?.getAttribute("src"),
      // display computado, não a propriedade `hidden`: `.cl-sum{display:flex}`
      // vence a regra de UA e o resumo da categoria anterior ficava na tela.
      sumEscondido: getComputedStyle(document.getElementById("cl-sum")).display,
    };
  });
  assert.match(r.txt, /WhatsApp/);
  assert.match(r.txt, /padaria/);
  assert.ok(r.sticker?.startsWith("/brand/stickers/"), r.sticker);
  assert.equal(r.sumEscondido, "none",
    "o resumo do pedido ANTERIOR continuou na tela por cima do estado vazio");
  await page.close();
});

test("resumo fica ACIMA da lista e a contagem no rodapé", async () => {
  const page = await loadDashboardJs();
  const r = await page.evaluate(async () => {
    window.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({
      ok: true,
      launches: [{ tipo: "despesa", valor: 50, categoria: "saúde", descricao: "consulta",
        nota: "consulta", alvo: null, data: "2026-02-09",
        criado_em: "2026-02-09T09:00:00-03:00", fonte: "launches", user_seq: 7,
        id: 4242, is_internal_movement: false }],
      resumo: { n_total: 312, despesa: 840, receita: 120 } }) });
    await openCategoryLaunches("saúde", { from: "2026-02-01", to: "2026-02-28",
                                          tipo: "despesa", includeInternal: false });
    const sum = document.getElementById("cl-sum");
    return {
      sum: sum.textContent,
      verdeSoNaEntrada: [...sum.querySelectorAll("span")].filter(e => e.className === "cl-in").length,
      antesDaLista: !!(sum.compareDocumentPosition(document.getElementById("cl-list"))
                       & Node.DOCUMENT_POSITION_FOLLOWING),
      foot: document.getElementById("cl-foot").textContent,
      sub: document.getElementById("cl-sub").textContent,
    };
  });
  assert.match(r.sum, /R\$ 840,00/);
  assert.match(r.sum, /R\$ 120,00/);
  assert.equal(r.verdeSoNaEntrada, 1, "verde é só pra valor positivo (entradas)");
  assert.equal(r.antesDaLista, true, "o número que o usuário veio ver ficou embaixo da lista");
  assert.match(r.foot, /Mostrando 1 de 312 lançamentos/);
  // O subtítulo tem que dizer QUAL conjunto está na tela — é o que impede a
  // lista de parecer contradizer a barra clicada.
  assert.match(r.sub, /Despesas de Fevereiro/);
  await page.close();
});

/* ══════════════════════════════════════════════════════════════════════
 * Rodada 3 — a máquina de overlays ganhou o eixo que faltava.
 *
 * B1. ORDEM DE REGISTRO dos keydown de ESC. Os `_ensure*Modal` são
 *     preguiçosos, então quem o usuário abriu primeiro registra primeiro e,
 *     em bolha, roda primeiro no MESMO `document`. Com o detalhe aberto POR
 *     CIMA da lista, um ESC (1) fazia o handler do detalhe fechar e REABRIR a
 *     lista sincronamente e (2) o handler da lista, registrado depois, via
 *     `open` e a fechava — um ESC levava os dois. Só acontecia se o usuário
 *     tivesse aberto algum detalhe ANTES (Visão Geral/Histórico), que é a
 *     ordem de uso normal. O conserto é captura + stopPropagation, o mesmo
 *     remédio do #generic-confirm-overlay (dashboard.js:2655).
 * B2. `has_time` + `posted_at` vêm da rota; a lista imprime a HORA onde a
 *     Visão Geral imprime.
 * B5. Trap de Tab no modal de DETALHE: sem ele o Tab alcançava a linha da
 *     Distribuição atrás do overlay e o Enter abria um segundo overlay.
 * B6. `readApiError` voltou a aproveitar `detail.message`/`detail.code`.
 * B7. "desde" com ANO.
 * ══════════════════════════════════════════════════════════════════════ */

const UMA_LINHA = [
  { tipo: "despesa", valor: 50, categoria: "saúde", descricao: "consulta",
    nota: "consulta", alvo: null, data: "2026-03-10",
    criado_em: "2026-03-10T00:30:00-03:00", posted_at: null, has_time: true,
    fonte: "launches", user_seq: 7, id: 4242, is_internal_movement: false },
];

/** Mocka a rota e abre a lista. `ordem` decide QUEM registra o keydown antes. */
const mockLista = (page, linhas) => page.evaluate((l) => {
  window.__L = l;
  window.__mock = () => {
    window.fetch = () => Promise.resolve({
      ok: true,
      json: () => Promise.resolve({
        ok: true, launches: window.__L,
        resumo: { n_total: 1, despesa: 50, receita: 0 },
        window: { from: null, to: null },
      }),
    });
  };
}, linhas);

test("B1: ESC no detalhe aberto pela lista, com o detalhe registrado PRIMEIRO", async () => {
  // A ordem que quebrava: o usuário viu um detalhe na Visão Geral antes de ir
  // em Categorias, então `_ensureLaunchDetailModal` rodou primeiro.
  const page = await loadDashboardJs();
  await mockLista(page, UMA_LINHA);
  const r = await page.evaluate(async () => {
    _renderedLaunches = [{ id: 1, tipo: "despesa", valor: 9, nota: "cafe",
                           criado_em: "2026-02-01T10:00:00-03:00" }];
    openLaunchDetail(0);                      // registra o keydown do detalhe
    document.getElementById("ld-close").click();

    window.__mock();
    await openCategoryLaunches("saúde", {});  // registra o keydown da lista
    document.querySelectorAll("#cl-list .bar-row")[0].click();
    const detalheAntes = document.getElementById("launch-detail-overlay").classList.contains("open");

    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    await new Promise((res) => setTimeout(res, 0));
    return {
      detalheAntes,
      detalhe: document.getElementById("launch-detail-overlay").classList.contains("open"),
      lista: document.getElementById("cat-launches-overlay").classList.contains("open"),
    };
  });
  assert.equal(r.detalheAntes, true, "o detalhe não abriu pela lista");
  assert.equal(r.detalhe, false, "o ESC não fechou o detalhe");
  assert.equal(r.lista, true,
    "UM ESC fechou o detalhe E a lista: o usuário caiu no dashboard, não voltou pra lista");
  await page.close();
});

test("B1 controle: na ordem inversa (lista primeiro) o ESC também devolve pra lista", async () => {
  const page = await loadDashboardJs();
  await mockLista(page, UMA_LINHA);
  const r = await page.evaluate(async () => {
    window.__mock();
    await openCategoryLaunches("saúde", {});
    document.querySelectorAll("#cl-list .bar-row")[0].click();
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    await new Promise((res) => setTimeout(res, 0));
    return {
      detalhe: document.getElementById("launch-detail-overlay").classList.contains("open"),
      lista: document.getElementById("cat-launches-overlay").classList.contains("open"),
    };
  });
  assert.equal(r.detalhe, false);
  assert.equal(r.lista, true);
  await page.close();
});

test("B1 controle: com a lista SOZINHA, o ESC continua fechando ela", async () => {
  // O conserto não pode ter deixado a lista impossível de fechar pelo teclado.
  const page = await loadDashboardJs();
  await mockLista(page, UMA_LINHA);
  const r = await page.evaluate(async () => {
    window.__mock();
    await openCategoryLaunches("saúde", {});
    const antes = document.getElementById("cat-launches-overlay").classList.contains("open");
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    return { antes, depois: document.getElementById("cat-launches-overlay").classList.contains("open") };
  });
  assert.equal(r.antes, true);
  assert.equal(r.depois, false, "o ESC parou de fechar a lista");
  await page.close();
});

test("B2: a lista imprime a HORA onde a Visão Geral imprime (has_time)", async () => {
  const page = await loadDashboardJs();
  await mockLista(page, UMA_LINHA);
  const r = await page.evaluate(async () => {
    window.__mock();
    await openCategoryLaunches("saúde", {});
    return {
      sub: document.querySelector("#cl-list .bar-sub").textContent,
      // a MESMA função que a Visão Geral usa, com a MESMA linha
      visaoGeral: fmtLaunchWhen(window.__L[0]),
    };
  });
  // 00:30 em São Paulo — a linha nasceu no dia 10, e é o dia 10 que aparece.
  assert.match(r.sub, /10\/03, 00:30/, `a lista escreveu ${JSON.stringify(r.sub)}`);
  assert.equal(r.sub.startsWith(r.visaoGeral), true,
    `lista=${r.sub} × visão geral=${r.visaoGeral}`);
  await page.close();
});

test("B2 controle: linha de extrato (sem hora) continua saindo só com a data", async () => {
  const page = await loadDashboardJs();
  await mockLista(page, [{ ...UMA_LINHA[0], has_time: false, posted_at: "2026-03-10",
                           criado_em: "2026-03-11T02:30:00+00:00" }]);
  const sub = await page.evaluate(async () => {
    window.__mock();
    await openCategoryLaunches("saúde", {});
    return document.querySelector("#cl-list .bar-sub").textContent;
  });
  assert.match(sub, /^10\/03/, sub);
  assert.ok(!/:/.test(sub.split("•")[0]), `inventou hora: ${sub}`);
  await page.close();
});

/* Tab de VERDADE (page.keyboard.press): `dispatchEvent` de um KeyboardEvent
   sintético não move o foco, então uma trilha montada assim dá "BODY" com e sem
   o trap — mede zero. E o `modals.js` entra na página porque é ele que expõe o
   `window.pigTrapTab`; a dashboard.html carrega os dois, nesta ordem. */
async function paginaComModals() {
  const page = await loadDashboardJs();
  await page.addStyleTag({ path: DASHBOARD_CSS });
  await page.addScriptTag({ path: join(dirname(fileURLToPath(import.meta.url)),
                                       "..", "..", "frontend", "modals.js") });
  return page;
}

test("B5: com o detalhe aberto, o Tab não alcança a linha do gráfico atrás", async () => {
  const page = await paginaComModals();
  await mockLista(page, UMA_LINHA);
  await page.evaluate(async () => {
    lastData = { year: 2026, month: 3, expense_categories: [{ categoria: "saúde", total: 50 }] };
    _renderCategoriesDistribution([]);
    window.__mock();
    await openCategoryLaunches("saúde", {});
    document.querySelectorAll("#cl-list .bar-row")[0].click();
  });
  const trilha = [];
  for (let i = 0; i < 8; i++) {
    await page.keyboard.press("Tab");
    trilha.push(await page.evaluate(() => {
      const a = document.activeElement;
      return a === document.body ? "BODY"
        : (a.id || a.className || a.tagName)
          + (a.closest("#categories-distribution") ? " <<< ATRÁS DO OVERLAY" : "");
    }));
  }
  assert.ok(!trilha.some((t) => t.includes("ATRÁS")),
    `o Tab saiu do detalhe e chegou na barra do gráfico: ${JSON.stringify(trilha)}`);
  // e continua sendo UM overlay: o Enter que abria a lista por cima não tem
  // mais onde acontecer
  const r = await page.evaluate(() => ({
    detalhe: document.getElementById("launch-detail-overlay").classList.contains("open"),
    lista: document.getElementById("cat-launches-overlay").classList.contains("open"),
  }));
  assert.equal(r.detalhe, true);
  assert.equal(r.lista, false, "dois overlays abertos ao mesmo tempo");
  await page.close();
});

test("B5 controle: os 3 botões do detalhe continuam alcançáveis pelo Tab", async () => {
  const page = await paginaComModals();
  await mockLista(page, UMA_LINHA);
  await page.evaluate(async () => {
    window.__mock();
    await openCategoryLaunches("saúde", {});
    document.querySelectorAll("#cl-list .bar-row")[0].click();
  });
  const vistos = new Set();
  for (let i = 0; i < 6; i++) {
    await page.keyboard.press("Tab");
    vistos.add(await page.evaluate(() => document.activeElement.id));
  }
  for (const id of ["ld-delete", "ld-edit", "ld-close"]) {
    assert.ok(vistos.has(id), `${id} ficou inalcançável: ${[...vistos]}`);
  }
  await page.close();
});

test("B6: readApiError aproveita detail.message/code, sem vazar JSON cru", async () => {
  const page = await loadDashboardJs();
  const r = await page.evaluate(async () => {
    const mk = (body, status) => ({ status, json: () => Promise.resolve(body) });
    return {
      plan_limit: await readApiError(mk({ detail: { error: "plan_limit",
        message: "Você atingiu o limite de 50 lançamentos do plano Grátis." } }, 403)),
      code: await readApiError(mk({ detail: { code: "same_plan" } }, 400)),
      // sem nada legível: cai no genérico em vez de imprimir o objeto
      semMensagem: await readApiError(mk({ detail: { error: "subscription_required" } }, 402)),
      lista422: await readApiError(mk({ detail: [{ loc: ["query", "limit"],
        msg: "Input should be >= 1", type: "greater_than_equal" }] }, 422)),
      semDetail: await readApiError(mk({ error: "boom" }, 500)),
      string: await readApiError(mk({ detail: "Janela inválida." }, 400)),
    };
  });
  assert.match(r.plan_limit, /limite de 50/, "a mensagem do servidor sumiu");
  assert.equal(r.code, "same_plan");
  assert.equal(r.string, "Janela inválida.");
  for (const [k, v] of Object.entries(r)) {
    assert.ok(!/[{}[\]]|object Object|"detail"/.test(v), `${k} vazou JSON cru: ${v}`);
  }
  assert.match(r.semMensagem, /402/);
  assert.match(r.lista422, /422/);
  assert.match(r.semDetail, /500/);
  await page.close();
});

/* ── D2. O SUBTÍTULO tem que dizer a verdade quando o plano corta ─────────
 * A rota corta a janela em `history_earliest_date` (frontend/routes/categories.py),
 * e numa conta Grátis isso é `history_current_month_only` → dia 1 do mês. O
 * subtítulo dizia "Tudo nesta categoria: despesas, receitas e movimentações
 * internas" mostrando UM MÊS — o aviso existia só no rodapé, a .72rem.
 *
 * Controle NEGATIVO: faça `_clSubtitulo` ignorar `capped_by_plan` (devolver
 * sempre o texto de baixo) e `B7` fica vermelho.
 * Controle POSITIVO: `B7b` — quem NÃO tem teto continua lendo "Tudo nesta
 * categoria", com o mesmo `window.from` no corpo. */

const UMA = (extra = {}) => [{ tipo: "despesa", valor: 50, categoria: "saúde",
  descricao: "consulta", nota: "consulta", alvo: null, data: "2026-02-09",
  posted_at: null, has_time: true, criado_em: "2026-02-09T09:00:00-03:00",
  fonte: "launches", user_seq: 7, id: 4242, is_internal_movement: false, ...extra }];

const abrirCom = (page, corpo, nome = "saúde", opts = {}) =>
  page.evaluate(async ([c, n, o]) => {
    window.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve(c) });
    await openCategoryLaunches(n, o);
    return {
      sub: document.getElementById("cl-sub").textContent,
      foot: document.getElementById("cl-foot").textContent,
      more: document.getElementById("cl-more").textContent,
      linhas: document.querySelectorAll("#cl-list .cl-row").length,
    };
  }, [corpo, nome, opts]);

test("B7: com corte de plano o subtítulo diz DESDE QUANDO, com ano", async () => {
  const page = await loadDashboardJs();
  const r = await abrirCom(page, {
    ok: true, launches: UMA(), resumo: { n_total: 1, despesa: 50, receita: 0 },
    // o plano cortou o "histórico inteiro" em 2024
    window: { from: "2024-05-28", to: null, capped_by_plan: true },
  });
  assert.match(r.sub, /desde 28\/05\/2024/, r.sub);
  assert.ok(!/Tudo nesta categoria/.test(r.sub),
    `com corte a tela não pode prometer "tudo": ${r.sub}`);
  await page.close();
});

test("B7b: SEM corte de plano o subtítulo continua dizendo 'Tudo nesta categoria'",
  async () => {
    const page = await loadDashboardJs();
    const r = await abrirCom(page, {
      ok: true, launches: UMA(), resumo: { n_total: 1, despesa: 50, receita: 0 },
      // mesma `window.from` do teste acima — o que muda é só o `capped_by_plan`.
      // Se o front derivasse o corte de `from`, este teste ficaria vermelho.
      window: { from: "2024-05-28", to: null, capped_by_plan: false },
    });
    assert.match(r.sub, /Tudo nesta categoria/, r.sub);
    assert.ok(!/desde/.test(r.sub), r.sub);
    await page.close();
  });

test("B7c: pela Distribuição, o corte de plano aparece mesmo com from/to no pedido",
  async () => {
    // O caminho em que derivar do `window.from` NUNCA funcionaria: a barra do
    // gráfico sempre manda uma janela, então `from` preenchido não distingue
    // "o usuário pediu este mês" de "o plano cortou".
    const page = await loadDashboardJs();
    const r = await abrirCom(page, {
      ok: true, launches: UMA(), resumo: { n_total: 1, despesa: 50, receita: 0 },
      window: { from: "2026-02-01", to: "2026-02-28", capped_by_plan: true },
    }, "saúde", { from: "2026-01-01", to: "2026-01-31",
                  tipo: "despesa", includeInternal: false });
    assert.match(r.sub, /desde 01\/02\/2026/, r.sub);
    await page.close();
  });

test("B3: o estado vazio ensina a hashtag só quando o nome cabe inteiro nela", async () => {
  // A frase é medida pelo `handle_incoming` em
  // tests/test_categoria_frase_estado_vazio.py — aqui só se prova que a tela
  // escreve as DUAS variantes certas, porque o `#` casa um token só. O critério
  // é a CLASSE de caracteres, não o espaço (ver D3a).
  const page = await loadDashboardJs();
  const r = await page.evaluate(async () => {
    window.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({
      ok: true, launches: [], resumo: { n_total: 0, despesa: 0, receita: 0 } }) });
    await openCategoryLaunches("transporte", {});
    const simples = document.getElementById("cl-list").textContent;
    await openCategoryLaunches("gastos da vovó", {});
    return { simples, composto: document.getElementById("cl-list").textContent };
  });
  assert.match(r.simples, /gastei 30 na loja #transporte/);
  assert.match(r.composto, /gastei 30 em gastos da vovó/);
  assert.ok(!/#gastos da vovó/.test(r.composto),
    `a hashtag num nome composto mandaria o gasto pra "gastos": ${r.composto}`);
  await page.close();
});

/* ══ D1. "Carregar mais" ═══════════════════════════════════════════════════
 * A tela escrevia "Mostrando 50 de 312" e não havia saída — o pedido era ver
 * TODOS os lançamentos da categoria.
 *
 * Controle NEGATIVO: troque o `insertAdjacentHTML("beforeend", ...)` de
 * `loadMoreCategoryLaunches` por `list.innerHTML = ...` (substituir em vez de
 * anexar) e `D1a` fica vermelho (fica 1 linha, não 3).
 * Controle POSITIVO: `D1a` também prova que a PRIMEIRA página não muda, e
 * `D1d` que o botão some quando acaba. */

/** Servidor falso com N linhas, que pagina por CURSOR como a rota: o cursor é
    o índice da última linha entregue e a página seguinte começa DEPOIS dela.
    (A rota manda `<criado_em>|<fonte>|<ord_id>`; o que o front tem que provar é
    que devolve o cursor recebido e nunca calcula deslocamento sozinho.) */
const servidorPaginado = (page, total, pagina) => page.evaluate(([t, p]) => {
  window.__pedidos = [];
  window.fetch = (url) => {
    const u = new URL(url, "http://x");
    const cur = u.searchParams.get("cursor");
    const off = cur === null ? 0 : Number(cur.split("|")[2]) + 1;
    const lim = Number(u.searchParams.get("limit") || 50);
    window.__pedidos.push(cur);
    const linhas = [];
    for (let i = off; i < Math.min(off + Math.min(lim, p), t); i++) {
      linhas.push({ tipo: "despesa", valor: 10 + i, categoria: "saúde",
        // `describeLaunch` (dashboard.js) monta o rótulo de `alvo`/`nota`, não
        // de `descricao` — é `nota` que tem que carregar o número do item.
        descricao: `item ${String(i).padStart(3, "0")}`, alvo: null,
        nota: `item ${String(i).padStart(3, "0")}`,
        data: "2026-02-09", posted_at: null, has_time: true,
        criado_em: "2026-02-09T09:00:00-03:00", fonte: "launches",
        user_seq: i + 1, id: 1000 + i, is_internal_movement: false });
    }
    const ultimo = off + linhas.length - 1;
    return Promise.resolve({ ok: true, json: () => Promise.resolve({
      ok: true, launches: linhas,
      resumo: { n_total: t, despesa: 10 * t, receita: 0 },
      next_cursor: linhas.length ? `2026-02-09T09:00:00-03:00|launches|${ultimo}` : null,
      window: { from: null, to: null, capped_by_plan: false } }) });
  };
}, [total, pagina]);

const estadoDaLista = (page) => page.evaluate(() => ({
  linhas: [...document.querySelectorAll("#cl-list .cl-row .name")].map((e) => e.textContent),
  foot: document.getElementById("cl-foot").textContent,
  temBotao: !!document.getElementById("cl-more-btn"),
  pedidos: window.__pedidos,
}));

test("D1a: 'Carregar mais' ANEXA a página seguinte, sem repetir nem perder linha",
  async () => {
    const page = await loadDashboardJs();
    await servidorPaginado(page, 5, 2);
    await page.evaluate(() => openCategoryLaunches("saúde", {}));
    const p1 = await estadoDaLista(page);
    assert.deepEqual(p1.linhas, ["item 000", "item 001"], JSON.stringify(p1));
    assert.match(p1.foot, /Mostrando 2 de 5/, p1.foot);
    assert.ok(p1.temBotao, "sem botão não há como ver o resto");

    await page.evaluate(() => loadMoreCategoryLaunches());
    const p2 = await estadoDaLista(page);
    assert.deepEqual(p2.linhas, ["item 000", "item 001", "item 002", "item 003"],
      JSON.stringify(p2));
    assert.deepEqual(p2.pedidos, [null, "2026-02-09T09:00:00-03:00|launches|1"],
      "o 'carregar mais' não devolveu o cursor da última linha na tela");
    assert.match(p2.foot, /Mostrando 4 de 5/, p2.foot);
    await page.close();
  });

test("D1b: o clique numa linha ANEXADA abre o lançamento certo", async () => {
  // O índice do onclick é GLOBAL: se a página 2 recomeçasse do zero, clicar na
  // linha 3 abriria o detalhe da linha 1 — com o Excluir dela.
  const page = await loadDashboardJs();
  await servidorPaginado(page, 4, 2);
  const desc = await page.evaluate(async () => {
    await openCategoryLaunches("saúde", {});
    await loadMoreCategoryLaunches();
    window._renderLaunchDetail = () => {};
    const linhas = document.querySelectorAll("#cl-list .cl-row");
    linhas[3].click();
    return _launchDetailCurrent.descricao;
  });
  assert.equal(desc, "item 003", desc);
  await page.close();
});

test("D1c: clique repetido em 'Carregar mais' não duplica linha", async () => {
  // Dois cliques seguidos pedem o MESMO cursor (a lista ainda não cresceu). O
  // guard recusa o segundo antes do canal de fetch.
  const page = await loadDashboardJs();
  await servidorPaginado(page, 6, 2);
  await page.evaluate(async () => {
    await openCategoryLaunches("saúde", {});
    await Promise.all([loadMoreCategoryLaunches(), loadMoreCategoryLaunches()]);
  });
  const r = await estadoDaLista(page);
  assert.deepEqual(r.linhas, ["item 000", "item 001", "item 002", "item 003"],
    JSON.stringify(r));
  assert.deepEqual([...new Set(r.linhas)], r.linhas, "linha duplicada");
  await page.close();
});

test("D1d: o botão some quando não sobra página, e o rodapé para de dizer 'Mostrando'",
  async () => {
    const page = await loadDashboardJs();
    await servidorPaginado(page, 4, 2);
    await page.evaluate(async () => {
      await openCategoryLaunches("saúde", {});
      await loadMoreCategoryLaunches();
    });
    const r = await estadoDaLista(page);
    assert.equal(r.linhas.length, 4);
    assert.ok(!r.temBotao, "o botão ficou pedindo uma página que não existe");
    assert.match(r.foot, /^4 lançamentos/, r.foot);
    await page.close();
  });

test("D1e: total menor que a página não mostra 'Carregar mais'", async () => {
  const page = await loadDashboardJs();
  await servidorPaginado(page, 3, 50);
  await page.evaluate(() => openCategoryLaunches("saúde", {}));
  const r = await estadoDaLista(page);
  assert.ok(!r.temBotao, "botão apareceu com tudo já na tela");
  assert.match(r.foot, /^3 lançamentos/, r.foot);
  await page.close();
});

test("D1f: trocar de categoria com uma página em voo não mistura as listas",
  async () => {
    /* A corrida que o `makeFetchChannel` sozinho não fecha: a resposta do
       "carregar mais" de saúde pode chegar DEPOIS da abertura de mercado. O
       append é recusado por `_catLaunchesCtx !== ctx`. */
    const page = await loadDashboardJs();
    const r = await page.evaluate(async () => {
      let solta;
      window.fetch = (url) => {
        const cur = new URL(url, "http://x").searchParams.get("cursor");
        const off = cur ? 1 : 0;
        const linha = (i, cat) => ({ tipo: "despesa", valor: 10, categoria: cat,
          descricao: `${cat} ${i}`, nota: `${cat} ${i}`, alvo: null, data: "2026-02-09",
          posted_at: null, has_time: true, criado_em: "2026-02-09T09:00:00-03:00",
          fonte: "launches", user_seq: i, id: i, is_internal_movement: false });
        const corpo = (cat, i) => ({ ok: true, launches: [linha(i, cat)],
          resumo: { n_total: 9, despesa: 10, receita: 0 },
          next_cursor: `2026-02-09T09:00:00-03:00|launches|${i}`,
          window: { from: null, to: null, capped_by_plan: false } });
        if (off === 0 && !window.__abriu) {
          return Promise.resolve({ ok: true, json: () => Promise.resolve(corpo("saude", 0)) });
        }
        if (off > 0) {   // o "carregar mais" de saúde: fica pendurado
          return new Promise((res) => { solta = () => res({ ok: true,
            json: () => Promise.resolve(corpo("saude", 1)) }); });
        }
        return Promise.resolve({ ok: true, json: () => Promise.resolve(corpo("mercado", 0)) });
      };
      await openCategoryLaunches("saúde", {});
      const maisEmVoo = loadMoreCategoryLaunches();
      window.__abriu = true;
      await openCategoryLaunches("mercado", {});
      solta();
      await maisEmVoo;
      return {
        titulo: document.getElementById("cl-title").textContent,
        linhas: [...document.querySelectorAll("#cl-list .cl-row .name")]
          .map((e) => e.textContent),
      };
    });
    assert.match(r.titulo, /mercado/);
    assert.ok(!r.linhas.some((l) => /saude/.test(l)),
      `linha de saúde entrou sob o título de mercado: ${JSON.stringify(r.linhas)}`);
    await page.close();
  });

/* ══ D3a. A frase do estado vazio não pode POLUIR a base ═══════════════════
 * A regra era "tem espaço?"; a real é a classe de caracteres que o `#` casa
 * (parsers.py:119). Fora dela a hashtag corta o nome e CRIA uma categoria
 * fantasma — medido pelo handle_incoming em
 * tests/test_categoria_frase_estado_vazio.py. Aqui se prova que a TELA escreve
 * a variante certa para cada nome.
 *
 * Controle NEGATIVO: volte `_CAT_HASHTAG_OK.test(nome)` para `!/\s/.test(nome)`
 * e D3a fica vermelho em mcdonald's, uber/99, l'occitane e 100%natural. */

const fraseVazia = (page, nome) => page.evaluate(async (n) => {
  window.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({
    ok: true, launches: [], resumo: { n_total: 0, despesa: 0, receita: 0 },
    window: { from: null, to: null, capped_by_plan: false } }) });
  await openCategoryLaunches(n, {});
  return document.getElementById("cl-list").textContent;
}, nome);

test("D3a: nome fora da classe da hashtag recebe a MENÇÃO, não o #", async () => {
  const page = await loadDashboardJs();
  for (const nome of ["mcdonald's", "uber/99", "l'occitane", "cafe & cia",
                      "100%natural", "mercado(bairro)", "🍕pizza",
                      "gastos da vovó"]) {
    const txt = await fraseVazia(page, nome);
    assert.match(txt, new RegExp(`gastei 30 em ${nome.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`),
      `${nome}: a tela não ensinou a menção — ${txt}`);
    assert.ok(!txt.includes("#"), `${nome}: a hashtag cortaria o nome e criaria categoria fantasma — ${txt}`);
  }
  await page.close();
});

test("D3a2: nome INTEIRO dentro da classe continua recebendo a hashtag", async () => {
  // Controle POSITIVO: é a hashtag que acerta as 5 do seed em que a menção erra
  // (transporte, saúde, educação, pets, investimento_aporte).
  const page = await loadDashboardJs();
  for (const nome of ["transporte", "saúde", "day-trade", "x_y", "n1",
                      "investimento_aporte"]) {
    const txt = await fraseVazia(page, nome);
    assert.match(txt, new RegExp(`gastei 30 na loja #${nome}`), `${nome}: ${txt}`);
  }
  await page.close();
});

test("D3a3: o estado vazio não promete mais que o lançamento 'aparece aqui'",
  async () => {
    // Existe nome (b+c) em que NENHUMA das duas frases casa: o gasto cai em
    // "outros". Prometer o resultado ali era a tela mentindo.
    const page = await loadDashboardJs();
    const txt = await fraseVazia(page, "b+c");
    assert.ok(!/aparece aqui/.test(txt), `promessa que a tela não cumpre: ${txt}`);
    assert.match(txt, /trocar no próprio lançamento/, txt);
    await page.close();
  });

/* ══ M4. O galho CATCH do readApiError ═════════════════════════════════════
 * O B6 usa mocks cujo `json()` sempre RESOLVE, então o `catch` (HTML de proxy,
 * 502, corpo vazio) tinha zero cobertura nos 11 call sites — justo o galho que
 * mudou de `resp.text()` para a mensagem genérica.
 *
 * Controle NEGATIVO: volte o catch para `return await resp.text()` e M4a fica
 * vermelho (a página de erro do proxy sai inteira na tela). */

test("M4a: corpo não-JSON (HTML de proxy, 502, vazio) vira mensagem de gente",
  async () => {
    const page = await loadDashboardJs();
    const r = await page.evaluate(async () => {
      const quebra = (msg, status) => ({
        status,
        json: () => Promise.reject(new SyntaxError(msg)),
        text: () => Promise.resolve("<html><body><h1>502 Bad Gateway</h1></body></html>"),
      });
      return {
        html: await readApiError(quebra("Unexpected token '<'", 502)),
        vazio: await readApiError(quebra("Unexpected end of JSON input", 504)),
        // json() que estoura de outro jeito (rede caiu no meio do corpo)
        rede: await readApiError({ status: 500,
          json: () => Promise.reject(new TypeError("Failed to fetch")) }),
      };
    });
    for (const [k, v] of Object.entries(r)) {
      assert.ok(!/</.test(v), `${k} jogou HTML na tela: ${v}`);
      assert.ok(!/Unexpected|Failed to fetch/.test(v),
        `${k} vazou erro de parser pro usuário: ${v}`);
    }
    assert.match(r.html, /502/, r.html);
    assert.match(r.vazio, /504/, r.vazio);
    assert.match(r.rede, /500/, r.rede);
    await page.close();
  });

test("M4b: a lista mostra a mensagem genérica quando o proxy devolve HTML",
  async () => {
    // O call site de verdade: o catch tem que chegar na CAIXA de erro da lista,
    // não só sair do helper.
    const page = await loadDashboardJs();
    const txt = await page.evaluate(async () => {
      window.fetch = () => Promise.resolve({
        ok: false, status: 502,
        json: () => Promise.reject(new SyntaxError("Unexpected token '<'")),
        text: () => Promise.resolve("<html>502</html>"),
      });
      await openCategoryLaunches("saúde", {});
      return document.getElementById("cl-list").textContent;
    });
    assert.match(txt, /Não deu pra carregar/, txt);
    assert.match(txt, /502/, txt);
    assert.ok(!/<html>|Unexpected/.test(txt), txt);
    await page.close();
  });

test("D1g: 'Carregar mais' que falha mostra o motivo E volta a ser clicável",
  async () => {
    /* As linhas já carregadas não podem sumir por causa da página que faltou —
       e o botão não pode ficar preso em "Carregando…", senão a única saída é
       fechar e reabrir a lista. */
    const page = await loadDashboardJs();
    await servidorPaginado(page, 6, 2);
    const r = await page.evaluate(async () => {
      await openCategoryLaunches("saúde", {});
      const bom = window.fetch;
      window.fetch = () => Promise.resolve({ ok: false, status: 503,
        json: () => Promise.resolve({ detail: "Serviço indisponível." }) });
      await loadMoreCategoryLaunches();
      const depoisDoErro = {
        linhas: document.querySelectorAll("#cl-list .cl-row").length,
        erro: (document.querySelector(".cl-more-err") || {}).textContent,
        botao: (document.getElementById("cl-more-btn") || {}).textContent,
        travado: (document.getElementById("cl-more-btn") || {}).disabled,
      };
      window.fetch = bom;                 // tenta de novo, agora com o servidor de pé
      await loadMoreCategoryLaunches();
      return { depoisDoErro,
        linhasDepoisDaRetentativa: document.querySelectorAll("#cl-list .cl-row").length };
    });
    assert.equal(r.depoisDoErro.linhas, 2, "as linhas já carregadas sumiram");
    assert.match(r.depoisDoErro.erro, /Serviço indisponível/, r.depoisDoErro.erro);
    assert.match(r.depoisDoErro.botao, /Carregar mais/, r.depoisDoErro.botao);
    assert.ok(!r.depoisDoErro.travado, "o botão ficou preso em 'Carregando…'");
    assert.equal(r.linhasDepoisDaRetentativa, 4, "a retentativa não trouxe a página");
    await page.close();
  });
