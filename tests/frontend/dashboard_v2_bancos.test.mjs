// Bancos conectados no /painel (#/conta/bancos, conta/Bancos.tsx, conta/BancoCard.tsx, lib/bancos.ts): a leitura. A desconexão mora em dashboard_v2_bancos_desconectar*.test.mjs. Plano:
// docs/plano-bancos-conectados-painel.md, W1. Três leituras: o snapshot do Open Finance (`/open-finance/{uid}`, o estado de cada
// conexão), `/api/v2/bancos` (os valores) e `/open-finance/{uid}/limite` (as vagas); só o snapshot é obrigatório.
//   · o topo é o `total` do servidor, nunca a soma dos cards; a junção é por `conexao_id`, nunca por nome; null é "—", nunca "R$ 0,00";
//   · a barra por banco só aparece quando nada deixa o número incerto e a soma fecha EXATA com o topo;
//   · o `uid` vem só da sessão; perfil sem `user_id` válido não pede nada de /open-finance.
import { test } from "node:test";
import assert from "node:assert/strict";
import { RESPOSTAS } from "./_painel.mjs";
import { abrir, navegador } from "./_guia.mjs";
import { CDN_OK, ERRO_404, ERRO_500, LIMITE, LOGO, PADRAO, con, servidorBancos } from "./_bancos.mjs";

const browser = navegador();
void browser;

async function bancos(opts = {}, tela = {}) {
  const s = servidorBancos(opts);
  const p = await abrir({ guia: "concluido", rota: "/conta/bancos", antes: s.antes, ...tela });
  return { ...p, srv: s }; // `s` é o do guia (_guia.mjs); o do servidor de bancos é `srv`
}
const esperaCards = (page, n) => page.waitForFunction((n) => document.querySelectorAll(".banco-card").length === n, n, { timeout: 8000 });
// Espera o fim da leitura de /bancos (o "…" sai do card).
const semReticencias = (page) => page.waitForFunction(() => !document.querySelector(".banco-total")?.textContent.includes("…") && document.querySelector(".banco-total"), null, { timeout: 8000 });
// Espera o /limite terminar e a tela pintar o que depende dele: sem isto, o teste lê a tela entre o snapshot e o /limite (CI lento).
const limiteLido = async ({ page, srv }) => { await srv.limiteFim; await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))); };
const totais = (page) => page.locator(".banco-card .banco-total").allTextContents();
const corpo = (page) => page.locator("body").innerText();
// O detalhe de um banco (dialog modal): abre pela linha `n` e devolve o locator dele; `fechaDetalhe` usa o X.
const detalhe = async (page, n = 0) => { await page.locator(".banco-linha").nth(n).click(); const d = page.locator("dialog.banco-det[open]"); await d.waitFor(); return d; };
const fechaDetalhe = async (page) => { await page.locator("dialog.banco-det[open]").getByRole("button", { name: "Fechar" }).click(); await page.locator("dialog.banco-det[open]").waitFor({ state: "detached" }); };

test("o topo é o total do servidor, não a soma dos cards: snapshot só com o banco 11 mostra R$ 67.000,00 no topo e R$ 45.000,00 no card", async () => {
  const { ctx, page, erros } = await bancos({ conexoes: [con(11, "Nubank")], bancos: "padrao" });
  await esperaCards(page, 1);
  await semReticencias(page);
  const r = { topo: await page.locator(".bancos-total").innerText(), cards: await totais(page), selos: await page.locator(".bancos-topo .selo").allTextContents() };
  await ctx.close();
  assert.deepEqual(r, { topo: "R$ 67.000,00", cards: ["R$ 45.000,00"], selos: ["conexão pausada"] });
  assert.deepEqual(erros, []);
});

test("dois bancos com o mesmo nome: cada card tem o valor da SUA conexão (junção por id, na ordem do snapshot)", async () => {
  const { ctx, page } = await bancos({ conexoes: [con(2, "Nubank"), con(1, "Nubank")], bancos: "mesmo_nome" });
  await esperaCards(page, 2);
  await semReticencias(page);
  const cards = await totais(page);
  await ctx.close();
  assert.deepEqual(cards, ["R$ 200,00", "R$ 100,00"]);
});

test("null é '—', nunca R$ 0,00: investido nulo, banco com tudo nulo e nenhuma 'R$ 0,00' na tela (lista e detalhe)", async () => {
  const a = await bancos({ conexoes: [con(11, "Nubank")], bancos: "investido_nulo" });
  await esperaCards(a.page, 1);
  await semReticencias(a.page);
  const d1 = await detalhe(a.page);
  const um = { detalhe: await d1.locator(".banco-det-dados").innerText(), corpo: await corpo(a.page) };
  await a.ctx.close();
  assert.match(um.detalhe, /Em conta\s+R\$ 5\.000,00/);
  assert.match(um.detalhe, /Investido\s+—/);
  assert.ok(!um.corpo.includes("R$ 0,00"), um.corpo);
  const b = await bancos({ bancos: "padrao" }); // o banco 13 do padrão: total, saldo e investido nulos
  await esperaCards(b.page, 3);
  await semReticencias(b.page);
  const d3 = await detalhe(b.page, 2);
  const tres = { linha: await b.page.locator(".banco-card").nth(2).innerText(), detalhe: await d3.locator(".banco-det-dados").innerText(), total: await d3.locator(".banco-det-total").innerText(), corpo: await corpo(b.page) };
  await b.ctx.close();
  assert.match(tres.detalhe, /Em conta\s+—/);
  assert.match(tres.detalhe, /Investido\s+—/);
  assert.equal(tres.total, "—");
  assert.ok(!tres.corpo.includes("R$ 0,00") && !tres.linha.includes("R$ 0,00"), tres.corpo);
});

test("barra por banco: 2 segmentos e 'Nubank 67%, Inter 33%' quando fecha; some com motivo no topo ou no banco, 1 banco só, banco fora do snapshot, total nulo e soma que não fecha", async () => {
  const nBarras = async (opts) => {
    const { ctx, page } = await bancos(opts);
    await page.locator(".bancos-topo").waitFor({ timeout: 8000 });
    await semReticencias(page);
    const barra = page.locator(".bancos-topo .comp-bar");
    const r = { barras: await barra.count(), segmentos: await page.locator(".bancos-topo .comp-bar span").count(), aria: (await barra.count()) ? await barra.getAttribute("aria-label") : null };
    await ctx.close();
    return r;
  };
  const dois = [con(11, "Nubank"), con(12, "Inter")];
  assert.deepEqual(await nBarras({ conexoes: dois, bancos: "barra" }), { barras: 1, segmentos: 2, aria: "Nubank 67%, Inter 33%" });
  const sem = { barras: 0, segmentos: 0, aria: null };
  assert.deepEqual(await nBarras({ conexoes: dois, bancos: "desatualizado" }), sem); // motivo no topo e no banco
  assert.deepEqual(await nBarras({ conexoes: PADRAO(), bancos: "padrao" }), sem); // motivo no topo, banco 13 com total nulo
  assert.deepEqual(await nBarras({ conexoes: [con(11, "Nubank")], bancos: "um_banco" }), sem); // menos de 2 não é composição
  assert.deepEqual(await nBarras({ conexoes: [con(11, "Nubank")], bancos: "barra" }), sem); // banco 12 que o snapshot não conhece
  const nulo = structuredClone(RESPOSTAS.bancos.barra); // banco sem número e sem motivo
  Object.assign(nulo.bancos[1], { saldo_conta: null, investido: null, total: null });
  nulo.total = "45000.00";
  assert.deepEqual(await nBarras({ conexoes: dois, bancos: nulo }), sem);
  const naoFecha = structuredClone(RESPOSTAS.bancos.barra);
  naoFecha.total = "67000.01"; // o servidor diz um centavo a mais do que as partes
  assert.deepEqual(await nBarras({ conexoes: dois, bancos: naoFecha }), sem);
});

test("leituras opcionais: /bancos 500 e 404 deixam os cards com nome e pílula, valores '—', sem topo nem barra; /limite 500 tira só as vagas", async () => {
  for (const [rotulo, bancosRes] of [["500", ERRO_500], ["404", ERRO_404]]) {
    const { ctx, page } = await bancos({ bancosRes });
    await esperaCards(page, 3);
    await page.waitForFunction(() => document.querySelector(".banco-total").textContent === "—", null, { timeout: 8000 }); // 500: 3 tentativas, ~3 s
    const r = {
      nomes: await page.locator(".banco-nome").allTextContents(), pilulas: await page.locator(".banco-estado").allTextContents(),
      totais: await totais(page), topo: await page.locator(".bancos-topo").count(), barra: await page.locator(".comp-bar").count(),
      vagas: await corpo(page).then((t) => /\d+ de \d+ bancos/.test(t)),
    };
    await ctx.close();
    assert.deepEqual(r, { nomes: ["Nubank", "Inter", "Itaú"], pilulas: ["Atualizado", "Atualizado", "Pausado"], totais: ["—", "—", "—"], topo: 0, barra: 0, vagas: true }, rotulo);
  }
  const { ctx, page } = await bancos({ limiteRes: ERRO_500, bancos: "padrao" });
  await esperaCards(page, 3);
  await semReticencias(page);
  await page.waitForTimeout(3500); // as tentativas do /limite
  const r = { vagas: /\d+ de \d+ bancos/.test(await corpo(page)), topo: await page.locator(".bancos-total").innerText(), cards: await totais(page), conectar: await page.getByRole("link", { name: "Conectar banco" }).count() };
  await ctx.close();
  assert.deepEqual(r, { vagas: false, topo: "R$ 67.000,00", cards: ["R$ 45.000,00", "R$ 22.000,00", "—"], conectar: 1 });
});

