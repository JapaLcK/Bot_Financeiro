// O painel lateral da conversa (#/piggy, só a partir de 1181 px): a geometria compartilhada da coluna, da barra, da fileira e da lista do @
// (chat.css), os agentes que dá para chamar, o índice dos cards e o isolamento entre contas (webapp: parts/PainelChat.tsx).
// O backend é de mentira (ctx.route); o contrato é o de frontend/routes/agents.py.
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { chromium } from "playwright";
import { abrirPainel, exigeArtefatoEmDia } from "./_painel.mjs";

let browser;
before(async () => { exigeArtefatoEmDia(); browser = await chromium.launch(); });
after(() => browser?.close());

const SHOTS = process.env.PAINEL_SHOTS; // pasta dos screenshots (opcional)
// kind, nome, custo. Orçamento 7 com detetive+xerife ativos (usa 5): Repórter, Carteiro e Banqueiro cabem; Barão e Faria Limer não.
const CAT = [["xerife", "Xerife", 2], ["reporter", "Repórter", 1], ["carteiro", "Carteiro", 1], ["detetive", "Detetive", 3], ["cofre", "Banqueiro", 2], ["barao", "Barão", 3], ["faria_limer", "Faria Limer", 4]];
const LONGA = Array.from({ length: 14 }, (_, i) => `Linha ${i + 1} da resposta, com texto para ocupar a coluna.`).join("\n");

// A conta 43 tem outro catálogo (só o Xerife ativo e o Repórter por ativar, custo 5): as tags dela não se confundem com as da 42.
const catalogo43 = { catalog: [{ kind: "xerife", nome: "Xerife", disponivel: true, status: "active", energy_cost: 2 }, { kind: "reporter", nome: "Repórter", disponivel: true, status: null, energy_cost: 5 }],
  can_chat: true, can_activate: true, energy_enabled: true, energy_budget: 99, energy_used: 2 };
const perfil = (id, nome) => ({ json: { user_id: id, display_name: nome, email: null, whatsapp_linked: true } });

// `lista`: sobrescreve o GET do catálogo; `chat(corpo, n, kind)`: o POST de chat; `perfilRota`: o handler do perfil (para prender a leitura).
async function abrir({ width, demo = false, ativos = ["detetive", "xerife"], lista, chat, perfilRota, opts = {} }) {
  const { ctx, page, erros, ir } = await abrirPainel(browser, { width, demo, espera: "#askbar-input", ...opts });
  const pedidos = [], chats = [];
  page.on("request", (r) => { const u = new URL(r.url()); if (/^\/agents\/\d/.test(u.pathname)) pedidos.push(`${r.method()} ${u.pathname}`); });
  await ctx.route("**/auth/dashboard-profile", perfilRota ?? ((r) => r.fulfill(perfil(42, "Marina"))));
  await ctx.route(/\/agents\/42(\/|$)/, (r) => {
    const m = new URL(r.request().url()).pathname.match(/^\/agents\/42(?:\/(\w+)\/(chat|activate))?$/);
    if (!m?.[1]) {
      if (lista) return r.fulfill(lista);
      return r.fulfill({ json: {
        // saved_365d e fired_30d existem no corpo real e NUNCA aparecem no painel.
        catalog: [...CAT.map(([kind, nome, energy_cost]) => ({ kind, nome, disponivel: true, status: ativos.includes(kind) ? "active" : null, energy_cost, saved_365d: 1234.56, fired_30d: 9 })),
          { kind: "aviador", nome: "Aviador", disponivel: false, status: null, energy_cost: 0 }],
        can_chat: true, can_activate: true, energy_enabled: true, energy_budget: 7,
        energy_used: ativos.reduce((s, k) => s + CAT.find((c) => c[0] === k)[2], 0),
      } });
    }
    const kind = m[1], corpo = r.request().postDataJSON();
    chats.push({ kind, ...corpo });
    const n = chats.length;
    return r.fulfill((chat && chat(corpo, n, kind)) || { json: { reply: `Resposta ${n} do ${kind}.\n${LONGA}`, context: `ctx-${n}`, redirects: [] } });
  });
  await ir("/piggy");
  await page.locator(".chat-empty").waitFor();
  return { ctx, page, erros, pedidos, chats };
}
const campo = (page) => page.locator("#askbar-input");
const enviar = async (page, t) => { await campo(page).fill(t); await campo(page).press("Enter"); };
const painel = (page) => page.locator(".chat-painel");
const botao = (page, nome) => painel(page).getByRole("button", { name: `Perguntar ao ${nome}` });
const caixa = (page, sel) => page.locator(sel).first().evaluate((e) => { const r = e.getBoundingClientRect(); return { left: r.left, right: r.right, width: r.width, top: r.top }; });
const foco = (page) => page.evaluate(() => { window.dispatchEvent(new Event("visibilitychange")); });
const shot = async (page, nome, width) => { if (SHOTS) { mkdirSync(SHOTS, { recursive: true }); await page.screenshot({ path: join(SHOTS, `${nome}-${width}.png`) }); } };
const alinhados = (a, b, msg) => { assert.ok(Math.abs(a.left - b.left) <= 1 && Math.abs(a.width - b.width) <= 1, `${msg}: ${JSON.stringify({ a, b })}`); };

