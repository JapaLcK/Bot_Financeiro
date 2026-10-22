// Os cards que a IA manda no chat do /painel (webapp: parts/CardIa.tsx e parts/cardIa/), a FUNDAÇÃO, em laço pelos 7 cards do PR 3 da Etapa 7:
// contas, hero, compromissos, patrimonio, wealth, assinaturas e metas. O bloco desenha, o inválido vira marcador, um campo fundo malformado não derruba
// a página, as abas trocam, texto do modelo é texto, o card não consulta o servidor (é foto) e a troca de conta o apaga. As AÇÕES de cada card têm os
// arquivos deles (dashboard_v2_chat_ia_cards_lancamentos / _pedido). O stream é de mentira mas é stream de verdade: ver _chat_ia.mjs.
// Cada grupo foi provado vermelho com uma mutação do código que ele cobre (a tabela está no relato do PR, não aqui).
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { RESPOSTAS, SEM_CARD, abrir, blocoDe, encerrar, enviar, evento, fim, iniciar, trocarConta, ultima } from "./_chat_ia.mjs";
import { moneyText as M } from "../../webapp/src/dashboard/lib/format.js";

before(iniciar);
after(encerrar);
const F = RESPOSTAS;
const pronto = (page) => page.waitForFunction(() => document.querySelector(".askbar-send")?.getAttribute("aria-label") === "Enviar", null, { timeout: 5000 });
const com = (r, f) => { const c = structuredClone(r); f(c); return c; };
const P = "<img src=x onerror=\"window.__xss=1\">";
const marco30 = F.previsao.pro30.marcos.find((m) => m.dias === 30);

// `quebra`: passa por objeto mas NÃO passa pelo `contrato` do card e desenharia sem lançar (então só o contrato o pega).
// `fundo`: passa pelo contrato e LANÇA no render (sem o `LimiteCard` desmontaria a raiz). Wealth e assinaturas não têm: o contrato delas já cobre tudo que o render lê.
// `injeta`: coloca o texto `p` num campo de string livre do card.
const CASOS = {
  contas: { titulo: "Contas", manchete: M(F.contas.todos_os_estados.total), recursos: { contas: F.contas.todos_os_estados },
    quebra: (r) => com(r, (c) => { c.contas.total = "lixo"; }), fundo: (r) => com(r, (c) => { c.contas.contas[0].instituicao = 5; }),
    injeta: (r, p) => com(r, (c) => { c.contas.contas[0].nome = p; }) },
  hero: { titulo: "Saldo previsto", manchete: M(marco30.saldo), recursos: { previsao: F.previsao.pro30 },
    quebra: (r) => com(r, (c) => { c.previsao.estado = 7; }), fundo: (r) => com(r, (c) => { c.previsao.marcos[0] = null; }),
    injeta: (r, p) => com(r, (c) => { c.previsao.estado = p; }) },
  compromissos: { titulo: "Próximos 30 dias", manchete: F.previsao.pro30.compromissos[0].nome, recursos: { previsao: F.previsao.pro30 },
    quebra: (r) => com(r, (c) => { c.previsao.dias = "x"; }), fundo: (r) => com(r, (c) => { c.previsao.compromissos[0] = null; }),
    injeta: (r, p) => com(r, (c) => { c.previsao.compromissos[0].nome = p; }) },
  patrimonio: { titulo: "Patrimônio", manchete: M(F.patrimonio.continuo.total), recursos: { patrimonio: F.patrimonio.continuo },
    quebra: (r) => com(r, (c) => { c.patrimonio.total = "lixo"; }), fundo: (r) => com(r, (c) => { c.patrimonio.historico = [null]; }),
    injeta: (r, p) => com(r, (c) => { c.patrimonio.motivos = [p]; }) },
  wealth: { titulo: "Onde está o dinheiro", manchete: "Contas bancárias", recursos: { patrimonio: F.patrimonio.continuo },
    quebra: (r) => com(r, (c) => { c.patrimonio.total = "lixo"; }), fundo: null,
    injeta: (r, p) => com(r, (c) => { c.patrimonio.motivos = [p]; }) },
  assinaturas: { titulo: "Assinaturas", manchete: M(F.assinaturas.cheia.total_mensal), recursos: { assinaturas: F.assinaturas.cheia },
    quebra: (r) => com(r, (c) => { c.assinaturas.total_mensal = 7; }), fundo: null,
    injeta: (r, p) => com(r, (c) => { c.assinaturas.servicos[0].nome = p; }) },
  metas: { titulo: "Metas", manchete: F.metas.varios.metas[0].nome, recursos: { metas: F.metas.varios },
    quebra: (r) => com(r, (c) => { c.metas.metas = ""; }), fundo: (r) => com(r, (c) => { c.metas.metas[0] = null; }),
    injeta: (r, p) => com(r, (c) => { c.metas.metas[0].nome = p; }) },
};
const IDS = Object.keys(CASOS);

