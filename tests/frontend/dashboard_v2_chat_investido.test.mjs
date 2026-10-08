// O assunto `investido` do chat do /painel (parts/InvestidoResposta.tsx): a resposta pronta
// do servidor (`GET /api/v2/investido`, regra em db/investido.py), sem IA.
//   · chip e texto livre ("aplicado", "INVESTIDO") pedem a rota uma vez e mostram total,
//     por tipo e por banco; a mensagem real não leva o selo "demonstração", a de exemplo leva;
//   · "investimentos" (sem "investid") segue no exemplo, sem requisição;
//   · erro, sem banco, motivos e parte sem saldo; nunca "R$ 0,00" no lugar de null;
//   · no protótipo, o valor semeado em main.tsx e nenhuma requisição.
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { PAINEL, PROTOTIPO, RAIZ, RESPOSTAS, exigeArtefatoEmDia, servir } from "./_painel.mjs";

let browser;
before(async () => {
  exigeArtefatoEmDia();
  browser = await chromium.launch();
});
after(() => browser?.close());

async function abrir({ width = 1440, investido = "com_banco", falha = false, demo = false, sse = false } = {}) {
  const ctx = await browser.newContext({ viewport: { width, height: width > 500 ? 900 : 844 }, reducedMotion: "reduce" });
  await servir(ctx, demo ? RAIZ : undefined, { investido });
  // Guarda o EventSource do painel para o teste disparar um aviso (o mesmo caminho do SSE).
  if (sse) await ctx.addInitScript(() => { const Real = window.EventSource; window.EventSource = class extends Real { constructor(...args) { super(...args); window.sseDoPainel = this; } }; });
  if (falha) {
    const e = RESPOSTAS.erros["500"];
    await ctx.route("**/api/v2/investido", (r) => r.fulfill({ status: e.status, json: e.body }));
  }
  const page = await ctx.newPage();
  const gets = [];
  page.on("request", (r) => { if (new URL(r.url()).pathname === "/api/v2/investido") gets.push(r.method()); });
  const erros = [];
  page.on("pageerror", (e) => erros.push(e.message));
  await page.goto(`${demo ? PROTOTIPO : PAINEL}#/piggy`);
  await page.locator("#page-title").waitFor();
  return { ctx, page, gets, erros };
}
const ultima = (page) => page.locator(".chat > .msg-piggy").last();
async function perguntar(page, texto) {
  const antes = await page.locator(".chat > .msg-piggy").count();
  await page.locator("#askbar-input").fill(texto);
  await page.locator("#askbar-input").press("Enter");
  await page.waitForFunction((n) => document.querySelectorAll(".chat > .msg-piggy").length > n, antes);
}
// O texto da última resposta depois de sair do "Calculando…".
async function resposta(page) {
  await page.waitForFunction(() => ![...document.querySelectorAll(".chat > .msg-piggy")].pop().querySelector(".msg-text").textContent.includes("Calculando"));
  return ultima(page).locator(".msg-text").innerText();
}

for (const width of [1440, 390]) {
  test(`${width}: o chip responde o total real, uma requisição, sem selo; a resposta de exemplo vizinha tem selo`, async () => {
    const { ctx, page, gets, erros } = await abrir({ width });
    await page.locator(".chat-empty .chat-follow button", { hasText: "Quanto eu tenho investido?" }).click();
    const texto = await resposta(page);
    await page.waitForTimeout(300); // o role=status monta a mesma resposta 60 ms depois
    const real = await page.locator('.chat > li[data-dado="real"]').evaluateAll((lis) => lis.map((li) => li.textContent));
    const estoura = await ultima(page).evaluate((li) => [li.scrollWidth > li.clientWidth, li.querySelector(".msg-text").scrollWidth > li.querySelector(".msg-text").clientWidth]);
    await perguntar(page, "oi");
    const exemplo = await ultima(page).evaluate((li) => [li.dataset.dado, li.querySelector(".msg-by").textContent]);
    const docLargo = await page.evaluate(() => document.scrollingElement.scrollWidth - document.scrollingElement.clientWidth);
    await ctx.close();
    assert.match(texto, /Você tem R\$ 57\.123,45 investidos nos bancos conectados\./);
    assert.match(texto, /Por tipo: Renda fixa R\$ 40\.000,00 · Ações R\$ 17\.123,45\./);
    assert.match(texto, /Por banco: Nubank R\$ 50\.000,00 · XP R\$ 7\.123,45\./);
    assert.deepEqual(gets, ["GET"]);
    assert.equal(real.length, 1);
    assert.doesNotMatch(real[0], /demonstração/);
    assert.deepEqual(exemplo, ["exemplo", "Piggy demonstração"]);
    assert.deepEqual(estoura, [false, false]);
    assert.equal(docLargo, 0);
    assert.deepEqual(erros, []);
  });
}

test("texto livre: 'aplicado' e 'INVESTIDO' vão para o real; 'investimentos' segue no exemplo, sem requisição", async () => {
  const { ctx, page, gets } = await abrir();
  await perguntar(page, "como estão meus investimentos?");
  const exemplo = await ultima(page).evaluate((li) => [li.dataset.dado, li.querySelector(".msg-by").textContent]);
  const antes = gets.length;
  await perguntar(page, "quanto tenho aplicado?");
  const aplicado = await resposta(page);
  await perguntar(page, "QUANTO TENHO INVESTIDO");
  const caixa = await resposta(page);
  const dados = await page.locator(".chat > li.msg-piggy").evaluateAll((lis) => lis.map((li) => li.dataset.dado));
  await ctx.close();
  assert.deepEqual(exemplo, ["exemplo", "Piggy demonstração"]);
  assert.equal(antes, 0);
  assert.match(aplicado, /^Você tem R\$ 57\.123,45/);
  assert.match(caixa, /^Você tem R\$ 57\.123,45/);
  assert.deepEqual(dados, ["exemplo", "real", "real"]);
});

