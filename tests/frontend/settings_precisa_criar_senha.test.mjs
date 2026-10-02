/**
 * Conta que pagou e ainda não criou a senha (/auth/me → precisa_criar_senha:true)
 * em frontend/settings.html (#758, Origem: #716).
 *
 * INVARIANTE: travada, a página mostra só a Segurança (com o card do link da senha
 * no topo e o e-mail na primeira tela do celular), não chama os carregadores que o
 * servidor nega com 403 password_required (logo, nenhum toast sem a pessoa pedir) e
 * libera sozinha quando a senha passa a existir (PTR ou volta do foco). Quem tem
 * credencial, e a conta só-Google (has_password:false), ficam exatamente como antes.
 *
 * Os carregadores barrados respondem 403 password_required DE VERDADE enquanto o
 * `me` corrente precisa de senha: com o catch-all 200 o toast nunca acenderia e o
 * teste do toast não mediria nada.
 *
 * CONTROLES DO GRUPO (§3 do CLAUDE.md). Cada mutação foi APLICADA na settings.html e
 * este arquivo rodado inteiro (2026-10-02; 15 pass na baseline). Vermelhos:
 *   flag sempre false ............................ 11: T1 ×2, T2, T3, T4, T5a, T5b ×3, T5c, T7
 *   sem o guard do showSettingsSection ............ 2: T2, T3
 *   sem o guard do loadData ....................... 7: T1 ×2, T2, T3, T4, T5a, T7
 *   sem o guard do loadActivityLog ................ 7: T1 ×2, T2, T3, T4, T5a, T7
 *   sem o reload no refreshHasPassword ............ 1: T5b PBRefresh
 *   flag posta DEPOIS do showSettingsSection/loadData 7: T1 ×2, T2, T3, T4, T5a, T7
 *   CSS sem `!important` .......................... 1: T1 no app (o app-mode.css devolvia o menu)
 *   sem mover o card da senha / o e-mail no DOM ... 2 cada: T1 ×2 (+ T9 sem credencial ×2)
 *   volta ao `order:-1` no CSS, sem mover no DOM .. 2: T9 sem credencial 390x844 e 1280x800
 *   sem o `if (el)` antes do prepend .............. 2: T10 ×2 (TypeError aborta o boot)
 *   MFA e dispositivos / banner visíveis .......... 2 cada: T1 ×2
 *   sem os listeners de foco ...................... 3: T5b visibilitychange, T5b pageshow, T5c
 *   sem o guard de rascunho ....................... 1: T5c
 *   falha do /auth/me tratada como liberada ....... 1: T5c
 *   reload sem olhar o `me` (loop) ................ 1: T5a
 * Positivo: os T6 (campo ausente, com senha, só-Google) e o T8 (/auth/me 500) ficaram
 * verdes em TODAS. Sem eles o grupo passaria numa versão que trava todo mundo.
 * Limite: no T1 o assert de "nenhum carregador barrado" vem antes do do toast, então
 * nenhuma mutação deixa o toast como ÚNICO vermelho; ele é redundante de propósito.
 *
 * Rodar:  node --test tests/frontend/settings_precisa_criar_senha.test.mjs
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
const BLOQUEADO = /\/open-finance\/|\/settings\/1\/(activity|notifications)/;
const SECOES = ["open-finance", "security", "notifications", "data", "legal"];

async function waitFor(cond, what, timeoutMs = 10000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) { if (await cond()) return; await sleep(50); }
  throw new Error(`timeout esperando: ${what}`);
}

/** Abre a /settings. `estado.me` é mutável (a senha criada noutra aba); `estado.meStatus`
    força falha do /auth/me; `estado.meDelay` atrasa. `reqs` guarda [path+query, t]. */
