/**
 * /painel#/orcamento (Orçamento doméstico, método dos potes) sobre /api/v2/orcamento-domestico:
 * os 6 potes e a renda com origem, o convite do Essencial (sem POST), os erros da leitura, a edição
 * dos percentuais (soma ao vivo, Salvar desligado fora de 100, CSRF, 422, duplo clique), a renda
 * (editar e "Usar a calculada"), o mês da barra de cima, o protótipo (DEMO) e 375/1440 sem rolagem lateral.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { RESPOSTAS, abrirPainel, exigeArtefatoEmDia } from "./_painel.mjs";

let browser;
before(async () => { exigeArtefatoEmDia(); browser = await chromium.launch(); });
after(() => browser?.close());

async function abrir({ width, ...opts } = {}) {
  const p = await abrirPainel(browser, { espera: null, width, ...opts });
  const pedidos = [];
  p.page.on("request", (r) => { if (r.url().includes("/orcamento-domestico")) pedidos.push({ m: r.method(), u: new URL(r.url()), h: r.headers(), corpo: r.postData() }); });
  await p.ir("/orcamento");
  return { ...p, pedidos };
}
const painel = (page) => page.locator("#w-orcamento");
const aberto = async (opts) => { const p = await abrir(opts); await p.page.locator(".orc-pote").first().waitFor(); return p; };
const semRolagem = (page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth);

test("lê os 6 potes e a renda com a origem, pelo mês da barra de cima", async () => {
  const { ctx, page, pedidos, erros } = await aberto();
  const potes = await page.locator(".orc-pote h3").allInnerTexts();
  const t = await painel(page).innerText();
  await ctx.close();
  assert.deepEqual(potes, ["Custos fixos", "Conforto", "Metas", "Prazeres", "Liberdade financeira", "Conhecimento"]);
  assert.match(t, /Renda do mês\s+R\$ 5\.000,00\s+calculada pelas receitas do mês/);
  assert.match(t, /Custos fixos[\s\S]*55%[\s\S]*Teto\s+R\$ 2\.750,00[\s\S]*Gasto\s+R\$ 2\.000,00 · 73%[\s\S]*Restante\s+R\$ 750,00/);
  assert.match(t, /Total dos tetos\s+R\$ 5\.000,00[\s\S]*Gasto no mês\s+R\$ 2\.985,00 · 60%/);
  assert.equal(pedidos[0].u.searchParams.get("mes"), "2026-10");
  assert.deepEqual(erros, []);
});

test("pote que passou do teto diz \"passou\" em texto, não só na cor", async () => {
  const { ctx, page } = await aberto();
  const conforto = await page.locator(".orc-pote").filter({ hasText: "Conforto" }).innerText();
  const warn = await page.locator(".orc-pote").filter({ hasText: "Conforto" }).locator(".meter.warn").count();
  await ctx.close();
  assert.match(conforto, /passou R\$ 60,00 do teto/);
  assert.equal(warn, 1);
});

test("donut da alocação: 6 fatias, renda no centro e aria-label com a lista dos potes", async () => {
  const { ctx, page } = await aberto();
  const fatias = await page.locator(".orc-donut circle.orc-fatia").count();
  const rotulo = await page.locator(".orc-donut svg").getAttribute("aria-label");
  const papel = await page.locator(".orc-donut svg").getAttribute("role");
  const centro = await page.locator(".orc-donut-centro").innerText();
  await ctx.close();
  assert.equal(fatias, 6);
  assert.equal(papel, "img");
  assert.match(rotulo, /Custos fixos: 55% \(R\$ 2\.750,00\); Conforto: 5% \(R\$ 250,00\);/);
  assert.match(centro, /R\$ 5\.000,00/);
});

test("donut com renda zero: estado vazio, sem fatias e sem NaN", async () => {
  const { ctx, page } = await aberto({ orcamento: "renda_zero" });
  const html = await page.locator(".orc-donut").innerHTML();
  const fatias = await page.locator(".orc-donut circle.orc-fatia").count();
  const rotulo = await page.locator(".orc-donut svg").getAttribute("aria-label");
  await ctx.close();
  assert.equal(fatias, 0);
  assert.doesNotMatch(html, /NaN|Infinity/);
  assert.match(rotulo, /sem renda/);
});

test("clicar no pote abre o modal com os lançamentos; Esc fecha e devolve o foco ao card", async () => {
  const { ctx, page } = await aberto();
  const botao = page.getByRole("button", { name: "Custos fixos" });
  await botao.click();
  const dlg = page.locator("dialog.orc-modal");
  await dlg.waitFor();
  const t = await dlg.innerText();
  const chips = await dlg.locator(".orc-chips li").allInnerTexts();
  await page.keyboard.press("Escape");
  await dlg.waitFor({ state: "detached" });
  const foco = await page.evaluate(() => document.activeElement?.textContent);
  await ctx.close();
  assert.match(t, /Custos fixos · outubro de 2026/);
  assert.match(t, /Categorias neste pote: Alimentação, Mercado, Moradia, Transporte, Saúde, Assinaturas/);
  assert.match(t, /Gasto R\$ 2\.000,00 de R\$ 2\.750,00/);
  assert.match(t, /Aluguel\s+R\$ 1\.534,10\s+Moradia · 05\/10/);
  assert.match(t, /Pão de Açúcar\s+R\$ 320,40\s+Mercado · 08\/10 · Cartão/);
  assert.doesNotMatch(t.match(/Aluguel[^\n]*\n[^\n]*\n/)[0], /Cartão/);
  assert.deepEqual(chips.map((c) => c.replace(/\s+R\$.*/s, "")), ["Moradia", "Mercado", "Transporte", "Assinaturas"]);
  assert.equal(foco, "Custos fixos");
});

