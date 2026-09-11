/**
 * O estado "sem plano ativo" (402 do #380) em frontend/settings.html.
 *
 * A /settings continua abrindo para quem foi cortado — é a saída de emergência
 * (exportar os dados, excluir a conta). Nas seções cujas rotas NÃO são isentas
 * (`shared.authorize_account_access`) o 402 é ESTADO, não erro: copy no lugar da
 * lista, sem toast, e o pull-to-refresh RESOLVE em vez de tingir o indicador de
 * âmbar. Este arquivo é o controle executável disso — a metade de frontend do PR
 * não tinha nenhum.
 *
 * CONTROLES DECLARADOS (`docs/controles_declarados.md`) — injeção -> VERMELHO
 * ──────────────────────────────────────────────────────────────────────────
 * As quatro mudam um VALOR; nenhuma apaga código (a patologia do
 * `docs/controles_declarados.md`). Medidas em 2026-09-11, uma de cada vez.
 * 1. Em `refreshOpenFinance`, `if (resp.status === 402) {` -> `=== 999`. ->
 *    "open finance: 402 vira estado e o PTR resolve" (`__done` vira
 *    `rejected:Sua conta está sem plano ativo.`). Direção: o gesto fica ÂMBAR em
 *    cima de um painel que acabou de dizer "Indisponível".
 * 2. Em `loadNotificationSettings`,
 *    `document.getElementById("notif-tip").hidden = true;` -> `= false`. ->
 *    "notificações: 402 esconde lista e dica; o 200 seguinte devolve as duas".
 *    Direção: a tela oferece "pausar dicas e insights" logo abaixo de
 *    "Indisponível sem plano ativo".
 * 3. Em `readApiError`, `ERRO_DE_GATE.get(detail.error)` ->
 *    `Object.fromEntries(ERRO_DE_GATE)[detail.error]` (o lookup por literal que
 *    existia antes). -> "readApiError não lê o Object.prototype". Direção: chave
 *    do servidor cai na cadeia de protótipo, o toast recebe uma FUNÇÃO e o
 *    `detail.code` real some.
 * 4. Na CSS, `[hidden] { display: none !important; }` -> `[hidden] { }`. -> o
 *    mesmo teste do nº 2, pelos dois asserts de `display === "none"`. Direção: o
 *    atributo `hidden` perde para o `display` da classe e nada esconde.
 *
 * POSITIVO: "pagante não vê 'Indisponível' em seção nenhuma" — sem ele o grupo
 * passaria num código que pinta o estado de corte para todo mundo.
 *
 * Rodar:  npm run test:frontend
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
let ORIGIN;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const json = (body) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
/** O corpo EXATO que `_enforce_subscription_gate` levanta: sem `message`, sem `code`. */
const gate402 = () => ({ status: 402, contentType: "application/json",
                         body: JSON.stringify({ detail: { error: "subscription_required" } }) });
/** Campos como `_get_notification_settings` devolve (o front lê `*_enabled`). */
const NOTIF_OK = { email: "a@b.com", whatsapp_destination: "5511999999999",
                   email_notifications_available: true, whatsapp_updates_available: true,
                   tip_email_enabled: true, insight_email_enabled: true,
                   daily_report_enabled: true, daily_report_hour: 9 };

async function waitFor(cond, what, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await cond()) return;
    await sleep(50);
  }
  throw new Error(`timeout esperando: ${what}`);
}

let server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer());
                     browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

async function newPage({ ofUi = false } = {}) {
  const page = await browser.newPage();
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
  await page.route("**/auth/me", (route) => route.fulfill(json({ of_ui_enabled: ofUi })));
  await page.route("**/auth/mfa/status", (route) => route.fulfill(json({ enabled: false })));
  await page.addInitScript(() => {
    window.__unhandled = [];
    window.addEventListener("unhandledrejection", (e) =>
      window.__unhandled.push(String((e.reason && e.reason.message) || e.reason)));
  });
  return page;
}

