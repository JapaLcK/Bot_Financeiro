/**
 * Lacunas da campanha (Black Friday) da 1ª passada: `/` logado sem assinatura, `degraded` e as DUAS ordens dos
 * fetches; vitalício na /precos; "Tem cupom?" na página de pagamento (`semCupom`).
 * Controles (2026-11-06, mutação em produção e revertida): sem `campanha()` no oferta.js, sem o teste de
 * `degraded`, sem o `!subState.lifetime` da /precos ou sem as chamadas de `semCupom` os testes ficam vermelhos;
 * positivos: promotionCode do cliente e sessão sem desconto mantêm o "Tem cupom?".
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { startServer } from "./_server.mjs";
import { chromium } from "playwright";
import { CAMP, CFG } from "./_campanha.mjs";
import { abrirPagina as abrirP, caixas } from "./_assinar.mjs";

let ORIGIN, server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer()); browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

const json = (r, corpo, status = 200) => r.fulfill({ status, contentType: "application/json", body: JSON.stringify(corpo) });
const espera = (ms) => new Promise((ok) => setTimeout(ok, ms));

// logado: validate 200 e a assinatura que o teste mandar; `atrasoSub`/`atrasoCfg` escolhem a ordem das respostas.
async function pagina(url, sub, { atrasoSub = 0, atrasoCfg = 0 } = {}) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  await ctx.route("**/auth/validate", (r) => json(r, {}));
  await ctx.route("**/auth/me", (r) => json(r, { detail: "x" }, 401));
  await ctx.route("**/billing/subscription", async (r) => { await espera(atrasoSub); return json(r, sub); });
  await ctx.route("**/billing/plans-config", async (r) => { await espera(atrasoCfg); return json(r, CFG(CAMP)); });
  const page = await ctx.newPage();
  await page.goto(`${ORIGIN}/${url}`, { waitUntil: "load" });
  return { ctx, page };
}
const faixa = (page) => page.locator("#oferta-faixa").isVisible();
const ciclo = (page) => page.locator("#planos").getAttribute("data-ciclo");

const ORDENS = [["config primeiro", { atrasoSub: 900 }], ["assinatura primeiro", { atrasoCfg: 900 }]];

for (const [ordem, atraso] of ORDENS) {
  test(`/ logado sem assinatura vê a campanha (${ordem})`, async () => {
    const { ctx, page } = await pagina("index.html", { active: false }, atraso);
    await page.waitForFunction(() => document.getElementById("oferta-faixa")?.offsetParent, null, { timeout: 5000 });
    assert.equal(await ciclo(page), "anual");
    await ctx.close();
  });

  test(`/ com degraded deixa como está (${ordem})`, async () => {
    const { ctx, page } = await pagina("index.html", { active: false, degraded: true }, atraso);
    await page.waitForTimeout(1600);
    assert.equal(await faixa(page), false);
    assert.equal(await ciclo(page), "mensal");
    await ctx.close();
  });

  test(`/ com assinatura ativa nunca vê a campanha (${ordem})`, async () => {
    const { ctx, page } = await pagina("index.html", { active: true, plan: "plus", interval: "monthly" }, atraso);
    await page.waitForTimeout(1600);
    assert.equal(await faixa(page), false);
    assert.equal(await ciclo(page), "mensal");
    await ctx.close();
  });
}

test("/ logado: enquanto a assinatura não chega a campanha não aparece (só depois de saber)", async () => {
  const { ctx, page } = await pagina("index.html", { active: false }, { atrasoSub: 1500 });
  await page.waitForTimeout(600);
  assert.equal(await faixa(page), false);
  await page.waitForFunction(() => document.getElementById("oferta-faixa")?.offsetParent, null, { timeout: 5000 });
  await ctx.close();
});

test("/precos: vitalício não vê faixa, selo nem preço de oferta", async () => {
  const { ctx, page } = await pagina("precos.html", { active: true, lifetime: true, plan: "plus", interval: null });
  await page.waitForSelector("#plans-v2 .plan");
  await page.waitForFunction(() => window.pbPlanAuthState !== "unknown", null, { timeout: 10000 });
  await page.waitForTimeout(600);
  assert.equal(await faixa(page), false);
  assert.equal(await page.locator(".rm-selo, .rm-cheio, .rm-on").count(), 0);
  await ctx.close();
});

