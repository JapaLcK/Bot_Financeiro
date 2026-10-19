/**
 * /painel#/orcamentos-categoria (um teto mensal por categoria) sobre /api/v2/orcamentos-categoria:
 * navegação (card de Ferramentas, ⌘K, fora do menu e sem seletor de mês), lista e totais, vazio, teto do
 * plano Grátis, criar/editar/apagar (CSRF, JSON, duplo clique), erros do POST, protótipo (DEMO) e 375/1280 sem rolagem lateral.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { RESPOSTAS, abrirPainel, exigeArtefatoEmDia } from "./_painel.mjs";

let browser;
before(async () => { exigeArtefatoEmDia(); browser = await chromium.launch(); });
after(() => browser?.close());

const URL_ = "**/api/v2/orcamentos-categoria*";
async function abrir({ width, ...opts } = {}) {
  const p = await abrirPainel(browser, { espera: null, width, ...opts });
  const pedidos = [];
  p.page.on("request", (r) => { if (r.url().includes("/orcamentos-categoria")) pedidos.push({ m: r.method(), u: new URL(r.url()), h: r.headers(), corpo: r.postData() }); });
  await p.ir("/orcamentos-categoria");
  return { ...p, pedidos };
}
const painel = (page) => page.locator("#w-orcamentos-categoria");
const aberto = async (opts) => { const p = await abrir(opts); await p.page.locator(".orc-linha, .empty").first().waitFor(); return p; };
const semRolagem = (page) => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
const erroEnvelope = (status, code, details) => ({ status, json: { error: { code, message: "x", ...(details ? { details } : {}) } } });
const abrirNovo = async (page) => { await page.getByRole("button", { name: "Novo orçamento" }).click(); await page.locator("dialog.orc-form").waitFor(); };

test("o card de Ferramentas e o ⌘K abrem a página; fora do menu lateral e do celular; sem seletor de mês", async () => {
  const p = await abrirPainel(browser, { espera: null });
  await p.ir("/ferramentas");
  await p.page.locator(".tools li").first().waitFor();
  await p.page.locator(".tools a", { hasText: "Orçamentos por categoria" }).click();
  await p.page.waitForFunction(() => location.hash === "#/orcamentos-categoria");
  await p.page.locator(".orc-linha").first().waitFor();
  const seletor = await p.page.locator(".month-title").count();
  const rail = p.page.locator(".rail-list");
  const noRail = [await rail.locator('a[href="#/orcamentos-categoria"]').count(), await rail.locator('a[href="#/metas"]').count()]; // o 2º: controle positivo
  await p.ir("/ferramentas");
  await p.page.locator(".cmd-trigger").focus();
  await p.page.keyboard.press("Enter");
  await p.page.locator(".cmdk input").fill("por categoria");
  const opcao = p.page.getByRole("option", { name: /Orçamentos por categoria/ });
  assert.equal(await opcao.count(), 1);
  await p.page.keyboard.press("Enter");
  await p.page.waitForFunction(() => location.hash === "#/orcamentos-categoria");
  await p.ctx.close();
  assert.equal(seletor, 0);
  assert.deepEqual(noRail, [0, 1]);
  const m = await abrir({ width: 375 });
  await m.page.evaluate(() => window.dispatchEvent(new Event("dash:menu")));
  const menu = m.page.locator("dialog.menu-telas");
  const noMenu = [await menu.locator('a[href="#/orcamentos-categoria"]').count(), await menu.locator('a[href="#/metas"]').count()];
  await m.ctx.close();
  assert.deepEqual(noMenu, [0, 1]);
});

test("totais e linhas em BRL; o vermelho diz \"passou\" com ícone; sem NaN nem float cru", async () => {
  const { ctx, page, pedidos, erros } = await aberto();
  const t = await painel(page).innerText();
  const aviso = await page.locator(".orc-linha").filter({ hasText: "lazer" }).locator(".ph-warning").count();
  const warn = await page.locator(".orc-linha").filter({ hasText: "lazer" }).locator(".meter.warn").count();
  await ctx.close();
  assert.match(t, /Orçamentos de outubro de 2026/i);
  assert.match(t, /Orçado\s+R\$ 1\.450,00[\s\S]*Gasto no mês\s+R\$ 1\.053,80 · 73%[\s\S]*Disponível\s+R\$ 396,20[\s\S]*Em risco\s+3/);
  assert.match(t, /alimentação[\s\S]*R\$ 412,30 de R\$ 800,00[\s\S]*52% · R\$ 387,70 restantes/);
  assert.match(t, /mercado[\s\S]*85% · perto do teto/);
  assert.match(t, /transporte[\s\S]*chegou ao teto/);
  assert.match(t, /lazer[\s\S]*passou R\$ 36,50/);
  assert.doesNotMatch(t, /NaN|undefined|\d\.\d{2}\b/);
  assert.equal(aviso, 1);
  assert.equal(warn, 1);
  assert.equal(pedidos[0].u.searchParams.get("mes"), null); // só o mês corrente: sem ?mes=
  assert.deepEqual(erros, []);
});

