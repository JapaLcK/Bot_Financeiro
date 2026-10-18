/**
 * /painel /gastos, "fazer tudo na mesma tela": tocar num dia do "Dia a dia" (ou em "Ver compras" do Detalhe) abre o
 * card de compras ali mesmo, sem ir aos Lançamentos (docs/plano-etapa5-gastos.md §4.4).
 *
 *   · a soma, em centavos, das linhas que o total soma (sem marca) é o total do dia; a nota só vem com linha marcada;
 *   · comportamento: abrir, fechar no mesmo dia, trocar de dia, "Dia mais caro", Esc e foco, aria-expanded/controls,
 *     modo categoria, categoria escolhida, troca de mês, carregando, erro e "Carregar mais";
 *   · layout com números: coluna da direita acima de 1100px, depois do calendário (dia) ou do Detalhe (categoria) abaixo;
 *   · Resumo: tocar no dia leva à /gastos com o card aberto e à vista, nos dois modos de movimento.
 *
 * Rodar:  node --test tests/frontend/dashboard_v2_gastos_dia.test.mjs   (mudou webapp/src, rode `npm --prefix webapp run build`)
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { chromium } from "playwright";
import { RAIZ, RESPOSTAS, abrirPainel, exigeArtefatoEmDia } from "./_painel.mjs";

let browser;
before(async () => { exigeArtefatoEmDia(); browser = await chromium.launch(); });
after(() => browser?.close());

const ABRIR = { gastos: "padrao", lancamentos: "dia_7", espera: "#w-cat-detalhe .detail-facts" };
const abrir = (opts) => abrirPainel(browser, { ...ABRIR, ...opts });
const LANC = (u) => u.pathname === "/api/v2/lancamentos";
const DIA7 = RESPOSTAS.lancamentos.dia_7;
const centavos = (t) => Number(t.replace(/\D/g, ""));
const celula = (page, n) => page.locator("#w-calendario .cal-day", { hasText: new RegExp(`^${n}$`) });
const pedidos = (page) => {
  const urls = [];
  page.on("request", (r) => { const u = new URL(r.url()); if (u.pathname === "/api/v2/lancamentos") urls.push(u.search); });
  return urls;
};
// O que a pessoa vê no card: o total do dia, e cada linha com a marca que o servidor mandou.
const lerCard = (page) => page.evaluate(() => { const lede = () => { const r = document.querySelector("#gastos-compras .dia-rotulo"), t = document.querySelector("#gastos-compras .dia-total"); return r && `${r.textContent} · ${t.textContent}`; }; return {
  lede: lede(),
  nota: document.querySelector(".compras-nota")?.textContent ?? null,
  linhas: [...document.querySelectorAll("#gastos-compras .lanc-linha")].map((li) => ({
    valor: li.querySelector(".lanc-valor").textContent,
    marcada: /fora de Entrou\/Saiu/.test(li.textContent) || [...li.querySelectorAll(".selo")].some((x) => x.textContent === "outra moeda"),
  })),
}; });
// "Gasto no dia · R$ 290,00": o rótulo e o total do dia, que agora moram na faixa e não numa linha só.
const ledeDoDia = (page) => page.evaluate(() => `${document.querySelector("#gastos-compras .dia-rotulo").textContent} · ${document.querySelector("#gastos-compras .dia-total").textContent}`);
// A data do card (o título é "Seu dia"): espera o dia certo no lugar do antigo `#w-compras-h` com a data.
const diaAberto = (page, texto) => page.locator("#gastos-compras .dia-data", { hasText: texto });
const somaNaoMarcadas = (linhas) => linhas.filter((l) => !l.marcada).reduce((a, l) => a + centavos(l.valor), 0);

async function cardDoDia(itens) {
  const { ctx, page, ir } = await abrir({});
  if (itens) await ctx.route(LANC, (r) => r.fulfill({ json: { ...DIA7, itens: itens(DIA7.itens) } }));
  await ir("/gastos");
  await celula(page, 7).click();
  await page.locator("#gastos-compras .lanc-linha").first().waitFor();
  const card = await lerCard(page);
  await ctx.close();
  return card;
}

test("soma: as linhas sem marca somam o total do dia, em centavos; sem a marca a soma diverge; a nota só vem com linha marcada", async () => {
  const real = await cardDoDia();
  assert.equal(real.lede, "Gasto no dia · R$ 290,00");
  assert.equal(real.linhas.length, 4);
  assert.deepEqual(real.linhas.map((l) => l.marcada), [false, false, true, true]); // o interno e o de outra moeda
  assert.equal(somaNaoMarcadas(real.linhas), centavos(real.lede));
  assert.match(real.nota, /^Movimentos entre contas, guardados e saídas em outra moeda aparecem marcados e ficam fora do total\.$/);

  // controle negativo: o mesmo dia sem a marca do servidor. A conta passaria a incluir o interno e a outra moeda
  // (R$ 470,00) e não bateria com o total; é isso que prova que a marca é o que fecha a soma.
  const semMarca = await cardDoDia((itens) => itens.map((i) => ({ ...i, interno: false, motivos: [] })));
  assert.notEqual(somaNaoMarcadas(semMarca.linhas), centavos(semMarca.lede));
  assert.equal(somaNaoMarcadas(semMarca.linhas), 47000);

  // controle positivo: sem linha marcada não há nota, e a soma continua batendo
  const limpo = await cardDoDia((itens) => itens.filter((i) => !i.interno && !i.motivos.length));
  assert.equal(limpo.nota, null);
  assert.equal(somaNaoMarcadas(limpo.linhas), centavos(limpo.lede));
});

test("dia: tocar abre sem sair da tela, no mesmo dia fecha, em outro troca; 'Dia mais caro' só abre; aria-expanded/controls; foco e Esc", async () => {
  const { ctx, page, erros, ir } = await abrir({});
  const lanc = pedidos(page);
  await ir("/gastos");
  const ver = () => page.evaluate(() => {
    const card = document.querySelector("#gastos-compras");
    const cel = (b) => b.textContent;
    return {
      hash: location.hash, aberto: !!card, titulo: card && document.querySelector("#w-compras-h").textContent,
      foco: document.activeElement?.id || document.activeElement?.getAttribute("aria-label")?.slice(0, 18) || null,
      expandidas: [...document.querySelectorAll("#w-calendario .cal-day[aria-expanded=true]")].map(cel),
      controla: [...document.querySelectorAll("#w-calendario .cal-day[aria-controls]")].map((b) => `${cel(b)}:${b.getAttribute("aria-controls")}`),
      pressed: document.querySelectorAll("#w-calendario .cal-day[aria-pressed]").length,
    };
  });
  const inicial = await ver();
  assert.deepEqual(inicial, { hash: "#/gastos", aberto: false, titulo: null, foco: inicial.foco, expandidas: [], controla: [], pressed: 0 });
  assert.equal(await celula(page, 7).getAttribute("aria-expanded"), "false");

  await celula(page, 7).click();
  await page.locator("#gastos-compras .lanc-linha").first().waitFor();
  assert.deepEqual(await ver(), { hash: "#/gastos", aberto: true, titulo: "Seu dia", foco: "gastos-compras", expandidas: ["7"], controla: ["7:gastos-compras"], pressed: 0 });
  assert.equal(await page.locator("#gastos-compras").getAttribute("role"), "region");
  assert.deepEqual(lanc.at(-1), "?mes=2026-10&dia=2026-10-07&tipo=saida&guardado=true");

  await celula(page, 3).click(); // outro dia: troca
  await diaAberto(page, "3 de outubro").waitFor();
  assert.deepEqual((await ver()).expandidas, ["3"]);
  assert.equal(lanc.at(-1), "?mes=2026-10&dia=2026-10-03&tipo=saida&guardado=true");
  await celula(page, 3).click(); // o mesmo dia: fecha
  await page.locator("#gastos-compras").waitFor({ state: "detached" });
  assert.deepEqual((await ver()).expandidas, []);

  const caro = page.locator("#w-calendario .cal-foot .link");
  await caro.click(); // "Dia mais caro" (7): abre
  await diaAberto(page, "7 de outubro").waitFor();
  await caro.click(); // e nunca fecha
  assert.deepEqual([(await ver()).aberto, (await ver()).expandidas], [true, ["7"]]);
  await page.locator("#gastos-compras").focus();
  await page.keyboard.press("Escape"); // foco dentro do card: Esc fecha e devolve o foco à célula do dia
  await page.locator("#gastos-compras").waitFor({ state: "detached" });
  const fim = await ver();
  assert.deepEqual([fim.aberto, fim.foco, fim.expandidas], [false, "qua, 7 de outubro:", []]);

  await celula(page, 20).focus(); // pelo teclado; dia futuro com lançamento agendado
  await page.keyboard.press("Enter");
  await diaAberto(page, "20 de outubro").waitFor();
  assert.equal(await ledeDoDia(page), "Agendado para o dia · R$ 50,00");
  await celula(page, 5).click(); // dia sem entrada: o texto e, mesmo assim, a lista é pedida (pode trazer só linha marcada)
  await diaAberto(page, "5 de outubro").waitFor();
  assert.equal(await page.locator("#gastos-compras .faint").first().textContent(), "Nenhum gasto neste dia.");
  await page.locator("#gastos-compras .lanc-linha").first().waitFor();
  assert.equal(lanc.at(-1), "?mes=2026-10&dia=2026-10-05&tipo=saida&guardado=true");
  await ctx.close();
  assert.deepEqual(erros, []);
});

test("'Ver em Lançamentos' ainda leva `mes` e `dia`; com categoria escolhida o card segue a categoria (etiqueta 'Só ...') e a leva junto", async () => {
  const { ctx, page, ir } = await abrir({});
  const lanc = pedidos(page);
  await ir("/gastos");
  await page.locator("#w-categorias .cat", { hasText: "sem categoria" }).click();
  await page.locator("#w-calendario .tag-demo").waitFor();
  await celula(page, 7).click();
  await page.locator("#gastos-compras .tag-demo", { hasText: "Só alimentação" }).waitFor(); // a fixture da categoria responde alimentação
  assert.equal(await ledeDoDia(page), "Gasto no dia · R$ 200,00"); // o total do dia na categoria
  assert.equal(lanc.at(-1), "?mes=2026-10&dia=2026-10-07&tipo=saida&categoria=sem+categoria&guardado=true");
  await page.getByRole("button", { name: "Ver em Lançamentos" }).click();
  await page.waitForFunction(() => location.hash === "#/lancamentos");
  await page.locator(".ledger-chips .chip").waitFor();
  const r = [lanc.at(-1), await page.getByRole("combobox", { name: /^Categoria/ }).inputValue(), await page.locator(".ledger-chips .chip").textContent()];
  await ctx.close();
  assert.deepEqual(r, ["?mes=2026-10&categoria=sem+categoria&dia=2026-10-07&guardado=true", "sem categoria", "dia 7remover filtro"]);
});

test("categoria: 'Ver compras' abre o card do mês da categoria e alterna; outra categoria e o mês novo fecham; a mesma categoria não; o dia vira categoria", async () => {
  const { ctx, page, ir } = await abrir({});
  const lanc = pedidos(page);
  await ir("/gastos");
  const botao = page.getByRole("button", { name: "Ver compras" });
  assert.equal(await botao.getAttribute("aria-expanded"), "false");
  assert.equal(await botao.getAttribute("aria-controls"), null);
  await botao.click();
  await page.locator("#gastos-compras .lanc-linha").first().waitFor();
  const aberto = await page.evaluate(() => ({ titulo: document.querySelector("#w-compras-h").textContent.trim(), lede: document.querySelector("#gastos-compras .w-lede").textContent,
    foco: document.activeElement.id, exp: document.querySelector('[aria-controls="gastos-compras"]').textContent }));
  assert.deepEqual(aberto, { titulo: "Alimentação · outubro", lede: "R$ 300,00 · 2 compras", foco: "gastos-compras", exp: "Ver compras" });
  assert.equal(lanc.at(-1), "?mes=2026-10&tipo=saida&categoria=alimentacao&guardado=true");
  await page.keyboard.press("Escape");
  await page.locator("#gastos-compras").waitFor({ state: "detached" });
  assert.equal(await page.evaluate(() => document.activeElement.textContent), "Ver compras"); // o foco volta a quem abriu

  await botao.click(); // abre de novo; o mesmo botão fecha
  await page.locator("#gastos-compras").waitFor();
  await botao.click();
  await page.locator("#gastos-compras").waitFor({ state: "detached" });

  await botao.click();
  await page.locator("#gastos-compras").waitFor();
  await page.locator("#w-categorias .cat", { hasText: "alimentação" }).click(); // escolher a MESMA que o card mostra: segue aberto
  await page.locator("#w-categorias .cat[aria-pressed=true]").waitFor();
  assert.equal(await page.locator("#gastos-compras").count(), 1);
  await page.locator("#w-categorias .cat", { hasText: "sem categoria" }).click(); // outra categoria: fecha
  await page.locator("#gastos-compras").waitFor({ state: "detached" });

  await celula(page, 7).click(); // dia aberto: o "Ver compras" troca para a categoria
  await diaAberto(page, "7 de outubro").waitFor();
  await botao.click();
  await page.locator("#w-compras-h", { hasText: "· outubro" }).waitFor();
  assert.equal(await page.locator("#w-calendario .cal-day[aria-expanded=true]").count(), 0);
  await page.getByRole("button", { name: "Mês anterior", exact: true }).click(); // mês novo: fecha
  await page.locator("#gastos-compras").waitFor({ state: "detached" });
  await ctx.close();
});

test("carregando, erro com 'Tentar de novo' (44px) e 'Carregar mais'", async () => {
  const { ctx, page, erros, ir } = await abrir({ lancamentos: "estados" });
  let estado = "segura";
  let libera;
  const segurada = new Promise((r) => { libera = r; });
  await ctx.route(LANC, async (r) => {
    if (estado === "segura") await segurada;
    if (estado === "erro") return r.fulfill({ status: 500, json: { error: { code: "internal_error", message: "x" } } });
    if (estado === "erro2" && new URL(r.request().url()).searchParams.has("cursor")) return r.fulfill({ status: 500, json: { error: { code: "internal_error", message: "x" } } });
    return r.fulfill({ json: new URL(r.request().url()).searchParams.has("cursor") ? RESPOSTAS.lancamentos.segunda : RESPOSTAS.lancamentos.estados });
  });
  await ir("/gastos");
  await celula(page, 7).click();
  await page.locator('#gastos-compras [role="status"]', { hasText: "Carregando…" }).waitFor(); // o total do dia já está, a lista carrega
  assert.equal(await ledeDoDia(page), "Gasto no dia · R$ 290,00");
  estado = "erro";
  libera();
  await page.locator("#gastos-compras [role=alert]").waitFor({ timeout: 15000 });
  const erro = await page.evaluate(() => { const a = document.querySelector("#gastos-compras [role=alert]"); return [a.querySelector("p").textContent, a.querySelector("button").textContent, a.querySelector("button").getBoundingClientRect().height >= 44]; });
  assert.deepEqual(erro, ["Não deu para carregar.", "Tentar de novo", true]);
  estado = "ok";
  await page.locator("#gastos-compras [role=alert] button").click();
  await page.locator("#gastos-compras .lanc-linha").first().waitFor();

  const mais = page.locator("#gastos-compras .compras-mais button");
  assert.deepEqual([await mais.textContent(), (await mais.boundingBox()).height >= 44], ["Carregar mais", true]);
  estado = "erro2";
  await mais.click();
  await page.locator("#gastos-compras .compras-mais [role=alert]").waitFor({ timeout: 15000 });
  assert.deepEqual([await page.locator("#gastos-compras .compras-mais [role=alert]").textContent(), await mais.textContent()], ["Não deu para carregar mais.", "Tentar de novo"]);
  estado = "ok";
  await mais.click();
  await page.locator('#gastos-compras [data-id="l55"]').waitFor();
  assert.equal(await page.locator("#gastos-compras .compras-mais").count(), 0);
  await ctx.close();
  assert.deepEqual(erros, []);
});

// --- layout --------------------------------------------------------------------------------------------------

const caixas = (page) => page.evaluate(() => {
  const c = (sel) => { const e = document.querySelector(sel); const r = (e.closest(".panel") ?? e).getBoundingClientRect(); return e && { x: Math.round(r.x), r: Math.round(r.right), y: Math.round(r.y + scrollY), b: Math.round(r.bottom + scrollY) }; };
  return { cal: c("#w-calendario"), det: c("#w-cat-detalhe"), card: c("#gastos-compras"), lateral: document.scrollingElement.scrollWidth - document.scrollingElement.clientWidth };
});
const naDireita = (c) => c.card.x >= c.det.x && c.card.r <= c.det.r && c.card.y >= c.det.b; // coluna da direita, abaixo do Detalhe
const depoisDoCalendario = (c) => c.card.y >= c.cal.b && c.card.b <= c.det.y; // coluna única, entre o calendário e o Detalhe
const depoisDoDetalhe = (c) => c.card.y >= c.det.b;

test("o corte do layout é o do CSS: 1100px é uma coluna só, 1101px é a coluna da direita; o resize reordena", async () => {
  const css = readFileSync(join(RAIZ, "webapp/src/dashboard/styles/pages.css"), "utf8");
  const tsx = readFileSync(join(RAIZ, "webapp/src/dashboard/pages.tsx"), "utf8");
  const corte = Number(/@media \(max-width: (\d+)px\) \{\s*\.page-grid > \*/.exec(css)?.[1]);
  assert.equal(Number(/useMedia\("\(min-width: (\d+)px\)"\)/.exec(tsx)?.[1]), corte + 1); // o número de um é o do outro (§0.7)
  assert.equal(corte, 1100);

  const { ctx, page, ir } = await abrir({ width: corte + 1 });
  await ir("/gastos");
  await celula(page, 7).click();
  await page.locator("#gastos-compras .lanc-linha").first().waitFor();
  const em1101 = await caixas(page);
  await page.setViewportSize({ width: corte, height: 900 }); // o aparelho vira: o mesmo card troca de lugar sem reabrir
  await page.waitForFunction(() => { const c = document.querySelector("#gastos-compras").getBoundingClientRect(), d = document.querySelector("#w-cat-detalhe").getBoundingClientRect(); return c.bottom <= d.top; });
  const em1100 = await caixas(page);
  await ctx.close();
  console.log("# 1101:", JSON.stringify(em1101), "1100:", JSON.stringify(em1100));
  assert.ok(naDireita(em1101), JSON.stringify(em1101));
  assert.ok(depoisDoCalendario(em1100), JSON.stringify(em1100));
  assert.deepEqual([em1101.lateral, em1100.lateral], [0, 0]);
});

