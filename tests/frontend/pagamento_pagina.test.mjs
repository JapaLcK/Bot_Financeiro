/**
 * /assinar com a página de pagamento própria (frontend/pagamento-pagina.js): a resposta do create-checkout com
 * `pagina: true` monta o Payment Element (Stripe.js endive), o resumo, as caixas do order bump e o cupom. Sem
 * `pagina` o embutido de antes segue (os assinar_*.test.mjs são o controle). Stripe.js falso em _assinar.mjs.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { abrir, tela, ME, CSRF, HOSPEDADO, EXTRAS, PAGINA, EMBUTIDO, comPagina, abrirPagina as abrirP, caixas, ate }
  from "./_assinar.mjs";

let browser;
before(async () => { browser = await chromium.launch(); });
after(async () => { await browser?.close(); });

const CHECKOUT = "/billing/create-checkout";
const BUMP = "/billing/checkout/bump";
const LOGADO = { "GET /auth/me": ME("ana@x.com") };
const abrirPagina = (opts) => abrirP(browser, opts);
const stripeJs = (reqs) => reqs.filter((r) => r.url.includes("js.stripe.com")).map((r) => r.url);

test("pagina: Payment Element (só cartão, endive, pt-BR), três caixas e o pedido com pagina:true", async () => {
  const { ctx, page, reqs, posts } = await abrirPagina();
  assert.deepEqual(stripeJs(reqs), ["https://js.stripe.com/endive/stripe.js"]);
  const reg = await page.evaluate(() => window.__stripe);
  assert.deepEqual(reg.pe, { wallets: { applePay: "never", googlePay: "never" } });
  assert.equal(reg.op.locale, "pt-BR");
  assert.equal(reg.cs, "cs_emb_secret");
  assert.equal(await page.locator("#stripe-checkout").isVisible(), false);
  assert.equal(await caixas(page).count(), 3);
  assert.deepEqual(posts(CHECKOUT).map((r) => r.body),
    [{ plan: "plus", interval: "monthly", embutido: true, pagina: true, origem: "assinar" }]);
  // Trial sem caderno: o texto do protótipo; o resumo vem do `change`.
  assert.equal(await page.textContent("#pp-cta"), "Começar meus 15 dias grátis");
  assert.equal(await page.textContent("#pp-cta-sub"), "Hoje você não paga nada");
  assert.match(await page.textContent("#pp-resumo"), /Total hoje\s*R\$\s0,00/);
  assert.match(await page.textContent("#pp-resumo"), /Depois do teste: R\$\s19,90\/mês/);
  const fbq = await page.evaluate(() => window.__fbq);
  assert.equal(fbq.filter((e) => e[1] === "InitiateCheckout").length, 1, "disparaInicio depois de montar");
  await ctx.close();
});

test("sem pagina na resposta: o embutido de antes (dahlia), sem a página própria", async () => {
  const { ctx, page, reqs } = await abrir(browser, { api: LOGADO });
  await page.locator("#stripe-checkout iframe").waitFor();
  assert.deepEqual(stripeJs(reqs), ["https://js.stripe.com/dahlia/stripe.js"]);
  assert.equal(await page.locator("#pagina").isVisible(), false);
  await ctx.close();
});

test("marcar a 1ª e a 3ª: /bump com posicoes [1] e depois [1,3], com CSRF e o sid da sessão", async () => {
  const { ctx, page, posts } = await abrirPagina();
  await caixas(page).nth(0).check();
  await page.waitForFunction(() => !document.getElementById("pp-pagar").disabled);
  await caixas(page).nth(2).check();
  await page.waitForFunction(() => !document.getElementById("pp-pagar").disabled);
  assert.deepEqual(posts(BUMP).map((r) => r.body),
    [{ sid: "cs_test_abc", posicoes: [1] }, { sid: "cs_test_abc", posicoes: [1, 3] }]);
  assert.ok(posts(BUMP).every((r) => r.csrf === CSRF));
  assert.equal(await page.locator("#pp-bump").evaluate((e) => e.classList.contains("marcado")), true);
  await ctx.close();
});

test("durante o /bump as três caixas e o botão de pagar ficam travados", async () => {
  let solta;
  const pendente = new Promise((ok) => { solta = ok; });
  const { ctx, page } = await abrirPagina({ api: { [`POST ${BUMP}`]: () => pendente } });
  await caixas(page).nth(1).check();
  await page.waitForFunction(() => document.getElementById("pp-pagar").disabled);
  const travas = await page.$$eval("#pp-bump input", (l) => l.map((c) => c.disabled));
  assert.deepEqual(travas, [true, true, true]);
  solta([200, { ok: true }]);
  await page.waitForFunction(() => !document.getElementById("pp-pagar").disabled);
  assert.deepEqual(await page.$$eval("#pp-bump input", (l) => l.map((c) => c.disabled)), [false, false, false]);
  await ctx.close();
});

test("409 extra_recusado no /bump: as caixas voltam ao que o servidor tem, com o aviso", async () => {
  const extras = EXTRAS.map((x) => ({ ...x, no_carrinho: x.posicao === 2 }));
  const { ctx, page } = await abrirPagina({ extras, api: { [`POST ${BUMP}`]: [409, { detail: { error: "extra_recusado" } }] } });
  assert.deepEqual(await page.$$eval("#pp-bump input", (l) => l.map((c) => c.checked)), [false, true, false]);
  // click, e não check: o 409 desmarca tão rápido que o check() do Playwright veria "não mudou".
  await caixas(page).nth(0).click();
  await page.locator("#pp-erro.show").waitFor();
  assert.equal(await page.textContent("#pp-erro"), "Não deu para atualizar seu pedido. Ele continua como estava.");
  assert.deepEqual(await page.$$eval("#pp-bump input", (l) => l.map((c) => c.checked)), [false, true, false]);
  await ctx.close();
});

test("pagar com caixas: o /bump com o estado da tela vem ANTES do confirm", async () => {
  const { ctx, page, reqs } = await abrirPagina();
  await page.click("#pp-pagar");
  await page.waitForFunction(() => window.__stripe.run > 0);
  await ate(() => reqs.some((r) => r.path === "/__stripe/confirm"));
  const ordem = reqs.filter((r) => r.path === BUMP || r.path === "/__stripe/confirm").map((r) => r.path);
  assert.deepEqual(ordem, [BUMP, "/__stripe/confirm"]);
  assert.deepEqual(reqs.find((r) => r.path === BUMP).body, { sid: "cs_test_abc", posicoes: [] });
  await ctx.close();
});

test("pagar com o /bump falhando: não confirma, mostra o erro", async () => {
  const { ctx, page, reqs } = await abrirPagina({ api: { [`POST ${BUMP}`]: [502, { detail: "x" }] } });
  await page.click("#pp-pagar");
  await page.locator("#pp-erro.show").waitFor();
  assert.match(await page.textContent("#pp-erro"), /página segura do Stripe/);
  assert.equal(reqs.filter((r) => r.path === "/__stripe/confirm").length, 0);
  assert.equal(await page.locator("#s4-b").isVisible(), true);
  await ctx.close();
});

test("pagar sem oferta: confirma direto, sem /bump nem caixa", async () => {
  const { ctx, page, reqs, posts } = await abrirPagina({ extras: [] });
  assert.equal(await page.locator("#pp-bump").isVisible(), false);
  await page.click("#pp-pagar");
  await ate(() => reqs.some((r) => r.path === "/__stripe/confirm"));
  assert.equal(posts(BUMP).length, 0);
  await ctx.close();
});

test("confirm com erro do cartão: a mensagem do Stripe e o botão destrava", async () => {
  const { ctx, page } = await abrirPagina({ extras: [],
    sdk: { confirma: { type: "error", error: { code: "card_declined", message: "Seu cartão foi recusado." } } } });
  await page.click("#pp-pagar");
  await page.locator("#pp-erro.show").waitFor();
  assert.equal(await page.textContent("#pp-erro"), "Seu cartão foi recusado.");
  assert.equal(await page.locator("#pp-pagar").isDisabled(), false);
  await ctx.close();
});

for (const [nome, opts] of [
  ["409 sessao_fechada no /bump", { api: { [`POST ${BUMP}`]: [409, { detail: { error: "sessao_fechada" } }] } }],
  ["confirm com erro e a sessão expirada (status.type)", { extras: [], sdk: { expiraNoConfirm: true, confirma: { type: "error", error: { message: "x" } } } }],
]) {
  test(`${nome}: aviso no S3 e o "Tentar de novo" refaz o checkout`, async () => {
    const { ctx, page, posts } = await abrirPagina(opts);
    await page.click("#pp-pagar");
    await tela(page, "s3");
    assert.match(await page.textContent("#s3-erro"), /tempo para pagar acabou/);
    await page.click("#s3-retry");
    await tela(page, "s4");
    assert.equal(posts(CHECKOUT).filter((r) => r.body.embutido).length, 2);
    await page.locator("#pagamento iframe").waitFor();
    assert.equal(await page.locator("#pagamento iframe").count(), 1, "Payment Element montado duas vezes");
    await ctx.close();
  });
}

test("uma oferta só: a versão A original, linha única com 'Sim! Quero o caderno … por R$ 11,90'", async () => {
  const { ctx, page } = await abrirPagina({ extras: [EXTRAS[0]] });
  assert.equal(await caixas(page).count(), 1);
  assert.equal(await page.textContent("#pp-bump .pp-tit"), "Sim! Quero o caderno Saia do vermelho por R$\u00a011,90");
  assert.equal(await page.locator("#pp-bump .pp-capa-g").count(), 1);
  assert.equal(await page.locator("#pp-bump .pp-seta").count(), 1);
  await ctx.close();
});

test("texto do Stripe só como texto: nome com <img onerror> não vira elemento, capa só https", async () => {
  const xss = '<img src=x onerror="window.__xss=1">';
  const extras = [{ ...EXTRAS[0], nome: xss, descricao: xss }, { ...EXTRAS[1], imagem: "javascript:alert(1)" },
                  { ...EXTRAS[2], imagem: "http://inseguro.test/c.png" }];
  const { ctx, page } = await abrirPagina({ extras });
  await page.waitForTimeout(100);
  assert.equal(await page.evaluate(() => window.__xss), undefined);
  assert.equal(await page.locator("#pp-bump .pp-tit b").first().textContent(), xss);
  assert.equal(await page.locator("#pp-bump img[src='x']").count(), 0);
  // Só a capa https da 1ª: a `javascript:` e a `http:` não viram <img>.
  assert.deepEqual(await page.$$eval("#pp-bump img", (l) => l.map((i) => i.getAttribute("src"))), [EXTRAS[0].imagem]);
  await ctx.close();
});

test("com caderno no carrinho e trial: 'Hoje: só os cadernos, R$ …' a partir do total da sessão", async () => {
  const extras = EXTRAS.map((x) => ({ ...x, no_carrinho: x.posicao !== 3 }));
  const sessao = { lineItems: [{ name: "PigBank Plus", total: { minorUnitsAmount: 0 } },
                               { name: "Saia do vermelho", total: { minorUnitsAmount: 1190 } },
                               { name: "Primeira reserva", total: { minorUnitsAmount: 1190 } }],
                   total: { total: { minorUnitsAmount: 2380 } } };
  const { ctx, page } = await abrirPagina({ extras, sdk: { sessao } });
  await page.waitForFunction(() => /23,80/.test(document.getElementById("pp-cta-sub").textContent));
  assert.equal(await page.textContent("#pp-cta-sub"), "Hoje: só os cadernos, R$ 23,80");
  assert.equal(await page.locator("#pp-bump .pp-linha.on").count(), 2);
  await ctx.close();
});

test("sem trial: 'Assinar · R$ X hoje'", async () => {
  const sessao = { lineItems: [], total: { total: { minorUnitsAmount: 1990 } } };
  const { ctx, page } = await abrir(browser, { sdk: { sessao }, api: { ...LOGADO,
    [`POST ${CHECKOUT}`]: [200, { ...PAGINA()[1], trial_days: 0 }] } });
  await page.waitForFunction(() => /19,90/.test(document.getElementById("pp-cta").textContent));
  assert.equal(await page.textContent("#pp-cta"), "Assinar · R$ 19,90 hoje");
  await ctx.close();
});

test("cupom: 'Tem cupom?' abre o campo e chama applyPromotionCode; erro aparece embaixo", async () => {
  const { ctx, page, posts } = await abrirPagina({ sdk: { cupom: { type: "error", error: { message: "Cupom inválido." } } } });
  assert.equal(await page.locator("#pp-cupom").isVisible(), false);
  await page.click("#pp-cupom-abre");
  await page.fill("#pp-cupom-cod", " PIGGY10 ");
  await page.click("#pp-cupom-ok");
  await page.locator("#pp-cupom-erro.show").waitFor();
  assert.deepEqual(posts("/__stripe/cupom").map((r) => r.body), [{ code: "PIGGY10" }]);
  assert.equal(await page.textContent("#pp-cupom-erro"), "Cupom inválido.");
  await ctx.close();
});

test("relógio: Payment Element sem iframe em 10 s leva ao hospedado", async () => {
  const { ctx, page, posts } = await abrir(browser, { api: comPagina(), stripe: "vazio", relogio: true });
  await tela(page, "s4");
  await page.clock.runFor(9_000);
  assert.equal(posts(CHECKOUT).filter((r) => r.body.embutido === false).length, 0);
  await page.clock.runFor(1_000);
  await page.waitForURL(HOSPEDADO);
  await ctx.close();
});

test("plano B manual na página própria: destroy() e hospedado", async () => {
  const { ctx, page } = await abrirPagina();
  await page.click("#s4-b");
  await page.waitForURL(HOSPEDADO);
  await ctx.close();
});

test("loadActions com erro: plano B", async () => {
  const { ctx, page } = await abrir(browser, { api: comPagina(), stripe: "acoes-erro" });
  await page.waitForURL(HOSPEDADO);
  await ctx.close();
});

for (const [q, esperado] of [["&origem=precos", "precos"], ["&origem=lixo", "assinar"]]) {
  test(`origem da query (${q}): vai como "${esperado}" no embutido e no hospedado`, async () => {
    const { ctx, page, posts } = await abrir(browser, { query: `?plano=plus&ciclo=monthly${q}`,
      api: { ...LOGADO, [`POST ${CHECKOUT}`]: (req) => (req.body.embutido ? EMBUTIDO : [200, { checkout_url: HOSPEDADO }]) } });
    await page.locator("#stripe-checkout iframe").waitFor();
    await page.click("#s4-b");
    await page.waitForURL(HOSPEDADO);
    assert.deepEqual(posts(CHECKOUT).map((r) => r.body.origem), [esperado, esperado]);
    await ctx.close();
  });
}

// ── Layout, medido ──────────────────────────────────────────────────────────
for (const [nome, viewport] of [["desktop", { width: 1280, height: 800 }], ["celular", { width: 390, height: 844 }]]) {
  test(`layout da página própria com 3 caixas no ${nome} (${viewport.width}×${viewport.height})`, async () => {
    const { ctx, page } = await abrirPagina({ viewport });
    const m = await page.evaluate(() => {
      const r = (id) => document.getElementById(id).getBoundingClientRect();
      const linhas = [...document.querySelectorAll("#pp-bump .pp-linha")].map((l) => l.getBoundingClientRect().height);
      const cb = document.querySelector("#pp-bump input").getBoundingClientRect();
      return { sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth,
               css: getComputedStyle(document.getElementById("pagina")).display,
               bump: r("pp-bump").height, linhas, cb: cb.width,
               resumo: r("pp-resumo"), pag: r("pagamento"), cta: r("pp-pagar") };
    });
    assert.equal(m.css, "grid", "assinar.css não carregou");
    assert.ok(m.sw <= m.cw, `scrollWidth ${m.sw} > clientWidth ${m.cw}`);
    assert.ok(m.linhas.every((h) => h >= 56), `linha < 56 px: ${m.linhas}`);
    assert.ok(m.cb >= 26, `checkbox com ${m.cb}px`);
    assert.ok(m.bump <= 320, `caixa com 3 linhas tem ${m.bump}px`);
    if (viewport.width >= 880) assert.ok(m.pag.left > m.resumo.right, "não abriu em duas colunas");
    else assert.ok(m.pag.top > m.resumo.bottom, "no celular o pagamento vem depois do resumo e das caixas");
    await page.locator("#pp-pagar").scrollIntoViewIfNeeded();
    const cta = await page.evaluate(() => {
      const r = document.getElementById("pp-pagar").getBoundingClientRect();
      return { top: r.top, bottom: r.bottom, vh: innerHeight,
               noCentro: document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)?.closest("#pp-pagar") !== null };
    });
    assert.ok(cta.top >= 0 && cta.bottom <= cta.vh && cta.noCentro, JSON.stringify(cta));
    console.log(`medida ${nome}: ${JSON.stringify({ ...m, cta })}`);
    if (process.env.PB_SHOT_DIR) await page.screenshot({ path: `${process.env.PB_SHOT_DIR}/pagina-${nome}.png`, fullPage: true });
    await ctx.close();
  });
}

// ── O `change` do Stripe depois de um /bump e de um cupom (o fake manda uma sessão NOVA) ─────────────────
const PLUS = { name: "PigBank Plus", total: { minorUnitsAmount: 0 } };
const DEPOIS = { recurring: { dueNext: { total: { minorUnitsAmount: 1990 } } } };
const textos = (page) => page.evaluate(() => ({
  resumo: [...document.querySelectorAll("#pp-resumo .pp-r-linha")].map((x) => x.textContent),
  cta: document.getElementById("pp-pagar").textContent }));

test("change depois do /bump: resumo, Total hoje e o botão passam a cobrar o caderno", async () => {
  const depoisBump = { ...DEPOIS, lineItems: [PLUS, { name: "Saia do vermelho", total: { minorUnitsAmount: 1190 } }],
                       total: { total: { minorUnitsAmount: 1190 } } };
  const { ctx, page } = await abrirPagina({ sdk: { depoisBump } });
  assert.equal((await textos(page)).cta, "Começar meus 15 dias grátisHoje você não paga nada");
  await caixas(page).nth(0).click();
  await page.waitForFunction(() => /11,90/.test(document.getElementById("pp-cta-sub").textContent));
  assert.deepEqual(await textos(page), {
    resumo: ["PigBank PlusR$ 0,00", "Saia do vermelhoR$ 11,90", "Total hojeR$ 11,90"],
    cta: "Começar meus 15 dias grátisHoje: só o caderno, R$ 11,90" });
  assert.equal(await page.locator("#pp-pagar").isDisabled(), false);
  await ctx.close();
});

test("change depois do cupom: a linha do cupom e o total com desconto aparecem", async () => {
  const depoisCupom = { ...DEPOIS, lineItems: [PLUS], total: { total: { minorUnitsAmount: 0 }, discount: { minorUnitsAmount: 199 } },
                        discountAmounts: [{ promotionCode: "PIGGY10" }] };
  const { ctx, page } = await abrirPagina({ sdk: { depoisCupom } });
  await page.click("#pp-cupom-abre");
  await page.fill("#pp-cupom-cod", "PIGGY10");
  await page.click("#pp-cupom-ok");
  await page.waitForFunction(() => /PIGGY10/.test(document.getElementById("pp-resumo").textContent));
  assert.deepEqual((await textos(page)).resumo, ["PigBank PlusR$ 0,00", "Cupom PIGGY10 aplicado−R$ 1,99", "Total hojeR$ 0,00"]);
  await ctx.close();
});

test("plano anual (o ciclo vem do create-checkout, não do SDK): 'Depois do teste: …/ano'", async () => {
  const anual = (req) => (req.body.embutido ? [200, { ...PAGINA()[1], interval: "annual" }] : [200, { checkout_url: HOSPEDADO }]);
  const sessao = { lineItems: [PLUS], total: { total: { minorUnitsAmount: 0 } },
                   recurring: { dueNext: { total: { minorUnitsAmount: 19900 } } } };  // sem `interval` no SDK
  const { ctx, page } = await abrirPagina({ sdk: { sessao }, api: { [`POST ${CHECKOUT}`]: anual } });
  assert.equal(await page.textContent("#pp-resumo .pp-r-depois"), "Depois do teste: R$ 199,00/ano");
  await ctx.close();
});

test("trial com total de hoje > 0 e sem linhas: nunca 'Hoje você não paga nada'", async () => {
  const sessao = { lineItems: [], total: { total: { minorUnitsAmount: 2380 } } };
  const { ctx, page } = await abrirPagina({ sdk: { sessao } });
  assert.equal(await page.textContent("#pp-cta-sub"), "Hoje: R$ 23,80");
  await ctx.close();
});
