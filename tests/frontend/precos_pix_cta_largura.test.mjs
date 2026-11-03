/**
 * O botão "Pagar no Pix (à vista, sem teste grátis)" dos cartões da /precos (anual) não pode alargar o cartão
 * nem a página, nem deixar o texto passar da borda: ele quebra em 2 linhas só onde não cabe (precos.html, regra
 * do `.pix-cta`). Onde cabe, fica em 1 linha (1280: 50px, como antes do texto novo).
 *
 * Controle (CLAUDE.md §3), medido em 2026-11-03: sem a @media do `.pix-cta`, 320 e 375 ficam vermelhos (a
 * página ganha 35/8px de rolagem lateral), 390 e 414 também (o cartão passa da grade) e 1024 (texto 35px fora do
 * botão). Positivo: 1280 em 1 linha, verde com e sem a regra.
 *
 * Rodar: node --test tests/frontend/precos_pix_cta_largura.test.mjs
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { startServer } from "./_server.mjs";
import { chromium } from "playwright";

let ORIGIN, server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer()); browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

const json = (r, status, corpo) => r.fulfill({ status, contentType: "application/json", body: JSON.stringify(corpo) });

async function medir(largura) {
  const page = await browser.newPage({ viewport: { width: largura, height: 900 } });
  await page.route("**/auth/me", (r) => json(r, 401, { detail: "x" }));
  await page.route("**/billing/subscription", (r) => json(r, 401, { detail: "x" }));
  await page.route("**/billing/plans-config", (r) => json(r, 200, {
    essencial_available: true, plus_available: true, pro_available: true, pix_annual_available: true }));
  await page.goto(`${ORIGIN}/precos.html`);
  await page.waitForSelector("#plans-v2 .plan");
  await page.evaluate(() => document.fonts.ready);
  await page.click("#cycle-annual");
  await page.waitForSelector("[data-pix-cta]");
  const m = await page.$$eval("[data-pix-cta]", (bs) => {
    const grade = document.getElementById("plans-v2").getBoundingClientRect();
    return {
      pagina: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      botoes: bs.map((b) => {
        const r = b.getBoundingClientRect(), cartao = b.closest(".plan").getBoundingClientRect();
        return { texto: b.textContent.trim(), corte: b.scrollWidth - b.clientWidth, direita: r.right,
                 cartaoNaGrade: cartao.right <= grade.right + 0.5, altura: Math.round(r.height) };
      }),
    };
  });
  await page.close();
  return m;
}

for (const largura of [320, 375, 390, 414, 1024]) {
  test(`${largura}px: o botão do Pix não alarga a página nem o cartão, e o texto não passa da borda`, async () => {
    const m = await medir(largura);
    assert.equal(m.pagina, 0, "rolagem lateral");
    assert.equal(m.botoes.length, 3);
    for (const b of m.botoes) {
      assert.equal(b.texto, "Pagar no Pix (à vista, sem teste grátis)");
      assert.ok(b.direita <= largura && b.cartaoNaGrade, `botão ou cartão fora do lugar: ${JSON.stringify(b)}`);
      assert.ok(b.corte <= 0, `texto ${b.corte}px fora do botão`);
    }
  });
}

test("1280px: o botão do Pix cabe em 1 linha (50px), como antes do texto novo", async () => {
  const m = await medir(1280);
  assert.equal(m.pagina, 0);
  assert.deepEqual(m.botoes.map((b) => [b.altura, b.corte <= 0]), Array(3).fill([50, true]));
});
