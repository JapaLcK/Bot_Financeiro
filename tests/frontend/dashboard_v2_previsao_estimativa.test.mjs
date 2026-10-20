/**
 * "Gastos do dia a dia" no /painel (webapp/src/dashboard/widgets/Previsao.tsx): a estimativa do servidor
 * (`estimativa_variavel`, `pior_dia.despesa_variavel`) aparece como veio; sem ela (null) a tela é a de sempre.
 *
 *   · fato "−R$ X/mês" no card do Resumo e na página; categorias (na ordem do servidor) só na página;
 *   · pior dia: a frase ganha a parte da estimativa, inclusive quando a queda é só por ela (sem causas);
 *   · dinheiro é o texto da API (moneyText); a única conta do cliente é a largura da barra.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { RESPOSTAS, abrirPainel, exigeArtefatoEmDia } from "./_painel.mjs";

let browser;
before(async () => { exigeArtefatoEmDia(); browser = await chromium.launch(); });
after(() => browser?.close());

const BASE = { espera: "#page-title", agora: "2026-10-06T15:00:00Z" };
async function abrir(previsao, { rota = "/previsao", corpo, width, espera } = {}) {
  const r = await abrirPainel(browser, { ...BASE, previsao, width, ...(espera && { espera }) });
  if (corpo) await r.ctx.route("**/api/v2/previsao**", (q) => q.fulfill({ json: corpo }));
  await r.ir(rota);
  await r.page.locator(".prev-hora").first().waitFor();
  return r;
}
const fatos = (page) => page.locator("#w-previsao .prev-facts > div").evaluateAll((ds) => ds.map((d) => d.textContent));
const lede = (page) => page.locator("#w-previsao p.w-lede").last().innerText();

test("Pro 90 com estimativa: fato mensal, categorias na ordem do servidor e a parte no pior dia", async () => {
  const { ctx, page } = await abrir("pro90_com_estimativa");
  const f = await fatos(page);
  const cats = await page.locator("#w-previsao-qualidade .cats li").evaluateAll((ls) => ls.map((l) => [l.querySelector(".cat-nome").textContent, l.querySelector(".cat-val").textContent, l.querySelector(".cat-track").getAttribute("aria-hidden"), l.querySelector(".cat-fill").style.transform]));
  const frase = await lede(page);
  await ctx.close();
  assert.ok(f.includes("Gastos do dia a dia−R$ 330,00/mês"), JSON.stringify(f));
  assert.deepEqual(cats, [
    ["Mercado", "−R$ 180,00", "true", "scaleX(1)"], ["Transporte", "−R$ 90,00", "true", "scaleX(0.5)"], ["Lazer", "−R$ 60,00", "true", "scaleX(0.333333)"],
  ]);
  assert.equal(frase, "Fica negativo em 19/10, desde 14/10: Aluguel −R$ 1.800,00, Gastos do dia a dia −R$ 66,00");
});

test("histórico curto, queda só pela estimativa (sem causas): a frase do pior dia ainda diz de onde veio", async () => {
  const { ctx, page } = await abrir("pro30_estimativa_curta");
  const frase = await lede(page);
  await ctx.close();
  assert.equal(frase, "Fica negativo em 05/11, desde 25/10: Gastos do dia a dia −R$ 132,00");
});

test("Plus: fato e lista, sem pior dia", async () => {
  const { ctx, page } = await abrir("plus30_com_estimativa");
  const f = await fatos(page);
  const cats = await page.locator("#w-previsao-qualidade .cats .cat-nome").allInnerTexts();
  await ctx.close();
  assert.ok(f.includes("Gastos do dia a dia−R$ 100,00/mês"), JSON.stringify(f));
  assert.deepEqual(cats, ["Mercado", "Transporte"]);
});

