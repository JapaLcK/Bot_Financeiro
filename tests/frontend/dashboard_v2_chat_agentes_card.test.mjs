// O card com os dados do tema acima do texto da resposta do Faria Limer e do Barão (webapp: parts/CardAgente.tsx, parts/CardInvestido.tsx).
// O servidor só diz QUAL card (`card.tipo`); os números vêm do /api/v2/investido, conferida a conta da sessão (conta/api.ts: daConta).
// O backend é de mentira (ctx.route); o contrato é o de frontend/routes/agents.py. Tudo em 1440 e 390.
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { mkdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { chromium } from "playwright";
import { abrirPainel, exigeArtefatoEmDia, RESPOSTAS } from "./_painel.mjs";
import { servirSemConferencia, viraConta43NoGet as vira } from "./_isolamento_card.mjs";

let browser;
before(async () => { exigeArtefatoEmDia(); browser = await chromium.launch(); });
after(() => browser?.close());

const TELAS = [1440, 390];
const FIXTURE = RESPOSTAS.investido.com_banco; // total 57123.45: renda fixa 40000.00, ações 17123.45
const CAT = [["faria_limer", "Faria Limer"], ["barao", "Barão"], ["detetive", "Detetive"]];
const FARIA = { json: { reply: "Você tem cerca de R$ 57 mil.", context: "ctx-1", redirects: [{ kind: "barao", name: "Barão", question: "e a renda fixa?", access: "ready" }], usage: { used: 1, limit: 100 }, card: { tipo: "investido" } } };
const resposta = (card, redirects = []) => ({ json: { reply: "Texto do agente.", context: "ctx-1", redirects, usage: { used: 1, limit: 100 }, ...(card === undefined ? {} : { card }) } });
const SHOTS = process.env.PR3A_SHOTS; // pasta dos screenshots (opcional)

// `investido`: o corpo do GET (ou função da chamada); `status`: o do GET. Devolve os GETs de /api/v2/investido e os do perfil.
async function abrir({ width, chat = FARIA, investido = FIXTURE, status = 200 }) {
  const { ctx, page, erros, ir } = await abrirPainel(browser, { width, espera: "#askbar-input" });
  const gets = { investido: 0, perfil: 0 };
  await ctx.route("**/auth/dashboard-profile", (r) => { gets.perfil++; r.fulfill({ json: { user_id: 42, display_name: "Marina", email: null, whatsapp_linked: true } }); });
  await ctx.route("**/api/v2/investido", (r) => { gets.investido++; r.fulfill({ status, json: typeof investido === "function" ? investido(gets.investido) : investido }); });
  await ctx.route(/\/agents\/42(\/|$)/, (r) => {
    if (!/\/chat$/.test(new URL(r.request().url()).pathname)) {
      return r.fulfill({ json: { catalog: CAT.map(([kind, nome]) => ({ kind, nome, disponivel: true, status: "active", energy_cost: 1 })), can_chat: true, can_activate: true, energy_enabled: false, energy_budget: 99, energy_used: 0 } });
    }
    return r.fulfill(typeof chat === "function" ? chat() : chat);
  });
  await ir("/piggy");
  await page.locator(".chat-empty").waitFor();
  return { ctx, page, erros, gets };
}
const perguntar = async (page, t = "@Faria Limer como está minha carteira?") => {
  await page.locator("#askbar-input").fill(t);
  await page.locator("#askbar-input").press("Enter");
  await page.locator(".chat > .msg-piggy .msg-text:not([data-carga])").waitFor();
};
const card = (page) => page.locator(".chat > .msg-piggy .card-agente");
const shot = async (page, nome, width) => { if (SHOTS) { mkdirSync(SHOTS, { recursive: true }); await page.screenshot({ path: join(SHOTS, `${nome}-${width}.png`) }); } };

for (const width of TELAS) {
  test(`${width}: Faria Limer: o card fica acima do texto, e o texto acima de "Perguntar ao X", com o número do /api/v2/investido`, async () => {
    const { ctx, page, erros, gets } = await abrir({ width });
    await perguntar(page);
    await card(page).locator(".card-agente-total").waitFor();
    const r = await page.evaluate(() => {
      const li = document.querySelector(".chat > .msg-piggy"), top = (s) => li.querySelector(s).getBoundingClientRect(), c = top(".card-agente"), l = li.getBoundingClientRect();
      const bar = [...li.querySelectorAll(".comp-bar span")];
      return {
        ordem: [c.bottom <= top(".msg-text").top, top(".msg-text").bottom <= top(".msg-acoes").top],
        total: li.querySelector(".card-agente-total").textContent, titulo: li.querySelector(".card-agente-tit").textContent,
        linhas: [...li.querySelectorAll(".card-agente-lista li")].map((e) => e.innerText.replace(/\s+/g, " ")),
        segmentos: bar.length, rotulo: li.querySelector(".comp-bar").getAttribute("aria-label"), role: li.querySelector(".comp-bar").getAttribute("role"),
        dentro: c.left >= l.left - 1 && c.right <= l.right + 1, semRolagem: document.documentElement.scrollWidth <= innerWidth, larg: c.width,
        status: document.querySelector("p[role=status]").textContent, textoDoP: li.querySelector(".msg-text").textContent,
      };
    });
    assert.deepEqual(r.ordem, [true, true]);
    assert.equal(r.total, "R$ 57.123,45");
    assert.match(r.titulo, /^Investimentos\s+nos bancos conectados$/);
    assert.equal(r.linhas.length, FIXTURE.por_tipo.length);
    assert.deepEqual(r.linhas, ["Renda fixa 70% R$ 40.000,00", "Ações 30% R$ 17.123,45"].map((t) => t));
    assert.equal(r.segmentos, 2);
    assert.equal(r.role, "img");
    assert.equal(r.rotulo, "Renda fixa 70%, Ações 30%");
    assert.ok(r.dentro && r.semRolagem && r.larg <= 480, JSON.stringify(r));
    assert.ok(!/57\.123|Investimentos/.test(r.status + r.textoDoP), "o card fica fora do texto e do role=status");
    assert.equal(gets.investido, 1);
    await shot(page, "faria-limer", width);
    await ctx.close();
    assert.deepEqual(erros, []);
  });

  test(`${width}: sem card, card null ou tipo desconhecido: nenhum card e nenhum GET a /api/v2/investido`, async () => {
    for (const card of [undefined, null, { tipo: "novo" }, "investido", { tipo: ["investido"] }]) {
      const { ctx, page, erros, gets } = await abrir({ width, chat: resposta(card) });
      await perguntar(page);
      await page.waitForTimeout(150);
      assert.equal(await card_(page), 0, JSON.stringify(card));
      assert.equal(gets.investido, 0, JSON.stringify(card));
      await ctx.close();
      assert.deepEqual(erros, [], JSON.stringify(card));
    }
  });

  test(`${width}: Barão: só Renda fixa e Tesouro, com a soma dos dois; sem ações`, async () => {
    const inv = { total: "300.00", motivos: [], por_banco: [], por_tipo: [
      { tipo: "acoes", rotulo: "Ações", valor: "149.85", motivos: [] }, { tipo: "renda_fixa", rotulo: "Renda fixa", valor: "100.10", motivos: [] }, { tipo: "tesouro", rotulo: "Tesouro Direto", valor: "50.05", motivos: [] }] };
    const { ctx, page } = await abrir({ width, chat: resposta({ tipo: "renda_fixa" }), investido: inv });
    await perguntar(page, "@Barão quanto tenho em renda fixa?");
    await card(page).locator(".card-agente-total").waitFor();
    const r = await card(page).evaluate((c) => ({ total: c.querySelector(".card-agente-total").textContent, titulo: c.querySelector(".card-agente-tit b").textContent,
      linhas: [...c.querySelectorAll(".card-agente-lista li")].map((e) => e.innerText.replace(/\s+/g, " ")), segmentos: c.querySelectorAll(".comp-bar span").length }));
    assert.deepEqual(r, { total: "R$ 150,15", titulo: "Renda fixa", linhas: ["Renda fixa 67% R$ 100,10", "Tesouro Direto 33% R$ 50,05"], segmentos: 2 });
    await shot(page, "barao", width);
    await ctx.close();
  });

  test(`${width}: valor de 9 dígitos não estoura o card`, async () => {
    const inv = { total: "987654321.99", motivos: [], por_banco: [], por_tipo: [
      { tipo: "fundos", rotulo: "Fundos de investimento", valor: "987654321.99", motivos: [] }] };
    const { ctx, page } = await abrir({ width, investido: inv });
    await perguntar(page);
    await card(page).locator(".card-agente-total").waitFor();
    const r = await card(page).evaluate((c) => {
      const b = c.getBoundingClientRect();
      return { total: c.querySelector(".card-agente-total").textContent, fora: [...c.querySelectorAll("*")].filter((e) => e.getBoundingClientRect().right > b.right + 0.5).map((e) => e.className || e.tagName),
        rolagem: c.scrollWidth > c.clientWidth, pagina: document.documentElement.scrollWidth > innerWidth };
    });
    assert.deepEqual(r, { total: "R$ 987.654.321,99", fora: [], rolagem: false, pagina: false });
    await shot(page, "nove-digitos", width);
    await ctx.close();
  });

  test(`${width}: as 7 cores dos tipos têm contraste não-texto >= 3:1 contra o fundo do card`, async () => {
    const tipos = ["renda_fixa", "tesouro", "acoes", "fii", "fundos", "etf", "outros"];
    const inv = { total: "700.00", motivos: [], por_banco: [], por_tipo: tipos.map((tipo) => ({ tipo, rotulo: tipo, valor: "100.00", motivos: [] })) };
    const { ctx, page } = await abrir({ width, investido: inv });
    await perguntar(page);
    await card(page).locator(".card-agente-total").waitFor();
    const m = await card(page).evaluate((c) => ({ fundo: getComputedStyle(c).backgroundColor,
      barra: [...c.querySelectorAll(".comp-bar span")].map((e) => getComputedStyle(e).backgroundColor), icones: [...c.querySelectorAll(".card-agente-lista i")].map((e) => getComputedStyle(e).color) }));
    await ctx.close();
    const rgb = (s) => s.match(/\d+/g).slice(0, 3).map((n) => n / 255);
    const lum = (s) => { const [r, g, b] = rgb(s).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4)); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
    const razao = (a, b) => (Math.max(lum(a), lum(b)) + 0.05) / (Math.min(lum(a), lum(b)) + 0.05);
    assert.equal(m.barra.length, 7);
    assert.equal(new Set(m.barra).size, 7);
    for (const cor of [...m.barra, ...m.icones]) assert.ok(razao(cor, m.fundo) >= 3, `${cor} contra ${m.fundo}: ${razao(cor, m.fundo).toFixed(2)}`);
    for (const proibida of ["rgb(61, 220, 151)", "rgb(245, 181, 68)", "rgb(255, 122, 69)"]) assert.ok(!m.barra.includes(proibida), `cor de estado ${proibida}`);
  });
}
const card_ = (page) => page.locator(".chat > .msg-piggy .card-agente").count();

