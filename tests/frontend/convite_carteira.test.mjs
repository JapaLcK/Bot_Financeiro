/**
 * PL-06, convite "Informe seu saldo" (PR 1): o destino do botão no painel antigo.
 *
 * O item `wallet_balance_unknown` traz `action_view: "carteira"`, que não é tela: `_switchToInsightView` o resolve em
 * `_INSIGHT_MODAIS` e abre o "Ajustar carteira" na variante sem banco, com o campo vazio. O botão da lista de Análises
 * e o CTA do card "Piggy notou" passam os dois por ali. Salvar esconde o convite na hora (o cache do servidor cai
 * depois, pelo `pb_escrita`). Quem não tem banco ganha o link "ajustar" no "Saldo atual" do mês corrente.
 *
 * Como roda: o padrão de `adjust_wallet_error.test.mjs` (dashboard.js inteiro + dashboard.css real, estilo
 * computado), com o markup REAL do modal e do card recortado da dashboard.html, para o teste não divergir dela.
 *
 * Controles negativos (medidos à mão, relato do PR): sem a variante sem banco → F1 (linha de bancos visível); o CTA
 * de volta ao `navigateTo` direto → F3; sem `_esconderConviteSaldo` → F4; sem o `opts.vazio` → F1b; sem o link → F5. Positivos: F2 (com banco,
 * o modal de hoje), F3 (`fixed` continua navegando), F5 (mês passado sem link).
 *
 * Rodar: node --test tests/frontend/convite_carteira.test.mjs
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { chromium } from "playwright";
import { IDS } from "./_dashboard_loader.mjs";

const FRONTEND = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "frontend");
const HTML = readFileSync(join(FRONTEND, "dashboard.html"), "utf8");

function recorte(inicio, fim) {
  const a = HTML.indexOf(inicio), b = HTML.indexOf(fim, a);
  assert.ok(a >= 0 && b > a, `recorte não achado: ${inicio}`);
  return HTML.slice(a, b);
}
const MODAL = recorte('<div class="overlay" id="adjust-wallet-overlay"', '<div class="overlay" id="investment-detail-overlay"');
const CARD = recorte('<div id="piggy-insight-card"', '<div class="grid" id="grid">');

const CONVITE = {
  key: "wallet_balance_unknown:carteira:2026-11", type: "wallet_balance_unknown", severity: "info", icon: "💰",
  title: "Quanto você tem na Carteira hoje?", message: "Informe o saldo de hoje da Carteira para melhorar a previsão do mês.",
  action_label: "Informar saldo", action_view: "carteira",
};
const CONTA = { ...CONVITE, key: "bill_due:1:2026-11-13", type: "bill_due", severity: "warning",
  title: "Luz vence hoje", action_label: "Ver contas a pagar", action_view: "fixed" };

let browser;
before(async () => { browser = await chromium.launch(); });
after(async () => { await browser?.close(); });

async function carregar() {
  const page = await browser.newPage();
  const errs = [];
  page.on("pageerror", (e) => errs.push(String(e)));
  // Origem de verdade: a dispensa local do convite (F4) mora no `localStorage`, que o about:blank recusa.
  await page.route("http://convite.test/", (r) => r.fulfill({ contentType: "text/html", body: "<!doctype html><body></body>" }));
  await page.goto("http://convite.test/");
  await page.setContent(
    IDS.filter((i) => i !== "grid").map((i) => `<div id="${i}"></div>`).join("") +
    CARD + '<div id="grid"></div><div id="toast"></div><div id="analytics-insights-list"></div>' + MODAL,
  );
  await page.evaluate(() => { window.fetch = () => new Promise(() => {}); });
  await page.addStyleTag({ path: join(FRONTEND, "dashboard.css") });
  await page.addScriptTag({ path: join(FRONTEND, "dashboard.js") });
  assert.deepEqual(errs, [], "dashboard.js não executou até o fim");
  await page.evaluate(() => {
    window.csrfHeaders = () => ({});
    window.__navegou = [];
    window.navigateTo = (v) => window.__navegou.push(v);
  });
  return page;
}

const modal = (page) => page.evaluate(() => {
  const vis = (id) => getComputedStyle(document.getElementById(id)).display !== "none";
  return {
    aberto: document.getElementById("adjust-wallet-overlay").classList.contains("open"),
    titulo: document.getElementById("adjust-wallet-title").textContent,
    sub: document.getElementById("adjust-wallet-sub").textContent,
    rotulo: document.getElementById("adjust-wallet-label").textContent.trim(),
    valor: document.getElementById("adjust-wallet-input").value,
    bancos: vis("adjust-wallet-banks-line"), dica: vis("adjust-wallet-hint"), total: vis("adjust-wallet-total-line"),
  };
});

test("F1: sem banco, pelo convite: campo vazio, sem bancos, dica nem total, textos da P14", async () => {
  const page = await carregar();
  // `lastData` e a abertura no MESMO evaluate: nada do boot do dashboard.js roda entre os dois.
  await page.evaluate(() => {
    lastData = { balance: -30, of_bank_count: 0, of_bank_balance: 0 };
    openAdjustWalletModal({ vazio: true });
  });
  const m = await modal(page);
  assert.equal(m.aberto, true);
  assert.equal(m.titulo, "Saldo da Carteira");
  assert.match(m.sub, /^Quanto você tem hoje\? Some o dinheiro vivo/);
  assert.equal(m.rotulo, "Saldo de hoje (R$)");
  assert.equal(m.valor, "", "o -30,00 de nascença não pode vir preenchido");
  assert.deepEqual([m.bancos, m.dica, m.total], [false, false, false]);
  // Salvar vazio mostra a mensagem da variante.
  await page.evaluate(() => submitAdjustWallet());
  assert.equal(await page.textContent("#adjust-wallet-error"), "Digite quanto você tem hoje (pode ser 0).");
  // Pelo link "ajustar" (sem `vazio`) e com a Carteira positiva, o valor de hoje vem preenchido.
  await page.evaluate(() => { lastData.balance = 120; closeAdjustWalletModal(); openAdjustWalletModal(); });
  assert.equal((await modal(page)).valor, "120.00");
  await page.close();
});

test("F1b: pelo convite, com a Carteira positiva, o campo também vem vazio", async () => {
  // Receita 500, gasto 300 e aluguel de 1.000: Carteira +200, o mês fecha negativo a partir dela e o convite sai.
  const page = await carregar();
  await comCard(page, [CONVITE]);
  await page.evaluate(() => { lastData.balance = 200; });
  await page.click("#piggy-insight-cta");
  assert.equal((await modal(page)).valor, "", "o +200,00 lançado não é o saldo informado");
  await page.close();
});

test("F2 (positivo): com banco, o modal é o de hoje", async () => {
  const page = await carregar();
  await page.evaluate(() => {
    lastData = { balance: 50, of_bank_count: 1, of_bank_balance: 200 };
    openAdjustWalletModal({ vazio: true });
  });
  const m = await modal(page);
  assert.equal(m.titulo, "Ajustar carteira");
  assert.match(m.sub, /^A Carteira é o dinheiro fora dos bancos conectados/);
  assert.equal(m.rotulo, "Carteira (dinheiro fora do banco)");
  assert.equal(m.valor, "50.00");
  assert.deepEqual([m.bancos, m.dica, m.total], [true, true, true]);
  assert.equal(await page.textContent("#adjust-wallet-total"), "R$ 250,00");
  await page.evaluate(() => { document.getElementById("adjust-wallet-input").value = ""; submitAdjustWallet(); });
  assert.equal(await page.textContent("#adjust-wallet-error"), "Digite um valor (use 0 se está tudo no banco).");
  await page.close();
});

async function comCard(page, lista) {
  await page.evaluate(async (l) => {
    USER_ID = 7; USER_GATES = { insights: true };
    lastData = { balance: -30, of_bank_count: 0, of_bank_balance: 0 };
    window.fetch = async () => ({ ok: true, json: async () => ({ insights: l }) });
    await loadPiggyInsight();
    _analyticsCache = { insights: l };
    renderAnalyticsInsights(l);
  }, lista);
}

test("F3: o botão da lista e o CTA do card abrem o modal; `fixed` continua navegando", async () => {
  const page = await carregar();
  await comCard(page, [CONVITE, CONTA]);
  assert.equal(await page.textContent("#piggy-insight-cta"), "Informar saldo →");
  await page.click("#piggy-insight-cta");
  assert.equal((await modal(page)).aberto, true, "o CTA do card não abriu o modal");
  await page.evaluate(() => closeAdjustWalletModal());
  await page.click("#analytics-insights-list .tx-row:nth-child(1) .mini-action");
  assert.equal((await modal(page)).aberto, true, "o botão da lista não abriu o modal");
  await page.evaluate(() => closeAdjustWalletModal());
  await page.click("#analytics-insights-list .tx-row:nth-child(2) .mini-action");
  assert.deepEqual(await page.evaluate(() => window.__navegou), ["fixed"]);
  assert.equal((await modal(page)).aberto, false);
  await page.close();
});

test("F4: depois de salvar, o convite some da lista e do card na hora", async () => {
  const page = await carregar();
  await comCard(page, [CONVITE, CONTA]);
  assert.equal(await page.isVisible("#piggy-insight-card"), true);
  await page.click("#piggy-insight-cta");
  await page.evaluate(async () => {
    window.fetch = async () => ({ ok: true, json: async () => ({ ok: true, delta: 0, launch_id: null }) });
    document.getElementById("adjust-wallet-input").value = "0";
    await submitAdjustWallet();
  });
  const r = await page.evaluate(() => ({
    aberto: document.getElementById("adjust-wallet-overlay").classList.contains("open"),
    titulos: [...document.querySelectorAll("#analytics-insights-list .tx-desc")].map((e) => e.textContent),
    card: getComputedStyle(document.getElementById("piggy-insight-card")).display,
  }));
  assert.equal(r.aberto, false);
  assert.deepEqual(r.titulos, ["Luz vence hoje"], "o convite continuou na lista (e a conta tem de ficar)");
  assert.equal(r.card, "none", "o card continuou mostrando o convite");
  await page.close();
});

test("F4b (positivo): salvar não esconde o card que mostra outro aviso", async () => {
  const page = await carregar();
  await comCard(page, [CONTA, CONVITE]);
  await page.evaluate(async () => {
    window.fetch = async () => ({ ok: true, json: async () => ({ ok: true }) });
    openAdjustWalletModal({ vazio: true });
    document.getElementById("adjust-wallet-input").value = "10";
    await submitAdjustWallet();
  });
  assert.equal(await page.isVisible("#piggy-insight-card"), true);
  await page.close();
});

const linkAjustar = (page, d) => page.evaluate((dd) => {
  const NOW = new Date();
  try {
    render({ year: NOW.getFullYear(), month: NOW.getMonth() + 1, monthly_income: 0, monthly_expense: 30,
      expense_categories: [], launches: [], pockets: [], investments: [], credit_cards: [], budgets: {}, ...dd });
  } catch { /* cauda do render, fora do que este teste mede */ }
  return [...document.querySelectorAll("#grid .ov-adjust-lnk")].map((b) => [b.textContent, b.getAttribute("onclick")]);
}, d);

test("F5: o link \"ajustar\" sai sem banco no mês corrente e não sai em mês passado", async () => {
  const page = await carregar();
  assert.deepEqual(await linkAjustar(page, { balance: -30, of_bank_count: 0, is_current_month: true }),
    [["ajustar", "openAdjustWalletModal()"]]);
  assert.deepEqual(await linkAjustar(page, { balance: -30, of_bank_count: 0, is_current_month: false }), []);
  assert.deepEqual(await linkAjustar(page, { balance: 10, of_bank_count: 1, of_bank_balance: 5, is_current_month: true }),
    [["ajustar", "openAdjustWalletModal()"]], "com banco, o link de sempre (um só)");
  await page.close();
});
