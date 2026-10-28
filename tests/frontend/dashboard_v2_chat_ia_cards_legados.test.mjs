// Os 6 cards "legados" da IA no chat do /painel (PR 6 da Etapa 7; webapp: parts/cardIa/{caixinhas,cartao,renda,piggy,rendimento}.tsx): caixinhas, fatura, parcelas, renda, piggy e
// rendimento. A FUNDAÇÃO do PR 3 (dashboard_v2_chat_ia_cards.test.mjs) em laço pelos 6: o bloco válido, o contrato torto, o LimiteCard, o HTML do modelo, nenhuma rota, 390 e 1440, ids.
// O que é de cada card: dashboard_v2_chat_ia_cards_legados_b.test.mjs. Os 5 legados chegam em float (sem Pydantic): o contrato confere TODO número desenhado, e "R$ NaN" e "∞" nunca
// chegam à tela. Cada grupo foi provado vermelho com uma mutação do código que ele cobre (a tabela está no relato do PR, não aqui).
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { SEM_CARD, abrir, encerrar, enviar, evento, fim, iniciar, ultima } from "./_chat_ia.mjs";
import { CASOS, FX, IDS, P, abreTudo, blocoDoCaso, blocoInf, com, pronto, responde } from "./_chat_ia_legados.mjs";

before(iniciar);
after(encerrar);

// 1. O bloco válido desenha -----------------------------------------------------------------------------------------------------------------------
for (const id of IDS) {
  test(`${id}: bloco válido desenha o card (título, manchete), antes do texto; sem link nem diálogo; sem erro de página`, async () => {
    const { page, erros } = await responde([blocoDoCaso(id)]);
    const card = ultima(page).locator(".card-ia");
    assert.equal(await card.count(), 1);
    assert.equal(await card.locator("h2").innerText(), CASOS[id].titulo);
    assert.ok((await card.innerText()).includes(CASOS[id].manchete), `manchete "${CASOS[id].manchete}" não está no card: ${await card.innerText()}`);
    assert.equal(await ultima(page).getByText(SEM_CARD).count(), 0);
    assert.equal(await card.locator("a, dialog").count(), 0, "tudo inline: nenhum link nem folha");
    assert.ok(await ultima(page).evaluate((el) => !!(el.querySelector(".card-ia").compareDocumentPosition(el.querySelector(".msg-ia")) & Node.DOCUMENT_POSITION_FOLLOWING)), "o texto do fim vem depois do card");
    assert.deepEqual(erros, []);
    await page.context().close();
  });
}

