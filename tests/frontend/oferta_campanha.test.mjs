/**
 * Black Friday (plans-config `campanha`, textos prontos do servidor) em /precos, / e /assinar.
 * Controles (2026-11-06): sem a chamada `aplicarDescontoRetomada(campanha)` da /precos, sem o `campanha()` do
 * oferta.js ou sem o `oferta()` do assinar.js, os testes "com campanha" ficam vermelhos; positivos: Essencial
 * intacto, assinante sem preço de oferta, sem `campanha` nada muda.
 * O servidor de teste é estático: a campanha só existe aqui pelo plans-config mockado (o relógio real não entra).
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { startServer } from "./_server.mjs";
import { chromium } from "playwright";
import { CAMP, CFG } from "./_campanha.mjs";
import { abrir as abrirAssinar, tela, ME, EMBUTIDO } from "./_assinar.mjs";

let ORIGIN, server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer()); browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

const n = (t) => t.replace(/\s+/g, " ").trim();
const json = (r, corpo, status = 200) => r.fulfill({ status, contentType: "application/json", body: JSON.stringify(corpo) });
const VP = [["desktop", { width: 1280, height: 900 }], ["mobile", { width: 390, height: 844 }]];

async function ctxCom({ campanha = CAMP, assinante = false, viewport, atrasoIlha = 0, atrasoSub = 0 } = {}) {
  const ctx = await browser.newContext({ viewport });
  await ctx.route("**/auth/validate", (r) => json(r, {}, assinante ? 200 : 401));
  await ctx.route("**/auth/me", (r) => json(r, { detail: "x" }, 401));
  await ctx.route("**/billing/subscription", async (r) => {
    await new Promise((ok) => setTimeout(ok, atrasoSub));
    return assinante ? json(r, { active: true, plan: "plus", interval: "monthly" }) : json(r, { detail: "x" }, 401); });
  await ctx.route("**/billing/plans-config", (r) => json(r, CFG(campanha)));
  if (atrasoIlha) await ctx.route("**/precos-app.js*", async (r) => { await new Promise((ok) => setTimeout(ok, atrasoIlha)); return r.continue(); });
  return ctx;
}

// ── /precos ─────────────────────────────────────────────────────────────────

async function precos(opts) {
  const ctx = await ctxCom(opts);
  const page = await ctx.newPage();
  await page.goto(`${ORIGIN}/precos.html`);
  await page.waitForSelector("#plans-v2 .plan");
  await page.waitForFunction(() => window.pbPlanAuthState !== "unknown", null, { timeout: 10000 });
  await page.waitForTimeout(500);
  return { ctx, page };
}

for (const [nome, viewport] of VP) {
  test(`/precos com campanha (${nome}): faixa, Plus e Pro com 40%, Essencial intacto, tabela pintada`, async () => {
    const { ctx, page } = await precos({ viewport });
    assert.equal(n(await page.locator("#oferta-faixa").innerText()), CAMP.frase);
    const cards = page.locator("#plans-v2 article.plan");
    for (const [i, cheio] of [[1, "R$ 19,90"], [2, "R$ 49,90"]]) {
      const m = cards.nth(i).locator("[data-price-monthly]");
      assert.equal(await m.locator(".rm-selo").innerText(), "40% OFF");
      assert.equal(n(await m.locator("s.rm-cheio").innerText()), cheio);
    }
    assert.match(n(await cards.nth(1).locator("[data-price-monthly]").innerText()), /R\$ 11,94\s*\/mês/);
    assert.equal(n(await cards.nth(1).locator(".price-cycle-copy").innerText()), "40% OFF na 1ª cobrança, depois R$ 19,90");
    const ess = cards.nth(0);
    assert.equal(await ess.locator(".rm-selo, .rm-cheio").count(), 0);
    assert.equal(await ess.evaluate((e) => e.classList.contains("rm-on")), false);
    assert.ok(await ess.locator(".price-flow-host").first().isVisible(), "NumberFlow do Essencial");
    await page.click("#cycle-annual");
    assert.equal(await ess.locator(".price-eq").first().isVisible(), true, "Equivale do Essencial");
    assert.equal(n(await cards.nth(2).locator("[data-price-annual]").first().innerText()).includes("R$ 299,40"), true);
    const tab = (t, a) => page.locator(`#cmp-v2 [data-plano="${t}"] [data-price-${a}]`).first().textContent().then(n);
    assert.match(await tab("plus", "annual"), /R\$ 119,40/);
    assert.match(await tab("pro", "monthly"), /R\$ 29,94/);
    assert.doesNotMatch(await tab("essencial", "annual"), /OFF|119/);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), "overflow horizontal");
    await page.screenshot({ path: `${process.env.TMPDIR || "/tmp"}/campanha-precos-${nome}.png`, fullPage: false });
    await ctx.close();
  });
}

