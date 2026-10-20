/**
 * PR-A2b-1: o modal de conferência (frontend/bank-movements.js) para despesa/receita "pelo banco":
 * texto do fluxo (D8), trocar e desfazer só depois do confirm, um POST por ação e o 404 sem alerta.
 * O backend é simulado por rota; o contrato real está em tests/test_declaracao_a2b_acoes.py.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { chromium } from "playwright";
import { startServer } from "./_server.mjs";

let browser, server, origin;
before(async () => { ({ proc: server, origin } = await startServer()); browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });
async function until(fn, ms = 5000) {
  for (const end = Date.now() + ms; !fn();) {
    if (Date.now() > end) throw new Error("tempo esgotado");
    await new Promise(r => setTimeout(r, 25));
  }
}
const json = (body, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(body) });
const tx = (id, description, date) => ({ id, description, bank: "Nubank", amount: -50, date });
const gasto = { launch_id: 71, name: "mercado", bank: null, amount: -50, date: "2026-10-14", tipo: "despesa",
  fluxo: true, manual_only: false, requires_review: false, match: null, candidates: [tx(22, "SUPERMERCADO XYZ", "2026-10-14")] };
const receita = { ...gasto, launch_id: 72, name: "freela", amount: 80, tipo: "receita", candidates: [] };
const aporte = { launch_id: 73, name: "viagem", bank: "Nubank · Conta", amount: -500, date: "2026-10-14",
  tipo: "deposito_caixinha", fluxo: false, manual_only: false, requires_review: false, match: null, candidates: [] };
const casada = { ...gasto, manual_only: true, match: tx(22, "SUPERMERCADO XYZ", "2026-10-14"),
  candidates: [tx(23, "PIX MARIA", "2026-10-15")] };

async function pageFor(width, movements, { onPost, launchId } = {}) {
  const page = await browser.newPage({ viewport: { width, height: 900 } });
  const posts = [], gets = [];
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.origin !== origin) return route.abort();
    if (/\.[a-z0-9]+$/i.test(url.pathname)) return route.continue();
    if (url.pathname === "/auth/validate") return route.fulfill(json({ user_id: 1 }));
    const m = url.pathname.match(/^\/open-finance\/1\/movements\/(confirm|undo)$/);
    if (m) {
      posts.push({ path: m[1], body: route.request().postDataJSON() });
      const out = onPost ? await onPost(m[1]) : null;
      return route.fulfill(out || json({ ok: true }));
    }
    if (url.pathname === "/open-finance/1/movements") {
      gets.push(url.search);
      return route.fulfill(json({ movements }));
    }
    return route.fulfill(json({}));
  });
  await page.goto(`${origin}/dashboard.html`);
  await page.waitForFunction(() => Boolean(window.BankMovements) && USER_ID === 1);
  await page.evaluate(id => { _ensureGenericConfirmModal(); window.__saved = [];
    window.BankMovements.open(1, async msg => { window.__saved.push(msg); }, { launchId: id }); }, launchId ?? null);
  return { page, posts, gets };
}

for (const width of [1365, 390]) {
  test(`fluxo: gasto e receita sem sinal, aporte como hoje, dentro da tela e com foco preso em ${width}px`, async () => {
    const { page, gets } = await pageFor(width, [gasto, receita, aporte]);
    const modal = page.locator("#bank-movements-overlay .modal");
    try {
      await page.getByText("Gasto · R$ 50,00 · Pago pelo banco · 2026-10-14", { exact: true }).waitFor();
      await page.getByText("Receita · R$ 80,00 · Recebido no banco · 2026-10-14", { exact: true }).waitFor();
      await page.getByText("-R$ 500,00 · Nubank · Conta · 2026-10-14", { exact: true }).waitFor();
      const texto = await modal.innerText();
      assert.ok(!texto.includes("Valor líquido a conferir") && !texto.includes("Banco a conferir"), texto);
      assert.deepEqual(gets, [""]);
      assert.equal(await page.getByRole("button", { name: "Apagar" }).count(), 2);  // só o fluxo
      const b = await modal.boundingBox();
      assert.ok(b.x >= 0 && b.x + b.width <= width, JSON.stringify(b));
      assert.ok(await modal.evaluate(n => n.scrollWidth <= n.clientWidth));
      for (let i = 0; i < 10; i++) {
        await page.keyboard.press("Tab");
        assert.ok(await page.evaluate(() => document.getElementById("bank-movements-overlay").contains(document.activeElement)));
      }
      if (process.env.PB_QA_DIR) {
        mkdirSync(process.env.PB_QA_DIR, { recursive: true });
        await modal.evaluate(async n => { await Promise.all(n.getAnimations().map(a => a.finished)); });
        await page.screenshot({ path: `${process.env.PB_QA_DIR}/a2b-fluxo-${width}.png`, fullPage: true });
      }
    } finally { await page.close(); }
  });

  test(`casada: mostra o par; trocar envia só depois do confirm em ${width}px`, async () => {
    const { page, posts, gets } = await pageFor(width, [casada], { launchId: 71 });
    try {
      await page.getByText("Conferida com o extrato", { exact: true }).waitFor();
      await page.getByText("SUPERMERCADO XYZ · -R$ 50,00 · 2026-10-14 · Nubank", { exact: true }).waitFor();
      assert.deepEqual(gets, ["?launch_id=71"]);
      if (process.env.PB_QA_DIR) {
        await page.locator("#bank-movements-overlay .modal").evaluate(async n => { await Promise.all(n.getAnimations().map(a => a.finished)); });
        await page.screenshot({ path: `${process.env.PB_QA_DIR}/a2b-casada-${width}.png` });
      }
      await page.locator("#bank-movement-71").selectOption("23");
      await page.getByRole("button", { name: "Trocar a transação" }).click();
      await page.getByText("Passa a ser: 2026-10-15 · -R$ 50,00 · Nubank · PIX MARIA", { exact: false }).waitFor();
      assert.ok((await page.locator("#generic-confirm-body").innerText()).replaceAll("\u00a0", " ").includes("Conferida hoje com: 2026-10-14 · -R$ 50,00 · Nubank · SUPERMERCADO XYZ"));
      assert.deepEqual(posts, []);
      await page.locator("#generic-confirm-ok").click();
      await until(() => gets.length === 2);  // recarregou depois do POST
      assert.deepEqual(posts, [{ path: "confirm", body: { launch_id: 71, transaction_id: 23, replace_transaction_id: 22 } }]);
    } finally { await page.close(); }
  });
}

test("desfazer: confirm de perigo, um POST mesmo com dois cliques, corpo com o par visto", async () => {
  let solta;
  const segura = new Promise(r => { solta = r; });
  const { page, posts, gets } = await pageFor(800, [casada], { launchId: 71, onPost: () => segura.then(() => null) });
  try {
    const undo = page.getByRole("button", { name: "Desfazer a conferência" });
    await undo.waitFor();
    await undo.evaluate(b => { b.click(); b.click(); });  // clique duplo antes do confirm
    await page.getByText("Ao desfazer, a transação volta para o extrato", { exact: false }).waitFor();
    assert.equal(await page.locator("#generic-confirm-ok").getAttribute("class"), "inst-delete-btn");
    await page.locator("#generic-confirm-ok").click();
    await until(() => posts.length === 1);
    assert.equal(await undo.isDisabled(), true);
    await undo.evaluate(b => b.click());  // em voo: travado
    solta();
    await until(() => gets.length === 2);
    assert.deepEqual(posts, [{ path: "undo", body: { launch_id: 71, transaction_id: 22 } }]);
  } finally { await page.close(); }
});

test("afterSave: ok leva a mensagem, 404 leva undefined sem alerta; outro erro alerta, recarrega e não chama", async () => {
  const esperado = { 200: ["Conferência desfeita."], 404: [undefined], 409: [] };
  for (const [status, alerta] of [[200, false], [404, false], [409, true]]) {
    const { page, gets } = await pageFor(800, [casada], { launchId: 71, onPost: () => json({ detail: "x" }, status) });
    try {
      await page.getByRole("button", { name: "Desfazer a conferência" }).click();
      await page.locator("#generic-confirm-ok").click();
      if (alerta) {
        await page.getByText("Não foi possível concluir", { exact: false }).waitFor();
        await page.locator("#generic-confirm-ok").click();
      }
      await until(() => gets.length === 2);
      assert.equal(await page.getByText("Não foi possível concluir", { exact: false }).isVisible(), false, `status ${status}`);
      assert.deepEqual(await page.evaluate(() => window.__saved), esperado[status], `afterSave com status ${status}`);
    } finally { await page.close(); }
  }
});

test("fechar o modal com o POST em voo: sem alerta falso e o afterSave roda", async () => {
  let solta;
  const segura = new Promise(r => { solta = r; });
  const { page, posts } = await pageFor(800, [casada], { launchId: 71, onPost: () => segura.then(() => null) });
  try {
    await page.getByRole("button", { name: "Desfazer a conferência" }).click();
    await page.locator("#generic-confirm-ok").click();
    await until(() => posts.length === 1);
    await page.getByRole("button", { name: "Fechar" }).click();
    solta();
    await page.waitForFunction(() => window.__saved.length === 1);
    assert.equal(await page.locator("#generic-confirm-overlay.open").count(), 0, "alerta de erro depois de sucesso");
  } finally { await page.close(); }
});

test("texto do banco e nome da declaração entram como texto, nunca como HTML", async () => {
  const evil = '<img src=x onerror="window.__xss=1">';
  const { page } = await pageFor(800, [{ ...casada, name: evil, match: tx(22, evil, "2026-10-14"), candidates: [tx(23, evil, "2026-10-15")] }], { launchId: 71 });
  try {
    await page.locator("#bank-movement-71").selectOption("23");
    await page.getByRole("button", { name: "Trocar a transação" }).click();
    await page.locator("#generic-confirm-ok").waitFor();
    assert.equal(await page.evaluate(() => window.__xss), undefined);
    assert.equal(await page.locator("#bank-movements-overlay img, #generic-confirm-overlay img").count(), 0);
  } finally { await page.close(); }
});

test("Esc no confirm não envia POST e o botão continua utilizável", async () => {
  const { page, posts } = await pageFor(800, [casada], { launchId: 71 });
  try {
    await page.getByRole("button", { name: "Desfazer a conferência" }).click();
    await page.locator("#generic-confirm-ok").waitFor();
    await page.keyboard.press("Escape");
    await new Promise(r => setTimeout(r, 300));
    assert.deepEqual(posts, []);
    if (await page.locator("#bank-movements-overlay").count()) {
      assert.equal(await page.getByRole("button", { name: "Desfazer a conferência" }).isDisabled(), false);
    }
  } finally { await page.close(); }
});

test("escolher no fluxo avisa que vira só manual; apagar fecha e chama o apagar do dashboard", async () => {
  const { page, posts } = await pageFor(800, [gasto]);
  try {
    await page.locator("#bank-movement-71").selectOption("22");
    await page.getByRole("button", { name: "Conferir vínculo" }).click();
    await page.getByText("não será mais ligada automaticamente", { exact: false }).waitFor();
    await page.locator("#generic-confirm-cancel").click();
    assert.deepEqual(posts, []);
    await page.evaluate(() => { window.confirmDeleteLaunch = (...a) => { window.__apagou = a; }; });
    await page.getByRole("button", { name: "Apagar" }).click();
    assert.equal(await page.locator("#bank-movements-overlay").count(), 0);
    assert.deepEqual(await page.evaluate(() => window.__apagou), [71, "mercado", 50]);
  } finally { await page.close(); }
});