test("pote sem lançamento mostra o estado vazio no modal", async () => {
  const { ctx, page } = await aberto();
  await page.getByRole("button", { name: "Metas" }).click();
  const t = await page.locator("dialog.orc-modal").innerText();
  await ctx.close();
  assert.match(t, /Guardado R\$ 0,00 de R\$ 500,00/);
  assert.match(t, /Nenhum lançamento neste pote em outubro de 2026\./);
});

test("Metas e Liberdade acima do teto são guardados: sem aviso, sem \"passou\", total só em aviso por pote não guardado", async () => {
  const dados = JSON.parse(JSON.stringify(RESPOSTAS.orcamento_domestico.padrao));
  const set = (i, gasto, restante) => { dados.potes[i].gasto = gasto; dados.potes[i].restante = restante; dados.potes[i].usado_pct = "120.00"; };
  set(2, "600.00", "-100.00"); // Metas
  set(4, "560.00", "-60.00"); // Liberdade financeira
  dados.potes[1].gasto = "250.00"; dados.potes[1].restante = "0.00"; // Conforto no teto: ninguém não guardado estourou
  dados.potes[3].gasto = "125.00"; // Prazeres
  dados.total.restante = "-160.00"; dados.total.restante_livre = "1575.00"; // o líquido (-160) inclui o excedente guardado; o livre não
  const p = await abrirPainel(browser, { espera: null });
  await p.ctx.route("**/api/v2/orcamento-domestico*", (r) => r.request().method() === "GET" ? r.fulfill({ json: dados }) : r.fallback());
  await p.ir("/orcamento");
  await p.page.locator(".orc-pote").first().waitFor();
  const guardados = [];
  for (const nome of ["Metas", "Liberdade financeira"]) {
    const c = p.page.locator(".orc-pote").filter({ hasText: nome });
    guardados.push({ t: await c.innerText(), warn: await c.locator(".warn").count() });
  }
  const total = await p.page.locator(".orc-total").innerText();
  const totalWarn = await p.page.locator(".orc-total .warn").count();
  await p.page.getByRole("button", { name: "Metas" }).click();
  const modal = await p.page.locator("dialog.orc-modal").innerText();
  await p.page.keyboard.press("Escape");
  await p.ctx.close();
  for (const g of guardados) { assert.equal(g.warn, 0); assert.doesNotMatch(g.t, /passou|Gasto/); assert.match(g.t, /Guardado/); assert.match(g.t, /guardou R\$ \d+,00 a mais/); }
  assert.equal(totalWarn, 0);
  assert.doesNotMatch(total, /passou|guardou/);
  assert.match(total, /Restante\s+R\$ 1\.575,00/);
  assert.match(modal, /Guardado R\$ 600,00 de R\$ 500,00/);
});

