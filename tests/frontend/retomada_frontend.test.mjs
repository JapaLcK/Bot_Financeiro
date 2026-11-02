/**
 * PR 5 da retomada de checkout: /precos, pix-poll e /retomar/pronto com o cookie `pb_retomada`
 * (o servidor devolve `retomada: {email}` no plans-config; aqui tudo por page.route).
 *
 * Controles: negativos = desligar cada ramo novo (precos.html `cfgPlanos?.retomada`, pix-poll.js
 * `emRetomada` em pixPago e pixDesistir) deixa estes testes vermelhos; positivos = retomada null
 * continua "anonymous" (cadastro), logado continua indo à /home e o 401 sem retomada não mostra o link.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { startServer } from "./_server.mjs";
import { chromium } from "playwright";

let ORIGIN, server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer()); browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

const json = (r, corpo, status = 200) => r.fulfill({ status, contentType: "application/json", body: JSON.stringify(corpo) });
const CFG = { essencial_available: true, plus_available: true, pro_available: true, pix_annual_available: true };
const RET = { email: "l•••@dominio.com" };

async function abrirPrecos({ retomada = null, sub = [401, { detail: "x" }], poll = { status: "pending" }, pollStatus = 200, pixRet, viewport } = {}) {
  const page = await browser.newPage({ viewport: viewport || { width: 1280, height: 900 } });
  const subs = [];
  await page.route("**/auth/me", (r) => json(r, { detail: "x" }, 401));
  await page.route("**/billing/plans-config", (r) => json(r, { ...CFG, retomada }));
  await page.route("**/billing/subscription", (r) => { subs.push(1); return json(r, sub[1], sub[0]); });
  await page.route("**/billing/pix/checkout", (r) => json(r, {
    public_token: "tok_abc", qr_payload: "00020126-payload", qr_image: "", amount_cents: 19900, plan: "plus", ...(pixRet && { retomada: true }) }));
  await page.route("**/billing/pix/tok_abc", (r) => json(r, poll, pollStatus));
  for (const p of ["**/home*", "**/retomar/pronto*", "**/cadastro*", "**/login*"]) {
    await page.route(p, (r) => r.fulfill({ contentType: "text/html", body: "<html><body>destino</body></html>" }));
  }
  await page.goto(`${ORIGIN}/precos.html`);
  await page.waitForSelector("#plans-v2 .plan");
  await page.waitForFunction(() => !!window.pbPixState, null, { timeout: 10000 });
  await page.waitForFunction(() => window.pbPlanAuthState !== "unknown", null, { timeout: 10000 });
  return { page, subs };
}

async function abrirModalPix(page) {
  await page.click("#cycle-annual");
  await page.click('[data-pix-cta="plus"]');
  await page.fill(".pix-doc", "11122233344");
  await page.click('.pix-form button[type="submit"]');
}

test("/precos em retomada: estado retomada, sem /billing/subscription, Pix abre o modal", async () => {
  const { page, subs } = await abrirPrecos({ retomada: RET });
  assert.equal(await page.evaluate(() => window.pbPlanAuthState), "retomada");
  assert.equal(subs.length, 0, "consultou a assinatura de quem não tem sessão");
  await page.click("#cycle-annual");
  await page.click('[data-pix-cta="plus"]');
  await page.waitForSelector(".pix-doc");   // o modal pede o documento, não manda ao cadastro
  assert.match(page.url(), /precos/);
  await page.close();
});

test("/precos sem retomada: continua anonymous e o Pix leva ao cadastro", async () => {
  const { page, subs } = await abrirPrecos({ retomada: null });
  assert.equal(await page.evaluate(() => window.pbPlanAuthState), "anonymous");
  assert.equal(subs.length, 1);
  await page.click("#cycle-annual");
  await page.click('[data-pix-cta="plus"]');
  await page.waitForURL(/cadastro|login/, { timeout: 5000 });
  await page.close();
});

test("pix-poll em retomada: pago vai para /retomar/pronto com gw e sid", async () => {
  const { page } = await abrirPrecos({ retomada: RET, poll: { status: "paid" } });
  await abrirModalPix(page);
  await page.waitForURL(/\/retomar\/pronto/, { timeout: 10000 });
  const u = new URL(page.url());
  assert.equal(u.searchParams.get("gw"), "pix");
  assert.equal(u.searchParams.get("sid"), "tok_abc");
  await page.close();
});

