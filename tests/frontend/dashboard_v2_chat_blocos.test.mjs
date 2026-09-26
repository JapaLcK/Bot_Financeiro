// Protótipo dashboard-v2: os blocos vivos da conversa com o Piggy (parts/LiveAnswer.tsx).
//   · cada resposta tem o próprio estado: mexer num bloco não mexe no painel nem nas
//     outras respostas, e o detalhe/extrato abre dentro da própria resposta;
//   · "Abrir no painel" leva o estado da resposta para a página, mesmo com outro mês no painel;
//   · visão compacta: bloco alto corta com "Ver tudo", e o foco num controle cortado abre;
//   · no painel (sem conversa) os blocos seguem escrevendo no estado global.
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

async function abrir({ width = 1440, hash = "#/", perfil = "padrao" } = {}) {
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
  await page.goto(`${ORIGIN}/dashboard-v2/${hash}`);
  await page.locator("#page-title").waitFor();
  return { ctx, page, erros };
}
const respostas = (page) => page.locator(".chat > .msg-piggy");
// Pergunta livre (o Piggy responde com os atalhos) e segue o atalho `nome`.
async function atalho(page, nome) {
  const antes = await respostas(page).count();
  await page.locator("#askbar-input").fill("oi");
  await page.locator("#askbar-input").press("Enter");
  await page.waitForFunction((n) => document.querySelectorAll(".chat > .msg-piggy").length > n, antes);
  await respostas(page).last().locator(".chat-follow button", { hasText: nome }).click();
  await page.waitForFunction((n) => document.querySelectorAll(".chat > .msg-piggy").length > n + 1, antes);
  const r = respostas(page).nth(antes + 1); // fixa esta resposta: `.last()` seguiria a próxima
  await r.locator(".msg-block").first().waitFor();
  return r;
}
const irPara = async (page, hash, titulo) => {
  await page.evaluate((h) => { location.hash = h; }, hash);
  await page.locator("#page-title", { hasText: titulo }).waitFor();
};

test("escolher a categoria na resposta abre o detalhe nela, e o painel fica sem filtro", async () => {
  const { ctx, page, erros } = await abrir();
  const r = await atalho(page, "Pra onde vai meu dinheiro?");
  const antes = await r.locator(".msg-block").count();
  await r.locator(".cat", { hasText: "Delivery" }).click();
  const depois = await r.evaluate((m) => [
    [...m.querySelectorAll(".cat[aria-pressed='true']")].map((b) => b.textContent.replace(/R\$.*/, "")),
    [...m.querySelectorAll(".msg-block article")].map((a) => a.id.replace(/^m\d+-/, "")),
    m.querySelectorAll(".msg-block article")[1]?.querySelector(".w-title").textContent,
  ]);
  await irPara(page, "#/gastos", "Para onde vai");
  const painel = await page.locator(".cats [aria-pressed='true']").count();
  await ctx.close();
  assert.equal(antes, 1);
  assert.deepEqual(depois, [["Delivery"], ["w-categorias", "w-cat-detalhe"], "Delivery"]);
  assert.equal(painel, 0);
  assert.deepEqual(erros, []);
});

test("o estado da resposta sobrevive à troca de página: categoria, detalhe e \"Ver tudo\"", async () => {
  const { ctx, page, erros } = await abrir();
  const r = await atalho(page, "Pra onde vai meu dinheiro?");
  await r.locator(".cat", { hasText: "Delivery" }).click();
  await r.locator(".msg-more").first().click();
  const estado = (m) => [
    [...m.querySelectorAll(".cat[aria-pressed='true']")].map((b) => b.textContent.replace(/R\$.*/, "")),
    [...m.querySelectorAll(".msg-block article")].map((a) => a.id.replace(/^m\d+-/, "")),
    m.querySelector(".msg-more")?.getAttribute("aria-expanded"),
  ];
  const antes = await r.evaluate(estado);
  await irPara(page, "#/gastos", "Para onde vai");
  const painel = await page.locator(".cats [aria-pressed='true']").count();
  await irPara(page, "#/piggy", "Converse com o Piggy");
  await r.locator(".msg-more").first().waitFor(); // a medição da altura chega depois da 1ª pintura
  const depois = await r.evaluate(estado);
  await ctx.close();
  assert.deepEqual(antes, [["Delivery"], ["w-categorias", "w-cat-detalhe"], "true"]);
  assert.deepEqual(depois, antes);
  assert.equal(painel, 0);
  assert.deepEqual(erros, []);
});

