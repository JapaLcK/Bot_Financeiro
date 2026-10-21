/**
 * "Comparado ao mês anterior" no dashboard v2 (webapp/src/dashboard/widgets/Comparacao.tsx), sobre a
 * /api/v2/comparacao servida pelas fixtures (respostas reais do endpoint, tests/test_api_v2_contrato.py):
 *
 *   · Plus: a `escolha` do servidor, Saiu/Entrou/Resultado com o valor de antes, as categorias
 *     (`nova` sem %, `sem_gasto` = 100% para baixo), "+N outras";
 *   · Essencial: fora do layout mesmo salvo nele, com cadeado no Organizar, e nenhum pedido sai;
 *   · downgrade na sessão: 200 e depois 403 no refetch → o convite, sem número velho na tela;
 *   · `inicio_do_historico` vira a frase (nunca R$ 0,00), `sem_base` diz o mês, e trocar o mês pede o `mes` certo;
 *   · layout a 375, 390, 1280, 1366 e 1440 (rótulo cortado, sobreposição, totais à vista); protótipo sem "Carregando…" eterno.
 *
 * Rodar:  npm run test:frontend   (abre o artefato commitado: mudou webapp/src, rode
 *         `npm --prefix webapp run build`)
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { RESPOSTAS, abrirPainel, exigeArtefatoEmDia } from "./_painel.mjs";
import { COMPARACAO_EXEMPLO } from "../../webapp/src/dashboard/lib/data.js";

const W = "#w-comparacao";
let browser;
before(async () => { exigeArtefatoEmDia(); browser = await chromium.launch(); });
after(() => browser?.close());

// O painel com o bloco no layout salvo do perfil padrão; `pedidos` = os `mes` pedidos à /comparacao.
async function abrir({ layout = ["comparacao"], demo = false, ...opts } = {}) {
  const r = await abrirPainel(browser, { demo, ...opts });
  await r.page.addInitScript((l) => {
    localStorage.setItem("pigbank.dashboard.layout.v1.padrao", JSON.stringify(l));
    localStorage.setItem("pigbank.dashboard.profile.v1", JSON.stringify("padrao")); // o protótipo lê daqui
  }, layout);
  const pedidos = [];
  r.page.on("request", (q) => { const u = new URL(q.url()); if (u.pathname === "/api/v2/comparacao") pedidos.push(u.searchParams.get("mes")); });
  return { ...r, pedidos };
}
const texto = (page) => page.locator(W).innerText();
const linha = (page, nome) => page.locator(`${W} .cmp-linha`).filter({ has: page.locator(".cat-nome", { hasText: new RegExp(`^${nome}$`) }) });

test("Plus: a escolha do servidor, os totais com o antes e as categorias", async () => {
  const { ctx, page, ir, pedidos, erros } = await abrir({ plano: "plus" });
  await ir();
  await page.locator(`${W} .cmp-linha`).first().waitFor();
  const t = await texto(page);
  assert.ok(t.includes(RESPOSTAS.comparacao.aberto.escolha), t);
  assert.match(await linha(page, "Saiu").innerText(), /R\$ 2\.012,00[\s\S]*39,9%[\s\S]*em setembro: R\$ 3\.350,00/);
  assert.match(await linha(page, "Entrou").locator(".cat-delta").innerText(), /20,0%/);
  assert.match(await linha(page, "Resultado").locator(".cat-delta").innerText(), /R\$ 1\.838,00/);
  assert.equal((await linha(page, "Streaming").locator(".cat-delta").innerText()).trim(), "nova");
  assert.equal((await linha(page, "Lazer").locator(".cat-delta").innerText()).trim(), "100,0%");
  assert.equal(await linha(page, "Lazer").locator(".cat-delta i").getAttribute("class"), "ph ph-arrow-down");
  assert.ok(!t.includes("outras"));
  assert.deepEqual(pedidos, ["2026-10"]);
  assert.deepEqual(erros, []);
  await ctx.close();
});

// Decisão do dono PB-1: no mês aberto (o mês inteiro até agora × o anterior inteiro) a queda ainda não se sabe
// e fica neutra; a alta já é fato e mantém a cor. No mês fechado, as cores de sempre.
test("cores: no mês aberto a queda é neutra e a alta colorida; no fechado, as de sempre", async () => {
  // `receita`: troca só a linha Entrou da resposta real (nenhuma fixture tem receita caindo)
  const tons = async (comparacao, nomes, receita) => {
    const { ctx, page, ir } = await abrir({ plano: "plus", comparacao });
    const real = RESPOSTAS.comparacao[comparacao];
    if (receita) await ctx.route("**/api/v2/comparacao*", (r) => r.fulfill({ json: { ...real, receita: { ...real.receita, ...receita } } }));
    await ir();
    await page.locator(`${W} .cmp-linha`).first().waitFor();
    const r = {};
    for (const nome of nomes) r[nome] = (await linha(page, nome).locator(".cat-delta").getAttribute("class")).replace("cat-delta num ", "");
    await ctx.close();
    return r;
  };
  // aberto: Saiu, Lazer e Alimentação caíram; Entrou subiu (bom); o Resultado é sempre neutro no mês aberto
  // (decisão de 2026-10-20), aqui subindo; em `muitas` o gasto subiu (ruim)
  assert.deepEqual(await tons("aberto", ["Saiu", "Lazer", "Alimentação", "Entrou", "Resultado"]),
    { Saiu: "faint", Lazer: "faint", "Alimentação": "faint", Entrou: "gain", Resultado: "faint" });
  assert.deepEqual(await tons("muitas", ["Saiu", "Resultado"]), { Saiu: "warn", Resultado: "faint" });
  // fechado: o gasto subiu (âmbar) e o Transporte caiu (verde, como sempre)
  assert.deepEqual(await tons("fechado", ["Saiu", "Transporte", "Entrou", "Resultado"]),
    { Saiu: "warn", Transporte: "gain", Entrou: "gain", Resultado: "warn" });
  // receita caindo: cinza no mês aberto, âmbar no fechado
  assert.deepEqual(await tons("aberto", ["Entrou"], { total_atual: "2000.00", total_delta: "-500.00", pct: "-20.0" }), { Entrou: "faint" });
  assert.deepEqual(await tons("fechado", ["Entrou"], { total_atual: "800.00", total_delta: "-200.00", pct: "-20.0" }), { Entrou: "warn" });
});