test("erro 500: o texto de erro, nenhum número; perguntar de novo tenta outra vez", async () => {
  const { ctx, page } = await abrir({ falha: true });
  await perguntar(page, "quanto tenho investido?");
  await page.locator(".chat > .msg-piggy .msg-text", { hasText: "Não consegui buscar agora" }).waitFor({ timeout: 15000 });
  const t = await ultima(page).locator(".msg-text").innerText();
  await ctx.route("**/api/v2/investido", (r) => r.fulfill({ json: RESPOSTAS.investido.com_banco }));
  await perguntar(page, "quanto tenho investido?");
  const depois = await resposta(page);
  await ctx.close();
  assert.equal(t, "Não consegui buscar agora. Pergunta de novo daqui a pouco.");
  assert.doesNotMatch(t, /R\$/);
  assert.match(depois, /^Você tem R\$ 57\.123,45/);
});

test("sem banco, com motivos e com parte sem saldo", async () => {
  const leia = async (investido) => {
    const { ctx, page } = await abrir({ investido });
    await perguntar(page, "quanto tenho investido?");
    const t = await resposta(page);
    const selos = await ultima(page).locator(".msg-text .selo").allInnerTexts();
    await ctx.close();
    return [t, selos];
  };
  const [semBanco, selosSem] = await leia("sem_banco");
  assert.equal(semBanco, "Ainda não sei: conecte seu banco para eu ver seus investimentos.");
  assert.deepEqual(selosSem, []);
  const [pausada] = await leia("pausada");
  assert.match(pausada, /conecte seu banco/);
  const [motivos, selos] = await leia("com_motivos");
  assert.match(motivos, /^Você tem R\$ 1\.234,50/);
  assert.deepEqual(selos, ["banco desatualizado", "outra moeda"]);
  const [semSaldo] = await leia("parte_sem_saldo");
  assert.match(semSaldo, /Por tipo: Renda fixa R\$ 500,00 · Fundos de investimento: saldo não informado\./);
  assert.doesNotMatch(semSaldo, /R\$ 0,00/);
  // total null: sem_banco_conectado > saldo_ausente > nenhum_investimento > o resto (a mesma ordem do WhatsApp e da IA)
  assert.equal((await leia("todas_sem_saldo"))[0], "Ainda não sei: seu banco não informou o saldo dos seus investimentos.");
  assert.equal((await leia("nunca_lida"))[0], "Ainda não sei: ainda não consegui ler seus investimentos no banco.");
  const [nenhum, selosNenhum] = await leia("nenhum_investimento");
  assert.equal(nenhum, "Não encontrei investimentos nos seus bancos conectados.");
  assert.deepEqual(selosNenhum, []);
});

// Fora da /piggy a consulta fica inativa: o aviso do SSE só a marca como invalidada. Voltar
// tem de reler (o snapshot velho não fica na tela), e a pergunta seguinte já vê o novo.
test("aviso do SSE com a conversa fechada: voltar relê uma vez e a pergunta seguinte mostra o número novo", async () => {
  const { ctx, page, gets } = await abrir({ sse: true });
  await perguntar(page, "quanto tenho investido?");
  const antes = await resposta(page);
  await page.evaluate(() => { location.hash = "#/"; });
  await page.locator(".chat").waitFor({ state: "detached" });
  await ctx.route("**/api/v2/investido", (r) => r.fulfill({ json: RESPOSTAS.investido.com_motivos }));
  await page.evaluate(() => window.sseDoPainel.dispatchEvent(new MessageEvent("message", { data: '{"recurso":"open_finance"}' })));
  const inativa = gets.length;
  await page.evaluate(() => { location.hash = "#/piggy"; });
  await page.locator(".chat").waitFor();
  await perguntar(page, "quanto tenho investido?");
  await page.waitForFunction(() => document.querySelector(".chat > .msg-piggy:last-child .msg-text").textContent.includes("1.234,50"), null, { timeout: 5000 }).catch(() => {});
  const depois = await resposta(page);
  await page.waitForTimeout(300); // o role=status monta a mesma resposta 60 ms depois: não pode pedir de novo
  await ctx.close();
  assert.match(antes, /^Você tem R\$ 57\.123,45/);
  assert.equal(inativa, 1); // inativa: o aviso não relê sozinho
  assert.match(depois, /^Você tem R\$ 1\.234,50/);
  assert.deepEqual(gets, ["GET", "GET"]);
});

test("protótipo: o valor semeado, nenhuma requisição", async () => {
  const { ctx, page, gets } = await abrir({ demo: true });
  await perguntar(page, "quanto tenho investido?");
  const t = await resposta(page);
  const dado = await ultima(page).evaluate((li) => [li.dataset.dado, li.querySelector(".msg-by").textContent]);
  await ctx.close();
  assert.match(t, /^Você tem R\$ 23\.480,00 investidos nos bancos conectados\.\s*Por tipo: Renda fixa R\$ 20\.000,00 · Ações R\$ 3\.480,00\.\s*Por banco: Nubank R\$ 23\.480,00\./);
  assert.deepEqual(gets, []);
  assert.deepEqual(dado, ["exemplo", "Piggy"]); // protótipo: sem selo por mensagem e tudo é exemplo (como o Frame)
});