for (const [width, onde] of [[1440, "direita"], [390, "coluna"]]) {
  test(`${width}px: dia e categoria no lugar certo (${onde}), sem rolagem lateral`, async () => {
    const { ctx, page, ir } = await abrir({ width });
    await ir("/gastos");
    await celula(page, 7).click();
    await page.locator("#gastos-compras .lanc-linha").first().waitFor();
    const dia = await caixas(page);
    await page.getByRole("button", { name: "Ver compras" }).click();
    await page.locator("#w-compras-h", { hasText: "· outubro" }).waitFor();
    const categoria = await caixas(page);
    await ctx.close();
    console.log(`# ${width}px dia:`, JSON.stringify(dia), "categoria:", JSON.stringify(categoria));
    assert.deepEqual([dia.lateral, categoria.lateral], [0, 0]);
    if (width === 1440) assert.ok(naDireita(dia) && naDireita(categoria), JSON.stringify([dia, categoria]));
    else assert.ok(depoisDoCalendario(dia) && depoisDoDetalhe(categoria), JSON.stringify([dia, categoria]));
  });
}

for (const width of [1440, 390]) {
  test(`${width}px, R$ 999.999.990,00 e descrição de 200 caracteres: nenhum elemento do card passa da largura`, async () => {
    const { ctx, page, ir } = await abrir({ width });
    const gastos = RESPOSTAS.gastos.padrao;
    await ctx.route((u) => u.pathname === "/api/v2/gastos", (r) => r.fulfill({ json: { ...gastos, dias: gastos.dias.map((d) => (d.dia === "2026-10-07" ? { ...d, total: "999999990.00" } : d)) } }));
    await ctx.route(LANC, (r) => r.fulfill({ json: { ...DIA7, itens: DIA7.itens.map((i) => ({ ...i, valor: "999999990.00", descricao: "A".repeat(200), mensagem: "N".repeat(200) })) } }));
    await ir("/gastos");
    await celula(page, 7).click();
    await page.locator("#gastos-compras .lanc-linha").first().waitFor();
    const m = await page.evaluate(() => {
      const card = document.querySelector("#gastos-compras").getBoundingClientRect();
      return {
        lateral: document.scrollingElement.scrollWidth - document.scrollingElement.clientWidth,
        estouram: [...document.querySelectorAll("#gastos-compras *:not(.sr-only)")].filter((e) => e.scrollWidth > e.clientWidth && e.clientWidth > 0).map((e) => `${e.className || e.tagName}:${e.scrollWidth}>${e.clientWidth}`),
        fora: [...document.querySelectorAll("#gastos-compras .lanc-linha, #gastos-compras .lanc-valor")].filter((e) => e.getBoundingClientRect().right > card.right + 0.5).length,
        lede: `${document.querySelector("#gastos-compras .dia-rotulo").textContent} · ${document.querySelector("#gastos-compras .dia-total").textContent}`, linhas: document.querySelectorAll("#gastos-compras .lanc-linha").length,
      };
    });
    await ctx.close();
    console.log(`# ${width}px máximo:`, JSON.stringify(m));
    assert.deepEqual([m.lateral, m.estouram, m.fora, m.lede, m.linhas], [0, [], 0, "Gasto no dia · R$ 999.999.990,00", 4]);
  });
}

