/**
 * /black-friday no navegador (o HTML cru: o estado por servidor é testado em tests/test_black_friday_pagina.py).
 * Cobre o que só o navegador faz: o repasse de utm_* e fbclid aos CTAs (`teste-piggy.js`, reaproveitado) e o aviso
 * do suporte no lugar dos botões para quem já assina (`/billing/subscription`). Controles: sem o `data-utm` dos CTAs
 * ou sem o `fetch` da assinatura os testes ficam vermelhos; positivos: anônimo e `degraded` não mudam nada.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { startServer } from "./_server.mjs";
import { chromium } from "playwright";
import { CAMP, CFG } from "./_campanha.mjs";

let ORIGIN, server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer()); browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

const json = (r, corpo, status = 200) => r.fulfill({ status, contentType: "application/json", body: JSON.stringify(corpo) });

async function abre(sub, query = "") {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
  await ctx.route("**/billing/subscription", (r) => json(r, sub.corpo || { detail: "x" }, sub.status || 401));
  const page = await ctx.newPage();
  await page.goto(`${ORIGIN}/black-friday.html${query}`);
  await page.waitForTimeout(500);
  return { ctx, page };
}
const ctas = (page) => page.locator("a.bf-cta[data-utm]");

test("utm e fbclid chegam ao /assinar em todos os CTAs; o resto da query não", async () => {
  const { ctx, page } = await abre({}, "?utm_source=email&utm_medium=remarketing&utm_campaign=black-friday-2026&fbclid=AB1&x=1");
  const hrefs = await ctas(page).evaluateAll((as) => as.map((a) => a.getAttribute("href")));
  assert.ok(hrefs.length >= 3, "topo, cartões e fim");
  for (const h of hrefs) {
    assert.match(h, /^\/assinar\?plano=(plus|pro)&ciclo=annual&utm_source=email&utm_medium=remarketing&utm_campaign=black-friday-2026&fbclid=AB1$/);
  }
  await ctx.close();
});

test("anônimo e degraded: os botões ficam e o aviso do suporte não aparece", async () => {
  for (const sub of [{}, { status: 200, corpo: { active: false, degraded: true } }, { status: 200, corpo: { active: false } }]) {
    const { ctx, page } = await abre(sub);
    assert.ok(await ctas(page).first().isVisible());
    assert.equal(await page.locator(".bf-assinante:visible").count(), 0);
    await ctx.close();
  }
});

for (const corpo of [{ active: true, plan: "plus" }, { active: false, lifetime: true }]) {
  test(`assinante (${JSON.stringify(corpo)}): aviso do suporte no lugar dos botões, sem overflow`, async () => {
    const { ctx, page } = await abre({ status: 200, corpo });
    assert.equal(await page.locator("a.bf-cta:visible").count(), 0);
    assert.ok(await page.locator(".bf-assinante:visible").count() >= 3);
    assert.match(await page.locator(".bf-assinante:visible").first().innerText(), /fala com o suporte/);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), "overflow horizontal");
    await ctx.close();
  });
}

// Pop-up do cupom de 5% (cupom-popup.js): não abre na campanha. Controle negativo: sem a leitura do plans-config
// no `agendar`, o 1º caso abre o pop-up; positivo: sem `campanha` ele abre (o relógio é falso: 15 s viram instantes).
for (const [rotulo, campanha, abre_] of [["com campanha", CAMP, false], ["sem campanha", null, true]]) {
  test(`pop-up do cupom ${rotulo}: ${abre_ ? "abre" : "não abre"}`, async () => {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    await ctx.addInitScript(() => Object.defineProperty(navigator, "webdriver", { get: () => false }));
    await ctx.route("**/auth/validate", (r) => json(r, {}, 401));
    await ctx.route("**/billing/plans-config", (r) => json(r, CFG(campanha)));
    const page = await ctx.newPage();
    await page.clock.install();
    await page.goto(`${ORIGIN}/precos.html`);
    await page.waitForTimeout(500);
    await page.clock.fastForward(16000);
    await page.waitForTimeout(300);
    assert.equal(await page.locator("#pb-cupom").count(), abre_ ? 1 : 0);
    await ctx.close();
  });
}