test("snapshot é obrigatório: 500 mostra o erro e 'Tentar de novo' (que recupera); 401 mostra 'Sua sessão terminou.' e nenhum card", async () => {
  let falha = true;
  const a = await bancos({ snap: (r) => (falha ? ERRO_500(r) : r.fulfill({ json: { ok: true, connections: PADRAO() } })), bancos: "padrao" });
  await a.page.getByRole("alert").waitFor({ timeout: 8000 });
  const erro = { texto: await a.page.getByRole("alert").innerText(), cards: await a.page.locator(".banco-card").count() };
  falha = false;
  await a.page.getByRole("button", { name: "Tentar de novo" }).click();
  await esperaCards(a.page, 3);
  await a.ctx.close();
  assert.match(erro.texto, /Não deu para carregar seus bancos\./);
  assert.equal(erro.cards, 0);
  const b = await bancos({ snap: (r) => r.fulfill({ status: 401, json: { detail: "Not authenticated" } }), bancos: "padrao" });
  await b.page.getByRole("alert").waitFor({ timeout: 8000 });
  const sessao = { texto: await b.page.getByRole("alert").innerText(), cards: await b.page.locator(".banco-card").count(), recarregar: await b.page.getByRole("button", { name: "Recarregar" }).count() };
  await b.ctx.close();
  assert.match(sessao.texto, /Sua sessão terminou\./);
  assert.deepEqual([sessao.cards, sessao.recarregar], [0, 1]);
});

test("vagas: '2 de 3 bancos' com o link 'Conectar banco' para /settings?view=open-finance; sem teto não diz 'de'; pode_adicionar false mostra a mensagem do servidor e nenhum link", async () => {
  const ler = async (limite) => {
    const p = await bancos({ limite, bancos: "padrao" });
    const { ctx, page } = p;
    await esperaCards(page, 3);
    await limiteLido(p);
    const r = { vagas: (await corpo(page)).match(/\d+ de \d+ bancos/)?.[0] ?? null, hrefs: await page.getByRole("link", { name: "Conectar banco" }).evaluateAll((l) => l.map((a) => a.getAttribute("href"))), corpo: await corpo(page) };
    await ctx.close();
    return r;
  };
  const a = await ler(LIMITE());
  assert.deepEqual([a.vagas, a.hrefs], ["2 de 3 bancos", ["/settings?view=open-finance"]]);
  const b = await ler(LIMITE({ of_banks_max: null }));
  assert.deepEqual([b.vagas, b.hrefs.length], [null, 1]);
  assert.ok(!/ de \d+ bancos/.test(b.corpo));
  const c = await ler(LIMITE({ pode_adicionar: false, message: "Seu plano permite 3 bancos." }));
  assert.deepEqual([c.vagas, c.hrefs.length, c.corpo.includes("Seu plano permite 3 bancos.")], ["2 de 3 bancos", 0, true]);
});

test("nenhum banco: o Piggy, 'Nenhum banco conectado.' e o link para conectar, sem topo", async () => {
  const { ctx, page } = await bancos({ conexoes: [], bancos: "vazio" });
  await page.locator(".empty img").waitFor({ timeout: 8000 });
  const r = { src: await page.locator(".empty img").getAttribute("src"), texto: await page.locator(".empty").innerText(), href: await page.getByRole("link", { name: "Conectar banco" }).getAttribute("href"), topo: await page.locator(".bancos-topo").count(), cards: await page.locator(".banco-card").count() };
  await ctx.close();
  assert.match(r.src, /brand\/icon\.png$/);
  assert.match(r.texto, /Nenhum banco conectado\./);
  assert.deepEqual([r.href, r.topo, r.cards], ["/settings?view=open-finance", 0, 0]);
});

test("nenhum banco: 'Conectar banco' tem 44 px em 390×844 e em 1280×800, sem rolagem lateral (só o estado vazio; na lista com bancos o '+' é o `.banco-mais`)", async () => {
  for (const [width, height] of [[390, 844], [1280, 800]]) {
    const { ctx, page } = await bancos({ conexoes: [], bancos: "vazio" }, { width, height });
    await page.locator(".empty img").waitFor({ timeout: 8000 });
    const r = await page.evaluate(() => ({ alt: Math.round(document.querySelector(".empty a.btn").getBoundingClientRect().height), lateral: document.documentElement.scrollWidth - innerWidth }));
    await ctx.close();
    assert.ok(r.alt >= 44, `${width}×${height}: botão com ${r.alt}px`);
    assert.equal(r.lateral, 0);
  }
});

test("tempo de sincronia (no detalhe), com o relógio em 2026-10-02 15:00Z: há 12 min (6 casas de fração), há 3 h, há 2 dias; <time> com datetime e title; sem data não há <time> nem a linha; a lista não tem <time>", async () => {
  const conexoes = [con(11, "Nubank", "updated", "Atualizado", "2026-10-02T14:47:59.123456+00:00"), con(12, "Inter", "updated", "Atualizado", "2026-10-02T12:00:00+00:00"),
    con(13, "Itaú", "updated", "Atualizado", "2026-09-30T15:00:00+00:00"), con(14, "Caixa", "updated", "Atualizado", null)];
  const { ctx, page } = await bancos({ conexoes, bancos: "padrao" });
  await esperaCards(page, 4);
  const naLista = await page.locator(".banco-card time").count();
  const r = [];
  for (let n = 0; n < 4; n++) {
    const d = await detalhe(page, n);
    const t = await d.locator("time").evaluateAll((l) => l.map((e) => [e.textContent, e.getAttribute("datetime"), e.title]));
    r.push({ t: t[0] ?? null, linha: await d.getByText("Última atualização").count() });
    await fechaDetalhe(page);
  }
  await ctx.close();
  assert.equal(naLista, 0);
  assert.deepEqual(r.map((x) => x.t && x.t[0]), ["há 12 min", "há 3 h", "há 2 dias", null]);
  assert.deepEqual(r.map((x) => x.linha), [1, 1, 1, 0]);
  assert.equal(r[0].t[1], "2026-10-02T14:47:59.123456+00:00");
  assert.match(r[0].t[2], /^02\/10\/2026,? 11:47$/); // o title em São Paulo
});

test("cor do banco: Itaú na cor da marca; Nubank (roxo, 2,64:1 sobre o card), BTG (azul-marinho) e banco desconhecido na escala de tinta", async () => {
  const { ctx, page } = await bancos({ conexoes: [con(11, "Nubank"), con(12, "Itaú Unibanco"), con(13, "Banco XYZ"), con(14, "BTG Pactual")], bancos: "padrao" });
  await esperaCards(page, 4);
  const cores = await page.locator(".banco-ini").evaluateAll((l) => l.map((e) => getComputedStyle(e).backgroundColor));
  await ctx.close();
  const tintas = ["rgb(244, 244, 246)", "rgb(168, 168, 179)", "rgb(139, 139, 150)"];
  assert.equal(cores[1], "rgb(236, 112, 0)");
  assert.ok(tintas.includes(cores[0]), `Nubank: ${cores[0]}`);
  assert.ok(tintas.includes(cores[2]), `Banco XYZ: ${cores[2]}`);
  assert.ok(tintas.includes(cores[3]), `BTG: ${cores[3]}`);
});

test("estado: updated é ok (pontinho verde), needs_user_action é erro (laranja), estado desconhecido é pendente (âmbar, nunca verde); o rótulo de ui.label está na linha e ui.detail só no detalhe", async () => {
  const conexoes = [con(11, "Nubank", "updated", "Atualizado"), con(12, "Inter", "needs_user_action", "Reconectar", undefined, "O banco pediu novo login."), con(13, "Itaú", "foo", ""), con(14, "Caixa", "constructor", "")];
  const { ctx, page } = await bancos({ conexoes, bancos: "padrao" });
  await esperaCards(page, 4);
  const r = await page.locator(".banco-card").evaluateAll((cs) => cs.map((c) => { const s = c.querySelector(".banco-estado"); return [s.className.replace("banco-estado ", ""), s.textContent, getComputedStyle(s.querySelector(".ponto")).backgroundColor, c.innerText.includes("O banco pediu novo login.")]; }));
  const d = await detalhe(page, 1);
  const noDetalhe = await d.innerText();
  await ctx.close();
  // "constructor" não é estado: pendente, não "estado-function Object()…"
  assert.deepEqual(r, [["estado-ok", "Atualizado", "rgb(61, 220, 151)", false], ["estado-erro", "Reconectar", "rgb(255, 122, 69)", false], ["estado-pendente", "Pendente", "rgb(245, 181, 68)", false], ["estado-pendente", "Pendente", "rgb(245, 181, 68)", false]]);
  assert.ok(noDetalhe.includes("O banco pediu novo login.") && noDetalhe.includes("Reconectar"), noDetalhe);
});

