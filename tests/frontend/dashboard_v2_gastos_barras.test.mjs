/**
 * Barras de Gastos no /painel (GastosCategorias e GastosDetalhe): o comprimento vem de `medida` (lib/format.js),
 * a mesma guarda da Previsão (#1205). Texto malformado ou negativo do servidor vira barra vazia; as outras barras
 * da lista seguem proporcionais. Antes, um "abc" punha scaleX(NaN) — o navegador descarta e TODAS as barras da
 * lista saíam cheias — e um negativo espelhava a barra para fora da trilha.
 *
 * Rodar:  npm run test:frontend   (mudou webapp/src, rode `npm --prefix webapp run build`)
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { RESPOSTAS, abrirPainel, exigeArtefatoEmDia } from "./_painel.mjs";

let browser;
before(async () => { exigeArtefatoEmDia(); browser = await chromium.launch(); });
after(() => browser?.close());

async function medir(width, gastos, categoria) {
  const { ctx, page, ir, erros } = await abrirPainel(browser, { espera: "#w-categorias", width });
  await ctx.route((u) => u.pathname === "/api/v2/gastos", (r) => r.fulfill({ json: gastos }));
  await ctx.route((u) => u.pathname === "/api/v2/gastos/categoria", (r) => r.fulfill({ json: categoria }));
  await ir("/gastos");
  await page.locator("#w-cat-detalhe .places li").first().waitFor();
  await page.locator("#w-categorias .cat").first().waitFor();
  const r = await page.evaluate(() => {
    const e = document.documentElement;
    const escalas = (sel) => [...document.querySelectorAll(sel)].map((x) => x.style.transform);
    return {
      x: e.scrollWidth <= e.clientWidth,
      cats: escalas("#w-categorias .cat-fill"), marcas: [...document.querySelectorAll("#w-categorias .cat-ghost")].map((x) => x.style.left), lojas: escalas("#w-cat-detalhe .place-bar i"), meses: escalas("#w-cat-detalhe .months-bar i"),
      // a barra não sai da trilha (a .cat-track não corta o que passa dela)
      fora: [...document.querySelectorAll("#w-categorias .cat-fill")].filter((f) => { const a = f.getBoundingClientRect(), t = f.parentElement.getBoundingClientRect(); return a.left < t.left - 0.5 || a.right > t.right + 0.5; }).length,
    };
  });
  await ctx.close();
  return { ...r, erros };
}

for (const width of [1280, 390]) {
  test(`${width}px: valor malformado vira barra vazia, as outras da lista seguem proporcionais, sem rolagem lateral`, async () => {
    const g = structuredClone(RESPOSTAS.gastos.padrao);
    g.categorias[0].valor = "abc";
    const c = structuredClone(RESPOSTAS.gastos_categoria.padrao);
    c.estabelecimentos[0].valor = "abc";
    c.serie[0].valor = "abc";
    const r = await medir(width, g, c);
    assert.deepEqual(r.erros, []);
    assert.ok(r.x, "rolagem lateral com valor malformado");
    // maior medida válida de cada lista: 250 (anterior de alimentação), 100 (Mercado) e 300 (série de outubro)
    assert.deepEqual([r.cats, r.marcas, r.lojas, r.meses], [["scaleX(0)", "scaleX(0.56)"], ["100%"], ["scaleX(0)", "scaleX(1)"], ["scaleY(0)", "scaleY(0.833333)", "scaleY(1)"]]);
  });

  test(`${width}px: valor negativo (estorno) vira barra vazia e nenhuma barra sai da trilha`, async () => {
    const g = structuredClone(RESPOSTAS.gastos.padrao);
    g.categorias.push({ ...g.categorias[1], chave: "lazer", nome: "lazer", valor: "-50.00", anterior_ate_corte: "-80.00" });
    const c = structuredClone(RESPOSTAS.gastos_categoria.padrao);
    c.estabelecimentos.push({ nome: "Estorno", valor: "-900.00", quantidade: 1 });
    c.serie[0].valor = "-80.00";
    const r = await medir(width, g, c);
    assert.deepEqual(r.erros, []);
    assert.ok(r.x, "rolagem lateral com valor negativo");
    assert.equal(r.fora, 0);
    assert.deepEqual([r.cats, r.marcas, r.lojas, r.meses], [["scaleX(1)", "scaleX(0.466667)", "scaleX(0)"], ["83.3333%"], ["scaleX(1)", "scaleX(0.5)", "scaleX(0)"], ["scaleY(0)", "scaleY(0.833333)", "scaleY(1)"]]);
  });
}

test("valores válidos: as proporções de sempre (controle)", async () => {
  const r = await medir(1280, RESPOSTAS.gastos.padrao, RESPOSTAS.gastos_categoria.padrao);
  assert.deepEqual([r.x, r.fora, r.cats, r.marcas, r.lojas, r.meses], [true, 0, ["scaleX(1)", "scaleX(0.466667)"], ["83.3333%"], ["scaleX(1)", "scaleX(0.5)"], ["scaleY(0.6)", "scaleY(0.833333)", "scaleY(1)"]]);
});