// A MESMA tabela está em tests/test_agent_chat_card.py (§0.7): o `total_renda_fixa` do servidor e o foco "Renda fixa" do card.
const inv = (total, partes) => ({ total, motivos: total === null ? ["nenhum_investimento"] : [], por_banco: [], por_tipo: partes.map(([tipo, valor]) => ({ tipo, rotulo: tipo, valor, motivos: valor === null ? ["saldo_ausente"] : [] })) });
// A mesma tabela do tests/test_agent_chat_card.py (a regra existe em Python e em TS, §0.7). Sem total: nenhum número; sem soma: "—".
const brl = (v) => `R$ ${new Intl.NumberFormat("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(Number(v))}`;
const TABELA = JSON.parse(readFileSync(new URL("./total_renda_fixa.json", import.meta.url), "utf8"))
  .map((c) => [inv(c.total, c.partes), c.total === null ? null : c.esperado === null ? "—" : brl(c.esperado)]);
test("Barão: o total do card segue a tabela literal do total_renda_fixa do servidor", async () => {
  for (const [corpo, esperado] of TABELA) {
    const { ctx, page } = await abrir({ width: 1440, chat: resposta({ tipo: "renda_fixa" }), investido: corpo });
    await perguntar(page, "@Barão quanto tenho em renda fixa?");
    await card(page).locator(".card-agente-tit").waitFor();
    await page.waitForFunction(() => !document.querySelector(".card-agente[aria-busy]"));
    const total = await card(page).locator(".card-agente-total").allTextContents();
    assert.deepEqual(total, esperado === null ? [] : [esperado], JSON.stringify(corpo));
    await ctx.close();
  }
});

