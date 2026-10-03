/**
 * #758, P2 do Codex no PR #760: a escrita do gate no BOOT obedece à mesma regra de
 * ordem dos outros escritores. Se um puxão de tela (PBRefresh) emitido DEPOIS do
 * /auth/me do boot já respondeu, a resposta velha do boot não decide: ela não trava
 * a tela de quem acabou de criar a senha, nem destrava a de quem precisa dela.
 *
 * Determinístico: a resposta do boot fica presa numa promise que o teste solta, depois
 * de o puxão já ter respondido e assentado (await do PBRefresh). Sem relógio.
 *
 * CONTROLE NEGATIVO (§3 do CLAUDE.md): com a escrita do boot incondicional (sem o
 * respostaDoGateMaisNova no initSettings), cai o B1; comparando com `meGen ===
 * _hasPwGen`, cai o B4 (a falha do puxão apaga a resposta do boot). O B2 cobre o
 * espelho (o puxão trava e recarrega) e os B3 são o positivo: sem puxão, o boot decide.
 *
 * Rodar:  node --test tests/frontend/settings_precisa_criar_senha_boot.test.mjs
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
const GOOGLE = { precisa_criar_senha: false, has_password: false, of_ui_enabled: true };
const FALHA = [500, { detail: "erro" }];
const BLOQUEADO = /\/open-finance\/|\/settings\/1\/(activity|notifications)/;
const SECOES = ["open-finance", "security", "notifications", "data", "legal"];

async function waitFor(cond, what, timeoutMs = 10000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) { if (await cond()) return; await sleep(50); }
  throw new Error(`timeout esperando: ${what}`);
}

/** `meDe(n)` = [status, corpo] da n-ésima chamada ao /auth/me (0 = a do boot). Com
    `prendeBoot`, a chamada 0 só responde quando o teste chamar `soltaBoot()`. Os
    carregadores barrados respondem 403 enquanto a conta corrente precisa de senha. */
async function abre(meDe, { prendeBoot = false } = {}) {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const page = await ctx.newPage();
  const reqs = [], cargas = [];
  let chamadasMe = 0, soltaBoot = () => {}, contaSemSenha = false;
  const bootPreso = prendeBoot ? new Promise((r) => { soltaBoot = r; }) : null;
  page.on("request", (r) => { const u = new URL(r.url()); reqs.push(u.pathname + u.search); });
  page.on("load", () => cargas.push(reqs.length));
  await page.route("**/*", (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== ORIGIN) return route.abort();
    if (/\.[a-z0-9]+$/i.test(url.pathname)) return route.continue();
    if (BLOQUEADO.test(url.pathname) && contaSemSenha) return route.fulfill(json({ detail: { error: "password_required" } }, 403));
    return route.fulfill(json({}));
  });
  await page.route("**/static/auth-refresh.js", (route) => route.fulfill({ status: 200,
    contentType: "application/javascript", body: readFileSync(join(FRONTEND, "static", "auth-refresh.js"), "utf8") }));
  await page.route("**/auth/validate", (route) => route.fulfill(json({ user_id: 1 })));
  await page.route("**/auth/me", async (route) => {
    const n = chamadasMe++;
    if (n === 0 && bootPreso) await bootPreso;
    const [status, corpo] = meDe(n);
    await route.fulfill(json(corpo, status)).catch(() => {});   // a recarga pode ter abortado
  });
  await page.route("**/settings/1/security", (route) => route.fulfill(json({ email: "a@b.com", plan: "pro" })));
  await page.route("**/auth/mfa/status", (route) => route.fulfill(json({ enabled: false })));
  await page.route("**/settings/1/sessions", (route) => route.fulfill(json({ sessions: [] })));
  await page.goto(`${ORIGIN}/settings.html?view=security`);
  return {
    page, fechar: () => ctx.close(), chamadasMe: () => chamadasMe, soltaBoot: () => soltaBoot(),
    cargas: () => cargas.length, bloqueados: () => reqs.filter((p) => BLOQUEADO.test(p)),
    contaSemSenha: (v) => { contaSemSenha = v; },
  };
}
const bootou = (page) => waitFor(() => page.evaluate(() => !document.getElementById("section-security").hidden), "o boot");
const visiveis = (page) => page.evaluate(() =>
  [...document.querySelectorAll(".sidebar-item")].filter((e) => e.getBoundingClientRect().height > 0).map((e) => e.dataset.section));
