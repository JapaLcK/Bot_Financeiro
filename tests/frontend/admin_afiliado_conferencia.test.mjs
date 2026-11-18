/**
 * Comissões em conferência na Stripe no /admin (#1538, PR 1): a lista, Estornar, Concluir conferência e o histórico.
 * DOM renderizado pelo caminho real (`loadAffiliates` → fetch → render), em desktop e celular. As ações e o porquê
 * de cada recusa vêm do servidor: aqui se mede que a tela mostra o que ele mandou, não a regra. Em cada teste, o
 * comentário de cima diz qual mutação o deixa vermelho.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { startServer } from "./_server.mjs";
import { chromium } from "playwright";

let ORIGIN, server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer()); browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

const NO_SAQUE = "A comissão está no saque 7, ainda aberto. Rejeite o saque se o Pix não saiu (ela volta marcada e pode ser estornada) ou registre \"Pagamento já realizado\" se saiu.";
const JA_PAGA = "Comissão já paga: reembolso confirmado vira recuperação.";
const linha = (id, extra) => ({ id, code: "ANA2026", amount_cents: 10000, status: "pending", payout_id: null, payout_status: null, marcada: true,
  marked_at: "2026-11-02T10:00:00.123456+00:00", marked_on: "2026-11-02T10:00:00.123456+00:00", tried_at: "2026-11-03T10:00:00+00:00",
  reason: "sem_cobranca", has_stripe_invoice: true, in_review: false, in_recovery: false, actions: { reverse: null }, ...extra });
const LISTA = [
  linha(11, { actions: { release: null, reverse: null } }),
  linha(12, { has_stripe_invoice: false, payout_id: 7, payout_status: "requested", reason: "disputa_aberta", actions: { reverse: NO_SAQUE } }),
  linha(13, { status: "paid", payout_id: 5, payout_status: "paid", reason: "forma_inesperada", in_recovery: true,
    actions: { conclude: null, reverse: JA_PAGA } }),
  linha(14, { status: "paid", payout_id: 5, payout_status: "paid", reason: "disputa_aberta",
    actions: { conclude: "Disputa aberta: a conferência fecha sozinha quando a disputa terminar.", reverse: JA_PAGA } }),
  linha(15, { code: "<b>EVA</b>", reason: null, tried_at: null, marked_at: null, marked_on: "2026-11-01T10:00:00+00:00", in_review: true }),
  linha(16, { reason: "stripe_403", actions: { release: "Em revisão de reembolso: decida a pendência antes.", reverse: null } }),
];
const evento = (id, action, extra) => ({ id, review_id: null, action, actor: "lucas", before_cents: 10000, after_cents: 0, payout_id: null,
  event_on: null, receipt_ref: "ticket:SUP-1", justification: "Indicado pediu de volta", created_at: "2026-11-05T12:00:00+00:00",
  tipo: null, commission_id: 11, code: "ANA2026", ...extra });
const VISAO = {
  affiliates: [], payouts: [], refund_reviews: [], refund_recoveries: [],
  refund_history: [evento(1, "check_reversed", { receipt_ref: "<i>cn_1</i>" }), evento(2, "check_concluded", { after_cents: 10000 }),
    evento(3, "check_released", { after_cents: 10000 })],
  commission_checks: { count: 6, oldest: "2026-11-01T10:00:00+00:00" },
  commission_check_list: LISTA,
};

async function abrir({ width = 1280, height = 900, visao = VISAO, resposta = { status: 200, body: "{\"ok\":true}" } } = {}) {
  const page = await browser.newPage({ viewport: { width, height } });
  const posts = [], leituras = [];
  await page.route("**/admin/api/**", (r) => r.fulfill({ contentType: "application/json", body: "{}" }));
  await page.route("**/admin/api/affiliates", (r) => { leituras.push(Date.now()); return r.fulfill({ contentType: "application/json", body: JSON.stringify(visao) }); });
  await page.route(/\/admin\/api\/affiliates\/commissions\/\d+\/check-(reverse|conclude|release)$/, (r) => {
    posts.push({ url: new URL(r.request().url()).pathname, corpo: r.request().postDataJSON() });
    return r.fulfill({ contentType: "application/json", ...resposta });
  });
  await page.goto(`${ORIGIN}/admin-dashboard.html`, { waitUntil: "domcontentloaded" });
  await page.evaluate(() => window.loadAffiliates());
  await page.locator("#aff-check-list tr").first().waitFor({ state: "attached" });
  return { page, posts, leituras };
}
const TAMANHOS = [["desktop", 1280, 900], ["celular", 390, 844], ["320", 320, 568]];
const linhas = (page) => page.locator("#aff-check-list tr").all();