test("Faria Limer sem total (sem banco): a frase do chat no card, sem número", async () => {
  const { ctx, page } = await abrir({ width: 1440, investido: RESPOSTAS.investido.sem_banco });
  await perguntar(page);
  await card(page).locator(".card-agente-vazio").waitFor();
  assert.match(await card(page).innerText(), /conecte seu banco para eu ver seus investimentos/);
  assert.equal(await card(page).locator(".card-agente-total").count(), 0);
  await ctx.close();
});

test("parte sem saldo: 'saldo não informado', fora da barra", async () => {
  const { ctx, page } = await abrir({ width: 1440, investido: RESPOSTAS.investido.parte_sem_saldo });
  await perguntar(page);
  await card(page).locator(".card-agente-total").waitFor();
  assert.equal(await card(page).locator(".comp-bar span").count(), 1);
  assert.match(await card(page).locator(".card-agente-lista li").nth(1).innerText(), /Fundos de investimento\s+saldo não informado/);
  await ctx.close();
});

for (const width of TELAS) {
  test(`${width}: erro 500: o texto fica, o card pede 'Tentar de novo' e o clique carrega`, async () => {
    let ok = false;
    const { ctx, page, erros } = await abrir({ width, investido: () => FIXTURE });
    await ctx.route("**/api/v2/investido", (r) => ok ? r.fulfill({ json: FIXTURE }) : r.fulfill({ status: 500, json: { detail: "x" } }));
    await perguntar(page);
    await card(page).getByRole("button", { name: "Tentar de novo" }).waitFor({ timeout: 15000 });
    assert.match(await card(page).innerText(), /Não deu para carregar os dados agora\./);
    assert.equal(await page.locator(".chat > .msg-piggy .msg-text").innerText(), "Texto do agente.".replace("Texto do agente.", "Você tem cerca de R$ 57 mil."));
    ok = true;
    await card(page).getByRole("button", { name: "Tentar de novo" }).click();
    await card(page).locator(".card-agente-total").waitFor();
    assert.equal(await card(page).locator(".card-agente-total").innerText(), "R$ 57.123,45");
    await ctx.close();
    assert.deepEqual(erros, []);
  });

  test(`${width}: 403 do /api/v2/investido (plano) ou 401: o card não mostra nada`, async () => {
    for (const status of [401, 403]) {
      const { ctx, page } = await abrir({ width, status, investido: { detail: { error: "pro_required", feature: "investments" } } });
      await perguntar(page);
      await page.waitForTimeout(300);
      assert.equal(await card_(page), 0, String(status));
      await ctx.close();
    }
  });
}

