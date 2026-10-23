// Os 4 cards de GASTO da IA no chat do /painel (PR 4 da Etapa 7; webapp: parts/cardIa/gastos.tsx e resumo.tsx): resumo, categorias, calendario e periodo. A
// FUNDAÇÃO do PR 3 (dashboard_v2_chat_ia_cards.test.mjs) em laço pelos 4, mais o que é de cada um: "Outras", os dias, os meses do período, o saldo negativo. A busca de
// lançamentos ao tocar numa categoria ou num dia (e a guarda de conta) é do dashboard_v2_chat_ia_cards_lancamentos_mes.test.mjs. Fixtures: o dump do Pydantic
// (tests/fixtures/ai_chat_recurso_detalhes.json, ai_chat_bloco_periodo.json, conferidas por tests/test_ai_chat_cards_fixtures_painel.py) e RESPOSTAS.resumo_do_mes.
// Cada grupo foi provado vermelho com uma mutação do código que ele cobre (a tabela está no relato do PR, não aqui).
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { RESPOSTAS, SEM_CARD, abrir, encerrar, enviar, evento, fim, fixture, iniciar, ultima } from "./_chat_ia.mjs";

before(iniciar);
after(encerrar);
const F = RESPOSTAS;
const D = fixture("ai_chat_recurso_detalhes.json"), PERIODO = fixture("ai_chat_bloco_periodo.json");
const pronto = (page) => page.waitForFunction(() => document.querySelector(".askbar-send")?.getAttribute("aria-label") === "Enviar", null, { timeout: 5000 });
const com = (r, f) => { const c = structuredClone(r); f(c); return c; };
const P = "<img src=x onerror=\"window.__xss=1\">";
const bloco = (card, mes, recursos) => ({ tipo: "bloco", bloco: { card, abas: [{ mes, recursos }] } });
const meses12 = Array.from({ length: 12 }, (_, i) => ({ mes: `${2025 + (i > 7 ? 1 : 0)}-${String(((i + 4) % 12) + 1).padStart(2, "0")}`, entrou: "100.00", saiu: `${100 + i * 10}.00` }));

// `mes`: o da aba (o servidor o manda nos três cards do mês, e `null` no periodo). `quebra`: passa por objeto mas NÃO passa pelo `contrato` e desenharia sem lançar.
// `fundo`: passa pelo contrato e LANÇA no render (sem o `LimiteCard` desmontaria a raiz); resumo e calendario não têm: o contrato já cobre tudo que o render lê.
const CASOS = {
  resumo: { mes: "2026-10", titulo: "Resumo do mês · Outubro 2026", manchete: "R$ 600,00", recursos: { resumo: F.resumo_do_mes.exato, detalhes: D },
    quebra: (r) => com(r, (c) => { c.resumo.motivos = "x"; }), fundo: null, injeta: (r, p) => com(r, (c) => { c.resumo.motivos = [p]; }) },
  categorias: { mes: "2026-10", titulo: "Para onde vai · Outubro 2026", manchete: "R$ 3.010,50", recursos: { detalhes: D },
    quebra: (r) => com(r, (c) => { c.detalhes.mes = "lixo"; }), fundo: (r) => com(r, (c) => { c.detalhes.categorias[0].categoria = 5; }),
    injeta: (r, p) => com(r, (c) => { c.detalhes.categorias[0].categoria = p; }) },
  calendario: { mes: "2026-10", titulo: "Dia a dia · Outubro 2026", manchete: "Dom · 25/10", recursos: { detalhes: D },
    quebra: (r) => com(r, (c) => { c.detalhes.mes = "lixo"; }), fundo: null, injeta: (r, p) => com(r, (c) => { c.detalhes.dias[0].dia = p; }) },
  periodo: { mes: null, titulo: "Agosto a setembro 2026", manchete: "R$ 1.699,50", recursos: { periodo: PERIODO },
    quebra: (r) => com(r, (c) => { c.periodo.saldo = "lixo"; }), fundo: (r) => com(r, (c) => { c.periodo.categorias[0].categoria = 5; }),
    injeta: (r, p) => com(r, (c) => { c.periodo.categorias[0].categoria = p; }) },
};
const IDS = Object.keys(CASOS);
const blocoDoCaso = (id, recursos = CASOS[id].recursos) => bloco(id, CASOS[id].mes, recursos);

