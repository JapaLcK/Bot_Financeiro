/**
 * Os cadernos extras no modal do Pix anual da /precos (frontend/pix-extras.js + bump-caixas.js).
 *
 * O modal pede o CPF/CNPJ e, com oferta no `GET /billing/pix-extras`, desenha as mesmas caixas da /assinar
 * dentro do formulário. O que a tela mostra marcado é o que vai no `POST /billing/pix/checkout` (`extras`, só
 * ids), inclusive no reenvio da migração; o QR mostra o `total_cents` e "Inclui N caderno(s)".
 *
 * Controles (CLAUDE.md §3):
 *   · negativo — tire o `extras:` do corpo no `pixEnviar` e E1, E2, E3 e E5 ficam vermelhos; tire o
 *     `pixExtrasMontar` do `pixFormulario` e E1 fica vermelho; troque `total_cents ?? amount_cents` por
 *     `amount_cents` no pix-poll.js e E1 fica vermelho; tire o `|| antes.disabled` do GET e E6 fica vermelho;
 *     tire o filtro do ramo da migração no `pixExtrasRecusa` e E5b fica vermelho; tire o `pixExtrasTravar(true)` do
 *     `pixEnviar` e E8 fica vermelho;
 *   · positivo — E4 (GET falhando: o Pix do plano sai igual, `extras: []`, sem "Inclui") e E6 (o GET que chega
 *     com o POST em voo não desenha caixa que o pedido não leva).
 *
 * O que NÃO alcança: o backend real (tudo é `page.route`), o Stripe de verdade e a área segura do overlay
 * (`env(safe-area-inset-*)` vale 0 no headless).
 *
 * Rodar:  node --test tests/frontend/precos_pix_extras.test.mjs
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { startServer } from "./_server.mjs";
import { chromium } from "playwright";

let ORIGIN, server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer());
                     browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

const CPF = "11122233344";
const QR_IMG = "data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==";
// A forma do `extras_assinar.para_tela` + `price` (core/services/pix_extras.py:oferta). O nome do 1º traz
// marcação: tem de chegar à tela como TEXTO.
const OFERTA = [
  { posicao: 1, price: "price_a", nome: "Saia do <b>vermelho</b>", descricao: "Diagnóstico das dívidas.",
    imagem: "https://files.stripe.com/capa.png", valor_centavos: 1190, no_carrinho: false },
  { posicao: 2, price: "price_b", nome: "Primeira reserva", descricao: "Quanto guardar por mês.",
    imagem: null, valor_centavos: 1990, no_carrinho: false },
  { posicao: 3, price: "price_c", nome: "Cartão sem susto", descricao: "Fatura e juros sem mistério.",
    imagem: "javascript:alert(1)", valor_centavos: 990, no_carrinho: false },
];
const QR = (extra = {}) => ({ public_token: "tok_x", qr_payload: "000201pix", qr_image: QR_IMG,
  amount_cents: 19900, credit_cents: 0, total_cents: 19900, starts_at: null, plan: "plus", ...extra });

/**
 * Abre a /precos, o CTA do Plus no anual e o formulário. `extras`: corpo do GET, `null` = 500, "aborta" = rede
 * fora, ou fn() → Promise (o teste decide quando ele volta). `pix`: lista de [status, corpo] do POST, uma por
 * chamada (a última se repete).
 */
async function abrirForm({ extras = { extras: OFERTA, selecao: [] }, pix = [[200, QR()]],
                           viewport = { width: 1280, height: 800 } } = {}) {
  const page = await browser.newPage({ viewport });
  const posts = [];
  const json = (r, status, corpo) => r.fulfill({ status, contentType: "application/json", body: JSON.stringify(corpo) });
  await page.route("**/auth/me", (r) => json(r, 200, { user_id: 42, needs_plan_selection: true }));
  await page.route("**/billing/plans-config", (r) => json(r, 200, {
    essencial_available: true, plus_available: true, pro_available: true, pix_annual_available: true }));
  await page.route("**/billing/subscription", (r) => json(r, 200, { active: false }));
  await page.route("**/billing/pix/*", (r) => json(r, 200, { status: "pending" }));   // o poll
  await page.route("**/billing/pix/checkout", (r) => {
    posts.push(JSON.parse(r.request().postData() || "{}"));
    const [status, corpo] = pix[Math.min(posts.length, pix.length) - 1];
    return json(r, status, corpo);
  });
  await page.route("**/billing/pix-extras", async (r) => {
    if (extras === "aborta") return r.abort();
    if (extras === null) return json(r, 500, { error: "x" });
    return json(r, 200, typeof extras === "function" ? await extras() : extras);
  });
  await page.goto(`${ORIGIN}/precos.html`);
  await page.waitForSelector("#plans-v2 .plan");
  await page.waitForTimeout(600);        // loadPlansState = 2 awaits de rede
  await page.click("#cycle-annual");
  await page.click('[data-pix-cta="plus"]');
  await page.waitForSelector(".pix-doc");
  await page.fill(".pix-doc", CPF);
  return { page, posts };
}