// --- Resumo --------------------------------------------------------------------------------------------------

for (const width of [1440, 390]) for (const movimento of ["reduce", "no-preference"]) {
  test(`Resumo ${width}px, movimento ${movimento}: tocar no dia (e no 'Dia mais caro') vai à /gastos com o card aberto, à vista e com o foco nele`, async () => {
    const { ctx, page, erros, ir } = await abrir({ width, espera: "#w-calendario" });
    await page.emulateMedia({ reducedMotion: movimento });
    const lanc = pedidos(page);
    await ir("/");
    assert.equal(await celula(page, 7).getAttribute("aria-expanded"), null); // no Resumo a célula navega, não expande
    const medidas = [];
    for (const toque of ["dia", "caro"]) {
      await page.evaluate(() => { location.hash = "#/"; });
      await page.locator("#w-calendario .cal-day").first().waitFor();
      await (toque === "dia" ? celula(page, 7) : page.locator("#w-calendario .cal-foot .link")).click();
      await page.waitForFunction(() => location.hash === "#/gastos");
      await page.locator("#gastos-compras .lanc-linha").first().waitFor();
      for (const espera of [100, 1100]) { // logo depois e já assentado: o foco do título e o scroll suave do roteador chegam depois do efeito do card
        await page.waitForTimeout(espera === 100 ? 100 : 1000);
        medidas.push(await page.evaluate(([t, e]) => {
          const r = document.querySelector("#gastos-compras").getBoundingClientRect();
          return { toque: t, depois: e, scrollY: Math.round(scrollY), topo: Math.round(r.top), base: Math.round(r.bottom), alturaDaJanela: innerHeight, foco: document.activeElement.id, titulo: document.querySelector("#w-compras-h").textContent };
        }, [toque, espera]));
      }
    }
    await ctx.close();
    console.log(`# Resumo ${width}px ${movimento}:`, JSON.stringify(medidas));
    for (const m of medidas) {
      assert.equal(m.foco, "gastos-compras", JSON.stringify(m));
      assert.equal(m.titulo, "Seu dia");
      // à vista: pelo menos o título, quando o scroll assentou (com movimento ele é suave, e no meio do caminho o card passa por fora)
      if (m.depois === 1100) assert.ok(m.topo >= 0 && m.topo + 48 <= m.alturaDaJanela, `card fora da vista: ${JSON.stringify(m)}`);
    }
    assert.equal(lanc.at(-1), "?mes=2026-10&dia=2026-10-07&tipo=saida&guardado=true");
    assert.deepEqual(erros, []);
  });
}