async function responde(blocos, opts) {
  const c = await abrir(opts);
  await enviar(c.page, "me mostra");
  await c.flux.espera(1);
  await c.flux.push(0, evento(...blocos, fim("Texto final.")));
  await pronto(c.page);
  return c;
}

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
test("contrato: mês do detalhe fora de AAAA-MM, `dias` que não é lista, e período com mês que não existe viram marcador (um cada); o válido do lado desenha", async () => {
  const ruins = [
    bloco("categorias", "2026-10", { detalhes: { ...D, mes: "lixo" } }), bloco("calendario", "2026-10", { detalhes: { ...D, dias: "x" } }), bloco("categorias", "2026-10", { detalhes: { ...D, motivos: [{ a: 1 }] } }),
    bloco("periodo", null, { periodo: { ...PERIODO, inicio: "2026-13" } }), bloco("periodo", null, { periodo: { ...PERIODO, meses: [{ mes: "x", entrou: "1.00", saiu: "1.00" }] } }),
    bloco("resumo", "2026-10", { resumo: F.resumo_do_mes.exato }),
  ];
  const { page, erros } = await responde([...ruins, blocoDoCaso("resumo")]);
  assert.equal(await ultima(page).getByText(SEM_CARD).count(), ruins.length);
  assert.equal(await ultima(page).locator(".card-ia").count(), 1, "controle positivo: o bloco válido da mesma resposta desenha");
  assert.deepEqual(erros, []);
  await page.context().close();
});

// 3. O LimiteCard ---------------------------------------------------------------------------------------------------------------------------------
for (const id of IDS.filter((i) => CASOS[i].fundo)) {
  test(`${id}: campo fundo malformado que passa pelo contrato vira o marcador SÓ deste card; o outro bloco, a fala e a página seguem`, async () => {
    const outro = id === "resumo" ? "calendario" : "resumo";
    const { page, erros } = await responde([blocoDoCaso(id, CASOS[id].fundo(CASOS[id].recursos)), blocoDoCaso(outro)]);
    assert.equal(await ultima(page).getByText(SEM_CARD).count(), 1);
    assert.equal(await ultima(page).locator(".card-ia h2").innerText(), CASOS[outro].titulo, "só o card do outro bloco desenhou");
    assert.equal(await ultima(page).locator(".msg-ia").innerText(), "Texto final.");
    assert.equal(await page.locator("#askbar-input").count(), 1, "a página segue de pé");
    assert.deepEqual(erros, [], "o erro foi contido: nada chega como erro de página");
    await page.context().close();
  });
}

// 4. Texto do modelo é só texto ---------------------------------------------------------------------------------------------------------------------
for (const id of IDS) {
  test(`${id}: HTML num campo de texto livre aparece como texto (nenhum elemento, nenhum script)`, async () => {
    const { page, erros } = await responde([blocoDoCaso(id, CASOS[id].injeta(CASOS[id].recursos, P))]);
    const card = ultima(page).locator(".card-ia");
    for (const b of await card.locator(".ci-mais").all()) await b.click(); // o dia sem data válida vai para o fim da lista, atrás do "+N"
    assert.ok((await card.innerText()).includes(P), "o literal tem de aparecer no card");
    assert.equal(await page.locator("img[src=x]").count(), 0, "o <img> do texto não virou elemento");
    await page.waitForTimeout(300);
    assert.equal(await page.evaluate(() => window.__xss), undefined);
    assert.deepEqual(erros, []);
    await page.context().close();
  });
}

