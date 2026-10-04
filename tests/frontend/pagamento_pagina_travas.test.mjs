/**
 * Página de pagamento própria: travas, prazos, desmontagem, cupom aplicado e degradação (achados do Tester,
 * passada 1 do PR 3). O básico (montagem, ordem bump → confirm, XSS, layout) está em pagamento_pagina.test.mjs.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { abrir, tela, continuar, ME, HOSPEDADO, EXTRAS, PAGINA, EMBUTIDO, comPagina, abrirPagina as abrirP, caixas, marcadas, ate }
  from "./_assinar.mjs";

let browser;
before(async () => { browser = await chromium.launch(); });
after(async () => { await browser?.close(); });

const CHECKOUT = "/billing/create-checkout";
const BUMP = "/billing/checkout/bump";
const CONFIRM = "/__stripe/confirm";
const NEUTRO = "Não deu para atualizar seu pedido. Ele continua como estava.";
const abrirPagina = (opts) => abrirP(browser, opts);
const pausa = (ms) => new Promise((ok) => setTimeout(ok, ms));
const destravou = (page) => page.waitForFunction(() => !document.getElementById("pp-pagar").disabled);
/** Uma resposta que o teste solta quando quiser. */
function segura() {
  let solta;
  const p = new Promise((ok) => { solta = ok; });
  return { resp: () => p, solta: (r) => solta(r) };
}

test("desmarcar com /bump 502: mensagem neutra e a caixa continua marcada (o caderno segue no pedido)", async () => {
  const extras = EXTRAS.map((x) => ({ ...x, no_carrinho: x.posicao === 1 }));
  const { ctx, page } = await abrirPagina({ extras, api: { [`POST ${BUMP}`]: [502, { detail: "x" }] } });
  await caixas(page).nth(0).click();
  await page.locator("#pp-erro.show").waitFor();
  assert.equal(await page.textContent("#pp-erro"), NEUTRO);
  assert.deepEqual(await marcadas(page), [true, false, false]);
  await ctx.close();
});

test("sem total na sessão (nem change, nem getSession): Pagar desabilitado e sem 'Hoje você não paga nada'", async () => {
  const { ctx, page } = await abrir(browser, { api: comPagina(), sdk: { semChange: true, sessao: { lineItems: [] } } });
  await page.locator("#pagamento iframe").waitFor();
  await page.waitForFunction(() => !document.getElementById("pp-cupom-ok").disabled);  // as ações chegaram
  assert.equal(await page.locator("#pp-pagar").isDisabled(), true);
  assert.equal(await page.textContent("#pp-cta-sub"), "");
  assert.equal(await page.textContent("#pp-cta"), "Começar meus 15 dias grátis");
  await ctx.close();
});

test("sem o change inicial: o total vem do getSession() e o Pagar habilita depois do loadActions", async () => {
  const { ctx, page } = await abrir(browser, { api: comPagina(), sdk: { semChange: true } });
  await destravou(page);
  assert.equal(await page.textContent("#pp-cta-sub"), "Hoje você não paga nada");
  assert.match(await page.textContent("#pp-resumo"), /Total hoje/);
  await ctx.close();
});

test("foi digitar no cupom durante o /bump: o foco NÃO volta à caixa e o texto não se perde", async () => {
  const t = segura();
  const { ctx, page, posts } = await abrirPagina({ api: { [`POST ${BUMP}`]: t.resp } });
  await caixas(page).nth(1).focus();
  await page.keyboard.press("Space");
  await ate(() => posts(BUMP).length > 0);
  await page.click("#pp-cupom-abre");  // foca o campo do cupom
  await page.keyboard.type("AB");
  t.solta([200, { ok: true }]);
  await destravou(page);
  await page.keyboard.type("CD");
  assert.equal(await page.evaluate(() => document.activeElement.id), "pp-cupom-cod");
  assert.equal(await page.inputValue("#pp-cupom-cod"), "ABCD");
  await ctx.close();
});