// --- achados do Tester ---------------------------------------------------------------------------------------

const compras = (page) => page.locator("#gastos-compras .lanc-linha").first().waitFor();
// Espera observável no lugar de sleep: 2 quadros (os rAF do card e do pai já rodaram) e o scrollY parado por 5 quadros (scroll suave terminou).
const assentou = (page) => page.evaluate(() => new Promise((ok) => { let y = -1, n = 0, q = 0; const f = () => { n = scrollY === y ? n + 1 : 0; y = scrollY; ++q >= 2 && n >= 5 ? ok() : requestAnimationFrame(f); }; requestAnimationFrame(f); }));

test("sair de /gastos fecha o card: voltar pelo menu não o reabre, não rouba o foco nem rola; o Resumo ainda o abre", async () => {
  const { ctx, page, erros, ir } = await abrir({});
  await ir("/gastos");
  await celula(page, 7).click();
  await compras(page);
  await page.getByRole("button", { name: "Ver em Lançamentos" }).click();
  await page.waitForFunction(() => location.hash === "#/lancamentos");
  await page.locator(".rail a[href='#/gastos']").click();
  await page.locator("#w-calendario .cal-day").first().waitFor();
  await page.waitForTimeout(1200); // o rAF do card e o scroll suave: se ele reabrisse, já teria rolado
  const volta = await page.evaluate(() => ({ card: !!document.querySelector("#gastos-compras"), foco: document.activeElement.id, scrollY: Math.round(scrollY), expandidas: document.querySelectorAll("#w-calendario .cal-day[aria-expanded=true]").length }));
  assert.deepEqual(volta, { card: false, foco: "page-title", scrollY: 0, expandidas: 0 });

  // controle positivo: a ida do Resumo, que escreve `gastosAberto` e navega, continua abrindo o card
  await page.evaluate(() => { location.hash = "#/"; });
  await page.locator("#w-calendario .cal-day").first().waitFor();
  await celula(page, 7).click();
  await page.waitForFunction(() => location.hash === "#/gastos");
  await compras(page);
  await assentou(page); // o foco do título da página (efeito do pai) vem antes do foco no card (rAF)
  assert.equal(await page.evaluate(() => document.activeElement.id), "gastos-compras");
  await ctx.close();
  assert.deepEqual(erros, []);
});

