// Os cards do Carteiro (contas a pagar) e do Banqueiro (caixinhas, kind "cofre") acima do texto da resposta (webapp: parts/CardAgente.tsx, CardContas.tsx,
// CardCaixinhas.tsx). Os outros cards estão em dashboard_v2_chat_agentes_card_temas.test.mjs. O servidor só diz QUAL card; os números vêm da rota
// v2 do tipo (/api/v2/contas-a-pagar, /api/v2/caixinhas), conferida a conta da sessão (conta/api.ts: daConta). Tudo em 1440 e 390. Nenhum dos dois
// tem link: o painel não tem tela de contas a pagar nem de caixinhas.
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
const SHOTS = process.env.PR3C_SHOTS; // pasta dos screenshots (opcional)
const TIPOS = {
  contas: { nome: "Carteiro", kind: "carteiro", card: { tipo: "contas", hoje: "2026-10-20" }, url: /\/api\/v2\/contas-a-pagar$/, ok: RESPOSTAS.contas_a_pagar.cheia, vazio: RESPOSTAS.contas_a_pagar.vazia },
  caixinhas: { nome: "Banqueiro", kind: "cofre", card: { tipo: "caixinhas" }, url: /\/api\/v2\/caixinhas$/, ok: RESPOSTAS.caixinhas.cheia, vazio: RESPOSTAS.caixinhas.vazia },
};
const CAT = Object.values(TIPOS).map((t) => [t.kind, t.nome]);
const resposta = (card) => ({ json: { reply: "Texto do agente.", context: "ctx-1", redirects: [{ kind: "barao", name: "Barão", question: "e a renda fixa?", access: "ready" }], usage: { used: 1, limit: 100 }, ...(card === undefined ? {} : { card }) } });

