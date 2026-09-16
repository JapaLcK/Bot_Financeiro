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
    if (!authenticated) return route.fulfill({
      status: 401,
      headers: { "WWW-Authenticate": "Bearer" },
      contentType: "application/json",
      body: "{}",
    });
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

test("fechar o QR de um Pix retomado devolve uma saída à rota técnica", async () => {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  await page.addInitScript(() => {
    if (location.pathname !== "/continuar-compra") return;
    sessionStorage.setItem("pb_purchase_intent_v1", JSON.stringify({
      version: 1,
      plan: "plus",
      cycle: "annual",
      method: "pix",
      status: "awaiting_auth",
      createdAt: Date.now(),
    }));
  });
  await page.route("**/continuar-compra", (route) => route.fulfill({
    contentType: "text/html",
    body: fs.readFileSync("frontend/precos.html", "utf8"),
  }));
  await page.route("**/precos", (route) => route.fulfill({
    contentType: "text/html",
    body: fs.readFileSync("frontend/precos.html", "utf8"),
  }));
  await page.route("**/billing/plans-config", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({
      essencial_available: true,
      plus_available: true,
      pro_available: true,
      pix_annual_available: true,
    }),
  }));
  await page.route("**/billing/pix/checkout", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({
      public_token: "pix-retomado",
      amount_cents: 19900,
      qr_payload: "000201-pix-retomado",
      expires_at: "2099-01-01T00:00:00Z",
    }),
  }));
  await page.route("**/billing/pix/pix-retomado", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ status: "pending" }),
  }));
  await page.route("**/billing/subscription", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ active: false }),
  }));

  await page.goto(`${ORIGIN}/continuar-compra`);
  await page.fill(".pix-doc", "12345678901");
  await page.click(".pix-form button[type=submit]");
  await page.waitForSelector(".pix-code");
  await page.getByRole("button", { name: "Fechar" }).click();
  await page.waitForSelector(".pix-ov", { state: "detached" });
  assert.equal(await page.isVisible("#purchase-continuation-actions"), true);
  assert.match(await page.textContent("#purchase-continuation"), /pagamento foi fechado/i);
  assert.equal(await page.isVisible("#purchase-continuation-retry"), false,
    "fechar um QR ainda pagável não deve oferecer outra cobrança");
  await page.getByRole("link", { name: "Voltar aos planos" }).click();
  await page.waitForURL("**/precos");
  await page.waitForTimeout(300);
  assert.equal(await page.evaluate(() => sessionStorage.getItem("pb_purchase_intent_v1")), null,
    "voltar aos planos deve encerrar a retomada anterior");
  assert.equal(await page.$(".pix-doc"), null,
    "voltar aos planos não deve reabrir automaticamente o Pix anterior");
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

test("sessão expirada leva ao login e mantém a compra pendente", async () => {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
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
  await page.route("**/login?*", (route) => route.fulfill({
    contentType: "text/html",
    body: fs.readFileSync("frontend/login.html", "utf8"),
  }));
  await page.route("**/billing/plans-config", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ essencial_available: true, plus_available: true, pro_available: true }),
  }));
  await page.route("**/billing/subscription", (route) => route.fulfill({ status: 500, body: "{}" }));
  await page.route("**/billing/create-checkout", (route) => route.fulfill({
    status: 401,
    headers: { "WWW-Authenticate": "Bearer" },
    contentType: "application/json",
    body: "{}",
  }));
  await page.route("**/auth/refresh", (route) => route.fulfill({ status: 401, body: "{}" }));

  await page.goto(`${ORIGIN}/continuar-compra`);
  await page.waitForSelector("#purchase-continuation-actions.show");
  assert.equal(await page.textContent("#purchase-continuation-retry"), "Entrar novamente");
  assert.deepEqual(await page.evaluate(() => {
    const intent = window.PBPurchaseIntent.pending();
    return intent && { plan: intent.plan, cycle: intent.cycle, method: intent.method };
  }), { plan: "plus", cycle: "monthly", method: "card" });
  await page.click("#purchase-continuation-retry");
  await page.waitForURL("**/login?next=%2Fcontinuar-compra");
  assert.match(await page.textContent(".purchase-intent"), /Plus · Mensal · Cartão/);
  await page.close();
});