async function abre(me, query = "view=security", { app = false, meStatus = 200, meDelay = 0, viewport = { width: 390, height: 844 }, html = null } = {}) {
  const estado = { me, meStatus, meDelay, meRespondido: 0 };
  const ctx = await browser.newContext({
    viewport,
    ...(app ? { userAgent: "Mozilla/5.0 (iPhone) AppleWebKit/605.1.15 PigBankApp" } : {}),
  });
  const page = await ctx.newPage();
  const reqs = [];
  page.on("request", (r) => { const u = new URL(r.url()); reqs.push([u.pathname + u.search, Date.now(), r]); });
  await page.route("**/*", (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== ORIGIN) return route.abort();
    if (/\.[a-z0-9]+$/i.test(url.pathname)) return route.continue();
    if (BLOQUEADO.test(url.pathname) && estado.me?.precisa_criar_senha === true)
      return route.fulfill(json({ detail: { error: "password_required" } }, 403));
    return route.fulfill(json({}));
  });
  await page.route("**/static/auth-refresh.js", (route) => route.fulfill({ status: 200,
    contentType: "application/javascript", body: readFileSync(join(FRONTEND, "static", "auth-refresh.js"), "utf8") }));
  await page.route("**/auth/validate", (route) => route.fulfill(json({ user_id: 1 })));
  await page.route("**/auth/me", async (route) => {
    if (estado.meDelay) await sleep(estado.meDelay);
    estado.meRespondido = Date.now();
    await route.fulfill(json(estado.meStatus === 200 ? estado.me : { detail: "erro" }, estado.meStatus));
  });
  await page.route("**/settings/1/security", (route) => route.fulfill(json({ email: "a@b.com", plan: "pro", display_name: "Japa" })));
  await page.route("**/auth/mfa/status", (route) => route.fulfill(json({ enabled: false })));
  await page.route("**/settings/1/sessions", (route) => route.fulfill(json({ sessions: [{ id: 1, current: true }] })));
  // `html`: troca o texto da settings.html servida (ex.: um id dessincronizado do JS).
  if (html) await page.route("**/settings.html*", (route) => route.fulfill({ status: 200, contentType: "text/html",
    body: html(readFileSync(join(FRONTEND, "settings.html"), "utf8")) }));
  const erros = [];
  page.on("pageerror", (e) => erros.push(String(e)));
  await page.goto(`${ORIGIN}/settings.html?${query}`);
  await bootou(page);
  const fechar = () => ctx.close();
  return { page, reqs, estado, fechar, erros, bloqueados: () => reqs.filter(([p]) => BLOQUEADO.test(p)).map(([p]) => p) };
}
/** Boot = `#section-security` sem `hidden`: quem tira é o showSettingsSection, depois do
    /auth/me. Todos os casos daqui terminam o boot na Segurança. A do Open Finance NÃO
    serve: ela nasce sem `hidden` no HTML, e esperar por ela mediria o HTML estático. */
const bootou = (page) => waitFor(() => page.evaluate(() => !document.getElementById("section-security").hidden), "o boot");
const visiveis = (page) => page.evaluate(() =>
  [...document.querySelectorAll(".sidebar-item")].filter((e) => e.getBoundingClientRect().height > 0).map((e) => e.dataset.section));
const caixa = (page, sel) => page.evaluate((s) => { const b = document.querySelector(s).getBoundingClientRect(); return { top: b.top, bottom: b.bottom, h: b.height }; }, sel);
const secaoAberta = (page) => page.evaluate((s) => s.filter((x) => !document.getElementById("section-" + x).hidden), SECOES);
const viewDaUrl = (page) => page.evaluate(() => new URLSearchParams(location.search).get("view"));
const marca = (page) => page.evaluate(() => { window.__semReload = 1; });
const naoRecarregou = (page) => page.evaluate(() => window.__semReload === 1);

// ── (a) sem credencial ────────────────────────────────────────────────────────

