/**
 * /lp — landing de anúncio: o único botão ("Quero o PigBank" → quiz.pigbankai.com) só
 * aparece depois de a VSL tocar até o fim COM SOM. O portão é o <script> do fim
 * de frontend/lp.html, e a trava nasce no <head>.
 *
 * Mídia REAL por `data:` URI (WAV), como no vsl_gate.test.mjs: o `route.fulfill`
 * do Playwright não atende Range, o `seekable` fica em 0 e o Chromium grampeia
 * qualquer busca sozinho — o caso do "não dá pra avançar" passaria verde sem o
 * `seeking`. A rota do /brand/vsl.mp4 existe só para o primeiro src não dar 404.
 *
 * Controles do CLAUDE.md §3:
 *   · negativo — "fim mudo não libera", "avançar não vale" e "sem JS o botão
 *     aparece" são os casos que morrem se a regra correspondente sair;
 *   · positivo — "ouvir até o fim libera" e "a marca mostra o botão na volta":
 *     sem eles o grupo passaria numa página que nunca mostra o botão.
 *
 * Rodar: node --test tests/frontend/lp.test.mjs
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
const WAV = wavSilencioso(2);

/** Contexto novo a cada caso: a marca mora no localStorage, e reaproveitar o
 *  contexto faria o caso seguinte começar liberado por contaminação. */
async function abrir({ visto = false, erro = false, js = true, calmo = false, query = "", wav = WAV, antes } = {}) {
  const ctx = await browser.newContext({
    viewport: { width: 1280, height: 800 }, javaScriptEnabled: js,
    reducedMotion: calmo ? "reduce" : "no-preference",
  });
  const page = await ctx.newPage();
  await page.addInitScript(visto => {
    window.__ev = [];
    window.gtag = (...a) => window.__ev.push(a);
    if (visto) localStorage.setItem("pb_lp_vsl_visto", "1");
  }, visto);
  if (antes) await page.addInitScript(antes);
  await page.route("**/vsl.mp4*", r => erro
    ? r.fulfill({ status: 404, contentType: "text/plain", body: "no" })
    : r.fulfill({ status: 200, contentType: "audio/wav", body: wav }));
  await page.goto(`${ORIGIN}/lp.html${query}`, { waitUntil: "domcontentloaded" });
  if (js && !erro) {
    await page.evaluate(async uri => {
      const v = document.getElementById("vsl");
      v.src = uri; v.load();
      await new Promise(ok => v.addEventListener("loadedmetadata", ok, { once: true }));
    }, "data:audio/wav;base64," + wav.toString("base64"));
  }
  return { page, ctx };
}

const ctaVisivel = page => page.$eval("#lp-cta", a => getComputedStyle(a).visibility === "visible");
const marca = page => page.evaluate(() => localStorage.getItem("pb_lp_vsl_visto"));

test("ao abrir: botão escondido, vídeo mudo em loop sem controles, botão de som à vista", async () => {
  const { page, ctx } = await abrir();
  assert.equal(await ctaVisivel(page), false, "o botão nasceu visível");
  const v = await page.$eval("#vsl", v => ({ muted: v.muted, loop: v.loop, controls: v.hasAttribute("controls") }));
  assert.deepEqual(v, { muted: true, loop: true, controls: false });
  assert.ok(await page.isVisible("#vsl-som"));
  assert.match(await page.textContent("#vsl-som"), /Toque para ouvir/);
  await ctx.close();
});

test("chegar ao fim MUDO não libera", async () => {
  const { page, ctx } = await abrir();
  await page.evaluate(() => new Promise(ok => {
    const v = document.getElementById("vsl");
    v.loop = false; v.muted = true;
    v.addEventListener("ended", ok, { once: true });
    v.play();
  }));
  await page.waitForTimeout(200);
  assert.equal(await ctaVisivel(page), false, "o fim mudo liberou o botão");
  assert.equal(await marca(page), null);
  await ctx.close();
});

