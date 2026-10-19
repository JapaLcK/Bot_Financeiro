/**
 * /painel#/orcamento, "+ Gasto fixo" (POST /api/v2/gastos-fixos): o botão abre o formulário, o envio manda o corpo certo
 * com CSRF, o 422 aparece no formulário (rascunho mantido), o sucesso fecha e avisa, o 403 pro_required convida ao plano,
 * o protótipo (DEMO) não tem o botão e 375px/1280px não rolam para o lado com o formulário aberto.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { RESPOSTAS, abrirPainel, exigeArtefatoEmDia } from "./_painel.mjs";

let browser;
before(async () => { exigeArtefatoEmDia(); browser = await chromium.launch(); });
after(() => browser?.close());

const CRIADO = { id: 7, nome: "Aluguel", valor: "1500.50", dia: 5, categoria: "Moradia", forma: "conta" };

async function aberto(opts = {}, resposta = { json: CRIADO }) {
  const p = await abrirPainel(browser, { espera: null, ...opts });
  const posts = [];
  await p.ctx.route("**/api/v2/gastos-fixos", (r) => { posts.push({ h: r.request().headers(), corpo: r.request().postData() }); return r.fulfill(resposta); });
  await p.ir("/orcamento");
  await p.page.locator(".orc-pote").first().waitFor();
  return { ...p, posts };
}
const abrirForm = async (page) => {
  await page.getByRole("button", { name: "+ Gasto fixo" }).click();
  await page.locator("dialog.orc-form").waitFor();
};
const preenche = async (page, { nome = "Aluguel", valor = "1500,50", dia = "5" } = {}) => {
  await page.locator('input[name="nome"]').fill(nome);
  await page.locator('input[name="valor"]').fill(valor);
  await page.locator('input[name="dia"]').fill(dia);
};
const cadastrar = (page) => page.getByRole("button", { name: /Cadastrar|Salvando/ });

test("o botão abre o formulário com a explicação e as categorias do usuário", async () => {
  const { ctx, page } = await aberto();
  await abrirForm(page);
  const dlg = page.locator("dialog.orc-form");
  const t = await dlg.innerText();
  const opcoes = await dlg.locator('select[name="categoria"] option').allInnerTexts();
  await ctx.close();
  assert.match(t, /Novo gasto fixo/);
  assert.match(t, /entra na Previsão e só conta no pote quando o pagamento vira lançamento/);
  assert.deepEqual(opcoes, RESPOSTAS.categorias.padrao.categorias.map((c) => c.nome));
});

test("envio manda o corpo certo com CSRF, fecha e avisa", async () => {
  const { ctx, page, posts } = await aberto();
  await abrirForm(page);
  await preenche(page);
  await page.locator('select[name="categoria"]').selectOption({ index: 1 });
  const cat = RESPOSTAS.categorias.padrao.categorias[1].nome;
  await cadastrar(page).click();
  await page.locator("dialog.orc-form").waitFor({ state: "detached" });
  const aviso = await page.getByRole("status").filter({ hasText: "Gasto fixo cadastrado" }).innerText();
  await ctx.close();
  assert.equal(posts.length, 1);
  assert.equal(posts[0].h["x-csrf-token"], "tok-123");
  assert.deepEqual(JSON.parse(posts[0].corpo), { nome: "Aluguel", valor: "1500.50", dia: 5, categoria: cat, forma: "conta" });
  assert.match(aviso, /Gasto fixo cadastrado\. Ele entra na Previsão\./);
});

test("validação no cliente: nome, valor e dia inválidos não mandam POST", async () => {
  const { ctx, page, posts } = await aberto();
  await abrirForm(page);
  const alerta = page.locator("dialog.orc-form").getByRole("alert");
  await preenche(page, { nome: "  " });
  await cadastrar(page).click();
  assert.match(await alerta.innerText(), /Dê um nome/);
  await preenche(page, { valor: "0" });
  await cadastrar(page).click();
  assert.match(await alerta.innerText(), /Use um valor maior que zero/);
  await preenche(page, { valor: "10", dia: "32" });
  await cadastrar(page).click();
  assert.match(await alerta.innerText(), /dia do vencimento vai de 1 a 31/);
  await ctx.close();
  assert.equal(posts.length, 0);
});

test("422 do servidor aparece no formulário, mantém o rascunho e duplo clique manda um POST só", async () => {
  const erro = { status: 422, json: { error: { code: "validation_error", message: "x", details: [{ loc: ["body", "categoria"], msg: "x", type: "value_error" }] } } };
  const { ctx, page, posts } = await aberto({}, erro);
  await abrirForm(page);
  await preenche(page, { nome: "Internet" });
  await cadastrar(page).dblclick();
  await page.locator("dialog.orc-form").getByRole("alert").filter({ hasText: "Escolha uma das suas categorias." }).waitFor();
  const nome = await page.locator('input[name="nome"]').inputValue();
  const aberto2 = await page.locator("dialog.orc-form").count();
  await ctx.close();
  assert.equal(posts.length, 1);
  assert.equal(nome, "Internet");
  assert.equal(aberto2, 1);
});

test("403 pro_required mostra o convite do plano no formulário", async () => {
  const e = RESPOSTAS.erros["403_pro_required"];
  const { ctx, page } = await aberto({}, { status: e.status, json: e.body });
  await abrirForm(page);
  await preenche(page);
  await cadastrar(page).click();
  const dlg = page.locator("dialog.orc-form");
  await dlg.getByRole("link", { name: "Ver planos" }).waitFor();
  const t = await dlg.innerText();
  await ctx.close();
  assert.match(t, /Gasto fixo é do Essencial para cima/);
});

test("protótipo (DEMO): sem o botão de gasto fixo", async () => {
  const { ctx, page, posts } = await aberto({ demo: true });
  const n = await page.getByRole("button", { name: "+ Gasto fixo" }).count();
  await ctx.close();
  assert.equal(n, 0);
  assert.deepEqual(posts, []);
});

for (const width of [375, 1280]) {
  test(`${width}px com o formulário aberto: caixa dentro da tela e sem rolagem lateral`, async () => {
    const { ctx, page } = await aberto({ width });
    await abrirForm(page);
    await preenche(page, { nome: "Um nome de gasto fixo bem comprido para testar a quebra de linha no celular" });
    const ok = await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth);
    const caixa = await page.locator("dialog.orc-form").boundingBox();
    if (process.env.PB_SHOT) await page.screenshot({ path: `${process.env.PB_SHOT}-gasto-fixo-${width}.png` });
    await ctx.close();
    assert.ok(ok);
    assert.ok(caixa.x >= 0 && caixa.x + caixa.width <= width);
  });
}
