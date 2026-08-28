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

// No Ubuntu do CI o interpretador é `python3`; no Windows esse nome resolve pro
// stub da Microsoft Store e o servidor morre na hora, com todos os casos
// vermelhos por "porta ocupada" — que é o sintoma errado da causa certa.
const PY = process.env.PB_PYTHON || (process.platform === "win32" ? "python" : "python3");

async function startServer() {
  const proc = spawn(PY, ["-m", "http.server", String(PORT), "--bind", "127.0.0.1",
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
async function abrirSettings({ banksMax = 2, conexoes = [], conectores = BANCOS } = {}) {
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
    route.fulfill(json({ connectors: conectores })));
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

/**
 * O picker está VISÍVEL? Nó ausente conta como fechado.
 *
 * Perguntar pelo nó ("existe e não tem .open") seria asserção sobre
 * implementação: antes da extração o overlay morava no HTML e nascia escondido;
 * depois, o módulo o injeta na primeira abertura. As duas coisas são o mesmo
 * fato para quem usa a tela — não há picker na frente dele —, e é esse fato que
 * o teste tem de fixar para valer antes e depois.
 *
 * Mede display computado, não a classe: `.bankpick-overlay` é `display:none` e
 * só `.open` a torna `flex`, então é o computado que diz a verdade.
 */
const pickerAberto = (page) =>
  page.evaluate(() => {
    const el = document.getElementById("bankpick-overlay");
    return !!el && getComputedStyle(el).display !== "none";
  });

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

test("estado de plano indisponível não deixa CONECTAR", async () => {
  // A garantia é a mesma que o Codex pediu (não autorizar na Pluggy sem saber o
  // teto), mas no ponto certo: o modal abre normalmente — abrir não autoriza
  // nada — e quem espera pelos limites é o confirmar, que é de onde a Pluggy é
  // aberta e de onde sairia o consentimento órfão.
  //
  // Controle negativo: fazer o confirmPick() seguir quando refreshLimits()
  // devolve false deixa este caso vermelho.
  const page = await abrirSettings({ banksMax: 1 });
  // Derruba o /auth/me ANTES de abrir: a busca de limites parte do open(), e o
  // confirmar espera por ela.
  await page.route("**/auth/me", (route) => route.fulfill({ status: 500, body: "{}" }));
  await abrirPicker(page);
  await page.click('#bankpick-list .bank-row[data-name="Nubank"]');
  await page.click("#bankpick-go");
  await sleep(600);

  assert.equal(await pickerAberto(page), true,
    "seguiu pro widget sem conhecer o teto do plano");
  await page.__ctx.close();
});

test("fechar durante a espera cancela o confirmar", async () => {
  // Mover a espera dos limites pro confirmar criou uma janela que o confirmar
  // original (síncrono) não tinha: Esc, clique no fundo ou o X fecham o picker
  // enquanto os limites ainda vêm — e sem a checagem a Pluggy abriria depois,
  // sozinha, sobre uma tela que o usuário já dispensou.
  //
  // Controle negativo: remover o `if (!root || !root.classList...) return;` de
  // depois do await deixa este caso vermelho.
  const page = await abrirSettings();
  let liberar;
  const presa = new Promise((r) => { liberar = r; });
  await page.route("**/auth/me", async (route) => {
    await presa;
    route.fulfill(json({ app_access: true, of_ui_enabled: true, of_banks_max: 2 }));
  });
  // Conta quantas vezes o fluxo seguiu pra Pluggy.
  let tokens = 0;
  await page.route("**/open-finance/1/connect-token", (route) => {
    tokens += 1;
    route.fulfill(json({ accessToken: "x" }));
  });

  await abrirPicker(page);
  await page.click('#bankpick-list .bank-row[data-name="Nubank"]');
  await page.click("#bankpick-go");     // limites ainda presos
  await page.keyboard.press("Escape");  // usuário desiste
  await liberar();
  await sleep(600);

  assert.equal(tokens, 0, "seguiu pra Pluggy depois de o usuário fechar o picker");
  assert.equal(await pickerAberto(page), false);
  await page.__ctx.close();
});

test("destroy() cancela o que está em voo e a Pluggy não abre depois", async () => {
  // Quatro rodadas de revisão bateram nesta mesma classe — "operação em voo
  // sobrevive ao destroy()" — uma fronteira assíncrona por vez. A resposta aqui
  // não é mais uma guarda depois do await: `destroy()` ABORTA as requisições,
  // então a continuação nem roda. Este caso fixa a pior delas, o /connect-token,
  // cuja continuação abriria o widget da Pluggy sobre uma tela desmontada.
  //
  // Controle negativo: tirar o `abortInflight()` do destroy() deixa este caso
  // vermelho.
  const page = await abrirSettings();
  // Fábrica falsa da Pluggy: registra se alguém tentou construir o widget.
  await page.addInitScript(() => {
    window.__widgets = 0;
    window.PluggyConnect = function () { window.__widgets += 1; this.init = function () {}; };
  });
  await page.reload();
  await page.waitForFunction(() => {
    const b = document.getElementById("connect-btn");
    const l = document.getElementById("connections-list");
    return !!(b && b.onclick && l && l.children.length > 0);
  });

  let liberar;
  const presa = new Promise((r) => { liberar = r; });
  await page.route("**/open-finance/1/connect-token", async (route) => {
    await presa;
    route.fulfill(json({ accessToken: "x" }));
  });

  await abrirPicker(page);
  await page.click('#bankpick-list .bank-row[data-name="Nubank"]');
  await page.click("#bankpick-go");                       // dispara o connect-token
  await page.evaluate(() => window.PBOpenFinance.destroy());
  await liberar();
  await sleep(600);

  assert.equal(await page.evaluate(() => window.__widgets), 0,
    "a Pluggy abriu depois do destroy()");
  await page.__ctx.close();
});

test("mensagem estruturada do backend chega ao usuário", async () => {
  // Apontamento P2 do Codex: o detail do FastAPI vem string OU objeto. Aceitar
  // só string trocaria a mensagem acionável do limite de plano por um genérico.
  const page = await abrirSettings();
  await page.route("**/open-finance/1/connect-token", (route) =>
    route.fulfill({
      status: 402,
      contentType: "application/json",
      body: JSON.stringify({ detail: { code: "OF_BANK_LIMIT", message: "Seu plano acabou: /precos" } }),
    }));

  await abrirPicker(page);
  await page.click('#bankpick-list .bank-row[data-name="Nubank"]');
  await page.click("#bankpick-go");

  await page.waitForFunction(() => {
    const t = document.getElementById("toast");
    return t && /Seu plano acabou/.test(t.textContent || "");
  }, { timeout: 8000 });
  await page.__ctx.close();
});

test("o CTA do modal não estica pelo rodapé nem espreme o nome do banco", async () => {
  // Ordem da cascata, não especificidade: `.bankpick-go{width:auto}` (no CSS do
  // componente) e `.btn-connect{width:100%}` (inline do settings) empatam em
  // 0,1,0, então vence a última declarada. Com o <link> antes do <style>, o
  // botão passou a ocupar o rodapé inteiro e quebrar o "Selecionado: <banco>"
  // em duas linhas — medido: botão 330px e rótulo 180px quebrado, contra
  // 207px/301px numa linha depois do conserto.
  //
  // Controle negativo: mover o <link> pra antes do <style> no settings.html
  // deixa este caso vermelho.
  const page = await abrirSettings({
    conectores: [{ id: 612, name: "Banco com Nome Bem Longo S.A.", color: "820ad1", logo: "", inv: true }],
  });
  await abrirPicker(page);
  await page.click("#bankpick-list .bank-row");

  const m = await page.evaluate(() => {
    const go = document.getElementById("bankpick-go");
    const cnt = document.getElementById("bankpick-count");
    const r = (e) => e.getBoundingClientRect();
    return {
      botao: Math.round(r(go).width),
      rodape: Math.round(r(go.parentElement).width),
      rotulo: Math.round(r(cnt).width),
    };
  });

  // Proporções, não pixels: altura de linha e largura de texto mudam com a
  // fonte, e um limiar em px passa no Windows e reprova no Ubuntu do CI (foi o
  // que aconteceu — 24px aqui, 32px lá, com o layout correto nos dois). O que
  // não depende de fonte é quanto do rodapé cada um ocupa.
  assert.ok(m.botao < m.rodape / 2,
    `o CTA ocupou ${m.botao}px de um rodapé de ${m.rodape}px — voltou a esticar`);
  assert.ok(m.rotulo > m.rodape * 0.4,
    `sobrou só ${m.rotulo}px de ${m.rodape}px pro nome do banco — o CTA espremeu o rótulo`);
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
