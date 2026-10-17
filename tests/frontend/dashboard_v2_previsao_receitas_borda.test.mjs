/**
 * Bordas da caixa "Receitas do extrato" (ReceitasDoBanco.tsx): foco, rolagem, anúncio e resposta perdida.
 * O caso comum está em dashboard_v2_previsao_receitas.test.mjs; o mock com estado, em _receitas.mjs.
 *
 *   · o foco do resultado nunca mexe na rolagem (o html tem `scroll-behavior: smooth`), no sucesso e na falha;
 *   · o foco dentro da caixa (um `summary`, que não fica `disabled`) também vai para o resultado;
 *   · o Desfazer que esvazia a lista mantém a caixa: o anúncio sobrevive e o foco não cai no <body>;
 *   · resposta perdida: Não e Desfazer contam como gravados, sem alerta;
 *   · chave fora da lista relida NÃO conta como gravada (banco fora do ar também devolve lista vazia): alerta, sem anúncio;
 *   · duas receitas de mesmo nome: cada resposta muda o DOM da região viva (senão o leitor de tela não repete).
 * Rodar:  npm run test:frontend   (artefato: `npm --prefix webapp run build`)
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { exigeArtefatoEmDia } from "./_painel.mjs";
import { ALFA, MARIA, JOAO, CAIXA, PEND, abrir as abrirComBrowser, texto, abrirRespondidas, dito, semPendente } from "./_receitas.mjs";

let browser;
before(async () => { exigeArtefatoEmDia(); browser = await chromium.launch(); });
after(() => browser?.close());

const abrir = (opts) => abrirComBrowser(browser, opts);
const comFoco = (page) => page.waitForFunction(() => document.activeElement?.classList.contains("rec-foco"), null, { timeout: 5000 });
const ERRO500 = { status: 500, json: { error: { code: "internal", message: "Internal Server Error" } } };

for (const [nome, falha] of [["sucesso", undefined], ["falha (foco no aviso)", ERRO500]]) {
  test(`rolou a página com o POST no ar: o foco do resultado (${nome}) não leva a página de volta ao topo`, async () => {
    let soltar;
    const trava = new Promise((ok) => { soltar = ok; });
    const { ctx, page, posts } = await abrir({ width: 1280, post: async () => { await trava; return falha; } });
    await page.getByRole("button", { name: /^Sim/ }).click();
    while (!posts.length) await page.waitForTimeout(20);
    // Não se mede o `scrollY`: o scroll anchoring o corrige quando a caixa, acima da tela, muda de altura.
    // Mede-se onde o saldo (abaixo da caixa) está na janela: parado se a rolagem não foi mexida.
    const topo = () => page.evaluate(() => document.querySelector("#w-previsao").getBoundingClientRect().top);
    await page.evaluate(() => scrollTo({ top: 500, behavior: "instant" }));
    const y0 = await topo();
    soltar();
    await comFoco(page);
    await page.waitForTimeout(1200); // o scroll-behavior: smooth levaria esse tempo até o topo
    const y1 = await topo();
    const rolou = await page.evaluate(() => scrollY);
    await ctx.close();
    assert.ok(rolou > 100, `a página voltou ao topo: scrollY ${rolou}`);
    assert.ok(Math.abs(y1 - y0) < 5, `o saldo andou de ${y0} para ${y1} na janela`);
  });
}

test("foco no `summary` da caixa durante o POST: o resultado ainda recebe o foco", async () => {
  let soltar;
  const trava = new Promise((ok) => { soltar = ok; });
  const { ctx, page, posts } = await abrir({ post: () => trava });
  await page.getByRole("button", { name: /^Sim/ }).click();
  while (!posts.length) await page.waitForTimeout(20);
  await page.locator(`${CAIXA} summary`).click();
  const antes = await page.evaluate(() => document.activeElement?.tagName);
  soltar();
  await comFoco(page);
  await ctx.close();
  assert.equal(antes, "SUMMARY");
});

test("Desfazer que esvazia a lista: a caixa fica, o anúncio sobrevive e o foco pousa no título (não no <body>)", async () => {
  const { ctx, page } = await abrir({ receitas: [{ chave: JOAO, nome: "Pix do João", valor: "250.50", dia: 20, status: "negada" }], post: () => ({ sumir: true }) });
  await abrirRespondidas(page);
  await page.getByRole("button", { name: /^Desfazer/ }).click();
  await comFoco(page);
  const foco = await page.evaluate(() => document.activeElement?.textContent);
  const falado = await dito(page);
  const alerta = (await texto(page, `${CAIXA} [role=alert]`))[0];
  await ctx.close();
  assert.equal(foco, "Nenhuma receita a confirmar.");
  assert.equal(falado, "Pix do João: resposta desfeita.");
  assert.equal(alerta, "");
});

for (const [nome, clicar, post, esperado] of [
  ["Não", (p) => p.getByRole("button", { name: /^Não/ }).click(), { perdida: true }, "Empresa Alfa Ltda: recusada."],
  ["Desfazer", async (p) => { await abrirRespondidas(p); await p.getByRole("button", { name: "Desfazer resposta: Maria Souza" }).click(); }, { perdida: true }, "Maria Souza: resposta desfeita."],
]) {
  test(`resposta perdida no ${nome}: a releitura mostra que pegou, sem aviso de erro`, async () => {
    const { ctx, page, posts } = await abrir({ post: () => post });
    await clicar(page);
    await comFoco(page);
    const alerta = (await texto(page, `${CAIXA} [role=alert]`))[0];
    const falado = await dito(page);
    await ctx.close();
    assert.equal(posts.length, 1);
    assert.equal(alerta, "");
    assert.equal(falado, esperado);
  });
}

const ERRO404 = { status: 404, json: { error: { code: "not_found", message: "Not Found" } } };
const desfazerMaria = async (p) => { await abrirRespondidas(p); await p.getByRole("button", { name: "Desfazer resposta: Maria Souza" }).click(); };
const MARIA_SO = [{ chave: MARIA, nome: "Maria Souza", valor: "1500.00", dia: 10, status: "confirmada" }];
const ALFA_SO = [{ chave: ALFA, nome: "Empresa Alfa Ltda", valor: "4000.00", dia: 4, status: "pendente" }];
for (const [nome, receitas, clicar, post] of [
  ["Desfazer com a resposta perdida e a linha sumida da lista", undefined, desfazerMaria, { perdida: true, sumir: true }],
  ["Desfazer com POST 500 e a chave fora da lista relida", undefined, desfazerMaria, { ...ERRO500, sumir: true }],
  ["Desfazer com POST 500 e a lista relida vazia (banco fora do ar)", MARIA_SO, desfazerMaria, { ...ERRO500, sumir: true }],
  ["Sim com POST 404 porque a pendente sumiu da lista", undefined, (p) => p.getByRole("button", { name: /^Sim/ }).click(), { ...ERRO404, sumir: true }],
  ["Sim na única pendente, POST 404 e a lista relida vazia (a caixa fica, com o alerta)", ALFA_SO, (p) => p.getByRole("button", { name: /^Sim/ }).click(), { ...ERRO404, sumir: true }],
]) {
  test(`${nome}: alerta, sem anúncio de sucesso, foco no aviso`, async () => {
    const { ctx, page } = await abrir({ receitas, post: () => post });
    await clicar(page);
    await comFoco(page);
    const alerta = (await texto(page, `${CAIXA} [role=alert]`))[0];
    const falado = await dito(page);
    const foco = await page.evaluate(() => document.activeElement?.getAttribute("role"));
    await ctx.close();
    assert.equal(alerta, "Não deu para salvar. Tente de novo.");
    assert.equal(falado, "");
    assert.equal(foco, "alert");
  });
}

test("duas receitas de mesmo nome: cada Sim muda o DOM da região viva (o 2º é reanunciado)", async () => {
  const mk = (chave, valor, dia) => ({ chave, nome: "Empresa X", valor, dia, status: "pendente" });
  const { ctx, page } = await abrir({ receitas: [mk("extrato#00000000000000a1", "900.00", 5), mk("extrato#00000000000000a2", "800.00", 6)] });
  await page.evaluate(() => {
    window.__mut = 0;
    new MutationObserver((l) => { window.__mut += l.length; }).observe(document.querySelector("#w-previsao-receitas [role=status]"), { childList: true, characterData: true, subtree: true });
  });
  const sim = page.getByRole("button", { name: /^Sim/ }).first();
  await sim.click();
  await page.locator(PEND).nth(1).waitFor({ state: "detached" });
  const a = await page.evaluate(() => window.__mut);
  await sim.click();
  await semPendente(page);
  const b = await page.evaluate(() => window.__mut);
  const falado = await dito(page);
  await ctx.close();
  assert.ok(a > 0, `1º Sim sem mutação: ${a}`);
  assert.ok(b > a, `2º Sim não mexeu no DOM: ${a} -> ${b}`);
  assert.equal(falado, "Empresa X: confirmada.");
});
