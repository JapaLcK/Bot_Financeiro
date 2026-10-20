/**
 * /painel#/orcamento, a visão da página: o toggle Gasto/Orçado troca o centro e a legenda do donut, o número do
 * centro nunca vaza do anel (valores enormes, 1280 e 375), 375px sem rolagem lateral e na ordem Renda, Visão, Potes,
 * e os botões do cabeçalho (Gasto fixo, Editar percentuais) abrem os formulários e ficam onde o desenho manda.
 * O centro é <text> do SVG (sem `cqw`, que o Safari 14 a 15 não tem). Capturas: PB_SHOT=<prefixo> grava `<prefixo>-orcamento-<largura>[-grande].png`.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { RESPOSTAS, abrirPainel, exigeArtefatoEmDia } from "./_painel.mjs";

let browser;
before(async () => { exigeArtefatoEmDia(); browser = await chromium.launch(); });
after(() => browser?.close());

const LINHA = ".orc-tabela tbody tr";
async function abrir({ width = 1280, dados } = {}) {
  const p = await abrirPainel(browser, { espera: null, width });
  if (dados) await p.ctx.route("**/api/v2/orcamento-domestico*", (r) => r.request().method() === "GET" ? r.fulfill({ json: dados }) : r.fallback());
  await p.ir("/orcamento");
  await p.page.locator(LINHA).first().waitFor({ state: "attached" });
  return p;
}
const comValores = (renda, gasto) => {
  const d = JSON.parse(JSON.stringify(RESPOSTAS.orcamento_domestico.padrao));
  d.renda.valor = renda;
  d.total.gasto = gasto;
  d.potes.forEach((p, i) => { p.gasto = i === 0 ? gasto : "0.00"; p.teto = renda; });
  return d;
};
const semRolagem = (page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth);
const GRANDES = [["100000.00", "100000.00"], ["1234567.89", "1234567.89"], ["987654321.00", "123456789.00"]];

test("toggle Gasto/Orçado troca o centro, a legenda e as fatias", async () => {
  const { ctx, page } = await abrir();
  const lido = async () => ({
    centro: await page.locator(".orc-donut-centro").evaluate((e) => [...e.children].map((c) => c.textContent).join(" ")),
    leg: await page.locator(".orc-leg li").first().innerText(),
    fatias: await page.locator(".orc-donut circle.orc-fatia").count(),
    marcado: await page.locator('.seg [aria-checked="true"]').innerText(),
  });
  const gasto = await lido();
  await page.getByRole("radio", { name: "Orçado" }).click();
  const orcado = await lido();
  await ctx.close();
  assert.equal(gasto.marcado, "Gasto");
  assert.match(gasto.centro, /R\$ 2\.985,00\s+gasto no mês\s+60% da renda/);
  assert.match(gasto.leg, /Custos fixos\s+67%\s+R\$ 2\.000,00/);
  assert.equal(orcado.marcado, "Orçado");
  assert.match(orcado.centro, /R\$ 5\.000,00\s+renda do mês\s+100% distribuída/);
  assert.match(orcado.leg, /Custos fixos\s+55%\s+R\$ 2\.750,00/);
  assert.deepEqual([gasto.fatias, orcado.fatias], [5, 6]);
});