test("rota e isolamento: lê /open-finance/7 e /open-finance/7/limite (nunca /api/v2/open-finance); perfil sem user_id válido não pede nada de /open-finance e mostra o erro", async () => {
  const { srv, ctx, page } = await bancos({ bancos: "padrao" });
  await esperaCards(page, 3);
  const pedidos = [...new Set(srv.reqs)].sort();
  await ctx.close();
  assert.deepEqual(pedidos, ["/api/v2/bancos", "/auth/dashboard-profile", "/open-finance/7", "/open-finance/7/limite"]);
  for (const perfil of [{ display_name: "Ana", email: "a@b.c", whatsapp_linked: false }, { display_name: "Ana", email: "a@b.c", user_id: 0, whatsapp_linked: false }, { display_name: "Ana", email: "a@b.c", user_id: "7/../8", whatsapp_linked: false }]) {
    const o = await bancos({ perfil, bancos: "padrao" });
    await o.page.getByRole("alert").waitFor({ timeout: 8000 });
    await o.page.getByRole("button", { name: "Tentar de novo" }).click(); // o "de novo" também não inventa um uid
    await o.page.waitForTimeout(300);
    const r = { of: o.srv.reqs.filter((p) => p.includes("open-finance")), texto: await o.page.getByRole("alert").innerText() };
    await o.ctx.close();
    assert.deepEqual(r.of, [], JSON.stringify(perfil));
    assert.match(r.texto, /Não deu para carregar seus bancos\./);
  }
});

test("navegação: 'Bancos conectados' no trilho da Conta (aria-current, título, sem seletor de mês) e no grupo Conta do Menu do celular", async () => {
  const a = await bancos({ bancos: "padrao" }, { width: 1280, height: 800 });
  await esperaCards(a.page, 3);
  const r = { atual: await a.page.locator('.rail-conta [aria-current="page"]').innerText(), titulo: await a.page.title(), h1: await a.page.locator("#page-title").innerText(), mes: await a.page.locator(".topbar .month-switch").count() };
  await a.ctx.close();
  assert.deepEqual(r, { atual: "Bancos conectados", titulo: "PigBank · Bancos conectados", h1: "Bancos conectados", mes: 0 });
  const b = await bancos({ bancos: "padrao" }, { width: 390, height: 844 });
  await b.page.locator(".tabbar").getByRole("button", { name: "Menu" }).click();
  const itens = await b.page.locator(".menu-telas-conta li").allTextContents();
  await b.ctx.close();
  assert.ok(itens.map((t) => t.trim()).includes("Bancos conectados"), String(itens));
});

test("decimal em notação científica não derruba a tela: '1E-7' vira R$ 0,0000001, '1000' R$ 1.000,00, '0' R$ 0,00", async () => {
  const { ctx, page, erros } = await bancos({ conexoes: [con(11, "Nubank"), con(12, "Inter")], bancos: "cientifico" });
  await esperaCards(page, 2);
  await semReticencias(page);
  const d = await detalhe(page, 0); // o "R$ 0,00" é o investido "0" do 1º banco
  const t = await corpo(page) + await d.innerText();
  await ctx.close();
  for (const esperado of ["R$ 0,0000001", "R$ 1.000,00", "R$ 0,00"]) assert.ok(t.includes(esperado), `${esperado} em ${t}`);
  assert.deepEqual(erros, []);
});

test("layout 1280×800: dois cards lado a lado, no mesmo topo; 390×844: uma coluna, sem rolagem lateral, o botão mais de conectar e a linha com 44 px; nome de 70 letras sem espaço a 320, 375 e 1280 também", async () => {
  const a = await bancos({ conexoes: [con(11, "Nubank"), con(12, "Inter")], bancos: "barra" }, { width: 1280, height: 800 });
  await esperaCards(a.page, 2);
  const caixas = await a.page.locator(".banco-card").evaluateAll((l) => l.map((e) => { const r = e.getBoundingClientRect(); return { top: Math.round(r.top), left: Math.round(r.left) }; }));
  await a.ctx.close();
  assert.equal(caixas[0].top, caixas[1].top);
  assert.notEqual(caixas[0].left, caixas[1].left);
  const medidas = (page) => page.evaluate(() => ({ lateral: document.documentElement.scrollWidth - innerWidth, botao: Math.round(document.querySelector("a.banco-mais").getBoundingClientRect().height), linha: Math.round(document.querySelector(".banco-linha").getBoundingClientRect().height), lefts: [...document.querySelectorAll(".banco-card")].map((e) => Math.round(e.getBoundingClientRect().left)) }));
  const b = await bancos({ conexoes: [con(11, "Nubank"), con(12, "Inter")], bancos: "barra" }, { width: 390, height: 844 });
  await esperaCards(b.page, 2);
  const m = await medidas(b.page);
  await b.ctx.close();
  assert.equal(m.lateral, 0);
  assert.equal(new Set(m.lefts).size, 1); // uma coluna
  assert.ok(m.botao >= 44 && m.linha >= 44, `botão com ${m.botao}px, linha com ${m.linha}px`);
  const longo = "W".repeat(70);
  for (const width of [320, 375, 1280]) {
    const c = await bancos({ conexoes: [con(11, longo), con(12, "Inter")], bancos: "barra" }, { width, height: 800 });
    await esperaCards(c.page, 2);
    await semReticencias(c.page);
    const l = await medidas(c.page);
    await c.ctx.close();
    assert.equal(l.lateral, 0, `${width}: rolagem lateral`);
  }
});

// ---- Achados do Tester no W1 ----
// Um corpo de /bancos montado à mão: `[id, total]` por banco (saldo = total, sem investimento), topo e motivos do topo.
const corpoBancos = (topo, linhas) => ({
  total: topo, motivos: [],
  bancos: linhas.map(([conexao_id, total]) => ({ conexao_id, saldo_conta: total, investido: null, total, contas: 1, cartoes: 0, investimentos: 0, motivos: [] })),
});
const refoco = (page) => page.evaluate(() => window.dispatchEvent(new Event("visibilitychange"))); // o foco da aba: o React Query relê as leituras vivas
// A barra mede: quantos segmentos, o aria-label e quanto da largura a barra preenche (borda direita do último segmento − borda esquerda do primeiro, contra a barra).
const barraMedida = async (opts) => {
  const { ctx, page } = await bancos(opts);
  await page.locator(".bancos-topo").waitFor({ timeout: 8000 });
  await semReticencias(page);
  const r = await page.evaluate(() => {
    const b = document.querySelector(".bancos-topo .comp-bar");
    if (!b) return { segmentos: 0, aria: null, vazio: null };
    const s = [...b.querySelectorAll("span")].map((e) => e.getBoundingClientRect()), br = b.getBoundingClientRect();
    return { segmentos: s.length, aria: b.getAttribute("aria-label"), vazio: Math.round((br.right - s.at(-1).right + (s[0].left - br.left)) * 10) / 10 };
  });
  await ctx.close();
  return r;
};
const dois = [con(11, "Nubank"), con(12, "Inter")];

test("a barra preenche a largura toda mesmo quando os totais somam menos de 1 (R$ 0,40 + R$ 0,30, topo R$ 0,70)", async () => {
  const pequeno = await barraMedida({ conexoes: dois, bancos: corpoBancos("0.70", [[11, "0.40"], [12, "0.30"]]) });
  assert.deepEqual([pequeno.segmentos, pequeno.aria], [2, "Nubank 57%, Inter 43%"]);
  assert.ok(pequeno.vazio <= 1, `sobrou ${pequeno.vazio}px vazios na barra`);
  const grande = await barraMedida({ conexoes: dois, bancos: "barra" }); // o caminho que já funcionava continua inteiro
  assert.deepEqual([grande.segmentos, grande.aria], [2, "Nubank 67%, Inter 33%"]);
  assert.ok(grande.vazio <= 1, `sobrou ${grande.vazio}px vazios na barra`);
});

test("banco com total zero não vira segmento nem impede a barra; total negativo (mesmo com a soma fechando) e nulo recusam a barra", async () => {
  const tres = [...dois, con(14, "C6 Bank")];
  assert.deepEqual(await barraMedida({ conexoes: tres, bancos: corpoBancos("67000.00", [[11, "45000.00"], [12, "22000.00"], [14, "0.00"]]) }),
    { segmentos: 2, aria: "Nubank 67%, Inter 33%", vazio: 0 }); // o C6 aparece como card, mas não na barra
  const semBarra = { segmentos: 0, aria: null, vazio: null };
  assert.deepEqual(await barraMedida({ conexoes: tres, bancos: corpoBancos("66999.99", [[11, "45000.00"], [12, "22000.00"], [14, "-0.01"]]) }), semBarra);
  assert.deepEqual(await barraMedida({ conexoes: tres, bancos: corpoBancos("67000.00", [[11, "45000.00"], [12, "22000.00"], [14, null]]) }), semBarra);
  assert.deepEqual(await barraMedida({ conexoes: tres, bancos: corpoBancos("45000.00", [[11, "45000.00"], [12, "0.00"], [14, "0.00"]]) }), semBarra); // 1 segmento só
});

test("a barra recusa conexao_id repetido em /bancos (a soma fecha e todos os ids são do snapshot)", async () => {
  const r = await barraMedida({ conexoes: dois, bancos: corpoBancos("67000.00", [[11, "45000.00"], [11, "22000.00"]]) });
  assert.deepEqual(r, { segmentos: 0, aria: null, vazio: null });
});

