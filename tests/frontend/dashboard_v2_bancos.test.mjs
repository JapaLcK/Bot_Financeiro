// Bancos conectados no /painel (#/conta/bancos, conta/Bancos.tsx, conta/BancoCard.tsx, lib/bancos.ts): a leitura. A desconexão mora em dashboard_v2_bancos_desconectar*.test.mjs. Plano:
// docs/plano-bancos-conectados-painel.md, W1. Três leituras: o snapshot do Open Finance (`/open-finance/{uid}`, o estado de cada
// conexão), `/api/v2/bancos` (os valores) e `/open-finance/{uid}/limite` (as vagas); só o snapshot é obrigatório.
//   · a lista é um grupo só, sem valor (sem topo, total, barra): os números estão no detalhe, onde a junção é por `conexao_id`, nunca por nome, e null é "—", nunca "R$ 0,00";
//   · "Disponíveis para conectar" (/connectors): populares em ordem fixa, no máximo 5, fora os já conectados POR ID; o "+" abre o seletor (o módulo legado, em stub);
//   · o `uid` vem só da sessão; perfil sem `user_id` válido não pede nada de /open-finance.
import { test } from "node:test";
import assert from "node:assert/strict";
import { RESPOSTAS } from "./_painel.mjs";
import { abrir, navegador } from "./_guia.mjs";
import { CDN_OK, ERRO_404, ERRO_500, LIMITE, LOGO, PADRAO, con, maisExato, regiao, semReticencias, servidorBancos, totalDe } from "./_bancos.mjs";

const browser = navegador();
void browser;

async function bancos(opts = {}, tela = {}) {
  const s = servidorBancos(opts);
  const p = await abrir({ guia: "concluido", rota: "/conta/bancos", antes: s.antes, ...tela });
  return { ...p, srv: s }; // `s` é o do guia (_guia.mjs); o do servidor de bancos é `srv`
}
const esperaCards = (page, n) => page.waitForFunction((n) => document.querySelectorAll(".banco-card").length === n, n, { timeout: 8000 });
// O /limite chegou: a linha de vagas do LIMITE() padrão está na tela. A lista vem antes dele; o que depende das vagas espera isto.
const esperaLimite = (page) => page.getByText("2 de 3 bancos").waitFor({ timeout: 8000 });
const corpo = (page) => page.locator("body").innerText();
// O detalhe de um banco (dialog modal): abre pela linha `n` e devolve o locator dele; `fechaDetalhe` usa o X.
const detalhe = async (page, n = 0) => { await page.locator(".banco-linha").nth(n).click(); const d = page.locator("dialog.banco-det[open]"); await d.waitFor(); return d; };
const fechaDetalhe = async (page) => { await page.locator("dialog.banco-det[open]").getByRole("button", { name: "Fechar" }).click(); await page.locator("dialog.banco-det[open]").waitFor({ state: "detached" }); };

test("a lista não traz valor (sem topo, total nem barra, nenhum 'R$'); o total do servidor está no detalhe: snapshot só com o banco 11 mostra R$ 45.000,00, nunca os R$ 67.000,00 do topo antigo", async () => {
  const { ctx, page } = await bancos({ conexoes: [con(11, "Nubank")], bancos: "padrao" });
  await esperaCards(page, 1);
  await semReticencias(page);
  const r = { lixo: await page.locator(".bancos-topo, .bancos-total, .banco-total, .comp-bar, .banco-barra").count(), tela: await page.locator("#main").innerText() };
  const total = await totalDe(page);
  await ctx.close();
  assert.equal(r.lixo, 0);
  assert.ok(!r.tela.includes("R$") && !r.tela.includes("67.000"), r.tela);
  assert.equal(total, "R$ 45.000,00");
});

