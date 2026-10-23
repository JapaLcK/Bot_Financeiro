// O card `simulador` da IA no chat do /painel, modo com controles (webapp: parts/cardIa/simulador.tsx, naConta.ts). Soltar um controle manda UM POST /api/v2/simulador/habitos;
// o servidor lê a estimativa da SESSÃO (a categoria `mercado` existe em toda conta), então a conta é conferida ANTES do POST e DEPOIS dele (isolamento entre usuários).
// S1 a S9 do plano: positivo, troca antes e durante, 500, erros de plano/sessão, 422, número velho, prazo e duplo soltar. A rota é DO TESTE (_chat_ia_sim.mjs).
// S10 a S18: dashboard_v2_chat_ia_cards_simulador_b.test.mjs. Cada guarda foi provada vermelha com uma mutação do código que ela cobre (a tabela está no relato do PR).
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { encerrar, iniciar } from "./_chat_ia.mjs";
import { GENERICO, MUDOU, SEM_PLANO, SESSAO, ate, prepararSim } from "./_chat_ia_sim.mjs";

before(iniciar);
after(encerrar);
const CORPO30 = { cortes: [{ categoria: "mercado", percentual: 30 }] };

test("S1 positivo: soltar 30 em Mercado manda UM POST (corpo exato) com a conta conferida antes e depois; o resultado do servidor fica DENTRO do card, com o título sem o mês", async () => {
  const { page, t, card, soltar, erros } = await prepararSim({}, { mes: "2026-10" });
  assert.equal(await card.locator("h2").innerText(), "Simulador", "o título do app não leva o mês");
  assert.equal(await card.locator("input[type=range]").count(), 3, "um controle por categoria do bloco");
  t.ordem.length = 0;
  await soltar("Mercado", 30);
  await card.locator(".sim-facts").waitFor();
  assert.deepEqual(t.corpos, [CORPO30]);
  const iPost = t.ordem.indexOf("POST");
  assert.ok(iPost > 0 && t.ordem.slice(0, iPost).every((x) => x === "perfil"), "conta lida antes do POST");
  assert.deepEqual(t.ordem.slice(iPost), ["POST", "perfil"], "conta relida logo depois do POST");
  assert.match(await card.locator(".sim-facts").innerText(), /Sobra por mês\s*R\$ 54,00/);
  assert.equal(await card.locator("a, dialog").count(), 0, "sem link e sem folha");
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  assert.deepEqual(erros, []);
  await page.context().close();
});

test("S2 troca de conta ANTES de soltar (a aba ainda não sabe): nenhum POST sai e o card de A some", async () => {
  const { page, t, soltar, conta, cardSumiu } = await prepararSim();
  conta.uid = 2; // a sessão virou B noutra aba; esta não recebeu o foco
  await soltar("Mercado", 30);
  await cardSumiu();
  assert.deepEqual(t.corpos, [], "nenhum POST saiu em nome de B");
  await page.context().close();
});

test("S3 troca DURANTE o POST: a resposta (marcada 987,65) nunca é desenhada e o card de A some", async () => {
  const { page, t, soltar, conta, vistos, cardSumiu } = await prepararSim();
  t.modo = "preso";
  await soltar("Mercado", 30);
  await ate(() => t.presos[0]);
  conta.uid = 2;
  t.soltar(0, "987.65");
  await cardSumiu();
  await page.waitForTimeout(300);
  assert.ok(!(await vistos()).includes("987,65"), "o 987,65 nunca entrou no DOM, nem por um quadro");
  await page.context().close();
});

test("S4 500: o texto genérico, sem número, exatamente UM POST (soltar de novo é a retentativa); outro valor com 200 mostra os números", async () => {
  const { page, t, card, soltar, sai } = await prepararSim();
  t.modo = "500";
  await soltar("Mercado", 30);
  await sai().getByRole("alert").waitFor({ timeout: 12000 });
  assert.equal(await sai().innerText(), GENERICO);
  assert.equal(await card.locator(".sim-facts").count(), 0, "erro nunca fica ao lado de número");
  assert.equal(await card.locator("input[type=range]").count(), 3, "os controles de pé");
  assert.equal(t.corpos.length, 1, "5xx não repete sozinho");
  t.modo = "ok";
  await soltar("Mercado", 50);
  await card.locator(".sim-facts").waitFor();
  assert.match(await card.locator(".sim-facts").innerText(), /R\$ 90,00/);
  assert.equal(t.corpos.length, 2);
  await page.context().close();
});

test("S5 plano e sessão: 403 pro_required é \"indisponível no seu plano\" (sem link); 403 sem código e 401 são a sessão", async () => {
  for (const [modo, texto] of [["403pro", SEM_PLANO], ["403", SESSAO], ["401", SESSAO]]) {
    const { page, t, card, soltar, sai } = await prepararSim();
    t.modo = modo;
    await soltar("Mercado", 30);
    await sai().getByRole("alert").waitFor();
    assert.equal(await sai().innerText(), texto, modo);
    assert.equal(await card.locator("a, button").filter({ hasText: /preço|plano|Pro/ }).count(), 0, `${modo}: sem link nem botão de plano`);
    assert.equal(await card.locator(".sim-facts").count(), 0);
    assert.equal(t.corpos.length, 1, `${modo}: 4xx não repete`);
    await page.context().close();
  }
});

