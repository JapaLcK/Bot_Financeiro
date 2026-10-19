// "Disponíveis para conectar" na tela Bancos conectados do /painel (conta/Disponiveis.tsx, lib/bancos.ts::disponiveis) e o seletor de bancos (o módulo legado
// /open-finance-connect.js, aqui em STUB: `window.__of` registra init/open/destroy). Plano: docs/plano-bancos-conectados-painel.md.
//   · "já conectado" é pelo ID do conector (institution_id do snapshot × id do /connectors), nunca pelo nome;
//   · os populares em ordem fixa, no máximo 5, mais "Ver todos os bancos"; a seção só existe com vaga (`pode`), e falha do /connectors a esconde sozinha;
//   · o "Conectar" de cada um abre o seletor com `open({id, name})`; sem o script, o href do /settings segue valendo.
import { test } from "node:test";
import assert from "node:assert/strict";
import { abrir, navegador } from "./_guia.mjs";
import { CONECTORES, ERRO_500, LIMITE, LOGO, abreDet, con, disponiveisDe, maisExato, regiao, servidorBancos } from "./_bancos.mjs";

const browser = navegador();
void browser;

async function bancos(opts = {}, tela = {}) {
  const s = servidorBancos(opts);
  const p = await abrir({ guia: "concluido", rota: "/conta/bancos", antes: s.antes, ...tela });
  return { ...p, srv: s }; // `s` é o do guia (_guia.mjs); o do servidor de bancos é `srv`
}
const esperaCards = (page, n) => page.waitForFunction((n) => document.querySelectorAll(".banco-card").length === n, n, { timeout: 8000 });
const esperaLimite = (page) => page.getByText("2 de 3 bancos").waitFor({ timeout: 8000 });
const corpo = (page) => page.locator("body").innerText();
const of = (page) => page.evaluate(() => ({ inits: window.__of.inits, opens: window.__of.opens, destroys: window.__of.destroys }));
const esperaRegiao = (page) => regiao(page).waitFor({ timeout: 8000 });
const gemeo = () => ({ ...con(13, "Itaú"), institution_id: "999" }); // o "Itaú" conectado por outro conector (o direto × o Open Finance): mesmo nome, id diferente

test("Disponíveis: 'já conectado' é pelo ID do conector (institution_id), nunca pelo nome: o Itaú de id 201 conectado sai; o de mesmo nome e id 999 deixa o Itaú disponível", async () => {
  const por = async (conexoes) => {
    const { ctx, page } = await bancos({ conexoes, bancos: "padrao" });
    await esperaCards(page, 3);
    await esperaRegiao(page);
    const r = await disponiveisDe(page);
    await ctx.close();
    return r;
  };
  // Nubank (208) e Inter (206) conectados em ambos; o Itaú muda de id.
  assert.deepEqual(await por([con(11, "Nubank"), con(12, "Inter"), con(13, "Itaú")]), ["Bradesco", "Banco do Brasil", "Santander", "Caixa", "C6 Bank"]); // positivo: o 201 conectado sai
  assert.deepEqual(await por([con(11, "Nubank"), con(12, "Inter"), gemeo()]), ["Itaú", "Bradesco", "Banco do Brasil", "Santander", "Caixa"]); // o de id 999 NÃO tira o Itaú 201
});