test("dois bancos com o mesmo nome: o detalhe de cada um tem o valor da SUA conexão (junção por id, na ordem do snapshot)", async () => {
  const { ctx, page } = await bancos({ conexoes: [con(2, "Nubank"), con(1, "Nubank")], bancos: "mesmo_nome" });
  await esperaCards(page, 2);
  await semReticencias(page);
  const totais = [await totalDe(page, 0), await totalDe(page, 1)];
  await ctx.close();
  assert.deepEqual(totais, ["R$ 200,00", "R$ 100,00"]);
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

test("leituras opcionais: /bancos 500 e 404 deixam as linhas com nome e estado e o detalhe com '—'; /limite 500 tira só as vagas", async () => {
  for (const [rotulo, bancosRes] of [["500", ERRO_500], ["404", ERRO_404]]) {
    const { ctx, page } = await bancos({ bancosRes });
    await esperaCards(page, 3);
    await esperaLimite(page);
    const r = {
      nomes: await page.locator(".banco-nome").allTextContents(), estados: await page.locator(".banco-card .banco-estado").allTextContents(),
      vagas: await corpo(page).then((t) => /\d+ de \d+ bancos/.test(t)), total: await totalDe(page), // 500: 3 tentativas, ~3 s (o detalhe espera sair do "…")
    };
    await ctx.close();
    assert.deepEqual(r.nomes.slice(0, 3), ["Nubank", "Inter", "Itaú"], rotulo);
    assert.deepEqual([r.estados, r.vagas, r.total], [["Atualizado", "Atualizado", "Pausado"], true, "—"], rotulo);
  }
  const { ctx, page } = await bancos({ limiteRes: ERRO_500, bancos: "padrao" });
  await esperaCards(page, 3);
  await semReticencias(page);
  await page.waitForTimeout(3500); // as tentativas do /limite
  const r = { vagas: /\d+ de \d+ bancos/.test(await corpo(page)), total: await totalDe(page), conectar: await maisExato(page).count() };
  await ctx.close();
  assert.deepEqual(r, { vagas: false, total: "R$ 45.000,00", conectar: 1 });
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
    await page.locator(".bancos-conectar, a.banco-mais").first().waitFor({ timeout: 8000 });
    const r = { vagas: (await corpo(page)).match(/\d+ de \d+ bancos/)?.[0] ?? null, hrefs: await maisExato(page).evaluateAll((l) => l.map((a) => a.getAttribute("href"))), corpo: await corpo(page) };
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
  const r = { src: await page.locator(".empty img").getAttribute("src"), texto: await page.locator(".empty").innerText(), href: await maisExato(page).getAttribute("href"), topo: await page.locator(".bancos-topo").count(), cards: await page.locator(".banco-card").count() };
  await ctx.close();
  assert.match(r.src, /brand\/icon\.png$/);
  assert.match(r.texto, /Nenhum banco conectado\./);
  assert.deepEqual([r.href, r.topo, r.cards], ["/settings?view=open-finance", 0, 0]);
});

test("nenhum banco: 'Conectar banco' tem 44 px em 390×844 e em 1280×800, sem rolagem lateral (só o estado vazio; na lista com bancos o '+' é o `.banco-mais`)", async () => {
  for (const [width, height] of [[390, 844], [1280, 800]]) {
    const { ctx, page } = await bancos({ conexoes: [], bancos: "vazio" }, { width, height });
    await page.locator(".empty img").waitFor({ timeout: 8000 });
    await esperaLimite(page);
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
  const cores = await page.locator(".banco-card .banco-ini").evaluateAll((l) => l.map((e) => getComputedStyle(e).backgroundColor));
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

test("rota e isolamento: lê /open-finance/7, /limite e /connectors (nunca /api/v2/open-finance); perfil sem user_id válido não pede nada de /open-finance e mostra o erro", async () => {
  const { srv, ctx, page } = await bancos({ bancos: "padrao" });
  await esperaCards(page, 3);
  await regiao(page).waitFor({ timeout: 8000 }); // o /connectors só é pedido depois do /limite
  const pedidos = [...new Set(srv.reqs)].sort();
  await ctx.close();
  assert.deepEqual(pedidos, ["/api/v2/bancos", "/auth/dashboard-profile", "/open-finance-connect.css", "/open-finance-connect.js", "/open-finance/7", "/open-finance/7/connectors", "/open-finance/7/limite"]); // o painel carrega o seletor (css e js) e nada mais de Open Finance
  for (const perfil of [{ display_name: "Ana", email: "a@b.c", whatsapp_linked: false }, { display_name: "Ana", email: "a@b.c", user_id: 0, whatsapp_linked: false }, { display_name: "Ana", email: "a@b.c", user_id: "7/../8", whatsapp_linked: false }]) {
    const o = await bancos({ perfil, bancos: "padrao" });
    await o.page.getByRole("alert").waitFor({ timeout: 8000 });
    await o.page.getByRole("button", { name: "Tentar de novo" }).click(); // o "de novo" também não inventa um uid
    await o.page.waitForTimeout(300);
    const r = { of: o.srv.reqs.filter((p) => p.startsWith("/open-finance/")), texto: await o.page.getByRole("alert").innerText() };
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

test("decimal em notação científica não derruba a tela: '1E-7' vira R$ 0,0000001, '1000' R$ 1.000,00, '0' R$ 0,00 (nos detalhes)", async () => {
  const { ctx, page, erros } = await bancos({ conexoes: [con(11, "Nubank"), con(12, "Inter")], bancos: "cientifico" });
  await esperaCards(page, 2);
  await semReticencias(page);
  let t = await corpo(page);
  for (const n of [0, 1]) { const d = await detalhe(page, n); t += await d.innerText(); await fechaDetalhe(page); } // o "R$ 0,00" é o investido "0" do 1º banco
  await ctx.close();
  for (const esperado of ["R$ 0,0000001", "R$ 1.000,00", "R$ 0,00"]) assert.ok(t.includes(esperado), `${esperado} em ${t}`);
  assert.deepEqual(erros, []);
});

test("layout: o grupo é uma coluna só em 1280×800 e em 390×844, sem rolagem lateral, o '+' do topo e a linha com 44 px; nome de 70 letras sem espaço a 320, 375 e 1280 também", async () => {
  const medidas = async (page) => (await esperaLimite(page), page.evaluate(() => ({ lateral: document.documentElement.scrollWidth - innerWidth, botao: Math.round(document.querySelector("a.banco-mais").getBoundingClientRect().height), linha: Math.round(document.querySelector(".banco-linha").getBoundingClientRect().height), lefts: [...document.querySelectorAll(".banco-card")].map((e) => Math.round(e.getBoundingClientRect().left)) })));
  for (const [width, height] of [[1280, 800], [390, 844]]) {
    const a = await bancos({ conexoes: [con(11, "Nubank"), con(12, "Inter")], bancos: "barra" }, { width, height });
    await esperaCards(a.page, 2);
    const m = await medidas(a.page);
    await a.ctx.close();
    assert.equal(m.lateral, 0, `${width}: rolagem lateral`);
    assert.equal(new Set(m.lefts).size, 1, `${width}: uma coluna`);
    assert.ok(m.botao >= 44 && m.linha >= 44, `${width}: botão com ${m.botao}px, linha com ${m.linha}px`);
  }
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
const dois = [con(11, "Nubank"), con(12, "Inter")];

test("releitura de /bancos que falha tira os números da tela (nunca deixa o valor velho do detalhe); idem /limite e as vagas", async () => {
  let falha = false;
  const { ctx, page } = await bancos({
    bancos: "padrao", bancosRes: (r) => (falha ? ERRO_500(r) : r.fulfill({ json: RESPOSTAS.bancos.padrao })),
    limiteRes: (r) => (falha ? ERRO_500(r) : r.fulfill({ json: LIMITE() })),
  });
  await esperaCards(page, 3);
  await semReticencias(page);
  await esperaLimite(page);
  const d = await detalhe(page, 0); // o detalhe fica aberto durante a releitura
  await page.waitForFunction(() => document.querySelector("dialog.banco-det[open] .banco-det-total")?.textContent !== "…", null, { timeout: 8000 });
  const antes = { total: await d.locator(".banco-det-total").innerText(), vagas: /2 de 3 bancos/.test(await corpo(page)) };
  falha = true;
  await refoco(page);
  await page.waitForFunction(() => document.querySelector("dialog.banco-det[open] .banco-det-total")?.textContent === "—" && !/\d+ de \d+ bancos/.test(document.body.innerText), null, { timeout: 12000 }); // 3 tentativas, ~3 s
  const depois = { cards: await page.locator(".banco-card").count(), corpo: await corpo(page) };
  await ctx.close();
  assert.deepEqual(antes, { total: "R$ 45.000,00", vagas: true });
  assert.equal(depois.cards, 3);
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
  const vagas = async (limite, chega = false) => {
    const { ctx, page } = await bancos({ limite, bancos: "padrao" });
    await esperaCards(page, 3);
    await semReticencias(page);
    if (chega) await esperaLimite(page);
    const t = await corpo(page);
    await ctx.close();
    return /\bde\b \d+ bancos|null|undefined/.test(t);
  };
  assert.equal(await vagas(LIMITE({ of_banks_max: 0, em_uso: 0 })), false);
  assert.equal(await vagas(LIMITE({ of_banks_max: 3, em_uso: null })), false);
  assert.equal(await vagas(LIMITE({ of_banks_max: 3, em_uso: "2" })), false);
  assert.equal(await vagas(LIMITE(), true), true); // controle positivo: com número a linha aparece
});

test("/limite com pode_adicionar false e message nula não oferece 'Conectar banco' (nem na lista vazia); com message mostra o texto", async () => {
  // O /limite fica preso até a lista estar na tela: conta o link com ele pendente e de novo depois que chega (a linha de vagas
  // é o sinal de que chegou). Sem a trava, a contagem caía antes ou depois do /limite conforme a máquina (flake do CI).
  const links = async (limite, conexoes) => {
    let soltar;
    const preso = new Promise((r) => { soltar = r; });
    const { ctx, page } = await bancos({ limite, conexoes, bancos: "padrao", limiteRes: async (r) => { await preso; await r.fulfill({ json: limite }); } });
    await (conexoes?.length === 0 ? page.locator(".empty img").waitFor({ timeout: 8000 }) : esperaCards(page, 3));
    const conta = () => maisExato(page).count();
    const pendente = await conta();
    soltar();
    await esperaLimite(page);
    const chegou = await conta();
    await ctx.close();
    return [pendente, chegou];
  };
  assert.deepEqual(await links(LIMITE({ pode_adicionar: false, message: null })), [0, 0]);
  assert.deepEqual(await links(LIMITE({ pode_adicionar: false, message: null }), []), [0, 0]);
  assert.deepEqual(await links(LIMITE({ pode_adicionar: true, message: null })), [0, 1]); // controle positivo
});

test("/limite 500: o 'Conectar banco' volta na 1ª falha, sem esperar as novas tentativas (3 pedidos, ~3 s)", async () => {
  const { ctx, page, srv } = await bancos({ limiteRes: ERRO_500, bancos: "padrao" });
  await esperaCards(page, 3);
  await maisExato(page).waitFor({ timeout: 8000 });
  const pedidos = srv.reqs.filter((p) => p === "/open-finance/7/limite").length;
  await ctx.close();
  assert.ok(pedidos < 3, `o link só veio depois de ${pedidos} pedidos do /limite`);
});

test("/bancos fora do contrato (sem 'bancos', sem 'motivos', banco sem 'motivos', item null em 'bancos') é leitura falha: linhas com nome e o detalhe com '—', sem erro de página", async () => {
  for (const corpoRuim of [{ total: "10.00", motivos: [] }, { total: "10.00", bancos: [] }, { total: "10.00", motivos: [], bancos: [{ conexao_id: 11, total: "10.00" }] }, { total: "10.00", motivos: [], bancos: [null] }, []]) {
    const { ctx, page, erros } = await bancos({ conexoes: dois, bancos: corpoRuim });
    await esperaCards(page, 2);
    const r = { nomes: await page.locator(".banco-card .banco-nome").allTextContents(), total: await totalDe(page), lixo: await page.locator(".bancos-topo, .comp-bar").count() };
    await ctx.close();
    assert.deepEqual(r, { nomes: ["Nubank", "Inter"], total: "—", lixo: 0 }, JSON.stringify(corpoRuim));
    assert.deepEqual(erros, [], JSON.stringify(corpoRuim));
  }
});

// ---- Consertos dos achados do Tester (rodada 2) ----
test("motivos que não são texto ({'x':1}), no topo ou no banco, tiram os números do detalhe e não derrubam a SPA", async () => {
  const objeto = { ...corpoBancos("67000.00", [[11, "45000.00"], [12, "22000.00"]]), motivos: [{ x: 1 }] };
  const noBanco = corpoBancos("67000.00", [[11, "45000.00"], [12, "22000.00"]]);
  noBanco.bancos[0].motivos = [{ x: 1 }];
  for (const corpoRuim of [objeto, noBanco]) {
    const { ctx, page, erros } = await bancos({ conexoes: dois, bancos: corpoRuim });
    await esperaCards(page, 2);
    const r = { total: await totalDe(page), lixo: await page.locator(".bancos-topo, .comp-bar").count() };
    await ctx.close();
    assert.deepEqual(r, { total: "—", lixo: 0 });
    assert.deepEqual(erros, []);
  }
});

test("snapshot fora do contrato não derruba a SPA: institution_name número, ui.label/ui.detail objeto, last_sync_at número viram nulo (nome 'Banco', 'Pendente', sem detalhe, sem <time>)", async () => {
  const feia = con(11, 5, "updated", { a: 1 }, 5, { b: 2 });
  const { ctx, page, erros } = await bancos({ conexoes: [feia, con(12, "Inter")], bancos: "barra" });
  await esperaCards(page, 2);
  const d = await detalhe(page, 0);
  const r = { nomes: await page.locator(".banco-card .banco-nome").allTextContents(), selo: await page.locator(".banco-card").first().locator(".banco-estado").innerText(), card: await page.locator(".banco-card").first().innerText() + await d.innerText(), tempos: await d.locator("time").count() };
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
    await esperaLimite(page);
    const r = { area: await page.locator(".bancos-conectar").count(), links: await maisExato(page).count() };
    await ctx.close();
    return r;
  };
  assert.deepEqual(await lido("   "), { area: 0, links: 0 });
  assert.deepEqual(await lido("Plano cheio."), { area: 1, links: 0 }); // controle positivo
});

test("total não finito ('1E999' com duas partes de '5E998'): nunca 'NaN' na tela nem no detalhe; sem erro de página", async () => {
  const { ctx, page, erros } = await bancos({ conexoes: dois, bancos: corpoBancos("1E999", [[11, "5E998"], [12, "5E998"]]) });
  await esperaCards(page, 2);
  await semReticencias(page);
  const d = await detalhe(page, 0);
  const t = await corpo(page) + await d.innerText();
  await ctx.close();
  assert.ok(!t.includes("NaN"), t);
  assert.deepEqual(erros, []);
});

// ---- Codex no #1116 ----
test("releitura do snapshot que falha (500) depois de a lista estar na tela: o erro com 'Tentar de novo', nunca a lista velha; o botão recupera", async () => {
  let falha = false;
  const { ctx, page } = await bancos({ snap: (r) => (falha ? ERRO_500(r) : r.fulfill({ json: { ok: true, connections: PADRAO() } })), bancos: "padrao" });
  await esperaCards(page, 3);
  falha = true;
  await refoco(page);
  await page.getByRole("alert").waitFor({ timeout: 12000 }); // 3 tentativas, ~3 s
  const r = { texto: await page.getByRole("alert").innerText(), cards: await page.locator(".banco-card").count() };
  falha = false;
  await page.getByRole("button", { name: "Tentar de novo" }).click();
  await esperaCards(page, 3);
  await ctx.close();
  assert.match(r.texto, /Não deu para carregar seus bancos\./);
  assert.equal(r.cards, 0);
});

test("troca de conta em outra aba: com o /bancos da conta nova ainda no ar, o total da conta antiga nunca aparece (nem no detalhe) ao lado da lista nova", async () => {
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
  await semReticencias(page);
  uid = 8;
  await refoco(page);
  await page.waitForFunction(() => document.querySelectorAll(".banco-card").length === 1, null, { timeout: 8000 });
  const d = await detalhe(page, 0);
  const r = { corpo: await corpo(page), total: await d.locator(".banco-det-total").innerText() };
  solta();
  await ctx.close();
  assert.match(r.corpo, /C6 Bank/);
  assert.ok(!r.corpo.includes("67.000,00") && !r.corpo.includes("45.000,00"), r.corpo);
  assert.equal(r.total, "…");
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
  await semReticencias(page);
  await detalhe(page, 0); // o total aparece no detalhe: a amostra abaixo o vê junto do nome do banco antigo
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

test("rede caída em todas as leituras e 'Tentar de novo': volta a lista E os números (detalhe e vagas), não só a lista", async () => {
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
  await esperaLimite(page);
  const r = { total: await totalDe(page), vagas: /2 de 3 bancos/.test(await corpo(page)) };
  await ctx.close();
  assert.deepEqual(r, { total: "R$ 45.000,00", vagas: true });
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
  await semReticencias(page);
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
const comLogo = (opts = {}) => bancos({ conexoes: [con(11, "Itaú Unibanco", "updated", "Atualizado", undefined, null, LOGO), con(12, "Inter")], bancos: "padrao", conectores: [], ...opts }); // sem catálogo: os logos dos Disponíveis não entram na conta de `srv.cdn`
const ini = (page, n) => page.locator(".banco-card").nth(n).locator(".banco-ini");
const bg = (loc) => loc.evaluate((e) => getComputedStyle(e).backgroundColor);

test("logo: com logo_url o ladrilho traz a imagem (alt vazio, src do servidor, carregada), sem a letra, em fundo claro neutro; o banco sem logo segue com letra e cor", async () => {
  const { ctx, page, srv, erros } = await comLogo();
  await esperaCards(page, 2);
  await page.waitForFunction(() => { const i = document.querySelector(".banco-ini img"); return i && i.complete && i.naturalWidth > 0; }, null, { timeout: 8000 });
  const img = ini(page, 0).locator("img");
  const r = {
    alt: await img.getAttribute("alt"), src: await img.getAttribute("src"), referrer: await img.getAttribute("referrerpolicy"), imgs: await page.locator(".banco-card .banco-ini img").count(),
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
  const r = { imgs: await page.locator(".banco-card .banco-ini img").count(), letra: await ini(page, 0).innerText(), fundo: await bg(ini(page, 0)), classe: await ini(page, 0).getAttribute("class") };
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
  const { ctx, page, srv, erros } = await bancos({ conexoes, bancos: "padrao", conectores: [] });
  await esperaCards(page, ruins.length);
  await page.waitForTimeout(300); // uma imagem indevida teria sido pedida a esta altura
  const r = { imgs: await page.locator(".banco-card .banco-ini img").count(), letras: await page.locator(".banco-card .banco-ini").allInnerTexts() };
  await ctx.close();
  assert.deepEqual({ ...r, cdn: srv.cdn }, { imgs: 0, letras: ruins.map(() => "A"), cdn: [] });
  assert.deepEqual(erros, []);
});

test("logo: nenhum texto novo (o nome é o mesmo; o resto da linha é idêntico ao do banco sem logo) e o ladrilho segue aria-hidden", async () => {
  const { ctx, page } = await bancos({ conexoes: [con(11, "Alfa", "updated", "Atualizado", undefined, null, LOGO), con(11, "Alfa")], bancos: "padrao" });
  await esperaCards(page, 2);
  await semReticencias(page);
  const lado = (n) => page.locator(".banco-card").nth(n).evaluate((c) => c.querySelector(".banco-id").innerText);
  const r = { a: await lado(0), b: await lado(1), nomes: await page.locator(".banco-card .banco-nome").allInnerTexts(), aria: await page.locator(".banco-card .banco-ini").evaluateAll((l) => l.map((e) => e.getAttribute("aria-hidden"))) };
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

test("a linha é um <button> de 44 px+ com o chevron; sem <details>/<summary> nem 'Gerenciar' na página; a linha só tem ladrilho, nome e estado (sem valor nem barra)", async () => {
  const { ctx, page } = await bancos({ conexoes: dois_, bancos: "barra" });
  await esperaCards(page, 2);
  await semReticencias(page);
  const r = await page.evaluate(() => {
    const b = document.querySelector(".banco-linha");
    return { tag: b.tagName, tipo: b.getAttribute("type"), alto: Math.round(b.getBoundingClientRect().height), chevron: !!b.querySelector(".ph-caret-right[aria-hidden=true]"), details: document.querySelectorAll("details, summary").length,
      gerenciar: document.body.innerText.includes("Gerenciar"), texto: b.innerText.split("\n").map((t) => t.trim()).filter(Boolean), barra: b.querySelector(".comp-bar, .banco-barra, .banco-total") };
  });
  await ctx.close();
  assert.deepEqual([r.tag, r.tipo, r.chevron, r.details, r.gerenciar], ["BUTTON", "button", true, 0, false]);
  assert.ok(r.alto >= 44, `linha com ${r.alto}px`);
  assert.deepEqual(r.texto, ["N", "Nubank", "Atualizado"]); // a inicial do ladrilho, o nome e o estado; sem valor, "há N min", contagens, barra nem ui.detail
  assert.equal(r.barra, null);
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
  await esperaLimite(b.page);
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
test("os pontinhos de 'Em conta' e 'Investido' no detalhe diferem entre si, não usam o rosa e passam de 3:1 contra o fundo do detalhe", async () => {
  const { ctx, page } = await bancos({ conexoes: dois_, bancos: "barra" }, { width: 1280, height: 800 });
  await esperaCards(page, 2);
  await semReticencias(page);
  const d = await detalhe(page, 0);
  const marcas = await d.locator(".banco-det-dados .marca").evaluateAll((l) => l.map((x) => [x.closest("dt").textContent, getComputedStyle(x).backgroundColor, x.getAttribute("aria-hidden")]));
  const fundo = await d.evaluate((e) => getComputedStyle(e).backgroundColor);
  await ctx.close();
  const lum = (c) => { const [r, g, b] = c.match(/\d+/g).slice(0, 3).map((v) => { const x = v / 255; return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4; }); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
  const razao = (a, b) => (Math.max(lum(a), lum(b)) + 0.05) / (Math.min(lum(a), lum(b)) + 0.05);
  assert.deepEqual(marcas.map((m) => [m[0], m[2]]), [["Em conta", "true"], ["Investido", "true"]]);
  const cores = marcas.map((m) => m[1]);
  assert.ok(!cores.includes("rgb(255, 45, 142)"), cores.join()); // --pink
  assert.notEqual(cores[0], cores[1]);
  for (const c of cores) assert.ok(razao(c, fundo) >= 3, `${c} contra ${fundo}`);
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
