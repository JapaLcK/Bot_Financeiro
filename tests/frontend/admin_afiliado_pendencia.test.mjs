/**
 * Pendências de reembolso no /admin: blocos, saque bloqueado sem os botões de pagar, formulários.
 * DOM renderizado pelo caminho real (`loadAffiliates` → fetch → render), em desktop e celular. Em cada teste, o comentário de cima diz qual mutação o deixa vermelho.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { startServer } from "./_server.mjs";
import { chromium } from "playwright";

let ORIGIN, server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer()); browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

const saque = (id, extra) => ({ id, code: "ANA2026", email: "ana@x.com", pix_key: "ana@pix.com", amount_cents: 17000, status: "requested", note: null, requested_at: "2026-11-05T12:00:00+00:00", blocked: false, ...extra });
const pendencia = (id, extra) => ({ id, code: "ANA2026", tipo: "partial", status: "open", refunded_cents: 30000, amount_cents: 10000, recovery_cents: null, refunded_at: "2026-11-05T12:00:00+00:00", notified_at: null, commission_id: 50 + id, commission_cents: 10000, commission_status: "pending", payout_id: 7, payout_status: "requested", origem: null, referencia: null, ...extra });
const VISAO = {
  affiliates: [],
  payouts: [saque(7, { blocked: true }), saque(8)],
  refund_reviews: [pendencia(1), pendencia(2, { tipo: "total" })],
  refund_recoveries: [pendencia(3, { status: "recovery", recovery_cents: 4000, origem: "paid_before_refund", referencia: "E1234567" })],
  refund_history: [{ id: 9, review_id: 4, action: "recovery_waived", actor: "lucas", before_cents: 4000, after_cents: null, payout_id: null, event_on: null, receipt_ref: null, justification: "<b>sem</b> retorno", created_at: "2026-11-05T12:00:00+00:00", tipo: "total", commission_id: 54, code: "BIA2026" }],
};

async function abrir({ width = 1280, height = 900, visao = VISAO, resposta = { status: 200, body: "{\"ok\":true}" } } = {}) {
  const page = await browser.newPage({ viewport: { width, height } });
  const posts = [], leituras = [];
  await page.route("**/admin/api/**", (r) => r.fulfill({ contentType: "application/json", body: "{}" }));
  await page.route("**/admin/api/affiliates", (r) => { leituras.push(Date.now()); return r.fulfill({ contentType: "application/json", body: JSON.stringify(visao) }); });
  await page.route(/\/admin\/api\/affiliates\/(payouts|refund-reviews)\/\d+\/(payment-recorded|approve|close-recovery)$/, (r) => {
    posts.push({ url: new URL(r.request().url()).pathname, corpo: r.request().postDataJSON() });
    return r.fulfill({ contentType: "application/json", ...resposta });
  });
  await page.goto(`${ORIGIN}/admin-dashboard.html`, { waitUntil: "domcontentloaded" });
  await page.evaluate(() => window.loadAffiliates());
  await page.locator("#aff-reviews tr").first().waitFor({ state: "attached" });
  return { page, posts, leituras };
}
const TAMANHOS = [["desktop", 1280, 900], ["celular", 390, 844], ["320", 320, 568]];

// mutação: tirar o ramo `p.blocked` de `loadAffiliates`: o saque bloqueado volta a mostrar "Pagar via Pix"
for (const [nome, width, height] of TAMANHOS) {
  test(`saque bloqueado sem botões de pagar, e o livre com eles (${nome})`, async () => {
    const { page } = await abrir({ width, height });
    try {
      const [bloqueado, livre] = await page.locator("#aff-payouts tr").all();
      const texto = await bloqueado.innerText();
      assert.doesNotMatch(texto, /Pagar via Pix|Marcar pago/);
      assert.match(texto, /Pagamento já realizado/);
      assert.match(texto, /Rejeitar/);
      assert.match(texto, /reembolso em análise/);
      assert.match(await livre.innerText(), /Pagar via Pix[\s\S]*Marcar pago[\s\S]*Rejeitar/);  // positivo
      assert.doesNotMatch(await livre.innerText(), /Pagamento já realizado/);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), true);
    } finally { await page.close(); }
  });
}

