/**
 * Conta criada só via Google (/auth/me → has_password:false) em
 * frontend/settings.html.
 *
 * INVARIANTE: os 6 fluxos que re-autenticam por senha não podem MOSTRAR campo
 * de senha para quem não tem senha nenhuma — o abridor aborta e o diálogo manda
 * DEFINIR a senha. A autoridade continua sendo o servidor (409); isto aqui é a
 * camada advisory, e é o que evita a pessoa digitar num campo que nunca aceita.
 *
 * CONTROLES DO GRUPO (§3 do CLAUDE.md). Cada mutação foi APLICADA e este arquivo
 * rodado inteiro — os números são a saída do `node --test`. Baseline: 9 pass.
 *   negativo — desligando a guarda dos abridores (requireSenhaDefinida sempre
 *     true, o mesmo efeito de trocar `if (!requireSenhaDefinida()) return;` por
 *     `if (false) return;` nos 6): 5 vermelhos, 4 verdes. Caem os três `sem
 *     senha:` (inclusive o `?view=data`, onde moram 3 dos 6 pontos) e os dois
 *     `PBRefresh rebusca …`, que começam checando o bloqueio do boot. Se algum
 *     deles continuasse verde, mediria o boot e não a guarda. Os dois testes de
 *     corrida NÃO caem: eles medem outra coisa, e têm o controle próprio abaixo.
 *   negativo B — tirando `refreshHasPassword()` do window.PBRefresh (as duas
 *     abas): 3 vermelhos, 6 verdes. Os dois `PBRefresh rebusca has_password na
 *     aba …` (o abridor continua preso depois do PTR — um por aba porque o
 *     branch `data` era `return Promise.resolve()` e o `security` não) e também
 *     o `PTR durante o boot`, que puxa a tela na aba `data`: sem o rebusque,
 *     sobra só o /auth/me lento do boot e os 6 ficam trancados.
 *   positivo — os dois últimos testes (/auth/me = {} e = {has_password:true})
 *     provam que quem TEM senha continua abrindo os 6 como antes. Eles ficaram
 *     verdes nas TRÊS mutações, que é o trabalho deles. Sem eles o grupo
 *     passaria numa versão que bloqueia todo mundo, que é pior que o bug.
 *     E o PBRefresh continua sendo aguardado sem catch: se ele rejeitasse (o
 *     PTR ficaria âmbar), o teste da aba correspondente falharia.
 *
 * A espera de boot é `#section-<view>` deixar de ser hidden: quem tira o hidden
 * é o showSettingsSection, chamado DEPOIS da leitura do /auth/me no
 * initSettings. Esperar o texto de um card em vez disso mediria o HTML estático.
 *
 * Rodar:  npm run test:frontend
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
// Porta própria: o node --test roda os arquivos em paralelo. 8899 fanout,
// 8901 hiw_rail, 8903 modal_keys, 8905 of_refresh, 8907 onboarding,
// 8909 of_connect, 8911 handlers_inline. Compartilhar porta faz a sonda adotar
// o servidor alheio e o teste morrer com ERR_CONNECTION_REFUSED quando o dono
// termina — foi o que aconteceu aqui com a 8903.
const PORT = Number(process.env.PB_SEMSENHA_TEST_PORT || 8913);
const ORIGIN = `http://127.0.0.1:${PORT}`;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const json = (body) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });

async function startServer() {
  const proc = spawn("python3", ["-m", "http.server", String(PORT), "--bind", "127.0.0.1", "--directory", FRONTEND],
    { stdio: "ignore" });
  for (let i = 0; i < 100; i++) {
    await sleep(100);
    if (proc.exitCode !== null) throw new Error(`http.server morreu (porta ${PORT} ocupada?)`);
    try { if ((await fetch(`${ORIGIN}/settings.html`)).ok) return proc; } catch { /* subindo */ }
  }
  proc.kill();
  throw new Error(`http.server não subiu em ${ORIGIN}`);
}