for (const app of [false, true]) {
  test(`T1 sem credencial${app ? " no app (PigBankApp)" : ""}: só Segurança, sem 403 nem toast, card da senha e e-mail na dobra`, async () => {
    const { page, fechar, bloqueados } = await abre(SEM, "view=security", { app });
    try {
      await sleep(1500);   // o carregador falso mais lento já teria respondido
      await toastAssentado(page);
      assert.deepEqual(bloqueados(), [], "chamou carregador barrado");
      assert.equal(await page.evaluate(() => getComputedStyle(document.getElementById("toast")).opacity), "0", "toast sozinho ao abrir");
      assert.deepEqual(await secaoAberta(page), ["security"]);
      assert.deepEqual(await visiveis(page), ["security"], "o menu oferece seção barrada");
      assert.equal(await page.evaluate(() => document.getElementById("sidebar-lock").textContent), "Crie sua senha para liberar o resto.");
      assert.ok((await caixa(page, "#sidebar-lock")).h > 0, "a frase não aparece");
      for (const sel of [".pb-protect", ".activity-embed", "#mfa-card", "#sessions-card"])
        assert.equal((await caixa(page, sel)).h, 0, `${sel} continua visível`);
      // D1: o card do link vem antes dos dados da conta, e botão + e-mail cabem em 844px.
      const reset = await caixa(page, "#security-reset-btn"), email = await caixa(page, "#security-email-edit-btn");
      // No app a dobra é o topo da tab bar (ela cobre o fim da tela).
      const valor = await caixa(page, "#security-email-value");
      const dobra = await page.evaluate(() => document.querySelector(".pb-tabbar")?.getBoundingClientRect().top ?? innerHeight);
      assert.ok(reset.top < email.top, `card da senha não subiu (botão ${reset.top}, e-mail ${email.top})`);
      assert.ok(reset.bottom <= dobra && valor.bottom <= dobra, `fora da dobra ${dobra}: botão ${reset.bottom}, e-mail ${valor.bottom}`);
    } finally { await fechar(); }
  });
}

test("T2 sem credencial: ?view= de outra seção ou inválida cai em Segurança e reescreve a URL", async () => {
  for (const view of ["notifications", "data", "legal", "open-finance", "xyz"]) {
    const { page, fechar, bloqueados } = await abre(SEM, `view=${view}`);
    try {
      await sleep(300);
      assert.deepEqual(await secaoAberta(page), ["security"], `?view=${view}`);
      assert.equal(await viewDaUrl(page), "security", `?view=${view} não reescreveu`);
      assert.deepEqual(bloqueados(), [], `?view=${view} chamou carregador barrado`);
    } finally { await fechar(); }
  }
});

test("T3 sem credencial: showSettingsSection de outra seção (handler, console) fica em Segurança", async () => {
  const { page, fechar, bloqueados } = await abre(SEM);
  try {
    await page.evaluate(() => { showSettingsSection("notifications"); showSettingsSection("open-finance"); });
    await sleep(400);
    assert.deepEqual(await secaoAberta(page), ["security"]);
    assert.deepEqual(bloqueados(), []);
  } finally { await fechar(); }
});

test("T4 sem credencial: troca o e-mail e o Enviar e-mail dispara o POST, sem /activity", async () => {
  const { page, fechar, reqs, bloqueados } = await abre(SEM);
  try {
    await page.route("**/settings/1/security/contact", (r) => r.fulfill(json({ email: "novo@b.com", plan: "pro" })));
    await page.route("**/settings/1/password-reset", (r) => r.fulfill(json({ message: "Link enviado para novo@b.com" })));
    const toast = () => page.evaluate(() => document.getElementById("toast").textContent);
    await page.click("#security-email-edit-btn");
    await page.fill("#security-email-input", "novo@b.com");
    await page.evaluate(() => saveSecurityContact("email"));
    const patch = reqs.find(([p, , r]) => p === "/settings/1/security/contact" && r.method() === "PATCH");
    assert.ok(patch, "PATCH do contato não saiu");
    assert.deepEqual(JSON.parse(patch[2].postData()), { email: "novo@b.com" });
    await waitFor(async () => (await toast()).includes("E-mail atualizado"), "toast do e-mail");
    await page.click("#security-reset-btn");
    await waitFor(async () => (await toast()).includes("Link enviado para novo@b.com"), "toast do link");
    await sleep(300);   // o finally do envio recarrega a segurança
    assert.equal(reqs.filter(([p, , r]) => p === "/settings/1/password-reset" && r.method() === "POST").length, 1);
    assert.deepEqual(bloqueados(), [], "a ação puxou carregador barrado");
  } finally { await fechar(); }
});

// ── liberar: PTR e volta do foco (D2) ─────────────────────────────────────────

test("T5a PBRefresh com a conta ainda sem senha: resolve, continua travada, sem 403", async () => {
  const { page, fechar, bloqueados } = await abre(SEM);
  try {
    await marca(page);
    await page.evaluate(() => window.PBRefresh());
    await sleep(300);
    assert.ok(await naoRecarregou(page), "recarregou sem a senha existir");
    assert.deepEqual(await visiveis(page), ["security"]);
    assert.deepEqual(bloqueados(), []);
  } finally { await fechar(); }
});