// --- Isolamento: a /api/v2 lê o usuário da SESSÃO. Se a sessão virou a conta B entre o pedido e a resposta, o total de B nunca aparece. ---
const DA_B = { total: "98765.43", motivos: [], por_banco: [], por_tipo: [{ tipo: "acoes", rotulo: "Ações", valor: "98765.43", motivos: [] }] };
const viraConta43NoGet = (ctx, page) => vira(ctx, page, "**/api/v2/investido", DA_B, "98.765,43");
for (const width of TELAS) {
  test(`${width}: ISOLAMENTO: depois do GET do card a sessão vira a conta 43: o total dela nunca aparece e a conversa é limpa`, async () => {
    const { ctx, page, erros } = await abrir({ width });
    const arma = await viraConta43NoGet(ctx, page);
    arma();
    const lido = page.waitForResponse((r) => new URL(r.url()).pathname === "/api/v2/investido");
    await page.locator("#askbar-input").fill("@Faria Limer como está minha carteira?");
    await page.locator("#askbar-input").press("Enter");
    await lido; // a conversa pode já ter sido limpa quando a resposta chega: não há o que esperar nela
    await page.locator(".chat-empty").waitFor();
    await page.waitForTimeout(300);
    assert.equal(await page.evaluate(() => window.__viu), false, "o total da conta B apareceu no DOM");
    assert.equal(await page.evaluate(() => window.__viuErro), false, "na troca de conta o card não mostra nada, nem o erro");
    assert.ok(!(await page.locator("body").innerText()).includes("98.765,43"));
    assert.equal(await page.locator(".chat > .msg-piggy").count(), 0, "a conversa da conta anterior sumiu");
    await ctx.close();
    assert.deepEqual(erros, []);
  });
}
test("ISOLAMENTO, controle negativo: com a conferência da conta desligada no bundle, o total de B APARECE (o teste acima mede)", async () => {
  const { ctx, page } = await abrir({ width: 1440 });
  const servido = await servirSemConferencia(ctx);
  const arma = await viraConta43NoGet(ctx, page);
  await page.reload();
  await page.locator(".chat-empty").waitFor();
  arma();
  await page.locator("#askbar-input").fill("@Faria Limer como está minha carteira?");
  await page.locator("#askbar-input").press("Enter");
  await page.waitForFunction(() => window.__viu, null, { timeout: 5000 }).catch(() => {});
  assert.ok(servido.n > 0, "o bundle desligado não foi servido: o controle não mede nada");
  assert.equal(await page.evaluate(() => window.__viu), true, "sem a conferência o total de B tem de aparecer");
  await ctx.close();
});