test("Disponíveis: os populares na ordem fixa (não a do servidor), no máximo 5, sem os não populares, e a última linha 'Ver todos os bancos' abre o seletor inteiro", async () => {
  const extra = [{ id: 300, name: "Banco XYZ", type: "PERSONAL_BANK", color: "123456", logo: "", inv: false }, { id: 209, name: "BTG Pactual", type: "PERSONAL_BANK", color: "001E62", logo: "", inv: true }, { id: 210, name: "Mercado Pago", type: "PERSONAL_BANK", color: "009EE3", logo: "", inv: false }];
  const conectores = [...CONECTORES, ...extra].reverse(); // a ordem do servidor é outra
  const { ctx, page, erros } = await bancos({ conexoes: [con(11, "Nubank")], conectores, bancos: "padrao" });
  await esperaCards(page, 1);
  await esperaRegiao(page);
  const itens = await regiao(page).locator("li").count();
  const r = { nomes: await disponiveisDe(page), ultima: await regiao(page).locator("li").last().innerText(), subtexto: await regiao(page).locator(".banco-sub").allTextContents(), rotulo: await regiao(page).getByRole("heading").innerText() };
  await regiao(page).getByRole("link", { name: "Ver todos os bancos" }).click();
  const abertura = await of(page);
  const aberto = await page.evaluate(() => window.__of.opens);
  await ctx.close();
  assert.deepEqual(r.nomes, ["Itaú", "Bradesco", "Banco do Brasil", "Santander", "Caixa"]); // Inter, C6, BTG e Mercado Pago ficaram de fora pelo teto de 5; Banco XYZ nunca entra
  assert.equal(itens, 6); // 5 + o "Ver todos"
  assert.deepEqual([r.ultima, r.rotulo], ["Ver todos os bancos", "Disponíveis para conectar"]);
  assert.deepEqual(r.subtexto, Array(5).fill("Conecte sua conta e tenha uma visão completa."));
  assert.deepEqual(aberto, [null]); // sem pré-seleção
  assert.equal(abertura.inits, 1);
  assert.deepEqual(erros, []);
});

test("Disponíveis: com a lista toda conectada nos populares, a seção (e o 'Ver todos') some junto; sem conector nenhum também", async () => {
  const tudo = ["Nubank", "Inter", "Itaú", "Caixa", "Santander", "Bradesco", "Banco do Brasil", "C6 Bank"].map((n, i) => con(11 + i, n)); // os 8 populares do catálogo, cada um com o id do conector
  const a = await bancos({ conexoes: tudo, bancos: "padrao" });
  await esperaCards(a.page, 8);
  await esperaLimite(a.page); // 8 bancos em 3 vagas não é um caso real: só importa a seção
  await a.page.waitForTimeout(800);
  const semLista = await regiao(a.page).count();
  await a.ctx.close();
  const b = await bancos({ conectores: [], bancos: "padrao" });
  await esperaCards(b.page, 3);
  await esperaLimite(b.page);
  await b.page.waitForTimeout(800);
  const semCatalogo = { regiao: await regiao(b.page).count(), todos: await b.page.getByRole("link", { name: "Ver todos os bancos" }).count() };
  await b.ctx.close();
  assert.equal(semLista, 0);
  assert.deepEqual(semCatalogo, { regiao: 0, todos: 0 });
});

test("o 'Conectar' de um disponível chama open({id, name}) do seletor; o do resumo e o 'Ver todos' chamam open() sem argumento; sem o script o href do /settings segue valendo", async () => {
  const a = await bancos({ conexoes: [con(11, "Nubank")], bancos: "padrao" });
  await esperaCards(a.page, 1);
  await esperaRegiao(a.page);
  const mais = a.page.getByRole("link", { name: "Conectar Bradesco", exact: true });
  const href = await mais.getAttribute("href");
  const caixa = await mais.evaluate((e) => [Math.round(e.getBoundingClientRect().width), Math.round(e.getBoundingClientRect().height)]);
  await mais.click();
  await maisExato(a.page).click();
  const url = a.page.url();
  const r = await of(a.page);
  await a.ctx.close();
  assert.equal(href, "/settings?view=open-finance");
  assert.ok(caixa[0] >= 44 && caixa[1] === 44, `botão 'Conectar' com ${caixa}`); // o botão rosa com texto: 44 de altura, largura do conteúdo
  assert.deepEqual(r.opens, [{ id: 202, name: "Bradesco" }, null]);
  assert.ok(url.endsWith("/painel.html#/conta/bancos"), url); // o clique foi do seletor: a página não navegou
  // Sem o script do seletor: o link navega.
  const b = await bancos({ conexoes: [con(11, "Nubank")], bancos: "padrao", semSeletor: true });
  await esperaCards(b.page, 1);
  await esperaRegiao(b.page);
  await Promise.all([b.page.waitForURL(/\/settings\?view=open-finance$/), b.page.getByRole("link", { name: "Conectar Bradesco", exact: true }).click()]);
  await b.ctx.close();
});