test("marcar pelo teclado: o foco volta à caixa depois do /bump", async () => {
  const { ctx, page, posts } = await abrirPagina();
  await caixas(page).nth(1).focus();
  await page.keyboard.press("Space");
  await ate(() => posts(BUMP).length > 0);
  await destravou(page);
  assert.equal(await page.evaluate(() => document.activeElement.dataset.pos), "2");
  await ctx.close();
});

test("Sair desmonta: resumo e caixas somem, Pagar sem handler; o /bump velho que volta depois não mexe na tela", async () => {
  const t = segura();
  const { ctx, page } = await abrirPagina({ api: { [`POST ${BUMP}`]: t.resp } });
  await caixas(page).nth(0).click();
  await page.click("#sair");
  await tela(page, "s1");
  t.solta([502, { detail: "x" }]);
  await pausa(200);
  const m = await page.evaluate(() => ({
    resumo: document.getElementById("pp-resumo").childElementCount, bump: document.getElementById("pp-bump").childElementCount,
    pagar: document.getElementById("pp-pagar").disabled, handler: document.getElementById("pp-pagar").onclick,
    erro: document.getElementById("pp-erro").textContent,
    cta: document.getElementById("pp-cta").textContent + document.getElementById("pp-cta-sub").textContent }));
  assert.deepEqual(m, { resumo: 0, bump: 0, pagar: true, handler: null, erro: "", cta: "" });
  await ctx.close();
});

test("cupom em voo, Sair e entrar de novo: campo vazio e o Aplicar da conta nova funciona", async () => {
  const { ctx, page } = await abrirPagina({ api: { "POST /__stripe/cupom": null, "POST /auth/quiz/conta": [200, { estado: "logado" }] } });
  await page.click("#pp-cupom-abre");
  await page.fill("#pp-cupom-cod", "VELHO");
  await page.click("#pp-cupom-ok");
  await page.click("#sair");
  await tela(page, "s1");
  await continuar(page, { nome: "Bia", email: "bia@x.com", whatsapp: "11912345678" });
  await tela(page, "s4");
  await destravou(page);
  assert.equal(await page.inputValue("#pp-cupom-cod"), "");
  assert.equal(await page.locator("#pp-cupom-ok").isDisabled(), false);
  assert.equal(await page.locator("#pp-cupom").isVisible(), false);
  await ctx.close();
});

test("cupom aplicado: 'Cupom PIGGY10 aplicado' e o desconto numa linha; a linha do caderno sem o desconto", async () => {
  // Formato medido no Stripe de teste: `total` da linha já vem com o desconto; `discount` é o desconto dela.
  const sessao = { lineItems: [{ name: "PigBank Plus", total: { minorUnitsAmount: 0 } },
    { name: "Saia do vermelho", total: { minorUnitsAmount: 1071 }, discount: { minorUnitsAmount: 119 } }],
  total: { total: { minorUnitsAmount: 1071 }, discount: { minorUnitsAmount: 119 } },
  discountAmounts: [{ promotionCode: "PIGGY10", amount: 119 }] };
  const { ctx, page } = await abrirPagina({ sdk: { sessao } });
  const linhas = await page.$$eval("#pp-resumo .pp-r-linha", (l) => l.map((x) => x.textContent));
  assert.deepEqual(linhas, ["PigBank PlusR$ 0,00", "Saia do vermelhoR$ 11,90",
    "Cupom PIGGY10 aplicado−R$ 1,19", "Total hojeR$ 10,71"]);
  await ctx.close();
});

