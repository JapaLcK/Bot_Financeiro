/**
 * Trilho "Como funciona" da index: um card aberto por vez.
 *
 * Onze asserções, cada uma discriminando uma forma de quebrar:
 *  1. hover no card 3 → 3 aberto, 1 e 2 com check (is-done), nenhum is-idle;
 *  2. card aberto ≥ 2× a largura de um fechado (falha se o CSS não carregar —
 *     é a prova de que a medição mede algo);
 *  3. hover PAUSA o ciclo: 7s (> os 6000ms) com o mouse sobre o card 1 e ele
 *     continua aberto;
 *  4. bolinhas alinhadas entre cards E entre estados: done/idle ancoram o
 *     CÍRCULO no meio do card (altura igual), não o bloco dentro do stage (que
 *     encolhe quando o h4 do foot quebra, e cujo texto tem 1 ou 2 linhas);
 *  5. o autoplay RODA sem interação nenhuma — o par da asserção 3, que sozinha
 *     passa igual com o setInterval deletado;
 *  6. mobile 390×844: a demo do card aberto não cobre nem corta o h4 do rodapé;
 *  7. nada começa antes de o trilho aparecer na tela (ciclo e animações da demo);
 *  8. o ciclo RETOMA quando o mouse sai do trilho (a pausa em si é a 3);
 *  9. Espaço com um card focado continua rolando a página;
 * 10. clique antes de o observer disparar mantém o ciclo parado depois dele;
 * 11. o donut do card 3 usa os offsets do SVG — sem eles as 4 fatias saem todas
 *     das 12h, empilhadas, e a de 16% desaparece por baixo da última.
 *
 * Rodar:  npm run test:frontend
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { chromium } from "playwright";

const REPO = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const FRONTEND = join(REPO, "frontend");
const PORT = Number(process.env.PB_HIW_TEST_PORT || 8901);
const ORIGIN = `http://127.0.0.1:${PORT}`;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function startServer() {
  const proc = spawn("python3", ["-m", "http.server", String(PORT), "--bind", "127.0.0.1", "--directory", FRONTEND],
    { stdio: "ignore" });
  for (let i = 0; i < 100; i++) {
    await sleep(100);
    if (proc.exitCode !== null) throw new Error(`http.server morreu (porta ${PORT} ocupada?)`);
    try { if ((await fetch(`${ORIGIN}/index.html`)).ok) return proc; } catch { /* ainda subindo */ }
  }
  proc.kill();
  throw new Error(`http.server não subiu em ${ORIGIN}`);
}

