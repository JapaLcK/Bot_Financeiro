// Guia do /painel (#728): a coreografia de parts/Guia.tsx e parts/guia-voo.ts. Cada passo anda
// em etapas: o balão apresenta o bloco (aceso, sem toque) e espera o "Entendi"; o Piggy voa até
// o alvo, que ganha o anel; entre passos, festa, voo até a aba, pausa, aperto e troca de tela.
// Os tempos vêm de TEMPO (guia-voo.ts), lido do fonte: a fonte é uma só.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { RAIZ } from "./_painel.mjs";
import { FAZER, PASSOS, abrir, acoes, anelNoAlvo, bora, botaoEntendi, entendi, esperaTitulo, navegador, piggyEm } from "./_guia.mjs";

navegador();

const fonte = readFileSync(join(RAIZ, "webapp/src/dashboard/parts/guia-voo.ts"), "utf8");
const TEMPO = Object.fromEntries([...fonte.match(/TEMPO = \{([^}]*)\}/)[1].matchAll(/(\w+): (\d+)/g)].map(([, k, v]) => [k, Number(v)]));
const SAIU = '[data-guia="resumo.saiu"]', SETA = '[data-guia="mes.trocar"]';
// Animações de script (WAAPI) do guia, no navegador: no Piggy, no balão ou numa aba (o aperto).
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
// O POST `feito` sai com 200: a hora em que o servidor de mentira respondeu.
const comHoraDoFeito = (t) => ({ post: (c) => { if (c.acao === "feito") t.feito = Date.now(); return null; } });

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

// O voo é a mola (dashboard_v2_guia_mola.test.mjs). Pulo e aperto são WAAPI: com `reduce`, nenhum.
test("com `reduce`, nenhuma animação de script do Bora à ida", async () => {
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
  await esperaTitulo(r.page, PASSOS[1].fala.titulo); // festa, ida, aperto e troca já passaram
  const max = await r.page.evaluate(() => window.__max);
  await r.ctx.close();
  assert.equal(max, 0);
});

// Depois da festa, o guia não leva de cara: o Piggy vai até a aba e espera a pausa (com `reduce`
// o voo e o aperto valem 0, decisão D4; a pausa fica). Na pausa a aba está acesa e com anel, mas
// não se toca.
test("navegação espera a pausa: do 200 do feito à troca de tela ≥ festa + pausa; na pausa, aba com anel, sob o véu, \"Vem comigo pra Gastos\"", async () => {
  const t = {};
  const { ctx, page } = await abrir(comHoraDoFeito(t));
  const hashes = await relogioDeHash(page);
  await bora(page);
  await FAZER["mes.trocado"](page);
  await page.locator("#guia-titulo", { hasText: "Vem comigo pra Gastos" }).waitFor({ timeout: 5000 });
  await page.waitForTimeout(TEMPO.pausa / 4);
  const pausa = await page.evaluate((sel) => {
    const e = document.querySelector(sel), r = e.getBoundingClientRect(), t = document.elementFromPoint((r.left + r.right) / 2, (r.top + r.bottom) / 2);
    const a = document.querySelector(".guia-anel"), q = a.getBoundingClientRect();
    const anel = a.hidden ? null : [r.left - q.left, r.top - q.top, q.right - r.right, q.bottom - r.bottom].every((d) => d >= 2 && d <= 8);
    return [location.hash, e.contains(t) ? "aba" : t?.className, anel];
  }, '.rail [data-guia="nav.gastos"]');
  await esperaTitulo(page, PASSOS[1].fala.titulo);
  await ctx.close();
  const espera = hashes[0] - t.feito;
  console.log(`# do 200 à troca: ${espera} ms (festa ${TEMPO.festa} + pausa ${TEMPO.pausa})`);
  assert.deepEqual(pausa, ["#/", "guia-veu", true]);
  assert.equal(hashes.length, 1);
  assert.ok(espera >= TEMPO.festa + TEMPO.pausa - 50, `${espera} ms`);
});

