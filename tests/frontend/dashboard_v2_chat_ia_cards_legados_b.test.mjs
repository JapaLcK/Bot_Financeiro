// O que é de CADA um dos 6 cards legados da IA no chat do /painel (PR 6 da Etapa 7): os vazios com o texto certo, o que cada número vira na tela, o que abre e o que esconde. A fundação
// (bloco válido, contrato, LimiteCard, HTML do modelo, nenhuma rota, 390 e 1440, ids) é do dashboard_v2_chat_ia_cards_legados.test.mjs. O relógio do harness é 2026-10-02 (SP).
// Cada grupo foi provado vermelho com uma mutação do código que ele cobre (a tabela está no relato do PR, não aqui).
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { encerrar, iniciar } from "./_chat_ia.mjs";
import { FX, blocoDoCaso, com, linhasDe, responde } from "./_chat_ia_legados.mjs";

before(iniciar);
after(encerrar);
const um = async (id, recursos, opts) => responde([blocoDoCaso(id, recursos)], opts);
const escala = (page, sel) => page.locator(sel).evaluateAll((is) => is.map((i) => i.style.transform));

// caixinhas ------------------------------------------------------------------------------------------------------------------------------------------
test("caixinhas: sem meta é nome, saldo e a origem; com meta, objetivo e prazo, os dois ritmos, a barra e o indicador; banco sem plano ativo avisa; meta sem prazo e sem alvo", async () => {
  const a = await um("caixinhas", { caixinhas: FX.caixinhas });
  assert.deepEqual(await linhasDe(a.page, ".card-ia .ci-grupo > .ci-linha"), ["Viagem R$ 300,50", "Reserva do banco R$ 5.200,40", "Moto R$ 150,25"]);
  assert.deepEqual(await linhasDe(a.page, ".card-ia .ci-grupo > .ci-cap"), ["Caixinha manual", "Caixinha do banco com sincronização indisponível", "Caixinha manual"]);
  assert.deepEqual(await linhasDe(a.page, ".card-ia .ci-sub"), ["Objetivo · 22/12/2026 R$ 1.000,75", "Ritmo mensal atual R$ 100,50", "Ritmo mensal necessário R$ 350,12"]);
  assert.equal(await a.page.locator(".card-ia .selo").innerText(), "Abaixo do ritmo necessário");
  assert.match((await escala(a.page, ".card-ia .ci-trilho > i"))[0], /^scaleX\(0\.305\)$/);
  assert.equal(await a.page.locator(".card-ia .ci-trilho").getAttribute("aria-label"), "31% do objetivo");
  await a.page.context().close();
  const g = (f) => com(FX.caixinhas, (c) => { c.goals = [{ ...c.goals[0], ...f }]; });
  const b = await um("caixinhas", { caixinhas: g({ source: "open_finance", of_plan_active: true, target_date: null, target_amount: null, pct_complete: null, monthly_pace_current: null, monthly_pace_needed: null, indicator: "no_deadline" }) });
  assert.equal(await b.page.locator(".card-ia .ci-grupo > .ci-cap").innerText(), "Caixinha do banco");
  assert.deepEqual(await linhasDe(b.page, ".card-ia .ci-sub"), ["Objetivo · Sem prazo definido Não informado", "Ritmo mensal atual Não informado", "Ritmo mensal necessário Não informado"]);
  assert.equal(await b.page.locator(".card-ia .ci-trilho").count(), 0, "sem % não há barra");
  assert.equal(await b.page.locator(".card-ia .selo").innerText(), "Sem prazo definido");
  await b.page.context().close();
  const c = await um("caixinhas", { caixinhas: g({ pct_complete: 140.5, indicator: "inventado_pelo_futuro" }) });
  assert.equal((await escala(c.page, ".card-ia .ci-trilho > i"))[0], "scaleX(1)", "mais de 100% não passa da barra");
  assert.equal(await c.page.locator(".card-ia .ci-trilho > i").evaluate((e) => e.style.background), "var(--gain)");
  assert.equal(await c.page.locator(".card-ia .selo").innerText(), "Ritmo da meta a conferir");
  await c.page.context().close();
  const vazio = await um("caixinhas", { caixinhas: { ok: true, goals: [] } });
  assert.equal(await vazio.page.locator(".card-ia .ci-vazio").innerText(), "Nenhuma caixinha ainda.");
  await vazio.page.context().close();
});

