// Os cards do Detetive (assinaturas), do Repórter (resumo do mês) e do Xerife (orçamentos) acima do texto da resposta (webapp: parts/CardAgente.tsx,
// CardAssinaturas.tsx, CardResumoMes.tsx, CardOrcamentos.tsx). O Faria Limer e o Barão estão em dashboard_v2_chat_agentes_card.test.mjs.
// O servidor só diz QUAL card (e o mês); os números vêm da rota v2 do tipo, conferida a conta da sessão (conta/api.ts: daConta). Tudo em 1440 e 390.
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { chromium } from "playwright";
import { abrirPainel, exigeArtefatoEmDia, RESPOSTAS } from "./_painel.mjs";
import { servirSemConferencia, viraConta43NoGet as vira } from "./_isolamento_card.mjs";

let browser;
before(async () => { exigeArtefatoEmDia(); browser = await chromium.launch(); });
after(() => browser?.close());

const TELAS = [1440, 390];
const SHOTS = process.env.PR3B_SHOTS; // pasta dos screenshots (opcional)
// A tela de cada tipo: o agente, o `card` do servidor, a rota v2 que o card lê e o corpo dela (api_v2_respostas.json).
const TIPOS = {
  assinaturas: { nome: "Detetive", kind: "detetive", card: { tipo: "assinaturas" }, url: /\/api\/v2\/assinaturas$/, ok: RESPOSTAS.assinaturas.cheia },
  resumo_mes: { nome: "Repórter", kind: "reporter", card: { tipo: "resumo_mes", mes: "2026-10" }, url: /\/api\/v2\/resumo-do-mes\?/, ok: RESPOSTAS.resumo_do_mes.exato },
  orcamentos: { nome: "Xerife", kind: "xerife", card: { tipo: "orcamentos", mes: "2026-10" }, url: /\/api\/v2\/orcamentos-categoria$/, ok: RESPOSTAS.orcamentos_categoria.padrao },
};
// O Repórter com dois meses: duas leituras do resumo-do-mes, uma por mês (o corpo casa com o `mes` pedido).
const RES = (entrou, saiu, mes = "2026-10") => ({ ...RESPOSTAS.resumo_do_mes.exato, mes, entrou, saiu });
const mesDe = (req) => new URL(req.url()).searchParams.get("mes");
const PAR = { "2026-09": ["1000000.00", "1234567.89"], "2026-10": ["2234567.89", "1000000.00"] };
const CMP = { nome: "Repórter", kind: "reporter", card: { tipo: "comparacao_meses", meses: ["2026-09", "2026-10"], motivos: [] }, url: /\/api\/v2\/resumo-do-mes\?/,
  ok: (req) => RES(...PAR[mesDe(req)], mesDe(req)) };
const TODOS = { ...TIPOS, comparacao_meses: CMP };
const CAT = Object.values(TIPOS).map((t) => [t.kind, t.nome]);
const resposta = (card) => ({ json: { reply: "Texto do agente.", context: "ctx-1", redirects: [{ kind: "barao", name: "Barão", question: "e a renda fixa?", access: "ready" }], usage: { used: 1, limit: 100 }, ...(card === undefined ? {} : { card }) } });

