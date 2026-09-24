/**
 * Protótipo dashboard-v2, revisão do PR #546: o filtro de origem do extrato
 * (parts/Ledger.tsx) mora no store junto dos outros filtros, e a paleta
 * (parts/Command.tsx) o zera ao levar a um lançamento. Antes ele era estado local do
 * Ledger: já em #/lancamentos a paleta não remonta o Ledger, e com "WhatsApp" marcado o
 * lançamento de outra origem escolhido na paleta ficava escondido (lista vazia).
 *
 * Rodar:  npm run test:frontend   (o `before` gera o bundle, gitignored, em dashboard-v2/)
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { execSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { chromium } from "playwright";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const ORIGIN = "http://127.0.0.1:1"; // fictícia: a rota atende da raiz do repositório (como em dashboard_v2_rodada8)

let browser;
before(async () => {
  execSync("npm --prefix webapp run build:dashboard", { cwd: ROOT, stdio: "pipe" });
  browser = await chromium.launch();
});
after(() => browser?.close());

async function abrir() {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce" });
  await ctx.route("**/*", (r) => {
    const url = new URL(r.request().url());
    if (url.origin !== ORIGIN) return r.abort();
    const path = decodeURIComponent(url.pathname).replace(/\/$/, "/index.html");
    return r.fulfill({ path: join(ROOT, path) }).catch(() => r.fulfill({ status: 404, body: "" }));
  });
  const page = await ctx.newPage();
  await page.goto(`${ORIGIN}/dashboard-v2/#/lancamentos`);
  await page.locator(".ledger").waitFor();
  return { ctx, page };
}

const origem = (page, nome) => page.locator(".ledger .seg").getByRole("radio", { name: nome });
const linhas = (page) => page.locator(".ledger .row-label").allTextContents();

test("paleta: lançamento de outra origem aparece mesmo com WhatsApp marcado, e a origem volta a Todos", async () => {
  const { ctx, page } = await abrir();
  await origem(page, "WhatsApp").click();
  await page.keyboard.press("Control+k");
  await page.locator(".cmdk input").fill("Aluguel");
  const primeiro = await page.locator(".cmdk [role=option]").first().textContent();
  await page.keyboard.press("Enter");
  const r = { linhas: await linhas(page), todos: await origem(page, "Todos").getAttribute("aria-checked"), hash: await page.evaluate(() => location.hash) };
  await ctx.close();
  assert.match(primeiro, /Aluguel \(sua parte\)/); // o Enter escolheu o lançamento (Open Finance)
  assert.equal(r.hash, "#/lancamentos");
  assert.ok(r.linhas.includes("Aluguel (sua parte)"), `lista: ${JSON.stringify(r.linhas)}`);
  assert.equal(r.todos, "true");
});

test("positivo: o segmentado de origem continua filtrando", async () => {
  const { ctx, page } = await abrir();
  const todas = await linhas(page);
  await origem(page, "WhatsApp").click();
  const zap = await linhas(page);
  const fontes = await page.locator(".ledger .row-src").allTextContents();
  await ctx.close();
  assert.ok(todas.includes("Aluguel (sua parte)")); // Open Finance, visível em Todos
  assert.ok(zap.length > 0 && !zap.includes("Aluguel (sua parte)"));
  assert.ok(fontes.every((f) => f.trim() === "WhatsApp"), JSON.stringify(fontes));
});

test("sair do extrato devolve a origem a Todos (não se soma ao filtro de outra tela)", async () => {
  const { ctx, page } = await abrir();
  const todas = await linhas(page);
  await origem(page, "WhatsApp").click();
  const menu = (nome) => page.locator(".rail-list").getByRole("link", { name: nome, exact: true }).click();
  await menu("Resumo");
  await page.waitForFunction(() => location.hash === "#/");
  await menu("Lançamentos");
  await page.locator(".ledger").waitFor();
  const r = { linhas: await linhas(page), todos: await origem(page, "Todos").getAttribute("aria-checked") };
  await ctx.close();
  assert.equal(r.todos, "true");
  assert.deepEqual(r.linhas, todas);
});

// Busca e dia velhos: marcados antes, eles se somariam ao recorte que o atalho promete.
async function filtrosVelhos(page) {
  await page.evaluate(() => { location.hash = "#/gastos"; });
  await page.locator(".cal-day:not([disabled])").first().click();
  await page.evaluate(() => { location.hash = "#/lancamentos"; });
  await page.locator(".ledger input[type=search]").fill("Aluguel");
}
const recorte = async (page) => ({
  linhas: (await linhas(page)).length,
  busca: await page.locator(".ledger input[type=search]").inputValue(),
  chips: (await page.locator(".ledger-chips .chip").allTextContents()).map((t) => t.replace("remover filtro", "").trim()),
});

test("paleta: “Filtrar por categoria” zera a busca e o dia antigos", async () => {
  const { ctx, page } = await abrir();
  await filtrosVelhos(page);
  await page.keyboard.press("Control+k");
  await page.locator(".cmdk input").fill("Delivery");
  await page.locator(".cmdk [role=option]").filter({ hasText: /^Delivery$/ }).click(); // não o "Simular: delivery…"
  const r = await recorte(page);
  await ctx.close();
  assert.deepEqual(r.chips, ["Delivery"]);
  assert.equal(r.busca, "");
  assert.ok(r.linhas > 0, JSON.stringify(r));
});

test("“Ver N lançamentos” do detalhe de categoria mostra os N, mesmo com busca e dia antigos", async () => {
  const { ctx, page } = await abrir();
  await filtrosVelhos(page);
  await page.evaluate(() => { location.hash = "#/gastos"; });
  const ver = page.getByRole("button", { name: /^Ver \d+ lançamentos/ });
  const n = Number((await ver.textContent()).match(/\d+/)[0]);
  await ver.click();
  await page.locator(".ledger").waitFor();
  const r = await recorte(page);
  await ctx.close();
  assert.ok(n > 0);
  assert.equal(r.linhas, n, JSON.stringify(r));
  assert.equal(r.busca, "");
});