// 2. O bloco inválido vira marcador, sem número ---------------------------------------------------------------------------------------------------------
for (const id of IDS) {
  test(`${id}: recurso que não passa no contrato vira o marcador (sem card, sem a manchete)`, async () => {
    const { page, erros } = await responde([blocoDoCaso(id, CASOS[id].quebra(CASOS[id].recursos))]);
    assert.equal(await ultima(page).getByText(SEM_CARD).count(), 1);
    assert.equal(await ultima(page).locator(".card-ia").count(), 0);
    assert.equal(await ultima(page).locator(".msg-ia").innerText(), "Texto final.", "a fala segue");
    assert.deepEqual(erros, []);
    await page.context().close();
  });
}
test("contrato: lista que não é lista, id sem data, dinheiro que é texto, null onde não pode e `ok` falso viram marcador (um cada); o válido do lado desenha", async () => {
  const ruins = [
    blocoDoCaso("caixinhas", { caixinhas: { ...FX.caixinhas, goals: "x" } }), blocoDoCaso("caixinhas", { caixinhas: { ...FX.caixinhas, ok: false } }),
    blocoDoCaso("fatura", { cartoes: com(FX.cartoes, (c) => { c.cards[0].open_bill.due_amount = "10"; }) }), blocoDoCaso("fatura", { cartoes: com(FX.cartoes, (c) => { c.cards[0].due_day = 17.5; }) }),
    // O chat segue "só float": texto decimal válido na /api/v2 (o do Resumo) é recusado aqui ("10" acima já cai pelo formato do texto, não por ser texto).
    blocoDoCaso("fatura", { cartoes: com(FX.cartoes, (c) => { c.cards[0].credit_used = "10.00"; }) }),
    blocoDoCaso("parcelas", { parcelas: com(FX.parcelas, (c) => { c.installments[0].parcelas = null; }) }), blocoDoCaso("parcelas", { parcelas: com(FX.parcelas, (c) => { c.installments[0].parcelas[0].is_paid = "sim"; }) }),
    blocoDoCaso("renda", { renda: com(FX.renda, (c) => { c.evolution[0].expense = null; }) }), blocoDoCaso("renda", { renda: { ...FX.renda, evolution: {} } }),
    blocoDoCaso("piggy", { insights: com(FX.insights, (c) => { c.insights[1].message = null; }) }), blocoDoCaso("piggy", { insights: [] }),
    blocoDoCaso("rendimento", { rendimento: com(FX.rendimento, (c) => { c.rendimentos.categorias[0].ativos = "x"; }) }),
    blocoDoCaso("rendimento", { rendimento: com(FX.rendimento, (c) => { c.tipo = "outro"; }) }),
    blocoDoCaso("rendimento", { rendimento: com(FX.rendimento, (c) => { c.rendimentos.categorias[0].ativos[0].saldo = 5; }) }),
  ];
  const { page, erros } = await responde([...ruins, blocoDoCaso("renda")]);
  assert.equal(await ultima(page).getByText(SEM_CARD).count(), ruins.length);
  assert.equal(await ultima(page).locator(".card-ia").count(), 1, "controle positivo: o bloco válido da mesma resposta desenha");
  assert.deepEqual(erros, []);
  await page.context().close();
});
test("dinheiro não finito (1e999 no JSON) vira marcador nos 4 cards de float (caixinhas, fatura, renda, parcelas), nunca \"R$ ∞\" nem \"NaN\"; o card válido da mesma resposta desenha", async () => {
  const inf = (r, f) => com(r, f);
  const { page, erros } = await responde([blocoDoCaso("piggy")], undefined, [
    blocoInf("caixinhas", { caixinhas: inf(FX.caixinhas, (c) => { c.goals[0].balance = "__INF__"; }) }), blocoInf("fatura", { cartoes: inf(FX.cartoes, (c) => { c.cards[0].credit_used = "__INF__"; }) }),
    blocoInf("renda", { renda: inf(FX.renda, (c) => { c.evolution[1].income = "__INF__"; }) }), blocoInf("parcelas", { parcelas: inf(FX.parcelas, (c) => { c.installments[0].parcelas[0].valor = "__INF__"; }) }),
  ].join(""));
  assert.equal(await ultima(page).getByText(SEM_CARD).count(), 4);
  assert.equal(await ultima(page).locator(".card-ia").count(), 1, "controle positivo: só o piggy, que é válido, desenhou");
  assert.ok(!/∞|NaN/.test(await ultima(page).innerText()));
  assert.deepEqual(erros, []);
  await page.context().close();
});

// 3. O LimiteCard ---------------------------------------------------------------------------------------------------------------------------------
for (const id of IDS.filter((i) => CASOS[i].fundo)) {
  test(`${id}: campo fundo malformado que passa pelo contrato vira o marcador SÓ deste card; o outro bloco, a fala e a página seguem`, async () => {
    const { page, erros } = await responde([blocoDoCaso(id, CASOS[id].fundo(CASOS[id].recursos)), blocoDoCaso("piggy")]);
    assert.equal(await ultima(page).getByText(SEM_CARD).count(), 1);
    assert.equal(await ultima(page).locator(".card-ia h2").innerText(), CASOS.piggy.titulo, "só o card do outro bloco desenhou");
    assert.equal(await ultima(page).locator(".msg-ia").innerText(), "Texto final.");
    assert.equal(await page.locator("#askbar-input").count(), 1, "a página segue de pé");
    assert.deepEqual(erros, [], "o erro foi contido: nada chega como erro de página");
    await page.context().close();
  });
}

// 4. Texto do modelo é só texto ---------------------------------------------------------------------------------------------------------------------
for (const id of IDS.filter((i) => CASOS[i].injeta)) {
  test(`${id}: HTML num campo de texto livre aparece como texto (nenhum elemento, nenhum script)`, async () => {
    const { page, erros } = await responde([blocoDoCaso(id, CASOS[id].injeta(CASOS[id].recursos, P))]);
    const card = ultima(page).locator(".card-ia");
    for (const b of await card.locator(".ci-mais").all()) await b.click();
    assert.ok((await card.innerText()).includes(P), "o literal tem de aparecer no card");
    assert.equal(await page.locator("img[src=x]").count(), 0, "o <img> do texto não virou elemento");
    await page.waitForTimeout(300);
    assert.equal(await page.evaluate(() => window.__xss), undefined);
    assert.deepEqual(erros, []);
    await page.context().close();
  });
}

