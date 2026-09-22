/** Matriz real de requests e render: o básico continua visível no Essencial. */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { abrirBrowser, fecharBrowser, loadDashboardJs } from "./_dashboard_loader.mjs";

before(abrirBrowser);
after(fecharBrowser);

for (const tier of ["essencial", "plus", "pro"]) {
  test(`analytics ${tier}: busca somente capacidades disponíveis`, async () => {
    const page = await loadDashboardJs();
    const result = await page.evaluate(async tier => {
      USER_GATES = { financial_comparison: tier !== "essencial", insights: tier !== "essencial" };
      const urls = [];
      window.fetch = async url => {
        urls.push(url);
        return { ok: true, json: async () => ({ kpis: { total_income: 200 }, categories: [{ name: "mercado", total: 20 }] }) };
      };
      const data = await _fetchAnalyticsAll(3, { force: true });
      return { urls, data };
    }, tier);
    assert.equal(result.urls.length, tier === "essencial" ? 3 : 7);
    assert.equal(result.data.kpis.total_income, 200);
    assert.equal(result.data.categories[0].total, 20);
    if (tier === "essencial") {
      assert.ok(result.urls.every(url => !/evolution|weekday|patterns|insights/.test(url)));
    }
    await page.close();
  });
}

test("forecast Plus renderiza só 30 dias; Pro exibe 30, 60 e 90", async () => {
  const page = await loadDashboardJs();
  const result = await page.evaluate(() => {
    const el = document.createElement("div");
    el.id = "forecast-result";
    document.body.append(el);
    const p = { projetado: 100, tranquilo: true };
    _renderForecast({ horizons: { 30: p } });
    const plus = el.textContent;
    _renderForecast({ horizons: { 30: p, 60: p, 90: p } });
    return { plus, pro: el.textContent };
  });
  assert.match(result.plus, /Em 30 dias/);
  assert.doesNotMatch(result.plus, /Em (60|90) dias/);
  assert.match(result.pro, /Em 60 dias/);
  assert.match(result.pro, /Em 90 dias/);
  await page.close();
});

test("dashboard real oculta cards avançados e mantém categorias/estabelecimentos", async () => {
  const page = await loadDashboardJs();
  const html = readFileSync(new URL("../../frontend/dashboard.html", import.meta.url), "utf8");
  await page.evaluate(html => {
    const doc = new DOMParser().parseFromString(html, "text/html");
    document.body.append(doc.getElementById("analytics-view"));
    USER_GATES = { financial_comparison: false, insights: false };
    applyProGates();
  }, html);
  const hidden = await page.locator("[data-plan-content]").evaluateAll(els => els.map(el => el.style.display));
  assert.ok(hidden.length >= 5);
  assert.ok(hidden.every(display => display === "none"));
  assert.equal(await page.locator("#mock-category-donut").count(), 1);
  await page.evaluate(() => {
    USER_GATES = { financial_comparison: true, insights: true };
    applyProGates();
  });
  assert.ok((await page.locator("[data-plan-content]").evaluateAll(els => els.map(el => el.style.display))).every(display => display === ""));
  await page.close();
});

async function insightPage() {
  const page = await loadDashboardJs();
  await page.evaluate(() => {
    USER_ID = 1;
    document.body.insertAdjacentHTML("beforeend", '<div id="user-label"></div><div id="user-email"></div><div id="user-plan"></div><div id="piggy-insight-card" style="display:none"><div id="piggy-insight-title"></div><div id="piggy-insight-message"></div><button id="piggy-insight-cta"></button></div>');
    window.__calls = 0;
    window.fetch = () => {
      window.__calls++;
      return new Promise(resolve => { window.__resolveInsight = () => resolve({ ok: true, json: async () => ({ insights: [{ title: "Insight permitido" }] }) }); });
    };
  });
  return page;
}

for (const tier of ["essencial", "plus", "pro"]) {
  test(`perfil ${tier} após primeiro render: insight respeita gates confirmados`, async () => {
    const page = await insightPage();
    await page.evaluate(() => loadPiggyInsight());
    assert.equal(await page.evaluate(() => window.__calls), 0);
    await page.evaluate(tier => applyUserMenuState("a@test", tier, "A", { insights: tier !== "essencial" }), tier);
    assert.equal(await page.evaluate(() => window.__calls), tier === "essencial" ? 0 : 1);
    if (tier !== "essencial") {
      await page.evaluate(() => window.__resolveInsight());
      await page.waitForFunction(() => document.getElementById("piggy-insight-title").textContent === "Insight permitido");
      assert.equal(await page.locator("#piggy-insight-card").isVisible(), true);
      await page.evaluate(() => applyUserMenuState("a@test", "essencial", "A", { insights: false }));
    }
    assert.equal(await page.locator("#piggy-insight-card").isVisible(), false);
    await page.close();
  });
}

test("resposta de insight em voo não repinta card depois do downgrade", async () => {
  const page = await insightPage();
  await page.evaluate(() => {
    applyUserMenuState("a@test", "plus", "A", { insights: true });
    applyUserMenuState("a@test", "essencial", "A", { insights: false });
    window.__resolveInsight();
  });
  // Uma leitura de DOM após o evaluate deixa as promises já resolvidas assentarem.
  assert.equal(await page.locator("#piggy-insight-title").textContent(), "");
  assert.equal(await page.locator("#piggy-insight-card").isVisible(), false);
  assert.equal(await page.evaluate(() => window.__calls), 1);
  await page.close();
});
