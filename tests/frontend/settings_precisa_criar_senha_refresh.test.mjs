/**
 * #758, apontamento do Codex no PR #760: o gate da /settings também tem de TRAVAR,
 * não só destravar. `PRECISA_SENHA` false pode querer dizer "não sei" (o /auth/me do
 * boot falhou e a tela abriu livre, ou o puxão de tela saiu antes do /auth/me do
 * boot). Se um /auth/me posterior diz `precisa_criar_senha:true`, a página recarrega
 * e termina travada, sem chamar os carregadores barrados. O inverso (gate livre,
 * servidor livre, ou campo ausente) não recarrega.
 *
 * Arquivo à parte do settings_precisa_criar_senha.test.mjs só pelo teto de linhas;
 * o harness é o mesmo molde, enxugado para o /auth/me por chamada.
 *
 * CONTROLE NEGATIVO (§3 do CLAUDE.md), cada um aplicado na settings.html:
 *   recarregarSeGateMudou só destravando ....... R1 (os dois), R2, R3 sem laço
 *   listeners de foco só com a conta travada ... R1 volta do foco
 *   decisão sem olhar a geração (_hasPwGen) .... R3 resposta velha
 *   PRECISA_SENHA não vira antes do reload ...... R2 (o puxão bate na /activity). O R2
 *     segura o documento da recarga para isso não depender de corrida (antes, isolado,
 *     ele passava às vezes com esta mutação).
 *   recarrega sem olhar o `me` ................. todos os R3 positivos
 * Os R3 positivos ficam verdes nas outras.
 *
 * Rodar:  node --test tests/frontend/settings_precisa_criar_senha_refresh.test.mjs
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { startServer } from "./_server.mjs";
import { toastAssentado } from "./_toast.mjs";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { chromium } from "playwright";

const FRONTEND = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "frontend");
let ORIGIN, server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer()); browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const json = (body, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(body) });
const SEM = { precisa_criar_senha: true, has_password: false, of_ui_enabled: true };
const LIVRE = { precisa_criar_senha: false, has_password: true, of_ui_enabled: true };
const FALHA = [500, { detail: "erro" }];
const BLOQUEADO = /\/open-finance\/|\/settings\/1\/(activity|notifications)/;
const SECOES = ["open-finance", "security", "notifications", "data", "legal"];

async function waitFor(cond, what, timeoutMs = 10000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) { if (await cond()) return; await sleep(50); }
  throw new Error(`timeout esperando: ${what}`);
}

/** `meDe(n)` devolve [status, corpo, atrasoMs?] para a n-ésima chamada ao /auth/me
    (0 = a do boot). Os carregadores barrados dão 403 password_required sempre: o
    servidor real nega a conta sem senha mesmo quando o /auth/me falha. */
async function abre(meDe, { esperaBoot = true, seguraRecarga = 0 } = {}) {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const page = await ctx.newPage();
  const reqs = [], cargas = [];
  let chamadasMe = 0;
  page.on("request", (r) => { const u = new URL(r.url()); reqs.push(u.pathname + u.search); });
  page.on("load", () => cargas.push(reqs.length));
  await page.route("**/*", (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== ORIGIN) return route.abort();
    if (/\.[a-z0-9]+$/i.test(url.pathname)) return route.continue();
    if (BLOQUEADO.test(url.pathname)) return route.fulfill(json({ detail: { error: "password_required" } }, 403));
    return route.fulfill(json({}));
  });
  await page.route("**/static/auth-refresh.js", (route) => route.fulfill({ status: 200,
    contentType: "application/javascript", body: readFileSync(join(FRONTEND, "static", "auth-refresh.js"), "utf8") }));
  await page.route("**/auth/validate", (route) => route.fulfill(json({ user_id: 1 })));
  await page.route("**/auth/me", async (route) => {
    const [status, corpo, atraso] = meDe(chamadasMe++);
    if (atraso) await sleep(atraso);
    await route.fulfill(json(corpo, status)).catch(() => {});   // a recarga pode ter abortado
  });
  await page.route("**/settings/1/security", (route) => route.fulfill(json({ email: "a@b.com", plan: "pro" })));
  await page.route("**/auth/mfa/status", (route) => route.fulfill(json({ enabled: false })));
  await page.route("**/settings/1/sessions", (route) => route.fulfill(json({ sessions: [] })));
  // `seguraRecarga`: atrasa o documento da recarga. Até a navegação nova assentar o
  // documento velho segue vivo, e o que o puxão dele ainda dispara fica no registro,
  // sem depender de quem ganha a corrida entre as microtasks e a navegação.
  let docs = 0;
  if (seguraRecarga) await page.route("**/settings.html*", async (route) => {
    if (docs++ > 0) await sleep(seguraRecarga);
    await route.continue();
  });
  await page.goto(`${ORIGIN}/settings.html?view=security`);
  if (esperaBoot) await bootou(page);
  return {
    page, fechar: () => ctx.close(), chamadasMe: () => chamadasMe, cargas: () => cargas.length,
    bloqueados: (desde = 0) => reqs.slice(desde).filter((p) => BLOQUEADO.test(p)),
    desdeUltimaCarga: () => cargas[cargas.length - 1],
  };
}
const bootou = (page) => waitFor(() => page.evaluate(() => !document.getElementById("section-security").hidden), "o boot");
const visiveis = (page) => page.evaluate(() =>
  [...document.querySelectorAll(".sidebar-item")].filter((e) => e.getBoundingClientRect().height > 0).map((e) => e.dataset.section));
