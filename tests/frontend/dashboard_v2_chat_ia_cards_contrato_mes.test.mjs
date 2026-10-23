// O que os cards de gasto da IA (PR 4 da Etapa 7; webapp: parts/cardIa/resumo.tsx, gastos.tsx, Expansivel.tsx) exigem do bloco e do DOM, em casos pequenos:
// o contrato dos `maiores` do período (a mesma régua do `itemOk` da lista de lançamentos) e do `ate` das categorias; o `aria-controls` das linhas que abrem; o "<1%".
// Cada grupo foi provado vermelho com a mutação do código que ele cobre (a tabela está no relato do PR, não aqui).
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { SEM_CARD, abrir, encerrar, enviar, evento, fim, fixture, iniciar, ultima } from "./_chat_ia.mjs";

before(iniciar);
after(encerrar);
const D = fixture("ai_chat_recurso_detalhes.json"), P = fixture("ai_chat_bloco_periodo.json");
const ok = P.categorias[0].maiores[0];
const pronto = (page) => page.waitForFunction(() => document.querySelector(".askbar-send")?.getAttribute("aria-label") === "Enviar", null, { timeout: 5000 });
const bloco = (card, mes, recursos) => ({ tipo: "bloco", bloco: { card, abas: [{ mes, recursos }] } });
async function responde(blocos) {
  const c = await abrir();
  await enviar(c.page, "me mostra");
  await c.flux.espera(1);
  await c.flux.push(0, evento(...blocos, fim("Texto final.")));
  await pronto(c.page);
  return c;
}
const periodoCom = (maiores) => bloco("periodo", null, { periodo: { ...P, categorias: [{ ...P.categorias[0], maiores }, P.categorias[1]] } });
const valido = bloco("categorias", "2026-10", { detalhes: D }); // o controle positivo de cada caso: o bloco válido da MESMA resposta desenha

// 1. `maiores` do período: cada entrada com a régua do `itemOk` --------------------------------------------------------------------------------------------
const RUINS = {
  null: [null], vazio: [{}], "texto": ["x"], "valor abc": [{ ...ok, valor: "abc" }], "valor 1e999": [{ ...ok, valor: "1e999" }],
  "instituicao objeto": [{ ...ok, instituicao: { x: 1 } }], "hora objeto": [{ ...ok, hora: {} }], "uma boa e uma ruim": [ok, { ...ok, id: "l11", valor: "abc" }],
};
for (const [nome, maiores] of Object.entries(RUINS)) {
  test(`periodo: maiores com entrada malformada (${nome}) vira o marcador, desde o desenho; o bloco válido do lado desenha`, async () => {
    const { page, erros } = await responde([periodoCom(maiores), valido]);
    assert.equal(await ultima(page).getByText(SEM_CARD).count(), 1);
    assert.equal(await ultima(page).locator(".card-ia").count(), 1);
    assert.doesNotMatch(await ultima(page).innerText(), /NaN|∞|Infinity|\[object/);
    assert.deepEqual(erros, []);
    await page.context().close();
  });
}
test("periodo: maiores válidos (e vazios) desenham e abrem", async () => {
  const { page, erros } = await responde([periodoCom([ok]), periodoCom([])]);
  assert.equal(await ultima(page).getByText(SEM_CARD).count(), 0);
  assert.equal(await ultima(page).locator(".card-ia").count(), 2);
  await ultima(page).locator(".card-ia .ci-cat[aria-expanded]").first().click();
  assert.equal(await ultima(page).locator(".card-ia .lanc-linha").count(), 1);
  assert.deepEqual(erros, []);
  await page.context().close();
});

// 2. `ate` das categorias: uma data que existe --------------------------------------------------------------------------------------------------------------
test("categorias: `ate` que não é uma data (\"x\", vazio, 30 de fevereiro) vira o marcador; o válido desenha \"até 22/10\"", async () => {
  const { page, erros } = await responde(["x", "", "2026-02-30"].map((ate) => bloco("categorias", "2026-10", { detalhes: { ...D, ate } })).concat(valido));
  assert.equal(await ultima(page).getByText(SEM_CARD).count(), 3);
  assert.equal(await ultima(page).locator(".card-ia").count(), 1);
  assert.equal(await ultima(page).locator(".card-ia .ci-leg").innerText(), "até 22/10");
  assert.deepEqual(erros, []);
  await page.context().close();
});

// 3. A linha que abre aponta para a região aberta ----------------------------------------------------------------------------------------------------------
for (const [card, recursos, mes, linha] of [["periodo", { periodo: P }, null, ".ci-cat[aria-expanded]"], ["calendario", { detalhes: D }, "2026-10", ".ci-dia[aria-expanded]"]]) {
  test(`${card}: a linha aberta tem aria-controls com o id da região aberta; fechada, não tem`, async () => {
    const { page } = await responde([bloco(card, mes, recursos)]);
    const b = ultima(page).locator(`.card-ia ${linha}`).first();
    assert.equal(await b.getAttribute("aria-controls"), null);
    await b.click();
    const id = await b.getAttribute("aria-controls");
    assert.ok(id, "aberta sem aria-controls");
    assert.equal(await ultima(page).locator(".card-ia .ci-aberta").getAttribute("id"), id, "o id da região aberta é o do aria-controls");
    await b.click();
    assert.equal(await b.getAttribute("aria-controls"), null);
    await page.context().close();
  });
}

// 4. "<1%": a fatia que arredonda a 0 com valor > 0 ---------------------------------------------------------------------------------------------------------
test("categorias e periodo: % que arredonda a 0 com valor > 0 mostra \"<1%\"; valor zero mostra \"0%\"", async () => {
  const cats = [{ categoria: "casa", valor: "900.00", quantidade: 1 }, { categoria: "lazer", valor: "0.50", quantidade: 1 }, { categoria: "saude", valor: "0.00", quantidade: 1 }];
  const { page } = await responde([bloco("categorias", "2026-10", { detalhes: { ...D, categorias: cats } }), bloco("periodo", null, { periodo: { ...P, categorias: cats.map((c) => ({ ...c, percentual: c.categoria === "casa" ? 100 : 0, maiores: [] })) } })]);
  for (const i of [0, 1]) {
    const linhas = await ultima(page).locator(".card-ia").nth(i).locator(".ci-cat").evaluateAll((ls) => ls.map((l) => l.innerText.replace(/\s+/g, " ").trim()));
    assert.deepEqual(linhas.map((l) => l.split(" ").slice(1, 2)[0]), ["100%", "<1%", "0%"], `card ${i}: ${linhas}`);
  }
  await page.context().close();
});

// 5. Data de outro ano nos `maiores` do período (parcela antiga, período que vira o ano): leva o ano; a do ano do último mês, não -------------------------------
test("periodo: maior lançamento de outro ano leva o ano na data; o do ano do período não", async () => {
  const { page } = await responde([periodoCom([{ ...ok, id: "velha", data: "2025-10-17" }, { ...ok, id: "nova", data: "2026-09-03" }])]);
  await page.locator(".card-ia .ci-cat[aria-expanded]").first().click();
  const sub = (id) => page.locator(`.card-ia .lanc-linha[data-id=${id}] .faint`).innerText();
  assert.match(await sub("velha"), /^17 out 2025\b/);
  assert.doesNotMatch(await sub("nova"), /2026/);
  await page.context().close();
});
