/**
 * Dock do modo app quando a troca de tela é feita pelo pb-nav (SPA, POC
 * desligado por padrão — ver docs/armadilhas.md). Irmão de
 * `dock_quarta_aba.test.mjs`, que cobre o mesmo invariante só em carga MPA;
 * o máquinário comum (abrir, tocar, assentar, estado, problemas…) mora em
 * `./_dock.mjs` — os dois passavam do teto do `quality/max-lines` juntos
 * (CLAUDE.md §0.5), e um `.test.mjs` não pode importar outro sem registrar os
 * testes em dobro.
 *
 * Apontamento do Codex (confirmado no código): com o motor ligado (?pbspa=1,
 * três condições em pb-nav.js: modo app, flag em sessionStorage,
 * startViewTransition/DOMParser), `PBNav.onNavigate` só fazia `livePage = key`,
 * `hardenGlyphs()`, achar o índice pelo href e sincronizar bolha/aba — nunca
 * chamava `fourthTab()`/`reconcileFourthTab` de novo. Duas quebras:
 *   - Pro com Notícias no 4º lugar, tocando em Início: a aba ficava presa em
 *     "O que pedir" pelo resto da sessão SPA (S1);
 *   - de Início pra comandos com Notícias no 4º lugar: PAGES[href] não bate
 *     com o href ANTIGO da 4ª aba, `i<0`, `onNavigate` retorna sem achar aba
 *     nenhuma — a página muda, a barra não acompanha (S2);
 *   - caso D: /auth/me processado DEPOIS do toque e ANTES do `onNavigate` —
 *     enquanto a Início ainda está sendo buscada/montada, `livePage` continua
 *     "comandos" e o `syncNewsTab` reescreve a 4ª aba com o plano velho; sem
 *     a chamada nova em `onNavigate`, nada corrige o 4º lugar depois que o
 *     mount termina (S3).
 * O conserto: extrair a reescrita da 4ª aba (`reconcileFourthTab`) e chamá-la
 * também do `onNavigate`, depois de `livePage = key`/`hardenGlyphs()` e antes
 * de achar o índice — `frontend/app-mode.js`.
 *
 * Cada caso prova que a navegação foi CLIENT-SIDE por evidência, não por nome
 * de função: uma marca em `window` sobrevive a uma troca SPA e some se o
 * documento recarregar — é a prova de vida antes de conferir a barra.
 *
 * Cego a: arrasto da bolha DEPOIS de uma troca SPA (T4, no arquivo irmão, só
 * arrasta em carga de documento); ao `popstate` (botão voltar do WebView,
 * pb-nav.js); ao ramo de CACHE do motor (`mountCached`, segunda visita a uma
 * página já vista nesta sessão SPA — todo caso aqui visita cada página uma
 * vez só); a uma "prova de vida" mais forte que a marca em `window`: ela
 * mostra que o documento não recarregou, não que nenhum estado de página foi
 * perdido no meio do caminho; ao caso D espelhado (Início → comandos com o
 * /auth/me chegando durante a troca — a correção acerta esse caminho também,
 * só não tem caso próprio aqui porque duplicaria o S3 quase inteiro para
 * provar o mesmo tipo de janela); e a um limite do próprio `setLive`: com a
 * bolha em arrasto (POC de SPA ligado), se o conteúdo da aba viva muda (por
 * `/auth/me` ou por `PBNav.go`) sem o ÍNDICE dela mudar, a bolha pode manter
 * o ícone antigo — `setLive` sai cedo quando `i === live`.
 *
 * Rodar: node --test tests/frontend/dock_quarta_aba_spa.test.mjs
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { join } from "node:path";
import { chromium } from "playwright";
import { PRO, FREE, ESPERA, LIMITE, APP_UA, ORIGIN, FRONTEND, json,
  abrirSpa, tocar, assentar, estado, problemas, quartaAba } from "./_dock.mjs";

let browser;
before(async () => { browser = await chromium.launch(); });
after(async () => { await browser?.close(); });

test("S1 (SPA): Pro cache 1 em O que pedir, toque em Início troca por SPA e a 4ª aba vira Notícias",
  LIMITE, async () => {
  const { ctx, page } = await abrirSpa(browser, "/comandos-app.html", "1", PRO);
  const erros = [];
  try {
    erros.push(...problemas(await estado(page), "O que pedir", "S1 antes do toque"));

    await page.evaluate(() => { window.__pbNavProva = "viva"; });
    await tocar(page, "/home", "/home");
    assert.equal(await page.evaluate(() => window.__pbNavProva), "viva",
      "S1: a marca em window sumiu — a troca recarregou o documento, não foi SPA");

    const depois = await estado(page);
    erros.push(...problemas(depois, "Início", "S1 depois do toque (SPA)"));
    erros.push(...quartaAba("Notícias", "/changelog")(depois, "S1 depois do toque"));
  } finally { await ctx.close(); }
  assert.deepEqual(erros, []);
});

test("S1 positivo (SPA): Free cache 0 faz o mesmo toque e o 4º lugar continua O que pedir",
  LIMITE, async () => {
  const { ctx, page } = await abrirSpa(browser, "/comandos-app.html", "0", FREE);
  const erros = [];
  try {
    await tocar(page, "/home", "/home");
    const s = await estado(page);
    erros.push(...problemas(s, "Início", "S1 positivo depois do toque"));
    erros.push(...quartaAba("O que pedir", "/comandos-app")(s, "S1 positivo depois do toque"));
  } finally { await ctx.close(); }
  assert.deepEqual(erros, []);
});

test("S2 (SPA): da Início por SPA com Notícias no 4º lugar, PBNav.go para comandos reativa O que pedir",
  LIMITE, async () => {
  const { ctx, page } = await abrirSpa(browser, "/comandos-app.html", "1", PRO);
  const erros = [];
  try {
    await tocar(page, "/home", "/home");
    const naInicio = await estado(page);
    erros.push(...quartaAba("Notícias", "/changelog")(naInicio, "S2 pré-condição (Início)"));

    // /comandos-app.html: em 127.0.0.1 o pb-nav mapeia esta variante pra
    // "comandos" também (ROUTES, só em localhost/127.0.0.1) — é o href que
    // resolve num arquivo servido do disco pela rota deste teste; produção
    // não tem ".html" nas rotas, e o S1 já cobre a carga direta dela.
    await page.evaluate(() => { window.__pbNavProva2 = "viva"; });
    await page.evaluate(() => window.PBNav.go("/comandos-app.html"));
    await page.waitForFunction(() => location.pathname === "/comandos-app.html", null, ESPERA)
      .catch(async (e) => {
        const onde = await page.evaluate(() => location.pathname);
        throw new Error(`PBNav.go não chegou a /comandos-app.html (ficou em "${onde}") — ${e.message}`);
      });
    await assentar(page);
    assert.equal(await page.evaluate(() => window.__pbNavProva2), "viva",
      "S2: a marca em window sumiu — a troca recarregou o documento, não foi SPA");
    // #hero-title só existe no comandos-app.html real: o pb-nav pode ter
    // prefetchado por "/comandos-app" (sem .html, via warmUp — mesma "key" do
    // ROUTES) e montado esse HTML em vez do buscado no clique; sem isto o
    // teste passava com a página montada em cima de JSON genérico (§ do _dock.mjs).
    assert.equal(await page.evaluate(() => !!document.getElementById("hero-title")), true,
      "S2: comandos-app não montou o conteúdo real (#hero-title ausente)");

    const depois = await estado(page);
    erros.push(...problemas(depois, "O que pedir", "S2 depois do PBNav.go"));
  } finally { await ctx.close(); }
  assert.deepEqual(erros, []);
});

test("S3 (SPA, caso D): /auth/me chega com o toque em Início EM VOO — a Início monta com Notícias",
  LIMITE, async () => {
  // Três travas: /auth/me (liberada 1ª), e a montagem da Início presa na
  // busca de /modals.js (só a Início carrega esse script, e o mountNew do
  // pb-nav espera a promise dele SEM limite de tempo — ver ensureExternalScripts
  // em pb-nav.js). Prender aqui, não /home: /home tem teto de 5s no pb-nav
  // (AbortController), e passar dele derruba pro reload de documento — o
  // teste ficaria vermelho pela marca SPA, não pelo caso D.
  let liberarMe, travaChegou, liberarMontagem;
  const meAtrasado = new Promise((ok) => { liberarMe = ok; });
  const chegouNaTrava = new Promise((ok) => { travaChegou = ok; });
  const montagemPresa = new Promise((ok) => { liberarMontagem = ok; });
  let prender = false;
  const ctx = await browser.newContext({ userAgent: APP_UA, viewport: { width: 390, height: 844 },
                                         reducedMotion: "reduce", serviceWorkers: "block" });
  const erros = [];
  try {
    // Mesma rota de `abrir` (_dock.mjs — reaproveitada aqui porque este caso
    // precisa de DOIS pontos de espera que `abrir` não parametriza: o
    // /auth/me atrasado e a montagem presa em /modals.js).
    await ctx.route("**/*", async (r) => {
      const url = new URL(r.request().url());
      if (url.origin !== ORIGIN) return r.abort();
      if (url.pathname === "/auth/me") { await meAtrasado; return r.fulfill(json(PRO)); }
      if (url.pathname === "/auth/validate") return r.fulfill(json({ user_id: 1 }));
      if (prender && url.pathname === "/modals.js") { travaChegou(); await montagemPresa; }
      if (url.pathname === "/home") return r.fulfill({ path: join(FRONTEND, "home.html") });
      if (!/\.[a-z0-9]+$/i.test(url.pathname)) return r.fulfill(json({}));
      return r.fulfill({ path: join(FRONTEND, decodeURIComponent(url.pathname)) })
        .catch(() => r.fulfill({ status: 404, body: "" }));
    });
    const page = await ctx.newPage();
    await page.goto(`${ORIGIN}/manifest.json`);
    await page.evaluate(() => localStorage.setItem("pbNewsTab", "0"));

    await page.goto(`${ORIGIN}/comandos-app.html?pbspa=1`, ESPERA);
    await page.waitForFunction(() => document.readyState === "complete"
      && document.querySelectorAll(".pb-tabbar .pb-tab:not(.pb-tab-fab)").length === 4, null, ESPERA);
    assert.equal(await page.evaluate(() => !!(window.PBNav && window.PBNav.enabled)), true);
    await assentar(page);

    await page.evaluate(() => { window.__pbNavProva = "viva"; });
    prender = true; // só depois do boot: o boot em si não deve tocar /modals.js
    await page.click('.pb-tab[href="/home"]');
    await chegouNaTrava; // estado: a montagem da Início chegou em /modals.js e está presa ali

    liberarMe();
    await page.waitForFunction(() => localStorage.getItem("pbNewsTab") === "1", null, ESPERA);
    // Prova que a janela do caso D foi exercitada: o /auth/me já respondeu e
    // a Início ainda não montou (o onNavigate, chamado dentro do mount, ainda
    // não rodou) — sem isto o teste passaria mesmo revertendo o conserto.
    assert.equal(await page.evaluate(() => location.pathname), "/comandos-app.html",
      "S3: a Início já tinha montado antes do /auth/me — a janela do caso D não foi exercitada");

    liberarMontagem();
    await page.waitForFunction(() => location.pathname === "/home", null, ESPERA);
    await assentar(page);
    assert.equal(await page.evaluate(() => window.__pbNavProva), "viva",
      "S3: a marca em window sumiu — a troca recarregou o documento, não foi SPA");

    const s = await estado(page);
    erros.push(...problemas(s, "Início", "S3 depois do /auth/me em voo"));
    erros.push(...quartaAba("Notícias", "/changelog")(s, "S3 depois do /auth/me em voo"));
  } finally { liberarMe(); liberarMontagem(); await ctx.close(); }
  assert.deepEqual(erros, []);
});

