// Extrato redesenhado: chips de atalho, painel de filtros, fichas, cabeçalho do dia com total, linha limpa dentro do card.
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { abrirFiltros, abrirPainel, exigeArtefatoEmDia, RESPOSTAS } from "./_painel.mjs";
let browser;
before(async () => { exigeArtefatoEmDia(); browser = await chromium.launch(); });
after(() => browser?.close());
const abrir = (opts = {}) => abrirPainel(browser, { espera: '[data-id="l901"]', ...opts });
const fixture = RESPOSTAS.lancamentos.estados;
const fichas = (page) => page.locator(".ledger-chips .chip").allTextContents();
const pressionado = (page) => page.locator(".lanc-atalhos .chip[aria-pressed=true]").allTextContents();
const espiar = (page) => { const urls = []; page.on("request", (r) => { const u = new URL(r.url()); if (u.pathname === "/api/v2/lancamentos" && r.method() === "GET") urls.push(u.searchParams); }); return urls; };

test("chips: Saídas só tipo, Cartão só origem, combinação pela folha sem chip e com duas fichas, Tudo mantém o resto", async () => {
  const { ctx, page, ir, erros } = await abrir();
  const urls = espiar(page);
  await ir("/lancamentos");
  assert.deepEqual(await pressionado(page), ["Tudo"]);
  await page.getByRole("button", { name: "Saídas", exact: true }).click();
  await page.waitForFunction(() => document.querySelector(".lanc-atalhos .chip[aria-pressed=true]")?.textContent === "Saídas");
  await page.waitForTimeout(200);
  assert.equal(urls.at(-1).get("tipo"), "saida"); assert.equal(urls.at(-1).has("origem"), false);
  assert.deepEqual(await fichas(page), []); // o chip representa o par: sem ficha
  await page.getByRole("button", { name: "Cartão", exact: true }).click();
  await page.waitForTimeout(200);
  assert.equal(urls.at(-1).get("origem"), "cartao"); assert.equal(urls.at(-1).has("tipo"), false);
  assert.deepEqual(await pressionado(page), ["Cartão"]);
  // saída + banco pelo painel: nenhum chip e duas fichas (com o painel fechado)
  await abrirFiltros(page);
  await page.getByRole("combobox", { name: /^Origem/ }).selectOption("banco");
  await page.getByRole("combobox", { name: /^Tipo/ }).selectOption("saida");
  await page.getByRole("combobox", { name: /^Categoria/ }).selectOption("alimentacao");
  await page.getByRole("button", { name: "Filtros", exact: true }).click();
  assert.deepEqual(await pressionado(page), []);
  assert.deepEqual((await fichas(page)).map((t) => t.replace("remover filtro", "")), ["alimentação", "Saídas", "Banco"]);
  assert.equal(await page.getByRole("button", { name: "Filtros", exact: true }).getAttribute("data-ativo"), "true");
  // Tudo troca SÓ tipo e origem: a categoria fica
  await page.getByRole("button", { name: "Tudo", exact: true }).click();
  await page.waitForTimeout(200);
  assert.equal(urls.at(-1).get("categoria"), "alimentacao"); assert.equal(urls.at(-1).has("tipo"), false); assert.equal(urls.at(-1).has("origem"), false);
  assert.deepEqual((await fichas(page)).map((t) => t.replace("remover filtro", "")), ["alimentação"]);
  await page.locator(".ledger-chips .chip").click(); // remover a ficha limpa a categoria
  await page.waitForTimeout(200);
  assert.equal(urls.at(-1).has("categoria"), false);
  assert.equal(await page.getByRole("button", { name: "Filtros", exact: true }).getAttribute("data-ativo"), "false");
  // ficha de Conta: aparece com o painel fechado e some ao tirar
  await abrirFiltros(page);
  await page.getByRole("combobox", { name: /^Conta/ }).selectOption("7");
  await page.getByRole("button", { name: "Filtros", exact: true }).click();
  assert.equal((await fichas(page)).length, 1);
  assert.match((await fichas(page))[0], /remover filtro$/);
  await page.locator(".ledger-chips .chip").click();
  await page.waitForTimeout(200);
  assert.equal(urls.at(-1).has("conta"), false); assert.deepEqual(await fichas(page), []);
  assert.deepEqual(erros, []);
  await ctx.close();
});