test("cupom em voo trava Pagar e caixas; com /bump em voo o cupom não sai", async () => {
  const { ctx, page, posts } = await abrirPagina({ api: { "POST /__stripe/cupom": null, [`POST ${BUMP}`]: null } });
  await page.click("#pp-cupom-abre");
  await page.fill("#pp-cupom-cod", "PIGGY10");
  await caixas(page).nth(0).click();  // /bump pendurado
  await page.waitForFunction(() => document.getElementById("pp-cupom-ok").disabled);
  await page.evaluate(() => document.getElementById("pp-cupom").onsubmit(new Event("submit")));
  await pausa(100);
  assert.equal(posts("/__stripe/cupom").length, 0, "cupom saiu com o /bump em voo");
  await ctx.close();
  const b = await abrirPagina({ api: { "POST /__stripe/cupom": null } });
  await b.page.click("#pp-cupom-abre");
  await b.page.fill("#pp-cupom-cod", "PIGGY10");
  await b.page.click("#pp-cupom-ok");
  await b.page.waitForFunction(() => document.getElementById("pp-pagar").disabled);
  assert.deepEqual(await b.page.$$eval("#pp-bump input", (l) => l.map((c) => c.disabled)), [true, true, true]);
  await b.ctx.close();
});

test("pagar com 409 extra_recusado: caixas no último aceito, aviso da caixa, sem confirm, botão destravado", async () => {
  const { ctx, page, posts } = await abrirPagina({ api: { [`POST ${BUMP}`]: [[200, { ok: true }], [409, { detail: { error: "extra_recusado" } }]] } });
  await caixas(page).nth(0).click();
  await destravou(page);
  await page.click("#pp-pagar");
  await page.locator("#pp-erro.show").waitFor();
  assert.equal(await page.textContent("#pp-erro"), NEUTRO);
  assert.deepEqual(await marcadas(page), [true, false, false]);
  assert.equal(posts(CONFIRM).length, 0);
  assert.equal(await page.locator("#pp-pagar").isDisabled(), false);
  await ctx.close();
});

test("loadActions pendurado: 10 s e plano B (o iframe existe, o relógio do assinar.js não age)", async () => {
  const { ctx, page, posts } = await abrir(browser, { api: comPagina(), stripe: "acoes-pendura", relogio: true });
  await page.locator("#pagamento iframe").waitFor();
  await page.clock.runFor(9_000);
  assert.equal(posts(CHECKOUT).filter((r) => r.body.embutido === false).length, 0);
  await page.clock.runFor(1_000);
  await page.waitForURL(HOSPEDADO);
  await ctx.close();
});

test("/bump pendurado: 15 s e as caixas voltam com o aviso", async () => {
  const { ctx, page, posts } = await abrirPagina({ api: { [`POST ${BUMP}`]: null }, relogio: true });
  await caixas(page).nth(0).click();
  await ate(() => posts(BUMP).length > 0);
  await page.clock.runFor(14_000);
  assert.equal(await page.locator("#pp-erro.show").count(), 0);
  await page.clock.runFor(1_000);
  await page.locator("#pp-erro.show").waitFor();
  assert.deepEqual(await marcadas(page), [false, false, false]);
  await ctx.close();
});

test("sem o pagamento-pagina.js: o embutido de hoje monta normal; a página própria cai no plano B", async () => {
  const sem = { "GET /pagamento-pagina.js": [404, {}] };
  const a = await abrir(browser, { api: { "GET /auth/me": ME("ana@x.com"), ...sem } });
  await a.page.locator("#stripe-checkout iframe").waitFor();
  assert.equal(a.posts(CHECKOUT).filter((r) => r.body.embutido === false).length, 0);
  await a.ctx.close();
  const b = await abrir(browser, { api: { ...comPagina(), ...sem } });
  await b.page.waitForURL(HOSPEDADO);
  await b.ctx.close();
});

test("relógio com o Payment Element montado: 11 s depois continua na página (não vai ao hospedado)", async () => {
  const { ctx, page, posts } = await abrirPagina({ relogio: true });
  await page.clock.runFor(11_000);
  await pausa(200);
  assert.equal(posts(CHECKOUT).filter((r) => r.body.embutido === false).length, 0);
  assert.equal(await page.locator("#s4").isVisible(), true);
  await ctx.close();
});