test("o seletor nasce com o uid da sessão ao montar e é destruído ao sair da página; onConnected relê snapshot, /limite, /bancos e /connectors (o banco novo sai dos disponíveis); o aviso do seletor aparece na tela", async () => {
  const { ctx, page, srv } = await bancos({ conexoes: [con(11, "Nubank")], bancos: "padrao" });
  await esperaCards(page, 1);
  await esperaRegiao(page);
  const inicio = await page.evaluate(() => ({ inits: window.__of.inits, cfg: { apiBase: window.__of.cb.apiBase, userId: window.__of.cb.userId } }));
  const antes = await disponiveisDe(page);
  srv.conexoes = [con(11, "Nubank"), con(14, "Santander")]; // o 204 acabou de ser conectado no seletor
  const nReqs = srv.reqs.length;
  await page.evaluate(() => window.__of.cb.onConnected({}));
  await esperaCards(page, 2);
  const depois = await disponiveisDe(page);
  const relidas = [...new Set(srv.reqs.slice(nReqs))].sort();
  await page.evaluate(() => window.__of.cb.notify("Seu plano conecta até 3 bancos. Faça upgrade pra conectar mais: /precos", "error"));
  const alerta = await page.getByRole("alert").innerText();
  await page.evaluate(() => window.__of.cb.onConnectStart());
  const sumiu = await page.getByRole("alert").count();
  await page.evaluate(() => { location.hash = "#/"; });
  await page.waitForFunction(() => window.__of.destroys >= 1, null, { timeout: 8000 });
  await ctx.close();
  assert.deepEqual(inicio, { inits: 1, cfg: { apiBase: "http://127.0.0.1:1", userId: 7 } });
  assert.ok(antes.includes("Santander") && !depois.includes("Santander"), `${antes} → ${depois}`);
  for (const rota of ["/api/v2/bancos", "/open-finance/7", "/open-finance/7/connectors", "/open-finance/7/limite"]) assert.ok(relidas.includes(rota), `${rota} em ${relidas}`);
  assert.match(alerta, /Seu plano conecta até 3 bancos/);
  assert.equal(sumiu, 0);
});

test("/connectors que falha (500, 403, corpo fora do contrato) esconde só a seção: as linhas, o '+' e as vagas seguem, sem alerta nem erro de página", async () => {
  const ruins = [["500", ERRO_500], ["403", (r) => r.fulfill({ status: 403, json: { detail: "Acesso negado." } })], ["corpo", (r) => r.fulfill({ json: { ok: true, connectors: "nada" } })]];
  for (const [rotulo, conectoresRes] of ruins) {
    const { ctx, page, srv, erros } = await bancos({ conectoresRes, bancos: "padrao" });
    await esperaCards(page, 3);
    await esperaLimite(page);
    await page.waitForTimeout(3500); // as tentativas do 500
    const r = { pedido: srv.reqs.includes("/open-finance/7/connectors"), regiao: await regiao(page).count(), cards: await page.locator(".banco-card").count(), mais: await maisExato(page).count(), vagas: /2 de 3 bancos/.test(await corpo(page)), alertas: await page.getByRole("alert").count() };
    await ctx.close();
    assert.deepEqual(r, { pedido: true, regiao: 0, cards: 3, mais: 1, vagas: true, alertas: 0 }, rotulo);
    assert.deepEqual(erros, [], rotulo);
  }
});

