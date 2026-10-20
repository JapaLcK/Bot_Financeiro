/**
 * A / (frontend/index.html, ex-/oferta, + oferta.js). O markup é estático e
 * o JS só liga ciclo, chat, animações ao rolar e a barra fixa; cada caso abaixo morre se
 * a peça correspondente sair (controles do CLAUDE.md §3: tirar o listener do ciclo
 * deixa "ciclo" vermelho; tirar o armar() deixa "animações" vermelho).
 *
 * Rodar: node --test tests/frontend/oferta.test.mjs
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { startServer } from "./_server.mjs";
import { chromium } from "playwright";

let ORIGIN, server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer());
                     browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

/** Contexto novo por caso. `erros` junta pageerror e resposta >= 400. */
async function abrir({ js = true, calmo = false, mobile = false, relogio = false, largura, logado = false, disponivel = {} } = {}) {
  const ctx = await browser.newContext({
    viewport: largura ? { width: largura, height: 800 } : mobile ? { width: 390, height: 844 } : { width: 1280, height: 800 },
    javaScriptEnabled: js, reducedMotion: calmo ? "reduce" : "no-preference",
  });
  // o servidor estático não tem /auth: o nav-auth.js decide logado/deslogado pelo /auth/validate
  await ctx.route("**/auth/validate", r => logado
    ? r.fulfill({ status: 200, contentType: "application/json", body: '{"ok":true}' })
    : r.fulfill({ status: 401, contentType: "application/json", body: "{}" }));
  await ctx.route("**/auth/dashboard-profile", r => r.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({ email: "ana.beatriz@gmail.com", display_name: "Ana Beatriz", plan: "free" }) }));
  // o servidor estático não tem o /billing/plans-config: por padrão os três planos estão à venda
  await ctx.route("**/billing/plans-config", r => r.fulfill({ status: 200, contentType: "application/json",
    body: JSON.stringify({ essencial_available: true, plus_available: true, pro_available: true, ...disponivel }) }));
  const page = await ctx.newPage();
  const erros = [];
  page.on("pageerror", e => erros.push("pageerror: " + e.message));
  page.on("response", r => { if (r.status() >= 400 && !r.url().endsWith("/auth/validate")) erros.push(`${r.status()} ${r.url()}`); });
  if (relogio) await page.clock.install({ time: new Date("2026-01-01T08:00:00") });
  await page.goto(`${ORIGIN}/index.html`, { waitUntil: "load" });
  if (relogio) await page.clock.pauseAt(new Date("2026-01-01T09:00:00")); // daqui o tempo só anda no runFor
  return { page, ctx, erros };
}

const visivel = (page, sel) => page.locator(sel + ":visible").first();
const bolhas = page => page.locator(".ch-msg").count();
const ir = (page, sel) => page.evaluate(s => document.querySelector(s).scrollIntoView({ block: "start" }), sel);
const txt = (page, sel) => page.evaluate(s => document.querySelector(s).textContent, sel);

test("ciclo: nasce anual, Mensal troca preço, nota e link, Anual volta", async () => {
  const { page, ctx, erros } = await abrir();
  const card = '#planos [data-plano="essencial"]';
  const estado = async () => ({
    preco: await visivel(page, `${card} .pb-d[class*="so-"]`).textContent(),
    nota: (await visivel(page, `${card} span[class^=so-]`).textContent()).trim(),
    href: await visivel(page, `${card} a`).getAttribute("href"),
  });
  assert.deepEqual(await estado(), { preco: "R$ 8,25", nota: "R$ 99 por ano · 2 meses grátis", href: "/assinar?plano=essencial&ciclo=annual" });
  const pressed = () => page.$$eval("#planos [data-set]", bs => bs.map(b => `${b.dataset.set}=${b.getAttribute("aria-pressed")}`));
  assert.deepEqual(await pressed(), ["mensal=false", "anual=true"]);

  await page.click('[data-set="mensal"]');
  assert.deepEqual(await estado(), { preco: "R$ 9,90", nota: "Cobrado por mês", href: "/assinar?plano=essencial&ciclo=monthly" });
  assert.deepEqual(await pressed(), ["mensal=true", "anual=false"]);

  await page.click('[data-set="anual"]');
  assert.equal((await estado()).preco, "R$ 8,25");
  assert.deepEqual(await pressed(), ["mensal=false", "anual=true"]);
  assert.deepEqual(erros, []);
  await ctx.close();
});

