// Os passos Organizar e Ferramentas do guia do /painel (#728), e o convite de quem já tinha o
// roteiro antigo. Os outros testes do guia andam pelos 3 passos de sempre (`roteiro: "antigo"`,
// _guia.mjs); aqui o roteiro é o da fixture inteira (`roteiro: "todos"`).
//   · Organizar: o passo conta quando a pessoa LIGA o Organizar (o guia some enquanto organiza e
//     volta no Pronto); layout já salvo no navegador = passo feito, sem festa;
//   · Ferramentas: conta abrir uma ferramenta da lista; voltar/ir a outra aba não conta, nem o
//     "Painel antigo" (recarrega a página); no celular a aba é o Menu, e o guia espera a folha;
//   · convite de novidades: quem já fez parte do roteiro ouve quantos passos são novos.
import { test } from "node:test";
import assert from "node:assert/strict";
import { PAINEL } from "./_painel.mjs";
import { FAZER, PASSOS_TODOS, ROTA, abrir, acoes, anelNoAlvo, bora, entendi, esperaTitulo, navegador, tocarAba } from "./_guia.mjs";

navegador();

const NOVOS = ["resumo.organizar", "ferramentas.abrir"];
const TODOS = PASSOS_TODOS.map((p) => p.id);
// O que o servidor já tem feito quando a página abre.
const feitos = (ids, estado = "em_andamento") => (_ctx, s) => {
  s.g.passos.forEach((p) => { p.feito = ids.includes(p.id); });
  s.g.estado = estado;
};
const ajuda = (page) => page.locator(".rail").getByRole("button", { name: "Ajuda" }).click();
const titulo = (t) => new RegExp(`^${t}$`);

test("fonte única: os passos novos têm âncora na tela deles, na aba e uma ação tratada aqui", async () => {
  const novos = PASSOS_TODOS.filter((p) => NOVOS.includes(p.id));
  assert.equal(novos.length, 2);
  assert.deepEqual(novos.map((p) => p.acao).filter((a) => !FAZER[a]), []);
  const { ctx, page } = await abrir({ guia: "concluido", roteiro: "todos" });
  const faltam = [];
  for (const p of novos) {
    await page.goto(`${PAINEL}#${ROTA[p.tela]}`);
    await page.locator("#page-title").waitFor();
    await page.locator(`[data-guia="${p.ancora}"]`).first().waitFor({ timeout: 5000 }).catch(() => faltam.push(p.ancora));
    if (!(await page.locator(`[data-guia="nav.${p.tela}"]`).count())) faltam.push(`nav.${p.tela}`);
  }
  await ctx.close();
  assert.deepEqual(faltam, []);
});

test("Organizar: ligar o Organizar avança, o guia some enquanto organiza e volta no Pronto", async () => {
  const { ctx, page, s } = await abrir({ guia: "em_andamento", roteiro: "todos", antes: feitos(["resumo.saiu"]) });
  await ajuda(page);
  await esperaTitulo(page, "Do seu jeito");
  await entendi(page);
  await anelNoAlvo(page, '[data-guia="painel.organizar"]');
  await page.locator('[data-guia="painel.organizar"]').click();
  await page.waitForFunction(() => !document.querySelector(".guia-balao") && !document.querySelector(".guia-veu"));
  await page.waitForTimeout(2200); // a festa (1,6 s) passa escondida
  const durante = [acoes(s), await page.locator(".guia-balao").count()];
  await page.locator('[data-guia="painel.organizar"]').click(); // Pronto
  await esperaTitulo(page, "Pra onde foi o dinheiro"); // o guia voltou e segue (a aba Gastos, tocada pelo helper)
  await ctx.close();
  assert.deepEqual(durante, [["reabrir", "feito:resumo.organizar"], 0]);
});

test("Organizar: o Organizar já ligado antes de o passo começar não conta; Pronto sozinho também não", async () => {
  const { ctx, page, s } = await abrir({ guia: "em_andamento", roteiro: "todos", antes: feitos(["resumo.saiu"]) });
  await page.locator('[data-guia="painel.organizar"]').click(); // organiza ANTES do guia
  await ajuda(page); // a Ajuda abre o guia enquanto organiza: o guia espera
  await page.waitForTimeout(500);
  await page.locator('[data-guia="painel.organizar"]').click(); // Pronto
  await esperaTitulo(page, "Do seu jeito");
  await ctx.close();
  assert.deepEqual(acoes(s), ["reabrir"]);
});