for (const width of [1280, 375]) {
  test(`${width}px: o texto do centro do donut cabe no círculo interno, com valores enormes, nos dois modos`, async () => {
    for (const [renda, gasto] of GRANDES) {
      const { ctx, page } = await abrir({ width, dados: comValores(renda, gasto) });
      for (const modo of ["Gasto", "Orçado"]) {
        await page.getByRole("radio", { name: modo }).click();
        const m = await page.evaluate(() => {
          const d = document.querySelector(".orc-donut").getBoundingClientRect();
          const cx = d.x + d.width / 2, cy = d.y + d.height / 2, raio = d.width * 0.37; // anel de r 84 e traço 20 num viewBox de 200
          return [...document.querySelectorAll(".orc-donut-centro > *")].map((el) => { // <text> do SVG: a caixa já escala com o viewBox, sem container query
            const b = el.getBoundingClientRect();
            const pior = Math.max(...[[b.left, b.top], [b.right, b.top], [b.left, b.bottom], [b.right, b.bottom]].map(([x, y]) => Math.hypot(x - cx, y - cy)));
            return { txt: el.textContent, pior, raio, largura: b.width, tag: el.tagName, cq: getComputedStyle(el.closest(".orc-donut")).containerType };
          });
        });
        for (const l of m) assert.deepEqual([l.tag, l.cq], ["text", "normal"], "o centro tem de ser <text> do SVG, não HTML sobre ele com container query");
        for (const l of m) assert.ok(l.pior <= l.raio, `${width} ${modo} ${renda}/${gasto}: "${l.txt}" a ${l.pior.toFixed(1)}px do centro, raio interno ${l.raio.toFixed(1)}px`);
        assert.ok(m[0].txt.startsWith("R$ "), m[0].txt);
        if (process.env.PB_SHOT && modo === "Gasto" && gasto === "100000.00") await page.screenshot({ path: `${process.env.PB_SHOT}-orcamento-${width}-grande.png` });
      }
      assert.ok(await semRolagem(page), `${width} ${renda}`);
      await ctx.close();
    }
  });
}

test("375px: sem rolagem lateral, na ordem Renda, Visão, Potes, legenda só com %, cards no lugar da tabela", async () => {
  const { ctx, page } = await abrir({ width: 375 });
  const y = (id) => page.locator(`#w-orcamento-${id}`).evaluate((e) => e.getBoundingClientRect().top);
  const ordem = [await y("renda"), await y("visao"), await y("potes")];
  const valorNaLegenda = await page.locator(".orc-leg-valor").first().isVisible();
  const tabela = await page.locator(".orc-tabela").isVisible();
  const cards = await page.locator(".orc-cards li").count();
  const nota = await page.locator(".orc-nota").isVisible();
  const ok = await semRolagem(page);
  if (process.env.PB_SHOT) await page.screenshot({ path: `${process.env.PB_SHOT}-orcamento-375.png`, fullPage: true });
  await ctx.close();
  assert.ok(ordem[0] < ordem[1] && ordem[1] < ordem[2], JSON.stringify(ordem));
  assert.deepEqual([valorNaLegenda, tabela, cards, nota], [false, false, 6, false]);
  assert.ok(ok);
});

test("1280px: Visão (7) e Renda (5) lado a lado, Potes embaixo, sem rolagem lateral", async () => {
  const { ctx, page } = await abrir();
  const caixa = (id) => page.locator(`#w-orcamento-${id}`).boundingBox();
  const [v, r, t] = [await caixa("visao"), await caixa("renda"), await caixa("potes")];
  const ok = await semRolagem(page);
  if (process.env.PB_SHOT) await page.screenshot({ path: `${process.env.PB_SHOT}-orcamento-1280.png`, fullPage: true });
  await ctx.close();
  assert.ok(r.x > v.x + v.width - 1 && Math.abs(v.y - r.y) < 2, JSON.stringify({ v, r }));
  assert.ok(t.y > v.y + v.height && t.width > v.width + r.width, JSON.stringify({ t }));
  assert.ok(ok);
});

test("botões do cabeçalho: à direita do título no desktop, em linha própria no celular, e abrem os formulários", async () => {
  for (const [width, desktop] of [[1280, true], [375, false]]) {
    const { ctx, page } = await abrir({ width });
    const h1 = await page.locator("#page-title").boundingBox();
    const lede = await page.locator(".page-lede").boundingBox();
    const acoes = await page.locator(".page-acoes").boundingBox();
    const nomes = await page.locator(".page-acoes button").allInnerTexts();
    await ctx.close();
    assert.deepEqual(nomes.map((n) => n.trim()), ["Gasto fixo", "Editar percentuais"]);
    if (desktop) assert.ok(acoes.x > h1.x + 400 && acoes.y < lede.y + lede.height, JSON.stringify({ h1, lede, acoes }));
    else assert.ok(acoes.y >= lede.y + lede.height && acoes.width > 330, JSON.stringify({ lede, acoes }));
  }
  const { ctx, page } = await abrir();
  await page.getByRole("button", { name: "Gasto fixo", exact: true }).click();
  await page.locator("dialog.orc-form").getByRole("heading", { name: "Novo gasto fixo" }).waitFor();
  await page.keyboard.press("Escape");
  await page.locator("dialog.orc-form").waitFor({ state: "detached" });
  await page.getByRole("button", { name: "Editar percentuais" }).click();
  await page.locator("dialog.orc-form").getByRole("heading", { name: "Editar percentuais" }).waitFor();
  await ctx.close();
});

