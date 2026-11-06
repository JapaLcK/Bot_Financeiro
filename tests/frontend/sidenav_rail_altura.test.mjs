/**
 * O trilho lateral com os 10 itens do menu (a Casa, PR 6c, é o 9º) e o grupo Conta, que RECOLHE (parts/RailConta.tsx).
 * FECHADO (o padrão fora da Conta): o trilho não rola em nenhuma altura >= 600px (medido até 560px) e os itens ficam em 36px (>= 802px), 32px (670 a 801px) e 30px (<= 669px).
 * O Sair fica fora do que recolhe: à vista (dentro da janela) nos dois estados. ABERTO (numa tela da Conta): o menu tem a Casa a mais e o subtítulo de 44px; o Sair nunca fica cortado sem rolagem: ou cabe na janela, ou o trilho rola (<= 650px).
 * As fronteiras 801 e 669 existem por causa do aberto (787-801px e 657-669px cortavam o Sair sem rolagem); quem mexer num item do menu, da Conta ou no CSS do trilho (shell.css) mede de novo.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { abrir, navegador } from "./_guia.mjs";

const browser = navegador();
void browser;

// [largura, altura, altura do item, espaço entre itens]
const FECHADO = [[1440, 900, 36, 2], [1440, 802, 36, 2], [1440, 801, 32, 0], [1440, 787, 32, 0], [1440, 786, 32, 0], [1440, 670, 32, 0], [1440, 669, 30, 0], [1440, 600, 30, 0], [1366, 650, 30, 0], [1280, 801, 32, 0], [1024, 786, 32, 0]];

for (const [width, height, item, gap] of FECHADO) {
  test(`fechado ${width}x${height}: o trilho não rola, o Sair está na tela, itens de ${item}px e espaço de ${gap}px`, async () => {
    const { page, ctx } = await abrir({ guia: "concluido", width, height });
    try {
      await page.locator(".rail-conta").waitFor();
      const r = await page.evaluate(() => {
        const rail = document.querySelector(".rail"), sair = document.querySelector('.rail-conta button[title="Sair"]').getBoundingClientRect();
        const itens = [...document.querySelectorAll(".rail-list a, .rail-list button, .rail-conta li:not([hidden]) a, .rail-conta li:not([hidden]) button")];
        return {
          rola: rail.scrollHeight - rail.clientHeight, sairAbaixo: Math.round(sair.bottom - innerHeight), itens: [...new Set(itens.map((e) => e.getBoundingClientRect().height))],
          gaps: [getComputedStyle(document.querySelector(".rail-list")).rowGap, getComputedStyle(document.querySelector(".rail-conta ul")).rowGap],
          visiveis: document.querySelectorAll(".rail-conta li:not([hidden])").length, total: document.querySelectorAll(".rail-conta li").length,
          familia: document.querySelectorAll(".rail-conta a[href$=\"/conta/familia\"]").length,
        };
      });
      assert.equal(r.total, 6); // 5 telas da Conta (a Família é a 5ª) e o Sair, todos no DOM
      assert.equal(r.visiveis, 1); // só o Sair, fora do grupo
      assert.ok(r.sairAbaixo <= 0, `Sair ${r.sairAbaixo}px abaixo da janela`);
      assert.equal(r.familia, 1);
      assert.equal(r.rola, 0);
      assert.deepEqual(r.itens, [item]);
      assert.deepEqual(r.gaps, [`${gap}px`, `${gap}px`]);
    } finally { await ctx.close(); }
  });
}

// Aberto (rota da Conta): sem rolar o trilho quando cabe (>= 848px mede o rodapé também); cortado sem rolagem, nunca.
for (const [width, height] of [[1440, 900], [1440, 848], [1440, 802], [1440, 801], [1440, 787], [1440, 700], [1366, 669], [1366, 657], [1440, 651], [1440, 650], [1366, 600], [1280, 600]]) {
  test(`aberto ${width}x${height}: o Sair está na tela ou o trilho rola até ele`, async () => {
    const { page, ctx } = await abrir({ guia: "concluido", width, height, rota: "/conta/familia" });
    try {
      await page.locator(".rail-conta").waitFor();
      const r = await page.evaluate(() => {
        const rail = document.querySelector(".rail"), sair = [...document.querySelectorAll(".rail-conta button")].at(-1);
        return { sair: sair.textContent.trim(), abaixo: Math.round(sair.getBoundingClientRect().bottom - innerHeight), rola: getComputedStyle(rail).overflowY === "auto", visiveis: document.querySelectorAll(".rail-conta li:not([hidden])").length };
      });
      console.log(`  aberto ${width}x${height}: Sair ${r.abaixo <= 0 ? -r.abaixo + "px acima do pé" : r.abaixo + "px abaixo"}; overflow-y ${r.rola ? "auto" : "visible"}`);
      assert.equal(r.sair, "Sair");
      assert.equal(r.visiveis, 6);
      assert.ok(r.abaixo <= 0 || r.rola, `Sair ${r.abaixo}px abaixo da janela e o trilho não rola`);
    } finally { await ctx.close(); }
  });
}
