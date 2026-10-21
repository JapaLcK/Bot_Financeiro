/**
 * PR-A2b-2: "Foi em dinheiro" no modal de conferência (frontend/bank-movements.js) e a linha de contexto/aviso
 * do apagar no detalhe do Histórico (frontend/dashboard.js). Abre a dashboard.html real, então a CSS do modal está
 * na página. O backend é simulado por rota; o contrato real está em tests/test_declaracao_a2b2_*.py.
 *
 * Controle: voltar o `picker` à fileira única de botões do fim do item -> VERMELHO "cada botão fica logo abaixo do
 * seu select" (o "Conferir vínculo" cai depois do select da Carteira).
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
const carteira = (id, name, date = "2026-10-15") => ({ id, name, category: "alimentacao", amount: 50, date });
const gasto = { launch_id: 71, name: "mercado", bank: null, amount: -50, date: "2026-10-14", tipo: "despesa",
  fluxo: true, manual_only: false, requires_review: false, match: null, candidates: [tx(22, "SUPERMERCADO XYZ", "2026-10-14")],
  cash: [carteira(55, "feira"), carteira(56, "padaria", "2026-10-16")] };
const receita = { ...gasto, launch_id: 72, name: "freela", amount: 80, tipo: "receita", candidates: [], cash: [] };
const aporte = { launch_id: 73, name: "viagem", bank: "Nubank · Conta", amount: -500, date: "2026-10-14",
  tipo: "deposito_caixinha", fluxo: false, manual_only: false, requires_review: false, match: null, candidates: [], cash: null };
const casada = { ...gasto, manual_only: true, match: tx(22, "SUPERMERCADO XYZ", "2026-10-14"), candidates: [tx(23, "PIX MARIA", "2026-10-15")], cash: null };
const limpa = s => s.replaceAll(" ", " ");

async function pageFor(width, movements, { onPost, launchId, wallet = 950 } = {}) {
  const page = await browser.newPage({ viewport: { width, height: 900 } });
  const posts = [], gets = [];
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.origin !== origin) return route.abort();
    if (/\.[a-z0-9]+$/i.test(url.pathname)) return route.continue();
    if (url.pathname === "/auth/validate") return route.fulfill(json({ user_id: 1 }));
    const m = url.pathname.match(/^\/open-finance\/1\/movements\/(confirm|undo|cash)$/);
    if (m) {
      posts.push({ path: m[1], body: route.request().postDataJSON() });
      const out = onPost ? await onPost(m[1]) : null;
      return route.fulfill(out || json({ ok: true }));
    }
    if (url.pathname === "/open-finance/1/movements") {
      gets.push(url.search);
      return route.fulfill(json({ ok: true, movements, wallet_balance: wallet }));
    }
    return route.fulfill(json({}));
  });
  await page.goto(`${origin}/dashboard.html`);
  await page.waitForFunction(() => Boolean(window.BankMovements) && USER_ID === 1);
  await page.evaluate(id => { _ensureGenericConfirmModal(); window.__saved = [];
    window.BankMovements.open(1, async msg => { window.__saved.push(msg); }, { launchId: id }); }, launchId ?? null);
  return { page, posts, gets };
}
const corpo = page => page.locator("#generic-confirm-body").innerText().then(limpa);

for (const width of [1365, 375]) {
  test(`o grupo só existe no fluxo sem par; cada botão fica logo abaixo do seu select, dentro da tela em ${width}px`, async () => {
    const longo = "x".repeat(80);
    const { page } = await pageFor(width, [{ ...gasto, cash: [carteira(55, longo)] }, receita, aporte]);
    const modal = page.locator("#bank-movements-overlay .modal");
    try {
      await page.getByText("Gasto · R$ 50,00", { exact: false }).waitFor();
      assert.equal(await page.getByRole("button", { name: "Foi em dinheiro" }).count(), 1);
      assert.equal(await page.getByRole("button", { name: "Recebi em dinheiro" }).count(), 1);  // a receita; o aporte não tem
      // grupos: banco + Carteira do gasto; só a Carteira da receita (sem candidato do banco); nenhum no aporte
      assert.equal(await page.locator("#bank-movements-overlay").getByRole("group").count(), 3);
      const g = page.locator("#bank-movements-overlay").getByRole("group", { name: "Foi em dinheiro" });
      assert.equal(await g.count(), 1);
      assert.equal(await g.locator("select option").count(), 3);  // placeholder + 1 candidato + "nenhum destes"
      assert.equal(await g.getByRole("button", { name: "Foi em dinheiro" }).isDisabled(), true);  // placeholder: nada a enviar
      const m = await page.evaluate(() => {
        const box = n => n.getBoundingClientRect();
        const banco = document.getElementById("bank-movement-71"), dinheiro = document.getElementById("bank-cash-71");
        const bBanco = document.evaluate(".//button[text()='Conferir vínculo']", banco.parentElement, null, 9).singleNodeValue;
        const bDinheiro = document.evaluate(".//button[text()='Foi em dinheiro']", dinheiro.parentElement, null, 9).singleNodeValue;
        const modal = document.querySelector("#bank-movements-overlay .modal");
        return { selBancoFim: box(banco).bottom, bBanco: box(bBanco).top, bBancoFim: box(bBanco).bottom, selDinheiro: box(dinheiro).top,
          selDinheiroFim: box(dinheiro).bottom, bDinheiro: box(bDinheiro).top, sw: modal.scrollWidth, cw: modal.clientWidth,
          selW: box(dinheiro).width, modalW: box(modal).width, rotuloDinheiro: box(document.getElementById("bank-cash-71-label")).top };
      });
      assert.ok(m.bBanco >= m.selBancoFim && m.bBancoFim <= m.selDinheiro, `botão do banco entre os dois selects: ${JSON.stringify(m)}`);
      // folga: o botão do banco fica mais perto do SEU select do que do rótulo do grupo seguinte (senão parece dele)
      assert.ok(m.rotuloDinheiro - m.bBancoFim >= m.bBanco - m.selBancoFim, `folga entre grupos: ${JSON.stringify(m)}`);
      assert.ok(m.bDinheiro >= m.selDinheiroFim, `botão da Carteira abaixo do select da Carteira: ${JSON.stringify(m)}`);
      assert.ok(m.sw <= m.cw && m.selW <= m.modalW, `sem overflow com nome de 80 caracteres: ${JSON.stringify(m)}`);
      const b = await modal.boundingBox();
      assert.ok(b.x >= 0 && b.x + b.width <= width, JSON.stringify(b));
      for (let i = 0; i < 12; i++) {
        await page.keyboard.press("Tab");
        assert.ok(await page.evaluate(() => document.getElementById("bank-movements-overlay").contains(document.activeElement)));
      }
      if (process.env.PB_QA_DIR) {
        mkdirSync(process.env.PB_QA_DIR, { recursive: true });
        await modal.evaluate(async n => { await Promise.all(n.getAnimations().map(a => a.finished)); });
        await page.screenshot({ path: `${process.env.PB_QA_DIR}/a2b2-dinheiro-${width}.png`, fullPage: true });
      }
    } finally { await page.close(); }
  });
}

test("casada e aporte não ganham o grupo; modo launchId resolvido mostra a mensagem certa", async () => {
  const { page } = await pageFor(800, [casada], { launchId: 71 });
  try {
    await page.getByText("Conferida com o extrato", { exact: true }).waitFor();
    assert.equal(await page.getByRole("button", { name: /em dinheiro/ }).count(), 0);
  } finally { await page.close(); }
  const vazio = await pageFor(800, [], { launchId: 71 });
  try {
    await vazio.page.getByText("Esta declaração já foi resolvida ou apagada.", { exact: true }).waitFor();
  } finally { await vazio.page.close(); }
  const lista = await pageFor(800, []);
  try {
    await lista.page.getByText("Nenhuma declaração aguardando confirmação.", { exact: true }).waitFor();
  } finally { await lista.page.close(); }
});

test("selecionar: data original e 'saldo não muda' no confirm, nada enviado antes, um POST com o id escolhido", async () => {
  let solta;
  const segura = new Promise(r => { solta = r; });
  const { page, posts, gets } = await pageFor(800, [gasto], { onPost: () => segura.then(() => null) });
  try {
    await page.locator("#bank-cash-71").selectOption("55");
    const botao = page.getByRole("button", { name: "Foi em dinheiro" });
    assert.equal(await botao.isDisabled(), false);
    await botao.evaluate(b => { b.click(); b.click(); });  // clique duplo antes do confirm
    await page.locator("#generic-confirm-ok").waitFor();
    const texto = await corpo(page);
    assert.ok(texto.includes("Declaração: mercado, R$ 50,00, 2026-10-14."), texto);  // a data original da declaração
    assert.ok(texto.includes("2026-10-15 · R$ 50,00 · feira") && texto.includes("o saldo da Carteira não muda"), texto);
    assert.deepEqual(posts, []);
    await page.keyboard.press("Escape");  // Esc cancela sem POST e o botão segue usável
    await new Promise(r => setTimeout(r, 300));
    assert.deepEqual(posts, []);
    assert.equal(await botao.isDisabled(), false);
    await botao.click();
    await page.locator("#generic-confirm-ok").click();
    await until(() => posts.length === 1);
    assert.equal(await botao.isDisabled(), true);  // em voo
    solta();
    await until(() => gets.length === 2);
    await page.waitForFunction(() => window.__saved.length >= 1);  // o afterSave roda depois do load()
    assert.deepEqual(posts, [{ path: "cash", body: { launch_id: 71, carteira_launch_id: 55 } }]);
    assert.deepEqual(await page.evaluate(() => window.__saved), ["Associado ao lançamento da Carteira."]);
  } finally { await page.close(); }
});

test("converter: saldo antes e depois no confirm, aviso só quando fica negativo, corpo com null", async () => {
  for (const [wallet, negativo] of [[950, false], [10, true]]) {
    const { page, posts } = await pageFor(800, [gasto], { wallet });
    try {
      await page.locator("#bank-cash-71").selectOption("none");
      await page.getByRole("button", { name: "Foi em dinheiro" }).click();
      await page.locator("#generic-confirm-ok").waitFor();
      const texto = await corpo(page);
      assert.ok(texto.includes(`Sua Carteira vai de ${limpa(wallet === 950 ? "R$ 950,00" : "R$ 10,00")} para ${wallet === 950 ? "R$ 900,00" : "-R$ 40,00"}.`), texto);
      assert.ok(texto.includes("com a data da declaração (2026-10-14)"), texto);
      assert.equal(texto.includes("a Carteira vai ficar negativa"), negativo, texto);
      assert.equal(limpa(await page.locator("#generic-confirm-ok").innerText()), "Lançar na Carteira");
      assert.deepEqual(posts, []);
      await page.locator("#generic-confirm-ok").click();
      await until(() => posts.length === 1);
      assert.deepEqual(posts, [{ path: "cash", body: { launch_id: 71, carteira_launch_id: null } }]);
      await page.waitForFunction(() => window.__saved.length === 1);
      assert.deepEqual(await page.evaluate(() => window.__saved), ["Registrado como pago em dinheiro."]);
    } finally { await page.close(); }
  }
});

test("receita: 'Recebi em dinheiro' e o saldo sobe", async () => {
  const { page } = await pageFor(800, [receita], { wallet: 100 });
  try {
    await page.locator("#bank-cash-72").selectOption("none");
    await page.getByRole("button", { name: "Recebi em dinheiro" }).click();
    await page.locator("#generic-confirm-ok").waitFor();
    const texto = await corpo(page);
    assert.ok(texto.includes("Vou lançar R$ 80,00 na Carteira") && texto.includes("vai de R$ 100,00 para R$ 180,00"), texto);
  } finally { await page.close(); }
});

test("404 recarrega sem alerta; outro erro alerta e não chama o afterSave", async () => {
  for (const [status, alerta] of [[404, false], [409, true]]) {
    const { page, gets } = await pageFor(800, [gasto], { onPost: () => json({ detail: "x" }, status) });
    try {
      await page.locator("#bank-cash-71").selectOption("none");
      await page.getByRole("button", { name: "Foi em dinheiro" }).click();
      await page.locator("#generic-confirm-ok").click();
      if (alerta) {
        await page.getByText("Não foi possível concluir", { exact: false }).waitFor();
        await page.locator("#generic-confirm-ok").click();
      }
      await until(() => gets.length === 2);
      await page.waitForFunction(n => window.__saved.length >= n, status === 404 ? 1 : 0);  // afterSave só depois do load()
      assert.equal(await page.getByText("Não foi possível concluir", { exact: false }).isVisible(), false, `status ${status}`);
      assert.deepEqual(await page.evaluate(() => window.__saved), status === 404 ? [undefined] : []);
    } finally { await page.close(); }
  }
});

test("alvo e categoria NULL ou só espaços do lançamento da Carteira não viram null nem sobra de separador", async () => {
  const { page } = await pageFor(800, [{ ...gasto, cash: [{ id: 55, name: null, category: null, amount: 50, date: "2026-10-15" },
    { id: 56, name: "   ", category: "  ", amount: 50, date: "2026-10-16" }] }]);
  try {
    assert.equal(limpa(await page.locator("#bank-cash-71 option").nth(1).innerText()), "2026-10-15 · R$ 50,00 · sem descrição");
    assert.equal(limpa(await page.locator("#bank-cash-71 option").nth(2).innerText()), "2026-10-16 · R$ 50,00 · sem descrição");
    await page.locator("#bank-cash-71").selectOption("55");
    await page.getByRole("button", { name: "Foi em dinheiro" }).click();
    await page.locator("#generic-confirm-ok").waitFor();
    const texto = await corpo(page);
    assert.ok(texto.includes("2026-10-15 · R$ 50,00 · sem descrição") && !texto.includes("null"), texto);
  } finally { await page.close(); }
});

test("declaração com alvo NULL (\"gastei 50\") não mostra null no item nem no confirm de converter", async () => {
  const { page } = await pageFor(800, [{ ...gasto, name: null }]);
  try {
    assert.equal(limpa(await page.locator("#bank-movements-overlay h4").innerText()), "sem descrição");
    await page.locator("#bank-cash-71").selectOption("none");
    await page.getByRole("button", { name: "Foi em dinheiro" }).click();
    await page.locator("#generic-confirm-ok").waitFor();
    const texto = await corpo(page);
    assert.ok(texto.includes("Declaração: sem descrição, R$ 50,00") && !texto.includes("null"), texto);
  } finally { await page.close(); }
});

test("nome do lançamento da Carteira entra como texto, nunca como HTML", async () => {
  const evil = '<img src=x onerror="window.__xss=1">';
  const { page } = await pageFor(800, [{ ...gasto, cash: [carteira(55, evil)] }]);
  try {
    await page.locator("#bank-cash-71").selectOption("55");
    await page.getByRole("button", { name: "Foi em dinheiro" }).click();
    await page.locator("#generic-confirm-ok").waitFor();
    assert.equal(await page.evaluate(() => window.__xss), undefined);
    assert.equal(await page.locator("#bank-movements-overlay img, #generic-confirm-overlay img").count(), 0);
    assert.ok((await corpo(page)).includes(evil));
  } finally { await page.close(); }
});

// ---- Histórico: a linha de contexto e o aviso do apagar ----
const CONTEXTO = { tipo: "despesa", valor: "50.00", alvo: "mercado", nota: "compras da semana", categoria: "mercado",
  declarada_em: "2026-10-03T10:00:00-03:00", numero: 12 };
const ITENS = [
  { id: 401, tipo: "despesa", valor: 50, alvo: "mercado", nota: "feira", criado_em: "2026-10-04T10:00:00-03:00", bank_declaration: false, cash_declaration: CONTEXTO },
  { id: 402, tipo: "despesa", valor: 30, alvo: "padaria", nota: null, criado_em: "2026-10-04T09:00:00-03:00", bank_declaration: false, cash_declaration: null },
];

async function historico(width, itens = ITENS) {
  const page = await browser.newPage({ viewport: { width, height: 800 } });
  const deletes = [];
  await page.route("**/*", route => {
    const url = new URL(route.request().url());
    if (url.origin !== origin) return route.abort();
    if (/\.[a-z0-9]+$/i.test(url.pathname)) return route.continue();
    if (url.pathname === "/auth/validate") return route.fulfill(json({ user_id: 1 }));
    if (route.request().method() === "DELETE") { deletes.push(url.pathname); return route.fulfill(json({ ok: true })); }
    return route.fulfill(json({ ok: true, items: [] }));
  });
  await page.goto(`${origin}/dashboard.html`);
  await page.waitForFunction(() => Boolean(window.BankMovements) && USER_ID === 1);
  await page.mouse.move(width - 10, 700);
  await page.evaluate(itens => { document.getElementById("history-view").classList.add("active"); renderHistoryTimeline({ ok: true, items: itens }, false); }, itens);
  return { page, deletes };
}