test("Pagar chamado duas vezes seguidas: um confirm só", async () => {
  const { ctx, page, posts } = await abrirPagina({ extras: [], api: { [`POST ${CONFIRM}`]: null } });
  await page.evaluate(() => { const b = document.getElementById("pp-pagar"); b.onclick(); b.onclick(); });
  await ate(() => posts(CONFIRM).length > 0);
  await pausa(150);
  assert.equal(posts(CONFIRM).length, 1);
  await ctx.close();
});

test("409 sessao_fechada ao marcar uma caixa: tela de pagamento expirado", async () => {
  const { ctx, page } = await abrirPagina({ api: { [`POST ${BUMP}`]: [409, { detail: { error: "sessao_fechada" } }] } });
  await caixas(page).nth(0).click();
  await tela(page, "s3");
  assert.match(await page.textContent("#s3-erro"), /tempo para pagar acabou/);
  await ctx.close();
});

test("1ª caixa aceita e 2ª recusada: a 1ª continua marcada", async () => {
  const { ctx, page } = await abrirPagina({ api: { [`POST ${BUMP}`]: [[200, { ok: true }], [502, { detail: "x" }]] } });
  await caixas(page).nth(0).click();
  await destravou(page);
  await caixas(page).nth(1).click();
  await page.locator("#pp-erro.show").waitFor();
  assert.deepEqual(await marcadas(page), [true, false, false]);
  await ctx.close();
});

test("2ª versão do Stripe.js recusada: endive na 1ª, embutido (dahlia) no 'Tentar de novo' vai ao plano B", async () => {
  let n = 0;
  const { ctx, page, reqs } = await abrirPagina({ api: {
    [`POST ${CHECKOUT}`]: (req) => (!req.body.embutido ? [200, { checkout_url: HOSPEDADO }] : n++ === 0 ? PAGINA() : EMBUTIDO),
    [`POST ${BUMP}`]: [409, { detail: { error: "sessao_fechada" } }] } });
  await caixas(page).nth(0).click();
  await tela(page, "s3");
  await page.click("#s3-retry");
  await page.waitForURL(HOSPEDADO);
  assert.deepEqual(reqs.filter((r) => r.url.includes("js.stripe.com")).map((r) => r.url), ["https://js.stripe.com/endive/stripe.js"]);
  await ctx.close();
});

test("confirm que lança: mensagem padrão (nunca a exceção) e o botão destrava", async () => {
  const { ctx, page } = await abrirPagina({ extras: [], sdk: { confirma: "lanca" } });
  await page.click("#pp-pagar");
  await page.locator("#pp-erro.show").waitFor();
  assert.equal(await page.textContent("#pp-erro"), "O pagamento não foi concluído.");
  assert.equal(await page.locator("#pp-pagar").isDisabled(), false);
  await ctx.close();
});

// Um wrapper de window.Stripe que segura o 1º initCheckoutElementsSdk por 2,5 s (sonda do Tester, passada 2).
const ATRASA = "<script>(function(){var n=0,w;Object.defineProperty(window,'Stripe',{configurable:true,get:function(){return w;},"
  + "set:function(v){w=function(pk,op){var s=v(pk,op),o=s.initCheckoutElementsSdk;s.initCheckoutElementsSdk=function(x){n++;"
  + "var r=o(x);return n===1?new Promise(function(ok){setTimeout(function(){ok(r);},2500);}):r;};return s;};}});})();</script>";

test("1ª montagem lenta, Sair e outra conta antes dela resolver: a velha não toma o Pagar da nova", async () => {
  const { ctx, page, posts } = await abrir(browser, { html: (t) => t.replace("<head>", "<head>" + ATRASA),
    api: { ...comPagina(), "POST /auth/quiz/conta": [200, { estado: "logado" }] } });
  await tela(page, "s4");
  await page.click("#sair");
  await tela(page, "s1");
  await continuar(page, { nome: "Bia", email: "bia@x.com", whatsapp: "11912345678" });
  await tela(page, "s4");
  await destravou(page);
  await pausa(3000);  // a montagem velha resolve aqui
  assert.equal(await page.locator(".pp-bump-topo").count(), 1);
  await page.click("#pp-pagar");
  await ate(() => posts(CONFIRM).length > 0);
  assert.equal(posts(CONFIRM).length, 1);
  await ctx.close();
});

