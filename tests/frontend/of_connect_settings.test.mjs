/**
 * Fluxo "conectar banco" do Open Finance, pela tela de Configurações.
 *
 * ESTE ARQUIVO É A REDE DE SEGURANÇA DA EXTRAÇÃO. Ele foi escrito e ficou verde
 * contra o settings.html ANTES de o fluxo sair de lá para
 * frontend/open-finance-connect.js. É o que transforma "a extração não mudou
 * nada" de opinião em medição: o MESMO arquivo tem de passar antes e depois.
 *
 * Por isso ele testa pelo comportamento observável na página — abrir, filtrar,
 * selecionar, teclado, e as regras de plano — e nunca pelos nomes internos das
 * funções, que a extração muda de propósito.
 *
 * Três casos merecem nota, porque protegem coisas que já custaram caro:
 *
 *  - Esc e Tab: o trap de foco nasceu de um apontamento de revisão (o mesmo
 *    defeito do modal da Início no #70). O modal declara aria-modal="true", o
 *    que AFIRMA pro leitor de tela que o resto da página não está disponível;
 *    sem trap isso é mentira.
 *  - Teto do plano atingido: bloquear ANTES de abrir a Pluggy evita item e
 *    consentimento órfãos — o /pluggy-item recusaria com 402 depois de o banco
 *    já ter autorizado.
 *  - Reconexão do mesmo banco: é o controle POSITIVO do par acima. Sem ele, um
 *    bloqueio que recusasse tudo passaria no teste — e seria pior que o bug.
 *
 * Precisa de `npm ci` na raiz (playwright) + `npx playwright install chromium`.
 * Rodar: node --test tests/frontend/of_connect_settings.test.mjs
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
// Porta própria: 8899 fanout, 8901 hiw_rail, 8903 modais, 8905 of_refresh,
// 8907 onboarding_visibility. O `node --test` roda os arquivos em PARALELO e
// duas suítes na mesma porta se matam.
const PORT = Number(process.env.PB_OFCONN_TEST_PORT || 8909);
const ORIGIN = `http://127.0.0.1:${PORT}`;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const json = (body) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });

const BANCOS = [
  { id: 201, name: "Banco do Brasil", color: "0033a0", logo: "", inv: false },
  { id: 612, name: "Nubank",          color: "820ad1", logo: "", inv: true  },
  { id: 601, name: "Itaú",            color: "ec7000", logo: "", inv: false },
];

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

let server, browser;
before(async () => { server = await startServer(); browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

/**
 * Abre o Settings já na aba de Open Finance, com o backend simulado.
 * `banksMax` é o teto do plano; `conexoes` são as conexões existentes.
 */
async function abrirSettings({ banksMax = 2, conexoes = [] } = {}) {
  const ctx = await browser.newContext();
  const page = await ctx.newPage();

  // Genérico PRIMEIRO: no Playwright a última rota registrada é a que ganha.
  await page.route("**/*", (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== ORIGIN) return route.abort();      // CDN (pluggy, chart) fora
    if (/\.[a-z0-9]+$/i.test(url.pathname)) return route.continue();
    return route.fulfill(json({}));
  });
  await page.route("**/static/auth-refresh.js", (route) =>
    route.fulfill({ status: 200, contentType: "application/javascript",
                    body: readFileSync(join(FRONTEND, "auth-refresh.js"), "utf8") }));
  await page.route("**/auth/validate", (route) => route.fulfill(json({ user_id: 1 })));
  await page.route("**/auth/me", (route) =>
    route.fulfill(json({ app_access: true, of_ui_enabled: true, of_banks_max: banksMax })));
  await page.route("**/open-finance/1/connectors", (route) =>
    route.fulfill(json({ connectors: BANCOS })));
  await page.route("**/open-finance/1", (route) =>
    route.fulfill(json({ connections: conexoes, accounts: [], transactions: [] })));
  // A /precos é destino real do CTA de upgrade no Free: serve uma página inerte
  // pra a navegação acontecer sem sair do servidor de teste.
  await page.route("**/precos**", (route) =>
    route.fulfill({ status: 200, contentType: "text/html", body: "<title>precos</title>" }));

  await page.goto(`${ORIGIN}/settings.html?view=open-finance`);

  // Esperar TEXTO no botão não serve: ele já nasce com rótulo no HTML, e o
  // comportamento só chega depois do /auth/me. O sinal certo é o handler ter
  // sido atribuído — e as conexões terem sido renderizadas, que é o que fixa a
  // contagem usada pelas regras de teto.
  await page.waitForFunction(() => {
    const b = document.getElementById("connect-btn");
    const lista = document.getElementById("connections-list");
    return !!(b && b.onclick && lista && lista.children.length > 0);
  });
  page.__ctx = ctx;
  return page;
}

const pickerAberto = (page) =>
  page.$eval("#bankpick-overlay", (e) => e.classList.contains("open"));

const linhasVisiveis = (page) =>
  page.$$eval("#bankpick-list .bank-row", (rs) => rs.map((r) => r.getAttribute("data-name")));

async function abrirPicker(page) {
  await page.click("#connect-btn");
  await page.waitForFunction(() =>
    document.querySelectorAll("#bankpick-list .bank-row").length > 0);
}

// ── Caminho feliz do picker ─────────────────────────────────────────────────