// 5. O card é uma foto do bloco -----------------------------------------------------------------------------------------------------------------------
test("os 4 cards desenham SÓ o bloco: com as rotas dos widgets de gasto respondendo outro dado, nenhuma é chamada e nada da rota aparece; lançamentos, só ao tocar", async () => {
  const c = await abrir();
  const chamadas = [];
  for (const rota of ["resumo-do-mes", "gastos", "gastos/categoria", "lancamentos"]) {
    await c.ctx.route(`**/api/v2/${rota}*`, (r) => { chamadas.push(rota); return r.fulfill({ json: { ...F.resumo_do_mes.exato, mes: "DA-ROTA", motivos: ["DA-ROTA"] } }); });
  }
  const antes = chamadas.length;
  await enviar(c.page, "me mostra");
  await c.flux.espera(1);
  await c.flux.push(0, evento(...IDS.map((id) => blocoDoCaso(id, CASOS[id].injeta(CASOS[id].recursos, "DO-BLOCO"))), fim("Texto final.")));
  await pronto(c.page);
  await c.page.waitForTimeout(400);
  assert.equal(await ultima(c.page).locator(".card-ia").count(), 4);
  assert.ok(!(await ultima(c.page).innerText()).includes("DA-ROTA"), "nada da rota apareceu");
  assert.ok((await ultima(c.page).innerText()).includes("DO-BLOCO"));
  assert.deepEqual(chamadas.slice(antes), [], "nenhuma rota foi chamada entre o bloco e o fim");
  await c.page.context().close();
});

// 6. Os meses (o servidor manda 1 a 3 abas com mês em resumo, categorias e calendario) ----------------------------------------------------------------------
test("categorias com 2 abas de mês: o Seg troca o mês (título, total), o estado da linha aberta reinicia e a aba ruim derruba o card inteiro", async () => {
  const set = com(D, (c) => { c.mes = "2026-09"; c.ate = "2026-09-30"; c.categorias = [{ categoria: "mercado", valor: "77.00", quantidade: 1 }]; });
  const abas = (...a) => ({ tipo: "bloco", bloco: { card: "categorias", abas: a } });
  const { page } = await responde([abas({ mes: "2026-09", recursos: { detalhes: set } }, { mes: "2026-10", recursos: { detalhes: D } })]);
  assert.deepEqual(await page.getByRole("radiogroup", { name: "Mês do card" }).getByRole("radio").allInnerTexts(), ["set", "out"]);
  assert.equal(await page.locator(".card-ia h2").innerText(), "Para onde vai · Setembro 2026");
  assert.ok((await page.locator(".card-ia").innerText()).includes("R$ 77,00"));
  await page.getByRole("radio", { name: "out", exact: true }).click();
  assert.equal(await page.locator(".card-ia h2").innerText(), "Para onde vai · Outubro 2026");
  assert.ok((await page.locator(".card-ia").innerText()).includes("R$ 3.010,50"));
  await page.context().close();
  const ruim = await responde([abas({ mes: "2026-09", recursos: { detalhes: set } }, { mes: "2026-10", recursos: { detalhes: { ...D, categorias: "x" } } })]);
  assert.equal(await ultima(ruim.page).getByText(SEM_CARD).count(), 1);
  assert.equal(await ultima(ruim.page).locator(".card-ia").count(), 0);
  await ruim.page.context().close();
});

// 7. O que é de cada card -------------------------------------------------------------------------------------------------------------------------------
// Abre tudo o que abre agora (os "+N" também). Pelos `elementHandles`: um `locator.all()` reavalia o seletor a cada clique, e a linha aberta já não casa com `aria-expanded=false`.
const abreTudo = async (page) => { for (const h of await page.locator(".card-ia button[aria-expanded=false]").elementHandles()) { await h.click(); await page.waitForTimeout(60); } };
const linhasDe = (page, sel) => page.locator(sel).evaluateAll((ls) => ls.map((l) => l.innerText.replace(/\s+/g, " ").trim()));