test("duas respostas iguais: mexer numa não mexe na outra, e os ids seguem únicos com detalhe e extrato abertos", async () => {
  const { ctx, page, erros } = await abrir();
  const a = await atalho(page, "Pra onde vai meu dinheiro?");
  const b = await atalho(page, "Pra onde vai meu dinheiro?");
  await b.locator(".cat", { hasText: "Mercado" }).click();
  const [pa, pb] = [await a.locator(".cat[aria-pressed='true']").count(), await b.locator(".cat[aria-pressed='true']").count()];
  // Abre detalhe e extrato nas duas: os mesmos widgets duas vezes na página.
  await a.locator(".cat", { hasText: "Delivery" }).click();
  for (const r of [a, b]) await r.locator("button.link", { hasText: /^Ver \d+ lançamentos/ }).click();
  const r = await page.evaluate(() => {
    const ids = [...document.querySelectorAll("[id]")].map((e) => e.id);
    return [ids.filter((x, i) => ids.indexOf(x) !== i), document.querySelectorAll(".chat .ledger").length];
  });
  const chips = [await a.locator(".ledger-chips").textContent(), await b.locator(".ledger-chips").textContent()];
  await ctx.close();
  assert.deepEqual([pa, pb], [0, 1]);
  assert.deepEqual(r, [[], 2]);
  assert.match(chips[0], /Delivery/);
  assert.match(chips[1], /Mercado/);
  assert.deepEqual(erros, []);
});

test("\"Ver N lançamentos\" abre o extrato da categoria na própria resposta, sem sair da conversa", async () => {
  const { ctx, page } = await abrir();
  await atalho(page, "Pra onde vai meu dinheiro?");
  await respostas(page).last().locator(".chat-follow button", { hasText: /^Me mostra o detalhe de/ }).click();
  await page.waitForFunction(() => document.querySelectorAll(".chat > .msg-piggy").length === 3);
  const r = respostas(page).last();
  const ver = await r.locator("button.link", { hasText: /^Ver \d+ lançamentos/ }).textContent();
  const cat = await r.locator(".detail-title").evaluate((t) => t.childNodes[1].textContent);
  await r.locator("button.link", { hasText: /^Ver \d+ lançamentos/ }).click();
  await r.locator(".ledger").waitFor({ timeout: 2000 }).catch(() => {});
  const extrato = await r.evaluate((m) => [
    m.querySelectorAll(".ledger").length, m.querySelector(".ledger-chips")?.textContent, m.querySelectorAll(".ledger .row").length,
  ]);
  const hash = await page.evaluate(() => location.hash);
  await ctx.close();
  assert.equal(hash, "#/piggy");
  assert.deepEqual(extrato, [1, cat + "remover filtro", Number(ver.match(/\d+/)[0])]);
});

test("\"Abrir no painel\" leva a categoria, inclusive com outro mês escolhido no painel", async () => {
  const { ctx, page } = await abrir({ hash: "#/gastos" });
  await page.locator("[aria-label='Mês anterior']").click(); // o painel em agosto
  const r = await atalho(page, "Pra onde vai meu dinheiro?");
  await r.locator(".cat", { hasText: "Delivery" }).click();
  await r.locator(".msg-open").click();
  await page.locator("#page-title", { hasText: "Para onde vai" }).waitFor();
  const painel = await page.evaluate(() => [
    [...document.querySelectorAll(".cats [aria-pressed='true']")].map((b) => b.textContent.replace(/R\$.*/, "")),
    document.querySelector(".month-switch").textContent,
  ]);
  await ctx.close();
  assert.deepEqual(painel[0], ["Delivery"]);
  assert.match(painel[1], /setembro/i);
});

