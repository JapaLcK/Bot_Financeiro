/**
 * O aviso do caderno na página de pagamento da /assinar: com trial, o caderno do order bump é cobrado HOJE, e a
 * caixa diz isso antes da escolha (`.pp-bump-aviso`, logo depois da faixa). O número vem do `trial_days` do
 * backend. Sem trial (td=0) não há aviso nem promessa de dias grátis. O modal do Pix não passa o aviso
 * (precos_pix_extras).
 *
 * Controles (CLAUDE.md §3), medidos em 2026-11-02: sem o 3º argumento do `montar` no pagamento-pagina.js, "td=15
 * com 3 caixas" fica vermelho; sem a condição `td > 0`, "td=0" fica vermelho. Positivo: "td=15 com 3 caixas".
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { EXTRAS, PAGINA, abrirPagina as abrirP } from "./_assinar.mjs";

let browser;
before(async () => { browser = await chromium.launch(); });
after(async () => { await browser?.close(); });

const comTrial = (td, extras) => ({ extras, api: { "POST /billing/create-checkout": [200, { ...PAGINA(extras)[1], trial_days: td }] } });
const caixa = (page) => page.$eval("#pp-bump", (b) => ({
  avisos: [...b.querySelectorAll(".pp-bump-aviso")].map((p) => p.textContent),
  depoisDaFaixa: b.querySelector(".pp-bump-topo")?.nextElementSibling?.className ?? null,
  texto: b.innerText,
}));

test("td=15 com 3 caixas: o aviso do caderno, com o número do backend, logo depois da faixa", async () => {
  const { ctx, page } = await abrirP(browser, comTrial(15, EXTRAS));
  const m = await caixa(page);
  await ctx.close();
  assert.deepEqual(m.avisos, ["Cobrado hoje. Os 15 dias grátis valem só para o plano."]);
  assert.equal(m.depoisDaFaixa, "pp-bump-aviso");
});

test("1 caixa (a versão A): o aviso aparece igual", async () => {
  const { ctx, page } = await abrirP(browser, comTrial(7, [EXTRAS[0]]));
  const m = await caixa(page);
  await ctx.close();
  assert.deepEqual(m.avisos, ["Cobrado hoje. Os 7 dias grátis valem só para o plano."]);
  assert.equal(m.depoisDaFaixa, "pp-bump-aviso");
});

test("td=0: sem aviso e sem nenhum 'dias grátis' na caixa", async () => {
  const { ctx, page } = await abrirP(browser, comTrial(0, EXTRAS));
  const m = await caixa(page);
  await ctx.close();
  assert.deepEqual(m.avisos, []);
  assert.doesNotMatch(m.texto, /dias grátis/);
});