test("botão de filtros: aria-expanded/aria-controls e o painel dos 4 selects nasce fechado", async () => {
  const { ctx, page, ir } = await abrir();
  await ir("/lancamentos");
  const botao = page.getByRole("button", { name: "Filtros", exact: true });
  assert.equal(await botao.getAttribute("aria-expanded"), "false");
  const id = await botao.getAttribute("aria-controls");
  assert.equal(await page.locator(`#${id}`).isHidden(), true);
  await botao.click();
  assert.equal(await botao.getAttribute("aria-expanded"), "true");
  assert.equal(await page.locator(`#${id}`).isVisible(), true);
  assert.equal(await page.locator(`#${id} select`).count(), 4);
  await ctx.close();
});

test("(d) Hoje pelo relógio de São Paulo, não pelo do aparelho (Tóquio já é dia 20)", async () => {
  const { ctx, page, ir } = await abrir({ tz: "Asia/Tokyo", agora: "2026-10-19T16:00:00Z" });
  await ctx.route("**/api/v2/lancamentos?*", (r) => r.fulfill({ json: { ...fixture, itens: [{ ...fixture.itens[0], data: "2026-10-19" }, { ...fixture.itens[1], data: "2026-10-18" }], proximo: null } }));
  await ir("/lancamentos");
  assert.deepEqual(await page.locator(".lanc-dia h3 > span:first-child").allTextContents(), ["Hoje · 19/10", "Ontem · 18/10"]);
  await ctx.close();
});

test("(d2) Hoje/Ontem nunca levam ano (virada de ano); outro dia do ano passado leva /aa", async () => {
  const { ctx, page, ir } = await abrir({ agora: "2027-01-01T15:00:00Z" });
  await ctx.route("**/api/v2/lancamentos?*", (r) => r.fulfill({ json: { ...fixture, itens: [{ ...fixture.itens[0], data: "2027-01-01" }, { ...fixture.itens[1], data: "2026-12-31" }, { ...fixture.itens[2], data: "2026-12-20" }, { ...fixture.itens[3], data: "2026-02-30" }], proximo: null } }));
  await ir("/lancamentos");
  assert.deepEqual(await page.locator(".lanc-dia h3 > span:first-child").allTextContents(), ["Hoje · 01/01", "Ontem · 31/12", "Dom · 20/12/26", "seg, 2 de março"]); // data impossível (30/02): cai no formatador longo, nunca "30/02" como o app
  await ctx.close();
});

test("(e) total do dia: exclui outra moeda e interno, soma saída pelo módulo (valor negativo não vira entrada)", async () => {
  const { ctx, page, ir } = await abrir();
  const base = { ...fixture.itens[0], data: "2026-10-02", interno: false, guardado: false, moeda: "BRL" };
  const itens = [{ ...base, id: "l901", tipo: "entrada", valor: "50.00" }, { ...base, id: "t2", tipo: "saida", valor: "-10.00", categoria: "pagamento_fatura" }, { ...base, id: "t3", tipo: "saida", valor: "999.00", moeda: "USD" }, { ...base, id: "t4", tipo: "saida", valor: "500.00", interno: true }, { ...base, id: "t5", data: "2026-10-01", tipo: "saida", valor: "999.00", moeda: "USD" }];
  await ctx.route("**/api/v2/lancamentos?*", (r) => r.fulfill({ json: { ...fixture, itens, proximo: null } }));
  await ir("/lancamentos");
  assert.deepEqual((await page.locator(".lanc-dia h3 .num").allTextContents()).map((t) => t.replace(/\s/g, " ")), ["+R$ 40,00"]);
  assert.equal(await page.locator(".lanc-dia").count(), 2);
  assert.equal(await page.locator(".lanc-dia").nth(1).locator("h3 .num").count(), 0); // dia em que nada conta: sem total (nunca "R$ 0,00")
  assert.match(await page.locator('[data-id="t2"] .faint').textContent(), /Pagamento_fatura$/); // categoria fora do catálogo: a chave capitalizada, como no app
  await ctx.close();
});

