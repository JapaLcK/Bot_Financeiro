// O freio dos agentes (AGENTS_UI_ENABLED=0) no /painel: o /api/v2/me manda `agents_ui_enabled`.
//   · desligado: sem card em Ferramentas, sem "Agentes" no ⌘K, e o #/agentes só avisa, sem pedir /agents;
//   · ligado (o controle positivo): o card, o ⌘K e a galeria seguem lá.
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { PAINEL, exigeArtefatoEmDia, servir } from "./_painel.mjs";

let browser;
before(async () => {
  exigeArtefatoEmDia();
  browser = await chromium.launch();
});
after(() => browser?.close());

async function abrir(ligado) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: "reduce" });
  await servir(ctx);
  await ctx.route("**/api/v2/me", (r) => r.fulfill({ json: { plan_tier: "pro", agents_ui_enabled: ligado } }));
  const agents = [];
  await ctx.route(/\/agents\/\d+$/, (r) => { agents.push(r.request().url()); return r.fulfill({ status: ligado ? 200 : 404, json: ligado ? { catalog: [], can_chat: true, can_activate: true, energy_enabled: false } : { detail: "Not Found" } }); });
  await ctx.addInitScript(() => localStorage.setItem("pigbank.dashboard.profile.v1", '"padrao"'));
  const page = await ctx.newPage();
  await page.goto(`${PAINEL}#/ferramentas`);
  await page.locator(".tools li").first().waitFor();
  const cards = await page.locator(".tools li b").allTextContents();
  await page.locator(".cmd-trigger").click();
  await page.locator(".cmdk input").fill("agentes");
  const cmd = await page.locator(".cmdk").innerText();
  await page.keyboard.press("Escape");
  await page.goto(`${PAINEL}#/agentes`);
  await page.locator("#page-title", { hasText: "Agentes" }).waitFor();
  await page.waitForTimeout(500);
  const tela = { lede: await page.locator(".page-head").innerText(), faixas: await page.locator(".ag-faixa").count() };
  await ctx.close();
  return { cards, cmd, tela, agents: agents.length };
}

test("freio ligado (agents_ui_enabled false): sem card, sem ⌘K, e a tela não pede /agents", async () => {
  const r = await abrir(false);
  assert.ok(!r.cards.includes("Agentes"), JSON.stringify(r.cards));
  assert.ok(!/Agentes/.test(r.cmd), r.cmd);
  assert.match(r.tela.lede, /indisponíveis no momento/);
  assert.equal(r.tela.faixas, 0);
  assert.equal(r.agents, 0);
});

test("agentes liberados: o card, o ⌘K e a galeria seguem lá", async () => {
  const r = await abrir(true);
  assert.ok(r.cards.includes("Agentes"), JSON.stringify(r.cards));
  assert.match(r.cmd, /Agentes/);
  assert.equal(r.tela.faixas, 7);
  assert.ok(r.agents >= 1);
});