test("sessão expirada no retry de CSRF leva ao login e preserva a compra", async () => {
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
  await page.route("**/login?*", (route) => route.fulfill({
    contentType: "text/html",
    body: fs.readFileSync("frontend/login.html", "utf8"),
  }));
  await page.route("**/billing/plans-config", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ essencial_available: true, plus_available: true, pro_available: true }),
  }));
  await page.route("**/billing/create-checkout", (route) => {
    checkoutCalls += 1;
    if (checkoutCalls === 1) return route.fulfill({ status: 403, body: "{}" });
    return route.fulfill({
      status: 401,
      headers: { "WWW-Authenticate": "Bearer" },
      contentType: "application/json",
      body: "{}",
    });
  });
  await page.route("**/auth/refresh", (route) => route.fulfill({ status: 401, body: "{}" }));

  await page.goto(`${ORIGIN}/continuar-compra`);
  await page.waitForSelector("#purchase-continuation-actions.show");
  assert.equal(checkoutCalls, 2);
  assert.equal(await page.textContent("#purchase-continuation-retry"), "Entrar novamente");
  assert.deepEqual(await page.evaluate(() => {
    const intent = window.PBPurchaseIntent.pending();
    return intent && { plan: intent.plan, cycle: intent.cycle, method: intent.method };
  }), { plan: "plus", cycle: "monthly", method: "card" });
  await page.click("#purchase-continuation-retry");
  await page.waitForURL("**/login?next=%2Fcontinuar-compra");
  assert.match(await page.textContent(".purchase-intent"), /Plus · Mensal · Cartão/);
  await page.close();
});

test("sessão expirada no Pix leva ao login e mantém plano, ciclo e meio", async () => {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  await page.addInitScript(() => {
    sessionStorage.setItem("pb_purchase_intent_v1", JSON.stringify({
      version: 1,
      plan: "plus",
      cycle: "annual",
      method: "pix",
      status: "awaiting_auth",
      createdAt: Date.now(),
    }));
  });
  await page.route("**/continuar-compra", (route) => route.fulfill({
    contentType: "text/html",
    body: fs.readFileSync("frontend/precos.html", "utf8"),
  }));
  await page.route("**/login?*", (route) => route.fulfill({
    contentType: "text/html",
    body: fs.readFileSync("frontend/login.html", "utf8"),
  }));
  await page.route("**/billing/plans-config", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({
      essencial_available: true,
      plus_available: true,
      pro_available: true,
      pix_annual_available: true,
    }),
  }));
  await page.route("**/billing/subscription", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ active: false }),
  }));
  await page.route("**/billing/pix/checkout", (route) => route.fulfill({
    status: 401,
    headers: { "WWW-Authenticate": "Bearer" },
    contentType: "application/json",
    body: "{}",
  }));
  await page.route("**/auth/refresh", (route) => route.fulfill({ status: 401, body: "{}" }));

  await page.goto(`${ORIGIN}/continuar-compra`);
  await page.fill(".pix-doc", "12345678901");
  await page.click(".pix-form button[type=submit]");
  await page.waitForSelector("#purchase-continuation-actions.show");
  assert.equal(await page.textContent("#purchase-continuation-retry"), "Entrar novamente");
  assert.deepEqual(await page.evaluate(() => {
    const intent = window.PBPurchaseIntent.pending();
    return intent && { plan: intent.plan, cycle: intent.cycle, method: intent.method };
  }), { plan: "plus", cycle: "annual", method: "pix" });
  await page.click("#purchase-continuation-retry");
  await page.waitForURL("**/login?next=%2Fcontinuar-compra");
  assert.match(await page.textContent(".purchase-intent"), /Plus · Anual · Pix/);
  await page.close();
});

test("um segundo agendamento automático não repete a retomada", async () => {
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
  await page.evaluate(() => schedulePurchaseResume());
  await page.waitForTimeout(100);
  assert.equal(checkoutCalls, 1, "um segundo agendamento repetiu o checkout automaticamente");
  await page.close();
});