// Link de retomada + campanha: preço pintado pelo link (maior %), faixa da campanha, um selo por card.
const LINK = { email: "l•••@dominio.com", desconto_pct: 40, trial: true, precos: {
  essencial: { pct: 20, monthly: 792, annual: 7920, pix: 7920 }, plus: CAMP.precos.plus, pro: CAMP.precos.pro } };
async function precosLink(campanha) {
  const ctx = await ctxCom({ campanha, viewport: VP[0][1] });
  await ctx.route("**/billing/plans-config", (r) => json(r, { ...CFG(campanha), retomada: LINK }));
  const page = await ctx.newPage();
  await page.goto(`${ORIGIN}/precos.html`);
  await page.waitForSelector("#plans-v2 .plan");
  await page.waitForFunction(() => window.pbPlanAuthState !== "unknown", null, { timeout: 10000 });
  await page.waitForTimeout(500);
  return { ctx, page };
}
test("/precos pelo link + campanha: faixa geral, um selo por card, Essencial com o % do link", async () => {
  const { ctx, page } = await precosLink(CAMP);
  assert.equal(n(await page.locator("#oferta-faixa").innerText()), CAMP.frase);
  assert.ok(await page.locator("#oferta-faixa").isVisible());
  const cards = page.locator("#plans-v2 article.plan");
  for (const [i, pct] of [[0, "20% OFF"], [1, "40% OFF"], [2, "40% OFF"]]) {
    const m = cards.nth(i).locator("[data-price-monthly]");
    assert.equal(await m.locator(".rm-selo").count(), 1, `selo duplicado no card ${i}`);
    assert.equal(await m.locator(".rm-selo").innerText(), pct);
  }
  await ctx.close();
});
test("/precos pelo link sem campanha: sem faixa", async () => {
  const { ctx, page } = await precosLink(undefined);
  assert.equal(await page.locator("#oferta-faixa").isVisible(), false);
  await ctx.close();
});

// B1: o texto VISÍVEL da coluna (não o 1º span) no anual não pode ter equivalência do preço cheio.
test("/precos: coluna da tabela no anual mostra o equivalente com desconto, nos dois ciclos", async () => {
  const { ctx, page } = await precos({ viewport: VP[0][1] });
  const col = (t) => page.locator(`#cmp-v2 [data-plano="${t}"]`).first().innerText().then(n);
  await page.click("#cycle-annual");
  const [plus, pro] = [await col("plus"), await col("pro")];
  assert.match(plus, /R\$ 119,40/); assert.match(plus, /Equivale a R\$ 9,95\/mês/);
  assert.match(pro, /Equivale a R\$ 24,95\/mês/);
  for (const t of [plus, pro]) assert.doesNotMatch(t, /16,58|41,58/);
  await page.click("#cycle-annual");   // volta ao mensal
  assert.doesNotMatch(await col("plus"), /Equivale/);
  assert.match(await col("plus"), /R\$ 11,94/);
  await page.click("#cycle-annual");
  assert.match(await col("plus"), /Equivale a R\$ 9,95\/mês/);
  await ctx.close();
});

test("/precos: ilha React atrasada, a tabela e os cards ainda saem pintados", async () => {
  const { ctx, page } = await precos({ viewport: VP[0][1], atrasoIlha: 1500 });
  await page.waitForFunction(() => document.querySelector('#cmp-v2 [data-plano="plus"]'), null, { timeout: 8000 });
  await page.waitForTimeout(400);
  assert.match(n(await page.locator('#cmp-v2 [data-plano="plus"] [data-price-annual]').first().textContent()), /R\$ 119,40/);
  assert.equal(await page.locator("#plans-v2 .rm-selo").count() > 0, true);
  await ctx.close();
});

