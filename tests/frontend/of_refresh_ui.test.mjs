/**
 * Onda 1 do Open Finance, lado do navegador. Dois invariantes:
 *
 *  1. O botão "↻ Atualizar" do Open Finance some SÓ onde o gesto de puxar
 *     existe (html.pb-app = app iOS ou PWA instalada). No mobile web ele
 *     continua sendo a única forma de atualizar — some lá e o usuário fica sem
 *     ação nenhuma.
 *  2. O veredito do refresh só fica verde para estado que ele CONHECE como bom.
 *     Estado desconhecido, pausado, removido ou sem dados nunca vira
 *     "Tudo em dia!" — no gesto do celular isso é a diferença entre o indicador
 *     verde e o âmbar com a mensagem certa.
 *  3. O dashboard reage ao `open_finance_synced` que o backend JÁ mandava e
 *     NENHUM arquivo do front tratava — e reage UMA vez por rajada: a Pluggy
 *     manda item/updated e transactions/created com segundos de diferença.
 *
 * Cada teste discrimina: sem a regra do app-mode.css o 1º dá visível/visível;
 * com o antigo `return {tone:"ok"}` de default o 2º volta "Tudo em dia!" nos
 * cinco estados; sem a branch nova do ws.onmessage o 3º conta 0 `get_month`
 * (e sem o debounce, conta 3).
 *
 * Rodar:  npm run test:frontend
 *         (um arquivo só: node --test tests/frontend/of_refresh_ui.test.mjs)
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { chromium } from "playwright";

const REPO = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const FRONTEND = join(REPO, "frontend");
// porta própria: 8899 é do fanout, 8901 do hiw_rail, 8903 dos modais — o
// `node --test` roda os arquivos em PARALELO e duas suítes na mesma porta se matam.
const PORT = Number(process.env.PB_OF_TEST_PORT || 8905);
const ORIGIN = `http://127.0.0.1:${PORT}`;

const SAFARI = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Safari/604.1";
const APP_UA = SAFARI + " PigBankApp/1.0";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const json = (body) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });

async function startServer() {
  const proc = spawn("python3", ["-m", "http.server", String(PORT), "--bind", "127.0.0.1",
                                 "--directory", FRONTEND], { stdio: "ignore" });
  for (let i = 0; i < 100; i++) {
    await sleep(100);
    if (proc.exitCode !== null) throw new Error(`http.server morreu (porta ${PORT} ocupada?)`);
    try { if ((await fetch(`${ORIGIN}/settings.html`)).ok) return proc; } catch { /* subindo */ }
  }
  proc.kill();
  throw new Error(`http.server não subiu em ${ORIGIN}`);
}

async function waitFor(cond, what, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await cond()) return;
    await sleep(50);
  }
  throw new Error(`timeout esperando: ${what}`);
}