const caixas = (page) => page.locator(".pix-form .pp-bump input[type=checkbox]");
const marcadas = (page) => caixas(page).evaluateAll((l) => l.map((c) => c.checked));
const enviar = (page) => page.click('.pix-form button[type="submit"]');
async function esperarPosts(page, posts, n) {
  for (let i = 0; i < 100 && posts.length < n; i++) await page.waitForTimeout(50);
  assert.equal(posts.length, n, `POSTs: ${posts.length}`);
}

test("E1: as caixas no formulário; as marcadas vão no POST; o QR mostra o total e quantos cadernos", async () => {
  const { page, posts } = await abrirForm({ pix: [[200, QR({ total_cents: 19900 + 1190 + 990 })]] });
  await caixas(page).first().waitFor();
  assert.equal(await caixas(page).count(), 3);
  const caixa = await page.$eval(".pix-form .pp-bump", (b) => ({
    texto: b.textContent, b: b.querySelectorAll("b b").length, imgs: [...b.querySelectorAll("img")].map((i) => i.src) }));
  assert.match(caixa.texto, /Leva junto\? Só nesta compra/);
  assert.match(caixa.texto, /Saia do <b>vermelho<\/b>/, "o nome do Stripe não entrou como texto");
  assert.equal(caixa.b, 0, "o nome do Stripe virou marcação");
  assert.deepEqual(caixa.imgs, ["https://files.stripe.com/capa.png"], "capa fora de https entrou");
  assert.deepEqual(await marcadas(page), [false, false, false]);
  await caixas(page).nth(0).check();
  await caixas(page).nth(2).check();
  assert.equal(await page.$eval(".pix-form .pp-bump", (b) => b.classList.contains("marcado")), true);
  await enviar(page);
  await esperarPosts(page, posts, 1);
  assert.deepEqual(posts[0].extras, ["price_a", "price_c"]);
  assert.equal(posts[0].cpf_cnpj, CPF);
  await page.waitForSelector(".pix-qr");
  const box = await page.$eval(".pix-box", (b) => ({ titulo: b.querySelector("h3").textContent, texto: b.textContent,
    caixas: b.querySelectorAll(".pp-bump").length, valores: [...document.querySelectorAll("input")].map((i) => i.value) }));
  assert.match(box.titulo, /R\$\s*220,80/, `título sem o total: ${box.titulo}`);
  assert.match(box.texto, /Inclui 2 cadernos\./);
  assert.equal(box.caixas, 0, "as caixas sobraram na tela do QR");
  assert.ok(!box.valores.includes(CPF), "o CPF sobreviveu no value de um <input>");
  await page.close();
});

test("E2: Pix pendente com cadernos (Q6): reabrir vem marcado como a cobrança, e 1 caderno é singular", async () => {
  const { page, posts } = await abrirForm({ extras: { extras: OFERTA, selecao: ["price_b", "price_sumiu"] },
                                            pix: [[200, QR({ total_cents: 21890 })]] });
  await caixas(page).first().waitFor();
  assert.deepEqual(await marcadas(page), [false, true, false]);
  await enviar(page);
  await esperarPosts(page, posts, 1);
  assert.deepEqual(posts[0].extras, ["price_b"], "id fora da oferta foi no POST");
  await page.waitForSelector(".pix-qr");
  assert.match(await page.$eval(".pix-box", (b) => b.textContent), /Inclui 1 caderno\./);
  await page.close();
});