test("/precos: assinante ativo só vê a frase do Pix, sem preço de oferta", async () => {
  const { ctx, page } = await precos({ viewport: VP[0][1], assinante: true });
  assert.equal(n(await page.locator("#oferta-faixa").innerText()), CAMP.frase_assinante);
  assert.equal(await page.locator(".rm-selo, .rm-cheio, .rm-on").count(), 0);
  assert.equal(await page.locator('#cmp-v2 [data-plano="plus"] s').count(), 0);
  await ctx.close();
});

test("/precos sem campanha: nada muda", async () => {
  const { ctx, page } = await precos({ viewport: VP[0][1], campanha: null });
  assert.equal(await page.locator("#oferta-faixa").isVisible(), false);
  assert.equal(await page.locator(".rm-selo, .rm-cheio, .rm-on").count(), 0);
  await ctx.close();
});

// ── / ───────────────────────────────────────────────────────────────────────

async function home(opts) {
  const ctx = await ctxCom(opts);
  const page = await ctx.newPage();
  await page.goto(`${ORIGIN}/index.html`, { waitUntil: "load" });
  await page.waitForTimeout(700);
  return { ctx, page };
}
const card = (page, t) => page.locator(`#planos [data-plano="${t}"]`);
const visivel = (page, t, sel) => card(page, t).locator(sel).filter({ visible: true }).first().innerText().then(n);

for (const [nome, viewport] of VP) {
  test(`/ com campanha (${nome}): faixa, Plus e Pro trocados, abre em Anual, Essencial intacto`, async () => {
    const { ctx, page } = await home({ viewport });
    assert.equal(n(await page.locator("#oferta-faixa").innerText()), CAMP.frase);
    assert.equal(await page.locator("#planos").getAttribute("data-ciclo"), "anual");
    assert.equal(await visivel(page, "plus", "span.pb-d[class*=so-]"), "R$ 9,95");
    assert.equal(await visivel(page, "plus", "span.so-anual:not(.pb-d)"), "R$ 119,40 no 1º ano, depois R$ 199 por ano");
    assert.equal(await visivel(page, "pro", "span.pb-d[class*=so-]"), "R$ 24,95");
    assert.equal(await visivel(page, "pro", "span.so-anual:not(.pb-d)"), "R$ 299,40 no 1º ano, depois R$ 499 por ano");
    assert.equal(await visivel(page, "essencial", "span.so-anual:not(.pb-d)"), "R$ 99 por ano · 2 meses grátis");
    await page.locator("#planos [data-set=mensal]").click();
    assert.equal(await visivel(page, "plus", "span.pb-d[class*=so-]"), "R$ 11,94");
    assert.equal(await visivel(page, "plus", "span.so-mensal:not(.pb-d)"), "1º mês por R$ 11,94, depois R$ 19,90 por mês");
    assert.equal(await visivel(page, "pro", "span.so-mensal:not(.pb-d)"), "1º mês por R$ 29,94, depois R$ 49,90 por mês");
    assert.equal(await visivel(page, "essencial", "span.pb-d[class*=so-]"), "R$ 9,90");
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), "overflow horizontal");
    await page.screenshot({ path: `${process.env.TMPDIR || "/tmp"}/campanha-home-${nome}.png` });
    await ctx.close();
  });
}

test("/: com assinatura ativa a campanha nunca entra, em qualquer ordem dos fetches", async () => {
  for (const atrasoSub of [0, 800]) {
    const { ctx, page } = await home({ viewport: VP[0][1], assinante: true, atrasoSub });
    await page.waitForTimeout(atrasoSub);
    assert.equal(await page.locator("#oferta-faixa").isVisible(), false);
    assert.equal(await page.locator("#planos").getAttribute("data-ciclo"), "mensal");
    assert.equal(await visivel(page, "plus", "span.pb-d[class*=so-]"), "R$ 19,90");
    await ctx.close();
  }
});