async function abrir({ width, tipo, card, corpo, status = 200 }) {
  const t = TIPOS[tipo];
  const { ctx, page, erros, ir } = await abrirPainel(browser, { width, espera: "#askbar-input" });
  const gets = [];
  await ctx.route("**/auth/dashboard-profile", (r) => r.fulfill({ json: { user_id: 42, display_name: "Marina", email: null, whatsapp_linked: true } }));
  await ctx.route(t.url, (r) => { gets.push(r.request().url()); r.fulfill({ status, json: corpo ?? t.ok }); });
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
const carregado = (page) => card(page).locator(".card-agente-lista, .card-agente-vazio").waitFor();
const shot = async (page, nome, width) => { if (SHOTS) { mkdirSync(SHOTS, { recursive: true }); await page.screenshot({ path: join(SHOTS, `${nome}-${width}.png`) }); } };
const linhas = (loc, sel) => loc.locator(sel).evaluateAll((es) => es.map((e) => e.innerText.replace(/\s+/g, " ")));

// Posição (acima do texto, que fica acima das ações "Perguntar ao X"), geometria e rolagem do card.
const medida = (page) => page.evaluate(() => {
  const li = document.querySelector(".chat > .msg-piggy"), top = (s) => li.querySelector(s).getBoundingClientRect(), c = top(".card-agente"), l = li.getBoundingClientRect();
  return {
    ordem: [c.bottom <= top(".msg-text").top, top(".msg-text").bottom <= top(".msg-acoes").top], dentro: c.left >= l.left - 1 && c.right <= l.right + 1,
    semRolagem: document.documentElement.scrollWidth <= innerWidth && li.querySelector(".card-agente").scrollWidth <= Math.ceil(c.width), larg: c.width,
    link: li.querySelector(".card-agente a") !== null, status: document.querySelector("p[role=status]").textContent,
  };
});

for (const width of TELAS) {
  test(`${width}: Carteiro: total, vencidas e as 3 contas que vencem primeiro, sem link`, async () => {
    const { ctx, page, erros, gets } = await abrir({ width, tipo: "contas" });
    await perguntar(page, "Carteiro");
    await carregado(page);
    const m = await medida(page);
    assert.deepEqual(m.ordem, [true, true]);
    assert.ok(m.dentro && m.semRolagem && m.larg <= 480 && !m.link, JSON.stringify(m));
    assert.equal(await card(page).locator(".card-agente-total").innerText(), "R$ 1.245,60");
    assert.match(await card(page).innerText(), /estimado · 5 contas/);
    assert.equal(await card(page).locator(".card-agente-aviso").innerText(), "1 conta vencida");
    // a vencida primeiro e em destaque (.warn); as outras duas (Condomínio, Seguro) ficam no "e mais"
    assert.deepEqual(await linhas(card(page), ".card-agente-lista li"), ["Aluguel há 2 dias R$ 850,00", "Luz hoje R$ 132,60", "Internet em 3 dias R$ 99,90"]);
    assert.equal(await card(page).locator(".card-agente-lista li").first().locator(".warn").count(), 2);
    assert.equal(await card(page).locator(".card-agente-lista li").nth(1).locator(".warn").count(), 0);
    assert.match(await card(page).innerText(), /e mais 2 contas/);
    assert.ok(!/1\.245/.test(m.status), "o card fica fora do role=status");
    assert.equal(gets.length, 1);
    await shot(page, "carteiro", width);
    await ctx.close();
    assert.deepEqual(erros, []);
  });

  test(`${width}: Banqueiro: total guardado e as caixinhas com a barra de quem tem meta`, async () => {
    const { ctx, page, erros, gets } = await abrir({ width, tipo: "caixinhas" });
    await perguntar(page, "Banqueiro");
    await carregado(page);
    const m = await medida(page);
    assert.deepEqual(m.ordem, [true, true]);
    assert.ok(m.dentro && m.semRolagem && m.larg <= 480 && !m.link, JSON.stringify(m));
    assert.equal(await card(page).locator(".card-agente-total").innerText(), "R$ 7.350,50");
    assert.deepEqual(await linhas(card(page), ".card-agente-lista li"), [
      "Reserva de emergência R$ 4.500,50 45% · meta R$ 10.000,00", "Viagem R$ 2.000,00 Meta batida · meta R$ 2.000,00",
      "Presente R$ 850,00", "Moto R$ 0,00 0% · meta R$ 15.000,00"]);
    const barras = await card(page).locator(".meter").evaluateAll((es) => es.map((e) => [e.getAttribute("aria-valuenow"), e.firstElementChild.style.width, e.classList.contains("ok")]));
    assert.deepEqual(barras, [["45", "45%", false], ["100", "100%", true], ["0", "0%", false]]); // Presente não tem meta: sem barra
    assert.equal(gets.length, 1);
    await shot(page, "banqueiro", width);
    await ctx.close();
    assert.deepEqual(erros, []);
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

test("Carteiro: a rota já está noutro dia (virou a meia-noite depois da consulta): nenhum card, nem erro; sem o dia, nenhum GET", async () => {
  let { ctx, page, gets } = await abrir({ width: 1440, tipo: "contas", card: { tipo: "contas", hoje: "2026-10-19" } });
  await perguntar(page, "Carteiro");
  await page.waitForFunction(() => !document.querySelector(".chat > .msg-piggy .card-agente[aria-busy]"));
  await page.waitForTimeout(200);
  assert.equal(gets.length, 1);
  assert.equal(await card(page).count(), 0);
  await ctx.close();
  ({ ctx, page, gets } = await abrir({ width: 1440, tipo: "contas", card: { tipo: "contas" } }));
  await perguntar(page, "Carteiro");
  await page.waitForTimeout(300);
  assert.equal(gets.length, 0);
  assert.equal(await card(page).count(), 0);
  await ctx.close();
});

test("Carteiro: conta variável sem estimativa (gravada como 0) diz 'valor a definir', não R$ 0,00", async () => {
  const base = RESPOSTAS.contas_a_pagar.cheia;
  const contas = base.contas.map((c, i) => (i === 1 ? { ...c, valor: "0.00", variavel: true } : i === 2 ? { ...c, valor: "0.00", variavel: false } : c));
  // o total do servidor deixa de fora a variável sem estimativa (0) e a fixa zerada: 1245,60 − 132,60 − 99,90
  const { ctx, page } = await abrir({ width: 1440, tipo: "contas", corpo: { ...base, contas, total_estimado: "1013.10" } });
  await perguntar(page, "Carteiro");
  await carregado(page);
  const l = await linhas(card(page), ".card-agente-lista li");
  assert.match(l[1], /valor a definir/);
  assert.match(l[2], /R\$ 0,00/); // fixa de valor zero continua R$ 0,00
  assert.equal(await card(page).locator(".card-agente-total").innerText(), "R$ 1.013,10");
  assert.match(await card(page).innerText(), /fora 1 conta a definir/); // o total não conta a de valor a definir
  await ctx.close();
});

test("Carteiro: só conta variável sem estimativa: o total diz 'Valor a definir', não R$ 0,00", async () => {
  const corpo = { hoje: "2026-10-20", pendentes: 1, vencidas: 0, total_estimado: "0.00", tem_variavel: true,
    contas: [{ id: 1, nome: "Luz", categoria: null, vence_em: "2026-10-25", dias: 5, valor: "0.00", variavel: true, vencida: false }] };
  const { ctx, page } = await abrir({ width: 390, tipo: "contas", corpo });
  await perguntar(page, "Carteiro");
  await carregado(page);
  assert.equal(await card(page).locator(".card-agente-total").innerText(), "Valor a definir");
  assert.ok(!/R\$ 0,00/.test(await card(page).innerText()));
  await ctx.close();
});

test("Banqueiro: sem a meta batida o card nunca diz 100% (99,96% é 99%)", async () => {
  const cx = { total: "999.60", caixinhas: [{ id: 1, nome: "Quase", saldo: "999.60", meta: "1000.00", progresso_pct: "99.96", atingida: false }] };
  const { ctx, page } = await abrir({ width: 1440, tipo: "caixinhas", corpo: cx });
  await perguntar(page, "Banqueiro");
  await carregado(page);
  const r = await card(page).evaluate((c) => ({ sub: c.querySelector(".card-agente-sub").textContent, valor: c.querySelector("[role=meter]").getAttribute("aria-valuenow"), barra: c.querySelector("[role=meter] > span").style.width }));
  assert.deepEqual(r, { sub: "99% · meta R$ 1.000,00", valor: "99", barra: "99%" }); // a barra não fica cheia ao lado de "99%"
  await ctx.close();
});

test("estados vazios: sem conta pendente e sem caixinha dizem isso, sem total nem lista", async () => {
  for (const [tipo, frase] of [["contas", /Nenhuma conta a pagar pendente/], ["caixinhas", /Nenhuma caixinha ainda/]]) {
    const { ctx, page } = await abrir({ width: 390, tipo, corpo: TIPOS[tipo].vazio });
    await perguntar(page, TIPOS[tipo].nome);
    await carregado(page);
    assert.match(await card(page).innerText(), frase);
    assert.equal(await card(page).locator(".card-agente-total, .card-agente-lista, .card-agente-aviso").count(), 0);
    await ctx.close();
  }
});

test("sem plano (403) ou sem sessão (401): nenhum card; tipo desconhecido: nenhum card e nenhum GET", async () => {
  for (const status of [401, 403]) {
    const { ctx, page } = await abrir({ width: 1440, tipo: "contas", status, corpo: { detail: { error: "pro_required", feature: "recurring_expenses" } } });
    await perguntar(page, "Carteiro");
    await page.waitForTimeout(300);
    assert.equal(await card(page).count(), 0, String(status));
    await ctx.close();
  }
  const { ctx, page, gets } = await abrir({ width: 1440, tipo: "contas", card: { tipo: "contas2" } });
  await perguntar(page, "Carteiro");
  await page.waitForTimeout(150);
  assert.equal(await card(page).count(), 0);
  assert.equal(gets.length, 0);
  await ctx.close();
});

test("nomes e valores enormes não estouram o card em 390; corpo fora do contrato: erro, nunca número", async () => {
  const longo = "Nome de conta muito longo ".repeat(6);
  const grande = { ...RESPOSTAS.contas_a_pagar.cheia, total_estimado: "987654321.99", contas: RESPOSTAS.contas_a_pagar.cheia.contas.map((c) => ({ ...c, nome: longo, valor: "987654321.99" })) };
  const cx = { total: "987654321.99", caixinhas: RESPOSTAS.caixinhas.cheia.caixinhas.map((c) => ({ ...c, nome: longo, saldo: "987654321.99" })) };
  for (const [tipo, corpo] of [["contas", grande], ["caixinhas", cx]]) {
    const { ctx, page } = await abrir({ width: 390, tipo, corpo });
    await perguntar(page, TIPOS[tipo].nome);
    await carregado(page);
    assert.deepEqual(await card(page).evaluate((c) => ({ rolagem: c.scrollWidth > c.clientWidth, pagina: document.documentElement.scrollWidth > innerWidth })), { rolagem: false, pagina: false }, tipo);
    await ctx.close();
  }
  const ruins = [["contas", { ...RESPOSTAS.contas_a_pagar.cheia, total_estimado: 12 }], ["contas", { ...RESPOSTAS.contas_a_pagar.cheia, contas: null }],
    ["caixinhas", { ...RESPOSTAS.caixinhas.cheia, total: "abc" }], ["caixinhas", { total: "1.00", caixinhas: [{ nome: "x", saldo: 5, meta: null, progresso_pct: null }] }]];
  for (const [tipo, corpo] of ruins) {
    const { ctx, page } = await abrir({ width: 1440, tipo, corpo });
    await perguntar(page, TIPOS[tipo].nome);
    await card(page).getByRole("button", { name: "Tentar de novo" }).waitFor({ timeout: 5000 });
    assert.equal(await card(page).locator(".card-agente-total").count(), 0, JSON.stringify(corpo));
    await ctx.close();
  }
});

// --- Isolamento: a /api/v2 lê o usuário da SESSÃO. Se a sessão virou a conta B entre o pedido e a resposta, o número de B nunca aparece. ---
const DA_B = { contas: [{ ...RESPOSTAS.contas_a_pagar.cheia, total_estimado: "98765.43" }, "98.765,43"], caixinhas: [{ ...RESPOSTAS.caixinhas.cheia, total: "98765.43" }, "98.765,43"] };
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