// A barra fixa (askbar no desktop, tabbar no celular) não cobre os botões do pé do card logo depois de abrir (WCAG 2.4.11).
// A fixture tem UMA linha na lista: com o topo do dia (faixa, fatos, barra) um card de 4 linhas passa da janela em ~900px de altura
// (limite declarado no doc); aqui se mede só que nenhuma barra fixa cobre o pé. Em janela baixa (720) os botões podem ficar abaixo
// da dobra, e então elementFromPoint fora da janela é null.
for (const [width, height] of [[1440, 900], [1180, 800], [390, 844], [1440, 720]]) {
  test(`${width}x${height}: 'Ver em Lançamentos' e 'Fechar' ${height === 720 ? "sem barra fixa por cima" : "ficam à vista, nenhuma barra fixa por cima,"} logo depois de abrir`, async () => {
    const { ctx, page, ir } = await abrir({ width });
    await page.setViewportSize({ width, height });
    await ctx.route(LANC, (r) => r.fulfill({ json: { ...DIA7, itens: DIA7.itens.slice(0, 1) } })); // uma linha: com o topo do dia o card de 4 linhas passa da janela em 900px
    await ir("/gastos");
    await celula(page, 7).click();
    await compras(page);
    await assentou(page);
    const m = await page.evaluate(() => [...document.querySelectorAll("#gastos-compras .compras-pe .link")].map((b) => {
      const r = b.getBoundingClientRect(), topo = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
      return { botao: b.textContent, livre: topo === b || b.contains(topo), base: Math.round(r.bottom), janela: innerHeight, cobre: topo && !b.contains(topo) ? topo.className || topo.tagName : null };
    }));
    await ctx.close();
    console.log(`# ${width}x${height}:`, JSON.stringify(m));
    assert.deepEqual(m.map((x) => [x.botao, x.livre || (height === 720 && x.base > x.janela && x.cobre === null)]), [["Ver em Lançamentos", true], ["Fechar", true]], JSON.stringify(m));
  });
}