// fatura ---------------------------------------------------------------------------------------------------------------------------------------------
test("fatura: nome legível e final, a fatura em destaque, vencimento e fechamento, limite, crédito usado com a barra só quando há limite, e o já pago; vazio com o texto certo", async () => {
  const a = await um("fatura", { cartoes: FX.cartoes });
  assert.deepEqual(await linhasDe(a.page, ".card-ia .ci-grupo > .ci-linha"), ["Nubank · 4321 R$ 999,50", "Ultraviolet Black R$ 0,00"]);
  assert.deepEqual(await linhasDe(a.page, ".card-ia .ci-grupo > .ci-cap"), ["Vencimento dia 17 · fechamento dia 10", "Vencimento dia 12 · fechamento dia 5"]);
  assert.deepEqual(await linhasDe(a.page, ".card-ia .ci-sub"), ["Limite R$ 5.000,50", "Crédito utilizado R$ 1.354,75", "Já pago nesta fatura R$ 100,40", "Limite Não informado", "Crédito utilizado R$ 50,25", "Já pago nesta fatura R$ 0,00"]);
  assert.deepEqual((await escala(a.page, ".card-ia .ci-trilho > i")).map((t) => t.slice(0, 12)), ["scaleX(0.270"], "só o cartão com limite tem barra: 1.354,75 de 5.000,50");
  await a.page.context().close();
  const cartao = (f) => com(FX.cartoes, (c) => { c.cards = [{ ...c.cards[0], ...f }]; });
  const b = await um("fatura", { cartoes: cartao({ credit_limit: 0 }) });
  assert.equal(await b.page.locator(".card-ia .ci-trilho").count(), 0, "limite zero não divide");
  await b.page.context().close();
  const c = await um("fatura", { cartoes: cartao({ credit_used: 9000.5 }) });
  assert.equal((await escala(c.page, ".card-ia .ci-trilho > i"))[0], "scaleX(1)", "estourou o limite: a barra para na ponta");
  await c.page.context().close();
  const vazio = await um("fatura", { cartoes: { ok: true, cards: [] } });
  assert.equal(await vazio.page.locator(".card-ia .ci-vazio").innerText(), "Nenhum cartão cadastrado.");
  await vazio.page.context().close();
});

