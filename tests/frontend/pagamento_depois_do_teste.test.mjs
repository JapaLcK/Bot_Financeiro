/**
 * A linha "depois" do resumo da página de pagamento com cupom AUTOMÁTICO de 1ª cobrança (Black Friday, link E1/E2/E3).
 * Medido em 2026-11-06 (Stripe.js real, 4 Checkouts de teste com test clock): com o e-book (one_time) no carrinho o
 * `dueNext` chega SEM desconto (subtotal 19900, discount 0, total 19900) e a tela dizia "Depois do teste: R$ 199,00",
 * enquanto a fatura do dia 8 cobra 119,40. Os dados falsos abaixo têm esse formato.
 * Controles (2026-11-06, mutação em produção e revertida, grupo inteiro rodado a cada uma):
 *  - negativo: `proxima` devolvendo sempre `{depois: n.total}` -> 11 vermelhos: o 1 (anual/mensal, marcar/desmarcar,
 *    sessão reaproveitada), o 2, o 2c, o 4 e o 5 (com e-book mostrava "Depois do teste: R$ 199,00/ano");
 *  - mutação por guarda (cada uma derruba pelo menos um caso): sem `!d.promotionCode`, sem `td > 0`, sem `td < 28`,
 *    sem `!(hoje > 0)`, sem `type === "repeating"`, sem `durationInMonths === 1`, sem `discount > 0`, o arredondamento do DESCONTO trocado por `Math.ceil` do total (sub*(100-pct)/100);
 *  - positivo: o 2 (sem e-book) e o 3 (sem trial) mantêm o número do Stripe.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { abrirPagina, caixas, PAGINA } from "./_assinar.mjs";

let browser;
before(async () => { browser = await chromium.launch(); });
after(async () => { await browser?.close(); });

// Formato do SDK real (medido 2026-11-06): dinheiro `{amount, minorUnitsAmount}`; `recurring` null no once.
const m = (c) => ({ amount: "R$ " + (c / 100).toFixed(2).replace(".", ","), minorUnitsAmount: c });
const AUTO = (pct = 40, rec = { type: "repeating", durationInMonths: 1 }) => ({ promotionCode: null, percentOff: pct, amount: m(0).amount, displayName: pct + "% OFF", recurring: rec });
const PLUS = { name: "PigBank Plus", total: m(0) };
const EBOOK = { name: "Saia do vermelho", total: m(2700) };
// `ebook`: a sessão com o e-book (dueNext sem desconto, como o SDK mandou); sem ele, o desconto vem no dueNext.
const sessao = (sub, { pct = 40, ebook = false, auto = [AUTO(pct)], dueNext } = {}) => {
  const d = Math.round(sub * pct / 100);
  return { lineItems: ebook ? [PLUS, EBOOK] : [PLUS], total: { total: m(ebook ? 2700 : 0) }, discountAmounts: auto,
    recurring: { dueNext: dueNext || { subtotal: m(sub), discount: m(ebook ? 0 : d), total: m(ebook ? sub : sub - d) } } };
};

async function abre({ trial = 15, interval = "annual", sdk }) {
  const cria = (req) => (req.body.embutido ? [200, { ...PAGINA()[1], trial_days: trial, interval }] : [200, { checkout_url: "x" }]);
  return abrirPagina(browser, { sdk, api: { "POST /billing/create-checkout": cria } });
}
const linha = async (page) => (await page.locator("#pp-resumo .pp-r-depois").textContent()).replace(/\u00a0/g, " ");
const semLinha = (page) => page.locator("#pp-resumo .pp-r-depois").count();

const ANUAL = [19900, "R$ 119,40 na 1ª cobrança, depois R$ 199,00/ano", "annual"];
const MENSAL = [1990, "R$ 11,94 na 1ª cobrança, depois R$ 19,90/mês", "monthly"];

for (const [sub, texto, interval] of [ANUAL, MENSAL]) {
  test(`1. campanha 40%, trial, e-book (${interval}): marcar e desmarcar`, async () => {
    const { ctx, page } = await abre({ interval, sdk: { sessao: sessao(sub), depoisBump: [sessao(sub, { ebook: true }), sessao(sub)] } });
    assert.equal(await linha(page), texto);  // sem e-book: o número do Stripe
    await caixas(page).nth(0).click();
    await page.waitForFunction(() => /Total hoje\s*R\$\s*27,00/.test(document.getElementById("pp-resumo").textContent.replace(/\u00a0/g, " ")));
    assert.equal(await linha(page), texto);  // com e-book: o número calculado
    await caixas(page).nth(0).click();
    await page.waitForFunction(() => /Total hoje\s*R\$\s*0,00/.test(document.getElementById("pp-resumo").textContent.replace(/\u00a0/g, " ")));
    assert.equal(await linha(page), texto);
    await ctx.close();
  });

  test(`1. sessão reaproveitada com e-book já no carrinho (${interval}): change e getSession`, async () => {
    for (const semChange of [false, true]) {
      const { ctx, page } = await abre({ interval, sdk: { sessao: sessao(sub, { ebook: true }), semChange } });
      assert.equal(await linha(page), texto, "semChange=" + semChange);
      await ctx.close();
    }
  });
}

test("2. trial sem e-book: o total do Stripe na linha nova", async () => {
  const { ctx, page } = await abre({ sdk: { sessao: sessao(19900, { pct: 40 }) } });
  assert.equal(await linha(page), "R$ 119,40 na 1ª cobrança, depois R$ 199,00/ano");
  await ctx.close();
});

test("2b. sem cobrança hoje e dueNext sem desconto: linha atual (a conta só vale com o e-book)", async () => {
  const dueNext = { subtotal: m(19900), discount: m(0), total: m(19900) };
  const { ctx, page } = await abre({ sdk: { sessao: sessao(19900, { dueNext }) } });
  assert.equal(await linha(page), "Depois do teste: R$ 199,00/ano");
  await ctx.close();
});

test("2c. o Stripe mandou o desconto com o e-book: o número dele ganha (não a conta)", async () => {
  const dueNext = { subtotal: m(19900), discount: m(5000), total: m(14900) };
  const { ctx, page } = await abre({ sdk: { sessao: sessao(19900, { ebook: true, dueNext }) } });
  assert.equal(await linha(page), "R$ 149,00 na 1ª cobrança, depois R$ 199,00/ano");
  await ctx.close();
});

test("3. sem trial com e-book: linha atual", async () => {
  const { ctx, page } = await abre({ trial: 0, sdk: { sessao: sessao(19900, { ebook: true }) } });
  assert.equal(await linha(page), "Depois: R$ 199,00/ano");
  await ctx.close();
});

for (const [pct, esperado] of [[10, "R$ 17,91"], [20, "R$ 15,92"]]) {
  test(`4. link ${pct}% mensal Plus com e-book: ${esperado}`, async () => {
    const { ctx, page } = await abre({ interval: "monthly", sdk: { sessao: sessao(1990, { pct, ebook: true }) } });
    assert.equal(await linha(page), `${esperado} na 1ª cobrança, depois R$ 19,90/mês`);
    await ctx.close();
  });
}

for (const [sub, esperado] of [[990, "R$ 9,40"], [1990, "R$ 18,90"], [4990, "R$ 47,40"]]) {
  test(`5. link 5% mensal ${sub} com e-book: o Stripe arredonda o desconto (${esperado})`, async () => {
    const { ctx, page } = await abre({ interval: "monthly", sdk: { sessao: sessao(sub, { pct: 5, ebook: true }) } });
    assert.equal(await linha(page), `${esperado} na 1ª cobrança, depois ${(sub / 100).toFixed(2).replace(".", ",").replace(/^/, "R$ ")}/mês`);
    await ctx.close();
  });
}

for (const [dias, ebook] of [[28, true], [30, true], [28, false], [90, false]]) {
  test(`6. trial de ${dias} dias${ebook ? " com e-book" : ""}: linha atual`, async () => {
    const { ctx, page } = await abre({ trial: dias, sdk: { sessao: sessao(19900, { ebook }) } });
    assert.match(await linha(page), /^Depois do teste: R\$ (199,00|119,40)\/ano$/);
    assert.doesNotMatch(await linha(page), /1ª cobrança/);
    await ctx.close();
  });
}

test("7. campos faltando: linha atual ou nenhuma, sem quebrar", async () => {
  const base = sessao(19900, { ebook: true });
  const casos = [
    ["sem subtotal", { ...base, recurring: { dueNext: { total: m(19900), discount: m(0) } } }, "Depois do teste: R$ 199,00/ano"],
    ["sem discountAmounts", { ...base, discountAmounts: undefined }, "Depois do teste: R$ 199,00/ano"],
    ["discountAmounts com null", { ...base, discountAmounts: [null] }, "Depois do teste: R$ 199,00/ano"],
    ["sem recurring", { ...base, recurring: undefined }, null],
    ["sem dueNext.total", { ...base, recurring: { dueNext: { subtotal: m(19900) } } }, null],
    ["desconto sem recurring", { ...base, discountAmounts: [{ promotionCode: null, percentOff: 40 }] }, "Depois do teste: R$ 199,00/ano"],
  ];
  for (const [nome, s, texto] of casos) {
    const { ctx, page } = await abre({ sdk: { sessao: s } });
    if (texto === null) assert.equal(await semLinha(page), 0, nome);
    else assert.equal(await linha(page), texto, nome);
    await ctx.close();
  }
});

test("8/10. código digitado (once, repeating, forever): linha atual", async () => {
  const rec = [null, { type: "repeating", durationInMonths: 1 }, { type: "repeating", durationInMonths: 6 }, { type: "forever" }];
  for (const r of rec) {
    const auto = [{ promotionCode: "PIGGY40", percentOff: 40, amount: m(0).amount, displayName: "PIGGY40", recurring: r }];
    const { ctx, page } = await abre({ sdk: { sessao: sessao(19900, { ebook: true, auto }) } });
    assert.equal(await linha(page), "Depois do teste: R$ 199,00/ano", JSON.stringify(r));
    await ctx.close();
  }
});

test("8b. código digitado com trial sem e-book: discountAmounts vazio, linha atual", async () => {
  const { ctx, page } = await abre({ sdk: { sessao: sessao(19900, { auto: [] }) } });
  assert.equal(await linha(page), "Depois do teste: R$ 119,40/ano");
  await ctx.close();
});

test("9. cupom automático que não é de 1ª cobrança (once, forever, 3 meses; o `type` sozinho também, com durationInMonths:1): linha atual", async () => {
  for (const r of [null, { type: "forever" }, { type: "forever", durationInMonths: 1 }, { type: "repeating", durationInMonths: 3 }]) {
    const { ctx, page } = await abre({ sdk: { sessao: sessao(19900, { ebook: true, auto: [AUTO(40, r)] }) } });
    assert.equal(await linha(page), "Depois do teste: R$ 199,00/ano", JSON.stringify(r));
    await ctx.close();
  }
});

test("11. amount_off (sem percentOff): linha atual; sem e-book, o Stripe mandou o desconto e o total dele vale", async () => {
  const auto = [{ promotionCode: null, percentOff: null, amount: m(5000).amount, displayName: "R$ 50 OFF", recurring: { type: "repeating", durationInMonths: 1 } }];
  let { ctx, page } = await abre({ sdk: { sessao: sessao(19900, { ebook: true, auto }) } });
  assert.equal(await linha(page), "Depois do teste: R$ 199,00/ano");
  await ctx.close();
  // Discrimina a guarda `percentOff > 0`: sem ela a conta daria `sub` e cairia na mesma linha antiga por outro caminho.
  const dueNext = { subtotal: m(19900), discount: m(5000), total: m(14900) };
  ({ ctx, page } = await abre({ sdk: { sessao: sessao(19900, { auto, dueNext }) } }));
  assert.equal(await linha(page), "Depois do teste: R$ 149,00/ano");
  await ctx.close();
});