test("conta já assinante entra no fluxo de troca em vez de repetir o 409", async () => {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  let changeCalls = 0;
  await page.addInitScript(() => {
    if (location.pathname !== "/continuar-compra") return;
    sessionStorage.setItem("pb_purchase_intent_v1", JSON.stringify({
      version: 1,
      plan: "pro",
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
  await page.route("**/billing/subscription", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({
      active: true,
      plan: "plus",
      interval: "monthly",
      current_period_end: "2026-10-15",
      scheduled_change: null,
    }),
  }));
  await page.route("**/billing/create-checkout", (route) => route.fulfill({
    status: 409,
    contentType: "application/json",
    body: JSON.stringify({
      detail: { error: "already_subscribed", message: "Você já possui uma assinatura ativa." },
    }),
  }));
  await page.route("**/billing/change-plan", async (route) => {
    changeCalls += 1;
    await new Promise((resolve) => setTimeout(resolve, 150));
    return route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ effective_at: "2026-10-15" }),
    });
  });
  await page.route("**/home", (route) => route.fulfill({
    contentType: "text/html",
    body: "<html><body>home</body></html>",
  }));

  await page.goto(`${ORIGIN}/continuar-compra`);
  await page.waitForFunction(() => {
    const modal = document.getElementById("chg-overlay");
    const recovery = document.getElementById("purchase-continuation-actions");
    return modal?.style.display === "flex" || recovery?.classList.contains("show");
  });
  assert.equal(await page.isVisible("#chg-overlay"), true, "não abriu o fluxo de troca de plano");
  assert.match(await page.textContent("#chg-body"), /Plus.*Pro/s);
  assert.equal(await page.isVisible("#purchase-continuation-actions"), false);
  await page.click("#chg-overlay .btn-outline");
  await page.waitForFunction(() => document.getElementById("chg-overlay")?.style.display === "none");
  assert.equal(await page.isVisible("#purchase-continuation-actions"), true,
    "cancelar a troca deixou a rota técnica sem saída");

  await page.click("#purchase-continuation-retry");
  await page.waitForFunction(() => document.getElementById("chg-overlay")?.style.display === "flex");
  await page.click("#chg-confirm");
  await page.evaluate(() => closeChangeModal());
  assert.equal(await page.isVisible("#chg-overlay"), true,
    "não deve fechar a troca enquanto o POST está pendente");
  assert.equal(await page.isVisible("#purchase-continuation-actions"), false,
    "não deve liberar uma nova tentativa durante o POST");
  await page.waitForURL("**/home", { timeout: 5000 });
  assert.equal(changeCalls, 1);
  assert.equal(await page.evaluate(() => sessionStorage.getItem("pb_purchase_intent_v1")), null);
  await page.close();
});

test("retomada do mesmo plano ativo segue para a conta sem abrir troca", async () => {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  let changeCalls = 0;
  await page.addInitScript(() => {
    if (location.pathname !== "/continuar-compra") return;
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
  await page.route("**/billing/subscription", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({
      active: true,
      plan: "plus",
      interval: "monthly",
      current_period_end: "2026-10-15",
      scheduled_change: null,
    }),
  }));
  await page.route("**/billing/create-checkout", (route) => route.fulfill({
    status: 409,
    contentType: "application/json",
    body: JSON.stringify({
      detail: { error: "already_subscribed", message: "Você já possui uma assinatura ativa." },
    }),
  }));
  await page.route("**/billing/change-plan", (route) => {
    changeCalls += 1;
    return route.fulfill({
      status: 400,
      contentType: "application/json",
      body: JSON.stringify({ detail: { error: "same_plan", message: "Mesmo plano." } }),
    });
  });
  await page.route("**/home", (route) => route.fulfill({
    contentType: "text/html",
    body: "<html><body>home</body></html>",
  }));

  await page.goto(`${ORIGIN}/continuar-compra`);
  await page.waitForFunction(() => location.pathname === "/home"
    || document.getElementById("chg-overlay")?.style.display === "flex");
  assert.equal(new URL(page.url()).pathname, "/home");
  assert.equal(changeCalls, 0, "o plano já ativo não deve chamar a troca");
  assert.equal(await page.evaluate(() => sessionStorage.getItem("pb_purchase_intent_v1")), null);
  await page.close();
});

