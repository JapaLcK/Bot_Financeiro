// /precos com a página própria (PR 4): resposta com `pagina` → paga na /assinar
// (`origem=precos`), sem InitiateCheckout/begin_checkout aqui (a /assinar dispara
// ao montar). Sem `pagina` → o hospedado de antes, com o rastreio de antes.
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

// O rastreio vai para a sessionStorage, que sobrevive à navegação da mesma origem
// (a página da /precos morre no `location.href`). Só nas páginas que vendem: a
// /assinar e o hospedado aqui são stubs sem rastreio.
const ESPIAO = () => {
  if (!/^\/(precos\.html|continuar-compra)$/.test(location.pathname)) return;
  const anota = (nome, params) => sessionStorage.setItem("__rastro",
    JSON.stringify([...JSON.parse(sessionStorage.getItem("__rastro") || "[]"), { nome, params }]));
  window.fbq = (_tipo, nome, params) => anota(nome, params);
  window.pbTrack = (nome, params, depois) => { anota(nome, params); if (depois) depois(); };
};

// UA do WebView do app: o auth-refresh.js liga window.PB_IN_APP pela substring
// PigBankApp (o mesmo mecanismo de precos_sem_plano_gratis.test.mjs).
const APP_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
  + "AppleWebKit/605.1.15 Safari/604.1 PigBankApp/1.0";

// `subs`: respostas do /billing/subscription em ordem (a última se repete).
async function abre({ resposta, status = 200, subs = [{ active: false }],
                      intencao = null, caminho = "/precos.html", app = false }) {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 },
                                       ...(app ? { userAgent: APP_UA } : {}) });
  const corpos = [];
  await page.addInitScript(ESPIAO);
  if (intencao) {
    await page.addInitScript((i) => {
      if (location.pathname !== "/continuar-compra") return;
      sessionStorage.setItem("pb_purchase_intent_v1", JSON.stringify({
        version: 1, method: "card", status: "awaiting_auth", createdAt: Date.now(), ...i,
      }));
    }, intencao);
  }
  await page.route("**/continuar-compra", (route) => route.fulfill({
    contentType: "text/html", body: fs.readFileSync("frontend/precos.html", "utf8"),
  }));
  await page.route("**/auth/me", (route) => route.fulfill({
    contentType: "application/json", body: JSON.stringify({ user_id: 42, app_access: true }),
  }));
  await page.route("**/billing/plans-config", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ essencial_available: true, plus_available: true, pro_available: true }),
  }));
  const lidas = { sub: 0 };
  await page.route("**/billing/subscription", (route) => {
    lidas.sub += 1;
    return route.fulfill({
      contentType: "application/json", body: JSON.stringify(subs.length > 1 ? subs.shift() : subs[0]),
    });
  });
  await page.route("**/billing/create-checkout", (route) => {
    corpos.push(JSON.parse(route.request().postData() || "{}"));
    return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(resposta) });
  });
  for (const stub of ["**/assinar?*", "**/hospedado-ok"]) {
    await page.route(stub, (route) => route.fulfill({
      contentType: "text/html", body: "<html><body>destino</body></html>",
    }));
  }
  await page.goto(`${ORIGIN}${caminho}`);
  return { page, corpos, lidas };
}

// Só os eventos de checkout (o ViewContent da abertura não conta).
const rastro = (page) => page.evaluate(() => JSON.parse(sessionStorage.getItem("__rastro") || "[]")
  .filter((e) => /checkout/i.test(e.nome)));
// O rastreio do hospedado de hoje, com os parâmetros (Plus mensal = R$ 19,90).
const RASTRO_HOSPEDADO_PLUS = [
  { nome: "InitiateCheckout", params: { content_category: "plus", content_name: "monthly" } },
  { nome: "begin_checkout", params: { currency: "BRL", value: 19.9,
    items: [{ item_id: "plus", item_name: "Plus", item_category: "monthly" }] } },
];
const intencao = (page) => page.evaluate(() => JSON.parse(sessionStorage.getItem("pb_purchase_intent_v1")));