test("/limite pendente ou sem vaga: nem o '+' nem os Disponíveis (e nenhum /connectors pedido); com vaga, os dois chegam", async () => {
  const estados = async (limite) => {
    let soltar;
    const preso = new Promise((r) => { soltar = r; });
    const { ctx, page, srv } = await bancos({ limite, bancos: "padrao", limiteRes: async (r) => { await preso; await r.fulfill({ json: limite }); } });
    await esperaCards(page, 3);
    await page.waitForTimeout(400);
    const pendente = { mais: await maisExato(page).count(), regiao: await regiao(page).count(), pedido: srv.reqs.includes("/open-finance/7/connectors") };
    soltar();
    await esperaLimite(page);
    if (limite.pode_adicionar) await esperaRegiao(page); else await page.waitForTimeout(800);
    const chegou = { mais: await maisExato(page).count(), regiao: await regiao(page).count(), pedido: srv.reqs.includes("/open-finance/7/connectors"), aviso: (await corpo(page)).includes("Seu plano permite 3 bancos.") };
    await ctx.close();
    return [pendente, chegou];
  };
  const sem = { mais: 0, regiao: 0, pedido: false };
  assert.deepEqual(await estados(LIMITE({ pode_adicionar: false, message: "Seu plano permite 3 bancos." })), [sem, { ...sem, aviso: true }]);
  assert.deepEqual(await estados(LIMITE()), [sem, { mais: 1, regiao: 1, pedido: true, aviso: false }]); // controle positivo
});

test("Disponíveis sem vaga (pode_adicionar false): nenhuma seção e a mensagem do servidor no lugar; o estado vazio também", async () => {
  const limite = LIMITE({ pode_adicionar: false, message: "Seu plano permite 3 bancos." });
  const { ctx, page } = await bancos({ conexoes: [], limite, bancos: "vazio" });
  await page.locator(".empty img").waitFor({ timeout: 8000 });
  await page.getByText("Seu plano permite 3 bancos.").waitFor({ timeout: 8000 });
  await page.waitForTimeout(800);
  const r = { regiao: await regiao(page).count(), links: await page.getByRole("link", { name: /^Conectar / }).count() };
  await ctx.close();
  assert.deepEqual(r, { regiao: 0, links: 0 });
});

test("a página é uma coluna de 4 cards (resumo, Meus bancos, Disponíveis, segurança) da mesma borda e largura, superfície do .panel e canto de 16 px, com divisor fino entre as linhas, sem valor nem barra, alvos de 44 px e sem rolagem lateral em 390×844 e 1280×800", async () => {
  for (const [width, height] of [[390, 844], [1280, 800]]) {
    const { ctx, page } = await bancos({ bancos: "padrao" }, { width, height });
    await esperaCards(page, 3);
    await esperaRegiao(page);
    const r = await page.evaluate(() => {
      const cards = [...document.querySelectorAll(".bancos-pag > section.panel")];
      const caixa = (e) => { const b = e.getBoundingClientRect(); return [Math.round(b.width), Math.round(b.height)]; };
      const listas = [...document.querySelectorAll(".banco-lista")];
      return {
        cards: cards.map((c) => c.className.replace(" panel", "").replace("panel ", "")), bordas: cards.map((c) => { const b = c.getBoundingClientRect(); return [Math.round(b.left), Math.round(b.width)]; }),
        fundo: cards.map((g) => getComputedStyle(g).backgroundColor), canto: cards.map((g) => getComputedStyle(g).borderTopLeftRadius), pagina: getComputedStyle(document.body).backgroundColor,
        aninhado: document.querySelectorAll(".banco-sec .panel, .banco-resumo .panel, .banco-seguro .panel").length,
        divisores: listas.map((g) => [...g.children].map((li) => parseFloat(getComputedStyle(li).borderTopWidth) > 0)), // a 1ª linha também: o divisor separa o cabeçalho do card
        lixo: document.querySelectorAll(".bancos-topo, .comp-bar, .banco-barra, .banco-total").length, dinheiro: document.querySelector("#main").innerText.includes("R$"),
        alvos: [...document.querySelectorAll(".banco-card button, .banco-item a, .banco-todos, .banco-resumo a, .banco-saiba")].map(caixa), lateral: document.documentElement.scrollWidth - innerWidth,
      };
    });
    await ctx.close();
    const rotulo = `${width}×${height}`;
    assert.deepEqual(r.cards, ["banco-resumo", "banco-sec", "banco-sec", "banco-seguro"], rotulo);
    assert.equal(new Set(r.bordas.map((b) => b.join())).size, 1, `${rotulo}: ${JSON.stringify(r.bordas)}`); // uma coluna: todos com a mesma borda esquerda e largura
    assert.ok(r.fundo.every((f) => f === "rgb(16, 16, 19)") && r.fundo[0] !== r.pagina, `${rotulo}: ${r.fundo} sobre ${r.pagina}`);
    assert.deepEqual(r.canto, Array(4).fill("16px"), rotulo);
    assert.equal(r.aninhado, 0, rotulo); // dentro de um card não há outro card
    assert.deepEqual(r.divisores, [[true, true, true], [true, true, true, true, true, true]], rotulo); // 3 bancos; 5 disponíveis + "Ver todos"
    assert.deepEqual([r.lixo, r.dinheiro, r.lateral], [0, false, 0], rotulo);
    assert.ok(r.alvos.length === 3 * 3 + 5 + 1 + 1 + 1 && r.alvos.every(([w, h]) => w >= 44 && h >= 44), `${rotulo}: ${JSON.stringify(r.alvos)}`); // 3×3 botões dos bancos, 5 "Conectar", "Ver todos", "Conectar banco" do resumo e "Saiba mais"
  }
});

