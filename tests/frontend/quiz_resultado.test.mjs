/**
 * /q (frontend/quiz-resultado.js): o fragmento do quiz vira o cookie `quiz_result`
 * e a página segue pro /cadastro levando a UTM e deixando p/r para trás.
 * O servidor revalida o cookie; aqui se mede só o lado do navegador.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { startServer } from "./_server.mjs";
import { chromium } from "playwright";

const FRONTEND = new URL("../../frontend/", import.meta.url);
const RASTREIO = /facebook|googletagmanager|clarity/;

let ORIGIN, server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer());
                     browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

/** Abre a /q e devolve para onde ela mandou, o cookie gravado e o rastreio pedido. */
async function abrir(url, prepara = async () => {}) {
  const ctx = await browser.newContext();
  const externos = [];
  ctx.on("request", (req) => { if (RASTREIO.test(req.url())) externos.push(req.url()); });
  await prepara(ctx);
  const page = await ctx.newPage();
  await page.goto(url, { waitUntil: "commit" });
  await page.waitForURL(/\/cadastro/, { waitUntil: "commit" });
  const destino = new URL(page.url());
  const cookie = (await ctx.cookies()).find((c) => c.name === "quiz_result");
  await ctx.close();
  assert.deepEqual(externos, []);
  assert.equal(destino.pathname, "/cadastro");
  assert.equal(destino.hash, "");
  return { destino, cookie };
}

const q = (resto) => `${ORIGIN}/quiz-resultado.html${resto}`;

test("fragmento vira cookie de 24h e a UTM segue sem p nem r", async () => {
  const { destino, cookie } = await abrir(q("?utm_source=ig&fbclid=teste#p=dividas&r=acdbd"));
  assert.equal(cookie.value, "v1.dividas.acdbd");
  assert.equal(cookie.sameSite, "Lax");
  assert.equal(cookie.secure, false);
  assert.ok(Math.abs(cookie.expires - (Date.now() / 1000 + 86400)) < 120, `expires ${cookie.expires}`);
  assert.equal(destino.search, "?utm_source=ig&fbclid=teste");
});

for (const resto of ["#p=dividas&r=acdbd?utm_source=ig&fbclid=teste", "#p=dividas&r=acdbd&utm_source=ig&fbclid=teste"]) {
  test(`UTM grudada depois do #: ${resto}`, async () => {
    const { destino, cookie } = await abrir(q(resto));
    assert.equal(cookie.value, "v1.dividas.acdbd");
    assert.equal(destino.search, "?utm_source=ig&fbclid=teste");
  });
}

test("p e r na query por engano saem da URL e não viram cookie", async () => {
  const { destino, cookie } = await abrir(q("?p=dividas&r=acdbd&utm_source=ig#p=investir"));
  assert.equal(cookie.value, "v1.investir");
  assert.equal(destino.search, "?utm_source=ig");
});

for (const perfil of ["padrao", "admin"]) {
  test(`perfil fora da lista (${perfil}) não grava cookie e segue pro cadastro`, async () => {
    const { destino, cookie } = await abrir(q(`#p=${perfil}&r=acdbd`));
    assert.equal(cookie, undefined);
    assert.equal(destino.search, "");
  });
}

const velho = (ctx) => ctx.addCookies([{ name: "quiz_result", value: "v1.dividas.acdbd", url: ORIGIN }]);

for (const resto of ["#p=admin", ""]) {
  test(`resultado rejeitado (${resto || "sem fragmento"}) apaga o cookie de uma visita anterior`, async () => {
    const { cookie } = await abrir(q(resto), velho);
    assert.equal(cookie, undefined);
  });
}

test("resultado válido sobrescreve o cookie de uma visita anterior", async () => {
  const { cookie } = await abrir(q("#p=investir&r=bdcae"), velho);
  assert.equal(cookie.value, "v1.investir.bdcae");
});

test("respostas inválidas gravam só o perfil", async () => {
  const { cookie } = await abrir(q("#p=dividas&r=zzzzz"));
  assert.equal(cookie.value, "v1.dividas");
});

test("em https o cookie sai Secure", async () => {
  const html = readFileSync(new URL("quiz-resultado.html", FRONTEND), "utf8");
  const js = readFileSync(new URL("quiz-resultado.js", FRONTEND), "utf8");
  const { cookie } = await abrir("https://pigbankai.test/q#p=dividas&r=acdbd", (ctx) =>
    ctx.route("https://pigbankai.test/**", (route) => {
      const { pathname } = new URL(route.request().url());
      if (pathname === "/quiz-resultado.js") return route.fulfill({ contentType: "application/javascript", body: js });
      return route.fulfill({ contentType: "text/html", body: pathname === "/q" ? html : "cadastro" });
    }));
  assert.equal(cookie.value, "v1.dividas.acdbd");
  assert.equal(cookie.secure, true);
});

// ── e/c: a /q confirma o e-mail e cria a conta pelo /auth/verify-email ──────────

const CSRF = "csrf-q";

/** Abre a /q com e/c, grava os POST de /auth e responde verify/resend com `respostas`. */
async function confirmar(resto, respostas = {}, viewport = undefined) {
  const ctx = await browser.newContext(viewport ? { viewport } : {});
  await ctx.addCookies([{ name: "csrf_token", value: CSRF, url: ORIGIN }]);
  const posts = [];
  await ctx.route(`${ORIGIN}/auth/**`, (route) => {
    const req = route.request();
    const { pathname } = new URL(req.url());
    posts.push({ url: req.url(), pathname, body: req.postDataJSON(), csrf: req.headers()["x-csrf-token"] });
    const [status, body] = respostas[pathname] || [200, { ok: true }];
    return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
  });
  const page = await ctx.newPage();
  await page.goto(q(resto));
  await page.locator("#confirma").waitFor();
  return { ctx, page, posts };
}