async function waitFor(cond, what, timeoutMs = 10000) {
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

const SECURITY_OK = { ok: true, user_id: 1, email: "a@b.com", plan: "pro", display_name: "Japa", identities: [] };

/** `me` = corpo do /auth/me, ou função que o devolve (pra mudar entre um PTR e
    outro — é assim que o teste do upgrade path simula a senha sendo definida em
    outra aba). Catch-all primeiro (menor prioridade no Playwright). */
async function newPage(me) {
  const corpoMe = () => (typeof me === "function" ? me() : me);
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
  await page.route("**/auth/me", (route) => route.fulfill(json(corpoMe())));
  await page.route("**/settings/1/security", (route) => route.fulfill(json(SECURITY_OK)));
  await page.route("**/auth/mfa/status", (route) => route.fulfill(json({ enabled: false })));
  return page;
}

/** Abre a página e espera o initSettings passar do /auth/me. */
async function abrePagina(me, query, secao) {
  const page = await newPage(me);
  await page.goto(`${ORIGIN}/settings.html?${query}`);
  await waitFor(() => page.evaluate((s) => !document.getElementById("section-" + s).hidden, secao),
                `a aba ${secao} aparecer (boot do initSettings)`);
  return page;
}

const modalAberto = (page) => page.evaluate(() => !!document.querySelector(".pig-modal-overlay"));
const temClasse = (page, id, c) => page.evaluate(([i, k]) => document.getElementById(i).classList.contains(k), [id, c]);
const fechaModal = (page) => page.evaluate(() => document.querySelector(".pig-modal-overlay")?.remove());
const tituloCard = (page) => page.evaluate(() => document.getElementById("security-reset-title").textContent);

/** Chama o abridor e devolve se o painel de senha correspondente apareceu. */
async function abre(page, fn, id, classe) {
  await page.evaluate((f) => window[f](), fn);
  await sleep(120);   // o diálogo entra num requestAnimationFrame
  return temClasse(page, id, classe);
}

const OS_SEIS = [
  ["showExportConfirm", "export-confirm", "show"],
  ["showResetConfirm", "reset-confirm", "show"],
  ["showDeleteConfirm", "delete-confirm", "show"],
  ["openMfaSetupModal", "mfa-setup-overlay", "open"],
  ["openMfaDisableModal", "mfa-disable-overlay", "open"],
  ["openMfaRegenerateModal", "mfa-regen-overlay", "open"],
];

// ── negativo do produto: conta SEM senha ────────────────────────────────────

test("sem senha: os 6 abridores não revelam campo de senha e abrem o diálogo", async () => {
  // ?view=data é a armadilha: exportar/recomeçar/excluir moram nesta aba, não
  // na security — um teste que só cobrisse a security passaria cego a ela.
  const page = await abrePagina({ has_password: false }, "view=data", "data");
  try {
    for (const [fn, id, classe] of OS_SEIS) {
      assert.equal(await abre(page, fn, id, classe), false, `${fn} revelou o campo de senha`);
      assert.equal(await modalAberto(page), true, `${fn} não abriu o diálogo de definir senha`);
      await fechaModal(page);
    }
  } finally { await page.close(); }
});

test("sem senha: o botão do diálogo leva para a aba de segurança, com a copy de DEFINIR", async () => {
  const page = await abrePagina({ has_password: false }, "view=data", "data");
  try {
    await page.evaluate(() => showExportConfirm());
    await waitFor(() => modalAberto(page), "o diálogo de definir senha");

    await page.click(".pig-modal-btn-primary, .pig-modal-btn-destructive");
    await waitFor(() => page.evaluate(() => !document.getElementById("section-security").hidden),
                  "a aba de segurança ficar visível");
    assert.equal(await page.evaluate(() => new URLSearchParams(location.search).get("view")), "security");

    // Para quem nunca teve senha isso é DEFINIR, não "resetar".
    await waitFor(async () => (await tituloCard(page)) === "Definir senha", "a copy do card virar 'Definir senha'");
  } finally { await page.close(); }
});

test("sem senha: ?view=security&autoOpenMfa=1 (o caminho da /home) não abre o setup", async () => {
  // A /home manda esta URL e o initSettings chama openMfaSetupModal() DIRETO,
  // sem passar pelo onMfaToggleClick — por isso a guarda vive no abridor.
  const page = await abrePagina({ has_password: false }, "view=security&autoOpenMfa=1", "security");
  try {
    await waitFor(() => modalAberto(page), "o diálogo de definir senha do autoOpenMfa");   // setTimeout 400ms
    assert.equal(await temClasse(page, "mfa-setup-overlay", "open"), false,
                 "o autoOpenMfa abriu o setup de MFA numa conta sem senha");
  } finally { await page.close(); }
});

// ── upgrade path: definiu a senha fora daqui e puxou a tela ─────────────────

for (const view of ["data", "security"]) {
  test(`PBRefresh rebusca has_password na aba ${view}: os 6 destravam sem reload`, async () => {
    // O PTR do app NÃO recarrega o documento (é o motivo do PBRefresh existir),
    // então sem rebuscar o /auth/me quem definiu a senha ficava preso. A aba
    // `data` é a armadilha: 3 dos 6 pontos moram nela e o branch dela era
    // `return Promise.resolve()`.
    let me = { has_password: false };
    const page = await abrePagina(() => me, `view=${view}`, view);
    try {
      assert.equal(await abre(page, "showExportConfirm", "export-confirm", "show"), false,
                   "boot com has_password:false devia bloquear");
      await fechaModal(page);

      me = { has_password: true };   // a senha acabou de ser definida em outra aba
      await page.evaluate(() => window.PBRefresh());

      for (const [fn, id, classe] of OS_SEIS) {
        assert.equal(await abre(page, fn, id, classe), true, `${fn} continuou preso após o PBRefresh`);
        assert.equal(await modalAberto(page), false, `${fn} ainda abriu o diálogo de definir senha`);
        await page.evaluate(([i, c]) => document.getElementById(i).classList.remove(c), [id, classe]);
      }
      // Na security o /auth/me roda ANTES dos loaders de propósito:
      // applySecuritySettings lê HAS_PASSWORD, e a copy tem de saber VOLTAR.
      if (view === "security") assert.equal(await tituloCard(page), "Resetar senha por e-mail");
    } finally { await page.close(); }
  });
}

// ── corrida: o /auth/me VELHO não pode vencer o NOVO ────────────────────────
//
// Os dois casos abaixo são o buraco que o rebusque do PBRefresh abriu: um
// segundo /auth/me em voo. HAS_PASSWORD não é pixel — a escrita velha tranca os
// 6 fluxos até outro PTR. Controle negativo do PAR: removendo a guarda de
// geração (o `if (myGen !== _hasPwGen) return;` de setHasPassword) e rodando o
// arquivo, dá 2 vermelhos e 7 verdes — os DOIS daqui, e só eles.

/** Sequencia as respostas do /auth/me: `[delayMs, corpo]` por chamada, a última
    repete. Registrada DEPOIS do newPage, então tem prioridade (rota do
    Playwright é LIFO). `vistos()` conta as requisições já recebidas. */
async function sequenciaAuthMe(page, respostas) {
  let vistos = 0;
  await page.route("**/auth/me", async (route) => {
    const [delay, corpo] = respostas[Math.min(vistos++, respostas.length - 1)];
    if (delay) await sleep(delay);
    await route.fulfill(json(corpo));
  });
  return () => vistos;
}

/** Nenhum dos 6 pode estar barrado. */
async function osSeisDestravados(page, quando) {
  for (const [fn, id, classe] of OS_SEIS) {
    assert.equal(await abre(page, fn, id, classe), true, `${fn} ficou preso ${quando}`);
    await page.evaluate(([i, c]) => document.getElementById(i).classList.remove(c), [id, classe]);
  }
}

test("dois refreshHasPassword sobrepostos: a resposta VELHA não sobrescreve a NOVA", async () => {
  const page = await abrePagina({ has_password: false }, "view=data", "data");
  try {
    // #1 lenta com o estado VELHO (sem senha), #2 rápida com o NOVO (a senha
    // acabou de ser definida em outra aba). A VELHA assenta por último.
    await sequenciaAuthMe(page, [[800, { has_password: false }], [0, { has_password: true }]]);
    await page.evaluate(() => Promise.all([refreshHasPassword(), refreshHasPassword()]));
    await osSeisDestravados(page, "depois da resposta velha assentar por último");
  } finally { await page.close(); }
});

test("PTR durante o boot: o /auth/me do boot (emitido ANTES) não vence o do PTR", async () => {
  // window.PBRefresh é atribuído no parse, enquanto o initSettings ainda espera
  // o /auth/validate + /auth/me dele: puxar a tela nessa janela deixa dois
  // /auth/me em voo, e o do boot é o mais velho dos dois.
  const page = await newPage({});
  try {
    const vistos = await sequenciaAuthMe(page, [[900, { has_password: false }], [0, { has_password: true }]]);
    await page.goto(`${ORIGIN}/settings.html?view=data`);
    await waitFor(() => vistos() >= 1, "o /auth/me do boot ser emitido");
    await page.evaluate(() => window.PBRefresh());
    // O boot só chega no showSettingsSection depois do /auth/me dele: esperar a
    // aba aparecer é esperar a escrita velha ter tido a chance de assentar.
    await waitFor(() => page.evaluate(() => !document.getElementById("section-data").hidden),
                  "o boot passar do /auth/me lento");
    await osSeisDestravados(page, "depois do /auth/me do boot assentar por último");
  } finally { await page.close(); }
});

// ── positivo do grupo: quem TEM senha abre os 6 como sempre ─────────────────

for (const [rotulo, me] of [["{} (campo ausente)", {}], ["has_password:true", { has_password: true }]]) {
  test(`com /auth/me = ${rotulo}: os 6 abridores abrem como hoje`, async () => {
    const page = await abrePagina(me, "view=security", "security");
    try {
      for (const [fn, id, classe] of OS_SEIS) {
        assert.equal(await abre(page, fn, id, classe), true, `${fn} foi bloqueado indevidamente`);
        assert.equal(await modalAberto(page), false, `${fn} abriu o diálogo de definir senha à toa`);
        await page.evaluate(([i, c]) => document.getElementById(i).classList.remove(c), [id, classe]);
      }
      // applySecuritySettings JÁ rodou nesta aba (o boot esperou a security):
      // com senha, a copy do card não pode ter virado "Definir senha".
      assert.equal(await tituloCard(page), "Resetar senha por e-mail");
    } finally { await page.close(); }
  });
}
