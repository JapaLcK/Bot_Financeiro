/**
 * A VSL da landing é opcional: nenhum caminho para /cadastro depende do vídeo.
 *
 * O grupo protege três contratos:
 *   · os seis CTAs são links acionáveis no HTML, inclusive sem JavaScript;
 *   · o vídeo continua `preload="none"` e permite busca livre;
 *   · play e progresso continuam disponíveis para a análise do funil.
 *
 * Rodar: npm run test:frontend
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { startServer } from "./_server.mjs";
import { chromium } from "playwright";

let ORIGIN, server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer());
                     browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

function wavSilencioso(segundos) {
  const taxa = 8000, n = taxa * segundos;
  const buf = Buffer.alloc(44 + n, 0x80);
  buf.write("RIFF", 0); buf.writeUInt32LE(36 + n, 4); buf.write("WAVE", 8);
  buf.write("fmt ", 12); buf.writeUInt32LE(16, 16);
  buf.writeUInt16LE(1, 20); buf.writeUInt16LE(1, 22);
  buf.writeUInt32LE(taxa, 24); buf.writeUInt32LE(taxa, 28);
  buf.writeUInt16LE(1, 32); buf.writeUInt16LE(8, 34);
  buf.write("data", 36); buf.writeUInt32LE(n, 40);
  return buf;
}

async function abrirLanding({ logado = false } = {}) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await ctx.newPage();
  const wav = wavSilencioso(1);
  if (logado) await page.route("**/auth/validate", r =>
    r.fulfill({ status: 200, contentType: "application/json", body: "{}" }));
  await page.route("**/vsl.mp4*", route =>
    route.fulfill({ status: 200, contentType: "audio/wav", body: wav }));
  await page.goto(`${ORIGIN}/index.html`, { waitUntil: "domcontentloaded" });
  await page.evaluate(async uri => {
    const v = document.getElementById("vsl-video");
    v.src = uri; v.load();
    await new Promise(ok => v.addEventListener("loadedmetadata", ok, { once: true }));
  }, "data:audio/wav;base64," + wav.toString("base64"));
  return { page, ctx };
}

test("todos os CTAs levam ao cadastro sem exigir o vídeo", async () => {
  const { page, ctx } = await abrirLanding();
  assert.equal(await page.$eval("#vsl-video", video => video.preload), "none",
               "a mídia não deve transferir bytes antes da intenção de reprodução");
  const estados = await page.$$eval('a[href="/cadastro"]', as => as.map(a => ({
    travado: a.classList.contains("is-locked"),
    aria: a.getAttribute("aria-disabled"),
  })));
  assert.equal(estados.length, 6, "a landing tem 6 CTAs de /cadastro");
  assert.ok(estados.every(e => !e.travado && e.aria === null),
            "nenhum CTA pode nascer bloqueado");
  assert.equal((await page.textContent("#vsl-cta")).trim(), "COMECE JÁ");

  await Promise.all([
    page.waitForURL(/\/cadastro$/, { timeout: 5000 }),
    page.click('.hero-cta a[href="/cadastro"]'),
  ]);
  await ctx.close();
});

test("o visitante pode avançar o vídeo sem bloquear o cadastro", async () => {
  const { page, ctx } = await abrirLanding();
  const posicao = await page.evaluate(async () => {
    window.__eventosVsl = [];
    window.gtag = (...args) => window.__eventosVsl.push(args);
    const v = document.getElementById("vsl-video");
    v.currentTime = v.duration - 0.01;
    await new Promise(ok => setTimeout(ok, 300));
    return {
      atual: v.currentTime,
      duracao: v.duration,
      progresso: window.__eventosVsl.filter(e => e[1] === "vsl_progress"),
    };
  });
  assert.ok(posicao.atual > posicao.duracao / 2,
            `a busca foi impedida: ${posicao.atual}/${posicao.duracao}`);
  assert.deepEqual(posicao.progresso, [],
                   "avançar o cursor não pode contar como tempo assistido");
  await ctx.close();
});

test("play e marcos de progresso continuam sendo medidos", async () => {
  const { page, ctx } = await abrirLanding();
  const eventos = await page.evaluate(async () => {
    window.__eventosVsl = [];
    window.gtag = (...args) => window.__eventosVsl.push(args);
    const v = document.getElementById("vsl-video");
    v.muted = true;
    await new Promise((ok, erro) => {
      v.addEventListener("ended", ok, { once: true });
      v.play().catch(erro);
    });
    return window.__eventosVsl;
  });
  assert.deepEqual(eventos.map(e => e[1]),
                   ["vsl_play", "vsl_progress", "vsl_progress", "vsl_progress"]);
  assert.deepEqual(eventos.slice(1).map(e => e[2].percent), [25, 50, 75]);
  await ctx.close();
});

test("o último intervalo antes da pausa também conta como assistido", async () => {
  const { page, ctx } = await abrirLanding();
  const eventos = await page.evaluate(() => {
    window.__eventosVsl = [];
    window.gtag = (...args) => window.__eventosVsl.push(args);
    const v = document.getElementById("vsl-video");
    Object.defineProperties(v, {
      currentTime: { configurable: true, writable: true, value: 0 },
      duration: { configurable: true, value: 1 },
      paused: { configurable: true, value: true },
    });
    v.dispatchEvent(new Event("play"));
    v.currentTime = 0.26;
    // O navegador já expõe paused=true quando entrega o timeupdate final.
    v.dispatchEvent(new Event("timeupdate"));
    return window.__eventosVsl.filter(e => e[1] === "vsl_progress");
  });
  assert.deepEqual(eventos.map(e => e[2].percent), [25]);
  await ctx.close();
});

test("quem já tem conta continua sendo direcionado ao dashboard", async () => {
  const { page, ctx } = await abrirLanding({ logado: true });
  await page.waitForFunction(
    () => document.querySelectorAll('a[href="/cadastro"]').length === 0,
    null, { timeout: 5000 });
  assert.equal(await page.textContent("#vsl-cta"), "Ir para o dashboard");
  await ctx.close();
});

test("sem JavaScript o cadastro também fica disponível", async () => {
  const ctx = await browser.newContext({ javaScriptEnabled: false });
  const page = await ctx.newPage();
  await page.goto(`${ORIGIN}/index.html`, { waitUntil: "domcontentloaded" });
  const b = await page.$eval("#vsl-cta", el => ({
    href: el.getAttribute("href"), classe: el.className,
    aria: el.getAttribute("aria-disabled"), texto: el.textContent.trim(),
  }));
  assert.equal(b.href, "/cadastro");
  assert.ok(!b.classe.includes("is-locked"));
  assert.equal(b.aria, null);
  assert.equal(b.texto, "COMECE JÁ");
  await ctx.close();
});
