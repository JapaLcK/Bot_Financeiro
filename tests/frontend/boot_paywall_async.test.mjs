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

// Mês do seed, lido UMA vez: o seed (dentro do navegador) e a expectativa
// (aqui) têm de ser a mesma chave, ou uma suíte que começa segundos antes da
// virada de mês semeia um mês e espera outro.
const _SEED_D = new Date();
const SEED_ANO = _SEED_D.getFullYear();
const SEED_MES = String(_SEED_D.getMonth() + 1).padStart(2, "0");

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
async function bootApp({ me, meDelayMs = 400, meStatus = 200, wsMode = "silent",
                         seedSnap = false, meAfter = null }) {
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  let meCalls = 0;
  if (seedSnap) {
    // Snapshot que a "aba anterior" gravou (USER_ID 42, mês corrente) — o que
    // o restoreSnapshotFromSession consome no boot. pb_home_42 é o par dele: a
    // MESMA aba pinta a /home com essa chave, e o clearSessionSnapshots limpava
    // só o pb_snap_.
    // O mês vem de FORA (SEED_ANO/SEED_MES): calculá-lo aqui dentro daria uma
    // segunda `new Date()`, e uma suíte que começa segundos antes da virada de
    // mês semearia um mês e esperaria outro (Codex, este PR).
    await page.addInitScript(([ano, mes]) => {
      if (location.pathname !== "/app") return;
      sessionStorage.setItem(`pb_snap_42_${ano}_${mes}`, JSON.stringify({
        year: ano, month: Number(mes), launches: [],
        launches_pagination: { page: 1, filter_type: "all", query: "" },
      }));
      sessionStorage.setItem("pb_home_42", JSON.stringify({ userId: 42, snapshot: { total: 1 } }));
    }, [SEED_ANO, SEED_MES]);
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
        } else if (mode === "open-then-reject") {
          // 1º socket abre (plano ainda válido) e cai; os seguintes são
          // rejeitados pelo gate (plano revogado no meio da sessão).
          const primeiro = window._wsCount === 1;
          setTimeout(() => {
            if (primeiro) {
              this.readyState = 1; this.onopen?.();
              setTimeout(() => { this.readyState = 3; this.onclose?.({ code: 1006 }); }, 300);
            } else {
              this.readyState = 3; this.onclose?.({ code: 1006 });
            }
          }, 30);
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
      meCalls += 1;
      // meAfter: resposta das chamadas seguintes (assinatura revogada no meio
      // da sessão) — a 1ª continua sendo `me`.
      const corpo = meCalls === 1 || !meAfter ? me : meAfter;
      return route.fulfill({ status: meStatus, contentType: "application/json", body: JSON.stringify(corpo) });
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

// Os dois prefixos que a aba usa para pintar dinheiro na hora.
const snapKeys = (page) => page.evaluate(
  () => Object.keys(sessionStorage)
              .filter((k) => k.startsWith("pb_snap_") || k.startsWith("pb_home_")).sort());

// As duas chaves do seed, por NOME. Contar (`length === 2`) passava com as duas
// semeadas apagadas e duas outras no lugar — e isso é alcançável: qualquer
// payload entregue pelo WS grava um pb_snap_ novo pelo persistSnapshotToSession.
// Já ordenadas ("pb_home_" < "pb_snap_"), como o snapKeys devolve.
const SEED_KEYS = ["pb_home_42", `pb_snap_42_${SEED_ANO}_${SEED_MES}`];

test("paywall NEGA: pb_snap_* E pb_home_* somem — reload da aba não repinta saldo", async () => {
  const { ctx, page } = await bootApp({ me: { app_access: false }, seedSnap: true });
  await page.waitForURL("**/precos?ativar=1", { timeout: 5000 });
  const chaves = await snapKeys(page);
  assert.deepEqual(chaves, [], `snapshot sobreviveu ao veredito negativo: ${chaves}`);
  await ctx.close();
});

test("paywall APROVA: restore intacto (nenhum gate novo no caminho quente)", async () => {
  const { ctx, page } = await bootApp({ me: { app_access: true }, seedSnap: true });
  await page.waitForFunction(() => !!window.PBRefresh, { timeout: 5000 });
  const chaves = await snapKeys(page);
  assert.deepEqual(chaves, SEED_KEYS,
                   "as chaves semeadas não podem ser apagadas no caminho aprovado");
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

test("plano revogado no meio da sessão: revalida, redireciona e PARA de reconectar", async () => {
  // Codex-8: socket abre com plano válido; a assinatura é revogada e as
  // reconexões passam a ser rejeitadas (1006, pré-accept — o cliente não lê
  // o motivo). Antes: retry fixo de 3 s para sempre, /auth/me nunca refeito.
  const { ctx, page } = await bootApp({
    me: { app_access: true }, meAfter: { app_access: false },
    meDelayMs: 30, wsMode: "open-then-reject",
  });
  await page.waitForURL("**/precos?ativar=1", { timeout: 15000 });
  const antes = await page.evaluate(() => window._wsCount || 0);
  await page.waitForTimeout(5000);
  const depois = await page.evaluate(() => window._wsCount || 0);
  assert.equal(depois, antes, `continuou reconectando após o veredito: ${antes} -> ${depois}`);
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
