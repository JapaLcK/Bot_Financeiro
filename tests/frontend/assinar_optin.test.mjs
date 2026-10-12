/**
 * /assinar: a frase de opt-in do WhatsApp (remarketing, PR 1). Vale como consentimento
 * (docs/plano-remarketing.md, decisão 4), então a medição é a de leitor: a frase exata,
 * logo abaixo do campo, visível e sem estourar a largura, no desktop e no celular.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { abrir, tela } from "./_assinar.mjs";

const FRASE = "Você vai receber mensagens do PigBank neste número. Responda PARAR para sair.";

let browser;
before(async () => { browser = await chromium.launch(); });
after(async () => { await browser?.close(); });

for (const [nome, viewport] of [["desktop", { width: 1280, height: 800 }], ["mobile", { width: 390, height: 844 }]]) {
  test(`opt-in sob o campo de WhatsApp (${nome} ${viewport.width}px)`, async () => {
    const { ctx, page } = await abrir(browser, { viewport });
    await tela(page, "s1");
    const hint = page.locator(".field:has(#whatsapp) .hint", { hasText: FRASE });
    await hint.waitFor({ state: "visible" });
    assert.equal((await hint.textContent()).trim(), FRASE);
    const campo = await page.locator("#whatsapp").boundingBox();
    const caixa = await hint.boundingBox();
    const medidas = await page.evaluate(() => ({ rolagem: document.documentElement.scrollWidth, janela: innerWidth }));
    console.log(`# ${nome}: campo.bottom=${campo.y + campo.height} hint.top=${caixa.y} hint.x=${caixa.x}..${caixa.x + caixa.width} ` +
      `altura=${caixa.height} rolagem=${medidas.rolagem}/${medidas.janela}`);
    assert.ok(caixa.y >= campo.y + campo.height, "a frase fica abaixo do campo");
    assert.ok(caixa.x >= 0 && caixa.x + caixa.width <= viewport.width, "a frase cabe na largura");
    assert.ok(medidas.rolagem <= medidas.janela, "sem rolagem horizontal");
    if (process.env.PB_SHOT) await page.screenshot({ path: `${process.env.PB_SHOT}-${nome}.png` });
    await ctx.close();
  });
}