test("categorias: 7 categorias viram as 4 maiores e \"Outras\" (a soma exata das restantes, % da soma); \"Outras\" abre as 3 escondidas; até 5, todas pelo nome", async () => {
  const { page } = await responde([blocoDoCaso("categorias")]);
  assert.deepEqual(await linhasDe(page, ".card-ia > .ci-corpo > .ci-lista > li > .ci-cat"), [
    "Alimentação 33% R$ 980,50", "Casa 30% R$ 900,00", "Mercado 14% R$ 420,00", "Transporte 10% R$ 300,00", "Outras 14% R$ 410,00",
  ]);
  assert.equal(await page.locator(".card-ia .comp-bar > span").count(), 5);
  await page.locator(".card-ia .ci-cat").nth(4).click();
  assert.deepEqual(await linhasDe(page, ".card-ia .ci-aberta .ci-cat"), ["Lazer 7% R$ 200,00", "Saude 4% R$ 110,00", "Sem categoria 3% R$ 100,00"]);
  assert.equal(await page.locator(".card-ia .ci-cat[aria-expanded=true]").count(), 1, "só \"Outras\" está aberta");
  await page.locator(".card-ia .ci-cat").nth(0).click();
  assert.equal(await page.locator(".card-ia .ci-cat[aria-expanded=true]").count(), 1, "uma aberta por vez: abrir a 1ª fecha \"Outras\"");
  await page.context().close();
  const cinco = await responde([blocoDoCaso("categorias", { detalhes: { ...D, categorias: D.categorias.slice(0, 5) } })]);
  assert.deepEqual((await linhasDe(cinco.page, ".card-ia .ci-cat")).map((t) => t.split(" ")[0]), ["Alimentação", "Casa", "Mercado", "Transporte", "Lazer"]);
  await cinco.page.context().close();
  const nenhuma = await responde([blocoDoCaso("categorias", { detalhes: { ...D, categorias: [] } })]);
  assert.equal(await nenhuma.page.getByText("Nenhum gasto em reais registrado neste período.").count(), 1);
  await nenhuma.page.context().close();
});

