/**
 * Página de pagamento própria: Apple Pay/Google Pay pelo Express Checkout Element (frontend/pagamento-pagina.js,
 * PR 6). O que só o aparelho prova (o botão aparecer de verdade no iPhone/Android, a folha com o total certo)
 * fica fora: aqui o Stripe.js é o falso de _assinar.mjs, cujo botão #ex-btn entrega o `confirm` direto.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { abrir, tela, comPagina, HOSPEDADO, abrirPagina as abrirP, caixas, ate } from "./_assinar.mjs";
import { chromium } from "playwright";

let browser;
before(async () => { browser = await chromium.launch(); });
after(async () => { await browser?.close(); });

const BUMP = "/billing/checkout/bump";
const CONFIRM = "/__stripe/confirm";
const abrirPagina = (opts) => abrirP(browser, opts);
const pausa = (ms) => new Promise((ok) => setTimeout(ok, ms));
const caixa = (page, sel) => page.locator(sel).evaluate((e) => { const r = e.getBoundingClientRect(); return { top: r.top, h: r.height }; });
/** Clique de mouse de verdade nas coordenadas do botão: o que o `inert` bloqueia é o hit-test, não o .click(). */
async function clicaCarteira(page) {
  const b = await page.locator("#ex-btn").boundingBox();
  await page.mouse.click(b.x + b.width / 2, b.y + b.height / 2);
}
/** Botão visível (o availablepaymentmethodschange chegou) e destravado (ações e total, nada em voo). */
const pronta = (page) => page.waitForFunction(() => {
  const e = document.getElementById("pp-express");
  return !e.inert && !e.classList.contains("pp-espera");
});
function segura() {
  let solta;
  const p = new Promise((ok) => { solta = ok; });
  return { resp: () => p, solta: (r) => solta(r) };
}

test("Express montado no #pp-express, acima do Payment Element, com os botões de checkout e 48 px", async () => {
  const { ctx, page } = await abrirPagina();
  await page.waitForFunction(() => !document.getElementById("pp-express").classList.contains("pp-espera"));
  const reg = await page.evaluate(() => window.__stripe);
  assert.deepEqual(reg.ex, { buttonType: { applePay: "check-out", googlePay: "checkout" }, buttonHeight: 48 });
  assert.equal(reg.exMount, 1);
  assert.equal(await page.locator("#pp-express #ex-btn").count(), 1);
  const ex = await caixa(page, "#pp-express"), pe = await caixa(page, "#pagamento");
  assert.ok(ex.h >= 48 && ex.top < pe.top, JSON.stringify({ ex, pe }));
  await ctx.close();
});

for (const [nome, carteiras] of [["antes do availablepaymentmethodschange", "nunca"], ["sem nenhum botão (paymentMethods vazio)", null]]) {
  test(`${nome}: o Express fica sem altura e o Payment Element encosta no lugar dele (sem buraco)`, async () => {
    const { ctx, page } = await abrirPagina({ sdk: { carteiras } });
    if (carteiras === null) await page.waitForFunction(() => window.__stripe.pmc === true);
    await pausa(100);
    const ex = await caixa(page, "#pp-express"), pe = await caixa(page, "#pagamento");
    assert.equal(ex.h, 0);
    assert.equal(pe.top, ex.top, "buraco entre o topo do Express e o Payment Element");
    assert.equal(await page.locator("#ex-btn").isVisible(), false);
    await ctx.close();
  });
}

test("confirm do Express → actions.confirm com o expressCheckoutConfirmEvent, sem /bump antes, e trava o Pagar", async () => {
  const { ctx, page, posts } = await abrirPagina({ api: { [`POST ${CONFIRM}`]: null } });
  await pronta(page);
  await clicaCarteira(page);
  await ate(() => posts(CONFIRM).length > 0);
  assert.deepEqual(posts(CONFIRM).map((r) => r.body), [{ ev: true }]);
  assert.equal(posts(BUMP).length, 0);
  assert.equal(await page.locator("#pp-pagar").isDisabled(), true);
  await ctx.close();
});

