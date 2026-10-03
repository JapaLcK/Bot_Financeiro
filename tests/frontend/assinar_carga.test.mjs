/**
 * /assinar: o /auth/me da CARGA (estado C) não tem prazo — o ME_MS é só do clique do
 * S2 (assinar_estados). Plano §3, C: pendurado continua em C; 401/rede → S1. 5xx → S1,
 * como rede (o plano não cobre; é o que o código já fazia).
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { abrir, tela, FRAG } from "./_assinar.mjs";

let browser;
before(async () => { browser = await chromium.launch(); });
after(async () => { await browser?.close(); });

test("/auth/me pendurado na carga: 60 s depois continua em C, sem cair no S1", async () => {
  const { ctx, page, reqs } = await abrir(browser, { hash: FRAG, api: { "GET /auth/me": null }, relogio: true });
  while (!reqs.some((r) => r.path === "/auth/me")) await new Promise((ok) => setTimeout(ok, 20));
  await page.clock.runFor(60000);
  await new Promise((ok) => setTimeout(ok, 300));  // tempo real: deixa um S1 tardio aparecer
  assert.equal(await page.locator("#c-carga").isVisible(), true);
  assert.equal(await page.locator("#s1").isVisible(), false);
  await ctx.close();
});

for (const [nome, resp] of [["falha de rede", "aborta"], ["503", [503, { detail: "x" }]]]) {
  test(`/auth/me da carga com ${nome}: S1 com os campos do fragmento`, async () => {
    const { ctx, page } = await abrir(browser, { hash: FRAG, api: { "GET /auth/me": resp } });
    await tela(page, "s1");
    assert.equal(await page.inputValue("#email"), "ana@x.com");
    await ctx.close();
  });
}