test("FAQ: a 1ª nasce aberta e abrir a 3ª fecha a 1ª", async () => {
  const { page, ctx, erros } = await abrir();
  const abertas = () => page.$$eval("details[name=faq]", ds => ds.map(d => d.open));
  const [primeira, ...resto] = await abertas();
  assert.equal(primeira, true);
  assert.ok(resto.length === 7 && resto.every(o => !o));
  await page.click("details[name=faq]:nth-of-type(3) > summary");
  assert.deepEqual(await abertas(), [false, false, true, false, false, false, false, false]);
  assert.deepEqual(erros, []);
  await ctx.close();
});

test("chat: bolha → ✓ azul → digitando → resposta, trava durante a resposta, só 6 bolhas", async () => {
  const { page, ctx, erros } = await abrir({ relogio: true });
  const btns = page.locator(".pb-wchat button");
  const tick = () => page.locator(".ch-msg .ch-tk").last().getAttribute("stroke");
  assert.equal(await bolhas(page), 1); // a saudação do Piggy já está no HTML

  await btns.nth(1).click(); // "Quanto gastei com delivery?" → resposta com 71 caracteres
  const resposta = await btns.nth(1).getAttribute("data-resposta");
  assert.equal(await bolhas(page), 2);
  assert.match(await page.locator(".ch-msg").nth(1).textContent(), /Quanto gastei com delivery\?/);
  assert.equal(await tick(), "#8696A0");
  assert.ok(await page.locator("#ch-typ").isHidden());

  await page.clock.runFor(449);
  assert.equal(await tick(), "#8696A0");
  await page.clock.runFor(1);
  assert.equal(await tick(), "#53BDEB");

  await page.clock.runFor(300); // 750 ms
  assert.ok(await page.locator("#ch-typ").isVisible());
  assert.equal(await txt(page, "#ch-status"), "digitando...");

  await btns.nth(0).click(); // durante a resposta: ignorado
  assert.equal(await bolhas(page), 2);

  const espera = 750 + Math.min(2600, 900 + 14 * resposta.length);
  await page.clock.runFor(espera - 750 - 1);
  assert.equal(await bolhas(page), 2);
  await page.clock.runFor(1);
  assert.equal(await bolhas(page), 3);
  assert.equal(await page.locator(".ch-msg").last().locator(".ch-txt").textContent(), resposta);
  assert.ok(await page.locator("#ch-typ").isHidden());
  assert.equal(await txt(page, "#ch-status"), "online");

  for (let i = 0; i < 6; i++) { await btns.nth(i % 4).click(); await page.clock.runFor(4000); }
  assert.equal(await bolhas(page), 6); // 7 cliques no total, nunca mais que 6 bolhas
  assert.deepEqual(erros, []);
  await ctx.close();
});

test("animações: armam ao carregar e contam até o valor final ao rolar", async () => {
  const { page, ctx, erros } = await abrir();
  assert.ok(await page.evaluate(() => document.querySelector("#bento").classList.contains("pb-wait")));
  assert.equal(await txt(page, "#bento [data-alvo]"), "0");
  await ir(page, "#bento");
  await page.waitForFunction(() => document.querySelector("#bento [data-alvo]").textContent === "1.247", null, { timeout: 5000 });
  assert.ok(await page.evaluate(() => document.querySelector("#bento").classList.contains("pb-go")));

  assert.equal(await txt(page, "#dash [data-alvo]"), "0");
  await ir(page, "#dash");
  const finais = ["3.412", "12", "5.352", "1.940", "38", "24", "18", "12", "312", "78"];
  await page.waitForFunction(f => [...document.querySelectorAll("#dash [data-alvo]")].every((n, i) => n.textContent === f[i]), finais, { timeout: 5000 });
  assert.deepEqual(erros, []);
  await ctx.close();
});

test("animações: com reduced-motion o valor final está lá sem rolar e nada recebe -wait", async () => {
  const { page, ctx, erros } = await abrir({ calmo: true });
  assert.equal(await txt(page, "#bento [data-alvo]"), "1.247");
  assert.equal(await txt(page, "#dash [data-alvo]"), "3.412");
  assert.equal(await page.evaluate(() => document.querySelectorAll('[class*="-wait"]').length), 0);
  assert.deepEqual(erros, []);
  await ctx.close();
});

