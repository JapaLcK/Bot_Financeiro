/**
 * /painel /gastos, o topo do card "Seu dia" (docs/plano-etapa5-gastos.md §4.4, parts/GastosDiaTopo.tsx):
 *
 *   · lib/gastos-dia.ts: `mediaDoPeriodo`, `diaAbrivel`, `vizinho`, `contraMedia` (unidade) e o calendário lendo a MESMA regra;
 *   · a tabela dourada `tests/fixtures/gastos_dia_casos.json` (a que o app confere) é a saída atual dessa lib;
 *   · pílula contra a média: cada estado (abaixo, na média, acima neutro, acima âmbar, as bordas −5 e +15) e a AUSÊNCIA no
 *     dia em curso, no agendado, no dia sem gasto e sem média;
 *   · fatos e barra por categoria: fatia ≤ 0 descartada, a barra some com uma fatia e com categoria escolhida;
 *   · setas: navegam, desabilitam no limite, não roubam o foco do botão e, ao desabilitar, o foco vai ao card;
 *   · modo categoria compacto (sem setas, faixa, pílula, fatos nem barra);
 *   · medidas a 1440 e 390: sem rolagem lateral, alvos de 44px, contraste do rosa da faixa.
 *
 * Rodar:  node --test tests/frontend/dashboard_v2_gastos_dia_topo.test.mjs   (mudou webapp/src, rode `npm --prefix webapp run build`)
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { chromium } from "playwright";
import { tabela } from "../../scripts/gerar_gastos_dia_casos.mjs";
import { RESPOSTAS, abrirPainel, exigeArtefatoEmDia } from "./_painel.mjs";
import { contraMedia, diaAbrivel, mediaDoPeriodo, vizinho } from "../../webapp/src/dashboard/lib/gastos-dia.ts";

let browser;
before(async () => { exigeArtefatoEmDia(); browser = await chromium.launch(); });
after(() => browser?.close());

const GASTOS = (u) => u.pathname === "/api/v2/gastos";
const k = (n) => `2026-10-${String(n).padStart(2, "0")}`;
const cat = (chave, valor) => ({ chave, valor });
const dia = (n, total, categorias, quantidade = categorias.length, maiores = [{ descricao: "Compra", valor: categorias[0]?.valor ?? total, categoria: categorias[0]?.chave ?? "sem categoria" }]) => ({
  dia: k(n), total, quantidade, categorias, maiores,
});
// hoje = 14. Soma até hoje = 1400 em 14 dias → média R$ 100,00/dia exatos. A conta de cada dia contra ela (pct):
//   2 → 0 (na média)   3 → −5 (verde, borda)   4 → −4 (cinza)   5 → +14 (cinza)   6 → +15 (âmbar, borda)   8 → +80 (âmbar)
//   1 → +550   14 (hoje) e 20 (agendado, fora da média) → sem pílula
const DIAS = [
  dia(1, "650.00", [cat("alimentacao", "650.00")]),
  dia(2, "100.00", [cat("alimentacao", "60.00"), cat("sem categoria", "40.00")], 3),
  dia(3, "95.00", [cat("alimentacao", "95.00")]),
  dia(4, "96.00", [cat("alimentacao", "96.00"), cat("sem categoria", "0.00"), cat("transporte", "-0.50")], 2), // fatia ≤ 0: o servidor pode mandar
  dia(5, "114.00", [cat("alimentacao", "70.00"), cat("sem categoria", "44.00")]),
  dia(6, "115.00", [cat("alimentacao", "115.00")]),
  dia(8, "180.00", [cat("alimentacao", "100.00"), cat("transporte", "80.00")], 4), // "transporte" não está no catálogo: nome = a chave, emoji e cor neutros
  dia(14, "50.00", [cat("alimentacao", "50.00")]),
  dia(20, "40.00", [cat("sem categoria", "40.00")]),
];
const comDias = (dias, extra = {}) => ({ ...RESPOSTAS.gastos.padrao, dias, ...extra });

async function abrir({ width = 1440, height, dias = DIAS, extra, catalogo } = {}) {
  const r = await abrirPainel(browser, { width, gastos: "padrao", lancamentos: "dia_7", espera: "#w-cat-detalhe .detail-facts" });
  await r.ctx.route(GASTOS, (rt) => rt.fulfill({ json: comDias(dias, { ...(catalogo && { categorias: catalogo }), ...extra }) }));
  if (height) await r.page.setViewportSize({ width, height });
  await r.ir("/gastos");
  return r;
}
const celula = (page, n) => page.locator("#w-calendario .cal-day", { hasText: new RegExp(`^${n}$`) });
const abrirDia = async (page, n) => { await celula(page, n).click(); await page.locator("#gastos-compras .dia-data").waitFor(); };
const topo = (page) => page.evaluate(() => {
  const q = (s) => document.querySelector(`#gastos-compras ${s}`);
  const pil = q(".dia-pilula");
  return {
    data: q(".dia-data")?.textContent, rotulo: q(".dia-rotulo")?.textContent ?? null, total: q(".dia-total")?.textContent ?? null,
    pilula: pil ? pil.textContent.replace(/\s+/g, " ").trim() : null, tom: pil?.dataset.tom ?? null,
    media: q(".dia-media .faint")?.textContent ?? null,
    fatos: [...document.querySelectorAll("#gastos-compras .dia-facts > div")].map((d) => `${d.querySelector("dt").textContent} ${d.querySelector("dd").textContent}`.replace(/\s+/g, " ").trim()),
    fatias: document.querySelectorAll("#gastos-compras .comp-bar span").length,
    legenda: [...document.querySelectorAll("#gastos-compras .dia-legend li")].map((l) => l.textContent.replace(/\s+/g, " ").trim()),
    vazio: [...document.querySelectorAll("#gastos-compras .faint")].some((p) => p.textContent === "Nenhum gasto neste dia."),
    faixa: !!q(".dia-faixa"),
    icones: { fato: [...document.querySelectorAll("#gastos-compras .dia-facts i")].map((i) => i.className), legenda: [...document.querySelectorAll("#gastos-compras .dia-legend li i")].map((i) => i.className) },
  };
});

// --- lib: unidade --------------------------------------------------------------------------------------------

const d = (extra = {}) => ({ mes: "2026-10", hoje: k(14), historico_desde: null, dias: DIAS, ...extra });

test("lib mediaDoPeriodo: de = max(dia 1, histórico), ate = min(hoje, fim), vividos só em [de, ate] e a média é Σ ÷ nDias", () => {
  const m = mediaDoPeriodo(d());
  assert.deepEqual([m.de, m.ate, m.fim, m.nDias, m.vividos.length, m.media], [k(1), k(14), k(31), 14, 8, 100]); // o dia 20 (agendado) fica fora
  const cortado = mediaDoPeriodo(d({ historico_desde: k(6) })); // antes do corte o dia é desconhecido, não zero
  assert.deepEqual([cortado.de, cortado.nDias, cortado.vividos.length, cortado.media], [k(6), 9, 3, (115 + 180 + 50) / 9]);
  assert.equal(mediaDoPeriodo(d({ hoje: k(31) })).nDias, 31); // mês passado: vai até o fim
  assert.equal(mediaDoPeriodo(d({ historico_desde: k(20) })).media, null); // corte depois de hoje: nenhum dia vivido, sem média
  assert.equal(mediaDoPeriodo(d({ dias: [dia(8, "0.00", [])] })).media, 0); // sem gasto nenhum: média 0, não null
});

test("lib diaAbrivel/vizinho: dentro de [de, fim]; o futuro só com gasto agendado; passado sem gasto abre; vizinho é ±1 no mês", () => {
  const abre = (n, extra) => diaAbrivel(k(n), d(extra));
  assert.deepEqual([abre(1), abre(9), abre(14), abre(15), abre(20), abre(31)], [true, true, true, false, true, false]);
  assert.equal(abre(5, { historico_desde: k(6) }), false); // antes do corte
  assert.equal(diaAbrivel("2026-11-01", d()), false); // fora do mês
  assert.deepEqual([vizinho(k(1), -1, d()), vizinho(k(1), 1, d()), vizinho(k(14), 1, d()), vizinho(k(14), -1, d())], [null, k(2), null, k(13)]);
  assert.deepEqual([vizinho(k(19), 1, d()), vizinho(k(20), -1, d()), vizinho(k(31), 1, d({ hoje: k(31) }))], [k(20), null, null]);
});

test("lib contraMedia: pct inteiro, tom nas bordas (−5 verde, +15 âmbar), 0 é 'na média'; sem média utilizável, nada", () => {
  const c = (total, media = 100) => contraMedia(total, media);
  assert.deepEqual([c(100), c(95), c(96), c(114), c(115), c(40), c(180)].map((x) => [x.pct, x.tom]),
    [[0, ""], [-5, "gain"], [-4, ""], [14, ""], [15, "warn"], [-60, "gain"], [80, "warn"]]);
  assert.equal(Object.is(c(99.6).pct, 0), true); // −0,4% arredonda a 0 e não vira −0
  assert.deepEqual([c(10, null), c(10, 0), c(10, -3), c(NaN)], [null, null, null, null]);
});

test("lib: a tabela dourada commitada (tests/fixtures/gastos_dia_casos.json, lida pelo jest do app) é a saída ATUAL do web; mudou o web, `node scripts/gerar_gastos_dia_casos.mjs`", () => {
  const commitada = JSON.parse(readFileSync(new URL("../fixtures/gastos_dia_casos.json", import.meta.url), "utf8"));
  assert.deepEqual(commitada, JSON.parse(JSON.stringify(tabela())));
});

// --- pílula --------------------------------------------------------------------------------------------------

test("pílula: cada estado, texto + seta (nunca só cor) e a média do mês ao lado; sem pílula no dia em curso, no agendado, no dia sem gasto", async () => {
  const { ctx, page, erros } = await abrir();
  const lido = {};
  for (const n of [1, 2, 3, 4, 5, 6, 8, 14, 20, 9]) { await abrirDia(page, n); lido[n] = await topo(page); }
  await ctx.close();
  const p = (n) => [lido[n].pilula, lido[n].tom];
  assert.deepEqual(p(2), ["na média", null]); // |pct| < 1: cinza, sem seta
  assert.deepEqual(p(3), ["↓ 5% abaixo da média", "gain"]); // a borda: −5 já é verde
  assert.deepEqual(p(4), ["↓ 4% abaixo da média", null]); // −4: abaixo, mas cinza
  assert.deepEqual(p(5), ["↑ 14% acima da média", null]); // +14: acima, mas cinza
  assert.deepEqual(p(6), ["↑ 15% acima da média", "warn"]); // a borda: +15 já é âmbar
  assert.deepEqual(p(8), ["↑ 80% acima da média", "warn"]);
  assert.deepEqual(p(1), ["↑ 550% acima da média", "warn"]);
  for (const n of [2, 3, 4, 5, 6, 8, 1]) assert.equal(lido[n].media, "média do mês: R$ 100,00/dia", `dia ${n}`); // o mesmo número e formato do calendário
  // sem pílula: hoje (14) e agendado (20) mostram o total e a média, nunca a comparação
  assert.deepEqual([lido[14].pilula, lido[14].rotulo, lido[14].total, lido[14].media], [null, "Gasto no dia", "R$ 50,00", "média do mês: R$ 100,00/dia"]);
  assert.deepEqual([lido[20].pilula, lido[20].rotulo, lido[20].total], [null, "Agendado para o dia", "R$ 40,00"]);
  // dia passado sem gasto: só o texto, sem faixa, pílula, fatos nem barra
  assert.deepEqual([lido[9].vazio, lido[9].faixa, lido[9].pilula, lido[9].fatos, lido[9].fatias], [true, false, null, [], 0]);
  assert.deepEqual(erros, []);
});

test("pílula e média sem média utilizável: dia com total zero (média 0) não compara nem mostra 'média do mês'", async () => {
  const { ctx, page } = await abrir({ dias: [dia(8, "0.00", [cat("alimentacao", "0.00")])] });
  await abrirDia(page, 8);
  const t = await topo(page);
  await ctx.close();
  assert.deepEqual([t.total, t.pilula, t.media], ["R$ 0,00", null, null]);
});

test("o calendário e o card leem a mesma regra: a média do calendário é a do card e o disabled de cada dia é `!diaAbrivel`", async () => {
  const { ctx, page } = await abrir();
  const cal = await page.evaluate(() => ({ lede: document.querySelector("#w-calendario .w-lede").textContent, off: [...document.querySelectorAll("#w-calendario .cal-day")].map((b) => b.disabled) }));
  await ctx.close();
  assert.match(cal.lede, /^média R\$ 100,00\/dia/);
  assert.deepEqual(cal.off, Array.from({ length: 31 }, (_, i) => !diaAbrivel(k(i + 1), d())));
});

// --- fatos e barra -------------------------------------------------------------------------------------------

test("fatos: Compras, Maior compra e Mais em (emoji e nome do catálogo; chave fora dele = a chave com emoji neutro)", async () => {
  const { ctx, page } = await abrir();
  await abrirDia(page, 2);
  const a = await topo(page);
  await abrirDia(page, 8);
  const b = await topo(page);
  await ctx.close();
  assert.deepEqual(a.fatos, ["Compras 3", "Maior compra R$ 60,00", "Mais em alimentação"]);
  assert.deepEqual(b.fatos, ["Compras 4", "Maior compra R$ 100,00", "Mais em alimentação"]);
  assert.deepEqual(b.legenda, ["alimentação · R$ 100,00", "transporte · R$ 80,00"]);
  // o ícone vem do `icone` do servidor (nunca o emoji): o do catálogo na legenda e no fato; chave fora do catálogo = `tag`
  assert.deepEqual(b.icones, { fato: ["ph ph-fork-knife"], legenda: ["ph ph-fork-knife", "ph ph-tag"] });
});

test("barra: só fatias > 0 (a de zero e a negativa saem), com ≥ 2 fatias; legenda com valor e sem percentual; uma fatia não desenha barra", async () => {
  const { ctx, page } = await abrir();
  await abrirDia(page, 2);
  const duas = await topo(page);
  const flex = await page.evaluate(() => [...document.querySelectorAll("#gastos-compras .comp-bar span")].map((s) => [getComputedStyle(s).flexGrow, getComputedStyle(s).backgroundColor]));
  await abrirDia(page, 4); // o servidor mandou 3 categorias, 2 delas ≤ 0: sobra uma
  const uma = await topo(page);
  await abrirDia(page, 3);
  const so = await topo(page);
  await ctx.close();
  assert.deepEqual([duas.fatias, duas.legenda], [2, ["alimentação · R$ 60,00", "sem categoria · R$ 40,00"]]);
  assert.deepEqual(flex, [["60", "rgb(249, 115, 22)"], ["40", "rgb(148, 163, 184)"]]); // a largura é o próprio valor; a cor, a do catálogo
  assert.deepEqual([uma.fatias, uma.legenda, so.fatias, so.legenda], [0, [], 0, []]);
  assert.deepEqual(uma.fatos.slice(-1), ["Mais em alimentação"]); // a de zero nunca é "Mais em"
});

test("categoria escolhida: o card segue a categoria (tag 'Só …'), sem barra nem 'Mais em'; a pílula fica (compara com a média da categoria, a do calendário)", async () => {
  const { ctx, page } = await abrir();
  // O servidor só manda a categoria escolhida; aqui o dia 7 traz DUAS, para provar que o card não desenha barra nem "Mais em" mesmo assim.
  const catDia = RESPOSTAS.gastos_categoria.padrao;
  await ctx.route((u) => u.pathname === "/api/v2/gastos/categoria", (r) => r.fulfill({ json: { ...catDia, dias: catDia.dias.map((x) => (x.dia === k(7) ? { ...x, categorias: [cat("alimentacao", "120.00"), cat("sem categoria", "80.00")] } : x)) } }));
  await page.locator("#w-categorias .cat", { hasText: "sem categoria" }).click();
  await page.locator("#w-calendario .tag-demo").waitFor();
  await abrirDia(page, 7);
  await page.locator("#gastos-compras .tag-demo", { hasText: "Só alimentação" }).waitFor();
  const t = await topo(page);
  const cal = await page.locator("#w-calendario .w-lede").textContent();
  await ctx.close();
  assert.deepEqual([t.fatias, t.legenda, t.fatos], [0, [], ["Compras 1", "Maior compra R$ 200,00"]]);
  assert.equal(t.media, "média do mês: R$ 21,43/dia"); // (100 + 200) / 14, o mesmo da linha "Só esta categoria · média" do calendário
  assert.match(cal, /^Só esta categoria · média R\$ 21,43\/dia/);
  assert.equal(t.pilula, "↑ 833% acima da média");
});

// --- setas ---------------------------------------------------------------------------------------------------

const foco = (page) => page.evaluate(() => ({ id: document.activeElement.id, rotulo: document.activeElement.getAttribute("aria-label") }));
const seta = (page, nome) => page.locator("#gastos-compras .dia-seta").filter({ has: page.locator(`i.ph-arrow-${nome}`) });

test("setas: navegam ±1 dia, o nome acessível diz o dia, a lista é pedida do dia novo e o foco FICA no botão apertado", async () => {
  const { ctx, page, erros } = await abrir();
  const pedidos = [];
  page.on("request", (r) => { const u = new URL(r.url()); if (u.pathname === "/api/v2/lancamentos") pedidos.push(u.search); });
  await abrirDia(page, 5);
  assert.equal(await seta(page, "left").getAttribute("aria-label"), "Dia anterior, dom, 4 de outubro");
  assert.equal(await seta(page, "right").getAttribute("aria-label"), "Próximo dia, ter, 6 de outubro");
  await seta(page, "left").click();
  await page.locator("#gastos-compras .dia-data", { hasText: "dom, 4 de outubro" }).waitFor();
  await page.waitForTimeout(150); // o rAF do card: se ele roubasse o foco, já teria roubado
  assert.deepEqual(await foco(page), { id: "", rotulo: "Dia anterior, sáb, 3 de outubro" }); // continua no botão (que agora aponta o dia 3)
  assert.equal(pedidos.at(-1), "?mes=2026-10&dia=2026-10-04&tipo=saida&guardado=true");
  await page.keyboard.press("Enter"); // pelo teclado, sem tocar no mouse: segue andando
  await page.locator("#gastos-compras .dia-data", { hasText: "sáb, 3 de outubro" }).waitFor();
  assert.equal((await foco(page)).rotulo, "Dia anterior, sex, 2 de outubro");
  await seta(page, "right").click();
  await page.locator("#gastos-compras .dia-data", { hasText: "dom, 4 de outubro" }).waitFor();
  assert.equal((await foco(page)).rotulo, "Próximo dia, seg, 5 de outubro");
  assert.equal(await page.locator("#w-calendario .cal-day[aria-expanded=true]").textContent(), "4"); // o calendário acompanha
  // o anúncio: região polida pequena com a data e o total, sem mover o foco
  assert.equal(await page.locator("#gastos-compras [role=status].sr-only").textContent(), "dom, 4 de outubro. Gasto no dia: R$ 96,00");
  await ctx.close();
  assert.deepEqual(erros, []);
});

test("setas: desabilitam no limite (dia 1, o dia seguinte ao hoje sem gasto agendado) e, ao desabilitar, o foco vai ao card; o calendário continua focando o card", async () => {
  const { ctx, page } = await abrir();
  await abrirDia(page, 2);
  assert.equal(await seta(page, "left").isDisabled(), false);
  await seta(page, "left").click(); // dia 1: o limite do mês
  await page.locator("#gastos-compras .dia-data", { hasText: "qui, 1 de outubro" }).waitFor();
  await page.waitForFunction(() => document.activeElement.id === "gastos-compras"); // o botão desabilitou: foco no card, não no BODY
  assert.equal(await seta(page, "left").isDisabled(), true);
  assert.equal(await seta(page, "left").getAttribute("aria-label"), "Dia anterior");
  assert.equal(await seta(page, "right").isDisabled(), false);

  await abrirDia(page, 13);
  await seta(page, "right").click(); // dia 14 (hoje): o 15 não existe ainda
  await page.locator("#gastos-compras .dia-data", { hasText: "qua, 14 de outubro" }).waitFor();
  await page.waitForFunction(() => document.activeElement.id === "gastos-compras");
  assert.equal(await seta(page, "right").isDisabled(), true);

  await abrirDia(page, 20); // agendado: abre, mas os dois vizinhos (19 e 21) são futuro sem gasto
  assert.deepEqual([await seta(page, "right").isDisabled(), await seta(page, "left").isDisabled()], [true, true]);
  await ctx.close();
});

test("foco: depois de usar as setas, tocar num dia do calendário volta a focar o card", async () => {
  const { ctx, page } = await abrir();
  await abrirDia(page, 5);
  await seta(page, "right").click();
  await page.locator("#gastos-compras .dia-data", { hasText: "ter, 6 de outubro" }).waitFor();
  await celula(page, 8).click();
  await page.locator("#gastos-compras .dia-data", { hasText: "qui, 8 de outubro" }).waitFor();
  await page.waitForFunction(() => document.activeElement.id === "gastos-compras");
  await ctx.close();
});

// --- modo categoria ------------------------------------------------------------------------------------------

test("modo categoria compacto: título com emoji, uma linha 'R$ … · N compras'; sem setas, faixa, pílula, fatos nem barra; lista e pé como antes", async () => {
  const { ctx, page } = await abrir();
  await page.getByRole("button", { name: "Ver compras" }).click();
  await page.locator("#w-compras-h", { hasText: "· outubro" }).waitFor();
  await page.locator("#gastos-compras .lanc-linha").first().waitFor();
  const r = await page.evaluate(() => ({
    titulo: document.querySelector("#w-compras-h").textContent.trim(), icone: document.querySelector("#w-compras-h i")?.className, lede: document.querySelector("#gastos-compras .w-lede").textContent,
    ausentes: [".dia-nav", ".dia-seta", ".dia-faixa", ".dia-pilula", ".dia-facts", ".comp-bar", ".dia-lista-t"].filter((s) => document.querySelector(`#gastos-compras ${s}`)),
    pe: [...document.querySelectorAll("#gastos-compras .compras-pe .link")].map((b) => b.textContent),
  }));
  await ctx.close();
  assert.deepEqual(r, { titulo: "Alimentação · outubro", icone: "ph ph-fork-knife", lede: "R$ 300,00 · 2 compras", ausentes: [], pe: ["Ver em Lançamentos", "Fechar"] });
});

test("modo categoria: uma compra é 'compra' (singular)", async () => {
  const { ctx, page } = await abrir();
  await ctx.route((u) => u.pathname === "/api/v2/gastos/categoria", (r) => r.fulfill({ json: { ...RESPOSTAS.gastos_categoria.padrao, total: "49.90", compras: 1 } }));
  await page.getByRole("button", { name: "Ver compras" }).click();
  await page.locator("#gastos-compras .w-lede", { hasText: "R$ 49,90" }).waitFor();
  assert.equal(await page.locator("#gastos-compras .w-lede").textContent(), "R$ 49,90 · 1 compra");
  await ctx.close();
});

// --- medidas -------------------------------------------------------------------------------------------------

const lum = ([r, g, b]) => { const f = (c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); };
const contraste = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };

for (const width of [1440, 390]) {
  test(`${width}px: sem rolagem lateral, alvos de 44px, total dentro da faixa e contraste do rosa ≥ 3:1`, async () => {
    const { ctx, page } = await abrir({ width });
    await abrirDia(page, 2);
    const m = await page.evaluate(() => {
      const box = (s) => { const r = document.querySelector(`#gastos-compras ${s}`).getBoundingClientRect(); return { x: r.x, r: r.right, w: r.width, h: r.height }; };
      const rgb = (c) => c.match(/[\d.]+/g).map(Number);
      const cs = (s) => getComputedStyle(document.querySelector(`#gastos-compras ${s}`));
      const painel = rgb(getComputedStyle(document.querySelector("#gastos-compras")).backgroundColor);
      const faixa = rgb(cs(".dia-faixa").backgroundColor); // rgba do rosa a 10%
      const fundo = faixa.slice(0, 3).map((c, i) => Math.round(c * faixa[3] + painel[i] * (1 - faixa[3])));
      return {
        lateral: document.scrollingElement.scrollWidth - document.scrollingElement.clientWidth,
        setas: [...document.querySelectorAll("#gastos-compras .dia-seta")].map((b) => { const r = b.getBoundingClientRect(); return [Math.round(r.width), Math.round(r.height)]; }),
        pe: [...document.querySelectorAll("#gastos-compras .compras-pe .link")].map((b) => Math.round(b.getBoundingClientRect().height)),
        faixa: box(".dia-faixa"), total: box(".dia-total"), fonte: cs(".dia-total").fontSize, peso: cs(".dia-total").fontWeight,
        cor: rgb(cs(".dia-total").color).slice(0, 3), fundo,
        pilula: box(".dia-pilula"), card: box(""), fatos: box(".dia-facts"),
        estouram: [...document.querySelectorAll("#gastos-compras *:not(.sr-only)")].filter((e) => e.scrollWidth > e.clientWidth && e.clientWidth > 0).map((e) => e.className),
      };
    });
    await ctx.close();
    const c = contraste(m.cor, m.fundo);
    console.log(`# ${width}px:`, JSON.stringify({ ...m, contraste: Number(c.toFixed(2)) }));
    assert.equal(m.lateral, 0);
    assert.deepEqual(m.estouram, []);
    assert.deepEqual(m.setas, [[44, 44], [44, 44]]);
    assert.ok(m.pe.every((h) => h >= 44), JSON.stringify(m.pe));
    assert.ok(m.total.x >= m.faixa.x && m.total.r <= m.faixa.r + 0.5, "o total passou da faixa");
    assert.ok(m.pilula.r <= m.faixa.r && m.fatos.r <= m.card.r + 0.5, "pílula ou fatos passaram da caixa");
    assert.ok(parseFloat(m.fonte) >= 24 && Number(m.peso) >= 700, `texto grande bold: ${m.fonte} ${m.peso}`);
    assert.deepEqual(m.cor, [255, 45, 142]); // o rosa da marca, o token --pink
    assert.ok(c >= 3, `contraste ${c}`);
  });
}

// --- correções do ataque do Tester ---------------------------------------------------------------------------

const medeSeta = (page) => page.evaluate(() => ({ sy: Math.round(scrollY), top: Math.round(document.querySelector("#gastos-compras .dia-seta").getBoundingClientRect().top), card: Math.round(document.querySelector("#gastos-compras").getBoundingClientRect().height) }));

for (const [width, height] of [[390, 844], [1440, 900]]) {
  test(`${width}x${height}: apertar a seta de dia não rola a página (card com muitas linhas, mais alto que a janela) e o botão fica onde estava`, async () => {
    const { ctx, page } = await abrir({ width, height });
    // a linha limpa (sem contexto) é mais baixa: dobra as linhas da fixture para o card seguir mais alto que a janela
    const pagina = RESPOSTAS.lancamentos.estados;
    await ctx.route((u) => u.pathname === "/api/v2/lancamentos", (r) => r.fulfill({ json: { ...pagina, itens: [...pagina.itens, ...pagina.itens.map((i) => ({ ...i, id: `${i.id}-b` }))], proximo: null } }));
    await abrirDia(page, 5);
    await page.locator("#gastos-compras .lanc-linha").first().waitFor();
    await page.waitForTimeout(500);
    await page.evaluate(() => { const r = document.querySelector("#gastos-compras .dia-seta").getBoundingClientRect(); scrollBy(0, r.top - innerHeight / 2); }); // as setas no meio da janela
    await page.waitForTimeout(200);
    const antes = await medeSeta(page);
    for (const lado of ["right", "right", "left"]) {
      await seta(page, lado).click();
      await page.locator("#gastos-compras .lanc-linha").first().waitFor();
      await page.waitForTimeout(500); // o rAF do card e o da lista pronta
    }
    const depois = await medeSeta(page);
    await ctx.close();
    assert.ok(antes.card > height, `o card (${antes.card}px) tem de ser mais alto que a janela (${height}px), senão o teste não mede`);
    assert.ok(Math.abs(depois.sy - antes.sy) <= 1 && Math.abs(depois.top - antes.top) <= 1, JSON.stringify({ antes, depois }));
  });
}

test("390: a troca de dia vinda do calendário (foco fora do card) continua rolando até o card, mesmo depois de usar as setas", async () => {
  const { ctx, page } = await abrir({ width: 390, height: 844 });
  await abrirDia(page, 5);
  await seta(page, "right").click(); // marca o dia 6 como "chegou pelas setas"
  await page.locator("#gastos-compras .dia-data", { hasText: "ter, 6 de outubro" }).waitFor();
  await page.waitForTimeout(500);
  await page.evaluate(() => scrollTo(0, 0));
  await celula(page, 8).click();
  await page.locator("#gastos-compras .dia-data", { hasText: "qui, 8 de outubro" }).waitFor();
  await page.waitForTimeout(500);
  const top = await page.evaluate(() => document.querySelector("#gastos-compras").getBoundingClientRect().top);
  await ctx.close();
  assert.ok(top < 844, `o card ficou abaixo da janela (top ${top})`); // sem o scroll do calendário ele ficaria fora da janela
});

test("região: o nome acessível é o título + a data no modo dia; no modo categoria, só o título", async () => {
  const { ctx, page } = await abrir();
  await abrirDia(page, 5);
  const dia5 = await page.getByRole("region", { name: "Seu dia seg, 5 de outubro", exact: true }).count();
  await seta(page, "left").click();
  await page.locator("#gastos-compras .dia-data", { hasText: "dom, 4 de outubro" }).waitFor();
  const dia4 = await page.getByRole("region", { name: "Seu dia dom, 4 de outubro", exact: true }).count();
  await page.getByRole("button", { name: "Ver compras" }).click();
  await page.locator("#w-compras-h", { hasText: "· outubro" }).waitFor();
  const categoria = await page.getByRole("region", { name: "Alimentação · outubro", exact: true }).count();
  await ctx.close();
  assert.deepEqual([dia5, dia4, categoria], [1, 1, 1]);
});

const LONGO = "Supercalifragilistico".repeat(4).slice(0, 70); // 70 caracteres, sem espaço (o servidor aceita até 80)
for (const width of [320, 390, 1101, 1440]) {
  test(`${width}px: nome de categoria de 70 caracteres sem espaço não gera rolagem lateral nem passa da borda do card (barra, legenda e "Mais em")`, async () => {
    const catalogo = RESPOSTAS.gastos.padrao.categorias.map((c, i) => ({ ...c, nome: i === 0 ? LONGO : LONGO.toUpperCase() }));
    const { ctx, page } = await abrir({ width, catalogo });
    const lido = {};
    for (const n of [2, 3]) { // 2: duas fatias (barra + legenda + "Mais em"); 3: uma fatia (só "Mais em")
      await abrirDia(page, n);
      lido[n] = await page.evaluate(() => {
        const card = document.querySelector("#gastos-compras").getBoundingClientRect();
        return {
          lateral: document.scrollingElement.scrollWidth - document.scrollingElement.clientWidth,
          fora: [...document.querySelectorAll("#gastos-compras *:not(.sr-only)")].filter((e) => { const r = e.getBoundingClientRect(); return r.width > 0 && (r.right > card.right + 0.5 || r.left < card.left - 0.5); }).map((e) => e.className || e.tagName),
          nomes: document.querySelectorAll("#gastos-compras .dia-facts dd, #gastos-compras .dia-legend li").length,
        };
      });
    }
    await ctx.close();
    assert.deepEqual([lido[2].lateral, lido[2].fora, lido[3].lateral, lido[3].fora], [0, [], 0, []]);
    assert.ok(lido[2].nomes >= 5 && lido[3].nomes >= 3, "o nome longo tem de estar na tela (fato e legenda), senão o teste não mede");
  });
}

test("dia que existe com total R$ 0,00 e nenhuma compra: sem pílula (nem '100% abaixo') e sem o fato 'Compras 0'; o dia com gasto segue com os dois", async () => {
  const { ctx, page } = await abrir({ dias: [...DIAS, dia(9, "0.00", [], 0, [])] }); // média do mês > 0: a comparação "contra zero" seria possível
  await abrirDia(page, 9);
  const zero = await topo(page);
  await abrirDia(page, 3);
  const com = await topo(page);
  await ctx.close();
  assert.deepEqual([zero.total, zero.pilula, zero.media, zero.fatos], ["R$ 0,00", null, "média do mês: R$ 100,00/dia", []]);
  assert.deepEqual([com.pilula, com.fatos.slice(0, 1)], ["↓ 5% abaixo da média", ["Compras 1"]]);
});

test("'Maior compra' é a primeira de `maiores` (o servidor manda a maior primeiro), não a última", async () => {
  const dias = [...DIAS.filter((x) => x.dia !== k(2)), dia(2, "100.00", [cat("alimentacao", "100.00")], 3, [
    { descricao: "Grande", valor: "70.00", categoria: "alimentacao" }, { descricao: "Média", valor: "20.00", categoria: "alimentacao" }, { descricao: "Pequena", valor: "10.00", categoria: "alimentacao" }])];
  const { ctx, page } = await abrir({ dias });
  await abrirDia(page, 2);
  const t = await topo(page);
  await ctx.close();
  assert.deepEqual(t.fatos, ["Compras 3", "Maior compra R$ 70,00", "Mais em alimentação"]);
});

test("com `historico_desde` no meio do mês, a média do card é a do calendário e a pílula parte dela (não da média do mês inteiro)", async () => {
  const { ctx, page } = await abrir({ extra: { historico_desde: k(6) } }); // média = (115 + 180 + 50) / 9 dias = R$ 38,33; sem o corte seria R$ 100,00
  await abrirDia(page, 8);
  const t = await topo(page);
  const lede = await page.locator("#w-calendario .w-lede").textContent();
  await ctx.close();
  assert.match(lede, /^média R\$ 38,33\/dia/);
  assert.deepEqual([t.media, t.pilula], ["média do mês: R$ 38,33/dia", "↑ 370% acima da média"]); // 180 / 38,33 − 1
});

test("as três pílulas (verde, âmbar, cinza) têm cores renderizadas distintas entre si", async () => {
  const { ctx, page } = await abrir();
  const cor = {};
  for (const [nome, n, texto] of [["gain", 3, "5% abaixo"], ["warn", 6, "15% acima"], ["neutra", 4, "4% abaixo"]]) {
    await abrirDia(page, n);
    await page.locator("#gastos-compras .dia-pilula", { hasText: texto }).waitFor(); // o dia novo já está na tela
    await page.waitForTimeout(100); // o prefers-reduced-motion deixa 1ms de transição em tudo: a cor computada atrasa um quadro
    cor[nome] = await page.evaluate(() => { const c = getComputedStyle(document.querySelector("#gastos-compras .dia-pilula")); return `${c.color}|${c.backgroundColor}`; });
  }
  await ctx.close();
  assert.equal(new Set(Object.values(cor)).size, 3, JSON.stringify(cor));
  for (const v of Object.values(cor)) assert.ok(!v.includes("rgba(0, 0, 0, 0)"), v); // todas têm fundo
});
