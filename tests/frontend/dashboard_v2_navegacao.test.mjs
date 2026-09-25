// Protótipo dashboard-v2: a navegação com o Piggy no meio e a página de Ferramentas.
//   · celular: barra de baixo Resumo, Gastos, Piggy (meio), Metas, Extrato;
//   · menu lateral sem o Piggy (no desktop o acesso é a barra de conversa) e com Ferramentas;
//   · "E se…" virou "Simulador" em todo lugar; o botão rosa virou "Ferramentas", com o
//     nome escrito também no celular;
//   · Ferramentas: só o Simulador leva a algum lugar, o resto está "Em breve";
//   · 320 a 1440 sem nada saindo da barra de cima.
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { execSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { chromium } from "playwright";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const ORIGIN = "http://127.0.0.1:1"; // fictícia: a rota atende da raiz do repositório

let browser;
before(async () => {
  execSync("npm --prefix webapp run build:dashboard", { cwd: ROOT, stdio: "pipe" });
  browser = await chromium.launch();
});
after(() => browser?.close());

async function abrir(width, hash = "#/") {
  const ctx = await browser.newContext({ viewport: { width, height: 800 }, reducedMotion: "reduce" });
  await ctx.route("**/*", (r) => {
    const url = new URL(r.request().url());
    if (url.origin !== ORIGIN) return r.abort();
    const path = decodeURIComponent(url.pathname).replace(/\/$/, "/index.html");
    return r.fulfill({ path: join(ROOT, path) }).catch(() => r.fulfill({ status: 404, body: "" }));
  });
  await ctx.addInitScript(() => localStorage.setItem("pigbank.dashboard.profile.v1", '"padrao"'));
  const page = await ctx.newPage();
  await page.goto(`${ORIGIN}/dashboard-v2/${hash}`);
  await page.locator("#page-title").waitFor();
  return { ctx, page };
}
const textos = (page, sel) => page.locator(sel).evaluateAll((els) => els.map((e) => e.textContent.trim()));

test("celular: o Piggy é o botão do meio e leva à página dele", async () => {
  const { ctx, page } = await abrir(390);
  const abas = await textos(page, ".tabbar a");
  await page.locator('.tabbar a[data-tab="piggy"]').click();
  await page.waitForFunction(() => location.hash === "#/piggy");
  const r = [await page.locator("#page-title").innerText(), await page.locator('.tabbar a[data-tab="piggy"]').getAttribute("aria-current")];
  await ctx.close();
  assert.deepEqual(abas, ["Resumo", "Gastos", "Piggy", "Metas", "Extrato"]);
  assert.deepEqual(r, ["Converse com o Piggy", "page"]);
});

test("desktop: menu com Simulador e Ferramentas, sem Piggy; o botão rosa abre Ferramentas", async () => {
  const { ctx, page } = await abrir(1440);
  const menu = await textos(page, ".rail-list a");
  const botao = await page.locator(".topbar .btn-primary").innerText();
  await page.locator(".topbar .btn-primary").click();
  await page.waitForFunction(() => location.hash === "#/ferramentas");
  await page.locator(".tools li").first().waitFor();
  const cards = await page.locator(".tools li").evaluateAll((lis) => lis.map((li) => [li.querySelector("b").textContent, li.querySelector("a")?.getAttribute("href") ?? li.querySelector("[aria-disabled]")?.textContent.includes("Em breve")]));
  await ctx.close();
  assert.deepEqual(menu, ["Resumo", "Previsão", "Para onde vai", "Simulador", "Metas", "Patrimônio", "Lançamentos", "Ferramentas"]);
  assert.equal(botao.trim(), "Ferramentas");
  assert.deepEqual(cards[0], ["Simulador", "#/simulador"]);
  assert.equal(cards.length, 6);
  assert.ok(cards.slice(1).every(([, v]) => v === true), JSON.stringify(cards)); // o resto: "Em breve", sem link
});

test("o nome antigo \"E se…\" não aparece mais", async () => {
  const { ctx, page } = await abrir(1440, "#/");
  const r = await page.evaluate(() => document.body.innerText.includes("E se…"));
  await ctx.close();
  assert.equal(r, false);
});

test("barra de cima: o botão Ferramentas fica dentro da margem de 320 a 1440", async () => {
  for (const width of [320, 340, 360, 361, 375, 383, 390, 414, 760, 1440]) { // 361–383: a busca volta e ainda tem de caber
    const { ctx, page } = await abrir(width);
    const r = await page.evaluate(() => {
      const b = document.querySelector(".topbar .btn-primary").getBoundingClientRect();
      const t = document.querySelector(".month-title");
      return [document.documentElement.clientWidth - Math.round(b.right) >= 16, b.width > 0 && /Ferramentas/.test(document.querySelector(".topbar .btn-primary").textContent),
        t.scrollWidth <= t.clientWidth, document.scrollingElement.scrollWidth - document.scrollingElement.clientWidth];
    });
    await ctx.close();
    assert.deepEqual(r, [true, true, true, 0], String(width));
  }
});

test("a aba do Piggy mostra quando é a página atual, como as outras", async () => {
  const { ctx, page } = await abrir(390);
  const estilo = () => page.locator('.tabbar a[data-tab="piggy"] img').evaluate((i) => `${getComputedStyle(i).filter}|${getComputedStyle(i).opacity}`);
  const fora = await estilo();
  await page.locator('.tabbar a[data-tab="piggy"]').click();
  await page.locator('.tabbar a[data-tab="piggy"][aria-current="page"]').waitFor();
  await page.waitForFunction(() => getComputedStyle(document.querySelector('.tabbar a[data-tab="piggy"] img')).opacity === "1", null, { timeout: 3000 }).catch(() => {}); // há transição
  const dentro = await estilo();
  await ctx.close();
  assert.notEqual(fora, dentro);
  assert.equal(dentro, "none|1");
});
