/**
 * Boot do dashboard sem esperar o /auth/me (O1-4) — e o paywall continua valendo.
 *
 * Antes, o IIFE de boot aguardava validate E me antes do connect(): o WebSocket
 * (que traz o snapshot que pinta a tela) pagava a latência do /auth/me inteira.
 * Agora o connect() dispara com o USER_ID em mãos e o bloco do /me vira .then.
 *
 * O que NÃO pode regredir (fail-closed de dinheiro): usuário sem plano
 * continua caindo em /precos quando o /me chegar — mesmo com o /me lento.
 *
 * Rodar:  npm run test:frontend
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { chromium } from "playwright";

const FRONTEND = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "frontend");
const DASHBOARD_JS = readFileSync(join(FRONTEND, "dashboard.js"), "utf8");

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
<!-- o boot chama updateInvestmentRateHint/TaxHint antes do connect(); eles
     leem .value destes campos e um throw ali abortaria o IIFE inteiro -->
<select id="inv-period"><option value="pct_cdi" selected></option></select>
<span id="inv-rate-label"></span><input id="inv-rate">
<select id="inv-asset-type"><option value="CDB" selected></option></select>
<select id="inv-frequency"><option value="none" selected></option></select>
<input id="inv-issuer"><input id="inv-name"><span id="inv-tax-hint"></span>
<script src="/dashboard.js"></script>
</body></html>`;

let browser;
before(async () => { browser = await chromium.launch(); });
after(async () => { await browser?.close(); });

// Sobe uma página REAL (URL http, pra location.replace funcionar) com rotas
// stubadas: validate responde na hora; /auth/me demora ME_DELAY_MS.
// wsMode: "silent" = conecta e fica (default) | "reject" = handshake cai 30ms
// depois (gate/outage) | "open" = onopen dispara (sessão que JÁ abriu).
async function bootApp({ me, meDelayMs = 400, meStatus = 200, wsMode = "silent", seedSnap = false }) {
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  if (seedSnap) {
    // Snapshot que a "aba anterior" gravou (USER_ID 42, mês corrente) — o que
    // o restoreSnapshotFromSession consome no boot.
    await page.addInitScript(() => {
      if (location.pathname !== "/app") return;
      const d = new Date();
      const key = `pb_snap_42_${d.getFullYear()}_${String(d.getMonth() + 1).padStart(2, "0")}`;
      sessionStorage.setItem(key, JSON.stringify({
        year: d.getFullYear(), month: d.getMonth() + 1, launches: [],
        launches_pagination: { page: 1, filter_type: "all", query: "" },
      }));
    });
  }
  await page.addInitScript((mode) => {
    window._t0 = Date.now();
    window._wsCreatedAt = null;
    window._wsCount = 0;
    window.WebSocket = class FakeWS {
      static OPEN = 1;
      constructor() {
        window._ws = this; window._wsCount++;
        window._wsCreatedAt = Date.now() - window._t0; this.readyState = 1;
        if (mode === "reject") {
          setTimeout(() => { this.readyState = 3; this.onclose?.({ code: 1006 }); }, 30);
        } else if (mode === "open") {
          setTimeout(() => { this.onopen?.(); }, 30);
        }
      }
      close() { this.readyState = 3; } send() {}
    };
  }, wsMode);
  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/app") {
      return route.fulfill({ contentType: "text/html; charset=utf-8", body: PAGE_HTML });
    }
    if (url.pathname === "/dashboard.js") {
      return route.fulfill({ contentType: "application/javascript; charset=utf-8", body: DASHBOARD_JS });
    }
    if (url.pathname === "/auth/validate") {
      return route.fulfill({ contentType: "application/json", body: JSON.stringify({ user_id: 42 }) });
    }
    if (url.pathname === "/auth/me") {
      await new Promise((r) => setTimeout(r, meDelayMs));
      return route.fulfill({ status: meStatus, contentType: "application/json", body: JSON.stringify(me) });
    }
    if (url.pathname.startsWith("/precos")) {
      return route.fulfill({ contentType: "text/html", body: "<title>precos</title>PRECOS" });
    }
    // /auth/dashboard-profile, /history, /insights, etc.: pendura (nunca resolve)
    return new Promise(() => {});
  });
  await page.goto("http://pb.test/app", { waitUntil: "domcontentloaded" });
  return { ctx, page };
}

test("sem plano ativo: continua caindo em /precos quando o /me chega", async () => {
  const { ctx, page } = await bootApp({ me: { app_access: false } });
  await page.waitForURL("**/precos?ativar=1", { timeout: 5000 });
  assert.match(page.url(), /\/precos\?ativar=1/);
  await ctx.close();
});