test("/ sem campanha: como hoje", async () => {
  const { ctx, page } = await home({ viewport: VP[0][1], campanha: null });
  assert.equal(await page.locator("#oferta-faixa").isVisible(), false);
  assert.equal(await page.locator("#planos").getAttribute("data-ciclo"), "mensal");
  await ctx.close();
});

// ── /assinar ────────────────────────────────────────────────────────────────

const API = { "GET /billing/plans-config": [200, CFG(CAMP)] };
for (const [plano, ciclo, esperado] of [
  ["plus", "annual", CAMP.textos.plus.frase_anual], ["pro", "monthly", CAMP.textos.pro.frase_mensal]]) {
  test(`/assinar ${plano} ${ciclo}: a frase da campanha no S1`, async () => {
    const { ctx, page } = await abrirAssinar(browser, { query: `?plano=${plano}&ciclo=${ciclo}`, api: API });
    await tela(page, "s1");
    await page.waitForFunction(() => !document.getElementById("s1-oferta").hidden);
    assert.equal(n(await page.locator("#s1-oferta").innerText()), esperado);
    await ctx.close();
  });
}

// R7: conta logada de OUTRO e-mail cai no S1 (sem avanço automático) e ainda assim vê a frase.
test("/assinar plus annual, logado com outro e-mail: S1 com a frase da campanha", async () => {
  const { ctx, page } = await abrirAssinar(browser, { query: "?plano=plus&ciclo=annual", hash: "#e=ana%40x.com",
    api: { ...API, "GET /auth/me": ME("outra@x.com") } });
  await tela(page, "s1");
  await page.waitForFunction(() => !document.getElementById("s1-oferta").hidden);
  assert.equal(n(await page.locator("#s1-oferta").innerText()), CAMP.textos.plus.frase_anual);
  await ctx.close();
});

test("/assinar Essencial: nenhuma frase", async () => {
  const { ctx, page } = await abrirAssinar(browser, { query: "?plano=essencial&ciclo=annual", api: API });
  await tela(page, "s1");
  await page.waitForTimeout(300);
  assert.equal(await page.locator("#s1-oferta").isVisible(), false);
  await ctx.close();
});

// S4 (logado, o checkout embutido): a mesma frase, com e sem teste grátis; Essencial sem frase.
for (const [plano, ciclo, trial, esperado] of [
  ["plus", "annual", 7, CAMP.textos.plus.frase_anual], ["pro", "monthly", 0, CAMP.textos.pro.frase_mensal],
  ["essencial", "annual", 7, null]]) {
  test(`/assinar ${plano} ${ciclo} trial ${trial}: a frase da campanha no S4`, async () => {
    const { ctx, page } = await abrirAssinar(browser, { query: `?plano=${plano}&ciclo=${ciclo}`,
      api: { ...API, "GET /auth/me": ME("ana@x.com"), "POST /billing/create-checkout": [200, { ...EMBUTIDO[1], trial_days: trial }] } });
    await tela(page, "s4");
    await page.waitForTimeout(300);
    if (esperado) assert.equal(n(await page.locator("#s4-oferta").innerText()), esperado);
    else assert.equal(await page.locator("#s4-oferta").isVisible(), false);
    await ctx.close();
  });
}

// /precos com `degraded` (Stripe falhou, não se sabe se assina): não pinta, como a /. `active:false` sem `degraded` pinta.
for (const [rotulo, sub, pinta] of [["degraded", { active: false, degraded: true }, false], ["active:false", { active: false }, true]]) {
  test(`/precos com ${rotulo}: ${pinta ? "pinta" : "não pinta"} a campanha`, async () => {
    const ctx = await ctxCom({ viewport: VP[0][1] });
    await ctx.route("**/billing/subscription", (r) => json(r, sub));
    const page = await ctx.newPage();
    await page.goto(`${ORIGIN}/precos.html`);
    await page.waitForSelector("#plans-v2 .plan");
    await page.waitForFunction(() => window.pbPlanAuthState !== "unknown", null, { timeout: 10000 });
    await page.waitForTimeout(500);
    assert.equal(await page.locator("#plans-v2 .rm-selo").count() > 0, pinta);
    assert.equal(await page.locator("#oferta-faixa").isVisible(), pinta);
    await ctx.close();
  });
}