/** Uma conversa de uma pergunta: o stream 0 recebe os blocos e o `fim`; espera o turno acabar. */
async function responde(blocos, opts) {
  const c = await abrir(opts);
  await enviar(c.page, "me mostra");
  await c.flux.espera(1);
  await c.flux.push(0, evento(...blocos, fim("Texto final.")));
  await pronto(c.page);
  return c;
}

// 1. O bloco válido desenha ------------------------------------------------------------------------------------------------------------------
for (const id of IDS) {
  test(`${id}: bloco válido desenha o card (título, manchete), antes do texto; sem link nem diálogo; sem erro de página`, async () => {
    const { page, erros } = await responde([blocoDe(id, CASOS[id].recursos)]);
    const card = ultima(page).locator(".card-ia");
    assert.equal(await card.count(), 1);
    assert.equal(await card.locator("h2").innerText(), CASOS[id].titulo);
    assert.ok((await card.innerText()).includes(CASOS[id].manchete), `manchete "${CASOS[id].manchete}" não está no card: ${await card.innerText()}`);
    assert.equal(await ultima(page).getByText(SEM_CARD).count(), 0);
    assert.equal(await card.locator("a, dialog").count(), 0, "tudo inline: nenhum link nem folha");
    assert.equal(await card.getAttribute("aria-labelledby"), await card.locator("h2").getAttribute("id"));
    assert.ok(await ultima(page).evaluate((el) => !!(el.querySelector(".card-ia").compareDocumentPosition(el.querySelector(".msg-ia")) & Node.DOCUMENT_POSITION_FOLLOWING)), "o texto do fim vem depois do card");
    assert.deepEqual(erros, []);
    await page.context().close();
  });
}

// 2. O bloco inválido vira marcador, sem número -------------------------------------------------------------------------------------------------
for (const id of IDS) {
  test(`${id}: recurso que não passa no contrato vira o marcador (sem card, sem a manchete)`, async () => {
    const { page, erros } = await responde([blocoDe(id, CASOS[id].quebra(CASOS[id].recursos))]);
    assert.equal(await ultima(page).getByText(SEM_CARD).count(), 1);
    assert.equal(await ultima(page).locator(".card-ia").count(), 0);
    assert.equal(await ultima(page).locator(".msg-ia").innerText(), "Texto final.", "a fala segue");
    assert.deepEqual(erros, []);
    await page.context().close();
  });
}

test("envelope torto: recursos vazio, lista, mês fora de AAAA-MM, card desconhecido, \"constructor\" e card de PR futuro viram um marcador cada; o válido do lado desenha", async () => {
  const bom = CASOS.assinaturas.recursos;
  const aba = (card, abas) => ({ tipo: "bloco", bloco: { card, abas } });
  const { page, erros } = await responde([
    aba("contas", [{ mes: null, recursos: {} }]), aba("contas", [{ mes: null, recursos: [] }]), aba("contas", [{ mes: "lixo", recursos: CASOS.contas.recursos }]),
    aba("contas", [{ recursos: CASOS.contas.recursos }]), aba("nao_existe", [{ mes: null, recursos: bom }]), aba("constructor", [{ mes: null, recursos: bom }]),
    aba("resumo", [{ mes: null, recursos: bom }]), blocoDe("assinaturas", bom),
  ]);
  assert.equal(await ultima(page).getByText(SEM_CARD).count(), 7);
  assert.equal(await ultima(page).locator(".card-ia").count(), 1, "controle positivo: o bloco válido da mesma resposta desenha");
  assert.deepEqual(erros, []);
  await page.context().close();
});

