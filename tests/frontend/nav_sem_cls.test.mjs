/**
 * Reprodução do salto causado pelo aprimoramento progressivo da nav mobile.
 * Atrasar o nav-burger.js garante um paint com o fallback antes de o menu ser
 * recolhido, exatamente a sequência que gerou CLS 0,139 em produção.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { startServer } from "./_server.mjs";
import { chromium } from "playwright";

let ORIGIN, server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer());
                     browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

test("ativar o menu mobile não desloca o conteúdo depois do primeiro paint", async () => {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
  let liberarScript;
  const scriptBloqueado = new Promise((resolve) => { liberarScript = resolve; });
  await ctx.route("**/nav-burger.js*", async (route) => {
    await scriptBloqueado;
    await route.continue();
  });

  const page = await ctx.newPage();
  const navegacao = page.goto(`${ORIGIN}/index.html`);
  await page.waitForSelector("main.wrap");
  // Dois frames garantem que o estado anterior ao script chegou a ser pintado.
  await page.evaluate(() => new Promise((resolve) =>
    requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const topoAntes = await page.$eval("main.wrap", (el) => el.getBoundingClientRect().top);

  liberarScript();
  await navegacao;
  await page.waitForSelector(".nav.pb-nav-ready");
  const topoDepois = await page.$eval("main.wrap", (el) => el.getBoundingClientRect().top);

  assert.equal(topoDepois, topoAntes,
    `o conteúdo pulou ${+(topoDepois - topoAntes).toFixed(1)}px quando o menu foi ativado`);
  await ctx.close();
});
