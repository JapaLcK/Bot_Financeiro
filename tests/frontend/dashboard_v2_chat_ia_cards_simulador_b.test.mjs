// O card `simulador` da IA no chat do /painel (continuação de dashboard_v2_chat_ia_cards_simulador.test.mjs): S10 a S18 do plano. Mesmo soltar, mesmo mesmo valor, teclado, aviso do SSE,
// troca percebida pelo foco, o modo do corte pedido no chat (sem controle e sem POST), contrato, "Zerar simulação", card sem dono e visual em 1440 e 390.
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { RESPOSTAS, SEM_CARD, encerrar, iniciar, trocarConta } from "./_chat_ia.mjs";
import { GENERICO, PREV, ate, mover, prepararSim, respostaDe } from "./_chat_ia_sim.mjs";

before(iniciar);
after(encerrar);
const CORTE = respostaDe({ cortes: [{ categoria: "mercado", percentual: 30 }, { categoria: "lazer", percentual: 10 }] }); // o que o servidor manda quando a pessoa PEDIU o corte

test("S10 soltar o MESMO valor duas vezes não manda POST: o próximo POST que sai (outro controle) é o 2º, nunca um 2º do mesmo corte", async () => {
  const { page, t, card, soltar } = await prepararSim();
  await soltar("Mercado", 30);
  await card.locator(".sim-facts").waitFor();
  await soltar("Mercado", 30);
  await page.waitForTimeout(1000); // mais que a espera (300 ms) e as leituras: um POST do soltar repetido sairia aqui, antes de o Lazer abortá-lo
  await soltar("Lazer", 10);
  await page.waitForFunction(() => /R\$ 60,00/.test(document.querySelector(".card-ia .sim-facts")?.textContent ?? ""), null, { timeout: 8000 });
  assert.deepEqual(t.corpos.map((c) => c.cortes.length), [1, 2]);
  await page.context().close();
});

test("S11 teclado: 3x ArrowRight no controle: o último POST leva 30 e a tela mostra a resposta do 30 (relata quantos POSTs e leituras de perfil saíram)", async (tc) => {
  const { page, t, card, alavanca } = await prepararSim();
  await alavanca("Mercado").focus();
  t.ordem.length = 0;
  for (let i = 0; i < 3; i++) await page.keyboard.press("ArrowRight");
  await page.waitForFunction(() => /R\$ 54,00/.test(document.querySelector(".card-ia .sim-facts")?.textContent ?? ""), null, { timeout: 8000 });
  await page.waitForTimeout(300);
  assert.deepEqual(t.corpos.at(-1), { cortes: [{ categoria: "mercado", percentual: 30 }] });
  assert.match(await card.locator(".sim-facts").innerText(), /Sobra por mês\s*R\$ 54,00/);
  tc.diagnostic(`3 teclas: ${t.corpos.length} POST(s) e ${t.ordem.filter((x) => x === "perfil").length} leitura(s) de perfil`);
  await page.context().close();
});

test("S12 o aviso do SSE com o resultado na tela NÃO refaz o POST (foto); o controle positivo é o /me, que o mesmo aviso relê", async () => {
  const a = await prepararSim({ sse: true });
  await a.soltar("Mercado", 30);
  await a.card.locator(".sim-facts").waitFor();
  const me = a.t.me;
  await ate(() => a.t.soltaSse);
  a.t.soltaSse();
  await ate(() => a.t.me > me); // positivo: o aviso invalidou o resto
  await a.page.waitForTimeout(1000);
  assert.equal(a.t.corpos.length, 1, "nenhum POST novo");
  assert.match(await a.card.locator(".sim-facts").innerText(), /R\$ 54,00/, "a foto fica");
  await a.page.context().close();
});

test("S13 troca de conta percebida pelo foco com o resultado na tela: o card some e NENHUM POST novo sai com a sessão de B", async () => {
  const { page, t, card, conta, soltar, cardSumiu } = await prepararSim();
  await soltar("Mercado", 30);
  await card.locator(".sim-facts").waitFor();
  await trocarConta(page, conta, 2);
  await cardSumiu();
  await page.waitForTimeout(500);
  assert.equal(t.corpos.length, 1, "nenhuma releitura do corte de A em nome de B");
  await page.context().close();
});

