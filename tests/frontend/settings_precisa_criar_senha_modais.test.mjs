/**
 * #758: a recarga que troca o gate da /settings (recarregarSeGateMudou) não pode
 * atropelar o que o usuário tem aberto e não volta: os códigos de backup do MFA
 * (aparecem UMA vez), o QR/segredo do setup, senha digitada nos painéis de exportar,
 * recomeçar e excluir, um diálogo do modals.js no meio da confirmação. Classe já paga
 * neste repositório (CLAUDE.md §4: reload por cima dos códigos de backup).
 *
 * Com algo aberto a recarga não acontece e nada fica agendado: o próximo gesto
 * depois de fechar refaz a decisão. O último passo de cada caso prova isso, e é o
 * controle positivo: sem nada aberto, a recarga sai.
 *
 * CONTROLE NEGATIVO (§3 do CLAUDE.md): com a guarda de volta a só
 * `.security-edit.show`, caem todos os M1 (livre → travada) e o M2 (travada → livre).
 *
 * Rodar:  node --test tests/frontend/settings_precisa_criar_senha_modais.test.mjs
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { startServer } from "./_server.mjs";
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
const ARG = { c: "<code>AAAA-1111</code><code>BBBB-2222</code>" };

async function waitFor(cond, what, timeoutMs = 10000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) { if (await cond()) return; await sleep(50); }
  throw new Error(`timeout esperando: ${what}`);
}

/** Boot com `boot` no /auth/me; toda chamada seguinte (o rebusque) devolve `depois`. */
async function abre(boot, depois) {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const page = await ctx.newPage();
  let cargas = 0, chamadasMe = 0;
  page.on("load", () => { cargas++; });
  await page.route("**/*", (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== ORIGIN) return route.abort();
    if (/\.[a-z0-9]+$/i.test(url.pathname)) return route.continue();
    return route.fulfill(json({}));
  });
  await page.route("**/static/auth-refresh.js", (route) => route.fulfill({ status: 200,
    contentType: "application/javascript", body: readFileSync(join(FRONTEND, "static", "auth-refresh.js"), "utf8") }));
  await page.route("**/auth/validate", (route) => route.fulfill(json({ user_id: 1 })));
  await page.route("**/auth/me", (route) => route.fulfill(json(chamadasMe++ === 0 ? boot : depois)).catch(() => {}));
  await page.route("**/settings/1/security", (route) => route.fulfill(json({ email: "a@b.com", plan: "pro" })));
  await page.route("**/auth/mfa/status", (route) => route.fulfill(json({ enabled: true })));
  await page.goto(`${ORIGIN}/settings.html?view=security`);
  await waitFor(() => page.evaluate(() => !document.getElementById("section-security").hidden), "o boot");
  return { page, fechar: () => ctx.close(), cargas: () => cargas };
}

const PTR = (page) => page.evaluate(() => { window.PBRefresh(); });

/** Abre, puxa a tela (o rebusque discorda do gate), confere que NADA recarregou e o
    estado do usuário continua; fecha e puxa de novo: agora recarrega. */
async function naoAtropela(t, { abrir, estado, fechar }, arg) {
  await t.page.evaluate(abrir, arg);
  await sleep(150);   // os diálogos entram num requestAnimationFrame
  const antes = await t.page.evaluate(estado, arg);
  assert.ok(antes, "o estado do usuário não chegou a existir: o caso não mede nada");
  await PTR(t.page);
  await sleep(800);
  assert.equal(t.cargas(), 1, "recarregou por cima do que estava aberto");
  assert.equal(await t.page.evaluate(estado, arg), antes, "o estado do usuário sumiu");
  await t.page.evaluate(fechar, arg);
  const recarga = t.page.waitForEvent("load");
  await PTR(t.page);
  await recarga;   // fechado, o próximo gesto refaz a decisão
}

const DIALOGO = {
  estado: () => document.querySelector(".pig-modal-overlay")?.textContent || "",
  fechar: () => document.querySelector(".pig-modal-overlay")?.remove(),
};
const CASOS = [
  ["MFA regenerar com os códigos novos na tela", {}, {
    abrir: ({ c }) => { openMfaRegenerateModal(); document.getElementById("mfa-regen-form").style.display = "none";
      document.getElementById("mfa-regen-result").style.display = ""; document.getElementById("mfa-regen-codes-list").innerHTML = c; },
    estado: () => document.getElementById("mfa-regen-codes-list").innerHTML,
    fechar: () => closeMfaRegenerateModal() }],
  ["MFA setup no passo dos códigos de backup", {}, {
    abrir: ({ c }) => { openMfaSetupModal(); document.getElementById("mfa-setup-step1").style.display = "none";
      document.getElementById("mfa-setup-step3").style.display = ""; document.getElementById("mfa-backup-codes-list").innerHTML = c; },
    estado: () => document.getElementById("mfa-backup-codes-list").innerHTML,
    fechar: () => document.getElementById("mfa-setup-overlay").classList.remove("open") }],
  ["MFA desativar com a senha digitada", {}, {
    abrir: () => { openMfaDisableModal(); document.getElementById("mfa-disable-password").value = "minha-senha"; },
    estado: () => document.getElementById("mfa-disable-password").value,
    fechar: () => closeMfaDisableModal() }],
  ...[["export", "Export"], ["reset", "Reset"], ["delete", "Delete"]].map(([k, K]) => [`painel ${k}-confirm com a senha digitada`, { k, K }, {
    abrir: ({ k, K }) => { window[`show${K}Confirm`](); document.getElementById(`${k}-password`).value = "minha-senha"; },
    estado: ({ k }) => document.getElementById(`${k}-confirm`).classList.contains("show") && document.getElementById(`${k}-password`).value,
    fechar: ({ k }) => document.getElementById(`${k}-confirm`).classList.remove("show") }]),
  ["diálogo do modals.js no meio da confirmação", {}, {
    abrir: () => { confirmModal("Tem certeza?", { title: "Confirmar" }); }, ...DIALOGO }],
];

// ── M1: livre → travada (o rebusque descobre a conta sem senha) ───────────────────
for (const [rotulo, extra, caso] of CASOS) {
  test(`M1 livre → travada com ${rotulo}: não recarrega até fechar`, async () => {
    const t = await abre(LIVRE, SEM);
    try { await naoAtropela(t, caso, { ...ARG, ...extra }); } finally { await t.fechar(); }
  });
}

// ── M2: travada → livre (a direção antiga) com o diálogo "Defina uma senha" aberto ──
// Travada, MFA e Meus dados estão escondidos; o diálogo do requireSenhaDefinida é o
// que ainda abre (o abridor do MFA chamado pelo ?autoOpenMfa=1 da /home, por exemplo).
test("M2 travada → livre com o diálogo do requireSenhaDefinida aberto: não recarrega até fechar", async () => {
  const t = await abre(SEM, LIVRE);
  try { await naoAtropela(t, { abrir: () => { openMfaSetupModal(); }, ...DIALOGO }, ARG); } finally { await t.fechar(); }
});