test("+N outras e sem base dizem o que falta", async () => {
  for (const [fixture, frase] of [["muitas", "+3 outras"], ["sem_base", "Sem lançamentos em setembro para comparar."]]) {
    const { ctx, page, ir } = await abrir({ plano: "pro", comparacao: fixture });
    await ir();
    await page.locator(`${W} .cmp-linha`).first().waitFor();
    assert.ok((await texto(page)).includes(frase), fixture);
    await ctx.close();
  }
});

test("Essencial: fora do layout salvo, com cadeado no Organizar, e nenhum pedido sai", async () => {
  const { ctx, page, ir, pedidos } = await abrir({ plano: "essencial", layout: ["comparacao", "resumo"] });
  await ir();
  await page.locator("#w-resumo").waitFor();
  assert.equal(await page.locator(W).count(), 0);
  await page.getByRole("button", { name: "Organizar" }).click();
  await page.locator("#board-add").click();
  const item = page.locator("#board-catalog .catalog-item", { hasText: "Comparado ao mês anterior" });
  assert.equal(await item.getAttribute("aria-disabled"), "true");
  assert.match(await item.innerText(), /No Plus/);
  await item.dispatchEvent("click");                               // aria-disabled: o Playwright não clica
  await page.waitForTimeout(300);
  assert.equal(await page.locator(W).count(), 0);
  assert.deepEqual(pedidos, []);
  await ctx.close();
});

