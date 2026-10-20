/**
 * Simulador de corte de hábito com backend (webapp/src/dashboard/widgets/SimuladorHabitos.tsx): bloco do Resumo e #/simulador.
 *
 *   · as categorias vêm da estimativa da previsão (zero GET a mais); o arraste é rascunho, o POST sai UMA vez, ao soltar;
 *   · a tela mostra o TEXTO da API (a resposta mockada é incoerente de propósito: não há conta no cliente);
 *   · resposta velha nunca fica no lugar da nova; Plus: convite sem requisição; sem histórico: texto e 0 POST;
 *   · 422 categoria_fora_da_estimativa: relê a previsão e avisa, sem número velho; protótipo (DEMO) mantém o exemplo.
 *
 * Rodar:  npm run test:frontend   (abre o artefato commitado: mudou webapp/src, rode `npm --prefix webapp run build`)
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { RESPOSTAS, abrirPainel, exigeArtefatoEmDia } from "./_painel.mjs";

let browser;
before(async () => { exigeArtefatoEmDia(); browser = await chromium.launch(); });
after(() => browser?.close());

const BASE = { espera: "#page-title", agora: "2026-10-06T15:00:00Z", previsao: "pro90_com_estimativa" };
// incoerente com a base: 12,34 de economia. Se a tela mostrar isso, o texto é o da API.
const OK = { ...RESPOSTAS.simulador.habitos, economia_mensal: "12.34" };
const env = (status, code, details) => ({ status, json: { error: { code, message: "x", details } } });

async function abrir({ resposta = { json: OK }, rota = "/simulador", ...opts } = {}) {
  const r = await abrirPainel(browser, { ...BASE, ...opts });
  r.pedidos = [];
  r.gets = 0;
  r.ctx.on("request", (q) => { if (q.method() === "GET" && new URL(q.url()).pathname === "/api/v2/previsao") r.gets++; });
  await r.ctx.route("**/api/v2/simulador/habitos", (rt) => { r.pedidos.push(rt.request().postDataJSON()); return typeof resposta === "function" ? resposta(r.pedidos.length, rt) : rt.fulfill(resposta); });
  await r.ir(rota);
  return r;
}
const alavanca = (page, nome) => page.locator(".lever-habito", { hasText: nome }).locator("input[type=range]");
const tecla = async (page, nome, n) => { const a = alavanca(page, nome); await a.focus(); for (let i = 0; i < n; i++) await page.keyboard.press("ArrowRight"); };

for (const largura of [1280, 390]) {
  test(`${largura}: 3 categorias da estimativa com o valor da API; arrastar dá 1 POST com o corpo exato; texto da resposta`, async () => {
    const { page, pedidos, erros, ctx } = await abrir({ width: largura });
    await page.locator(".lever-habito").first().waitFor();
    const txt = await page.locator("#w-habitos-alavancas").innerText();
    for (const [n, v] of [["Mercado", "180,00"], ["Transporte", "90,00"], ["Lazer", "60,00"]]) assert.match(txt, new RegExp(`${n}[\\s\\S]*?R\\$ ${v}/mês`));
    const r = alavanca(page, "Mercado");
    const b = await r.boundingBox();
    await page.mouse.move(b.x + 2, b.y + b.height / 2);
    await page.mouse.down();
    await page.mouse.move(b.x + b.width * 0.25, b.y + b.height / 2, { steps: 8 });
    await page.mouse.move(b.x + b.width * 0.5, b.y + b.height / 2, { steps: 8 });
    assert.equal(pedidos.length, 0, "arrastando: só rascunho");
    await page.mouse.up();
    await page.locator(".sim-facts").waitFor();
    assert.equal(pedidos.length, 1);
    assert.deepEqual(pedidos[0], { cortes: [{ categoria: "mercado", percentual: Number(await r.inputValue()) }] });
    const p = pedidos[0].cortes[0].percentual;
    assert.ok(p >= 40 && p <= 60 && p % 10 === 0, `percentual ${p}`);
    const res = await page.locator("#w-habitos-resultado").innerText();
    assert.match(res, /Sobra por mês\s*R\$ 12,34/);
    assert.match(res, /Saldo no dia 90\s*−R\$ 1\.610,00/);
    assert.match(res, /Menor saldo\s*−R\$ 1\.610,00 em 04\/01/);
    assert.match(res, /Sem o corte/);
    assert.match(res, /Com o corte/);
    assert.equal(await page.locator("#w-habitos-resultado svg[role=img]").count(), 1);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true, "sem overflow horizontal");
    assert.deepEqual(erros, []);
    await ctx.close();
  });
}