// `corpo`: o corpo do GET (ou função da chamada); `status`: o do GET. `gets`: as URLs pedidas à rota do tipo.
async function abrir({ width, tipo, card, corpo, status = 200 }) {
  const t = TODOS[tipo];
  const { ctx, page, erros, ir } = await abrirPainel(browser, { width, espera: "#askbar-input" });
  const gets = [];
  await ctx.route("**/auth/dashboard-profile", (r) => r.fulfill({ json: { user_id: 42, display_name: "Marina", email: null, whatsapp_linked: true } }));
  await ctx.route(t.url, (r) => { gets.push(r.request().url()); r.fulfill({ status, json: typeof corpo === "function" ? corpo(gets.length, r.request()) : (corpo ?? (typeof t.ok === "function" ? t.ok(r.request()) : t.ok)) }); });
  await ctx.route(/\/agents\/42(\/|$)/, (r) => {
    if (!/\/chat$/.test(new URL(r.request().url()).pathname)) {
      return r.fulfill({ json: { catalog: CAT.map(([kind, nome]) => ({ kind, nome, disponivel: true, status: "active", energy_cost: 1 })), can_chat: true, can_activate: true, energy_enabled: false, energy_budget: 99, energy_used: 0 } });
    }
    return r.fulfill(resposta(card === undefined ? t.card : card));
  });
  await ir("/piggy");
  await page.locator(".chat-empty").waitFor();
  return { ctx, page, erros, gets, t };
}
const perguntar = async (page, nome) => {
  await page.locator("#askbar-input").fill(`@${nome} me mostra?`);
  await page.locator("#askbar-input").press("Enter");
  await page.locator(".chat > .msg-piggy .msg-text:not([data-carga])").waitFor();
};
const card = (page) => page.locator(".chat > .msg-piggy .card-agente");
const carregado = (page) => card(page).locator("a.msg-open").waitFor();
const shot = async (page, nome, width) => { if (SHOTS) { mkdirSync(SHOTS, { recursive: true }); await page.screenshot({ path: join(SHOTS, `${nome}-${width}.png`) }); } };
const linhas = (loc, sel) => loc.locator(sel).evaluateAll((es) => es.map((e) => e.innerText.replace(/\s+/g, " ")));

// Posição, geometria e número de cada card (a mesma medição para os três).
const medida = (page) => page.evaluate(() => {
  const li = document.querySelector(".chat > .msg-piggy"), top = (s) => li.querySelector(s).getBoundingClientRect(), c = top(".card-agente"), l = li.getBoundingClientRect();
  const a = li.querySelector(".card-agente a.msg-open");
  return {
    ordem: [c.bottom <= top(".msg-text").top, top(".msg-text").bottom <= top(".msg-acoes").top], dentro: c.left >= l.left - 1 && c.right <= l.right + 1,
    semRolagem: document.documentElement.scrollWidth <= innerWidth && li.querySelector(".card-agente").scrollWidth <= Math.ceil(c.width), larg: c.width,
    link: a.getAttribute("href"), alvo: a.getBoundingClientRect().height, status: document.querySelector("p[role=status]").textContent,
  };
});

