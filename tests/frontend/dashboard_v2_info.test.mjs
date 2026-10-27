/**
 * O ⓘ do painel (webapp/src/dashboard/parts/Info.tsx): o texto que explica sai do card e abre num diálogo.
 * Em 1440 e 390, duas vezes: nativo, e fallback do Safari 14 (`showModal` apagado antes da página carregar).
 *
 *   · clique abre (aria-expanded, diálogo com o nome certo); Esc fecha e o foco volta ao ⓘ; clique no fundo fecha;
 *   · no card "Saldo previsto" do Resumo o diálogo fica por cima de tudo (elementFromPoint no centro cai nele: o portal
 *     tira o `position: fixed` do fallback de dentro do clip-path da célula do grid) e cabe na tela, sem rolagem lateral;
 *   · em /previsao a explicação não está na tela fechada e está no diálogo;
 *   · Esc no ⓘ do card de compras fecha só o diálogo (o evento de portal sobe pela árvore do React).
 * Controles negativos (feitos à mão, ver o relato do PR): sem o `createPortal` o elementFromPoint do fallback no Resumo
 * falha; sem o `stopPropagation` o caso do Esc nas compras falha.
 *
 * Rodar:  node --test tests/frontend/dashboard_v2_info.test.mjs   (mudou webapp/src, rode `npm --prefix webapp run build`)
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { RESPOSTAS, abrirInfo, abrirPainel, exigeArtefatoEmDia } from "./_painel.mjs";

let browser;
before(async () => { exigeArtefatoEmDia(); browser = await chromium.launch(); });
after(() => browser?.close());

const SEM_SHOWMODAL = () => { delete HTMLDialogElement.prototype.showModal; }; // o Safari < 15.4: o painel cai no fallback
const abrir = async ({ width, fallback, ...opts }) => {
  const r = await abrirPainel(browser, { width, agora: "2026-10-06T15:00:00Z", ...opts });
  if (fallback) await r.ctx.addInitScript(SEM_SHOWMODAL);
  return r;
};
const semRolagem = (page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth);
const caixa = (dialogo) => dialogo.evaluate((d) => { const r = d.getBoundingClientRect(); return { l: r.left, t: r.top, r: r.right, b: r.bottom, vw: innerWidth, vh: innerHeight }; });

for (const width of [1440, 390]) for (const fallback of [false, true]) {
  const nome = `${width}px, ${fallback ? "fallback (sem showModal)" : "nativo"}`;

  test(`${nome}: Resumo, card Saldo previsto: abre, fica por cima, cabe na tela; Esc fecha e devolve o foco; o fundo fecha`, async () => {
    const { ctx, page, erros, ir } = await abrir({ width, fallback, espera: "#w-hero .hero-value" });
    await ir("/");
    const botao = page.locator("#w-hero .w-info");
    assert.equal(await botao.getAttribute("aria-expanded"), "false");
    assert.equal(await page.locator("dialog.lanc-form").count(), 0, "o conteúdo só existe no DOM com o ⓘ aberto");
    const dialogo = await abrirInfo(page, "#w-hero");
    assert.equal(await botao.getAttribute("aria-expanded"), "true");
    assert.match(await dialogo.innerText(), /Premissas/);
    const centro = await dialogo.evaluate((d) => { const r = d.getBoundingClientRect(); return document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)?.closest("dialog") === d; });
    const c = await caixa(dialogo);
    assert.ok(centro, "elementFromPoint no centro do diálogo tem de cair nele");
    assert.ok(c.l >= 0 && c.t >= 0 && c.r <= c.vw && c.b <= c.vh, JSON.stringify(c));
    assert.ok(await semRolagem(page));

    await page.keyboard.press("Escape");
    await page.locator("dialog.lanc-form").waitFor({ state: "detached" });
    assert.equal(await botao.getAttribute("aria-expanded"), "false");
    assert.equal(await page.evaluate(() => document.activeElement?.classList.contains("w-info")), true, "o foco volta ao ⓘ");

    await botao.click();
    await page.getByRole("dialog").waitFor();
    await page.waitForTimeout(450); // o clique no fundo só vale passada a janela do duplo clique (dialog.ts)
    await page.mouse.click(4, 4);
    await page.locator("dialog.lanc-form").waitFor({ state: "detached" });
    assert.equal(await page.evaluate(() => document.activeElement?.classList.contains("w-info")), true);
    await ctx.close();
    assert.deepEqual(erros, []);
  });

  test(`${nome}: /previsao: a explicação não está na tela fechada e está no diálogo; sem rolagem lateral`, async () => {
    const { ctx, page, erros, ir } = await abrir({ width, fallback, espera: "#page-title", previsao: "real_diario" });
    await ir("/previsao");
    await page.locator("#w-previsao .prev-hora").waitFor();
    const fechada = await page.locator("main").innerText();
    const dialogo = await abrirInfo(page, "#w-previsao");
    const aberta = await dialogo.innerText();
    const c = await caixa(dialogo);
    const lateral = await semRolagem(page);
    await ctx.close();
    for (const t of ["Premissas", "Por que é condicional", "Saldo de partida:"]) {
      assert.ok(!fechada.includes(t), `"${t}" ainda está na tela`);
      assert.ok(aberta.includes(t), `"${t}" falta no diálogo`);
    }
    assert.ok(c.l >= 0 && c.t >= 0 && c.r <= c.vw && c.b <= c.vh, JSON.stringify(c));
    assert.ok(lateral);
    assert.deepEqual(erros, []);
  });

  test(`${nome}: Esc no ⓘ do card de compras fecha só o diálogo, não o card`, async () => {
    const { ctx, page, ir } = await abrir({ width, fallback, gastos: "padrao", lancamentos: "dia_7", espera: "#w-cat-detalhe .detail-facts" });
    await ir("/gastos");
    await page.locator("#w-calendario .cal-day", { hasText: /^7$/ }).click();
    await page.locator("#gastos-compras .lanc-linha").first().waitFor();
    await abrirInfo(page, "#w-compras");
    await page.keyboard.press("Escape");
    await page.locator("dialog.lanc-form").waitFor({ state: "detached" });
    const cardAberto = await page.locator("#gastos-compras .lanc-linha").first().isVisible();
    await ctx.close();
    assert.ok(cardAberto, "o card de compras fechou junto com o diálogo");
  });
}

test("Resumo, card Próximos dias: com item 'estimado' na lista há ⓘ (o estado sem estimado não é alcançável: a lista vem do protótipo)", async () => {
  const { ctx, page, ir } = await abrir({ width: 1440, espera: "#w-hero .hero-value", demo: true });
  await ir("/");
  await page.locator("#w-compromissos .bill").first().waitFor();
  const r = await page.evaluate(() => ({
    estimados: [...document.querySelectorAll("#w-compromissos .bill-status")].filter((e) => e.textContent.trim() === "estimado").length,
    info: document.querySelectorAll("#w-compromissos .w-info").length }));
  await ctx.close();
  assert.ok(r.estimados > 0 && r.info === 1, JSON.stringify(r));
});

test("RESPOSTAS.lancamentos.dia_7 tem linha marcada (o ⓘ das compras só existe com ela)", () => {
  assert.ok(RESPOSTAS.lancamentos.dia_7.itens.some((i) => i.interno || i.motivos?.length));
});
