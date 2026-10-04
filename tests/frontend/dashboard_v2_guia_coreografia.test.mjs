// Guia do /painel (#728): a coreografia de parts/Guia.tsx e parts/guia-voo.ts. Cada passo anda
// em etapas: o balão apresenta o bloco (aceso, sem toque) e espera o "Entendi"; o Piggy voa até
// o alvo, que ganha o anel; entre passos, festa e voo até a aba, que ganha o anel e espera o
// toque: a pessoa navega, o guia nunca (dono, 2026-10-03: toda etapa espera um toque).
// Os tempos vêm de TEMPO (guia-voo.ts), lido do fonte: a fonte é uma só.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { RAIZ } from "./_painel.mjs";
import { FAZER, PASSOS, abrir, acoes, anelNoAlvo, bora, botaoEntendi, entendi, esperaTitulo, navegador, piggyEm, tocarAba, vaoDoVeu } from "./_guia.mjs";

navegador();

const fonte = readFileSync(join(RAIZ, "webapp/src/dashboard/parts/guia-voo.ts"), "utf8");
const TEMPO = Object.fromEntries([...fonte.match(/TEMPO = \{([^}]*)\}/)[1].matchAll(/(\w+): (\d+)/g)].map(([, k, v]) => [k, Number(v)]));
const SAIU = '[data-guia="resumo.saiu"]', SETA = '[data-guia="mes.trocar"]';
// Animações de script (WAAPI) do guia, no navegador: no Piggy, no balão ou numa aba.
// As do CSS (entrada, pulso, as de 1 ms do `reduce`) e as dos widgets não contam.
const deScript = (x) => !(x instanceof CSSAnimation) && !(x instanceof CSSTransition)
  && !!x.effect?.target?.matches?.('.guia-piggy, .guia-balao, [data-guia^="nav."], [data-guia="piggy.pergunta"]');
const escuro = (page, sel) => page.evaluate((sel) => {
  const r = document.querySelector(sel).getBoundingClientRect();
  return document.querySelector(".guia-sombra path").isPointInFill(new DOMPoint((r.left + r.right) / 2, (r.top + r.bottom) / 2));
}, sel);
// A hora (no relógio do node) de cada hashchange.
const relogioDeHash = async (page) => {
  const hashes = [];
  await page.exposeFunction("__hash", () => hashes.push(Date.now()));
  await page.evaluate(() => addEventListener("hashchange", () => window.__hash()));
  return hashes;
};

for (const [width, height] of [[1280, 800], [375, 812]]) {
  test(`etapas do passo 1 ${width}×${height}: Bora → bloco (Saiu aceso, Entendi, só o visto); Entendi → alvo (anel e Piggy na seta, sem POST); seta → feito`, async () => {
    const { ctx, page, s } = await abrir({ width, height });
    await bora(page);
    await page.waitForTimeout(300); // o isPointInFill do Chromium lê o path de alguns quadros antes
    const bloco = [await piggyEm(page, SAIU), await escuro(page, SAIU), await botaoEntendi(page).count(), acoes(s)];
    console.log(`# bloco ${width}:`, JSON.stringify(bloco));
    assert.ok(bloco[0][0] && bloco[0].includes(8), `Piggy no Saiu: ${bloco[0]}`);
    assert.deepEqual(bloco.slice(1), [false, 1, ["visto"]]);
    await entendi(page);
    await page.waitForTimeout(300);
    assert.deepEqual([await botaoEntendi(page).count(), acoes(s), await page.evaluate(() => document.activeElement?.id)], [0, ["visto"], "guia-titulo"]); // o Entendi não grava nada
    await anelNoAlvo(page, SETA);
    const naSeta = await piggyEm(page, SETA);
    assert.ok(naSeta[0] && naSeta.includes(8), `Piggy na seta: ${naSeta}`);
    await FAZER["mes.trocado"](page);
    await esperaTitulo(page, "Isso aí!");
    await ctx.close();
    assert.deepEqual(acoes(s), ["visto", "feito:resumo.saiu"]);
  });
}