// parcelas -------------------------------------------------------------------------------------------------------------------------------------------
test("parcelas: uma linha por compra (nome, cartão, \"n de N pagas\", trilho), sem barras por mês; tocar abre as parcelas ali, uma compra por vez; compra sem cartão e data torta", async () => {
  const a = await um("parcelas", { parcelas: FX.parcelas });
  assert.deepEqual(await linhasDe(a.page, ".card-ia .ci-compra"), ["Sofá Nubank 0 de 3 pagas", "Notebook Nubank 1 de 3 pagas"], "o Tênis (2 de 2) está quitado e sai do card");
  assert.deepEqual((await escala(a.page, ".card-ia .ci-trilho > i")).map((t) => t.slice(0, 12)), ["scaleX(0)", "scaleX(0.333"]);
  assert.equal(await a.page.locator(".card-ia .months").count(), 0, "sem MonthBars");
  await a.page.locator(".card-ia .ci-compra").nth(1).click();
  assert.deepEqual(await linhasDe(a.page, ".card-ia .ci-aberta .ci-sub"), ["Parcela 1 de 3 17/09/2026 · paga R$ 833,33", "Parcela 2 de 3 17/10/2026 · próxima R$ 833,33", "Parcela 3 de 3 17/11/2026 · pendente R$ 833,34"]);
  await a.page.locator(".card-ia .ci-compra").nth(0).click();
  assert.equal(await a.page.locator(".card-ia .ci-compra[aria-expanded=true]").count(), 1, "uma aberta por vez");
  assert.equal(await a.page.locator(".card-ia .ci-aberta .ci-sub").count(), 3);
  await a.page.context().close();
  const torta = com(FX.parcelas, (c) => { c.installments = [{ ...c.installments[0], card_name: null, parcelas: [{ ...c.installments[0].parcelas[0], due_date: "lixo" }] }]; });
  const b = await um("parcelas", { parcelas: torta });
  assert.deepEqual(await linhasDe(b.page, ".card-ia .ci-compra"), ["Sofá 0 de 3 pagas"], "sem cartão, sem a linha do cartão; N é o total do banco");
  await b.page.locator(".card-ia .ci-compra").click();
  assert.deepEqual(await linhasDe(b.page, ".card-ia .ci-aberta .ci-sub"), ["Parcela 1 de 3 Data não informada · próxima R$ 100,50"]);
  await b.page.context().close();
  const vazio = await um("parcelas", { parcelas: { ok: true, installments: [] } });
  assert.equal(await vazio.page.locator(".card-ia .ci-vazio").innerText(), "Não há parcelamentos registrados.");
  await vazio.page.context().close();
});
test("parcelas: N é o total do banco e as parcelas antes da 1ª vista contam como pagas (Open Finance); só quitadas = o vazio", async () => {
  const pc = (no, pago) => ({ ...FX.parcelas.installments[0].parcelas[0], tx_id: 100 + no, installment_no: no, is_paid: pago, is_next: !pago });
  const of = com(FX.parcelas, (c) => { c.installments = [
    { ...c.installments[0], group_id: "q", name: "Quitada", installments_total: 12, parcelas: [pc(12, true)] },
    { ...c.installments[0], group_id: "m", name: "No meio", installments_total: 12, parcelas: [pc(5, false), pc(6, false)] },
  ]; });
  const a = await um("parcelas", { parcelas: of });
  assert.deepEqual(await linhasDe(a.page, ".card-ia .ci-compra"), ["No meio Nubank 4 de 12 pagas"]);
  await a.page.context().close();
  const so = com(FX.parcelas, (c) => { c.installments = [{ ...c.installments[0], installments_total: 12, parcelas: [pc(12, true)] }]; });
  const b = await um("parcelas", { parcelas: so });
  assert.equal(await b.page.locator(".card-ia .ci-vazio").innerText(), "Não há parcelamentos registrados.");
  await b.page.context().close();
});
test("parcelas: 7 compras mostram 5 e \"+2\" abre as outras (\"Mostrar menos\" volta)", async () => {
  const sete = com(FX.parcelas, (c) => { c.installments = Array.from({ length: 7 }, (_, i) => ({ ...c.installments[0], group_id: `g${i}`, name: `Compra ${i + 1}` })); });
  const { page } = await um("parcelas", { parcelas: sete });
  assert.equal(await page.locator(".card-ia .ci-compra").count(), 5);
  await page.getByRole("button", { name: "+2, mostrar mais 2 compras" }).click();
  assert.equal(await page.locator(".card-ia .ci-compra").count(), 7);
  await page.getByRole("button", { name: "Mostrar menos compras" }).click();
  assert.equal(await page.locator(".card-ia .ci-compra").count(), 5);
  await page.context().close();
});

