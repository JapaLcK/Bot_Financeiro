/**
 * #1411 no /app (frontend/dashboard.js, `#generic-confirm-overlay`): o 2º clique do duplo clique (ou duplo toque) no "Sacar" caía no
 * "Confirmar saque" da confirmação que o 1º clique tinha acabado de abrir, ou no fundo, que a fechava. Na janela de
 * `JANELA_DO_DUPLO_CLIQUE` os botões não recebem ponteiro e o fundo não fecha; o Enter segurado (repetição) não confirma.
 * Controle negativo: tirar o `pointerEvents = "none"` de `_genericModalAbrir` deixa vermelhos os "alinhado" de 390 e 320 e os toques;
 * tirar a conta de tempo do clique no fundo deixa vermelhos o "sem alinhar" de 1280 e o alerta; tirar o `e.repeat` deixa vermelho o Enter segurado.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { chromium } from "playwright";
import { startServer } from "./_server.mjs";
import { CHAVE, abrirApp, naArea } from "./_afiliado.mjs";

let ORIGIN, server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer()); browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

const TAMANHOS = [[1280, 800], [390, 844], [320, 568]];
const ABERTO = "#generic-confirm-overlay.open";
const JANELA = 450; // acima da constante (400 ms)

/** Na área do afiliado com a chave digitada, anotando os POSTs do saque. */
async function naTela(opts) {
  const a = await abrirApp(browser, ORIGIN, opts);
  const posts = [];
  await a.ctx.route("**/api/affiliate/payout", (r) => { posts.push(r.request().postData()); return r.fulfill({ json: { ok: true, payout: { id: 9 } } }); });
  await naArea(a.page);
  await a.page.locator("#affiliate-pix-input").fill(CHAVE);
  return { ...a, posts };
}

/** O centro do Sacar e o que fica sob ele com a confirmação aberta. Abre e fecha a confirmação de verdade (sem ponteiro) antes,
 *  para medir com o texto real do saque. */
async function sobOSacar(page) {
  await page.evaluate(() => { document.getElementById("affiliate-payout-btn").click(); document.getElementById("generic-confirm-cancel").click(); });
  const b = await page.locator("#affiliate-payout-btn").boundingBox();
  const x = b.x + b.width / 2, y = b.y + b.height / 2;
  const sob = await page.evaluate(([px, py]) => {
    const ov = document.getElementById("generic-confirm-overlay");
    ov.classList.add("open");
    ov.querySelector(".modal-acts").style.pointerEvents = ""; // a geometria, não a janela
    const el = document.elementFromPoint(px, py);
    ov.classList.remove("open");
    return el?.id || el?.className || el?.tagName;
  }, [x, y]);
  return { x, y, sob };
}

/** Rola a página para o centro do Sacar ficar na altura do centro do "Confirmar saque" (a página pode não rolar o bastante). */
async function alinhar(page) {
  const okY = await page.evaluate(() => {
    document.getElementById("affiliate-payout-btn").click();
    const r = document.getElementById("generic-confirm-ok").getBoundingClientRect();
    document.getElementById("generic-confirm-cancel").click();
    return (r.top + r.bottom) / 2;
  });
  const b0 = await page.locator("#affiliate-payout-btn").boundingBox();
  await page.evaluate((dy) => window.scrollBy(0, dy), b0.y + b0.height / 2 - okY);
  return sobOSacar(page);
}