const travada = (page) => page.evaluate(() => document.body.classList.contains("pb-sem-senha"));

// ── B1: boot velho diz SEM senha, puxão novo diz COM senha e chega antes ──────────
test("B1 boot velho precisa_criar_senha:true, puxão novo false chega antes: termina LIVRE", async () => {
  const t = await abre((n) => (n === 0 ? [200, SEM] : [200, LIVRE]), { prendeBoot: true });
  try {
    await waitFor(() => t.chamadasMe() >= 1, "o /auth/me do boot sair");
    await t.page.evaluate(() => window.PBRefresh());   // a resposta nova assenta inteira
    t.soltaBoot();                                     // só agora a velha chega
    await bootou(t.page);
    await waitFor(async () => (await t.page.evaluate(() => typeof _dataGen === "number" && _dataGen > 0)), "o loadData do boot");
    assert.equal(await travada(t.page), false, "a resposta velha do boot travou a conta que já tem senha");
    assert.deepEqual(await visiveis(t.page), SECOES);
    assert.equal(t.cargas(), 1, "recarregou sem motivo");
  } finally { await t.fechar(); }
});

// ── B2: o espelho — boot velho diz COM senha, puxão novo diz SEM senha ────────────
test("B2 boot velho precisa_criar_senha:false, puxão novo true chega antes: termina TRAVADA, sem 403", async () => {
  const t = await abre((n) => (n === 0 ? [200, LIVRE] : [200, SEM]), { prendeBoot: true });
  try {
    t.contaSemSenha(true);   // o servidor de agora nega os carregadores
    await waitFor(() => t.chamadasMe() >= 1, "o /auth/me do boot sair");
    const recarga = t.page.waitForEvent("load");
    await t.page.evaluate(() => { window.PBRefresh(); });
    await recarga;           // o puxão discorda do gate e recarrega
    t.soltaBoot();
    await bootou(t.page);
    await sleep(300);        // ausência: dá tempo de um carregador do boot sair, se fosse sair
    assert.equal(await travada(t.page), true);
    assert.deepEqual(await visiveis(t.page), ["security"]);
    assert.deepEqual(t.bloqueados(), [], "algum carregador bateu nos 403");
  } finally { await t.soltaBoot(); await t.fechar(); }
});

// ── B4: o puxão novo FALHA; a resposta válida do boot (mais velha) ainda decide ────
// Falha não é resposta: ela sobe o _hasPwGen mas não pode apagar o que o boot sabe.
// Comparar com `meGen === _hasPwGen` (a regra do setHasPassword) deixava a tela livre.
test("B4 boot velho precisa_criar_senha:true, puxão novo falha antes: termina TRAVADA", async () => {
  const t = await abre((n) => (n === 0 ? [200, SEM] : FALHA), { prendeBoot: true });
  try {
    await waitFor(() => t.chamadasMe() >= 1, "o /auth/me do boot sair");
    await t.page.evaluate(() => window.PBRefresh().catch(() => {}));   // PTR âmbar: falhou
    t.soltaBoot();
    await bootou(t.page);
    assert.equal(await travada(t.page), true, "a falha do puxão apagou a resposta do boot");
    assert.deepEqual(await visiveis(t.page), ["security"]);
  } finally { await t.fechar(); }
});

// ── B3 positivo: sem puxão, o boot decide sozinho ─────────────────────────────────
for (const [rotulo, resp, menu, trava] of [
  ["precisa_criar_senha:true", [200, SEM], ["security"], true],
  ["com senha", [200, LIVRE], SECOES, false],
  ["só-Google (has_password:false)", [200, GOOGLE], SECOES, false],
  ["/auth/me 500 (fail-open)", FALHA, SECOES, false],
]) {
  test(`B3 boot sem puxão, ${rotulo}: o boot decide`, async () => {
    const t = await abre(() => resp);
    try {
      await bootou(t.page);
      assert.equal(await travada(t.page), trava);
      assert.deepEqual(await visiveis(t.page), menu);
    } finally { await t.fechar(); }
  });
}