// 3. O LimiteCard: um campo fundo malformado não derruba a página ---------------------------------------------------------------------------------
for (const id of IDS.filter((i) => CASOS[i].fundo)) {
  test(`${id}: campo fundo malformado que passa pelo contrato vira o marcador SÓ deste card; o outro bloco, a fala e a página seguem`, async () => {
    const outro = id === "metas" ? "assinaturas" : "metas";
    const { page, erros } = await responde([blocoDe(id, CASOS[id].fundo(CASOS[id].recursos)), blocoDe(outro, CASOS[outro].recursos)]);
    assert.equal(await ultima(page).getByText(SEM_CARD).count(), 1);
    assert.equal(await ultima(page).locator(".card-ia h2").innerText(), CASOS[outro].titulo, "só o card do outro bloco desenhou");
    assert.equal(await ultima(page).locator(".msg-ia").innerText(), "Texto final.");
    assert.equal(await page.locator("#askbar-input").count(), 1, "a página segue de pé");
    assert.deepEqual(erros, [], "o erro foi contido: nada chega como erro de página");
    await page.context().close();
  });
}

// 4. As abas ----------------------------------------------------------------------------------------------------------------------------------
const soCarteira = (total) => com(F.contas.so_carteira, (c) => { c.total = total; c.carteira.saldo = total; });
const aba = (mes, total) => ({ mes, recursos: { contas: soCarteira(total) } });
const abas = (...a) => ({ tipo: "bloco", bloco: { card: "contas", abas: a } });
const radio = (page, nome) => page.getByRole("radiogroup", { name: "Mês do card" }).getByRole("radio", { name: nome, exact: true });

test("três abas: o Seg troca o conteúdo (clique e seta), o estado local reinicia e o rótulo sem mês é 1, 2, 3", async () => {
  const { page } = await responde([abas(aba(null, "100.00"), aba(null, "150.00"), aba(null, "175.00"))]);
  const card = ultima(page).locator(".card-ia");
  assert.ok((await card.innerText()).includes("R$ 100,00"));
  assert.deepEqual(await page.getByRole("radiogroup", { name: "Mês do card" }).getByRole("radio").allInnerTexts(), ["1", "2", "3"]);
  await radio(page, "2").click();
  assert.ok((await card.innerText()).includes("R$ 150,00") && !(await card.innerText()).includes("R$ 100,00"));
  assert.equal(await radio(page, "2").getAttribute("aria-checked"), "true");
  await radio(page, "2").press("ArrowRight");
  await page.waitForFunction(() => document.querySelector(".card-ia")?.innerText.includes("R$ 175,00"));
  assert.equal(await radio(page, "3").getAttribute("aria-checked"), "true");
  await page.context().close();
});

test("abas com mês: rótulo curto (ago, set, out), título com o mês; anos diferentes levam o ano (dez/25, jan/26)", async () => {
  const a = await responde([abas(aba("2026-08", "100.00"), aba("2026-09", "150.00"), aba("2026-10", "175.00"))]);
  assert.deepEqual(await a.page.getByRole("radiogroup", { name: "Mês do card" }).getByRole("radio").allInnerTexts(), ["ago", "set", "out"]);
  assert.equal(await a.page.locator(".card-ia h2").innerText(), "Contas · Agosto 2026");
  await radio(a.page, "out").click();
  assert.equal(await a.page.locator(".card-ia h2").innerText(), "Contas · Outubro 2026");
  await a.page.context().close();
  const b = await responde([abas(aba("2025-12", "100.00"), aba("2026-01", "150.00"))]);
  assert.deepEqual(await b.page.getByRole("radiogroup", { name: "Mês do card" }).getByRole("radio").allInnerTexts(), ["dez/25", "jan/26"]);
  await b.page.context().close();
});

test("uma aba inválida (a 2ª) faz o card INTEIRO virar marcador: nenhuma aba desenha", async () => {
  const { page } = await responde([abas(aba(null, "100.00"), { mes: null, recursos: { contas: { ...soCarteira("150.00"), total: "lixo" } } }, aba(null, "175.00"))]);
  assert.equal(await ultima(page).getByText(SEM_CARD).count(), 1);
  assert.equal(await ultima(page).locator(".card-ia").count(), 0);
  await page.context().close();
});

test("o botão do Seg tem 26px, mas a área de toque do card é de 44px (acima e abaixo do botão o clique ainda é dele)", async () => {
  const { page } = await responde([abas(aba(null, "100.00"), aba(null, "150.00"))]);
  const r = await radio(page, "2").boundingBox();
  const quem = (y) => page.evaluate(([x, y]) => document.elementFromPoint(x, y)?.closest("[role=radio]")?.textContent ?? null, [r.x + r.width / 2, y]);
  assert.equal(await quem(r.y - 8), "2");
  assert.equal(await quem(r.y + r.height + 8), "2");
  assert.equal(await quem(r.y - 12), null, "controle: mais longe que 9px já não é do botão");
  await page.context().close();
});

