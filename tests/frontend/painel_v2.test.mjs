/**
 * O dashboard v2 nas DUAS páginas que carregam o mesmo artefato (frontend/dashboard-app.*):
 * o /painel (frontend/painel.html, servido da raiz como em produção) e o protótipo
 * dashboard-v2/index.html (servido da raiz do repositório, como pelo http.server).
 *
 *  · nenhuma requisição falha: o bundle resolve o ícone do Piggy pelo endereço do próprio
 *    script, e um caminho relativo à página quebraria uma das duas;
 *  · a etiqueta "Dados de demonstração" aparece UMA vez, na tela, em toda rota de 320 a
 *    1440, sem rolagem lateral (no celular a barra de cima não tinha lugar para ela, e o
 *    Resumo a repetia na linha da data);
 *  · "Painel antigo" em Ferramentas leva ao /app.
 *
 * Rodar:  npm run test:frontend   (abre o artefato commitado: mudou webapp/src, rode
 *         `npm --prefix webapp run build`)
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { PAINEL, PROTOTIPO, RAIZ, exigeArtefatoEmDia, servir } from "./_painel.mjs";

const ROTAS = ["/", "/previsao", "/gastos", "/assinaturas", "/simulador", "/metas", "/patrimonio", "/lancamentos", "/ferramentas", "/piggy"];
const PAGINAS = [["/painel", PAINEL, undefined], ["protótipo", PROTOTIPO, RAIZ]];

let browser;
before(async () => { exigeArtefatoEmDia(); browser = await chromium.launch(); });
after(() => browser?.close());

async function abrir(url, raiz, width) {
  const ctx = await browser.newContext({ viewport: { width, height: width > 760 ? 900 : 844 }, reducedMotion: "reduce" });
  await servir(ctx, raiz);
  await ctx.addInitScript(() => localStorage.setItem("pigbank.dashboard.profile.v1", '"padrao"'));
  const page = await ctx.newPage();
  const falhas = [];
  page.on("requestfailed", (r) => falhas.push(`${r.url()} (${r.failure()?.errorText})`));
  page.on("response", (r) => { if (r.status() >= 400) falhas.push(`${r.url()} ${r.status()}`); });
  page.on("pageerror", (e) => falhas.push(`pageerror: ${e.message}`));
  await page.goto(`${url}#/`);
  await page.locator("#page-title").waitFor();
  return { ctx, page, falhas };
}

const ir = async (page, rota) => {
  await page.evaluate((h) => { location.hash = h; }, rota);
  await page.waitForFunction((h) => document.querySelector(".page")?.dataset.page === h, rota);
};

// A etiqueta: todo nó de texto que CONTÉM "Dados de demonstração" e tem caixa não vazia e
// visível em algum lugar da página (rolado ou não; `display: none` dá caixa vazia). Tem de
// haver exatamente um, e ele inteiro no viewport: duas cópias na mesma tela é repetição.
const etiquetas = (page) => page.evaluate(() => {
  const achadas = [];
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n; (n = w.nextNode());) {
    if (!n.textContent.includes("Dados de demonstração") || !n.parentElement.checkVisibility({ visibilityProperty: true })) continue;
    const r = document.createRange();
    r.selectNodeContents(n);
    const b = r.getBoundingClientRect();
    if (b.width > 0 && b.height > 0) {
      achadas.push({ onde: n.parentElement.className, naTela: b.top >= 0 && b.left >= 0 && b.bottom <= innerHeight && b.right <= innerWidth });
    }
  }
  return achadas;
});

for (const [nome, url, raiz] of PAGINAS) {
  test(`${nome}: etiqueta de demonstração em toda rota de 320 a 1440, sem rolagem lateral nem requisição com falha`, async () => {
    const problemas = [];
    for (const width of [320, 390, 1440]) {
      const { ctx, page, falhas } = await abrir(url, raiz, width);
      for (const rota of ROTAS) {
        await ir(page, rota);
        const achadas = await etiquetas(page);
        if (achadas.length !== 1 || !achadas[0].naTela) problemas.push(`${width} ${rota}: etiqueta ${JSON.stringify(achadas)} (esperada uma, na tela)`);
        const lateral = await page.evaluate(() => document.scrollingElement.scrollWidth - document.scrollingElement.clientWidth);
        if (lateral > 0) problemas.push(`${width} ${rota}: rola ${lateral}px para o lado`);
      }
      await page.waitForLoadState("networkidle");
      problemas.push(...falhas.map((f) => `${width}: ${f}`));
      await ctx.close();
    }
    assert.deepEqual(problemas, []);
  });

  test(`${nome}: "Painel antigo" em Ferramentas leva ao /app`, async () => {
    const { ctx, page } = await abrir(url, raiz, 1440);
    await ir(page, "/ferramentas");
    const href = await page.locator(".tools a.tool", { hasText: "Painel antigo" }).getAttribute("href");
    await ctx.close();
    assert.equal(href, "/app");
  });
}
