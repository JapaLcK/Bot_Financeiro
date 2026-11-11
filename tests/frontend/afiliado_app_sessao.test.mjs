/**
 * #1412 no /app (frontend/dashboard.js, `requestAffiliatePayout`): o saque confere com o servidor (`GET /auth/validate`) que a sessão
 * ainda é da conta da página antes do POST. O marcador `pb_conta` deixou de decidir: ele sumia ou aparecia sem troca de conta e o
 * saque morria numa recarga calada. A chave nunca sai da página.
 * Controles negativos: forçar a comparação a dar "igual" deixa vermelhos C3 e C4; voltar a linha do marcador
 * (`getCookie("pb_conta") !== _contaDaCarga` → reload) deixa vermelhos C1 e C2.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { startServer } from "./_server.mjs";
import { CHAVE, abrirApp, naArea, pedirApp } from "./_afiliado.mjs";

let ORIGIN, server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer()); browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

const TROCOU = "A conta conectada mudou. Esta solicitação de saque não foi enviada. A página será recarregada; confira a conta antes de tentar novamente.";
const FALHOU = "Não conseguimos verificar sua sessão. Esta solicitação de saque não foi enviada. Tente novamente.";
const JANELA = 450; // a confirmação ignora o ponteiro nos primeiros 400 ms (#1411)

/** A confirmação aberta; `antes` roda com ela na tela (a outra aba agindo). Clica "Confirmar saque" e devolve o que se seguiu. */
async function confirmarCom({ marca = "42", width, height } = {}, antes) {
  const a = await abrirApp(browser, ORIGIN, { marca, width, height });
  const posts = [];
  const logs = [];
  a.page.on("console", (m) => logs.push(m.text()));
  let recargas = 0;
  a.page.on("framenavigated", (f) => { if (f === a.page.mainFrame()) recargas += 1; });
  await pedirApp(a.page, a.ctx, posts);
  await antes?.(a);
  await a.page.waitForTimeout(JANELA);
  await a.page.locator("#generic-confirm-ok").click();
  await a.page.waitForTimeout(400);
  return { ...a, posts, logs, recargas: () => recargas };
}
const alerta = (page) => page.locator("#generic-confirm-overlay.open #generic-confirm-body");
const semAlerta = async (page) => assert.equal(await page.locator("#generic-confirm-overlay.open").count(), 0);

// C1, C2: o marcador mudou sem troca de conta. Antes a página recarregava calada e o saque não saía.
for (const [nome, marca, mexe] of [
  ["sumiu (Max-Age expirou)", "42", () => { document.cookie = "pb_conta=; path=/; max-age=0"; }],
  ["apareceu (sessão de antes do deploy)", null, () => { document.cookie = "pb_conta=42; path=/"; }],
]) {
  test(`C: o marcador ${nome} e o servidor diz a mesma conta: o saque sai, sem recarga nem alerta`, async () => {
    const t = await confirmarCom({ marca }, ({ page }) => page.evaluate(mexe));
    try {
      assert.deepEqual(t.posts, [JSON.stringify({ pix_key: CHAVE })]);
      assert.equal(t.recargas(), 0);
      await semAlerta(t.page);
    } finally { await t.ctx.close(); }
  });
}