const caminhos = {};
for (const [w, h] of TAMANHOS) {
  test(`${w}x${h}: duplo clique no Sacar alinhado ao "Confirmar saque" deixa a confirmação aberta, sem POST`, async () => {
    const { ctx, page, posts } = await naTela({ width: w, height: h });
    try {
      const { x, y, sob } = await alinhar(page);
      caminhos[`${w} alinhado`] = sob;
      await page.mouse.dblclick(x, y);
      await page.waitForTimeout(500);
      assert.deepEqual(posts, [], `sob o Sacar: ${sob}`);
      assert.equal(await page.locator(ABERTO).count(), 1, `sob o Sacar: ${sob}`);
    } finally { await ctx.close(); }
  });

  test(`${w}x${h}: duplo clique no Sacar sem alinhar deixa a confirmação aberta, sem POST`, async () => {
    const { ctx, page, posts } = await naTela({ width: w, height: h });
    try {
      await page.locator("#affiliate-payout-btn").scrollIntoViewIfNeeded();
      const { x, y, sob } = await sobOSacar(page);
      caminhos[`${w} sem alinhar`] = sob;
      await page.mouse.dblclick(x, y);
      await page.waitForTimeout(500);
      assert.deepEqual(posts, [], `sob o Sacar: ${sob}`);
      assert.equal(await page.locator(ABERTO).count(), 1);
    } finally { await ctx.close(); }
  });

  test(`${w}x${h}: duplo clique no Sacar sem chave deixa o alerta aberto`, async () => {
    const { ctx, page } = await naTela({ width: w, height: h });
    try {
      await page.locator("#affiliate-pix-input").fill("");
      await page.locator("#affiliate-payout-btn").dblclick();
      await page.waitForTimeout(500);
      assert.equal(await page.locator(ABERTO).count(), 1);
      assert.match(await page.locator("#generic-confirm-body").innerText(), /Informe sua chave Pix/);
    } finally { await ctx.close(); }
  });
}

// Os dois caminhos do defeito estão cobertos: num caso o 2º clique cai no OK, noutro no fundo (o registro sai na mensagem).
test("os tamanhos exercitam o caminho do botão e o do fundo", () => {
  const sob = Object.values(caminhos);
  assert.ok(sob.includes("generic-confirm-ok"), JSON.stringify(caminhos));
  assert.ok(sob.includes("generic-confirm-overlay"), JSON.stringify(caminhos));
});

for (const ms of [0, 120, 250]) {
  test(`390x844 toque: dois toques no Sacar com ${ms} ms deixam a confirmação aberta, sem POST`, async () => {
    const { ctx, page, posts } = await naTela({ width: 390, height: 844, contexto: { isMobile: true, hasTouch: true, deviceScaleFactor: 3 } });
    try {
      const { x, y } = await alinhar(page);
      await page.touchscreen.tap(x, y);
      if (ms) await page.waitForTimeout(ms);
      await page.touchscreen.tap(x, y);
      await page.waitForTimeout(500);
      assert.deepEqual(posts, []);
      assert.equal(await page.locator(ABERTO).count(), 1);
    } finally { await ctx.close(); }
  });
}

// ── positivos: o conserto não virou recusa ──
for (const [w, h] of TAMANHOS) {
  test(`${w}x${h}: passada a janela, o "Confirmar saque" manda 1 POST com {pix_key}`, async () => {
    const { ctx, page, posts } = await naTela({ width: w, height: h });
    try {
      await page.locator("#affiliate-payout-btn").click();
      await page.waitForTimeout(JANELA);
      await page.locator("#generic-confirm-ok").click();
      await page.waitForFunction(() => !document.querySelector("#generic-confirm-overlay.open"));
      await page.waitForTimeout(200);
      assert.deepEqual(posts, [JSON.stringify({ pix_key: CHAVE })]);
    } finally { await ctx.close(); }
  });
}

test("Esc dentro da janela fecha, sem POST", async () => {
  const { ctx, page, posts } = await naTela({});
  try {
    await page.locator("#affiliate-payout-btn").click();
    await page.keyboard.press("Escape");
    await page.waitForTimeout(200);
    assert.equal(await page.locator(ABERTO).count(), 0);
    assert.deepEqual(posts, []);
  } finally { await ctx.close(); }
});