test("sessão expirada ao confirmar troca preserva a compra e oferece novo login", async () => {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  let changeCalls = 0;
  await page.addInitScript(() => {
    if (location.pathname !== "/continuar-compra") return;
    sessionStorage.setItem("pb_purchase_intent_v1", JSON.stringify({
      version: 1,
      plan: "pro",
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
  await page.route("**/billing/subscription", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({
      active: true,
      plan: "plus",
      interval: "monthly",
      current_period_end: "2026-10-15",
      scheduled_change: null,
    }),
  }));
  await page.route("**/billing/create-checkout", (route) => route.fulfill({
    status: 409,
    contentType: "application/json",
    body: JSON.stringify({
      detail: { error: "already_subscribed", message: "Você já possui uma assinatura ativa." },
    }),
  }));
  await page.route("**/billing/change-plan", async (route) => {
    changeCalls += 1;
    await new Promise((resolve) => setTimeout(resolve, 150));
    return route.fulfill({
      status: 401,
      headers: { "WWW-Authenticate": "Bearer" },
      contentType: "application/json",
      body: JSON.stringify({ detail: "Sessão expirada." }),
    });
  });
  await page.route("**/auth/refresh", (route) => route.fulfill({ status: 401, body: "{}" }));
  await page.route("**/login?*", (route) => route.fulfill({
    contentType: "text/html",
    body: "<html><body>login</body></html>",
  }));

  await page.goto(`${ORIGIN}/continuar-compra`);
  await page.waitForFunction(() => document.getElementById("chg-overlay")?.style.display === "flex");
  await page.click("#chg-confirm");
  await page.evaluate(() => closeChangeModal());
  assert.equal(await page.isVisible("#chg-overlay"), true,
    "não deve fechar a troca enquanto a sessão é confirmada");
  assert.equal(await page.isVisible("#purchase-continuation-actions"), false,
    "não deve liberar nova tentativa antes da resposta 401");
  await page.waitForFunction(() => (
    document.getElementById("purchase-continuation-retry")?.textContent === "Entrar novamente"
  ), { timeout: 5000 });
  assert.equal(changeCalls, 1);
  assert.equal(await page.textContent("#purchase-continuation-retry"), "Entrar novamente");
  const restored = await page.evaluate(() => window.PBPurchaseIntent.pending());
  assert.equal(restored?.plan, "pro");
  assert.equal(restored?.cycle, "monthly");
  await page.click("#purchase-continuation-retry");
  await page.waitForURL("**/login?*");
  assert.equal(new URL(page.url()).searchParams.get("next"), "/continuar-compra");
  await page.close();
});

test("sessão expirada ao carregar assinatura após 409 preserva a compra", async () => {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  let subscriptionCalls = 0;
  await page.addInitScript(() => {
    if (location.pathname !== "/continuar-compra") return;
    sessionStorage.setItem("pb_purchase_intent_v1", JSON.stringify({
      version: 1,
      plan: "pro",
      cycle: "annual",
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
  await page.route("**/billing/create-checkout", (route) => route.fulfill({
    status: 409,
    contentType: "application/json",
    body: JSON.stringify({
      detail: { error: "already_subscribed", message: "Você já possui uma assinatura ativa." },
    }),
  }));
  await page.route("**/billing/subscription", (route) => {
    subscriptionCalls += 1;
    return route.fulfill({
      status: 401,
      headers: { "WWW-Authenticate": "Bearer" },
      contentType: "application/json",
      body: "{}",
    });
  });
  await page.route("**/auth/refresh", (route) => route.fulfill({ status: 401, body: "{}" }));
  await page.route("**/login?*", (route) => route.fulfill({
    contentType: "text/html",
    body: "<html><body>login</body></html>",
  }));

  await page.goto(`${ORIGIN}/continuar-compra`);
  await page.waitForSelector("#purchase-continuation-actions.show");
  assert.equal(subscriptionCalls, 1);
  assert.equal(await page.$("#chg-overlay"), null, "não deve abrir troca sem assinatura carregada");
  assert.equal(await page.textContent("#purchase-continuation-retry"), "Entrar novamente");
  const restored = await page.evaluate(() => window.PBPurchaseIntent.pending());
  assert.equal(restored?.plan, "pro");
  assert.equal(restored?.cycle, "annual");
  await page.click("#purchase-continuation-retry");
  await page.waitForURL("**/login?*");
  assert.equal(new URL(page.url()).searchParams.get("next"), "/continuar-compra");
  await page.close();
});

test("continuação não consulta assinatura nem perde a intenção em sessão expirada", async () => {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  let subscriptionCalls = 0;
  let authMeCalls = 0;
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
  await page.route("**/pix-ui.js*", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 250));
    return route.continue();
  });
  await page.route("**/billing/plans-config", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ essencial_available: true, plus_available: true, pro_available: true }),
  }));
  await page.route("**/billing/subscription", (route) => {
    subscriptionCalls += 1;
    return route.fulfill({
      status: 401,
      headers: { "WWW-Authenticate": "Bearer" },
      contentType: "application/json",
      body: "{}",
    });
  });
  await page.route("**/auth/me", (route) => {
    authMeCalls += 1;
    return route.fulfill({
      status: 401,
      headers: { "WWW-Authenticate": "Bearer" },
      contentType: "application/json",
      body: "{}",
    });
  });
  await page.route("**/auth/refresh", (route) => route.fulfill({ status: 401, body: "{}" }));
  await page.route("**/billing/create-checkout", (route) => {
    checkoutCalls += 1;
    return route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ checkout_url: `${ORIGIN}/checkout-ok` }),
    });
  });
  await page.route("**/checkout-ok", (route) => route.fulfill({
    contentType: "text/html",
    body: "<html><body>checkout</body></html>",
  }));

  await page.goto(`${ORIGIN}/continuar-compra`);
  await page.waitForFunction(() => location.pathname === "/checkout-ok"
    || document.getElementById("purchase-continuation-actions")?.classList.contains("show"));
  assert.equal(subscriptionCalls, 0, "a rota técnica consultou a assinatura em paralelo");
  assert.equal(authMeCalls, 0, "a rota técnica consultou /auth/me em paralelo");
  assert.equal(checkoutCalls, 1);
  assert.equal(new URL(page.url()).pathname, "/checkout-ok");
  await page.close();
});