for (const width of [1365, 375, 320]) {
  test(`detalhe do Histórico: linha de contexto só na associada, apagar avisa, sem overflow em ${width}px`, async () => {
    const { page, deletes } = await historico(width);
    try {
      await page.locator(".tx-row", { hasText: "padaria" }).click();
      await page.locator("#launch-detail-overlay.open").waitFor();
      assert.equal(await page.getByText("Declarada pelo banco", { exact: true }).count(), 0);
      await page.locator("#ld-close").click();

      await page.locator(".tx-row", { hasText: "mercado" }).click();
      await page.locator("#launch-detail-overlay.open").waitFor();
      const linha = await page.locator(".ld-row", { hasText: "Declarada pelo banco" }).innerText();
      assert.equal(limpa(linha).replace(/\s+/g, " "), "Declarada pelo banco 03/10: mercado, R$ 50,00 - compras da semana");
      const m = await page.evaluate(() => {
        const modal = document.querySelector("#launch-detail-overlay .modal");
        return { sw: modal.scrollWidth, cw: modal.clientWidth, acts: document.querySelector(".ld-acts").scrollWidth <= document.querySelector(".ld-acts").clientWidth };
      });
      assert.ok(m.sw <= m.cw && m.acts, JSON.stringify(m));
      if (process.env.PB_QA_DIR) {
        mkdirSync(process.env.PB_QA_DIR, { recursive: true });
        await page.locator("#launch-detail-overlay .modal").evaluate(async n => { await Promise.all(n.getAnimations().map(a => a.finished)); });
        await page.screenshot({ path: `${process.env.PB_QA_DIR}/a2b2-detalhe-${width}.png` });
      }
      await page.locator("#ld-delete").click();
      await page.locator("#generic-confirm-ok").waitFor();
      const aviso = limpa(await page.locator("#generic-confirm-body").innerText());
      assert.ok(aviso.includes("Este lançamento está associado à declaração de mercado, R$ 50,00, de 03/10. Apagar remove os dois registros."), aviso);
      await page.locator("#generic-confirm-cancel").click();
      assert.deepEqual(deletes, []);
    } finally { await page.close(); }
  });
}