test("S14 modo do corte pedido no chat (`simulador_habitos` no bloco): sem controle e sem botão; o resultado e uma linha por corte; com valor em reais o texto é o do widget", async () => {
  const a = await prepararSim({}, { recursos: { previsao: PREV, simulador_habitos: CORTE } });
  assert.equal(await a.card.locator("input[type=range]").count(), 0);
  assert.equal(await a.card.locator("button").count(), 0, "nada a apertar");
  const txt = await a.card.innerText();
  assert.match(txt, /Sobra por mês\s*R\$ 60,00/);
  assert.match(txt, /Mercado: −30% = R\$ 54,00 por mês/);
  assert.match(txt, /Lazer: −10% = R\$ 6,00 por mês/);
  assert.equal(await a.card.locator("svg[role=img]").count(), 1, "a curva com e sem corte");
  await a.page.context().close();
  const pedido = { ...CORTE, cortes: [{ ...CORTE.cortes[0], percentual: 28, economia_mensal: "50.40", valor_pedido: "50.00" }] };
  const b = await prepararSim({}, { recursos: { previsao: PREV, simulador_habitos: pedido } });
  assert.match(await b.card.innerText(), /Mercado: −28% = R\$ 50,40 por mês/);
  await b.page.context().close();
});

const MALFORMADOS = [["economia_mensal que não é dinheiro", (d) => ({ ...d, economia_mensal: "abc" })], ["economia_mensal infinita", (d) => ({ ...d, economia_mensal: "1e999" })],
  ["trajetoria que não é lista", (d) => ({ ...d, atual: { ...d.atual, trajetoria: {} } })], ["pior_dia.saldo que não é texto", (d) => ({ ...d, com_corte: { ...d.com_corte, pior_dia: { data: "2026-10-10", saldo: 1 } } })],
  ["cortes que não é lista", (d) => ({ ...d, cortes: "x" })], ["data de ponto que não existe", (d) => ({ ...d, atual: { ...d.atual, trajetoria: [{ data: "2026-02-30", saldo: "1.00" }] } })]];
for (const [nome, estraga] of MALFORMADOS) {
  test(`S15 contrato: simulador_habitos do bloco com ${nome} vira o marcador e a página segue de pé`, async () => {
    const a = await prepararSim({}, { recursos: { previsao: PREV, simulador_habitos: estraga(CORTE) } });
    assert.equal(await a.page.getByText(SEM_CARD).count(), 1);
    assert.equal(await a.card.count(), 0);
    assert.equal(await a.page.locator("#askbar-input").isVisible(), true);
    assert.deepEqual(a.erros, []);
    await a.page.context().close();
  });
}
test("S15b contrato: a RESPOSTA do POST malformada é o genérico e os controles ficam de pé (o card não vira marcador); previsão do bloco malformada é o marcador", async () => {
  const a = await prepararSim();
  a.t.modo = "malformado";
  await a.soltar("Mercado", 30);
  await a.sai().getByRole("alert").waitFor();
  assert.equal(await a.sai().innerText(), GENERICO);
  assert.equal(await a.card.locator("input[type=range]").count(), 3);
  assert.equal(await a.card.locator(".sim-facts").count(), 0);
  await a.page.context().close();
  const est = (categorias) => ({ ...PREV, estimativa_variavel: { ...PREV.estimativa_variavel, categorias } });
  for (const categorias of [[{ chave: "", nome: "x", despesa_mensal: "1.00" }], [{ chave: "a", nome: 1, despesa_mensal: "1.00" }], [{ chave: "a", nome: "x", despesa_mensal: "NaN" }], "x"]) {
    const b = await prepararSim({}, { recursos: { previsao: est(categorias) } });
    assert.equal(await b.page.getByText(SEM_CARD).count(), 1, JSON.stringify(categorias));
    await b.page.context().close();
  }
});