test("pote não guardado estourado continua em aviso e põe o total em aviso (controle da regra dos guardados)", async () => {
  const dados = JSON.parse(JSON.stringify(RESPOSTAS.orcamento_domestico.padrao)); // Conforto: passou R$ 60,00
  dados.total.restante = "-60.00"; dados.total.restante_livre = "-60.00";
  const p = await abrirPainel(browser, { espera: null });
  await p.ctx.route("**/api/v2/orcamento-domestico*", (r) => r.request().method() === "GET" ? r.fulfill({ json: dados }) : r.fallback());
  await p.ir("/orcamento");
  await p.page.locator(".orc-pote").first().waitFor();
  const totalWarn = await p.page.locator(".orc-total .warn").count();
  const total = await p.page.locator(".orc-total").innerText();
  await p.ctx.close();
  assert.equal(totalWarn, 1);
  assert.match(total, /passou R\$ 60,00/);
});

test("misto: Conforto -60 + Metas -2000 guardado com folga nos outros: rodapé mostra o restante_livre, sem aviso", async () => {
  const dados = JSON.parse(JSON.stringify(RESPOSTAS.orcamento_domestico.padrao)); // Conforto -60; guardado de verdade vem do servidor
  dados.potes[2].restante = "-2000.00"; // Metas guardado
  dados.total.restante = "-485.00"; dados.total.restante_livre = "1515.00"; // 750 - 60 + 375 + 450 (+ 0 do guardado)
  const p = await abrirPainel(browser, { espera: null });
  await p.ctx.route("**/api/v2/orcamento-domestico*", (r) => r.request().method() === "GET" ? r.fulfill({ json: dados }) : r.fallback());
  await p.ir("/orcamento");
  await p.page.locator(".orc-pote").first().waitFor();
  const totalWarn = await p.page.locator(".orc-total .warn").count();
  const total = await p.page.locator(".orc-total").innerText();
  await p.ctx.close();
  assert.equal(totalWarn, 0);
  assert.match(total, /Restante\s+R\$ 1\.515,00/);
  assert.doesNotMatch(total, /passou|guardou/);
});

test("misto: Conforto -2000 + Metas -100 guardado: rodapé em aviso com o restante_livre negativo", async () => {
  const dados = JSON.parse(JSON.stringify(RESPOSTAS.orcamento_domestico.padrao));
  dados.potes[1].restante = "-2000.00"; dados.potes[2].restante = "-100.00";
  dados.total.restante = "-1000.00"; dados.total.restante_livre = "-875.00"; // 750 - 2000 + 375 + 450 + 0 + 0 (liberdade 0)
  const p = await abrirPainel(browser, { espera: null });
  await p.ctx.route("**/api/v2/orcamento-domestico*", (r) => r.request().method() === "GET" ? r.fulfill({ json: dados }) : r.fallback());
  await p.ir("/orcamento");
  await p.page.locator(".orc-pote").first().waitFor();
  const totalWarn = await p.page.locator(".orc-total .warn").count();
  const total = await p.page.locator(".orc-total").innerText();
  await p.ctx.close();
  assert.equal(totalWarn, 1);
  assert.match(total, /passou R\$ 875,00/);
});

