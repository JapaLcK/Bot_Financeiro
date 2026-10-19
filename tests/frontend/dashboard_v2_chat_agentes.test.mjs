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

// `lista`: sobrescreve o GET (status/json); `chat(corpo, n, kind)`: sobrescreve o POST de chat; `segura`: promise que segura o chat; `ativos`: quem começa ativo.
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
    await segura;
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

  test(`${width}: "fulano@gmail.com" não abre a lista nem pede nada; sem @ nenhuma requisição a /agents; o Piggy leva ícone e nome`, async () => {
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
    assert.deepEqual(pedidos, []);
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
    assert.deepEqual(chats.map(({ csrf, ...c }) => c), [{ kind: "detetive", message: "tem duplicada?", context: null }]);
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

  test(`${width}: o redirect "Perguntar ao Carteiro" manda a pergunta dele ao Carteiro`, async () => {
    const { ctx, page, chats } = await abrir({ width, ativos: ["detetive", "carteiro"],
      chat: (c, n, kind) => (kind === "detetive" ? { json: { reply: "Isso é com o Carteiro.", context: "c1", redirects: [{ kind: "carteiro", name: "Carteiro", question: "Que contas vencem?", access: "ready" }] } } : null) });
    await digitar(page, "@det");
    await lista(page).waitFor();
    await campo(page).pressSequentially("etive e os boletos?");
    await campo(page).press("Enter");
    await page.getByRole("button", { name: "Perguntar ao Carteiro" }).click();
    await resposta(page).locator(".msg-text", { hasText: "Resposta 2 do carteiro." }).waitFor();
    await ctx.close();
    assert.deepEqual(chats.map((c) => [c.kind, c.message]), [["detetive", "e os boletos?"], ["carteiro", "Que contas vencem?"]]);
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