// Vindo do Resumo o Detalhe, acima do card, carrega depois e o empurraria para baixo da dobra; o card volta ao início da janela.
for (const atraso of [300, 2500]) {
  test(`Resumo 1440x900, Detalhe com ${atraso} ms de atraso: o card termina no início da janela e inteiro à vista; o primeiro gesto da pessoa para o realinhamento`, async () => {
    for (const gesto of [false, true]) {
      if (gesto && atraso !== 2500) continue;
      const { ctx, page, erros, ir } = await abrir({ gastos: "muitas", espera: "#w-calendario" });
      await ctx.route(LANC, (r) => r.fulfill({ json: { ...DIA7, itens: DIA7.itens.slice(0, 2) } })); // com o topo do dia, "inteiro à vista" em 900px só cabe com até 2 linhas
      await ctx.route((u) => u.pathname === "/api/v2/gastos/categoria", async (r) => { await new Promise((ok) => setTimeout(ok, atraso)); await r.fulfill({ json: RESPOSTAS.gastos_categoria.padrao }); });
      await ir("/");
      await celula(page, 7).click();
      await page.waitForFunction(() => location.hash === "#/gastos");
      await compras(page);
      if (gesto) { await page.mouse.move(700, 500); await page.mouse.wheel(0, 150); await page.waitForTimeout(100); }
      await page.locator("#w-cat-detalhe .detail-facts").waitFor({ timeout: 15000 });
      await page.waitForTimeout(500);
      const m = await page.evaluate(() => { const r = document.querySelector("#gastos-compras").getBoundingClientRect(); return { topo: Math.round(r.top), base: Math.round(r.bottom), janela: innerHeight }; });
      await ctx.close();
      console.log(`# Detalhe +${atraso} ms, gesto ${gesto}:`, JSON.stringify(m));
      assert.deepEqual(erros, []);
      if (gesto) assert.ok(Math.abs(m.topo - 100) > 20, `a pessoa rolou e o card foi puxado de volta: ${JSON.stringify(m)}`);
      else assert.ok(m.topo >= 0 && m.topo <= 150 && m.base <= m.janela, JSON.stringify(m));
    }
  });
}