test("contorno do card: aparece com Tab, não aparece após clique de mouse + fechar o modal", async () => {
  const { ctx, page } = await aberto();
  const contorno = (nome) => page.locator(".orc-pote").filter({ hasText: nome }).evaluate((e) => getComputedStyle(e).outlineStyle);
  await page.keyboard.press("Tab");
  for (let i = 0; i < 40 && !(await page.evaluate(() => document.activeElement?.classList.contains("orc-pote-btn"))); i++) await page.keyboard.press("Tab");
  const comTab = await page.evaluate(() => getComputedStyle(document.activeElement.closest(".orc-pote")).outlineStyle);
  await page.getByRole("button", { name: "Conforto" }).click();
  await page.locator("dialog.orc-modal").waitFor();
  await page.locator("dialog.orc-modal header").getByRole("button", { name: "Fechar" }).click(); // fechar com o mouse (Esc é teclado e legitimamente acende)
  await page.locator("dialog.orc-modal").waitFor({ state: "detached" });
  const aposMouse = await contorno("Conforto");
  await ctx.close();
  assert.equal(comTab, "solid");
  assert.equal(aposMouse, "none");
});

test("renda informada mostra a origem e \"Usar a calculada\" manda valor null", async () => {
  const { ctx, page, pedidos } = await aberto({ orcamento: "informada" });
  assert.match(await painel(page).innerText(), /informada por você/);
  // O POST responde a renda calculada (a fixture `padrao`): a espera abaixo só passa depois da resposta.
  await ctx.route("**/api/v2/orcamento-domestico/renda", (r) => r.fulfill({ json: RESPOSTAS.orcamento_domestico.padrao }));
  await page.getByRole("button", { name: "Usar a calculada" }).click();
  await page.waitForFunction(() => document.querySelector("#w-orcamento")?.textContent.includes("calculada pelas receitas"));
  const post = pedidos.find((p) => p.m === "POST");
  await ctx.close();
  assert.equal(post.u.pathname, "/api/v2/orcamento-domestico/renda");
  assert.deepEqual(JSON.parse(post.corpo), { mes: "2026-10", valor: null });
});

test("percentual fracionário sai em pt-BR (33,33%) e o inteiro sem casas (55%)", async () => {
  const dados = JSON.parse(JSON.stringify(RESPOSTAS.orcamento_domestico.padrao));
  dados.potes[1].pct = "33.33";
  const p = await abrirPainel(browser, { espera: null });
  await p.ctx.route("**/api/v2/orcamento-domestico*", (r) => r.request().method() === "GET" ? r.fulfill({ json: dados }) : r.fallback());
  await p.ir("/orcamento");
  await p.page.locator(".orc-pote").first().waitFor();
  const fixos = await p.page.locator(".orc-pote").filter({ hasText: "Custos fixos" }).innerText();
  const conforto = await p.page.locator(".orc-pote").filter({ hasText: "Conforto" }).innerText();
  await p.ctx.close();
  assert.match(fixos, /55%/);
  assert.match(conforto, /33,33%/);
});

test("renda zero: traço no uso e a dica para informar a renda", async () => {
  const { ctx, page } = await aberto({ orcamento: "renda_zero" });
  const t = await painel(page).innerText();
  await ctx.close();
  assert.match(t, /Sem renda neste mês: informe a renda para dividir nos potes\./);
  assert.match(t, /Gasto\s+R\$ 0,00(?! ·)/);
});

test("Essencial vê o convite e nenhum POST sai", async () => {
  const { ctx, page, pedidos } = await abrir({ plano: "essencial" });
  await page.getByRole("link", { name: "Ver planos" }).waitFor();
  const t = await painel(page).innerText();
  await ctx.close();
  assert.match(t, /O Orçamento doméstico é do Plus e do Pro\./);
  assert.deepEqual(pedidos.filter((p) => p.m === "POST"), []);
});