for (const width of TELAS) {
  test(`${width}: o card entra com o fade dos botões uma vez e não anima ao voltar ao /piggy`, async () => {
    const { ctx, page } = await abrir({ width });
    await page.emulateMedia({ reducedMotion: "no-preference" }); // com movimento reduzido não há fade nem marca (PiggyChat.tsx)
    await perguntar(page);
    await page.locator(".chat > .msg-piggy .card-slot[data-revela]").waitFor();
    await card(page).locator(".card-agente-total").waitFor();
    await page.evaluate(() => { location.hash = "#/conta/perfil"; });
    await page.locator(".chat").waitFor({ state: "detached" });
    await page.evaluate(() => { location.hash = "#/piggy"; });
    await card(page).locator(".card-agente-total").waitFor();
    assert.equal(await page.locator(".chat > .msg-piggy .card-slot").count(), 1);
    assert.equal(await page.locator(".chat > .msg-piggy .card-slot[data-revela]").count(), 0);
    await ctx.close();
  });
}

// --- Cobertura do contrato, dos números e da entrada do card (mutantes que sobreviviam) ---
const BASE = { total: "100.00", motivos: [], por_banco: [], por_tipo: [{ tipo: "renda_fixa", rotulo: "Renda fixa", valor: "100.00", motivos: [] }] };
test("corpo fora do contrato: o card mostra o erro e não desenha número", async () => {
  const ruins = [
    { ...BASE, total: 100 }, { ...BASE, total: "abc" }, { ...BASE, motivos: undefined }, { ...BASE, por_tipo: null },
    { ...BASE, por_tipo: [{ tipo: "renda_fixa", rotulo: "Renda fixa", valor: "1e3", motivos: [] }] },
    { ...BASE, por_tipo: [{ tipo: "renda_fixa", rotulo: 5, valor: "1.00", motivos: [] }] },
    { ...BASE, motivos: [{}] }, { ...BASE, motivos: [7] },
    { ...BASE, por_tipo: [{ tipo: "renda_fixa", rotulo: "Renda fixa", valor: "1.00", motivos: [{ x: 1 }] }] },
    { ...BASE, por_tipo: [{ tipo: "renda_fixa", rotulo: "Renda fixa", valor: "1.00", motivos: "saldo_ausente" }] },
    { ...BASE, por_tipo: [{ tipo: "renda_fixa", rotulo: "Renda fixa", valor: "1.00" }] },
  ];
  for (const corpo of ruins) {
    const { ctx, page } = await abrir({ width: 1440, investido: corpo });
    await perguntar(page);
    await card(page).getByRole("button", { name: "Tentar de novo" }).waitFor({ timeout: 5000 });
    assert.match(await card(page).innerText(), /Não deu para carregar os dados agora\./, JSON.stringify(corpo));
    assert.equal(await card(page).locator(".card-agente-total, .card-agente-lista").count(), 0, JSON.stringify(corpo));
    await ctx.close();
  }
});

