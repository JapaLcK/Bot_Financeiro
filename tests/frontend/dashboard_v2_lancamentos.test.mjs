import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { abrirPainel, exigeArtefatoEmDia, RESPOSTAS } from "./_painel.mjs";
let browser;
before(async () => { exigeArtefatoEmDia(); browser = await chromium.launch(); });
after(() => browser?.close());
const abrir = (opts = {}) => abrirPainel(browser, { espera: '[data-id="l901"]', ...opts });
const fixture = RESPOSTAS.lancamentos.estados;

test("mês real, quatro origens, nota, fatura de outro mês, selos e cursor opaco sem totais parciais", async () => {
  const { ctx, page, ir, erros } = await abrir();
  const requests = [];
  page.on("request", (r) => { if (r.url().includes("/api/v2/lancamentos?")) requests.push(new URL(r.url())); });
  await ir("/lancamentos");
  assert.equal(requests[0].searchParams.get("mes"), "2026-10");
  const text = await page.locator(".lancamentos").textContent();
  for (const t of ["Carteira Piggy", "Banco", "Cartão", "Registro antigo", "Nota: gastei 42,90", "Parcela 1 de 3", "Fatura de Outubro 2026", "Movimento interno · fora de Entrou/Saiu", "transação pendente", "20 set"]) assert.ok(text.includes(t), t);
  assert.doesNotMatch(text, /total do dia|\d+ de \d+ resultados|WhatsApp/i);
  await page.getByRole("button", { name: "Carregar mais", exact: true }).click();
  await page.locator('[data-id="l55"]').waitFor();
  assert.equal(requests.at(-1).searchParams.get("cursor"), fixture.proximo);
  assert.equal(await page.locator('[data-id="c55"]').count(), 1);
  assert.equal(await page.locator('.lanc-dia[aria-label*="20 de setembro"]').count(), 1);
  assert.equal(await page.getByRole("button", { name: "Carregar mais", exact: true }).count(), 0);
  assert.deepEqual(erros, []);
  await ctx.close();
});

test("filtros e busca histórica só no servidor; trocar combinação reinicia cursor", async () => {
  const { ctx, page, ir } = await abrir();
  const urls = [];
  await ctx.route("**/api/v2/lancamentos?*", (r) => {
    const u = new URL(r.request().url()); urls.push(u);
    return r.fulfill({ json: { ...fixture, itens: u.searchParams.has("q") ? [fixture.itens[4]] : fixture.itens, proximo: null } });
  });
  await ir("/lancamentos");
  await page.getByRole("combobox", { name: /^Origem/ }).selectOption("banco");
  await page.getByRole("combobox", { name: /^Tipo/ }).selectOption("saida");
  await page.getByRole("combobox", { name: /^Categoria/ }).selectOption("alimentacao");
  await page.getByRole("combobox", { name: /^Conta/ }).selectOption("7");
  await page.getByLabel("Buscar", { exact: true }).fill("loja ação");
  await page.waitForFunction(() => document.querySelector('.lancamentos .w-lede')?.textContent.includes("loja ação"));
  const u = urls.at(-1);
  assert.deepEqual(Object.fromEntries(u.searchParams), { mes: "2026-10", origem: "banco", tipo: "saida", categoria: "alimentacao", conta: "7", q: "loja ação" });
  assert.equal(await page.locator('[data-id="c55"]').count(), 1); // backend pode responder data de outro mês
  await page.getByLabel("Buscar", { exact: true }).fill("a x");
  await page.getByText("Use pelo menos uma palavra de 2 ou mais caracteres para buscar.").waitFor();
  assert.equal(urls.at(-1).searchParams.has("q"), false);
  await page.getByLabel("Buscar", { exact: true }).fill("");
  await page.getByRole("button", { name: "Mês anterior", exact: true }).click();
  await page.waitForFunction(() => document.querySelector('.month-title')?.textContent === "Setembro 2026");
  await page.waitForTimeout(50);
  assert.equal(urls.at(-1).searchParams.get("mes"), "2026-09");
  assert.equal(urls.at(-1).searchParams.has("cursor"), false);
  await ctx.close();
});

