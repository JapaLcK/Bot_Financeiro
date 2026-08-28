/**
 * Visibilidade real dos elementos que o wizard controla por `hidden`.
 *
 * ESTE TESTE EXISTE POR CAUSA DE UM BUG QUE CHEGOU EM PRODUÇÃO. O atributo
 * `hidden` só esconde por causa de `[hidden]{display:none}` na folha do
 * NAVEGADOR, e qualquer regra de autor que declare `display` ganha dela — não
 * por especificidade (ambas 0,1,0), mas porque folha de autor vence folha de
 * user-agent. Como `.onb-done` declara `display:flex` e `.btn` declara
 * `display:inline-flex`, quatro elementos ficavam SEMPRE visíveis:
 * "Você já tem saldo lançado", "WhatsApp já conectado", o botão do WhatsApp e
 * o "+ Adicionar cartão". O usuário viu o primeiro no passo 2, antes de lançar
 * qualquer saldo.
 *
 * A verificação que deixou isso passar media `el.hasAttribute("hidden")` — que
 * estava CERTO o tempo todo. Só o estilo computado revela o defeito, e é por
 * isso que este arquivo usa browser de verdade em vez do vm dos outros testes:
 * um teste sem CSS é estruturalmente cego a esta classe inteira.
 *
 * Controle negativo: apagar a regra `[hidden]{display:none!important}` de
 * comecar.css deixa os quatro casos vermelhos.
 *
 * Precisa de `npm ci` na raiz (playwright) + `npx playwright install chromium`.
 * Rodar: node --test tests/frontend/onboarding_visibility.test.mjs
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
// Porta própria: 8899 settings_security_fanout, 8901 hiw_rail, 8903 modal_keys,
// 8905 of_refresh_ui. Repetir uma faz o http.server morrer com "Address already
// in use" e o arquivo fica vermelho por colisão, não por defeito.
const PORT = Number(process.env.PB_ONB_TEST_PORT || 8907);
const ORIGIN = `http://127.0.0.1:${PORT}`;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const json = (body) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });

async function startServer() {
  const proc = spawn("python3", ["-m", "http.server", String(PORT), "--bind", "127.0.0.1", "--directory", FRONTEND],
    { stdio: "ignore" });
  for (let i = 0; i < 100; i++) {
    await sleep(100);
    if (proc.exitCode !== null) throw new Error(`http.server morreu (porta ${PORT} ocupada?)`);
    try { if ((await fetch(`${ORIGIN}/comecar.html`)).ok) return proc; } catch { /* ainda subindo */ }
  }
  proc.kill();
  throw new Error(`http.server não subiu em ${ORIGIN}`);
}