// --- Alinhamento: a coluna, a barra, a fileira e a lista do @ saem do mesmo --chat-x/--chat-w ---
for (const width of [1440, 1366, 1181, 1180]) {
  test(`${width}: a barra, a fileira de perguntas e a lista do @ alinham com a coluna (±1 px), no vazio e na conversa longa; sem rolagem horizontal`, async () => {
    const { ctx, page, erros } = await abrir({ width });
    const semRolagem = () => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth);
    alinhados(await caixa(page, ".askbar"), await caixa(page, ".chat-empty"), "barra × vazio");
    alinhados(await caixa(page, ".chat-follow"), await caixa(page, ".chat-empty"), "fileira × vazio");
    await campo(page).click();
    await campo(page).pressSequentially("@");
    await page.locator("#mencoes-lista").waitFor();
    alinhados(await caixa(page, ".mencoes"), await caixa(page, ".chat-empty"), "lista do @ × vazio");
    assert.ok(await semRolagem());
    await campo(page).fill("");
    for (const [i, q] of ["um", "dois", "tres", "quatro"].entries()) { // uma por vez: com a anterior em voo o mesmo agente recusa a pergunta
      await enviar(page, `@Detetive ${q}`);
      await page.locator(".chat > .msg-piggy .msg-text", { hasText: `Resposta ${i + 1} do detetive.` }).waitFor();
    }
    assert.ok(await page.evaluate(() => document.documentElement.scrollHeight > innerHeight), "a conversa tem de rolar");
    alinhados(await caixa(page, ".askbar"), await caixa(page, ".chat"), "barra × conversa");
    await campo(page).click();
    await campo(page).pressSequentially("@");
    await page.locator("#mencoes-lista").waitFor();
    alinhados(await caixa(page, ".mencoes"), await caixa(page, ".chat"), "lista do @ × conversa");
    assert.ok(await semRolagem());
    if (width >= 1181) {
      const c = await caixa(page, ".chat"), p = await caixa(page, ".chat-painel");
      assert.ok(p.left >= c.right + 23 && p.right <= width, `painel fora do lugar: ${JSON.stringify({ c, p })}`);
      assert.ok(await page.evaluate(() => [...document.querySelectorAll(".msg-piggy")].every((e) => e.scrollWidth <= e.clientWidth + 1)), "estouro numa resposta");
    } else {
      assert.equal(await painel(page).count(), 0);
      assert.equal(await page.evaluate(() => document.body.hasAttribute("data-chat-painel")), false);
    }
    await shot(page, "conversa", width);
    await ctx.close();
    assert.deepEqual(erros, []);
  });
}

test("1181 e 1440: com a coluna centralizada (sem painel) e com ele, a coluna não passa de 760 px e o painel tem 264 px", async () => {
  for (const width of [1440, 1181]) {
    const { ctx, page } = await abrir({ width });
    await painel(page).locator(".painel-item").first().waitFor(); // o painel aparece já carregando: espera o catálogo
    const c = await caixa(page, ".chat-empty"), p = await caixa(page, ".chat-painel");
    assert.ok(c.width <= 760.5, `coluna ${c.width}`);
    assert.equal(Math.round(p.right - p.left), 264);
    await ctx.close();
  }
});

