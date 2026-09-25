// Protótipo dashboard-v2: a conversa com o Piggy (#/piggy, parts/PiggyChat.tsx) e a
// barra de conversa (parts/AskBar.tsx).
//   · a barra flutua em todas as páginas no desktop; no celular só existe na conversa;
//   · digitar + Enter abre a conversa com a pergunta; sem IA no protótipo, texto livre
//     recebe os atalhos, e cada atalho responde com texto, blocos e próximas perguntas;
//   · os 8 assuntos respondem sem erro, sem id repetido na página;
//   · os blocos da resposta são uma foto: clicar neles não mexe no painel (PR 3 os anima);
//   · a conversa sobrevive à troca de página e some ao recarregar;
//   · estado vazio com as sugestões do perfil primeiro; Essencial vê o convite do Plus.
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

async function abrir({ width = 1440, hash = "#/", perfil = "padrao", qs = "" } = {}) {
  const ctx = await browser.newContext({ viewport: { width, height: 900 }, reducedMotion: "reduce" });
  await ctx.route("**/*", (r) => {
    const url = new URL(r.request().url());
    if (url.origin !== ORIGIN) return r.abort();
    const path = decodeURIComponent(url.pathname).replace(/\/$/, "/index.html");
    return r.fulfill({ path: join(ROOT, path) }).catch(() => r.fulfill({ status: 404, body: "" }));
  });
  await ctx.addInitScript((p) => localStorage.setItem("pigbank.dashboard.profile.v1", JSON.stringify(p)), perfil);
  const page = await ctx.newPage();
  const erros = [];
  page.on("pageerror", (e) => erros.push(e.message));
  await page.goto(`${ORIGIN}/dashboard-v2/${qs}${hash}`);
  await page.locator("#page-title").waitFor();
  return { ctx, page, erros };
}
const perguntar = async (page, texto) => {
  await page.locator("#askbar-input").fill(texto);
  await page.locator("#askbar-input").press("Enter");
  await page.waitForFunction(() => location.hash === "#/piggy");
};
const respostas = (page) => page.locator(".chat > .msg-piggy").count();
// Clica uma próxima pergunta da última resposta e espera a resposta dela chegar.
const seguir = async (page, nome) => {
  const antes = await respostas(page);
  await page.locator(".chat > .msg-piggy").last().locator(".chat-follow button", { hasText: nome }).click();
  await page.waitForFunction((n) => document.querySelectorAll(".chat > .msg-piggy").length > n, antes);
};

test("desktop: a barra está em todas as páginas; celular: só na conversa", async () => {
  const { ctx, page } = await abrir();
  const desktop = {};
  for (const h of ["#/", "#/gastos", "#/simulador", "#/ferramentas", "#/piggy"]) {
    await page.evaluate((x) => { location.hash = x; }, h);
    await page.waitForFunction((x) => location.hash === x, h);
    desktop[h] = await page.locator(".askbar").isVisible();
  }
  await page.setViewportSize({ width: 390, height: 844 });
  const celular = {};
  for (const h of ["#/", "#/piggy"]) {
    await page.evaluate((x) => { location.hash = x; }, h);
    await page.waitForFunction((x) => location.hash === x, h);
    celular[h] = await page.locator(".askbar").isVisible();
  }
  await ctx.close();
  assert.deepEqual(desktop, { "#/": true, "#/gastos": true, "#/simulador": true, "#/ferramentas": true, "#/piggy": true });
  assert.deepEqual(celular, { "#/": false, "#/piggy": true });
});

test("digitar numa página qualquer abre a conversa; texto livre recebe os atalhos", async () => {
  const { ctx, page, erros } = await abrir({ hash: "#/gastos" });
  await perguntar(page, "qual a capital da França?");
  await page.locator(".chat > .msg-piggy").first().waitFor();
  const r = await page.evaluate(() => [
    document.querySelector(".chat .msg-user").textContent,
    document.querySelector(".chat .msg-piggy .msg-text").textContent.startsWith("Aqui no protótipo"),
    document.querySelectorAll(".chat .msg-piggy .chat-follow button").length,
    document.querySelector("#askbar-input").value,
  ]);
  await ctx.close();
  assert.deepEqual(r, ["qual a capital da França?", true, 4, ""]);
  assert.deepEqual(erros, []);
});

