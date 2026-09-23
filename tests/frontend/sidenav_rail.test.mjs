import { before, after, test } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { startServer } from "./_server.mjs";

let server, browser, origin;
before(async () => {
  ({ proc: server, origin } = await startServer());
  browser = await chromium.launch();
});
after(async () => { await browser?.close(); server?.kill(); });

async function openPage(path, options = {}) {
  const context = await browser.newContext(options);
  const page = await context.newPage();
  await page.route("**/*", route => {
    const url = new URL(route.request().url());
    if (url.origin !== origin) return route.abort();
    if (/\.[a-z0-9]+$/i.test(url.pathname)) return route.continue();
    return route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
  });
  await page.goto(`${origin}/${path}`, { waitUntil: "domcontentloaded" });
  return { page, context };
}

// Entre as transições do menu, largura (sidenav-rail.css:24, .22s), opacidade dos
// rótulos (:70, .12s) e cores do item (`all .18s`, dashboard.css:214 e home.html:98)
// são SEPARADAS: um hover interrompido reverte a opacidade depois da largura, e a
// largura já era 68 com o `.sn-label` em 0,09–0,16 (medido em 2026-09-23). Espera a
// largura E nenhuma transição no menu; quem decide o estilo continua sendo o assert
// seguinte. O giro do `.sn-caret` (dashboard.css:260, home.html:109) também está no
// subtree, mas só dispara ao clicar num grupo, e nenhum caso que espera aqui clica.
const menuAssentado = (page, largura) => page.waitForFunction(w => {
  const menu = document.querySelector("#sidenav");
  return !menu.inert && Math.round(menu.getBoundingClientRect().width) === w
    && menu.getAnimations({ subtree: true }).length === 0;
}, largura);

for (const path of ["dashboard.html", "home.html"]) {
  test(`${path}: barra de ícones abre sobre o conteúdo e fecha ao sair`, async () => {
    const { page, context } = await openPage(path, { viewport: { width: 1440, height: 900 } });
    try {
      const nav = page.locator("#sidenav");
      const icon = nav.locator('.sidenav-item[href="/home"] .sn-icon');
      const label = nav.locator('.sidenav-item[href="/home"] .sn-label');
      // O ponteiro inicial pode estar sobre a barra e iniciar a expansão.
      await page.mouse.move(900, 500);
      await menuAssentado(page, 68);
      assert.equal(Math.round((await nav.boundingBox()).width), 68);
      assert.equal(await label.evaluate(el => getComputedStyle(el).opacity), "0");
      assert.equal(await page.locator("body").evaluate(el => getComputedStyle(el).paddingLeft), "68px");

      await icon.hover();
      await menuAssentado(page, 260);
      assert.equal(await label.evaluate(el => getComputedStyle(el).opacity), "1");
      await page.mouse.move(900, 500);
      await menuAssentado(page, 68);
      assert.equal(await page.locator("#sidenav-backdrop").evaluate(el => getComputedStyle(el).display), "none");
      assert.equal(await page.locator(".sidenav-toggle").evaluate(el => getComputedStyle(el).display), "none");

      await page.keyboard.press("Tab");
      await menuAssentado(page, 260);
      assert.equal(await page.evaluate(() => document.activeElement?.getAttribute("href")), "/home");
    } finally { await context.close(); }
  });

  test(`${path}: destaque rosa acompanha hover ou foco de teclado no item ativo`, async () => {
    const { page, context } = await openPage(path, { viewport: { width: 1440, height: 900 } });
    try {
      const nav = page.locator("#sidenav");
      const active = nav.locator(".sidenav-item.active");
      const visual = () => active.evaluate(el => ({
        background: getComputedStyle(el).backgroundImage,
        border: getComputedStyle(el).borderTopColor,
        shadow: getComputedStyle(el).boxShadow,
        icon: getComputedStyle(el.querySelector(".sn-icon")).backgroundImage,
      }));
      await page.mouse.move(900, 500);
      await menuAssentado(page, 68);
      assert.equal((await visual()).background, "none");
      assert.match((await visual()).border, /,\s*0\)$/);
      assert.equal((await visual()).shadow, "none");
      assert.equal((await visual()).icon, "none");

      await active.locator(".sn-icon").hover();
      await menuAssentado(page, 260);
      assert.match((await visual()).background, /gradient/);
      assert.match((await visual()).icon, /gradient/);

      await nav.locator(".sidenav-item:not(.active)").first().hover();
      assert.equal((await visual()).background, "none");
      assert.equal((await visual()).icon, "none");

      await page.mouse.move(900, 500);
      await page.keyboard.press("Tab");
      await active.focus();
      assert.equal(await active.evaluate(el => el.matches(":focus-visible")), true);
      assert.match((await visual()).background, /gradient/);
      assert.match((await visual()).icon, /gradient/);
    } finally { await context.close(); }
  });

  test(`${path}: indicador da rolagem aparece apenas enquanto o menu rola`, async () => {
    const { page, context } = await openPage(path, { viewport: { width: 1024, height: 440 } });
    try {
      const nav = page.locator("#sidenav");
      const thumbColor = () => nav.evaluate(el => getComputedStyle(el, "::-webkit-scrollbar-thumb").backgroundColor);
      assert.ok(await nav.evaluate(el => el.scrollHeight > el.clientHeight));
      assert.match(await thumbColor(), /,\s*0\)$/);

      await nav.evaluate(el => { el.scrollTop = 80; });
      await page.waitForFunction(() => document.querySelector("#sidenav").classList.contains("is-scrolling"));
      assert.doesNotMatch(await thumbColor(), /,\s*0\)$/);

      await page.waitForFunction(() => !document.querySelector("#sidenav").classList.contains("is-scrolling"));
      assert.match(await thumbColor(), /,\s*0\)$/);
    } finally { await context.close(); }
  });
}

test("home marca Início como ativo; dashboard mantém a gaveta no toque", async () => {
  const home = await openPage("home.html", { viewport: { width: 1440, height: 900 } });
  try {
    assert.equal(await home.page.locator('.sidenav-item[href="/home"]').getAttribute("aria-current"), "page");
  } finally { await home.context.close(); }

  const touch = await openPage("dashboard.html", {
    viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true,
  });
  try {
    const nav = touch.page.locator("#sidenav");
    await touch.page.waitForFunction(() => document.querySelector("#sidenav").inert);
    assert.ok((await nav.boundingBox()).x < 0);
    await touch.page.locator(".sidenav-toggle").click();
    await touch.page.waitForFunction(() => document.querySelector("#sidenav").getBoundingClientRect().x === 0);
    assert.equal(await nav.getAttribute("inert"), null);
    assert.equal(await touch.page.locator(".sidenav-toggle").getAttribute("aria-expanded"), "true");
  } finally { await touch.context.close(); }

  const homeTouch = await openPage("home.html", {
    viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true,
    userAgent: "Mozilla/5.0 PigBankApp/1.0",
  });
  try {
    const nav = homeTouch.page.locator("#sidenav");
    await homeTouch.page.waitForFunction(() => document.documentElement.classList.contains("pb-app"));
    await homeTouch.page.locator(".sidenav-toggle").click();
    await homeTouch.page.waitForFunction(() => document.querySelector("#sidenav").getBoundingClientRect().x === 0);
    assert.equal(await nav.getAttribute("inert"), null);
    await homeTouch.page.keyboard.press("Escape");
    assert.equal(await nav.getAttribute("inert"), "");
  } finally { await homeTouch.context.close(); }
});