test("sem orçamentos: estado vazio com o botão de criar", async () => {
  const { ctx, page } = await aberto({ orcamentosCategoria: "vazio" });
  const t = await painel(page).innerText();
  const botao = await page.getByRole("button", { name: "Criar orçamento" }).count();
  await ctx.close();
  assert.match(t, /Nenhum orçamento ainda[\s\S]*Crie um teto para uma categoria/);
  assert.equal(botao, 1);
});

test("Grátis no teto: \"3 de 3\", Novo orçamento desligado e Ver planos aponta para /precos", async () => {
  const { ctx, page } = await aberto({ plano: "free", orcamentosCategoria: "gratis_no_limite" });
  const t = await painel(page).innerText();
  const desligado = await page.getByRole("button", { name: "Novo orçamento" }).isDisabled();
  const opacidade = await page.getByRole("button", { name: "Novo orçamento" }).evaluate((b) => Number(getComputedStyle(b).opacity)); // desligado não pode parecer ativo
  const href = await painel(page).getByRole("link", { name: "Ver planos" }).getAttribute("href");
  await ctx.close();
  assert.match(t, /3 de 3 orçamentos no seu plano/);
  assert.equal(desligado, true);
  assert.ok(opacidade < 1, `opacity ${opacidade}`);
  assert.equal(href, "/precos");
});

test("criar: o select só oferece categoria sem orçamento; \"1234,56\" sai como texto; 0 não faz requisição", async () => {
  const { ctx, page, pedidos } = await aberto();
  await abrirNovo(page);
  const opcoes = await page.locator('select[name="categoria"] option').allInnerTexts();
  assert.deepEqual(opcoes, ["Casa"]); // alimentação e mercado já têm orçamento (compara pela chave)
  const campo = page.locator('input[name="valor"]');
  await campo.fill("0");
  await page.getByRole("button", { name: "Salvar", exact: true }).click();
  assert.match(await page.getByRole("alert").innerText(), /Use um valor maior que zero/);
  assert.equal(pedidos.filter((p) => p.m === "POST").length, 0);
  await campo.fill("1234,56");
  await page.getByRole("button", { name: "Salvar", exact: true }).click();
  await page.locator("dialog.orc-form").waitFor({ state: "detached" });
  const post = pedidos.find((p) => p.m === "POST");
  await ctx.close();
  assert.equal(post.u.pathname, "/api/v2/orcamentos-categoria");
  assert.equal(post.h["x-csrf-token"], "tok-123");
  assert.deepEqual(JSON.parse(post.corpo), { categoria: "Casa", valor: "1234.56" });
});

test("duplo clique em Salvar manda um POST só", async () => {
  const { ctx, page } = await aberto();
  let posts = 0;
  await ctx.route(URL_, async (r) => {
    if (r.request().method() !== "POST") return r.fallback();
    posts++;
    await new Promise((f) => setTimeout(f, 300));
    return r.fulfill({ json: RESPOSTAS.orcamentos_categoria.padrao });
  });
  await abrirNovo(page);
  await page.locator('input[name="valor"]').fill("10");
  await page.getByRole("button", { name: /Salvar|Salvando/ }).dblclick();
  await page.locator("dialog.orc-form").waitFor({ state: "detached" });
  await ctx.close();
  assert.equal(posts, 1);
});

for (const [nome, resposta, texto] of [
  ["403 plan_limit", erroEnvelope(403, "plan_limit"), /Você chegou ao limite de 3 orçamentos do seu plano\./],
  ["422 em categoria", erroEnvelope(422, "validation_error", [{ loc: ["body", "categoria"], msg: "x", type: "value_error" }]), /Escolha uma das suas categorias\./],
  ["422 em valor", erroEnvelope(422, "validation_error", [{ loc: ["body", "valor"], msg: "x", type: "value_error" }]), /Use um valor maior que zero/],
]) {
  test(`erro do POST: ${nome} mostra o texto fixo e mantém o rascunho`, async () => {
    const { ctx, page } = await aberto({ plano: "free", orcamentosCategoria: "gratis_no_limite" });
    await ctx.route(URL_, (r) => r.request().method() === "POST" ? r.fulfill(resposta) : r.fallback());
    // 3 de 3 desliga o botão: abre pela edição, que não conta para o teto
    await page.locator(".orc-linha").first().click();
    await page.locator("dialog.orc-form").waitFor();
    await page.locator('input[name="valor"]').fill("55");
    await page.getByRole("button", { name: "Salvar", exact: true }).click();
    await page.getByRole("alert").filter({ hasText: texto }).waitFor();
    const valor = await page.locator('input[name="valor"]').inputValue();
    const planos = await page.locator("dialog.orc-form").getByRole("link", { name: "Ver planos" }).count();
    await ctx.close();
    assert.equal(valor, "55");
    assert.equal(planos, nome === "403 plan_limit" ? 1 : 0);
  });
}

