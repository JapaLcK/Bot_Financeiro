/**
 * A Casa é item próprio do menu (decisão do dono, 2026-11-02), rota própria `#/casa` fora da Conta (webapp/src/dashboard/router.ts):
 * menu lateral, Menu do celular, ⌘K, sem seletor de mês (NO_MONTH) e fora do protótipo (DEMO, ATIVAS). Quem não tem família também vê o item.
 * Em cada teste, o comentário de cima diz qual mutação o deixa vermelho.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { navegador } from "./_guia.mjs";
import { PROTOTIPO, RAIZ, servir } from "./_painel.mjs";
import { comCasa } from "./_familia.mjs";

const browser = navegador();

// mutação: tirar a rota `/casa` de ROUTES (router.ts): sem item; ou o `path !== "/casa"` do `ATIVAS`: a Casa aparece no protótipo
test("menu lateral: 'Casa' entre Lançamentos e Ferramentas, fora da Conta; a tela abre, marca aria-current e não tem seletor de mês", async () => {
  await comCasa({}, async (t) => {
    const itens = await t.page.locator(".rail-list a").evaluateAll((a) => a.map((e) => [e.textContent.trim(), e.getAttribute("href")]));
    const i = itens.findIndex(([n]) => n === "Casa");
    assert.deepEqual(itens.slice(i - 1, i + 2), [["Lançamentos", "#/lancamentos"], ["Casa", "#/casa"], ["Ferramentas", "#/ferramentas"]]);
    assert.equal(await t.page.locator(".rail-conta a", { hasText: "Casa" }).count(), 0, "a Casa não é da Conta");
    assert.equal(await t.page.locator('.rail-list a[aria-current="page"]').innerText(), "Casa");
    assert.equal(await t.page.locator("#page-title").innerText(), "Casa");
    assert.equal(await t.page.locator(".month-switch").count(), 0, "NO_MONTH: sem seletor de mês");
  });
});

// mutação: `RAIL` sem a Casa, ou a Casa só no desktop: o Menu do celular não a lista
test("celular: o Menu lista a Casa e leva a ela; ⌘K 'Ir para' também", async () => {
  await comCasa({ width: 390, height: 844, casa: null }, async (t) => { // sem família: o item existe do mesmo jeito
    await t.page.locator(".tabbar").getByRole("button", { name: "Menu" }).click();
    await t.page.locator(".menu-telas").getByRole("link", { name: "Casa" }).click();
    assert.equal(await t.page.evaluate(() => location.hash), "#/casa");
  });
  await comCasa({ casa: null }, async (t) => {
    await t.page.goto(t.page.url().replace("#/casa", "#/"));
    await t.page.locator(".cmd-trigger").click();
    await t.page.locator(".cmdk input").fill("Casa");
    await t.page.locator(".cmdk").getByText("Casa", { exact: true }).first().click();
    await t.page.waitForFunction(() => location.hash === "#/casa");
    await t.page.locator("#page-title", { hasText: "Casa" }).waitFor();
  });
});

// mutação: tirar `|| r.path === "/casa"` de ATIVAS (router.ts): o hash #/casa abre a Casa no protótipo e o menu a lista
test("protótipo (DEMO): sem Casa no menu, no ⌘K e na rota (#/casa vira o Resumo)", async () => {
  const { ctx, page } = await navegarPrototipo();
  try {
    assert.equal(await page.locator(".rail-list a", { hasText: "Casa" }).count(), 0);
    assert.equal(await page.evaluate(() => location.hash), "#/");
    assert.match(await page.locator("#page-title").innerText(), /^Resumo/);
    await page.locator(".cmd-trigger").click();
    await page.locator(".cmdk input").fill("Casa");
    assert.equal(await page.locator(".cmdk").getByText("Casa", { exact: true }).count(), 0);
  } finally { await ctx.close(); }
});
async function navegarPrototipo() {
  const ctx = await browser().newContext({ viewport: { width: 1280, height: 800 }, reducedMotion: "reduce" });
  await servir(ctx, RAIZ);
  await ctx.addInitScript(() => localStorage.setItem("pigbank.dashboard.profile.v1", '"padrao"'));
  const page = await ctx.newPage();
  await page.goto(`${PROTOTIPO}#/casa`);
  await page.locator("#page-title").waitFor();
  return { ctx, page };
}
