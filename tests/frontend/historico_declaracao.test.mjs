/**
 * PR-A2b-1: o detalhe do lançamento no Histórico ganha "Conferência com o banco" (#ld-bank) só para a
 * despesa/receita declarada pelo banco (`bank_declaration`, db/analytics.py), e só na origem Histórico
 * (DP-6). Abre a dashboard.html real: a CSS do detalhe (.ld-acts) está na página, então a medida de
 * largura muda se o botão novo estourar a linha.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { chromium } from "playwright";
import { startServer } from "./_server.mjs";

let browser, server, origin;
before(async () => { ({ proc: server, origin } = await startServer()); browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });
const json = body => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
const ITENS = [
  { id: 301, tipo: "despesa", valor: 50, alvo: "Mercado", nota: null, criado_em: "2026-10-14T10:00:00", bank_declaration: true },
  { id: 302, tipo: "despesa", valor: 30, alvo: "Padaria", nota: null, criado_em: "2026-10-14T09:00:00", bank_declaration: false },
];

const tx = (id, description) => ({ id, description, bank: "Nubank", amount: -50, date: "2026-10-14" });
const ID301 = { launch_id: 301, name: "Mercado", bank: null, amount: -50, date: "2026-10-14", tipo: "despesa", fluxo: true,
  manual_only: false, requires_review: false, match: null, candidates: [tx(22, "SUPERMERCADO XYZ")] };
async function until(fn, ms = 5000) {
  for (const end = Date.now() + ms; !fn();) {
    if (Date.now() > end) throw new Error("tempo esgotado");
    await new Promise(r => setTimeout(r, 25));
  }
}

async function pageFor(width, { movements = [], depois = ITENS } = {}) {
  const page = await browser.newPage({ viewport: { width, height: 800 } });
  const gets = [], posts = [], listGets = [], deletes = [];
  await page.route("**/*", route => {
    const url = new URL(route.request().url());
    if (url.origin !== origin) return route.abort();
    if (/\.[a-z0-9]+$/i.test(url.pathname)) return route.continue();
    if (url.pathname === "/auth/validate") return route.fulfill(json({ user_id: 1 }));
    if (url.pathname === "/open-finance/1/movements") {
      gets.push(url.search);
      return route.fulfill(json({ movements }));
    }
    if (/^\/open-finance\/1\/movements\/(confirm|undo)$/.test(url.pathname)) {
      posts.push(url.pathname);
      return route.fulfill(json({ ok: true }));
    }
    if (url.pathname === "/history/1/list") {  // só o recarregamento chega aqui: a timeline inicial é desenhada direto
      listGets.push(url.search);
      return route.fulfill(json({ ok: true, items: depois }));
    }
    if (route.request().method() === "DELETE" && url.pathname === "/launches/1/301") {
      deletes.push(url.pathname);
      return route.fulfill(json({ ok: true }));
    }
    return route.fulfill(json({}));
  });
  await page.goto(`${origin}/dashboard.html`);
  await page.waitForFunction(() => Boolean(window.BankMovements) && USER_ID === 1);
  await page.mouse.move(width - 10, 700);  // fora do menu lateral, que abre por cima no :hover
  await page.evaluate(itens => {
    document.getElementById("history-view").classList.add("active");
    renderHistoryTimeline({ ok: true, items: itens }, false);
  }, ITENS);
  return { page, gets, posts, listGets, deletes };
}

test("#ld-bank só na declaração e só a partir do Histórico; abre a conferência dela, um overlay por vez", async () => {
  const { page, gets } = await pageFor(1365);
  try {
    await page.locator(".tx-row", { hasText: "Padaria" }).click();
    await page.locator("#launch-detail-overlay.open").waitFor();
    assert.equal(await page.locator("#ld-bank").isVisible(), false);
    await page.locator("#ld-close").click();

    await page.evaluate(l => { _launchDetailCurrent = l; _launchDetailSource = "overview"; _renderLaunchDetail(l); }, ITENS[0]);
    assert.equal(await page.locator("#ld-bank").isVisible(), false, "fora do Histórico o botão não aparece");
    await page.locator("#ld-close").click();

    await page.locator(".tx-row", { hasText: "Mercado" }).click();
    await page.locator("#ld-bank").click();
    await page.getByRole("dialog", { name: "Movimentações bancárias" }).waitFor();
    assert.deepEqual(gets, ["?launch_id=301"]);
    assert.equal(await page.locator(".overlay.open").count(), 1);
    assert.equal(await page.locator("#launch-detail-overlay.open").count(), 0);
  } finally { await page.close(); }
});

