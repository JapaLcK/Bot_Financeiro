/**
 * /assinar com o cookie de retomada: sem sessão, o plans-config traz `retomada.email` e a
 * página pula o S1 direto para o S3 com o e-mail mascarado. Controles: sem retomada o S1
 * continua; sessão viva vence; Sair faz POST /auth/logout e volta ao S1.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { abrir, tela, ME } from "./_assinar.mjs";

let browser;
before(async () => { browser = await chromium.launch(); });
after(async () => { await browser?.close(); });

const RET = [200, { pix_annual_available: false, retomada: { email: "l•••@dominio.com" } }];
const SEM = [200, { pix_annual_available: false, retomada: null }];

test("em retomada: #conta mascarado e nenhum S1", async () => {
  const { ctx, page } = await abrir(browser, { api: { "GET /billing/plans-config": RET } });
  await page.locator("#stripe-checkout iframe").waitFor();  // o S3 passa direto ao S4
  assert.equal(await page.locator("#conta").isVisible(), true);
  assert.equal(await page.locator("#conta-email").textContent(), "l•••@dominio.com");
  assert.equal(await page.locator("#s1").isVisible(), false);
  await ctx.close();
});

test("sem retomada e sem sessão: S1", async () => {
  const { ctx, page } = await abrir(browser, { api: { "GET /billing/plans-config": SEM } });
  await tela(page, "s1");
  await ctx.close();
});

test("plans-config falha: S1 como hoje", async () => {
  const { ctx, page } = await abrir(browser, { api: { "GET /billing/plans-config": "aborta" } });
  await tela(page, "s1");
  await ctx.close();
});

test("plans-config pendurado: S1 depois do prazo", async () => {
  const { ctx, page } = await abrir(browser, { api: { "GET /billing/plans-config": null } });
  await tela(page, "s1");
  await ctx.close();
});

test("sessão viva vence a retomada", async () => {
  const { ctx, page } = await abrir(browser, { api: { "GET /auth/me": ME("ana@x.com"), "GET /billing/plans-config": RET } });
  await page.locator("#stripe-checkout iframe").waitFor();
  assert.equal(await page.locator("#conta-email").textContent(), "a•••@x.com");
  await ctx.close();
});

test("Sair em retomada: POST /auth/logout e volta ao S1", async () => {
  // O plans-config devolve a retomada só até o logout; depois dele o cookie morreu.
  let saiu = false;
  const { ctx, page, posts } = await abrir(browser, { api: {
    "GET /billing/plans-config": () => (saiu ? SEM : RET),
    "POST /auth/logout": () => { saiu = true; return [200, { ok: true }]; },
  } });
  await page.locator("#stripe-checkout iframe").waitFor();
  await page.click("#sair");
  await tela(page, "s1");
  assert.equal(posts("/auth/logout").length, 1);
  await ctx.close();
});

test("sessão viva (A) com e-mail digitado ≠ A: nunca mostra a retomada (B)", async () => {
  // Sem sessão viva o ramo da retomada roda; com sessão ele não pode rodar (`atual === ""`, não `!== null`).
  const B = [200, { pix_annual_available: false, retomada: { email: "b•••@outro.com" } }];
  const { ctx, page } = await abrir(browser, { hash: "#e=digitado@x.com", api: { "GET /auth/me": ME("ana@x.com"), "GET /billing/plans-config": B } });
  await tela(page, "s1");
  assert.equal(await page.locator("#stripe-checkout iframe").count(), 0, "foi ao pagamento com a conta B");
  assert.equal(await page.locator("#conta-email").textContent(), "");  // B nunca no #conta
  assert.doesNotMatch(await page.locator("body").innerText(), /b•••@outro/);
  await ctx.close();
});