test("com /bump em voo o Express fica inert e o clique não abre a folha; ao voltar, abre e confirma", async () => {
  const t = segura();
  const { ctx, page, posts } = await abrirPagina({ api: { [`POST ${BUMP}`]: t.resp } });
  await pronta(page);
  await caixas(page).nth(0).click();
  await ate(() => posts(BUMP).length > 0);
  assert.equal(await page.locator("#pp-express").evaluate((e) => e.inert), true);
  await clicaCarteira(page);
  await page.locator("#ex-btn").evaluate((b) => b.focus());
  await page.keyboard.press("Enter");
  await pausa(150);
  assert.equal(await page.evaluate(() => window.__stripe.folha || 0), 0, "a folha abriu com o /bump em voo");
  assert.equal(posts(CONFIRM).length, 0);
  // Controle positivo: o mesmo clique, com o /bump de volta, confirma.
  t.solta([200, { ok: true }]);
  await pronta(page);
  await clicaCarteira(page);
  await ate(() => posts(CONFIRM).length > 0);
  await ctx.close();
});

test("confirm que chega com /bump em voo (folha aberta antes do clique na caixa): ignorado, sem cobrar", async () => {
  const { ctx, page, posts } = await abrirPagina({ api: { [`POST ${BUMP}`]: null } });
  await pronta(page);
  await caixas(page).nth(0).click();
  await ate(() => posts(BUMP).length > 0);
  await page.evaluate(() => window.__exConfirma());
  await pausa(150);
  assert.equal(posts(CONFIRM).length, 0);
  await ctx.close();
});

test("confirm do Express com erro: a mensagem do Stripe, e Pagar e Express destravam", async () => {
  const { ctx, page } = await abrirPagina({ sdk: { confirma: { type: "error", error: { message: "Pagamento recusado pela carteira." } } } });
  await pronta(page);
  await clicaCarteira(page);
  await page.locator("#pp-erro.show").waitFor();
  assert.equal(await page.textContent("#pp-erro"), "Pagamento recusado pela carteira.");
  await page.waitForFunction(() => !document.getElementById("pp-pagar").disabled && !document.getElementById("pp-express").inert);
  await ctx.close();
});

test("confirm do Express que lança: mensagem padrão, nunca a exceção", async () => {
  const { ctx, page } = await abrirPagina({ sdk: { confirma: "lanca" } });
  await pronta(page);
  await clicaCarteira(page);
  await page.locator("#pp-erro.show").waitFor();
  assert.equal(await page.textContent("#pp-erro"), "O pagamento não foi concluído.");
  await ctx.close();
});

test("confirm do Express com a sessão expirada (status.type): tela de pagamento expirado", async () => {
  const { ctx, page } = await abrirPagina({ sdk: { expiraNoConfirm: true, confirma: { type: "error", error: { message: "x" } } } });
  await pronta(page);
  await clicaCarteira(page);
  await tela(page, "s3");
  assert.match(await page.textContent("#s3-erro"), /tempo para pagar acabou/);
  await ctx.close();
});

test("Sair desmonta o Express: destroy chamado e o #pp-express vazio", async () => {
  const { ctx, page } = await abrirPagina();
  await page.click("#sair");
  await tela(page, "s1");
  assert.equal(await page.evaluate(() => window.__stripe.exDestroy), 1);
  assert.equal(await page.locator("#pp-express").evaluate((e) => e.childElementCount), 0);
  await ctx.close();
});

test("sem o pagamento-caixas.js (desenho das caixas e do resumo): a página própria cai no plano B", async () => {
  const { ctx, page } = await abrir(browser, { api: { ...comPagina(), "GET /pagamento-caixas.js": [404, {}] } });
  await page.waitForURL(HOSPEDADO);
  await ctx.close();
});