const DESTINO = [200, { dashboard_url: "http://127.0.0.1:1/precos?escolha=1" }];

test("e/c: mostra o e-mail, nada sai antes do clique, e/c só no corpo do POST", async () => {
  const { ctx, page, posts } = await confirmar(
    "?utm_source=ig#p=dividas&r=acdbd&e=joao+x@gmail.com&c=123456&fbclid=f1",
    { "/auth/verify-email": DESTINO });
  assert.equal(await page.locator("h1 strong").textContent(), "joao+x@gmail.com?");
  const semFrag = new URL(page.url());
  assert.equal(semFrag.hash, "");
  assert.equal(semFrag.search, "?utm_source=ig&fbclid=f1");
  await page.waitForTimeout(300);
  assert.deepEqual(posts, [], "POST saiu antes do clique em Continuar");
  const cookie = (await ctx.cookies()).find((c) => c.name === "quiz_result");
  assert.equal(cookie.value, "v1.dividas.acdbd");

  const navegou = page.waitForRequest((req) => req.url().includes("/precos"));
  await page.click("#continuar");
  const final = new URL((await navegou).url());
  assert.equal(posts.length, 1);
  assert.equal(posts[0].pathname, "/auth/verify-email");
  assert.deepEqual(posts[0].body, { email: "joao+x@gmail.com", code: "123456" });
  assert.equal(posts[0].csrf, CSRF);
  assert.equal(new URL(posts[0].url).search, "");
  assert.equal(final.pathname, "/precos");
  assert.equal(final.search, "?escolha=1&utm_source=ig&fbclid=f1");
  await ctx.close();
});

test("e-mail vai por textContent, nunca como HTML", async () => {
  const { ctx, page } = await confirmar("#p=dividas&e=%3Cimg%20src%3Dx%3E@a.com&c=123456");
  assert.equal(await page.locator("h1 img").count(), 0);
  assert.equal(await page.locator("h1 strong").textContent(), "<img+src=x>@a.com?");
  await ctx.close();
});

test("400: pede o código de novo, não mostra o detail, e o reenviar chama /auth/quiz/resend", async () => {
  const { ctx, page, posts } = await confirmar("#p=dividas&e=a@b.com&c=111111",
    { "/auth/verify-email": [400, { detail: "segredo-do-servidor" }] });
  assert.equal(await page.locator("#campo-codigo").isVisible(), false);
  await page.click("#continuar");
  await page.locator("#campo-codigo").waitFor();
  assert.ok(!(await page.content()).includes("segredo-do-servidor"));
  assert.equal(await page.locator("a[href='/login']").isVisible(), true);

  await page.click("#reenviar");
  await page.waitForFunction(() => document.getElementById("msg").textContent !== "");
  assert.deepEqual(posts.map((p) => [p.pathname, p.body]), [
    ["/auth/verify-email", { email: "a@b.com", code: "111111" }],
    ["/auth/quiz/resend", { email: "a@b.com" }],
  ]);
  await page.fill("#codigo", "654321");
  await page.click("#continuar");
  await page.waitForFunction(() => !document.getElementById("continuar").disabled);
  assert.deepEqual(posts[2].body, { email: "a@b.com", code: "654321" });
  await ctx.close();
});

test("429: pede para aguardar", async () => {
  const { ctx, page } = await confirmar("#p=dividas&e=a@b.com&c=111111",
    { "/auth/verify-email": [429, { detail: "x" }] });
  await page.click("#continuar");
  await page.waitForFunction(() => /Aguarde/.test(document.getElementById("msg").textContent));
  await ctx.close();
});

test("e sem c segue pro /cadastro sem levar o e-mail", async () => {
  const { destino, cookie } = await abrir(q("?utm_source=ig#p=dividas&e=a@b.com"));
  assert.equal(cookie.value, "v1.dividas");
  assert.equal(destino.search, "?utm_source=ig");
});

for (const [nome, viewport] of [["desktop", { width: 1280, height: 800 }], ["mobile", { width: 375, height: 667 }]]) {
  test(`confirmação sem estouro horizontal no ${nome}, com e-mail longo e o formulário aberto`, async () => {
    const longo = `${"nome.sobrenome.bem.comprido".repeat(3)}@exemplo-de-dominio.com.br`;
    const { ctx, page } = await confirmar(`#p=dividas&e=${longo}&c=111111`,
      { "/auth/verify-email": [400, {}] }, viewport);
    await page.click("#continuar");
    await page.locator("#campo-codigo").waitFor();
    const { sw, cw } = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth,
                                                    cw: document.documentElement.clientWidth }));
    assert.ok(sw <= cw, `scrollWidth ${sw} > clientWidth ${cw}`);
    // Para olhar a tela: PB_SHOT_DIR=<pasta> node --test tests/frontend/quiz_resultado.test.mjs
    if (process.env.PB_SHOT_DIR) await page.screenshot({ path: `${process.env.PB_SHOT_DIR}/q-${nome}.png` });
    await ctx.close();
  });
}
