/**
 * /assinar: o embutido do Stripe e o plano B (D-p). Os cinco gatilhos levam ao
 * mesmo `irParaHospedado`, que faz UM POST `embutido:false` e navega para o
 * `checkout_url`. No fim, o layout medido nos dois viewports.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { abrir, tela, continuar, ME, FRAG, HOSPEDADO, EMBUTIDO } from "./_assinar.mjs";

let browser;
before(async () => { browser = await chromium.launch(); });
after(async () => { await browser?.close(); });

const LOGADO = { "GET /auth/me": ME("ana@x.com") };
const CHECKOUT = "/billing/create-checkout";
const hospedados = (posts) => posts(CHECKOUT).filter((r) => r.body.embutido === false);

/** Espera a navegação para o hospedado e confere o POST único. */
async function foiAoHospedado(page, posts) {
  await page.waitForURL(HOSPEDADO);
  assert.deepEqual(hospedados(posts).map((r) => r.body),
    [{ plan: "plus", interval: "monthly", embutido: false, origem: "assinar" }]);
}

test("(1) Stripe.js bloqueado: um POST hospedado e a navegação para o checkout_url", async () => {
  const { ctx, page, posts } = await abrir(browser, { api: LOGADO, stripe: "aborta" });
  await foiAoHospedado(page, posts);
  await ctx.close();
});

for (const stripe of ["rejeita", "mount-lanca", "lanca", "sem-global"]) {
  test(`(2) montagem que falha (${stripe}): o mesmo`, async () => {
    const { ctx, page, posts } = await abrir(browser, { api: LOGADO, stripe });
    await foiAoHospedado(page, posts);
    await ctx.close();
  });
}

for (const stripe of ["vazio", "pendura"]) {
  test(`(3/4) ${stripe === "vazio" ? "montagem sem iframe" : "Stripe.js que nunca responde"}: o relógio de 10 s leva ao hospedado`, async () => {
    const { ctx, page, posts } = await abrir(browser, { api: LOGADO, stripe, relogio: true });
    await tela(page, "s4");
    await page.clock.runFor(9_000);
    assert.equal(hospedados(posts).length, 0, "foi ao hospedado antes dos 10 s");
    await page.clock.runFor(1_000);
    await foiAoHospedado(page, posts);
    await ctx.close();
  });
}

test("(5) no app (UA PigBankApp): nenhum Stripe.js, nenhum POST embutido, direto ao hospedado", async () => {
  const { ctx, page, posts, reqs } = await abrir(browser, { api: LOGADO, userAgent: "Mozilla/5.0 PigBankApp/1.0" });
  await foiAoHospedado(page, posts);
  assert.equal(reqs.filter((r) => r.url.includes("js.stripe.com")).length, 0);
  assert.equal(posts(CHECKOUT).filter((r) => r.body.embutido === true).length, 0);
  await ctx.close();
});

test("(6) link manual com o embutido funcionando: destroy() e hospedado", async () => {
  // O hospedado responde devagar, para dar tempo de ler o destroy() antes de a página sair.
  const devagar = (req) => (req.body.embutido ? [200, { client_secret: "cs", publishable_key: "pk" }]
    : new Promise((ok) => setTimeout(() => ok([200, { checkout_url: HOSPEDADO }]), 300)));
  const { ctx, page, posts } = await abrir(browser, { api: { ...LOGADO, [`POST ${CHECKOUT}`]: devagar } });
  await page.locator("#stripe-checkout iframe").waitFor();
  await page.click("#s4-b");
  await tela(page, "h");
  assert.equal(await page.evaluate(() => window.__stripe.destroy), 1);
  await foiAoHospedado(page, posts);
  await ctx.close();
});

// O `setTimeout` do "rejeita-tarde" só nasce quando o Stripe.js carregou e o
// client_secret foi pedido; adiantar o relógio antes disso o empurraria para depois.
const prontoParaRejeitar = (page) => page.waitForFunction(() => window.__stripe && window.__stripe.cs);

// O POST hospedado fica em voo nos dois (7): a tela H não sai antes de o relógio passar.
const EMVOO = { ...LOGADO, [`POST ${CHECKOUT}`]: (req) => (req.body.embutido ? EMBUTIDO : null) };

test("(7a) a montagem rejeita aos 9,9 s e o relógio venceria aos 10 s: um POST só", async () => {
  const { ctx, page, posts } = await abrir(browser, { api: EMVOO, stripe: "rejeita-tarde", atraso: 9_900, relogio: true });
  await tela(page, "s4");
  await prontoParaRejeitar(page);
  await page.clock.runFor(9_950);
  await tela(page, "h");
  await page.clock.runFor(1_000);
  await page.waitForTimeout(200);
  assert.equal(hospedados(posts).length, 1);
  await ctx.close();
});