// renda ----------------------------------------------------------------------------------------------------------------------------------------------
const mesesDe = (n, f) => Array.from({ length: n }, (_, i) => { const m = i + 10 - n + 1, ano = m < 1 ? 2025 : 2026, mm = ((m + 11) % 12) + 1; return { month: `${ano}-${String(mm).padStart(2, "0")}`, ...f(i) }; });
test("renda: as barras são o que entrou, só até o mês atual (o mês a mais do UTC fica fora delas e entra na lista), com o atual destacado; abaixo, entrou e saídas por mês", async () => {
  const a = await um("renda", { renda: FX.renda });
  assert.deepEqual(await linhasDe(a.page, ".card-ia .months li .sr-only"), ["junho: R$ 0", "julho: R$ 3.001", "agosto: R$ 3.001", "setembro: R$ 3.200", "outubro: R$ 3.100"], "2026-11 não está nas barras");
  assert.deepEqual(await a.page.locator(".card-ia .months li").evaluateAll((ls) => ls.map((l) => l.hasAttribute("data-on"))), [false, false, false, false, true]);
  assert.deepEqual(await a.page.locator(".card-ia .months li .months-bar i").evaluateAll((is) => is.map((i) => getComputedStyle(i).opacity)), ["0.6", "0.6", "0.6", "0.6", "1"], "o mês atual é o único cheio");
  assert.deepEqual(await linhasDe(a.page, ".card-ia .ci-grupo"), ["Junho 2026 R$ 0,00 Saídas R$ 0,00", "Julho 2026 R$ 3.000,50 Saídas R$ 2.100,25", "Agosto 2026 R$ 3.000,50 Saídas R$ 2.400,75", "Setembro 2026 R$ 3.200,00 Saídas R$ 2.900,10", "Outubro 2026 R$ 3.100,25 Saídas R$ 1.800,30", "Novembro 2026 R$ 0,00 Saídas R$ 0,00"]);
  assert.equal(await a.page.locator(".card-ia .months b").first().isVisible(), true);
  await a.page.context().close();
  const doze = await um("renda", { renda: { ok: true, months: 12, evolution: mesesDe(12, (i) => ({ income: 1000.5 + i, expense: 500.25, net: 500.25 })) } });
  assert.equal(await doze.page.locator(".card-ia .months li").count(), 12);
  assert.equal(await doze.page.locator(".card-ia .months b").first().isVisible(), false, "com 12 barras o valor some e fica o texto para leitor de tela");
  await doze.page.context().close();
  const zerada = await um("renda", { renda: com(FX.renda, (c) => { c.evolution.forEach((m) => { m.income = 0; }); }) });
  assert.equal(await zerada.page.locator(".card-ia .months").count(), 0, "sem entrada nenhuma não há gráfico");
  assert.equal(await zerada.page.locator(".card-ia .ci-grupo").count(), 6, "a lista fica");
  await zerada.page.context().close();
  const vazio = await um("renda", { renda: { ok: true, evolution: [], months: 6 } });
  assert.equal(await vazio.page.locator(".card-ia .ci-vazio").innerText(), "Ainda não há histórico de renda disponível.");
  await vazio.page.context().close();
});

// piggy ----------------------------------------------------------------------------------------------------------------------------------------------
test("piggy: título em destaque e a frase em cinza, na ordem; `key` repetida do modelo não quebra; sem destaque, o vazio com o texto certo", async () => {
  const igual = com(FX.insights, (c) => { c.insights[1].key = c.insights[0].key; });
  const a = await um("piggy", { insights: igual });
  assert.deepEqual(await linhasDe(a.page, ".card-ia .ci-grupo"), ["Mercado subiu Você gastou R$ 80,50 a mais em mercado que no mês passado.", "Fatura chegando A fatura do Nubank vence dia 17."]);
  assert.equal(await a.page.locator(".card-ia .ci-grupo b").count(), 2);
  assert.equal(await a.page.locator(".card-ia button, .card-ia a").count(), 0, "o action_view do destaque não vira botão");
  assert.deepEqual(a.erros, []);
  await a.page.context().close();
  const vazio = await um("piggy", { insights: { ok: true, insights: [] } });
  assert.equal(await vazio.page.locator(".card-ia .ci-vazio").innerText(), "O Piggy ainda não encontrou um destaque para você.");
  await vazio.page.context().close();
});