// C3: a troca de verdade (login de outra conta noutra aba): o marcador e o servidor dizem 43.
for (const [w, h] of [[1280, 800], [390, 844], [320, 568]]) {
  test(`C ${w}x${h}: a conta trocou com a confirmação aberta: nenhum POST, o aviso na tela e a recarga depois do OK`, async () => {
    const t = await confirmarCom({ width: w, height: h }, ({ page, sessao }) => { sessao.uid = 43; return page.evaluate(() => { document.cookie = "pb_conta=43; path=/"; }); });
    try {
      assert.deepEqual(t.posts, [], "nenhum POST com a conta de outra pessoa");
      assert.equal(await alerta(t.page).innerText(), TROCOU);
      const caixa = await t.page.locator("#generic-confirm-overlay .modal").boundingBox();
      assert.ok(caixa.y >= 0 && caixa.y + caixa.height <= h && caixa.x >= 0 && caixa.x + caixa.width <= w, JSON.stringify(caixa));
      assert.equal(t.recargas(), 0, "a recarga espera o OK");
      const onde = await t.page.evaluate(() => JSON.stringify([{ ...localStorage }, { ...sessionStorage }, document.cookie, location.href]));
      assert.ok(!onde.includes(CHAVE), onde);
      assert.ok(!t.logs.some((l) => l.includes(CHAVE)), "console");
      await t.page.waitForTimeout(JANELA);
      await t.page.locator("#generic-confirm-ok").click();
      await t.page.waitForTimeout(400);
      assert.ok(t.recargas() >= 1, "a página recarrega");
      assert.deepEqual(t.posts, []);
    } finally { await t.ctx.close(); }
  });
}

// C4: o marcador sumiu E a sessão é de outra conta: quem decide é o servidor.
test("C: marcador apagado e o servidor diz outra conta: nenhum POST, o aviso", async () => {
  const t = await confirmarCom({}, ({ page, sessao }) => { sessao.uid = 43; return page.evaluate(() => { document.cookie = "pb_conta=; path=/; max-age=0"; }); });
  try {
    assert.deepEqual(t.posts, []);
    assert.equal(await alerta(t.page).innerText(), TROCOU);
  } finally { await t.ctx.close(); }
});

// C5: não deu para conferir: nada sai, a chave fica e o Sacar volta.
for (const [nome, falha] of [["sem rede", (r) => r.abort()], ["500", (r) => r.fulfill({ status: 500, json: { detail: "x" } })]]) {
  test(`C: a conferência falha (${nome}): nenhum POST, o aviso de falha, a chave fica e o Sacar volta`, async () => {
    const t = await confirmarCom({}, ({ ctx }) => ctx.route("**/auth/validate", falha));
    try {
      assert.deepEqual(t.posts, []);
      assert.equal(await alerta(t.page).innerText(), FALHOU);
      await t.page.waitForTimeout(JANELA);
      await t.page.locator("#generic-confirm-ok").click();
      await semAlerta(t.page);
      assert.equal(await t.page.locator("#affiliate-pix-input").inputValue(), CHAVE);
      assert.equal(await t.page.locator("#affiliate-payout-btn").isEnabled(), true);
      assert.equal(t.recargas(), 0);
    } finally { await t.ctx.close(); }
  });
}

// C6: a sessão caiu entre a conferência e o POST e o refresh traz outra conta: o auth-refresh.js não refaz o POST e vai ao login.
test("C: o POST volta 401 e o refresh diz sessão divergente: 1 POST só, sem retry, e o login com 'acao-nao-enviada'", async () => {
  const a = await abrirApp(browser, ORIGIN, { marca: "42" });
  try {
    const posts = [];
    await a.ctx.route("**/api/affiliate/payout", (r) => { posts.push(r.request().postData()); return r.fulfill({ status: 401, headers: { "WWW-Authenticate": "Bearer" }, json: { detail: "x" } }); });
    await a.ctx.route("**/auth/refresh", (r) => r.fulfill({ status: 401, json: { detail: "sessao_divergente" } }));
    await a.ctx.route("**/auth/logout", (r) => r.fulfill({ json: { ok: true } }));
    await a.ctx.route("**/login**", (r) => r.fulfill({ contentType: "text/html", body: "<p>login</p>" }));
    await naArea(a.page);
    await a.page.locator("#affiliate-pix-input").fill(CHAVE);
    await a.page.locator("#affiliate-payout-btn").click();
    await a.page.waitForTimeout(JANELA);
    await Promise.all([a.page.waitForURL(/\/login\?/), a.page.locator("#generic-confirm-ok").click()]);
    const url = new URL(a.page.url());
    assert.equal(url.searchParams.get("aviso"), "acao-nao-enviada");
    assert.equal(posts.length, 1, "sem retry");
  } finally { await a.ctx.close(); }
});