test("layout salvo no navegador: o Organizar vale como feito, sem festa; sem layout, o passo vem", async () => {
  const com = await abrir({ guia: "oferecer", roteiro: "todos", antes: (ctx) => ctx.addInitScript(() => {
    localStorage.setItem("pigbank.dashboard.layout.v1.economizar", JSON.stringify(["contas", "resumo"]));
    // Registra se a festa "Isso aí!" chegou a aparecer, por mais breve que seja.
    new MutationObserver(() => { if (document.body?.innerText.includes("Isso aí!")) window.festaVista = true; }).observe(document, { childList: true, subtree: true, characterData: true });
  }) });
  await bora(com.page);
  await com.page.waitForTimeout(800); // o 200 do feito:resumo.organizar já voltou
  const festa = await com.page.evaluate(() => !!window.festaVista);
  assert.equal(festa, false);
  await FAZER["mes.trocado"](com.page);
  await esperaTitulo(com.page, "Pra onde foi o dinheiro");
  const pulou = acoes(com.s);
  await com.ctx.close();
  assert.deepEqual(pulou, ["visto", "feito:resumo.organizar", "feito:resumo.saiu"]);

  const sem = await abrir({ guia: "oferecer", roteiro: "todos" });
  await bora(sem.page);
  await FAZER["mes.trocado"](sem.page);
  await esperaTitulo(sem.page, "Do seu jeito");
  const veio = acoes(sem.s);
  await sem.ctx.close();
  assert.deepEqual(veio, ["visto", "feito:resumo.saiu"]);
});

test("Ferramentas (desktop): abrir uma ferramenta avança; sair para outra aba, ou o Painel antigo, não", async () => {
  const fim = TODOS.filter((i) => i !== "ferramentas.abrir");
  const { ctx, page, s } = await abrir({ guia: "em_andamento", roteiro: "todos", antes: feitos(fim) });
  await ajuda(page);
  await esperaTitulo(page, titulo("Ferramentas")); // a aba "Agora toca em Ferramentas" é tocada no caminho
  await entendi(page);
  await anelNoAlvo(page, '[data-guia="ferramentas.compra"]');
  // Controle: sair de Ferramentas por outro caminho que não uma ferramenta não conta.
  await page.evaluate(() => { location.hash = "#/gastos"; });
  await page.waitForTimeout(600);
  const fora = acoes(s);
  await page.evaluate(() => { location.hash = "#/ferramentas"; }); // o guia pede a aba e volta ao alvo
  await FAZER["ferramenta.aberta"](page);
  await naRotaDaCompra(page);
  await page.getByText("Isso aí!").or(page.getByText("Fechou!")).first().waitFor({ timeout: 5000 });
  await ctx.close();
  assert.deepEqual(fora, ["reabrir"]);
  assert.deepEqual(acoes(s).slice(-1), ["feito:ferramentas.abrir"]);
});
const naRotaDaCompra = (page) => page.waitForFunction(() => location.hash === "#/simulador/compra", null, { timeout: 5000 });

test("Ferramentas (celular): a aba é o Menu; o guia espera a folha aberta, o Esc fecha só a folha", async () => {
  const fim = TODOS.filter((i) => i !== "ferramentas.abrir");
  const { ctx, page, s } = await abrir({ width: 390, height: 844, guia: "em_andamento", roteiro: "todos", antes: feitos(fim) });
  await page.locator(".tabbar").getByRole("button", { name: "Menu" }).click();
  await page.locator(".menu-telas").getByRole("button", { name: "Guia do painel" }).click();
  const leva = page.locator("#guia-titulo", { hasText: "Agora toca" });
  await leva.waitFor();
  assert.equal(await leva.innerText(), "Agora toca em Menu e depois em Ferramentas.");
  await tocarAba(page);
  await page.locator(".menu-telas[open]").waitFor();
  await page.waitForFunction(() => !document.querySelector(".guia-balao"));
  await page.keyboard.press("Escape");
  await page.waitForFunction(() => !document.querySelector(".menu-telas[open]"));
  await leva.waitFor(); // a folha fechou: o guia voltou, ainda apontando o Menu
  const aposEsc = acoes(s);
  await tocarAba(page);
  await page.locator(".menu-telas").getByRole("link", { name: "Ferramentas" }).click();
  await esperaTitulo(page, titulo("Ferramentas")); // chegou: o passo segue normal
  await entendi(page);
  await FAZER["ferramenta.aberta"](page);
  await naRotaDaCompra(page);
  await page.getByText("Isso aí!").or(page.getByText("Fechou!")).first().waitFor({ timeout: 5000 });
  await ctx.close();
  assert.deepEqual(aposEsc, ["reabrir"]); // nem dispensou, nem fez o passo
  assert.deepEqual(acoes(s).slice(-1), ["feito:ferramentas.abrir"]);
});