test("declaração com alvo NULL: detalhe do Histórico e texto do apagar não mostram null", async () => {
  const sem = [{ ...ITENS[0], id: 403, alvo: "feira", cash_declaration: { ...CONTEXTO, alvo: null, nota: null } }];
  const { page } = await historico(1365, sem);
  try {
    await page.locator(".tx-row", { hasText: "feira" }).click();
    await page.locator("#launch-detail-overlay.open").waitFor();
    const linha = limpa(await page.locator(".ld-row", { hasText: "Declarada pelo banco" }).innerText()).replace(/\s+/g, " ");
    assert.equal(linha, "Declarada pelo banco 03/10: sem descrição, R$ 50,00");
    await page.locator("#ld-delete").click();
    await page.locator("#generic-confirm-ok").waitFor();
    const aviso = limpa(await page.locator("#generic-confirm-body").innerText());
    assert.ok(aviso.includes("associado à declaração de sem descrição, R$ 50,00, de 03/10.") && !aviso.includes("null"), aviso);
  } finally { await page.close(); }
});

test("apagar um lançamento comum não ganha o aviso", async () => {
  const { page } = await historico(1365);
  try {
    await page.locator(".tx-row", { hasText: "padaria" }).click();
    await page.locator("#ld-delete").click();
    await page.locator("#generic-confirm-ok").waitFor();
    assert.ok(!(await page.locator("#generic-confirm-body").innerText()).includes("associado à declaração"));
  } finally { await page.close(); }
});