const liberou = async (page, reqs) => {
  await bootou(page);
  assert.deepEqual(await visiveis(page), SECOES, "não liberou o menu");
  assert.equal((await caixa(page, "#sidebar-lock")).h, 0);
  await waitFor(() => reqs.some(([p]) => p === "/open-finance/1"), "o loadData do modo livre");
};

for (const [rotulo, dispara] of [
  ["PBRefresh", (page) => page.evaluate(() => { window.PBRefresh(); })],
  ["visibilitychange", (page) => page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")))],
  ["pageshow do bfcache", (page) => page.evaluate(() => window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true })))],
]) {
  test(`T5b senha criada noutra aba + ${rotulo}: recarrega e libera`, async () => {
    const { page, fechar, reqs, estado } = await abre(SEM);
    try {
      estado.me = LIVRE;
      const recarga = page.waitForEvent("load");
      await dispara(page);
      await recarga;
      await liberou(page, reqs);
    } finally { await fechar(); }
  });
}

test("T5c volta do foco NÃO recarrega: rascunho no editor, /auth/me falhando, conta ainda sem senha", async () => {
  const { page, fechar, estado } = await abre(SEM);
  const volta = () => page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
  try {
    await marca(page);
    await volta();   // /auth/me ainda diz precisa_criar_senha: nada de loop
    estado.meStatus = 500; estado.me = LIVRE;
    await volta();   // falha: fail-open, a tela fica como está
    await sleep(400);
    assert.ok(await naoRecarregou(page), "recarregou sem motivo");
    estado.meStatus = 200;
    await page.click("#security-email-edit-btn");
    await page.fill("#security-email-input", "rascunho@b.com");
    await volta();
    await page.evaluate(() => { window.PBRefresh(); });
    await sleep(600);
    assert.ok(await naoRecarregou(page), "recarregou por cima do rascunho");
    assert.equal(await page.inputValue("#security-email-input"), "rascunho@b.com");
    await page.evaluate(() => toggleSecurityEdit("email", false));   // rascunho largado: agora pode
    const recarga = page.waitForEvent("load");
    await volta();
    await recarga;
  } finally { await fechar(); }
});

// ── (b)(c) positivo: com credencial e só-Google, igual a antes ────────────────

for (const [rotulo, me] of [
  ["/auth/me = {} (campo ausente)", { of_ui_enabled: true }],
  ["com senha (precisa_criar_senha:false)", LIVRE],
  ["só-Google (has_password:false, precisa_criar_senha:false)", { has_password: false, precisa_criar_senha: false, of_ui_enabled: true }],
]) {
  test(`T6 ${rotulo}: menu inteiro, todos os carregadores, ordem dos cards de hoje`, async () => {
    const { page, fechar, reqs } = await abre(me);
    try {
      await waitFor(() => reqs.some(([p]) => p === "/open-finance/1") && reqs.some(([p]) => p.startsWith("/settings/1/activity")), "OF e atividade");
      assert.deepEqual(await visiveis(page), SECOES);
      assert.equal((await caixa(page, "#sidebar-lock")).h, 0, "frase aparece para conta livre");
      for (const sel of [".pb-protect", ".activity-embed", "#mfa-card", "#sessions-card"])
        assert.ok((await caixa(page, sel)).h > 0, `${sel} sumiu`);
      const ordem = await page.evaluate(() => ["security-reset-card", "sessions-card", "mfa-card"].map((id) => document.getElementById(id).getBoundingClientRect().top));
      assert.ok(ordem[0] > ordem[1] && ordem[1] > ordem[2], `ordem dos cards mudou: ${ordem}`);
      assert.ok((await caixa(page, "#security-name-value")).top < (await caixa(page, "#security-email-value")).top, "o e-mail passou o nome");
      const mes = () => reqs.filter(([p]) => p === "/auth/me").length;
      const antes = mes();
      await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
      await sleep(300);
      assert.equal(mes(), antes, "conta livre rebuscou o /auth/me no foco");
      await page.evaluate(() => showSettingsSection("notifications"));
      await waitFor(() => reqs.some(([p]) => p === "/settings/1/notifications"), "notificações");
      assert.deepEqual(await secaoAberta(page), ["notifications"]);
    } finally { await fechar(); }
  });
}

