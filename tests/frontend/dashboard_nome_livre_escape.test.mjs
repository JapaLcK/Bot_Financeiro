/**
 * Texto livre do servidor que vai para `innerHTML` no dashboard passa por
 * `escapeHtmlSafe` — nome de cartão (`populateLaunchCards`) e nome/categoria
 * de parcelamento no histórico (`renderInstallmentsView`). A categoria custom
 * do editor de lançamento mora no `dashboard_category_escape.test.mjs` (C3),
 * que já tem o modal real montado.
 *
 * Controle NEGATIVO: tire o `escapeHtmlSafe(...)` de um dos sites em
 * `frontend/dashboard.js` — o teste hostil daquele site fica vermelho:
 *   `populateLaunchCards`            → "A hostil"
 *   "Maior compra" (stat-delta)      → "B/C hostil" (asserção do nome)
 *   "Categoria mais comum"           → "B/C hostil" (asserção da categoria)
 * Controle POSITIVO: os testes "positivo" (texto com & e acento sai exato,
 * seleção preservada, estados de 0 e 1 cartão).
 *
 * Rodar: node --test tests/frontend/dashboard_nome_livre_escape.test.mjs
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { abrirBrowser, fecharBrowser, loadDashboardJs } from "./_dashboard_loader.mjs";

before(abrirBrowser);
after(fecharBrowser);

const HOSTIS = [
  '<img src=x onerror="window.__pwned++">',
  '</option></select><img src=x onerror="window.__pwned++">',
  '<svg onload="window.__pwned++">',
];

/** Monta o `<select id="launch-card">`, põe `cards` em lastData e popula. */
const popular = (page, cards, antes = null) => page.evaluate(async ([cards, antes]) => {
  window.__pwned = 0;
  if (!document.getElementById("launch-card")) {
    document.body.insertAdjacentHTML("beforeend", '<select id="launch-card"></select>');
  }
  const sel = document.getElementById("launch-card");
  lastData = { credit_cards: cards };
  populateLaunchCards();
  if (antes !== null) { sel.value = antes; populateLaunchCards(); }
  await new Promise((r) => setTimeout(r, 300));
  return {
    nos: document.querySelectorAll('img[src="x"], svg').length,
    opts: [...sel.options].map((o) => ({ v: o.value, t: o.textContent })),
    valor: sel.value,
    pwned: window.__pwned,
  };
}, [cards, antes]);

test("A hostil: nome de cartão vira texto da opção, não nó", async () => {
  const page = await loadDashboardJs();
  const cards = HOSTIS.map((name, i) => ({ id: 10 + i, name }));
  const r = await popular(page, cards);
  assert.equal(r.nos, 0, `nome de cartão virou ${r.nos} nó(s) img/svg no documento`);
  assert.equal(r.opts.length, 1 + cards.length, JSON.stringify(r.opts));
  cards.forEach((c, i) => {
    assert.equal(r.opts[i + 1].t, c.name);
    assert.equal(r.opts[i + 1].v, String(c.id));
  });
  assert.equal(r.pwned, 0);
  await page.close();
});

test("A positivo: & e acento exatos, seleção preservada, 0 e 1 cartão", async () => {
  const page = await loadDashboardJs();
  const cards = [{ id: 7, name: "Nubank & Cia" }, { id: 8, name: "Cartão Itaú" }];
  let r = await popular(page, cards, "8");
  assert.deepEqual(r.opts.slice(1).map((o) => o.t), ["Nubank & Cia", "Cartão Itaú"]);
  assert.equal(r.valor, "8", "a seleção anterior se perdeu ao repopular");
  r = await popular(page, [{ id: 9, name: "Inter" }]);
  assert.equal(r.valor, "9", "cartão único não veio selecionado");
  r = await popular(page, []);
  assert.deepEqual(r.opts, [{ v: "", t: "— Nenhum cartão cadastrado —" }]);
  await page.close();
});

/** Renderiza o histórico de parcelamentos com um grupo e lê os dois tiles. */
const historico = (page, name, categoria) => page.evaluate(async ([name, categoria]) => {
  window.__pwned = 0;
  document.body.insertAdjacentHTML("beforeend",
    '<div id="installments-stats"></div><div id="installments-list"></div>');
  _instTab = "history";
  renderInstallmentsView([{
    group_id: 1, name, categoria, total: 10, paid_amount: 10,
    n_pending: 0, n_paid: 1, installments_total: 1, parcelas: [],
  }]);
  await new Promise((r) => setTimeout(r, 300));
  const tile = (rotulo) => [...document.querySelectorAll("#installments-stats .stat-tile")]
    .find((t) => t.querySelector(".stat-label").textContent === rotulo);
  return {
    nos: document.querySelectorAll('img[src="x"], svg').length,
    maior: tile("Maior compra")?.querySelector(".stat-delta")?.textContent,
    categoria: tile("Categoria mais comum")?.querySelector(".stat-value")?.textContent,
    pwned: window.__pwned,
  };
}, [name, categoria]);

// a mesma capitalização de `_instMostCommonCategory`
const cap = (s) => { const c = s.toLowerCase(); return c.charAt(0).toUpperCase() + c.slice(1); };

test("B/C hostil: nome e categoria do parcelamento viram texto nos tiles", async () => {
  const page = await loadDashboardJs();
  const nome = '<svg onload="window.__pwned++">';
  const cat = '<img src=x onerror="window.__pwned++">';
  const r = await historico(page, nome, cat);
  assert.equal(r.maior, nome.slice(0, 26), "Maior compra não saiu como texto");
  assert.equal(r.categoria, cap(cat), "Categoria mais comum não saiu como texto");
  assert.equal(r.nos, 0, `virou ${r.nos} nó(s) img/svg no documento`);
  assert.equal(r.pwned, 0);
  await page.close();
});

test("B/C positivo: Café & Pão sai exato nos dois tiles", async () => {
  const page = await loadDashboardJs();
  const r = await historico(page, "Café & Pão", "Café & Pão");
  assert.equal(r.maior, "Café & Pão");
  assert.equal(r.categoria, cap("Café & Pão"));
  await page.close();
});
