/**
 * INVARIANTE: em frontend/settings.html o toast pinta ACIMA de qualquer overlay
 * da própria página, e ABAIXO do diálogo do modals.js.
 *
 * O defeito: `#toast` tinha `z-index: 50` — abaixo das duas overlays de 9999
 * (`.mfa-overlay`, em settings.html, e `.bankpick-overlay`, em
 * open-finance-connect.css). Toda mensagem disparada com um desses modais aberto
 * era escrita ATRÁS dele. Pesa porque são fluxos de segurança ("Código inválido"
 * no MFA, e os códigos de backup, que aparecem uma vez só) e de venda (os dois
 * toasts de open-finance-connect.js:509 e :521).
 *
 * A SONDA, e por que ela não é `getComputedStyle().zIndex`:
 *   - ler o z-index e comparar com um número mede a string que acabamos de
 *     escrever no CSS (verde por construção), e é cega para a `.bankpick-overlay`,
 *     que vive em OUTRO arquivo — quem decide é a composição das duas folhas;
 *   - `elementFromPoint` puro também não serve: `#toast` é `pointer-events: none`
 *     de propósito, então o hit-test devolve o overlay COM e SEM o conserto.
 * Daí ligar `pointer-events: auto` só durante a amostra: isso não muda a ordem de
 * PINTURA, só torna o toast elegível ao hit-test, que passa então a responder
 * "quem está por cima aqui?". A restauração ao valor anterior é obrigatória:
 * `noTopo` (modals.js:303) decide se o Esc fecha o diálogo com um
 * `elementFromPoint` no centro da tela, e um toast deixado com `auto` envenenaria
 * esse hit-test (modal_keys.test.mjs).
 *
 * Rodar: NODE_PATH=$(npm root -g) node --test tests/frontend/settings_toast_acima_dos_overlays.test.mjs
 * Precisa de `npm ci` na raiz (playwright) + `npx playwright install chromium`.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { startServer } from "./_server.mjs";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { chromium } from "playwright";

const REPO = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const FRONTEND = join(REPO, "frontend");
let ORIGIN;   // a porta é efêmera, o `before` preenche

const json = (body) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });

const BANCOS = [{ id: 201, name: "Banco do Brasil", color: "0033a0", logo: "", inv: false }];

let server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer());
                     browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

/**
 * Abre o settings na `view` pedida com o backend simulado. O catch-all vem
 * PRIMEIRO porque no Playwright a última rota registrada é a que ganha: asset com
 * extensão vai pro http.server, o resto é API e vira `{}`.
 */
async function abrir(view) {
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  await page.route("**/*", (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== ORIGIN) return route.abort();          // CDN (pluggy) fora
    if (/\.[a-z0-9]+$/i.test(url.pathname)) return route.continue();
    return route.fulfill(json({}));
  });
  await page.route("**/static/auth-refresh.js", (route) =>
    route.fulfill({ status: 200, contentType: "application/javascript",
                    body: readFileSync(join(FRONTEND, "static", "auth-refresh.js"), "utf8") }));
  await page.route("**/auth/validate", (route) => route.fulfill(json({ user_id: 1 })));
  await page.route("**/auth/me", (route) =>
    route.fulfill(json({ app_access: true, of_ui_enabled: true, of_banks_max: 2 })));
  await page.route("**/open-finance/1/connectors", (route) => route.fulfill(json({ connectors: BANCOS })));
  await page.route("**/open-finance/1", (route) =>
    route.fulfill(json({ connections: [], accounts: [], transactions: [] })));

  await page.goto(`${ORIGIN}/settings.html?view=${view}`);
  await page.waitForFunction(() => typeof window.showToast === "function");
  page.__ctx = ctx;
  return page;
}

/** Dispara o toast de verdade e espera a transição de .2s assentar. */
async function comToast(page) {
  await page.evaluate(() => window.showToast("Código inválido", "error"));
  await page.waitForFunction(() => getComputedStyle(document.getElementById("toast")).opacity === "1");
}

/** Quem o navegador pinta no CENTRO DO PRÓPRIO TOAST (ele fica em bottom:28px). */
const quemPinta = (page) => page.evaluate(() => {
  const t = document.getElementById("toast");
  const prev = t.style.pointerEvents;
  t.style.pointerEvents = "auto";          // não altera ordem de pintura
  const r = t.getBoundingClientRect();
  const el = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
  t.style.pointerEvents = prev;            // RESTAURAR: modals.js:303 usa elementFromPoint
  if (el && t.contains(el)) return "toast";
  return (el && (el.id || el.className.split(" ")[0])) || "outro";
});

// ── Overlays da própria página: o toast tem de vencer os quatro ──────────────

test("MFA setup aberto: o toast pinta na frente", async () => {
  const page = await abrir("security");
  await page.evaluate(() => window.openMfaSetupModal());
  await comToast(page);
  assert.equal(await quemPinta(page), "toast");
  await page.__ctx.close();
});

test("MFA regenerar códigos aberto: o toast pinta na frente", async () => {
  // Os códigos de backup aparecem UMA vez; um toast escondido aqui some pra sempre.
  const page = await abrir("security");
  await page.evaluate(() => window.openMfaRegenerateModal());
  await comToast(page);
  assert.equal(await quemPinta(page), "toast");
  await page.__ctx.close();
});

test("modal de atividade aberto: o toast pinta na frente", async () => {
  const page = await abrir("security");
  await page.evaluate(() => window.openActivityModal());
  await comToast(page);
  assert.equal(await quemPinta(page), "toast");
  await page.__ctx.close();
});

test("picker de bancos aberto: o toast pinta na frente", async () => {
  // A .bankpick-overlay mora em open-finance-connect.css — outro arquivo, mesmo
  // 9999. É por isso que a sonda mede composição e não a string do z-index.
  const page = await abrir("open-finance");
  await page.waitForFunction(() => {
    const b = document.getElementById("connect-btn");
    const l = document.getElementById("connections-list");
    return !!(b && b.onclick && l && l.children.length > 0);
  });
  await page.click("#connect-btn");
  await page.waitForFunction(() => {
    const el = document.getElementById("bankpick-overlay");
    return !!el && getComputedStyle(el).display !== "none";
  });
  await comToast(page);
  assert.equal(await quemPinta(page), "toast");
  await page.__ctx.close();
});

// ── Controle POSITIVO ───────────────────────────────────────────────────────

test("pig-modal aberto: o toast NÃO pode tapá-lo", async () => {
  // O conserto SOBE o toast; este caso prende o teto. Falha se alguém "melhorar"
  // 10000 para 999999 e o toast passar por cima do diálogo do modals.js (99999).
  const page = await abrir("security");
  await page.evaluate(() => { window.__confirm = window.confirmModal("Tem certeza?"); });
  await page.waitForFunction(() => !!document.querySelector(".pig-modal-overlay.open"));
  await comToast(page);
  assert.equal(await quemPinta(page), "pig-modal-overlay");
  await page.__ctx.close();
});