test("S16 \"Zerar simulação\": sem resultado, controles a 0, nenhum POST novo; com um POST em voo, ele é abortado e nada dele é desenhado", async () => {
  const a = await prepararSim();
  assert.equal(await a.card.getByRole("button", { name: "Zerar simulação" }).count(), 0, "sem corte, sem botão");
  await a.soltar("Mercado", 30);
  await a.card.locator(".sim-facts").waitFor();
  await a.card.getByRole("button", { name: "Zerar simulação" }).click();
  assert.equal(await a.card.locator(".sim-facts").count(), 0);
  assert.deepEqual(await a.card.locator("input[type=range]").evaluateAll((rs) => rs.map((r) => r.value)), ["0", "0", "0"]);
  assert.equal(await a.card.getByRole("button", { name: "Zerar simulação" }).count(), 0);
  await a.page.waitForTimeout(1000); // mais que a espera (300 ms) mais as leituras do perfil: um POST do estado zerado já teria saído
  assert.equal(a.t.corpos.length, 1);
  await a.page.context().close();
  const b = await prepararSim();
  b.t.modo = "preso";
  await b.soltar("Mercado", 50);
  await ate(() => b.t.presos[0]);
  await b.card.getByRole("button", { name: "Zerar simulação" }).click();
  await ate(() => b.t.falhas.length > 0);
  assert.deepEqual(b.t.falhas, ["net::ERR_ABORTED"]);
  b.t.soltar(0, "222.22");
  await b.page.waitForTimeout(300);
  assert.ok(!(await b.vistos()).includes("222,22"), "o resultado do POST abortado nunca entrou no DOM");
  await b.page.context().close();
});

test("S17 card sem dono (uid que não é inteiro positivo): nenhum controle; só a lista de categorias; sem estimativa, o texto vazio", async () => {
  const a = await prepararSim({ uid: 0 });
  assert.equal(await a.card.locator("input[type=range]").count(), 0);
  const txt = await a.card.locator(".ci-subs").innerText();
  for (const [n, v] of [["Mercado", "180,00"], ["Transporte", "90,00"], ["Lazer", "60,00"]]) assert.match(txt, new RegExp(`${n}\\s*R\\$ ${v}/mês`));
  await a.page.context().close();
  const b = await prepararSim({}, { recursos: { previsao: RESPOSTAS.previsao.pro90 } });
  assert.equal(await b.card.locator("input[type=range]").count(), 0);
  assert.equal(await b.card.locator(".ci-vazio").innerText(), "Ainda sem histórico para estimar.");
  await b.page.context().close();
});

for (const width of [1440, 390]) {
  test(`S18 visual (${width}px): sem rolagem lateral, a curva e o resultado cabem no balão, controle com alvo de toque de 44 px, "Zerar" também`, async () => {
    const a = await prepararSim({ width });
    await a.soltar("Mercado", 30);
    await a.card.locator("svg[role=img]").waitFor();
    const m = await a.page.evaluate(() => {
      const c = document.querySelector(".card-ia").getBoundingClientRect();
      const fora = [...document.querySelectorAll(".card-ia *")].filter((e) => { const r = e.getBoundingClientRect(); return r.width && (r.right > c.right + 1 || r.left < c.left - 1); }).map((e) => e.className || e.tagName);
      const alt = (s) => Math.round(document.querySelector(s).getBoundingClientRect().height);
      return { larg: [document.documentElement.scrollWidth, innerWidth], fora, range: alt(".card-ia input[type=range]"), zerar: alt(".card-ia .btn-ghost") };
    });
    assert.ok(m.larg[0] <= m.larg[1], `rolagem lateral: ${m.larg}`);
    assert.deepEqual(m.fora, [], "algo vaza do balão");
    assert.ok(m.range >= 44, `alvo do controle ${m.range}px`);
    assert.ok(m.zerar >= 44, `alvo do Zerar ${m.zerar}px`);
    assert.deepEqual(a.erros, []);
    await a.page.context().close();
  });
}