// mutação: tirar o `porque === null` do render (todo motivo de recusa vira botão), trocar o rótulo do motivo,
// ou tirar a guarda de largura (`.table-wrap`/`as-cards`): rolagem horizontal no celular
for (const [nome, width, height] of TAMANHOS) {
  test(`lista: motivo, datas, selos e só as ações que o servidor permite (${nome})`, async () => {
    const { page } = await abrir({ width, height });
    try {
      const [solta, noSaque, paga, pagaDisputa, nova, s403] = await linhas(page);
      const t = await solta.innerText();
      assert.match(t, /ANA2026[\s\S]*R\$ 100,00[\s\S]*fora do saque[\s\S]*02\/11\/2026[\s\S]*03\/11\/2026[\s\S]*Pago com saldo de crédito/);
      assert.deepEqual(await solta.getByRole("button").allInnerTexts(), ["Liberar", "Estornar"]);
      // "Ver fatura": link para a rota do admin, sem o id da fatura na tela
      const ver = solta.getByRole("link", { name: "Ver fatura" });
      assert.equal(await ver.getAttribute("href"), "/admin/api/affiliates/commissions/11/stripe-invoice");
      assert.equal(await ver.getAttribute("rel"), "noopener noreferrer");
      // comissão Pix (`has_stripe_invoice` falso): sem link, que o servidor responderia 404
      assert.equal(await noSaque.getByRole("link", { name: "Ver fatura" }).count(), 0);
      assert.match(await noSaque.innerText(), /no saque 7[\s\S]*Disputa aberta[\s\S]*ainda aberto\. Rejeite o saque/);
      assert.equal(await noSaque.getByRole("button").count(), 0);
      const tp = await paga.innerText();
      assert.match(tp, /já paga[\s\S]*recuperação aberta[\s\S]*Pago fora do cartão/);
      assert.deepEqual(await paga.getByRole("button").allInnerTexts(), ["Concluir conferência"]);
      assert.doesNotMatch(tp, /Liberar/);
      assert.match(tp, /Comissão já paga: reembolso confirmado vira recuperação/);
      assert.equal(await pagaDisputa.getByRole("button").count(), 0);
      assert.match(await pagaDisputa.innerText(), /a conferência fecha sozinha/);
      const tn = await nova.innerText();
      assert.match(tn, /<b>EVA<\/b>[\s\S]*em revisão de reembolso[\s\S]*01\/11\/2026[\s\S]*ainda não conferida[\s\S]*Aguardando a 1ª conferência/);
      assert.equal(await nova.locator("b").count(), 0);
      assert.match(await s403.innerText(), /Stripe recusou a consulta \(403\)[\s\S]*Em revisão de reembolso: decida a pendência antes/);
      assert.deepEqual(await s403.getByRole("button").allInnerTexts(), ["Estornar"]);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), true);
      for (const caixa of await page.locator("#aff-check-list button").evaluateAll((bs) => bs.map((b) => b.getBoundingClientRect().toJSON()))) {
        assert.ok(caixa.left >= 0 && caixa.right <= width, `botão passa da tela: ${JSON.stringify(caixa)}`);
        if (width <= 820) assert.ok(caixa.height >= 44, `alvo de toque baixo: ${caixa.height}`);
      }
    } finally { await page.close(); }
  });
}