test("1180 e 390: sem painel e sem nenhum GET /agents (sem \"@\"); 1440: um GET /agents/42 e nenhum POST", async () => {
  for (const width of [1180, 390]) {
    const { ctx, page, pedidos } = await abrir({ width });
    await page.waitForTimeout(300);
    assert.deepEqual(pedidos, [], String(width));
    assert.equal(await painel(page).count(), 0);
    await ctx.close();
  }
  const { ctx, page, pedidos } = await abrir({ width: 1440 });
  await painel(page).locator(".painel-item").first().waitFor(); // o painel aparece já carregando: espera o catálogo
  assert.deepEqual(pedidos, ["GET /agents/42"]);
  await ctx.close();
});

test("390: a barra segue a regra do celular (esquerda e direita na margem), sem tocar no painel", async () => {
  const { ctx, page } = await abrir({ width: 390 });
  const b = await caixa(page, ".askbar");
  assert.ok(Math.abs(b.left - 16) <= 1 && Math.abs(b.right - (390 - 16)) <= 1, JSON.stringify(b));
  await ctx.close();
});

// --- Fallback A: sem catálogo a tela é a de uma coluna só ---
test("1440: catálogo em 404: nenhum painel, nenhuma reserva de espaço, a coluna centralizada", async () => {
  const { ctx, page } = await abrir({ width: 1440, lista: { status: 404, json: { detail: "beta" } } });
  await page.waitForTimeout(400);
  assert.equal(await painel(page).count(), 0);
  assert.equal(await page.evaluate(() => document.body.hasAttribute("data-chat-painel")), false);
  const c = await caixa(page, ".chat-empty"), rail = await page.evaluate(() => document.querySelector(".rail").getBoundingClientRect().right);
  const meio = (rail + 1440) / 2;
  assert.ok(Math.abs((c.left + c.right) / 2 - meio) <= 1, `centro ${(c.left + c.right) / 2} × ${meio}`);
  alinhados(await caixa(page, ".askbar"), c, "barra × vazio");
  await shot(page, "sem-painel", 1440);
  await ctx.close();
});

// --- Agentes ---
test("1440: catálogo em 500/rede: o painel fica (carregando, depois \"Tentar de novo\") e a coluna não salta; tentar de novo traz os agentes", async () => {
  let falha = true;
  const { ctx, page } = await abrir({ width: 1440, lista: { status: 500, json: { detail: "x" } } });
  await ctx.route(/\/agents\/42$/, (r) => r.fulfill(falha ? { status: 500, json: { detail: "x" } } : { json: catalogo43 }));
  const inicio = await caixa(page, ".askbar");
  assert.ok(await painel(page).innerText() !== "", "enquanto carrega o painel não é um vão vazio");
  const tentar = painel(page).getByRole("button", { name: "Tentar de novo" });
  await tentar.waitFor({ timeout: 10000 }); // depois das tentativas (meQuery.retry)
  assert.equal((await caixa(page, ".askbar")).left, inicio.left, "a barra não saltou");
  assert.equal(await page.evaluate(() => document.body.hasAttribute("data-chat-painel")), true);
  falha = false;
  await tentar.click();
  await botao(page, "Xerife").waitFor();
  assert.equal((await caixa(page, ".askbar")).left, inicio.left);
  await ctx.close();
});

test("1440: os grupos e as tags seguem o catálogo; o agente indisponível não aparece", async () => {
  const { ctx, page } = await abrir({ width: 1440 });
  await botao(page, "Xerife").waitFor(); // o painel aparece já carregando: espera o catálogo
  assert.deepEqual(await painel(page).locator("h2").allTextContents(), ["Agentes ativos", "Ativar para conversar"]);
  const itens = await painel(page).locator(".painel-item").evaluateAll((es) => es.map((e) => [e.getAttribute("aria-label"), e.querySelector(".msg-tag")?.textContent ?? null]));
  assert.deepEqual(itens, [["Perguntar ao Xerife", null], ["Perguntar ao Detetive", null], ["Perguntar ao Repórter", "⚡1"], ["Perguntar ao Carteiro", "⚡1"], ["Perguntar ao Banqueiro", "⚡2"], ["Perguntar ao Barão", "⚡3"], ["Perguntar ao Faria Limer", "⚡4"]]);
  await ctx.close();
});