test("convite de novidades: quem já fez parte do roteiro ouve quantos passos são novos", async () => {
  const antigos = TODOS.filter((i) => !NOVOS.includes(i));
  const { ctx, page, s } = await abrir({ guia: "oferecer", roteiro: "todos", antes: feitos(antigos, "oferecer") });
  const balao = page.locator(".guia-balao");
  await balao.getByRole("button", { name: "Bora", exact: true }).waitFor();
  const texto = await balao.innerText();
  await balao.getByRole("button", { name: "Bora", exact: true }).click();
  await esperaTitulo(page, "Do seu jeito"); // começa no 1º não feito
  const bolinhas = await page.locator(".guia-passo i").count();
  await ctx.close();
  assert.match(texto, /Tem 2 novidades no painel/);
  assert.equal(bolinhas, TODOS.length); // a indicação continua contando todos
  assert.deepEqual(acoes(s), ["visto"]);
});

// Layout salvo = o Organizar será pulado: não é novidade a oferecer.
const comLayout = (ctx) => ctx.addInitScript(() => localStorage.setItem("pigbank.dashboard.layout.v1.economizar", JSON.stringify(["contas", "resumo"])));
const semOrganizar = TODOS.filter((i) => i !== "resumo.organizar");

test("convite de novidades com layout salvo: conta só o que falta de verdade (Ferramentas = 1 passo)", async () => {
  const { ctx, page, s } = await abrir({ guia: "oferecer", roteiro: "todos", antes: async (c, st) => { feitos(TODOS.filter((i) => !NOVOS.includes(i)), "oferecer")(c, st); await comLayout(c); } });
  const balao = page.locator(".guia-balao");
  await balao.getByRole("button", { name: "Bora", exact: true }).waitFor();
  const texto = await balao.innerText();
  await ctx.close();
  assert.match(texto, /Tem 1 novidade no painel\. São 1 passo rapidinho/);
  assert.deepEqual(acoes(s), ["visto"]);
});

test("convite de quem nunca viu o guia: conta 5 passos, ou 4 com layout salvo (o Organizar é pulado)", async () => {
  const texto = async (layout) => {
    const { ctx, page } = await abrir({ guia: "oferecer", roteiro: "todos", antes: layout ? comLayout : undefined });
    const balao = page.locator(".guia-balao");
    await balao.getByRole("button", { name: "Bora", exact: true }).waitFor();
    const t = await balao.innerText();
    await ctx.close();
    return t;
  };
  assert.match(await texto(false), /São 5 passos rapidinhos/);
  assert.match(await texto(true), /São 4 passos rapidinhos/);
});

test("convite de novidades: se só o Organizar falta e há layout salvo, nada é oferecido; o passo é dado em silêncio", async () => {
  const { ctx, page, s } = await abrir({ guia: "oferecer", roteiro: "todos", antes: async (c, st) => { feitos(semOrganizar, "oferecer")(c, st); await comLayout(c); } });
  await page.waitForFunction(() => true);
  await page.waitForTimeout(1200);
  const balao = await page.locator(".guia-balao").count();
  const estado = s.g.estado;
  await ctx.close();
  assert.equal(balao, 0);
  assert.deepEqual(acoes(s), ["feito:resumo.organizar"]);
  assert.equal(estado, "concluido");
});

test("Esc com o guia pausado (Organizar ligado) não dispensa o guia; ele volta no Pronto", async () => {
  const { ctx, page, s } = await abrir({ guia: "em_andamento", roteiro: "todos", antes: feitos(["resumo.saiu"]) });
  await ajuda(page);
  await esperaTitulo(page, "Do seu jeito");
  await entendi(page);
  await anelNoAlvo(page, '[data-guia="painel.organizar"]');
  await page.locator('[data-guia="painel.organizar"]').click();
  await page.waitForFunction(() => !document.querySelector(".guia-balao"));
  await page.waitForTimeout(2200);
  await page.keyboard.press("Escape");
  await page.waitForTimeout(300);
  await page.locator('[data-guia="painel.organizar"]').click(); // Pronto
  await esperaTitulo(page, "Pra onde foi o dinheiro");
  await ctx.close();
  assert.ok(!acoes(s).includes("dispensar"), acoes(s).join());
});

test("Organizar ligado: sair do Resumo desliga o modo e o guia volta nas outras telas", async () => {
  const { ctx, page, s } = await abrir({ guia: "em_andamento", roteiro: "todos", antes: feitos(["resumo.saiu"]) });
  await ajuda(page);
  await esperaTitulo(page, "Do seu jeito");
  await entendi(page);
  await anelNoAlvo(page, '[data-guia="painel.organizar"]');
  await page.locator('[data-guia="painel.organizar"]').click();
  await page.waitForFunction(() => !document.querySelector(".guia-balao"));
  await page.evaluate(() => { location.hash = "#/gastos"; });
  await esperaTitulo(page, "Pra onde foi o dinheiro"); // sem o conserto o guia fica invisível aqui
  await ctx.close();
  assert.deepEqual(acoes(s), ["reabrir", "feito:resumo.organizar"]);
});