test("barra fixa (390x844): oculta, aparece depois do hero e some em #planos", async () => {
  const { page, ctx, erros } = await abrir({ mobile: true });
  const barra = () => page.evaluate(() => {
    const b = document.querySelector("#sticky");
    return { on: b.classList.contains("st-on"), aria: b.getAttribute("aria-hidden"), tab: b.querySelector("a").getAttribute("tabindex") };
  });
  assert.deepEqual(await barra(), { on: false, aria: "true", tab: "-1" });
  await ir(page, "#painel");
  await page.waitForFunction(() => document.querySelector("#sticky").classList.contains("st-on"));
  assert.deepEqual(await barra(), { on: true, aria: "false", tab: "0" });
  await page.waitForTimeout(450); // a transição de entrada (.35 s)
  assert.ok(await page.locator("#sticky").isVisible());
  assert.equal(await page.evaluate(() => getComputedStyle(document.querySelector("#sticky")).opacity), "1");
  await ir(page, "#planos");
  await page.waitForFunction(() => !document.querySelector("#sticky").classList.contains("st-on"));
  assert.deepEqual(await barra(), { on: false, aria: "true", tab: "-1" });
  assert.deepEqual(erros, []);
  await ctx.close();
});

test("sem JS: preços e links anuais, 1ª FAQ aberta, números finais e sem -wait", async () => {
  const { page, ctx, erros } = await abrir({ js: false });
  const card = '#planos [data-plano="plus"]';
  assert.equal(await visivel(page, `${card} .pb-d[class*="so-"]`).textContent(), "R$ 16,58");
  assert.equal(await visivel(page, `${card} a`).getAttribute("href"), "/assinar?plano=plus&ciclo=annual");
  assert.equal(await page.$eval("details[name=faq]", d => d.open), true);
  assert.equal(await txt(page, "#bento [data-alvo]"), "1.247");
  assert.equal(await txt(page, "#dash [data-alvo]"), "3.412");
  assert.equal(await page.evaluate(() => document.querySelectorAll('[class*="-wait"]').length), 0);
  assert.deepEqual(erros, []);
  await ctx.close();
});

test("fatura (390x844): o número acompanha o tamanho do pai e não encolhe a ~12px", async () => {
  const { page, ctx, erros } = await abrir({ mobile: true, calmo: true });
  const t = await page.$eval(".pb-fat-n", n => ({ pai: parseFloat(getComputedStyle(n).fontSize), num: parseFloat(getComputedStyle(n.querySelector("[data-alvo]")).fontSize) }));
  assert.ok(t.pai > 40, t.pai);
  assert.equal(t.num, t.pai);
  assert.deepEqual(erros, []);
  await ctx.close();
});

test("links internos: todo #alvo existe, o clique rola até a seção (não salta) e com reduced-motion salta", async () => {
  const clicarPlanos = async page => page.evaluate(async () => {
    // 150 ms: a rolagem suave já saiu do lugar e ainda não chegou; o salto já chegou.
    const quadro = () => new Promise(r => setTimeout(r, 150));
    document.querySelector('nav a[href="#planos"]').click();
    await quadro();
    return { meio: scrollY, fim: document.getElementById("planos").getBoundingClientRect().top + scrollY };
  });
  const noTopo = page => page.waitForFunction(() => Math.abs(document.getElementById("planos").getBoundingClientRect().top) < 2);

  const { page, ctx, erros } = await abrir();
  const alvos = await page.$$eval('a[href^="#"]', as => as.map(a => a.getAttribute("href")));
  assert.ok(alvos.length >= 10, alvos.join());
  for (const h of alvos) assert.ok(await page.$(h), `alvo sem seção: ${h}`);
  const { meio, fim } = await clicarPlanos(page);
  assert.ok(meio > 0 && meio < fim - 50, `rolou ${meio} de ${fim}: saltou em vez de rolar`);
  await noTopo(page);
  assert.deepEqual(erros, []);
  await ctx.close();

  const calmo = await abrir({ calmo: true });
  const r = await clicarPlanos(calmo.page);
  assert.ok(Math.abs(r.meio - r.fim) < 2, `reduced-motion devia saltar: ${r.meio} de ${r.fim}`);
  await calmo.ctx.close();
});