test("1440: o painel não mostra valor nenhum (nem o saved_365d do catálogo, nem a energia livre)", async () => {
  const { ctx, page } = await abrir({ width: 1440 });
  await painel(page).locator(".painel-item").first().waitFor(); // o painel aparece já carregando: espera o catálogo
  const t = await painel(page).innerText();
  assert.doesNotMatch(t, /R\$|\d+,\d{2}|1234|\b9\b/);
  assert.doesNotMatch(t, /livre|energia/i);
  await ctx.close();
});

test("1440: clicar num agente insere a menção: campo vazio, texto, menção do começo e token em edição; foco e cursor no fim, lista fechada", async () => {
  const { ctx, page, chats } = await abrir({ width: 1440, ativos: ["faria_limer", "detetive"] });
  await painel(page).locator(".painel-item").first().waitFor(); // o painel aparece já carregando: espera o catálogo
  const estado = () => page.evaluate(() => { const i = document.getElementById("askbar-input"); return { v: i.value, foco: document.activeElement === i, ini: i.selectionStart, fim: i.selectionEnd, lista: !!document.getElementById("mencoes-lista") }; });
  const fim = (v) => ({ v, foco: true, ini: v.length, fim: v.length, lista: false });
  await botao(page, "Faria Limer").click();
  assert.deepEqual(await estado(), fim("@Faria Limer "));
  await campo(page).fill("quanto tenho?");
  await botao(page, "Faria Limer").click();
  assert.deepEqual(await estado(), fim("@Faria Limer quanto tenho?"));
  await campo(page).fill("@Detetive x");
  await botao(page, "Faria Limer").click();
  assert.deepEqual(await estado(), fim("@Faria Limer x"));
  await campo(page).fill("");
  await campo(page).pressSequentially("@fa");
  await page.locator("#mencoes-lista").waitFor();
  await botao(page, "Detetive").click();
  assert.deepEqual(await estado(), fim("@Detetive "));
  await campo(page).fill("");
  await botao(page, "Faria Limer").click();
  await campo(page).pressSequentially("como está?");
  await campo(page).press("Enter");
  await page.locator(".chat > .msg-piggy .msg-text", { hasText: "Resposta 1 do faria_limer." }).waitFor();
  assert.deepEqual(chats.map((c) => [c.kind, c.message]), [["faria_limer", "como está?"]]);
  await ctx.close();
});

test("1440: um \"nome\" que não é de agente no evento é ignorado", async () => {
  const { ctx, page } = await abrir({ width: 1440 });
  await campo(page).fill("oi");
  await page.evaluate(() => window.dispatchEvent(new CustomEvent("dash:mencao", { detail: { nome: "Fulano" } })));
  await page.waitForTimeout(100);
  assert.equal(await campo(page).inputValue(), "oi");
  await ctx.close();
});

// --- Cards ---
const CARDS = (corpo, n, kind) => kind === "faria_limer" ? { json: { reply: `Investido.\n${LONGA}`, context: "c1", redirects: [], card: { tipo: "investido" } } }
  : kind === "reporter" ? { json: { reply: `Resumo.\n${LONGA}`, context: "c2", redirects: [], card: { tipo: "resumo_mes", mes: "2026-09", motivos: [] } } } : null;

