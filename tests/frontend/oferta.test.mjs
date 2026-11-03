/**
 * A / (frontend/index.html, ex-/oferta, + oferta.js). O markup é estático e
 * o JS só liga ciclo, chat, animações ao rolar e a barra fixa; cada caso abaixo morre se
 * a peça correspondente sair (controles do CLAUDE.md §3: tirar o listener do ciclo
 * deixa "ciclo" vermelho; tirar o armar() deixa "animações" vermelho).
 * Regra dos CTAs (oferta.js + teste-piggy.js), medido em 2026-11-02: voltar a "qualquer sessão
 * → /precos" deixa "logado sem assinatura" vermelho; tirar o `sub.degraded` deixa "degraded";
 * tirar o ramo trial_disponivel false deixa "Assinar o Plus"; olhar o trial antes do active
 * deixa "ativa (trial false)"; perguntar a assinatura sem sessão ou tirar a trava data-utm-base
 * deixa "anônimo". Positivo: "logado sem assinatura e com trial" (textos e /assinar intactos).
 * Layout estreito: tirar o `#hero-cta{flex-wrap:wrap}` do @media 360 deixa "CTAs com …" vermelho
 * (link em -3..186 a 320); tirar o padding do card do #cta-final (@media 360) deixa o CTA final
 * com 100px a 320, e o do link (@media 400) deixa 100px a 375 (3 linhas; o teto é 2); tirar o
 * `#sticky>span>*{white-space:normal}` corta o preço da barra com "Gerenciar meu plano" (43px a 320).
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
async function abrir({ js = true, calmo = false, mobile = false, relogio = false, largura, logado = false, disponivel = {}, planosDepois = 0,
                       assinatura = { active: false, trial_disponivel: true }, query = "" } = {}) {
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
  await ctx.route("**/billing/plans-config", async r => (await new Promise(ok => setTimeout(ok, planosDepois)), r.fulfill({ status: 200, contentType: "application/json",
    body: JSON.stringify({ essencial_available: true, plus_available: true, pro_available: true, ...disponivel }) })));
  // `assinatura`: corpo do GET /billing/subscription, ou um número = só o status (ex.: 403 sem credencial)
  const subs = [];
  await ctx.route("**/billing/subscription", r => (subs.push(r.request().url()), typeof assinatura === "number"
    ? r.fulfill({ status: assinatura, contentType: "application/json", body: "{}" })
    : r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(assinatura) })));
  const page = await ctx.newPage();
  const erros = [];
  page.on("pageerror", e => erros.push("pageerror: " + e.message));
  page.on("response", r => { if (r.status() >= 400 && !r.url().endsWith("/auth/validate")) erros.push(`${r.status()} ${r.url()}`); });
  if (relogio) await page.clock.install({ time: new Date("2026-01-01T08:00:00") });
  await page.goto(`${ORIGIN}/index.html${query}`, { waitUntil: "load" });
  if (relogio) await page.clock.pauseAt(new Date("2026-01-01T09:00:00")); // daqui o tempo só anda no runFor
  return { page, ctx, erros, subs };
}

const visivel = (page, sel) => page.locator(sel + ":visible").first();
const bolhas = page => page.locator(".ch-msg").count();
const ir = (page, sel) => page.evaluate(s => document.querySelector(s).scrollIntoView({ block: "start" }), sel);
const txt = (page, sel) => page.evaluate(s => document.querySelector(s).textContent, sel);