test("releitura de /bancos que falha tira os números da tela (nunca deixa o valor velho); idem /limite e as vagas", async () => {
  let falha = false;
  const { ctx, page } = await bancos({
    bancos: "padrao", bancosRes: (r) => (falha ? ERRO_500(r) : r.fulfill({ json: RESPOSTAS.bancos.padrao })),
    limiteRes: (r) => (falha ? ERRO_500(r) : r.fulfill({ json: LIMITE() })),
  });
  await esperaCards(page, 3);
  await semReticencias(page);
  const antes = { topo: await page.locator(".bancos-total").innerText(), vagas: /2 de 3 bancos/.test(await corpo(page)) };
  falha = true;
  await refoco(page);
  await page.waitForFunction(() => !document.querySelector(".bancos-topo") && !/\d+ de \d+ bancos/.test(document.body.innerText), null, { timeout: 12000 }); // 3 tentativas, ~3 s
  const depois = { totais: await totais(page), cards: await page.locator(".banco-card").count(), corpo: await corpo(page) };
  await ctx.close();
  assert.deepEqual(antes, { topo: "R$ 67.000,00", vagas: true });
  assert.deepEqual([depois.totais, depois.cards], [["—", "—", "—"], 3]);
  assert.ok(!depois.corpo.includes("45.000,00"), depois.corpo);
});

test("sessão acaba depois de a lista já estar em cache: o 401 da releitura esconde os cards", async () => {
  let expirou = false;
  const { ctx, page } = await bancos({ snap: (r) => (expirou ? r.fulfill({ status: 401, json: { detail: "Not authenticated" } }) : r.fulfill({ json: { ok: true, connections: PADRAO() } })), bancos: "padrao" });
  await esperaCards(page, 3);
  expirou = true;
  await refoco(page);
  await page.getByRole("alert").waitFor({ timeout: 8000 });
  const r = { texto: await page.getByRole("alert").innerText(), cards: await page.locator(".banco-card").count() };
  await ctx.close();
  assert.match(r.texto, /Sua sessão terminou\./);
  assert.equal(r.cards, 0);
});

test("of_banks_max 0 não mostra '0 de 0 bancos'; em_uso que não é número também não mostra a linha ('null de 3 bancos')", async () => {
  const vagas = async (limite) => {
    const p = await bancos({ limite, bancos: "padrao" });
    const { ctx, page } = p;
    await esperaCards(page, 3);
    await semReticencias(page);
    await limiteLido(p);
    const t = await corpo(page);
    await ctx.close();
    return /\bde\b \d+ bancos|null|undefined/.test(t);
  };
  assert.equal(await vagas(LIMITE({ of_banks_max: 0, em_uso: 0 })), false);
  assert.equal(await vagas(LIMITE({ of_banks_max: 3, em_uso: null })), false);
  assert.equal(await vagas(LIMITE({ of_banks_max: 3, em_uso: "2" })), false);
  assert.equal(await vagas(LIMITE()), true); // controle positivo: com número a linha aparece
});

test("/limite com pode_adicionar false e message nula não oferece 'Conectar banco' (nem na lista vazia); com message mostra o texto", async () => {
  const links = async (limite, conexoes) => {
    const p = await bancos({ limite, conexoes, bancos: "padrao" });
    const { ctx, page } = p;
    await (conexoes?.length === 0 ? page.locator(".empty img").waitFor({ timeout: 8000 }) : esperaCards(page, 3));
    await limiteLido(p);
    const n = await page.getByRole("link", { name: "Conectar banco" }).count();
    await ctx.close();
    return n;
  };
  assert.equal(await links(LIMITE({ pode_adicionar: false, message: null })), 0);
  assert.equal(await links(LIMITE({ pode_adicionar: false, message: null }), []), 0);
  assert.equal(await links(LIMITE({ pode_adicionar: true, message: null })), 1); // controle positivo
});

test("/bancos fora do contrato (sem 'bancos', sem 'motivos', banco sem 'motivos', item null em 'bancos') é leitura falha: cards com nome e '—', sem topo nem barra, sem erro de página", async () => {
  for (const corpoRuim of [{ total: "10.00", motivos: [] }, { total: "10.00", bancos: [] }, { total: "10.00", motivos: [], bancos: [{ conexao_id: 11, total: "10.00" }] }, { total: "10.00", motivos: [], bancos: [null] }, []]) {
    const { ctx, page, erros } = await bancos({ conexoes: dois, bancos: corpoRuim });
    await esperaCards(page, 2);
    await page.waitForFunction(() => document.querySelector(".banco-total").textContent === "—", null, { timeout: 8000 });
    const r = { nomes: await page.locator(".banco-nome").allTextContents(), totais: await totais(page), topo: await page.locator(".bancos-topo").count(), barra: await page.locator(".comp-bar").count() };
    await ctx.close();
    assert.deepEqual(r, { nomes: ["Nubank", "Inter"], totais: ["—", "—"], topo: 0, barra: 0 }, JSON.stringify(corpoRuim));
    assert.deepEqual(erros, [], JSON.stringify(corpoRuim));
  }
});

// ---- Consertos dos achados do Tester (rodada 2) ----
const SEM_NUMEROS = { topo: 0, barra: 0, totais: ["—", "—"] };
test("motivos que não são texto ({'x':1}) tiram os números e não derrubam a SPA; 'constructor'/'toString'/'__proto__' viram texto cru, nunca função", async () => {
  const objeto = { ...corpoBancos("67000.00", [[11, "45000.00"], [12, "22000.00"]]), motivos: [{ x: 1 }] };
  const noBanco = corpoBancos("67000.00", [[11, "45000.00"], [12, "22000.00"]]);
  noBanco.bancos[0].motivos = [{ x: 1 }];
  for (const corpoRuim of [objeto, noBanco]) {
    const { ctx, page, erros } = await bancos({ conexoes: dois, bancos: corpoRuim });
    await esperaCards(page, 2);
    await page.waitForFunction(() => document.querySelector(".banco-total").textContent === "—", null, { timeout: 8000 });
    const r = { topo: await page.locator(".bancos-topo").count(), barra: await page.locator(".comp-bar").count(), totais: await totais(page) };
    await ctx.close();
    assert.deepEqual(r, SEM_NUMEROS);
    assert.deepEqual(erros, []);
  }
  const protos = ["constructor", "toString", "__proto__"];
  const corpoProto = { ...corpoBancos("67000.00", [[11, "45000.00"], [12, "22000.00"]]), motivos: protos };
  corpoProto.bancos[0].motivos = protos;
  const { ctx, page, erros } = await bancos({ conexoes: dois, bancos: corpoProto });
  await page.locator(".bancos-topo").waitFor({ timeout: 8000 });
  const selos = await page.locator(".bancos-topo .selo").allTextContents();
  await ctx.close();
  assert.deepEqual(selos, protos); // texto cru (código desconhecido), uma vez cada
  assert.deepEqual(erros, []);
});

test("snapshot fora do contrato não derruba a SPA: institution_name número, ui.label/ui.detail objeto, last_sync_at número viram nulo (nome 'Banco', 'Pendente', sem detalhe, sem <time>)", async () => {
  const feia = con(11, 5, "updated", { a: 1 }, 5, { b: 2 });
  const { ctx, page, erros } = await bancos({ conexoes: [feia, con(12, "Inter")], bancos: "barra" });
  await esperaCards(page, 2);
  const d = await detalhe(page, 0);
  const r = { nomes: await page.locator(".banco-nome").allTextContents(), selo: await page.locator(".banco-card").first().locator(".banco-estado").innerText(), card: await page.locator(".banco-card").first().innerText() + await d.innerText(), tempos: await d.locator("time").count() };
  await ctx.close();
  assert.deepEqual(r.nomes, ["Banco", "Inter"]);
  assert.equal(r.selo, "Pendente");
  assert.ok(!r.card.includes("object"), r.card);
  assert.equal(r.tempos, 0);
  assert.deepEqual(erros, []);
});

test("/limite com pode_adicionar false e message só de espaços: nem mensagem nem link (como message nula); com texto mostra a mensagem", async () => {
  const lido = async (message) => {
    const p = await bancos({ limite: LIMITE({ pode_adicionar: false, message }), bancos: "padrao" });
    const { ctx, page } = p;
    await esperaCards(page, 3);
    await limiteLido(p);
    const r = { area: await page.locator(".bancos-conectar").count(), links: await page.getByRole("link", { name: "Conectar banco" }).count() };
    await ctx.close();
    return r;
  };
  assert.deepEqual(await lido("   "), { area: 0, links: 0 });
  assert.deepEqual(await lido("Plano cheio."), { area: 1, links: 0 }); // controle positivo
});

test("topo não finito ('1E999' com duas partes de '5E998'): sem barra (nunca 'NaN%'); sem erro de página", async () => {
  const r = await barraMedida({ conexoes: dois, bancos: corpoBancos("1E999", [[11, "5E998"], [12, "5E998"]]) });
  assert.deepEqual(r, { segmentos: 0, aria: null, vazio: null });
  const { ctx, page, erros } = await bancos({ conexoes: dois, bancos: corpoBancos("1E999", [[11, "5E998"], [12, "5E998"]]) });
  await esperaCards(page, 2);
  const t = await corpo(page);
  await ctx.close();
  assert.ok(!t.includes("NaN"), t);
  assert.deepEqual(erros, []);
});

