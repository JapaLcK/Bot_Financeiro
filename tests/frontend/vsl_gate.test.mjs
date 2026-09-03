/**
 * A VSL da landing trava os CTAs de /cadastro até o vídeo acabar.
 *
 * Pedido explícito do dono: assistir é obrigatório e não dá pra pular. O
 * portão vive no <script> "vsl-gate" do fim do frontend/index.html.
 *
 * A mídia é REAL, não simulada: um WAV de 1s gerado aqui. O <video> toca WAV
 * como toca MP4 — o que está sob teste é a máquina de eventos de mídia, e não
 * o decodificador.
 *
 * Ela entra por `data:` URI, e isso não é estilo. Servida pelo `route.fulfill`
 * do Playwright, que não atende Range, o Chromium reporta `seekable.end(0) === 0`
 * e grampeia QUALQUER busca de posição em 0 sozinho. O caso do avanço — o
 * coração do "não pode pular" — passava verde com o `seeking` inteiro apagado,
 * medindo o harness em vez do portão. Medido: com `fulfill`, `duration` 1 e
 * `seekable` 0; com `data:`, `seekable` 1 e o `currentTime` grudando em 0.99
 * antes de o clamp devolver pro 0. A rota continua existindo para o
 * /brand/vsl.mp4 não dar 404 e abrir o portão pelo `error` antes da troca.
 *
 * O grupo tem os dois controles do CLAUDE.md §3:
 *   · conserto — os 5 CTAs nascem travados, e o clique NÃO navega;
 *   · anti-pulo — arrastar pro fim volta pro ponto assistido e o portão
 *     continua fechado (é o caso que morre se o `seeking` sair);
 *   · falha ABERTA — vídeo em 404 libera. Portão que trava por defeito zera
 *     o cadastro do dia em silêncio;
 *   · quem JÁ tem conta — o nav-auth.js real (com /auth/validate mockado em
 *     200) troca o href dos CTAs por /app, e o portão tem de abrir. Sem este
 *     caso, todo visitante logado da landing via botão cinza para sempre:
 *     `liberar()` reconsultava por `a[href="/cadastro"]`, que a essa altura
 *     não casa com nada;
 *   · controle POSITIVO — assistindo até o fim o clique navega pro /cadastro
 *     de verdade. Sem ele o grupo passaria numa página com todos os botões
 *     quebrados, que é pior que o bug.
 *
 * Rodar:  npm run test:frontend
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { startServer } from "./_server.mjs";
import { chromium } from "playwright";

let ORIGIN, server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer());
                     browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

/** WAV PCM 8-bit mono, 8 kHz, `segundos` de silêncio. Chromium toca sem codec
 *  externo, e é o menor arquivo de mídia que dá duração e `ended` reais. */
function wavSilencioso(segundos) {
  const taxa = 8000, n = taxa * segundos;
  const buf = Buffer.alloc(44 + n, 0x80);          // 0x80 = silêncio em 8-bit
  buf.write("RIFF", 0); buf.writeUInt32LE(36 + n, 4); buf.write("WAVE", 8);
  buf.write("fmt ", 12); buf.writeUInt32LE(16, 16);
  buf.writeUInt16LE(1, 20); buf.writeUInt16LE(1, 22);
  buf.writeUInt32LE(taxa, 24); buf.writeUInt32LE(taxa, 28);
  buf.writeUInt16LE(1, 32); buf.writeUInt16LE(8, 34);
  buf.write("data", 36); buf.writeUInt32LE(n, 40);
  return buf;
}

/**
 * Abre a landing. `midia: false` deixa o vídeo dar 404 — é o caso da falha
 * aberta. O contexto é novo a cada chamada: o portão memoriza em
 * localStorage, e um contexto reaproveitado faria o caso seguinte começar
 * já liberado (verde por contaminação, não por conserto).
 */
async function abrirLanding({ midia = true, logado = false } = {}) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await ctx.newPage();
  const wav = wavSilencioso(1);
  // O nav-auth.js que roda é o de verdade — servido pelo mesmo servidor. Só a
  // resposta do /auth/validate é encenada; a reescrita dos CTAs é dele.
  if (logado) await page.route("**/auth/validate", r =>
    r.fulfill({ status: 200, contentType: "application/json", body: "{}" }));
  await page.route("**/vsl.mp4*", route => midia
    ? route.fulfill({ status: 200, contentType: "audio/wav", body: wav })
    : route.fulfill({ status: 404, contentType: "text/plain", body: "no" }));
  await page.goto(`${ORIGIN}/index.html`, { waitUntil: "domcontentloaded" });
  if (midia) {
    // Troca a fonte por uma buscável (ver o cabeçalho). Os ouvintes do portão
    // moram no ELEMENTO, não na fonte — trocar o `src` não desfaz nenhum.
    await page.evaluate(async uri => {
      const v = document.getElementById("vsl-video");
      v.src = uri; v.load();
      await new Promise(ok => v.addEventListener("loadedmetadata", ok, { once: true }));
    }, "data:audio/wav;base64," + wav.toString("base64"));
  }
  return { page, ctx };
}

