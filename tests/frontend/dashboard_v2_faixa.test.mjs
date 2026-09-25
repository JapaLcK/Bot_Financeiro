// Protótipo dashboard-v2: a faixa do Piggy no topo do Resumo (parts/PiggyBand.tsx).
//   · o sorteio (lib/prompts.js): peso, e nunca a mesma opção duas visitas seguidas;
//   · fixa em todos os painéis e fora do grid (sem ✕, fora do catálogo);
//   · o clique abre o chat de produção com a pergunta já enviada;
//   · no Essencial vira convite para o Plus, sem abrir o chat;
//   · 320 e 390 sem rolagem para o lado.
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { execSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { chromium } from "playwright";
import { BY_PROFILE, COMMON, pick } from "../../webapp/src/dashboard/lib/prompts.js";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const ORIGIN = "http://127.0.0.1:1"; // fictícia: a rota atende da raiz do repositório
const PERFIS = ["padrao", "economizar", "investir", "controlar", "dividas", "autonomo"];

test("sorteio: respeita o peso e não repete a última", () => {
  const ops = [{ key: "a", weight: 1 }, { key: "b", weight: 3 }];
  assert.equal(pick(ops, null, () => 0.2).key, "a");  // 0,2 × 4 = 0,8 < 1
  assert.equal(pick(ops, null, () => 0.3).key, "b");  // 1,2 cai no peso do b
  assert.equal(pick(ops, "b", () => 0.99).key, "a");  // a última sai do sorteio
  assert.equal(pick([{ key: "só", weight: 1 }], "só", () => 0).key, "só"); // sem outra, repete
});

test("frases: chaves únicas, todo perfil tem 2 ou 3, toda pergunta tem convite", () => {
  const all = [...COMMON, ...Object.values(BY_PROFILE).flat()];
  assert.equal(new Set(all.map((p) => p.key)).size, all.length);
  assert.deepEqual(Object.keys(BY_PROFILE).sort(), PERFIS.filter((p) => p !== "padrao").sort());
  for (const list of Object.values(BY_PROFILE)) assert.ok(list.length >= 2 && list.length <= 3);
  for (const p of all) assert.ok(p.head && (p.ask === null || p.ask.length > 10), p.key);
});

let browser;
before(async () => {
  execSync("npm --prefix webapp run build:dashboard", { cwd: ROOT, stdio: "pipe" });
  browser = await chromium.launch();
});
after(() => browser?.close());

// `sorte` fixa o Math.random (0 = sempre a primeira opção do sorteio).
async function abrir({ width = 1440, perfil = "padrao", qs = "", sorte = null } = {}) {
  const ctx = await browser.newContext({ viewport: { width, height: 900 }, reducedMotion: "reduce" });
  await ctx.route("**/*", (r) => {
    const url = new URL(r.request().url());
    if (url.origin !== ORIGIN) return r.abort();
    const path = decodeURIComponent(url.pathname).replace(/\/$/, "/index.html");
    return r.fulfill({ path: join(ROOT, path) }).catch(() => r.fulfill({ status: 404, body: "" }));
  });
  await ctx.addInitScript((p) => localStorage.setItem("pigbank.dashboard.profile.v1", JSON.stringify(p)), perfil);
  if (sorte !== null) await ctx.addInitScript((v) => { Math.random = () => v; }, sorte);
  const page = await ctx.newPage();
  const erros = [];
  page.on("pageerror", (e) => erros.push(e.message));
  await page.goto(`${ORIGIN}/dashboard-v2/${qs}#/`);
  await page.locator(".piggy-band").waitFor();
  return { ctx, page, erros };
}
const faixa = (page) => page.locator(".piggy-band").getAttribute("data-band");

test("fixa em todos os painéis, fora do grid, sem ✕ no Organizar", async () => {
  for (const perfil of PERFIS) {
    const { ctx, page, erros } = await abrir({ perfil });
    await page.getByRole("button", { name: "Organizar" }).click();
    const r = await page.evaluate(() => {
      const b = document.querySelector(".piggy-band");
      return [document.querySelectorAll(".piggy-band").length, !!b.closest("[data-widget-id]"), !!b.querySelector("[data-slot=widget-remove]")];
    });
    await ctx.close();
    assert.deepEqual(r, [1, false, false], perfil);
    assert.deepEqual(erros, [], perfil);
  }
});

test("a cada visita outra opção: com a mesma sorte, duas visitas seguidas diferem", async () => {
  const { ctx, page } = await abrir({ perfil: "investir", sorte: 0 });
  const vistas = [await faixa(page)];
  for (let i = 0; i < 3; i++) { await page.reload(); await page.locator(".piggy-band").waitFor(); vistas.push(await faixa(page)); }
  await ctx.close();
  for (let i = 1; i < vistas.length; i++) assert.notEqual(vistas[i], vistas[i - 1], JSON.stringify(vistas));
});

test("insight do dia: o clique abre o chat com a pergunta dele já enviada", async () => {
  const { ctx, page, erros } = await abrir({ perfil: "economizar", sorte: 0 }); // 1ª opção = 1º insight
  const chave = await faixa(page);
  const cta = await page.locator(".piggy-band-cta").innerText();
  await page.locator(".piggy-band").click();
  await page.locator("#pigbank-chat-root").getByText("Aqui é a demonstração").waitFor();
  const r = await page.evaluate(() => [window.PigBankChatUI.isOpen("piggy"), document.getElementById("pigbank-chat-root").innerText]);
  await ctx.close();
  assert.match(chave, /^insight-/);
  const pergunta = cta.match(/“(.+)”/)[1];
  assert.equal(r[0], true);
  assert.ok(r[1].includes(pergunta), `${pergunta} ⊄ ${r[1]}`);
  assert.deepEqual(erros, []);
});

test("Essencial: convite para o Plus que leva aos planos e não abre o chat", async () => {
  const { ctx, page } = await abrir({ perfil: "investir", qs: "?plano=essencial" });
  const r = await page.evaluate(() => {
    const b = document.querySelector(".piggy-band");
    return [b.tagName, b.getAttribute("href"), b.dataset.band, /Plus/.test(b.textContent)];
  });
  const positivo = await abrir({ perfil: "investir", qs: "?plano=plus" });
  const tag = await positivo.page.locator(".piggy-band").evaluate((b) => b.tagName);
  await ctx.close(); await positivo.ctx.close();
  assert.deepEqual(r, ["A", "../frontend/precos.html", "plus", true]);
  assert.equal(tag, "BUTTON");
});

test("320 e 390: a faixa cabe na tela", async () => {
  for (const width of [320, 390]) {
    const { ctx, page } = await abrir({ width, perfil: "autonomo" });
    const r = await page.evaluate(() => {
      const b = document.querySelector(".piggy-band").getBoundingClientRect();
      return [document.scrollingElement.scrollWidth - document.scrollingElement.clientWidth, b.left >= 0 && b.right <= innerWidth];
    });
    await ctx.close();
    assert.deepEqual(r, [0, true], String(width));
  }
});

test("trocar de perfil sorteia de novo (a faixa não fica presa ao perfil de antes)", async () => {
  const { ctx, page } = await abrir({ perfil: "investir", sorte: 0 });
  const antes = await faixa(page);
  await page.selectOption("#board-profile", "dividas");
  await page.locator(`.piggy-band:not([data-band="${antes}"])`).waitFor({ timeout: 3000 }).catch(() => {});
  const depois = await faixa(page);
  await ctx.close();
  assert.notEqual(depois, antes); // com a mesma sorte, só muda se a faixa sortear de novo
});