test("a cor de cada segmento da barra é a do card do mesmo banco, com um banco de total zero NO MEIO do snapshot", async () => {
  const tres = [con(11, "Alfa"), con(12, "Beta"), con(13, "Gama")]; // nomes sem cor de marca: escala de tinta pelo índice do snapshot
  const { ctx, page } = await bancos({ conexoes: tres, bancos: corpoBancos("67000.00", [[11, "45000.00"], [12, "0.00"], [13, "22000.00"]]) });
  await esperaCards(page, 3);
  await semReticencias(page);
  const cores = (loc) => loc.evaluateAll((l) => l.map((e) => getComputedStyle(e).backgroundColor));
  const r = { barra: await cores(page.locator(".bancos-topo .comp-bar span")), cards: await cores(page.locator(".banco-ini")) };
  await ctx.close();
  assert.equal(r.barra.length, 2);
  assert.notEqual(r.cards[0], r.cards[2]); // sem isto o teste não distinguiria o índice 0 do 2
  assert.notEqual(r.cards[1], r.cards[2]); // nem o índice recontado só entre bancos com saldo (Gama seria o 1)
  assert.deepEqual(r.barra, [r.cards[0], r.cards[2]]);
});

// ---- Codex no #1116 ----
test("releitura do snapshot que falha (500) depois de a lista estar na tela: o erro com 'Tentar de novo', nunca a lista velha; o botão recupera", async () => {
  let falha = false;
  const { ctx, page } = await bancos({ snap: (r) => (falha ? ERRO_500(r) : r.fulfill({ json: { ok: true, connections: PADRAO() } })), bancos: "padrao" });
  await esperaCards(page, 3);
  falha = true;
  await refoco(page);
  await page.getByRole("alert").waitFor({ timeout: 12000 }); // 3 tentativas, ~3 s
  const r = { texto: await page.getByRole("alert").innerText(), cards: await page.locator(".banco-card").count(), topo: await page.locator(".bancos-topo").count() };
  falha = false;
  await page.getByRole("button", { name: "Tentar de novo" }).click();
  await esperaCards(page, 3);
  await ctx.close();
  assert.match(r.texto, /Não deu para carregar seus bancos\./);
  assert.deepEqual([r.cards, r.topo], [0, 0]);
});

test("troca de conta em outra aba: com o /bancos da conta nova ainda no ar, o total da conta antiga nunca aparece ao lado da lista nova", async () => {
  let uid = 7, solta;
  const preso = new Promise((ok) => { solta = ok; });
  const s = servidorBancos({ bancos: "padrao" });
  const { ctx, page } = await abrir({
    guia: "concluido", rota: "/conta/bancos",
    antes: async (c) => {
      await s.antes(c);
      // Registradas depois, vencem as do servidor de bancos.
      await c.route((u) => u.pathname === "/auth/dashboard-profile", (r) => r.fulfill({ json: { display_name: "Ana", email: "ana@exemplo.com", user_id: uid, whatsapp_linked: false } }));
      await c.route((u) => u.pathname === "/open-finance/8", (r) => r.fulfill({ json: { ok: true, connections: [con(21, "C6 Bank")] } }));
      await c.route((u) => u.pathname === "/open-finance/8/limite", (r) => r.fulfill({ json: LIMITE({ em_uso: 1 }) }));
      await c.route((u) => u.pathname === "/api/v2/bancos", async (r) => { if (uid === 8) await preso; return r.fulfill({ json: RESPOSTAS.bancos.padrao }).catch(() => {}); });
    },
  });
  await esperaCards(page, 3);
  await page.locator(".bancos-topo").waitFor({ timeout: 8000 });
  uid = 8;
  await refoco(page);
  await page.waitForFunction(() => document.querySelectorAll(".banco-card").length === 1, null, { timeout: 8000 });
  const r = { corpo: await corpo(page), topo: await page.locator(".bancos-topo").count(), card: await page.locator(".banco-card .banco-total").innerText() };
  solta();
  await ctx.close();
  assert.match(r.corpo, /C6 Bank/);
  assert.equal(r.topo, 0, r.corpo);
  assert.ok(!r.corpo.includes("67.000,00"), r.corpo);
  assert.equal(r.card, "…");
});

test("troca de conta no meio da releitura: /bancos já com a sessão nova, perfil e snapshot da conta antiga presos; a lista antiga sai, o perfil é relido e vem a conta nova", async () => {
  let uid = 7, presos = 0, solta;
  const preso = new Promise((ok) => { solta = ok; });
  const novo = { total: "9999.00", motivos: [], bancos: [{ conexao_id: 21, saldo_conta: "9999.00", investido: null, total: "9999.00", contas: 1, cartoes: 0, investimentos: 0, motivos: [] }] };
  const s = servidorBancos({ bancos: "padrao" });
  const { ctx, page } = await abrir({
    guia: "concluido", rota: "/conta/bancos",
    antes: async (c) => {
      await s.antes(c);
      // A 1ª leitura do perfil depois da troca (a do foco) e o snapshot antigo ficam presos; a sessão já é da conta 8.
      await c.route((u) => u.pathname === "/auth/dashboard-profile", async (r) => {
        if (uid === 8 && presos++ === 0) await preso;
        return r.fulfill({ json: { display_name: "Ana", email: "ana@exemplo.com", user_id: uid, whatsapp_linked: false } }).catch(() => {});
      });
      await c.route((u) => u.pathname === "/open-finance/7", async (r) => { if (uid === 8) await preso; return r.fulfill({ json: { ok: true, connections: PADRAO() } }).catch(() => {}); });
      await c.route((u) => u.pathname === "/open-finance/8", (r) => r.fulfill({ json: { ok: true, connections: [con(21, "C6 Bank")] } }));
      await c.route((u) => u.pathname === "/open-finance/8/limite", (r) => r.fulfill({ json: LIMITE({ em_uso: 1 }) }));
      await c.route((u) => u.pathname === "/api/v2/bancos", async (r) => {
        if (uid === 8) await new Promise((ok) => setTimeout(ok, 300)); // depois da leitura do perfil do foco
        return r.fulfill({ json: uid === 8 ? novo : RESPOSTAS.bancos.padrao }).catch(() => {});
      });
    },
  });
  await esperaCards(page, 3);
  await page.locator(".bancos-topo").waitFor({ timeout: 8000 });
  uid = 8;
  // Amostra a tela a cada quadro: em nenhum o total da conta nova aparece junto dos bancos da antiga.
  await page.evaluate(() => { window.__mistura = false; const f = () => { const t = document.body.innerText; if (t.includes("9.999,00") && t.includes("Nubank")) window.__mistura = true; requestAnimationFrame(f); }; f(); });
  await refoco(page);
  await page.waitForFunction(() => document.querySelectorAll(".banco-card").length === 1 && document.body.innerText.includes("C6 Bank"), null, { timeout: 8000 });
  const r = { corpo: await corpo(page), mistura: await page.evaluate(() => window.__mistura) };
  solta();
  await ctx.close();
  assert.equal(r.mistura, false);
  assert.ok(!/Nubank|Inter|Itaú/.test(r.corpo), r.corpo); // o snapshot antigo segue preso, e a lista dele já saiu
});

test("401 numa leitura opcional (/bancos ou /limite) depois de a lista estar na tela: 'Sua sessão terminou.' e nenhum card; o 500 delas segue opcional", async () => {
  const r = {};
  for (const qual of ["bancos", "limite"]) {
    let expirou = false;
    const nao = (ok) => (q) => (expirou ? q.fulfill({ status: 401, json: { detail: "Not authenticated" } }) : ok(q));
    const { ctx, page } = await bancos({
      bancos: "padrao",
      ...(qual === "bancos" ? { bancosRes: nao((q) => q.fulfill({ json: RESPOSTAS.bancos.padrao })) } : { limiteRes: nao((q) => q.fulfill({ json: LIMITE() })) }),
    });
    await esperaCards(page, 3);
    // A 1ª leitura de cada uma tem de ter terminado: o foco com a leitura ainda no ar reaproveita a mesma, e o 401 nunca chega.
    await semReticencias(page);
    await page.waitForFunction(() => /2 de 3 bancos/.test(document.body.innerText), null, { timeout: 8000 });
    expirou = true;
    await refoco(page);
    await page.getByRole("alert").waitFor({ timeout: 8000 });
    r[qual] = { texto: await page.getByRole("alert").innerText(), cards: await page.locator(".banco-card").count() };
    await ctx.close();
  }
  for (const qual of ["bancos", "limite"]) {
    assert.match(r[qual].texto, /Sua sessão terminou\./, qual);
    assert.equal(r[qual].cards, 0, qual);
  }
});