test("parte negativa: sem % e fora da barra; os outros % saem da soma das positivas; Barão soma com sinal", async () => {
  const neg = { total: "150.00", motivos: [], por_banco: [], por_tipo: [
    { tipo: "renda_fixa", rotulo: "Renda fixa", valor: "100.00", motivos: [] }, { tipo: "acoes", rotulo: "Ações", valor: "-50.00", motivos: [] }, { tipo: "fundos", rotulo: "Fundos", valor: "100.00", motivos: [] }] };
  let { ctx, page } = await abrir({ width: 390, investido: neg });
  await perguntar(page);
  await card(page).locator(".card-agente-total").waitFor();
  let r = await card(page).evaluate((c) => ({ linhas: [...c.querySelectorAll(".card-agente-lista li")].map((e) => e.innerText.replace(/\s+/g, " ")),
    seg: c.querySelectorAll(".comp-bar span").length, rotulo: c.querySelector(".comp-bar").getAttribute("aria-label") }));
  assert.deepEqual(r.linhas, ["Renda fixa 50% R$ 100,00", "Ações \u2212R$ 50,00", "Fundos 50% R$ 100,00"]);
  assert.equal(r.seg, 2);
  assert.equal(r.rotulo, "Renda fixa 50%, Fundos 50%");
  await ctx.close();
  const barao = { total: "100.00", motivos: [], por_banco: [], por_tipo: [
    { tipo: "renda_fixa", rotulo: "Renda fixa", valor: "100.00", motivos: [] }, { tipo: "tesouro", rotulo: "Tesouro Direto", valor: "-30.00", motivos: [] }] };
  ({ ctx, page } = await abrir({ width: 390, chat: resposta({ tipo: "renda_fixa" }), investido: barao }));
  await perguntar(page, "@Barão quanto tenho?");
  await card(page).locator(".card-agente-total").waitFor();
  assert.equal(await card(page).locator(".card-agente-total").innerText(), "R$ 70,00", "a mesma soma com sinal do total_renda_fixa do servidor");
  assert.deepEqual(await card(page).locator(".card-agente-lista li").allInnerTexts().then((l) => l.map((t) => t.replace(/\s+/g, " "))), ["Renda fixa 100% R$ 100,00", "Tesouro Direto \u2212R$ 30,00"]);
  await ctx.close();
});

