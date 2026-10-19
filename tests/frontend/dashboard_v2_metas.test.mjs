/**
 * /painel#/metas com backend (leitura): cada estado da tabela de cópia com a fixture certa, os dois
 * ritmos lado a lado, os estados da página, o convite da reserva, o formulário sem o plano de
 * investimentos e o Resumo sem o card "Metas e caixinhas". A escrita é de
 * dashboard_v2_metas_escrita.test.mjs.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { PROTOTIPO, RESPOSTAS, abrirPainel, exigeArtefatoEmDia } from "./_painel.mjs";

let browser;
before(async () => { exigeArtefatoEmDia(); browser = await chromium.launch(); });
after(() => browser?.close());
const clone = (o) => JSON.parse(JSON.stringify(o));
const varios = () => clone(RESPOSTAS.metas.varios);

// `dados`: a resposta do GET /metas no lugar da fixture (ou uma função do pedido, para erro).
async function abrir({ dados, width, ...opts } = {}) {
  const p = await abrirPainel(browser, { espera: null, width, ...opts });
  if (dados) await p.ctx.route("**/api/v2/metas", (r) => r.request().method() === "GET" ? (typeof dados === "function" ? dados(r) : r.fulfill({ json: dados })) : r.fallback());
  await p.ir("/metas");
  await p.page.locator("#w-metas-painel").waitFor();
  return p;
}
const cartao = (page, nome) => page.locator("article.meta").filter({ has: page.getByRole("heading", { name: nome, exact: true }) });
const texto = (page, nome) => cartao(page, nome).innerText();

test("cada estado da tabela de cópia sai com a frase e o botão certos", async () => {
  const { ctx, page, erros } = await abrir();
  await page.locator("article.meta").first().waitFor();
  const t = Object.fromEntries(await Promise.all(RESPOSTAS.metas.varios.metas.map(async (m) => [m.nome, await texto(page, m.nome)])));
  assert.match(t["Reserva de emergência"], /Reserva de emergência · 6 meses das suas contas fixas \(6 × R\$ 3\.000,00\)/);
  assert.match(t["Reserva de emergência"], /R\$ 4\.500,00 de R\$ 18\.000,00[\s\S]*Falta R\$ 13\.500,00/);
  assert.match(t["Reserva de emergência"], /Sem prazo\. Escolha uma data e eu digo quanto guardar por mês\./);
  assert.match(t["Viagem pro Japão"], /Investido no Nubank/);
  assert.match(t["Viagem pro Japão"], /R\$ 888,89\/mês até junho de 2027/);
  assert.match(t["Viagem pro Japão"], /\+R\$ 600,00\/mês[\s\S]*inclui rendimento e variação[\s\S]*medido em 45 dias/);
  assert.match(t["Viagem pro Japão"], /Nesse ritmo, chega em dezembro de 2027, depois do prazo/);
  assert.match(t["Notebook novo"], /−R\$ 250,00\/mês[\s\S]*inclui rendimento e variação/);
  assert.match(t["Notebook novo"], /Nesse ritmo, a meta não anda\./);
  assert.match(t["Entrada do apê"], /Ainda estou juntando histórico \(4 de 7 dias\)\. Volto a calcular seu ritmo a partir de 7 dias\./);
  assert.match(t["Curso de inglês"], /Ainda estou juntando histórico dos seus bancos\. Em alguns dias eu calculo seu ritmo\./);
  assert.match(t.Moto, /um dos seus bancos não confirma a leitura dos investimentos[\s\S]*Conectar banco/);
  assert.match(t["Festa de formatura"], /Pra saldo de conta eu ainda não meço ritmo\. Só o que está investido tem histórico\./);
  assert.match(t["Festa de formatura"], /R\$ 467,00\/mês até fevereiro de 2027/);
  assert.match(t["Intercâmbio"], /O prazo era agosto de 2026[\s\S]*Mudar prazo/);
  assert.match(t["Fone de ouvido"], /Meta batida/);
  assert.doesNotMatch(t["Fone de ouvido"], /Falta|ritmo/);
  assert.match(t["Dinheiro que sumiu do banco"], /Não acho mais essa fonte\./);
  assert.match(t["Conta pausada"], /R\$ 0,00 de R\$ 4\.000,00\s+a conferir/);
  assert.match(t["Conta pausada"], /A conexão com o banco está pausada\. Reconecte pra eu voltar a ler\.[\s\S]*Conectar banco/);
  // os links: Conectar banco vai para o Open Finance
  assert.deepEqual(await cartao(page, "Moto").getByRole("link", { name: "Conectar banco" }).getAttribute("href"), "#/conta/bancos");
  assert.deepEqual(erros, []);
  await ctx.close();
});

