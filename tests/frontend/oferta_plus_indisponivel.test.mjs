/**
 * A / com o Plus fora de venda (`plus_available: false` no /billing/plans-config, o mesmo critério dos cartões):
 * os 4 CTAs diretos ao Plus (cabeçalho, hero, fim, barra) caem na tabela (#planos), sem UTM grudada, em
 * qualquer ordem entre o plans-config e o teste-piggy.js. Assinante ativo vai à /precos, em qualquer ordem
 * entre o plans-config e o /billing/subscription. Campo ausente ou erro no plans-config: nada muda.
 *
 * Controles (CLAUDE.md §3), medidos em 2026-11-03: sem o bloco `plus_available === false` do oferta.js, os
 * dois "Plus indisponível" ficam vermelhos (os CTAs seguem para a /assinar); sem o `[href^="/"]` do
 * teste-piggy.js, "plans-config ANTES do teste-piggy" fica vermelho (#planos ganha a UTM); sem o CTAS no
 * seletor do assinante, "assinante, plans-config antes" fica vermelho (fica #planos); sem a guarda
 * `startsWith("/assinar")` do plans-config, "assinante, plans-config depois" fica vermelho (perde o /precos).
 * Positivo: "Plus à venda".
 *
 * Rodar: node --test tests/frontend/oferta_plus_indisponivel.test.mjs
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { startServer } from "./_server.mjs";
import { chromium } from "playwright";

let ORIGIN, server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer()); browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

const CTAS = 'a[data-utm]:not([class*="so-"])';
const PLUS = "/assinar?plano=plus&ciclo=monthly&utm_source=fb&fbclid=abc";
const espera = (ms) => new Promise((ok) => setTimeout(ok, ms));
const json = (r, status, corpo) => r.fulfill({ status, contentType: "application/json", body: JSON.stringify(corpo) });

/** `cfg`: corpo do plans-config (número = só o status). `atraso`: ms em cada rota (a ordem dos eventos). */
async function abrir({ cfg, atraso = {}, assinante = false }) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  await ctx.route("**/auth/validate", (r) => json(r, assinante ? 200 : 401, {}));
  await ctx.route("**/auth/dashboard-profile", (r) => json(r, 200, { email: "a@b.com", plan: "plus" }));
  await ctx.route("**/billing/subscription", async (r) => { await espera(atraso.sub || 0); return json(r, 200, { active: true }); });
  await ctx.route("**/billing/plans-config", async (r) => { await espera(atraso.cfg || 0);
    return typeof cfg === "number" ? json(r, cfg, {}) : json(r, 200, cfg); });
  await ctx.route("**/teste-piggy.js*", async (r) => { await espera(atraso.utm || 0); return r.continue(); });
  const page = await ctx.newPage();
  await page.goto(`${ORIGIN}/index.html?utm_source=fb&fbclid=abc`, { waitUntil: "load" });
  return { ctx, page };
}
const hrefs = (page, sel = CTAS) => page.$$eval(sel, (as) => as.map((a) => a.getAttribute("href")));
const plusFora = { essencial_available: true, plus_available: false, pro_available: true };
const indisponivel = (page) => page.waitForFunction(() => document.querySelectorAll('[data-plano="plus"] [aria-disabled="true"]').length === 2);

for (const [ordem, atraso] of [["plans-config ANTES do teste-piggy", { utm: 900 }], ["plans-config DEPOIS do teste-piggy", { cfg: 900 }]]) {
  test(`Plus indisponível, ${ordem}: os 4 CTAs vão à tabela (#planos), sem UTM; os outros cartões levam a UTM`, async () => {
    const { ctx, page } = await abrir({ cfg: plusFora, atraso });
    await indisponivel(page);
    await page.waitForTimeout(200);
    const ctas = await hrefs(page), pro = await page.getAttribute('[data-plano="pro"] a.so-anual', "href");
    await ctx.close();
    assert.deepEqual(ctas, Array(4).fill("#planos"));
    assert.equal(pro, "/assinar?plano=pro&ciclo=annual&utm_source=fb&fbclid=abc");
  });
}

test("Plus à venda: os 4 CTAs seguem para a /assinar do Plus com a UTM", async () => {
  const { ctx, page } = await abrir({ cfg: { essencial_available: true, plus_available: true, pro_available: true } });
  await page.waitForTimeout(500);
  const ctas = await hrefs(page);
  await ctx.close();
  assert.deepEqual(ctas, Array(4).fill(PLUS));
});

for (const [nome, cfg] of [["sem o campo plus_available", { essencial_available: true }], ["plans-config com 500", 500]]) {
  test(`${nome}: nada muda nos CTAs`, async () => {
    const { ctx, page } = await abrir({ cfg });
    await page.waitForTimeout(500);
    const ctas = await hrefs(page);
    await ctx.close();
    assert.deepEqual(ctas, Array(4).fill(PLUS));
  });
}

for (const [ordem, atraso] of [["plans-config antes", { sub: 900 }], ["plans-config depois", { cfg: 900 }]]) {
  test(`assinante com o Plus indisponível, ${ordem}: os CTAs vão à /precos (regra fixa, sem depender da ordem)`, async () => {
    const { ctx, page } = await abrir({ cfg: plusFora, atraso, assinante: true });
    await indisponivel(page);
    await page.waitForFunction(() => document.querySelector("[data-rotulo]").textContent === "Gerenciar meu plano");
    await page.waitForTimeout(200);
    const ctas = await hrefs(page);
    await ctx.close();
    assert.ok(ctas.length >= 3, ctas.join());   // logado, o nav-auth troca o CTA do cabeçalho pelo menu da conta
    for (const h of ctas) assert.match(h, /^\/precos(\?|$)/, ctas.join());
  });
}
