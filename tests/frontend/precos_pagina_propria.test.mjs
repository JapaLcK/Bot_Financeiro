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

// O rastreio vai para o `window.name`, que sobrevive à navegação da mesma origem
// (a página da /precos morre no `location.href`). NÃO a sessionStorage: o 401 do
// /auth/refresh do deslogado a apaga (auth-refresh.js, _limpaEstadoDoDispositivo),
// e um evento disparado antes dele sumiria — o `[]` ficaria verde sem medir. Só
// nas páginas que vendem: a /assinar e o hospedado aqui são stubs sem rastreio.
const ESPIAO = () => {
  if (!/^\/(precos\.html|continuar-compra)$/.test(location.pathname)) return;
  const anota = (nome, params) => {
    window.name = JSON.stringify([...JSON.parse(window.name || "[]"), { nome, params }]);
  };
  window.fbq = (_tipo, nome, params) => anota(nome, params);
  window.pbTrack = (nome, params, depois) => { anota(nome, params); if (depois) depois(); };
};

// UA do WebView do app: o auth-refresh.js liga window.PB_IN_APP pela substring
// PigBankApp (o mesmo mecanismo de precos_sem_plano_gratis.test.mjs).
const APP_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
  + "AppleWebKit/605.1.15 Safari/604.1 PigBankApp/1.0";

// `subs`: respostas do /billing/subscription em ordem (a última se repete).
// `cfg`: campos a mais no plans-config. `logado: false` = o visitante: /auth/me,
// /billing/subscription e o create-checkout dão 401 (este com WWW-Authenticate, e o
// /auth/refresh também 401 — o par de purchase_flow.test.mjs). `atraso`: ms antes
// de o create-checkout responder.
async function abre({ resposta, status = 200, subs = [{ active: false }],
                      intencao = null, caminho = "/precos.html", app = false,
                      cfg = {}, logado = true, atraso = 0, cfgAtraso = 0, cfgStatus = 200 }) {
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
  const nao = (route) => route.fulfill({ status: 401, contentType: "application/json", body: "{}" });
  await page.route("**/auth/me", (route) => logado ? route.fulfill({
    contentType: "application/json", body: JSON.stringify({ user_id: 42, app_access: true }),
  }) : nao(route));
  await page.route("**/auth/refresh", nao);
  await page.route("**/billing/plans-config", async (route) => {
    if (cfgAtraso) await new Promise((r) => setTimeout(r, cfgAtraso));
    return route.fulfill({ status: cfgStatus,
    contentType: "application/json",
    body: JSON.stringify({ essencial_available: true, plus_available: true, pro_available: true, ...cfg }),
  }); });
  const lidas = { sub: 0 };
  await page.route("**/billing/subscription", (route) => {
    lidas.sub += 1;
    if (!logado) return nao(route);
    return route.fulfill({
      contentType: "application/json", body: JSON.stringify(subs.length > 1 ? subs.shift() : subs[0]),
    });
  });
  await page.route("**/billing/create-checkout", async (route) => {
    corpos.push(JSON.parse(route.request().postData() || "{}"));
    if (atraso) await new Promise((r) => setTimeout(r, atraso));
    if (!logado) return route.fulfill({ status: 401, headers: { "WWW-Authenticate": "Bearer" },
                                        contentType: "application/json", body: "{}" });
    return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(resposta) });
  });
  const navegou = [];
  page.on("framenavigated", (frame) => {
    if (frame === page.mainFrame()) navegou.push(new URL(frame.url()).pathname);
  });
  for (const stub of ["**/assinar?*", "**/hospedado-ok", "**/cadastro?*"]) {
    await page.route(stub, (route) => route.fulfill({
      contentType: "text/html", body: "<html><body>destino</body></html>",
    }));
  }
  await page.goto(`${ORIGIN}${caminho}`);
  return { page, corpos, lidas, navegou };
}

const rastroTodo = (page) => page.evaluate(() => JSON.parse(window.name || "[]"));
// Só os eventos de checkout (o ViewContent da abertura não conta).
const rastro = async (page) => (await rastroTodo(page)).filter((e) => /checkout/i.test(e.nome));
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

// ── Deslogado com a flag (CHECKOUT_PAGINA_PROPRIA → `pagina_propria` no plans-config) ──
// Conta + pagamento na /assinar, sem /cadastro. Sem `origem` (a /assinar cria a
// sessão dela) e sem `preservePurchaseForAuth`: uma intenção em `awaiting_auth`
// faria um /login posterior abrir um 2º checkout pela /continuar-compra.

// Clica no plano depois de o plans-config chegar (é ele que liga o desvio).
async function clicaDeslogado(page, plano) {
  await page.waitForFunction(() => window.pbPixState !== undefined);
  await page.click(`#plans-v2 [data-plan-btn="${plano}"]`);
}

test("deslogado + pagina_propria → /assinar?plano=plus&ciclo=monthly, sem /cadastro nem intenção", async () => {
  const { page, corpos, navegou } = await abre({ logado: false, cfg: { pagina_propria: true } });
  await clicaDeslogado(page, "plus");
  await page.waitForURL("**/assinar?*");
  const url = new URL(page.url());
  assert.equal(url.pathname + url.search, "/assinar?plano=plus&ciclo=monthly");
  assert.deepEqual(corpos, [{ interval: "monthly", pagina: true, plan: "plus" }]);
  // O espião sobreviveu ao 401 (o ViewContent da abertura está lá): o `[]` mede.
  assert.ok((await rastroTodo(page)).some((e) => e.nome === "ViewContent"), "o espião perdeu o rastro");
  assert.deepEqual(await rastro(page), []);
  const i = await intencao(page);
  assert.notEqual(i && i.status, "awaiting_auth", "intenção deixada para um /login posterior");
  assert.equal(navegou.includes("/cadastro"), false, navegou);
  await page.close();
});

