/**
 * /painel#/metas sob estresse (fixture `estresse`): nome de 60 caracteres sem espaço e todo valor no teto de
 * 999.999.999,99 (o ritmo negativo, a reserva de 24 meses). Em 375 e 1440, na lista, no formulário e no "Apagar"
 * (o título leva o nome): a página não ganha rolagem horizontal e nada passa da borda do cartão ou do diálogo.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { RESPOSTAS, abrirPainel, exigeArtefatoEmDia } from "./_painel.mjs";

let browser;
before(async () => { exigeArtefatoEmDia(); browser = await chromium.launch(); });
after(() => browser?.close());

// Rolagem horizontal da página + o que passa da direita do `caixa` (cada cartão, ou o diálogo).
const medir = (page, caixa) => page.evaluate((sel) => {
  const fora = [];
  for (const c of document.querySelectorAll(sel)) {
    const lim = c.getBoundingClientRect().right + 0.5;
    for (const e of c.querySelectorAll("*")) if (e.getBoundingClientRect().width && e.getBoundingClientRect().right > lim) fora.push(`${e.className || e.tagName}: ${(e.textContent || "").slice(0, 30)}`);
  }
  return { rolagem: document.documentElement.scrollWidth - window.innerWidth, fora };
}, caixa);

for (const width of [375, 1440]) test(`${width}: nome de 60 caracteres e valores no teto não estouram a lista, o formulário nem o Apagar`, async () => {
  const { ctx, page, ir } = await abrirPainel(browser, { espera: null, width });
  await ctx.route(/\/api\/v2\/metas$/, (r) => r.request().method() === "GET" ? r.fulfill({ json: RESPOSTAS.metas.estresse }) : r.fallback());
  await ir("/metas");
  await page.locator("article.meta").first().waitFor();
  const nome = RESPOSTAS.metas.estresse.metas[1].nome;
  assert.equal(nome.length, 60); // o estresse é o nome inteiro, sem espaço
  assert.deepEqual(await medir(page, "article.meta"), { rolagem: 0, fora: [] }, "lista");
  await page.getByRole("button", { name: "Nova meta" }).click();
  await page.locator(".meta-form[open]").waitFor();
  assert.deepEqual(await medir(page, ".meta-form"), { rolagem: 0, fora: [] }, "formulário");
  await page.locator(".meta-form").getByRole("button", { name: "Cancelar" }).click();
  await page.locator(".meta-form[open]").waitFor({ state: "detached" });
  await page.getByRole("button", { name: `Editar ${nome}` }).click();
  await page.locator(".meta-form").getByRole("button", { name: "Apagar", exact: true }).click();
  await page.locator(".meta-form h2", { hasText: `Apagar ${nome}?` }).waitFor();
  assert.deepEqual(await medir(page, ".meta-form"), { rolagem: 0, fora: [] }, "apagar");
  await ctx.close();
});
