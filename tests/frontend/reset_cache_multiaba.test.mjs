/**
 * "Recomeçar do zero" × múltiplas abas (Codex PR #217, 7º achado).
 *
 * sessionStorage é POR ABA: a limpeza de pb_snap_ e pb_home_ no sucesso do
 * reset só alcança a aba que disparou. A aba B guarda o snapshot pré-reset e,
 * num reload pós-onboarding, o restaurava (flash de dados apagados). O
 * conserto segue o padrão multi-aba que o repo já usa (finbot_logout_at em
 * localStorage): o settings grava `finbot_reset_at` e os restores da home e
 * do dashboard descartam snapshot que não for comprovadamente POSTERIOR.
 *
 * Os testes chamam as funções REAIS das páginas (globais de script clássico)
 * por page.evaluate — sem corrida com o boot, sem reimplementar o predicado.
 *
 * CONTROLE NEGATIVO do grupo (§3): com o predicado removido (código anterior),
 * os testes de descarte ficam vermelhos — verificado via git stash na sessão.
 * POSITIVO: os casos "sem marker" e "snapshot pós-reset" provam que o restore
 * legítimo continua funcionando.
 *
 * Rodar:  npm run test:frontend
 *         (um arquivo só: node --test tests/frontend/reset_cache_multiaba.test.mjs)
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { chromium } from "playwright";

const REPO = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const FRONTEND = join(REPO, "frontend");
// porta própria: o node --test roda os arquivos em paralelo (8899/8901/8903/
// 8905/8907/8909 já têm dono).
const PORT = Number(process.env.PB_RESET_TEST_PORT || 8911);
const ORIGIN = `http://127.0.0.1:${PORT}`;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const json = (body) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });

async function startServer() {
  const proc = spawn("python3", ["-m", "http.server", String(PORT), "--bind", "127.0.0.1",
                                 "--directory", FRONTEND], { stdio: "ignore" });
  for (let i = 0; i < 100; i++) {
    await sleep(100);
    if (proc.exitCode !== null) throw new Error(`http.server morreu (porta ${PORT} ocupada?)`);
    try { if ((await fetch(`${ORIGIN}/home.html`)).ok) return proc; } catch { /* subindo */ }
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
async function newPage() {
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  await page.route("**/*", (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== ORIGIN) return route.abort();     // CDN fora
    if (/\.[a-z0-9]+$/i.test(url.pathname)) return route.continue();
    return route.fulfill(json({}));
  });
  await page.route("**/auth/validate", (route) => route.fulfill(json({ user_id: 1 })));
  page.__ctx = ctx;
  return page;
}

/** Boota a home REAL (contrato pb-nav: restoreHomeCache é closure do init —
 *  inalcançável por evaluate; o boot o chama logo após o /auth/validate
 *  stubado). Observável: o destino da chave pb_home_1, que só o ramo de
 *  descarte remove. Devolve {raw, seed} pós-boot. */
async function bootHomeComSnapshot(page, { savedAt, resetAt }) {
  await page.addInitScript(([savedAt, resetAt]) => {
    // Espião: só o ramo de descarte do restore remove pb_home_1 (o flow
    // fresco pós-boot SOBRESCREVE a chave via setItem, nunca a remove) —
    // a remoção é o observável que discrimina os dois destinos.
    window.__removidas = [];
    const orig = Storage.prototype.removeItem;
    Storage.prototype.removeItem = function (k) { window.__removidas.push(k); return orig.call(this, k); };

    if (resetAt) localStorage.setItem("finbot_reset_at", String(resetAt));
    else localStorage.removeItem("finbot_reset_at");
    const entrada = { userId: 1, snapshot: { total: 1 }, history: [], email: "", displayName: "" };
    if (savedAt) entrada.savedAt = savedAt;
    sessionStorage.setItem("pb_home_1", JSON.stringify(entrada));
  }, [savedAt, resetAt]);
  await page.goto(`${ORIGIN}/home.html`);
  return page;
}