for (const width of TELAS) {
  test(`${width}: Detetive: total mensal e as 3 maiores assinaturas ativas, com link para /assinaturas`, async () => {
    const { ctx, page, erros, gets } = await abrir({ width, tipo: "assinaturas" });
    await perguntar(page, "Detetive");
    await carregado(page);
    const m = await medida(page);
    assert.deepEqual(m.ordem, [true, true]);
    assert.ok(m.dentro && m.semRolagem && m.larg <= 480 && m.alvo >= 44, JSON.stringify(m));
    assert.equal(m.link, "#/assinaturas");
    assert.equal(await card(page).locator(".card-agente-total").innerText(), "R$ 212,60");
    // a cancelada (Globoplay) e a 4ª ativa (iCloud) ficam de fora; o total é o do servidor, que soma as ativas
    assert.deepEqual(await linhas(card(page), ".card-agente-lista li"), ["Smart Fit R$ 119,90", "Netflix R$ 55,90", "Apple Music R$ 21,90"]);
    assert.equal(await card(page).locator("a.msg-open").innerText(), "Ver as 4 assinaturas");
    assert.ok(!/212/.test(m.status), "o card fica fora do role=status");
    assert.equal(gets.length, 1);
    await shot(page, "detetive", width);
    await ctx.close();
    assert.deepEqual(erros, []);
  });

  test(`${width}: Repórter: Entrou, Saiu e Sobrou do mês pedido, com link para o resumo`, async () => {
    const { ctx, page, erros, gets } = await abrir({ width, tipo: "resumo_mes" });
    await perguntar(page, "Repórter");
    await carregado(page);
    const m = await medida(page);
    assert.deepEqual(m.ordem, [true, true]);
    assert.ok(m.dentro && m.semRolagem && m.larg <= 480 && m.alvo >= 44, JSON.stringify(m));
    assert.equal(m.link, "#/");
    assert.equal(await card(page).locator(".card-agente-tit").innerText(), "Resumo de outubro de 2026");
    assert.deepEqual(await linhas(card(page), ".card-agente-valores > div"), ["Entrou R$ 1.040,00", "Saiu R$ 440,00", "Sobrou R$ 600,00"]);
    assert.equal(gets.length, 1);
    assert.equal(new URL(gets[0]).searchParams.get("mes"), "2026-10");
    await shot(page, "reporter", width);
    await ctx.close();
    assert.deepEqual(erros, []);
  });

  test(`${width}: Xerife: total usado e as categorias do mais perto do teto ao mais longe`, async () => {
    const { ctx, page, erros, gets } = await abrir({ width, tipo: "orcamentos" });
    await perguntar(page, "Xerife");
    await carregado(page);
    const m = await medida(page);
    assert.deepEqual(m.ordem, [true, true]);
    assert.ok(m.dentro && m.semRolagem && m.larg <= 480 && m.alvo >= 44, JSON.stringify(m));
    assert.equal(m.link, "#/orcamentos-categoria");
    assert.equal(await card(page).locator(".card-agente-total").innerText(), "R$ 1.053,80 de R$ 1.450,00");
    const cats = await card(page).locator(".orc-linha-topo b").allInnerTexts();
    assert.deepEqual(cats, ["lazer", "transporte", "mercado", "alimentação"]); // 118,25% 100% 85% 51,54%
    assert.match((await linhas(card(page), ".orc-linha"))[0], /lazer R\$ 236,50 de R\$ 200,00 .*passou R\$ 36,50/);
    assert.equal(gets.length, 1);
    await shot(page, "xerife", width);
    await ctx.close();
    assert.deepEqual(erros, []);
  });

  test(`${width}: card desconhecido, sem mês ou com mês inválido: nenhum card e nenhum GET`, async () => {
    const ruins = [[{ tipo: "novo" }, "assinaturas"], [{ tipo: "resumo_mes" }, "resumo_mes"], [{ tipo: "resumo_mes", mes: "2026-13" }, "resumo_mes"],
      [{ tipo: "resumo_mes", mes: "2026-10&x=1" }, "resumo_mes"], [{ tipo: "resumo_mes", mes: 202610 }, "resumo_mes"], [{ tipo: "resumo_mes", mes: "../../auth" }, "resumo_mes"], [{ tipo: "orcamentos" }, "orcamentos"], [{ tipo: "orcamentos", mes: "2026-13" }, "orcamentos"]];
    for (const [c, tipo] of ruins) {
      const { ctx, page, erros, gets } = await abrir({ width, tipo, card: c });
      await perguntar(page, TIPOS[tipo].nome);
      await page.waitForTimeout(150);
      assert.equal(await card(page).count(), 0, JSON.stringify(c));
      assert.equal(gets.length, 0, JSON.stringify(c));
      await ctx.close();
      assert.deepEqual(erros, [], JSON.stringify(c));
    }
  });

  for (const tipo of Object.keys(TIPOS)) {
    test(`${width}: ${tipo}: erro 500 mostra 'Tentar de novo' (o texto fica) e o clique carrega`, async () => {
      let n = 0;
      const { ctx, page, erros, t } = await abrir({ width, tipo });
      await ctx.route(t.url, (r) => (++n > 3 ? r.fulfill({ json: t.ok }) : r.fulfill({ status: 500, json: { detail: "x" } })));
      await perguntar(page, t.nome);
      await card(page).getByRole("button", { name: "Tentar de novo" }).waitFor({ timeout: 15000 });
      assert.match(await card(page).innerText(), /Não deu para carregar os dados agora\./);
      assert.equal(await page.locator(".chat > .msg-piggy .msg-text").innerText(), "Texto do agente.");
      await card(page).getByRole("button", { name: "Tentar de novo" }).click();
      await carregado(page);
      await ctx.close();
      assert.deepEqual(erros, []);
    });
  }
}

test("assinaturas sem plano (403) ou sem sessão (401): nenhum card", async () => {
  for (const status of [401, 403]) {
    const { ctx, page } = await abrir({ width: 1440, tipo: "assinaturas", status, corpo: { detail: { error: "pro_required", feature: "subscriptions" } } });
    await perguntar(page, "Detetive");
    await page.waitForTimeout(300);
    assert.equal(await card(page).count(), 0, String(status));
    await ctx.close();
  }
});

