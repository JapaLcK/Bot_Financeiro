// Guia do /painel (#728): o que o véu acende e deixa passar (o clique fora do alvo está em
// dashboard_v2_guia_tela.test.mjs). Teclado: a tecla só chega ao balão e ao alvo ATUAL; o foco que
// ficou num controle que o véu cobriu depois (a seta já usada, na comemoração; a linha de
// categoria de onde o alvo migrou) não ativa nada. Borda: o que está aceso é exatamente o que se
// toca. Por etapa: convite escuro; no bloco o Saiu aceso e sem toque; no alvo, furo e anel nele.
import { test } from "node:test";
import assert from "node:assert/strict";
import { FAZER, PASSOS, abrir, acoes, bora, esperaTitulo, irAoAlvo, navegador } from "./_guia.mjs";

navegador();

const mes = (page) => page.evaluate(() => document.querySelector(".month-title").textContent);
const noPonto = (page, sel) => page.locator(sel).evaluate((e) => { const r = e.getBoundingClientRect(); return [(r.left + r.right) / 2, (r.top + r.bottom) / 2]; });

// O guarda devolve o foco ao balão na 1ª tecla, então cada controle já usado só pode ser
// testado com uma: as duas variantes trocam Enter e Espaço entre a seta e a linha.
for (const [semDialog, naSeta, naLinha] of [[false, "Enter", "Space"], [true, "Space", "Enter"]]) {
  test(`teclado com o véu (${semDialog ? "Safari 14" : "nativo"}): Enter no alvo faz o passo; ${naSeta} na seta já usada e ${naLinha} na linha já usada não fazem nada`, async () => {
    const { ctx, page, s } = await abrir({ semDialog });
    await bora(page);
    await irAoAlvo(page, '[data-guia="mes.trocar"]');
    // Caminho legítimo: Tab até a seta e Enter.
    const inicio = await mes(page);
    for (let i = 0; i < 8 && !(await page.evaluate(() => document.activeElement?.matches('[data-guia="mes.trocar"]'))); i++) await page.keyboard.press("Tab");
    await page.keyboard.press("Enter");
    await page.locator(".guia-balao").getByText("Bora pro próximo").waitFor();
    const depois = await mes(page);
    // Comemoração: o véu fechou o furo, o foco ainda está na seta.
    const focoFesta = await page.evaluate(() => document.activeElement?.getAttribute("aria-label"));
    await page.keyboard.press(naSeta);
    await page.waitForTimeout(100);
    const festa = [await mes(page), await page.evaluate(() => document.activeElement?.id)];
    await esperaTitulo(page, PASSOS[1].fala.titulo);
    await page.locator('[data-guia="categorias.item"]').waitFor();
    await irAoAlvo(page, '[data-guia="categorias.item"]');
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
  await irAoAlvo(page, '[data-guia="mes.trocar"]');
  await page.waitForTimeout(300); // o isPointInFill do Chromium lê o path de antes por alguns quadros
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
  await irAoAlvo(page, '[data-guia="categorias.item"]');
  await page.waitForTimeout(300);
  const linha = await borda('[data-guia="categorias.item"]');
  await ctx.close();
  for (const b of [seta, linha]) {
    assert.deepEqual(b.fora, Array(4).fill("escuro:véu"), JSON.stringify(b));
    assert.deepEqual(b.dentro, Array(4).fill("claro:alvo"), JSON.stringify(b));
  }
});

// O que está no ponto central de cada seletor (o 1º visível): o próprio elemento, ou o véu.
const noCentro = (page, sels) => page.evaluate((sels) => sels.map((sel) => {
  const e = [...document.querySelectorAll(sel)].find((x) => x.getClientRects().length);
  if (!e) return `${sel}: ausente`;
  const r = e.getBoundingClientRect(), x = (r.left + r.right) / 2, y = (r.top + r.bottom) / 2;
  if (x < 0 || y < 0 || x > innerWidth || y > innerHeight) return `${sel}: fora da tela`;
  const t = document.elementFromPoint(x, y);
  return e.contains(t) ? "alvo" : t?.classList.contains("guia-veu") ? "véu" : `${sel}: ${t?.className || t?.tagName}`;
}), sels);
// O escuro (o path do véu) em cada centro: true = escuro, false = furo.
const escuro = (page, sels) => page.evaluate((sels) => sels.map((sel) => {
  const r = [...document.querySelectorAll(sel)].find((x) => x.getClientRects().length).getBoundingClientRect();
  const x = (r.left + r.right) / 2, y = (r.top + r.bottom) / 2;
  return x < 0 || y < 0 || x > innerWidth || y > innerHeight ? `${sel}: fora da tela` : document.querySelector(".guia-sombra path").isPointInFill(new DOMPoint(x, y));
}), sels);
const anelEm = (page, sel) => page.evaluate((sel) => {
  const a = document.querySelector(".guia-anel"), r = a.getBoundingClientRect(), e = document.querySelector(sel).getBoundingClientRect();
  return a.hidden ? null : [e.left - r.left, e.top - r.top, r.right - e.right, r.bottom - e.bottom].map(Math.round);
}, sel);
const SAIU = '[data-guia="resumo.saiu"]';

for (const [width, height] of [[1280, 800], [375, 812]]) {
  test(`véu ${width}×${height}: convite escuro e sem furo; no bloco o Saiu claro e sem toque, a seta escura e bloqueada; depois do Entendi furo e anel na seta; comemoração sem furo`, async () => {
    const { ctx, page, s } = await abrir({ width, height });
    const nav = width > 760 ? ".rail" : ".tabbar";
    await page.getByRole("button", { name: "Bora", exact: true }).waitFor();
    await page.waitForTimeout(200);
    const convite = [await noCentro(page, [".topbar .month-title", `${nav} [data-guia="nav.gastos"]`]), await escuro(page, [".topbar .month-title", '[data-guia="mes.trocar"]'])];
    await bora(page);
    await page.waitForTimeout(300);
    const alvo = '[data-guia="mes.trocar"]';
    const sels = [alvo, SAIU, `${nav} [data-guia="nav.gastos"]`, ".topbar .cmd-trigger, .topbar a.btn"];
    const bloco = [await noCentro(page, sels), await escuro(page, [alvo, SAIU, ".topbar .month-title"]), await anelEm(page, alvo)];
    // A seta no bloco: o clique de verdade no ponto não troca o mês nem grava.
    const mesAntes = await mes(page);
    await page.mouse.click(...await noPonto(page, alvo));
    await page.waitForTimeout(300);
    const naSeta = [await mes(page) === mesAntes, acoes(s)];
    await irAoAlvo(page, alvo);
    await page.waitForTimeout(300); // o isPointInFill do Chromium lê o path de antes por alguns quadros
    const passo = [await noCentro(page, sels), await escuro(page, [alvo, SAIU, ".topbar .month-title"]), await anelEm(page, alvo)];
    // O clique de verdade no ponto (o `locator.click` rola a página para achar a seta presa no
    // topo, o que tiraria o Saiu da tela; a pessoa não rola ao clicar).
    await page.mouse.click(...await noPonto(page, alvo));
    await page.locator(".guia-balao").getByText("Bora pro próximo").waitFor();
    const festa = [await noCentro(page, [alvo]), await escuro(page, [alvo, SAIU]), await anelEm(page, alvo)];
    await esperaTitulo(page, PASSOS[1].fala.titulo);
    await page.locator('[data-guia="categorias.item"]').waitFor();
    await irAoAlvo(page, '[data-guia="categorias.item"]');
    const passo2 = [await noCentro(page, ['[data-guia="categorias.item"]']), await anelEm(page, '[data-guia="categorias.item"]')];
    await ctx.close();
    console.log(`# véu ${width}×${height}:`, JSON.stringify({ convite, bloco, naSeta, passo, festa, passo2 }));
    assert.deepEqual(convite, [["véu", "véu"], [true, true]]);
    assert.deepEqual(bloco, [["véu", "véu", "véu", "véu"], [true, false, true], null]);
    assert.deepEqual(naSeta, [true, ["visto"]]);
    assert.deepEqual(passo.slice(0, 2), [["alvo", "véu", "véu", "véu"], [false, true, true]]);
    assert.ok(passo[2].every((d) => d >= 2 && d <= 8), `anel: ${passo[2]}`); // o anel envolve o alvo com folga de 2 a 8 px
    assert.deepEqual(festa, [["véu"], [true, true], null]);
    assert.equal(passo2[0][0], "alvo");
    assert.ok(passo2[1].every((d) => d >= 2 && d <= 8), `anel: ${passo2[1]}`);
  });
}