test("ladrilho dos Disponíveis: logo do CDN quando há; senão a inicial na cor do conector (hex de 6 dígitos); cor inválida ou injeção cai no neutro; logo fora da regra vira inicial; item fora do contrato some", async () => {
  const c = (id, name, color, logo) => ({ id, name, type: "PERSONAL_BANK", color, logo, inv: false });
  const conectores = [
    c(201, "Itaú", "EC7000", LOGO), c(202, "Bradesco", "EC7000", ""), c(203, "Banco do Brasil", "zzzzzz", ""),
    c(204, "Santander", "red;background:url(https://evil.example/x)", "http://cdn.pluggy.ai/a.svg"), c(205, "Caixa", "0070AF", "https://evil.example/a.svg"),
    null, 5, { id: "206", name: "Inter" }, { id: 207, name: "   " }, // fora do contrato: somem
  ];
  const { ctx, page, srv, erros } = await bancos({ conexoes: [con(11, "Nubank")], conectores, bancos: "padrao" });
  await esperaCards(page, 1);
  await esperaRegiao(page);
  await page.waitForFunction(() => { const i = document.querySelector("section .banco-ini img"); return i && i.complete && i.naturalWidth > 0; }, null, { timeout: 8000 });
  const lado = await regiao(page).locator(".banco-ini").evaluateAll((l) => l.map((e) => [e.textContent, e.querySelector("img")?.getAttribute("src") ?? null, getComputedStyle(e).backgroundColor, e.getAttribute("style") ?? "", e.getAttribute("aria-hidden")]));
  const nomes = await disponiveisDe(page);
  await ctx.close();
  const tintas = ["rgb(244, 244, 246)", "rgb(168, 168, 179)", "rgb(139, 139, 150)"];
  assert.deepEqual(nomes, ["Itaú", "Bradesco", "Banco do Brasil", "Santander", "Caixa"]);
  assert.deepEqual([lado[0][0], lado[0][1], lado[0][4]], ["", LOGO, "true"]); // logo: sem a letra
  assert.deepEqual([lado[1][0], lado[1][2]], ["B", "rgb(236, 112, 0)"]); // inicial na cor do conector
  assert.deepEqual([lado[2][0], lado[2][1]], ["B", null]);
  assert.ok(tintas.includes(lado[2][2]), `cor inválida: ${lado[2][2]}`);
  assert.ok(tintas.includes(lado[3][2]) && !lado[3].join().includes("url(") && !lado[3].join().includes("evil"), `injeção: ${lado[3]}`);
  assert.deepEqual([lado[3][0], lado[3][1], lado[4][0], lado[4][1], lado[4][2]], ["S", null, "C", null, "rgb(0, 112, 175)"]); // logo fora da regra: a letra, e a imagem nem é pedida
  assert.deepEqual(srv.cdn, [LOGO]);
  assert.deepEqual(erros, []);
});

test("/connectors: id que não é inteiro (string '201', 203.5) está fora do contrato e some, mesmo dentro do teto de 5; o de id inteiro aparece (controle positivo)", async () => {
  const c = (id, name) => ({ id, name, type: "PERSONAL_BANK", color: "EC7000", logo: "", inv: false });
  const { ctx, page, erros } = await bancos({ conexoes: [con(11, "Nubank")], conectores: [c("201", "Itaú"), c(203.5, "Banco do Brasil"), c(202, "Bradesco")], bancos: "padrao" });
  await esperaCards(page, 1);
  await esperaRegiao(page);
  const nomes = await disponiveisDe(page);
  await ctx.close();
  assert.deepEqual(nomes, ["Bradesco"]);
  assert.deepEqual(erros, []);
});