const startPtr = (page) => page.evaluate(() => {
  window.__done = null;
  window.PBRefresh().then(() => "resolved", (e) => "rejected:" + ((e && e.message) || e))
    .then((v) => { window.__done = v; });
});

/** `className` do toast: "" = nunca apareceu; "show error" = apareceu como erro. */
const toastClass = (page) => page.evaluate(() => document.getElementById("toast").className);

test("open finance: 402 vira estado e o PTR resolve, sem toast de erro", async () => {
  const page = await newPage({ ofUi: true });
  try {
    await page.route("**/open-finance/1/refresh*", (route) => route.fulfill(gate402()));
    await page.route("**/open-finance/1", (route) => route.fulfill(gate402()));

    await page.goto(`${ORIGIN}/settings.html?view=open-finance`);
    await waitFor(async () => (await page.evaluate(
      () => document.getElementById("connections-list").textContent)).includes("Indisponível"),
      "o painel de conexões pintar o estado de corte");

    const carga = await page.evaluate(() => ({
      conexoes: document.getElementById("connections-list").textContent,
      cta: document.querySelector("#connections-list .empty-cta")?.getAttribute("href"),
      contas: document.getElementById("accounts-list").textContent,
      caixinhas: document.getElementById("caixinhas-card").style.display,
    }));
    assert.match(carga.conexoes, /Indisponível sem plano ativo/);
    assert.equal(carga.cta, "/precos", "o estado de corte precisa do caminho pro /precos");
    assert.match(carga.contas, /Indisponível sem plano ativo/);
    assert.equal(carga.caixinhas, "none", "card de caixinhas visível num painel indisponível");
    assert.equal(await toastClass(page), "", "402 no boot não pode virar toast");

    // O gesto: sem o ramo de 402 no refreshOpenFinance ele REJEITA (indicador âmbar).
    await startPtr(page);
    await waitFor(() => page.evaluate(() => window.__done !== null), "PBRefresh assentar");
    await sleep(200);
    const out = await page.evaluate(() => ({ done: window.__done, unhandled: window.__unhandled }));
    assert.equal(out.done, "resolved", `402 é estado, não falha do gesto; deu: ${out.done}`);
    assert.deepEqual(out.unhandled, [], "rejeição solta na página");
    assert.ok(!(await toastClass(page)).includes("error"), "o PTR cortado não pode toastar erro");
  } finally { await page.close(); }
});

test("notificações: 402 esconde lista e dica; o 200 seguinte devolve as duas", async () => {
  const page = await newPage();
  let chamadas = 0;
  try {
    await page.route("**/settings/1/notifications", (route) => {
      chamadas++;
      if (chamadas === 1) return route.fulfill(gate402());
      route.fulfill(json(NOTIF_OK));
    });

    await page.goto(`${ORIGIN}/settings.html?view=notifications`);
    await waitFor(() => chamadas >= 1, "o GET de notificações");
    await waitFor(async () => (await page.evaluate(
      () => document.getElementById("notif-sem-plano").textContent)).includes("Indisponível"),
      "o estado de corte pintar");

    const cortado = await page.evaluate(() => ({
      lista: getComputedStyle(document.getElementById("notif-list")).display,
      dica: getComputedStyle(document.getElementById("notif-tip")).display,
      status: document.getElementById("notif-status-value").textContent,
      destino: document.getElementById("notif-email-value").textContent,
    }));
    assert.equal(cortado.lista, "none", "os toggles continuam na tela sob 'Indisponível'");
    assert.equal(cortado.dica, "none",
      "a dica 'pausar dicas e insights' contradiz o 'Indisponível' logo acima");
    assert.equal(cortado.status, "Indisponível sem plano ativo");
    assert.equal(cortado.destino, "—");
    assert.equal(await toastClass(page), "", "402 de notificações não pode virar toast");

    // Recuperação: o plano volta e o próximo GET responde 200.
    await startPtr(page);
    await waitFor(() => page.evaluate(() => window.__done !== null), "PBRefresh assentar");
    const voltou = await page.evaluate(() => ({
      done: window.__done,
      lista: getComputedStyle(document.getElementById("notif-list")).display,
      dica: getComputedStyle(document.getElementById("notif-tip")).display,
      semPlano: getComputedStyle(document.getElementById("notif-sem-plano")).display,
      toggle: document.getElementById("notif-daily-report").disabled,
    }));
    assert.equal(voltou.done, "resolved", `a recuperação não pode rejeitar, deu: ${voltou.done}`);
    assert.notEqual(voltou.lista, "none", "a lista não voltou depois do 200");
    assert.notEqual(voltou.dica, "none", "a dica não voltou depois do 200");
    assert.equal(voltou.semPlano, "none", "o bloco 'Indisponível' ficou por cima do 200");
    assert.equal(voltou.toggle, false, "os toggles ficaram desabilitados depois do 200");
  } finally { await page.close(); }
});