// O voo é a mola (dashboard_v2_guia_mola.test.mjs). O pulo é WAAPI: com `reduce`, nenhum.
test("com `reduce`, nenhuma animação de script do Bora ao passo 2", async () => {
  // O 1 ms global do base.css não pega WAAPI; o portão é o calmo() do guia-voo.ts.
  const r = await abrir();
  await r.page.evaluate(`window.deScript = ${deScript}`);
  await r.page.evaluate(() => {
    window.__max = 0;
    const conta = () => { window.__max = Math.max(window.__max, document.getAnimations().filter(window.deScript).length); };
    const f = () => { conta(); requestAnimationFrame(f); };
    f();
    new MutationObserver(conta).observe(document.body, { subtree: true, childList: true, attributes: true });
  });
  await bora(r.page);
  await FAZER["mes.trocado"](r.page);
  await esperaTitulo(r.page, PASSOS[1].fala.titulo); // festa, aba e tela nova já passaram
  const max = await r.page.evaluate(() => window.__max);
  await r.ctx.close();
  assert.equal(max, 0);
});

// Depois da festa o Piggy voa até a aba da próxima tela e espera: nada troca de tela sem o toque
// (> 3 s parado), o anel está na aba, ela é a única coisa que se toca (o clique de verdade no
// resto cai no véu) e o balão diz qual tocar. Tocada, a tela é a do passo 2 e ele apresenta o bloco.
const ABA = '.rail [data-guia="nav.gastos"]';
test("entre passos nada troca de tela sem toque: a aba com anel é o único tocável; tocada, leva ao bloco do passo 2", async () => {
  const { ctx, page, s } = await abrir();
  const hashes = await relogioDeHash(page);
  await bora(page);
  await FAZER["mes.trocado"](page);
  await esperaTitulo(page, "Agora toca em Gastos.");
  await page.waitForTimeout(3500);
  const v = await page.evaluate(vaoDoVeu);
  const parado = await page.evaluate(([aba, v]) => {
    const e = document.querySelector(aba), r = e.getBoundingClientRect(), a = document.querySelector(".guia-anel"), q = a.getBoundingClientRect();
    const noVao = document.elementFromPoint((v.left + v.right) / 2, (v.top + v.bottom) / 2);
    const quem = (sel) => { const x = [...document.querySelectorAll(sel)].find((y) => y.getClientRects().length), b = x.getBoundingClientRect(), t = document.elementFromPoint((b.left + b.right) / 2, (b.top + b.bottom) / 2); return x.contains(t) ? "ele" : t?.className; };
    return [location.hash, !a.hidden && [r.left - q.left, r.top - q.top, q.right - r.right, q.bottom - r.bottom].every((d) => d >= 2 && d <= 8),
      e.contains(noVao), ['.rail [data-guia="nav.resumo"]', '[data-guia="mes.trocar"]', ".topbar .cmd-trigger"].map(quem)];
  }, [ABA, v]);
  // O clique de verdade fora da aba: nada navega nem grava.
  for (const sel of ['.rail [data-guia="nav.resumo"]', '[data-guia="mes.trocar"]', ".topbar .cmd-trigger"]) await page.mouse.click(...await page.locator(sel).first().evaluate((e) => { const r = e.getBoundingClientRect(); return [(r.left + r.right) / 2, (r.top + r.bottom) / 2]; }));
  await page.waitForTimeout(400);
  const fora = [await page.evaluate(() => location.hash), acoes(s), await page.locator(".cmdk[open]").count(), hashes.length];
  await tocarAba(page);
  await esperaTitulo(page, PASSOS[1].fala.titulo);
  const chegou = [await page.evaluate(() => location.hash), await botaoEntendi(page).count()];
  await ctx.close();
  console.log("# na aba:", JSON.stringify({ parado, fora, chegou }));
  assert.deepEqual(parado, ["#/", true, true, ["guia-veu", "guia-veu", "guia-veu"]]);
  assert.deepEqual(fora, ["#/", ["visto", "feito:resumo.saiu"], 0, 0]);
  assert.deepEqual(chegou, ["#/gastos", 1]);
});