test("estados vazios: sem assinatura ativa e sem orçamento dizem isso, sem total", async () => {
  let { ctx, page } = await abrir({ width: 390, tipo: "assinaturas", corpo: RESPOSTAS.assinaturas.vazia });
  await perguntar(page, "Detetive");
  await carregado(page);
  assert.match(await card(page).innerText(), /Nenhuma assinatura ativa encontrada/);
  assert.equal(await card(page).locator(".card-agente-total, .card-agente-lista").count(), 0);
  await ctx.close();
  ({ ctx, page } = await abrir({ width: 390, tipo: "orcamentos", corpo: RESPOSTAS.orcamentos_categoria.vazio }));
  await perguntar(page, "Xerife");
  await carregado(page);
  assert.match(await card(page).innerText(), /Nenhum orçamento por categoria ainda/);
  assert.equal(await card(page).locator(".card-agente-total, .orc-lista").count(), 0);
  await ctx.close();
});

// Sobrou = entrou − saiu em inteiros exatos: em float, 0.1 − 0.3 daria -0.19999999999999998.
for (const [entrou, saiu, sobrou] of [["0.10", "0.30", "−R$ 0,20"], ["0", "310.00", "−R$ 310,00"], ["1040", "440.5", "R$ 599,50"], ["0.00", "0.00", "R$ 0,00"], ["987654321.99", "0.01", "R$ 987.654.321,98"]]) {
  test(`Repórter: Sobrou de ${entrou} − ${saiu} = ${sobrou}, sem estourar o card em 390`, async () => {
    const { ctx, page } = await abrir({ width: 390, tipo: "resumo_mes", corpo: RES(entrou, saiu) });
    await perguntar(page, "Repórter");
    await carregado(page);
    const r = await card(page).evaluate((c) => ({ sobrou: [...c.querySelectorAll(".card-agente-valores > div")][2].querySelector("dd").textContent,
      rolagem: c.scrollWidth > c.clientWidth, pagina: document.documentElement.scrollWidth > innerWidth }));
    assert.deepEqual(r, { sobrou, rolagem: false, pagina: false });
    await ctx.close();
  });
}

test("Repórter: os selos são os avisos que o agente deu (do card), não os da rota", async () => {
  // a rota avisa do corte no mês anterior; o agente, com o mês inteiro, não: o card segue o agente
  const corpo = { ...RES("10.00", "5.00"), motivos: ["inicio_do_historico", "conciliacao_pendente"] };
  for (const [motivos, esperado] of [[["conciliacao_pendente"], ["conciliação pendente"]], [[], []], [[{}], []]]) {
    const { ctx, page } = await abrir({ width: 1440, tipo: "resumo_mes", card: { tipo: "resumo_mes", mes: "2026-10", motivos }, corpo });
    await perguntar(page, "Repórter");
    await carregado(page);
    assert.deepEqual(await card(page).locator(".selo").allInnerTexts(), esperado, JSON.stringify(motivos));
    await ctx.close();
  }
});

for (const width of [390, 320]) test(`Xerife: total e linhas de 7 dígitos em ${width} não saem do card nem rolam a página`, async () => {
  const o = RESPOSTAS.orcamentos_categoria.padrao;
  const corpo = { ...o, total: { ...o.total, gasto: "1234567.89", teto: "9999999.99" }, orcamentos: o.orcamentos.map((x) => ({ ...x, gasto: "1234567.89", teto: "1234567.89" })) };
  const { ctx, page } = await abrir({ width, tipo: "orcamentos", corpo });
  await perguntar(page, "Xerife");
  await carregado(page);
  const r = await card(page).evaluate((c) => { const b = c.getBoundingClientRect();
    return { fora: [...c.querySelectorAll("*")].filter((e) => e.getBoundingClientRect().right > b.right + 0.5).map((e) => e.className), pagina: document.documentElement.scrollWidth > innerWidth }; });
  assert.deepEqual(r, { fora: [], pagina: false });
  await ctx.close();
});