test("Cancelar passada a janela fecha, mantém a chave e libera o Sacar", async () => {
  const { ctx, page, posts } = await naTela({});
  try {
    await page.locator("#affiliate-payout-btn").click();
    await page.waitForTimeout(JANELA);
    await page.locator("#generic-confirm-cancel").click();
    await page.waitForTimeout(200);
    assert.equal(await page.locator(ABERTO).count(), 0);
    assert.deepEqual(posts, []);
    assert.equal(await page.locator("#affiliate-pix-input").inputValue(), CHAVE);
    assert.equal(await page.locator("#affiliate-payout-btn").isEnabled(), true);
  } finally { await ctx.close(); }
});

test("Enter no 'Confirmar saque' confirma (o teclado não espera a janela)", async () => {
  const { ctx, page, posts } = await naTela({});
  try {
    await page.locator("#affiliate-payout-btn").click();
    assert.equal(await page.evaluate(() => document.activeElement?.id), "generic-confirm-ok");
    await page.keyboard.press("Enter");
    await page.waitForTimeout(300);
    assert.deepEqual(posts, [JSON.stringify({ pix_key: CHAVE })]);
  } finally { await ctx.close(); }
});

test("Enter segurado no Sacar abre a confirmação e a repetição não confirma", async () => {
  const { ctx, page, posts } = await naTela({});
  try {
    await page.locator("#affiliate-payout-btn").focus();
    await page.keyboard.down("Enter");
    await page.locator(ABERTO).waitFor();
    await page.keyboard.down("Enter"); // já apertada: sai com `repeat: true`
    await page.keyboard.down("Enter");
    await page.keyboard.up("Enter");
    await page.waitForTimeout(300);
    assert.equal(await page.locator(ABERTO).count(), 1);
    assert.deepEqual(posts, []);
  } finally { await ctx.close(); }
});

// §0.7: a janela tem três cópias (o JS clássico não importa o TS do /painel).
test("a janela é a mesma no /app, no modals.js e no /painel", () => {
  const ler = (f) => Number(readFileSync(new URL(`../../${f}`, import.meta.url), "utf8").match(/JANELA_DO_DUPLO_CLIQUE = (\d+)/)?.[1]);
  const v = ["frontend/dashboard.js", "frontend/modals.js", "webapp/src/dashboard/parts/dialog.ts"].map(ler);
  assert.deepEqual(v, [400, 400, 400]);
});

// ── o mesmo defeito no frontend/modals.js (`.pig-modal`), que é o confirmModal da settings: "Encerrar todas as outras" sessões ──
const SESSOES = { sessions: [
  { jti: "a", is_current: true, device_label: "Este", ip: "1.1.1.1", created_at: "2026-11-01T12:00:00Z", last_seen_at: "2026-11-10T12:00:00Z" },
  { jti: "b", is_current: false, device_label: "Outro", ip: "2.2.2.2", created_at: "2026-11-01T12:00:00Z", last_seen_at: "2026-11-10T12:00:00Z" },
] };
async function naSettings() {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const page = await ctx.newPage();
  const deletes = [];
  const corpo = (b) => ({ status: 200, contentType: "application/json", body: JSON.stringify(b) });
  await page.route("**/*", (r) => {
    const u = new URL(r.request().url());
    if (u.origin !== ORIGIN) return r.abort();
    return /\.[a-z0-9]+$/i.test(u.pathname) ? r.continue() : r.fulfill(corpo({}));
  });
  await page.route("**/auth/validate", (r) => r.fulfill(corpo({ user_id: 1 })));
  await page.route("**/auth/me", (r) => r.fulfill(corpo({ has_password: true })));
  await page.route("**/settings/1/security", (r) => r.fulfill(corpo({ ok: true, user_id: 1, email: "a@b.com", plan: "pro", identities: [] })));
  await page.route("**/settings/1/sessions", (r) => {
    if (r.request().method() === "DELETE") { deletes.push(1); return r.fulfill(corpo({ revoked: 1 })); }
    return r.fulfill(corpo(SESSOES));
  });
  await page.goto(`${ORIGIN}/settings.html`);
  const btn = page.locator("#sessions-revoke-others-btn");
  await btn.waitFor({ state: "visible" });
  // alinha o botão ao primário do modal (no celular os botões do modal ocupam a largura toda)
  const priY = await page.evaluate(async () => {
    document.getElementById("sessions-revoke-others-btn").click(); // o modal de verdade, com o texto de verdade
    await new Promise((r) => setTimeout(r, 300)); // passada a animação de entrada (.18 s)
    const r = document.querySelector(".pig-modal-btn-destructive").getBoundingClientRect();
    document.querySelector(".pig-modal-btn-cancel").click();
    return (r.top + r.bottom) / 2;
  });
  const b0 = await btn.boundingBox();
  await page.evaluate((dy) => window.scrollBy(0, dy), b0.y + b0.height / 2 - priY);
  const b = await btn.boundingBox();
  return { ctx, page, deletes, x: b.x + b.width / 2, y: b.y + b.height / 2 };
}
const pigAberto = (page) => page.locator(".pig-modal-overlay").count();