// Esc a qualquer momento: o guia some inteiro, sem animação órfã, sem relógio que ainda navegue
// e sem erro (a promise `finished` do WAAPI rejeitaria no cancel).
test("Esc no meio: em pleno voo some tudo, sem animação de script nem erro; na etapa da aba, nada navega depois", async () => {
  const voo = await abrir({ motion: "no-preference" });
  await bora(voo.page);
  await voo.page.waitForTimeout(1200);
  await entendi(voo.page);
  await voo.page.evaluate(`window.deScript = ${deScript}`);
  await voo.page.waitForFunction(() => document.querySelector(".guia-piggy")?.style.transform); // a mola no ar
  await voo.page.keyboard.press("Escape");
  await voo.page.waitForTimeout(300);
  const r1 = await voo.page.evaluate(() => [
    document.querySelectorAll(".guia-balao, .guia-piggy, .guia-veu, .guia-anel, .guia-sombra").length,
    document.getAnimations().filter(window.deScript).length,
  ]);
  await voo.ctx.close();
  assert.deepEqual([...r1, acoes(voo.s), voo.erros], [0, 0, ["visto", "dispensar"], []]);

  const ida = await abrir({ motion: "no-preference" });
  await bora(ida.page);
  await FAZER["mes.trocado"](ida.page);
  await esperaTitulo(ida.page, "Agora toca em Gastos.");
  await ida.page.waitForTimeout(TEMPO.voo / 2); // a mola até a aba no ar
  await ida.page.keyboard.press("Escape");
  await ida.page.waitForTimeout(TEMPO.voo + 500);
  await ida.page.evaluate(`window.deScript = ${deScript}`);
  const r2 = await ida.page.evaluate(() => [
    location.hash, document.querySelectorAll(".guia-balao, .guia-piggy, .guia-veu").length, document.getAnimations().filter(window.deScript).length,
  ]);
  await ida.ctx.close();
  assert.deepEqual([...r2, acoes(ida.s).at(-1), ida.erros], ["#/", 0, 0, "dispensar", []]);
});

// O SSE (lib/eventos.ts) avisa e o painel relê tudo, o guia junto. O passo não mudou: nada
// recomeça. O aviso chega quando o teste solta; o guia relido vem com outro objeto (um texto do
// passo 3 diferente), senão o compartilhamento estrutural do TanStack não o trocaria.
const comAviso = () => {
  const c = {};
  c.chega = new Promise((ok) => { c.soltar = ok; });
  c.antes = (ctx, s) => ctx.route("**/api/v2/eventos", async (r) => {
    await c.chega;
    s.g = structuredClone(s.g); s.g.passos[2].fala.texto += " ";
    return r.fulfill({ status: 200, headers: { "content-type": "text/event-stream" }, body: 'retry: 60000\ndata: {"recurso":"tudo"}\n\n' });
  });
  return c;
};
const contaGets = (page) => {
  const n = { gets: 0 };
  page.on("request", (q) => { if (q.method() === "GET" && q.url().endsWith("/api/v2/guia")) n.gets++; });
  return n;
};
test("refetch no meio: no alvo o Entendi não volta e o anel fica; na etapa da aba, ela fica e nada troca de tela", async () => {
  const a = comAviso();
  const alvo = await abrir({ antes: a.antes });
  const n = contaGets(alvo.page);
  await bora(alvo.page);
  await entendi(alvo.page);
  await anelNoAlvo(alvo.page, SETA);
  a.soltar();
  for (let i = 0; i < 50 && n.gets === 0; i++) await alvo.page.waitForTimeout(100);
  await alvo.page.waitForTimeout(500);
  assert.deepEqual([n.gets > 0, await botaoEntendi(alvo.page).count()], [true, 0]);
  await anelNoAlvo(alvo.page, SETA);
  await alvo.ctx.close();

  const b = comAviso();
  const ida = await abrir({ antes: b.antes });
  const m = contaGets(ida.page);
  const hashes = await relogioDeHash(ida.page);
  await bora(ida.page);
  await FAZER["mes.trocado"](ida.page);
  await esperaTitulo(ida.page, "Agora toca em Gastos.");
  b.soltar();
  for (let i = 0; i < 50 && m.gets === 0; i++) await ida.page.waitForTimeout(100);
  await ida.page.waitForTimeout(800);
  const r = [m.gets > 0, await ida.page.locator("#guia-titulo").textContent(), hashes.length];
  await tocarAba(ida.page);
  await esperaTitulo(ida.page, PASSOS[1].fala.titulo);
  await ida.ctx.close();
  assert.deepEqual(r, [true, "Agora toca em Gastos.", 0]);
  assert.equal(hashes.length, 1);
});

// Abaixo de 360px a Ajuda sai da barra de baixo: o guia continua em Ferramentas e no Cmd-K.
test("plano B em 320: o cartão \"Guia do painel\" em Ferramentas e o Cmd-K abrem o guia", async () => {
  const r = [];
  for (const como of ["cartão", "Cmd-K"]) {
    const { ctx, page, s } = await abrir({ width: 320, height: 640, guia: "concluido", rota: como === "cartão" ? "/ferramentas" : "/" });
    const cartao = page.locator(".tools").getByRole("button", { name: /Guia do painel/ });
    if (como === "Cmd-K") { await page.keyboard.press("Meta+k"); await page.locator(".cmdk input").fill("guia"); await page.keyboard.press("Enter"); }
    else if (await cartao.count()) await cartao.click();
    await page.locator(".guia-balao").waitFor({ timeout: 5000 }).catch(() => {});
    r.push([como, acoes(s), await page.evaluate(() => document.querySelector(".tabbar button")?.getClientRects().length)]);
    await ctx.close();
  }
  assert.deepEqual(r, [["cartão", ["reabrir"], 0], ["Cmd-K", ["reabrir"], 0]]);
});