test("500 na leitura mostra \"Tentar de novo\" e ele recarrega", async () => {
  const p = await abrirPainel(browser, { espera: null });
  let n = 0;
  await p.ctx.route("**/api/v2/orcamento-domestico*", (r) => ++n < 4 ? r.fulfill({ status: 500, json: { error: { code: "internal_error", message: "x" } } }) : r.fallback());
  await p.ir("/orcamento");
  const botao = p.page.getByRole("button", { name: "Tentar de novo" });
  await botao.waitFor({ timeout: 20000 });
  assert.match(await painel(p.page).innerText(), /Não deu para carregar o orçamento\./);
  await botao.click();
  await p.page.locator(".orc-pote").first().waitFor();
  await p.ctx.close();
});

test("editar percentuais: soma ao vivo, Salvar desligado fora de 100, POST com CSRF e JSON", async () => {
  const { ctx, page, pedidos } = await aberto();
  await page.getByRole("button", { name: "Editar percentuais" }).click();
  const salvar = page.getByRole("button", { name: "Salvar", exact: true });
  assert.equal(await salvar.isDisabled(), false); // os padrões fecham 100
  await page.locator('input[name="custos_fixos"]').fill("50");
  assert.match(await page.locator(".orc-soma").innerText(), /Soma: 95% · faltam 5%/);
  assert.equal(await salvar.isDisabled(), true);
  await page.locator('input[name="conforto"]').fill("5,5"); // vírgula vale
  await page.locator('input[name="metas"]').fill("15");
  await page.locator('input[name="prazeres"]').fill("9,5");
  await page.locator(".orc-soma", { hasText: "fecha 100%" }).waitFor(); // 50 + 5,5 + 15 + 9,5 + 10 + 10
  assert.equal(await salvar.isDisabled(), false);
  await page.locator('input[name="metas"]').fill("101");
  assert.equal(await salvar.isDisabled(), true);
  await page.locator('input[name="metas"]').fill("15");
  await page.locator('input[name="conforto"]').fill("5");
  await page.locator('input[name="prazeres"]').fill("10");
  await page.locator(".orc-soma", { hasText: "fecha 100%" }).waitFor();
  assert.equal(await salvar.isDisabled(), false);
  await salvar.click();
  await page.locator("dialog.orc-form").waitFor({ state: "detached" });
  const post = pedidos.find((p) => p.m === "POST");
  await ctx.close();
  assert.equal(post.u.pathname, "/api/v2/orcamento-domestico/potes");
  assert.equal(post.h["x-csrf-token"], "tok-123");
  assert.match(post.h["content-type"], /application\/json/);
  const corpo = JSON.parse(post.corpo);
  assert.equal(corpo.mes, "2026-10");
  assert.deepEqual(corpo.potes[0], { chave: "custos_fixos", pct: "50" });
  assert.equal(corpo.potes.length, 6);
});

test("422 mostra a mensagem fixa e mantém o rascunho; duplo clique manda um POST só", async () => {
  const p = await aberto();
  const { ctx, page } = p;
  let posts = 0;
  await ctx.route("**/api/v2/orcamento-domestico/potes", async (r) => {
    posts++;
    await new Promise((f) => setTimeout(f, 300));
    return r.fulfill({ status: 422, json: { error: { code: "validation_error", message: "x", details: [{ loc: ["body", "potes"], msg: "A soma precisa fechar 100%.", type: "value_error" }] } } });
  });
  await page.getByRole("button", { name: "Editar percentuais" }).click();
  await page.locator('input[name="custos_fixos"]').fill("45");
  await page.locator('input[name="conforto"]').fill("15");
  const salvar = page.getByRole("button", { name: /Salvar|Salvando/ });
  await salvar.dblclick();
  await page.getByRole("alert").filter({ hasText: "Confira os percentuais" }).waitFor();
  const valor = await page.locator('input[name="custos_fixos"]').inputValue();
  await ctx.close();
  assert.equal(posts, 1);
  assert.equal(valor, "45");
});

