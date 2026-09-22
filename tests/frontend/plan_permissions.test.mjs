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
