/**
 * Q40 no painel: com banco conectado, "Pago" pergunta a forma ANTES do valor, e
 * o modal "Novo lançamento" diz que o manual é só dinheiro vivo.
 *
 * O que se mede, no dashboard.html de verdade (servido do disco) a 1440 e a 390:
 *   - payBill com `exige_forma_pagamento`: Esc, clique no véu e Cancelar NÃO
 *     fazem POST; "Pelo banco" manda `{metodo:"banco"}` sem pedir valor;
 *     "Dinheiro vivo" pede o valor e manda `{metodo:"dinheiro", amount}`;
 *   - sem o campo, o fluxo de antes: prompt de valor e `{amount}` puro;
 *   - o modal de lançamento troca a copy só com o campo, e cabe em 390;
 *   - `submitLaunch` declara `funding_source: "carteira"` fora do crédito.
 *
 * Controle NEGATIVO (medido): trocar `if (_billsExigeForma)` por `if (false)`
 * em payBill deixa vermelhos os casos "banco" e "dinheiro"; tirar o
 * `funding_source` do corpo deixa vermelho o caso do submitLaunch.
 * POSITIVO: o caso "sem o campo" prova que o fluxo de antes segue igual.
 *
 * O que NÃO alcança: o app no aparelho (carrega o site ao vivo, só depois do
 * deploy) e `env(safe-area-inset-*)`, que vale 0 no headless.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { startServer } from "./_server.mjs";
import { chromium } from "playwright";

let ORIGIN, server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer());
                     browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

const json = (body) => (r) => r.fulfill({
  status: 200, contentType: "application/json", body: JSON.stringify(body) });

async function abrirDash(w) {
  const ctx = await browser.newContext({ viewport: { width: w, height: 812 } });
  await ctx.route("**/auth/validate", json({ ok: true, user_id: 42 }));
  await ctx.route("**/auth/me", json({ app_access: true, plan_tier: "pro" }));
  await ctx.route("**/auth/dashboard-profile", json({
    email: "ana@gmail.com", display_name: "Ana", plan: "pro", feature_gates: {} }));
  await ctx.route("**cdnjs.cloudflare.com/**", (r) => r.abort());
  const page = await ctx.newPage();
  await page.addInitScript(() => {
    class WSStub {
      static OPEN = 1;
      constructor() { this.readyState = 1; setTimeout(() => this.onopen && this.onopen({}), 0); }
      send() {} close() { this.readyState = 3; }
    }
    window.WebSocket = WSStub;
  });
  await page.goto(`${ORIGIN}/dashboard.html`);
  await page.waitForSelector("#dot.connected", { timeout: 15000 });
  // A partir daqui toda requisição é capturada; nada sai para a rede.
  await page.evaluate(() => {
    window.__posts = [];
    window.fetch = (url, opts = {}) => {
      if ((opts.method || "GET") === "POST") window.__posts.push({ url: String(url), body: JSON.parse(opts.body) });
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ ok: true }),
                               text: () => Promise.resolve("{}") });
    };
    window.loadBillsView = () => {}; window.sendRefresh = () => {};
  });
  return { ctx, page };
}

/** Clica "Pago" e espera a escolha de forma aparecer. */
async function abreEscolha(page, exige) {
  await page.evaluate((e) => {
    _billsExigeForma = e;
    window.__prompts = [];
    window.prompt = (msg, pre) => { window.__prompts.push(msg); return "132,50"; };
    window.__fim = payBill(7, 120, "Luz", true);
  }, exige);
}