test("o botão de conectar abre o picker com a lista de bancos", async () => {
  const page = await abrirSettings();
  assert.equal(await pickerAberto(page), false, "o picker não pode nascer aberto");
  await abrirPicker(page);

  assert.equal(await pickerAberto(page), true);
  assert.deepEqual(await linhasVisiveis(page), BANCOS.map((b) => b.name));
  await page.__ctx.close();
});

test("a busca filtra a lista", async () => {
  const page = await abrirSettings();
  await abrirPicker(page);

  await page.fill("#bankpick-search", "nu");
  await page.waitForFunction(() =>
    document.querySelectorAll("#bankpick-list .bank-row").length === 1);
  assert.deepEqual(await linhasVisiveis(page), ["Nubank"]);

  // Sem acento na busca tem de achar o nome acentuado.
  await page.fill("#bankpick-search", "itau");
  await page.waitForFunction(() =>
    document.querySelectorAll("#bankpick-list .bank-row").length === 1);
  assert.deepEqual(await linhasVisiveis(page), ["Itaú"]);
  await page.__ctx.close();
});

test("selecionar um banco habilita o CTA e mostra o nome escolhido", async () => {
  const page = await abrirSettings();
  await abrirPicker(page);

  assert.equal(await page.$eval("#bankpick-go", (b) => b.disabled), true,
    "o CTA tem de nascer desabilitado");

  await page.click('#bankpick-list .bank-row[data-name="Nubank"]');
  assert.equal(await page.$eval("#bankpick-go", (b) => b.disabled), false);
  assert.match(await page.$eval("#bankpick-count", (e) => e.textContent), /Nubank/);
  await page.__ctx.close();
});

// ── Teclado: o trap que o aria-modal promete ────────────────────────────────

test("Esc fecha o picker e devolve o foco a quem abriu", async () => {
  const page = await abrirSettings();
  await abrirPicker(page);

  await page.keyboard.press("Escape");
  assert.equal(await pickerAberto(page), false);
  assert.equal(await page.evaluate(() => document.activeElement?.id), "connect-btn",
    "o foco tem de voltar pro botão que abriu, senão o teclado cai no topo da página");
  await page.__ctx.close();
});

test("Tab não escapa do modal", async () => {
  const page = await abrirSettings();
  await abrirPicker(page);

  for (let i = 0; i < 12; i++) await page.keyboard.press("Tab");
  const dentro = await page.evaluate(() => {
    const modal = document.querySelector("#bankpick-overlay .bankpick-modal");
    return !!(modal && modal.contains(document.activeElement));
  });
  assert.equal(dentro, true, "o foco vazou do modal — o aria-modal vira mentira");
  await page.__ctx.close();
});

// ── Regras de plano ─────────────────────────────────────────────────────────

test("plano sem Open Finance não abre o picker: vira CTA de upgrade", async () => {
  const page = await abrirSettings({ banksMax: 0 });

  assert.match(await page.$eval("#connect-btn", (b) => b.className), /btn-connect--upgrade/);
  await page.click("#connect-btn");
  await page.waitForURL(/\/precos/);
  assert.equal(await pickerAberto(page).catch(() => false), false);
  await page.__ctx.close();
});

test("teto do plano atingido bloqueia banco NOVO antes de abrir a Pluggy", async () => {
  // Bloquear aqui é o que evita item e consentimento órfãos: o /pluggy-item
  // recusaria com 402 depois de o banco já ter autorizado.
  const page = await abrirSettings({
    banksMax: 1,
    conexoes: [{ id: 9, institution_name: "Nubank", status: "UPDATED" }],
  });
  await abrirPicker(page);
  await page.click('#bankpick-list .bank-row[data-name="Itaú"]');
  await page.click("#bankpick-go");

  assert.equal(await pickerAberto(page), true,
    "o picker tem de continuar aberto — nada de seguir pro widget");
  await page.__ctx.close();
});

test("teto atingido AINDA permite reconectar o mesmo banco", async () => {
  // Controle positivo do par: sem ele, um bloqueio que recusasse tudo passaria.
  const page = await abrirSettings({
    banksMax: 1,
    conexoes: [{ id: 9, institution_name: "Nubank", status: "UPDATED" }],
  });
  await abrirPicker(page);
  await page.click('#bankpick-list .bank-row[data-name="Nubank"]');
  await page.click("#bankpick-go");

  await page.waitForFunction(() =>
    !document.getElementById("bankpick-overlay").classList.contains("open"));
  assert.equal(await pickerAberto(page), false,
    "reconexão do mesmo banco tem de passar pelo bloqueio");
  await page.__ctx.close();
});

test("conexão PAUSED não consome o teto do plano", async () => {
  // Espelha a contagem do backend (_ofCountsTowardBankLimit).
  const page = await abrirSettings({
    banksMax: 1,
    conexoes: [{ id: 9, institution_name: "Nubank", status: "PAUSED" }],
  });
  await abrirPicker(page);
  await page.click('#bankpick-list .bank-row[data-name="Itaú"]');
  await page.click("#bankpick-go");

  await page.waitForFunction(() =>
    !document.getElementById("bankpick-overlay").classList.contains("open"));
  assert.equal(await pickerAberto(page), false,
    "com a única conexão pausada, o teto não está atingido");
  await page.__ctx.close();
});
