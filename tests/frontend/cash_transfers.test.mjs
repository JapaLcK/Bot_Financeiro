/**
 * Q41 PR B, /app: a linha "Dinheiro vivo" da faixa de avisos (dashboard.js,
 * `renderAlerts`) e o modal (frontend/cash-transfers.js). Página REAL
 * (dashboard.html) com `/data/1` e as rotas de /open-finance/1/cash-transfers
 * falsas, a 1280 e a 390 px.
 *
 * Controles NEGATIVOS (medidos, ver o relato do PR):
 *   - sem a guarda `window.CashTransfers` em `renderAlerts`, o caso do 404 fica
 *     vermelho (a linha aparece com um botão que não abre nada);
 *   - sem o ramo `cash_transfers`, a linha cai no `else` do orçamento e o caso
 *     da faixa fica vermelho.
 *
 * Rodar: node --test tests/frontend/cash_transfers.test.mjs
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { startServer } from "./_server.mjs";

let browser, server, origin;
before(async () => { ({ proc: server, origin } = await startServer()); browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

const json = (body, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(body) });
const CASH = (count) => ({ type: "cash_transfers", count });
const BUDGET = { type: "budget_warning", categoria: "mercado", spent: 90, budget: 100, pct: 90 };
const DATA = (alerts) => ({
  user_id: 1, year: 2026, month: 9, is_current_month: true, balance: 300, of_bank_count: 0,
  of_bank_balance: 0, pockets: [{ name: "Viagem", balance: 100 }], investments: [], credit_cards: [],
  bank_movements: { pending_count: 0 }, reconciliation: { pending_count: 0, delta_se_confirmar: 0 }, alerts,
});
const item = (o) => ({ id: 1, kind: "saque", status: "ativo", amount: 200, tx_date: "2026-03-10",
  institution: "Nubank", manual_alvo: null, manual_valor: null, manual_date: null, ...o });

// `state` é mutável: a ação padrão tira o item da lista e baixa o contador do
// /data, como o servidor faria — prova que o reload é busca nova.
async function pageFor(width, { items = [], alerts = [CASH(1)], actionHandler, js404 = false } = {}) {
  const page = await browser.newPage({ viewport: { width, height: 900 } });
  const state = { items: items.slice(), alerts, posts: [], lists: 0, errs: [] };
  page.on("pageerror", (e) => state.errs.push(String(e)));
  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== origin) return route.abort();
    if (js404 && url.pathname === "/cash-transfers.js") return route.fulfill({ status: 404, body: "nao existe" });
    if (/\.[a-z0-9]+$/i.test(url.pathname)) return route.continue();
    if (url.pathname === "/auth/validate") return route.fulfill(json({ user_id: 1 }));
    if (url.pathname === "/data/1") return route.fulfill(json(DATA(state.alerts)));
    const m = url.pathname.match(/^\/open-finance\/1\/cash-transfers\/(\d+)\/(\w+)$/);
    if (m && route.request().method() === "POST") {
      const id = Number(m[1]), action = m[2];
      state.posts.push({ id, action, csrf: route.request().headers()["x-csrf-token"] });
      const outcome = actionHandler ? await actionHandler(id, action) : null;
      if (outcome) return route.fulfill(json(outcome.body, outcome.status));
      state.items = state.items.filter((i) => i.id !== id);
      state.alerts = state.items.length ? [CASH(state.items.length)] : [];
      return route.fulfill(json({ ok: true, changed: true }));
    }
    if (url.pathname === "/open-finance/1/cash-transfers") {
      state.lists += 1;
      return route.fulfill(json({ ok: true, items: state.items }));
    }
    return route.fulfill(json({}));
  });
  await page.addInitScript(() => { document.cookie = "csrf_token=tok123"; });
  await page.goto(`${origin}/dashboard.html`);
  await page.waitForFunction(() => typeof USER_ID !== "undefined" && USER_ID === 1);
  // O servidor estático não fala WebSocket: o 1º render é à mão; depois de uma
  // ação o `refreshDashboardAfterInvestment` cai no /data/1 (fallback HTTP).
  await page.evaluate((d) => render(d), DATA(state.alerts));
  // O menu lateral abre no :hover e cobre o conteúdo (ver reconciliations.test.mjs).
  await page.mouse.move(0, 0);
  await page.mouse.move(width - 20, 450);
  return { page, state };
}

const faixa = (page) => page.locator("#alert-banner");
const faixaTexto = async (page) => (await faixa(page).textContent()).replace(/\s+/g, " ").trim();
const modal = (page) => page.locator("#cash-transfers-overlay .modal");

for (const width of [1280, 390]) {
  test(`${width}px: a faixa mostra o contador e o Conferir abre o modal; cabe na tela`, async () => {
    const { page, state } = await pageFor(width, { items: [item({})], alerts: [CASH(3), BUDGET] });
    try {
      await faixa(page).getByText("Dinheiro vivo").waitFor();
      assert.match(await faixaTexto(page), /Dinheiro vivo · 3 para conferir · Conferir/);
      assert.match(await faixaTexto(page), /mercado/, "o orçamento sumiu da faixa");
      const bf = await faixa(page).boundingBox();
      assert.ok(bf.x >= 0 && bf.x + bf.width <= width, `faixa transborda: ${JSON.stringify(bf)}`);
      await faixa(page).getByRole("button", { name: "Conferir" }).click();
      await modal(page).getByText("Somamos R$ 200,00 na sua Carteira.").waitFor();
      assert.equal(await page.locator("#cash-transfers-title").textContent(), "Dinheiro vivo");
      const b = await modal(page).boundingBox();
      assert.ok(b.x >= 0 && b.x + b.width <= width, `modal transborda em ${width}: ${JSON.stringify(b)}`);
      for (const btn of await modal(page).locator("button").all()) {
        const bb = await btn.boundingBox();
        assert.ok(bb.x >= 0 && bb.x + bb.width <= width, `botão fora da tela: ${await btn.textContent()}`);
      }
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      assert.deepEqual(state.errs, []);
    } finally { await page.close(); }
  });
}

test("contador 0 / sem o item: nenhuma linha; só o orçamento aparece", async () => {
  const { page, state } = await pageFor(1280, { alerts: [BUDGET] });
  try {
    await faixa(page).getByText("mercado").waitFor();
    assert.doesNotMatch(await faixaTexto(page), /Dinheiro vivo/);
    await page.evaluate(() => renderAlerts([]));
    assert.equal(await faixa(page).isVisible(), false);
    assert.deepEqual(state.errs, []);
  } finally { await page.close(); }
});

for (const [nome, alerts] of [["com orçamento", [CASH(2), BUDGET]], ["sozinho", [CASH(2)]]]) {
  test(`/cash-transfers.js em 404 (${nome}): sem a linha, overview inteiro e sem erro`, async () => {
    const { page, state } = await pageFor(1280, { alerts, js404: true });
    try {
      await page.locator(".ov-pk", { hasText: "Viagem" }).waitFor();
      assert.equal(await page.evaluate(() => Boolean(window.CashTransfers)), false);
      const t = await faixa(page).isVisible() ? await faixaTexto(page) : "";
      assert.doesNotMatch(t, /Dinheiro vivo/, "linha sem o script: o Conferir não abriria nada");
      assert.equal(/mercado/.test(t), alerts.length > 1);
      assert.deepEqual(state.errs, []);
    } finally { await page.close(); }
  });
}

test("o X da faixa não faz POST (não marca nada como visto)", async () => {
  const { page, state } = await pageFor(1280, { items: [item({})], alerts: [CASH(1)] });
  try {
    await faixa(page).getByText("Dinheiro vivo").waitFor();
    await page.locator("#alert-banner .alert-close").click();
    await page.waitForTimeout(300);
    assert.deepEqual(state.posts, []);
  } finally { await page.close(); }
});

// [item, frase, [[botão, ação], ...]] — a tabela do plano (+ "Não era dinheiro
// vivo" em toda pergunta de depósito e Pix Saque, decisão do dono).
const NAO = ["Não era dinheiro vivo", "not_cash"];
const TABELA = [
  [item({ id: 1 }), "Somamos R$ 200,00 na sua Carteira.", [["Ok", "seen"], ["Desfazer", "undo"]]],
  [item({ id: 2, kind: "deposito", amount: 300 }), "Tiramos R$ 300,00 da sua Carteira.", [["Ok", "seen"], ["Desfazer", "undo"]]],
  [item({ id: 3, kind: "deposito", status: "perguntar_fraco" }), "Foi dinheiro vivo que você depositou?", [["Sim, tira da Carteira", "cash"], NAO]],
  [item({ id: 4, kind: "fraco", status: "perguntar_fraco" }), "Você sacou dinheiro vivo com esse Pix?", [["Sim, soma na Carteira", "cash"], NAO]],
  [item({ id: 5, status: "perguntar_novo" }), "Você já anotou esse dinheiro na Carteira?", [["Já anotei", "already"], ["Não, soma na Carteira", "credit"]]],
  [item({ id: 6, kind: "deposito", status: "perguntar_novo" }), "Você já tirou esse dinheiro da Carteira?", [["Já tirei", "already"], ["Não, tira da Carteira", "credit"], NAO]],
  [item({ id: 7, kind: "fraco", status: "perguntar_novo" }), "Você já anotou esse dinheiro na Carteira?", [["Já anotei", "already"], ["Não, soma na Carteira", "credit"], NAO]],
  [item({ id: 8, status: "perguntar_manual", manual_alvo: "meu pai", manual_valor: 200, manual_date: "2026-03-09" }),
    "Você anotou “meu pai”, R$ 200,00 em 09/03. É o mesmo dinheiro?", [["É o mesmo", "same"], ["São diferentes, soma na Carteira", "different"]]],
  [item({ id: 9, kind: "deposito", status: "perguntar_manual", manual_alvo: "banco", manual_valor: 200, manual_date: "2026-03-09" }),
    "Você anotou “banco”, R$ 200,00 em 09/03. É o mesmo dinheiro?", [["É o mesmo", "same"], ["São diferentes, tira da Carteira", "different"], NAO]],
  // Manual apagado antes do sync: sem "É o mesmo" (daria 409) e sem "“—”, R$ 0,00 em ."
  [item({ id: 10, status: "perguntar_manual" }), "O lançamento que você anotou foi apagado.", [["Soma na Carteira", "different"]]],
  [item({ id: 11, kind: "deposito", status: "perguntar_manual" }), "O lançamento que você anotou foi apagado.", [["Tira da Carteira", "different"], NAO]],
  [item({ id: 12, kind: "fraco", amount: 250 }), "Somamos R$ 250,00 na sua Carteira.", [["Ok", "seen"], ["Desfazer", "undo"]]],
];
// A confirmação do Desfazer, por tipo (frase inteira).
const DESFAZ = {
  saque: "A Carteira volta a como estava. O saque continua fora dos seus gastos.",
  fraco: "A Carteira volta a como estava. O saque continua fora dos seus gastos.",
  deposito: "A Carteira volta a como estava. O depósito continua fora das suas receitas.",
};

test("cada estado × tipo mostra a frase e os botões da tabela, e cada botão manda a sua ação", async () => {
  const keep = () => ({ status: 200, body: { ok: true, changed: true } });
  const { page, state } = await pageFor(1280, { items: TABELA.map((t) => t[0]), actionHandler: keep });
  try {
    await page.evaluate(() => window.CashTransfers.open(1));
    await modal(page).getByText("Somamos R$ 200,00").waitFor();
    const linhas = modal(page).locator(".modal-row");
    assert.equal(await linhas.count(), TABELA.length);
    assert.equal(await linhas.nth(0).locator("p").first().textContent(), "Saque de R$ 200,00 · Nubank · 10/03");
    assert.equal(await linhas.nth(3).locator("p").first().textContent(), "Pix Saque de R$ 200,00 · Nubank · 10/03");
    for (const [i, [it, frase, botoes]] of TABELA.entries()) {
      const linha = () => modal(page).locator(".modal-row").nth(i);
      assert.equal(await linha().locator("p").nth(1).textContent(), frase, `item ${it.id}`);
      assert.deepEqual(await linha().locator("button").allTextContents(), botoes.map((b) => b[0]), `item ${it.id}`);
      for (const [rotulo, acao] of botoes) {
        const n = state.posts.length;
        await linha().getByRole("button", { name: rotulo, exact: true }).click();
        if (acao === "undo") {
          assert.equal(await page.locator("#generic-confirm-body").textContent(), DESFAZ[it.kind], `${it.id} Desfazer`);
          await page.locator("#generic-confirm-ok").click();
        }
        await waitFor(() => state.posts.length > n);
        assert.deepEqual(state.posts.at(-1), { id: it.id, action: acao, csrf: "tok123" }, `${it.id} ${rotulo}`);
        await waitFor(() => page.evaluate(() => !document.querySelector("#cash-transfers-overlay button:disabled")));
        assert.equal(await page.locator("#generic-confirm-overlay.open").count(), 0, `aviso no clique normal: ${it.id} ${rotulo}`);
      }
    }
    assert.deepEqual(state.errs, []);
  } finally { await page.close(); }
});

async function waitFor(fn, ms = 5000) {
  const fim = Date.now() + ms;
  while (Date.now() < fim) { if (await fn()) return; await new Promise((r) => setTimeout(r, 25)); }
  throw new Error("timeout");
}

test("XSS: banco e lançamento anotado entram como texto", async () => {
  const mau = item({ id: 1, status: "perguntar_manual", institution: "<img src=x onerror=\"window.__xss=1\">",
    manual_alvo: "<script>window.__xss=2</script>", manual_valor: 200, manual_date: "2026-03-09" });
  const { page } = await pageFor(390, { items: [mau] });
  try {
    await page.evaluate(() => window.CashTransfers.open(1));
    await modal(page).getByText("<img src=x", { exact: false }).waitFor();
    assert.equal(await modal(page).locator("img, script").count(), 0);
    assert.match(await modal(page).textContent(), /“<script>window.__xss=2<\/script>”/);
    assert.equal(await page.evaluate(() => window.__xss), undefined);
  } finally { await page.close(); }
});

test("os botões da linha travam juntos durante o POST", async () => {
  let solta;
  const segura = new Promise((r) => { solta = r; });
  const { page } = await pageFor(1280, {
    items: [item({ id: 6, kind: "deposito", status: "perguntar_novo" })],
    actionHandler: () => segura.then(() => null),
  });
  try {
    await page.evaluate(() => window.CashTransfers.open(1));
    await modal(page).getByRole("button", { name: "Já tirei" }).click();
    const travados = await modal(page).locator(".modal-row button").evaluateAll((bs) => bs.map((b) => b.disabled));
    assert.deepEqual(travados, [true, true, true]);
    solta();
    await modal(page).getByText("Nada para conferir.").waitFor();
  } finally { await page.close(); }
});

test("depois da ação: lista nova e o /data novo baixa o contador da faixa para 1", async () => {
  const { page, state } = await pageFor(1280, { items: [item({ id: 1 }), item({ id: 2, status: "perguntar_novo" })], alerts: [CASH(2)] });
  try {
    await faixa(page).getByText("2", { exact: true }).waitFor();
    await faixa(page).getByRole("button", { name: "Conferir" }).click();
    const listas = await waitFor(() => state.lists === 1).then(() => state.lists);
    await modal(page).getByRole("button", { name: "Ok", exact: true }).click();
    await waitFor(() => state.lists > listas);
    assert.equal(await modal(page).locator(".modal-row").count(), 1);
    await waitFor(async () => /Dinheiro vivo · 1 para conferir/.test(await faixaTexto(page)));
    assert.deepEqual(state.errs, []);
  } finally { await page.close(); }
});

test("409: mostra a mensagem do servidor e recarrega a lista", async () => {
  const msg = "O lançamento que você anotou mudou e não bate mais com este. Se forem diferentes, toque em “São diferentes”.";
  const { page, state } = await pageFor(1280, {
    items: [item({ id: 8, status: "perguntar_manual", manual_alvo: "meu pai", manual_valor: 200, manual_date: "2026-03-09" })],
    actionHandler: () => ({ status: 409, body: { detail: msg } }),
  });
  try {
    await page.evaluate(() => window.CashTransfers.open(1));
    await modal(page).getByRole("button", { name: "É o mesmo" }).click();
    await page.locator("#generic-confirm-body", { hasText: "mudou e não bate" }).waitFor();
    assert.equal(await page.locator("#generic-confirm-body").textContent(), msg);
    await page.locator("#generic-confirm-ok").click();
    await waitFor(() => state.lists === 2);
  } finally { await page.close(); }
});

test("200 changed:false (respondida noutra aba/no WhatsApp): avisa e recarrega", async () => {
  const { page, state } = await pageFor(1280, {
    items: [item({ id: 5, status: "perguntar_novo" })],
    actionHandler: () => { state.items = []; return { status: 200, body: { ok: true, changed: false } }; },
  });
  try {
    await page.evaluate(() => window.CashTransfers.open(1));
    await modal(page).getByRole("button", { name: "Já anotei" }).click();
    await page.locator("#generic-confirm-overlay.open").waitFor();
    assert.equal(await page.locator("#generic-confirm-body").textContent(), "Esse já tinha sido conferido. A lista foi atualizada.");
    await page.locator("#generic-confirm-ok").click();
    await modal(page).getByText("Nada para conferir.").waitFor();
    assert.equal(state.lists, 2);
    assert.deepEqual(state.errs, []);
  } finally { await page.close(); }
});