const travados = page => page.$$eval('a[href="/cadastro"]',
  as => as.map(a => a.classList.contains("is-locked") && a.getAttribute("aria-disabled") === "true"));

/** Assiste do começo ao fim, de verdade. `muted` porque a política de autoplay
 *  do Chromium recusa `play()` com som sem gesto do usuário. */
const assistirAteOFim = page => page.evaluate(() => new Promise(ok => {
  const v = document.getElementById("vsl-video");
  v.addEventListener("ended", ok, { once: true });
  v.muted = true;
  v.play();
}));

test("os CTAs de /cadastro nascem travados, e o clique não navega", async () => {
  const { page, ctx } = await abrirLanding();
  const estados = await travados(page);
  assert.equal(estados.length, 5, "a landing tem 5 CTAs de /cadastro");
  assert.ok(estados.every(Boolean), "todo CTA de /cadastro nasce travado");

  // `force` porque o Playwright recusa clicar em [aria-disabled=true] por conta
  // própria — e é justo o navegador de verdade que NÃO recusa: o atributo diz
  // ao leitor de tela que o CTA está indisponível, não impede o clique. Sem o
  // force, o caso mediria a regra do Playwright em vez do portão.
  await page.click('.hero-cta a[href="/cadastro"]', { force: true });
  await page.waitForTimeout(300);
  assert.ok(page.url().endsWith("/index.html"), `clique travado navegou: ${page.url()}`);
  assert.match(await page.textContent("#vsl-status"), /até o fim/);
  await ctx.close();
});

test("arrastar a barra pro fim não vale por assistir", async () => {
  const { page, ctx } = await abrirLanding();
  // Guarda do harness: sem isto o caso volta a medir o Playwright. `seekable`
  // em 0 significa que o navegador recusa a busca sozinho, e aí passar não
  // prova clamp nenhum.
  assert.ok(await page.evaluate(() => {
    const v = document.getElementById("vsl-video");
    return v.seekable.length > 0 && v.seekable.end(0) > 0.5;
  }), "a mídia do teste precisa ser buscável, senão o caso não mede nada");

  // Sem o clamp, isto sozinho levaria o `timeupdate` a chamar liberar().
  const parou = await page.evaluate(async () => {
    const v = document.getElementById("vsl-video");
    v.currentTime = v.duration - 0.01;
    await new Promise(ok => setTimeout(ok, 300));
    return v.currentTime;
  });
  assert.ok(parou < 0.5, `o cursor deveria ter voltado pro início, ficou em ${parou}`);
  assert.ok((await travados(page)).every(Boolean), "pulou o vídeo e o portão abriu");
  await ctx.close();
});

test("assistindo até o fim, o cadastro abre — e o clique navega", async () => {
  const { page, ctx } = await abrirLanding();
  await assistirAteOFim(page);
  assert.ok((await travados(page)).every(e => e === false), "assistiu e continuou travado");
  assert.match(await page.textContent("#vsl-status"), /liberado/);

  await Promise.all([
    page.waitForURL(/\/cadastro$/, { timeout: 5000 }),
    page.click('.hero-cta a[href="/cadastro"]'),
  ]);
  await ctx.close();
});

test("quem já assistiu não assiste de novo", async () => {
  const { page, ctx } = await abrirLanding();
  await assistirAteOFim(page);
  await page.reload({ waitUntil: "domcontentloaded" });
  assert.ok((await travados(page)).every(e => e === false), "recarregou e travou de novo");
  await ctx.close();
});

test("vídeo que não carrega LIBERA — o portão falha aberto", async () => {
  const { page, ctx } = await abrirLanding({ midia: false });
  await page.waitForFunction(
    () => !document.querySelector('.hero-cta a[href="/cadastro"]').classList.contains("is-locked"),
    null, { timeout: 5000 });
  await ctx.close();
});

test("quem já tem conta não fica com os CTAs travados", async () => {
  const { page, ctx } = await abrirLanding({ logado: true });
  // O nav-auth troca href e texto; o portão tem de soltar os mesmos elementos.
  await page.waitForFunction(
    () => document.querySelectorAll(".is-locked").length === 0,
    null, { timeout: 5000 });
  assert.equal(await page.$$eval('a[href="/cadastro"]', a => a.length), 0,
               "o nav-auth deveria ter reescrito todos os CTAs");
  // E não vira memória: quem sair da conta volta a ser visitante e reassiste.
  assert.equal(await page.evaluate(() => localStorage.getItem("pb_vsl_visto")), null);
  await ctx.close();
});