// rendimento -----------------------------------------------------------------------------------------------------------------------------------------
const rend = (f) => ({ rendimento: com(FX.rendimento, (c) => f(c.rendimentos, c)) });
const texto = (page) => page.locator(".card-ia").innerText();
test("rendimento: abre em Mês (ganho com + e em verde, chip, saldo, aviso de ativos sem dado); o Seg troca para Total; a barra só tem as fatias com valor", async () => {
  const { page } = await um("rendimento", { rendimento: FX.rendimento });
  assert.equal(await page.locator(".card-ia .stat-value").innerText(), "+R$ 112,35");
  assert.equal(await page.locator(".card-ia .stat-value.gain").count(), 1);
  assert.equal(await page.locator(".card-ia .selo.gain").innerText(), "+0,75%");
  assert.ok((await texto(page)).includes("neste mês · saldo R$ 15.100,50") && (await texto(page)).includes("3 de 10 ativos sem rendimento informado"));
  assert.equal(await page.locator(".card-ia .comp-bar > span").count(), 3);
  assert.deepEqual(await linhasDe(page, ".card-ia .ci-rend"), ["Renda fixa 7 ativos · R$ 13.800,00 +R$ 81,65 +0,60%", "Ações 2 ativos · R$ 2.100,00 +R$ 30,00 +1,45%", "Fundos imobiliários 1 ativo · R$ 700,50 +R$ 6,30 +0,91%"]);
  assert.ok((await texto(page)).includes("Open Finance sincronizado em 01/10 12:30. Estimado pela variação do saldo desde 30/09, sem contar aportes."));
  await page.getByRole("radio", { name: "Total", exact: true }).click();
  assert.equal(await page.locator(".card-ia .stat-value").innerText(), "+R$ 665,10");
  assert.ok((await texto(page)).includes("desde a aplicação · saldo R$ 15.100,50") && (await texto(page)).includes("Saldo menos o valor aplicado, como o banco informa."));
  assert.equal(await page.locator(".card-ia .comp-bar > span").count(), 1, "Ações e Fundos imobiliários não têm Total: só a renda fixa entra na barra");
  assert.match((await linhasDe(page, ".card-ia .ci-rend"))[1], /^Ações 2 ativos · R\$ 2\.100,00 Não informado$/);
  await page.context().close();
});
test("rendimento: sem o Mês (sem a foto do fechamento) abre em Total e o Mês avisa; perda sai com − e sem verde; zero sem sinal; data de outro ano leva o ano; sem sincronização, sem a frase", async () => {
  const { page } = await um("rendimento", rend((r) => { r.mes.valor = null; r.mes.pct = null; r.mes_desde = "2025-12-31"; r.sincronizado_em = null; }));
  assert.equal(await page.locator(".card-ia .stat-value").innerText(), "+R$ 665,10", "abre em Total");
  assert.equal(await page.getByRole("radio", { name: "Total", exact: true }).getAttribute("aria-checked"), "true");
  await page.getByRole("radio", { name: "Mês", exact: true }).click();
  assert.equal(await page.locator(".card-ia .ci-manchete").innerText(), "Não informado", "nunca zero");
  assert.equal(await page.locator(".card-ia .stat-value, .card-ia .selo").count(), 0);
  const t = await texto(page);
  assert.ok(t.includes("O rendimento do mês aparece quando houver a foto do fechamento do mês anterior.") && t.includes("desde 31/12/2025, sem contar aportes") && !t.includes("Open Finance sincronizado"));
  await page.context().close();
  const perda = await um("rendimento", rend((r) => { r.mes.valor = "-30.25"; r.mes.pct = "-0.20"; r.sincronizado_em = "2025-12-30T12:00:00Z"; }));
  assert.equal(await perda.page.locator(".card-ia .stat-value").innerText(), "−R$ 30,25");
  assert.equal(await perda.page.locator(".card-ia .stat-value.gain, .card-ia .selo.gain").count(), 0, "perda não é verde nem vermelha");
  assert.equal(await perda.page.locator(".card-ia .selo").innerText(), "−0,20%");
  assert.ok((await texto(perda.page)).includes("sincronizado em 30/12/2025 09:00"), "outro ano leva o ano");
  await perda.page.context().close();
  const zero = await um("rendimento", rend((r) => { r.mes.valor = "0.00"; r.mes.pct = "0.00"; }));
  assert.equal(await zero.page.locator(".card-ia .stat-value").innerText(), "R$ 0,00");
  assert.equal(await zero.page.locator(".card-ia .selo").innerText(), "0,00%");
  await zero.page.context().close();
});
test("rendimento: a hora da sincronização é a do fuso do app (São Paulo), em qualquer fuso do aparelho", async () => {
  // 2026-10-01T15:30Z = 12:30 de 01/10 em SP e 00:30 de 02/10 em Tóquio: sem o `timeZone` o dia e a hora mudam
  const { page } = await um("rendimento", { rendimento: FX.rendimento }, { tz: "Asia/Tokyo" });
  assert.equal(await page.evaluate(() => new Date("2026-10-01T15:30:00Z").getDate()), 2, "o aparelho do teste é mesmo o de Tóquio");
  assert.ok((await texto(page)).includes("sincronizado em 01/10 12:30."), await texto(page));
  await page.context().close();
});
test("rendimento: a categoria abre os ativos do que mais rendeu ao que menos, os sem valor no fim; 5 e \"+2\"; o Seg reordena pelo período; uma categoria aberta por vez", async () => {
  const { page } = await um("rendimento", { rendimento: FX.rendimento });
  await page.locator(".card-ia .ci-rend").first().click();
  const nomes = () => page.locator(".card-ia .ci-aberta .ci-sub .ci-dupla > span:first-child").allInnerTexts();
  assert.deepEqual(await nomes(), ["CDB Liquidez Diária", "LCI 2027", "Tesouro Selic", "LC Banco C6", "CDB sem leitura"]);
  await page.getByRole("button", { name: "+2, mostrar mais 2 ativos" }).click();
  assert.deepEqual((await nomes()).slice(5), ["CDB Pós 110% CDI", "Poupança antiga"], "perda antes do sem valor, que fica no fim");
  assert.match((await linhasDe(page, ".card-ia .ci-aberta .ci-sub")).pop(), /^Poupança antiga Caixa · R\$ 500,00 Não informado$/);
  await page.getByRole("radio", { name: "Total", exact: true }).click();
  assert.equal((await nomes())[0], "CDB Liquidez Diária");
  assert.equal(await page.locator(".card-ia .ci-aberta .ci-sub").count(), 5, "trocar o período fecha o \"+2\"");
  await page.locator(".card-ia .ci-rend").nth(1).click();
  assert.equal(await page.locator(".card-ia .ci-rend[aria-expanded=true]").count(), 1);
  assert.equal(await page.locator(".card-ia .ci-aberta .ci-sub").count(), 2, "Ações: 2 ativos");
  await page.context().close();
});
test("rendimento: sem plano de investimentos, sem categorias e sem leitura (null) mostram o texto do app, sem Seg nem números", async () => {
  for (const [f, frase] of [
    [(r) => { r.motivos = ["plano_sem_investimentos"]; }, "Seu plano atual não inclui investimentos."], [(r) => { r.categorias = []; }, "Não encontrei investimentos nos seus bancos conectados."],
  ]) {
    const { page } = await um("rendimento", rend(f));
    assert.equal(await page.locator(".card-ia .ci-vazio").innerText(), frase);
    assert.equal(await page.locator(".card-ia .seg, .card-ia .stat-value").count(), 0);
    await page.context().close();
  }
  const nulo = await um("rendimento", { rendimento: { ...FX.rendimento, rendimentos: null } });
  assert.equal(await nulo.page.locator(".card-ia .ci-vazio").innerText(), "Não foi possível ler seus rendimentos agora.");
  await nulo.page.context().close();
});
