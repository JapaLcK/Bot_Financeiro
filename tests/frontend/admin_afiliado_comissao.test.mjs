/**
 * Percentual de comissão no /admin: coluna, campo da criação e "Editar comissão".
 * DOM pelo caminho real (`loadAffiliates` → fetch → render), em desktop e celular. Em cada teste, o comentário de cima diz qual mutação o deixa vermelho.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { startServer } from "./_server.mjs";
import { chromium } from "playwright";

let ORIGIN, server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer()); browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

const afiliado = (id, extra) => ({ id, code: `AF${id}`, email: `af${id}@x.com`, commission_bps: 10000, referrals: 1, owed_cents: 0, paid_cents: 0, pix_key: null, status: "active", ...extra });
const VISAO = { affiliates: [afiliado(1), afiliado(2, { commission_bps: 1250 })], payouts: [], refund_reviews: [], refund_recoveries: [], refund_history: [] };

async function abrir({ width = 1280, height = 900, respostas = [], visao = VISAO } = {}) {
  const page = await browser.newPage({ viewport: { width, height } });
  const posts = [];
  await page.route("**/admin/api/**", (r) => r.fulfill({ contentType: "application/json", body: "{}" }));
  await page.route("**/admin/api/affiliates", (r) => {
    if (r.request().method() === "POST") {
      posts.push({ url: "/admin/api/affiliates", corpo: r.request().postDataJSON() });
      return r.fulfill({ contentType: "application/json", body: JSON.stringify({ ok: true, affiliate: { code: "NOVO" } }) });
    }
    return r.fulfill({ contentType: "application/json", body: JSON.stringify(visao) });
  });
  await page.route(/\/admin\/api\/affiliates\/\d+\/commission$/, (r) => {
    posts.push({ url: new URL(r.request().url()).pathname, corpo: r.request().postDataJSON() });
    return r.fulfill({ contentType: "application/json", body: "{\"ok\":true}" });
  });
  // prompt, confirm e alert respondem na ordem de `respostas`; o resto aceita.
  page.on("dialog", (d) => (d.type() === "prompt" ? d.accept(respostas.shift() ?? "") : d.accept()));
  await page.goto(`${ORIGIN}/admin-dashboard.html`, { waitUntil: "domcontentloaded" });
  await page.evaluate(() => window.loadAffiliates());
  await page.locator("#aff-list tr").first().waitFor({ state: "attached" });
  return { page, posts };
}
const TAMANHOS = [["desktop", 1280, 900], ["celular", 390, 844], ["320", 320, 568]];

// mutação: voltar a coluna a `toFixed(0)` (12,5% vira "13%"), ou tirar o botão das ações
for (const [nome, width, height] of TAMANHOS) {
  test(`coluna em português e botão "Editar comissão" em cada linha (${nome})`, async () => {
    const { page } = await abrir({ width, height });
    try {
      const comissoes = await page.locator('#aff-list td[data-label="Comissão"]').allInnerTexts();
      assert.deepEqual(comissoes.map((t) => t.trim()), ["100%", "12,5%"]);
      assert.equal(await page.getByRole("button", { name: "Editar comissão" }).count(), 2);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), true);
    } finally { await page.close(); }
  });
}

// mutação: mandar o percentual cru (30) em vez de bps, ou aceitar 101/"abc"/"12,5"
for (const [entrada, esperado] of [["30", { commission_bps: 3000 }], ["0", { commission_bps: 0 }], ["101", null], ["abc", null], ["12,5", null], ["", null]]) {
  test(`editar: prompt "${entrada}" ${esperado ? `envia ${esperado.commission_bps}` : "não envia"}`, async () => {
    const { page, posts } = await abrir({ respostas: [entrada] });
    try {
      await page.getByRole("button", { name: "Editar comissão" }).nth(1).click();
      await page.waitForTimeout(200);
      assert.deepEqual(posts, esperado ? [{ url: "/admin/api/affiliates/2/commission", corpo: esperado }] : []);
    } finally { await page.close(); }
  });
}

// mutação: tirar `commission_bps` do corpo da criação, ou mandar `pct` sem `* 100`
test("criar sem mexer no campo envia 10000; com 25 envia 2500; 101 não envia", async () => {
  const { page, posts } = await abrir();
  try {
    assert.equal(await page.locator("#aff-new-pct").inputValue(), "100");
    await page.locator("#aff-new-email").fill("nova@x.com");
    await page.getByRole("button", { name: "+ Criar afiliado" }).click();
    await page.waitForTimeout(200);
    await page.locator("#aff-new-email").fill("outra@x.com");
    await page.locator("#aff-new-pct").fill("25");
    await page.getByRole("button", { name: "+ Criar afiliado" }).click();
    await page.waitForTimeout(200);
    await page.locator("#aff-new-email").fill("errada@x.com");
    await page.locator("#aff-new-pct").fill("101");
    await page.getByRole("button", { name: "+ Criar afiliado" }).click();
    await page.waitForTimeout(200);
    assert.deepEqual(posts.map((p) => p.corpo), [{ email: "nova@x.com", commission_bps: 10000 }, { email: "outra@x.com", commission_bps: 2500 }]);
  } finally { await page.close(); }
});

// mutação: tirar o `white-space:nowrap` da célula: com e-mail e chave longos, "12,5%" quebra em duas linhas em 1280
for (const [nome, width, height] of TAMANHOS) {
  test(`a coluna % fica numa linha com e-mail e chave longos (${nome})`, async () => {
    const longo = (a) => ({ ...a, email: `afiliado-com-email-longo-${a.id}@exemplo.com.br`, pix_key: "chave-pix-bem-comprida@exemplo.com.br" });
    const { page } = await abrir({ width, height, visao: { ...VISAO, affiliates: VISAO.affiliates.map(longo) } });
    try {
      const linhas = await page.locator('#aff-list td[data-label="Comissão"]').evaluateAll((l) => l.map((e) => {
        const r = document.createRange(); r.selectNodeContents(e); return r.getClientRects().length;
      }));
      assert.deepEqual(linhas, [1, 1]);
    } finally { await page.close(); }
  });
}
