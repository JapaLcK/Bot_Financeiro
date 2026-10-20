/**
 * Variação de "Para onde vai" no /painel (GastosCategorias): o texto decimal do servidor passa por `decimal`
 * (lib/format.js) e por Number.isFinite antes da conta. Malformado ou infinito = sem comparação ("—" na linha,
 * sem frase no total), nunca "NaN%" nem "Infinity%" na tela ou no nome acessível.
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

async function ler(mudar) {
  const { ctx, page, ir, erros } = await abrirPainel(browser, { espera: "#w-categorias" });
  const g = structuredClone(RESPOSTAS.gastos.padrao);
  mudar(g);
  await ctx.route((u) => u.pathname === "/api/v2/gastos", (r) => r.fulfill({ json: g }));
  await ir("/gastos");
  await page.locator("#w-categorias .cat").first().waitFor();
  const r = await page.evaluate(() => {
    const w = document.querySelector("#w-categorias");
    return {
      lede: w.querySelector(".w-lede").textContent,
      delta: [...w.querySelectorAll(".cat-delta")].map((x) => x.textContent),
      fala: w.querySelector(".cat").getAttribute("aria-label"),
      tudo: w.textContent + [...w.querySelectorAll("[aria-label]")].map((x) => x.getAttribute("aria-label")).join(" "),
    };
  });
  await ctx.close();
  return { ...r, erros };
}

for (const [caso, mudar, frase] of [
  ["valor até o corte malformado", (g) => { g.categorias[0].valor_ate_corte = "abc"; }, false],
  ["valor até o corte vazio (antes virava 0: '100% a menos')", (g) => { g.categorias[0].valor_ate_corte = ""; }, false],
  ["valor até o corte infinito", (g) => { g.categorias[0].valor_ate_corte = "1e400"; }, false],
  ["anterior infinito", (g) => { g.categorias[0].anterior_ate_corte = "1e400"; }, true],
  // finitos um a um, a conta estoura: a razão da linha e a soma do total
  ["razão que estoura o double", (g) => { g.categorias[0].valor_ate_corte = "1e308"; g.categorias[0].anterior_ate_corte = "1e-300"; g.anterior_total_ate_corte = "1e-300"; }, false],
  ["razão finita cujo percentual estoura", (g) => { g.categorias[0].valor_ate_corte = "1e308"; g.categorias[0].anterior_ate_corte = "1"; g.anterior_total_ate_corte = "1"; }, false],
  ["soma que estoura o double", (g) => { g.categorias[0].valor_ate_corte = "1e308"; g.categorias[1].valor_ate_corte = "1e308"; g.categorias[0].anterior_ate_corte = "1e-300"; }, false],
]) {
  test(`${caso}: a linha fica sem comparação, sem NaN/Infinity na tela nem no nome acessível`, async () => {
    const r = await ler(mudar);
    assert.deepEqual(r.erros, []);
    assert.doesNotMatch(r.tudo, /NaN|Infinity/);
    assert.deepEqual(r.delta, ["—", "—"]);
    assert.equal(r.fala, "alimentação: R$ 300,00, sem comparação. Filtrar lançamentos.");
    // o total soma as linhas: uma linha malformada tira a frase; o anterior de uma linha não entra nele
    assert.equal(r.lede, frase ? "R$ 440,00 gastos no mês · Até o dia 14: 26% a mais que em setembro no mesmo dia" : "R$ 440,00 gastos no mês");
  });
}

test("total anterior malformado: sem frase no total, as linhas seguem comparando", async () => {
  const r = await ler((g) => { g.anterior_total_ate_corte = "abc"; });
  assert.deepEqual([r.lede, r.delta], ["R$ 440,00 gastos no mês", ["20%", "—"]]);
});

test("valores válidos: a variação de sempre (controle)", async () => {
  const r = await ler(() => {});
  assert.deepEqual([r.lede, r.delta, r.fala], ["R$ 440,00 gastos no mês · Até o dia 14: 26% a mais que em setembro no mesmo dia", ["20%", "—"],
    "alimentação: R$ 300,00, 20% a mais que no mesmo dia do mês anterior. Filtrar lançamentos."]);
});