test("teclado: 3 vezes ArrowRight: o último POST leva 30", async () => {
  const { page, pedidos, ctx } = await abrir();
  await page.locator(".lever-habito").first().waitFor();
  await tecla(page, "Mercado", 3);
  await page.locator(".sim-facts").waitFor();
  await page.waitForFunction(() => document.querySelector(".hab-res")?.getAttribute("aria-busy") === "false");
  assert.deepEqual(pedidos.at(-1), { cortes: [{ categoria: "mercado", percentual: 30 }] });
  await ctx.close();
});

test("resposta velha: o 1º POST demora e o 2º responde na hora; fica o 2º", async () => {
  const resposta = async (n, rt) => {
    if (n === 1) { await new Promise((ok) => setTimeout(ok, 800)); return rt.fulfill({ json: { ...OK, economia_mensal: "11.11" } }).catch(() => {}); }
    return rt.fulfill({ json: { ...OK, economia_mensal: "22.22" } });
  };
  const { page, pedidos, ctx } = await abrir({ resposta });
  await page.locator(".lever-habito").first().waitFor();
  await tecla(page, "Mercado", 1);
  await tecla(page, "Mercado", 1);
  await page.getByText("R$ 22,22").waitFor();
  await page.waitForTimeout(1000);
  const res = await page.locator("#w-habitos-resultado").innerText();
  assert.match(res, /R\$ 22,22/);
  assert.doesNotMatch(res, /11,11/);
  assert.equal(pedidos.length, 2);
  await ctx.close();
});

const SETE = JSON.parse(JSON.stringify(RESPOSTAS.previsao.pro90_com_estimativa));
for (let i = 4; i <= 7; i++) SETE.estimativa_variavel.categorias.push({ chave: `c${i}`, nome: `Cat${i}`, despesa_mensal: "10.00" });
for (const largura of [1280, 390]) {
  for (const [rota, ids, esperado, visiveis] of [["/simulador", ["Mercado", "Lazer"], ["mercado", "lazer"], 7], ["/", ["Cat5", "Mercado"], ["mercado", "c5"], 5]]) {
    test(`${largura}: ${rota}: cortes de várias categorias vão juntos, na ordem da estimativa (${visiveis} alavancas de 7)`, async () => {
      const { page, pedidos, ctx } = await abrir({ width: largura, rota });
      await ctx.route("**/api/v2/previsao*", (rt) => rt.fulfill({ json: SETE }));
      await page.reload();
      const raiz = rota === "/" ? page.locator("[data-widget-id=simulador]") : page;
      await raiz.locator(".lever-habito").first().waitFor();
      assert.equal(await raiz.locator(".lever-habito").count(), visiveis);
      for (const n of ids) {
        await raiz.locator(".lever-habito", { hasText: n }).locator("input[type=range]").focus();
        await page.keyboard.press("ArrowRight");
        await page.waitForFunction(() => document.querySelector(".hab-res")?.getAttribute("aria-busy") === "false");
      }
      // ordem da estimativa (não a do toque) e nenhuma categoria invisível no corpo
      assert.deepEqual(pedidos.at(-1), { cortes: esperado.map((categoria) => ({ categoria, percentual: 10 })) });
      await ctx.close();
    });
  }
}

test("zerar o corte de volta: sem POST novo e sem resultado", async () => {
  const { page, pedidos, ctx } = await abrir();
  await page.locator(".lever-habito").first().waitFor();
  await tecla(page, "Mercado", 1);
  await page.locator(".sim-facts").waitFor();
  await alavanca(page, "Mercado").press("ArrowLeft");
  await page.getByText("Arraste um controle").waitFor();
  assert.equal(pedidos.length, 1);
  assert.equal(await page.locator(".sim-facts").count(), 0);
  await ctx.close();
});