test("Xerife: a rota respondeu outro mês (virou o mês depois da consulta): nenhum card, nem erro", async () => {
  const { ctx, page, gets } = await abrir({ width: 1440, tipo: "orcamentos", card: { tipo: "orcamentos", mes: "2026-09" } });
  await perguntar(page, "Xerife");
  await page.waitForFunction(() => !document.querySelector(".chat > .msg-piggy .card-agente[aria-busy]"));
  await page.waitForTimeout(200);
  assert.equal(gets.length, 1);
  assert.equal(await card(page).count(), 0);
  await ctx.close();
});

test("Xerife: mês fora do padrão no corpo: erro, nunca número", async () => {
  const { ctx, page } = await abrir({ width: 1440, tipo: "orcamentos", corpo: { ...RESPOSTAS.orcamentos_categoria.padrao, mes: "xx" } });
  await perguntar(page, "Xerife");
  await card(page).getByRole("button", { name: "Tentar de novo" }).waitFor({ timeout: 5000 });
  assert.ok(!/undefined|NaN/.test(await card(page).innerText()));
  await ctx.close();
});

test("Repórter: corpo de outro mês ou fora do contrato: erro, nunca número", async () => {
  for (const corpo of [RES("1.00", "1.00", "2026-09"), RES(1, "1.00"), RES("abc", "1.00"), RES("1.00", null), { ...RES("1.00", "1.00"), motivos: [{}] }, { ...RES("1.00", "1.00"), motivos: null }]) {
    const { ctx, page } = await abrir({ width: 1440, tipo: "resumo_mes", corpo });
    await perguntar(page, "Repórter");
    await card(page).getByRole("button", { name: "Tentar de novo" }).waitFor({ timeout: 5000 });
    assert.equal(await card(page).locator(".card-agente-valores").count(), 0, JSON.stringify(corpo));
    await ctx.close();
  }
});

// --- Isolamento: a /api/v2 lê o usuário da SESSÃO. Se a sessão virou a conta B entre o pedido e a resposta, o número de B nunca aparece. ---
const DA_B = { assinaturas: [{ ...RESPOSTAS.assinaturas.cheia, total_mensal: "98765.43" }, "98.765,43"], resumo_mes: [RES("98765.43", "1.00"), "98.765,43"],
  orcamentos: [{ ...RESPOSTAS.orcamentos_categoria.padrao, total: { ...RESPOSTAS.orcamentos_categoria.padrao.total, gasto: "98765.43" } }, "98.765,43"] };
const viraConta43NoGet = async (ctx, page, tipo) => {
  const [corpoB, marca] = DA_B[tipo];
  return { arma: await vira(ctx, page, TIPOS[tipo].url, corpoB, marca), marca };
};
for (const width of TELAS) {
  for (const tipo of Object.keys(TIPOS)) {
    test(`${width}: ISOLAMENTO ${tipo}: depois do GET do card a sessão vira a conta 43: o número dela nunca aparece e a conversa é limpa`, async () => {
      const { ctx, page, erros, t } = await abrir({ width, tipo });
      const { arma, marca } = await viraConta43NoGet(ctx, page, tipo);
      arma();
      const lido = page.waitForResponse((r) => t.url.test(r.url()));
      await page.locator("#askbar-input").fill(`@${t.nome} me mostra?`);
      await page.locator("#askbar-input").press("Enter");
      await lido;
      await page.locator(".chat-empty").waitFor();
      await page.waitForTimeout(300);
      assert.equal(await page.evaluate(() => window.__viu), false, "o número da conta B apareceu no DOM");
      assert.equal(await page.evaluate(() => window.__viuErro), false, "na troca de conta o card não mostra nada, nem o erro");
      assert.ok(!(await page.locator("body").innerText()).includes(marca));
      assert.equal(await page.locator(".chat > .msg-piggy").count(), 0, "a conversa da conta anterior sumiu");
      await ctx.close();
      assert.deepEqual(erros, []);
    });
  }
}
for (const tipo of Object.keys(TIPOS)) {
  test(`ISOLAMENTO ${tipo}, controle negativo: com a conferência da conta desligada no bundle, o número de B APARECE (o teste acima mede)`, async () => {
    const { ctx, page, t } = await abrir({ width: 1440, tipo });
    const servido = await servirSemConferencia(ctx);
    const { arma } = await viraConta43NoGet(ctx, page, tipo);
    await page.reload();
    await page.locator(".chat-empty").waitFor();
    arma();
    await page.locator("#askbar-input").fill(`@${t.nome} me mostra?`);
    await page.locator("#askbar-input").press("Enter");
    await page.waitForFunction(() => window.__viu, null, { timeout: 5000 }).catch(() => {});
    assert.ok(servido.n > 0, "o bundle desligado não foi servido: o controle não mede nada");
    assert.equal(await page.evaluate(() => window.__viu), true, "sem a conferência o número de B tem de aparecer");
    await ctx.close();
  });
}

