import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { abrirFiltros, abrirPainel, exigeArtefatoEmDia, RESPOSTAS } from "./_painel.mjs";
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
  for (const t of ["Carteira Piggy", "Nubank", "Registro antigo", "Hoje · 02/10", "Ontem · 01/10", "Dom · 20/09"]) assert.ok(text.includes(t), t);
  // P2: nada do contexto fica na linha; ele mora no detalhe
  const linhas = await page.locator(".lanc-linha").allTextContents();
  assert.doesNotMatch(linhas.join("|"), /Nota:|Parcela|Fatura de|Movimento interno|transação pendente|Junto com o banco/);
  for (const [id, esperado] of [["l901", "Nota: gastei 42,90"], ["c55", "Parcela 1 de 3"], ["c55", "Fatura de Outubro 2026"], ["l899", "Movimento interno · fora de Entrou/Saiu"], ["l900", "transação pendente"], ["l898", "Junto com o banco"]]) {
    await page.locator(`[data-id="${id}"] .lanc-abrir`).click();
    await page.locator(".lanc-form[open]").waitFor();
    assert.ok((await page.locator(".lanc-form").textContent()).includes(esperado), esperado);
    await page.keyboard.press("Escape");
    await page.locator(".lanc-form[open]").waitFor({ state: "detached" });
  }
  assert.doesNotMatch(text, /\d+ de \d+ resultados|WhatsApp/i);
  // regra (a): com próxima página o último dia fica SEM total; os dois completos têm
  assert.deepEqual(await page.locator(".lanc-dia h3 .num").allTextContents(), ["−R$ 72,90", "+R$ 1.040,00"]);
  assert.equal(await page.locator(".lanc-dia").last().locator("h3 .num").count(), 0);
  await page.getByRole("button", { name: "Carregar mais", exact: true }).click();
  await page.locator('[data-id="l55"]').waitFor();
  assert.equal(requests.at(-1).searchParams.get("cursor"), fixture.proximo);
  assert.equal(await page.locator('[data-id="c55"]').count(), 1);
  assert.equal(await page.locator('.lanc-dia[aria-label*="20 de setembro"]').count(), 1);
  assert.deepEqual(await page.locator(".lanc-dia h3 .num").allTextContents(), ["−R$ 72,90", "+R$ 1.040,00", "−R$ 100,01"]); // sem próxima página o último dia fecha, juntando as duas páginas
  assert.equal(await page.getByRole("button", { name: "Carregar mais", exact: true }).count(), 0);
  assert.deepEqual(erros, []);
  await ctx.close();
});