// ── Nav da raiz: "Entrar" para o cliente chegar ao dashboard ─────────────────

const navMedida = page => page.evaluate(() => {
  const nav = document.querySelector("nav.nav"), b = nav.getBoundingClientRect();
  const itens = [...nav.querySelectorAll(":scope > a, :scope > .nav-right > *")].filter(e => e.getBoundingClientRect().width > 0);
  return { h: +b.height.toFixed(1), fora: itens.filter(e => e.getBoundingClientRect().right > innerWidth + 0.5).length,
           largura: document.documentElement.scrollWidth <= innerWidth };
});

test("nav deslogado: o link Entrar está no markup, visível e aponta para /login", async () => {
  const { page, ctx, erros } = await abrir();
  const entrar = page.locator('.nav .nav-right a[href="/login"]');
  assert.equal(await entrar.count(), 1);
  assert.equal((await entrar.textContent()).trim(), "Entrar");
  assert.ok(await entrar.isVisible());
  assert.deepEqual(erros, []);
  await ctx.close();
});

test("nav logado: o menu da conta troca o Entrar e leva ao Dashboard (/app) e ao Início (/home)", async () => {
  const { page, ctx, erros } = await abrir({ logado: true });
  await page.waitForSelector("#pb-acct-btn", { state: "visible" });
  assert.equal(await page.locator('.nav .nav-right a[href="/login"]').count(), 0);
  await page.waitForFunction(() => document.getElementById("pb-acct-lbl").textContent === "Ana Beatriz");
  await page.click("#pb-acct-btn");
  assert.ok(await page.locator("#pb-acct-dd").isVisible());
  assert.equal((await page.locator('#pb-acct-dd a[href="/app"]').textContent()).trim(), "Dashboard");
  assert.equal(await page.locator('#pb-acct-dd a[href="/home"]').count(), 1);
  assert.deepEqual(erros, []);
  await ctx.close();
});

for (const largura of [320, 375, 390, 1280]) {
  for (const logado of [false, true]) {
    test(`nav ${logado ? "logado" : "deslogado"} em ${largura}px: sem rolagem lateral, numa linha, Entrar com 44px`, async () => {
      const { page, ctx } = await abrir({ largura, logado });
      if (logado) await page.waitForFunction(() => document.getElementById("pb-acct-lbl")?.textContent === "Ana Beatriz");
      await page.evaluate(() => document.fonts.ready);
      const m = await navMedida(page);
      assert.ok(m.largura, `${largura}: scrollWidth > innerWidth`);
      assert.equal(m.fora, 0, `${largura}: item da nav fora da tela`);
      assert.ok(m.h <= 66, `${largura}: nav com ${m.h}px, passou de uma linha`);
      if (!logado) {
        const e = await page.locator('.nav .nav-right a[href="/login"]').boundingBox();
        assert.ok(e.height >= 44, `${largura}: Entrar com ${e.height}px`);
      } else {
        await page.click("#pb-acct-btn");
        const dd = await page.locator("#pb-acct-dd").boundingBox();
        assert.ok(dd.x >= 0 && dd.x + dd.width <= largura, `${largura}: menu da conta aberto em ${Math.round(dd.x)}..${Math.round(dd.x + dd.width)}`);
      }
      await ctx.close();
    });
  }
}

test("plano sem price no Stripe (plans-config): os botões dele viram 'Indisponível' sem link; os outros seguem para a /assinar", async () => {
  const { page, ctx, erros } = await abrir({ disponivel: { plus_available: false } });
  await page.waitForFunction(() => document.querySelectorAll('[data-plano="plus"] [aria-disabled="true"]').length === 2);
  const r = await page.$$eval("[data-plano] a.so-anual, [data-plano] a.so-mensal", as => as.map(a => [a.closest("[data-plano]").dataset.plano, a.getAttribute("href"), a.textContent.trim()]));
  await ctx.close();
  assert.deepEqual(r.filter(([t]) => t === "plus"), [["plus", null, "Indisponível"], ["plus", null, "Indisponível"]]);
  const outros = r.filter(([t]) => t !== "plus");
  assert.equal(outros.length, 4);
  for (const [t, href, txt] of outros) assert.ok(href.startsWith(`/assinar?plano=${t}&`) && txt !== "Indisponível", `${t}: ${href} ${txt}`);
  assert.deepEqual(erros, []);
});