// ── ordem de foco = ordem visual (WCAG 2.4.3) ─────────────────────────────────
// `order` no CSS só repinta: Tab e leitor de tela seguem o DOM. Mede as duas ordens.
// Só <button>: o Chromium do macOS pula <a> no Tab (o "Gerenciar" do plano).
const BOTOES = ["security-reset-btn", "security-email-edit-btn", "security-name-edit-btn", "security-phone-edit-btn"];
for (const viewport of [{ width: 390, height: 844 }, { width: 1280, height: 800 }]) {
  for (const [rotulo, me] of [["sem credencial", SEM], ["com senha", LIVRE]]) {
    test(`T9 ${rotulo} ${viewport.width}x${viewport.height}: ordem de Tab da Segurança = ordem visual`, async () => {
      const { page, fechar } = await abre(me, "view=security", { viewport });
      try {
        await sleep(300);
        const tab = [];
        await page.evaluate(() => document.activeElement?.blur());
        for (let i = 0; i < 80 && tab.length < BOTOES.length; i++) {
          await page.keyboard.press("Tab");
          const id = await page.evaluate(() => document.activeElement?.id);
          if (BOTOES.includes(id) && !tab.includes(id)) tab.push(id);
        }
        const visual = await page.evaluate((ids) => ids.map((id) => [id, document.getElementById(id).getBoundingClientRect()])
          .sort(([, a], [, b]) => a.top - b.top || a.left - b.left).map(([id]) => id), BOTOES);
        assert.deepEqual(tab, visual, "a ordem de Tab não é a visual");
        const [reset, email, nome, fone] = BOTOES;   // travada: link da senha, e-mail, nome
        assert.deepEqual(tab, me === SEM ? BOTOES : [nome, email, fone, reset]);
      } finally { await fechar(); }
    });
  }
}

// ── corrida e falha do /auth/me ───────────────────────────────────────────────

test("T7 /auth/me lento (800ms) e sem credencial: nenhum carregador antes da resposta", async () => {
  const { page, fechar, reqs, estado, bloqueados } = await abre(SEM, "view=security", { meDelay: 800 });
  try {
    await sleep(500);
    assert.deepEqual(bloqueados(), []);
    const sec = reqs.find(([p]) => p === "/settings/1/security");
    assert.ok(sec && sec[1] >= estado.meRespondido, "a segurança saiu antes do /auth/me responder");
    assert.deepEqual(await visiveis(page), ["security"]);
  } finally { await fechar(); }
});

test("T8 /auth/me 500: tela livre como antes (fail-open, decisão do plano)", async () => {
  const { page, fechar } = await abre(SEM, "view=security", { meStatus: 500 });
  try {
    assert.deepEqual(await visiveis(page), SECOES);
    assert.equal((await caixa(page, "#sidebar-lock")).h, 0);
  } finally { await fechar(); }
});

// O array de ids do initSettings e o HTML ficam ~1700 linhas distantes: um id renomeado
// não pode derrubar o boot (sem seção, sem listeners, sem saída). Sem o `if (el)`, o
// TypeError aborta o initSettings e o `abre` estoura em "timeout esperando: o boot".
for (const id of ["security-reset-card", "security-email-item"]) {
  test(`T10 sem credencial com #${id} sumido do HTML: o boot segue, sem erro`, async () => {
    const { page, fechar, erros, estado, reqs, bloqueados } = await abre(SEM, "view=security",
      { html: (s) => s.replace(`id="${id}"`, `id="${id}-renomeado"`) });
    try {
      await sleep(300);
      assert.deepEqual(erros, [], "erro de página no boot");
      assert.deepEqual(await secaoAberta(page), ["security"]);
      assert.deepEqual(bloqueados(), [], "chamou carregador barrado");
      estado.me = LIVRE;   // os listeners de foco foram registrados: libera ao voltar
      const recarga = page.waitForEvent("load");
      await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
      await recarga;
      await liberou(page, reqs);
    } finally { await fechar(); }
  });
}