// mutação: tirar `renderRefundBlocks` de `loadAffiliates`, ou o `esc` da justificativa
test("os blocos mostram pendência, recuperação e histórico, sem HTML cru", async () => {
  const { page } = await abrir();
  try {
    const [parcial, total] = await page.locator("#aff-reviews tr").all();
    assert.match(await parcial.innerText(), /Revisar valor/);
    assert.doesNotMatch(await total.innerText(), /Revisar valor/);  // `total` não fecha com nota
    const rec = await page.locator("#aff-recoveries").innerText();
    assert.match(rec, /40,00/);
    assert.match(rec, /Comissão já paga/);
    assert.match(rec, /E1234567/);
    assert.match(rec, /05\/11\/2026/);
    assert.match(await page.locator("#aff-refund-history").innerText(), /<b>sem<\/b> retorno/);
    assert.equal(await page.locator("#aff-refund-history b").count(), 0);
  } finally { await page.close(); }
});

// mutação: mandar reais em vez de centavos, esquecer o `refunded_cents` que o admin viu, ou tirar o `margin: auto` de `.aff-form`: o diálogo cola no canto
for (const [nome, width, height] of TAMANHOS) {
  test(`revisão: valor aprovado vira centavos, com o devolvido que o admin viu (${nome})`, async () => {
    const { page, posts } = await abrir({ width, height });
    try {
      await page.getByRole("button", { name: "Revisar valor" }).click();
      const dialogo = page.locator("#aff-form");
      assert.equal(await dialogo.isVisible(), true);
      assert.match(await dialogo.innerText(), /não escreva nome nem documento/i);
      assert.equal(await page.locator("#aff-f-ref").isVisible(), false);
      const caixa = await dialogo.boundingBox();
      assert.ok(caixa.x >= 0 && caixa.x + caixa.width <= width && caixa.height <= height, JSON.stringify(caixa));
      const larguraUtil = await page.evaluate(() => document.documentElement.clientWidth);
      assert.ok(Math.abs(caixa.x - (larguraUtil - caixa.width) / 2) <= 1, `centrado: ${JSON.stringify(caixa)}`);
      assert.ok(Math.abs(caixa.y - (height - caixa.height) / 2) <= 1, `centrado: ${JSON.stringify(caixa)}`);
      await page.locator("#aff-f-value").fill("60.10");
      await page.locator("#aff-f-just").fill("Ajuste pelo valor devolvido");
      await page.locator("#aff-form-send").click();
      await page.waitForFunction(() => !document.getElementById("aff-form").open);
      assert.deepEqual(posts, [{ url: "/admin/api/affiliates/refund-reviews/1/approve", corpo: { approved_cents: 6010, refunded_cents: 30000, justification: "Ajuste pelo valor devolvido" } }]);
    } finally { await page.close(); }
  });
}

// mutação: tirar o `max` do campo: valor acima da comissão sai para o servidor
test("revisão: valor acima da comissão nem sai do navegador", async () => {
  const { page, posts } = await abrir();
  try {
    await page.getByRole("button", { name: "Revisar valor" }).click();
    await page.locator("#aff-f-value").fill("100.01");
    await page.locator("#aff-f-just").fill("Ajuste pelo valor devolvido");
    await page.locator("#aff-form-send").click();
    await page.waitForTimeout(150);
    assert.deepEqual(posts, []);
    assert.equal(await page.locator("#aff-form").evaluate((d) => d.open), true);
  } finally { await page.close(); }
});

// mutação: tirar um dos três campos de `openRecordedForm`
test("'Pagamento já realizado' pede os três campos e manda para a rota certa", async () => {
  const { page, posts } = await abrir();
  try {
    await page.getByRole("button", { name: "Pagamento já realizado" }).click();
    await page.locator("#aff-form-send").click();
    await page.waitForTimeout(100);
    assert.deepEqual(posts, []);  // justificativa e referência vazias
    await page.locator("#aff-f-ref").fill("E1234567");
    await page.locator("#aff-f-just").fill("Pix feito antes do reembolso");
    await page.locator("#aff-form-send").click();
    await page.waitForFunction(() => !document.getElementById("aff-form").open);
    assert.equal(posts[0].url, "/admin/api/affiliates/payouts/7/payment-recorded");
    assert.deepEqual(Object.keys(posts[0].corpo).sort(), ["justification", "paid_on", "receipt_ref"]);
    assert.match(posts[0].corpo.paid_on, /^\d{4}-\d{2}-\d{2}$/);
  } finally { await page.close(); }
});

// mutação: mandar data e referência na dispensa: a rota recusa com 422
test("encerrar recuperação: dispensada esconde data e referência, e não as manda", async () => {
  const { page, posts } = await abrir();
  try {
    await page.getByRole("button", { name: "Encerrar" }).click();
    await page.locator("#aff-f-result").selectOption("waived");
    assert.equal(await page.locator("#aff-f-ref").isVisible(), false);
    assert.equal(await page.locator("#aff-f-date").isVisible(), false);
    await page.locator("#aff-f-just").fill("Valor pequeno, sem cobrança");
    await page.locator("#aff-form-send").click();
    await page.waitForFunction(() => !document.getElementById("aff-form").open);
    assert.deepEqual(posts, [{ url: "/admin/api/affiliates/refund-reviews/3/close-recovery", corpo: { result: "waived", justification: "Valor pequeno, sem cobrança" } }]);
  } finally { await page.close(); }
});