test("atual null vira traço e frase, nunca R$ 0,00; só o 0.00 de verdade mostra zero", async () => {
  const { ctx, page } = await abrir();
  await page.locator("article.meta").first().waitFor();
  const sumiu = await texto(page, "Dinheiro que sumiu do banco");
  assert.doesNotMatch(sumiu, /R\$/);
  assert.match(sumiu, /^Dinheiro que sumiu do banco[\s\S]*—/);
  assert.equal(await cartao(page, "Dinheiro que sumiu do banco").getByRole("progressbar").count(), 0);
  assert.match(await texto(page, "Conta pausada"), /R\$ 0,00 de R\$ 4\.000,00/);
  await ctx.close();
});

test("barra de progresso: valor, texto falado e verde só na meta batida; ritmo negativo em warn, positivo em gain", async () => {
  const { ctx, page } = await abrir();
  const barra = cartao(page, "Viagem pro Japão").getByRole("progressbar");
  assert.deepEqual(
    [await barra.getAttribute("aria-valuenow"), await barra.getAttribute("aria-valuetext"), await barra.getAttribute("aria-label")],
    ["60", "60%: R$ 12.000,00 de R$ 20.000,00", "Progresso de Viagem pro Japão"]);
  assert.equal(await page.evaluate(() => document.querySelector("article.meta .meter.ok") !== null), true);
  assert.equal(await cartao(page, "Fone de ouvido").locator(".meter.ok").count(), 1);
  assert.equal(await cartao(page, "Viagem pro Japão").locator(".meter.ok").count(), 0);
  assert.equal(await cartao(page, "Notebook novo").locator(".meta-numero.warn").count(), 1);
  assert.equal(await cartao(page, "Viagem pro Japão").locator(".meta-numero.gain").count(), 1);
  // o texto longo do "medido em N dias" é a descrição do selo
  const selo = cartao(page, "Viagem pro Japão").locator(".selo", { hasText: "medido em 45 dias" });
  const desc = await selo.getAttribute("aria-describedby");
  assert.equal(await page.locator(`[id="${desc}"]`).textContent(), "menos de 3 meses: ainda pode mudar bastante");
  await ctx.close();
});

test("chega_em dentro do prazo (comparado como texto AAAA-MM); a reserva vem primeiro mesmo no fim da lista", async () => {
  const d = varios();
  d.metas.find((m) => m.id === 2).chega_em = "2027-06"; // o mesmo mês do prazo (2027-06-30): dentro
  d.metas.push(d.metas.shift()); // a reserva no fim
  const { ctx, page } = await abrir({ dados: d });
  await page.locator("article.meta").first().waitFor();
  assert.match(await texto(page, "Viagem pro Japão"), /Nesse ritmo, chega em junho de 2027, dentro do prazo/);
  assert.equal(await page.locator("article.meta h3").first().textContent(), "Reserva de emergência");
  await ctx.close();
});

test("reserva sem custo: guarda o valor, diz por quê e oferece Definir o valor eu mesmo; meta sem o plano manda Ver planos", async () => {
  const { ctx, page } = await abrir({ metas: "sem_custo" });
  await page.locator("article.meta").first().waitFor();
  const r = await texto(page, "Reserva de emergência");
  assert.match(r, /R\$ 800,00 guardado/);
  assert.doesNotMatch(r, /Falta| de R\$/);
  assert.match(r, /Seu plano não calcula as contas fixas\. Defina o valor você mesmo\.[\s\S]*Definir o valor eu mesmo/);
  const v = await texto(page, "Viagem");
  assert.match(v, /—[\s\S]*Seu plano atual não inclui investimentos\.[\s\S]*Ver planos/);
  assert.equal(await cartao(page, "Viagem").getByRole("link", { name: "Ver planos" }).getAttribute("href"), "/precos");
  await ctx.close();
});