// ── Imagens: só o topo baixa de cara, e o espaço de cada uma já está reservado ─────────

for (const [w, h] of [[1440, 900], [390, 844], [320, 640]]) {
  test(`imagens em ${w}px: a dobra é eager, o resto é lazy, e a altura não depende do download`, async () => {
    const medir = async bloquear => {
      const ctx = await browser.newContext({ viewport: { width: w, height: h } });
      if (bloquear) await ctx.route(/\/brand\/oferta\//, r => r.abort());
      const page = await ctx.newPage();
      await page.goto(`${ORIGIN}/index.html`, { waitUntil: "load" });
      const topo = await page.evaluate(() => ({
        lazyNaDobra: [...document.images].filter(i => { const r = i.getBoundingClientRect();
          return i.loading === "lazy" && r.width > 0 && r.top < innerHeight; }).map(i => i.src.split("/").pop()),
        lazy: [...document.images].filter(i => i.loading === "lazy").length,
        total: document.images.length,
      }));
      // força o download de tudo para comparar a altura final e a proporção de cada arte
      if (!bloquear) {
        await page.evaluate(() => [...document.images].forEach(i => { i.loading = "eager"; }));
        await page.waitForFunction(() => [...document.images].every(i => i.complete && i.naturalWidth));
      }
      const fim = await page.evaluate(() => ({
        alturas: [...document.images].map(i => Math.round(i.getBoundingClientRect().height)),
        // width/height viram proporção, não tamanho: nenhuma arte sai esticada (fora as de caixa
        // fixa do design: cover recorta, e altura no style é ícone quadrado de propósito)
        esticadas: [...document.images].filter(i => { const r = i.getBoundingClientRect();
          return i.naturalWidth && r.width > 2 && getComputedStyle(i).objectFit !== "cover" &&
            !(i.style.height && i.style.height !== "auto") &&
            Math.abs(r.height - r.width * i.naturalHeight / i.naturalWidth) > 2; })
          .map(i => i.src.split("/").pop()),
        medidas: [...document.images].filter(i => i.naturalWidth && i.getBoundingClientRect().width > 2).length,
      }));
      await ctx.close();
      return { ...topo, ...fim };
    };
    const com = await medir(false), sem = await medir(true);
    assert.deepEqual(com.lazyNaDobra, [], "imagem da dobra adiada");
    assert.ok(com.lazy >= com.total - 4, `só ${com.lazy} de ${com.total} são lazy`);
    assert.ok(com.medidas >= 30, `só ${com.medidas} imagens medidas: a medição não achou a página`);
    assert.deepEqual(com.esticadas, [], "arte fora da proporção");
    // sem o download, cada <img> já ocupa a altura final: rolar não empurra o conteúdo
    assert.deepEqual(sem.alturas, com.alturas);
  });
}

// ── Guard da PWA instalada (index.html, 1º <script>): standalone entra pelo app ─────────

/** Abre `/index.html` + `query` com `display-mode: standalone` emulado e devolve o pathname final.
 *  O servidor estático não tem /login (404): vale o pathname da URL, não o status. */
async function pathAposAbrirStandalone(query) {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
  await ctx.addInitScript(() => {
    const real = window.matchMedia.bind(window);
    window.matchMedia = q => /display-mode:\s*standalone/.test(q) ? { matches: true, media: q, addEventListener() {}, removeEventListener() {} } : real(q);
  });
  const page = await ctx.newPage();
  await page.goto(`${ORIGIN}/index.html${query}`, { waitUntil: "load" }).catch(() => {});
  await page.waitForTimeout(500);
  const path = new URL(page.url()).pathname;
  await ctx.close();
  return path;
}

test("PWA instalada (standalone): a / manda para /login; com ?site=1 fica na landing", async () => {
  assert.equal(await pathAposAbrirStandalone(""), "/login");
  assert.equal(await pathAposAbrirStandalone("?site=1"), "/index.html");
});
