/**
 * /assinar: os handlers fora do `acao` ("Usar outro e-mail", "Tentar de novo") respeitam a trava, e a volta
 * pelo bfcache para o S2 recarrega (a sessão pode ser outra depois do /login ou do Google).
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { abrir, tela, continuar, ME, EMBUTIDO } from "./_assinar.mjs";

let browser;
before(async () => { browser = await chromium.launch(); });
after(async () => { await browser?.close(); });

/** Resposta que só sai quando o teste chama `solta`. */
function segura() {
  let solta;
  const p = new Promise((ok) => { solta = ok; });
  return { resp: () => p, solta };
}

const visivel = (page, id) => page.locator(`#${id}`).isVisible();
const navs = (reqs) => reqs.filter((r) => r.navegacao && r.path === "/assinar.html").length;

// Com sessão de a@x (me guardado), o S2 espera o logout; sem sessão, espera o /auth/me do clique.
for (const [botao, sessao, espera, destino] of [
  ["#s2-senha", true, "/auth/logout", /\/login\?next=/],
  ["#s2-google", false, "/auth/me", /\/auth\/google\/start\?next=/],
]) {
  test(`${botao} em voo (${espera} pendente): "Usar outro e-mail" é ignorado, e só a 1ª ação navega`, async () => {
    const s = segura();
    const api = { "POST /auth/quiz/conta": [200, { estado: "tem_conta" }] };
    if (sessao) Object.assign(api, { "GET /auth/me": ME("a@x.com"), "POST /auth/logout": s.resp });
    else api["GET /auth/me"] = [[401, { detail: "x" }], s.resp];
    const { ctx, page } = await abrir(browser, { hash: "#e=b%40x.com&w=11987654321", api });
    await tela(page, "s1");
    await continuar(page);
    await tela(page, "s2");
    const pedido = page.waitForRequest((r) => new URL(r.url()).pathname === espera);
    await page.click(botao);
    await pedido;
    await page.click("#s2-outro");
    await page.waitForTimeout(150);
    assert.equal(await visivel(page, "s1"), false, "foi ao S1 com o handoff do S2 ainda em voo");
    assert.equal(await visivel(page, "s2"), true);
    s.solta(sessao ? [200, { ok: true }] : [401, { detail: "x" }]);
    await page.waitForURL(destino);
    await ctx.close();
  });
}

test('"Usar outro e-mail" fora de espera: S1 com o foco no e-mail', async () => {
  const { ctx, page } = await abrir(browser, { hash: "#e=b%40x.com&w=11987654321",
    api: { "POST /auth/quiz/conta": [200, { estado: "tem_conta" }] } });
  await tela(page, "s1");
  await continuar(page);
  await tela(page, "s2");
  await page.click("#s2-outro");
  await tela(page, "s1");
  assert.equal(await page.evaluate(() => document.activeElement.id), "email");
  await ctx.close();
});

test('Sair em voo no S3e: o "Tentar de novo" não reabre o checkout da conta que está saindo', async () => {
  const s = segura();
  const { ctx, page, posts } = await abrir(browser, { api: { "GET /auth/me": ME("a@x.com"), "POST /auth/logout": s.resp,
    "POST /billing/create-checkout": [[503, { detail: "x" }], EMBUTIDO] } });
  await tela(page, "s3-retry");
  const pedido = page.waitForRequest(/\/auth\/logout$/);
  await page.click("#sair");
  await pedido;
  await page.click("#s3-retry");
  await page.waitForTimeout(150);
  assert.equal(posts("/billing/create-checkout").length, 1, "o Tentar de novo reabriu o checkout durante o Sair");
  s.solta([200, { ok: true }]);
  await tela(page, "s1");
  assert.equal(posts("/billing/create-checkout").length, 1);
  await ctx.close();
});

const PAGESHOW = (persisted) => window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted }));

test("S2 restaurado do bfcache com a sessão já viva: recarrega e cai no S3 da conta que entrou", async () => {
  const { ctx, page, reqs, posts } = await abrir(browser, { hash: "#e=b%40x.com&w=11987654321", api: {
    "GET /auth/me": [[401, { detail: "x" }], ME("b@x.com")],
    "POST /auth/quiz/conta": [200, { estado: "tem_conta" }] } });
  await tela(page, "s1");
  await continuar(page);
  await tela(page, "s2");
  await page.evaluate(PAGESHOW, true).catch(() => {});  // o reload pode derrubar o contexto do evaluate
  await page.locator("#s4").waitFor({ state: "visible", timeout: 5000 });
  assert.equal(navs(reqs), 2);
  assert.equal(await page.locator("#conta-email").textContent(), "b•••@x.com");
  assert.equal(posts("/auth/logout").length, 0, "deslogou a conta que acabou de entrar");
  await ctx.close();
});

test("pageshow não persistido no S2: não recarrega", async () => {
  const { ctx, page, reqs } = await abrir(browser, { hash: "#e=b%40x.com&w=11987654321",
    api: { "POST /auth/quiz/conta": [200, { estado: "tem_conta" }] } });
  await tela(page, "s1");
  await continuar(page);
  await tela(page, "s2");
  await page.evaluate(PAGESHOW, false);
  await page.waitForTimeout(300);
  assert.equal(navs(reqs), 1);
  assert.equal(await visivel(page, "s2"), true);
  await ctx.close();
});

test("pix restaurado do bfcache: não recarrega, o embutido segue montado e a trava sai", async () => {
  const { ctx, page, reqs } = await abrir(browser, { api: { "GET /auth/me": ME("ana@x.com"),
    "GET /billing/plans-config": [200, { pix_annual_available: true }], "GET /continuar-compra": [204, {}] } });
  await page.locator("#stripe-checkout iframe").waitFor();
  await page.locator("#s4-pix").waitFor({ state: "visible" });
  const clicaPix = () => page.evaluate(() => document.getElementById("s4-pix").click());  // o click() esperaria a navegação
  const pedido = page.waitForRequest(/\/continuar-compra$/);
  await clicaPix();  // o 204 cancela a navegação: a página segue, no estado "pix"
  await pedido;
  await page.evaluate(PAGESHOW, true);
  await page.waitForTimeout(300);
  assert.equal(navs(reqs), 1, "recarregou e desmontou o embutido");
  assert.equal(await page.evaluate(() => window.__stripe.mount), 1);
  assert.equal(await page.locator("#stripe-checkout iframe").count(), 1);
  await clicaPix();  // a trava saiu: o 2º clique navega de novo
  await page.waitForTimeout(150);
  assert.equal(reqs.filter((r) => r.navegacao && r.path === "/continuar-compra").length, 2);
  await ctx.close();
});