// mutação: reler `affConferencia` no envio (anula o 409), mandar a rota errada, ou tirar `refRotulo`
for (const [nome, width, height] of TAMANHOS) {
  test(`estornar: evidência obrigatória, e o corpo leva a versão vista na abertura (${nome})`, async () => {
    const { page, posts } = await abrir({ width, height });
    try {
      await (await linhas(page))[0].getByRole("button", { name: "Estornar" }).click();
      const dialogo = page.locator("#aff-form");
      assert.equal(await dialogo.isVisible(), true);
      assert.match(await dialogo.innerText(), /Estornar comissão de R\$ 100,00[\s\S]*Sai do saldo do afiliado de vez/);
      assert.match(await dialogo.innerText(), /Evidência \(id do ticket, da nota de crédito ou do comprovante\)/);
      assert.equal(await page.locator("#aff-f-date").isVisible(), false);
      const caixa = await dialogo.boundingBox();
      assert.ok(caixa.x >= 0 && caixa.x + caixa.width <= width && caixa.height <= height, JSON.stringify(caixa));
      await page.locator("#aff-f-just").fill("Indicado pediu de volta pelo suporte");
      await page.locator("#aff-form-send").click();  // sem evidência
      await page.locator("#aff-f-ref").fill("ticket SUP 1");  // espaço: fora do `pattern`
      await page.locator("#aff-form-send").click();
      await page.waitForTimeout(150);
      assert.deepEqual(posts, []);
      // a lista recarregada no meio não muda o que vai: é a versão da abertura
      await page.evaluate(() => { const r = affConferencia.get(11); r.marked_at = "2099-01-01T00:00:00+00:00"; r.amount_cents = 7000; });
      await page.locator("#aff-f-ref").fill("ticket:SUP-1");
      await page.locator("#aff-form-send").click();
      await page.waitForFunction(() => !document.getElementById("aff-form").open);
      assert.deepEqual(posts, [{ url: "/admin/api/affiliates/commissions/11/check-reverse", corpo: {
        marked_at: "2026-11-02T10:00:00.123456+00:00", reason: "sem_cobranca", amount_cents: 10000, evidence: "ticket:SUP-1",
        justification: "Indicado pediu de volta pelo suporte" } }]);
    } finally { await page.close(); }
  });
}

// mutação: trocar a ação do botão da paga, ou deixar o rótulo da evidência no formulário seguinte
test("concluir: rota própria, e o formulário seguinte volta ao rótulo da referência", async () => {
  const { page, posts } = await abrir({ visao: { ...VISAO, payouts: [{ id: 8, code: "ANA2026", email: "a@x.com", pix_key: "a@pix.com",
    amount_cents: 17000, status: "requested", note: null, requested_at: "2026-11-05T12:00:00+00:00", blocked: true }] } });
  try {
    await (await linhas(page))[2].getByRole("button", { name: "Concluir conferência" }).click();
    assert.match(await page.locator("#aff-form-hint").innerText(), /nada de saldo, saque ou recuperação muda/);
    await page.locator("#aff-f-ref").fill("cn_1Qx9");
    await page.locator("#aff-f-just").fill("Pago por boleto fora da Stripe");
    await page.locator("#aff-form-send").click();
    await page.waitForFunction(() => !document.getElementById("aff-form").open);
    assert.deepEqual(posts, [{ url: "/admin/api/affiliates/commissions/13/check-conclude", corpo: {
      marked_at: "2026-11-02T10:00:00.123456+00:00", reason: "forma_inesperada", amount_cents: 10000, evidence: "cn_1Qx9",
      justification: "Pago por boleto fora da Stripe" } }]);
    await page.getByRole("button", { name: "Pagamento já realizado" }).click();
    assert.match(await page.locator("#aff-form").innerText(), /Referência do comprovante/);
  } finally { await page.close(); }
});

// mutação: tirar `release` de AFF_DECISAO (o botão some do clique) ou mandar para a rota do estorno
for (const [nome, width, height] of TAMANHOS) {
  test(`liberar: rota própria e o texto do formulário (${nome})`, async () => {
    const { page, posts } = await abrir({ width, height });
    try {
      await (await linhas(page))[0].getByRole("button", { name: "Liberar" }).click();
      assert.match(await page.locator("#aff-form").innerText(), /Liberar comissão de R\$ 100,00[\s\S]*Entra no saldo disponível do afiliado/);
      const caixa = await page.locator("#aff-form").boundingBox();
      assert.ok(caixa.x >= 0 && caixa.x + caixa.width <= width && caixa.height <= height, JSON.stringify(caixa));
      await page.locator("#aff-f-ref").fill("ticket:SUP-1");
      await page.locator("#aff-f-just").fill("Fatura da Stripe não existe mais");
      await page.locator("#aff-form-send").click();
      await page.waitForFunction(() => !document.getElementById("aff-form").open);
      assert.deepEqual(posts.map((p) => p.url), ["/admin/api/affiliates/commissions/11/check-release"]);
    } finally { await page.close(); }
  });
}