test("pagante não vê 'Indisponível' em seção nenhuma (positivo)", async () => {
  const page = await newPage({ ofUi: true });
  try {
    await page.route("**/open-finance/1/caixinhas", (route) => route.fulfill(json({ caixinhas: [], metas: [] })));
    await page.route("**/open-finance/1", (route) =>
      route.fulfill(json({ connections: [], accounts: [], transactions: [] })));
    await page.route("**/settings/1/notifications", (route) => route.fulfill(json(NOTIF_OK)));

    await page.goto(`${ORIGIN}/settings.html?view=open-finance`);
    await waitFor(async () => (await page.evaluate(
      () => document.getElementById("connections-list").textContent)).includes("Nenhum banco"),
      "o estado vazio normal do pagante");

    await page.evaluate(() => window.showSettingsSection("notifications"));
    await waitFor(async () => (await page.evaluate(
      () => document.getElementById("notif-email-value").textContent)).includes("a@b.com"),
      "as preferências do pagante carregarem");

    const tela = await page.evaluate(() => ({
      // innerText e NÃO textContent: o SEM_PLANO_HTML é um template literal
      // dentro do <script> inline, e textContent do body o inclui — o assert
      // ficava vermelho lendo o CÓDIGO em vez da TELA.
      corpo: document.body.innerText,
      dica: getComputedStyle(document.getElementById("notif-tip")).display,
      lista: getComputedStyle(document.getElementById("notif-list")).display,
    }));
    assert.ok(!tela.corpo.includes("Indisponível sem plano ativo"),
      "o estado de corte apareceu para quem está pagando");
    assert.notEqual(tela.dica, "none", "o pagante perdeu a dica das notificações");
    assert.notEqual(tela.lista, "none", "o pagante perdeu os toggles");
  } finally { await page.close(); }
});

test("readApiError não lê o Object.prototype", async () => {
  const page = await newPage();
  try {
    await page.goto(`${ORIGIN}/settings.html?view=data`);
    await page.waitForFunction(() => typeof window.readApiError === "function");
    const lido = await page.evaluate(async () => {
      const fake = (detail) => ({ text: async () => JSON.stringify({ detail }) });
      return {
        proto: await window.readApiError(fake({ error: "constructor", code: "CODIGO_REAL" })),
        gate: await window.readApiError(fake({ error: "subscription_required" })),
        code: await window.readApiError(fake({ code: "OF_BANK_LIMIT" })),
      };
    });
    // Com o objeto literal isto vinha "function Object() { [native code] }".
    assert.equal(lido.proto, "CODIGO_REAL", "chave do servidor caiu na cadeia de protótipo");
    assert.equal(lido.gate, "Sua conta está sem plano ativo.", "o mapa do gate parou de traduzir");
    assert.equal(lido.code, "OF_BANK_LIMIT", "o caminho pré-existente do detail.code quebrou");
  } finally { await page.close(); }
});