test("deslogado + pagina_propria, anual + Pro → /assinar?plano=pro&ciclo=annual", async () => {
  const { page } = await abre({ logado: false, cfg: { pagina_propria: true } });
  await page.waitForFunction(() => window.pbPixState !== undefined);
  await page.click("#cycle-annual");
  await clicaDeslogado(page, "pro");
  await page.waitForURL("**/assinar?*");
  const url = new URL(page.url());
  assert.equal(url.pathname + url.search, "/assinar?plano=pro&ciclo=annual");
  await page.close();
});

for (const [nome, cfg] of [["pagina_propria:false", { pagina_propria: false }], ["sem o campo", {}]]) {
  test(`controle positivo: deslogado com ${nome} → /cadastro?compra=1 com a intenção, como hoje`, async () => {
    const { page } = await abre({ logado: false, cfg });
    await clicaDeslogado(page, "plus");
    await page.waitForURL("**/cadastro?compra=1");
    const i = await intencao(page);
    assert.equal(i.status, "awaiting_auth");
    assert.equal(i.plan, "plus");
    assert.equal(i.method, "card");
    await page.close();
  });
}

test("logado + pagina_propria: a resposta `pagina` segue com origem=precos e checkout_started", async () => {
  const { page } = await abre({ cfg: { pagina_propria: true },
                                resposta: { pagina: true, client_secret: "cs_x", extras: [] } });
  await page.waitForSelector('#plans-v2 [data-plan-btn="plus"]');
  await Promise.all([page.waitForURL("**/assinar?*"), page.click('#plans-v2 [data-plan-btn="plus"]')]);
  const url = new URL(page.url());
  assert.equal(url.pathname + url.search, "/assinar?plano=plus&ciclo=monthly&origem=precos");
  assert.equal((await intencao(page)).status, "checkout_started");
  await page.close();
});

test("/continuar-compra com 401 + pagina_propria → fica em 'Entrar novamente', sem ir à /assinar", async () => {
  // O atraso deixa o plans-config chegar antes do 401: sem ele o desvio nem
  // teria o cfg e o caso passaria sem a guarda `!resuming`.
  const { page, navegou } = await abre({ logado: false, cfg: { pagina_propria: true }, atraso: 300,
    intencao: { plan: "plus", cycle: "monthly" }, caminho: "/continuar-compra" });
  await page.waitForFunction(() =>
    document.getElementById("purchase-continuation-retry")?.textContent === "Entrar novamente");
  assert.equal(await page.evaluate(() => window.pbPixState?.cfg?.pagina_propria), true,
    "o plans-config não tinha chegado: o caso não mede a guarda");
  await page.waitForTimeout(600);
  assert.equal(new URL(page.url()).pathname, "/continuar-compra");
  assert.equal(navegou.includes("/assinar"), false, navegou);
  await page.close();
});

test("app deslogado + pagina_propria → /assinar, e o corpo não leva `pagina`", async () => {
  const { page, corpos } = await abre({ logado: false, cfg: { pagina_propria: true }, app: true });
  // Antes do clique: depois dele a navegação pode destruir o contexto da página.
  assert.equal(await page.evaluate(() => window.PB_IN_APP === true), true);
  await clicaDeslogado(page, "plus");
  await page.waitForURL("**/assinar?*");
  const url = new URL(page.url());
  assert.equal(url.pathname + url.search, "/assinar?plano=plus&ciclo=monthly");
  assert.deepEqual(corpos, [{ interval: "monthly", plan: "plus" }]);
  await page.close();
});

test("Pix deslogado + pagina_propria → /cadastro?compra=1 com intenção pix, como hoje", async () => {
  const { page } = await abre({ logado: false, cfg: { pagina_propria: true, pix_annual_available: true } });
  await page.waitForTimeout(650);
  await page.click("#cycle-annual");
  await page.click('[data-pix-cta="plus"]');
  await page.waitForURL("**/cadastro?compra=1");
  const i = await intencao(page);
  assert.equal(i.method, "pix");
  assert.equal(i.status, "awaiting_auth");
  await page.close();
});

// Clique ANTES de o plans-config chegar: `cfgPlanos` ainda é o `null` do escopo do
// script. Sem a declaração lá, `assinarDeslogado` lança ReferenceError e o botão
// morre em "Carregando…" — o caminho de antes (/cadastro) tem de seguir valendo.
for (const [nome, cfgStatus] of [["atrasado", 200], ["com 500", 500]]) {
  test(`deslogado com o plans-config ${nome}: clique antes dele → /cadastro?compra=1, como hoje`, async () => {
    const { page } = await abre({ logado: false, cfg: { pagina_propria: true }, cfgAtraso: 3000, cfgStatus });
    await page.waitForSelector('#plans-v2 [data-plan-btn="plus"]');
    await page.click('#plans-v2 [data-plan-btn="plus"]');
    await page.waitForURL("**/cadastro?compra=1");
    const i = await intencao(page);
    assert.equal(i.status, "awaiting_auth");
    assert.equal(i.plan, "plus");
    await page.close();
  });
}