// mutação: tirar o `loadAffiliates` do 409 em `submitAffForm`, ou fechar o diálogo no erro
for (const [status, recarrega] of [[409, true], [422, false]]) {
  test(`resposta ${status}: o erro fica no formulário${recarrega ? " e a lista recarrega" : ""}`, async () => {
    const detalhe = status === 409 ? "A comissão mudou: recarregue antes de decidir." : "Evidência inválida: de 4 a 120 caracteres, só letras, números e . _ : / -";
    const { page, leituras } = await abrir({ resposta: { status, body: JSON.stringify({ detail: detalhe }) } });
    try {
      await (await linhas(page))[0].getByRole("button", { name: "Estornar" }).click();
      await page.locator("#aff-f-ref").fill("ticket:SUP-1");
      await page.locator("#aff-f-just").fill("Indicado pediu de volta pelo suporte");
      const antes = leituras.length;
      await page.locator("#aff-form-send").click();
      await page.locator("#aff-form-error").filter({ hasText: detalhe }).waitFor();
      await page.waitForTimeout(150);
      assert.equal(await page.locator("#aff-form").evaluate((d) => d.open), true);
      assert.equal(leituras.length > antes, recarrega);
    } finally { await page.close(); }
  });
}

// mutação: tirar as três ações novas de `AFF_ACAO`, ou o `esc` da referência
test("histórico: as três decisões com a evidência, sem HTML cru; título novo", async () => {
  const { page } = await abrir();
  try {
    const texto = await page.locator("#aff-refund-history").innerText();
    assert.match(texto, /Estornada na conferência[\s\S]*<i>cn_1<\/i>/);
    assert.match(texto, /Conferência concluída \(já paga\)[\s\S]*ticket:SUP-1/);
    assert.match(texto, /Liberada na conferência/);
    assert.equal(await page.locator("#aff-refund-history i").count(), 0);
    assert.match(await page.locator("h2", { hasText: "Histórico" }).innerText(), /Histórico de reembolsos e conferências/);
  } finally { await page.close(); }
});

// mutação: tirar o aviso de corte de `renderRefundBlocks`: com mais de 100, a tela não diz que falta linha
test("#aff-checks fica no bloco da conferência e avisa quando a lista corta", async () => {
  const { page } = await abrir({ visao: { ...VISAO, commission_checks: { count: 250, oldest: "2026-11-01T10:00:00+00:00" } } });
  try {
    assert.equal(await page.locator("#aff-checks").innerText(),
      "250 comissões aguardam conferência na Stripe, a mais antiga de 01/11/2026. Aqui, as 6 mais antigas de 250");
    assert.equal(await page.locator("#aff-checks + .table-wrap #aff-check-list").count(), 1);
  } finally { await page.close(); }
  const vazio = await abrir({ visao: { ...VISAO, commission_checks: { count: 0, oldest: null }, commission_check_list: [] } });
  try {
    assert.match(await vazio.page.locator("#aff-check-list").innerText(), /Nenhuma comissão em conferência/);
  } finally { await vazio.page.close(); }
});

// mutação: tirar o `aria-label` dos botões ou o texto do `<th>`: com várias linhas, o leitor de tela ouve só
// "Estornar, botão" sem saber de qual comissão
test("acessibilidade: cada botão diz a comissão e o afiliado, e a coluna de ações tem nome", async () => {
  const { page } = await abrir();
  try {
    assert.equal(await page.getByRole("button", { name: "Estornar: comissão 11 de ANA2026, R$ 100,00", exact: true }).count(), 1);
    assert.equal(await page.getByRole("button", { name: "Concluir conferência: comissão 13 de ANA2026, R$ 100,00", exact: true }).count(), 1);
    assert.equal(await page.getByRole("button", { name: /^Estornar: comissão 15 de <b>EVA<\/b>/ }).count(), 1);  // escapado, não HTML
    assert.equal(await page.locator("#aff-check-list b").count(), 0);
    const cabecalho = page.locator("table:has(#aff-check-list) thead th").last();
    assert.equal((await cabecalho.textContent()).trim(), "Ações");
  } finally { await page.close(); }
});