test("loadActions que rejeita: plano B", async () => {
  const { ctx, page } = await abrir(browser, { api: comPagina(), stripe: "acoes-rejeita" });
  await page.waitForURL(HOSPEDADO, { timeout: 5000 });  // antes do prazo de 10 s: é a rejeição que leva
  await ctx.close();
});

test("cupom só no plano durante o trial (desconto 0 hoje): o código aparece, sem valor", async () => {
  const sessao = { lineItems: [{ name: "PigBank Plus", total: { minorUnitsAmount: 0 }, discount: { minorUnitsAmount: 0 } }],
    total: { total: { minorUnitsAmount: 0 }, discount: { minorUnitsAmount: 0 } }, discountAmounts: [{ promotionCode: "PIGGY10", amount: 0 }],
    recurring: { interval: "month", dueNext: { total: { minorUnitsAmount: 1791 } } } };
  const { ctx, page } = await abrirPagina({ sdk: { sessao } });
  const linhas = await page.$$eval("#pp-resumo > *", (l) => l.map((x) => x.textContent));
  assert.deepEqual(linhas, ["PigBank PlusR$\u00a00,00", "Cupom PIGGY10 aplicado", "Total hojeR$\u00a00,00",
    "Depois do teste: R$\u00a017,91/mês"]);
  await ctx.close();
});

// ── O que o SDK real exige e como ele sinaliza a expiração (docs.stripe.com/js/custom_checkout) ────────
const PLUS = { name: "PigBank Plus", total: { minorUnitsAmount: 0 } };

test("confirm só depois de ler total, currency e minorUnitsAmountDivisor (senão o SDK real lança)", async () => {
  const { ctx, page, posts } = await abrirPagina({ extras: [] });
  await page.click("#pp-pagar");
  await ate(() => posts("/__stripe/confirm").length > 0);
  assert.deepEqual(await page.evaluate(() => window.__stripe.lido), { total: true, currency: true, minorUnitsAmountDivisor: true });
  await ctx.close();
});

test("a moeda e o divisor vêm da sessão: currency usd → 'US$'", async () => {
  const sessao = { currency: "usd", lineItems: [], total: { total: { minorUnitsAmount: 1990 } } };
  const { ctx, page } = await abrirPagina({ sdk: { sessao } });
  assert.equal(await page.textContent("#pp-resumo .pp-r-hoje span:last-child"), "US$\u00a019,90");
  await ctx.close();
});

test("change com a sessão expirada (status.type): tela de pagamento expirado", async () => {
  const depoisBump = { lineItems: [PLUS], total: { total: { minorUnitsAmount: 0 } }, status: { type: "expired" } };
  const { ctx, page } = await abrirPagina({ sdk: { depoisBump } });
  await caixas(page).nth(0).click();
  await tela(page, "s3");
  assert.match(await page.textContent("#s3-erro"), /tempo para pagar acabou/);
  await ctx.close();
});

test("sessão já expirada ao carregar as ações (sem change): tela de pagamento expirado", async () => {
  const sessao = { lineItems: [PLUS], total: { total: { minorUnitsAmount: 0 } }, status: { type: "expired" } };
  const { ctx, page } = await abrir(browser, { api: comPagina(), sdk: { sessao, semChange: true } });
  // O S3 já aparece no "Preparando o pagamento…": espera o AVISO, não a tela.
  await page.waitForFunction(() => /tempo para pagar acabou/.test(document.getElementById("s3-erro").textContent));
  assert.equal(await page.locator("#s3-retry").isVisible(), true);
  await ctx.close();
});