test("(f) chip de atalho selecionado mantém o rosa com o mouse em cima", async () => {
  const { ctx, page, ir } = await abrir();
  await ir("/lancamentos");
  const cor = () => page.locator(".lanc-atalhos .chip[aria-pressed=true]").evaluate((e) => getComputedStyle(e).color);
  const antes = await cor();
  await page.locator(".lanc-atalhos .chip[aria-pressed=true]").hover();
  await page.waitForTimeout(400); // passa da transição de cor (--t-fast)
  assert.equal(await cor(), antes);
  await ctx.close();
});

test("(g) o leitor de tela ouve o subtítulo (instituição · categoria): o aria-label do botão troca o nome, o aria-describedby devolve o contexto", async () => {
  const { ctx, page, ir, erros } = await abrir();
  await ir("/lancamentos");
  await page.locator('[data-id="l901"] .ph-shopping-cart').waitFor();
  const d = await page.evaluate(() => { const b = document.querySelector('[data-id="l901"] button.lanc-abrir'); const alvo = document.getElementById(b.getAttribute("aria-describedby") ?? ""); return { desc: alvo?.textContent ?? null, dentro: b.contains(alvo), sub: b.querySelector(".faint").textContent }; });
  assert.ok(d.sub.includes(" · "), d.sub); assert.equal(d.desc, d.sub); assert.equal(d.dentro, true);
  assert.deepEqual(erros, []);
  await ctx.close();
});

for (const width of [1440, 390]) test(`visual ${width}: linha dentro do card, ícones certos, alvos de 44px`, async () => {
  const { ctx, page, ir, erros } = await abrir({ width });
  await ir("/lancamentos");
  await page.locator('[data-id="l901"] .ph-shopping-cart').waitFor(); // o ícone vem do catálogo (/categorias), pedido à parte da lista: sem esperar, cai em ph-tag
  const m = await page.evaluate(() => {
    const r = (e) => e.getBoundingClientRect();
    const glifo = (e) => { const c = getComputedStyle(e, "::before").content; return c && c !== "none" && c !== "normal" && c !== '""'; };
    const fora = [...document.querySelectorAll(".lanc-dia")].flatMap((d) => { const ul = r(d.querySelector("ul")); return [...d.querySelectorAll(".lanc-linha, .lanc-valor, .lanc-ico")].filter((e) => r(e).left < ul.left - 0.5 || r(e).right > ul.right + 0.5).map((e) => e.className); });
    const altura = [...document.querySelectorAll(".lanc-busca .field, .lanc-busca-linha .icon-btn, .lanc-atalhos .chip")].map((e) => Math.round(r(e).height));
    const ico = (id) => document.querySelector(`[data-id="${id}"] .lanc-ico i`);
    return {
      fora, altura, lateral: document.documentElement.scrollWidth - innerWidth,
      entrada: ico("l898").className, entradaCor: getComputedStyle(ico("l898").parentElement).color, valorEntrada: document.querySelector('[data-id="l898"] .lanc-valor').className,
      saida: ico("l901").className, saidaCor: getComputedStyle(ico("l901").parentElement).color,
      glifos: ["l898", "l901"].map((id) => glifo(ico(id))).concat([glifo(document.querySelector(".lanc-busca-linha .icon-btn i")), glifo(document.querySelector(".lanc-busca .ph"))]),
      card: getComputedStyle(document.querySelector(".lanc-dia ul")).borderTopWidth,
    };
  });
  assert.deepEqual(m.fora, []); assert.equal(m.lateral <= 0, true, String(m.lateral));
  assert.ok(m.altura.length === 6 && m.altura.every((h) => h >= 44), String(m.altura));
  assert.match(m.entrada, /ph-arrow-down-left/); assert.equal(m.entradaCor, "rgb(61, 220, 151)"); assert.match(m.valorEntrada, /gain/);
  assert.match(m.saida, /ph-shopping-cart/); assert.notEqual(m.saidaCor, "rgb(61, 220, 151)");
  assert.deepEqual(m.glifos, [true, true, true, true]);
  assert.equal(m.card, "1px");
  assert.deepEqual(erros, []);
  await ctx.close();
});