let server, browser;
before(async () => { server = await startServer(); browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

/** Página com as rotas de API neutralizadas (asset vai pro http.server). */
async function newPage(contextOpts = {}) {
  const ctx = await browser.newContext({ ...contextOpts });
  const page = await ctx.newPage();
  await page.route("**/*", (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== ORIGIN) return route.abort();     // CDN (pluggy, chart.js) fora
    if (/\.[a-z0-9]+$/i.test(url.pathname)) return route.continue();
    return route.fulfill(json({}));
  });
  await page.route("**/static/auth-refresh.js", (route) =>
    route.fulfill({ status: 200, contentType: "application/javascript",
                    body: readFileSync(join(FRONTEND, "auth-refresh.js"), "utf8") }));
  await page.route("**/auth/validate", (route) => route.fulfill(json({ user_id: 1 })));
  await page.route("**/auth/me", (route) => route.fulfill(json({ app_access: true, plan_tier: "pro" })));
  page.__ctx = ctx;
  return page;
}

async function botaoVisivel(page) {
  return page.evaluate(() => {
    const b = document.getElementById("of-refresh-btn");
    if (!b) return null;
    return getComputedStyle(b).display !== "none";
  });
}

test("botão Atualizar do OF: some no modo app, fica no mobile web", async () => {
  // No app (UA PigBankApp) o app-mode.js liga html.pb-app + body.pb-page-settings,
  // que é exatamente o que a regra do app-mode.css exige.
  const noApp = await newPage({ userAgent: APP_UA, viewport: { width: 390, height: 844 },
                                hasTouch: true, isMobile: true });
  try {
    await noApp.goto(`${ORIGIN}/settings.html?view=open-finance`);
    await waitFor(() => noApp.evaluate(() => document.documentElement.classList.contains("pb-app")),
                  "html.pb-app no modo app");
    assert.equal(await botaoVisivel(noApp), false, "no app o botão tem que sumir (o gesto substitui)");
  } finally { await noApp.__ctx.close(); }

  // Mesmo aparelho, navegador comum: o botão é a única forma de atualizar.
  const noWeb = await newPage({ userAgent: SAFARI, viewport: { width: 390, height: 844 },
                               hasTouch: true, isMobile: true });
  try {
    await noWeb.goto(`${ORIGIN}/settings.html?view=open-finance`);
    await sleep(300);
    assert.equal(await noWeb.evaluate(() => document.documentElement.classList.contains("pb-app")), false);
    assert.equal(await botaoVisivel(noWeb), true, "no mobile web o botão CONTINUA");
  } finally { await noWeb.__ctx.close(); }
});

/**
 * O veredito é uma tabela de permissão: `OF_VERDICT_OK` decide quem pode ser
 * verde. O teste discrimina — com o `default: return {tone:"ok"}` que existia
 * antes, os quatro casos aqui voltavam "Tudo em dia!".
 */
test("veredito do refresh: só estado conhecido-bom fica verde", async () => {
  const page = await newPage({ viewport: { width: 390, height: 844 } });
  try {
    await page.goto(`${ORIGIN}/settings.html?view=open-finance`);
    await waitFor(() => page.evaluate(() => typeof window.refreshVerdict === "function"),
                  "refreshVerdict existir na página");

    // NUNCA verde — inclusive o estado inventado, que é o ponto do teste: o
    // backend pode ganhar um caso novo antes desta tela saber dele.
    for (const state of ["no_accounts", "paused", "removed", "item_missing",
                         "estado_que_ninguem_implementou_ainda"]) {
      const v = await page.evaluate((s) =>
        window.refreshVerdict({ ok: true, still_updating: 0, items: [{
          item_id: "i1", institution: "Nubank", state: s, label: s, detail: null }] }), state);
      assert.notEqual(v.tone, "ok", `${state} não pode ser verde (veio "${v.msg}")`);
      assert.notEqual(v.msg, "Tudo em dia!", `${state} devolveu "Tudo em dia!"`);
    }

    // Um item bom + um desconhecido: o desconhecido manda.
    const misto = await page.evaluate(() => window.refreshVerdict({ ok: true, items: [
      { item_id: "a", state: "updated" }, { item_id: "b", state: "coisa_nova" }] }));
    assert.notEqual(misto.tone, "ok", "um item desconhecido derruba o verde do outro");

    // CONTROLE POSITIVO: o caminho legítimo continua verde — sem isto o teste
    // passaria num código que recusa tudo.
    for (const bons of [["updated"], ["updated", "rate_limited"], ["rate_limited"]]) {
      const v = await page.evaluate((estados) => window.refreshVerdict({
        ok: true, still_updating: 0,
        items: estados.map((s, n) => ({ item_id: `i${n}`, state: s })) }), bons);
      assert.equal(v.tone, "ok", `${bons.join("+")} tinha que ficar verde`);
      assert.match(v.msg, /tudo em dia/i, v.msg);
    }
    // Só cooldown: verde, mas dizendo que não pediu coleta nova.
    const cooldown = await page.evaluate(() => window.refreshVerdict({
      ok: true, items: [{ item_id: "a", state: "rate_limited" }] }));
    assert.equal(cooldown.tone, "ok");
    assert.match(cooldown.msg, /acabou de atualizar/i, cooldown.msg);
  } finally { await page.__ctx.close(); }
});

test("dashboard: rajada de open_finance_synced vira UM get_month", async () => {
  const page = await newPage();
  try {
    // WebSocket falso: guarda o que a página manda e deixa a gente empurrar
    // mensagens do servidor. Chart é stub porque o CDN está bloqueado aqui.
    await page.addInitScript(() => {
      window.__sent = [];
      window.Chart = function () { return { destroy() {}, update() {}, data: {}, options: {} }; };
      window.Chart.register = () => {};
      class FakeWS {
        constructor(url) {
          this.url = url; this.readyState = 1; window.__ws = this;
          setTimeout(() => this.onopen && this.onopen(), 0);
        }
        send(data) { window.__sent.push(JSON.parse(data)); }
        close() {}
      }
      FakeWS.OPEN = 1;
      window.WebSocket = FakeWS;
    });

    await page.goto(`${ORIGIN}/dashboard.html`);
    await waitFor(() => page.evaluate(() => !!window.__ws), "o dashboard abrir o WebSocket");

    const antes = await page.evaluate(() =>
      window.__sent.filter((m) => m.type === "get_month").length);

    // três eventos em 200ms, como a Pluggy manda
    await page.evaluate(async () => {
      const msg = JSON.stringify({ type: "open_finance_synced", item_id: "item-1" });
      for (let i = 0; i < 3; i++) {
        window.__ws.onmessage({ data: msg });
        await new Promise((r) => setTimeout(r, 70));
      }
    });

    await sleep(2500);   // debounce de 1,5s + folga
    const depois = await page.evaluate(() =>
      window.__sent.filter((m) => m.type === "get_month").length);

    assert.equal(depois - antes, 1,
      `esperava 1 get_month para a rajada (antes=${antes}, depois=${depois})`);
  } finally { await page.__ctx.close(); }
});