test("S6 422 categoria_fora_da_estimativa: \"ajuste de novo\", sem número e SEM reler a previsão (o bloco é foto); 422 de outro tipo é o genérico", async () => {
  const a = await prepararSim();
  a.t.modo = "422";
  await a.soltar("Mercado", 30);
  await a.sai().getByRole("alert").waitFor();
  assert.equal(await a.sai().innerText(), MUDOU);
  assert.equal(await a.card.locator(".sim-facts").count(), 0);
  await a.page.waitForTimeout(600);
  assert.equal(a.t.gets, 0, "nenhum GET /api/v2/previsao depois do 422");
  assert.equal(a.t.corpos.length, 1);
  await a.page.context().close();
  const b = await prepararSim();
  b.t.modo = "422outro";
  await b.soltar("Mercado", 30);
  await b.sai().getByRole("alert").waitFor();
  assert.equal(await b.sai().innerText(), GENERICO);
  await b.page.context().close();
});

test("S7 número velho: soltar 50 com o POST preso tira o resultado do 30 na hora e mostra \"Calculando…\" (aria-busy); a resposta do 50 o substitui", async () => {
  const { page, t, card, soltar, sai } = await prepararSim();
  await soltar("Mercado", 30);
  await card.locator(".sim-facts").waitFor();
  assert.match(await sai().innerText(), /R\$ 54,00/);
  t.modos[1] = "preso";
  await soltar("Mercado", 50);
  await ate(() => t.presos[1]);
  const txt = await sai().innerText();
  assert.ok(!txt.includes("54,00"), `o número do corte antigo ao lado do novo: ${txt}`);
  assert.equal(txt, "Calculando…");
  assert.equal(await sai().locator("[aria-busy=true]").count(), 1);
  t.soltar(1, "90.00");
  await card.locator(".sim-facts").waitFor();
  assert.match(await sai().innerText(), /Sobra por mês\s*R\$ 90,00/);
  await page.context().close();
});

test("S8 prazo de 15 s: POST pendurado carrega até 14 s e vira o genérico aos 16 s; com o perfil pendurado (antes do POST) nenhum POST sai e é o genérico", async () => {
  const a = await prepararSim({ relogio: "instalado" });
  a.t.modo = "pendurado";
  await a.soltar("Mercado", 30);
  await a.sai().getByText("Calculando…").waitFor();
  await a.page.clock.runFor(14_000);
  assert.equal(await a.sai().innerText(), "Calculando…", "aos 14 s ainda carrega");
  await a.page.clock.runFor(2_000);
  await a.sai().getByRole("alert").waitFor();
  assert.equal(await a.sai().innerText(), GENERICO);
  assert.equal(a.t.corpos.length, 1, "o corte do prazo não repete");
  await a.page.context().close();
  const b = await prepararSim({ relogio: "instalado" });
  b.t.perfil = "pendurado";
  await b.soltar("Mercado", 30);
  await b.sai().getByText("Calculando…").waitFor();
  await b.page.clock.runFor(14_000);
  assert.equal(await b.sai().innerText(), "Calculando…");
  await b.page.clock.runFor(2_000);
  await b.sai().getByRole("alert").waitFor();
  assert.equal(await b.sai().innerText(), GENERICO);
  assert.deepEqual(b.t.corpos, [], "o prazo cobre a conferência de antes: nenhum POST");
  await b.page.context().close();
});

test("S9 duplo soltar: o 1º POST (30) fica preso, o 2º (50) responde e fica; a resposta tardia do 1º (111,11) nunca é desenhada e o pedido dele foi ABORTADO", async () => {
  const { page, t, card, soltar, sai, vistos } = await prepararSim();
  t.modos[0] = "preso";
  await soltar("Mercado", 30);
  await ate(() => t.presos[0]);
  await soltar("Mercado", 50);
  await card.locator(".sim-facts").waitFor();
  assert.match(await sai().innerText(), /Sobra por mês\s*R\$ 90,00/);
  await ate(() => t.falhas.length > 0);
  assert.deepEqual(t.falhas, ["net::ERR_ABORTED"], "o POST do 30 foi cancelado, não deixado no ar");
  t.soltar(0, "111.11");
  await page.waitForTimeout(300);
  assert.ok(!(await vistos()).includes("111,11"), "a resposta do 1º nunca entrou no DOM");
  assert.match(await sai().innerText(), /R\$ 90,00/);
  assert.deepEqual(t.corpos.map((c) => c.cortes[0].percentual), [30, 50]);
  await page.context().close();
});