test("(7b) o relógio vence aos 10 s e a montagem rejeita depois, com o POST hospedado em voo: um POST só", async () => {
  const { ctx, page, posts } = await abrir(browser, { api: EMVOO, stripe: "rejeita-tarde", atraso: 10_100, relogio: true });
  await tela(page, "s4");
  await prontoParaRejeitar(page);
  await page.clock.runFor(10_000);
  await tela(page, "h");
  await page.clock.runFor(500);
  await page.waitForTimeout(200);
  assert.equal(hospedados(posts).length, 1);
  await ctx.close();
});

test("(8) H com 503: S3e, e o 'Tentar de novo' faz um 2º POST hospedado", async () => {
  const respostas = [[503, { detail: "Pagamentos ainda não configurados." }], [200, { checkout_url: HOSPEDADO }]];
  let n = 0;
  const { ctx, page, posts } = await abrir(browser, { stripe: "aborta",
    api: { ...LOGADO, [`POST ${CHECKOUT}`]: (req) => (req.body.embutido ? [200, { client_secret: "cs", publishable_key: "pk" }] : respostas[n++]) } });
  await tela(page, "s3-retry");
  assert.equal(hospedados(posts).length, 1);
  await page.click("#s3-retry");
  await page.waitForURL(HOSPEDADO);
  assert.equal(hospedados(posts).length, 2);
  await ctx.close();
});

test("positivo: UA comum e montagem ok, 30 s depois → zero POST hospedado e InitiateCheckout uma vez", async () => {
  const { ctx, page, posts } = await abrir(browser, { api: LOGADO, relogio: true });
  await page.locator("#stripe-checkout iframe").waitFor();
  await page.clock.runFor(30_000);
  await page.waitForTimeout(200);
  assert.equal(hospedados(posts).length, 0);
  const st = await page.evaluate(() => window.__stripe);
  assert.deepEqual([st.pk, st.cs, st.mount], ["pk_test_x", "cs_emb_secret", 1]);
  const inicio = (await page.evaluate(() => window.__fbq)).filter((c) => c[1] === "InitiateCheckout");
  assert.equal(inicio.length, 1);
  const ga = (await page.evaluate(() => window.__ga)).filter((c) => c[0] === "begin_checkout");
  assert.deepEqual(ga, [["begin_checkout", { items: [{ item_id: "plus", item_category: "monthly" }] }]]);
  await ctx.close();
});

// ── plans-config: só serve ao link do Pix, e nunca segura o checkout ─────────
const PENDURADO = { ...LOGADO, "GET /billing/plans-config": null };

test("plans-config pendurado: o embutido monta, sem o link do Pix", async () => {
  const { ctx, page } = await abrir(browser, { api: PENDURADO });
  await page.locator("#stripe-checkout iframe").waitFor({ timeout: 5_000 });
  assert.equal(await page.locator("#s4-pix").isVisible(), false);
  await ctx.close();
});

test("plans-config pendurado e montagem sem iframe: o relógio de 10 s leva ao hospedado", async () => {
  const { ctx, page, posts } = await abrir(browser, { api: PENDURADO, stripe: "vazio", relogio: true });
  await page.locator("#s4").waitFor({ state: "visible", timeout: 5_000 });
  await page.clock.runFor(10_000);
  await foiAoHospedado(page, posts);
  await ctx.close();
});

/** Um plans-config que só responde quando o teste chama `solta(resp)`. */
function tardio() {
  let solta;
  const p = new Promise((ok) => { solta = ok; });
  return { solta, resp: () => p };
}

for (const [nome, resp, visivel] of [["true", [200, { pix_annual_available: true }], true],
                                     ["false", [200, { pix_annual_available: false }], false],
                                     ["falha de rede", "aborta", false]]) {
  test(`plans-config que volta depois do embutido montado (${nome}): link do Pix ${visivel ? "aparece" : "oculto"}`, async () => {
    const t = tardio();
    const { ctx, page } = await abrir(browser, { api: { ...LOGADO, "GET /billing/plans-config": t.resp } });
    await page.locator("#stripe-checkout iframe").waitFor();
    assert.equal(await page.locator("#s4-pix").isVisible(), false);
    const fim = resp === "aborta" ? page.waitForEvent("requestfailed") : page.waitForResponse(/plans-config/);
    t.solta(resp);
    await fim;
    if (visivel) await page.locator("#s4-pix").waitFor({ state: "visible" });
    else await page.waitForTimeout(150);
    assert.equal(await page.locator("#s4-pix").isVisible(), visivel);
    await ctx.close();
  });
}