const chaveFoiRemovida = (page) =>
  page.evaluate(() => (window.__removidas || []).includes("pb_home_1"));

test("home: snapshot anterior ao finbot_reset_at é descartado no restore", async () => {
  const page = await newPage();
  try {
    await bootHomeComSnapshot(page, { savedAt: 1000, resetAt: 2000 });
    await waitFor(() => chaveFoiRemovida(page), "descarte do pb_home_1 pré-reset");
  } finally { await page.__ctx.close(); }
});

test("home: snapshot gravado DEPOIS do reset restaura normal", async () => {
  const page = await newPage();
  try {
    await bootHomeComSnapshot(page, { savedAt: 3000, resetAt: 2000 });
    await sleep(1500);   // janela em que o teste acima prova que o restore roda
    assert.equal(await chaveFoiRemovida(page), false,
                 "snapshot pós-reset é legítimo — não pode ser descartado");
  } finally { await page.__ctx.close(); }
});

test("home: sem marker (nunca resetou), o restore segue como antes", async () => {
  const page = await newPage();
  try {
    await bootHomeComSnapshot(page, { savedAt: 1000, resetAt: 0 });
    await sleep(1500);
    assert.equal(await chaveFoiRemovida(page), false,
                 "sem finbot_reset_at nada muda no restore");
  } finally { await page.__ctx.close(); }
});

test("dashboard: snapshot anterior ao finbot_reset_at é descartado no restore", async () => {
  const page = await newPage();
  try {
    await page.goto(`${ORIGIN}/dashboard.html`);
    await waitFor(() => page.evaluate(() => typeof restoreSnapshotFromSession === "function"),
                  "funções do dashboard carregadas");
    const resultado = await page.evaluate(() => {
      window.USER_ID = 1;
      localStorage.setItem("finbot_reset_at", "2000");
      const key = `pb_snap_1_${viewYear}_${String(viewMonth).padStart(2, "0")}`;
      sessionStorage.setItem(key, JSON.stringify(
        { year: viewYear, month: viewMonth, pb_saved_at: 1000 }));
      const restaurou = restoreSnapshotFromSession();
      return { restaurou, ficou: sessionStorage.getItem(key) !== null };
    });
    assert.equal(resultado.restaurou, false, "restore de snapshot pré-reset tinha que devolver false");
    assert.equal(resultado.ficou, false, "a chave pré-reset tinha que ser removida");
  } finally { await page.__ctx.close(); }
});

test("settings: sucesso do reset grava o marker e limpa os snapshots da aba", async () => {
  const page = await newPage();
  try {
    await page.goto(`${ORIGIN}/settings.html`);
    await waitFor(() => page.evaluate(() => typeof requestAccountReset === "function"),
                  "funções do settings carregadas");
    await page.evaluate(() => {
      localStorage.removeItem("finbot_reset_at");
      sessionStorage.setItem("pb_snap_1_2026_01", "{}");
      sessionStorage.setItem("pb_home_1", "{}");
      window.confirmModal = async () => true;   // pula o modal destrutivo
      document.getElementById("reset-password").value = "senha";
      requestAccountReset();                    // POST stubado (200) → marker → /onboarding
    });
    await waitFor(() => page.evaluate(() => location.pathname === "/onboarding"),
                  "redirect pro /onboarding");
    const estado = await page.evaluate(() => ({
      marker: localStorage.getItem("finbot_reset_at"),
      snap: sessionStorage.getItem("pb_snap_1_2026_01"),
      home: sessionStorage.getItem("pb_home_1"),
    }));
    assert.ok(Number(estado.marker) > 0, "o reset tinha que gravar finbot_reset_at");
    assert.equal(estado.snap, null, "pb_snap_* da própria aba tinha que sumir");
    assert.equal(estado.home, null, "pb_home_* da própria aba tinha que sumir");
  } finally { await page.__ctx.close(); }
});