test("S4 (SPA): montar a Início instala o CSS externo dos grupos da sidenav",
  LIMITE, async () => {
  const { ctx, page } = await abrirSpa(browser, "/comandos-app.html", "0", FREE);
  try {
    await tocar(page, "/home", "/home");
    const fechado = await page.evaluate(() => {
      const grupo = document.querySelector('.sidenav-group[data-group="acompanhamento"]');
      const itens = grupo?.querySelector(".sidenav-subitems");
      return {
        css: !!document.querySelector('link[rel~="stylesheet"][href*="sidenav-rail.css"]'),
        display: itens ? getComputedStyle(itens).display : null,
      };
    });
    assert.deepEqual(fechado, { css: true, display: "none" });

    await page.evaluate(() => window.toggleSidenavGroup("acompanhamento"));
    assert.equal(await page.locator(
      '.sidenav-group[data-group="acompanhamento"] .sidenav-subitems',
    ).evaluate((el) => getComputedStyle(el).display), "block");
  } finally { await ctx.close(); }
});

test("S5 (SPA): segundo toque espera o stylesheet já inserido e ainda pendente",
  LIMITE, async () => {
  const { ctx, page } = await abrirSpa(browser, "/comandos-app.html", "0", FREE);
  let liberarCss;
  let avisarCss;
  const cssLiberado = new Promise((ok) => { liberarCss = ok; });
  const cssChegou = new Promise((ok) => { avisarCss = ok; });
  try {
    await page.route("**/sidenav-rail.css*", async (route) => {
      avisarCss();
      await cssLiberado;
      await route.fulfill({ path: join(FRONTEND, "sidenav-rail.css") });
    });

    await page.evaluate(() => window.PBNav.go("/home"));
    await cssChegou;
    await page.evaluate(() => window.PBNav.go("/home"));

    const montouSemCss = await page.waitForFunction(
      () => location.pathname === "/home", null, { timeout: 800 },
    ).then(() => true, () => false);
    assert.equal(montouSemCss, false, "a segunda navegação montou /home antes do CSS terminar");

    liberarCss();
    await page.waitForFunction(() => location.pathname === "/home", null, ESPERA);
    assert.equal(await page.locator(
      '.sidenav-group[data-group="acompanhamento"] .sidenav-subitems',
    ).evaluate((el) => getComputedStyle(el).display), "none");
  } finally { liberarCss(); await ctx.close(); }
});
