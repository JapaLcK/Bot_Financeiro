/**
 * Banner de trial do /app (`#trial-banner`): só aparece para quem o checkout
 * daria o teste (`trial_disponivel === true` do /billing/subscription). O teste é
 * 1 por telefone na vida; antes, todo Grátis sem trial ativo via "7 dias grátis",
 * inclusive quem já tinha usado o teste e seria cobrado na hora.
 *
 * Na dúvida não mostra: 403, erro de rede, `degraded`, `active` e campo ausente
 * ficam escondidos. No app iOS (PB_IN_APP) nem pergunta.
 *
 * Mais duas copies fora do /app: o banner do /home não promete os 7 dias e a
 * oferta do quiz diz que o teste é no cartão.
 *
 * Rodar:  node --test tests/frontend/trial_banner_elegibilidade.test.mjs
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { chromium } from "playwright";

const FRONTEND = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "frontend");
const DASHBOARD_JS = readFileSync(join(FRONTEND, "dashboard.js"), "utf8");

// Os ids que o boot lê antes do /auth/me (molde de boot_paywall_async.test.mjs).
const IDS = [
  "grid", "bgt-overlay", "bgt-input", "investment-detail-overlay",
  "investment-help-overlay", "edit-launch-overlay", "launch-overlay",
  "launch-valor", "pocket-overlay", "pocket-name", "pocket-history-overlay",
  "card-overlay", "card-name", "card-closing-day", "card-due-day",
  "bill-detail-overlay", "pay-bill-overlay", "pay-bill-receipt-overlay",
  "pay-bill-amount", "overview-heading", "launches-title", "launches-wrap",
  "charts-title", "charts-grid", "alert-banner", "last-update",
  "categories-distribution", "month-label", "btn-next", "btn-prev",
  "dot", "status-text", "refresh-btn",
];
const PAGE_HTML = `<!doctype html><html><body>
${IDS.map((i) => `<div id="${i}"></div>`).join("")}
<select id="inv-period"><option value="pct_cdi" selected></option></select>
<span id="inv-rate-label"></span><input id="inv-rate">
<select id="inv-asset-type"><option value="CDB" selected></option></select>
<select id="inv-frequency"><option value="none" selected></option></select>
<input id="inv-issuer"><input id="inv-name"><span id="inv-tax-hint"></span>
<div id="trial-banner" style="display:none">banner</div>
<script src="/dashboard.js"></script>
</body></html>`;
const LIMITE_MS = 15000;
const ME_FREE = { app_access: true, plan_tier: "free", trial: { active: false } };

let browser;
before(async () => { browser = await chromium.launch(); });
after(async () => { await browser?.close(); });

// `sub`: corpo do /billing/subscription; `status`: HTTP; `rede`: aborta a requisição.
async function banner({ sub = null, status = 200, rede = false, inApp = false }) {
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  let subCalls = 0;
  await page.addInitScript(() => {
    window.WebSocket = class { constructor() { this.readyState = 1; } close() {} send() {} };
  });
  if (inApp) await page.addInitScript(() => { window.PB_IN_APP = true; });
  await page.route("**/*", async (route) => {
    const p = new URL(route.request().url()).pathname;
    const json = (body, s = 200) => route.fulfill({ status: s, contentType: "application/json", body: JSON.stringify(body) });
    if (p === "/app") return route.fulfill({ contentType: "text/html; charset=utf-8", body: PAGE_HTML });
    if (p === "/dashboard.js") return route.fulfill({ contentType: "application/javascript", body: DASHBOARD_JS });
    if (p === "/auth/validate") return json({ user_id: 42 });
    if (p === "/auth/me") return json(ME_FREE);
    if (p === "/billing/subscription") {
      subCalls += 1;
      return rede ? route.abort() : json(sub, status);
    }
    return new Promise(() => {});  // o resto pendura
  });
  await page.goto("http://pb.test/app", { waitUntil: "domcontentloaded" });
  // O PBRefresh nasce logo depois do bloco do /me, que é quem chama o banner.
  await page.waitForFunction(() => !!window.PBRefresh, undefined, { timeout: LIMITE_MS });
  // Deixa a cadeia fetch → json → then terminar antes de ler.
  await page.evaluate(() => new Promise((r) => setTimeout(r, 300)));
  const visivel = await page.evaluate(() => document.getElementById("trial-banner").style.display === "block");
  await ctx.close();
  return { visivel, subCalls };
}

test("POSITIVO: Grátis com teste disponível vê o banner", async () => {
  const r = await banner({ sub: { active: false, trial_disponivel: true } });
  assert.deepEqual(r, { visivel: true, subCalls: 1 });
});

for (const [nome, caso] of [
  ["teste já usado (trial_disponivel:false)", { sub: { active: false, trial_disponivel: false } }],
  ["campo ausente", { sub: { active: false } }],
  ["já assinante (active:true)", { sub: { active: true, trial_disponivel: true } }],
  ["Stripe degradado", { sub: { active: false, degraded: true, trial_disponivel: true } }],
  ["403", { sub: { detail: "password_required" }, status: 403 }],
  // Corpo que mostraria o banner: só o `r.ok` o esconde.
  ["500 com corpo elegível", { sub: { active: false, trial_disponivel: true }, status: 500 }],
  ["erro de rede", { rede: true }],
]) {
  test(`esconde: ${nome}`, async () => {
    const r = await banner(caso);
    assert.equal(r.visivel, false, `banner apareceu com ${nome}`);
  });
}

test("app iOS: não mostra e nem pergunta", async () => {
  const r = await banner({ sub: { active: false, trial_disponivel: true }, inApp: true });
  assert.deepEqual(r, { visivel: false, subCalls: 0 });
});

test("/home: o banner do Free não promete os dias grátis", () => {
  const html = readFileSync(join(FRONTEND, "home.html"), "utf8");
  const m = html.match(/<div class="free-banner" id="free-banner">[\s\S]*?<\/div>\s*<a/);
  assert.ok(m, "banner #free-banner não encontrado");
  assert.doesNotMatch(m[0], /dias grátis/);
  assert.match(m[0], /PigBank\+/);  // positivo: o banner continua lá, com a oferta do plano
});

test("quiz: a oferta diz que o teste é no cartão, um por telefone", () => {
  const html = readFileSync(join(FRONTEND, "quiz", "quiz.html"), "utf8");
  const m = html.match(/var OFERTA = \[\s*"([^"]*)"/);
  assert.ok(m, "OFERTA[0] não encontrado");
  assert.match(m[1], /7 dias grátis\*\* no cartão/);
  assert.match(m[1], /um teste por número de telefone/);
});