test("downgrade na sessão: o refetch com 403 troca o número pelo convite", async () => {
  const { ctx, page, ir } = await abrir({ plano: "plus" });
  await ir();
  await page.locator(`${W} .cmp-linha`).first().waitFor();
  assert.ok((await texto(page)).includes("R$ 2.012,00"));
  const e = RESPOSTAS.erros["403_pro_required"];
  await ctx.route("**/api/v2/comparacao*", (r) => r.fulfill({ status: e.status, json: e.body }));
  await page.evaluate(() => window.dispatchEvent(new Event("visibilitychange"))); // o focusManager do TanStack ouve na window
  await page.locator(`${W} .convite`).waitFor();
  const t = await texto(page);
  assert.ok(t.includes("Comparar períodos é do Plus e do Pro."), t);
  assert.ok(!/R\$/.test(t), t);                                   // nenhum número antigo
  await ctx.close();
});

test("início do histórico vira a frase, e trocar o mês pede o mês certo", async () => {
  const { ctx, page, ir, pedidos } = await abrir({ plano: "plus", comparacao: "historico" });
  await ir();
  await page.locator(`${W} .empty`).waitFor();
  const t = await texto(page);
  assert.ok(t.includes("O mês anterior está fora do histórico do seu plano."), t);
  assert.ok(!t.includes("R$ 0,00"), t);
  await page.getByRole("button", { name: "Mês anterior", exact: true }).click();
  await page.waitForFunction(() => document.querySelector(".month-title").textContent === "Setembro 2026");
  await page.waitForFunction(() => document.querySelector("#w-comparacao .empty"));
  assert.deepEqual([...new Set(pedidos)], ["2026-10", "2026-09"]);
  await ctx.close();
});

// 1280 e 1366: a grade de 4 colunas deixa a célula estreita. O corte mora no `.cat-nome`
// (overflow: hidden), não na linha; os totais têm de caber sem rolar e nada pode se sobrepor. Uma
// categoria da resposta real ganha um nome de 60 letras sem espaço: com os nomes curtos das fixtures,
// tirar a quebra do nome não reprovava nada.
const LONGO = "Supermercado".repeat(5);
for (const width of [375, 390, 1280, 1366, 1440]) {
  test(`layout a ${width}px: nenhum rótulo cortado, nada sobreposto, totais à vista`, async () => {
    for (const comparacao of ["aberto", "muitas"]) {
      const { ctx, page, ir } = await abrir({ plano: "plus", width, comparacao, layout: ["comparacao", "resumo", "categorias"] });
      const real = RESPOSTAS.comparacao[comparacao];
      await ctx.route("**/api/v2/comparacao*", (r) => r.fulfill({ json: { ...real, categorias: real.categorias.map((c, i) => (i === 1 ? { ...c, categoria: LONGO } : c)) } }));
      await ir();
      await page.locator(`${W} .cat-nome`, { hasText: LONGO }).waitFor();
      await page.locator(`${W} .cmp-linha`).first().waitFor();
      const m = await page.evaluate((w) => {
        const el = document.querySelector(w), corpo = el.querySelector(".cmp-corpo");
        const totais = [...el.querySelectorAll(".cmp-totais .cmp-linha")].at(-1).getBoundingClientRect();
        return {
          doc: document.documentElement.scrollWidth - innerWidth,
          rolaX: corpo.scrollWidth - corpo.clientWidth,
          cortados: [...el.querySelectorAll(".cat-nome, .cmp-linha > *")].filter((e) => e.scrollWidth > e.clientWidth).map((e) => e.textContent),
          // a caixa que encolheu e deixou o conteúdo vazar por cima da vizinha também conta
          sobrepostos: [...corpo.children].filter((e, i) => e.scrollHeight > e.clientHeight + 1
            || (i && e.getBoundingClientRect().top < e.previousElementSibling.getBoundingClientRect().bottom - 0.5)).length,
          totaisVisiveis: totais.bottom <= corpo.getBoundingClientRect().bottom + 0.5,
          alcanca: corpo.scrollHeight <= corpo.clientHeight || getComputedStyle(corpo).overflowY === "auto",
        };
      }, W);
      assert.deepEqual(m, { doc: 0, rolaX: 0, cortados: [], sobrepostos: 0, totaisVisiveis: true, alcanca: true }, `${comparacao}: ${JSON.stringify(m)}`);
      await ctx.close();
    }
  });
}

