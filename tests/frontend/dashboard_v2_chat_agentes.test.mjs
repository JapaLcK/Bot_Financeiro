// "@" na barra do Piggy (#/piggy e as outras páginas): a lista de agentes, a menção em qualquer lugar da
// mensagem, as rotas que já existem em /agents/{uid} e os estados da resposta. O backend é de mentira
// (ctx.route); o contrato é o de frontend/routes/agents.py. Tudo em 1440 e 390.
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { abrirPainel, exigeArtefatoEmDia } from "./_painel.mjs";

let browser;
before(async () => { exigeArtefatoEmDia(); browser = await chromium.launch(); });
after(() => browser?.close());

const TELAS = [1440, 390];
// kind, nome, custo de energia. Orçamento 7 com detetive+xerife ativos (usa 5): Repórter, Carteiro e Banqueiro cabem; Barão e Faria Limer não.
const CAT = [["xerife", "Xerife", 2], ["reporter", "Repórter", 1], ["carteiro", "Carteiro", 1], ["detetive", "Detetive", 3], ["cofre", "Banqueiro", 2], ["barao", "Barão", 3], ["faria_limer", "Faria Limer", 4]];

// `lista`: sobrescreve o GET (status/json); `chat(corpo, n, kind)`: sobrescreve o POST de chat; `segura`: promise (ou `kind => promise`) que segura o chat; `ativos`: quem começa ativo.
// A sessão vira a conta 43 noutra aba: o perfil passa a responder 43 e o /agents/43 existe. Devolve os POSTs de chat da 43.
async function paraConta43(ctx) {
  const da43 = [];
  await ctx.route("**/auth/dashboard-profile", (r) => r.fulfill({ json: { user_id: 43, display_name: "Outra", email: null, whatsapp_linked: true } }));
  await ctx.route(/\/agents\/43(\/|$)/, (r) => {
    const m = new URL(r.request().url()).pathname.match(/^\/agents\/43\/(\w+)\/chat$/);
    if (!m) return r.fulfill({ json: { catalog: CAT.map(([kind, nome, energy_cost]) => ({ kind, nome, disponivel: true, status: "active", energy_cost })), can_chat: true, can_activate: true, energy_enabled: false, energy_budget: 99, energy_used: 0 } });
    da43.push({ kind: m[1], context: r.request().postDataJSON().context });
    return r.fulfill({ json: { reply: "Oi, conta nova.", context: "ctx-43", redirects: [] } });
  });
  return da43;
}
// A Conta relê /auth/dashboard-profile (staleTime 0) sem recarregar a página; depois volta ao chat.
async function idaEVoltaPelaConta(page) {
  await page.evaluate(() => { location.hash = "#/conta/perfil"; });
  await page.waitForResponse((r) => r.url().includes("/auth/dashboard-profile"));
  await page.evaluate(() => { location.hash = "#/piggy"; });
}

async function abrir({ width, demo = false, ativos = ["detetive", "xerife"], lista, chat, segura, ativarErro, soltaAtivar, opts = {} }) {
  const { ctx, page, erros, ir } = await abrirPainel(browser, { width, demo, espera: "#askbar-input", ...opts });
  const pedidos = [], chats = [], ativacoes = [];
  page.on("request", (r) => { const u = new URL(r.url()); if (/^\/agents\/\d/.test(u.pathname)) pedidos.push(`${r.method()} ${u.pathname}`); });
  await ctx.route("**/auth/dashboard-profile", (r) => r.fulfill({ json: { user_id: 42, display_name: "Marina", email: null, whatsapp_linked: true } }));
  await ctx.route(/\/agents\/42(\/|$)/, async (r) => {
    const m = new URL(r.request().url()).pathname.match(/^\/agents\/42(?:\/(\w+)\/(chat|activate))?$/);
    if (!m?.[1]) {
      if (lista) return r.fulfill(lista);
      return r.fulfill({ json: {
        catalog: [...CAT.map(([kind, nome, energy_cost]) => ({ kind, nome, disponivel: true, status: ativos.includes(kind) ? "active" : null, energy_cost })),
          { kind: "aviador", nome: "Aviador", disponivel: false, status: null, energy_cost: 0 }],
        can_chat: true, can_activate: true, energy_enabled: true, energy_budget: 7,
        energy_used: ativos.reduce((s, k) => s + CAT.find((c) => c[0] === k)[2], 0),
      } });
    }
    const [, kind, acao] = m;
    if (acao === "activate") {
      ativacoes.push(kind);
      await soltaAtivar;
      if (ativarErro) return r.fulfill(ativarErro);
      ativos.push(kind);
      return r.fulfill({ json: { ok: true, agent: { kind } } });
    }
    const corpo = r.request().postDataJSON();
    chats.push({ kind, ...corpo, csrf: r.request().headers()["x-csrf-token"] });
    await (typeof segura === "function" ? segura(kind) : segura);
    const n = chats.length;
    return r.fulfill((chat && chat(corpo, n, kind)) || { json: { reply: `Resposta ${n} do ${kind}.`, context: `ctx-${n}`, redirects: [], usage: { used: n, limit: 100 } } });
  });
  await ir("/piggy");
  await page.locator(".chat-empty").waitFor();
  return { ctx, page, erros, pedidos, chats, ativacoes };
}
const campo = (page) => page.locator("#askbar-input");
const digitar = async (page, t) => { await campo(page).click(); await campo(page).pressSequentially(t); };
const lista = (page) => page.locator("#mencoes-lista");
const nomes = (page) => page.locator("#mencoes-lista [role=option] > span:first-of-type").allTextContents();
const resposta = (page, i = -1) => page.locator(".chat > .msg-piggy").nth(i);
const enviar = async (page, t) => { await campo(page).fill(t); await campo(page).press("Enter"); };

