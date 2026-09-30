/**
 * As duas portas de entrada do /painel (dashboard v2) fora dele.
 *
 *  · /app: "Painel novo (beta)" no menu da conta aparece só com `dashboard_v2_enabled`
 *    true no /auth/me e fora do app. O display é medido computado: a `.user-menu-link`
 *    declara `display:flex`, que anula o `hidden` se nada o reafirmar.
 *  · /login: `?next=/painel` com sessão viva leva ao /painel; um `next` fora da
 *    allowlist cai no destino padrão (controle: sem ele a allowlist aceitaria tudo).
 *
 * Rodar:  npm run test:frontend
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { startServer } from "./_server.mjs";

const UA_APP = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 PigBankApp/1.0";
let ORIGIN, server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer());
                     browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

const json = (body, status = 200) => (r) => r.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

async function linkNoApp(liberado, userAgent) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 }, ...(userAgent && { userAgent }) });
  await ctx.route("**/auth/validate", json({ ok: true, user_id: 42 }));
  await ctx.route("**/auth/me", json({ app_access: true, plan_tier: "pro", dashboard_v2_enabled: liberado }));
  await ctx.route("**/auth/dashboard-profile", json({ email: "ana@x.com", plan: "pro", feature_gates: {} }));
  await ctx.route("**cdnjs.cloudflare.com/**", (r) => r.abort());
  const page = await ctx.newPage();
  await page.addInitScript(() => { window.WebSocket = class { constructor() { this.readyState = 0; } send() {} close() {} }; });
  await page.goto(`${ORIGIN}/dashboard.html`);
  // o `window.PBRefresh` nasce no fim do bloco do /auth/me (meGate): o veredito já correu
  await page.waitForFunction(() => typeof window.PBRefresh === "function", null, { timeout: 15000 });
  const r = await page.locator("#user-dropdown [data-painel-v2]").evaluate((a) => ({
    href: a.getAttribute("href"), display: getComputedStyle(a).display, texto: a.textContent.trim(),
  }));
  await ctx.close();
  return r;
}

test("/app: liberado vê o link para o /painel no menu da conta", async () => {
  const r = await linkNoApp(true);
  assert.equal(r.href, "/painel");
  assert.equal(r.texto, "Painel novo (beta)");
  assert.notEqual(r.display, "none");
});

test("/app: não liberado não vê o link", async () => {
  assert.equal((await linkNoApp(false)).display, "none");
});

test("/app no app (UA PigBankApp): liberado não vê o link", async () => {
  assert.equal((await linkNoApp(true, UA_APP)).display, "none");
});

async function loginCom(next) {
  const ctx = await browser.newContext();
  await ctx.route("**/auth/validate", json({ ok: true, user_id: 42 }));
  const pagina = (r) => r.fulfill({ contentType: "text/html", body: "<h1>destino</h1>" });
  await ctx.route((u) => u.pathname === "/painel" || u.pathname === "/home", pagina);
  const page = await ctx.newPage();
  await page.goto(`${ORIGIN}/login.html?next=${encodeURIComponent(next)}`);
  // Compara o pathname: "**/painel" casaria a query ?next=/painel do próprio login.
  await page.waitForURL((u) => u.pathname !== "/login.html", { timeout: 15000 });
  const destino = new URL(page.url()).pathname;
  await ctx.close();
  return destino;
}

test("/login com sessão viva e ?next=/painel vai ao /painel", async () => {
  assert.equal(await loginCom("/painel"), "/painel");
});

test("controle: /login com ?next fora da allowlist vai ao destino padrão", async () => {
  assert.equal(await loginCom("/qualquer"), "/home");
});