test("403 plan_limit com limite null (plano caiu depois do GET): texto sem número", async () => {
  const { ctx, page } = await aberto({ orcamentosCategoria: "vazio" }); // limite null no GET
  await ctx.route(URL_, (r) => r.request().method() === "POST" ? r.fulfill(erroEnvelope(403, "plan_limit")) : r.fallback());
  await page.getByRole("button", { name: "Criar orçamento" }).click();
  await page.locator("dialog.orc-form").waitFor();
  await page.locator('input[name="valor"]').fill("10");
  await page.getByRole("button", { name: "Salvar", exact: true }).click();
  const alerta = await page.getByRole("alert").filter({ hasText: "limite de orçamentos do seu plano" }).innerText();
  await ctx.close();
  assert.equal(alerta, "Você chegou ao limite de orçamentos do seu plano.");
});

test("resposta perdida (abort): texto de \"pode ter sido salvo\" e um GET novo", async () => {
  const { ctx, page, pedidos } = await aberto();
  await ctx.route(URL_, (r) => r.request().method() === "POST" ? r.abort() : r.fallback());
  await abrirNovo(page);
  await page.locator('input[name="valor"]').fill("10");
  const gets = pedidos.filter((p) => p.m === "GET").length;
  await page.getByRole("button", { name: "Salvar", exact: true }).click();
  await page.getByRole("alert").filter({ hasText: "O orçamento pode ter sido salvo" }).waitFor();
  await page.waitForTimeout(500); // a invalidação do onError relê o GET
  const depois = pedidos.filter((p) => p.m === "GET").length;
  await ctx.close();
  assert.ok(depois > gets, `${gets} -> ${depois}`);
});

test("editar: categoria fixa e valor preenchido; apagar confirma e manda a categoria exata; 404 fecha", async () => {
  const { ctx, page, pedidos } = await aberto();
  await page.locator(".orc-linha").filter({ hasText: "alimentação" }).click();
  await page.locator("dialog.orc-form").waitFor();
  const dialogo = page.locator("dialog.orc-form");
  assert.equal(await dialogo.locator("select").count(), 0);
  assert.match(await dialogo.innerText(), /alimentação/);
  assert.equal(await page.locator('input[name="valor"]').inputValue(), "800,00");
  await page.getByRole("button", { name: "Apagar", exact: true }).click();
  assert.match(await dialogo.innerText(), /Apagar o orçamento de alimentação\?[\s\S]*Os seus lançamentos não mudam\./);
  await ctx.route("**/api/v2/orcamentos-categoria/apagar", (r) => r.fulfill(erroEnvelope(404, "not_found")));
  await page.getByRole("button", { name: "Apagar", exact: true }).click(); // o submit da confirmação
  await dialogo.waitFor({ state: "detached" });
  const post = pedidos.find((p) => p.m === "POST");
  await ctx.close();
  assert.equal(post.u.pathname, "/api/v2/orcamentos-categoria/apagar");
  assert.deepEqual(JSON.parse(post.corpo), { categoria: "alimentação" });
});

test("500 na leitura mostra \"Tentar de novo\" e ele recarrega", async () => {
  const p = await abrirPainel(browser, { espera: null });
  let n = 0;
  await p.ctx.route(URL_, (r) => ++n < 4 ? r.fulfill(erroEnvelope(500, "internal_error")) : r.fallback());
  await p.ir("/orcamentos-categoria");
  const botao = p.page.getByRole("button", { name: "Tentar de novo" });
  await botao.waitFor({ timeout: 20000 });
  assert.match(await painel(p.page).innerText(), /Não deu para carregar os orçamentos\./);
  await botao.click();
  await p.page.locator(".orc-linha").first().waitFor();
  await p.ctx.close();
});

test("1280 e 390: sem rolagem lateral e o dialog cabe", async () => {
  for (const width of [1280, 390]) {
    for (const orcamentosCategoria of ["padrao", "vazio", "gratis_no_limite"]) {
      const { ctx, page } = await aberto({ width, orcamentosCategoria });
      const ok = await semRolagem(page);
      let cabe = true;
      if (orcamentosCategoria === "padrao") {
        await abrirNovo(page);
        cabe = await page.locator("dialog.orc-form").evaluate((d) => { const r = d.getBoundingClientRect(); return r.left >= 0 && r.right <= window.innerWidth && r.top >= 0 && r.bottom <= window.innerHeight; });
      }
      await ctx.close();
      assert.ok(ok, `${width} ${orcamentosCategoria}`);
      assert.ok(cabe, `dialog ${width}`);
    }
  }
});

test("protótipo (DEMO): os 4 do seed, sem botões de escrita nem requisições", async () => {
  const { ctx, page, pedidos } = await abrir({ demo: true });
  await page.locator(".orc-linha").first().waitFor();
  const nomes = await page.locator(".orc-linha-topo b").allInnerTexts();
  const botoes = await painel(page).getByRole("button").count();
  await ctx.close();
  assert.deepEqual(nomes, ["alimentação", "transporte", "lazer", "saúde"]);
  assert.equal(botoes, 0);
  assert.deepEqual(pedidos, []);
});
