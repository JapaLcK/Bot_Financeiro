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
// `fakeWs`: mesmo FakeWS de of_refresh_ui.test.mjs (readyState OPEN desde o
// construtor) — o `http.server` estático desta suíte não fala WebSocket, e
// sem isso `ws` nunca chega a WebSocket.OPEN em teste nenhum (é assim que o
// caminho HTTP de fallback é hoje o único exercitado por acidente).
async function pageFor(width, initialRows, { actionHandler, billPay, fakeWs = false } = {}) {
  const page = await browser.newPage({ viewport: { width, height: 900 } });
  const rows = initialRows.slice();
  const posts = [];
  if (fakeWs) {
    await page.addInitScript(() => {
      window.__sent = [];
      class FakeWS {
        constructor(url) {
          this.url = url; this.readyState = 1; window.__ws = this;
          setTimeout(() => this.onopen && this.onopen(), 0);
        }
        send(data) { window.__sent.push(JSON.parse(data)); }
        close() {}
      }
      FakeWS.OPEN = 1;
      window.WebSocket = FakeWS;
    });
  }
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
      // "abort": simula falha de rede (fetch rejeita, sem status) — diferente
      // de um 4xx/5xx, que chega como resposta.
      if (outcome === "abort") return route.abort();
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
      // Achado do Tester: o teste anterior só olhava "Banco:" — "PigBank:" tinha
      // o sinal fora do fmtBRL (launchAmount montava "-R$ 0,01" à mão) e ficava
      // cego. pendingRow.launch = despesa de 50, mesmo par da linha do banco.
      const linhaPig = await page.getByText("PigBank:", { exact: false }).first().innerText();
      assert.ok(linhaPig.includes("R$ -50,00"), `formato do fmtBRL não apareceu na linha PigBank: ${linhaPig}`);
      assert.ok(!linhaPig.includes("-R$"), `sinal antes do "R$" ainda aparece na linha PigBank: ${linhaPig}`);
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

// Achado do Codex (P2, reconciliations.js:73): só o botão clicado travava —
// com o confirmar em voo, "São gastos diferentes" da MESMA linha seguia
// clicável. Se o confirmar commita primeiro (status vira "confirmed" no
// servidor), reject_reconciliation trata isso como no-op de sucesso
// (db/reconciliation.py::reject_reconciliation) — a UI parecia aceitar a
// rejeição com o par continuando confirmado.
test("confirmar em voo trava 'São gastos diferentes' da mesma linha (P2 Codex)", async () => {
  let releaseConfirm;
  const confirmHeld = new Promise(r => { releaseConfirm = r; });
  const { page, posts, rows } = await pageFor(800, [pendingRow], {
    // Splice manual: com `actionHandler` presente o splice automático do
    // `pageFor` (sucesso remove a linha) não roda — sem isto o `load()` de
    // depois do release recarregaria a MESMA pendência e "Nada pendente"
    // nunca apareceria (contrato de _server.mjs, comentado em `pageFor`).
    actionHandler: (ofTxId, action) => action === "confirm"
      ? confirmHeld.then(() => {
          const idx = rows.findIndex(r => r.of_tx_id === ofTxId);
          if (idx >= 0) rows.splice(idx, 1);
          return { body: { ok: true, changed: true }, status: 200 };
        })
      : null,
  });
  try {
    await page.evaluate(() => window.Reconciliations.open(1));
    await page.locator("#reconciliations-overlay").getByText("Mercado", { exact: false }).first().waitFor();
    const posted = page.waitForRequest(r => r.url().endsWith("/confirm"));
    await page.getByRole("button", { name: "É o mesmo gasto" }).click();
    await posted;
    assert.equal(await page.getByRole("button", { name: "São gastos diferentes" }).isDisabled(), true,
      "'São gastos diferentes' devia travar enquanto o confirm da mesma linha está em voo");
    // .click() nativo (não o do Playwright, que recusa clicar num elemento
    // desabilitado): um botão disabled não dispara onclick — é isso que se
    // verifica. Só há 1 linha (pendingRow), então a classe é única na tela.
    await page.evaluate(() => document.querySelector(".btn-cancel").click());
    assert.equal(await page.locator("#generic-confirm-overlay.open").count(), 0,
      "clique em 'São gastos diferentes' travado abriu a confirmação de rejeitar");
    releaseConfirm();
    await page.waitForFunction(() => document.getElementById("reconciliations-overlay").textContent.includes("Nada pendente"));
    assert.deepEqual(posts, [{ ofTxId: 501, action: "confirm", csrf: "tok123" }],
      "nenhum POST de reject pode ter saído enquanto o confirm estava em voo");
  } finally { await page.close(); }
});

// Controle positivo do teste acima: erro no confirmar reabilita OS DOIS
// botões da linha (não só o clicado) — verificado ANTES do reload de load(),
// que substituiria a linha por botões novos (já nascidos habilitados) e
// mascararia uma falta de reabilitação.
test("confirmar com erro reabilita os dois botões da linha antes do reload (controle positivo, P2 Codex)", async () => {
  const { page } = await pageFor(800, [pendingRow], {
    actionHandler: () => ({ status: 409, body: { detail: "Não foi possível concluir. Atualize a lista e confira novamente." } }),
  });
  try {
    await page.evaluate(() => window.Reconciliations.open(1));
    await page.locator("#reconciliations-overlay").getByText("Mercado", { exact: false }).first().waitFor();
    await page.getByRole("button", { name: "É o mesmo gasto" }).click();
    await page.getByText("Não foi possível concluir. Atualize a lista", { exact: false }).waitFor();
    assert.equal(await page.getByRole("button", { name: "É o mesmo gasto" }).isDisabled(), false);
    assert.equal(await page.getByRole("button", { name: "São gastos diferentes" }).isDisabled(), false,
      "'São gastos diferentes' devia voltar a ficar clicável depois do erro no confirmar");
    await page.locator("#generic-confirm-ok").click();
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

// As 3 respostas de `_launchDetailUndo` (SÉRIO — o `return` do conserto
// anterior deixava 404 mudo e sem reload, e 409/erro sem reload nenhum).
// Mesmo contrato provado pra reconciliations.js::_run no teste "409 mostra
// o detail do servidor... 404 só recarrega, sem alerta", lá em cima.
async function abreDetalheEClicaUndo(page, items = [
  { id: 301, tipo: "despesa", valor: 50, alvo: "Mercado", nota: null, criado_em: "2026-09-10T10:00:00", reconciliation_of_tx_id: 501 },
]) {
  await page.evaluate((its) => {
    document.getElementById("history-view").classList.add("active");
    renderHistoryTimeline({ ok: true, items: its }, false);
  }, items);
  await page.locator(".tx-row", { hasText: "Mercado" }).click();
  await page.getByText("Detalhe do lançamento", { exact: false }).waitFor();
  await page.locator("#ld-undo").click();
  await page.getByText("Ao desfazer", { exact: false }).waitFor();
  await page.locator("#generic-confirm-ok").click();
}

test("Desfazer no detalhe: 404 fecha e recarrega o histórico, sem alerta", async () => {
  const { page, posts } = await pageFor(800, [], {
    actionHandler: () => ({ status: 404, body: { detail: "Não encontrado." } }),
  });
  try {
    await abreDetalheEClicaUndo(page);
    await page.waitForFunction(() => document.getElementById("launch-detail-overlay").classList.contains("open") === false);
    assert.equal(await page.locator("#generic-confirm-overlay.open").count(), 0,
      "404 não pode mostrar alerta — é o par já desfeito noutra aba");
    assert.deepEqual(posts, [{ ofTxId: 501, action: "undo", csrf: "tok123" }]);
  } finally { await page.close(); }
});

test("Desfazer no detalhe: 409 mostra o detail do servidor e ainda assim fecha e recarrega", async () => {
  const { page, posts } = await pageFor(800, [], {
    actionHandler: () => ({ status: 409, body: { detail: "Não foi possível concluir. Atualize a lista e confira novamente." } }),
  });
  try {
    await abreDetalheEClicaUndo(page);
    await page.getByText("Não foi possível concluir. Atualize a lista", { exact: false }).waitFor();
    await page.locator("#generic-confirm-ok").click();
    await page.waitForFunction(() => document.getElementById("launch-detail-overlay").classList.contains("open") === false);
    assert.deepEqual(posts, [{ ofTxId: 501, action: "undo", csrf: "tok123" }]);
  } finally { await page.close(); }
});

test("Desfazer no detalhe: falha de rede também mostra alerta e fecha/recarrega", async () => {
  const { page, posts } = await pageFor(800, [], { actionHandler: () => "abort" });
  try {
    await abreDetalheEClicaUndo(page);
    // `fetch()` rejeita (TypeError sem `.status`) antes de existir `response` —
    // a mensagem é a do navegador ("Failed to fetch"), não a de `act()`; o que
    // importa aqui é o mesmo ramo do 409 (não-404): mostra ALGUM alerta e segue.
    await page.locator("#generic-confirm-overlay.open").waitFor();
    await page.locator("#generic-confirm-ok").click();
    await page.waitForFunction(() => document.getElementById("launch-detail-overlay").classList.contains("open") === false);
    assert.equal(posts.length, 1);
  } finally { await page.close(); }
});

test("Desfazer no detalhe: sucesso reseta o botão pro próximo detalhe aberto (achado do Tester)", async () => {
  // Mutação: apagar `ldUndoBtn.disabled = false;` (dashboard.js, dentro de
  // _renderLaunchDetail) deixa este teste vermelho — o botão do 2º lançamento
  // nasce travado pelo undo do 1º, que nunca reabilitou o próprio botão no
  // caminho de sucesso (só o de cancelar/erro faz `btn.disabled = false`).
  const { page } = await pageFor(800, []);
  try {
    await abreDetalheEClicaUndo(page, [
      { id: 301, tipo: "despesa", valor: 50, alvo: "Mercado", nota: null, criado_em: "2026-09-10T10:00:00", reconciliation_of_tx_id: 501 },
      { id: 302, tipo: "despesa", valor: 30, alvo: "Padaria", nota: null, criado_em: "2026-09-10T09:00:00", reconciliation_of_tx_id: 502 },
    ]);
    await page.waitForFunction(() => document.getElementById("launch-detail-overlay").classList.contains("open") === false);
    // `_historyResetAndReload()` refaz o fetch (não mockado aqui) e substitui o
    // DOM da timeline — `_renderedHistoryItems` sobrevive, então abre pelo
    // índice em vez de clicar a linha, que pode não estar mais desenhada.
    await page.evaluate(() => openHistoryDetail(1));
    await page.waitForFunction(() => document.getElementById("launch-detail-overlay").classList.contains("open"));
    assert.equal(await page.locator("#ld-undo").isDisabled(), false,
      "o botão do 2º detalhe nasceu travado pelo undo do 1º");
  } finally { await page.close(); }
});

// Achado do Codex (P2, dashboard.js:8447): sendRefresh() sozinho só atualiza
// saldo/aviso com WS aberto (_doRefresh só age em WebSocket.OPEN). O
// `http.server` estático desta suíte nunca deixa `ws` chegar a OPEN, então
// este é o estado "de fábrica" de todo teste daqui — sem o fallback
// `fetchMonthHttp` (o mesmo que refreshDashboardAfterInvestment já usa como
// onSave da tela de conferência), o undo bem-sucedido nunca refaz o /data/1.
test("Desfazer no detalhe: sem WebSocket aberto, o undo cai no fallback HTTP do mês (P2 Codex)", async () => {
  const { page } = await pageFor(800, []);
  try {
    const monthReq = page.waitForRequest(r =>
      new URL(r.url()).pathname === "/data/1" && r.method() === "GET");
    await abreDetalheEClicaUndo(page);
    const req = await monthReq;
    assert.equal(new URL(req.url()).pathname, "/data/1");
  } finally { await page.close(); }
});

// Controle positivo do par acima: com WS aberto, o caminho por WebSocket
// continua sendo o usado — nada de fallback HTTP disparando por cima dele.
test("Desfazer no detalhe: com WebSocket aberto, o refresh vai pelo WS (controle positivo)", async () => {
  const { page } = await pageFor(800, [], { fakeWs: true });
  const httpMonthReqs = [];
  page.on("request", r => { if (new URL(r.url()).pathname === "/data/1") httpMonthReqs.push(r.url()); });
  try {
    await page.waitForFunction(() => Boolean(window.__ws));
    const antes = await page.evaluate(() => window.__sent.filter(m => m.type === "get_month").length);
    await abreDetalheEClicaUndo(page);
    await page.waitForFunction(
      (n) => window.__sent.filter(m => m.type === "get_month").length > n, antes);
    assert.deepEqual(httpMonthReqs, [], "com WS aberto não devia cair no fallback HTTP /data/1");
  } finally { await page.close(); }
});

test("Desfazer no detalhe: sem window.Reconciliations o botão some (achado do Tester)", async () => {
  // Mesma categoria da guarda de render() (teste "SÉRIO" acima): sem o script,
  // o botão continuava desenhado e o clique estourava "Reconciliations is not
  // defined". Mutação: tirar `&& window.Reconciliations` de _renderLaunchDetail
  // deixa este teste vermelho.
  const { page } = await pageFor(800, []);
  try {
    await page.evaluate(() => { delete window.Reconciliations; });
    await page.evaluate(() => {
      document.getElementById("history-view").classList.add("active");
      renderHistoryTimeline({ ok: true, items: [
        { id: 301, tipo: "despesa", valor: 50, alvo: "Mercado", nota: null, criado_em: "2026-09-10T10:00:00", reconciliation_of_tx_id: 501 },
      ] }, false);
    });
    await page.locator(".tx-row", { hasText: "Mercado" }).click();
    await page.getByText("Detalhe do lançamento", { exact: false }).waitFor();
    assert.equal(await page.locator("#ld-undo").isVisible(), false,
      "sem window.Reconciliations o botão Desfazer não pode aparecer");
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

// Achado do Tester (P2 Codex, dashboard.js:8440): a continuação de
// _launchDetailUndo fechava (e no erro, alertava) o detalhe INCONDICIONALMENTE.
// Com o POST em voo, o usuário podia fechar o detalhe e abrir OUTRO
// lançamento antes de a resposta voltar — a continuação então mexia no
// detalhe errado. Pior: o alertModal do undo velho sequestrava um
// confirmModal já aberto sobre o novo lançamento (mesmo _genericModalResolver
// pros dois, dashboard.js:3124).
const doisItensLD = [
  { id: 301, tipo: "despesa", valor: 50, alvo: "Mercado", nota: null, criado_em: "2026-09-10T10:00:00", reconciliation_of_tx_id: 501 },
  { id: 302, tipo: "despesa", valor: 30, alvo: "Padaria", nota: null, criado_em: "2026-09-10T09:00:00", reconciliation_of_tx_id: null },
];

async function abreEDisparaUndoLD(page, idx = 0) {
  await page.evaluate((its) => {
    document.getElementById("history-view").classList.add("active");
    renderHistoryTimeline({ ok: true, items: its }, false);
  }, doisItensLD);
  await page.evaluate((i) => openHistoryDetail(i), idx);
  await page.getByText("Detalhe do lançamento", { exact: false }).waitFor();
  await page.locator("#ld-undo").click();
  await page.getByText("Ao desfazer", { exact: false }).waitFor();
  const req = page.waitForRequest(r => new URL(r.url()).pathname === "/open-finance/1/reconciliations/501/undo");
  await page.locator("#generic-confirm-ok").click();
  await req;
}

const fechadoresLD = {
  "botão Fechar": page => page.locator("#ld-close").click(),
  "clique no backdrop": page => page.locator("#launch-detail-overlay").click({ position: { x: 5, y: 5 } }),
  "Esc": page => page.keyboard.press("Escape"),
};

for (const [nome, fechar] of Object.entries(fechadoresLD)) {
  test(`Desfazer: fechar por ${nome} com POST em voo + abrir outro lançamento -> detalhe NOVO continua aberto (P2 Codex)`, async () => {
    let release;
    const held = new Promise(r => { release = r; });
    const { page, posts } = await pageFor(800, [], {
      actionHandler: (_id, a) => a === "undo" ? held.then(() => ({ body: { ok: true, changed: true }, status: 200 })) : null,
    });
    try {
      await abreEDisparaUndoLD(page, 0);
      await fechar(page);
      await page.waitForFunction(() => !document.getElementById("launch-detail-overlay").classList.contains("open"));
      await page.evaluate(() => openHistoryDetail(1));
      await page.getByText("Padaria", { exact: false }).first().waitFor();
      release();
      await page.waitForTimeout(300);
      assert.equal(
        await page.locator("#launch-detail-overlay").evaluate(el => el.classList.contains("open")), true,
        "o detalhe NOVO (Padaria) foi fechado pela continuação do undo do lançamento antigo");
      assert.equal(await page.locator("#ld-desc").innerText(), "Padaria");
      assert.equal(posts.length, 1);
    } finally { await page.close(); }
  });

  test(`Desfazer: fechar por ${nome} com erro (409) + abrir outro lançamento -> sem alerta sobre o detalhe novo (P2 Codex)`, async () => {
    let release;
    const held = new Promise(r => { release = r; });
    const { page } = await pageFor(800, [], {
      actionHandler: () => held.then(() => ({ body: { detail: "Par 501 já foi desfeito." }, status: 409 })),
    });
    try {
      await abreEDisparaUndoLD(page, 0);
      await fechar(page);
      await page.waitForFunction(() => !document.getElementById("launch-detail-overlay").classList.contains("open"));
      await page.evaluate(() => openHistoryDetail(1));
      await page.getByText("Padaria", { exact: false }).first().waitFor();
      release();
      await page.waitForTimeout(300);
      assert.equal(await page.locator("#generic-confirm-overlay.open").count(), 0,
        "o alerta do undo de OUTRO lançamento apareceu por cima do detalhe novo");
      assert.equal(
        await page.locator("#launch-detail-overlay").evaluate(el => el.classList.contains("open")), true,
        "o detalhe novo (Padaria) foi fechado pela continuação do undo antigo");
    } finally { await page.close(); }
  });
}

test("Desfazer: o 409 de um undo antigo não sequestra a confirmação de 'Apagar lançamento' de outro detalhe (P2 Codex)", async () => {
  let release;
  const held = new Promise(r => { release = r; });
  const { page } = await pageFor(800, [], {
    actionHandler: () => held.then(() => ({ body: { detail: "Par 501 já foi desfeito." }, status: 409 })),
  });
  const deletes = [];
  await page.route("**/launches/1/302", route => {
    deletes.push(route.request().method());
    return route.fulfill(json({ ok: true }));
  });
  try {
    await abreEDisparaUndoLD(page, 0);               // POST undo 501 em voo
    await page.locator("#ld-close").click();          // usuário fecha o detalhe
    await page.evaluate(() => openHistoryDetail(1));   // abre o detalhe da Padaria
    await page.locator("#ld-delete").click();          // e pede pra apagar
    await page.getByText("Apagar lançamento", { exact: false }).waitFor();
    release();                                          // chega o 409 do undo velho
    await page.waitForTimeout(300);
    const dialogo = await page.locator("#generic-confirm-overlay").innerText();
    assert.ok(dialogo.includes("Apagar lançamento"),
      `o 409 do undo de outro lançamento reescreveu a confirmação de exclusão: ${JSON.stringify(dialogo)}`);
    await page.locator("#generic-confirm-ok").click();
    await page.waitForTimeout(200);
    assert.deepEqual(deletes, ["DELETE"], "o DELETE da Padaria não saiu depois da confirmação");
  } finally { await page.close(); }
});

test("Desfazer: sem trocar de detalhe, o fluxo normal continua igual — fecha, recarrega e no erro alerta (controle positivo)", async () => {
  const { page, posts } = await pageFor(800, [], {
    actionHandler: () => ({ status: 409, body: { detail: "Não foi possível concluir. Atualize a lista e confira novamente." } }),
  });
  try {
    await abreEDisparaUndoLD(page, 0);
    await page.getByText("Não foi possível concluir. Atualize a lista", { exact: false }).waitFor();
    await page.locator("#generic-confirm-ok").click();
    await page.waitForFunction(() => document.getElementById("launch-detail-overlay").classList.contains("open") === false);
    assert.deepEqual(posts, [{ ofTxId: 501, action: "undo", csrf: "tok123" }]);
  } finally { await page.close(); }
});

// Achado do Codex, confirmado pelo Tester (P2): fechar o diálogo (Esc, botão
// "Fechar" ou clique no backdrop) com o POST de confirmar/rejeitar/desfazer
// ainda em voo. `close()` põe `overlay` em null antes da Promise do fetch
// resolver; sem guarda em `_run`, a continuação chama `load()` — que faz
// `overlay.querySelector` — e estoura, para os TRÊS caminhos de fechamento:
// alerta técnico cru pro usuário, TypeError não tratada, e `onSave` (refresh
// do dashboard) nunca roda mesmo quando a ação DEU CERTO no servidor.
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
async function raced(promise, ms) {
  return Promise.race([promise.then(() => true), sleep(ms).then(() => false)]);
}
const fechamentos = {
  "Esc": page => page.keyboard.press("Escape"),
  "botão Fechar": page => page.getByRole("button", { name: "Fechar" }).click(),
  "clique no backdrop": page => page.locator("#reconciliations-overlay").click({ position: { x: 4, y: 4 } }),
};

for (const [nome, fechar] of Object.entries(fechamentos)) {
  test(`fechar por ${nome} com POST em voo + sucesso: sem alerta técnico e onSave é chamado (P2 Codex)`, async () => {
    let release;
    const held = new Promise(r => { release = r; });
    const { page, posts } = await pageFor(800, [pendingRow], { actionHandler: () => held.then(() => null) });
    const erros = [];
    page.on("pageerror", e => erros.push(e.message));
    let onSaveCalls = 0, resolveOnSave;
    const onSaveCalled = new Promise(r => { resolveOnSave = r; });
    await page.exposeFunction("__onSave", () => { onSaveCalls++; resolveOnSave(); });
    try {
      await page.evaluate(() => window.Reconciliations.open(1, window.__onSave));
      await page.locator("#reconciliations-overlay").getByText("Mercado", { exact: false }).first().waitFor();
      const posted = page.waitForRequest(r => r.url().endsWith("/confirm"));
      await page.getByRole("button", { name: "É o mesmo gasto" }).click();
      await posted;
      await fechar(page);
      assert.equal(await page.locator("#reconciliations-overlay").count(), 0, "overlay devia estar fechado");
      release();
      const chamou = await raced(onSaveCalled, 1000);
      assert.ok(chamou, "onSave não foi chamado a tempo — a ação deu certo no servidor mas o dashboard não é atualizado");
      assert.equal(onSaveCalls, 1);
      assert.equal(await page.locator("#generic-confirm-overlay.open").count(), 0,
        "alerta técnico apareceu pra uma ação que deu certo (overlay.querySelector em null)");
      assert.deepEqual(erros, [], `pageerror não tratado: ${erros.join(" | ")}`);
      assert.deepEqual(posts, [{ ofTxId: 501, action: "confirm", csrf: "tok123" }]);
    } finally { await page.close(); }
  });

  test(`fechar por ${nome} com POST em voo + falha do servidor: sem alerta técnico e dashboard não é tocado (P2 Codex)`, async () => {
    let release;
    const held = new Promise(r => { release = r; });
    const { page, posts } = await pageFor(800, [pendingRow], {
      actionHandler: () => held.then(() => ({ status: 409, body: { detail: "Não foi possível concluir. Atualize a lista e confira novamente." } })),
    });
    const erros = [];
    page.on("pageerror", e => erros.push(e.message));
    let onSaveCalls = 0;
    await page.exposeFunction("__onSave", () => { onSaveCalls++; });
    try {
      await page.evaluate(() => window.Reconciliations.open(1, window.__onSave));
      await page.locator("#reconciliations-overlay").getByText("Mercado", { exact: false }).first().waitFor();
      const posted = page.waitForRequest(r => r.url().endsWith("/confirm"));
      await page.getByRole("button", { name: "É o mesmo gasto" }).click();
      await posted;
      await fechar(page);
      assert.equal(await page.locator("#reconciliations-overlay").count(), 0, "overlay devia estar fechado");
      const resp = page.waitForResponse(r => r.url().endsWith("/confirm"));
      release();
      await resp;
      // Sem mais nada esperando o quê: dá tempo pra continuação de `_run`
      // rodar (ou travar num alerta que não devia existir) antes de olhar.
      await page.waitForTimeout(200);
      assert.equal(await page.locator("#generic-confirm-overlay.open").count(), 0,
        "alerta técnico apareceu pra um diálogo que já não está na tela");
      assert.equal(onSaveCalls, 0, "dashboard foi atualizado como se a ação tivesse dado certo");
      assert.deepEqual(erros, [], `pageerror não tratado: ${erros.join(" | ")}`);
      assert.deepEqual(posts, [{ ofTxId: 501, action: "confirm", csrf: "tok123" }]);
    } finally { await page.close(); }
  });
}