let server, browser;
before(async () => { server = await startServer(); browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

/** Abre o wizard com o backend simulado. `waLinked` troca o cenário do passo 3. */
async function abrirWizard({ balance = 0, waLinked = false } = {}) {
  const page = await browser.newPage();

  // O catch-all vem PRIMEIRO de propósito: no Playwright a última rota
  // registrada é a que ganha, então as específicas abaixo têm de vir depois.
  // Registrar na ordem inversa faz o genérico engolir todas elas.
  await page.route("**/*", (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== ORIGIN) return route.abort();          // CDN bloqueado
    if (/\.[a-z0-9]+$/i.test(url.pathname)) return route.continue();
    return route.fulfill(json({}));
  });
  await page.route("**/static/auth-refresh.js", (route) =>
    route.fulfill({ status: 200, contentType: "application/javascript",
                    body: readFileSync(join(FRONTEND, "static", "auth-refresh.js"), "utf8") }));
  await page.route("**/auth/dashboard-profile", (route) =>
    route.fulfill(json({ user_id: 1, display_name: "Lucas", plan: "free" })));
  await page.route("**/onboarding/state", (route) =>
    route.fulfill(json({ step: 1, completed: false, total_steps: 5 })));
  await page.route("**/account/1/setup-status", (route) =>
    route.fulfill(json({ ok: true, balance, n_launches: 0, n_cards: 0 })));
  await page.route("**/cards/1/summary", (route) => route.fulfill(json({ ok: true, cards: [] })));
  await page.route("**/settings/1/security", (route) =>
    route.fulfill(json({ ok: true, identities: waLinked ? [{ provider: "whatsapp" }] : [] })));
  await page.route("**/auth/link-code", (route) =>
    route.fulfill(json({ link_code: "482913", whatsapp_link: "https://api.whatsapp.com/send?phone=55" })));
  await page.route("**/settings/1/notifications", (route) =>
    route.fulfill(json({ ok: true, daily_report_enabled: true, weekly_report_enabled: true,
                         monthly_report_enabled: true, daily_report_hour: 9 })));

  await page.goto(`${ORIGIN}/comecar.html`);
  await page.waitForSelector('.onb-step[data-step="1"]:not([hidden])');
  return page;
}

const display = (page, role) =>
  page.$eval(`[data-role="${role}"]`, (e) => getComputedStyle(e).display);

async function irPara(page, passo) {
  await page.click(`.onb-step[data-step="${passo - 1}"] [data-action="next"]`);
  await page.waitForSelector(`.onb-step[data-step="${passo}"]:not([hidden])`);
}

// ── Passo 2 ─────────────────────────────────────────────────────────────────

test("conta sem saldo NÃO mostra o aviso de saldo já lançado", async () => {
  const page = await abrirWizard({ balance: 0 });
  await irPara(page, 2);
  await page.waitForFunction(() =>
    !document.querySelector('[data-role="balance-form"]').hasAttribute("hidden"));

  assert.equal(await display(page, "balance-done"), "none",
    'o aviso verde aparecia junto com o formulário, antes de o usuário lançar qualquer saldo');
  assert.notEqual(await display(page, "balance-form"), "none");
  await page.close();
});

test("conta COM saldo mostra o aviso e esconde o formulário", async () => {
  // Controle positivo do par: prova que a regra não escondeu tudo pra sempre.
  const page = await abrirWizard({ balance: 1250.4 });
  await irPara(page, 2);
  await page.waitForFunction(() =>
    document.querySelector('[data-role="balance-form"]').hasAttribute("hidden"));

  assert.equal(await display(page, "balance-done"), "flex");
  assert.equal(await display(page, "balance-form"), "none");
  await page.close();
});

test("o formulário de cartão só aparece depois de clicar em adicionar", async () => {
  const page = await abrirWizard();
  await irPara(page, 2);
  assert.equal(await display(page, "card-form"), "none");
  assert.notEqual(await display(page, "add-card-btn"), "none");

  await page.click('[data-action="add-card"]');
  assert.notEqual(await display(page, "card-form"), "none");
  assert.equal(await display(page, "add-card-btn"), "none",
    'o botão "+ Adicionar cartão" continuava visível com o formulário aberto');
  await page.close();
});

// ── Passo 3 ─────────────────────────────────────────────────────────────────

test("sem WhatsApp vinculado, mostra o código e esconde a confirmação", async () => {
  const page = await abrirWizard({ waLinked: false });
  await irPara(page, 2);
  await irPara(page, 3);
  await page.waitForFunction(() =>
    !document.querySelector('[data-role="wa-pending"]').hasAttribute("hidden"));

  assert.equal(await display(page, "wa-linked"), "none",
    '"WhatsApp já conectado" aparecia junto com o pedido de vínculo');
  assert.notEqual(await display(page, "wa-pending"), "none");
  assert.equal(await page.$eval('[data-role="wa-code"]', (e) => e.textContent), "link 482913");
  await page.close();
});

test("com WhatsApp vinculado, mostra a confirmação e esconde o código", async () => {
  const page = await abrirWizard({ waLinked: true });
  await irPara(page, 2);
  await irPara(page, 3);
  await page.waitForFunction(() =>
    !document.querySelector('[data-role="wa-linked"]').hasAttribute("hidden"));

  assert.equal(await display(page, "wa-linked"), "flex");
  assert.equal(await display(page, "wa-pending"), "none");
  await page.close();
});

// ── A regra em si ───────────────────────────────────────────────────────────

test("comecar.css neutraliza o [hidden] contra as regras de display", () => {
  const css = readFileSync(join(FRONTEND, "comecar.css"), "utf8");
  assert.match(css, /\[hidden\]\s*\{[^}]*display:\s*none\s*!important/,
    "sem esse reset, .onb-done e .btn voltam a vencer o [hidden] do navegador");
});
