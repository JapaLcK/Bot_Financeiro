/**
 * Q41 PR B, /home: o aviso "Dinheiro vivo" (home.html, `renderAlerts`) lê o
 * mesmo item de `alerts` que a faixa do /app e leva ao /app. Página REAL com o
 * `/data` falso (molde: home_tipo_atividade.test.mjs), a 1280 e a 390 px.
 *
 * Controle NEGATIVO (medido, ver o relato do PR): com o bloco do aviso depois
 * dos `return` da fatura, o caso "sozinho" fica vermelho (sem fatura a função
 * sai antes de chegar nele).
 *
 * Rodar: node --test tests/frontend/cash_transfers_home.test.mjs
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { chromium } from "playwright";
import { startServer } from "./_server.mjs";

let server, browser, origin;
before(async () => { ({ proc: server, origin } = await startServer()); browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

const json = (body) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
const segura = async (fn) => { try { await fn(); } catch { /* navegou/fechou */ } };
const snapshot = (o) => ({
  balance: 0, of_bank_balance: 0, monthly_income: 0, monthly_expense: 0, credit_cards: [], pockets: [],
  investments: [], alerts: [], recent_launches: [], launches_pagination: { page: 1, limit: 25, total: 0, total_pages: 1 }, ...o,
});
// Fatura vencida: fechou há 2 meses, vence dia 5 do mês seguinte → atrasada.
const vencida = () => {
  const pe = new Date(); pe.setDate(1); pe.setMonth(pe.getMonth() - 2);
  return { name: "Nubank", due_amount: 120, period_end: pe.toISOString().slice(0, 10), due_day: 5 };
};

async function abrirHome(width, snap) {
  const page = await browser.newPage({ viewport: { width, height: 900 } });
  const errs = [];
  page.on("pageerror", (e) => errs.push(String(e)));
  await page.route("**/*", (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== origin) return segura(() => route.abort());
    if (/\.[a-z0-9]+$/i.test(url.pathname)) return segura(() => route.continue());
    if (url.pathname === "/auth/validate") return segura(() => route.fulfill(json({ user_id: 1 })));
    if (url.pathname.startsWith("/data/")) return segura(() => route.fulfill(json(snapshot(snap))));
    if (url.pathname.startsWith("/history/")) return segura(() => route.fulfill(json({ data: [] })));
    return segura(() => route.fulfill(json({})));
  });
  await page.goto(`${origin}/home.html`);
  await page.waitForFunction(() => document.querySelector("#stat-balance-sub")?.textContent.trim());
  return { page, errs };
}

const avisos = (page) => page.locator("#alert-banner-slot .alert-banner");

for (const width of [1280, 390]) {
  test(`${width}px: sozinho, o aviso aparece com o contador e leva ao /app; cabe`, async () => {
    const { page, errs } = await abrirHome(width, { alerts: [{ type: "cash_transfers", count: 3 }] });
    try {
      await avisos(page).first().waitFor();
      assert.equal(await avisos(page).count(), 1);
      assert.equal((await avisos(page).textContent()).replace(/\s+/g, " ").trim(), "Dinheiro vivo: 3 para conferir Conferir");
      const link = avisos(page).getByRole("link", { name: "Conferir" });
      assert.equal(await link.getAttribute("href"), "/app");
      const rotas = [...readFileSync("frontend/routes/static_pages.py", "utf8").matchAll(/@router\.get\("([^"]+)"\)/g)].map((m) => m[1]);
      assert.ok(rotas.includes("/app"), "/app não é página registrada");
      for (const el of [avisos(page), link]) {
        const b = await el.boundingBox();
        assert.ok(b.x >= 0 && b.x + b.width <= width, `fora da tela em ${width}: ${JSON.stringify(b)}`);
      }
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      assert.deepEqual(errs, []);
    } finally { await page.close(); }
  });
}

test("com fatura vencida também: os dois avisos aparecem", async () => {
  const { page, errs } = await abrirHome(390, { alerts: [{ type: "cash_transfers", count: 2 }], credit_cards: [vencida()] });
  try {
    await avisos(page).nth(1).waitFor();
    const textos = await avisos(page).allTextContents();
    assert.match(textos[0], /Dinheiro vivo:\s*2\s*para conferir/);
    assert.match(textos[1], /Fatura\s*Nubank/);
    assert.deepEqual(errs, []);
  } finally { await page.close(); }
});

test("positivo: count 0 (ou sem o item) não mostra aviso, e a fatura segue sozinha", async () => {
  const { page, errs } = await abrirHome(1280, { alerts: [{ type: "cash_transfers", count: 0 }], credit_cards: [vencida()] });
  try {
    await avisos(page).first().waitFor();
    const textos = await avisos(page).allTextContents();
    assert.equal(textos.length, 1);
    assert.match(textos[0], /Fatura/);
    assert.deepEqual(errs, []);
  } finally { await page.close(); }
});

test("a linha da conciliação debaixo do saldo continua igual", async () => {
  const { page, errs } = await abrirHome(1280, {
    alerts: [{ type: "cash_transfers", count: 1 }],
    reconciliation: { pending_count: 1, delta_se_confirmar: 50, receita_back: 0 },
  });
  try {
    await page.locator("#stat-balance-sub a", { hasText: "conferir" }).waitFor();
    assert.doesNotMatch(await page.locator("#stat-balance-sub").textContent(), /Dinheiro vivo/);
    assert.equal(await avisos(page).count(), 1);
    assert.deepEqual(errs, []);
  } finally { await page.close(); }
});