let server, browser;
before(async () => { server = await startServer(); browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

/** Desktop: o trilho só vira linha (flex-grow) a partir de 900px. */
async function openIndex(opts = { viewport: { width: 1280, height: 900 } }) {
  const page = await browser.newPage(opts);
  await page.goto(`${ORIGIN}/index.html`);
  await page.waitForSelector(".hiw-card");
  return page;
}

/** Espera o scroll suave (html { scroll-behavior: smooth } na site.css) parar.
    Sem isto, uma medição de rolagem mede a inércia da rolagem anterior. */
const scrollParado = async (page) => {
  let a = -1, b = await page.evaluate(() => scrollY);
  for (let i = 0; i < 30 && a !== b; i++) { await sleep(100); a = b; b = await page.evaluate(() => scrollY); }
  return b;
};

/** Rola até o trilho: ele só começa a rodar quando entra na tela. */
const verTrilho = async (page) => {
  await page.evaluate(() => document.querySelector(".hiw-rail").scrollIntoView({ block: "center" }));
  await scrollParado(page);
};

const states = (page) => page.$$eval(".hiw-card", (els) =>
  els.map((e) => ["is-open", "is-done", "is-idle"].filter((c) => e.classList.contains(c)).join("|")));

test("hover no 3º card: ele abre, os anteriores viram concluídos", async () => {
  const page = await openIndex();
  await page.hover(".hiw-card:nth-child(3)");
  assert.deepEqual(await states(page), ["is-done", "is-done", "is-open"]);
  assert.deepEqual(await page.$$eval(".hiw-card", (els) => els.map((e) => e.getAttribute("aria-current"))),
    [null, null, "step"]);
  await page.close();
});

test("card aberto é pelo menos 2× mais largo que um fechado", async () => {
  const page = await openIndex();
  await page.hover(".hiw-card:nth-child(1)");
  await sleep(800);   // transição de flex-grow é .6s
  const [aberto, fechado] = await page.$$eval(".hiw-card", (els) =>
    [els[0].getBoundingClientRect().width, els[1].getBoundingClientRect().width]);
  assert.ok(aberto >= 2 * fechado, `aberto=${Math.round(aberto)}px fechado=${Math.round(fechado)}px`);
  await page.close();
});

test("hover pausa o ciclo (6000ms): 7s parado e o card 1 segue aberto", async () => {
  const page = await openIndex();
  await page.hover(".hiw-card:nth-child(1)");
  await sleep(7000);
  assert.deepEqual(await states(page), ["is-open", "is-idle", "is-idle"]);
  await page.close();
});

test("bolinhas do mesmo estado ficam na mesma altura entre os cards", async () => {
  const page = await openIndex();
  /** centro y do seletor dentro de cada card marcado com `classe` */
  const centros = (classe, sel) => page.$$eval(`.hiw-card.${classe} ${sel}`, (els) =>
    els.map((e) => { const b = e.getBoundingClientRect(); return b.top + b.height / 2; }));

  await page.click(".hiw-card:nth-child(3)");   // 1 e 2 viram is-done
  await sleep(800);                             // flex-grow leva .6s
  const [t1, t2] = await centros("is-done", ".hiw-tick");
  assert.ok(Math.abs(t1 - t2) <= 1, `check: c1=${t1.toFixed(1)} c2=${t2.toFixed(1)}`);

  await page.click(".hiw-card:nth-child(1)");   // 2 e 3 viram is-idle
  await sleep(800);
  const [r2, r3] = await centros("is-idle", ".hiw-ring");
  assert.ok(Math.abs(r2 - r3) <= 1, `anel: c2=${r2.toFixed(1)} c3=${r3.toFixed(1)}`);

  // o caso que o usuário vê: check (c1) e anel (c3) lado a lado, estados diferentes
  await page.click(".hiw-card:nth-child(2)");
  await sleep(800);
  const [[k1], [a3]] = [await centros("is-done", ".hiw-tick"), await centros("is-idle", ".hiw-ring")];
  assert.ok(Math.abs(k1 - a3) <= 1, `check c1=${k1.toFixed(1)} vs anel c3=${a3.toFixed(1)}`);
  await page.close();
});

test("sem interação nenhuma o autoplay abre o card 2 (ciclo de 6000ms)", async () => {
  const page = await openIndex();
  // sem isto o teste passaria a testar o nada: o autoplay só liga em hover fino
  assert.ok(await page.evaluate(() => matchMedia("(hover: hover) and (pointer: fine)").matches),
    "contexto sem hover fino — o autoplay nem ligaria");
  await verTrilho(page);
  await sleep(7000);   // mouse parado em 0,0, fora do trilho
  assert.deepEqual(await states(page), ["is-done", "is-open", "is-idle"]);
  await page.close();
});

test("mobile 390×844: a demo do card aberto não cobre nem corta o h4 do rodapé", async () => {
  // touch emulado: é o alvo real e, de quebra, desliga o autoplay (hover: none)
  const page = await openIndex({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
  for (const n of [1, 2, 3]) {
    await page.click(`.hiw-card:nth-child(${n})`);
    const m = await page.$eval(`.hiw-card:nth-child(${n})`, (c) => {
      const r = (s) => c.querySelector(s).getBoundingClientRect();
      return { demo: r(".hiw-demo").bottom, h4t: r(".hiw-foot h4").top, h4b: r(".hiw-foot h4").bottom,
               base: c.getBoundingClientRect().bottom, aberto: c.classList.contains("is-open") };
    });
    assert.ok(m.aberto, `card ${n} não abriu no toque`);
    assert.ok(m.h4t - m.demo > 0, `card ${n}: a demo invade o h4 em ${(m.demo - m.h4t).toFixed(0)}px`);
    assert.ok(m.base - m.h4b > 0, `card ${n}: overflow:hidden corta ${(m.h4b - m.base).toFixed(0)}px do h4`);
  }
  await page.close();
});

test("nada começa antes de o trilho aparecer na tela", async () => {
  const page = await openIndex();          // carrega no topo; o trilho fica ~4000px abaixo
  await sleep(7000);                       // mais que um ciclo de 6000ms
  assert.deepEqual(await states(page), ["is-open", "is-idle", "is-idle"], "o ciclo andou fora da tela");
  const gastas = await page.$eval(".hiw-card:nth-child(1)", (c) => c.getAnimations({ subtree: true }).length);
  assert.equal(gastas, 0, `a demo gastou ${gastas} animações antes de aparecer`);

  await verTrilho(page);
  assert.equal(await page.$eval(".hiw-card:nth-child(1)", (c) => c.getAnimations({ subtree: true }).length), 3,
    "as 3 animações do chat do card 1 não começaram na entrada");
  await sleep(7000);
  assert.deepEqual(await states(page), ["is-done", "is-open", "is-idle"], "o ciclo não andou depois de aparecer");
  await page.close();
});

test("o ciclo retoma quando o mouse sai do trilho", async () => {
  const page = await openIndex({ viewport: { width: 1440, height: 900 } });
  // faixa em que o trilho já está visível mas o observer ainda não disparou
  const topo = await page.$eval(".hiw-rail", (r) => r.getBoundingClientRect().top + scrollY);
  await page.evaluate((y) => scrollTo(0, y), topo - 800);
  await sleep(400);
  // movimento de verdade: hover() teleporta e não produz o caminho do usuário
  for (const x of [700, 712, 724]) await page.mouse.move(x, 850);
  await verTrilho(page);          // o leitor continua rolando
  await page.mouse.move(20, 700); // e tira o mouse do trilho
  await sleep(7000);
  assert.deepEqual(await states(page), ["is-done", "is-open", "is-idle"]);
  await page.close();
});

test("Espaço com um card focado continua rolando a página", async () => {
  const page = await openIndex();
  await verTrilho(page);
  await page.focus(".hiw-card:nth-child(1)");
  const antes = await scrollParado(page);
  await page.keyboard.press(" ");
  await sleep(200);
  const depois = await scrollParado(page);
  assert.ok(depois > antes, `o Espaço não rolou a página: ${antes} -> ${depois}`);
  await page.close();
});

test("clique antes de o observer disparar mantém o ciclo parado", async () => {
  const page = await openIndex();
  const topo = await page.$eval(".hiw-rail", (r) => r.getBoundingClientRect().top + scrollY);
  await page.evaluate((y) => scrollTo(0, y), topo - 841);   // ~9% do trilho na tela
  await scrollParado(page);
  assert.ok(await page.$eval(".hiw-rail", (r) => r.classList.contains("is-esperando")),
    "o observer já disparou — o teste não está mais medindo o caso");

  // card 3, não o 2: "is-done,is-open,is-idle" é o mesmo estado de um passo do
  // ciclo, então o teste passava até com o listener de clique deletado.
  const x = await page.$eval(".hiw-card:nth-child(3)", (c) => {
    const b = c.getBoundingClientRect(); return Math.round(b.left + b.width / 2);
  });
  await page.mouse.click(x, 870);   // por coordenada: o click() do locator rolaria a página antes
  await page.mouse.move(20, 200);   // e o mouse sai: o que tem de segurar é o clique, não o hover

  assert.deepEqual(await states(page), ["is-done", "is-done", "is-open"], "o clique não abriu o card 3");

  await verTrilho(page);
  await sleep(7000);   // um passo do ciclo levaria ao card 1 (volta do módulo)
  assert.deepEqual(await states(page), ["is-done", "is-done", "is-open"]);
  await page.close();
});

test("o donut do card 3 usa os offsets do SVG, não o zero da CSS", async () => {
  const page = await openIndex();
  await page.click(".hiw-card:nth-child(3)");
  const [attr, computado] = await page.$$eval(".hiw-card:nth-child(3) .hiw-donut circle", (cs) => [
    cs.map((c) => Number(c.getAttribute("stroke-dashoffset") || 0)),
    cs.map((c) => parseFloat(getComputedStyle(c).strokeDashoffset)),
  ]);
  assert.ok(attr.filter((v) => v !== 0).length === 3, `os offsets sumiram do HTML: ${attr}`);
  assert.deepEqual(computado, attr, "a CSS está sobrescrevendo o stroke-dashoffset do SVG");
  await page.close();
});