test("E3: 409 extras_indisponiveis redesenha com a oferta nova e mantém marcados só os que valem", async () => {
  const nova = [OFERTA[1], OFERTA[2]];
  const { page, posts } = await abrirForm({ pix: [[409, { detail: { error: "extras_indisponiveis", extras: nova } }],
                                                  [200, QR({ total_cents: 21890 })]] });
  await caixas(page).first().waitFor();
  await caixas(page).nth(0).check();
  await caixas(page).nth(1).check();
  await enviar(page);
  await esperarPosts(page, posts, 1);
  await page.waitForFunction(() => document.querySelectorAll(".pix-form .pp-bump input").length === 2);
  assert.deepEqual(await marcadas(page), [true, false], "a caixa válida desmarcou ou a nova veio marcada");
  const t = await page.$eval(".pix-form", (f) => f.textContent);
  assert.match(t, /Os cadernos disponíveis mudaram/);
  assert.doesNotMatch(t, /Saia do/, "a caixa que saiu da oferta continuou na tela");
  assert.equal(await page.locator(".pix-qr").count(), 0, "apareceu QR depois do 409");
  assert.equal(await page.locator('.pix-form button[type="submit"]').isDisabled(), false, "o botão ficou travado");
  await enviar(page);
  await esperarPosts(page, posts, 2);
  assert.deepEqual(posts[1].extras, ["price_b"]);
  await page.waitForSelector(".pix-qr");
  await page.close();
});

for (const [nome, extras] of [["500", null], ["rede fora", "aborta"], ["oferta vazia", { extras: [], selecao: [] }]]) {
  test(`E4: GET ${nome} ⇒ sem caixas, e o Pix do plano sai igual`, async () => {
    const { page, posts } = await abrirForm({ extras });
    await page.waitForTimeout(300);
    assert.equal(await caixas(page).count(), 0);
    assert.equal(await page.locator(".pix-form .pp-bump:not([hidden])").count(), 0, "sobrou caixa vazia visível");
    await enviar(page);
    await esperarPosts(page, posts, 1);
    assert.deepEqual(posts[0].extras, []);
    await page.waitForSelector(".pix-qr");
    const box = await page.$eval(".pix-box", (b) => ({ titulo: b.querySelector("h3").textContent, texto: b.textContent }));
    assert.match(box.titulo, /R\$\s*199,00/);
    assert.doesNotMatch(box.texto, /Inclui/);
    await page.close();
  });
}

test("E5: a migração do cartão leva os mesmos cadernos no reenvio", async () => {
  const { page, posts } = await abrirForm({ pix: [
    [409, { detail: { error: "stripe_active", current_period_end: "2026-12-05" } }], [200, QR({ total_cents: 21090 })]] });
  await caixas(page).first().waitFor();
  await caixas(page).nth(0).check();
  await enviar(page);
  await esperarPosts(page, posts, 1);
  await page.getByRole("button", { name: "Continuar no Pix" }).click();
  await esperarPosts(page, posts, 2);
  assert.deepEqual(posts[1].extras, ["price_a"]);
  assert.equal(posts[1].confirm_cancel_stripe, true);
  await page.waitForSelector(".pix-qr");
  assert.match(await page.$eval(".pix-box", (b) => b.textContent), /Inclui 1 caderno\./);
  await page.close();
});

// A caixa da migração já tirou o formulário da tela: o 409 dos cadernos não tem onde redesenhar. A seleção
// encolhe para a oferta nova, o aviso vai para o toast, e só um NOVO clique cobra (o QR diz quantos cadernos).
test("E5b: 409 extras_indisponiveis no reenvio da migração: toast, nada cobrado, o próximo clique leva só os válidos", async () => {
  const { page, posts } = await abrirForm({ pix: [
    [409, { detail: { error: "stripe_active", current_period_end: "2026-12-05" } }],
    [409, { detail: { error: "extras_indisponiveis", extras: [OFERTA[1]] } }], [200, QR({ total_cents: 21890 })]] });
  await caixas(page).first().waitFor();
  await caixas(page).nth(0).check();
  await caixas(page).nth(1).check();
  await enviar(page);
  await esperarPosts(page, posts, 1);
  const continuar = page.getByRole("button", { name: "Continuar no Pix" });
  await continuar.click();
  await esperarPosts(page, posts, 2);
  await page.waitForFunction(() => /cadernos disponíveis mudaram/.test(document.getElementById("toast").textContent));
  assert.equal(await page.locator(".pix-qr").count(), 0, "apareceu QR depois do 409");
  await continuar.click();
  await esperarPosts(page, posts, 3);
  assert.deepEqual(posts[2].extras, ["price_b"]);
  assert.equal(posts[2].confirm_cancel_stripe, true);
  await page.waitForSelector(".pix-qr");
  assert.match(await page.$eval(".pix-box", (b) => b.textContent), /Inclui 1 caderno\./);
  await page.close();
});