test("pix-poll: plans-config perdeu a retomada mas o checkout a trouxe, pago vai para /retomar/pronto", async () => {
  // A sessão de 15 min pode vencer entre o plans-config e o POST; quem decide é a resposta do POST.
  const { page } = await abrirPrecos({ sub: [200, { active: false }], poll: { status: "paid" }, pixRet: true });
  await abrirModalPix(page);
  await page.waitForURL(/\/retomar\/pronto/, { timeout: 10000 });
  await page.close();
});

test("pix-poll logado: pago continua indo para /home?upgrade=success", async () => {
  const { page } = await abrirPrecos({ sub: [200, { active: false }], poll: { status: "paid" } });
  await abrirModalPix(page);
  await page.waitForURL(/\/home\?upgrade=success/, { timeout: 10000 });
  await page.close();
});

test("pix-poll em retomada: 401 mostra o link /login por DOM e o QR fica", async () => {
  const { page } = await abrirPrecos({ retomada: RET, pollStatus: 401, poll: { detail: "x" } });
  await abrirModalPix(page);
  const a = page.locator(".pix-status a");
  await a.waitFor({ timeout: 10000 });
  assert.equal(await a.getAttribute("href"), "/login");
  assert.match(await page.locator(".pix-status").textContent(), /Se você já pagou, entre pelo e-mail/);
  assert.equal(await page.locator(".pix-code").count(), 1, "o código saiu da tela");
  await page.close();
});

test("pix-poll sem retomada: 401 não mostra o link /login", async () => {
  const { page } = await abrirPrecos({ sub: [200, { active: false }], pollStatus: 401, poll: { detail: "x" } });
  await abrirModalPix(page);
  await page.waitForFunction(() => /Entre de novo/.test(document.querySelector(".pix-status")?.textContent || ""), null, { timeout: 10000 });
  assert.equal(await page.locator(".pix-status a").count(), 0);
  await page.close();
});

// ── /retomar/pronto ─────────────────────────────────────────────────────────
async function abrirPronto(resposta, viewport, atraso = 0) {
  const page = await browser.newPage({ viewport: viewport || { width: 1280, height: 800 } });
  await page.context().addCookies([{ name: "csrf_token", value: "c", url: ORIGIN }]);
  const posts = [];
  await page.route("**/billing/retomada/senha", async (r) => {
    posts.push(r.request().headers()["x-csrf-token"]);
    if (atraso) await new Promise((ok) => setTimeout(ok, atraso));
    return resposta === "aborta" ? r.abort() : json(r, resposta[1], resposta[0]);
  });
  await page.route("**/billing/pix/*", (r) => { posts.push("CONSULTA"); return r.abort(); });
  await page.goto(`${ORIGIN}/retomar-pronto.html?gw=pix&sid=tok_abc`);
  return { page, posts };
}

for (const [nome, resp, texto, ok] of [
  ["200", [200, { message: "Se a conta precisar, o link vai para o e-mail." }], /link vai para o e-mail/, true],
  ["401", [401, { detail: "x" }], /expirou/, false],
  ["429", [429, { detail: "x" }], /Muitos pedidos/, false],
  ["rede fora", "aborta", /Sem conexão/, false],
]) {
  test(`/retomar/pronto: ${nome}`, async () => {
    const { page, posts } = await abrirPronto(resp);
    assert.equal(await page.locator("h1").textContent(), "Pagamento recebido");
    await page.click("#enviar");
    await page.locator("#aviso.show").waitFor();
    assert.match(await page.locator("#aviso").textContent(), texto);
    assert.equal(await page.locator("#aviso").evaluate((e) => e.classList.contains("ok")), ok);
    assert.deepEqual(posts.filter((p) => p === "CONSULTA"), [], "consultou a cobrança pelo sid");
    assert.equal(posts[0], "c", "sem o CSRF no POST");
    assert.equal(await page.locator('a[href="/login"]').count(), 1);
    assert.equal(await page.locator('a[href="/recuperar-senha"]').count(), 1);
    await page.close();
  });
}

test("/retomar/pronto em 375px: sem overflow horizontal", async () => {
  const { page } = await abrirPronto([200, { message: "ok" }], { width: 375, height: 700 });
  const [sw, cw] = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]);
  assert.ok(sw <= cw, `overflow ${sw} > ${cw}`);
  await page.close();
});