test("calendario: os dias do mais novo ao mais velho, 7 por vez (\"+N\" abre o resto, \"Mostrar menos\" volta); dia de outro ano leva o ano; sem dia, o aviso", async () => {
  const { page } = await responde([blocoDoCaso("calendario")]);
  const rot = () => linhasDe(page, ".card-ia .ci-dia .ci-nome");
  assert.deepEqual(await rot(), ["Dom · 25/10", "Qui · 08/10", "Ter · 06/10", "Seg · 05/10", "Dom · 04/10", "Sáb · 03/10", "Sex · 02/10"]);
  const mais = page.getByRole("button", { name: "+2, mostrar mais 2 dias" });
  await mais.click();
  assert.deepEqual((await rot()).slice(-2), ["Qui · 01/10", "Sáb · 20/12/25"]);
  await page.getByRole("button", { name: "Mostrar menos dias" }).click();
  assert.equal((await rot()).length, 7);
  assert.equal(await page.locator(".card-ia .ci-dia .gain").count(), 0, "o dia 01/10 (a entrada de R$ 7.200) está nos escondidos");
  await mais.click();
  assert.deepEqual(await linhasDe(page, ".card-ia .ci-dia .gain"), ["Entrou +R$ 7.200,00"]);
  // cada barra na sua escala: o salário (7.200) não amassa as saídas, a maior saída (900) enche a barra dela
  const escala = (rotulo, i) => page.locator(".card-ia .ci-dia", { hasText: rotulo }).locator(".ci-trilho > i").nth(i).evaluate((e) => e.style.transform);
  assert.deepEqual([await escala("Qui · 01/10", 0), await escala("Qui · 01/10", 1), await escala("Dom · 25/10", 0)], ["scaleX(1)", "scaleX(1)", "scaleX(0)"]);
  assert.match(await escala("Dom · 25/10", 1), /^scaleX\(0\.38/);
  await page.context().close();
  const vazio = await responde([blocoDoCaso("calendario", { detalhes: { ...D, dias: [] } })]);
  assert.equal(await vazio.page.getByText("Ainda não há movimentos neste período.").count(), 1);
  await vazio.page.context().close();
});

test("periodo: saldo em manchete, Entrou e Saiu, barras por mês, categorias com a % do servidor; os maiores abrem aqui (sem busca); sem maiores a linha não abre; 7+ meses escondem o valor da barra", async () => {
  const a = await responde([blocoDoCaso("periodo")]);
  assert.deepEqual(await linhasDe(a.page, ".card-ia .ci-fluxo"), ["Entrou R$ 3.000,00", "Saiu −R$ 1.300,50"]);
  assert.deepEqual(await linhasDe(a.page, ".card-ia .months li .sr-only"), ["agosto: R$ 600", "setembro: R$ 701"]);
  assert.deepEqual(await linhasDe(a.page, ".card-ia .ci-cat"), ["Mercado 70% R$ 900,50", "Transporte 30% R$ 400,00"]);
  assert.equal(await a.page.locator(".card-ia .ci-cat[aria-expanded]").count(), 1, "transporte não tem maiores: sem botão");
  await a.page.locator(".card-ia .ci-cat[aria-expanded]").click();
  assert.equal(await a.page.locator(".card-ia .lanc-linha").count(), 1);
  assert.equal(await a.page.locator(".card-ia .ci-aberta").getByText("Maiores lançamentos").count(), 1);
  assert.equal(await a.page.locator(".card-ia .months b").first().isVisible(), true, "até 6 meses o valor aparece sob a barra");
  await a.page.context().close();
  const muitos = await responde([blocoDoCaso("periodo", { periodo: { ...PERIODO, inicio: meses12[0].mes, fim: meses12[11].mes, meses: meses12 } })]);
  assert.equal(await muitos.page.locator(".card-ia .months li").count(), 12);
  assert.equal(await muitos.page.locator(".card-ia .months b").first().isVisible(), false);
  await muitos.page.context().close();
  const seis = await responde([blocoDoCaso("periodo", { periodo: { ...PERIODO, categorias: Array.from({ length: 7 }, (_, i) => ({ ...PERIODO.categorias[1], categoria: `c${i}`, percentual: 14 })) } })]);
  assert.equal(await seis.page.locator(".card-ia .ci-cat").count(), 5);
  await seis.page.getByRole("button", { name: "+2, mostrar mais 2 categorias" }).click();
  assert.equal(await seis.page.locator(".card-ia .ci-cat").count(), 7);
  await seis.page.context().close();
});

test("resumo: Sobrou em manchete (entrou − saiu, exato), Entrou e Saiu; negativo fica em aviso; selos do resumo e do detalhe sem repetir", async () => {
  const a = await responde([blocoDoCaso("resumo")]);
  assert.deepEqual(await linhasDe(a.page, ".card-ia .ci-fluxo"), ["Entrou R$ 1.040,00", "Saiu −R$ 440,00"]);
  assert.equal(await a.page.locator(".card-ia .stat-value.warn").count(), 0);
  const barras = await a.page.locator(".card-ia .ci-fluxo .ci-trilho > i").evaluateAll((is) => is.map((i) => i.style.transform));
  assert.equal(barras[0], "scaleX(1)", "o maior dos dois enche a barra");
  assert.match(barras[1], /^scaleX\(0\.42/, "saiu 440 de 1040");
  await a.page.context().close();
  const r = { ...F.resumo_do_mes.exato, entrou: "100.10", saiu: "250.20", motivos: ["conciliacao_pendente"] };
  const b = await responde([blocoDoCaso("resumo", { resumo: r, detalhes: { ...D, motivos: ["conciliacao_pendente", "arredondamento_por_grupo"] } })]);
  assert.equal(await b.page.locator(".card-ia .stat-value").innerText(), "−R$ 150,10");
  assert.equal(await b.page.locator(".card-ia .stat-value.warn").count(), 1);
  assert.deepEqual(await linhasDe(b.page, ".card-ia .selos"), ["conciliação pendente soma arredondada"]);
  await b.page.context().close();
});

// 8. Geometria, nas duas larguras --------------------------------------------------------------------------------------------------------------------
for (const w of [390, 1440]) {
  test(`${w}px: os 4 cards cabem (sem rolagem lateral, dentro da bolha), os alvos de toque têm 44px, e abrir o que abre não estoura`, async () => {
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
    assert.equal(fechado.length, 4);
    confere(fechado, "fechado");
    // abre a 1ª linha de cada card (a de categoria e de dia buscam; a rota padrão do harness devolve a lista) e "Outras" por dentro
    await abreTudo(page);
    await page.waitForTimeout(400);
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
    await flux.push(i, evento(...IDS.map((id) => blocoDoCaso(id)), fim(`Texto ${i}.`)));
    await pronto(page);
  }
  await abreTudo(page);
  await page.waitForTimeout(300);
  const repetidos = await page.evaluate(() => { const ids = [...document.querySelectorAll("[id]")].map((e) => e.id), vistos = new Set(); return ids.filter((x) => vistos.size === vistos.add(x).size); });
  assert.deepEqual(repetidos, []);
  assert.equal(await page.locator(".card-ia").count(), 8);
  await page.context().close();
});