test("tocar recomeça do zero com som, e o fim dessa execução libera e grava a marca", async () => {
  const { page, ctx } = await abrir();
  await page.evaluate(() => { document.getElementById("vsl").currentTime = 1.5; });
  await page.click("#vsl-som");
  const logo = await page.$eval("#vsl", v => ({ t: v.currentTime, muted: v.muted, loop: v.loop }));
  assert.ok(logo.t < 0.5, `não voltou do zero: ${logo.t}`);
  assert.equal(logo.muted, false);
  assert.equal(logo.loop, false);
  assert.equal(await page.isVisible("#vsl-som"), false);
  assert.equal(await ctaVisivel(page), false, "liberou antes do fim");

  await page.waitForFunction(() => document.getElementById("vsl").ended, null, { timeout: 8000 });
  await page.waitForFunction(() => getComputedStyle(document.getElementById("lp-cta")).visibility === "visible");
  assert.equal(await marca(page), "1");
  assert.equal(await page.isVisible("#vsl-som"), false, "'Toque para continuar' apareceu por cima do vídeo no fim");
  const ev = await page.evaluate(() => window.__ev.map(e => [e[1], e[2] && e[2].percent]));
  assert.deepEqual(ev, [["vsl_play", undefined], ["vsl_progress", 25], ["vsl_progress", 50], ["vsl_progress", 75]]);
  await ctx.close();
});

test("com som, avançar o cursor volta para o ponto já ouvido", async () => {
  const { page, ctx } = await abrir();
  assert.ok(await page.evaluate(() => {
    const v = document.getElementById("vsl");
    return v.seekable.length > 0 && v.seekable.end(0) > 1;
  }), "a mídia do teste precisa ser buscável, senão o caso não mede nada");
  await page.click("#vsl-som");
  const parou = await page.evaluate(async () => {
    const v = document.getElementById("vsl");
    v.currentTime = v.duration - 0.01;
    await new Promise(ok => setTimeout(ok, 300));
    return v.currentTime;
  });
  assert.ok(parou < 1, `o salto não voltou: ${parou}`);
  assert.equal(await ctaVisivel(page), false, "pulou o vídeo e o botão apareceu");
  await ctx.close();
});

test("com som e pausado, avançar não conta como ouvido nem dispara vsl_progress", async () => {
  const { page, ctx } = await abrir({ wav: wavSilencioso(4) });
  await page.click("#vsl-som");
  await page.waitForFunction(() => document.getElementById("vsl").currentTime > 0.2);
  await page.evaluate(async () => {
    const v = document.getElementById("vsl");
    v.pause(); v.currentTime = v.duration * 0.8;
    await new Promise(ok => setTimeout(ok, 500));
  });
  const pct = await page.evaluate(() => window.__ev.filter(e => e[1] === "vsl_progress").map(e => e[2].percent));
  assert.deepEqual(pct, [], "o salto contou como ouvido");
  assert.ok(await page.$eval("#vsl", v => v.currentTime < 1));
  await ctx.close();
});

test("quem já assistiu vê o botão na volta", async () => {
  const { page, ctx } = await abrir({ visto: true });
  assert.equal(await ctaVisivel(page), true);
  await ctx.close();
});

test("erro no vídeo libera na hora, sem gravar a marca", async () => {
  const { page, ctx } = await abrir({ erro: true });
  await page.waitForFunction(() => !document.documentElement.classList.contains("lp-travado"), null, { timeout: 5000 });
  assert.equal(await ctaVisivel(page), true);
  assert.equal(await marca(page), null);
  await ctx.close();
});

test("sem JavaScript o botão aparece e o vídeo tem controles", async () => {
  const { page, ctx } = await abrir({ js: false });
  assert.equal(await ctaVisivel(page), true);
  assert.equal(await page.getAttribute("#lp-cta", "href"), "https://quiz.pigbankai.com/");
  assert.ok(await page.$eval("#vsl", v => v.hasAttribute("controls")));
  await ctx.close();
});

test("movimento reduzido: sem autoplay, com o botão para assistir", async () => {
  const { page, ctx } = await abrir({ calmo: true });
  assert.equal(await page.$eval("#vsl", v => v.paused), true);
  assert.match(await page.textContent("#vsl-som"), /Toque para assistir/);
  assert.equal(await ctaVisivel(page), false);
  await ctx.close();
});

test("utm_* (sem diferenciar maiúsculas) e fbclid seguem para o quiz; o resto não", async () => {
  const { page, ctx } = await abrir({ visto: true, query: "?utm_source=meta&x=1&UTM_Campaign=lp%20a&fbclid=AbC" });
  assert.equal(await page.getAttribute("#lp-cta", "href"),
               "https://quiz.pigbankai.com/?utm_source=meta&UTM_Campaign=lp+a&fbclid=AbC");
  const sem = await abrir({ visto: true });
  assert.equal(await sem.page.getAttribute("#lp-cta", "href"), "https://quiz.pigbankai.com/");
  await sem.ctx.close();
  await ctx.close();
});