test("rede caída em todas as leituras e 'Tentar de novo': volta a lista E os números (topo e vagas), não só a lista", async () => {
  let caiu = false;
  const cai = (ok) => (q) => (caiu ? ERRO_500(q) : ok(q));
  const { ctx, page } = await bancos({
    snap: cai((q) => q.fulfill({ json: { ok: true, connections: PADRAO() } })),
    bancos: "padrao", bancosRes: cai((q) => q.fulfill({ json: RESPOSTAS.bancos.padrao })),
    limiteRes: cai((q) => q.fulfill({ json: LIMITE() })),
  });
  await esperaCards(page, 3);
  await semReticencias(page);
  await page.waitForFunction(() => /2 de 3 bancos/.test(document.body.innerText), null, { timeout: 8000 });
  caiu = true;
  await refoco(page);
  await page.getByRole("alert").waitFor({ timeout: 12000 });
  await page.waitForTimeout(3500); // as releituras opcionais também esgotam as tentativas
  caiu = false;
  await page.getByRole("button", { name: "Tentar de novo" }).click();
  await esperaCards(page, 3);
  await page.locator(".bancos-topo").waitFor({ timeout: 8000 }).catch(() => {});
  const r = { topo: await page.locator(".bancos-topo").count(), vagas: /2 de 3 bancos/.test(await corpo(page)) };
  await ctx.close();
  assert.deepEqual(r, { topo: 1, vagas: true });
});

test("troca de conta com TODA releitura do perfil presa (só a conferência do /bancos responde): a lista antiga sai e fica o erro, nunca os bancos da outra conta", async () => {
  let uid = 7, bancosOk = false, conferido = false, solta;
  const preso = new Promise((ok) => { solta = ok; });
  const s = servidorBancos({ bancos: "padrao" });
  const { ctx, page } = await abrir({
    guia: "concluido", rota: "/conta/bancos",
    antes: async (c) => {
      await s.antes(c);
      await c.route((u) => u.pathname === "/auth/dashboard-profile", async (r) => {
        if (uid === 8 && !(bancosOk && !conferido && (conferido = true))) await preso; // só a 1ª depois do /bancos (a conferência) responde
        return r.fulfill({ json: { display_name: "Ana", email: "ana@exemplo.com", user_id: uid, whatsapp_linked: false } }).catch(() => {});
      });
      await c.route((u) => u.pathname === "/open-finance/7", async (r) => { if (uid === 8) await preso; return r.fulfill({ json: { ok: true, connections: PADRAO() } }).catch(() => {}); });
      await c.route((u) => u.pathname === "/api/v2/bancos", async (r) => {
        if (uid === 8) { await new Promise((ok) => setTimeout(ok, 300)); bancosOk = true; }
        return r.fulfill({ json: RESPOSTAS.bancos.padrao }).catch(() => {});
      });
    },
  });
  await esperaCards(page, 3);
  await page.locator(".bancos-topo").waitFor({ timeout: 8000 });
  uid = 8;
  await refoco(page);
  await page.getByRole("alert").waitFor({ timeout: 8000 });
  const r = { corpo: await corpo(page), cards: await page.locator(".banco-card").count() };
  solta();
  await ctx.close();
  assert.equal(r.cards, 0, r.corpo);
  assert.match(r.corpo, /Não deu para carregar seus bancos\./);
});

// ---- logo do banco (`logo_url` do snapshot; plano B: o servidor o empresta do catálogo da Pluggy) ----
const naoCarregou = (r) => r.fulfill({ status: 404, body: "" });
const comLogo = (opts = {}) => bancos({ conexoes: [con(11, "Itaú Unibanco", "updated", "Atualizado", undefined, null, LOGO), con(12, "Inter")], bancos: "padrao", ...opts });
const ini = (page, n) => page.locator(".banco-card").nth(n).locator(".banco-ini");
const bg = (loc) => loc.evaluate((e) => getComputedStyle(e).backgroundColor);

test("logo: com logo_url o ladrilho traz a imagem (alt vazio, src do servidor, carregada), sem a letra, em fundo claro neutro; o banco sem logo segue com letra e cor", async () => {
  const { ctx, page, srv, erros } = await comLogo();
  await esperaCards(page, 2);
  await page.waitForFunction(() => { const i = document.querySelector(".banco-ini img"); return i && i.complete && i.naturalWidth > 0; }, null, { timeout: 8000 });
  const img = ini(page, 0).locator("img");
  const r = {
    alt: await img.getAttribute("alt"), src: await img.getAttribute("src"), referrer: await img.getAttribute("referrerpolicy"), imgs: await page.locator(".banco-ini img").count(),
    letra: await ini(page, 0).innerText(), aria: await ini(page, 0).getAttribute("aria-hidden"), fundo: await bg(ini(page, 0)), corte: await ini(page, 0).evaluate((e) => getComputedStyle(e).overflow),
    outra: await ini(page, 1).innerText(), outraCor: await bg(ini(page, 1)),
  };
  await ctx.close();
  assert.deepEqual({ ...r, cdn: srv.cdn }, { alt: "", src: LOGO, referrer: "no-referrer", imgs: 1, letra: "", aria: "true", fundo: "rgb(244, 244, 246)", corte: "hidden", outra: "I", outraCor: r.outraCor, cdn: [LOGO] });
  assert.notEqual(r.outraCor, "rgb(244, 244, 246)"); // o banco sem logo não herda o fundo claro por engano
  assert.deepEqual(erros, []);
});

test("logo: a imagem que não carrega (404) sai e a letra volta, com a cor do banco (Itaú: laranja da marca)", async () => {
  const { ctx, page } = await bancos({ conexoes: [con(11, "Itaú Unibanco", "updated", "Atualizado", undefined, null, LOGO)], bancos: "padrao", cdn: naoCarregou });
  await esperaCards(page, 1);
  await page.waitForFunction(() => document.querySelector(".banco-ini")?.textContent === "I", null, { timeout: 8000 });
  await page.waitForFunction(() => getComputedStyle(document.querySelector(".banco-ini")).backgroundColor === "rgb(236, 112, 0)", null, { timeout: 8000 }); // o fundo tem transição curta
  const r = { imgs: await page.locator(".banco-ini img").count(), letra: await ini(page, 0).innerText(), fundo: await bg(ini(page, 0)), classe: await ini(page, 0).getAttribute("class") };
  await ctx.close();
  assert.deepEqual(r, { imgs: 0, letra: "I", fundo: "rgb(236, 112, 0)", classe: "banco-ini" });
});

test("logo: depois de uma URL que falhou, uma URL NOVA do mesmo banco é tentada (a falha é da URL, não do cartão)", async () => {
  const nova = "https://cdn.pluggy.ai/assets/connector-icons/613.svg";
  let url = LOGO;
  const { ctx, page } = await bancos({
    snap: (r) => r.fulfill({ json: { ok: true, connections: [con(11, "Nubank", "updated", "Atualizado", undefined, null, url)] } }), bancos: "padrao",
    cdn: (r) => (r.request().url() === LOGO ? naoCarregou(r) : CDN_OK(r)),
  });
  await esperaCards(page, 1);
  await page.waitForFunction(() => document.querySelector(".banco-ini")?.textContent === "N", null, { timeout: 8000 });
  url = nova;
  await refoco(page);
  await page.waitForFunction((u) => document.querySelector(".banco-ini img")?.getAttribute("src") === u, nova, { timeout: 8000 });
  await page.waitForFunction(() => { const i = document.querySelector(".banco-ini img"); return i && i.complete && i.naturalWidth > 0; }, null, { timeout: 8000 });
  await ctx.close();
});

test("logo: sem logo_url (null, ausente, número) ou com URL fora da regra (http, outro host, query, user@) é a letra, e o navegador nem pede a imagem", async () => {
  const ruins = [null, undefined, 123, "http://cdn.pluggy.ai/a.svg", "https://evil.example/a.svg", "https://cdn.pluggy.ai/a.svg?x=1", "https://u@cdn.pluggy.ai/a.svg", "https://cdn.pluggy.ai.evil.example/a.svg", "javascript:alert(1)"];
  const conexoes = ruins.map((l, i) => con(20 + i, "Alfa", "updated", "Atualizado", undefined, null, l));
  const { ctx, page, srv, erros } = await bancos({ conexoes, bancos: "padrao" });
  await esperaCards(page, ruins.length);
  await page.waitForTimeout(300); // uma imagem indevida teria sido pedida a esta altura
  const r = { imgs: await page.locator(".banco-ini img").count(), letras: await page.locator(".banco-ini").allInnerTexts() };
  await ctx.close();
  assert.deepEqual({ ...r, cdn: srv.cdn }, { imgs: 0, letras: ruins.map(() => "A"), cdn: [] });
  assert.deepEqual(erros, []);
});

test("logo: nenhum texto novo (o nome é o mesmo; o resto da linha é idêntico ao do banco sem logo) e o ladrilho segue aria-hidden", async () => {
  const { ctx, page } = await bancos({ conexoes: [con(11, "Alfa", "updated", "Atualizado", undefined, null, LOGO), con(11, "Alfa")], bancos: "padrao" });
  await esperaCards(page, 2);
  await semReticencias(page);
  const lado = (n) => page.locator(".banco-card").nth(n).evaluate((c) => [".banco-id", ".banco-total"].map((s) => c.querySelector(s).innerText));
  const r = { a: await lado(0), b: await lado(1), nomes: await page.locator(".banco-nome").allInnerTexts(), aria: await page.locator(".banco-ini").evaluateAll((l) => l.map((e) => e.getAttribute("aria-hidden"))) };
  await ctx.close();
  assert.deepEqual(r.a, r.b);
  assert.deepEqual(r.nomes, ["Alfa", "Alfa"]);
  assert.deepEqual(r.aria, ["true", "true"]);
});

