// Bancos conectados no /painel (#/conta/bancos, conta/Bancos.tsx, conta/BancoCard.tsx, lib/bancos.ts), só leitura. Plano:
// docs/plano-bancos-conectados-painel.md, W1. Três leituras: o snapshot do Open Finance (`/open-finance/{uid}`, o estado de cada
// conexão), `/api/v2/bancos` (os valores) e `/open-finance/{uid}/limite` (as vagas); só o snapshot é obrigatório.
//   · o topo é o `total` do servidor, nunca a soma dos cards; a junção é por `conexao_id`, nunca por nome; null é "—", nunca "R$ 0,00";
//   · a barra por banco só aparece quando nada deixa o número incerto e a soma fecha EXATA com o topo;
//   · o `uid` vem só da sessão; perfil sem `user_id` válido não pede nada de /open-finance.
import { test } from "node:test";
import assert from "node:assert/strict";
import { RESPOSTAS } from "./_painel.mjs";
import { abrir, navegador } from "./_guia.mjs";
import { ERRO_404, ERRO_500, LIMITE, PADRAO, con, servidorBancos } from "./_bancos.mjs";

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
const totais = (page) => page.locator(".banco-card .banco-total").allTextContents();
const corpo = (page) => page.locator("body").innerText();

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

test("null é '—', nunca R$ 0,00: investido nulo, banco com tudo nulo e nenhuma 'R$ 0,00' na tela", async () => {
  const a = await bancos({ conexoes: [con(11, "Nubank")], bancos: "investido_nulo" });
  await esperaCards(a.page, 1);
  await semReticencias(a.page);
  const um = { card: await a.page.locator(".banco-card").innerText(), corpo: await corpo(a.page) };
  await a.ctx.close();
  assert.match(um.card, /Conta R\$ 5\.000,00 · Investido —/);
  assert.ok(!um.corpo.includes("R$ 0,00"), um.corpo);
  const b = await bancos({ bancos: "padrao" }); // o banco 13 do padrão: total, saldo e investido nulos
  await esperaCards(b.page, 3);
  await semReticencias(b.page);
  const tres = { card: await b.page.locator(".banco-card").nth(2).innerText(), corpo: await corpo(b.page) };
  await b.ctx.close();
  assert.match(tres.card, /Conta — · Investido —/);
  assert.ok(!tres.corpo.includes("R$ 0,00"), tres.corpo);
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
      nomes: await page.locator(".banco-card h2").allTextContents(), pilulas: await page.locator(".banco-card .selo").allTextContents(),
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
    const { ctx, page } = await bancos({ limite, bancos: "padrao" });
    await esperaCards(page, 3);
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

test("nenhum banco: 'Conectar banco' tem 44 px em 390×844 e em 1280×800, sem rolagem lateral (o mesmo link da lista com bancos)", async () => {
  for (const [width, height] of [[390, 844], [1280, 800]]) {
    const { ctx, page } = await bancos({ conexoes: [], bancos: "vazio" }, { width, height });
    await page.locator(".empty img").waitFor({ timeout: 8000 });
    const r = await page.evaluate(() => ({ alt: Math.round(document.querySelector(".empty a.btn").getBoundingClientRect().height), lateral: document.documentElement.scrollWidth - innerWidth }));
    await ctx.close();
    assert.ok(r.alt >= 44, `${width}×${height}: botão com ${r.alt}px`);
    assert.equal(r.lateral, 0);
  }
});

test("tempo de sincronia, com o relógio em 2026-10-02 15:00Z: há 12 min (6 casas de fração), há 3 h, há 2 dias; <time> com datetime e title; sem data não há <time>", async () => {
  const conexoes = [con(11, "Nubank", "updated", "Atualizado", "2026-10-02T14:47:59.123456+00:00"), con(12, "Inter", "updated", "Atualizado", "2026-10-02T12:00:00+00:00"),
    con(13, "Itaú", "updated", "Atualizado", "2026-09-30T15:00:00+00:00"), con(14, "Caixa", "updated", "Atualizado", null)];
  const { ctx, page } = await bancos({ conexoes, bancos: "padrao" });
  await esperaCards(page, 4);
  const r = await page.locator(".banco-card").evaluateAll((cs) => cs.map((c) => { const t = c.querySelector("time"); return t && [t.textContent, t.getAttribute("datetime"), t.title]; }));
  await ctx.close();
  assert.deepEqual(r.map((x) => x && x[0]), ["há 12 min", "há 3 h", "há 2 dias", null]);
  assert.equal(r[0][1], "2026-10-02T14:47:59.123456+00:00");
  assert.match(r[0][2], /^02\/10\/2026,? 11:47$/); // o title em São Paulo
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

test("pílula do estado: updated é ok, needs_user_action é erro, estado desconhecido é pendente (nunca verde); texto de ui.label e ui.detail na tela", async () => {
  const conexoes = [con(11, "Nubank", "updated", "Atualizado"), con(12, "Inter", "needs_user_action", "Reconectar", undefined, "O banco pediu novo login."), con(13, "Itaú", "foo", ""), con(14, "Caixa", "constructor", "")];
  const { ctx, page } = await bancos({ conexoes, bancos: "padrao" });
  await esperaCards(page, 4);
  const r = await page.locator(".banco-card").evaluateAll((cs) => cs.map((c) => { const s = c.querySelector(".selo"); return [s.className.replace("selo ", ""), s.textContent, c.innerText.includes("O banco pediu novo login.")]; }));
  await ctx.close();
  assert.deepEqual(r, [["selo-ok", "Atualizado", false], ["selo-erro", "Reconectar", true], ["selo-pendente", "Pendente", false], ["selo-pendente", "Pendente", false]]); // "constructor" não é estado: pendente, não "selo-function Object()…"
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
  const t = await corpo(page);
  await ctx.close();
  for (const esperado of ["R$ 0,0000001", "R$ 1.000,00", "R$ 0,00"]) assert.ok(t.includes(esperado), `${esperado} em ${t}`);
  assert.deepEqual(erros, []);
});

test("layout 1280×800: dois cards lado a lado, no mesmo topo; 390×844: uma coluna, sem rolagem lateral, 'Conectar banco' com 44 px; 320 com nome de 60 letras sem espaço também", async () => {
  const a = await bancos({ conexoes: [con(11, "Nubank"), con(12, "Inter")], bancos: "barra" }, { width: 1280, height: 800 });
  await esperaCards(a.page, 2);
  const caixas = await a.page.locator(".banco-card").evaluateAll((l) => l.map((e) => { const r = e.getBoundingClientRect(); return { top: Math.round(r.top), left: Math.round(r.left) }; }));
  await a.ctx.close();
  assert.equal(caixas[0].top, caixas[1].top);
  assert.notEqual(caixas[0].left, caixas[1].left);
  const medidas = (page) => page.evaluate(() => ({ lateral: document.documentElement.scrollWidth - innerWidth, botao: Math.round([...document.querySelectorAll("a.btn")].find((e) => e.textContent.includes("Conectar banco")).getBoundingClientRect().height), lefts: [...document.querySelectorAll(".banco-card")].map((e) => Math.round(e.getBoundingClientRect().left)) }));
  const b = await bancos({ conexoes: [con(11, "Nubank"), con(12, "Inter")], bancos: "barra" }, { width: 390, height: 844 });
  await esperaCards(b.page, 2);
  const m = await medidas(b.page);
  await b.ctx.close();
  assert.equal(m.lateral, 0);
  assert.equal(new Set(m.lefts).size, 1); // uma coluna
  assert.ok(m.botao >= 44, `botão com ${m.botao}px`);
  const longo = "W".repeat(60);
  const c = await bancos({ conexoes: [con(11, longo), con(12, "Inter")], bancos: "barra" }, { width: 320, height: 800 });
  await esperaCards(c.page, 2);
  const l = await medidas(c.page);
  await c.ctx.close();
  assert.equal(l.lateral, 0);
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
    const { ctx, page } = await bancos({ limite, bancos: "padrao" });
    await esperaCards(page, 3);
    await semReticencias(page);
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
    const { ctx, page } = await bancos({ limite, conexoes, bancos: "padrao" });
    await (conexoes?.length === 0 ? page.locator(".empty img").waitFor({ timeout: 8000 }) : esperaCards(page, 3));
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
    const r = { nomes: await page.locator(".banco-card h2").allTextContents(), totais: await totais(page), topo: await page.locator(".bancos-topo").count(), barra: await page.locator(".comp-bar").count() };
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
  const r = { nomes: await page.locator(".banco-card h2").allTextContents(), selo: await page.locator(".banco-card").first().locator(".selo").innerText(), card: await page.locator(".banco-card").first().innerText(), tempos: await page.locator(".banco-card").first().locator("time").count() };
  await ctx.close();
  assert.deepEqual(r.nomes, ["Banco", "Inter"]);
  assert.equal(r.selo, "Pendente");
  assert.ok(!r.card.includes("object"), r.card);
  assert.equal(r.tempos, 0);
  assert.deepEqual(erros, []);
});

test("/limite com pode_adicionar false e message só de espaços: nem mensagem nem link (como message nula); com texto mostra a mensagem", async () => {
  const lido = async (message) => {
    const { ctx, page } = await bancos({ limite: LIMITE({ pode_adicionar: false, message }), bancos: "padrao" });
    await esperaCards(page, 3);
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