test("1440: os cards viram itens do índice, em ordem; o clique rola até a resposta e põe o foco nela", async () => {
  const { ctx, page } = await abrir({ width: 1440, ativos: ["faria_limer", "reporter", "xerife"], chat: CARDS });
  await painel(page).locator(".painel-item").first().waitFor(); // o painel aparece já carregando: espera o catálogo
  assert.equal(await painel(page).getByText("Nesta conversa").count(), 0, "sem card, sem bloco");
  await enviar(page, "@Faria Limer carteira?");
  await page.locator(".chat > .msg-piggy").nth(0).locator(".card-agente").waitFor();
  await enviar(page, "@Repórter e o mês?");
  await page.locator(".chat > .msg-piggy").nth(1).locator(".card-agente-tit").waitFor();
  await enviar(page, "@Xerife e mais?"); // sem card: fica fora do índice, e dá conteúdo abaixo para a rolagem chegar ao topo
  await page.locator(".chat > .msg-piggy").nth(2).locator(".msg-text", { hasText: "Resposta 3 do xerife." }).waitFor();
  const titulos = painel(page).locator("section .painel-item span:first-of-type");
  assert.deepEqual(await titulos.allTextContents(), ["Investimentos", "Resumo de setembro de 2026"]);
  assert.doesNotMatch(await painel(page).locator("section").innerText(), /R\$|\d+,\d{2}/);
  await painel(page).locator("section .painel-item").nth(0).click();
  await page.waitForFunction(() => { const t = document.querySelectorAll(".chat > .msg-piggy")[0].getBoundingClientRect().top; return t >= 64 && t <= 90; }, null, { timeout: 4000 }).catch(() => {}); // a rolagem suave termina
  const r = await page.evaluate(() => { const li = document.querySelectorAll(".chat > .msg-piggy")[0]; return { top: li.getBoundingClientRect().top, foco: document.activeElement === li }; });
  assert.ok(r.top >= 64 && r.top <= 90, `topo ${r.top}`);
  assert.ok(r.foco, "o foco vai para a resposta");
  await shot(page, "cards", 1440);
  await ctx.close();
});

test("1181: o card dentro da resposta não estoura a coluna", async () => {
  const { ctx, page } = await abrir({ width: 1181, ativos: ["faria_limer"], chat: CARDS });
  await enviar(page, "@Faria Limer carteira?");
  await page.locator(".chat > .msg-piggy .card-agente").waitFor();
  assert.ok(await page.evaluate(() => { const c = document.querySelector(".chat").getBoundingClientRect(), k = document.querySelector(".card-agente").getBoundingClientRect(); return k.left >= c.left - 1 && k.right <= c.right + 1; }));
  await ctx.close();
});

test("1440: \"Tentar de novo\": durante o erro nenhum item no índice; depois da resposta, um só", async () => {
  let n = 0;
  const { ctx, page } = await abrir({ width: 1440, ativos: ["faria_limer"], chat: (c, i, kind) => (++n === 1 ? { status: 503, json: { detail: { message: "Falhou.", retryable: true } } } : CARDS(c, i, kind)) });
  await enviar(page, "@Faria Limer carteira?");
  await page.getByRole("button", { name: "Tentar de novo" }).waitFor();
  assert.equal(await painel(page).getByText("Nesta conversa").count(), 0);
  await page.getByRole("button", { name: "Tentar de novo" }).click();
  await page.locator(".chat > .msg-piggy .card-agente").waitFor();
  assert.deepEqual(await painel(page).locator("section .painel-item").count(), 1);
  await ctx.close();
});

// --- Isolamento entre contas ---
test("1440: a sessão vira a conta 43 com a releitura presa: o painel some (hidden e inert) e, solto, mostra só o da 43, sem card da 42", async () => {
  const { ctx, page } = await abrir({ width: 1440, ativos: ["faria_limer"], chat: CARDS });
  await painel(page).locator(".painel-item").first().waitFor(); // o painel aparece já carregando: espera o catálogo
  await enviar(page, "@Faria Limer carteira?");
  await page.locator(".chat > .msg-piggy .card-agente").waitFor();
  await painel(page).locator("section").waitFor();
  await ctx.route(/\/agents\/43(\/|$)/, (r) => r.fulfill({ json: catalogo43 }));
  let solta;
  const preso = new Promise((ok) => { solta = ok; });
  await ctx.route("**/auth/dashboard-profile", async (r) => { await preso; return r.fulfill(perfil(43, "Outra")); });
  await foco(page);
  await page.waitForFunction(() => document.querySelector(".chat-painel[data-reconferindo]"));
  const p = await painel(page).evaluate((e) => ({ inert: e.inert, visibility: getComputedStyle(e).visibility }));
  assert.deepEqual(p, { inert: true, visibility: "hidden" });
  assert.equal(await page.getByRole("button", { name: /Perguntar ao/ }).count(), 0, "nenhum nome clicável");
  solta();
  await page.locator(".chat-empty").waitFor(); // a conversa da 42 sumiu
  await botao(page, "Repórter").waitFor();
  assert.deepEqual(await painel(page).locator(".painel-item").evaluateAll((es) => es.map((e) => [e.getAttribute("aria-label"), e.querySelector(".msg-tag")?.textContent ?? null])), [["Perguntar ao Xerife", null], ["Perguntar ao Repórter", "⚡5"]]);
  assert.equal(await painel(page).getByText("Nesta conversa").count(), 0);
  assert.equal(await painel(page).getByText("Faria Limer").count(), 0);
  await ctx.close();
});

