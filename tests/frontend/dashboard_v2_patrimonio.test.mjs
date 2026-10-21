/**
 * /painel#/patrimonio sobre /api/v2/patrimonio (fixtures `patrimonio`: vazio, continuo, com_quebra): a linha quebra
 * entre trechos (um `M` por trecho; trecho de 1 ponto é círculo), a variação do servidor, "ainda sem histórico",
 * o motivo `carteira_nao_confirmada` fora da tela, erro com "Tentar de novo", a composição em texto e 390/1440 sem
 * rolagem lateral. Dinheiro sempre do texto da API.
 *
 * Rodar:  node --test tests/frontend/dashboard_v2_patrimonio.test.mjs   (mudou webapp/src, rode `npm --prefix webapp run build`)
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { RESPOSTAS, abrirPainel, exigeArtefatoEmDia } from "./_painel.mjs";

let browser;
before(async () => { exigeArtefatoEmDia(); browser = await chromium.launch(); });
after(() => browser?.close());

const copia = (nome, muda = (d) => d) => muda(structuredClone(RESPOSTAS.patrimonio[nome]));
async function abrir({ resposta, width, nome = "com_quebra" } = {}) {
  const p = await abrirPainel(browser, { espera: null, width });
  await p.ctx.route("**/api/v2/patrimonio", resposta ?? ((r) => r.fulfill({ json: RESPOSTAS.patrimonio[nome] })));
  await p.ir("/patrimonio");
  await p.page.locator("#w-patrimonio .stat-value, #w-patrimonio [role=alert]").waitFor();
  return p;
}
const linha = (page) => page.locator("#w-patrimonio path.line").getAttribute("d");
const texto = (page, id) => page.locator(`#w-${id}`).innerText();

test("com quebra: um M por trecho, o ponto de 12/10 não liga ao de 13/10 e vira círculo; +R$ 20,00 desde 13/10", async () => {
  const { ctx, page, erros } = await abrir();
  await page.locator("#w-patrimonio svg[role=img]").waitFor();
  const d = await linha(page);
  const circulos = await page.locator("#w-patrimonio svg circle.cross-dot").count();
  const aside = await page.locator("#w-patrimonio .w-aside-num").innerText();
  const gain = await page.locator("#w-patrimonio .w-aside-num.gain").count();
  const aria = await page.locator("#w-patrimonio svg[role=img]").getAttribute("aria-label");
  const total = await page.locator("#w-patrimonio .stat-value").innerText();
  await ctx.close();
  assert.equal((d.match(/M/g) || []).length, 2);
  assert.equal((d.match(/L/g) || []).length, 1); // 13/10 -> hoje; o 12/10 está sozinho
  assert.equal(circulos, 1);
  assert.equal(aside, "+R$ 20,00 desde 13/10");
  assert.equal(gain, 1);
  assert.equal(total, "R$ 1.720,00");
  assert.match(aria, /de R\$ 500,00 para R\$ 1\.720,00/);
  assert.deepEqual(erros, []);
});

test("contínuo: um M só, 3 pontos (12, 13 e hoje ao vivo), desde 12/10", async () => {
  const { ctx, page } = await abrir({ nome: "continuo" });
  await page.locator("#w-patrimonio svg[role=img]").waitFor();
  const d = await linha(page);
  const dias = await page.locator("#w-patrimonio .sr-only tbody th").allInnerTexts();
  const aside = await page.locator("#w-patrimonio .w-aside-num").innerText();
  await ctx.close();
  assert.equal((d.match(/M/g) || []).length, 1);
  assert.equal((d.match(/L/g) || []).length, 2);
  assert.deepEqual(dias, ["12/10", "13/10", "14/10"]);
  assert.equal(aside, "+R$ 20,00 desde 12/10");
});

test("vazio: 'Ainda sem histórico', sem variação e sem gráfico", async () => {
  const { ctx, page } = await abrir({ nome: "vazio" });
  const t = await texto(page, "patrimonio");
  const aside = await page.locator("#w-patrimonio .w-aside-num").count();
  const svg = await page.locator("#w-patrimonio svg[role=img]").count();
  await ctx.close();
  assert.match(t, /Ainda sem histórico/);
  assert.equal(aside, 0);
  assert.equal(svg, 0);
});

