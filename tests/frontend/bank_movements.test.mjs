import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { chromium } from "playwright";
import { startServer } from "./_server.mjs";
let browser, server, origin;
before(async () => { ({ proc: server, origin } = await startServer()); browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });
const json = body => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
const movement = {
  launch_id: 71, name: "Minha reserva de emergência <script>indesejado</script>",
  bank: "Nubank · Conta", amount: -500, date: "2026-09-14", requires_review: false,
  candidates: [{ id: 22, description: "Transferência para minha reserva", bank: "Nubank", amount: -500, date: "2026-09-14" }]
};
async function pageFor(width, movements, onConfirm) {
  const page = await browser.newPage({ viewport: { width, height: 900 } });
  await page.route("**/*", route => {
    const url = new URL(route.request().url());
    if (url.origin !== origin) return route.abort();
    if (/\.[a-z0-9]+$/i.test(url.pathname)) return route.continue();
    if (url.pathname === "/auth/validate") return route.fulfill(json({ user_id: 1 }));
    if (url.pathname.endsWith("/movements/confirm")) {
      onConfirm?.(route.request().postDataJSON());
      movements = [];
      return route.fulfill(json({ ok: true }));
    }
    if (url.pathname.endsWith("/movements")) return route.fulfill(json({ movements }));
    return route.fulfill(json({}));
  });
  await page.goto(`${origin}/dashboard.html`);
  await page.waitForFunction(() => Boolean(window.BankMovements) && USER_ID === 1);
  await page.evaluate(() => window.BankMovements.open(1));
  return page;
}
for (const width of [1365, 390]) {
  test(`declarações: leitura, escape e teclado em ${width}px`, async () => {
    const page = await pageFor(width, [movement, { ...movement, launch_id: 72, name: "Viagem", candidates: [] }]);
    try {
      await page.getByText("Ainda não há transação compatível.", { exact: false }).waitFor();
      assert.equal(await page.locator("#bank-movements-overlay script").count(), 0);
      assert.equal(await page.getByText(movement.name, { exact: true }).count(), 1);
      const bounds = await page.locator("#bank-movements-overlay .modal").boundingBox();
      assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= width);
      for (let i = 0; i < 6; i++) {
        await page.keyboard.press("Tab");
        assert.ok(await page.evaluate(() => document.getElementById("bank-movements-overlay").contains(document.activeElement)));
      }
      if (process.env.PB_QA_DIR) {
        mkdirSync(process.env.PB_QA_DIR, { recursive: true });
        await page.locator("#bank-movements-overlay .modal").evaluate(async node => { await Promise.all(node.getAnimations().map(a => a.finished)); });
        assert.ok(await page.locator("#bank-movements-overlay .modal").evaluate(node => getComputedStyle(node).opacity === "1" && node.contains(document.elementFromPoint(innerWidth / 2, innerHeight / 2))));
        await page.screenshot({ path: `${process.env.PB_QA_DIR}/movements-${width}.png` });
      }
      await page.keyboard.press("Escape");
      assert.equal(await page.locator("#bank-movements-overlay").count(), 0);
    } finally { await page.close(); }
  });
}

test("conferir exibe os dois fatos e só envia depois de confirmar", async () => {
  const posts = [];
  const page = await pageFor(800, [movement], body => posts.push(body));
  try {
    // O confirmador já existe no DOM antes de reabrir a lista.
    await page.evaluate(() => { window.BankMovements.close(); _ensureGenericConfirmModal(); window.BankMovements.open(1); });
    await page.locator("#bank-movement-71").selectOption("22");
    await page.getByRole("button", { name: "Conferir vínculo" }).click();
    await page.getByText("Confirma que são a mesma movimentação", { exact: false }).waitFor();
    assert.deepEqual(posts, []);
    await page.getByRole("button", { name: "São a mesma movimentação", exact: true }).click();
    await page.getByText("Nenhuma declaração aguardando confirmação.").waitFor();
    assert.deepEqual(posts, [{ launch_id: 71, transaction_id: 22 }]);
  } finally { await page.close(); }
});

test("patrimônio não soma uma declaração sem prova", async () => {
  const page = await pageFor(1365, []);
  try {
    await page.evaluate(() => {
      window.BankMovements.close();
      render({ user_id: 1, year: 2026, month: 9, is_current_month: true, balance: 0,
        of_bank_count: 1, of_bank_balance: 1000, pockets: [{ name: "viagem", balance: 500 }],
        investments: [], bank_movements: { pending_count: 1 } });
    });
    assert.equal(await page.locator("[data-num=pat]").count(), 0);
    // Inventário inline: BankMovements é o módulo público; USER_ID vem da
    // sessão validada; refreshDashboardAfterInvestment atualiza após conferência.
    // O cursor nasce sobre a barra lateral e pode expandi-la sobre este botão.
    // Afasta-o para que a ação teste o botão, não a sobreposição do menu.
    await page.mouse.move(800, 0);
    await page.waitForFunction(() => document.querySelector(".sidenav").getBoundingClientRect().width <= 70);
    await Promise.all([
      page.waitForRequest(request => new URL(request.url()).pathname === "/open-finance/1/movements"),
      page.getByRole("button", { name: "1 movimentação(ões) não confirmada(s)" }).click(),
    ]);
    await page.getByRole("dialog", { name: "Movimentações bancárias" }).waitFor();
    await page.getByText("Nenhuma declaração aguardando confirmação.").waitFor();
  } finally { await page.close(); }
});