// 5. Texto do modelo é só texto ---------------------------------------------------------------------------------------------------------------------
for (const id of IDS) {
  test(`${id}: HTML num campo de texto livre aparece como texto (nenhum elemento, nenhum script)`, async () => {
    const { page, erros } = await responde([blocoDe(id, CASOS[id].injeta(CASOS[id].recursos, P))]);
    const card = ultima(page).locator(".card-ia");
    assert.ok((await card.innerText()).includes(P), "o literal tem de aparecer no card");
    assert.equal(await page.locator("img[src=x]").count(), 0, "o <img> do texto não virou elemento (o logo do banco é outro <img>)");
    await page.waitForTimeout(300);
    assert.equal(await page.evaluate(() => window.__xss), undefined);
    assert.deepEqual(erros, []);
    await page.context().close();
  });
}

// 6. Isolamento: o card é uma foto do bloco ---------------------------------------------------------------------------------------------------
const ROTAS = { contas: "contas", previsao: "previsao", patrimonio: "patrimonio", assinaturas: "assinaturas", metas: "metas" };
const MARCA_DA_ROTA = {
  contas: (r) => com(r, (c) => { c.contas[0].nome = "DA-ROTA"; }), previsao: (r) => com(r, (c) => { c.compromissos[0].nome = "DA-ROTA"; }),
  patrimonio: (r) => com(r, (c) => { c.motivos = ["DA-ROTA"]; }), assinaturas: (r) => com(r, (c) => { c.servicos[0].nome = "DA-ROTA"; }), metas: (r) => com(r, (c) => { c.metas[0].nome = "DA-ROTA"; }),
};
const DADOS = { contas: F.contas.todos_os_estados, previsao: F.previsao.pro30, patrimonio: F.patrimonio.continuo, assinaturas: F.assinaturas.cheia, metas: F.metas.varios };

test("os 7 cards desenham SÓ o bloco: com as rotas dos widgets respondendo outro dado, nenhuma é chamada e nada da rota aparece", async () => {
  const c = await abrir();
  const chamadas = [];
  for (const rota of Object.keys(ROTAS)) {
    await c.ctx.route(`**/api/v2/${rota}*`, (r) => { chamadas.push(rota); return r.fulfill({ json: MARCA_DA_ROTA[rota](DADOS[rota]) }); });
  }
  const antes = chamadas.length;
  await enviar(c.page, "me mostra");
  await c.flux.espera(1);
  await c.flux.push(0, evento(...IDS.map((id) => blocoDe(id, CASOS[id].injeta(CASOS[id].recursos, "DO-BLOCO"))), fim("Texto final.")));
  await pronto(c.page);
  await c.page.waitForTimeout(400);
  assert.equal(await ultima(c.page).locator(".card-ia").count(), 7);
  const texto = await ultima(c.page).innerText();
  assert.equal((texto.match(/DO-BLOCO/g) ?? []).length, 7, "cada card mostra o dado do bloco");
  assert.ok(!texto.includes("DA-ROTA"), "nada da rota apareceu");
  assert.deepEqual(chamadas.slice(antes), [], "nenhuma rota de widget foi chamada entre o bloco e o fim");
  await c.page.context().close();
});

test("a troca de conta apaga o card de A, e um bloco atrasado do stream de A não aparece", async () => {
  const { page, flux, conta, erros } = await abrir();
  await enviar(page, "me mostra");
  await flux.espera(1);
  await flux.ignoraAbort();
  await flux.push(0, evento(blocoDe("assinaturas", CASOS.assinaturas.recursos)));
  await ultima(page).locator(".card-ia").waitFor();
  await trocarConta(page, conta, 2);
  await page.waitForFunction(() => document.querySelectorAll(".chat > .msg-piggy").length === 0);
  assert.equal(await page.locator(".card-ia").count(), 0, "o card de A sumiu com a conversa");
  await flux.push(0, evento(blocoDe("contas", CASOS.contas.recursos), fim("Texto de A.")));
  await page.waitForTimeout(400);
  assert.equal(await page.locator(".card-ia").count(), 0, "o bloco atrasado de A não entra na conversa nova");
  assert.equal(await page.getByText("Texto de A.").count(), 0);
  assert.deepEqual(erros, []);
  await page.context().close();
});