// --- Repórter compara dois meses: o card lê o resumo-do-mes dos dois (o servidor só diz QUAIS meses). ---
const rotulos = (page) => card(page).evaluate((c) => [...c.querySelectorAll(".card-agente-comp > div")].map((d) => ({
  rot: d.querySelector("dt").textContent, dif: d.querySelector(".card-agente-dif").textContent.replace("Diferença ", ""), cor: d.querySelector(".card-agente-dif").className.replace(/num|card-agente-dif/g, "").trim(),
  valores: [...d.querySelectorAll("dd:not(.card-agente-dif)")].map((x) => x.innerText.replace(/\s+/g, " ")) })));
for (const width of [1440, 390, 320]) {
  test(`${width}: Repórter compara dois meses: lado a lado, diferença com sinal e cor por sentido, sem estourar`, async () => {
    const { ctx, page, erros, gets } = await abrir({ width, tipo: "comparacao_meses" });
    await perguntar(page, "Repórter");
    await carregado(page);
    const m = await medida(page);
    assert.deepEqual(m.ordem, [true, true]);
    assert.ok(m.dentro && m.semRolagem && m.larg <= 480 && m.alvo >= 44, JSON.stringify(m));
    assert.equal(await card(page).locator(".card-agente-tit").innerText(), "Setembro × outubro de 2026");
    // Entrou subiu (verde), Saiu caiu (verde, com o sinal −), Sobrou subiu (verde)
    assert.deepEqual(await rotulos(page), [
      { rot: "Entrou", dif: "+R$ 1.234.567,89", cor: "gain", valores: ["Setembro R$ 1.000.000,00", "Outubro R$ 2.234.567,89"] },
      { rot: "Saiu", dif: "−R$ 234.567,89", cor: "gain", valores: ["Setembro R$ 1.234.567,89", "Outubro R$ 1.000.000,00"] },
      { rot: "Sobrou", dif: "+R$ 1.469.135,78", cor: "gain", valores: ["Setembro −R$ 234.567,89", "Outubro R$ 1.234.567,89"] }]);
    const fora = await card(page).evaluate((c) => { const b = c.getBoundingClientRect(); return [...c.querySelectorAll("*")].filter((e) => e.getBoundingClientRect().right > b.right + 0.5).map((e) => e.className); });
    assert.deepEqual(fora, []);
    assert.deepEqual(gets.map((u) => new URL(u).searchParams.get("mes")).sort(), ["2026-09", "2026-10"]);
    await shot(page, "comparacao", width);
    await ctx.close();
    assert.deepEqual(erros, []);
  });
}

// 9 dígitos (o teto do dinheiro é R$ 1 bi), positivo e negativo, nos dois meses: nada passa da borda do card nem da própria coluna.
for (const width of [1440, 390, 320]) {
  for (const [nome, par] of [["positivos", ["987654321.99", "1.00"]], ["Sobrou negativo", ["1.00", "987654321.99"]]]) {
    test(`${width}: Repórter compara com 9 dígitos (${nome}): sem estourar`, async () => {
      const { ctx, page } = await abrir({ width, tipo: "comparacao_meses", corpo: (_n, req) => RES(...par, mesDe(req)) });
      await perguntar(page, "Repórter");
      await carregado(page);
      const m = await card(page).evaluate((c) => {
        const b = c.getBoundingClientRect();
        const dds = [...c.querySelectorAll(".card-agente-comp dd")];
        return { card: b.width, maiorDd: Math.max(...dds.map((d) => d.getBoundingClientRect().width)),
          fora: [...c.querySelectorAll("*")].filter((e) => e.getBoundingClientRect().right > b.right + 0.5).map((e) => e.className),
          corta: dds.filter((d) => d.scrollWidth > d.clientWidth + 0.5).map((d) => d.innerText), pagina: document.documentElement.scrollWidth - innerWidth };
      });
      console.log(`# ${width} ${nome}: card ${m.card} maior dd ${m.maiorDd}`);
      assert.deepEqual([m.fora, m.corta], [[], []], JSON.stringify(m));
      assert.ok(m.pagina <= 0, JSON.stringify(m));
      await ctx.close();
    });
  }
}