test("390: positivo que arredonda a 0% mostra '<1%', nunca '0%'", async () => {
  const mini = { total: "100000.01", motivos: [], por_banco: [], por_tipo: [
    { tipo: "renda_fixa", rotulo: "Renda fixa", valor: "100000.00", motivos: [] }, { tipo: "fundos", rotulo: "Fundos", valor: "0.01", motivos: [] }] };
  const { ctx, page } = await abrir({ width: 390, investido: mini });
  await perguntar(page);
  await card(page).locator(".card-agente-total").waitFor();
  const r = await card(page).evaluate((c) => ({ linhas: [...c.querySelectorAll(".card-agente-lista li")].map((e) => e.innerText.replace(/\s+/g, " ")),
    rotulo: c.querySelector(".comp-bar").getAttribute("aria-label"), pagina: document.documentElement.scrollWidth > innerWidth }));
  assert.deepEqual(r.linhas, ["Renda fixa 100% R$ 100.000,00", "Fundos <1% R$ 0,01"]);
  assert.equal(r.rotulo, "Renda fixa 100%, Fundos <1%");
  assert.equal(r.pagina, false);
  await ctx.close();
});

test("selos: 'sem banco conectado' não vira selo no card; os outros motivos sim", async () => {
  const corpo = { ...BASE, motivos: ["sem_banco_conectado", "banco_desatualizado"] };
  const { ctx, page } = await abrir({ width: 1440, investido: corpo });
  await perguntar(page);
  await card(page).locator(".card-agente-total").waitFor();
  const t = await card(page).innerText();
  assert.match(t, /banco desatualizado/);
  assert.ok(!/sem banco conectado/.test(t), t);
  await ctx.close();
});

test("selos do Barão: só os da renda fixa e os do banco; o 'saldo ausente' dos fundos fica de fora", async () => {
  const corpo = { total: "100.00", motivos: ["saldo_ausente", "banco_desatualizado", "conexao_pausada"], por_banco: [], por_tipo: [
    { tipo: "renda_fixa", rotulo: "Renda fixa", valor: "100.00", motivos: ["banco_desatualizado"] }, { tipo: "fundos", rotulo: "Fundos", valor: null, motivos: ["saldo_ausente"] }] };
  const { ctx, page } = await abrir({ width: 1440, chat: resposta({ tipo: "renda_fixa" }), investido: corpo });
  await perguntar(page, "@Barão quanto tenho em renda fixa?");
  await card(page).locator(".card-agente-total").waitFor();
  const t = await card(page).innerText();
  assert.match(t, /banco desatualizado/);
  assert.match(t, /pausad/i);
  assert.ok(!/saldo ausente|sem saldo/i.test(t), t);
  await ctx.close();
});

for (const width of TELAS) {
  test(`${width}: o card entra primeiro e o texto se revela depois dele; os botões, depois do texto`, async () => {
    const longo = { json: { ...FARIA.json, reply: Array.from({ length: 80 }, (_, i) => `palavra${i}`).join(" ") } };
    const { ctx, page } = await abrir({ width, chat: longo });
    await page.emulateMedia({ reducedMotion: "no-preference" }); // o painel de teste abre com movimento reduzido (sem a animação do card)
    await perguntar(page);
    await page.locator(".chat > .msg-piggy .msg-text .rv").first().waitFor();
    const atrasos = await page.evaluate(() => {
      const li = document.querySelector(".chat > .msg-piggy"), d = (s) => li.querySelector(s).style.animationDelay;
      return { card: d(".card-slot[data-revela]"), palavra: d(".msg-text .rv"), botoes: d(".msg-acoes[data-revela]") };
    });
    // O card entra já; a 1ª palavra espera o fade dele (ACOES_ANIM 400); os botões, a última palavra: 400 + 2000 (teto) + 500 (REVELA_ANIM).
    assert.deepEqual(atrasos, { card: "0ms", palavra: "400ms", botoes: "2900ms" });
    await page.waitForFunction(() => getComputedStyle(document.querySelector(".card-slot")).opacity === "1", null, { timeout: 2000 });
    assert.ok(await page.locator(".chat > .msg-piggy .rv").count() > 0, "o card já entrou e o texto ainda está se revelando");
    await ctx.close();
  });
}

