import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { chromium } from "playwright";
import { startServer } from "./_server.mjs";

let ORIGIN, server, browser;

before(async () => {
  ({ proc: server, origin: ORIGIN } = await startServer());
  browser = await chromium.launch();
});

after(async () => {
  await browser?.close();
  server?.kill();
});

test("plano escolhido atravessa cadastro e segue ao checkout sem voltar aos planos", async () => {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  let authenticated = false;
  const checkoutBodies = [];

  await page.route("**/auth/me", (route) => authenticated
    ? route.fulfill({ contentType: "application/json", body: JSON.stringify({
      user_id: 42, needs_plan_selection: true, app_access: false,
    }) })
    : route.fulfill({ status: 401, contentType: "application/json", body: "{}" }));
  await page.route("**/billing/plans-config", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({
      essencial_available: true,
      plus_available: true,
      pro_available: true,
      pix_annual_available: true,
    }),
  }));
  await page.route("**/billing/subscription", (route) => authenticated
    ? route.fulfill({ contentType: "application/json", body: JSON.stringify({ active: false }) })
    : route.fulfill({ status: 401, contentType: "application/json", body: "{}" }));
  await page.route("**/billing/create-checkout", async (route) => {
    checkoutBodies.push(JSON.parse(route.request().postData() || "{}"));
    if (!authenticated) return route.fulfill({ status: 401, contentType: "application/json", body: "{}" });
    await new Promise((resolve) => setTimeout(resolve, 500));
    return route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ checkout_url: `${ORIGIN}/checkout-ok` }),
    });
  });
  await page.route("**/auth/refresh", (route) => route.fulfill({ status: 401, body: "{}" }));
  await page.route("**/auth/register", (route) => route.fulfill({
    contentType: "application/json", body: "{}",
  }));
  await page.route("**/auth/verify-email", (route) => {
    authenticated = true;
    return route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ user_id: 42, dashboard_url: "/precos?escolha=1" }),
    });
  });
  await page.route("**/cadastro?*", (route) => route.fulfill({
    contentType: "text/html",
    body: fs.readFileSync("frontend/cadastro.html", "utf8"),
  }));
  await page.route("**/continuar-compra", (route) => route.fulfill({
    contentType: "text/html",
    body: fs.readFileSync("frontend/precos.html", "utf8"),
  }));
  await page.route("**/checkout-ok", (route) => route.fulfill({
    contentType: "text/html", body: "<html><body>checkout</body></html>",
  }));

  await page.goto(`${ORIGIN}/precos.html`);
  await page.click('#plans-v2 [data-plan-btn="plus"]');
  await page.waitForURL("**/cadastro?compra=1");
  await page.waitForSelector(".purchase-intent");
  assert.match(await page.textContent(".purchase-intent"), /Plus · Mensal · Cartão/);
  assert.match(await page.textContent(".purchase-intent"), /segue direto para o pagamento/);
  if (process.env.PB_CAPTURE) {
    await page.screenshot({ path: "/tmp/pigbank-cadastro-compra.png", fullPage: true });
  }

  const pending = await page.evaluate(() => window.PBPurchaseIntent.pending());
  assert.equal(pending.plan, "plus");
  assert.equal(pending.cycle, "monthly");
  assert.equal(pending.method, "card");
  assert.equal(pending.status, "awaiting_auth");

  await page.fill("#reg-name", "Cliente Teste");
  await page.fill("#reg-email", "cliente@teste.local");
  await page.fill("#reg-phone", "11999999999");
  await page.fill("#reg-password", "senha1234");
  await page.fill("#reg-confirm", "senha1234");
  await page.check("#terms");
  await page.click("#btn-register");
  await page.waitForSelector('#form-verify:not([style*="display:none"])');
  await page.fill("#verify-code", "123456");
  const navigations = [];
  page.on("framenavigated", (frame) => {
    if (frame === page.mainFrame()) navigations.push(new URL(frame.url()).pathname);
  });
  await page.click("#btn-verify");
  await page.waitForURL("**/continuar-compra");
  await page.waitForSelector("#purchase-continuation", { state: "visible" });
  assert.equal(await page.isVisible("#plans-v2"), false, "a vitrine de planos reapareceu");
  assert.match(await page.textContent("#purchase-continuation"), /Preparando seu pagamento/);
  if (process.env.PB_CAPTURE) {
    await page.screenshot({ path: "/tmp/pigbank-continuar-checkout-desktop.png", fullPage: true });
  }
  await page.waitForURL("**/checkout-ok");
  assert.ok(navigations.includes("/continuar-compra"), navigations);
  assert.equal(navigations.includes("/precos"), false, navigations);
  assert.deepEqual(checkoutBodies, [
    { interval: "monthly", plan: "plus" },
    { interval: "monthly", plan: "plus" },
  ]);
  const resumed = await page.evaluate(() => JSON.parse(sessionStorage.getItem("pb_purchase_intent_v1")));
  assert.equal(resumed.status, "checkout_started");
  await page.close();
});