// 7. O esqueleto pela `previa` -------------------------------------------------------------------------------------------------------------------
test("a `previa` do passo mostra o esqueleto até o bloco chegar; sem `previa`, ou depois do fim, ele não aparece", async () => {
  const { page, flux } = await abrir();
  const esq = page.locator(".card-ia-esqueleto");
  await enviar(page, "me mostra");
  await flux.espera(1);
  await flux.push(0, evento({ tipo: "passo", texto: "Olhando suas contas…", previa: null }));
  await ultima(page).getByText("Olhando suas contas…").first().waitFor();
  assert.equal(await esq.count(), 0, "sem `previa`: sem esqueleto");
  await flux.push(0, evento({ tipo: "passo", texto: "Montando o card…", previa: "contas" }));
  await esq.waitFor();
  assert.equal(await esq.getAttribute("aria-hidden"), "true");
  await flux.push(0, evento(blocoDe("contas", CASOS.contas.recursos)));
  await ultima(page).locator(".card-ia").waitFor();
  assert.equal(await esq.count(), 0, "o bloco da `previa` chegou: o esqueleto sai");
  await flux.push(0, evento({ tipo: "passo", texto: "Mais um…", previa: "metas" }));
  await esq.waitFor();
  await flux.push(0, evento(fim("Texto final.")));
  await pronto(page);
  assert.equal(await esq.count(), 0, "o fim fecha o turno: nenhum esqueleto fica");
  await page.context().close();
});

// 8. Geometria, nas duas larguras --------------------------------------------------------------------------------------------------------------
for (const w of [390, 1440]) {
  test(`${w}px: os 7 cards cabem (sem rolagem lateral, dentro da bolha), os alvos de toque têm 44px, e abrir o que abre não estoura`, async () => {
    const { page, erros } = await responde(IDS.map((id) => blocoDe(id, CASOS[id].recursos)), { width: w });
    const medir = () => page.evaluate(() => [...document.querySelectorAll(".card-ia")].map((card) => {
      const li = card.closest("li") ?? card.parentElement, r = card.getBoundingClientRect(), l = li.getBoundingClientRect();
      return {
        h: card.querySelector("h2").textContent, docW: document.documentElement.scrollWidth, innerW: innerWidth, card: card.scrollWidth - card.clientWidth, fora: Math.round(r.right - l.right),
        baixos: [...card.querySelectorAll("button, a, [role=button]")].filter((e) => e.offsetParent && !e.closest(".seg") && e.getBoundingClientRect().height < 43.5).map((e) => (e.getAttribute("aria-label") ?? e.textContent).slice(0, 30)),
      };
    }));
    const confere = (ms, quando) => { for (const m of ms) { assert.ok(m.docW <= m.innerW, `${m.h} ${quando}: rolagem lateral (${m.docW} > ${m.innerW})`); assert.ok(m.card <= 0, `${m.h} ${quando}: conteúdo vaza o card`); assert.ok(m.fora <= 0, `${m.h} ${quando}: card fora da bolha`); assert.deepEqual(m.baixos, [], `${m.h} ${quando}: alvo < 44px`); } };
    const fechado = await medir();
    assert.equal(fechado.length, 7);
    confere(fechado, "fechado");
    for (const b of await page.locator(".card-ia button[aria-expanded]").all()) await b.click();
    await page.waitForTimeout(300);
    confere(await medir(), "aberto");
    assert.deepEqual(erros, []);
    await page.context().close();
  });
}

test("ids: o mesmo card em duas respostas não repete id (aria-labelledby e aria-controls ficam únicos)", async () => {
  const { page, flux } = await abrir();
  for (const i of [0, 1]) {
    await enviar(page, `me mostra ${i}`);
    await flux.espera(i + 1);
    await flux.push(i, evento(blocoDe("contas", CASOS.contas.recursos), blocoDe("compromissos", CASOS.compromissos.recursos), fim(`Texto ${i}.`)));
    await pronto(page);
  }
  for (const b of await page.locator(".card-ia button[aria-expanded]").all()) await b.click();
  const repetidos = await page.evaluate(() => { const ids = [...document.querySelectorAll("[id]")].map((e) => e.id), vistos = new Set(); return ids.filter((x) => vistos.size === vistos.add(x).size); });
  assert.deepEqual(repetidos, []);
  assert.equal(await page.locator(".card-ia").count(), 4);
  await page.context().close();
});