test("Repórter compara: piorou = aviso, zero = neutra (sem cor nem sinal)", async () => {
  const par = { "2026-09": ["500.00", "300.00"], "2026-10": ["400.00", "300.00"] };
  const { ctx, page } = await abrir({ width: 390, tipo: "comparacao_meses", corpo: (_n, req) => RES(...par[mesDe(req)], mesDe(req)) });
  await perguntar(page, "Repórter");
  await carregado(page);
  assert.deepEqual((await rotulos(page)).map((l) => [l.rot, l.dif, l.cor]), [["Entrou", "−R$ 100,00", "warn"], ["Saiu", "R$ 0,00", ""], ["Sobrou", "−R$ 100,00", "warn"]]);
  await ctx.close();
  // Saiu subiu = aviso; Entrou e Sobrou iguais = neutros
  const par2 = { "2026-09": ["500.00", "100.00"], "2026-10": ["500.00", "250.00"] };
  const r2 = await abrir({ width: 390, tipo: "comparacao_meses", corpo: (_n, req) => RES(...par2[mesDe(req)], mesDe(req)) });
  await perguntar(r2.page, "Repórter");
  await carregado(r2.page);
  assert.deepEqual((await rotulos(r2.page)).map((l) => [l.rot, l.dif, l.cor]), [["Entrou", "R$ 0,00", ""], ["Saiu", "+R$ 150,00", "warn"], ["Sobrou", "−R$ 150,00", "warn"]]);
  await r2.ctx.close();
});

test("Repórter compara: anos diferentes mostram os dois anos; selos são os do card", async () => {
  const card2 = { tipo: "comparacao_meses", meses: ["2025-12", "2026-01"], motivos: ["conciliacao_pendente"] };
  const { ctx, page } = await abrir({ width: 320, tipo: "comparacao_meses", card: card2, corpo: (_n, req) => RES("10.00", "5.00", mesDe(req)) });
  await perguntar(page, "Repórter");
  await carregado(page);
  assert.equal(await card(page).locator(".card-agente-tit").innerText(), "Dezembro de 2025 × janeiro de 2026");
  assert.deepEqual(await card(page).locator(".selo").allInnerTexts(), ["conciliação pendente"]);
  await ctx.close();
});

test("Repórter compara: card fora do contrato (um mês, três, iguais, decrescente, mês inválido, caminho): nenhum card e nenhum GET", async () => {
  const m = (meses) => ({ tipo: "comparacao_meses", meses, motivos: [] });
  for (const c of [m(["2026-10"]), m(["2026-08", "2026-09", "2026-10"]), m(["2026-10", "2026-10"]), m(["2026-10", "2026-09"]), m(["2026-09", "2026-13"]), m(["../../auth", "2026-10"]), m("2026-09"), { tipo: "comparacao_meses" }]) {
    const { ctx, page, erros, gets } = await abrir({ width: 390, tipo: "comparacao_meses", card: c });
    await perguntar(page, "Repórter");
    await page.waitForTimeout(150);
    assert.equal(await card(page).count(), 0, JSON.stringify(c));
    assert.equal(gets.length, 0, JSON.stringify(c));
    await ctx.close();
    assert.deepEqual(erros, [], JSON.stringify(c));
  }
});