// O corpo rola dentro da célula fixa do Resumo e não tem nada focável dentro: tem de estar no Tab por si
// (tabindex), com nome. O Chromium põe rolagem no Tab sozinho e o Safari não: por isso o atributo é
// medido, além do Tab (que no Chromium passaria mesmo sem ele).
for (const width of [1280, 1440]) {
  test(`${width}px: o corpo que rola entra no Tab, com nome, e a seta rola`, async () => {
    const { ctx, page, ir } = await abrir({ plano: "plus", width, comparacao: "muitas", layout: ["comparacao", "resumo"] });
    await ir();
    const corpo = page.locator(`${W} .cmp-corpo`);
    await corpo.waitFor();
    const m = await corpo.evaluate((c) => ({ rola: c.scrollHeight > c.clientHeight, tabindex: c.getAttribute("tabindex"), nome: c.getAttribute("aria-label") }));
    assert.deepEqual(m, { rola: true, tabindex: "0", nome: "Comparação do mês" });
    let tabs = 0;
    while (tabs++ < 80 && !(await corpo.evaluate((c) => c === document.activeElement))) await page.keyboard.press("Tab");
    assert.ok(tabs <= 80, "o Tab não chegou ao corpo");
    await page.keyboard.press("PageDown");
    await page.waitForFunction((w) => document.querySelector(`${w} .cmp-corpo`).scrollTop > 0, W);
    await ctx.close();
  });
}

// O único caminho em que a invalidação muda algo: fora do Resumo a consulta não tem observador e,
// com `gcTime: 0`, sai do cache (volta ao Resumo = pedido novo de qualquer jeito). Mas quem volta
// ao Resumo com o POST ainda no ar lê o número de ANTES da gravação; só a invalidação no fim do
// POST (parts/useLancamentoMutation.ts) pede de novo.
test("lançamento que termina com o bloco na tela pede a comparação de novo", async () => {
  const { ctx, page, ir, pedidos } = await abrir({ plano: "plus" });
  await ir();
  await page.locator(`${W} .cmp-linha`).first().waitFor();
  let libera;
  const segura = new Promise((r) => { libera = r; });
  await ctx.route("**/api/v2/lancamentos/carteira", async (r) => { await segura; return r.fulfill({ json: { id: "l999" } }); });
  await page.evaluate(() => { location.hash = "#/lancamentos"; });
  await page.getByRole("button", { name: "Lançar na Carteira", exact: true }).click();
  await page.locator('.lanc-form input[name="descricao"]').fill("Teste carteira");
  await page.locator('.lanc-form input[name="valor"]').fill("0,01");
  await page.locator(".lanc-form").getByRole("button", { name: "Salvar", exact: true }).click();
  await page.evaluate(() => { location.hash = "#/"; });             // volta com o POST no ar
  await page.locator(`${W} .cmp-linha`).first().waitFor();
  await page.waitForTimeout(300);
  const antes = pedidos.length;
  libera();
  await page.waitForTimeout(500);
  assert.ok(pedidos.length > antes, JSON.stringify({ antes, pedidos }));
  assert.equal(pedidos.at(-1), "2026-10");
  await ctx.close();
});

test("o exemplo do protótipo é a resposta real da fixture (§0.7)", () => {
  assert.deepEqual(COMPARACAO_EXEMPLO, RESPOSTAS.comparacao.aberto);
});

test("protótipo: o bloco mostra o exemplo, sem Carregando eterno", async () => {
  const { ctx, page, ir, pedidos } = await abrir({ demo: true });
  await ir();
  await page.locator(`${W} .cmp-linha`).first().waitFor();
  const t = await texto(page);
  assert.ok(t.includes(COMPARACAO_EXEMPLO.escolha) && t.includes("R$ 2.012,00") && !t.includes("Carregando"), t);
  assert.deepEqual(pedidos, []);
  await ctx.close();
});