test("variação zero real mostra R$ 0,00 desde DD/MM, sem verde; negativa com sinal de menos", async () => {
  for (const [valor, esperado, gain] of [["0.00", "R$ 0,00 desde 12/10", 0], ["-5.50", "−R$ 5,50 desde 12/10", 0], ["0e5", "R$ 0,00 desde 12/10", 0]]) {
    const { ctx, page } = await abrir({ resposta: (r) => r.fulfill({ json: copia("continuo", (d) => { d.variacao.valor = valor; return d; }) }) });
    const aside = await page.locator("#w-patrimonio .w-aside-num").innerText();
    const g = await page.locator("#w-patrimonio .w-aside-num.gain").count();
    await ctx.close();
    assert.equal(aside, esperado);
    assert.equal(g, gain);
  }
});

test("carteira_nao_confirmada não aparece (nem a da foto); banco_desatualizado aparece, sem repetir", async () => {
  const json = copia("continuo", (d) => {
    d.motivos = ["carteira_nao_confirmada", "banco_desatualizado"];
    d.historico[0].motivos = ["carteira_nao_confirmada", "banco_desatualizado"];
    return d;
  });
  const { ctx, page } = await abrir({ resposta: (r) => r.fulfill({ json }) });
  await page.locator("#w-composicao .wealth-rows").waitFor();
  const a = await page.locator("#w-patrimonio .selo").allInnerTexts();
  const b = await page.locator("#w-composicao .selo").allInnerTexts();
  await ctx.close();
  assert.deepEqual(a, ["banco desatualizado"]);
  assert.deepEqual(b, ["banco desatualizado"]);
});

test("a foto de hoje em historico sai da série: uma linha só, com o total ao vivo", async () => {
  const json = copia("continuo", (d) => { d.historico.push({ ...d.historico[1], dia: "2026-10-14", total: "9999.00" }); return d; });
  const { ctx, page } = await abrir({ resposta: (r) => r.fulfill({ json }) });
  await page.locator("#w-patrimonio svg[role=img]").waitFor();
  const linhas = await page.locator("#w-patrimonio .sr-only tbody tr").allInnerTexts();
  await ctx.close();
  assert.equal(linhas.filter((l) => l.startsWith("14/10")).length, 1);
  assert.match(linhas.find((l) => l.startsWith("14/10")), /1\.720,00/);
});

test("motivos: o da foto só vale na linha; o de hoje vale nos dois blocos", async () => {
  const json = copia("continuo", (d) => { d.motivos = ["saldo_ausente"]; d.historico[0].motivos = ["banco_desatualizado"]; return d; });
  const { ctx, page } = await abrir({ resposta: (r) => r.fulfill({ json }) });
  await page.locator("#w-composicao .wealth-rows").waitFor();
  const a = await page.locator("#w-patrimonio .selo").allInnerTexts();
  const b = await page.locator("#w-composicao .selo").allInnerTexts();
  await ctx.close();
  assert.deepEqual(a.sort(), ["banco desatualizado", "saldo ausente"]);
  assert.deepEqual(b, ["saldo ausente"]);
});

test("trechos vazio: a página não cai; total visível e 'Ainda sem histórico'", async () => {
  const json = copia("continuo", (d) => { d.trechos = []; return d; });
  const { ctx, page, erros } = await abrir({ resposta: (r) => r.fulfill({ json }) });
  const t = await texto(page, "patrimonio");
  await ctx.close();
  assert.match(t, /R\$ 1\.720,00/);
  assert.match(t, /Ainda sem histórico/);
  assert.doesNotMatch(t, /desde/);
  assert.deepEqual(erros, []);
});

