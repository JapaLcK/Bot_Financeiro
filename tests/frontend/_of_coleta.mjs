/**
 * Harness dos testes do acompanhamento da coleta nos Ajustes (Onda 5, PR-E):
 * of_acompanha_coleta.test.mjs (o ciclo) e of_acompanha_atualizar.test.mjs
 * (Atualizar com `still_updating`, seção visível, foco e corrida do botão).
 * O `_` no nome tira este arquivo do glob `*.test.mjs`.
 *
 * O relógio é o do Playwright, PARADO (`pauseAt`): só anda no `andar`, que
 * avança em passos e devolve a vez ao Node para a rota responder. O instante
 * de cada GET é lido do lado da página (`Date.now()` falso no `fetch`), então
 * as asserções de cadência são exatas, não janelas.
 */
import { before, after } from "node:test";
import { startServer } from "./_server.mjs";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { chromium } from "playwright";

const FRONTEND = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "frontend");
export const VIEWPORTS = [{ width: 1440, height: 900 }, { width: 390, height: 844 }];
const T0 = Date.parse("2026-10-08T12:00:00Z");
export const MIN = 60_000;
export const json = (body, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(body) });
export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Os rótulos de `_LABELS` (core/services/pluggy_health.py), copiados.
const LABEL = { updating: "Atualizando…", updated: "Atualizado", partial: "Dados parciais",
  error_recoverable: "Erro temporário", needs_user_action: "Ação necessária", no_accounts: "Sem dados" };
export const conn = (id, nome, state, detail = null) => ({
  id, institution_name: nome, provider_item_id: `item${id}`,
  last_sync_at: state === "updated" ? "2026-10-08T11:59:00Z" : null,
  ui: { state, label: LABEL[state], detail } });
export const NU = (s, d) => conn(1, "Nubank", s, d);

let ORIGIN, server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer());
                     browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

/**
 * Abre os Ajustes com o relógio parado. `responder(n)` decide o n-ésimo GET do
 * snapshot (n = 1 é o do boot): `{ conexoes }`, `{ status }`, `{ abortar }`,
 * e `segurar` (promise) para prender a resposta.
 */
export async function abrir(viewport, responder, { rotas, url } = {}) {
  const ctx = await browser.newContext({ viewport });
  const page = await ctx.newPage();
  await page.clock.install({ time: new Date(T0) });
  await page.clock.pauseAt(new Date(T0 + 1000));
  await page.addInitScript(() => {
    window.__gets = [];
    window.__toasts = [];
    window.__voo = 0;   // fetches em voo, com o corpo lido: o `andar` espera zerar
    const f = window.fetch;
    window.fetch = function (u, o) {
      const url = new URL(String((u && u.url) || u), location.href);
      const m = ((o && o.method) || "GET").toUpperCase();
      if (m === "GET" && url.pathname === "/open-finance/1") window.__gets.push(Date.now());
      window.__voo += 1;
      return f.apply(this, arguments).then(
        (r) => r.clone().text().then(() => r, () => r).finally(() => { window.__voo -= 1; }),
        (e) => { window.__voo -= 1; throw e; });
    };
    window.__ocultar = (h) => {
      Object.defineProperty(document, "hidden", { configurable: true, get: () => h });
      Object.defineProperty(document, "visibilityState", { configurable: true, get: () => (h ? "hidden" : "visible") });
      document.dispatchEvent(new Event("visibilitychange"));
    };
  });
  await page.route("**/*", (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== ORIGIN) return route.abort();
    if (/\.[a-z0-9]+$/i.test(url.pathname)) return route.continue();
    return route.fulfill(json({}));
  });
  await page.route("**/static/auth-refresh.js", (route) =>
    route.fulfill({ status: 200, contentType: "application/javascript",
                    body: readFileSync(join(FRONTEND, "static", "auth-refresh.js"), "utf8") }));
  await page.route("**/auth/validate", (route) => route.fulfill(json({ user_id: 1 })));
  await page.route("**/auth/me", (route) =>
    route.fulfill(json({ app_access: true, plan_tier: "pro", of_ui_enabled: true, of_banks_max: 5 })));
  const estado = { n: 0, emVoo: 0, maxEmVoo: 0 };
  await page.route(/\/open-finance\/1$/, async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    estado.n += 1;
    const r = responder(estado.n) || {};
    estado.emVoo += 1;
    estado.maxEmVoo = Math.max(estado.maxEmVoo, estado.emVoo);
    try {
      if (r.segurar) await r.segurar;
      if (r.abortar) return await route.abort("connectionreset");
      if (r.status) return await route.fulfill(json({ detail: "falhou" }, r.status));
      return await route.fulfill(json({ ok: true, connections: r.conexoes || [], accounts: [], transactions: [] }));
    } finally { estado.emVoo -= 1; }
  });
  if (rotas) await rotas(page);
  await page.goto(`${ORIGIN}/settings.html?view=${url || "open-finance"}`);
  await page.waitForFunction(() => window.__gets.length === 1
    && document.querySelector("#connections-list .connection-row, #connections-list .empty-state, #connections-list a"));
  await page.evaluate(() => {
    const o = window.showToast;
    window.showToast = (m, t) => { window.__toasts.push([m, t]); return o(m, t); };
  });
  page.__estado = estado;
  return page;
}

/** Espera a página assentar: nenhum fetch em voo, duas vezes seguidas. */
export async function assentar(page) {
  for (let quietas = 0; quietas < 2;) {
    await sleep(10);
    quietas = (await page.evaluate(() => window.__voo)) === 0 ? quietas + 1 : 0;
  }
}
/** Anda o relógio falso em passos; a cada passo, as respostas chegam antes do
 *  próximo. `preso: true` quando há resposta presa de propósito (não dá para
 *  esperar assentar): aí cada passo só devolve a vez por 15 ms. */
export async function andar(page, ms, passo = 1000, { preso = false } = {}) {
  for (let feito = 0; feito < ms; feito += passo) {
    await page.clock.runFor(Math.min(passo, ms - feito));
    if (preso) await sleep(15); else await assentar(page);
  }
}
export const gets = (page) => page.evaluate(() => window.__gets.map((t) => Math.round((t - window.__gets[0]) / 1000)));
export const pilulas = (page) => page.$$eval("#connections-list .connection-row .pill", (ps) => ps.map((p) => p.textContent.trim()));
export const agora = (page) => page.evaluate(() => Date.now());
