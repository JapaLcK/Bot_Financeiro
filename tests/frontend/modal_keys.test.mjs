/**
 * Teclado dos diálogos: trap de Tab e Esc condicionado (issue #76).
 *
 * UM invariante: enquanto um diálogo está aberto, o Tab não sai dele e o Esc
 * fecha — MENOS quando fechar destrói algo que não volta.
 *
 * A exceção não é detalhe: `#mfa-setup-overlay` (passo 3) e `#mfa-regen-overlay`
 * (resultado) mostram os códigos de backup UMA vez, e no regen os antigos já
 * foram invalidados quando os novos aparecem. Um Esc por engano ali tranca a
 * pessoa fora da recuperação de MFA. Por isso o Esc é recusado NESSE estado e
 * aceito no estado de formulário do MESMO diálogo — é o par que discrimina uma
 * guarda de verdade de um `return` que só desliga o Esc.
 *
 * Armadilha registrada na #76 e que vale para qualquer teste desta página: a
 * `settings.html` tem guarda de sessão que redireciona em laço sem backend. Sem
 * responder `/auth/validate`, a navegação destrói o contexto no meio da
 * medição. O `newPage()` abaixo responde.
 *
 * Rodar:  npm run test:frontend
 * Precisa de `npm ci` na raiz (playwright) + `npx playwright install chromium`.
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
// Porta própria: a 8899 é do settings_security_fanout e a 8901 é do hiw_rail.
// Repetir uma delas faz o http.server morrer com "Address already in use" e o
// arquivo inteiro fica vermelho por colisão, não por defeito — foi o que
// aconteceu na primeira versão deste teste.
const PORT = Number(process.env.PB_MODALS_TEST_PORT || 8903);
const ORIGIN = `http://127.0.0.1:${PORT}`;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const json = (body) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });

async function startServer() {
  const proc = spawn("python3", ["-m", "http.server", String(PORT), "--bind", "127.0.0.1", "--directory", FRONTEND],
    { stdio: "ignore" });
  for (let i = 0; i < 100; i++) {
    await sleep(100);
    if (proc.exitCode !== null) throw new Error(`http.server morreu (porta ${PORT} ocupada?)`);
    try { if ((await fetch(`${ORIGIN}/settings.html`)).ok) return proc; } catch { /* ainda subindo */ }
  }
  proc.kill();
  throw new Error(`http.server não subiu em ${ORIGIN}`);
}