test("\"Abrir no painel\" do dia da semana leva o dia escolhido, mesmo com outro mês no painel", async () => {
  const { ctx, page } = await abrir({ hash: "#/lancamentos", perfil: "controlar" });
  await page.locator("[aria-label='Mês anterior']").click();
  await irPara(page, "#/piggy", "Converse com o Piggy");
  await page.locator(".chat-follow button", { hasText: "Em que dia da semana eu mais gasto?" }).click();
  const r = respostas(page).last();
  await r.locator(".msg-more").click(); // o dia mais caro fica abaixo do corte da visão compacta
  const dia = r.locator(".cal-day[data-step='6']").first();
  const n = Number(await dia.textContent());
  await dia.click();
  const extrato = await r.locator(".ledger-chips").textContent(); // o extrato do dia, dentro da resposta
  await r.locator(".msg-open").click();
  await page.locator("#page-title", { hasText: "Lançamentos" }).waitFor();
  const chips = await page.locator(".ledger-chips").textContent().catch(() => "");
  await ctx.close();
  assert.match(extrato, new RegExp(`dia ${n}(?!\\d)`));
  assert.match(chips, new RegExp(`dia ${n}(?!\\d)`));
});

test("visão compacta: \"Ver tudo\" só em bloco alto, abre no lugar, e o foco num controle cortado abre", async () => {
  const { ctx, page } = await abrir();
  const f = await atalho(page, "E a minha fatura?"); // fatura (alto) e parcelas (baixo)
  await f.locator(".msg-block[data-cut]").waitFor(); // a medição da altura chega depois da 1ª pintura
  const blocos = await f.evaluate((m) => [...m.querySelectorAll(".msg-block")].map((b) => {
    const btn = b.nextElementSibling?.matches(".msg-more") ? b.nextElementSibling : null;
    return [Math.round(b.getBoundingClientRect().height), btn && [btn.textContent, btn.getAttribute("aria-expanded"), btn.getAttribute("aria-controls") === b.id]];
  }));
  await f.locator(".msg-more").click();
  const aberto = await f.evaluate((m) => [Math.round(m.querySelector(".msg-block").getBoundingClientRect().height), m.querySelector(".msg-more").getAttribute("aria-expanded"), m.querySelector(".msg-more").textContent]);

  const c = await atalho(page, "Pra onde vai meu dinheiro?");
  const bloco = c.locator(".msg-block").first();
  await c.locator(".msg-block[data-cut]").waitFor();
  // O último controle ainda visível e o primeiro cortado.
  const [visivel, cortado] = await bloco.evaluate((b) => {
    const top = b.getBoundingClientRect().top;
    const cs = [...b.querySelectorAll("button")];
    const i = cs.findIndex((x) => x.getBoundingClientRect().bottom - top > b.clientHeight);
    return [i - 1, i];
  });
  await bloco.locator("button").nth(visivel).focus();
  const naoAbriu = await bloco.getAttribute("data-cut");
  await page.keyboard.press("Tab");
  const depois = await bloco.evaluate((b, i) => [b.hasAttribute("data-cut"), document.activeElement === b.querySelectorAll("button")[i], b.getBoundingClientRect().height > 340, b.nextElementSibling.getAttribute("aria-expanded")], cortado);
  await ctx.close();
  assert.equal(blocos[0][0], 340);
  assert.deepEqual(blocos[0][1], ["Ver tudo", "false", true]);
  assert.ok(blocos[1][0] < 340 && blocos[1][1] === null, JSON.stringify(blocos[1]));
  assert.ok(aberto[0] > 340, String(aberto[0]));
  assert.deepEqual(aberto.slice(1), ["true", "Mostrar menos"]);
  assert.ok(visivel >= 0, "havia controle visível antes do corte");
  assert.equal(naoAbriu, "true"); // foco num controle visível não abre
  assert.deepEqual(depois, [false, true, true, "true"]);
});

test("no painel os blocos seguem escrevendo no estado global", async () => {
  const { ctx, page } = await abrir({ hash: "#/gastos" });
  await page.locator(".cats .cat", { hasText: "Delivery" }).click();
  const marcada = await page.locator(".cats [aria-pressed='true']").count();
  await page.locator("button.link", { hasText: /^Ver \d+ lançamentos/ }).click();
  await page.locator("#page-title", { hasText: "Lançamentos" }).waitFor();
  const r = await page.evaluate(() => [location.hash, document.querySelector(".ledger-chips")?.textContent, document.querySelector(".ledger").id]);
  await ctx.close();
  assert.equal(marcada, 1);
  assert.deepEqual(r, ["#/lancamentos", "Deliveryremover filtro", "lancamentos"]);
});
