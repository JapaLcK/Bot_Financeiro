// Guia do /painel (#728): o que o véu deixa passar, além do clique (dashboard_v2_guia_tela.test.mjs).
// Teclado: a tecla só chega ao balão e ao alvo ATUAL; o foco que ficou num controle que o véu
// cobriu depois (a seta já usada, na comemoração; a linha de categoria de onde o alvo migrou)
// não ativa nada. Borda: o que está aceso é exatamente o que se toca.
import { test } from "node:test";
import assert from "node:assert/strict";
import { FAZER, PASSOS, abrir, acoes, bora, esperaTitulo, navegador } from "./_guia.mjs";

navegador();

const mes = (page) => page.evaluate(() => document.querySelector(".month-title").textContent);
const noPonto = (page, sel) => page.locator(sel).evaluate((e) => { const r = e.getBoundingClientRect(); return [(r.left + r.right) / 2, (r.top + r.bottom) / 2]; });

// O guarda devolve o foco ao balão na 1ª tecla, então cada controle já usado só pode ser
// testado com uma: as duas variantes trocam Enter e Espaço entre a seta e a linha.
for (const [semDialog, naSeta, naLinha] of [[false, "Enter", "Space"], [true, "Space", "Enter"]]) {
  test(`teclado com o véu (${semDialog ? "Safari 14" : "nativo"}): Enter no alvo faz o passo; ${naSeta} na seta já usada e ${naLinha} na linha já usada não fazem nada`, async () => {
    const { ctx, page, s } = await abrir({ semDialog });
    await bora(page);
    await page.waitForTimeout(200);
    // Caminho legítimo: Tab até a seta e Enter.
    const inicio = await mes(page);
    for (let i = 0; i < 8 && !(await page.evaluate(() => document.activeElement?.matches('[data-guia="mes.trocar"]'))); i++) await page.keyboard.press("Tab");
    await page.keyboard.press("Enter");
    await page.locator(".guia-balao").getByText("Vem comigo").waitFor();
    const depois = await mes(page);
    // Comemoração: o véu fechou o furo, o foco ainda está na seta.
    const focoFesta = await page.evaluate(() => document.activeElement?.getAttribute("aria-label"));
    await page.keyboard.press(naSeta);
    await page.waitForTimeout(100);
    const festa = [await mes(page), await page.evaluate(() => document.activeElement?.id)];
    await esperaTitulo(page, PASSOS[1].fala.titulo);
    await page.locator('[data-guia="categorias.item"]').waitFor();
    await page.waitForTimeout(300);
    // Passo 2: a linha clicada fica com o foco; o `data-guia` migra para outra linha.
    await page.mouse.click(...await noPonto(page, '[data-guia="categorias.item"]'));
    await page.evaluate(() => { window.__linha = document.activeElement; });
    const cat = () => page.evaluate(() => [window.__linha.getAttribute("aria-pressed"), new URLSearchParams(location.hash.split("?")[1] || "").get("category")]);
    const aposClique = await cat();
    await page.keyboard.press(naLinha);
    await page.waitForTimeout(300);
    const aposTecla = [await cat(), await page.evaluate(() => document.activeElement?.id)];
    await ctx.close();
    assert.notEqual(depois, inicio, "o Enter no alvo troca o mês");
    assert.equal(focoFesta, "Mês anterior");
    assert.deepEqual(festa, [depois, "guia-titulo"], `na comemoração o ${naSeta} não troca o mês de novo`);
    assert.equal(aposClique[0], "true");
    assert.deepEqual(aposTecla, [aposClique, "guia-titulo"], `o ${naLinha} na linha já usada não desmarca nem troca de filtro`);
    assert.deepEqual(acoes(s), ["visto", "feito:resumo.saiu", "feito:gastos.categoria"]);
  });
}

// O furo escuro e as faixas que bloqueiam o toque usam o mesmo retângulo: 2 px fora da borda do
// alvo é escuro e véu; 2 px dentro é claro e o alvo. O anel fica por fora, sem toque.
test("borda do furo: o aceso é o clicável (2 px fora = escuro e véu; 2 px dentro = claro e alvo)", async () => {
  const { ctx, page } = await abrir();
  await bora(page);
  await page.waitForTimeout(300);
  const borda = (sel) => page.evaluate((sel) => {
    const e = document.querySelector(sel), r = e.getBoundingClientRect(), path = document.querySelector(".guia-sombra path");
    const mx = (r.left + r.right) / 2, my = (r.top + r.bottom) / 2;
    const ponto = (x, y) => {
      const t = document.elementFromPoint(x, y);
      return `${path.isPointInFill(new DOMPoint(x, y)) ? "escuro" : "claro"}:${e.contains(t) ? "alvo" : t?.classList.contains("guia-veu") ? "véu" : t?.className || t?.tagName}`;
    };
    return {
      fora: [ponto(r.left - 2, my), ponto(r.right + 2, my), ponto(mx, r.top - 2), ponto(mx, r.bottom + 2)],
      dentro: [ponto(r.left + 2, my), ponto(r.right - 2, my), ponto(mx, r.top + 2), ponto(mx, r.bottom - 2)],
    };
  }, sel);
  const seta = await borda('[data-guia="mes.trocar"]');
  await FAZER["mes.trocado"](page);
  await esperaTitulo(page, PASSOS[1].fala.titulo);
  await page.locator('[data-guia="categorias.item"]').waitFor();
  await page.waitForTimeout(300);
  const linha = await borda('[data-guia="categorias.item"]');
  await ctx.close();
  for (const b of [seta, linha]) {
    assert.deepEqual(b.fora, Array(4).fill("escuro:véu"), JSON.stringify(b));
    assert.deepEqual(b.dentro, Array(4).fill("claro:alvo"), JSON.stringify(b));
  }
});
