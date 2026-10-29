/**
 * As duas portas de entrada do /painel (dashboard v2) fora dele.
 *
 *  · Links "Dashboard" (menus, CTAs, aba do app) apontam fixo ao /painel; quem decide
 *    é o servidor (`serve_painel`, coberto em tests/test_painel_destino_caminhos.py).
 *  · /login: `?next=/painel` com sessão viva leva ao /painel; um `next` fora da
 *    allowlist cai no destino padrão (controle: sem ele a allowlist aceitaria tudo).
 *
 * Rodar:  npm run test:frontend
 */
import { test, before, after } from "node:test";
import { readdirSync, readFileSync } from "node:fs";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { startServer } from "./_server.mjs";
import { PRO, LIMITE, abrir as abrirDock, estado } from "./_dock.mjs";

let ORIGIN, server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer());
                     browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

const json = (body, status = 200) => (r) => r.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

const lerHref = (page, sel) => page.locator(sel).evaluateAll((as) => as.map((a) => a.getAttribute("href")));

test("/app: o 'Dashboard' do menu da conta vai ao /painel e o 'Painel novo (beta)' não existe", async () => {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  await ctx.route("**/auth/validate", json({ ok: true, user_id: 42 }));
  await ctx.route("**/auth/me", json({ app_access: true, plan_tier: "pro", dashboard_v2_enabled: true }));
  await ctx.route("**/auth/dashboard-profile", json({ email: "ana@x.com", plan: "pro", feature_gates: {} }));
  await ctx.route("**cdnjs.cloudflare.com/**", (r) => r.abort());
  const page = await ctx.newPage();
  await page.addInitScript(() => { window.WebSocket = class { constructor() { this.readyState = 0; } send() {} close() {} }; });
  await page.goto(`${ORIGIN}/dashboard.html`);
  await page.waitForFunction(() => typeof window.PBRefresh === "function", null, { timeout: 15000 });
  assert.deepEqual(await lerHref(page, "#user-dropdown a:has-text('Dashboard')"), ["/painel"]);
  assert.equal(await page.locator("[data-painel-v2]").count(), 0);
  await ctx.close();
});

// Páginas públicas logadas (nav-auth.js): menu da conta e CTAs de cadastro do corpo.
async function publicaLogada(pagina) {
  const ctx = await browser.newContext();
  await ctx.route("**/auth/validate", json({ ok: true }));
  await ctx.route("**/auth/dashboard-profile", json({ email: "a@b.c", plan: "plus" }));
  const page = await ctx.newPage();
  await page.goto(`${ORIGIN}/${pagina}.html`);
  await page.locator("#pb-acct-btn").click();
  const r = {
    menu: await lerHref(page, "#pb-acct-dd a:has-text('Dashboard')"),
    ctas: await lerHref(page, "a:has-text('Ir para o dashboard')"),
    agentes: await lerHref(page, "a[data-app-href]"),
  };
  await ctx.close();
  return r;
}

test("páginas públicas logadas: 'Dashboard' do menu e CTA de cadastro vão ao /painel", async () => {
  const r = await publicaLogada("como-funciona");
  assert.deepEqual(r.menu, ["/painel"]);
  assert.ok(r.ctas.length > 0 && r.ctas.every((h) => h === "/painel"), JSON.stringify(r.ctas));
});

test("controle positivo: CTA com data-app-href segue o destino próprio (/agents)", async () => {
  const r = await publicaLogada("agents");
  assert.ok(r.agentes.length > 0 && r.agentes.every((h) => h === "/app?view=agentes"), JSON.stringify(r.agentes));
});

test("varredura: todo link 'Dashboard' / 'Ver tudo no dashboard' do site aponta ao /painel", async () => {
  const dir = new URL("../../frontend/", import.meta.url);
  const achados = [];
  for (const f of [...readdirSync(dir)].filter((n) => n.endsWith(".html") || n === "nav-auth.js")) {
    const src = readFileSync(new URL(f, dir), "utf8");
    for (const m of src.matchAll(/<a\b[^>]*\bhref="([^"]*)"[^>]*>([\s\S]*?)<\/a>/g)) {
      const texto = m[2].replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();
      if (/^Dashboard\b|Ver dashboard|Ver tudo no dashboard/.test(texto)) achados.push([f, m[1]]);
    }
    for (const m of src.matchAll(/label: "Dashboard", href: "([^"]*)"/g)) achados.push([f, m[1]]);
  }
  assert.ok(achados.length >= 6, `esperava ao menos 6 links, achei ${achados.length}`);
  assert.deepEqual(achados.filter(([, h]) => h !== "/painel"), []);
});

// Medido no navegador (UA do app) nos pathnames REAIS do Capacitor, /app (dashboard.html) e /home:
// a aba Dashboard (href /painel) fica ativa em /app (PAGES["/painel"] === PAGES["/app"]) e não em /home.
// Não usar /dashboard.html nem /home.html: só existem no bloco extra de localhost do PAGES.
const abaDash = (s) => s.abas.find((a) => a.label === "Dashboard");

test("app-mode.js: no dashboard a aba Dashboard (/painel) é a ativa, com aria-current=page", LIMITE, async () => {
  const { ctx, page } = await abrirDock(browser, "/app", "0", PRO);
  try {
    assert.equal(await page.locator('.pb-tab[href="/painel"].active[aria-current="page"]').count(), 1);
    assert.ok(abaDash(await estado(page)).active);
  } finally { await ctx.close(); }
});

test("controle: fora do dashboard (/home) a aba Dashboard não fica ativa", LIMITE, async () => {
  const { ctx, page } = await abrirDock(browser, "/home", "0", PRO);
  try {
    assert.equal(await page.locator('.pb-tab[href="/painel"]').count(), 1);
    assert.equal(await page.locator('.pb-tab[href="/painel"].active, .pb-tab[href="/painel"][aria-current]').count(), 0);
  } finally { await ctx.close(); }
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