// --- Card acima do texto: o texto não desce quando o card carrega, nem herda o espaço de um card que não desenha nada ---
const LONGO = { json: { ...FARIA.json, reply: Array.from({ length: 40 }, (_, i) => `palavra${i}`).join(" ") } };
// O GET do card responde só quando `solta()` é chamado (ou nunca).
async function cardPreso(ctx) {
  let solta;
  const preso = new Promise((r) => { solta = r; });
  await ctx.unroute("**/api/v2/investido");
  await ctx.route("**/api/v2/investido", async (r) => { await preso; r.fulfill({ json: FIXTURE }).catch(() => {}); });
  return () => solta();
}
const topoTexto = (page) => page.evaluate(() => document.querySelector(".chat > .msg-piggy .msg-text").getBoundingClientRect().top);
const opacidadeDa1a = (page) => page.evaluate(() => getComputedStyle(document.querySelector(".chat > .msg-piggy .msg-text .rv")).opacity);

for (const width of TELAS) {
  test(`${width}: card lento: o texto espera o card carregar e não desce depois de visível`, async () => {
    const { ctx, page } = await abrir({ width, chat: LONGO });
    await page.emulateMedia({ reducedMotion: "no-preference" });
    const solta = await cardPreso(ctx);
    await perguntar(page);
    await page.locator(".chat > .msg-piggy .card-agente[aria-busy=true]").waitFor();
    await page.waitForTimeout(1200); // bem depois dos 400 ms em que o texto começaria
    assert.equal(await opacidadeDa1a(page), "0", "com o card carregando, nenhuma palavra aparece");
    solta();
    await card(page).locator(".card-agente-total").waitFor();
    const t0 = Date.now();
    await page.waitForFunction(() => +getComputedStyle(document.querySelector(".chat > .msg-piggy .msg-text .rv")).opacity > 0, null, { timeout: 3000 });
    const visivel = await topoTexto(page); // onde o texto estava quando a 1ª palavra apareceu
    await page.locator(".chat > .msg-piggy .rv").first().waitFor({ state: "detached", timeout: 6000 });
    // A revelação inteira depois do card: 400 (fade do card) + 40 × 30 + 500 = 2100 ms; o relógio sozinho a cortaria no meio.
    assert.ok(Date.now() - t0 >= 2000, `a revelação foi cortada ${Date.now() - t0} ms depois do card`);
    assert.equal(await topoTexto(page), visivel, "o texto não se moveu depois de visível");
    await ctx.close();
  });

  test(`${width}: card pendurado: o texto aparece mesmo assim, no máximo ~3 s depois`, async () => {
    const { ctx, page } = await abrir({ width, chat: LONGO });
    await page.emulateMedia({ reducedMotion: "no-preference" });
    await cardPreso(ctx); // nunca solta
    await perguntar(page);
    await page.locator(".chat > .msg-piggy .rv").first().waitFor({ state: "detached", timeout: 8000 });
    assert.equal(await card(page).getAttribute("aria-busy"), "true", "o card ainda carrega");
    assert.match(await page.locator(".chat > .msg-piggy .msg-text").innerText(), /^palavra0 .* palavra39$/);
    await ctx.close();
  });

  test(`${width}: card que não desenha nada (403): sem espaço em cima do texto, como resposta sem card`, async () => {
    const gap = async (o) => {
      const { ctx, page } = await abrir({ width, ...o });
      await perguntar(page);
      await page.waitForTimeout(300);
      const r = await page.evaluate(() => {
        const li = document.querySelector(".chat > .msg-piggy"), t = (s) => li.querySelector(s).getBoundingClientRect();
        return { gap: Math.round(t(".msg-text").top - t(".msg-autor").bottom), card: !!li.querySelector(".card-agente") };
      });
      await ctx.close();
      return r;
    };
    const semCard = await gap({ chat: resposta(null) }), com403 = await gap({ status: 403 });
    assert.equal(com403.card, false);
    assert.equal(com403.gap, semCard.gap, JSON.stringify({ semCard, com403 }));
  });
}