test("Repórter compara: corpo de outro mês ou fora do contrato num dos lados: erro, nunca meio card; 500 num: Tentar de novo", async () => {
  const bom = (req) => RES(...PAR[mesDe(req)], mesDe(req));
  for (const lado of ["2026-09", "2026-10"]) for (const ruim of [(req) => RES("1.00", "1.00", "2026-08"), (req) => RES(1, "1.00", mesDe(req)), (req) => ({ ...RES("1.00", "1.00", mesDe(req)), motivos: [{}] })]) {
    const { ctx, page } = await abrir({ width: 1440, tipo: "comparacao_meses", corpo: (_n, req) => (mesDe(req) === lado ? ruim(req) : bom(req)) });
    await perguntar(page, "Repórter");
    await card(page).getByRole("button", { name: "Tentar de novo" }).waitFor({ timeout: 5000 });
    assert.equal(await card(page).locator(".card-agente-comp").count(), 0, lado);
    assert.ok(!/R\$/.test(await card(page).innerText()), "nenhum número de um lado só");
    await ctx.close();
  }
  let n = 0;
  const { ctx, page, erros } = await abrir({ width: 390, tipo: "comparacao_meses" });
  await ctx.route(CMP.url, (r) => (mesDe(r.request()) === "2026-09" && ++n <= 3 ? r.fulfill({ status: 500, json: { detail: "x" } }) : r.fulfill({ json: bom(r.request()) })));
  await perguntar(page, "Repórter");
  await card(page).getByRole("button", { name: "Tentar de novo" }).waitFor({ timeout: 15000 });
  assert.equal(await page.locator(".chat > .msg-piggy .msg-text").innerText(), "Texto do agente.");
  await card(page).getByRole("button", { name: "Tentar de novo" }).click();
  await carregado(page);
  await ctx.close();
  assert.deepEqual(erros, []);
});

// Isolamento: a sessão vira na 1ª leitura, ou só na 2ª (a 1ª legítima): nem o número de B nem o da 1ª leitura (meio card) aparecem.
const MARCA_B = "98.765,43", DA_A = "1.000.000,00";
const corpoCmp = (req, n) => (n === 1 ? RES("1000000.00", "1.00", mesDe(req)) : RES("98765.43", "1.00", mesDe(req)));
for (const [viraNoN, nome] of [[1, "1ª leitura"], [2, "2ª leitura (a 1ª já era legítima)"]]) {
  test(`ISOLAMENTO comparacao_meses: a sessão vira a conta 43 na ${nome}: nenhum número aparece e a conversa é limpa`, async () => {
    const { ctx, page, erros } = await abrir({ width: 390, tipo: "comparacao_meses" });
    const arma = await vira(ctx, page, CMP.url, corpoCmp, MARCA_B, viraNoN);
    arma();
    await page.evaluate((a) => { new MutationObserver(() => { if (document.body.textContent.includes(a)) window.__viuA = true; }).observe(document, { subtree: true, childList: true, characterData: true }); }, DA_A);
    const lidas = page.waitForResponse((r) => CMP.url.test(r.url()));
    await page.locator("#askbar-input").fill("@Repórter me mostra?");
    await page.locator("#askbar-input").press("Enter");
    await lidas;
    await page.locator(".chat-empty").waitFor();
    await page.waitForTimeout(400);
    assert.equal(await page.evaluate(() => window.__viu), false, "o número da conta B apareceu");
    assert.equal(await page.evaluate(() => !!window.__viuA), false, "o número da 1ª leitura apareceu (meio card)");
    assert.equal(await page.evaluate(() => window.__viuErro), false);
    assert.equal(await page.locator(".chat > .msg-piggy").count(), 0);
    await ctx.close();
    assert.deepEqual(erros, []);
  });
}
test("ISOLAMENTO comparacao_meses, controle negativo: sem a conferência da conta no bundle, o número de B APARECE", async () => {
  const { ctx, page } = await abrir({ width: 1440, tipo: "comparacao_meses" });
  const servido = await servirSemConferencia(ctx);
  const arma = await vira(ctx, page, CMP.url, corpoCmp, MARCA_B, 2);
  await page.reload();
  await page.locator(".chat-empty").waitFor();
  arma();
  await page.locator("#askbar-input").fill("@Repórter me mostra?");
  await page.locator("#askbar-input").press("Enter");
  await page.waitForFunction(() => window.__viu, null, { timeout: 5000 }).catch(() => {});
  assert.ok(servido.n > 0, "o bundle desligado não foi servido: o controle não mede nada");
  assert.equal(await page.evaluate(() => window.__viu), true, "sem a conferência o número de B tem de aparecer");
  await ctx.close();
});