test("cadastro sem plano escolhido: cai em /precos?escolha=1", async () => {
  const { ctx, page } = await bootApp({ me: { needs_plan_selection: true } });
  await page.waitForURL("**/precos?escolha=1", { timeout: 5000 });
  await ctx.close();
});

test("paywall NEGA: pb_snap_* some — reload da aba não repinta saldo", async () => {
  const { ctx, page } = await bootApp({ me: { app_access: false }, seedSnap: true });
  await page.waitForURL("**/precos?ativar=1", { timeout: 5000 });
  const chaves = await page.evaluate(
    () => Object.keys(sessionStorage).filter((k) => k.startsWith("pb_snap_")));
  assert.deepEqual(chaves, [], `snapshot sobreviveu ao veredito negativo: ${chaves}`);
  await ctx.close();
});

test("paywall APROVA: restore intacto (nenhum gate novo no caminho quente)", async () => {
  const { ctx, page } = await bootApp({ me: { app_access: true }, seedSnap: true });
  await page.waitForFunction(() => !!window.PBRefresh, { timeout: 5000 });
  const chaves = await page.evaluate(
    () => Object.keys(sessionStorage).filter((k) => k.startsWith("pb_snap_")));
  assert.equal(chaves.length, 1, "snapshot de sessão não pode ser apagado no caminho aprovado");
  await ctx.close();
});

test("handshake rejeitado + /auth/me 500: retry com backoff, sockets ≤2 em 15s", async () => {
  // Codex-2 do PR #218: sem o backoff eram ~5 sockets em 15s (3s fixos),
  // martelando /ws para sempre numa outage de auth.
  const { ctx, page } = await bootApp({ me: { detail: "boom" }, meStatus: 500, meDelayMs: 50, wsMode: "reject" });
  await new Promise((r) => setTimeout(r, 15000));
  const n = await page.evaluate(() => window._wsCount);
  assert.ok(n <= 2, `${n} sockets em 15s — retry sem backoff (esperado ≤2)`);
  await ctx.close();
});

test("queda transitória DEPOIS de aberto: reconexão legítima segue em ~3s", async () => {
  const { ctx, page } = await bootApp({ me: { app_access: true }, meDelayMs: 50, wsMode: "open" });
  await page.waitForFunction(() => window._wsCount === 1, { timeout: 5000 });
  await page.waitForTimeout(100); // deixa o onopen (30ms) rodar
  await page.evaluate(() => { window._ws.onclose({ code: 1006 }); }); // queda
  await page.waitForFunction(() => window._wsCount >= 2, { timeout: 4500 });
  const n = await page.evaluate(() => window._wsCount);
  assert.ok(n >= 2, "socket que já abriu tem que reconectar em ~3s");
  await ctx.close();
});

test("connect() dispara ANTES de o /auth/me resolver (não paga mais essa RTT)", async () => {
  const { ctx, page } = await bootApp({ me: { app_access: true }, meDelayMs: 600 });
  await page.waitForFunction(() => window._wsCreatedAt !== null, { timeout: 5000 });
  const wsAt = await page.evaluate(() => window._wsCreatedAt);
  assert.ok(wsAt < 600, `WebSocket só nasceu em ${wsAt}ms — ainda espera o /auth/me (600ms)`);
  // e o PBRefresh (contrato do puxar-pra-atualizar) só nasce depois do /me:
  await page.waitForFunction(() => !!window.PBRefresh, { timeout: 5000 });
  await ctx.close();
});