test("/retomar/pronto: duplo clique manda 1 POST só (botão travado enquanto voa)", async () => {
  const { page, posts } = await abrirPronto([200, { message: "ok" }], null, 600);
  await page.locator("#enviar").dblclick({ force: true });
  await page.locator("#aviso.show").waitFor();
  assert.equal(posts.length, 1, "o duplo clique mandou " + posts.length + " POSTs");
  await page.close();
});

test("/retomar/pronto: mensagem do servidor entra como texto, não como HTML", async () => {
  const msg = "<b>oi</b><img src=x onerror=\"window.__xss=1\">";
  const { page } = await abrirPronto([200, { message: msg }]);
  await page.click("#enviar");
  await page.locator("#aviso.show").waitFor();
  assert.equal(await page.locator("#aviso").textContent(), msg);
  assert.equal(await page.locator("#aviso *").count(), 0, "criou elemento dentro do aviso");
  assert.equal(await page.evaluate(() => window.__xss), undefined);
  await page.close();
});

// ── /retomar/pronto: conversão (mesmo eventID da /home e da CAPI) ───────────
// Controle negativo: tirar `converter()` do retomar-pronto.js deixa os 3 primeiros vermelhos.
async function pixelEm(query) {
  const page = await browser.newPage();
  await page.addInitScript(() => { window.__t = []; window.fbq = (...a) => window.__t.push(a); window.gtag = (...a) => window.__t.push(a); });
  await page.goto(`${ORIGIN}/retomar-pronto.html${query}`);
  await page.locator("#enviar").waitFor();
  return page;
}
const trilha = (page) => page.evaluate(() => window.__t);

for (const [nome, q, esperado] of [
  ["cartão sem trial: Purchase uma vez", "?sid=cs_1&ev=purchase", [["track", "Purchase", { currency: "BRL" }, { eventID: "purchase_cs_1" }]]],
  ["Pix: Purchase uma vez", "?gw=pix&ev=purchase&sid=tok_abc", [["track", "Purchase", { currency: "BRL" }, { eventID: "purchase_tok_abc" }]]],
  ["cartão com trial: StartTrial + start_trial", "?sid=cs_2&ev=trial", [
    ["track", "StartTrial", { currency: "BRL" }, { eventID: "trial_cs_2" }],
    ["event", "start_trial", { transaction_id: "cs_2" }]]],
  ["sem sid (URL na mão): nada dispara", "", []],
]) {
  test(`/retomar/pronto conversão: ${nome}`, async () => {
    const page = await pixelEm(q);
    assert.deepEqual(await trilha(page), esperado);
    await page.close();
  });
}

test("/retomar/pronto conversão: reload não dispara de novo (URL limpa)", async () => {
  const page = await pixelEm("?sid=cs_1&ev=purchase");
  assert.equal(new URL(page.url()).search, "");
  await page.reload();
  assert.deepEqual(await trilha(page), [], "o reload repetiu o Purchase");
  await page.close();
});

// A /retomar-pronto também usa o assinar.css e o <main class="assinar">: o cartão de 640px e o título
// de 30px das etapas da /assinar (`.assinar-etapas`) não podem vazar para cá. Valores da main: o
// cartão de 456px do assinar.css e o h1 do site-auth.css (clamp 30–37px de 2,6vw; 32px no celular).
// Controle negativo: o seletor antigo (`.assinar:not(.largo)`) deixa os 2 casos vermelhos.
for (const [w, h, titulo] of [[1366, 768, Math.min(37, Math.max(30, 1366 * 0.026))], [390, 844, 32]]) {
  test(`/retomar/pronto em ${w}×${h}: cartão e título da main, sem o layout das etapas da /assinar`, async () => {
    const page = await browser.newPage({ viewport: { width: w, height: h } });
    await page.goto(`${ORIGIN}/retomar-pronto.html?gw=pix&sid=tok_abc`);
    const m = await page.evaluate(() => { const c = document.querySelector(".auth-card");
      return { max: getComputedStyle(c).maxWidth, h1: parseFloat(getComputedStyle(c.querySelector("h1")).fontSize) }; });
    await page.close();
    assert.equal(m.max, "456px");
    assert.ok(Math.abs(m.h1 - titulo) < 0.1, `h1 com ${m.h1}px, a main tem ${titulo}px`);
  });
}