const PTR = (page) => page.evaluate(() => { window.PBRefresh(); });
const FOCO = (page) => page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));

async function terminouTravada(t, desde) {
  await bootou(t.page);
  await sleep(1200);   // o carregador falso mais lento já teria respondido
  await toastAssentado(t.page);
  assert.deepEqual(await visiveis(t.page), ["security"], "não travou depois da recarga");
  assert.deepEqual(t.bloqueados(desde), [], "chamou carregador barrado depois da recarga");
  assert.equal(await t.page.evaluate(() => getComputedStyle(document.getElementById("toast")).opacity), "0", "toast");
}

// ── R1: boot sem /auth/me (500) abre livre; o rebusque descobre a conta sem senha ──
for (const [rotulo, gesto] of [["PBRefresh", PTR], ["volta do foco", FOCO]]) {
  test(`R1 boot com /auth/me 500 + ${rotulo} com precisa_criar_senha:true: recarrega e trava`, async () => {
    const t = await abre((n) => (n === 0 ? FALHA : [200, SEM]));
    try {
      assert.deepEqual(await visiveis(t.page), SECOES, "o boot falho devia abrir livre (fail-open)");
      const recarga = t.page.waitForEvent("load");
      await gesto(t.page);
      await recarga;
      await terminouTravada(t, t.desdeUltimaCarga());
    } finally { await t.fechar(); }
  });
}

// ── R2: puxão de tela dentro da janela do /auth/me do boot ────────────────────────
test("R2 PBRefresh com o /auth/me do boot ainda em voo: trava, sem 403 do puxão", async () => {
  const t = await abre((n) => (n === 0 ? [200, SEM, 1500] : [200, SEM]), { esperaBoot: false, seguraRecarga: 1000 });
  try {
    await waitFor(() => t.chamadasMe() >= 1, "o /auth/me do boot sair");
    const recarga = t.page.waitForEvent("load");
    await PTR(t.page);
    await recarga;
    await terminouTravada(t, 0);   // desde o começo: nem o puxão pode ter batido nos barrados
  } finally { await t.fechar(); }
});

// ── R3 positivo: nada a corrigir, nada recarrega; e não há laço ───────────────────
for (const [rotulo, meDe, gesto] of [
  ["com senha + PBRefresh", () => [200, LIVRE], PTR],
  ["só-Google (has_password:false, precisa:false) + PBRefresh", () => [200, { has_password: false, precisa_criar_senha: false, of_ui_enabled: true }], PTR],
  ["/auth/me sem o campo + PBRefresh", () => [200, { of_ui_enabled: true }], PTR],
  ["boot 500 + volta do foco com senha", (n) => (n === 0 ? FALHA : [200, LIVRE]), FOCO],
  ["boot 500 + volta do foco sem o campo", (n) => (n === 0 ? FALHA : [200, { of_ui_enabled: true }]), FOCO],
]) {
  test(`R3 ${rotulo}: não recarrega e segue livre`, async () => {
    const t = await abre(meDe);
    try {
      await gesto(t.page);
      await sleep(800);
      assert.equal(t.cargas(), 1, "recarregou sem motivo");
      assert.deepEqual(await visiveis(t.page), SECOES);
    } finally { await t.fechar(); }
  });
}

test("R3 resposta velha não decide: boot 500, rebusque lento diz sem senha e o novo diz com senha", async () => {
  const t = await abre((n) => (n === 0 ? FALHA : n === 1 ? [200, SEM, 800] : [200, LIVRE]));
  try {
    await t.page.evaluate(() => { refreshHasPassword().catch(() => {}); setTimeout(() => refreshHasPassword().catch(() => {}), 100); });
    await sleep(1500);   // a velha (SEM) assenta por último
    assert.equal(t.cargas(), 1, "a resposta velha recarregou por cima da nova");
    assert.deepEqual(await visiveis(t.page), SECOES);
  } finally { await t.fechar(); }
});

test("R3 sem laço: boot sempre 500 e o rebusque diz sem senha: UMA recarga por gesto", async () => {
  const t = await abre((n) => (n === 1 ? [200, SEM] : FALHA));   // só a 2ª chamada responde
  try {
    const recarga = t.page.waitForEvent("load");
    await PTR(t.page);
    await recarga;
    await bootou(t.page);   // o boot novo falhou de novo: livre, e ninguém mais rebusca sozinho
    await sleep(1500);
    assert.equal(t.cargas(), 2, "recarregou mais de uma vez sem gesto");
  } finally { await t.fechar(); }
});