test("Fechar com a célula do dia desabilitada (o dia não existe na categoria escolhida) não perde o foco para o BODY", async () => {
  const { ctx, page, ir } = await abrir({});
  await ir("/gastos");
  await celula(page, 20).click(); // agendado, só existe no geral
  await diaAberto(page, "20 de outubro").waitFor();
  await page.locator("#w-categorias .cat", { hasText: "alimentação" }).click();
  await page.locator("#w-categorias .cat[aria-pressed=true]").waitFor();
  await page.waitForFunction(() => document.querySelector("#w-calendario .tag-demo") && [...document.querySelectorAll("#w-calendario .cal-day")].find((b) => b.textContent === "20").disabled);
  await page.getByRole("button", { name: "Fechar" }).click();
  await page.locator("#gastos-compras").waitFor({ state: "detached" });
  const foco = await page.evaluate(() => ({ tag: document.activeElement.tagName, cal: !!document.activeElement.closest("#w-calendario .cal-day"), off: document.activeElement.disabled }));
  await ctx.close();
  assert.deepEqual(foco, { tag: "BUTTON", cal: true, off: false });
});

test("as linhas do card só mostram (nenhum botão 'Detalhes: …' nem elemento interativo morto); Esc com o foco fora do card não o fecha", async () => {
  const { ctx, page, ir } = await abrir({});
  await ir("/gastos");
  await celula(page, 7).click();
  await compras(page);
  const mortos = await page.evaluate(() => ({
    linhas: document.querySelectorAll("#gastos-compras .lanc-linha").length,
    interativos: document.querySelectorAll("#gastos-compras .lanc-linha :is(button, a, input, select, [tabindex], [role=button])").length,
    detalhes: [...document.querySelectorAll("#gastos-compras button")].filter((b) => /^Detalhes:/.test(b.getAttribute("aria-label") ?? "")).length,
  }));
  assert.deepEqual(mortos, { linhas: 4, interativos: 0, detalhes: 0 });

  await celula(page, 7).focus(); // fora do card: o Esc é de quem tem o foco
  await page.keyboard.press("Escape");
  await page.waitForTimeout(200);
  assert.equal(await page.locator("#gastos-compras").count(), 1);
  await page.locator("#gastos-compras").focus(); // dentro: fecha (controle positivo do mesmo teste)
  await page.keyboard.press("Escape");
  await page.locator("#gastos-compras").waitFor({ state: "detached" });
  await ctx.close();
});