test("sem estimativa (null): nada de 'Gastos do dia a dia'; o motivo antigo segue", async () => {
  const corpo = structuredClone(RESPOSTAS.previsao.pro90);
  corpo.motivos.push({ codigo: "gastos_variaveis_nao_estimados", direcao_do_erro: "so_melhora" });
  const { ctx, page } = await abrir("pro90", { corpo });
  const t = await page.locator("main").innerText();
  const [cats, h3] = [await page.locator("#w-previsao-qualidade .cats").count(), await page.locator("#w-previsao-qualidade .prev-h").allInnerTexts()];
  await ctx.close();
  assert.doesNotMatch(t, /Gastos do dia a dia/);
  assert.match(t, /gastos do dia a dia fora da conta/);
  assert.equal(cats, 0);
  assert.ok(!h3.includes("Gastos do dia a dia"));
});

test("estimativa com valor malformado: o fato diz 'indisponível', a página não quebra", async () => {
  const corpo = structuredClone(RESPOSTAS.previsao.pro90_com_estimativa);
  corpo.estimativa_variavel.despesa_mensal = "abc";
  corpo.estimativa_variavel.categorias[0].despesa_mensal = "abc";
  const { ctx, page, erros } = await abrir("pro90_com_estimativa", { corpo });
  const f = await fatos(page);
  const r = await page.evaluate(() => ({
    x: document.documentElement.scrollWidth <= document.documentElement.clientWidth,
    escalas: [...document.querySelectorAll("#w-previsao-qualidade .cat-fill")].map((e) => e.style.transform),
  }));
  await ctx.close();
  assert.ok(f.includes("Gastos do dia a diaindisponível"), JSON.stringify(f));
  assert.deepEqual(erros, []);
  assert.ok(r.x, "rolagem lateral com valor malformado");
  const n = r.escalas.map((t) => Number(t.match(/scaleX\(([^)]*)\)/)?.[1]));
  assert.ok(n.length === 3 && n.every((v) => v >= 0 && v <= 1), JSON.stringify(r.escalas));
});

for (const width of [1280, 390]) {
  test(`${width}px: card do Resumo e página sem rolagem lateral e sem sobreposição`, async () => {
    for (const [rota, espera] of [["/previsao", "#page-title"], ["/", "#board-profile"]]) {
      const { ctx, page } = await abrir("pro90_com_estimativa", { rota, width, espera });
      const r = await page.evaluate(() => {
        const alvo = document.querySelector("#w-previsao, #w-hero");
        const caixa = (e) => e.getBoundingClientRect();
        const dentro = [...alvo.querySelectorAll(".prev-facts > div, .cats li")].every((e) => { const c = caixa(e), a = caixa(alvo); return c.left >= a.left - 1 && c.right <= a.right + 1; });
        const sobre = [...alvo.querySelectorAll(".cat-est")].some((li) => { const n = caixa(li.querySelector(".cat-name")), v = caixa(li.querySelector(".cat-val")); return n.right > v.left + 0.5; });
        return { x: document.documentElement.scrollWidth <= document.documentElement.clientWidth, dentro, sobre, fato: alvo.querySelector(".prev-facts").textContent.includes("Gastos do dia a dia") };
      });
      await ctx.close();
      assert.deepEqual(r, { x: true, dentro: true, sobre: false, fato: true }, `${rota} ${width}`);
    }
  });
}

test("Resumo: as categorias não entram no card (só o fato mensal)", async () => {
  const { ctx, page } = await abrir("pro90_com_estimativa", { rota: "/", espera: "#board-profile" });
  const [cats, fato] = [await page.locator("#w-hero .cat-est").count(), await page.locator("#w-hero .prev-facts").innerText()];
  await ctx.close();
  assert.equal(cats, 0);
  assert.match(fato, /Gastos do dia a dia/);
});

test("320px: valor enorme no fato não vaza da célula do dd", async () => {
  const corpo = structuredClone(RESPOSTAS.previsao.pro90_com_estimativa);
  corpo.estimativa_variavel.despesa_mensal = "999999999.99";
  const { ctx, page } = await abrir("pro90_com_estimativa", { corpo, width: 320 });
  const r = await page.evaluate(() => [...document.querySelectorAll("#w-previsao .prev-facts dd")].map((d) => [d.scrollWidth, d.clientWidth]));
  await ctx.close();
  assert.ok(r.length && r.every(([s, c]) => s <= c), JSON.stringify(r));
});