test("chave de pote desconhecida cai no círculo e na cor do servidor; as conhecidas têm ícone e cor próprios", async () => {
  const d = JSON.parse(JSON.stringify(RESPOSTAS.orcamento_domestico.padrao));
  d.potes[5].chave = "novo_pote"; d.potes[5].cor = "#123456";
  const { ctx, page } = await abrir({ dados: d });
  const icones = await page.locator(".orc-tabela tbody .orc-ico i").evaluateAll((l) => l.map((e) => e.className.replace("ph ph-", "")));
  const cores = await page.locator(".orc-tabela tbody .orc-ico").evaluateAll((l) => l.map((e) => e.style.color));
  await ctx.close();
  assert.deepEqual(icones, ["house", "shopping-cart", "target", "confetti", "tree", "circle"]);
  assert.equal(cores[0], "rgb(255, 45, 142)");
  assert.equal(cores[5], "rgb(18, 52, 86)");
});

test("valores de 7 dígitos: a página não rola para o lado em 761, 768, 800, 860 e 1100px (a tabela rola no painel)", async () => {
  const d = JSON.parse(JSON.stringify(RESPOSTAS.orcamento_domestico.padrao));
  d.renda.valor = "9999999.99";
  d.potes.forEach((p) => { p.teto = "9999999.99"; p.gasto = "9999999.99"; p.restante = "9999999.99"; });
  for (const width of [761, 768, 800, 860, 1100]) {
    const { ctx, page } = await abrir({ width, dados: d });
    const m = await page.evaluate(() => {
      const r = document.querySelector(".orc-rola");
      return { pagina: document.documentElement.scrollWidth <= document.documentElement.clientWidth, painel: r.scrollWidth, caixa: r.clientWidth };
    });
    await ctx.close();
    assert.ok(m.pagina, `${width}: a página rola para o lado (${JSON.stringify(m)})`);
  }
});

test("celular: o card mostra Guardado/Gasto, \"passou\" no estourado e \"guardou R$ a mais\" no guardado acima do teto", async () => {
  const d = JSON.parse(JSON.stringify(RESPOSTAS.orcamento_domestico.padrao)); // Conforto passou R$ 60,00
  d.potes[2].gasto = "600.00"; d.potes[2].restante = "-100.00"; // Metas (guardado) acima do teto
  const { ctx, page } = await abrir({ width: 375, dados: d });
  const card = (nome) => page.locator(".orc-cards li").filter({ hasText: nome });
  const t = { conforto: await card("Conforto").innerText(), metas: await card("Metas").innerText(), fixos: await card("Custos fixos").innerText() };
  const warnMetas = await card("Metas").locator(".warn").count();
  await ctx.close();
  assert.match(t.conforto, /Gasto[\s\S]*passou R\$ 60,00/);
  assert.match(t.metas, /Guardado[\s\S]*guardou R\$ 100,00 a mais/);
  assert.doesNotMatch(t.metas, /passou|Gasto/);
  assert.equal(warnMetas, 0);
  assert.match(t.fixos, /Gasto/);
  assert.doesNotMatch(t.fixos, /passou|guardou|Guardado/);
});

test("clique do mouse na linha, fora do botão do nome: ao fechar o modal o foco volta ao botão da linha", async () => {
  const { ctx, page } = await abrir();
  await page.locator(LINHA).filter({ hasText: "Prazeres" }).locator("td").nth(2).click();
  await page.locator("dialog.orc-modal").waitFor();
  await page.keyboard.press("Escape");
  await page.locator("dialog.orc-modal").waitFor({ state: "detached" });
  const foco = await page.evaluate(() => document.activeElement?.textContent);
  await ctx.close();
  assert.equal(foco, "Prazeres");
});