let server, browser;
before(async () => { server = await startServer(); browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

async function newPage() {
  const page = await browser.newPage();
  await page.route("**/*", (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== ORIGIN) return route.abort();          // CDN bloqueado
    if (/\.[a-z0-9]+$/i.test(url.pathname)) return route.continue();
    return route.fulfill(json({}));
  });
  await page.route("**/static/auth-refresh.js", (route) =>
    route.fulfill({ status: 200, contentType: "application/javascript",
                    body: readFileSync(join(FRONTEND, "auth-refresh.js"), "utf8") }));
  await page.route("**/auth/validate", (route) => route.fulfill(json({ user_id: 1 })));
  await page.route("**/auth/me", (route) => route.fulfill(json({})));
  await page.route("**/auth/mfa/status", (route) => route.fulfill(json({ enabled: false })));
  return page;
}

const aberto = (page, id) => page.evaluate(
  (i) => document.getElementById(i).classList.contains("open"), id);

/** Onde o foco está: dentro do overlay, ou escapou? */
const focoDentro = (page, id) => page.evaluate((i) => {
  const ov = document.getElementById(i);
  return !!(ov && document.activeElement && ov.contains(document.activeElement));
}, id);

/**
 * Tab N vezes e devolve quantas vezes o foco estava FORA do overlay.
 * Mais voltas que controles focáveis de propósito: o vazamento só acontece na
 * borda, e uma volta só passaria mesmo sem trap nenhum.
 */
async function tabAndoCount(page, id, voltas = 12) {
  let fora = 0;
  for (let i = 0; i < voltas; i++) {
    await page.keyboard.press("Tab");
    if (!(await focoDentro(page, id))) fora++;
  }
  return fora;
}

async function abreSettings(query = "") {
  const page = await newPage();
  await page.goto(`${ORIGIN}/settings.html${query}`);
  await page.waitForFunction(() => typeof window.pigModalKeys !== "undefined"
                                || typeof window.pigTrapTab !== "undefined");
  return page;
}

// ── trap de Tab ─────────────────────────────────────────────────────────────

test("settings: diálogo aberto prende o Tab", async () => {
  const page = await abreSettings();
  try {
    await page.evaluate(() => openMfaDisableModal());
    assert.equal(await aberto(page, "mfa-disable-overlay"), true);
    assert.equal(await tabAndoCount(page, "mfa-disable-overlay"), 0,
      "o foco vazou do diálogo para a página atrás");
  } finally { await page.close(); }
});

test("settings: atividade recente prende o Tab e fecha no Esc", async () => {
  const page = await abreSettings();
  try {
    await page.evaluate(() => openActivityModal());
    assert.equal(await aberto(page, "activity-modal-overlay"), true);
    assert.equal(await tabAndoCount(page, "activity-modal-overlay"), 0, "foco vazou");
    await page.keyboard.press("Escape");
    assert.equal(await aberto(page, "activity-modal-overlay"), false, "Esc não fechou");
  } finally { await page.close(); }
});

// ── Esc: o par que discrimina ───────────────────────────────────────────────

test("settings: Esc fecha o setup de MFA no formulário", async () => {
  const page = await abreSettings();
  try {
    await page.evaluate(() => openMfaSetupModal());   // abre no passo 1
    await page.keyboard.press("Escape");
    assert.equal(await aberto(page, "mfa-setup-overlay"), false,
      "no passo do formulário o Esc TEM que fechar — senão a guarda só desligou o Esc");
  } finally { await page.close(); }
});

test("settings: Esc NÃO fecha o setup na tela dos códigos de backup", async () => {
  const page = await abreSettings();
  try {
    await page.evaluate(() => {
      openMfaSetupModal();
      document.getElementById("mfa-setup-step1").style.display = "none";
      document.getElementById("mfa-setup-step3").style.display = "";
    });
    await page.keyboard.press("Escape");
    assert.equal(await aberto(page, "mfa-setup-overlay"), true,
      "o Esc apagou os códigos de backup, que aparecem uma vez só");
    assert.equal(await tabAndoCount(page, "mfa-setup-overlay"), 0,
      "o trap de Tab vale nos dois estados");
  } finally { await page.close(); }
});

test("settings: Esc NÃO fecha os novos códigos de backup do regenerar", async () => {
  const page = await abreSettings();
  try {
    await page.evaluate(() => {
      openMfaRegenerateModal();
      document.getElementById("mfa-regen-form").style.display = "none";
      document.getElementById("mfa-regen-result").style.display = "";
    });
    await page.keyboard.press("Escape");
    assert.equal(await aberto(page, "mfa-regen-overlay"), true,
      "os antigos já foram invalidados: fechar aqui perde os dois lados");
  } finally { await page.close(); }
});

test("settings: Esc fecha o regenerar enquanto é formulário", async () => {
  const page = await abreSettings();
  try {
    await page.evaluate(() => openMfaRegenerateModal());
    await page.keyboard.press("Escape");
    assert.equal(await aberto(page, "mfa-regen-overlay"), false);
  } finally { await page.close(); }
});

// ── dashboard: Esc não pode fechar o que não está aberto ────────────────────
//
// Dois listeners globais chamavam os `close*` sem checar nada. Fechar o que já
// está fechado parece inofensivo e não é: um Esc destinado a um diálogo aberto
// POR CIMA derrubava os de baixo junto — foi o que colidiu com a confirmação de
// antecipar fatura no #73, que precisou de captura + stopPropagation só para se
// defender. O observável não é o DOM (fechado continua fechado): é a CHAMADA.

const FECHADORES = [
  "closeBillDetailModal", "closePayBillModal", "closePayReceiptModal",
  "closeInvestmentDetail", "closeInvestmentHelp", "closeLaunchModal",
];

test("dashboard: Esc com tudo fechado não chama nenhum fechador", async () => {
  const page = await newPage();
  try {
    await page.goto(`${ORIGIN}/dashboard.html`);
    await page.waitForFunction(
      (fs) => fs.every((f) => typeof window[f] === "function"), FECHADORES);

    const chamados = await page.evaluate((fs) => {
      window.__chamados = [];
      for (const f of fs) {
        const orig = window[f];
        window[f] = function () { window.__chamados.push(f); return orig.apply(this, arguments); };
      }
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
      return window.__chamados;
    }, FECHADORES);

    assert.deepEqual(chamados, [],
      `Esc fechou diálogo que não estava aberto: ${chamados.join(", ")}`);
  } finally { await page.close(); }
});

test("dashboard: Esc fecha o diálogo que ESTÁ aberto", async () => {
  const page = await newPage();
  try {
    await page.goto(`${ORIGIN}/dashboard.html`);
    await page.waitForFunction(
      (fs) => fs.every((f) => typeof window[f] === "function"), FECHADORES);

    const r = await page.evaluate((fs) => {
      window.__chamados = [];
      for (const f of fs) {
        const orig = window[f];
        window[f] = function () { window.__chamados.push(f); return orig.apply(this, arguments); };
      }
      document.getElementById("launch-overlay").classList.add("open");
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
      return { chamados: window.__chamados,
               aberto: document.getElementById("launch-overlay").classList.contains("open") };
    }, FECHADORES);

    assert.deepEqual(r.chamados, ["closeLaunchModal"],
      "só o fechador do diálogo aberto podia rodar");
    assert.equal(r.aberto, false, "o Esc tem que fechar o que está aberto");
  } finally { await page.close(); }
});

// ── os outros três dos sete ─────────────────────────────────────────────────

test("dashboard: upgrade e resultado do OFX prendem o Tab e fecham no Esc", async () => {
  const page = await newPage();
  try {
    await page.goto(`${ORIGIN}/dashboard.html`);
    await page.waitForFunction(() => typeof window.closeUpgradeModal === "function");

    for (const id of ["upgrade-overlay", "ofx-result-overlay"]) {
      await page.evaluate((i) => document.getElementById(i).classList.add("open"), id);
      assert.equal(await tabAndoCount(page, id), 0, `${id}: o foco vazou do diálogo`);
      await page.keyboard.press("Escape");
      assert.equal(await aberto(page, id), false, `${id}: o Esc não fechou`);
    }
  } finally { await page.close(); }
});

test("home: Esc no onboarding de MFA marca como visto, não só fecha", async () => {
  const page = await newPage();
  const vistos = [];
  try {
    // As funções da home NÃO são globais: os scripts dela estão embrulhados em
    // `PBPages.home.inits` (contrato do POC de SPA, frontend/pb-nav.js). Então o
    // observável aqui é o EFEITO — o POST que marca o onboarding como visto —
    // e não o nome de uma função, que nem existe no window.
    await page.route("**/auth/mfa/onboarding-seen", (route) => {
      vistos.push(1);
      route.fulfill(json({}));
    });
    await page.goto(`${ORIGIN}/home.html`);
    await page.waitForFunction(() => !!document.getElementById("mfa-onboarding-overlay"));

    await page.evaluate(() =>
      document.getElementById("mfa-onboarding-overlay").classList.add("open"));
    await page.keyboard.press("Escape");

    await page.waitForFunction(() =>
      !document.getElementById("mfa-onboarding-overlay").classList.contains("open"));
    // Fechar cru não marca nada, e o overlay voltaria no próximo login — o Esc
    // viraria um jeito de nunca se livrar dele.
    assert.equal(vistos.length, 1, "o Esc fechou sem marcar como visto");
  } finally { await page.close(); }
});