// Esc a qualquer momento: o guia some inteiro, sem animação órfã, sem relógio que ainda navegue
// e sem erro (a promise `finished` do WAAPI rejeitaria no cancel).
test("Esc no meio: em pleno voo some tudo, sem animação de script nem erro; na pausa da ida, nada navega depois", async () => {
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
  await ida.page.locator("#guia-titulo", { hasText: "Vem comigo pra Gastos" }).waitFor({ timeout: 5000 });
  await ida.page.waitForTimeout(TEMPO.voo + TEMPO.pausa / 4); // o voo pousou na aba: é a pausa
  await ida.page.keyboard.press("Escape");
  await ida.page.waitForTimeout(TEMPO.pausa + TEMPO.aperto + TEMPO.troca + 500);
  await ida.page.evaluate(`window.deScript = ${deScript}`);
  const r2 = await ida.page.evaluate(() => [
    location.hash, document.documentElement.hasAttribute("data-guia-troca"),
    document.querySelector('.rail [data-guia="nav.gastos"]').getAnimations().filter(window.deScript).length,
  ]);
  await ida.ctx.close();
  assert.deepEqual([...r2, acoes(ida.s).at(-1), ida.erros], ["#/", false, 0, "dispensar", []]);
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
test("refetch no meio: no alvo o Entendi não volta e o anel fica; na pausa da ida, uma troca de tela só, no tempo de sempre", async () => {
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

  const b = comAviso(), t = {};
  const ida = await abrir({ antes: b.antes, ...comHoraDoFeito(t) });
  const m = contaGets(ida.page);
  const hashes = await relogioDeHash(ida.page);
  await bora(ida.page);
  await FAZER["mes.trocado"](ida.page);
  await ida.page.locator("#guia-titulo", { hasText: "Vem comigo pra Gastos" }).waitFor({ timeout: 5000 });
  await ida.page.waitForTimeout(TEMPO.pausa / 2); // no meio da pausa
  b.soltar();
  await esperaTitulo(ida.page, PASSOS[1].fala.titulo);
  await ida.page.waitForTimeout(500);
  await ida.ctx.close();
  const espera = hashes[0] - t.feito;
  console.log(`# refetch na pausa: troca em ${espera} ms; GETs ${m.gets}`);
  assert.ok(m.gets > 0, "o aviso não releu o guia");
  assert.equal(hashes.length, 1);
  assert.ok(espera <= TEMPO.festa + TEMPO.pausa + 250, `a pausa recomeçou: ${espera} ms`);
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
// cada etapa muda o texto (texto igual, o leitor de tela cala). Na ida ela não adianta o passo
// seguinte: o título dele só chega com o bloco, junto com o foco no título do balão.
test("anúncio por etapa: convite, bloco (apresenta), alvo (instrução), festa, ida, bloco do passo 2; o título do passo 2 só no bloco", async () => {
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
    "Vem comigo pra Gastos.",
    `Passo 2 de 3: ${p2.fala.titulo}. ${p2.fala.apresenta}`,
  ]);
  assert.ok(foco[0] === "guia-titulo" && foco[1].startsWith(p2.fala.titulo), `foco: ${foco}`);
});

// "pro Piggy" (ele é masculino), e "pra Gastos": a preposição concorda com a tela. No celular a
// ida é a aba Piggy; no desktop, a barra de conversa (D9).
for (const [width, height] of [[375, 812], [1280, 800]]) {
  test(`passo 3 ${width}×${height}: "Vem comigo pro Piggy." no título e no anúncio, e leva à conversa`, async () => {
    const { ctx, page } = await abrir({ width, height, guia: "em_andamento", antes: (_, s) => { s.g.passos.forEach((p, i) => { p.feito = i < 2; }); } });
    await page.evaluate(() => dispatchEvent(new Event("dash:guia")));
    await esperaTitulo(page, "Vem comigo pro Piggy.");
    const fala = await page.locator(".guia-status").textContent();
    await esperaTitulo(page, PASSOS[2].fala.titulo);
    const hash = await page.evaluate(() => location.hash);
    await ctx.close();
    assert.deepEqual([fala, hash], ["Vem comigo pro Piggy.", "#/piggy"]);
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
