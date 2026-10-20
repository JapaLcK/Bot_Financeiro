/**
 * /oferta — LP de comparação (frontend/oferta.html + oferta.js). O markup é estático e
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
async function abrir({ js = true, calmo = false, mobile = false, relogio = false } = {}) {
  const ctx = await browser.newContext({
    viewport: mobile ? { width: 390, height: 844 } : { width: 1280, height: 800 },
    javaScriptEnabled: js, reducedMotion: calmo ? "reduce" : "no-preference",
  });
  const page = await ctx.newPage();
  const erros = [];
  page.on("pageerror", e => erros.push("pageerror: " + e.message));
  page.on("response", r => { if (r.status() >= 400) erros.push(`${r.status()} ${r.url()}`); });
  if (relogio) await page.clock.install({ time: new Date("2026-01-01T08:00:00") });
  await page.goto(`${ORIGIN}/oferta.html`, { waitUntil: "load" });
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
