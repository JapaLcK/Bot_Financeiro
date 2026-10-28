// Protótipo dashboard-v2: a faixa do Piggy no topo do Resumo (parts/PiggyBand.tsx).
//   · o sorteio (lib/prompts.js): peso, e nunca a mesma opção duas visitas seguidas;
//   · fixa em todos os painéis e fora do grid (sem ✕, fora do catálogo);
//   · o clique abre a conversa do Piggy (#/piggy) com a pergunta já enviada e respondida;
//   · com backend a faixa é só pergunta (os insights de exemplo são do protótipo; os reais ficam no bloco "Piggy notou"), sem selo e sem pedido próprio;
//   · 320 e 390 sem rolagem para o lado.
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { PAINEL, PROTOTIPO, RAIZ, exigeArtefatoEmDia, servir } from "./_painel.mjs";
import { BY_PROFILE, COMMON, pick } from "../../webapp/src/dashboard/lib/prompts.js";

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
  exigeArtefatoEmDia();
  browser = await chromium.launch();
});
after(() => browser?.close());

// `sorte` fixa o Math.random (0 = sempre a primeira opção do sorteio).
async function abrir({ width = 1440, perfil = "padrao", plano = "pro", sorte = null, demo = false } = {}) {
  const ctx = await browser.newContext({ viewport: { width, height: 900 }, reducedMotion: "reduce" });
  await servir(ctx, demo ? RAIZ : undefined, { plano, perfil }); // o perfil mora no servidor (/api/v2/perfil)
  if (demo) await ctx.addInitScript((p) => localStorage.setItem("pigbank.dashboard.profile.v1", JSON.stringify(p)), perfil); // o protótipo lê o perfil daqui
  if (sorte !== null) await ctx.addInitScript((v) => { Math.random = () => v; }, sorte);
  const page = await ctx.newPage();
  const erros = [];
  page.on("pageerror", (e) => erros.push(e.message));
  await page.goto(`${demo ? PROTOTIPO : PAINEL}#/`);
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

test("insight do dia, só no protótipo: o clique abre a conversa com a pergunta dele respondida", async () => {
  const { ctx, page, erros } = await abrir({ perfil: "economizar", sorte: 0, demo: true }); // 1ª opção = 1º insight
  const chave = await faixa(page);
  const cta = await page.locator(".piggy-band-cta").innerText();
  await page.locator(".piggy-band").click();
  await page.locator(".chat .msg-piggy .msg-block").first().waitFor();
  const r = await page.evaluate(() => [location.hash, document.querySelector(".chat .msg-user").textContent, document.querySelector(".chat .msg-piggy [id$='w-cat-detalhe']") !== null]);
  await ctx.close();
  assert.equal(chave, "insight-rise"); // delivery subiu: a resposta é o detalhe da categoria
  assert.deepEqual(r, ["#/piggy", cta.match(/“(.+)”/)[1], true]);
  assert.deepEqual(erros, []);
});

test("com backend a faixa é só pergunta: nenhum insight em nenhum plano, sem selo, sem pedido próprio; a conversa abre", async () => {
  const sortes = [0, 0.1, 0.2, 0.3, 0.45, 0.6, 0.8, 0.99];
  const vistas = [];
  for (const plano of ["essencial", "plus", "pro"]) for (const sorte of sortes) {
    const { ctx, page } = await abrir({ perfil: "investir", plano, sorte });
    vistas.push([await faixa(page), await page.locator(".piggy-band").evaluate((b) => b.tagName), await page.locator(".piggy-band .selo").count()]);
    await ctx.close();
  }
  assert.ok(vistas.every(([k, tag, selo]) => !k.startsWith("insight-") && tag === "BUTTON" && selo === 0), JSON.stringify(vistas));
  // Zero pedido da faixa: o Essencial (sem o bloco "Piggy notou") não pede /insights nem /renda; com o bloco, o pedido é um só e é dele.
  const pedidos = {};
  for (const plano of ["essencial", "plus"]) {
    const { ctx, page } = await abrir({ perfil: "investir", plano, sorte: 0 });
    pedidos[plano] = [];
    page.on("request", (q) => { const p = new URL(q.url()).pathname; if (p === "/api/v2/insights" || p === "/api/v2/renda") pedidos[plano].push(p); });
    await page.reload();
    await page.locator(".piggy-band").waitFor();
    await page.waitForTimeout(500);
    if (plano === "essencial") {
      await page.locator(".piggy-band").click();
      await page.locator(".chat > .msg-piggy .msg-text").first().waitFor();
      assert.equal(await page.evaluate(() => location.hash), "#/piggy");
    }
    await ctx.close();
  }
  assert.deepEqual(pedidos, { essencial: [], plus: ["/api/v2/insights"] });
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
