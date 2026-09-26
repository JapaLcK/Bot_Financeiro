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