for (const rota of ["/simulador", "/"]) {
  test(`${rota}: sem histórico (estimativa null): texto e nenhum POST`, async () => {
    const { page, pedidos, ctx } = await abrir({ previsao: "pro30", rota });
    await page.getByText("Ainda não há histórico para estimar o gasto do dia a dia.").waitFor();
    assert.equal(await page.locator(".lever-habito").count(), 0);
    assert.equal(pedidos.length, 0);
    await ctx.close();
  });
}

test("Plus: convite ao Pro, sem POST e sem GET de previsão do widget", async () => {
  const r = await abrir({ plano: "plus", previsao: "plus30_com_estimativa" });
  const { page, pedidos, ctx } = r;
  await page.locator(".convite").waitFor();
  assert.equal(await page.locator(".lever-habito").count(), 0);
  assert.equal(pedidos.length, 0);
  assert.equal(r.gets, 0, "a página #/simulador não lê a previsão no Plus");
  await ctx.close();
});

test("422 categoria_fora_da_estimativa: relê a previsão, avisa e não mostra número", async () => {
  const resposta = env(422, "validation_error", [{ loc: ["body", "cortes", 0, "categoria"], msg: "Valor inválido.", type: "categoria_fora_da_estimativa" }]);
  const r = await abrir({ resposta });
  const { page, pedidos, ctx } = r;
  await page.locator(".lever-habito").first().waitFor();
  const antes = r.gets;
  await tecla(page, "Mercado", 1);
  await page.getByText("Seus gastos do dia a dia mudaram. Ajuste de novo.").waitFor();
  await page.waitForTimeout(300);
  assert.equal(r.gets, antes + 1, "um GET /previsao a mais");
  assert.equal(pedidos.length, 1, "sem retentativa do POST");
  assert.equal(await page.locator(".sim-facts").count(), 0);
  await ctx.close();
});

test("falha genérica: mensagem curta e nenhum número; depois do erro um 200 mostra os números", async () => {
  const resposta = (n, rt) => rt.fulfill(n === 1 ? env(500, "internal_error") : { json: OK });
  const { page, ctx } = await abrir({ resposta });
  await page.locator(".lever-habito").first().waitFor();
  await tecla(page, "Mercado", 1);
  await page.getByText("Não deu para calcular agora.").waitFor();
  assert.equal(await page.locator(".sim-facts").count(), 0);
  await tecla(page, "Mercado", 1);
  await page.locator(".sim-facts").waitFor();
  assert.match(await page.locator("#w-habitos-resultado").innerText(), /R\$ 12,34/);
  await ctx.close();
});

for (const largura of [1280, 390]) {
  test(`${largura}: nome de 80 caracteres não estoura a largura (página e bloco do Resumo)`, async () => {
    const longa = JSON.parse(JSON.stringify(RESPOSTAS.previsao.pro90_com_estimativa));
    longa.estimativa_variavel.categorias[0].nome = "Supermercado".repeat(7).slice(0, 80);
    for (const [rota, espera] of [["/simulador", ".lever-habito"], ["/", "[data-widget-id=simulador] .lever-habito"]]) {
      const { page, ctx } = await abrir({ width: largura, rota, espera: "#page-title" });
      await ctx.route("**/api/v2/previsao*", (rt) => rt.fulfill({ json: longa }));
      await page.reload();
      await page.locator(espera).first().waitFor();
      const m = await page.locator(".lever-habito").first().evaluate((el) => ({ sw: el.scrollWidth, cw: el.clientWidth }));
      assert.ok(m.sw <= m.cw, `${rota}: ${m.sw} > ${m.cw}`);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true, `${rota}: overflow da página`);
      await ctx.close();
    }
  });
}

test("protótipo (DEMO): o exemplo continua, com presets", async () => {
  const { page, ctx } = await abrir({ demo: true, rota: "/simulador", espera: "#page-title", resposta: { json: OK } });
  await page.getByRole("button", { name: "Delivery pela metade" }).waitFor();
  assert.equal(await page.locator(".lever-habito").count(), 0);
  await ctx.close();
});