test("Pix pede autenticação antes do CPF e retoma no formulário do pagamento", async () => {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  let authenticated = false;
  let pixPosts = 0;

  await page.route("**/auth/me", (route) => authenticated
    ? route.fulfill({ contentType: "application/json", body: JSON.stringify({
      user_id: 42, needs_plan_selection: true, app_access: false,
    }) })
    : route.fulfill({ status: 401, contentType: "application/json", body: "{}" }));
  await page.route("**/billing/plans-config", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({
      essencial_available: true,
      plus_available: true,
      pro_available: true,
      pix_annual_available: true,
    }),
  }));
  await page.route("**/billing/subscription", (route) => authenticated
    ? route.fulfill({ contentType: "application/json", body: JSON.stringify({ active: false }) })
    : route.fulfill({ status: 401, contentType: "application/json", body: "{}" }));
  await page.route("**/auth/refresh", (route) => route.fulfill({ status: 401, body: "{}" }));
  await page.route("**/billing/pix/checkout", (route) => {
    pixPosts += 1;
    return route.fulfill({ contentType: "application/json", body: "{}" });
  });
  await page.route("**/cadastro?*", (route) => route.fulfill({
    contentType: "text/html",
    body: fs.readFileSync("frontend/cadastro.html", "utf8"),
  }));
  await page.route("**/continuar-compra", (route) => route.fulfill({
    contentType: "text/html",
    body: fs.readFileSync("frontend/precos.html", "utf8"),
  }));

  await page.goto(`${ORIGIN}/precos.html`);
  await page.waitForTimeout(650);
  await page.click("#cycle-annual");
  await page.click('[data-pix-cta="plus"]');
  await page.waitForURL("**/cadastro?compra=1");
  assert.equal(await page.$(".pix-doc"), null, "pediu CPF antes de autenticar");
  assert.match(await page.textContent(".purchase-intent"), /Plus · Anual · Pix/);
  assert.equal(pixPosts, 0);

  authenticated = true;
  await page.goto(`${ORIGIN}/continuar-compra`);
  await page.waitForSelector(".pix-doc");
  if (process.env.PB_CAPTURE) {
    await page.screenshot({ path: "/tmp/pigbank-pix-retomado.png", fullPage: true });
  }
  assert.equal(pixPosts, 0, "retomar o formulário não pode criar uma cobrança");
  const resumed = await page.evaluate(() => window.PBPurchaseIntent.read());
  assert.equal(resumed.status, "checkout_started");
  assert.equal(resumed.method, "pix");
  await page.close();
});

test("falha ao abrir checkout tem recuperação sem revelar a vitrine de planos", async () => {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  let checkoutCalls = 0;
  await page.addInitScript(() => {
    sessionStorage.setItem("pb_purchase_intent_v1", JSON.stringify({
      version: 1,
      plan: "plus",
      cycle: "monthly",
      method: "card",
      status: "awaiting_auth",
      createdAt: Date.now(),
    }));
  });
  await page.route("**/continuar-compra", (route) => route.fulfill({
    contentType: "text/html",
    body: fs.readFileSync("frontend/precos.html", "utf8"),
  }));
  await page.route("**/billing/plans-config", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ essencial_available: true, plus_available: true, pro_available: true }),
  }));
  await page.route("**/billing/subscription", (route) => route.fulfill({ status: 500, body: "{}" }));
  await page.route("**/billing/create-checkout", (route) => {
    checkoutCalls += 1;
    return route.fulfill({
      status: 500,
      contentType: "application/json",
      body: JSON.stringify({ detail: "Pagamento indisponível por alguns instantes." }),
    });
  });

  await page.goto(`${ORIGIN}/continuar-compra`);
  await page.waitForSelector("#purchase-continuation-actions.show");
  assert.equal(checkoutCalls, 1);
  assert.equal(await page.isVisible("#plans-v2"), false);
  assert.match(await page.textContent("#purchase-continuation"), /Pagamento indisponível/);
  assert.equal(await page.isVisible("#purchase-continuation-retry"), true);
  if (process.env.PB_CAPTURE) {
    await page.screenshot({ path: "/tmp/pigbank-continuar-erro-mobile.png", fullPage: true });
  }
  await page.click("#purchase-continuation-retry");
  await page.waitForFunction(() => document.querySelector("#purchase-continuation-actions")?.classList.contains("show"));
  assert.equal(checkoutCalls, 2);
  await page.close();
});

test("onboarding confirma a compra sem criar uma etapa paralela", async () => {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  await page.addInitScript(() => {
    sessionStorage.setItem("pb_purchase_complete_v1", JSON.stringify({
      version: 1,
      plan: "plus",
      cycle: "monthly",
      method: "card",
      status: "completed",
      createdAt: Date.now(),
      completedAt: Date.now(),
    }));
  });
  await page.route("**/auth/dashboard-profile", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ user_id: 42, display_name: "Lucas" }),
  }));
  await page.route("**/onboarding/state", (route) => route.fulfill({
    contentType: "application/json",
    body: route.request().method() === "GET" ? JSON.stringify({ step: 1 }) : "{}",
  }));

  await page.goto(`${ORIGIN}/comecar.html`);
  await page.waitForSelector('[data-step="1"]:not([hidden]) .purchase-intent');
  const summary = await page.textContent('[data-step="1"] .purchase-intent');
  assert.match(summary, /Plus · Mensal · Cartão/);
  assert.match(summary, /Período grátis ativado/);
  assert.equal(await page.$$eval(".onb-step", (steps) => steps.filter((step) => !step.hidden).length), 1);
  if (process.env.PB_CAPTURE) {
    await page.screenshot({ path: "/tmp/pigbank-onboarding-compra.png", fullPage: true });
  }
  await page.close();
});