for (const missingScript of ["pix-checkout.js", "pix-ui.js", "pix-poll.js"]) {
test(`falha em ${missingScript} nunca troca Pix para cartão nem trava a tela`, async () => {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  let cardCheckoutCalls = 0;
  let failedAssetRequests = 0;
  await page.addInitScript(() => {
    if (!sessionStorage.getItem("pb_purchase_intent_v1")) {
      sessionStorage.setItem("pb_purchase_intent_v1", JSON.stringify({
        version: 1,
        plan: "plus",
        cycle: "annual",
        method: "pix",
        status: "awaiting_auth",
        createdAt: Date.now(),
      }));
    }
  });
  await page.route("**/continuar-compra", (route) => route.fulfill({
    contentType: "text/html",
    body: fs.readFileSync("frontend/precos.html", "utf8"),
  }));
  await page.route(`**/${missingScript}*`, (route) => {
    failedAssetRequests += 1;
    return failedAssetRequests === 1 ? route.abort() : route.continue();
  });
  await page.route("**/billing/plans-config", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({
      essencial_available: true,
      plus_available: true,
      pro_available: true,
      pix_annual_available: true,
    }),
  }));
  await page.route("**/billing/subscription", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ active: false }),
  }));
  await page.route("**/billing/create-checkout", (route) => {
    cardCheckoutCalls += 1;
    return route.fulfill({
      status: 500,
      contentType: "application/json",
      body: JSON.stringify({ detail: "Checkout de cartão não deveria ser chamado." }),
    });
  });

  await page.goto(`${ORIGIN}/continuar-compra`);
  // A suíte de frontend roda muitos navegadores em paralelo no CI. O fallback
  // depende do DOMContentLoaded depois da falha do script, então use o teto
  // padrão do Playwright em vez de um limite curto sensível à contenção.
  await page.waitForSelector("#purchase-continuation-actions.show");
  assert.equal(cardCheckoutCalls, 0, "a intenção Pix caiu no checkout de cartão");
  assert.match(await page.textContent("#purchase-continuation"), /pagamento via Pix/i);
  assert.equal(await page.textContent("#purchase-continuation-retry"), "Recarregar pagamento");
  await page.click("#purchase-continuation-retry");
  await page.waitForSelector(".pix-doc");
  const restored = await page.evaluate(() => window.PBPurchaseIntent.read());
  assert.equal(restored?.method, "pix", "recarregar deve retomar a intenção Pix");
  assert.equal(restored?.status, "checkout_started");
  assert.equal(cardCheckoutCalls, 0, "recarregar não pode trocar Pix por cartão");
  await page.close();
});
}

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