// 5. O card é uma foto do bloco -----------------------------------------------------------------------------------------------------------------------
test("os 6 cards desenham SÓ o bloco: com as rotas dos widgets respondendo outro dado, nenhuma é chamada, nada da rota aparece e abrir tudo também não chama; só o bloco aparece", async () => {
  const c = await abrir();
  const chamadas = [];
  for (const rota of ["cards/**", "installments/**", "analytics/**", "insights/**", "goals/**", "api/v2/**", "api/app/**"]) {
    await c.ctx.route(`**/${rota}`, (r) => { chamadas.push(new URL(r.request().url()).pathname); return r.fulfill({ json: { ok: true, goals: [], cards: [], installments: [], evolution: [], insights: [], DAROTA: "DA-ROTA" } }); });
  }
  const antes = chamadas.length;
  await enviar(c.page, "me mostra");
  await c.flux.espera(1);
  await c.flux.push(0, evento(...IDS.map((id) => blocoDoCaso(id, CASOS[id].injeta ? CASOS[id].injeta(CASOS[id].recursos, "DO-BLOCO") : CASOS[id].recursos)), fim("Texto final.")));
  await pronto(c.page);
  await abreTudo(c.page);
  await c.page.waitForTimeout(400);
  assert.equal(await ultima(c.page).locator(".card-ia").count(), 6);
  assert.ok(!(await ultima(c.page).innerText()).includes("DA-ROTA"), "nada da rota apareceu");
  assert.ok((await ultima(c.page).innerText()).includes("DO-BLOCO"));
  assert.deepEqual(chamadas.slice(antes), [], "nenhuma rota foi chamada, nem ao abrir tudo");
  await c.page.context().close();
});

// 6. Geometria, nas duas larguras --------------------------------------------------------------------------------------------------------------------
for (const w of [390, 1440]) {
  test(`${w}px: os 6 cards cabem (sem rolagem lateral, dentro da bolha), os alvos de toque têm 44px, e abrir o que abre não estoura`, async () => {
    const { page, erros } = await responde(IDS.map((id) => blocoDoCaso(id)), { width: w });
    const medir = () => page.evaluate(() => [...document.querySelectorAll(".card-ia")].map((card) => {
      const li = card.closest("li") ?? card.parentElement, r = card.getBoundingClientRect(), l = li.getBoundingClientRect();
      return {
        h: card.querySelector("h2").textContent, docW: document.documentElement.scrollWidth, innerW: innerWidth, card: card.scrollWidth - card.clientWidth, fora: Math.round(r.right - l.right),
        baixos: [...card.querySelectorAll("button, a, [role=button]")].filter((e) => e.offsetParent && !e.closest(".seg") && e.getBoundingClientRect().height < 43.5).map((e) => (e.getAttribute("aria-label") ?? e.textContent).slice(0, 30)),
      };
    }));
    const confere = (ms, quando) => { for (const m of ms) { assert.ok(m.docW <= m.innerW, `${m.h} ${quando}: rolagem lateral (${m.docW} > ${m.innerW})`); assert.ok(m.card <= 0, `${m.h} ${quando}: conteúdo vaza o card`); assert.ok(m.fora <= 0, `${m.h} ${quando}: card fora da bolha`); assert.deepEqual(m.baixos, [], `${m.h} ${quando}: alvo < 44px`); } };
    const fechado = await medir();
    assert.equal(fechado.length, 6);
    confere(fechado, "fechado");
    await abreTudo(page);
    await page.waitForTimeout(400);
    confere(await medir(), "aberto");
    // a maior abertura: a 1ª compra e a 1ª categoria do rendimento (7 ativos) com o "+2" aberto
    await page.locator(".card-ia .ci-compra").first().click();
    await page.locator(".card-ia .ci-rend").first().click();
    for (const h of await page.locator(".card-ia .ci-mais[aria-expanded=false]").elementHandles()) await h.click();
    await page.waitForTimeout(300);
    assert.equal(await page.locator(".card-ia .ci-rend .ci-sub, .card-ia .ci-aberta .ci-sub").count() > 7, true, "o teste só vale com as parcelas e os ativos abertos");
    confere(await medir(), "aberto ao máximo");
    assert.deepEqual(erros, []);
    await page.context().close();
  });
}

test("ids: os 6 cards em duas respostas não repetem id (aria-labelledby e aria-controls ficam únicos), tudo aberto", async () => {
  const { page, flux } = await abrir();
  for (const i of [0, 1]) {
    await enviar(page, `me mostra ${i}`);
    await flux.espera(i + 1);
    await flux.push(i, evento(...IDS.map((id) => blocoDoCaso(id)), fim(`Texto ${i}.`)));
    await pronto(page);
  }
  await abreTudo(page);
  await page.waitForTimeout(300);
  const repetidos = await page.evaluate(() => { const ids = [...document.querySelectorAll("[id]")].map((e) => e.id), vistos = new Set(); return ids.filter((x) => vistos.size === vistos.add(x).size); });
  assert.deepEqual(repetidos, []);
  assert.equal(await page.locator(".card-ia").count(), 12);
  assert.equal(await page.locator(".card-ia [aria-controls]").count(), 4, "o teste só vale se há o que abrir: a compra e a categoria abertas, nas duas respostas");
  await page.context().close();
});