for (const width of TELAS) {
  test(`${width}: "@" abre a lista com os grupos na ordem, sem o agente indisponível; "@det" só o Detetive`, async () => {
    const { ctx, page, erros } = await abrir({ width });
    await digitar(page, "@");
    await lista(page).waitFor();
    const r = await page.evaluate(() => {
      const box = (s) => document.querySelector(s).getBoundingClientRect(), l = box(".mencoes"), b = box(".askbar"), i = document.querySelector("#askbar-input");
      return {
        grupos: [...document.querySelectorAll(".mencoes .cmdk-group")].map((e) => e.textContent),
        expandido: i.getAttribute("aria-expanded"), controla: i.getAttribute("aria-controls"), papel: i.getAttribute("role"),
        dentro: l.left >= 0 && l.right <= innerWidth && l.top >= 0, acima: l.bottom <= b.top, mesmaColuna: Math.abs((l.left + l.right) / 2 - (b.left + b.right) / 2) <= 1 && Math.abs(l.width - b.width) <= 1,
        z: +getComputedStyle(document.querySelector(".mencoes")).zIndex, zBarra: +getComputedStyle(document.querySelector(".askbar")).zIndex,
        rodape: getComputedStyle(document.querySelector(".mencoes .cmdk-foot")).display,
        imgs: [...document.querySelectorAll(".mencoes img")].map((e) => [e.width, e.getAttribute("src").split("/").slice(-2).join("/")]).slice(0, 2),
        fileira: getComputedStyle(document.querySelector(".chat-follow")).visibility,
      };
    });
    assert.deepEqual(r.grupos, ["Agentes ativos", "Ativar para conversar"]);
    assert.deepEqual(await nomes(page), ["Xerife", "Detetive", "Repórter", "Carteiro", "Banqueiro", "Barão", "Faria Limer"]);
    assert.deepEqual([r.expandido, r.controla, r.papel], ["true", "mencoes-lista", "combobox"]);
    assert.ok(r.dentro && r.acima && r.mesmaColuna, JSON.stringify(r));
    assert.ok(r.z > r.zBarra - 1 && r.z > 40, `z-index ${r.z}`);
    assert.equal(r.rodape, width > 760 ? "flex" : "none");
    assert.deepEqual(r.imgs, [[22, "agents/xerife.png"], [22, "agents/detetive.png"]]);
    await campo(page).pressSequentially("det");
    assert.deepEqual(await nomes(page), ["Detetive"]);
    await ctx.close();
    assert.deepEqual(erros, []);
  });

  test(`${width}: sem plano, a lista tem só "No PigBank+"`, async () => {
    const { ctx, page } = await abrir({ width, lista: { json: { catalog: CAT.map(([kind, nome]) => ({ kind, nome, disponivel: true, status: null, energy_cost: 1 })), can_chat: false, can_activate: false, energy_enabled: true, energy_budget: 0, energy_used: 0 } } });
    await digitar(page, "@");
    await lista(page).waitFor();
    assert.deepEqual(await page.locator(".mencoes .cmdk-group").allTextContents(), ["No PigBank+"]);
    await ctx.close();
  });

  test(`${width}: ↑ ↓ mexem o item ativo; Enter e Tab inserem a menção sem enviar; Esc fecha e mantém o texto`, async () => {
    const { ctx, page, chats } = await abrir({ width });
    await digitar(page, "@");
    await lista(page).waitFor();
    const ativo = () => campo(page).getAttribute("aria-activedescendant");
    assert.equal(await ativo(), "mencao-0");
    await page.keyboard.press("ArrowDown");
    assert.equal(await ativo(), "mencao-1");
    assert.equal(await page.locator("#mencao-1").getAttribute("aria-selected"), "true");
    await page.waitForFunction(() => getComputedStyle(document.querySelector("#mencao-1 img")).boxShadow.includes("255, 45, 142") && getComputedStyle(document.querySelector("#mencao-0 img")).boxShadow === "none"); // anel rosa só no avatar marcado (a sombra faz transição)
    await page.keyboard.press("ArrowDown");
    await page.keyboard.press("ArrowUp");
    assert.equal(await ativo(), "mencao-1");
    await page.keyboard.press("Enter"); // Detetive (o 2º da lista)
    assert.equal(await campo(page).inputValue(), "@Detetive ");
    assert.equal(await lista(page).count(), 0);
    await campo(page).fill("");
    await page.keyboard.type("@xer");
    await lista(page).waitFor();
    await page.keyboard.press("Tab");
    assert.equal(await campo(page).inputValue(), "@Xerife ");
    assert.equal(await campo(page).evaluate((e) => document.activeElement === e && e.selectionStart === e.value.length), true, "o foco e o cursor ficam depois da menção");
    await campo(page).fill("oi @");
    await lista(page).waitFor();
    await page.keyboard.press("Escape");
    assert.equal(await lista(page).count(), 0);
    assert.equal(await campo(page).inputValue(), "oi @");
    assert.equal(await campo(page).getAttribute("aria-expanded"), "false");
    await page.waitForFunction(() => getComputedStyle(document.querySelector(".chat-follow")).visibility === "visible"); // a fileira volta com a lista fechada
    assert.equal(await page.locator(".msg-user").count(), 0);
    await ctx.close();
    assert.deepEqual(chats, []);
  });

  test(`${width}: "fulano@gmail.com" não abre a lista nem pede nada; sem "@" o GET /agents é só o do painel lateral (≥1181 px), nunca um POST; o Piggy leva ícone e nome`, async () => {
    const { ctx, page, pedidos, chats } = await abrir({ width });
    await digitar(page, "fulano@gmail.com");
    await page.waitForTimeout(200);
    assert.equal(await lista(page).count(), 0);
    await campo(page).press("Enter");
    const cab = await resposta(page).locator(".msg-autor").evaluate((e) => ({ texto: e.textContent, src: e.querySelector("img").getAttribute("src").split("/").slice(-2).join("/"), tag: !!e.querySelector(".msg-tag"), w: e.querySelector("img").width }));
    await enviar(page, "quanto gastei?");
    await page.waitForTimeout(200);
    await ctx.close();
    assert.deepEqual(cab, { texto: "Piggy", src: "brand/icon.png", tag: false, w: 22 });
    assert.deepEqual(pedidos, width >= 1181 ? ["GET /agents/42"] : []); // o painel lateral lê o catálogo mesmo sem "@" (parts/PainelChat.tsx); no celular, nada
    assert.deepEqual(chats, []);
  });

  test(`${width}: a menção no meio manda o texto sem ela ao agente; a bolha destaca a menção; o 2º envio leva o context`, async () => {
    const { ctx, page, chats, erros } = await abrir({ width });
    await digitar(page, "tem duplicada @det");
    await lista(page).waitFor();
    await campo(page).pressSequentially("etive?");
    await campo(page).press("Enter");
    await resposta(page).locator(".msg-text", { hasText: "Resposta 1 do detetive." }).waitFor();
    const r = await page.evaluate(() => {
      const u = document.querySelector(".chat > .msg-user"), men = u.querySelector(".mencao");
      return { bolha: u.textContent, mencao: men.textContent, cor: getComputedStyle(men).color, pink: (() => { const e = document.createElement("i"); e.style.color = "var(--pink-ink)"; document.body.append(e); const c = getComputedStyle(e).color; e.remove(); return c; })(), url: location.hash };
    });
    const cab = await resposta(page).locator(".msg-autor").evaluate((e) => ({ texto: e.textContent, src: e.querySelector("img").getAttribute("src").split("/").slice(-2).join("/"), selo: e.querySelector(".msg-tag")?.textContent }));
    assert.deepEqual(chats.map((c) => ({ kind: c.kind, message: c.message, context: c.context })), [{ kind: "detetive", message: "tem duplicada?", context: null }]);
    assert.equal(chats[0].csrf, "tok-123");
    assert.equal(r.bolha, "tem duplicada @detetive?");
    assert.equal(r.mencao, "@detetive"); // como o usuário digitou
    assert.equal(r.cor, r.pink);
    assert.equal(r.url, "#/piggy");
    assert.equal(cab.texto, "DetetiveAGENTE");
    assert.equal(cab.src, "agents/detetive.png");
    await enviar(page, "@Detetive e a outra? também @Xerife hoje");
    await resposta(page).locator(".msg-text", { hasText: "Resposta 2 do detetive." }).waitFor();
    assert.deepEqual(chats.map((c) => [c.kind, c.message, c.context]), [["detetive", "tem duplicada?", null], ["detetive", "e a outra? também @Xerife hoje", "ctx-1"]]);
    assert.equal(await page.locator(".chat > .msg-user").last().locator(".mencao").count(), 2, "cada menção válida em rosa");
    await ctx.close();
    assert.deepEqual(erros, []);
  });

  test(`${width}: "ana@detetive.com @Xerife oi": o e-mail não é menção, só "@Xerife" fica rosa e o POST vai ao Xerife`, async () => {
    const { ctx, page, chats } = await abrir({ width });
    await enviar(page, "ana@detetive.com @Xerife oi");
    await resposta(page).locator(".msg-text", { hasText: "Resposta 1 do xerife." }).waitFor();
    const rosa = await page.locator(".chat > .msg-user .mencao").allTextContents();
    await ctx.close();
    assert.deepEqual(chats.map((c) => [c.kind, c.message]), [["xerife", "ana@detetive.com oi"]]);
    assert.deepEqual(rosa, ["@Xerife"]);
  });

  test(`${width}: 401, CSRF (403 em texto) e password_required: "Sua sessão terminou" ou pedido de senha, sem "Tentar de novo"`, async () => {
    const { ctx, page } = await abrir({ width, chat: (c, n) =>
      n === 1 ? { status: 401, headers: { "WWW-Authenticate": "Bearer" }, json: { detail: "Not authenticated" } }
      : n === 2 ? { status: 403, json: { detail: "Token CSRF inválido ou ausente." } }
      : { status: 403, json: { detail: { error: "password_required" } } } }); // o corpo real de /agents/* (fora do /api/v2)
    await digitar(page, "@det");
    await lista(page).waitFor();
    await campo(page).pressSequentially("etive um");
    await campo(page).press("Enter");
    await page.locator(".msg-text", { hasText: "Sua sessão terminou. Recarregue a página." }).waitFor();
    await enviar(page, "@Detetive dois");
    await page.locator(".msg-text", { hasText: "Sua sessão terminou. Recarregue a página." }).nth(1).waitFor();
    await enviar(page, "@Detetive três");
    await page.locator(".msg-text", { hasText: "Crie sua senha para conversar com os agentes." }).waitFor();
    assert.equal(await page.getByRole("button", { name: "Tentar de novo" }).count(), 0);
    await ctx.close();
  });

  test(`${width}: segundo envio ao mesmo agente em voo fica na barra e não manda outro POST`, async () => {
    let solta;
    const { ctx, page, chats } = await abrir({ width, segura: new Promise((ok) => { solta = ok; }) });
    await digitar(page, "x");
    await campo(page).fill("");
    await digitar(page, "@det");
    await lista(page).waitFor();
    await campo(page).pressSequentially("etive oi");
    await campo(page).press("Enter");
    const bolha = resposta(page).locator(".msg-text");
    await bolha.waitFor();
    assert.equal(await bolha.textContent(), "Preparando a resposta…");
    assert.equal(await bolha.getAttribute("aria-busy"), "true");
    await enviar(page, "@Detetive de novo");
    await page.waitForTimeout(150);
    assert.equal(await campo(page).inputValue(), "@Detetive de novo");
    assert.equal(chats.length, 1);
    solta();
    await bolha.filter({ hasText: "Resposta 1" }).waitFor();
    await ctx.close();
  });

  test(`${width}: dois agentes em voo: a resposta mais antiga que termina primeiro é anunciada ao leitor de tela`, async () => {
    const solta = {}, presas = { detetive: new Promise((ok) => { solta.detetive = ok; }), xerife: new Promise((ok) => { solta.xerife = ok; }) };
    const { ctx, page } = await abrir({ width, segura: (kind) => presas[kind] });
    await enviar(page, "@Detetive tem duplicada?");
    await resposta(page).locator(".msg-text", { hasText: "Preparando a resposta…" }).waitFor();
    await enviar(page, "@Xerife algo fora do padrão?");
    await page.locator(".chat > .msg-piggy").nth(1).locator(".msg-text", { hasText: "Preparando a resposta…" }).waitFor();
    solta.detetive();
    await resposta(page, 0).locator(".msg-text", { hasText: "do detetive." }).waitFor(); // o número da resposta é a contagem de POSTs na hora da resposta: os dois já chegaram
    await page.waitForFunction(() => /do detetive\.$/.test(document.querySelector("[role=status]:not(.guia-status)")?.textContent ?? ""), null, { timeout: 3000 });
    solta.xerife();
    await page.waitForFunction(() => /do xerife\.$/.test(document.querySelector("[role=status]:not(.guia-status)")?.textContent ?? ""), null, { timeout: 3000 });
    await ctx.close();
  });

  test(`${width}: duas respostas que terminam juntas: as duas chegam ao leitor de tela, nenhuma apaga a outra`, async () => {
    const solta = {}, presas = { detetive: new Promise((ok) => { solta.detetive = ok; }), xerife: new Promise((ok) => { solta.xerife = ok; }) };
    const { ctx, page } = await abrir({ width, segura: (kind) => presas[kind] });
    await enviar(page, "@Detetive tem duplicada?");
    await resposta(page).locator(".msg-text", { hasText: "Preparando a resposta…" }).waitFor();
    await enviar(page, "@Xerife algo fora do padrão?");
    await page.locator(".chat > .msg-piggy").nth(1).locator(".msg-text", { hasText: "Preparando a resposta…" }).waitFor();
    await page.evaluate(() => { // tudo o que a região viva chegou a dizer
      window.__ditos = [];
      const st = document.querySelector("[role=status]:not(.guia-status)");
      new MutationObserver(() => window.__ditos.push(st.textContent)).observe(st, { childList: true, subtree: true, characterData: true });
    });
    solta.detetive(); solta.xerife(); // no mesmo tick
    await page.locator(".chat > .msg-piggy").nth(1).locator(".msg-text", { hasText: "do xerife." }).waitFor();
    await resposta(page, 0).locator(".msg-text", { hasText: "do detetive." }).waitFor();
    await page.waitForTimeout(300);
    const ditos = (await page.evaluate(() => window.__ditos)).join("|");
    assert.match(ditos, /do detetive\./, ditos);
    assert.match(ditos, /do xerife\./, ditos);
    await ctx.close();
  });

  test(`${width}: sair do /piggy e voltar não anuncia de novo a conversa; a resposta que terminou longe é anunciada ao voltar`, async () => {
    let solta;
    const { ctx, page } = await abrir({ width, segura: (kind) => (kind === "xerife" ? new Promise((ok) => { solta = ok; }) : undefined) });
    const dito = () => page.evaluate(() => document.querySelector("[role=status]:not(.guia-status)")?.textContent ?? "");
    await enviar(page, "@Detetive tem duplicada?");
    await resposta(page).locator(".msg-text", { hasText: "Resposta 1 do detetive." }).waitFor();
    await page.waitForFunction(() => /do detetive\.$/.test(document.querySelector("[role=status]:not(.guia-status)")?.textContent ?? ""));
    await page.evaluate(() => { location.hash = "#/"; });
    await page.locator("#board-profile").waitFor();
    await page.evaluate(() => { location.hash = "#/piggy"; });
    await resposta(page).locator(".msg-text", { hasText: "Resposta 1 do detetive." }).waitFor();
    await page.waitForTimeout(300);
    assert.equal(await dito(), "", "a conversa que já foi anunciada não é lida de novo");
    await enviar(page, "@Xerife algo fora do padrão?");
    await resposta(page).locator(".msg-text", { hasText: "Preparando a resposta…" }).waitFor();
    await page.evaluate(() => { location.hash = "#/"; });
    await page.locator("#board-profile").waitFor();
    const respondeu = page.waitForResponse((r) => /\/agents\/42\/xerife\/chat$/.test(r.url()));
    solta();
    await respondeu;
    await page.evaluate(() => { location.hash = "#/piggy"; });
    await page.waitForFunction(() => /do xerife\.$/.test(document.querySelector("[role=status]:not(.guia-status)")?.textContent ?? ""), null, { timeout: 3000 });
    assert.doesNotMatch(await dito(), /do detetive/);
    await ctx.close();
  });

  // Só no desktop: no celular a barra só existe no /piggy, onde o chat já confere a conta ao montar.
  test(`${width}: a conta muda noutra aba e a menção sai logo, de outra página, com a anterior no cache: vai à conta nova`, { skip: width < 768 && "no celular a barra some fora do /piggy" }, async () => {
    const { ctx, page, chats } = await abrir({ width });
    await enviar(page, "@Detetive tem duplicada?"); // conta e catálogo da 42 no cache
    await resposta(page).locator(".msg-text", { hasText: "Resposta 1 do detetive." }).waitFor();
    await page.evaluate(() => { location.hash = "#/"; });
    await page.locator("#board-profile").waitFor();
    const da43 = await paraConta43(ctx);
    let soltaPerfil;
    const perfilPreso = new Promise((ok) => { soltaPerfil = ok; });
    await ctx.route("**/auth/dashboard-profile", async (r) => { await perfilPreso; return r.fallback(); }); // a releitura ainda em voo no envio
    await enviar(page, "@Detetive e agora?");
    await page.waitForTimeout(200);
    assert.equal(chats.length, 1, "com a releitura em voo, nada sai com a conta do cache");
    soltaPerfil();
    await page.locator(".chat > .msg-piggy .msg-text", { hasText: "Oi, conta nova." }).waitFor();
    assert.deepEqual(da43, [{ kind: "detetive", context: null }]);
    assert.equal(chats.length, 1, "nada mais à 42");
    assert.equal(await page.getByText("tem duplicada?").count(), 0);
    await ctx.close();
  });

  test(`${width}: a conta muda com o chat aberto: o anúncio da resposta da conta anterior não reaparece para a nova`, async () => {
    const { ctx, page } = await abrir({ width });
    await enviar(page, "@Detetive tem duplicada?");
    await page.waitForFunction(() => /do detetive\.$/.test(document.querySelector("[role=status]:not(.guia-status)")?.textContent ?? ""));
    await paraConta43(ctx);
    let solta;
    const presa = new Promise((ok) => { solta = ok; });
    await ctx.route(/\/agents\/43\/detetive\/chat$/, async (r) => { await presa; return r.fallback(); }); // a resposta da conta nova demora
    const relida = page.waitForResponse((r) => r.url().includes("/auth/dashboard-profile"));
    await page.evaluate(() => { window.dispatchEvent(new Event("visibilitychange")); window.dispatchEvent(new Event("focus")); });
    await relida;
    await page.locator(".chat-empty").waitFor();
    await enviar(page, "@Detetive e agora?"); // a conta nova abre a conversa; a resposta dela ainda não chegou
    await page.locator(".chat > .msg-user", { hasText: "e agora?" }).waitFor();
    await page.waitForTimeout(200);
    assert.equal(await page.evaluate(() => document.querySelector("[role=status]:not(.guia-status)")?.textContent ?? ""), "");
    solta();
    await ctx.close();
  });

  // Só no desktop: no celular a barra só existe no /piggy, onde o chat já confere a conta ao montar.
  test(`${width}: a conta muda noutra aba e uma pergunta SEM menção sai de outra página: ela fica, a conversa anterior some`, { skip: width < 768 && "no celular a barra some fora do /piggy" }, async () => {
    const { ctx, page } = await abrir({ width });
    await enviar(page, "@Detetive tem duplicada?");
    await resposta(page).locator(".msg-text", { hasText: "Resposta 1 do detetive." }).waitFor();
    await page.evaluate(() => { location.hash = "#/"; });
    await page.locator("#board-profile").waitFor();
    await paraConta43(ctx);
    await enviar(page, "quanto gastei?");
    await page.locator(".chat > .msg-user", { hasText: "quanto gastei?" }).waitFor();
    await page.waitForTimeout(300); // a conferência do chat ao montar não apaga a pergunta nova
    assert.equal(await page.locator(".chat > .msg-user", { hasText: "quanto gastei?" }).count(), 1);
    assert.equal(await page.getByText("tem duplicada?").count(), 0);
    await ctx.close();
  });

  test(`${width}: a conta muda: o dado em cache da anterior (investido) não responde à conta nova`, async () => {
    const { ctx, page } = await abrir({ width });
    const investido = (total) => ({ json: { total, motivos: [], por_tipo: [{ tipo: "renda_fixa", rotulo: "Renda fixa", valor: total, motivos: [] }], por_banco: [{ banco: "Nubank", valor: total, motivos: [] }] } });
    let total = "11111.11";
    await ctx.route("**/api/v2/investido", (r) => r.fulfill(investido(total)));
    await enviar(page, "quanto tenho aplicado?");
    await page.locator(".chat > .msg-piggy", { hasText: "11.111,11" }).first().waitFor();
    total = "99999.99";
    await paraConta43(ctx);
    const relida = page.waitForResponse((r) => r.url().includes("/auth/dashboard-profile"));
    await page.evaluate(() => { window.dispatchEvent(new Event("visibilitychange")); window.dispatchEvent(new Event("focus")); });
    await relida;
    await page.locator(".chat-empty").waitFor();
    await enviar(page, "quanto tenho aplicado?");
    await page.locator(".chat > .msg-piggy", { hasText: "99.999,99" }).first().waitFor();
    assert.equal(await page.getByText("11.111,11").count(), 0);
    await ctx.close();
  });

  test(`${width}: com a conta ainda sendo conferida, uma 2ª pergunta não some: o texto fica na barra`, async () => {
    const { ctx, page } = await abrir({ width });
    let soltaPerfil;
    const perfilPreso = new Promise((ok) => { soltaPerfil = ok; });
    await ctx.route("**/auth/dashboard-profile", async (r) => { await perfilPreso; return r.fallback(); });
    await enviar(page, "primeira");
    await enviar(page, "segunda");
    assert.equal(await campo(page).inputValue(), "segunda");
    soltaPerfil();
    await page.locator(".chat > .msg-user", { hasText: "primeira" }).waitFor();
    assert.equal(await campo(page).inputValue(), "segunda");
    await ctx.close();
  });

  test(`${width}: a conta muda e a lista do @ não mostra os agentes da anterior enquanto a conta é relida`, async () => {
    const { ctx, page } = await abrir({ width });
    await enviar(page, "@Detetive tem duplicada?"); // conta e catálogo da 42 no cache
    await resposta(page).locator(".msg-text", { hasText: "Resposta 1 do detetive." }).waitFor();
    await paraConta43(ctx);
    let soltaPerfil;
    const perfilPreso = new Promise((ok) => { soltaPerfil = ok; });
    await ctx.route("**/auth/dashboard-profile", async (r) => { await perfilPreso; return r.fallback(); });
    await digitar(page, "@");
    await page.waitForTimeout(300);
    assert.equal(await lista(page).count(), 0, "nada do catálogo da 42");
    soltaPerfil();
    await lista(page).waitFor();
    assert.equal((await nomes(page)).length, 7, "o catálogo da 43: os sete ativos");
    await ctx.close();
  });

  test(`${width}: a conta muda durante "Ativar e responder": a pergunta da conta anterior não sai`, async () => {
    let solta;
    const { ctx, page, chats } = await abrir({ width, soltaAtivar: new Promise((ok) => { solta = ok; }) });
    await digitar(page, "@rep");
    await lista(page).waitFor();
    await campo(page).pressSequentially("órter resume");
    await campo(page).press("Enter");
    const r = resposta(page);
    await r.getByRole("button", { name: "Ativar e responder" }).click();
    await r.getByRole("button", { name: "Ativando…" }).waitFor();
    await paraConta43(ctx);
    const relida = page.waitForResponse((x) => x.url().includes("/auth/dashboard-profile"));
    await page.evaluate(() => { window.dispatchEvent(new Event("visibilitychange")); window.dispatchEvent(new Event("focus")); });
    await relida;
    await page.locator(".chat-empty").waitFor();
    const ativou = page.waitForResponse((x) => /\/agents\/42\/reporter\/activate$/.test(x.url()));
    solta();
    await ativou;
    await page.waitForTimeout(300);
    assert.deepEqual(chats, [], "nada ao /agents/42/.../chat");
    await ctx.close();
  });

  test(`${width}: na volta do foco a conversa some até a conta ser relida; mesma conta, volta; conta nova, fica limpa`, async () => {
    const { ctx, page } = await abrir({ width });
    await enviar(page, "@Detetive tem duplicada?");
    await resposta(page).locator(".msg-text", { hasText: "Resposta 1 do detetive." }).waitFor();
    const foco = () => page.evaluate(() => { window.dispatchEvent(new Event("visibilitychange")); });
    const segura = async () => {
      let solta;
      const preso = new Promise((ok) => { solta = ok; });
      await ctx.route("**/auth/dashboard-profile", async (r) => { await preso; return r.fallback(); });
      return solta;
    };
    const velha = () => page.locator(".chat > .msg-user", { hasText: "tem duplicada?" });
    let solta = await segura(); // a mesma conta, relida devagar
    await foco();
    await page.locator(".chat[inert]").waitFor({ state: "attached" });
    await velha().waitFor({ state: "hidden", timeout: 2000 }); // escondida enquanto relê
    solta();
    await page.locator(".chat:not([inert])").waitFor({ state: "attached" });
    await page.locator(".chat > .msg-user", { hasText: "tem duplicada?" }).waitFor();
    await ctx.unroute("**/auth/dashboard-profile");
    await paraConta43(ctx);
    solta = await segura(); // agora a sessão é outra conta
    await foco();
    await page.locator(".chat[inert]").waitFor({ state: "attached" });
    await velha().waitFor({ state: "hidden", timeout: 2000 }); // a conversa da 42 não aparece enquanto a 43 é conferida
    await page.waitForTimeout(200);
    assert.equal(await velha().isVisible(), false, "segue escondida enquanto a leitura não volta");
    solta();
    await page.locator(".chat-empty").waitFor();
    await ctx.close();
  });

  test(`${width}: a resposta termina e o usuário sai do /piggy antes do anúncio (60 ms): ela é anunciada ao voltar`, async () => {
    let solta;
    const { ctx, page } = await abrir({ width, segura: () => new Promise((ok) => { solta = ok; }) });
    await enviar(page, "@Detetive tem duplicada?");
    await resposta(page).locator(".msg-text", { hasText: "Preparando a resposta…" }).waitFor();
    await page.evaluate(() => { // sai no mesmo instante em que a resposta aparece, antes dos 60 ms do anúncio
      new MutationObserver((_, mo) => {
        if (!document.querySelector(".chat")?.textContent.includes("do detetive.")) return;
        mo.disconnect();
        location.hash = "#/";
      }).observe(document.body, { childList: true, subtree: true, characterData: true });
    });
    solta();
    await page.waitForFunction(() => location.hash === "#/");
    await page.locator("#board-profile").waitFor();
    await page.evaluate(() => { location.hash = "#/piggy"; });
    await page.waitForFunction(() => /do detetive\.$/.test(document.querySelector("[role=status]:not(.guia-status)")?.textContent ?? ""), null, { timeout: 3000 });
    await ctx.close();
  });

  test(`${width}: letra não ASCII colada ao nome (@XerifeЖ) não é menção: vai ao Piggy`, async () => {
    const { ctx, page, chats } = await abrir({ width });
    await enviar(page, "@XerifeЖ amanhã");
    await resposta(page).locator(".msg-autor").waitFor();
    assert.equal(await resposta(page).locator(".msg-autor b").textContent(), "Piggy");
    assert.deepEqual(chats, []);
    await enviar(page, "@Xerife, amanhã"); // pontuação ainda delimita
    await page.locator(".chat > .msg-piggy .msg-autor b", { hasText: "Xerife" }).waitFor();
    await ctx.close();
  });

  test(`${width}: na volta do foco a região viva sai de cena até a conta ser relida; uma resposta antiga que chega nesse meio não é anunciada`, async () => {
    let soltaX;
    const { ctx, page } = await abrir({ width, segura: (kind) => (kind === "xerife" ? new Promise((ok) => { soltaX = ok; }) : undefined) });
    const regiao = () => page.locator("[role=status]:not(.guia-status)");
    await enviar(page, "@Detetive tem duplicada?");
    await page.waitForFunction(() => /do detetive\.$/.test(document.querySelector("[role=status]:not(.guia-status)")?.textContent ?? ""));
    await enviar(page, "@Xerife algo fora do padrão?"); // da 42, ainda em voo
    await page.locator(".chat > .msg-piggy").nth(1).locator(".msg-text", { hasText: "Preparando a resposta…" }).waitFor();
    await paraConta43(ctx);
    let soltaPerfil;
    const preso = new Promise((ok) => { soltaPerfil = ok; });
    await ctx.route("**/auth/dashboard-profile", async (r) => { await preso; return r.fallback(); });
    await page.evaluate(() => { window.dispatchEvent(new Event("visibilitychange")); });
    await page.locator("[role=status][hidden]:not(.guia-status)").waitFor({ state: "attached" });
    const respondeu = page.waitForResponse((r) => /\/agents\/42\/xerife\/chat$/.test(r.url()));
    soltaX(); // a resposta da 42 chega durante a releitura
    await respondeu;
    await page.waitForTimeout(300);
    assert.doesNotMatch(await regiao().textContent(), /xerife/, "nada da 42 é anunciado durante a releitura");
    soltaPerfil();
    await page.locator(".chat-empty").waitFor(); // era a 43: tudo limpo, nada a anunciar
    await ctx.close();
  });

  test(`${width}: mesma conta: a resposta que chegou durante a releitura do foco é anunciada quando ela termina`, async () => {
    let soltaX;
    const { ctx, page } = await abrir({ width, segura: (kind) => (kind === "xerife" ? new Promise((ok) => { soltaX = ok; }) : undefined) });
    await enviar(page, "@Xerife algo fora do padrão?");
    await resposta(page).locator(".msg-text", { hasText: "Preparando a resposta…" }).waitFor();
    let soltaPerfil;
    const preso = new Promise((ok) => { soltaPerfil = ok; });
    await ctx.route("**/auth/dashboard-profile", async (r) => { await preso; return r.fallback(); });
    await page.evaluate(() => { window.dispatchEvent(new Event("visibilitychange")); });
    await page.locator("[role=status][hidden]:not(.guia-status)").waitFor({ state: "attached" });
    const respondeu = page.waitForResponse((r) => /\/agents\/42\/xerife\/chat$/.test(r.url()));
    soltaX();
    await respondeu;
    await page.waitForTimeout(300);
    assert.doesNotMatch(await page.locator("[role=status]:not(.guia-status)").textContent(), /xerife/);
    soltaPerfil();
    await page.waitForFunction(() => /do xerife\.$/.test(document.querySelector("[role=status]:not([hidden]):not(.guia-status)")?.textContent ?? ""), null, { timeout: 3000 });
    await ctx.close();
  });

  // Só no desktop: no celular a barra só existe no /piggy.
  test(`${width}: resposta na fila ao sair do chat e a conta muda com ele fechado: a conta nova não ouve a da anterior`, { skip: width < 768 && "no celular a barra some fora do /piggy" }, async () => {
    let solta;
    const { ctx, page } = await abrir({ width, segura: () => new Promise((ok) => { solta = ok; }) });
    await enviar(page, "@Detetive tem duplicada?");
    await resposta(page).locator(".msg-text", { hasText: "Preparando a resposta…" }).waitFor();
    await page.evaluate(() => { // sai no instante em que a resposta aparece: ela fica na fila do anúncio
      new MutationObserver((_, mo) => {
        if (!document.querySelector(".chat")?.textContent.includes("do detetive.")) return;
        mo.disconnect();
        location.hash = "#/";
      }).observe(document.body, { childList: true, subtree: true, characterData: true });
    });
    solta();
    await page.waitForFunction(() => location.hash === "#/");
    await page.locator("#board-profile").waitFor();
    await paraConta43(ctx);
    await enviar(page, "quanto gastei?"); // a conta nova abre a conversa (e o chat) de outra página
    await page.locator(".chat > .msg-user", { hasText: "quanto gastei?" }).waitFor();
    await page.waitForTimeout(300);
    assert.doesNotMatch(await page.locator("[role=status]:not(.guia-status)").textContent(), /detetive/);
    await ctx.close();
  });

  test(`${width}: A→B→A com uma pergunta da A em voo: o context dela não volta para a conversa nova`, async () => {
    let solta;
    const { ctx, page, chats } = await abrir({ width, segura: () => (chats.length === 1 ? new Promise((ok) => { solta = ok; }) : undefined) });
    await enviar(page, "@Detetive tem duplicada?");
    await resposta(page).locator(".msg-text", { hasText: "Preparando a resposta…" }).waitFor();
    const relerNoFoco = async () => {
      const relida = page.waitForResponse((r) => r.url().includes("/auth/dashboard-profile"));
      await page.evaluate(() => { window.dispatchEvent(new Event("visibilitychange")); });
      await relida;
    };
    await paraConta43(ctx);
    await relerNoFoco(); // virou a 43: a conversa some
    await page.locator(".chat-empty").waitFor();
    await ctx.route("**/auth/dashboard-profile", (r) => r.fulfill({ json: { user_id: 42, display_name: "Marina", email: null, whatsapp_linked: true } }));
    await relerNoFoco(); // voltou a ser a 42
    const respondeu = page.waitForResponse((r) => /\/agents\/42\/detetive\/chat$/.test(r.url()));
    solta(); // a pergunta abandonada termina agora
    await respondeu;
    await page.waitForTimeout(200);
    await enviar(page, "@Detetive e agora?");
    await page.locator(".chat > .msg-piggy .msg-text", { hasText: "do detetive." }).waitFor();
    assert.deepEqual(chats.map((c) => c.context), [null, null], "a 2ª pergunta não leva o context da abandonada");
    await ctx.close();
  });

  test(`${width}: A→B→A com uma pergunta da A ainda em voo: a A volta podendo perguntar ao mesmo agente`, async () => {
    let solta;
    const { ctx, page, chats } = await abrir({ width, segura: () => (chats.length === 1 ? new Promise((ok) => { solta = ok; }) : undefined) });
    await enviar(page, "@Detetive tem duplicada?");
    await resposta(page).locator(".msg-text", { hasText: "Preparando a resposta…" }).waitFor();
    const relerNoFoco = async () => {
      const relida = page.waitForResponse((r) => r.url().includes("/auth/dashboard-profile"));
      await page.evaluate(() => { window.dispatchEvent(new Event("visibilitychange")); });
      await relida;
    };
    await paraConta43(ctx);
    await relerNoFoco();
    await page.locator(".chat-empty").waitFor();
    await ctx.route("**/auth/dashboard-profile", (r) => r.fulfill({ json: { user_id: 42, display_name: "Marina", email: null, whatsapp_linked: true } }));
    await relerNoFoco(); // voltou a ser a 42, com a 1ª pergunta ainda presa no servidor
    await enviar(page, "@Detetive e agora?");
    await page.locator(".chat > .msg-piggy .msg-text", { hasText: "Resposta 2 do detetive." }).waitFor();
    assert.equal(await campo(page).inputValue(), "", "a pergunta saiu: a trava da abandonada não ficou");
    solta();
    await ctx.close();
  });

  test(`${width}: uma leitura da conta que saiu antes da troca de conta não libera a conversa no foco`, async () => {
    const { ctx, page } = await abrir({ width });
    await enviar(page, "@Detetive tem duplicada?");
    await resposta(page).locator(".msg-text", { hasText: "Resposta 1 do detetive." }).waitFor();
    let soltaVelha;
    const velha = new Promise((ok) => { soltaVelha = ok; });
    await paraConta43(ctx);
    let pedidos = 0;
    await ctx.route("**/auth/dashboard-profile", async (r) => {
      if (++pedidos > 1) return r.fallback(); // as leituras seguintes já são da sessão da 43
      await velha; // a 1ª saiu com a sessão da 42 e só termina depois do foco
      await r.fulfill({ json: { user_id: 42, display_name: "Marina", email: null, whatsapp_linked: true } });
    });
    const saiu = page.waitForRequest((r) => r.url().includes("/auth/dashboard-profile"));
    await enviar(page, "@Detetive e agora?"); // a menção relê a conta: essa leitura fica presa
    await saiu;
    await page.evaluate(() => { window.dispatchEvent(new Event("visibilitychange")); }); // a sessão já virou a 43 noutra aba
    const nova = page.waitForResponse((r) => r.url().includes("/auth/dashboard-profile") && pedidos > 1);
    soltaVelha(); // a leitura de antes do foco termina com a 42: não basta para mostrar a conversa
    await nova;
    await page.locator(".chat > .msg-piggy").first().waitFor({ state: "detached" }).catch(() => {});
    await page.waitForTimeout(300);
    assert.equal(await page.getByText("tem duplicada?").count(), 0, "a conversa da 42 não volta com a leitura velha");
    await ctx.close();
  });

  test(`${width}: A→B→A: o invalid_context da pergunta abandonada não apaga o context da conversa nova`, async () => {
    let solta;
    const { ctx, page, chats } = await abrir({ width,
      segura: () => (chats.length === 1 ? new Promise((ok) => { solta = ok; }) : undefined),
      chat: (c) => (c.message === "velha" ? { status: 400, json: { detail: { error: "invalid_context", message: "A conversa expirou.", retryable: false } } } : null) });
    await enviar(page, "@Detetive velha");
    await resposta(page).locator(".msg-text", { hasText: "Preparando a resposta…" }).waitFor();
    const relerNoFoco = async () => {
      const relida = page.waitForResponse((r) => r.url().includes("/auth/dashboard-profile"));
      await page.evaluate(() => { window.dispatchEvent(new Event("visibilitychange")); });
      await relida;
    };
    await paraConta43(ctx);
    await relerNoFoco();
    await page.locator(".chat-empty").waitFor();
    await ctx.route("**/auth/dashboard-profile", (r) => r.fulfill({ json: { user_id: 42, display_name: "Marina", email: null, whatsapp_linked: true } }));
    await relerNoFoco(); // de volta à 42, com a "velha" ainda presa
    await enviar(page, "@Detetive nova");
    await page.locator(".chat > .msg-piggy .msg-text", { hasText: "Resposta 2 do detetive." }).waitFor();
    const recusada = page.waitForResponse((r) => /\/agents\/42\/detetive\/chat$/.test(r.url()) && r.status() === 400);
    solta(); // a abandonada termina com invalid_context
    await recusada;
    await page.waitForTimeout(200);
    await enviar(page, "@Detetive de novo");
    await page.locator(".chat > .msg-piggy .msg-text", { hasText: "Resposta 3 do detetive." }).waitFor();
    assert.deepEqual(chats.map((c) => c.context), [null, null, "ctx-2"], "a 3ª pergunta segue a conversa nova");
    await ctx.close();
  });

  test(`${width}: uma leitura da conta em voo antes de abrir o /piggy não libera a conversa`, async () => {
    const { ctx, page } = await abrir({ width });
    await enviar(page, "@Detetive tem duplicada?");
    await resposta(page).locator(".msg-text", { hasText: "Resposta 1 do detetive." }).waitFor();
    await page.evaluate(() => { location.hash = "#/"; });
    await page.locator("#board-profile").waitFor();
    let soltaVelha;
    const velha = new Promise((ok) => { soltaVelha = ok; });
    await paraConta43(ctx);
    let soltaNova;
    const nova = new Promise((ok) => { soltaNova = ok; });
    let pedidos = 0;
    await ctx.route("**/auth/dashboard-profile", async (r) => {
      const n = ++pedidos;
      if (n > 2) return r.fallback(); // as leituras seguintes já são da sessão da 43
      if (n === 2) { await nova; return r.fallback(); } // a 1ª depois de abrir o chat também espera: a conversa não pode aparecer antes dela
      await velha; // a 1ª saiu com a sessão da 42 e só termina com o chat já aberto
      await r.fulfill({ json: { user_id: 42, display_name: "Marina", email: null, whatsapp_linked: true } }).catch(() => {});
    });
    const saiu = page.waitForRequest((r) => r.url().includes("/auth/dashboard-profile"));
    await page.evaluate(() => { location.hash = "#/conta/perfil"; }); // a Conta lê o perfil: essa leitura fica presa
    await saiu;
    await page.evaluate(() => { location.hash = "#/piggy"; }); // o chat monta com ela em voo
    await page.locator(".chat-espera", { hasText: "Carregando a conversa…" }).waitFor();
    const voltou = page.waitForResponse((r) => r.url().includes("/auth/dashboard-profile"));
    soltaVelha(); // termina com a 42: não basta para mostrar a conversa
    await voltou;
    await page.waitForTimeout(300);
    assert.equal(await page.getByText("tem duplicada?").count(), 0, "a leitura de antes de abrir o chat não o libera");
    soltaNova();
    await page.locator(".chat-empty").waitFor(); // a leitura seguinte trouxe a 43 e a conversa da 42 sumiu
    assert.equal(await page.getByText("tem duplicada?").count(), 0);
    await ctx.close();
  });

  test(`${width}: escolher na lista com o cursor no meio de uma menção troca o token inteiro`, async () => {
    const { ctx, page } = await abrir({ width });
    await campo(page).fill("@Detetive pergunta");
    await campo(page).press("Home");
    for (let i = 0; i < 4; i++) await campo(page).press("ArrowRight"); // cursor em "@Det|etive"
    await lista(page).waitFor();
    await campo(page).press("Enter");
    assert.equal(await campo(page).inputValue(), "@Detetive pergunta");
    await campo(page).fill("@det resto");
    await campo(page).press("Home");
    for (let i = 0; i < 4; i++) await campo(page).press("ArrowRight"); // cursor no fim de "@det", antes do espaço
    await lista(page).waitFor();
    await campo(page).press("Enter");
    assert.equal(await campo(page).inputValue(), "@Detetive resto"); // caminho comum: o resto fica, com um espaço só
    await campo(page).fill("@Detetive,qual cobrança?");
    await campo(page).press("Home");
    for (let i = 0; i < 4; i++) await campo(page).press("ArrowRight"); // "@Det|etive,qual": a pontuação colada fica
    await lista(page).waitFor();
    await campo(page).press("Enter");
    assert.equal(await campo(page).inputValue(), "@Detetive ,qual cobrança?");
    await campo(page).fill("@Faria Limer pergunta");
    await campo(page).press("Home");
    for (let i = 0; i < 4; i++) await campo(page).press("ArrowRight"); // "@Far|ia Limer": o nome composto inteiro sai
    await lista(page).waitFor();
    await campo(page).press("Enter");
    assert.equal(await campo(page).inputValue(), "@Faria Limer pergunta");
    await campo(page).fill("@Faria pergunta");
    await campo(page).press("Home");
    for (let i = 0; i < 4; i++) await campo(page).press("ArrowRight"); // controle: sem a 2ª palavra do nome, a palavra seguinte fica
    await lista(page).waitFor();
    await campo(page).press("Enter");
    assert.equal(await campo(page).inputValue(), "@Faria Limer pergunta");
    await ctx.close();
  });

  // Leituras do perfil presas na ordem em que saem: `presa(n)` devolve a função que solta a n-ésima. A 1ª responde a 42 (saiu com a
  // sessão anterior); as outras já são da 43 (paraConta43 registrado antes).
  const leiturasPresas = async (ctx, quantas) => {
    const soltas = [], presas = [...Array(quantas)].map((_, i) => new Promise((ok) => { soltas[i] = ok; }));
    let pedidos = 0;
    await ctx.route("**/auth/dashboard-profile", async (r) => {
      const n = ++pedidos;
      if (n > quantas) return r.fallback();
      await presas[n - 1];
      if (n > 1) return r.fallback();
      await r.fulfill({ json: { user_id: 42, display_name: "Marina", email: null, whatsapp_linked: true } }).catch(() => {});
    });
    return { solta: (n) => soltas[n - 1](), pedidos: () => pedidos };
  };

  test(`${width}: dois focos seguidos com a releitura lenta: o fim da 1ª não mostra a conversa enquanto a do 2º não volta`, async () => {
    const { ctx, page } = await abrir({ width });
    await enviar(page, "@Detetive tem duplicada?");
    await resposta(page).locator(".msg-text", { hasText: "Resposta 1 do detetive." }).waitFor();
    await paraConta43(ctx);
    const l = await leiturasPresas(ctx, 2);
    const foco = () => page.evaluate(() => { window.dispatchEvent(new Event("visibilitychange")); });
    await foco(); // 1º foco: a leitura sai ainda com a sessão da 42
    await page.waitForFunction(() => document.querySelector("[data-reconferindo]"));
    await foco(); // a aba saiu e voltou de novo, com a sessão já na 43
    const voltou = page.waitForResponse((r) => r.url().includes("/auth/dashboard-profile"));
    l.solta(1);
    await voltou;
    await page.waitForTimeout(300);
    assert.equal(await page.getByText("tem duplicada?").isVisible(), false, "o fim da releitura do 1º foco não libera a conversa");
    l.solta(2);
    await page.locator(".chat-empty").waitFor();
    await ctx.close();
  });

  test(`${width}: a menção com uma leitura velha da conta em voo vai à conta da sessão atual`, async () => {
    const { ctx, page } = await abrir({ width });
    const da43 = await paraConta43(ctx);
    const l = await leiturasPresas(ctx, 1);
    const saiu = page.waitForRequest((r) => r.url().includes("/auth/dashboard-profile"));
    await campo(page).fill("@Detetive oi"); // o "@" liga a leitura da conta: ela sai ainda com a sessão da 42
    await saiu;
    await campo(page).press("Enter");
    l.solta(1);
    await page.locator(".chat > .msg-piggy .msg-text", { hasText: "Oi, conta nova." }).waitFor();
    assert.deepEqual(da43, [{ kind: "detetive", context: null }]);
    await ctx.close();
  });

  if (width >= 768) test(`${width}: a pergunta ao Piggy de outra página com uma leitura velha da conta em voo não some quando o chat confere`, async () => {
    const { ctx, page } = await abrir({ width });
    await page.evaluate(() => { location.hash = "#/"; });
    await page.locator("#board-profile").waitFor();
    await paraConta43(ctx);
    const l = await leiturasPresas(ctx, 1);
    const saiu = page.waitForRequest((r) => r.url().includes("/auth/dashboard-profile"));
    await campo(page).fill("@"); // liga a leitura da conta: ela sai ainda com a sessão da 42
    await saiu;
    await campo(page).fill("quanto gastei?");
    await campo(page).press("Enter");
    await page.waitForFunction(() => location.hash === "#/piggy");
    l.solta(1);
    await page.locator(".chat > .msg-user", { hasText: "quanto gastei?" }).waitFor();
    await page.waitForTimeout(500); // a conferência do chat (já com a 43) não a apaga
    assert.equal(await page.locator(".chat > .msg-user", { hasText: "quanto gastei?" }).count(), 1);
    await ctx.close();
  });

  test(`${width}: o plano muda noutra aba com o rascunho aberto: a menção relê o catálogo e vai ao agente`, async () => {
    const cat = (pro) => ({ json: { catalog: CAT.map(([kind, nome, energy_cost]) => ({ kind, nome, disponivel: true, status: "active", energy_cost })), can_chat: pro, can_activate: pro, energy_enabled: false, energy_budget: 99, energy_used: 0 } });
    let pro = false;
    const { ctx, page } = await abrir({ width, lista: undefined });
    await ctx.route(/\/agents\/42$/, (r) => r.fulfill(cat(pro)));
    await digitar(page, "@det");
    await lista(page).waitFor(); // o catálogo lido: sem plano
    await campo(page).press("Escape");
    pro = true; // assinou o PigBank+ noutra aba
    await campo(page).fill("@Detetive oi");
    await campo(page).press("Enter");
    await page.locator(".chat > .msg-piggy .msg-text", { hasText: "Resposta 1 do detetive." }).waitFor();
    await ctx.close();
  });

  // O envio relê a conta; enquanto ela é relida a lista sai do catálogo (uid indefinido), e a leitura do catálogo em voo é cancelada:
  // o envio sempre faz uma leitura nova. Este teste fica de guarda dessa cadeia (Codex no #1157).
  // Com o painel lateral (≥1181 px) a leitura em voo tem um observador fixo e não se cancela sozinha: o envio a cancela (AskBar.tsx).
  test(`${width}: o plano muda com a leitura do catálogo em voo: o envio lê de novo e vai ao agente`, async () => {
    const cat = (pro) => ({ json: { catalog: CAT.map(([kind, nome, energy_cost]) => ({ kind, nome, disponivel: true, status: "active", energy_cost })), can_chat: pro, can_activate: pro, energy_enabled: false, energy_budget: 99, energy_used: 0 } });
    let solta, n = 0;
    const presa = new Promise((ok) => { solta = ok; });
    const { ctx, page } = await abrir({ width });
    await ctx.route(/\/agents\/42$/, async (r) => {
      if (++n === 1) { await presa; return r.fulfill(cat(false)).catch(() => {}); } // saiu antes de assinar
      return r.fulfill(cat(true));
    });
    const saiu = page.waitForRequest((r) => /\/agents\/42$/.test(r.url()));
    await campo(page).fill("@Detetive oi");
    await saiu;
    await campo(page).press("Enter"); // assinou o PigBank+ noutra aba antes de enviar
    solta();
    await page.locator(".chat > .msg-piggy .msg-text", { hasText: "Resposta 1 do detetive." }).waitFor();
    await ctx.close();
  });

  test(`${width}: o que se digita enquanto a menção é conferida fica no campo`, async () => {
    const { ctx, page } = await abrir({ width });
    let soltaPerfil;
    const preso = new Promise((ok) => { soltaPerfil = ok; });
    await campo(page).fill("@Detetive oi");
    await page.waitForResponse((r) => r.url().includes("/auth/dashboard-profile")); // a leitura do "@" já voltou
    await ctx.route("**/auth/dashboard-profile", async (r) => { await preso; return r.fallback(); }); // a do envio demora
    const saiu = page.waitForRequest((r) => r.url().includes("/auth/dashboard-profile"));
    await campo(page).press("Enter");
    await saiu;
    await campo(page).fill("outra pergunta"); // começa a próxima enquanto a conta é conferida
    soltaPerfil();
    await page.locator(".chat > .msg-piggy .msg-text", { hasText: "Resposta 1 do detetive." }).waitFor();
    assert.equal(await campo(page).inputValue(), "outra pergunta");
    await enviar(page, "@Detetive oi"); // controle: sem edição no meio, o envio limpa o campo
    await page.locator(".chat > .msg-piggy .msg-text", { hasText: "Resposta 2 do detetive." }).waitFor();
    assert.equal(await campo(page).inputValue(), "");
    await ctx.close();
  });

  test(`${width}: a conta muda noutra aba: a Cobrança da anterior não aparece para a nova`, async () => {
    const cob = (plan) => ({ json: { active: true, lifetime: false, gateway: "stripe", plan, interval: "monthly", current_period_end: "2026-11-12", scheduled_change: null } });
    const { ctx, page } = await abrir({ width });
    await ctx.route("**/billing/subscription", (r) => r.fulfill(cob("pro"))); // a 42 assina o Pro
    await page.evaluate(() => { location.hash = "#/conta/cobranca"; });
    await page.locator("main", { hasText: "Pro Mensal" }).waitFor();
    await paraConta43(ctx);
    let solta;
    const presa = new Promise((ok) => { solta = ok; });
    await ctx.route("**/billing/subscription", async (r) => { await presa; return r.fulfill(cob("essencial")); }); // a da 43 demora
    await page.evaluate(() => { location.hash = "#/conta/perfil"; });
    await page.waitForResponse((r) => r.url().includes("/auth/dashboard-profile")); // a releitura da Conta descobre a 43
    await page.waitForTimeout(100);
    await page.evaluate(() => { location.hash = "#/conta/cobranca"; });
    await page.waitForTimeout(300);
    assert.doesNotMatch(await page.locator("main").innerText(), /Pro Mensal/, "o plano da 42 não aparece enquanto o da 43 carrega");
    solta();
    await page.locator("main", { hasText: "Essencial Mensal" }).waitFor();
    await ctx.close();
  });

  test(`${width}: a releitura da conta falha: a lista do "@" não volta com o catálogo guardado da anterior`, async () => {
    const { ctx, page } = await abrir({ width });
    await digitar(page, "@det");
    await lista(page).waitFor(); // a lista da 42, lida
    await campo(page).fill("");
    await ctx.route("**/auth/dashboard-profile", (r) => r.fulfill({ status: 401, json: { detail: "Não autenticado." } })); // a sessão mudou e a releitura falha
    const relida = page.waitForResponse((r) => r.url().includes("/auth/dashboard-profile"));
    await digitar(page, "@det");
    await relida;
    await page.waitForTimeout(300);
    assert.equal(await lista(page).count(), 0, "sem conta conferida, nada da lista da 42");
    await ctx.close();
  });

  if (width >= 768) test(`${width}: troca de conta saindo do uid 60 relê a previsão de 60 dias (a chave ["previsao", 60] não é de uid)`, async () => {
    const { ctx, page } = await abrir({ width });
    const previsoes = [];
    page.on("request", (r) => { const u = new URL(r.url()); if (u.pathname === "/api/v2/previsao") previsoes.push(u.search); });
    await page.evaluate(() => { location.hash = "#/"; });
    await page.getByRole("radio", { name: "60 dias" }).click();
    await page.waitForFunction(() => true);
    const virar = async (uid) => {
      await ctx.route("**/auth/dashboard-profile", (r) => r.fulfill({ json: { user_id: uid, display_name: "X", email: null, whatsapp_linked: true } }));
      const lida = page.waitForResponse((r) => r.url().includes("/auth/dashboard-profile"));
      await campo(page).fill("@"); // o "@" relê a conta: a troca é descoberta com a previsão na tela
      await lida;
      await campo(page).fill("");
      await page.waitForTimeout(400);
    };
    await virar(60); // 42 → 60
    const antes = previsoes.filter((x) => x === "?dias=60").length;
    await virar(43); // 60 → 43: o uid antigo é 60
    assert.ok(previsoes.filter((x) => x === "?dias=60").length > antes, `a previsão de 60 dias da conta anterior foi relida: ${previsoes}`);
    await ctx.close();
  });

  test(`${width}: com a conta lenta, a pergunta sem menção abre o chat em "Carregando a conversa…" e entra quando a conta volta`, async () => {
    const { ctx, page } = await abrir({ width });
    if (width >= 768) { // no desktop a barra também fica nas outras páginas
      await page.evaluate(() => { location.hash = "#/"; });
      await page.locator("#board-profile").waitFor();
    }
    let soltaPerfil;
    const preso = new Promise((ok) => { soltaPerfil = ok; });
    await ctx.route("**/auth/dashboard-profile", async (r) => { await preso; return r.fallback(); });
    await enviar(page, "quanto gastei?");
    await page.waitForFunction(() => location.hash === "#/piggy");
    await page.locator(".chat-espera", { hasText: "Carregando a conversa…" }).waitFor();
    soltaPerfil();
    await page.locator(".chat > .msg-user", { hasText: "quanto gastei?" }).waitFor();
    await ctx.close();
  });

  test(`${width}: a conta muda enquanto o chat está fechado: ao abrir o /piggy, nada da conversa anterior aparece antes de conferir a conta`, async () => {
    const { ctx, page } = await abrir({ width });
    await enviar(page, "@Detetive tem duplicada?");
    await resposta(page).locator(".msg-text", { hasText: "Resposta 1 do detetive." }).waitFor();
    await page.evaluate(() => { location.hash = "#/"; });
    await page.locator("#board-profile").waitFor();
    await paraConta43(ctx);
    let soltaPerfil;
    const perfilPreso = new Promise((ok) => { soltaPerfil = ok; });
    await ctx.route("**/auth/dashboard-profile", async (r) => { await perfilPreso; return r.fallback(); }); // o perfil da conta nova demora
    await page.evaluate(() => { location.hash = "#/piggy"; });
    await page.locator(".chat-espera", { hasText: "Carregando a conversa…" }).waitFor();
    assert.equal(await page.getByText("tem duplicada?").count(), 0);
    assert.equal(await page.getByText("Resposta 1 do detetive.").count(), 0);
    soltaPerfil();
    await page.locator(".chat-empty").waitFor(); // conferida: era outra conta, a conversa foi limpa
    assert.equal(await page.getByText("tem duplicada?").count(), 0);
    await ctx.close();
  });

  test(`${width}: sem conseguir conferir a conta (perfil 401) ao abrir o /piggy, a conversa não aparece`, async () => {
    const { ctx, page } = await abrir({ width });
    await enviar(page, "@Detetive tem duplicada?");
    await resposta(page).locator(".msg-text", { hasText: "Resposta 1 do detetive." }).waitFor();
    await page.evaluate(() => { location.hash = "#/"; });
    await page.locator("#board-profile").waitFor();
    await ctx.route("**/auth/dashboard-profile", (r) => r.fulfill({ status: 401, json: { detail: "Não autenticado." } }));
    await page.evaluate(() => { location.hash = "#/piggy"; });
    await page.locator(".chat-espera", { hasText: "Não foi possível confirmar sua conta. Recarregue a página." }).waitFor();
    assert.equal(await page.getByText("tem duplicada?").count(), 0);
    await ctx.close();
  });

  test(`${width}: a conta muda noutra aba: a 1ª pergunta da conta nova ao mesmo agente vai sem o context da anterior`, async () => {
    const { ctx, page, chats } = await abrir({ width });
    await enviar(page, "@Detetive tem duplicada?");
    await resposta(page).locator(".msg-text", { hasText: "Resposta 1 do detetive." }).waitFor();
    const da43 = await paraConta43(ctx);
    await idaEVoltaPelaConta(page);
    await page.locator(".chat-empty").waitFor(); // a conversa da conta anterior não aparece para a nova
    assert.equal(await page.getByText("tem duplicada?").count(), 0);
    assert.equal(await page.getByText("Resposta 1 do detetive.").count(), 0);
    await enviar(page, "@Detetive e agora?");
    await page.locator(".chat > .msg-piggy .msg-text", { hasText: "Oi, conta nova." }).waitFor();
    assert.deepEqual(da43, [{ kind: "detetive", context: null }]);
    assert.equal(chats.length, 1);
    await ctx.close();
  });

  test(`${width}: a conta muda noutra aba e a aba volta ao foco no /piggy: a conversa da anterior some sem sair da página`, async () => {
    const { ctx, page } = await abrir({ width });
    await enviar(page, "@Detetive tem duplicada?");
    await resposta(page).locator(".msg-text", { hasText: "Resposta 1 do detetive." }).waitFor();
    await paraConta43(ctx);
    const relida = page.waitForResponse((r) => r.url().includes("/auth/dashboard-profile"));
    await page.evaluate(() => { window.dispatchEvent(new Event("visibilitychange")); window.dispatchEvent(new Event("focus")); });
    await relida;
    await page.locator(".chat-empty").waitFor();
    assert.equal(await page.getByText("tem duplicada?").count(), 0);
    await ctx.close();
  });

  test(`${width}: uma pergunta da conta anterior ainda em voo não trava a conta nova no mesmo agente`, async () => {
    let solta;
    const { ctx, page } = await abrir({ width, segura: new Promise((ok) => { solta = ok; }) });
    await enviar(page, "@Detetive velha");
    await resposta(page).locator(".msg-text", { hasText: "Preparando a resposta…" }).waitFor();
    const da43 = await paraConta43(ctx);
    await idaEVoltaPelaConta(page);
    await page.locator(".chat-empty").waitFor();
    await enviar(page, "@Detetive nova");
    await page.locator(".chat > .msg-piggy .msg-text", { hasText: "Oi, conta nova." }).waitFor();
    assert.deepEqual(da43, [{ kind: "detetive", context: null }]);
    solta();
    await ctx.close();
  });

  test(`${width}: com a MESMA conta, ir à Conta e voltar mantém a conversa`, async () => {
    const { ctx, page } = await abrir({ width });
    await enviar(page, "@Detetive tem duplicada?");
    await resposta(page).locator(".msg-text", { hasText: "Resposta 1 do detetive." }).waitFor();
    await idaEVoltaPelaConta(page);
    await page.locator(".chat > .msg-piggy .msg-text", { hasText: "Resposta 1 do detetive." }).waitFor();
    assert.equal(await page.locator(".chat-empty").count(), 0);
    await page.evaluate(() => { location.hash = "#/"; }); // e pela Início, sem passar pela Conta: conferida, a conversa volta
    await page.locator("#board-profile").waitFor();
    await page.evaluate(() => { location.hash = "#/piggy"; });
    await page.locator(".chat > .msg-piggy .msg-text", { hasText: "Resposta 1 do detetive." }).waitFor();
    assert.equal(await page.locator(".chat-espera").count(), 0);
    await ctx.close();
  });

  test(`${width}: clicar fora da lista, ou o foco ir a outro controle, fecha a lista do @; clicar num item ainda o escolhe`, async () => {
    const { ctx, page } = await abrir({ width });
    await digitar(page, "@");
    await lista(page).waitFor();
    await page.locator(".chat-empty h2").click(); // fora da lista e do campo
    await lista(page).waitFor({ state: "detached" });
    assert.equal(await campo(page).inputValue(), "@", "o texto fica, como no Esc");
    await digitar(page, "d");
    await lista(page).waitFor();
    await page.evaluate(() => document.querySelector("a[href]").focus()); // o foco vai a outro controle (teclado, leitor de tela)
    await lista(page).waitFor({ state: "detached" });
    await digitar(page, "e");
    await lista(page).waitFor();
    await page.locator("#mencoes-lista [role=option]").first().click();
    await lista(page).waitFor({ state: "detached" });
    assert.equal(await campo(page).inputValue(), "@Detetive ");
    await ctx.close();
  });

  test(`${width}: trocar de página fecha a lista do @ (no celular a barra some fora do /piggy)`, async () => {
    const { ctx, page } = await abrir({ width });
    await digitar(page, "@");
    await lista(page).waitFor();
    await page.evaluate(() => { location.hash = "#/"; });
    await lista(page).waitFor({ state: "detached" });
    assert.equal(await page.locator(".mencoes").count(), 0);
    await ctx.close();
  });

  test(`${width}: o redirect "Perguntar ao Carteiro" manda a pergunta dele ao Carteiro`, async () => {
    const { ctx, page, chats } = await abrir({ width, ativos: ["detetive", "carteiro"],
      chat: (c, n, kind) => (kind === "detetive" ? { json: { reply: "Isso é com o Carteiro.", context: "c1", redirects: [{ kind: "carteiro", name: "Carteiro", question: "Que contas vencem?", access: "ready", needs_data: true }] } } : null) });
    await digitar(page, "@det");
    await lista(page).waitFor();
    await campo(page).pressSequentially("etive e os boletos?");
    await campo(page).press("Enter");
    await page.locator(".chat").getByRole("button", { name: "Perguntar ao Carteiro" }).click();
    await resposta(page).locator(".msg-text", { hasText: "Resposta 2 do carteiro." }).waitFor();
    await ctx.close();
    assert.deepEqual(chats.map((c) => [c.kind, c.message]), [["detetive", "e os boletos?"], ["carteiro", "Que contas vencem?"]]);
    // Só a pergunta do botão vai marcada: o Carteiro responde sem reclassificar (sem um 2º encaminhamento em cadeia).
    assert.deepEqual(chats.map((c) => [c.forwarded, c.needs_data]), [[undefined, undefined], [true, true]]);
  });

  test(`${width}: o redirect falha com erro repetível: "Tentar de novo" manda a pergunta ainda marcada como encaminhada`, async () => {
    const { ctx, page, chats } = await abrir({ width, ativos: ["detetive", "carteiro"],
      chat: (c, n, kind) => (kind === "detetive" ? { json: { reply: "Isso é com o Carteiro.", context: "c1", redirects: [{ kind: "carteiro", name: "Carteiro", question: "Que contas vencem?", access: "ready", needs_data: true }] } }
        : n === 2 ? { status: 502, json: { detail: { error: "model_unavailable", message: "Falhou agora.", retryable: true } } } : null) });
    await enviar(page, "@Detetive e os boletos?");
    await page.locator(".chat").getByRole("button", { name: "Perguntar ao Carteiro" }).click();
    await page.getByRole("button", { name: "Tentar de novo" }).click();
    await resposta(page).locator(".msg-text", { hasText: "Resposta 3 do carteiro." }).waitFor();
    await ctx.close();
    assert.deepEqual(chats.map((c) => [c.kind, c.forwarded, c.needs_data]), [["detetive", undefined, undefined], ["carteiro", true, true], ["carteiro", true, true]]);
  });

  test(`${width}: o redirect para um agente inativo: "Ativar e responder" manda a pergunta ainda marcada como encaminhada`, async () => {
    const { ctx, page, chats } = await abrir({ width, ativos: ["detetive"],
      chat: (c, n, kind) => (kind === "detetive" ? { json: { reply: "Isso é com o Carteiro.", context: "c1", redirects: [{ kind: "carteiro", name: "Carteiro", question: "Que contas vencem?", access: "activate" }] } } : null) });
    await enviar(page, "@Detetive e os boletos?");
    await page.locator(".chat").getByRole("button", { name: "Perguntar ao Carteiro" }).click();
    await page.getByRole("button", { name: "Ativar e responder" }).click();
    await resposta(page).locator(".msg-text", { hasText: "Resposta 2 do carteiro." }).waitFor();
    await ctx.close();
    assert.deepEqual(chats.map((c) => [c.kind, c.forwarded, c.needs_data]), [["detetive", undefined, undefined], ["carteiro", true, false]]);
  });

  test(`${width}: o redirect relê o acesso no clique: o Carteiro ativado depois da resposta recebe a pergunta`, async () => {
    const { ctx, page, chats } = await abrir({ width, ativos: ["detetive"],
      chat: (c, n, kind) => (kind === "detetive" ? { json: { reply: "Isso é com o Carteiro.", context: "c1", redirects: [{ kind: "carteiro", name: "Carteiro", question: "Que contas vencem?", access: "activate" }] } } : null) });
    await enviar(page, "@Detetive e os boletos?");
    await page.locator(".chat").getByRole("button", { name: "Perguntar ao Carteiro" }).waitFor();
    // o Carteiro é ativado noutra aba: o catálogo do servidor passa a dizer "active"
    await ctx.route(/\/agents\/42$/, (r) => r.fulfill({ json: { catalog: CAT.map(([kind, nome, energy_cost]) => ({ kind, nome, disponivel: true, status: ["detetive", "carteiro"].includes(kind) ? "active" : null, energy_cost })), can_chat: true, can_activate: true, energy_enabled: false, energy_budget: 99, energy_used: 0 } }));
    await page.locator(".chat").getByRole("button", { name: "Perguntar ao Carteiro" }).click();
    await resposta(page).locator(".msg-text", { hasText: "Resposta 2 do carteiro." }).waitFor();
    await ctx.close();
    assert.deepEqual(chats.map((c) => c.kind), ["detetive", "carteiro"]);
  });

  test(`${width}: duplo clique no redirect para um agente inativo cria uma bolha só`, async () => {
    const { ctx, page } = await abrir({ width, ativos: ["detetive"],
      chat: (c, n, kind) => (kind === "detetive" ? { json: { reply: "Isso é com o Carteiro.", context: "c1", redirects: [{ kind: "carteiro", name: "Carteiro", question: "Que contas vencem?", access: "activate" }] } } : null) });
    await enviar(page, "@Detetive e os boletos?");
    const botao = page.locator(".chat").getByRole("button", { name: "Perguntar ao Carteiro" });
    await botao.waitFor();
    await botao.evaluate((b) => { b.click(); b.click(); }); // no mesmo tick, antes do re-render
    await page.getByRole("button", { name: "Ativar e responder" }).first().waitFor();
    await page.waitForTimeout(300);
    assert.equal(await page.getByRole("button", { name: "Ativar e responder" }).count(), 1);
    assert.equal(await page.locator(".chat > .msg-user", { hasText: "Que contas vencem?" }).count(), 1);
    await botao.click(); // controle: depois da 1ª, um clique novo ainda segue
    await page.waitForFunction(() => document.querySelectorAll(".chat > .msg-user").length === 3);
    await ctx.close();
  });

  test(`${width}: a conta muda enquanto o redirect relê o catálogo: a pergunta da anterior não reaparece na conversa nova`, async () => {
    const { ctx, page, chats } = await abrir({ width, ativos: ["detetive", "carteiro"],
      chat: (c, n, kind) => (kind === "detetive" ? { json: { reply: "Isso é com o Carteiro.", context: "c1", redirects: [{ kind: "carteiro", name: "Carteiro", question: "Que contas vencem?", access: "ready" }] } } : null) });
    await enviar(page, "@Detetive e os boletos?");
    await page.locator(".chat").getByRole("button", { name: "Perguntar ao Carteiro" }).waitFor();
    let soltaPerfil, soltaCat;
    const perfilPreso = new Promise((ok) => { soltaPerfil = ok; }), catPreso = new Promise((ok) => { soltaCat = ok; });
    await paraConta43(ctx);
    await ctx.route("**/auth/dashboard-profile", async (r) => { await perfilPreso; return r.fallback(); }); // a sessão já é a 43; a leitura demora
    await ctx.route(/\/agents\/42$/, async (r) => { await catPreso; return r.fallback().catch(() => {}); }); // a releitura do catálogo do redirect também
    const lendo = page.waitForRequest((r) => r.url().includes("/auth/dashboard-profile"));
    await campo(page).fill("@"); // o "@" relê a conta antes do clique
    await lendo;
    const saiu = page.waitForRequest((r) => /\/agents\/42$/.test(r.url()));
    await page.locator(".chat").getByRole("button", { name: "Perguntar ao Carteiro" }).click();
    await saiu;
    soltaPerfil(); // a 43 chega com o catálogo do redirect ainda em voo: a conversa da 42 é limpa
    await page.locator(".chat-empty").waitFor();
    soltaCat();
    await page.waitForTimeout(500);
    assert.equal(await page.getByText("Que contas vencem?").count(), 0, "a pergunta do redirect da 42 não aparece para a 43");
    assert.deepEqual(chats.map((c) => c.kind), ["detetive"]);
    await ctx.close();
  });

  test(`${width}: agente inativo: nenhum POST de chat antes do clique; "Ativar e responder" ativa uma vez e responde`, async () => {
    let solta;
    const { ctx, page, chats, ativacoes } = await abrir({ width, soltaAtivar: new Promise((ok) => { solta = ok; }) });
    await digitar(page, "@rep");
    await lista(page).waitFor();
    await campo(page).pressSequentially("órter resume");
    await campo(page).press("Enter");
    const r = resposta(page);
    await r.locator(".msg-text", { hasText: "Ativar o Repórter usa ⚡1 de energia. Você tem 2 livre." }).waitFor();
    assert.equal(await r.locator(".msg-tag").textContent(), "NÃO ATIVO");
    assert.deepEqual(chats, []);
    const botao = r.getByRole("button", { name: "Ativar e responder" });
    await botao.evaluate((b) => { b.click(); b.click(); }); // no MESMO tick, antes do re-render: o `disabled` ainda não existe, quem segura é a guarda da função
    await r.getByRole("button", { name: "Ativando…" }).waitFor();
    assert.equal(await r.getByRole("button", { name: "Ativando…" }).isDisabled(), true);
    solta();
    await r.locator(".msg-tag", { hasText: /^AGENTE$/ }).waitFor(); // a etiqueta, não a resposta: sem a guarda a falha cai na contagem abaixo
    await page.waitForTimeout(150);
    assert.deepEqual(ativacoes, ["reporter"], "um duplo clique no mesmo tick = uma ativação");
    await r.locator(".msg-text", { hasText: "Resposta 1 do reporter." }).waitFor();
    assert.deepEqual(chats.map((c) => [c.kind, c.message, c.context]), [["reporter", "resume", null]]);
    assert.equal(await r.locator(".msg-tag").textContent(), "AGENTE");
    await ctx.close();
  });

  test(`${width}: "Ativar e responder" recusado por no_energy: o aviso não repete os números de quando a bolha nasceu`, async () => {
    const { ctx, page } = await abrir({ width, ativarErro: { status: 403, json: { detail: { error: "no_energy" } } } });
    await digitar(page, "@rep");
    await lista(page).waitFor();
    await campo(page).pressSequentially("órter resume");
    await campo(page).press("Enter");
    const r = resposta(page);
    await r.locator(".msg-text", { hasText: "Ativar o Repórter usa ⚡1 de energia. Você tem 2 livre." }).waitFor();
    await r.getByRole("button", { name: "Ativar e responder" }).click();
    await r.locator(".msg-tag", { hasText: "SEM ENERGIA" }).waitFor();
    assert.equal(await r.locator(".msg-text").first().textContent(), "Você está sem energia livre para ativar o Repórter.");
    await ctx.close();
  });

  test(`${width}: acento decomposto antes da menção (e + U+0301, texto colado): a menção e o texto saem inteiros`, async () => {
    const { ctx, page, chats } = await abrir({ width });
    await enviar(page, "cafe\u0301 @Detetive oi");
    await resposta(page).locator(".msg-text", { hasText: "Resposta 1 do detetive." }).waitFor();
    assert.deepEqual(chats.map((c) => [c.kind, c.message]), [["detetive", "cafe\u0301 oi"]]);
    assert.equal(await page.locator(".chat > .msg-user .mencao").textContent(), "@Detetive");
    await ctx.close();
  });

  test(`${width}: texto antes da menção que o NFD alonga (sílaba Hangul vira 2–3 jamos): a menção e o texto saem inteiros`, async () => {
    const { ctx, page, chats } = await abrir({ width });
    await enviar(page, "한국어 @Detetive oi");
    await resposta(page).locator(".msg-text", { hasText: "Resposta 1 do detetive." }).waitFor();
    assert.deepEqual(chats.map((c) => [c.kind, c.message]), [["detetive", "한국어 oi"]]);
    assert.equal(await page.locator(".chat > .msg-user .mencao").textContent(), "@Detetive");
    await ctx.close();
  });

  test(`${width}: nome do agente colado com acento decomposto (o + U+0301): a lista o acha e o envio vai ao agente`, async () => {
    const { ctx, page, chats } = await abrir({ width });
    await campo(page).fill("@Repo\u0301");
    await lista(page).waitFor();
    assert.deepEqual(await nomes(page), ["Repórter"]);
    await enviar(page, "@Repo\u0301rter resume");
    const r = resposta(page);
    await r.locator(".msg-text", { hasText: "Ativar o Repórter usa ⚡1 de energia. Você tem 2 livre." }).waitFor();
    assert.equal(await r.locator(".msg-autor b").textContent(), "Repórter");
    assert.equal(await page.locator(".chat > .msg-user .mencao").textContent(), "@Repo\u0301rter");
    assert.deepEqual(chats, []);
    await ctx.close();
  });

  test(`${width}: "Ativar e responder" com password_required (403, detail objeto): pedido de senha, sem "Tentar de novo"`, async () => {
    const { ctx, page } = await abrir({ width, ativarErro: { status: 403, json: { detail: { error: "password_required" } } } });
    await digitar(page, "@rep");
    await lista(page).waitFor();
    await campo(page).pressSequentially("órter resume");
    await campo(page).press("Enter");
    const r = resposta(page);
    await r.getByRole("button", { name: "Ativar e responder" }).click();
    await r.locator(".msg-text", { hasText: "Crie sua senha para conversar com os agentes." }).waitFor();
    assert.equal(await page.getByRole("button", { name: "Tentar de novo" }).count(), 0);
    assert.equal(await page.getByText("Não foi possível ativar o agente").count(), 0);
    await ctx.close();
  });

  for (const [nome, ativarErro] of [["401", { status: 401, json: { detail: "Não autenticado." } }], ["CSRF (403 em texto)", { status: 403, json: { detail: "Token CSRF inválido ou ausente." } }]]) {
    test(`${width}: "Ativar e responder" com ${nome}: "Sua sessão terminou", sem oferecer ativar de novo`, async () => {
      const { ctx, page } = await abrir({ width, ativarErro });
      await enviar(page, "@Repórter resume");
      const r = resposta(page);
      await r.getByRole("button", { name: "Ativar e responder" }).click();
      await r.locator(".msg-text", { hasText: "Sua sessão terminou. Recarregue a página." }).waitFor();
      assert.equal(await r.getByRole("button", { name: /Ativar e responder|Tentar de novo/ }).count(), 0);
      assert.equal(await page.getByText("Não foi possível ativar o agente").count(), 0);
      await ctx.close();
    });
  }

  test(`${width}: sem energia mostra o que falta e "Ver planos", sem POST`, async () => {
    const { ctx, page, chats, ativacoes } = await abrir({ width });
    await enviar(page, "x"); // abre a conversa só para garantir o estado inicial
    await digitar(page, "@bar");
    await lista(page).waitFor();
    await campo(page).pressSequentially("ão quanto rende?");
    await campo(page).press("Enter");
    const r = resposta(page);
    await r.locator(".msg-text", { hasText: "O Barão usa ⚡3 de energia e você tem 2 livre." }).waitFor();
    const link = r.getByRole("link", { name: "Ver planos" });
    assert.equal(await link.getAttribute("href"), "/precos");
    assert.equal(await r.locator(".msg-tag").textContent(), "SEM ENERGIA");
    assert.equal(await r.getByRole("button").count(), 0, "não oferece ativar nem pausar outro");
    await ctx.close();
    assert.deepEqual([chats, ativacoes], [[], []]);
  });

  test(`${width}: can_chat falso: "Ver planos" e nenhum POST`, async () => {
    const { ctx, page, chats } = await abrir({ width, lista: { json: { catalog: CAT.map(([kind, nome]) => ({ kind, nome, disponivel: true, status: "active", energy_cost: 1 })), can_chat: false, can_activate: false, energy_enabled: true, energy_budget: 0, energy_used: 0 } } });
    await digitar(page, "@det");
    await lista(page).waitFor();
    await campo(page).pressSequentially("etive oi");
    await campo(page).press("Enter");
    const r = resposta(page);
    await r.getByRole("link", { name: "Ver planos" }).waitFor();
    assert.equal(await r.getByRole("link", { name: "Ver planos" }).getAttribute("href"), "/precos");
    await ctx.close();
    assert.deepEqual(chats, []);
  });

  test(`${width}: 429 de cota mostra a mensagem do servidor sem "Tentar de novo"; 503 repetível tenta de novo com o mesmo texto`, async () => {
    const { ctx, page, chats } = await abrir({ width, chat: (c, n) =>
      n === 1 ? { status: 429, json: { detail: { error: "quota_exhausted", message: "Sua cota do mês acabou.", retryable: false } } }
      : n === 2 ? { status: 503, json: { detail: { error: "unavailable", message: "O agente está instável.", retryable: true } } } : null });
    await digitar(page, "@det");
    await lista(page).waitFor();
    await campo(page).pressSequentially("etive um");
    await campo(page).press("Enter");
    await resposta(page).locator(".msg-text", { hasText: "Sua cota do mês acabou." }).waitFor();
    assert.equal(await page.getByRole("button", { name: "Tentar de novo" }).count(), 0);
    await enviar(page, "@Detetive dois");
    await page.locator(".msg-text", { hasText: "O agente está instável." }).waitFor();
    await page.getByRole("button", { name: "Tentar de novo" }).click();
    await resposta(page).locator(".msg-text", { hasText: "Resposta 3 do detetive." }).waitFor();
    await ctx.close();
    assert.deepEqual(chats.map((c) => c.message), ["um", "dois", "dois"]);
  });

  test(`${width}: 429 do limitador (detail texto), 5xx com detail texto e 200 com JSON inválido: sem "Tentar de novo"; sem resposta (rede): com`, async () => {
    const { ctx, page } = await abrir({ width, chat: (c, n) =>
      n === 1 ? { status: 429, json: { detail: "Too Many Requests" } }
      : n === 2 ? { status: 502, json: { detail: "Bad Gateway" } }
      : n === 3 ? { status: 200, body: "<html>não é json</html>", contentType: "text/html" } : { json: { reply: "ok", context: null, redirects: [] } } });
    await digitar(page, "@det");
    await lista(page).waitFor();
    await campo(page).pressSequentially("etive um");
    await campo(page).press("Enter");
    await resposta(page).locator(".msg-text", { hasText: "Muitas mensagens em pouco tempo." }).waitFor();
    assert.equal(await page.getByRole("button", { name: "Tentar de novo" }).count(), 0);
    await enviar(page, "@Detetive dois");
    await page.locator(".msg-text", { hasText: "Não foi possível obter a resposta agora." }).waitFor();
    assert.equal(await page.getByRole("button", { name: "Tentar de novo" }).count(), 0);
    await enviar(page, "@Detetive três");
    await page.locator(".msg-text", { hasText: "Não foi possível obter a resposta agora." }).nth(1).waitFor();
    assert.equal(await page.getByRole("button", { name: "Tentar de novo" }).count(), 0);
    await ctx.close();
  });

  test(`${width}: falha de rede no chat oferece "Tentar de novo"`, async () => {
    const { ctx, page, chats } = await abrir({ width });
    await ctx.route(/\/agents\/42\/detetive\/chat/, (r) => r.abort("failed"));
    await digitar(page, "@det");
    await lista(page).waitFor();
    await campo(page).pressSequentially("etive um");
    await campo(page).press("Enter");
    await page.getByRole("button", { name: "Tentar de novo" }).waitFor();
    await ctx.close();
    assert.deepEqual(chats, []);
  });

  test(`${width}: enviar "@Detetive ..." antes do catálogo carregar espera e vai ao agente, não ao Piggy`, async () => {
    let solta;
    const { ctx, page, chats } = await abrir({ width, ativos: ["detetive"] });
    const seguro = new Promise((ok) => { solta = ok; });
    await ctx.route(/\/agents\/42$/, async (r) => { await seguro; await r.fallback(); });
    await campo(page).fill("@Detetive tem duplicada?");
    await campo(page).press("Enter"); // sem digitar: o catálogo ainda nem começou/terminou
    await page.waitForTimeout(150);
    assert.equal(await page.locator(".msg-user").count(), 0, "espera o catálogo");
    solta();
    await resposta(page).locator(".msg-text", { hasText: "Resposta 1 do detetive." }).waitFor();
    assert.equal(await page.locator(".chat > .msg-piggy").count(), 1);
    await ctx.close();
    assert.deepEqual(chats.map((c) => [c.kind, c.message]), [["detetive", "tem duplicada?"]]);
  });

  test(`${width}: durante a composição (IME) Enter e setas não tratam a lista`, async () => {
    const { ctx, page } = await abrir({ width });
    await digitar(page, "@");
    await lista(page).waitFor();
    await campo(page).evaluate((i) => i.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", isComposing: true, bubbles: true, cancelable: true })));
    assert.equal(await campo(page).inputValue(), "@");
    await campo(page).evaluate((i) => i.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", isComposing: true, bubbles: true, cancelable: true })));
    assert.equal(await campo(page).getAttribute("aria-activedescendant"), "mencao-0");
    await ctx.close();
  });

  test(`${width}: invalid_context apaga o context: o envio seguinte recomeça sem ele`, async () => {
    const { ctx, page, chats } = await abrir({ width, chat: (c, n) =>
      n === 2 ? { status: 400, json: { detail: { error: "invalid_context", message: "A conversa expirou.", retryable: false } } } : null });
    await digitar(page, "@det");
    await lista(page).waitFor();
    await campo(page).pressSequentially("etive um");
    await campo(page).press("Enter");
    await page.locator(".msg-text", { hasText: "Resposta 1" }).waitFor();
    await enviar(page, "@Detetive dois");
    await page.locator(".msg-text", { hasText: "A conversa expirou." }).waitFor();
    await enviar(page, "@Detetive três");
    await page.locator(".msg-text", { hasText: "Resposta 3" }).waitFor();
    await ctx.close();
    assert.deepEqual(chats.map((c) => c.context), [null, "ctx-1", null]);
  });

  test(`${width}: GET /agents 404 (beta desligado): a lista não abre e a mensagem vai ao Piggy`, async () => {
    const { ctx, page, chats } = await abrir({ width, lista: { status: 404, json: { detail: "Not Found" } } });
    await digitar(page, "@");
    await page.waitForTimeout(300);
    assert.equal(await lista(page).count(), 0);
    await enviar(page, "@Detetive oi");
    await resposta(page).locator(".msg-autor").waitFor();
    assert.equal(await resposta(page).locator(".msg-autor").textContent(), "Piggy");
    assert.equal(await page.locator(".mencao").count(), 0);
    await ctx.close();
    assert.deepEqual(chats, []);
  });

  test(`${width}: "@Detetive 👍" envia o emoji; "@Detetive ?" (só pontuação) não envia`, async () => {
    const { ctx, page, chats } = await abrir({ width });
    await enviar(page, "@Detetive ?");
    await page.waitForTimeout(150);
    assert.equal(await campo(page).inputValue(), "@Detetive ?"); // controle: só pontuação continua sem envio
    await enviar(page, "@Detetive 👍");
    await resposta(page).locator(".msg-text", { hasText: "Resposta 1 do detetive." }).waitFor();
    await ctx.close();
    assert.deepEqual(chats.map((c) => c.message), ["👍"]);
  });

  test(`${width}: só a menção não envia nada e a barra guarda o texto`, async () => {
    const { ctx, page, chats } = await abrir({ width });
    await digitar(page, "@det");
    await lista(page).waitFor();
    await campo(page).pressSequentially("etive");
    await campo(page).press("Enter"); // a lista ainda mostra o Detetive: escolhe
    await campo(page).press("Enter"); // agora a lista está fechada: só a menção, não envia
    await page.waitForTimeout(150);
    assert.equal(await campo(page).inputValue(), "@Detetive ");
    assert.equal(await page.locator(".msg-user").count(), 0);
    await ctx.close();
    assert.deepEqual(chats, []);
  });

  test(`${width}: protótipo: nenhuma requisição a /agents nem /auth; lista e resposta de exemplo; ativar troca só na memória`, async () => {
    const { ctx, page, erros, ir } = await abrirPainel(browser, { width, demo: true, espera: "#askbar-input" });
    const pedidos = [];
    page.on("request", (r) => { if (/\/(agents\/\d|auth\/)/.test(r.url())) pedidos.push(r.url()); });
    await ir("/piggy");
    await page.locator(".chat-empty").waitFor();
    await digitar(page, "@");
    await lista(page).waitFor();
    assert.deepEqual(await page.locator(".mencoes .cmdk-group").allTextContents(), ["Agentes ativos", "Ativar para conversar"]);
    await campo(page).pressSequentially("det");
    await campo(page).press("Enter");
    await campo(page).pressSequentially("tem duplicada?");
    await campo(page).press("Enter");
    await resposta(page).locator(".msg-text", { hasText: "Resposta de exemplo" }).waitFor();
    assert.equal(await resposta(page).locator(".msg-autor").textContent(), "DetetiveAGENTE");
    await enviar(page, "@Carteiro e agora?");
    const r = resposta(page);
    await r.getByRole("button", { name: "Ativar e responder" }).click();
    await r.locator(".msg-text", { hasText: "Resposta de exemplo" }).waitFor();
    await ctx.close();
    assert.deepEqual(pedidos, []);
    assert.deepEqual(erros, []);
  });
}

// --- Resposta viva: o texto aparece palavra por palavra e o "enviando" mostra frases do agente (PiggyChat.tsx, Carregando.tsx) ---
const MOVIMENTO = (page) => page.emulateMedia({ reducedMotion: "no-preference" }); // o painel de teste abre com movimento reduzido
const emAnimacao = (page) => page.evaluate(() => document.getAnimations().filter((a) => /^(rv|carga)-/.test(a.animationName) && a.playState !== "finished").map((a) => a.animationName)); // as do painel (view transition) ficam de fora; "finished" com fill:both ainda está na lista
// O nome da animação que a CSS aplica agora (getAnimations pode vir vazio mesmo com ela aplicada).
const animacoes = (page, sel) => page.evaluate((q) => [...document.querySelectorAll(q)].map((e) => getComputedStyle(e).animationName), sel);
const palavras = (n) => Array.from({ length: n }, (_, i) => `p${i}`).join(" ");
const RED = { kind: "carteiro", name: "Carteiro", question: "Que contas vencem?", access: "ready", needs_data: true };
// Por palavra: o atraso da animação, e a opacidade agora.
const revelacao = (page) => page.evaluate(() => [...document.querySelectorAll(".chat > .msg-piggy:last-child .msg-text .rv")].map((e) => ({
  atraso: e.getAnimations()[0]?.effect.getTiming().delay ?? null, op: +getComputedStyle(e).opacity, t: e.textContent })));

for (const width of TELAS) {
  test(`${width}: a resposta do agente aparece palavra por palavra: texto inteiro no DOM, atrasos crescentes, teto de 2 s, ações depois`, async () => {
    const texto = `Linha um ${palavras(197)}\nLinha dois.`; // 201 palavras: o passo encolhe de 30 ms para ~10 ms
    const { ctx, page, erros } = await abrir({ width, chat: () => ({ json: { reply: texto, context: "c", redirects: [RED] } }) });
    await MOVIMENTO(page);
    await enviar(page, "@Detetive tem duplicada?");
    const bolha = resposta(page).locator(".msg-text");
    await bolha.locator(".rv").first().waitFor();
    assert.equal(await bolha.textContent(), texto); // nada escondido: o texto inteiro, com a quebra de linha
    assert.equal(await bolha.evaluate((e) => getComputedStyle(e).whiteSpace), "pre-line");
    const ps = await revelacao(page);
    assert.equal(ps.length, 201);
    const atrasos = ps.map((p) => p.atraso);
    assert.ok(atrasos.every((a, i) => i === 0 || a >= atrasos[i - 1]) && atrasos.at(-1) > atrasos[0], "atrasos crescentes");
    assert.ok(atrasos.at(-1) <= 2000 && atrasos.at(-1) > 1500, `último atraso ${atrasos.at(-1)}`);
    assert.ok(ps.at(-1).op < 1, "a última palavra começa escondida");
    const acoes = resposta(page).locator(".msg-acoes");
    assert.equal(await acoes.locator("button", { hasText: "Perguntar ao Carteiro" }).count(), 1); // no DOM desde o início (getByRole não vê o que está visibility:hidden)
    assert.ok(+(await acoes.evaluate((e) => getComputedStyle(e).opacity)) < 1, "as ações esperam o texto");
    const atrasoAcoes = await acoes.evaluate((e) => e.getAnimations()[0].effect.getTiming().delay);
    assert.ok(atrasoAcoes >= atrasos.at(-1) + 500, `ações em ${atrasoAcoes} ms, última palavra começa em ${atrasos.at(-1)} e dura 500`);
    // O fade das ações tem de rodar inteiro, mesmo depois de o texto voltar a ser contínuo (a troca não pode cortá-lo): amostra a opacidade até a animação acabar.
    const fade = await acoes.evaluate((e) => new Promise((ok) => {
      const an = e.getAnimations()[0], op = [];
      const f = () => { op.push(+getComputedStyle(e).opacity); an.playState === "finished" || an.playState === "idle" ? ok({ op, estado: an.playState }) : requestAnimationFrame(f); };
      f();
    }));
    assert.equal(fade.estado, "finished", "a animação das ações não foi cortada");
    assert.ok(fade.op.some((o) => o > 0 && o < 1), `sem opacidade intermediária: ${[...new Set(fade.op)]}`);
    assert.equal(fade.op.at(-1), 1);
    await bolha.locator(".rv").first().waitFor({ state: "detached", timeout: 1000 }); // texto voltou a ser contínuo, logo depois do fade
    assert.equal(await acoes.getAttribute("data-revela"), null, "a marca dos botões saiu");
    await ctx.close();
    assert.deepEqual(erros, []);
  });

  test(`${width}: resposta curta: 30 ms por palavra`, async () => {
    const { ctx, page } = await abrir({ width, chat: () => ({ json: { reply: "um dois três", context: "c", redirects: [] } }) });
    await MOVIMENTO(page);
    await enviar(page, "@Detetive oi");
    await resposta(page).locator(".rv").first().waitFor();
    assert.deepEqual((await revelacao(page)).map((p) => p.atraso), [0, 30, 60]);
    await ctx.close();
  });

  test(`${width}: com movimento reduzido não há animação, só o texto`, async () => {
    const { ctx, page } = await abrir({ width }); // o contexto já é reduce
    await enviar(page, "@Detetive tem duplicada?");
    await resposta(page).locator(".msg-text", { hasText: "Resposta 1 do detetive." }).waitFor();
    assert.deepEqual(await emAnimacao(page), []);
    assert.equal(await resposta(page).locator(".msg-text").evaluate((e) => getComputedStyle(e).opacity), "1");
    assert.equal(await page.locator(".chat .rv").count(), 0, "sem movimento: texto contínuo, sem spans por palavra");
    await ctx.close();
  });

  test(`${width}: sair do /piggy e voltar não reanima, e erros aparecem direto`, async () => {
    const { ctx, page } = await abrir({ width, chat: (c, n) => (n === 2 ? { status: 500, json: { detail: { message: "Deu ruim.", retryable: true } } } : { json: { reply: palavras(80), context: "c", redirects: [] } }) });
    await MOVIMENTO(page);
    await enviar(page, "@Detetive tem duplicada?");
    await resposta(page).locator(".rv").first().waitFor();
    assert.ok((await revelacao(page)).at(-1).op < 1, "animou na primeira vez");
    await page.evaluate(() => { location.hash = "#/conta/perfil"; }); // sai no meio da revelação
    await page.locator(".chat").waitFor({ state: "detached" });
    await page.evaluate(() => { location.hash = "#/piggy"; });
    await page.locator(".chat").waitFor();
    assert.equal(await resposta(page).locator(".msg-text").textContent(), palavras(80));
    assert.deepEqual(await emAnimacao(page), []);
    assert.ok((await revelacao(page)).every((p) => p.op === 1));
    await enviar(page, "@Detetive e agora?");
    await resposta(page).locator(".msg-text", { hasText: "Deu ruim." }).waitFor();
    assert.equal(await resposta(page).locator(".rv").count(), 0);
    await ctx.close();
  });

  test(`${width}: enquanto envia: frases do agente que trocam a cada 2 s, sem ir ao leitor de tela`, async () => {
    let solta;
    const { ctx, page } = await abrir({ width, segura: new Promise((ok) => { solta = ok; }), chat: () => ({ json: { reply: "Feito, sem duplicada.", context: "c", redirects: [] } }) });
    await MOVIMENTO(page);
    await enviar(page, "@Detetive tem duplicada?");
    const carga = resposta(page).locator(".msg-carga");
    await carga.waitFor();
    assert.equal(await carga.getAttribute("aria-hidden"), "true");
    assert.equal(await carga.textContent(), "Procurando pistas nos lançamentos…");
    assert.equal(await resposta(page).locator(".msg-text").textContent(), "Preparando a resposta…"); // o que o leitor de tela e a bolha já tinham
    await page.evaluate(() => { // tudo o que a região viva chega a dizer
      window.__ditos = [];
      const st = document.querySelector("[role=status]:not(.guia-status)");
      new MutationObserver(() => window.__ditos.push(st.textContent)).observe(st, { childList: true, subtree: true, characterData: true });
    });
    await carga.filter({ hasText: "Comparando cobranças parecidas…" }).waitFor({ timeout: 3500 });
    const cabe = await carga.evaluate((e) => { const f = e.querySelector(".msg-carga-frase").getBoundingClientRect(); return f.right <= e.getBoundingClientRect().right + 1 && e.scrollWidth <= e.clientWidth + 1; });
    assert.ok(cabe, "a frase cabe na bolha");
    solta();
    await resposta(page).locator(".msg-text", { hasText: "Feito, sem duplicada." }).waitFor();
    await page.waitForFunction(() => /Feito, sem duplicada\.$/.test(document.querySelector("[role=status]:not(.guia-status)")?.textContent ?? ""));
    assert.equal(await resposta(page).locator(".msg-carga").count(), 0);
    assert.deepEqual((await page.evaluate(() => window.__ditos)).filter((t) => t && !/Feito, sem duplicada\./.test(t)), []);
    await ctx.close();
  });

  test(`${width}: enquanto envia, com movimento reduzido a frase fica fixa e sem brilho`, async () => {
    let solta;
    const { ctx, page } = await abrir({ width, segura: new Promise((ok) => { solta = ok; }) });
    await enviar(page, "@Xerife algo fora do padrão?");
    const carga = resposta(page).locator(".msg-carga");
    await carga.waitFor();
    await page.waitForTimeout(2400);
    assert.equal(await carga.textContent(), "Patrulhando os gastos…");
    assert.deepEqual(await animacoes(page, ".msg-carga-anel, .msg-carga-frase"), ["none", "none"]);
    assert.deepEqual(await emAnimacao(page), []);
    solta();
    await ctx.close();
  });
  test(`${width}: enquanto envia, ligar o movimento reduzido no meio da espera congela a frase`, async () => {
    let solta;
    const { ctx, page } = await abrir({ width, segura: new Promise((ok) => { solta = ok; }) });
    await MOVIMENTO(page);
    await enviar(page, "@Detetive tem duplicada?");
    const carga = resposta(page).locator(".msg-carga");
    await carga.filter({ hasText: "Comparando cobranças parecidas…" }).waitFor({ timeout: 3500 }); // trocando
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.waitForTimeout(4500); // duas trocas
    assert.equal(await carga.textContent(), "Comparando cobranças parecidas…");
    solta();
    await ctx.close();
  });
  test(`${width}: resposta mostrada com movimento reduzido não se revela depois que a preferência muda; revelada, a marca dos botões sai`, async () => {
    const { ctx, page } = await abrir({ width, chat: (c, n) => ({ json: { reply: n === 1 ? palavras(40) : "Segunda.", context: "c", redirects: [RED] } }) });
    await enviar(page, "@Detetive tem duplicada?"); // contexto reduce
    const primeira = page.locator(".chat > .msg-piggy").filter({ hasText: "p39" });
    await primeira.locator(".msg-text", { hasText: "p39" }).waitFor();
    await MOVIMENTO(page);
    await enviar(page, "@Detetive e agora?"); // re-render com a preferência nova
    await resposta(page).locator(".rv").first().waitFor();
    assert.equal(await primeira.locator(".rv").count(), 0, "a primeira resposta não reanima");
    assert.equal(await primeira.locator(".msg-acoes[data-revela]").count(), 0, "nem os botões dela");
    assert.equal(await primeira.locator(".msg-acoes button").first().evaluate((b) => b.checkVisibility({ visibilityProperty: true, opacityProperty: true })), true);
    const ultima = resposta(page).locator(".msg-acoes");
    assert.equal(await ultima.getAttribute("data-revela"), "true"); // a nova revela
    await page.waitForFunction(() => !document.querySelector(".chat > .msg-piggy:last-child .msg-acoes[data-revela]"), null, { timeout: 5000 });
    assert.equal(await resposta(page).locator(".rv").count(), 0);
    await ctx.close();
  });
  test(`${width}: durante a revelação os botões das ações não recebem foco; depois recebem e o clique funciona`, async () => {
    const { ctx, page } = await abrir({ width, ativos: ["detetive", "carteiro"], chat: (c, n, kind) => (kind === "detetive" ? { json: { reply: palavras(150), context: "c", redirects: [RED] } } : null) });
    await MOVIMENTO(page);
    await enviar(page, "@Detetive tem duplicada?");
    await resposta(page).locator(".rv").first().waitFor();
    const botao = resposta(page).locator(".msg-acoes button", { hasText: "Perguntar ao Carteiro" });
    const foca = () => botao.evaluate((b) => { b.focus(); return document.activeElement === b; });
    assert.equal(await foca(), false, "invisível e focável");
    assert.equal(await botao.evaluate((b) => b.checkVisibility({ visibilityProperty: true })), false);
    await page.waitForFunction(() => !document.getAnimations().some((a) => a.playState !== "finished"), null, { timeout: 5000 });
    assert.equal(await foca(), true);
    await botao.click();
    await resposta(page).locator(".msg-text", { hasText: "Resposta 2 do carteiro." }).waitFor();
    await ctx.close();
  });

  test(`${width}: URL gigante sem espaço não estoura a bolha`, async () => {
    const url = "https://exemplo.com/" + "a".repeat(300);
    const { ctx, page } = await abrir({ width, chat: () => ({ json: { reply: `Veja ${url}`, context: "c", redirects: [] } }) });
    await enviar(page, "@Detetive oi");
    const bolha = resposta(page).locator(".msg-text", { hasText: "exemplo.com" });
    await bolha.waitFor();
    const m = await page.evaluate(() => ({ doc: document.documentElement.scrollWidth, w: innerWidth }));
    assert.ok(m.doc <= m.w, `documento ${m.doc} > ${m.w}`);
    assert.ok(await bolha.evaluate((e) => e.scrollWidth <= e.clientWidth + 1), "texto passa da bolha");
    await ctx.close();
  });

  test(`${width}: URL gigante sem espaço não estoura a bolha DURANTE a revelação (.rv)`, async () => {
    const url = "https://exemplo.com/" + "a".repeat(300);
    const { ctx, page } = await abrir({ width, chat: () => ({ json: { reply: `Veja ${url}`, context: "c", redirects: [] } }) });
    await MOVIMENTO(page);
    await enviar(page, "@Detetive oi");
    const bolha = resposta(page).locator(".msg-text", { hasText: "exemplo.com" });
    await bolha.locator(".rv").first().waitFor();
    const m = await page.evaluate(() => ({ doc: document.documentElement.scrollWidth, w: innerWidth, rv: document.querySelectorAll(".rv").length }));
    assert.ok(m.rv > 0, "sem .rv o caso não mede nada");
    assert.ok(m.doc <= m.w, `documento ${m.doc} > ${m.w}`);
    assert.ok(await bolha.evaluate((e) => e.scrollWidth <= e.clientWidth + 1), "texto passa da bolha");
    await ctx.close();
  });

  test(`${width}: acabada a revelação a bolha volta ao texto contínuo, sem spans, sem salto`, async () => {
    const texto = `um dois três ${palavras(10)}`;
    const { ctx, page } = await abrir({ width, chat: () => ({ json: { reply: texto, context: "c", redirects: [] } }) });
    await MOVIMENTO(page);
    await enviar(page, "@Detetive oi");
    const bolha = resposta(page).locator(".msg-text");
    await bolha.locator(".rv").first().waitFor();
    assert.ok((await bolha.locator(".rv").count()) > 0, "durante a revelação há spans");
    const alt = await bolha.evaluate((e) => e.getBoundingClientRect().height);
    await bolha.locator(".rv").first().waitFor({ state: "detached", timeout: 4000 });
    assert.equal(await bolha.textContent(), texto);
    assert.equal(await bolha.evaluate((e) => e.getBoundingClientRect().height), alt, "sem salto de altura");
    await ctx.close();
  });

  test(`${width}: URL gigante sem espaço não estoura a bolha do usuário`, async () => {
    const url = "https://exemplo.com/" + "a".repeat(300);
    for (const pergunta of [url, `@Detetive ${url}`]) {
      const { ctx, page } = await abrir({ width });
      await enviar(page, pergunta);
      const bolha = page.locator(".chat .msg-user", { hasText: "exemplo.com" });
      await bolha.waitFor();
      const m = await page.evaluate(() => ({ doc: document.documentElement.scrollWidth, w: innerWidth }));
      assert.ok(m.doc <= m.w, `documento ${m.doc} > ${m.w}`);
      assert.ok(await bolha.evaluate((e) => e.scrollWidth <= e.clientWidth + 1), "texto do usuário passa da bolha");
      await ctx.close();
    }
  });
}