test("filtros e busca histórica só no servidor; trocar combinação reinicia cursor", async () => {
  const { ctx, page, ir } = await abrir();
  const urls = [];
  const respostaLista = (quando) => page.waitForResponse((r) => {
    const u = new URL(r.url());
    return r.request().method() === "GET" && u.pathname === "/api/v2/lancamentos" && quando(u.searchParams);
  });
  await ctx.route("**/api/v2/lancamentos?*", (r) => {
    const u = new URL(r.request().url()); urls.push(u);
    return r.fulfill({ json: { ...fixture, itens: u.searchParams.has("q") ? [fixture.itens[4]] : fixture.itens, proximo: null } });
  });
  await ir("/lancamentos");
  await abrirFiltros(page);
  await page.getByRole("combobox", { name: /^Origem/ }).selectOption("banco");
  await page.getByRole("combobox", { name: /^Tipo/ }).selectOption("saida");
  await page.getByRole("combobox", { name: /^Categoria/ }).selectOption("alimentacao");
  const conta = respostaLista((p) => p.has("conta"));
  await abrirFiltros(page);
  await page.getByRole("combobox", { name: /^Conta/ }).selectOption("7");
  await (await conta).finished();
  const historica = respostaLista((p) => p.has("q"));
  await page.getByLabel("Buscar lançamento", { exact: true }).fill("loja ação");
  await (await historica).finished();
  await page.waitForFunction(() => document.querySelector('.lancamentos .w-lede')?.textContent.includes("loja ação"));
  await page.locator(".lanc-linha").nth(1).waitFor({ state: "detached" });
  const u = urls.at(-1);
  assert.deepEqual(Object.fromEntries(u.searchParams), { mes: "2026-10", origem: "banco", tipo: "saida", categoria: "alimentacao", conta: "7", q: "loja ação", guardado: "true" });
  assert.equal(await page.locator('[data-id="c55"]').count(), 1); // backend pode responder data de outro mês
  const mensal = respostaLista((p) => !p.has("q"));
  await page.getByLabel("Buscar lançamento", { exact: true }).fill("a x");
  await (await mensal).finished();
  await page.getByText("Use pelo menos uma palavra de 2 ou mais caracteres para buscar.").waitFor();
  assert.equal(urls.at(-1).searchParams.has("q"), false);
  await page.getByLabel("Buscar lançamento", { exact: true }).fill("");
  const anterior = respostaLista((p) => p.get("mes") !== "2026-10");
  await page.getByRole("button", { name: "Mês anterior", exact: true }).click();
  await (await anterior).finished();
  await page.waitForFunction(() => document.querySelector('.month-title')?.textContent === "Setembro 2026");
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
  await abrirFiltros(page);
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
  await abrirFiltros(page);
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

test("fronteiras reais de exemplos: o CTA da categoria leva a escolha (sem tipo) aos lançamentos; Cmd-K omite resultados fictícios", async () => {
  const { ctx, page, ir } = await abrir({ espera: "#page-title" });
  const urls = [];
  page.on("request", (r) => { if (r.url().includes("/api/v2/lancamentos?")) urls.push(new URL(r.url())); });
  await ir("/gastos");
  await page.locator(".cats .cat", { hasText: "alimentação" }).click();
  await page.getByRole("button", { name: "Ver compras" }).click();
  await page.getByRole("button", { name: "Ver em Lançamentos" }).click();
  await page.waitForFunction(() => location.hash === "#/lancamentos");
  await page.locator(".lanc-linha").first().waitFor();
  // Decisão do dono: a ida é por filtro global, SEM `tipo` (a lista do extrato mostra entradas e saídas; a contagem pode passar a de "Compras").
  assert.deepEqual(Object.fromEntries(urls.at(-1).searchParams), { mes: "2026-10", categoria: "alimentacao", guardado: "true" });
  await abrirFiltros(page);
  assert.equal(await page.getByRole("combobox", { name: /^Categoria/ }).inputValue(), "alimentacao");
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
  assert.ok(await page.locator('[data-id="l900"] .faint').isVisible());
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
  assert.deepEqual(Object.fromEntries(urls.at(-1).searchParams), { mes: "2026-09", guardado: "true" });
  assert.equal(await page.getByLabel("Buscar lançamento", { exact: true }).inputValue(), "");
  await ctx.close();
});

for (const width of [1440, 390]) test(`guardado (P8, ${width}px): a tela pede guardado=true, diz Guardado, some com Registro antigo e não vira Entrada/Saída`, async () => {
  const { ctx, page, ir, erros } = await abrir({ width });
  const guardado = { ...fixture.itens[0], id: "l902", interno: true, guardado: true, descricao: "Viagem", origem: "registro_antigo", pode: [], motivos: [] };
  const saque = { ...guardado, id: "l903", tipo: "entrada", descricao: "Saque Viagem" };  // entrada: é onde o verde de ganho vazaria
  // o backend só devolve guardado a quem pede (opt-in): sem `guardado=true` a lista é a de antes
  await ctx.route("**/api/v2/lancamentos?*", (r) => r.fulfill({ json: { ...fixture, itens: new URL(r.request().url()).searchParams.get("guardado") === "true" ? [guardado, saque, ...fixture.itens] : fixture.itens, proximo: null } }));
  await ir("/lancamentos");
  const linha = page.locator('[data-id="l902"]');
  await linha.waitFor();
  const texto = await linha.textContent();
  assert.ok(texto.includes("Guardado") && texto.includes("Viagem") && !texto.includes("fora de Entrou/Saiu"), texto);
  assert.doesNotMatch(texto, /Movimento|Registro antigo|Entrada|Saída/);
  assert.equal(await linha.locator(".lanc-valor.gain").count(), 0);
  const entrada = page.locator('[data-id="l903"]');
  const textoSaque = await entrada.textContent();
  assert.ok(textoSaque.includes("Guardado") && textoSaque.includes("Saque Viagem"), textoSaque);
  assert.doesNotMatch(textoSaque, /Movimento|Registro antigo|Entrada|Saída/);
  assert.equal(await entrada.locator(".lanc-valor.gain").count(), 0);
  await linha.locator(".lanc-abrir").click();
  const dialog = page.locator("dialog.lanc-form");
  assert.ok((await dialog.textContent()).includes("Guardado · fora de Entrou/Saiu"));
  assert.doesNotMatch(await dialog.textContent(), /Movimento|Registro antigo|Apagar|Salvar/);
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), "sem rolagem horizontal");
  assert.deepEqual(erros, []);
  await ctx.close();
});