test("convite da reserva: sem reserva e com custo mostra o valor; sem custo, o motivo; com reserva, some", async () => {
  const sem = { metas: [], custo_mensal: RESPOSTAS.metas.vazio.custo_mensal };
  let p = await abrir({ metas: "vazio" });
  await p.page.locator(".meta-convite").waitFor();
  assert.equal(await p.page.locator(".meta-convite").innerText(), "Reserva de emergência: 6 meses das suas contas fixas (R$ 3.000,00/mês)\n\nMontar reserva");
  assert.match(await p.page.locator(".empty").innerText(), /Você ainda não tem metas/);
  await p.ctx.close();
  p = await abrir({ dados: { ...sem, custo_mensal: { valor: null, recorrencias: 0, motivos: ["sem_banco_conectado"] } } });
  await p.page.locator(".meta-convite").waitFor();
  const t = await p.page.locator(".meta-convite").innerText();
  assert.match(t, /Conecte um banco pra eu calcular suas contas fixas\./);
  assert.doesNotMatch(t, /R\$/);
  await p.ctx.close();
  // Plano sem o custo: nem o convite nem o formulário têm campo de valor, então não mandam "definir o valor".
  p = await abrir({ dados: { ...sem, custo_mensal: RESPOSTAS.metas.sem_custo.custo_mensal } });
  await p.page.locator(".meta-convite").waitFor();
  const guia = /Seu plano não calcula as contas fixas\. Depois de salvar a reserva, defina o valor no cartão dela\./;
  assert.match(await p.page.locator(".meta-convite").innerText(), guia);
  await p.page.getByRole("button", { name: "Montar reserva" }).click();
  const nota = await p.page.locator(".meta-form[open] .meta-nota").innerText();
  assert.match(nota, guia);
  assert.doesNotMatch(nota + await p.page.locator(".meta-convite").innerText(), /Defina o valor você mesmo/);
  await p.ctx.close();
  p = await abrir();
  await p.page.locator("article.meta").first().waitFor();
  assert.equal(await p.page.locator(".meta-convite").count(), 0);
  await p.ctx.close();
});

test("motivo que o front não conhece sai cru e a tela não quebra", async () => {
  const d = varios();
  const m = d.metas.find((x) => x.nome === "Curso de inglês");
  m.ritmo = { valor: null, dias_de_historico: null, parcial: false, motivos: ["motivo_novo_do_servidor"] };
  m.motivos = ["outro_motivo_novo"];
  d.custo_mensal = { valor: null, recorrencias: 0, motivos: ["custo_novo"] };
  d.metas = d.metas.filter((x) => x.tipo !== "reserva");
  const { ctx, page, erros } = await abrir({ dados: d });
  await page.locator("article.meta").first().waitFor();
  const t = await texto(page, "Curso de inglês");
  assert.match(t, /motivo_novo_do_servidor/);
  assert.match(t, /outro_motivo_novo/);
  assert.match(await page.locator(".meta-convite").innerText(), /custo_novo/);
  assert.deepEqual(erros, []);
  await ctx.close();
});

test("estados da página: carregando, erro de rede com Tentar de novo, 403 com Recarregar, senha e 500", async () => {
  let solta;
  const espera = new Promise((r) => { solta = r; });
  let p = await abrir({ dados: async (r) => { await espera; return r.fulfill({ json: RESPOSTAS.metas.vazio }); } });
  assert.equal(await p.page.getByRole("status").filter({ hasText: "Carregando…" }).count(), 1);
  assert.equal(await p.page.getByRole("button", { name: "Nova meta" }).isDisabled(), true);
  solta();
  await p.page.locator(".meta-convite").waitFor();
  await p.ctx.close();

  let falha = true;
  p = await abrir({ dados: (r) => (falha ? r.abort() : r.fulfill({ json: RESPOSTAS.metas.varios })) });
  await p.page.getByText("Não deu para carregar suas metas.").waitFor({ timeout: 15000 });
  falha = false;
  await p.page.getByRole("button", { name: "Tentar de novo" }).click();
  await p.page.locator("article.meta").first().waitFor();
  await p.ctx.close();

  const e403 = RESPOSTAS.erros["403_pro_required"];
  p = await abrir({ dados: (r) => r.fulfill({ status: e403.status, json: e403.body }) });
  await p.page.getByText("Não deu para abrir suas metas.").waitFor();
  assert.equal(await p.page.getByRole("button", { name: "Recarregar" }).count(), 1);
  assert.equal(await p.page.locator("article.meta").count(), 0);
  await p.ctx.close();

  const sen = RESPOSTAS.erros["403_password_required"];
  p = await abrir({ dados: (r) => r.fulfill({ status: sen.status, json: sen.body }) });
  await p.page.getByRole("link", { name: "Criar senha" }).waitFor();
  assert.equal(await p.page.getByRole("link", { name: "Criar senha" }).getAttribute("href"), "/home");
  await p.ctx.close();

  const e500 = RESPOSTAS.erros["500"];
  p = await abrir({ dados: (r) => r.fulfill({ status: e500.status, json: e500.body }) });
  await p.page.getByText("Não deu para carregar suas metas.").waitFor({ timeout: 15000 });
  assert.equal(await p.page.getByRole("button", { name: "Tentar de novo" }).count(), 1);
  await p.ctx.close();
});