test("sem window.BankMovements o botão não aparece", async () => {
  const { page } = await pageFor(1365);
  try {
    await page.evaluate(() => { delete window.BankMovements; });
    await page.locator(".tx-row", { hasText: "Mercado" }).click();
    await page.locator("#launch-detail-overlay.open").waitFor();
    assert.equal(await page.locator("#ld-bank").isVisible(), false);
  } finally { await page.close(); }
});

for (const width of [390, 320]) {
  test(`.ld-acts com o botão novo cabe em ${width}px`, async () => {
    const { page } = await pageFor(width);
    try {
      await page.locator(".tx-row", { hasText: "Mercado" }).click();
      await page.locator("#ld-bank").waitFor();
      const m = await page.evaluate(() => {
        const acts = document.querySelector(".ld-acts"), modal = document.querySelector("#launch-detail-overlay .modal");
        const caixa = modal.getBoundingClientRect();
        const fora = [...acts.querySelectorAll("button")].filter(b => b.offsetParent)
          .map(b => [b.id, b.getBoundingClientRect()]).filter(([, r]) => r.left < caixa.left || r.right > caixa.right).map(([id]) => id);
        return { sw: acts.scrollWidth, cw: acts.clientWidth, fora, modal: [caixa.left, caixa.right] };
      });
      assert.ok(m.sw <= m.cw && m.fora.length === 0, JSON.stringify(m));
      if (process.env.PB_QA_DIR) {
        mkdirSync(process.env.PB_QA_DIR, { recursive: true });
        await page.locator("#launch-detail-overlay .modal").evaluate(async n => { await Promise.all(n.getAnimations().map(a => a.finished)); });
        await page.screenshot({ path: `${process.env.PB_QA_DIR}/a2b-detalhe-${width}.png` });
      }
    } finally { await page.close(); }
  });
}

// Fiação de _launchDetailBank (dashboard.js): o modal abre POR CIMA do detalhe e, ao terminar, o Histórico
// recarrega e o dashboard é avisado. Cada ação traz o estado novo no corpo de /history/1/list.
const MERCADO_V2 = { ...ITENS[0], alvo: "Mercado v2" };
const casada = { ...ID301, match: tx(22, "SUPERMERCADO XYZ"), candidates: [tx(23, "PIX MARIA")] };
for (const [nome, msg, agir] of [
  ["desfazer", "Conferência desfeita.", p => p.getByRole("button", { name: "Desfazer a conferência" }).click()],
  ["trocar", "Conferência registrada.", async p => {
    await p.locator("#bank-movement-301").selectOption("23");
    await p.getByRole("button", { name: "Trocar a transação" }).click();
  }],
]) {
  test(`${nome} pelo modal aberto no detalhe: POST, Histórico recarrega com o estado novo e o dashboard recebe a mensagem`, async () => {
    const { page, posts, listGets } = await pageFor(1365, { movements: [casada], depois: [MERCADO_V2, ITENS[1]] });
    try {
      await page.locator(".tx-row", { hasText: "Mercado" }).click();
      await page.locator("#ld-bank").click();
      await agir(page);
      await page.locator("#generic-confirm-ok").click();
      await until(() => posts.length === 1);
      await page.locator(".tx-row", { hasText: "Mercado v2" }).waitFor();
      assert.equal(listGets.length, 1);  // o Histórico foi pedido de novo, uma vez
      await page.waitForFunction(m => document.getElementById("toast").textContent.includes(m) && document.getElementById("toast").classList.contains("show"), msg);
    } finally { await page.close(); }
  });
}

test("apagar pelo modal aberto no detalhe volta ao Histórico, sem a linha apagada", async () => {
  const { page, deletes, listGets } = await pageFor(1365, { movements: [ID301], depois: [ITENS[1]] });
  try {
    await page.evaluate(() => { lastData = { recent_launches: [] }; });  // o que uma sessão com o mês carregado já tem
    await page.locator(".tx-row", { hasText: "Mercado" }).click();
    await page.locator("#ld-bank").click();
    await page.getByRole("button", { name: "Apagar" }).click();
    await page.locator("#generic-confirm-ok").click();
    await until(() => deletes.length === 1 && listGets.length === 1);
    await page.waitForFunction(() => !document.querySelector("#history-timeline").textContent.includes("Mercado"));
    assert.equal(await page.locator(".tx-row", { hasText: "Padaria" }).count(), 1);
  } finally { await page.close(); }
});