test("logo: a imagem cabe no ladrilho de 44×44 da linha e no de 64×64 do detalhe, em 1280×800 e em 390×844, sem rolagem lateral", async () => {
  for (const [width, height] of [[1280, 800], [390, 844]]) {
    const { ctx, page } = await bancos({ conexoes: [con(11, "Nubank", "updated", "Atualizado", undefined, null, LOGO)], bancos: "padrao" }, { width, height });
    await esperaCards(page, 1);
    const medir = async (seletor) => {
      await page.waitForFunction((s) => { const i = document.querySelector(`${s} .banco-ini img`); return i && i.complete && i.naturalWidth > 0; }, seletor, { timeout: 8000 });
      return page.evaluate((s) => {
        const c = document.querySelector(`${s} .banco-ini`).getBoundingClientRect(), i = document.querySelector(`${s} .banco-ini img`).getBoundingClientRect();
        return { c: [c.width, c.height], i: [i.width, i.height], dentro: i.left >= c.left && i.top >= c.top && i.right <= c.right && i.bottom <= c.bottom, lateral: document.documentElement.scrollWidth > innerWidth };
      }, seletor);
    };
    const linha = await medir(".banco-linha");
    await detalhe(page);
    const grande = await medir("dialog.banco-det[open]");
    await ctx.close();
    assert.deepEqual(linha, { c: [44, 44], i: [44, 44], dentro: true, lateral: false }, `${width}x${height} linha`);
    assert.deepEqual(grande, { c: [64, 64], i: [64, 64], dentro: true, lateral: false }, `${width}x${height} detalhe`);
  }
});

// ---- A linha clicável e o detalhe (sem o "Gerenciar") ----
const dois_ = [con(11, "Nubank"), con(12, "Inter")];
const foco = (page) => page.evaluate(() => { const e = document.activeElement; return e?.classList?.contains("banco-linha") ? e.querySelector(".banco-nome").textContent : e?.id || e?.tagName; });
const noFoco = (page, t) => page.waitForFunction((t) => { const e = document.activeElement; return (e?.classList?.contains("banco-linha") ? e.querySelector(".banco-nome").textContent : e?.id || e?.tagName) === t; }, t, { timeout: 8000 });

test("a linha é um <button> de 44 px+ com o chevron; sem <details>/<summary> nem 'Gerenciar' na página; a linha só tem nome, estado e total (e a barra conta × investido)", async () => {
  const { ctx, page } = await bancos({ conexoes: dois_, bancos: "barra" });
  await esperaCards(page, 2);
  await semReticencias(page);
  const r = await page.evaluate(() => {
    const b = document.querySelector(".banco-linha");
    return { tag: b.tagName, tipo: b.getAttribute("type"), alto: Math.round(b.getBoundingClientRect().height), chevron: !!b.querySelector(".ph-caret-right[aria-hidden=true]"), details: document.querySelectorAll("details, summary").length,
      gerenciar: document.body.innerText.includes("Gerenciar"), texto: b.innerText.split("\n").map((t) => t.trim()).filter(Boolean), barra: b.querySelector(".banco-barra")?.getAttribute("aria-label") };
  });
  await ctx.close();
  assert.deepEqual([r.tag, r.tipo, r.chevron, r.details, r.gerenciar], ["BUTTON", "button", true, 0, false]);
  assert.ok(r.alto >= 44, `linha com ${r.alto}px`);
  assert.deepEqual(r.texto, ["N", "Nubank", "Atualizado", "R$ 45.000,00"]); // a inicial do ladrilho, o nome, o estado e o total; sem "há N min", contagens, "Conta … · Investido …" nem ui.detail
  assert.equal(r.barra, "Em conta 11%, investido 89%"); // 5.000 × 40.000
});

test("barra conta × investido da linha: só com os dois números, sem ressalva do servidor; 'investido 0' vira um segmento só", async () => {
  const lido = async (bancos_) => {
    const { ctx, page } = await bancos({ conexoes: [con(11, "Nubank")], bancos: bancos_ });
    await esperaCards(page, 1);
    await semReticencias(page);
    const r = await page.locator(".banco-barra").evaluateAll((l) => l.map((e) => [e.getAttribute("aria-label"), e.getAttribute("role"), e.children.length]));
    await ctx.close();
    return r;
  };
  assert.deepEqual(await lido("barra"), [["Em conta 11%, investido 89%", "img", 2]]); // o banco 12 que o snapshot não conhece só afeta a barra do topo
  const um = (o) => ({ ...corpoBancos("1.00", [[11, "1.00"]]), bancos: [{ conexao_id: 11, saldo_conta: "5000.00", investido: "40000.00", total: "45000.00", contas: 1, cartoes: 0, investimentos: 1, motivos: [], ...o }] });
  assert.deepEqual(await lido(um({})), [["Em conta 11%, investido 89%", "img", 2]]);
  assert.deepEqual(await lido(um({ investido: "0" })), [["Em conta 100%, investido 0%", "img", 1]]);
  assert.deepEqual(await lido(um({ investido: null })), []); // número nulo: sem barra
  assert.deepEqual(await lido(um({ saldo_conta: null })), []);
  assert.deepEqual(await lido(um({ investido: "-1.00" })), []);
  assert.deepEqual(await lido(um({ motivos: ["conexao_pausada"] })), []); // ressalva do servidor: o número é incerto
  assert.deepEqual(await lido(um({ saldo_conta: "0", investido: "0" })), []); // nada a comparar
});