test("os 8 assuntos respondem com blocos, sem erro e sem id repetido", async () => {
  const { ctx, page, erros } = await abrir();
  await perguntar(page, "oi");
  await page.locator(".chat > .msg-piggy").first().waitFor();
  const blocos = {};
  for (const [assunto, nome] of [
    ["categorias", "Pra onde vai meu dinheiro?"], ["lancamentos", "Quais foram meus maiores gastos?"],
    ["categoria", /^Me mostra o detalhe/], ["saldo", "Vai sobrar até o fim do mês?"], ["fatura", "E a minha fatura?"],
    ["saldo2", "Vai sobrar até o fim do mês?"], ["metas", "Quanto falta pras minhas metas?"],
    ["investimentos", "Como estão meus investimentos?"], ["renda", "Como anda a minha renda?"],
  ]) {
    await seguir(page, nome);
    blocos[assunto] = await page.locator(".chat > .msg-piggy").last().locator(".msg-block").count();
  }
  const r = await page.evaluate(() => {
    const ids = [...document.querySelectorAll("[id]")].map((e) => e.id);
    return [ids.filter((x, i) => ids.indexOf(x) !== i), document.querySelectorAll(".chat .chat-follow").length];
  });
  await ctx.close();
  assert.deepEqual(blocos, { categorias: 1, lancamentos: 1, categoria: 1, saldo: 2, fatura: 2, saldo2: 2, metas: 1, investimentos: 2, renda: 1 });
  assert.deepEqual(r, [[], 1]); // nenhum id repetido; só a última resposta oferece próximas perguntas
  assert.deepEqual(erros, []);
});

test("os blocos da resposta são uma foto: clicar neles não mexe no painel", async () => {
  const { ctx, page } = await abrir();
  await perguntar(page, "oi");
  await page.locator(".chat > .msg-piggy").first().waitFor();
  await seguir(page, "Pra onde vai meu dinheiro?");
  const bloco = page.locator(".chat > .msg-piggy").last().locator(".msg-block");
  const inerte = await bloco.evaluate((b) => b.inert);
  await bloco.locator("button").first().click({ force: true }); // o usuário tenta filtrar uma categoria
  await page.evaluate(() => { location.hash = "#/gastos"; });
  await page.locator("#page-title", { hasText: "Para onde vai" }).waitFor();
  const filtrado = await page.locator(".cats [aria-pressed='true'], .chip[aria-pressed='true']").count();
  await ctx.close();
  assert.equal(inerte, true);
  assert.equal(filtrado, 0);
});

test("a conversa sobrevive à troca de página e some ao recarregar", async () => {
  const { ctx, page } = await abrir();
  await perguntar(page, "oi");
  await page.locator(".chat > .msg-piggy").first().waitFor();
  await page.evaluate(() => { location.hash = "#/metas"; });
  await page.locator("#page-title", { hasText: "Metas" }).waitFor();
  await page.evaluate(() => { location.hash = "#/piggy"; });
  const volta = await page.locator(".chat > li").count();
  await page.reload();
  await page.locator("#page-title").waitFor();
  const depois = [await page.locator(".chat > li").count(), await page.locator(".chat-empty").count()];
  await ctx.close();
  assert.equal(volta, 2);
  assert.deepEqual(depois, [0, 1]);
});

test("estado vazio: as sugestões do perfil vêm primeiro e respondem", async () => {
  const { ctx, page } = await abrir({ hash: "#/piggy", perfil: "investir" });
  const ideias = await page.locator(".chat-empty .chat-follow button").evaluateAll((bs) => bs.map((b) => b.textContent));
  await page.locator(".chat-empty .chat-follow button").first().click();
  await page.locator(".chat > .msg-piggy .msg-block").first().waitFor();
  const blocos = await page.locator(".chat > .msg-piggy .msg-block").count();
  await ctx.close();
  assert.equal(ideias.length, 5);
  assert.equal(ideias[0], "Minha carteira está rendendo bem comparada ao CDI?");
  assert.equal(blocos, 2); // Rendimento × CDI + Onde está o dinheiro
});

test("Essencial: a conversa vira convite e a barra leva até ele", async () => {
  const { ctx, page } = await abrir({ hash: "#/gastos", qs: "?plano=essencial" });
  const barra = await page.locator(".askbar").evaluate((a) => [a.tagName, a.getAttribute("href"), a.textContent]);
  await page.locator(".askbar").click();
  await page.locator(".chat-plus").waitFor();
  const r = await page.evaluate(() => [!!document.querySelector("#askbar-input"), document.querySelector(".chat-plus-cta a").getAttribute("href")]);
  await ctx.close();
  assert.equal(barra[0], "A");
  assert.equal(barra[1], "#/piggy");
  assert.match(barra[2], /no Plus/);
  assert.deepEqual(r, [false, "../frontend/precos.html"]);
});

test("320 e 390: a conversa não rola para o lado e a barra fica acima da de baixo", async () => {
  for (const width of [320, 390]) {
    const { ctx, page } = await abrir({ width, hash: "#/piggy" });
    await page.locator(".chat-empty .chat-follow button").first().click();
    await page.locator(".chat > .msg-piggy .msg-block").first().waitFor();
    const r = await page.evaluate(() => {
      const a = document.querySelector(".askbar").getBoundingClientRect();
      const t = document.querySelector(".tabbar").getBoundingClientRect();
      return [document.scrollingElement.scrollWidth - document.scrollingElement.clientWidth, a.bottom <= t.top, a.left >= 0 && a.right <= innerWidth];
    });
    await ctx.close();
    assert.deepEqual(r, [0, true, true], String(width));
  }
});
