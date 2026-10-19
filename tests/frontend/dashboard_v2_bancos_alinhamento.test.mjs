// Bancos conectados: o desenho em colunas (conserto do #1142: o `.bancos-cab` ocupava a página inteira, o "+" ficava colado na borda da PÁGINA e a tela larga
// deixava a metade direita vazia). Com "Disponíveis para conectar" e viewport >= 1101 px: duas colunas, bancos à esquerda e disponíveis à direita, de topos alinhados;
// abaixo disso (e no celular) uma coluna, conectados primeiro. O "+" do topo alinha com a borda direita da coluna da direita (ou da única coluna), e o rótulo
// "Disponíveis para conectar" começa onde começa a coluna dele (sem o recuo de 4 px de antes).
//
// Controles negativos (desligue, rebuild `npm --prefix webapp run build`, o grupo tem de ficar vermelho; restaure e rebuild): em styles/bancos.css (a) tirar
// `max-width: 1100px` de `.bancos-cab.duas` (o "+" some longe da coluna da direita), (b) trocar `min-width: 1101px` por `min-width: 5000px` (nunca duas colunas),
// (f) tirar a regra `.bancos-esq > .banco-rotulo[aria-hidden="true"] { display: none; }` (a linha vazia ocupa 25 px em uma coluna), (c) pôr `padding: 0 4px` de volta em `.banco-rotulo`, (e) trocar `.bancos-esq > .banco-rotulo + * { margin-top: 0; }` por `margin-top: 20px` (o grupo da esquerda desce 20 px e os topos dos grupos não batem), (d) tirar `max-width: 720px` de `.bancos-cab` (o "+" some longe da lista em 1024 e sem "Disponíveis").
// Positivos: 1024 e 390 seguem em UMA coluna, sem rolagem horizontal; sem "Disponíveis" a coluna de bancos fica sozinha, sem buraco.
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { navegador } from "./_guia.mjs";
import { regiao } from "./_bancos.mjs";
import { telaRecon } from "./_bancos_reconectar.mjs";
import { linha, det } from "./_bancos_desconectar.mjs";

const browser = navegador();
void browser;
const TELAS = process.env.PB_TELAS; // opcional: pasta para os screenshots

const medidas = (page) => page.evaluate(() => {
  const r = (e) => { const b = e?.getBoundingClientRect(); return b && { left: b.left, right: b.right, top: b.top, bottom: b.bottom, width: b.width }; };
  const texto = (e) => { const g = document.createRange(); g.selectNodeContents(e); return g.getBoundingClientRect().left; };
  const grupo = document.querySelector("ul.banco-grupo[aria-label=Bancos]"), sec = document.querySelector("section.bancos-col"), rotulo = document.querySelector("section.bancos-col .banco-rotulo"), titulo = document.getElementById("page-title"), cols = document.querySelector(".bancos-cols");
  return { mais: r(document.querySelector(".bancos-cab .banco-mais")), grupo: r(grupo), sec: r(sec), cols: r(cols), vagas: r(document.querySelector(".bancos-esq > .banco-rotulo")), grupoDir: r(sec?.querySelector("ul.banco-grupo")), rotuloTexto: rotulo && texto(rotulo), tituloTexto: texto(titulo), rotuloEsq: rotulo && r(rotulo).left, rotuloTopo: rotulo && r(rotulo).top,
    ordem: !!sec && !!(grupo.compareDocumentPosition(sec) & Node.DOCUMENT_POSITION_FOLLOWING), largura: [document.documentElement.scrollWidth, innerWidth] };
});
const perto = (a, b, rotulo) => assert.ok(Math.abs(a - b) <= 1, `${rotulo}: ${a} × ${b}`);
async function foto(page, nome) { if (!TELAS) return; mkdirSync(TELAS, { recursive: true }); await page.screenshot({ path: join(TELAS, `${nome}.png`) }); }