test("#561: a tabela sr-only traz o mesmo do tooltip (dia, as 5 partes, total), linha a linha", async () => {
  const { ctx, page } = await abrir({ nome: "continuo" });
  await page.locator("#w-patrimonio svg[role=img]").waitFor();
  const tabela = await page.locator("#w-patrimonio .sr-only tbody tr").evaluateAll((trs) => trs.map((tr) => [...tr.children].map((c) => c.textContent.trim())));
  const cabecalho = await page.locator("#w-patrimonio .sr-only thead th").allTextContents();
  const box = await page.locator("#w-patrimonio .nw-chart").boundingBox();
  const n = tabela.length;
  const tooltip = [];
  for (let i = 0; i < n; i++) {
    await page.mouse.move(box.x + 6 + (i / (n - 1)) * (box.width - 12), box.y + box.height / 2);
    tooltip.push(await page.locator(".tip").evaluate((t) => [t.querySelector(".tip-title").textContent, ...[...t.querySelectorAll(".tip-row b")].map((b) => b.textContent), t.querySelector(".tip-value").textContent]));
  }
  const rotulos = await page.locator(".tip .tip-row").evaluateAll((rs) => rs.map((r) => r.textContent));
  await ctx.close();
  assert.equal(n, 3);
  assert.deepEqual(cabecalho, ["Dia", "Carteira", "Contas bancárias", "Investimentos do banco", "Caixinhas", "Investimentos manuais", "Total"]);
  assert.deepEqual(tabela, tooltip);
  assert.deepEqual(rotulos.map((r, i) => r.startsWith(cabecalho[1 + i])), [true, true, true, true, true]);
});

// retry do v2.ts (n < 2): os 3 primeiros pedidos falham; o 4º (o do botão) responde.
test("erro 500: 'Tentar de novo' relê e mostra o dado", async () => {
  let n = 0;
  const { ctx, page } = await abrir({ resposta: (r) => (n++ < 3 ? r.fulfill({ status: 500, json: { error: { code: "x", message: "x" } } }) : r.fulfill({ json: RESPOSTAS.patrimonio.continuo })) });
  const alerta = await page.locator("#w-patrimonio [role=alert]").innerText();
  await page.locator("#w-patrimonio").getByRole("button", { name: "Tentar de novo" }).click();
  await page.locator("#w-patrimonio .stat-value").waitFor();
  const t = await texto(page, "patrimonio");
  await ctx.close();
  assert.match(alerta, /Não deu para carregar o patrimônio/);
  assert.match(t, /R\$ 1\.720,00/);
});

test("composição: os 5 rótulos e os valores em texto; uma requisição só", async () => {
  let pedidos = 0;
  const { ctx, page } = await abrir({ nome: "continuo", resposta: (r) => { pedidos++; return r.fulfill({ json: RESPOSTAS.patrimonio.continuo }); } });
  await page.locator("#w-composicao .wealth-rows").waitFor();
  const linhas = await page.locator("#w-composicao .wealth-rows li").evaluateAll((ls) => ls.map((l) => [l.querySelector(".wealth-name").textContent, l.querySelector("b").textContent]));
  await ctx.close();
  assert.deepEqual(linhas, [["Carteira", "R$ 100,00"], ["Contas bancárias", "R$ 1.000,00"], ["Investimentos do banco", "R$ 500,00"], ["Caixinhas", "R$ 50,00"], ["Investimentos manuais", "R$ 70,00"]]);
  assert.equal(pedidos, 1);
});

test("composição com parte negativa ou tudo zero: sem NaN, barra sem a parte", async () => {
  const neg = copia("continuo", (d) => { d.partes.bancos = "-30.00"; return d; });
  let p = await abrir({ resposta: (r) => r.fulfill({ json: neg }) });
  await p.page.locator("#w-composicao .wealth-rows").waitFor();
  const t1 = await texto(p.page, "composicao");
  const barras = await p.page.locator("#w-composicao .comp-bar span").count();
  await p.ctx.close();
  assert.match(t1, /−R\$ 30,00/);
  assert.equal(barras, 4);
  p = await abrir({ nome: "vazio" });
  await p.page.locator("#w-composicao .wealth-rows").waitFor();
  const t2 = await texto(p.page, "composicao");
  const b0 = await p.page.locator("#w-composicao .comp-bar").count();
  await p.ctx.close();
  assert.doesNotMatch(t1 + t2, /NaN/);
  assert.equal(b0, 0);
});

for (const width of [1440, 390]) {
  test(`${width}px: a página não rola para o lado e o gráfico cabe`, async () => {
    const { ctx, page } = await abrir({ width });
    await page.locator("#w-patrimonio svg[role=img]").waitFor();
    const r = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth, svg: document.querySelector("#w-patrimonio svg").getBoundingClientRect().width, card: document.querySelector("#w-patrimonio").getBoundingClientRect().width }));
    await ctx.close();
    assert.ok(r.sw <= r.cw, JSON.stringify(r));
    assert.ok(r.svg <= r.card, JSON.stringify(r));
  });
}
