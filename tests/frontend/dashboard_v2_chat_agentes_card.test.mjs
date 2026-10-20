// O card com os dados do tema abaixo da resposta do Faria Limer e do Barão (webapp: parts/CardAgente.tsx, parts/CardInvestido.tsx).
// O servidor só diz QUAL card (`card.tipo`); os números vêm do /api/v2/investido, conferida a conta da sessão (conta/api.ts: daConta).
// O backend é de mentira (ctx.route); o contrato é o de frontend/routes/agents.py. Tudo em 1440 e 390.
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { mkdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { chromium } from "playwright";
import { abrirPainel, exigeArtefatoEmDia, FRONTEND, RESPOSTAS } from "./_painel.mjs";

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
  test(`${width}: Faria Limer: o card fica entre o texto e "Perguntar ao X", com o número do /api/v2/investido`, async () => {
    const { ctx, page, erros, gets } = await abrir({ width });
    await perguntar(page);
    await card(page).locator(".card-agente-total").waitFor();
    const r = await page.evaluate(() => {
      const li = document.querySelector(".chat > .msg-piggy"), top = (s) => li.querySelector(s).getBoundingClientRect(), c = top(".card-agente"), l = li.getBoundingClientRect();
      const bar = [...li.querySelectorAll(".comp-bar span")];
      return {
        ordem: [top(".msg-text").bottom <= c.top, c.bottom <= top(".msg-acoes").top],
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
// `sem`: no controle negativo a 1ª leitura do perfil depois do GET (a de daConta, que o bundle patchado ignora) ainda diz 42 e as seguintes
// demoram 500 ms: sem isso a limpeza da conversa (a releitura do próprio painel) corre com o desenho do card e o teste não mediria nada.
async function viraConta43NoGet(ctx, page, sem = false) {
  // O GET do card devolve o total da conta B e, no mesmo instante, a sessão passa a ser a 43 (outra aba entrou em outra conta).
  const observa = () => {
    window.__viu = false;
    window.__viuErro = false;
    new MutationObserver(() => { if (document.body.textContent.includes("98.765,43")) window.__viu = true; if (document.body.textContent.includes("Não deu para carregar")) window.__viuErro = true; }).observe(document, { subtree: true, childList: true, characterData: true });
  };
  await page.addInitScript(observa); // vale nas próximas navegações...
  await page.evaluate(observa); // ...e nesta
  let virou = false, armado = false; // só o GET depois de `arma()` (o do card) vira a sessão: o painel lê o /investido em outras telas
  let depois = 0;
  await ctx.route("**/auth/dashboard-profile", async (r) => {
    const n = virou ? ++depois : 0, conta = virou && !(sem && n === 1) ? 43 : 42;
    if (sem && n > 1) await new Promise((ok) => setTimeout(ok, 500));
    await r.fulfill({ json: { user_id: conta, display_name: conta === 43 ? "Outra" : "Marina", email: null, whatsapp_linked: true } });
  });
  await ctx.route("**/api/v2/investido", (r) => { virou = armado; r.fulfill({ json: DA_B }); });
  return () => { armado = true; };
}
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
  const js = readFileSync(join(FRONTEND, "dashboard-app.js"), "utf8");
  const desligado = js.replace(/user_id!==(\w+)\)throw new (\w+);return/, "user_id===-1)throw new $2;return");
  assert.notEqual(desligado, js, "o padrão da conferência não está mais no bundle: ajuste o teste");
  await ctx.route("**/dashboard-app.js", (r) => r.fulfill({ contentType: "application/javascript", body: desligado }));
  const arma = await viraConta43NoGet(ctx, page, true);
  await page.goto(page.url()); // recarrega com o bundle patchado (as rotas do contexto seguem valendo)
  await page.locator(".chat-empty").waitFor();
  arma();
  await page.locator("#askbar-input").fill("@Faria Limer como está minha carteira?");
  await page.locator("#askbar-input").press("Enter");
  await page.waitForFunction(() => window.__viu, null, { timeout: 5000 }).catch(() => {});
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
  test(`${width}: o card só fica visível quando o texto termina (invisível, sem foco, até lá)`, async () => {
    const longo = { json: { reply: Array.from({ length: 80 }, (_, i) => `palavra${i}`).join(" "), context: "c", redirects: [], usage: { used: 1, limit: 100 }, card: { tipo: "investido" } } };
    const { ctx, page } = await abrir({ width, chat: longo });
    await page.emulateMedia({ reducedMotion: "no-preference" }); // o painel de teste abre com movimento reduzido (sem a animação do card)
    await perguntar(page);
    const slot = page.locator(".chat > .msg-piggy .card-slot");
    await slot.waitFor({ state: "attached" });
    const antes = await slot.evaluate((e) => {
      const b = document.createElement("button"); // o "Tentar de novo" do card é um botão dentro do slot; o que mede é o slot
      e.appendChild(b); b.focus();
      const cs = getComputedStyle(e), r = { visibilidade: cs.visibility, opacidade: cs.opacity, foco: document.activeElement === b, delay: e.style.animationDelay, revela: e.hasAttribute("data-revela") };
      b.remove();
      return r;
    });
    assert.deepEqual(antes, { visibilidade: "hidden", opacidade: "0", foco: false, delay: "2500ms", revela: true });
    await page.waitForFunction(() => getComputedStyle(document.querySelector(".card-slot")).visibility === "visible", null, { timeout: 6000 });
    await ctx.close();
  });
}