test("o detalhe: nome, estado, ui.detail, total grande, linhas Última atualização / Em conta / Investido e só as contagens > 0 com ícone; 'Desconectar banco' no fim; nada de Reconectar, nem card dentro de card", async () => {
  const conexoes = [con(11, "Nubank", "needs_user_action", "Reconectar", undefined, "O banco pediu novo login."), con(12, "Inter")];
  const { ctx, page } = await bancos({ conexoes, bancos: "barra" });
  await esperaCards(page, 2);
  await semReticencias(page);
  const d = await detalhe(page, 0);
  const r = await d.evaluate((e) => ({
    nome: e.querySelector("h2").textContent, labelledby: e.getAttribute("aria-labelledby") === e.querySelector("h2").id, estado: e.querySelector(".banco-estado").textContent, nota: e.querySelector(".banco-det-id p")?.textContent, total: e.querySelector(".banco-det-total").textContent,
    linhas: [...e.querySelectorAll(".banco-det-dados > div:not(:has(.banco-det-cont))")].map((l) => l.innerText.replace(/\s+/g, " ").trim()),
    contagens: [...e.querySelectorAll(".banco-det-cont > span")].map((x) => [x.querySelector(".ph").className.replace("ph ", ""), x.querySelector("b").textContent, x.querySelector(".sr-only").textContent]),
    botoes: [...e.querySelectorAll("button")].map((b) => b.getAttribute("aria-label") || b.textContent.trim()), paineis: e.querySelectorAll(".panel").length, reconectar: /reconectar banco/i.test(e.innerText),
    seguro: (() => {
      const t = "Conexão regulamentada pelo Banco Central, oficial pelo Open Finance.", f = [...e.querySelectorAll(".banco-det-seguro")].find((x) => x.textContent.trim() === t);
      const lum = (c) => { const [r, g, b] = c.match(/\d+/g).slice(0, 3).map((v) => { const x = v / 255; return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4; }); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
      const cs = f && getComputedStyle(f), l1 = f && lum(cs.color), l2 = f && lum(cs.backgroundColor);
      return { noDetalhe: !!f, naLista: !!document.querySelector(".banco-linha")?.innerText.includes("Banco Central"), depoisDasLinhas: !!f && !!(e.querySelector(".banco-det-dados").compareDocumentPosition(f) & 4), antesDoBotao: !!f && !!(f.compareDocumentPosition(e.querySelector(".banco-det-acoes")) & 4),
        link: f ? f.querySelectorAll("a, button").length : -1, contraste: !!f && (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05) >= 4.5 };
    })(),
  }));
  await ctx.close();
  assert.equal(r.nome, "Nubank");
  assert.ok(r.labelledby);
  assert.deepEqual([r.estado, r.nota, r.total], ["Reconectar", "O banco pediu novo login.", "R$ 45.000,00"]);
  assert.deepEqual(r.linhas.map((l) => l.replace(/há .*/, "há …")), ["Última atualização há …", "Em conta R$ 5.000,00", "Investido R$ 40.000,00"]);
  assert.deepEqual(r.contagens, [["ph-bank", "2", "2 contas"], ["ph-credit-card", "1", "1 cartão"], ["ph-chart-line-up", "3", "3 investimentos"]]);
  assert.deepEqual(r.botoes, ["Fechar", "Desconectar banco"]);
  assert.deepEqual(r.seguro, { noDetalhe: true, naLista: false, depoisDasLinhas: true, antesDoBotao: true, link: 0, contraste: true }); // faixa estática, só no detalhe
  assert.deepEqual([r.paineis, r.reconectar], [0, false]);
});

test("o detalhe só lista as contagens > 0 (o Inter não tem cartão)", async () => {
  const { ctx, page } = await bancos({ conexoes: dois_, bancos: "barra" });
  await esperaCards(page, 2);
  await semReticencias(page);
  const d = await detalhe(page, 1);
  const r = await d.locator(".banco-det-cont span .sr-only").allTextContents();
  await ctx.close();
  assert.deepEqual(r, ["1 conta", "2 investimentos"]);
});

for (const semDialog of [false, true]) {
  test(`${semDialog ? "sem <dialog>" : "nativo"}: a linha abre o detalhe (foco no X); X, Esc e clique no fundo fecham e o foco volta à linha`, async () => {
    const { ctx, page, erros } = await bancos({ conexoes: dois_, bancos: "barra" }, { width: 1280, height: 800, semDialog });
    await esperaCards(page, 2);
    await semReticencias(page);
    const aberto = () => page.locator("dialog.banco-det[open]").count();
    await page.evaluate(() => document.querySelectorAll(".banco-linha").forEach((b) => b.addEventListener("mousedown", (e) => e.preventDefault()))); // como o Safari: clicar num botão não o foca, então o foco a devolver é o que o código escolhe, não o do navegador
    await page.locator(".banco-linha").nth(1).click();
    await page.locator("dialog.banco-det[open]").waitFor();
    const abertura = { n: await aberto(), foco: await page.evaluate(() => document.activeElement.getAttribute("aria-label")) };
    const saidas = {};
    await page.locator("dialog.banco-det[open]").getByRole("button", { name: "Fechar" }).click();
    await page.locator("dialog.banco-det[open]").waitFor({ state: "detached" });
    saidas.x = await foco(page);
    await page.locator(".banco-linha").nth(0).click();
    await page.locator("dialog.banco-det[open]").waitFor();
    await page.keyboard.press("Escape");
    await page.locator("dialog.banco-det[open]").waitFor({ state: "detached" });
    saidas.esc = await foco(page);
    await page.locator(".banco-linha").nth(1).click();
    await page.locator("dialog.banco-det[open]").waitFor();
    await page.waitForTimeout(500); // a janela do clique no fundo
    await page.mouse.click(300, 400); // fora do painel (à direita no nativo, centrado no fallback) e do trilho
    await page.locator("dialog.banco-det[open]").waitFor({ state: "detached" });
    saidas.fundo = await foco(page);
    await ctx.close();
    assert.deepEqual(abertura, { n: 1, foco: "Fechar" });
    assert.deepEqual(saidas, { x: "Inter", esc: "Nubank", fundo: "Inter" });
    assert.deepEqual(erros, []);
  });
}

test("o detalhe fecha sozinho quando a conexão some (outra aba a tirou) e o foco vai ao título", async () => {
  const { ctx, page, srv } = await bancos({ conexoes: [con(11, "Nubank"), con(12, "Inter")], bancos: "barra" });
  await esperaCards(page, 2);
  await semReticencias(page);
  await detalhe(page, 0);
  srv.conexoes = srv.conexoes.filter((c) => c.id !== 11);
  await refoco(page);
  await page.locator("dialog.banco-det[open]").waitFor({ state: "detached", timeout: 8000 });
  await esperaCards(page, 1);
  await noFoco(page, "page-title");
  const r = await page.locator("dialog.banco-det").count();
  await ctx.close();
  assert.equal(r, 0);
});

test("o '+' do topo: link redondo de 44 px com o nome 'Conectar banco' e o href do Open Finance; sem ele quando o servidor não deixa; nenhum botão 'Conectar banco' em texto na lista", async () => {
  const a = await bancos({ conexoes: dois_, bancos: "barra" });
  await esperaCards(a.page, 2);
  const mais = a.page.getByRole("link", { name: "Conectar banco", exact: true });
  const r = { n: await mais.count(), href: await mais.getAttribute("href"), medida: await mais.evaluate((e) => [Math.round(e.getBoundingClientRect().width), Math.round(e.getBoundingClientRect().height), e.textContent.trim(), !!e.closest("header")]) };
  await a.ctx.close();
  assert.deepEqual(r, { n: 1, href: "/settings?view=open-finance", medida: [44, 44, "", true] });
  const b = await bancos({ conexoes: dois_, bancos: "barra", limite: LIMITE({ pode_adicionar: false, message: "Plano cheio." }) });
  await esperaCards(b.page, 2);
  await limiteLido(b);
  const sem = await b.page.getByRole("link", { name: "Conectar banco" }).count();
  await b.ctx.close();
  assert.equal(sem, 0);
});

// O mesmo gesto no detalhe do banco (useDialogo): apertar no texto do painel e soltar no fundo não fecha; o clique simples no fundo fecha.
test("arrastar de dentro do detalhe e soltar no fundo não fecha o detalhe; o clique simples no fundo fecha", async () => {
  const { ctx, page } = await bancos({ conexoes: dois_, bancos: "barra" }, { width: 1280, height: 800 });
  await esperaCards(page, 2);
  await semReticencias(page);
  const d = await detalhe(page, 0);
  await page.waitForTimeout(500); // a janela do clique no fundo
  const h2 = await d.locator("h2").boundingBox();
  await page.mouse.move(h2.x + 3, h2.y + h2.height / 2);
  await page.mouse.down();
  await page.mouse.move(300, 400, { steps: 8 }); // o fundo, à esquerda do painel
  await page.mouse.up();
  assert.equal(await page.locator("dialog.banco-det[open]").count(), 1); // antes do clique seguinte: fechado, ele cairia na lista por baixo
  await page.mouse.click(300, 400);
  await page.locator("dialog.banco-det[open]").waitFor({ state: "detached" });
  await ctx.close();
});

// Cor da composição (conta × investido): dado usa neutro/série, nunca o rosa de decisão; o pontinho do detalhe tem a cor do segmento da barra.
test("a barra conta × investido não usa o rosa; o pontinho de 'Em conta' e 'Investido' no detalhe tem a cor do segmento e contraste >= 3:1 com o painel", async () => {
  const { ctx, page } = await bancos({ conexoes: dois_, bancos: "barra" }, { width: 1280, height: 800 });
  await esperaCards(page, 2);
  await semReticencias(page);
  const barra = await page.locator(".banco-barra").first().evaluate((b) => ({ cores: [...b.children].map((x) => getComputedStyle(x).backgroundColor), painel: getComputedStyle(b.closest(".panel")).backgroundColor }));
  const d = await detalhe(page, 0);
  const marcas = await d.locator(".banco-det-dados .marca").evaluateAll((l) => l.map((x) => [x.closest("dt").textContent, getComputedStyle(x).backgroundColor, x.getAttribute("aria-hidden")]));
  await ctx.close();
  const lum = (c) => { const [r, g, b] = c.match(/\d+/g).slice(0, 3).map((v) => { const x = v / 255; return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4; }); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
  const razao = (a, b) => (Math.max(lum(a), lum(b)) + 0.05) / (Math.min(lum(a), lum(b)) + 0.05);
  assert.equal(barra.cores.length, 2);
  assert.ok(!barra.cores.includes("rgb(255, 45, 142)"), barra.cores.join()); // --pink
  assert.notEqual(barra.cores[0], barra.cores[1]);
  for (const c of barra.cores) assert.ok(razao(c, barra.painel) >= 3, `${c} contra ${barra.painel}`);
  assert.deepEqual(marcas, [["Em conta", barra.cores[0], "true"], ["Investido", barra.cores[1], "true"]]);
});

// O X do detalhe fica à vista com o conteúdo rolado (a ação "Desconectar banco" do fim também), sem cobrir o conteúdo em repouso.
test("375×500: o X do detalhe segue visível depois de rolar até o fim, e em repouso não cobre o nome", async () => {
  const nota = "O banco pediu novo login e uma confirmação extra no aplicativo dele. ".repeat(4);
  const { ctx, page } = await bancos({ conexoes: [con(11, "Nubank", "needs_user_action", "Reconectar", undefined, nota), con(12, "Inter")], bancos: "barra" }, { width: 375, height: 500 });
  await esperaCards(page, 2);
  await semReticencias(page);
  const d = await detalhe(page, 0);
  const x = d.getByRole("button", { name: "Fechar" });
  const em = async () => {
    const [bx, h2, desc] = await Promise.all([x.boundingBox(), d.locator("h2").boundingBox(), d.getByRole("button", { name: "Desconectar banco" }).boundingBox()]);
    const topo = await x.evaluate((e) => { const r = e.getBoundingClientRect(), a = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2); return e === a || e.contains(a); });
    return { x: bx, h2, desc, topo, rolado: await d.evaluate((e) => e.scrollTop), cabe: await d.evaluate((e) => e.scrollHeight > e.clientHeight) };
  };
  const repouso = await em();
  await d.evaluate((e) => { e.scrollTop = e.scrollHeight; });
  await page.waitForTimeout(100);
  const fim = await em();
  await ctx.close();
  assert.ok(repouso.cabe && repouso.rolado === 0 && repouso.topo, JSON.stringify(repouso));
  assert.ok(repouso.h2.y >= repouso.x.y + repouso.x.height, `o X cobre o nome em repouso: ${JSON.stringify(repouso)}`);
  assert.ok(fim.rolado > 0 && fim.x.y >= 0 && fim.x.y + fim.x.height <= 500 && fim.topo, `o X saiu da tela: ${JSON.stringify(fim)}`);
  assert.ok(fim.desc.y >= 0 && fim.desc.y + fim.desc.height <= 500, `o Desconectar saiu da dobra: ${JSON.stringify(fim)}`);
});