test("ciclo: nasce mensal, Anual troca preço, nota e link, Mensal volta", async () => {
  const { page, ctx, erros } = await abrir();
  const card = '#planos [data-plano="essencial"]';
  const estado = async () => ({
    preco: await visivel(page, `${card} .pb-d[class*="so-"]`).textContent(),
    nota: (await visivel(page, `${card} span[class^=so-]`).textContent()).trim(),
    href: await visivel(page, `${card} a`).getAttribute("href"),
  });
  assert.deepEqual(await estado(), { preco: "R$ 9,90", nota: "Cobrado por mês", href: "/assinar?plano=essencial&ciclo=monthly" });
  const pressed = () => page.$$eval("#planos [data-set]", bs => bs.map(b => `${b.dataset.set}=${b.getAttribute("aria-pressed")}`));
  assert.deepEqual(await pressed(), ["mensal=true", "anual=false"]);

  await page.click('[data-set="anual"]');
  assert.deepEqual(await estado(), { preco: "R$ 8,25", nota: "R$ 99 por ano · 2 meses grátis", href: "/assinar?plano=essencial&ciclo=annual" });
  assert.deepEqual(await pressed(), ["mensal=false", "anual=true"]);

  await page.click('[data-set="mensal"]');
  assert.equal((await estado()).preco, "R$ 9,90");
  assert.deepEqual(await pressed(), ["mensal=true", "anual=false"]);
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

test("sem JS: preços e links mensais, 1ª FAQ aberta, números finais e sem -wait", async () => {
  const { page, ctx, erros } = await abrir({ js: false });
  const card = '#planos [data-plano="plus"]';
  assert.equal(await visivel(page, `${card} .pb-d[class*="so-"]`).textContent(), "R$ 19,90");
  assert.equal(await visivel(page, `${card} a`).getAttribute("href"), "/assinar?plano=plus&ciclo=monthly");
  // os 4 CTAs da campanha (cabeçalho, hero, fim, barra) já saem do HTML na /assinar do Plus mensal
  assert.deepEqual(await page.$$eval(CTAS_UTM, as => as.map(a => a.getAttribute("href"))), Array(4).fill("/assinar?plano=plus&ciclo=monthly"));
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
  // 7 em 2026-11-02 (os 4 CTAs foram para a /assinar). Remedir: o $$eval acima, em /index.html.
  assert.ok(alvos.length >= 7, alvos.join());
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

test("nav logado: o menu da conta troca o Entrar e leva ao Dashboard (/painel) e ao Início (/home)", async () => {
  const { page, ctx, erros } = await abrir({ logado: true });
  await page.waitForSelector("#pb-acct-btn", { state: "visible" });
  assert.equal(await page.locator('.nav .nav-right a[href="/login"]').count(), 0);
  await page.waitForFunction(() => document.getElementById("pb-acct-lbl").textContent === "Ana Beatriz");
  await page.click("#pb-acct-btn");
  assert.ok(await page.locator("#pb-acct-dd").isVisible());
  assert.equal((await page.locator('#pb-acct-dd a[href="/painel"]').textContent()).trim(), "Dashboard");
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

test("teclado: o 1º Tab é o 'Pular para o conteúdo', que aparece no foco e leva ao hero dentro do <main>; o Tab seguinte não volta ao nav", async () => {
  const { page, ctx, erros } = await abrir({ calmo: true });
  const estrutura = await page.evaluate(() => {
    const main = document.querySelectorAll("main");
    return [main.length, !!main[0]?.querySelector("#conteudo, #planos, #cta-final"), ["#conteudo", "#planos", "#cta-final"].every(s => main[0]?.contains(document.querySelector(s))),
      !main[0]?.contains(document.querySelector("footer"))];
  });
  assert.deepEqual(estrutura, [1, true, true, true]);
  const escondido = await page.locator(".pb-skip").evaluate(e => e.getBoundingClientRect().bottom <= 0);
  await page.keyboard.press("Tab");
  const pulo = await page.evaluate(() => { const a = document.activeElement, r = a.getBoundingClientRect();
    return [a.textContent.trim(), a.getAttribute("href"), r.top >= 0 && r.bottom <= innerHeight]; });
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => document.activeElement?.id === "conteudo");
  await page.keyboard.press("Tab");
  const depois = await page.evaluate(() => { const a = document.activeElement;
    return [!!a.closest(".pb-hero"), !!a.closest("nav"), a.textContent.trim().slice(0, 30)]; });
  await ctx.close();
  assert.ok(escondido, "o link aparece antes do foco");
  assert.deepEqual(pulo, ["Pular para o conteúdo", "#conteudo", true]);
  assert.deepEqual(depois.slice(0, 2), [true, false], `o Tab seguinte foi para "${depois[2]}"`);
  assert.deepEqual(erros, []);
});

// ── Regra dos CTAs (hero, fim, barra) e dos 6 cartões, pelo GET /billing/subscription ────────
// anônimo / sem assinatura / desconhecido → textos originais e /assinar; trial_disponivel false →
// "Assinar o Plus" e /assinar; assinatura ativa → "Gerenciar meu plano" e /precos.

const CARTOES = "[data-plano] a.so-anual, [data-plano] a.so-mensal";
const CTAS_UTM = 'a[data-utm]:not([class*="so-"])';  // os 4 CTAs (cabeçalho, hero, fim, barra), sem os 6 cartões
const PLUS_MENSAL = "/assinar?plano=plus&ciclo=monthly";
/** Espera `n` chamadas ao /billing/subscription (até 2 s) e mais 200 ms para o JS assentar. */
const assentou = async (page, subs, n = 1) => {
  for (let i = 0; subs.length < n && i < 100; i++) await page.waitForTimeout(20);
  await page.waitForTimeout(200);
};
/** Rótulo e destino dos CTAs com [data-rotulo] (hero, fim, barra), com o nome sem o que é aria-hidden. */
const ctas = page => page.$$eval("a:has(> [data-rotulo])", as => as.map(a => ({
  nome: [...a.childNodes].filter(n => !(n.nodeType === 1 && n.getAttribute("aria-hidden") === "true")).map(n => n.textContent).join("").trim(),
  seta: !!a.querySelector('[aria-hidden="true"]'),
  href: a.getAttribute("href"),
})));
const cartoes = page => page.$$eval(CARTOES, as => as.map(a => a.getAttribute("href")));
const ORIGINAIS = ["Começar meus 7 dias grátis", "Começar meus 7 dias grátis", "7 dias grátis"];

for (const trial_disponivel of [true, false]) {
  test(`com assinatura ativa (trial_disponivel ${trial_disponivel}): os 6 cartões e os 3 CTAs levam à /precos com 'Gerenciar meu plano'`, async () => {
    // com UTM na entrada: o repasse aos cartões (data-utm) não pode desfazer o /precos do assinante
    const { page, ctx, erros, subs } = await abrir({ logado: true, assinatura: { active: true, trial_disponivel }, query: "?utm_source=fb&fbclid=abc" });
    await page.waitForFunction(() => !document.querySelector('[data-plano] a[href^="/assinar"], a[data-utm][href^="/assinar"]'));
    assert.deepEqual(await cartoes(page), Array(6).fill("/precos"));
    assert.deepEqual(await ctas(page), Array(3).fill({ nome: "Gerenciar meu plano", seta: true, href: "/precos" }));
    assert.equal(subs.length, 1);
    // o teste-piggy.js carregado de novo não desfaz o /precos (o data-utm-base trava a 2ª passada)
    await page.addScriptTag({ url: "/teste-piggy.js" });
    assert.deepEqual((await ctas(page)).map(c => c.href), Array(3).fill("/precos"));
    await ctx.close();
    assert.deepEqual(erros, []);
  });
}

test("logado sem assinatura e com trial: cartões e CTAs seguem para a /assinar, com os textos originais", async () => {
  const { page, ctx, erros, subs } = await abrir({ logado: true });
  await assentou(page, subs);
  assert.equal(subs.length, 1);
  assert.equal((await cartoes(page)).filter(h => /^\/assinar\?plano=(essencial|plus|pro)&ciclo=(annual|monthly)$/.test(h)).length, 6);
  assert.deepEqual(await ctas(page), ORIGINAIS.map(nome => ({ nome, seta: true, href: PLUS_MENSAL })));
  await ctx.close();
  assert.deepEqual(erros, []);
});

test("logado sem assinatura e trial_disponivel false: 'Assinar o Plus' em hero/fim/barra, destino /assinar com a UTM", async () => {
  const { page, ctx, erros, subs } = await abrir({ logado: true, assinatura: { active: false, trial_disponivel: false },
    query: "?utm_source=fb&fbclid=abc&foo=1" });
  await page.waitForFunction(() => [...document.querySelectorAll("[data-rotulo]")].every(s => s.textContent === "Assinar o Plus"));
  const href = PLUS_MENSAL + "&utm_source=fb&fbclid=abc";
  assert.deepEqual(await ctas(page), Array(3).fill({ nome: "Assinar o Plus", seta: true, href }));
  // nome acessível de verdade (a barra fica fora da árvore enquanto aria-hidden, e no desktop nem aparece)
  assert.equal(await page.getByRole("link", { name: "Assinar o Plus", exact: true }).count(), 2);
  assert.equal(subs.length, 1);
  assert.equal((await cartoes(page)).filter(h => h.startsWith("/assinar?")).length, 6);
  await ctx.close();
  assert.deepEqual(erros, []);
});

for (const [nome, assinatura] of [["403 (sem credencial)", 403], ["degraded", { active: false, degraded: true, trial_disponivel: false }],
                                  ["sem o campo", { active: false }]]) {
  test(`logado com resposta incerta (${nome}): textos originais e /assinar`, async () => {
    const { page, ctx, erros, subs } = await abrir({ logado: true, assinatura });
    await assentou(page, subs);
    assert.equal(subs.length, 1);
    assert.deepEqual(await ctas(page), ORIGINAIS.map(nome => ({ nome, seta: true, href: PLUS_MENSAL })));
    assert.equal((await cartoes(page)).filter(h => h.startsWith("/assinar?")).length, 6);
    await ctx.close();
    assert.deepEqual(erros.filter(e => !e.includes("/billing/subscription")), []);
  });
}

test("anônimo: nenhuma chamada ao /billing/subscription, textos originais, UTM repassada; sem query o href é o exato", async () => {
  const comUtm = await abrir({ query: "?utm_source=ig&x=1&UTM_Campaign=a%20b&fbclid=AbC" });
  await assentou(comUtm.page, comUtm.subs, 0);
  const href = PLUS_MENSAL + "&utm_source=ig&UTM_Campaign=a+b&fbclid=AbC";
  assert.deepEqual(await ctas(comUtm.page), ORIGINAIS.map(nome => ({ nome, seta: true, href })));
  const cab = comUtm.page.locator("nav a.pb-nav-cta");
  assert.deepEqual([(await cab.textContent()).trim(), await cab.getAttribute("href")], ["7 dias grátis", href]);
  // carregado 2x: não duplica a query
  await comUtm.page.addScriptTag({ url: "/teste-piggy.js" });
  assert.deepEqual(await comUtm.page.$$eval(CTAS_UTM, as => as.map(a => a.getAttribute("href"))), Array(4).fill(href));
  // os 6 cartões também levam a campanha (B2): o do Pro anual com utm_source e fbclid
  assert.equal(await comUtm.page.getAttribute('[data-plano="pro"] a.so-anual', "href"), "/assinar?plano=pro&ciclo=annual&utm_source=ig&UTM_Campaign=a+b&fbclid=AbC");
  assert.equal((await cartoes(comUtm.page)).filter(h => h.endsWith("&utm_source=ig&UTM_Campaign=a+b&fbclid=AbC")).length, 6);
  assert.equal(comUtm.subs.length, 0);
  assert.deepEqual(comUtm.erros, []);
  await comUtm.ctx.close();

  const sem = await abrir({ query: "?x=1" });
  await assentou(sem.page, sem.subs, 0);
  assert.deepEqual(await sem.page.$$eval(CTAS_UTM, as => as.map(a => a.getAttribute("href"))), Array(4).fill(PLUS_MENSAL));
  assert.equal(sem.subs.length, 0);
  await sem.ctx.close();
});

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

test("com assinatura ativa e o plans-config chegando depois do /billing/subscription, o plano indisponível ainda vira 'Indisponível'", async () => {
  const { page, ctx, erros } = await abrir({ logado: true, assinatura: { active: true }, disponivel: { plus_available: false }, planosDepois: 600 });
  await page.waitForFunction(() => document.querySelectorAll('[data-plano] [aria-disabled="true"]').length === 2, null, { timeout: 5000 });
  const r = await page.$$eval("[data-plano] a.so-anual, [data-plano] a.so-mensal", as => as.map(a => [a.closest("[data-plano]").dataset.plano, a.getAttribute("href"), a.textContent.trim()]));
  await ctx.close();
  assert.deepEqual(r.filter(([t]) => t === "plus"), [["plus", null, "Indisponível"], ["plus", null, "Indisponível"]]);
  assert.deepEqual(r.filter(([t]) => t !== "plus").map(([, h]) => h), Array(4).fill("/precos"));
  assert.deepEqual(erros, []);
});

// O `scrollWidth` não pega link vazado (o #hero-cta é nowrap e centraliza): mede cada <a>.
for (const rotulo of [null, "Assinar o Plus", "Gerenciar meu plano"]) {
  test(`CTAs com '${rotulo || "Começar meus 7 dias grátis"}': hero dentro da tela, CTA final em até 2 linhas e preço da barra inteiro de 320 a 414`, async () => {
    for (const largura of [320, 360, 375, 390, 414]) {
      const { page, ctx } = await abrir({ largura });
      await page.evaluate(() => document.fonts.ready);
      if (rotulo) await page.evaluate(t => document.querySelectorAll("[data-rotulo]").forEach(s => { s.textContent = t; }), rotulo);
      const m = await page.evaluate(() => ({
        hero: [...document.querySelectorAll("#hero-cta a")].map(a => { const r = a.getBoundingClientRect(); return [Math.round(r.left), Math.round(r.right)]; }),
        fim: document.querySelector("#cta-final a[data-utm]").getBoundingClientRect().height,
        // o bloco do preço da barra fixa corta com overflow:hidden: texto cortado = scrollWidth > clientWidth
        preco: [...document.querySelectorAll("#sticky > span, #sticky > span > *")].map(e => e.scrollWidth - e.clientWidth),
      }));
      await ctx.close();
      for (const [l, r] of m.hero) assert.ok(l >= 0 && r <= largura, `${largura}: link do hero em ${l}..${r}`);
      assert.ok(m.fim <= 81, `${largura}: CTA final com ${m.fim}px (mais de 2 linhas)`);
      assert.deepEqual(m.preco, [0, 0, 0], `${largura}: preço da barra fixa cortado`);
    }
  });
}

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

// B1: o "Trocar de plano" do S1 tem de chegar à tabela da / também na PWA instalada, onde a / sem
// `?site=1` manda ao /login. Controle negativo: com href="/#planos" o caso standalone fica vermelho.
for (const standalone of [false, true]) {
  test(`'Trocar de plano' na /assinar ${standalone ? "na PWA instalada" : "no navegador"}: o clique abre a / já em #planos`, async () => {
    const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, reducedMotion: "reduce" });
    if (standalone) await ctx.addInitScript(() => { const real = window.matchMedia.bind(window);
      window.matchMedia = q => /display-mode:\s*standalone/.test(q) ? { matches: true, media: q, addEventListener() {}, removeEventListener() {} } : real(q); });
    for (const rota of ["**/auth/me", "**/auth/validate", "**/auth/refresh*"]) await ctx.route(rota, r => r.fulfill({ status: 401, contentType: "application/json", body: "{}" }));
    await ctx.route("**/billing/plans-config", r => r.fulfill({ status: 200, contentType: "application/json", body: "{}" }));
    const page = await ctx.newPage();
    await page.goto(`${ORIGIN}/assinar.html?plano=pro&ciclo=annual`, { waitUntil: "load" });
    await page.waitForSelector("#s1:not([hidden])");
    await Promise.all([page.waitForURL(u => !u.pathname.startsWith("/assinar")), page.click("text=Trocar de plano")]);
    await page.waitForLoadState("load");
    await page.waitForTimeout(300);  // a guarda da PWA (1º <script> da /) troca a URL na hora
    const url = new URL(page.url());
    const topo = await page.evaluate(() => document.getElementById("planos")?.getBoundingClientRect().top ?? null);
    await ctx.close();
    assert.equal(url.pathname, "/", `foi para ${url.pathname}`);
    assert.ok(topo !== null && Math.abs(topo) < 2, `#planos em ${topo}`);
  });
}

test("teste-piggy.js atrasado e o Plus 'Indisponível' antes dele: os cartões restantes ainda levam a UTM", async () => {
  // Sem o [href] no seletor do teste-piggy, o cartão sem href lança no meio do laço e os do Pro ficam sem UTM.
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  await ctx.route("**/auth/validate", r => r.fulfill({ status: 401, contentType: "application/json", body: "{}" }));
  await ctx.route("**/billing/plans-config", r => r.fulfill({ status: 200, contentType: "application/json",
    body: JSON.stringify({ essencial_available: true, plus_available: false, pro_available: true }) }));
  await ctx.route("**/teste-piggy.js*", async r => { await new Promise(ok => setTimeout(ok, 800)); await r.continue(); });
  const page = await ctx.newPage();
  const erros = [];
  page.on("pageerror", e => erros.push(e.message));
  await page.goto(`${ORIGIN}/index.html?utm_source=fb&fbclid=abc`, { waitUntil: "load" });
  const r = await page.$$eval(CARTOES, as => as.map(a => [a.closest("[data-plano]").dataset.plano, a.getAttribute("href")]));
  await ctx.close();
  assert.deepEqual(r.filter(([t]) => t === "plus").map(([, h]) => h), [null, null], "o Plus não virou Indisponível antes do script");
  assert.deepEqual(r.filter(([t]) => t === "pro").map(([, h]) => h),
    ["/assinar?plano=pro&ciclo=annual&utm_source=fb&fbclid=abc", "/assinar?plano=pro&ciclo=monthly&utm_source=fb&fbclid=abc"]);
  assert.deepEqual(erros, []);
});