test("S19 teclado, um passo a cada 60 ms: 10x ArrowRight (0 a 100) mandam UM POST (o de 100) e no máximo 2 leituras do perfil (o limite é 60/min); um soltar isolado depois ainda dá o resultado", async (tc) => {
  const { page, t, card, alavanca, soltar } = await prepararSim();
  await alavanca("Mercado").focus();
  t.ordem.length = 0;
  for (let i = 0; i < 10; i++) { await page.keyboard.press("ArrowRight"); await page.waitForTimeout(60); } // um passo a cada 60 ms: mais lento que uma leitura do perfil, então só a espera os junta
  await page.waitForFunction(() => /R\$ 180,00/.test(document.querySelector(".card-ia .sim-facts")?.textContent ?? ""), null, { timeout: 8000 });
  await page.waitForTimeout(500);
  assert.deepEqual(t.corpos, [{ cortes: [{ categoria: "mercado", percentual: 100 }] }]);
  const perfil = t.ordem.filter((x) => x === "perfil").length;
  tc.diagnostic(`10 teclas: ${t.corpos.length} POST(s) e ${perfil} leitura(s) de perfil`);
  assert.ok(perfil <= 2, `${perfil} leituras do perfil`);
  assert.match(await card.locator(".sim-facts").innerText(), /Sobra por mês\s*R\$ 180,00/);
  await soltar("Lazer", 10); // o soltar isolado (positivo): 1 POST a mais, com o resultado na tela
  await page.waitForFunction(() => /R\$ 186,00/.test(document.querySelector(".card-ia .sim-facts")?.textContent ?? ""), null, { timeout: 8000 });
  assert.equal(t.corpos.length, 2);
  await page.context().close();
});

test("S16b \"Zerar simulação\" pelo teclado: o botão some e o foco vai para o 1º controle, não para o <body>", async () => {
  const { page, card, soltar, alavanca } = await prepararSim();
  await soltar("Mercado", 30);
  await card.locator(".sim-facts").waitFor();
  const zerar = card.getByRole("button", { name: "Zerar simulação" });
  await zerar.focus();
  await zerar.press("Enter");
  await zerar.waitFor({ state: "detached" });
  assert.equal(await page.evaluate(() => document.activeElement === document.body), false, "o foco caiu no <body>");
  assert.equal(await alavanca("Mercado").evaluate((el) => el === document.activeElement), true);
  await page.context().close();
});

test("S20 três soltar espaçados de 400 ms: no máximo 2 leituras do perfil por POST (uma antes, uma depois; a query do próprio card em voo não vira \"leitura de perfil em voo\")", async (tc) => {
  const { page, t, card, soltar } = await prepararSim();
  t.ordem.length = 0;
  for (const v of [10, 20, 30]) { await soltar("Mercado", v); await page.waitForTimeout(400); }
  await card.locator(".sim-facts").waitFor();
  const perfil = t.ordem.filter((x) => x === "perfil").length;
  tc.diagnostic(`${t.corpos.length} POST(s) e ${perfil} leitura(s) de perfil`);
  assert.equal(t.corpos.length, 3);
  assert.ok(perfil <= 2 * t.corpos.length, `${perfil} leituras do perfil para ${t.corpos.length} POSTs`);
  await page.context().close();
});

test("S16c \"Zerar simulação\" com 2 cards na conversa: o foco vai para o 1º controle do card ONDE se zerou (o 2º), não para o do 1º card", async () => {
  const { page } = await prepararSim({}, { cards: 2 });
  const segundo = page.locator(".card-ia").nth(1), primeiro = segundo.locator("input[type=range]").first();
  await mover(segundo.locator(".lever-habito", { hasText: "Mercado" }).locator("input"), 30);
  await segundo.locator(".sim-facts").waitFor();
  const zerar = segundo.getByRole("button", { name: "Zerar simulação" });
  await zerar.focus();
  await zerar.press("Enter");
  await zerar.waitFor({ state: "detached" });
  assert.equal(await primeiro.evaluate((el) => el === document.activeElement), true, "o foco não está no 1º controle do 2º card");
  await page.context().close();
});