test("onError do seletor mostra o aviso na tela: a mensagem do módulo, ou a padrão quando vem vazia", async () => {
  const { ctx, page } = await bancos({ conexoes: [con(11, "Nubank")], bancos: "padrao" });
  await esperaCards(page, 1);
  await page.evaluate(() => window.__of.cb.onError("A Pluggy recusou a conexão."));
  const com = await page.getByRole("alert").innerText();
  await page.evaluate(() => window.__of.cb.onConnectStart());
  const aposInicio = await page.getByRole("alert").count();
  await page.evaluate(() => window.__of.cb.onError(""));
  const vazia = await page.getByRole("alert").innerText();
  await ctx.close();
  assert.deepEqual([com, aposInicio, vazia], ["A Pluggy recusou a conexão.", 0, "Não foi possível conectar o banco."]);
});

test("Disponíveis esperam o snapshot: com o /limite já com vaga e o snapshot pendente, nada de /connectors nem de seção (senão o banco já conectado piscaria como disponível); chegado o snapshot, a seção vem sem ele", async () => {
  let solta;
  const gate = new Promise((ok) => { solta = ok; });
  const { ctx, page, srv } = await bancos({ conexoes: [con(11, "Nubank")], bancos: "padrao", snap: async (r) => { await gate; return r.fulfill({ json: { ok: true, connections: [con(11, "Nubank")] } }); } });
  await page.waitForFunction(() => window.__of?.inits >= 1, null, { timeout: 8000 });
  await page.waitForFunction(() => !!document.querySelector("#main"));
  await page.waitForTimeout(800); // folga para um /connectors indevido sair
  const pendente = { regiao: await regiao(page).count(), connectors: srv.reqs.includes("/open-finance/7/connectors") };
  solta();
  await esperaRegiao(page);
  const nomes = await disponiveisDe(page);
  await ctx.close();
  assert.deepEqual(pendente, { regiao: 0, connectors: false });
  assert.ok(!nomes.includes("Nubank") && nomes.length > 0, `${nomes}`);
});

test("o ladrilho do detalhe tem a cor do ladrilho da mesma linha na lista (3 bancos), e a lista tem cores diferentes entre si", async () => {
  const { ctx, page } = await bancos({ bancos: "padrao" });
  await esperaCards(page, 3);
  const lista = await page.locator(".banco-card .banco-ini").evaluateAll((l) => l.map((e) => getComputedStyle(e).backgroundColor));
  const detalhe = [];
  for (let n = 0; n < 3; n++) {
    const d = await abreDet(page, n);
    detalhe.push(await d.locator(".banco-ini").evaluate((e) => getComputedStyle(e).backgroundColor));
    await d.getByRole("button", { name: "Fechar" }).click();
    await d.waitFor({ state: "detached" });
  }
  await ctx.close();
  assert.deepEqual(detalhe, lista);
  assert.ok(new Set(lista).size >= 2, `${lista}`);
});

const maisDe = (page, nome) => page.getByRole("link", { name: `Conectar ${nome}`, exact: true });
test("depois de onConnected o foco vai ao título da página (o '+' que abriu o seletor pode ter saído da lista), e não fica no body", async () => {
  const { ctx, page, srv } = await bancos({ conexoes: [con(11, "Nubank")], bancos: "padrao" });
  await esperaCards(page, 1);
  await esperaRegiao(page);
  await maisDe(page, "Santander").focus();
  srv.conexoes = [con(11, "Nubank"), con(14, "Santander")];
  await page.evaluate(() => window.__of.cb.onConnected({}));
  await esperaCards(page, 2);
  const foco = await page.evaluate(() => document.activeElement?.id ?? document.activeElement?.tagName);
  await ctx.close();
  assert.equal(foco, "page-title");
});