test("1440: a releitura dá 401: nem agente nem card da 42 na tela", async () => {
  const { ctx, page } = await abrir({ width: 1440, ativos: ["faria_limer"], chat: CARDS });
  await painel(page).locator(".painel-item").first().waitFor(); // o painel aparece já carregando: espera o catálogo
  await enviar(page, "@Faria Limer carteira?");
  await page.locator(".chat > .msg-piggy .card-agente").waitFor();
  await painel(page).locator("section").waitFor();
  await ctx.route("**/auth/dashboard-profile", (r) => r.fulfill({ status: 401, json: { detail: "Não autenticado" } }));
  await foco(page);
  await page.waitForTimeout(800);
  assert.equal(await page.getByRole("button", { name: /Perguntar ao/ }).count(), 0);
  assert.equal(await page.getByText("Investimentos").count(), 0);
  assert.equal(await page.getByText("Faria Limer").count(), 0);
  await ctx.close();
});

test("1440: perfil em voo na montagem: o painel fica sem dado e sem GET /agents até a conta ser conferida", async () => {
  let solta;
  const preso = new Promise((ok) => { solta = ok; });
  const { ctx, page, pedidos } = await abrir({ width: 1440, perfilRota: async (r) => { await preso; return r.fulfill(perfil(42, "Marina")); }, opts: { espera: "body" } });
  await page.waitForTimeout(600);
  assert.equal(await page.getByRole("button", { name: /Perguntar ao/ }).count(), 0);
  assert.deepEqual(pedidos, []);
  solta();
  await botao(page, "Xerife").waitFor();
  assert.deepEqual(pedidos, ["GET /agents/42"]);
  await ctx.close();
});

test("1440: volta ao /piggy com o perfil em releitura presa (a sessão virou a 43): o catálogo guardado da 42 não aparece nem é relido", async () => {
  const { ctx, page, pedidos } = await abrir({ width: 1440 });
  await botao(page, "Xerife").waitFor(); // a 42 está no cache
  await page.evaluate(() => { location.hash = "#/conta/perfil"; });
  await page.locator(".chat-empty").waitFor({ state: "detached" });
  let solta;
  const preso = new Promise((ok) => { solta = ok; });
  await ctx.route("**/auth/dashboard-profile", async (r) => { await preso; return r.fulfill(perfil(43, "Outra")); });
  await ctx.route(/\/agents\/43(\/|$)/, (r) => r.fulfill({ json: catalogo43 }));
  await page.evaluate(() => { location.hash = "#/piggy"; });
  await page.locator(".chat-empty").waitFor();
  await page.waitForTimeout(500);
  assert.equal(await page.getByRole("button", { name: /Perguntar ao/ }).count(), 0, "nada da 42 enquanto a conta não é conferida");
  assert.deepEqual(pedidos, ["GET /agents/42"], "nenhum GET /agents/42 novo");
  solta();
  await botao(page, "Repórter").waitFor();
  assert.equal(await botao(page, "Faria Limer").count(), 0);
  await ctx.close();
});

// --- Protótipo ---
test("1440: protótipo: os 7 agentes, nenhuma rede e nenhum bloco de cards", async () => {
  const { ctx, page, erros, ir } = await abrirPainel(browser, { width: 1440, demo: true, espera: "#askbar-input" });
  const pedidos = [];
  page.on("request", (r) => { if (/\/(agents\/\d|auth\/)/.test(r.url())) pedidos.push(r.url()); });
  await ir("/piggy");
  await page.locator(".chat-empty").waitFor();
  await painel(page).locator(".painel-item").first().waitFor(); // o painel aparece já carregando: espera o catálogo
  assert.equal(await painel(page).locator(".painel-item").count(), 7);
  assert.equal(await painel(page).getByText("Nesta conversa").count(), 0);
  assert.deepEqual(pedidos, []);
  await ctx.close();
  assert.deepEqual(erros, []);
});