// ── "Tem cupom?" na página de pagamento ─────────────────────────────────────────────────────────────────────

const PLUS = { name: "PigBank Plus", total: { minorUnitsAmount: 0 } };
const BASE = { lineItems: [PLUS], total: { total: { minorUnitsAmount: 0 } },
  recurring: { interval: "month", dueNext: { total: { minorUnitsAmount: 1194 } } } };
const AUTO = [{ amount: 796, displayName: "40% OFF", promotionCode: null }];
const DO_CLIENTE = [{ amount: 500, displayName: "PIGGY", promotionCode: "promo_x" }];
const abrirPagina = (sdk) => abrirP(browser, { sdk });
const abre = (page) => page.locator("#pp-cupom-abre").isVisible();

test("pagamento: desconto automático (campanha, link) esconde 'Tem cupom?'", async () => {
  const { ctx, page } = await abrirPagina({ sessao: { ...BASE, discountAmounts: AUTO } });
  assert.equal(await abre(page), false);
  await ctx.close();
});

test("pagamento: o desconto automático só no getSession (sem change) também esconde", async () => {
  const { ctx, page } = await abrirPagina({ sessao: { ...BASE, discountAmounts: AUTO }, semChange: true });
  assert.equal(await abre(page), false);
  await ctx.close();
});

test("pagamento: o desconto automático que chega num change posterior também esconde", async () => {
  const depoisBump = { ...BASE, discountAmounts: AUTO, lineItems: [PLUS, { name: "Caderno", total: { minorUnitsAmount: 1190 } }] };
  const { ctx, page } = await abrirPagina({ sessao: BASE, depoisBump });
  assert.equal(await abre(page), true);
  await caixas(page).nth(0).click();
  await page.waitForFunction(() => document.getElementById("pp-cupom-abre").hidden);
  await ctx.close();
});

test("pagamento: cupom do cliente (promotionCode) e sessão sem desconto mantêm 'Tem cupom?' (Essencial)", async () => {
  for (const discountAmounts of [DO_CLIENTE, [], undefined]) {
    const { ctx, page } = await abrirPagina({ sessao: { ...BASE, discountAmounts } });
    assert.equal(await abre(page), true, JSON.stringify(discountAmounts));
    await ctx.close();
  }
});

// Link + campanha: o maior % vale e a campanha NÃO repinta por cima. (Repintar não duplica o selo, a função é
// idempotente: o que o controle mede é o preço/selo do link MAIOR sobrescrito pelos 40%.)
test("/precos pelo link de 45% + campanha de 40%: o card do Plus fica com os 45% do link", async () => {
  const link = { email: "l•••@dominio.com", desconto_pct: 45, trial: true, precos: {
    essencial: { pct: 20, monthly: 792, annual: 7920, pix: 7920 },
    plus: { pct: 45, monthly: 1095, annual: 10945, pix: 10945 }, pro: CAMP.precos.pro } };
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  await ctx.route("**/auth/validate", (r) => json(r, {}, 401));
  await ctx.route("**/auth/me", (r) => json(r, { detail: "x" }, 401));
  await ctx.route("**/billing/plans-config", (r) => json(r, { ...CFG(CAMP), retomada: link }));
  const page = await ctx.newPage();
  await page.goto(`${ORIGIN}/precos.html`);
  await page.waitForSelector("#plans-v2 .plan");
  await page.waitForFunction(() => window.pbPlanAuthState !== "unknown", null, { timeout: 10000 });
  await page.waitForTimeout(500);
  const m = page.locator("#plans-v2 article.plan").nth(1).locator("[data-price-monthly]");
  assert.equal(await m.locator(".rm-selo").innerText(), "45% OFF");
  assert.match((await m.innerText()).replace(/\s+/g, " "), /R\$ 10,95/);
  assert.equal(await faixa(page), true);   // a faixa geral da campanha segue
  await ctx.close();
});
