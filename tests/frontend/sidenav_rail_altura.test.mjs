/**
 * O trilho lateral com os 5 itens da Conta (a Família é o 5º): em nenhuma altura >= 625px ele rola ou corta o "Sair", e o espaço de 2px
 * entre os itens (o visual de sempre) fica nas telas altas. Fronteiras medidas: normal a partir de 787px; compacto 32px de 657 a 786px;
 * compacto 30px de 625 a 656px. Quem mexer no item da Conta ou no CSS do trilho mede de novo estas alturas.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { abrir, navegador } from "./_guia.mjs";

const browser = navegador();
void browser;

// [largura, altura, altura do item, espaço entre itens]
const CASOS = [[1440, 900, 36, 2], [1440, 787, 36, 2], [1440, 786, 32, 0], [1440, 770, 32, 0], [1440, 769, 32, 0], [1440, 657, 32, 0], [1440, 656, 30, 0], [1440, 625, 30, 0], [1280, 770, 32, 0], [1024, 769, 32, 0]];

for (const [width, height, item, gap] of CASOS) {
  test(`${width}x${height}: o trilho não rola, o Sair está na tela, itens de ${item}px e espaço de ${gap}px`, async () => {
    const { page, ctx } = await abrir({ guia: "concluido", width, height });
    try {
      await page.locator(".rail-conta").waitFor();
      const r = await page.evaluate(() => {
        const rail = document.querySelector(".rail"), sair = [...document.querySelectorAll(".rail-conta button")].at(-1).getBoundingClientRect();
        const itens = [...document.querySelectorAll(".rail-list a, .rail-list button, .rail-conta a, .rail-conta button")];
        return {
          rola: rail.scrollHeight - rail.clientHeight, sairAbaixo: Math.round(sair.bottom - innerHeight), itens: [...new Set(itens.map((e) => e.getBoundingClientRect().height))],
          gaps: [getComputedStyle(document.querySelector(".rail-list")).rowGap, getComputedStyle(document.querySelector(".rail-conta ul")).rowGap],
          familia: document.querySelectorAll(".rail-conta a[href$=\"/conta/familia\"]").length, conta: document.querySelectorAll(".rail-conta li").length,
        };
      });
      console.log(`  ${width}x${height}: rola ${r.rola}px; Sair ${-r.sairAbaixo}px acima do pé; itens ${r.itens}; espaço ${r.gaps}`);
      assert.equal(r.conta, 6); // 5 telas da Conta (a Família é a 5ª) e o Sair
      assert.equal(r.familia, 1);
      assert.equal(r.rola, 0);
      assert.ok(r.sairAbaixo <= 0, `Sair ${r.sairAbaixo}px abaixo da janela`);
      assert.deepEqual(r.itens, [item]);
      assert.deepEqual(r.gaps, [`${gap}px`, `${gap}px`]);
    } finally { await ctx.close(); }
  });
}