test("E6: o GET que volta com o POST em voo não desenha caixa (o pedido em andamento não a leva)", async () => {
  let soltaGet, soltaPost;
  const get = new Promise((ok) => { soltaGet = ok; });
  const { page, posts } = await abrirForm({ extras: () => get.then(() => ({ extras: OFERTA, selecao: ["price_a"] })) });
  await page.route("**/billing/pix/checkout", async (r) => {
    posts.push(JSON.parse(r.request().postData() || "{}"));
    await new Promise((ok) => { soltaPost = ok; });
    return r.fulfill({ status: 400, contentType: "application/json", body: JSON.stringify({ detail: "Informe um CPF ou CNPJ válido." }) });
  });
  await enviar(page);
  await esperarPosts(page, posts, 1);
  soltaGet();
  await page.waitForTimeout(300);
  assert.equal(await caixas(page).count(), 0, "a caixa nasceu com o POST em voo");
  soltaPost();
  await page.waitForFunction(() => document.getElementById("pix-doc-erro")?.textContent);
  assert.deepEqual(posts[0].extras, []);
  await page.close();
});

// A página própria trava as caixas durante o envio (`trava` do pagamento-pagina.js); o modal do Pix também: o que
// se marca com o POST em voo não vai no pedido em andamento. O botão volta ⇒ elas voltam, com a mesma seleção.
for (const [status, corpo] of [[503, { detail: "Pix indisponível agora." }], [400, { detail: "Informe um CPF ou CNPJ válido." }]]) {
  test(`E8: POST em voo trava as caixas; o ${status} as destrava com a seleção de antes`, async () => {
    const { page, posts } = await abrirForm();
    let soltaPost;
    await page.route("**/billing/pix/checkout", async (r) => {
      posts.push(JSON.parse(r.request().postData() || "{}"));
      if (posts.length === 1) await new Promise((ok) => { soltaPost = ok; });
      return r.fulfill({ status, contentType: "application/json", body: JSON.stringify(corpo) });
    });
    await caixas(page).first().waitFor();
    await caixas(page).nth(0).check();
    await enviar(page);
    await esperarPosts(page, posts, 1);
    const travadas = () => caixas(page).evaluateAll((l) => l.map((c) => c.disabled));
    assert.deepEqual(await travadas(), [true, true, true], "as caixas ficaram livres com o POST em voo");
    await caixas(page).nth(1).click({ force: true });
    await page.locator(".pix-form .pp-linha").nth(0).click({ force: true });
    assert.deepEqual(await marcadas(page), [true, false, false], "o clique com o POST em voo mudou a seleção");
    soltaPost();
    await page.waitForFunction(() => !document.querySelector('.pix-form button[type="submit"]').disabled);
    assert.deepEqual(await travadas(), [false, false, false], "as caixas não destravaram com o botão");
    assert.deepEqual(await marcadas(page), [true, false, false]);
    await enviar(page);
    await esperarPosts(page, posts, 2);
    assert.deepEqual(posts[1].extras, ["price_a"]);
    await page.close();
  });
}

for (const viewport of [{ width: 1280, height: 800 }, { width: 390, height: 844 }]) {
  for (const n of [3, 1]) {
    test(`E7: ${viewport.width}x${viewport.height} com ${n} caixa(s): dentro do modal, sem rolagem lateral, botão alcançável`, async () => {
      const { page } = await abrirForm({ viewport, extras: { extras: OFERTA.slice(0, n), selecao: [] } });
      await caixas(page).first().waitFor();
      const m = await page.evaluate(() => {
        const r = (s) => document.querySelector(s).getBoundingClientRect();
        const box = r(".pix-box"), cx = r(".pix-form .pp-bump");
        return { box: [box.left, box.right], cx: [cx.left, cx.right], vw: innerWidth,
                 rolagem: document.documentElement.scrollWidth - innerWidth,
                 boxRola: document.querySelector(".pix-box").scrollWidth - document.querySelector(".pix-box").clientWidth };
      });
      console.log(`E7 ${viewport.width}x${viewport.height} n=${n}: ${JSON.stringify(m)}`);
      assert.ok(m.rolagem <= 0, `rolagem lateral da página: ${m.rolagem}px`);
      assert.ok(m.boxRola <= 0, `rolagem lateral dentro do modal: ${m.boxRola}px`);
      assert.ok(m.cx[0] >= m.box[0] && m.cx[1] <= m.box[1], `caixas fora do modal: ${JSON.stringify(m)}`);
      assert.ok(m.box[0] >= 0 && m.box[1] <= m.vw, `modal fora da tela: ${JSON.stringify(m)}`);
      const btn = page.locator('.pix-form button[type="submit"]');
      await btn.scrollIntoViewIfNeeded();
      const b = await btn.boundingBox();
      assert.ok(b && b.y >= 0 && b.y + b.height <= viewport.height, `botão fora da tela: ${JSON.stringify(b)}`);
      await page.close();
    });
  }
}
