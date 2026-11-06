/**
 * PR 6b da retomada: /precos com `retomada.desconto_pct` (plans-config) mostra cheio riscado, preço com
 * desconto (vindo pronto do servidor) e selo; sem retomada a tela fica como está.
 * Controles: negativo = tirar a chamada `aplicarDescontoRetomada` deixa os testes de "com desconto"
 * vermelhos; positivo = o teste "sem retomada" prova que nada aparece e o preço de hoje segue.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { startServer } from "./_server.mjs";
import { chromium } from "playwright";

let ORIGIN, server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer()); browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

const n = (t) => t.replace(/\s+/g, " ");   // \s cobre o nbsp
const json = (r, corpo, status = 200) => r.fulfill({ status, contentType: "application/json", body: JSON.stringify(corpo) });
const RET = {
  email: "l•••@dominio.com", desconto_pct: 20, trial: true,
  precos: {
    essencial: { pct: 20, monthly: 792, annual: 7920, pix: 7920 },
    plus: { pct: 20, monthly: 1592, annual: 15920, pix: 15920 },
    pro: { pct: 20, monthly: 3992, annual: 39920, pix: 39920 },
  },
};

async function abrir(retomada, viewport) {
  const page = await browser.newPage({ viewport });
  await page.route("**/auth/me", (r) => json(r, { detail: "x" }, 401));
  await page.route("**/billing/subscription", (r) => json(r, { detail: "x" }, 401));
  await page.route("**/billing/plans-config", (r) => json(r, {
    essencial_available: true, plus_available: true, pro_available: true, trial_days: 7, retomada }));
  await page.goto(`${ORIGIN}/precos.html`);
  await page.waitForSelector("#plans-v2 .plan");
  await page.waitForFunction(() => window.pbPlanAuthState !== "unknown", null, { timeout: 10000 });
  await page.waitForTimeout(400);   // dá tempo à ilha do NumberFlow montar por cima, se for montar
  return page;
}

for (const [nome, viewport] of [["desktop", { width: 1280, height: 900 }], ["mobile", { width: 390, height: 844 }]]) {
  test(`com retomada (${nome}): cheio riscado, preço com desconto, selo e texto do trial`, async () => {
    const page = await abrir(RET, viewport);
    const card = page.locator("#plans-v2 article.plan").first();
    const mensal = card.locator("[data-price-monthly]");
    assert.equal(n(await mensal.locator("s.rm-cheio").innerText()), "R$ 9,90");
    assert.match((await mensal.innerText()).replace(/\s+/g, " "), /R\$ 7,92\s*\/mês/);
    assert.equal(await mensal.locator(".rm-selo").innerText(), "20% OFF");
    assert.equal(await mensal.locator("s.rm-cheio").evaluate((e) => getComputedStyle(e).textDecorationLine), "line-through");
    assert.ok(await mensal.isVisible(), "o preço estático tem de estar visível (NumberFlow fora)");
    assert.equal(n(await card.locator(".price-cycle-copy").innerText()), "7 dias grátis no cartão, 20% OFF na 1ª cobrança, depois R$ 9,90");
    await page.click("#cycle-annual");
    const anual = card.locator("[data-price-annual]").first();
    assert.equal(n(await anual.locator("s.rm-cheio").innerText()), "R$ 99");
    assert.match(await anual.innerText(), /R\$\s*79,20/);
    assert.equal(n(await card.locator(".price-cycle-copy").innerText()), "7 dias grátis no cartão, 20% OFF na 1ª cobrança, depois R$ 99");
    assert.equal(await card.locator(".price-eq").isVisible(), false);   // "equivale a" seria o preço cheio
    assert.equal(await page.locator("#plans-v2 .price-flow-host:visible").count(), 0);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), "overflow horizontal");
    await page.screenshot({ path: `${process.env.TMPDIR || "/tmp"}/rm-desconto-${nome}.png` });
    await page.close();
  });
}

test("tabela, anual: o 'Equivale a' sai do preço com desconto (Essencial R$ 6,60)", async () => {
  const page = await abrir(RET, { width: 1280, height: 900 });
  await page.click("#cycle-annual");
  const col = (t) => page.locator(`#cmp-v2 [data-plano="${t}"]`).first().innerText().then(n);
  const [e, p] = [await col("essencial"), await col("plus")];
  assert.match(e, /R\$ 79,20/); assert.match(e, /Equivale a R\$ 6,60\/mês/); assert.doesNotMatch(e, /8,25/);
  assert.match(p, /Equivale a R\$ 13,26\/mês/); assert.doesNotMatch(p, /16,58/);
  await page.close();
});

test("sem trial: o texto diz que o desconto é só na 1ª cobrança", async () => {
  const page = await abrir({ ...RET, trial: false }, { width: 1280, height: 900 });
  const txt = (await page.locator("#plans-v2 .price-cycle-copy").first().innerText()).replace(/\s+/g, " ");
  assert.match(txt, /^20% OFF na 1ª cobrança, depois R\$ \d/);
  await page.close();
});

test("sem retomada: nenhum risco, nenhum selo, preço de hoje", async () => {
  const page = await abrir(null, { width: 1280, height: 900 });
  assert.equal(await page.locator(".rm-cheio, .rm-selo").count(), 0);
  assert.equal(await page.locator(".rm-on").count(), 0);
  assert.match((await page.locator("#plans-v2 [data-price-monthly]").first().textContent()).replace(/\s+/g, " "), /R\$\s*9,90/);
  await page.close();
});