for (const [width, height] of [[1440, 900], [1280, 800]]) {
  test(`${width}x${height}: duas colunas (bancos | disponíveis), topos alinhados, o '+' na borda direita da coluna da direita, o detalhe não desloca nada`, async () => {
    const { ctx, page } = await telaRecon({}, { width, height });
    await regiao(page).waitFor();
    const m = await medidas(page);
    await foto(page, `colunas-${width}`);
    await linha(page, "Nubank").click();
    await det(page).waitFor();
    const depois = await medidas(page);
    await foto(page, `colunas-${width}-detalhe`);
    await ctx.close();
    assert.ok(m.grupo.right < m.sec.left, `lado a lado: grupo até ${m.grupo.right}, disponíveis desde ${m.sec.left}`);
    perto(m.vagas.top, m.rotuloTopo, "topo das colunas (a linha de cima)");
    perto(m.grupo.top, m.grupoDir.top, "topo do grupo da esquerda × o da direita");
    perto(m.mais.right, m.sec.right, "+ × borda direita da coluna da direita");
    perto(m.rotuloEsq, m.sec.left, "rótulo × início da coluna");
    perto(m.rotuloTexto, m.sec.left, "texto do rótulo × início da coluna");
    assert.ok(m.grupo.width >= 480 && m.grupo.width <= 720 && m.sec.width >= 340, `larguras ${m.grupo.width} / ${m.sec.width}`);
    assert.ok(m.ordem, "conectados antes dos disponíveis no DOM");
    assert.ok(m.largura[0] <= m.largura[1], `rolagem horizontal ${m.largura}`);
    for (const k of ["grupo", "sec", "mais"]) { perto(depois[k].left, m[k].left, `${k} com o detalhe aberto`); perto(depois[k].top, m[k].top, `${k} topo com o detalhe aberto`); }
  });
}

for (const [width, height] of [[1024, 768], [390, 844]]) {
  test(`${width}x${height}: uma coluna (conectados e depois disponíveis), o '+' na borda direita da lista, rótulo alinhado ao título, sem rolagem horizontal`, async () => {
    const { ctx, page } = await telaRecon({}, { width, height });
    await regiao(page).waitFor();
    const m = await medidas(page);
    await foto(page, `coluna-${width}`);
    await ctx.close();
    assert.ok(m.sec.top >= m.grupo.bottom, `empilhadas: grupo até ${m.grupo.bottom}, disponíveis desde ${m.sec.top}`);
    perto(m.sec.left, m.grupo.left, "mesma borda esquerda");
    perto(m.mais.right, m.grupo.right, "+ × borda direita da lista");
    perto(m.rotuloTexto, m.tituloTexto, "texto do rótulo × texto do título");
    assert.ok(m.mais.left >= 0 && m.mais.right <= m.largura[1], `+ ${m.mais.left}..${m.mais.right} em ${m.largura[1]}`);
    assert.ok(m.ordem && m.largura[0] <= m.largura[1], `ordem ${m.ordem}, rolagem horizontal ${m.largura}`);
  });
}

test("1440x900 sem 'Disponíveis' (o /connectors falhou): a coluna de bancos fica sozinha à esquerda, sem buraco, e o '+' na borda direita dela", async () => {
  const { ctx, page } = await telaRecon({ conectoresRes: (r) => r.fulfill({ status: 500, json: { detail: "x" } }) }, { width: 1440, height: 900 });
  await page.waitForTimeout(600);
  const m = await medidas(page);
  await foto(page, "sozinha-1440");
  await ctx.close();
  assert.equal(m.sec, undefined);
  assert.ok(m.grupo.width <= 720 && m.grupo.width >= 600, `grupo ${m.grupo.width}`);
  perto(m.cols.width, m.grupo.width, "o conjunto tem a largura da coluna (sem buraco)");
  perto(m.mais.right, m.grupo.right, "+ × borda direita da lista");
  perto(m.grupo.left, m.tituloTexto, "grupo × título");
});

test("1440x900 sem vagas ('of_banks_max' nulo, a linha 'N de M' some): com a outra coluna, os grupos ainda começam na mesma altura", async () => {
  const { ctx, page } = await telaRecon({ limite: { ok: true, of_banks_max: null, em_uso: 2, pode_adicionar: true, code: null, message: null } }, { width: 1440, height: 900, vagas: "" });
  await regiao(page).waitFor();
  const m = await medidas(page);
  await ctx.close();
  perto(m.grupo.top, m.grupoDir.top, "topo do grupo da esquerda × o da direita");
});

for (const [width, height] of [[1024, 768], [390, 844]]) {
  test(`${width}x${height} sem vagas e com 'Disponíveis' (uma coluna): a linha vazia que alinha as duas colunas não ocupa espaço`, async () => {
    const { ctx, page } = await telaRecon({ limite: { ok: true, of_banks_max: null, em_uso: 2, pode_adicionar: true, code: null, message: null } }, { width, height, vagas: "" });
    await regiao(page).waitFor();
    const r = await page.evaluate(() => { const e = document.querySelector(".bancos-esq > .banco-rotulo"); return { display: getComputedStyle(e).display, altura: e.getBoundingClientRect().height }; });
    await ctx.close();
    assert.deepEqual(r, { display: "none", altura: 0 });
  });
}