test("resposta atrasada de filtro anterior não mistura linhas", async () => {
  const { ctx, page, ir } = await abrir();
  let liberar;
  const atraso = new Promise((resolve) => { liberar = resolve; });
  await ctx.route("**/api/v2/lancamentos?*", async (r) => {
    const origem = new URL(r.request().url()).searchParams.get("origem");
    if (origem === "banco") await atraso;
    await r.fulfill({ json: { ...fixture, itens: origem ? fixture.itens.filter((i) => i.origem === origem) : fixture.itens, proximo: null } }).catch(() => {});
  });
  await ir("/lancamentos");
  await page.getByRole("combobox", { name: /^Origem/ }).selectOption("banco");
  await page.getByRole("combobox", { name: /^Origem/ }).selectOption("cartao");
  await page.locator('[data-id="c55"]').waitFor();
  liberar();
  await page.waitForTimeout(100);
  assert.deepEqual(await page.locator(".lanc-linha").evaluateAll((els) => els.map((e) => e.dataset.id)), ["c55"]);
  await ctx.close();
});

test("vazio com motivo e catálogos falhos permitem carregar e tentar novamente", async () => {
  const { ctx, page, ir } = await abrir({ espera: ".lancamentos", lancamentos: "vazia" });
  await ctx.route("**/api/v2/categorias", (r) => r.fulfill({ status: 400, json: {} }));
  await ctx.route("**/api/v2/contas", (r) => r.fulfill({ status: 400, json: {} }));
  await ir("/lancamentos");
  await page.getByText("Nenhum lançamento encontrado.").waitFor();
  await page.getByText("Não foi possível carregar as categorias.", { exact: false }).waitFor();
  assert.match(await page.locator(".lancamentos .selos").textContent(), /início do histórico/);
  assert.equal(await page.getByRole("combobox", { name: /^Categoria/ }).isDisabled(), true);
  await ctx.close();
});

test("falha de carregar mais mantém itens e cursor; nova tentativa recupera segunda página", async () => {
  const { ctx, page, ir } = await abrir();
  let falhar = true;
  await ctx.route("**/api/v2/lancamentos?*", (r) => {
    const cursor = new URL(r.request().url()).searchParams.get("cursor");
    if (cursor && falhar) return r.fulfill({ status: 400, json: {} });
    return r.fulfill({ json: cursor ? RESPOSTAS.lancamentos.segunda : fixture });
  });
  await ir("/lancamentos");
  await page.getByRole("button", { name: "Carregar mais", exact: true }).click();
  await page.getByText("Não foi possível carregar mais lançamentos.").waitFor();
  assert.equal(await page.locator(".lanc-linha").count(), 5);
  falhar = false;
  await page.locator(".lanc-mais").getByRole("button", { name: "Tentar novamente" }).click();
  await page.locator('[data-id="l55"]').waitFor();
  await ctx.close();
});

test("fronteiras reais de exemplos: CTA categoria não transfere filtros; Cmd-K omite resultados fictícios", async () => {
  const { ctx, page, ir } = await abrir({ espera: "#page-title" });
  const urls = [];
  page.on("request", (r) => { if (r.url().includes("/api/v2/lancamentos?")) urls.push(new URL(r.url())); });
  await ir("/gastos");
  await page.locator(".cats .cat", { hasText: "Delivery" }).click();
  await page.getByRole("button", { name: "Abrir lançamentos reais" }).click();
  await page.locator(".lanc-linha").first().waitFor();
  assert.deepEqual(Object.fromEntries(urls.at(-1).searchParams), { mes: "2026-10" });
  await page.keyboard.press("Control+k");
  assert.equal(await page.locator(".cmdk input").getAttribute("placeholder"), "Buscar página ou ação…");
  await page.locator(".cmdk input").fill("Aluguel");
  assert.equal(await page.locator('.cmdk [role="option"]').count(), 0);
  await ctx.close();
});