// mutação: fechar o formulário sem olhar `res.ok`: o admin acha que salvou
test("409 fica no formulário, com a mensagem do servidor", async () => {
  const { page } = await abrir({ resposta: { status: 409, body: JSON.stringify({ detail: "A pendência mudou. Recarregue antes de decidir." }) } });
  try {
    await page.getByRole("button", { name: "Revisar valor" }).click();
    await page.locator("#aff-f-value").fill("60");
    await page.locator("#aff-f-just").fill("Ajuste pelo valor devolvido");
    await page.locator("#aff-form-send").click();
    await page.locator("#aff-form-error").filter({ hasText: "A pendência mudou" }).waitFor();
    assert.equal(await page.locator("#aff-form").evaluate((d) => d.open), true);
  } finally { await page.close(); }
});

// mutação: tirar o `loadAffiliates()` do 409 em `payoutAction`: o admin segue vendo o saque velho com botões que o servidor recusa
test("'Marcar pago' recusado com 409 recarrega a lista", async () => {
  const { page, leituras } = await abrir();
  const alertas = [];
  page.on("dialog", (d) => { if (d.type() === "alert") alertas.push(d.message()); return d.accept(); });
  await page.route("**/admin/api/affiliates/payouts/8/paid", (r) => r.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ detail: "Este saque tem reembolso de cliente em análise." }) }));
  try {
    const antes = leituras.length;
    await page.locator("#aff-payouts tr").nth(1).getByRole("button", { name: "Marcar pago" }).click();
    const fim = Date.now() + 4000;
    while (leituras.length === antes && Date.now() < fim) await page.waitForTimeout(50);
    assert.equal(leituras.length, antes + 1);
    assert.deepEqual(alertas, ["Este saque tem reembolso de cliente em análise."]);
  } finally { await page.close(); }
});

// mutação: voltar o cabeçalho a "A pagar": a soma inclui comissão em revisão e saque bloqueado
test("a coluna de saldo diz que inclui o que está em revisão", async () => {
  const { page } = await abrir();
  try {
    const cabecalho = await page.locator("#aff-list").locator("xpath=../thead").innerText();
    assert.match(cabecalho, /A PAGAR OU EM REVISÃO/i);
  } finally { await page.close(); }
});

const ORFA = {
  ...VISAO,
  payouts: [saque(9, { code: "EVA2026", email: null, pix_key: null, orphan: true }), saque(8)],
  refund_recoveries: [pendencia(3, { status: "recovery", recovery_cents: 4000, origem: "paid_before_refund", commission_id: null, commission_cents: null, commission_status: null, affiliate_deleted: true })],
  commission_checks: { count: 2, oldest: "2026-11-01T12:00:00+00:00" },
};

// mutação: tirar o `|| p.orphan` de `pagar`: o saque de quem excluiu a conta volta a mostrar "Pagar via Pix" e "Marcar pago"
for (const [nome, width, height] of TAMANHOS) {
  test(`saque órfão: selo 'conta excluída', só registrar ou rejeitar (${nome})`, async () => {
    const { page } = await abrir({ width, height, visao: ORFA });
    try {
      const [orfao, vivo] = await page.locator("#aff-payouts tr").all();
      const texto = await orfao.innerText();
      assert.match(texto, /conta excluída/);
      assert.doesNotMatch(texto, /Pagar via Pix|Marcar pago/);
      assert.match(texto, /Pagamento já realizado[\s\S]*Rejeitar/);
      assert.doesNotMatch(await vivo.innerText(), /conta excluída/);  // positivo
      assert.match(await page.locator("#aff-recoveries").innerText(), /conta excluída/);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), true);
    } finally { await page.close(); }
  });
}

// mutação: tirar o texto de `#aff-checks` em `renderRefundBlocks`: queda longa da Stripe some da tela
test("comissões aguardando conferência aparecem com a mais antiga; sem nenhuma, a linha some", async () => {
  const com = await abrir({ visao: ORFA });
  try {
    assert.equal(await com.page.locator("#aff-checks").innerText(), "2 comissões aguardam conferência na Stripe, a mais antiga de 01/11/2026");
  } finally { await com.page.close(); }
  const sem = await abrir();
  try {
    assert.equal(await sem.page.locator("#aff-checks").isVisible(), false);
  } finally { await sem.page.close(); }
});