test("a página só tem links para o quiz, o teste, /termos e /privacy", async () => {
  const { page, ctx } = await abrir({ visto: true });
  const hrefs = await page.$$eval("a[href]", as => as.map(a => a.getAttribute("href")).sort());
  assert.deepEqual(hrefs, ["/privacy", "/termos", "/teste", "https://quiz.pigbankai.com/"]);
  await ctx.close();
});

test("thread ocupada por 2,6s no meio da execução com som não trava o portão", async () => {
  const { page, ctx } = await abrir({ wav: wavSilencioso(6) });
  await page.click("#vsl-som");
  await page.waitForFunction(() => document.getElementById("vsl").currentTime > 0.3);
  await page.evaluate(() => { const fim = Date.now() + 2600; while (Date.now() < fim); });
  await page.waitForFunction(() => document.getElementById("vsl").ended, null, { timeout: 12000 });
  await page.waitForFunction(() => getComputedStyle(document.getElementById("lp-cta")).visibility === "visible",
                             null, { timeout: 2000 });
  const pct = await page.evaluate(() => window.__ev.filter(e => e[1] === "vsl_progress").map(e => e[2].percent));
  assert.deepEqual(pct, [25, 50, 75]);
  await ctx.close();
});

test("exceção no script do portão libera o botão e devolve os controles do vídeo", async () => {
  const { page, ctx } = await abrir({ antes: () => { delete window.URLSearchParams; } });
  assert.equal(await ctaVisivel(page), true, "JS quebrado deixou o botão travado");
  assert.ok(await page.$eval("#vsl", v => v.hasAttribute("controls")));
  assert.equal(await page.isVisible("#vsl-som"), false, "botão de som visível sem ninguém ouvindo o clique");
  await ctx.close();
});

test("localStorage que lança: abre travado e assistir com som libera", async () => {
  const { page, ctx } = await abrir({ antes: () => {
    Object.defineProperty(window, "localStorage", { get() { throw new DOMException("bloqueado", "SecurityError"); } });
  } });
  assert.equal(await ctaVisivel(page), false, "storage bloqueado abriu destravado");
  await page.click("#vsl-som");
  await page.waitForFunction(() => document.getElementById("vsl").ended, null, { timeout: 8000 });
  await page.waitForFunction(() => getComputedStyle(document.getElementById("lp-cta")).visibility === "visible",
                             null, { timeout: 2000 });
  await ctx.close();
});

test("duplo toque pausa e mostra 'Toque para continuar', que retoma de onde parou", async () => {
  const { page, ctx } = await abrir({ wav: wavSilencioso(6) });
  await page.click("#vsl-som");
  await page.waitForFunction(() => document.getElementById("vsl").currentTime > 0.8);
  await page.click("#vsl");                     // o 2º toque cai no vídeo e pausa
  await page.waitForSelector("#vsl-som", { state: "visible", timeout: 2000 });
  assert.match(await page.textContent("#vsl-som"), /Toque para continuar/);
  const parou = await page.$eval("#vsl", v => v.currentTime);
  await page.click("#vsl-som");
  await page.waitForSelector("#vsl-som", { state: "hidden", timeout: 2000 });
  const v = await page.$eval("#vsl", v => ({ t: v.currentTime, muted: v.muted, paused: v.paused }));
  assert.ok(v.t >= parou - 0.05, `voltou do zero em vez de retomar: ${v.t} < ${parou}`);
  assert.deepEqual([v.muted, v.paused], [false, false]);
  await ctx.close();
});

test("play() recusado ao tocar em ouvir mostra 'Toque para continuar'", async () => {
  const { page, ctx } = await abrir();
  await page.evaluate(() => {
    const v = document.getElementById("vsl");
    v.pause();
    window.__play = HTMLMediaElement.prototype.play;
    HTMLMediaElement.prototype.play = () => Promise.reject(new DOMException("não", "NotAllowedError"));
  });
  await page.click("#vsl-som");
  await page.waitForSelector("#vsl-som", { state: "visible", timeout: 2000 });
  assert.match(await page.textContent("#vsl-som"), /Toque para continuar/);
  await page.evaluate(() => { HTMLMediaElement.prototype.play = window.__play; });
  await page.click("#vsl-som");
  await page.waitForSelector("#vsl-som", { state: "hidden", timeout: 2000 });
  assert.equal(await page.$eval("#vsl", v => v.paused), false);
  await ctx.close();
});