test("formulário sem o plano de investimentos: só a conta, com Ver planos; investido com erro 500: banco desabilitado e Tentar de novo", async () => {
  const e = RESPOSTAS.erros["403_pro_required"];
  let p = await abrir({});
  await p.ctx.route("**/api/v2/investido", (r) => r.fulfill({ status: e.status, json: e.body }));
  await p.page.getByRole("button", { name: "Nova meta" }).click();
  const f = p.page.locator(".meta-form");
  await f.getByText("Seu plano atual não inclui investimentos. Dá para usar o saldo de uma conta.").waitFor();
  assert.deepEqual(await f.locator('input[name="fonte"]').evaluateAll((els) => els.map((x) => x.parentElement.textContent)), ["Saldo de uma conta"]);
  assert.equal(await f.getByRole("link", { name: "Ver planos" }).getAttribute("href"), "/precos");
  assert.doesNotMatch(await f.innerText(), /Plus|Pro\b|Essencial/);
  await p.ctx.close();

  p = await abrir({});
  const e500 = RESPOSTAS.erros["500"];
  await p.ctx.route("**/api/v2/investido", (r) => r.fulfill({ status: e500.status, json: e500.body }));
  await p.page.getByRole("button", { name: "Nova meta" }).click();
  await p.page.getByLabel("Investido em um banco").check();
  await p.page.getByRole("button", { name: "Tentar de novo" }).waitFor({ timeout: 15000 });
  assert.equal(await p.page.getByLabel("Banco", { exact: true }).isDisabled(), true);
  assert.equal(await p.page.getByLabel("Tudo que tenho investido").isEnabled(), true);
  await p.ctx.close();
});

test("o protótipo (demo) continua com a demonstração em /metas e não chama a API de metas", async () => {
  const pedidos = [];
  const { ctx, page } = await abrirPainel(browser, { demo: true, espera: null });
  page.on("request", (r) => { if (r.url().includes("/api/v2/metas")) pedidos.push(r.url()); });
  await page.goto(`${PROTOTIPO}#/metas`);
  await page.locator("#w-metas").waitFor();
  assert.equal(await page.locator(".metas-corpo").count(), 0);
  assert.match(await page.locator("#w-metas .w-title").innerText(), /Suas metas/);
  assert.deepEqual(pedidos, []);
  await ctx.close();
});

test("Resumo: o card Metas e caixinhas saiu e o Resumo do mês tem três números; layout salvo com metas não quebra", async () => {
  const { ctx, page, erros, ir } = await abrirPainel(browser, {});
  await ctx.addInitScript(() => localStorage.setItem("pigbank.dashboard.layout.v1.padrao", JSON.stringify(["metas", "resumo", "patrimonio"])));
  await ir("/");
  await page.locator("#w-resumo .stat-value").first().waitFor();
  const r = await page.evaluate(() => ({
    blocos: [...document.querySelectorAll("[data-widget-id]")].map((w) => w.dataset.widgetId),
    stats: [...document.querySelectorAll("#w-resumo .stat-title")].map((s) => s.textContent),
    guardado: document.body.innerText.includes("Guardado no mês"),
  }));
  assert.deepEqual(r.blocos.filter((b) => b === "metas"), []);
  assert.deepEqual(r.blocos.sort(), ["contas", "patrimonio", "resumo"]);
  assert.deepEqual(r.stats, ["Entrou", "Saiu", "Fatura aberta"]);
  assert.equal(r.guardado, false);
  assert.deepEqual(erros, []);
  await ctx.close();
});
