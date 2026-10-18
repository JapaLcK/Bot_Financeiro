/**
 * Bordas da caixa "Receitas do extrato" e da seção "Receitas na previsão" (ReceitasDoBanco.tsx): foco, rolagem, anúncio e resposta perdida.
 * O caso comum está em dashboard_v2_previsao_receitas.test.mjs; o mock com estado, em _receitas.mjs.
 *
 *   · o foco do resultado nunca mexe na rolagem (o html tem `scroll-behavior: smooth`), no sucesso e na falha;
 *   · o foco dentro da caixa (o título, que não fica `disabled`) também vai para o resultado;
 *   · o Desfazer que esvazia a lista mantém a seção (sr-only): o anúncio sobrevive e o foco não cai no <body>;
 *   · resposta perdida: Não e Desfazer contam como gravados, sem alerta;
 *   · chave fora da lista relida NÃO conta como gravada (banco fora do ar também devolve lista vazia): alerta, sem anúncio;
 *   · o alerta de falha é um só para as duas instâncias: um sucesso em qualquer uma limpa o alerta velho da outra;
 *   · trocar o horizonte desmonta as caixas e o alerta não sobrevive (nem ao voltar ao horizonte de antes);
 *   · duas receitas de mesmo nome: cada resposta muda o DOM da região viva (senão o leitor de tela não repete).
 * Rodar:  npm run test:frontend   (artefato: `npm --prefix webapp run build`)
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { exigeArtefatoEmDia } from "./_painel.mjs";
import { ALFA, MARIA, JOAO, CAIXA, SECAO, PEND, RESP, abrir as abrirComBrowser, dito, alerta, alertas, painelVisivel, semPendente } from "./_receitas.mjs";

let browser;
before(async () => { exigeArtefatoEmDia(); browser = await chromium.launch(); });
after(() => browser?.close());

const abrir = (opts) => abrirComBrowser(browser, opts);
const comFoco = (page) => page.waitForFunction(() => document.activeElement?.classList.contains("rec-foco"), null, { timeout: 5000 });
const ERRO500 = { status: 500, json: { error: { code: "internal", message: "Internal Server Error" } } };

// No sucesso da ÚLTIMA pendente o painel do topo sai do fluxo (some): o saldo sobe a altura dele, ou o scroll anchoring a compensa. O que não pode é a página voltar ao topo.
for (const [nome, falha, some] of [["sucesso", undefined, true], ["falha (foco no aviso)", ERRO500, false]]) {
  test(`rolou a página com o POST no ar: o foco do resultado (${nome}) não leva a página de volta ao topo`, async () => {
    let soltar;
    const trava = new Promise((ok) => { soltar = ok; });
    const { ctx, page, posts } = await abrir({ width: 1280, post: async () => { await trava; return falha; } });
    await page.getByRole("button", { name: /^Sim/ }).click();
    while (!posts.length) await page.waitForTimeout(20);
    // Não se mede o `scrollY`: o scroll anchoring o corrige quando a caixa, acima da tela, muda de altura.
    // Mede-se onde o saldo (abaixo da caixa) está na janela: parado se a rolagem não foi mexida.
    const topo = () => page.evaluate(() => document.querySelector("#w-previsao").getBoundingClientRect().top);
    const caixaTopo = () => page.evaluate(() => document.querySelector("#w-previsao-receitas").parentElement.getBoundingClientRect().top);
    await page.evaluate(() => scrollTo({ top: 500, behavior: "instant" }));
    const y0 = await topo();
    const tirada = y0 - await caixaTopo(); // altura da caixa mais o vão até o saldo
    soltar();
    await comFoco(page);
    await page.waitForTimeout(1200); // o scroll-behavior: smooth levaria esse tempo até o topo
    const y1 = await topo();
    const rolou = await page.evaluate(() => scrollY);
    await ctx.close();
    assert.ok(rolou > 100, `a página voltou ao topo: scrollY ${rolou}`);
    assert.ok((some ? [y0, y0 - tirada] : [y0]).some((e) => Math.abs(y1 - e) < 5), `o saldo andou de ${y0} para ${y1} na janela (caixa tirada: ${some ? tirada : 0})`);
  });
}

test("foco no título da caixa durante o POST: o resultado ainda recebe o foco", async () => {
  let soltar;
  const trava = new Promise((ok) => { soltar = ok; });
  const { ctx, page, posts } = await abrir({ post: () => trava });
  await page.getByRole("button", { name: /^Sim/ }).click();
  while (!posts.length) await page.waitForTimeout(20);
  await page.locator(`${CAIXA} .rec-foco`).first().focus();
  const antes = await page.evaluate(() => document.activeElement?.tagName + "|" + document.activeElement?.closest("article")?.id);
  soltar();
  await comFoco(page);
  await ctx.close();
  assert.equal(antes, "P|w-previsao-receitas");
});

test("Desfazer que esvazia a lista: a seção vira sr-only mas fica, o anúncio sobrevive e o foco pousa no título dela (não no <body>)", async () => {
  const { ctx, page } = await abrir({ receitas: [{ chave: JOAO, nome: "Pix do João", valor: "250.50", dia: 20, status: "negada" }], post: () => ({ sumir: true }) });
  await page.getByRole("button", { name: /^Desfazer/ }).click();
  await comFoco(page);
  const foco = await page.evaluate(() => document.activeElement?.textContent);
  const falado = await dito(page);
  const aviso = await alerta(page);
  const visivel = await painelVisivel(page, SECAO);
  await ctx.close();
  assert.equal(foco, "Nenhuma receita respondida.");
  assert.equal(falado, "Pix do João: resposta desfeita.");
  assert.equal(aviso, "");
  assert.equal(visivel, false);
});

for (const [nome, clicar, post, esperado] of [
  ["Não", (p) => p.getByRole("button", { name: /^Não/ }).click(), { perdida: true }, "Empresa Alfa Ltda: recusada."],
  ["Desfazer", (p) => p.getByRole("button", { name: "Desfazer resposta: Maria Souza" }).click(), { perdida: true }, "Maria Souza: resposta desfeita."],
]) {
  test(`resposta perdida no ${nome}: a releitura mostra que pegou, sem aviso de erro`, async () => {
    const { ctx, page, posts } = await abrir({ post: () => post });
    await clicar(page);
    await comFoco(page);
    const aviso = await alerta(page);
    const falado = await dito(page);
    await ctx.close();
    assert.equal(posts.length, 1);
    assert.equal(aviso, "");
    assert.equal(falado, esperado);
  });
}

const ERRO404 = { status: 404, json: { error: { code: "not_found", message: "Not Found" } } };
const desfazerMaria = (p) => p.getByRole("button", { name: "Desfazer resposta: Maria Souza" }).click();
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
    const aviso = await alerta(page);
    const falado = await dito(page);
    const foco = await page.evaluate(() => document.activeElement?.getAttribute("role"));
    await ctx.close();
    assert.equal(aviso, "Não deu para salvar. Tente de novo.");
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

// O alerta de falha vale para as duas instâncias juntas: erro velho numa não pode sobreviver a um sucesso na outra.
const FALHA = "Não deu para salvar. Tente de novo.";
for (const [nome, primeiro, segundo, onde, limpou] of [
  ["Desfazer (fim) falha, depois um Sim (topo) dá certo", desfazerMaria, (p) => p.getByRole("button", { name: /^Sim/ }).click(), ["", FALHA], semPendente],
  ["Sim (topo) falha, depois um Desfazer (fim) dá certo", (p) => p.getByRole("button", { name: /^Sim/ }).click(), desfazerMaria, [FALHA, ""],
    (p) => p.locator(RESP).filter({ hasText: "Maria Souza" }).waitFor({ state: "detached" })],
]) {
  test(`${nome}: o alerta velho some`, async () => {
    const { ctx, page } = await abrir({ post: (_, n) => (n === 1 ? ERRO500 : undefined) });
    await primeiro(page);
    await comFoco(page);
    const falhou = await alertas(page);
    await segundo(page);
    await limpou(page);
    const depois = await alertas(page);
    await ctx.close();
    assert.deepEqual(falhou, onde);
    assert.deepEqual(depois, ["", ""]);
  });
}

// As caixas desmontam na troca de horizonte (a leitura do outro horizonte ainda não chegou): o alerta, que mora no pai, tem de morrer junto.
test("POST 500 no Sim: alerta; trocar para 60 dias e voltar a 30 não traz o alerta de volta", async () => {
  const { ctx, page } = await abrir({ post: (_, n) => (n === 1 ? ERRO500 : undefined) });
  await page.getByRole("button", { name: /^Sim/ }).click();
  await comFoco(page);
  const falhou = await alertas(page);
  const emDezembro = () => page.waitForFunction(() => document.querySelector("#w-previsao .hero-sub")?.textContent.includes("5 de dezembro"));
  await page.getByRole("radio", { name: "60 dias" }).click();
  await emDezembro();
  await page.locator(PEND).waitFor();
  const em60 = await alertas(page);
  await page.getByRole("radio", { name: "30 dias" }).click();
  await page.waitForFunction(() => !document.querySelector("#w-previsao .hero-sub")?.textContent.includes("5 de dezembro"));
  await page.locator(PEND).waitFor();
  const em30 = await alertas(page);
  await ctx.close();
  assert.deepEqual(falhou, [FALHA, ""]);
  assert.deepEqual(em60, ["", ""]);
  assert.deepEqual(em30, ["", ""]);
});