test("editar a renda: valida (zero vale, vírgula vale), manda texto e fecha", async () => {
  const { ctx, page, pedidos } = await aberto();
  await page.getByRole("button", { name: "Editar renda" }).click();
  const campo = page.locator('input[name="valor"]');
  await campo.fill("abc");
  await page.getByRole("button", { name: "Salvar", exact: true }).click();
  assert.match(await page.getByRole("alert").innerText(), /Use um valor de 0 até 9 dígitos/);
  assert.equal(pedidos.filter((p) => p.m === "POST").length, 0);
  await campo.fill("1234,56");
  await page.getByRole("button", { name: "Salvar", exact: true }).click();
  await page.locator("dialog.orc-form").waitFor({ state: "detached" });
  const post = pedidos.find((p) => p.m === "POST");
  await ctx.close();
  assert.deepEqual(JSON.parse(post.corpo), { mes: "2026-10", valor: "1234.56" });
});

test("trocar o mês na barra de cima pede ?mes= do mês escolhido", async () => {
  const { ctx, page, pedidos } = await aberto();
  await page.getByRole("button", { name: /anterior/i }).first().click();
  await page.waitForFunction(() => document.querySelector(".month-title")?.textContent.toLowerCase().includes("setembro"));
  await page.waitForTimeout(300);
  const meses = pedidos.filter((p) => p.m === "GET").map((p) => p.u.searchParams.get("mes"));
  await ctx.close();
  assert.ok(meses.includes("2026-09"), JSON.stringify(meses));
});

test("a página não está no menu lateral nem no Menu do celular (por href, vale também abaixo de 1181px)", async () => {
  const d = await aberto();
  const rail = d.page.locator(".rail-list");
  const noRail = [await rail.locator('a[href="#/orcamento"]').count(), await rail.locator('a[href="#/metas"]').count()]; // o 2º: controle positivo, o rail não está vazio
  await d.ctx.close();
  assert.deepEqual(noRail, [0, 1]);
  const m = await aberto({ width: 375 });
  await m.page.evaluate(() => window.dispatchEvent(new Event("dash:menu")));
  const menu = m.page.locator("dialog.menu-telas");
  const noMenu = [await menu.locator('a[href="#/orcamento"]').count(), await menu.locator('a[href="#/metas"]').count()];
  await m.ctx.close();
  assert.deepEqual(noMenu, [0, 1]);
});

test("⌘K lista Orçamento doméstico e navega para #/orcamento", async () => {
  const p = await abrirPainel(browser, { espera: null });
  await p.ir("/ferramentas");
  await p.page.locator(".tools li").first().waitFor();
  await p.page.locator(".cmd-trigger").focus();
  await p.page.keyboard.press("Enter");
  await p.page.locator(".cmdk input").fill("Orçamento");
  const opcao = p.page.getByRole("option", { name: /Orçamento doméstico/ });
  assert.equal(await opcao.count(), 1);
  await p.page.keyboard.press("Enter");
  await p.page.waitForFunction(() => location.hash === "#/orcamento");
  await p.page.locator(".orc-pote").first().waitFor();
  await p.ctx.close();
});
test("375 e 1440: sem rolagem lateral, com e sem pote estourado", async () => {
  for (const width of [375, 1440]) {
    for (const orcamento of ["padrao", "informada", "renda_zero"]) {
      const { ctx, page } = await aberto({ width, orcamento });
      await page.getByRole("button", { name: "Custos fixos" }).click();
      await page.locator("dialog.orc-modal").waitFor();
      const ok = await semRolagem(page);
      await ctx.close();
      assert.ok(ok, `${width} ${orcamento}`);
    }
  }
});

test("protótipo (DEMO): potes sem requisição e sem botões de editar", async () => {
  const { ctx, page, pedidos } = await abrir({ demo: true });
  await page.locator(".orc-pote").first().waitFor();
  const botoes = await painel(page).getByRole("button", { name: /Editar|Usar a calculada/ }).count(); // os 6 botões dos potes só abrem o modal
  const n = await page.locator(".orc-pote").count();
  await ctx.close();
  assert.equal(n, 6);
  assert.equal(botoes, 0);
  assert.deepEqual(pedidos, []);
});