test("settings 390x844: dois cliques em 'Encerrar todas as outras' (120 ms) não confirmam, e o 2º no fundo não fecha", async () => {
  const { ctx, page, deletes, x, y } = await naSettings();
  try {
    await page.mouse.click(x, y);
    await page.waitForTimeout(120);
    await page.mouse.click(x, y);
    await page.waitForTimeout(400);
    assert.equal(await pigAberto(page), 1, "o 2º clique sobre o primário");
    assert.deepEqual(deletes, []);
    await page.keyboard.press("Escape");
    await page.mouse.click(x, y);
    await page.waitForTimeout(120);
    await page.mouse.click(5, 5);
    await page.waitForTimeout(300);
    assert.equal(await pigAberto(page), 1, "o 2º clique no fundo");
    assert.deepEqual(deletes, []);
  } finally { await ctx.close(); }
});

test("settings 390x844: passada a janela, o primário confirma (1 DELETE) e o fundo cancela", async () => {
  const { ctx, page, deletes, x, y } = await naSettings();
  try {
    await page.mouse.click(x, y);
    await page.waitForTimeout(JANELA);
    await page.mouse.click(5, 5);
    await page.waitForTimeout(200);
    assert.equal(await pigAberto(page), 0);
    assert.deepEqual(deletes, []);
    await page.mouse.click(x, y);
    await page.waitForTimeout(JANELA);
    await page.locator(".pig-modal-btn-destructive").click();
    await page.waitForTimeout(300);
    assert.deepEqual(deletes, [1]);
  } finally { await ctx.close(); }
});

// ── o rótulo: o confirmModal do /app lia só `okText`/`danger`, e 6 chamadores passam os nomes do modals.js (`confirmText`/`destructive`) ──
// Controle negativo: voltar a `opts.okText || "OK"` e `!!opts.danger` deixa vermelho o primeiro (o botão sai "OK", verde).
test("o botão do confirmModal do /app segue o que os chamadores reais pedem", async () => {
  const { ctx, page } = await abrirApp(browser, ORIGIN, {});
  try {
    const botao = () => page.evaluate(() => { const b = document.getElementById("generic-confirm-ok"); const r = [b.textContent, b.className]; document.getElementById("generic-confirm-cancel").click(); return r; });
    await page.evaluate(() => { deleteInvestment("Tesouro"); }); // confirmText + destructive
    assert.deepEqual(await botao(), ["Excluir", "inst-delete-btn"]);
    await page.evaluate(() => { _catEditState = { id: 7, is_system: false, original_name: "Mercado" }; categoryDeleteFromModal(); }); // okText + danger
    assert.deepEqual(await botao(), ["Excluir", "inst-delete-btn"]);
    await page.evaluate(() => { // confirmText + destructive: false (antecipar fatura futura)
      payBillState = { balance: 0, selectedId: 1, submitting: false, bills: [{ id: 1, due_amount: 100, period_end: "2099-12-31", card_name: "Nubank", label: "dez" }] };
      document.getElementById("pay-bill-amount").value = "10";
      submitPayBill();
    });
    assert.deepEqual(await botao(), ["Sim, pagar", "btn-save"]);
  } finally { await ctx.close(); }
});