// A região de anúncio do guia (a da conversa é outra) fala o que o balão diz em cada etapa, e
// cada etapa muda o texto (texto igual, o leitor de tela cala). Na etapa da aba ela diz o que
// tocar e não adianta o passo seguinte: o título dele só chega com o bloco, junto com o foco.
test("anúncio por etapa: convite, bloco (apresenta), alvo (instrução), festa, aba, bloco do passo 2; o título do passo 2 só no bloco", async () => {
  const { ctx, page } = await abrir();
  await page.evaluate(() => {
    window.__falas = [];
    const r = document.querySelector(".guia-status");
    const f = () => { const t = r.textContent; if (t && t !== window.__falas.at(-1)) window.__falas.push(t); };
    f();
    new MutationObserver(f).observe(r, { childList: true, characterData: true, subtree: true });
  });
  await bora(page);
  await FAZER["mes.trocado"](page);
  await esperaTitulo(page, PASSOS[1].fala.titulo);
  await page.waitForTimeout(300);
  const [falas, foco] = await page.evaluate(() => [window.__falas, [document.activeElement?.id, document.activeElement?.textContent]]);
  await ctx.close();
  console.log("# anúncios:", JSON.stringify(falas));
  const [p1, p2] = PASSOS;
  assert.deepEqual(falas, [
    "O Piggy quer te mostrar o painel.",
    `Passo 1 de 3: ${p1.fala.titulo}. ${p1.fala.apresenta}`,
    p1.fala.texto,
    "Passo feito. Vem comigo pra Gastos.",
    "Agora toca em Gastos.",
    `Passo 2 de 3: ${p2.fala.titulo}. ${p2.fala.apresenta}`,
  ]);
  assert.ok(foco[0] === "guia-titulo" && foco[1].startsWith(p2.fala.titulo), `foco: ${foco}`);
});

// "no Piggy" (ele é masculino), "em Gastos": a preposição concorda com a tela. No celular a aba
// é a do Piggy; no desktop, a barra de conversa (D9), que não é link: tocar nela leva à conversa.
for (const [width, height] of [[375, 812], [1280, 800]]) {
  test(`passo 3 ${width}×${height}: "Agora toca no Piggy." no título e no anúncio; parado, fica; tocar leva à conversa`, async () => {
    const { ctx, page } = await abrir({ width, height, guia: "em_andamento", antes: (_, s) => { s.g.passos.forEach((p, i) => { p.feito = i < 2; }); } });
    await page.evaluate(() => dispatchEvent(new Event("dash:guia")));
    await esperaTitulo(page, "Agora toca no Piggy.");
    await page.waitForTimeout(3500);
    const antes = [await page.locator(".guia-status").textContent(), await page.evaluate(() => location.hash)];
    await tocarAba(page);
    await esperaTitulo(page, PASSOS[2].fala.titulo);
    const depois = [await page.evaluate(() => location.hash), await botaoEntendi(page).count()];
    await ctx.close();
    assert.deepEqual([antes, depois], [["Agora toca no Piggy.", "#/"], ["#/piggy", 1]]);
  });
}

// A ação só conta na etapa alvo: no bloco a seta está sob o véu, mas um clique por script (ou
// um leitor de tela que ative o controle direto) passa por baixo dele. O guarda é o `etapa ===
// "alvo"` do efeito da ação real (Guia.tsx), não o véu.
test("no bloco a ação não conta: a seta clicada por script, por baixo do véu, não grava `feito`; depois do Entendi, conta", async () => {
  const { ctx, page, s } = await abrir();
  await bora(page);
  const clicar = () => page.evaluate((sel) => [...document.querySelectorAll(sel)].find((e) => e.getClientRects().length).click(), SETA);
  const mes = () => page.evaluate(() => document.querySelector(".topbar")?.textContent);
  const antes = await mes();
  await clicar();
  await page.waitForTimeout(500);
  const noBloco = [acoes(s), (await mes()) !== antes];
  await entendi(page);
  await anelNoAlvo(page, SETA);
  await clicar();
  await esperaTitulo(page, "Isso aí!");
  await ctx.close();
  assert.deepEqual([noBloco, acoes(s)], [[["visto"], true], ["visto", "feito:resumo.saiu"]]);
});
