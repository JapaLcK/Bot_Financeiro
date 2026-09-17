/**
 * Reconciliação OF x lançamento manual: a tela (frontend/reconciliations.js)
 * mais os três pontos que ela liga no dashboard.js — cartão de saldo (porta de
 * entrada), selo "Unido ao extrato" na timeline com "Desfazer" no DETALHE
 * (decisão do dono — não na linha), e a recusa de "Pagar fatura" que passou a
 * vir do servidor (frontend/routes/cards.py), sem checagem de saldo no cliente.
 *
 * `Reconciliations.aviso` espelha core/services/funding.py::aviso_conferir —
 * o teste que lê tests/fixtures/aviso_conferir.json é o controle de que os
 * dois lados (pytest e node) concordam (CLAUDE.md §0.7).
 *
 * Rodar: npm run test:frontend (ou só este arquivo com node --test).
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { chromium } from "playwright";
import { startServer } from "./_server.mjs";

const FIXTURE = JSON.parse(readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), "..", "fixtures", "aviso_conferir.json"), "utf8"));

let browser, server, origin;
before(async () => { ({ proc: server, origin } = await startServer()); browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

const json = (body, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(body) });

const pendingRow = {
  of_tx_id: 501, status: "pending",
  bank: { description: "Compra <script>document.title='x'</script>", amount: -50, date: "2026-09-10", institution: "Nubank" },
  launch: { id: 9001, tipo: "despesa", valor: 50, alvo: "Mercado <script>y</script>", nota: null, date: "2026-09-10" },
};
const siblingRow = {
  of_tx_id: 502, status: "pending",
  bank: { description: "Farmácia", amount: -30, date: "2026-09-11", institution: "Nubank" },
  launch: { id: 9002, tipo: "despesa", valor: 30, alvo: "Farmácia", nota: null, date: "2026-09-11" },
};

// `rows` é mutável: a ação padrão (sucesso) remove a linha da lista, como o
// servidor faria — prova que o reload é uma busca nova, não remendo local.
async function pageFor(width, initialRows, { actionHandler, billPay } = {}) {
  const page = await browser.newPage({ viewport: { width, height: 900 } });
  const rows = initialRows.slice();
  const posts = [];
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.origin !== origin) return route.abort();
    if (/\.[a-z0-9]+$/i.test(url.pathname)) return route.continue();
    if (url.pathname === "/auth/validate") return route.fulfill(json({ user_id: 1 }));
    const m = url.pathname.match(/^\/open-finance\/1\/reconciliations\/(\d+)\/(confirm|reject|undo)$/);
    if (m && route.request().method() === "POST") {
      const ofTxId = Number(m[1]), action = m[2];
      posts.push({ ofTxId, action, csrf: route.request().headers()["x-csrf-token"] });
      // `actionHandler` pode devolver uma Promise (ex.: segurar a resposta pra
      // simular um POST em voo) — await aqui é no-op pra quem já devolve objeto.
      const outcome = actionHandler ? await actionHandler(ofTxId, action) : null;
      if (outcome) return route.fulfill(json(outcome.body, outcome.status));
      const idx = rows.findIndex(r => r.of_tx_id === ofTxId);
      if (idx >= 0) rows.splice(idx, 1);
      return route.fulfill(json({ ok: true, changed: true }));
    }
    if (url.pathname === "/open-finance/1/reconciliations") return route.fulfill(json({ reconciliations: rows }));
    if (url.pathname === "/bills/1/5/pay" && route.request().method() === "POST") {
      posts.push({ bill: true, body: route.request().postDataJSON() });
      const outcome = billPay ? billPay() : { status: 400, body: { detail: "Saldo insuficiente." } };
      return route.fulfill(json(outcome.body, outcome.status));
    }
    return route.fulfill(json({}));
  });
  await page.addInitScript(() => { document.cookie = "csrf_token=tok123"; });
  await page.goto(`${origin}/dashboard.html`);
  await page.waitForFunction(() => Boolean(window.Reconciliations) && USER_ID === 1);
  return { page, posts, rows };
}

for (const width of [1365, 390]) {
  test(`reconciliação: leitura, escape e teclado em ${width}px`, async () => {
    const { page } = await pageFor(width, [pendingRow, siblingRow]);
    try {
      await page.evaluate(() => window.Reconciliations.open(1));
      await page.getByText("Farmácia", { exact: false }).first().waitFor();
      // Achado do Tester: `money()` (Intl, "-R$ 50,00") e `fmtBRL()` (sinal
      // depois do "R$ ", "R$ -50,00") coexistiam na mesma tela — a linha do
      // banco usava o Intl. Um formatador só (o valor da linha PigBank usa
      // um prefixo de sinal próprio p/ receita/despesa, fora de escopo aqui).
      const linhaBanco = await page.getByText("Banco:", { exact: false }).first().innerText();
      assert.ok(linhaBanco.includes("R$ -50,00"), `formato do fmtBRL não apareceu: ${linhaBanco}`);
      assert.ok(!linhaBanco.includes("-R$"), `formato antigo do Intl (sinal antes do "R$") ainda aparece: ${linhaBanco}`);
      assert.equal(await page.locator("#reconciliations-overlay script").count(), 0);
      const bounds = await page.locator("#reconciliations-overlay .modal").boundingBox();
      assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= width);
      for (let i = 0; i < 8; i++) {
        await page.keyboard.press("Tab");
        assert.ok(await page.evaluate(() => document.getElementById("reconciliations-overlay").contains(document.activeElement)));
      }
      await page.keyboard.press("Escape");
      assert.equal(await page.locator("#reconciliations-overlay").count(), 0);
    } finally { await page.close(); }
  });
}

test("confirmar é direto, manda X-CSRF-Token e a lista recarregada não traz mais a confirmada", async () => {
  const { page, posts } = await pageFor(800, [pendingRow, siblingRow]);
  try {
    await page.evaluate(() => window.Reconciliations.open(1));
    await page.getByText("Farmácia", { exact: false }).first().waitFor();
    await page.getByRole("button", { name: "É o mesmo gasto" }).first().click();
    // Espera o reload TERMINAR (não só o "Compra" sumir) — enquanto `load()`
    // troca o conteúdo por "Carregando…" a irmã também está ausente por um
    // instante, e checar só a ausência pega esse instante por engano.
    await page.waitForFunction(() => {
      const txt = document.getElementById("reconciliations-overlay").textContent;
      return txt.includes("Farmácia") && !txt.includes("Compra");
    });
    assert.deepEqual(posts, [{ ofTxId: 501, action: "confirm", csrf: "tok123" }]);
  } finally { await page.close(); }
});

test("rejeitar só sai depois do confirmModal; cancelar não envia nada", async () => {
  const { page, posts } = await pageFor(800, [pendingRow]);
  try {
    await page.evaluate(() => window.Reconciliations.open(1));
    await page.locator("#reconciliations-overlay").getByText("Mercado", { exact: false }).first().waitFor();
    await page.getByRole("button", { name: "São gastos diferentes" }).click();
    await page.getByText("não volta a ser sugerido", { exact: false }).waitFor();
    assert.equal(posts.length, 0);
    await page.locator("#generic-confirm-cancel").click();
    assert.equal(posts.length, 0);
    await page.getByRole("button", { name: "São gastos diferentes" }).click();
    await page.locator("#generic-confirm-ok").click();
    await page.waitForFunction(() => document.getElementById("reconciliations-overlay").textContent.includes("Nada pendente"));
    assert.equal(posts.length, 1);
    assert.equal(posts[0].action, "reject");
  } finally { await page.close(); }
});

test("409 mostra o detail do servidor e recarrega; 404 só recarrega, sem alerta", async () => {
  const { page, posts } = await pageFor(800, [pendingRow], {
    actionHandler: () => ({ status: 409, body: { detail: "Não foi possível concluir. Atualize a lista e confira novamente." } }),
  });
  try {
    await page.evaluate(() => window.Reconciliations.open(1));
    await page.locator("#reconciliations-overlay").getByText("Mercado", { exact: false }).first().waitFor();
    await page.getByRole("button", { name: "É o mesmo gasto" }).click();
    await page.getByText("Não foi possível concluir. Atualize a lista", { exact: false }).waitFor();
    await page.locator("#generic-confirm-ok").click();
    assert.equal(posts.length, 1);
  } finally { await page.close(); }
});

test("Reconciliations.aviso espelha a fixture inteira (pytest e node leem o mesmo arquivo)", async () => {
  const { page } = await pageFor(800, []);
  try {
    for (const caso of FIXTURE) {
      const got = await page.evaluate(
        c => window.Reconciliations.aviso(c.exibido, { pending_count: c.pending_count, delta_se_confirmar: c.delta_se_confirmar }),
        caso);
      const esperado = caso.pending_count > 0
        ? `⚠ ${caso.pending_count} lançamento(s) a conferir` + (caso.esperado_pode_ser ? ` · pode ser ${caso.esperado_pode_ser}` : "")
        : "";
      assert.equal(got, esperado, caso.nome);
    }
  } finally { await page.close(); }
});

test("cartão de saldo mostra o aviso da fixture e abre a conferência; sem pendência não aparece", async () => {
  const { page } = await pageFor(1365, [pendingRow]);
  try {
    const caso = FIXTURE.find(f => f.nome === "despesa_fica_maior");
    await page.evaluate(c => {
      window.Reconciliations.close();
      render({ user_id: 1, year: 2026, month: 9, is_current_month: true, balance: c.exibido,
        of_bank_count: 0, of_bank_balance: 0, pockets: [], investments: [], credit_cards: [],
        bank_movements: { pending_count: 0 },
        reconciliation: { pending_count: c.pending_count, delta_se_confirmar: c.delta_se_confirmar } });
    }, caso);
    const esperado = `⚠ ${caso.pending_count} lançamento(s) a conferir · pode ser ${caso.esperado_pode_ser}`;
    const btn = page.getByRole("button", { name: esperado, exact: true });
    await btn.waitFor();
    const requested = page.waitForRequest(r => new URL(r.url()).pathname === "/open-finance/1/reconciliations");
    await btn.click();
    await requested;
    await page.getByRole("dialog", { name: "Conferência com o extrato" }).waitFor();

    await page.evaluate(() => {
      window.Reconciliations.close();
      render({ user_id: 1, year: 2026, month: 9, is_current_month: true, balance: 300,
        of_bank_count: 0, of_bank_balance: 0, pockets: [], investments: [], credit_cards: [],
        bank_movements: { pending_count: 0 }, reconciliation: { pending_count: 0, delta_se_confirmar: 0 } });
    });
    assert.equal(await page.getByText("lançamento(s) a conferir", { exact: false }).count(), 0);
  } finally { await page.close(); }
});

test("SÉRIO (achado do Tester): render() sem window.Reconciliations não estoura e os cartões do overview aparecem", async () => {
  // /reconciliations.js é arquivo novo — sem cache prévio, um 503 do
  // service-worker no fallback ou um 404 logo após deploy deixam
  // `window.Reconciliations` indefinido. `render()` não pode confiar que ele
  // existe (mesma guarda que já existe em frontend/home.html:1001).
  const { page } = await pageFor(1365, []);
  try {
    await page.evaluate(() => { delete window.Reconciliations; });
    await page.evaluate(() => {
      render({ user_id: 1, year: 2026, month: 9, is_current_month: true, balance: 300,
        of_bank_count: 0, of_bank_balance: 0,
        pockets: [{ name: "Viagem", balance: 100 }], investments: [], credit_cards: [],
        bank_movements: { pending_count: 0 },
        reconciliation: { pending_count: 2, delta_se_confirmar: 5 } });
    });
    // Sem a guarda, a linha acima já teria lançado ReferenceError e o teste
    // falharia aqui — chegar até este ponto já é metade da prova.
    assert.equal(await page.locator(".ov-pk", { hasText: "Viagem" }).count(), 1,
      "cartão da caixinha sumiu: render() parou no meio por causa do aviso de reconciliação");
    assert.equal(await page.getByText("lançamento(s) a conferir", { exact: false }).count(), 0,
      "sem window.Reconciliations não dá pra montar o aviso clicável — não deveria aparecer nenhum");
  } finally { await page.close(); }
});

test("timeline: selo 'Unido ao extrato' e Desfazer no detalhe; sem id nenhum dos dois", async () => {
  const { page, posts } = await pageFor(800, []);
  try {
    await page.evaluate(() => {
      // ".dash-view" só fica visível com ".active" (dashboard.css) — sem isto
      // o clique na linha nunca chega, porque o elemento está fora da tela.
      document.getElementById("history-view").classList.add("active");
      renderHistoryTimeline({ ok: true, items: [
        { id: 301, tipo: "despesa", valor: 50, alvo: "Mercado", nota: null, criado_em: "2026-09-10T10:00:00", reconciliation_of_tx_id: 501 },
        { id: 302, tipo: "despesa", valor: 30, alvo: "Padaria", nota: null, criado_em: "2026-09-10T09:00:00", reconciliation_of_tx_id: null },
      ] }, false);
    });
    const rowWith = page.locator(".tx-row", { hasText: "Mercado" });
    const rowWithout = page.locator(".tx-row", { hasText: "Padaria" });
    assert.ok((await rowWith.innerText()).includes("Unido ao extrato"));
    assert.ok(!(await rowWithout.innerText()).includes("Unido ao extrato"));

    await rowWithout.click();
    await page.getByText("Detalhe do lançamento", { exact: false }).waitFor();
    assert.equal(await page.locator("#ld-undo").isVisible(), false);
    await page.locator("#ld-close").click();

    await rowWith.click();
    await page.getByText("Detalhe do lançamento", { exact: false }).waitFor();
    assert.equal(await page.locator("#ld-undo").isVisible(), true);
    await page.locator("#ld-undo").click();
    await page.getByText("Ao desfazer", { exact: false }).waitFor();
    // waitForResponse (não Request): o fechamento do detalhe só acontece depois
    // do `await act(...)` resolver — esperar só o envio da requisição corre
    // com o `closeLaunchDetail()` que vem em seguida.
    const resp = page.waitForResponse(r => new URL(r.url()).pathname === "/open-finance/1/reconciliations/501/undo");
    await page.locator("#generic-confirm-ok").click();
    await resp;
    await page.waitForFunction(() => document.getElementById("launch-detail-overlay").classList.contains("open") === false);
    assert.deepEqual(posts, [{ ofTxId: 501, action: "undo", csrf: "tok123" }]);
  } finally { await page.close(); }
});

test("Desfazer: 2º clique em #ld-undo durante o POST em voo não reabre a confirmação (achado do Tester)", async () => {
  let releaseUndo;
  const undoHeld = new Promise(r => { releaseUndo = r; });
  const { page, posts } = await pageFor(800, [], {
    actionHandler: (_id, action) => action === "undo"
      ? undoHeld.then(() => ({ body: { ok: true, changed: true }, status: 200 }))
      : null,
  });
  try {
    await page.evaluate(() => {
      document.getElementById("history-view").classList.add("active");
      renderHistoryTimeline({ ok: true, items: [
        { id: 301, tipo: "despesa", valor: 50, alvo: "Mercado", nota: null, criado_em: "2026-09-10T10:00:00", reconciliation_of_tx_id: 501 },
      ] }, false);
    });
    await page.locator(".tx-row", { hasText: "Mercado" }).click();
    await page.getByText("Detalhe do lançamento", { exact: false }).waitFor();
    await page.locator("#ld-undo").click();
    await page.getByText("Ao desfazer", { exact: false }).waitFor();
    const firstReq = page.waitForRequest(r => new URL(r.url()).pathname === "/open-finance/1/reconciliations/501/undo");
    await page.locator("#generic-confirm-ok").click();
    await firstReq;
    // 1º POST em voo (segurado por `undoHeld`): o modal de detalhe segue
    // aberto com #ld-undo visível — é a janela do clique duplo do achado.
    // .click() nativo (não o do Playwright) pra não esperar "actionable":
    // um botão desabilitado é justamente o que este teste verifica.
    await page.evaluate(() => document.getElementById("ld-undo").click());
    assert.equal(
      await page.locator("#generic-confirm-overlay.open").count(), 0,
      "2º clique em #ld-undo reabriu a confirmação — sem o `btn.disabled`, isso manda um 2º POST de undo");
    releaseUndo();
    await page.waitForFunction(() => document.getElementById("launch-detail-overlay").classList.contains("open") === false);
    assert.deepEqual(posts, [{ ofTxId: 501, action: "undo", csrf: "tok123" }]);
  } finally { await page.close(); }
});

test("pagar fatura: sem checagem de saldo no cliente — o POST sempre sai e a recusa é a do servidor", async () => {
  const { page, posts } = await pageFor(800, [], {
    billPay: () => ({ status: 400, body: { detail: "Saldo insuficiente: você tem R$ 40,00 na conta e o pagamento é de R$ 100,00." } }),
  });
  try {
    await page.evaluate(() => {
      // ".overlay" só fica visível com ".open" (dashboard.css), e o formulário
      // com o valor/erro só aparece quando openPayBillModal escolhe uma fatura
      // — aqui pulamos essa etapa de propósito, então os dois têm que ser
      // destravados à mão, senão o waitFor do erro nunca resolve.
      document.getElementById("pay-bill-overlay").classList.add("open");
      document.getElementById("pay-bill-form").style.display = "";
      payBillState = { balance: 40, bills: [{ id: 5, due_amount: 100, period_end: "2020-01-01" }], selectedId: 5, submitting: false };
      document.getElementById("pay-bill-amount").value = "100";
    });
    await page.evaluate(() => submitPayBill());
    await page.getByText("Saldo insuficiente: você tem R$ 40,00", { exact: false }).waitFor();
    // Controle: com a checagem antiga (removida em frontend/dashboard.js perto
    // da linha 9851), este clique nunca chegava ao fetch — 0 POST.
    assert.equal(posts.filter(p => p.bill).length, 1);
  } finally { await page.close(); }
});