for (const w of [1440, 390]) {
  test(`${w}: com banco, Esc/véu/Cancelar não fazem POST; banco e dinheiro mandam o corpo certo`, async () => {
    const { ctx, page } = await abrirDash(w);
    const ov = "#generic-confirm-overlay.open";

    for (const sair of ["esc", "veu", "cancelar"]) {
      await abreEscolha(page, true);
      await page.waitForSelector(ov);
      if (sair === "esc") await page.keyboard.press("Escape");
      if (sair === "veu") await page.mouse.click(5, 5);
      if (sair === "cancelar") await page.click("#generic-confirm-cancel");
      await page.evaluate(() => window.__fim);
      assert.deepEqual(await page.evaluate(() => [window.__posts, window.__prompts]), [[], []], sair);
    }

    // os três botões cabem na caixa do modal
    await abreEscolha(page, true);
    await page.waitForSelector(ov);
    const caixa = await page.evaluate(() => {
      const m = document.querySelector("#generic-confirm-overlay .modal").getBoundingClientRect();
      return [...document.querySelectorAll("#generic-confirm-overlay .modal-acts button")]
        .filter((b) => b.offsetWidth > 0)
        .map((b) => { const r = b.getBoundingClientRect();
                      return { t: b.textContent, dentro: r.left >= m.left - 0.5 && r.right <= m.right + 0.5,
                               inteiro: b.scrollWidth <= b.clientWidth + 1 }; });
    });
    assert.equal(caixa.length, 3, JSON.stringify(caixa));
    assert.ok(caixa.every((b) => b.dentro && b.inteiro), JSON.stringify(caixa));
    await page.screenshot({ path: `${process.env.Q40_SHOTS || "/tmp"}/q40-escolha-${w}.png` });

    await page.click("#generic-confirm-ok");              // "Pelo banco"
    await page.evaluate(() => window.__fim);
    assert.deepEqual(await page.evaluate(() => window.__posts.map((p) => p.body)), [{ metodo: "banco" }]);
    assert.deepEqual(await page.evaluate(() => window.__prompts), [], "banco não pede valor");

    await page.evaluate(() => { window.__posts = []; });
    await abreEscolha(page, true);
    await page.waitForSelector(ov);
    await page.click("#generic-confirm-alt");             // "Dinheiro vivo"
    await page.evaluate(() => window.__fim);
    assert.deepEqual(await page.evaluate(() => window.__posts.map((p) => p.body)),
                     [{ metodo: "dinheiro", amount: 132.5 }]);
    await ctx.close();
  });

  test(`${w}: sem banco, o fluxo de antes (prompt e {amount}, sem escolha)`, async () => {
    const { ctx, page } = await abrirDash(w);
    await abreEscolha(page, false);
    await page.evaluate(() => window.__fim);
    const r = await page.evaluate(() => ({
      posts: window.__posts.map((p) => p.body), prompts: window.__prompts.length,
      aberto: !!document.querySelector("#generic-confirm-overlay.open") }));
    assert.deepEqual(r, { posts: [{ amount: 132.5 }], prompts: 1, aberto: false });
    await ctx.close();
  });

  test(`${w}: modal de lançamento — copy só com banco, cabe na tela, declara carteira`, async () => {
    const { ctx, page } = await abrirDash(w);
    for (const exige of [false, true]) {
      const m = await page.evaluate((e) => {
        lastData = { ...(lastData || {}), exige_forma_pagamento: e };
        openLaunchModal({ tipo: "despesa" });
        const modal = document.querySelector("#launch-overlay .modal").getBoundingClientRect();
        const subs = [...document.querySelectorAll("#launch-overlay .tipo-sub")];
        const tipos = [...document.querySelectorAll("#launch-overlay .tipo-opt")];
        return {
          msub: document.getElementById("launch-msub").textContent,
          subsVisiveis: subs.filter((s) => s.offsetWidth > 0).length,
          tiposInteiros: tipos.every((t) => t.scrollWidth <= t.clientWidth + 1),
          dentro: modal.left >= 0 && modal.right <= innerWidth,
          alturaToggle: Math.round(document.querySelector("#launch-overlay .tipo-toggle").getBoundingClientRect().height),
        };
      }, exige);
      assert.equal(m.subsVisiveis, exige ? 2 : 0, JSON.stringify(m));
      assert.ok(exige ? m.msub.includes("Open Finance") : !m.msub.includes("Open Finance"), m.msub);
      assert.ok(m.tiposInteiros && m.dentro, JSON.stringify(m));
      console.log(`[q40] ${w} exige=${exige}`, JSON.stringify(m));
      await page.screenshot({ path: `${process.env.Q40_SHOTS || "/tmp"}/q40-lancamento-${w}-${exige}.png` });
      await page.evaluate(() => closeLaunchModal());
    }

    const corpos = await page.evaluate(async () => {
      window.closeLaunchModal = () => {}; window.showLaunchSuccessToast = () => {};
      window.readResponsePayload = async () => ({ ok: true });
      const out = [];
      for (const tipo of ["despesa", "receita"]) {
        window.__posts = [];
        openLaunchModal({ tipo });
        document.getElementById("launch-valor").value = "50";
        await submitLaunch();
        out.push(window.__posts[0].body.funding_source);
      }
      return out;
    });
    assert.deepEqual(corpos, ["carteira", "carteira"]);
    await ctx.close();
  });
}

// Conta paga pelo banco sem valor informado: `paid_amount` é null (o banco não
// pede valor; o débito vem pelo extrato). A lista de pagas não pode mostrar a
// ESTIMATIVA (`amount`) como se fosse o valor pago. Com valor, mostra o valor.
// NEGATIVO (medido): voltar ao `paid_amount ?? amount` deixa este caso vermelho.
for (const w of [1440, 390]) {
  test(`${w}: conta paga pelo banco sem valor não mostra a estimativa como pago`, async () => {
    const { ctx, page } = await abrirDash(w);
    const r = await page.evaluate(() => {
      navigateTo("fixed");
      setRecurringTab("bills");
      const base = { due_date: "2026-09-10", paid_at: "2026-09-12T10:00:00", status: "paid", variable_amount: true };
      _renderBillsView([
        { ...base, id: 1, name: "Luz", amount: 132.5, paid_amount: null, launch_id: null },
        { ...base, id: 2, name: "Água", amount: 80, paid_amount: 91.2, launch_id: 5 },
      ]);
      const lista = document.getElementById("recurring-bills-paid-list").getBoundingClientRect();
      return [...document.querySelectorAll("#recurring-bills-paid-list .tx-row")].map((row) => {
        const amt = row.querySelector(".tx-amt"), a = amt.getBoundingClientRect();
        return { texto: amt.textContent.trim(), dentro: a.left >= lista.left - 0.5 && a.right <= lista.right + 0.5,
                 inteiro: amt.scrollWidth <= amt.clientWidth + 1 };
      });
    });
    assert.equal(r.length, 2, JSON.stringify(r));
    assert.ok(!r[0].texto.includes("132"), `estimativa mostrada como pago: ${r[0].texto}`);
    assert.match(r[0].texto, /extrato/);
    assert.match(r[1].texto, /91,20/);
    assert.ok(r.every((x) => x.dentro && x.inteiro), JSON.stringify(r));
    await page.locator("#recurring-bills-paid-list").scrollIntoViewIfNeeded();
    await page.screenshot({ path: `${process.env.Q40_SHOTS || "/tmp"}/q40-pagas-${w}.png` });
    await ctx.close();
  });
}