test("pagina → /assinar?plano=plus&ciclo=monthly&origem=precos, sem InitiateCheckout na /precos", async () => {
  const { page, corpos } = await abre({ resposta: { pagina: true, client_secret: "cs_x", extras: [] } });
  await page.waitForSelector('#plans-v2 [data-plan-btn="plus"]');
  await Promise.all([
    page.waitForURL("**/assinar?*"),
    page.click('#plans-v2 [data-plan-btn="plus"]'),
  ]);
  const url = new URL(page.url());
  assert.equal(url.pathname + url.search, "/assinar?plano=plus&ciclo=monthly&origem=precos");
  assert.equal(url.hash, "", "nada no fragmento");
  assert.deepEqual(corpos, [{ interval: "monthly", pagina: true, plan: "plus" }]);
  assert.deepEqual(await rastro(page), [], "a /precos disparou rastreio de checkout");
  assert.equal((await intencao(page)).status, "checkout_started");
  await page.close();
});

test("controle positivo: checkout_url → hospedado de antes, com InitiateCheckout e begin_checkout", async () => {
  // Prova que o espião enxerga o rastreio: sem isto, o `[]` acima passaria com
  // um espião quebrado.
  const { page, corpos } = await abre({ resposta: { checkout_url: `${ORIGIN}/hospedado-ok` } });
  await page.waitForSelector('#plans-v2 [data-plan-btn="plus"]');
  await Promise.all([
    page.waitForURL("**/hospedado-ok"),
    page.click('#plans-v2 [data-plan-btn="plus"]'),
  ]);
  assert.deepEqual(corpos, [{ interval: "monthly", pagina: true, plan: "plus" }]);
  assert.deepEqual(await rastro(page), RASTRO_HOSPEDADO_PLUS);
  await page.close();
});

test("app: o corpo não leva `pagina` e o checkout_url vai direto ao hospedado", async () => {
  // A /assinar no app vai ao hospedado: uma sessão `elements` criada aqui seria
  // expirada lá e trocada por outra (2 sessões por compra).
  const { page, corpos } = await abre({ resposta: { checkout_url: `${ORIGIN}/hospedado-ok` }, app: true });
  await page.waitForSelector('#plans-v2 [data-plan-btn="plus"]');
  assert.equal(await page.evaluate(() => window.PB_IN_APP === true), true,
    "a UA do app não ligou window.PB_IN_APP — o teste não mediria o app");
  await Promise.all([
    page.waitForURL("**/hospedado-ok"),
    page.click('#plans-v2 [data-plan-btn="plus"]'),
  ]);
  assert.deepEqual(corpos, [{ interval: "monthly", plan: "plus" }]);
  assert.deepEqual(await rastro(page), RASTRO_HOSPEDADO_PLUS);
  await page.close();
});

test("409 already_subscribed com pagina no corpo → modal de troca, sem navegar", async () => {
  // A tela carregou sem assinatura (botão de compra); a assinatura existe quando o
  // checkout responde — é o caminho em que o 409 chega à /precos.
  const { page, corpos, lidas } = await abre({
    status: 409,
    resposta: { detail: { error: "already_subscribed", message: "Você já possui uma assinatura ativa." } },
    subs: [{ active: false }, { active: true, plan: "plus", interval: "monthly",
                                current_period_end: "2026-11-15", scheduled_change: null }],
  });
  await page.waitForSelector('#plans-v2 [data-plan-btn="pro"]');
  // O loadPlansState consome a 1ª resposta (sem assinatura) antes do clique.
  while (lidas.sub < 1) await new Promise((r) => setTimeout(r, 50));
  await page.click('#plans-v2 [data-plan-btn="pro"]');
  await page.waitForFunction(() => document.getElementById("chg-overlay")?.style.display === "flex");
  assert.match(await page.textContent("#chg-body"), /Plus.*Pro/s);
  assert.deepEqual(corpos, [{ interval: "monthly", pagina: true, plan: "pro" }]);
  assert.equal(new URL(page.url()).pathname, "/precos.html");
  await page.close();
});

test("retomada da /continuar-compra com pagina → a mesma navegação, com plano e ciclo da intenção", async () => {
  const { page, corpos } = await abre({
    resposta: { pagina: true, client_secret: "cs_x", extras: [] },
    intencao: { plan: "pro", cycle: "annual" },
    caminho: "/continuar-compra",
  });
  await page.waitForURL("**/assinar?*");
  const url = new URL(page.url());
  assert.equal(url.pathname + url.search, "/assinar?plano=pro&ciclo=annual&origem=precos");
  assert.deepEqual(corpos, [{ interval: "annual", pagina: true, plan: "pro" }]);
  assert.deepEqual(await rastro(page), []);
  assert.equal((await intencao(page)).status, "checkout_started");
  await page.close();
});