for (const width of [1440, 760, 390, 320]) test(`layout ${width}: metadados visíveis, sem overflow e modal cabe`, async () => {
  const { ctx, page, ir, erros } = await abrir({ width });
  await ctx.route("**/api/v2/lancamentos?*", (r) => r.fulfill({ json: { ...fixture, itens: [{ ...fixture.itens[0], descricao: "Descrição extensa ".repeat(12), mensagem: "Nota longa com detalhes ".repeat(45), valor: "999999999.99", moeda: "USD", motivos: ["outra_moeda", "moeda_presumida"] }, ...fixture.itens.slice(1)] } }));
  await ir("/lancamentos");
  assert.ok(await page.locator('[data-id="l900"] .lanc-meta').isVisible());
  await page.locator('[data-id="l901"] button').click();
  await page.locator(".lanc-form[open]").waitFor();
  const medidas = await page.evaluate(() => {
    const d = document.querySelector(".lanc-form").getBoundingClientRect();
    return [document.documentElement.scrollWidth - innerWidth, d.left, d.right - innerWidth, d.height - innerHeight, ...[...document.querySelectorAll('.lanc-form .btn, .lanc-form .field')].map((e) => e.getBoundingClientRect().height)];
  });
  assert.ok(medidas[0] <= 0 && medidas[1] >= 0 && medidas[2] <= 0 && medidas[3] <= 0, String(medidas));
  assert.ok(medidas.slice(4).every((n) => n >= 44));
  assert.deepEqual(erros, []);
  await ctx.close();
});

test("falha da primeira página permite repetir GET sem dados fictícios", async () => {
  const { ctx, page, ir } = await abrir({ espera: ".lancamentos" });
  let falhar = true;
  await ctx.route("**/api/v2/lancamentos?*", (r) => falhar ? r.fulfill({ status: 400, json: {} }) : r.fulfill({ json: fixture }));
  await ir("/lancamentos");
  await page.getByText("Não foi possível carregar os lançamentos.", { exact: false }).waitFor();
  assert.equal(await page.locator(".lanc-linha").count(), 0);
  falhar = false;
  await page.getByRole("button", { name: "Tentar novamente", exact: true }).click();
  await page.locator('[data-id="l901"]').waitFor();
  assert.equal(await page.locator(".lanc-linha").count(), 5);
  await ctx.close();
});

test("chat mantém extrato demonstrativo; abrir lançamentos reais preserva mês da barra sem recorte do exemplo", async () => {
  const { ctx, page, ir } = await abrir({ espera: "#page-title", perfil: "controlar" });
  await ir("/lancamentos");
  await page.getByRole("button", { name: "Mês anterior", exact: true }).click();
  const urls = [];
  page.on("request", (r) => { if (r.url().includes("/api/v2/lancamentos?")) urls.push(new URL(r.url())); });
  await page.evaluate(() => { location.hash = "#/piggy"; });
  await page.locator(".chat-follow button", { hasText: "Em que dia da semana eu mais gasto?" }).click();
  const resposta = page.locator(".chat > .msg-piggy").last();
  await resposta.locator(".msg-more").click();
  await resposta.locator(".cal-day[data-step='6']").first().click();
  assert.ok(await resposta.locator(".ledger").isVisible());
  assert.equal(await resposta.locator(".lanc-form").count(), 0);
  const abrirReal = resposta.getByRole("link", { name: "Abrir lançamentos reais" });
  await abrirReal.click();
  await page.locator('[data-id="l901"]').waitFor();
  assert.deepEqual(Object.fromEntries(urls.at(-1).searchParams), { mes: "2026-09" });
  assert.equal(await page.getByLabel("Buscar", { exact: true }).inputValue(), "");
  await ctx.close();
});