test("plans-config velho que volta depois do Sair não mostra o Pix no S4 da conta nova (guarda de geração)", async () => {
  const t = tardio();
  let n = 0;
  const { ctx, page } = await abrir(browser, { hash: FRAG, api: { ...LOGADO,
    "GET /billing/plans-config": () => (n++ === 0 ? t.resp() : null),
    "POST /auth/quiz/conta": [200, { estado: "logado" }] } });
  await page.locator("#stripe-checkout iframe").waitFor();
  await page.click("#sair");
  await tela(page, "s1");
  await continuar(page);
  await page.locator("#stripe-checkout iframe").waitFor();
  const fim = page.waitForResponse(/plans-config/);
  t.solta([200, { pix_annual_available: true }]);
  await fim;
  await page.waitForTimeout(150);
  assert.equal(await page.locator("#s4-pix").isVisible(), false);
  await ctx.close();
});

// ── Layout, medido ──────────────────────────────────────────────────────────

/** Mede a tela visível. `cta`: o id do CTA rosa do estado, ou null (S4 não tem). */
async function medir(page, cta) {
  return page.evaluate((cta) => {
    document.activeElement?.blur();
    const vis = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
    const ROSA = "rgb(255, 45, 142)";
    const rosas = [...document.querySelectorAll("body *")].filter((el) => vis(el)).filter((el) => {
      const s = getComputedStyle(el);
      return [s.backgroundColor, s.color, s.borderTopColor].includes(ROSA);
    }).map((el) => el.id || el.tagName);
    const out = { sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth,
                  vh: innerHeight, rosas,
                  // Guarda: sem a assinar.css carregada a medida não mede a página.
                  css: getComputedStyle(document.getElementById("conteudo")).display };
    if (cta) {
      const el = document.getElementById(cta);
      const r = el.getBoundingClientRect();
      out.cta = { top: r.top, bottom: r.bottom, left: r.left, right: r.right,
                  noCentro: document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)?.closest(`#${cta}`) === el };
    }
    const sc = document.getElementById("stripe-checkout");
    if (vis(sc)) {
      const a = sc.querySelector("iframe").getBoundingClientRect();
      const b = document.getElementById("s4-b").getBoundingClientRect();
      out.s4 = { largura: sc.getBoundingClientRect().width, cobre: b.top < a.bottom && b.bottom > a.top, linkVisivel: b.height > 0 };
    }
    return out;
  }, cta);
}

const ESTADOS = [
  ["S1", { hash: FRAG }, "s1", "s1-continuar", null],
  ["S2", { hash: FRAG, api: { "POST /auth/quiz/conta": [200, { estado: "tem_conta" }] } }, "s2", "s2-senha", continuar],
  ["S4", { api: LOGADO }, "s4", null, null],
  ["F1", { api: { ...LOGADO, [`POST ${CHECKOUT}`]: [409, { detail: { error: "already_subscribed" } }] } }, "f1", "f1-cta", null],
];

for (const [nome, viewport] of [["desktop", { width: 1280, height: 800 }], ["mobile", { width: 390, height: 844 }]]) {
  for (const [estado, opts, id, cta, passo] of ESTADOS) {
    test(`layout ${estado} no ${nome} (${viewport.width}×${viewport.height})`, async () => {
      const { ctx, page } = await abrir(browser, { ...opts, viewport });
      if (passo) { await tela(page, "s1"); await passo(page); }
      await tela(page, id);
      if (id === "s4") await page.locator("#stripe-checkout iframe").waitFor();
      const m = await medir(page, cta);
      assert.equal(m.css, "flex", "assinar.css não carregou");
      assert.ok(m.sw <= m.cw, `scrollWidth ${m.sw} > clientWidth ${m.cw}`);
      if (m.cta) {
        assert.ok(m.cta.top >= 0 && m.cta.bottom <= m.vh && m.cta.left >= 0 && m.cta.right <= m.cw, JSON.stringify(m.cta));
        assert.ok(m.cta.noCentro, "outro elemento cobre o CTA");
      }
      assert.deepEqual(m.rosas, cta ? [cta] : [], `rosa fora do CTA: ${m.rosas}`);
      if (id === "s4") {
        assert.ok(m.s4.linkVisivel && !m.s4.cobre, JSON.stringify(m.s4));
        if (viewport.width === 390) assert.ok(m.s4.largura >= 320, `#stripe-checkout com ${m.s4.largura}px`);
      }
      if (process.env.PB_SHOT_DIR) await page.screenshot({ path: `${process.env.PB_SHOT_DIR}/assinar-${estado}-${nome}.png`, fullPage: true });
      await ctx.close();
    });
  }
}
