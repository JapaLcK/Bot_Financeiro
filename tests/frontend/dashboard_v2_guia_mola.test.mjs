// Guia do /painel (#728): a mola do voo (parts/guia-voo.ts, `partir` e `quadro`). O recorte aceso,
// o balão e o Piggy vão de uma mira à outra na mesma mola; com `reduce`, saltam. O toque durante a
// mola é só o pedaço do alvo que já está aceso. E as bolinhas de progresso do balão.
// O relógio: a mola lê `performance.now()` no rAF do guia; o teste o congela e o avança à mão,
// então "o meio da mola" é sempre o mesmo instante (200 ms, metade do caminho) e "assentou" é
// TEMPO.voo depois.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { RAIZ } from "./_painel.mjs";
import { FAZER, PASSOS, abrir, bora, entendi, esperaTitulo, navegador } from "./_guia.mjs";

navegador();

const fonte = readFileSync(join(RAIZ, "webapp/src/dashboard/parts/guia-voo.ts"), "utf8");
const VOO = Number(fonte.match(/TEMPO = \{ voo: (\d+)/)[1]);
const relogio = (page) => page.evaluate(() => { window.__t = performance.now(); performance.now = () => window.__t; });
const avanca = (page, ms) => page.evaluate(async (ms) => {
  window.__t += ms;
  for (let i = 0; i < 3; i++) await new Promise((ok) => requestAnimationFrame(ok));
}, ms);

// Um quadro: o centro do Piggy; a quina do balão (left/top + o deslocamento da mola, sem a
// escala: o conteúdo troca na partida e muda a altura, então o centro não serve), opacidade e
// escala; o recorte aceso (lido do path do véu), o toque (o vão entre as quatro faixas), o alvo
// e quem recebe o clique em pontos do toque, do aceso fora do toque e de fora do aceso ("véu":
// as faixas, ou o balão por cima).
const foto = (page) => page.evaluate(() => {
  const r = (e) => { const q = e.getBoundingClientRect(); return { left: q.left, top: q.top, right: q.right, bottom: q.bottom }; };
  const centro = (q) => [(q.left + q.right) / 2, (q.top + q.bottom) / 2];
  const b = document.querySelector(".guia-balao"), cs = getComputedStyle(b), m = new DOMMatrix(cs.transform);
  const n = (document.querySelector(".guia-sombra path").getAttribute("d").split("Z")[1].match(/-?[\d.]+(e-?\d+)?/g) || []).map(Number);
  const aceso = n.length ? { left: n[0] - n[3], top: n[1], right: n[0] + n[2] + n[3], bottom: n[1] + n[10] + 2 * n[3] } : null;
  const [f0, f1, f2, f3] = [...document.querySelectorAll(".guia-veu")].map(r);
  const toque = { left: f2.right, top: f0.bottom, right: f3.left, bottom: f1.top };
  const temToque = toque.right > toque.left && toque.bottom > toque.top;
  const seta = [...document.querySelectorAll('[data-guia="mes.trocar"]')].find((e) => e.getClientRects().length);
  const quem = ([x, y]) => { const t = document.elementFromPoint(x, y); return seta.contains(t) ? "alvo" : t?.classList.contains("guia-veu") || b.contains(t) ? "véu" : t?.className || t?.tagName; };
  // Um ponto aceso fora do toque: o centro ou o meio de uma borda do aceso (1 px para dentro).
  const c = aceso && centro(aceso), noVao = (p) => temToque && p[0] >= toque.left - 0.5 && p[0] <= toque.right + 0.5 && p[1] >= toque.top - 0.5 && p[1] <= toque.bottom + 0.5;
  const sobra = aceso && [c, [aceso.left + 1, c[1]], [aceso.right - 1, c[1]], [c[0], aceso.top + 1], [c[0], aceso.bottom - 1]].find((p) => !noVao(p));
  return {
    pg: centro(r(document.querySelector(".guia-piggy"))), b: [parseFloat(b.style.left) + m.e, parseFloat(b.style.top) + m.f], op: Number(cs.opacity), escala: m.a,
    aceso, toque: temToque ? toque : null, alvo: r(seta), anel: !document.querySelector(".guia-anel").hidden,
    noToque: temToque ? quem(centro(toque)) : null, naSobra: sobra ? quem(sobra) : null, foraDoAceso: aceso ? quem([centro(aceso)[0], aceso.bottom + 3]) : null,
  };
});
const cx = (q) => [(q.left + q.right) / 2, (q.top + q.bottom) / 2];
const perto = (p, q, d = 1) => Math.hypot(p[0] - q[0], p[1] - q[1]) <= d;
const entre = (m, a, b) => m > Math.min(a, b) + 1 && m < Math.max(a, b) - 1;
const contem = (q, d) => d.left >= q.left - 0.5 && d.top >= q.top - 0.5 && d.right <= q.right + 0.5 && d.bottom <= q.bottom + 0.5;

// Do Saiu (bloco, aceso sem toque) à seta (alvo), pelo Entendi: o quadro de partida (A), o meio
// da mola (200 ms), um quadro quase assentado (450 ms, ~90% do caminho) e o pouso (TEMPO.voo).
async function voo(width, height, motion) {
  const { ctx, page } = await abrir({ width, height, motion });
  await bora(page);
  await page.waitForTimeout(1200); // a entrada (CSS) e o voo do convite ao Saiu
  await relogio(page);
  const A = await foto(page);
  await entendi(page);
  await avanca(page, 0);
  const zero = await foto(page);
  await avanca(page, 200);
  const meio = await foto(page);
  await avanca(page, 250);
  const quase = await foto(page);
  await avanca(page, VOO - 450);
  const B = await foto(page);
  await ctx.close();
  return { A, zero, meio, quase, B };
}

for (const [width, height] of [[1280, 800], [375, 812]]) {
  test(`mola ${width}×${height}: o recorte aceso desliza do Saiu à seta e assenta em TEMPO.voo; com reduce, salta`, async () => {
    const { A, meio, B } = await voo(width, height, "no-preference");
    console.log(`# recorte ${width}:`, JSON.stringify({ A: A.aceso, meio: meio.aceso, B: B.aceso }));
    assert.ok(A.aceso && !perto(cx(A.aceso), cx(B.aceso), 24), "Saiu e seta longe");
    assert.ok(entre(cx(meio.aceso)[0], cx(A.aceso)[0], cx(B.aceso)[0]) || entre(cx(meio.aceso)[1], cx(A.aceso)[1], cx(B.aceso)[1]), "o meio entre A e B");
    assert.ok(!perto(cx(meio.aceso), cx(A.aceso), 4) && !perto(cx(meio.aceso), cx(B.aceso), 4), "o meio é outro lugar");
    // Calma (dono): aos 200 ms, perto da metade do caminho (a mola de 1 s faz 54%; a de 0,55 s, 90%).
    const andou = Math.hypot(...[0, 1].map((i) => cx(meio.aceso)[i] - cx(A.aceso)[i])) / Math.hypot(...[0, 1].map((i) => cx(B.aceso)[i] - cx(A.aceso)[i]));
    assert.ok(andou > 0.4 && andou < 0.7, `aos 200 ms andou ${andou}`);
    assert.ok(contem(B.alvo, B.aceso) && contem(B.aceso, B.alvo), "pousado: o aceso é a seta");
    const r = await voo(width, height, "reduce");
    assert.ok(contem(r.zero.alvo, r.zero.aceso) && contem(r.zero.aceso, r.zero.alvo), `reduce: já na seta ${JSON.stringify(r.zero.aceso)}`);
  });

  test(`mola ${width}×${height}: o balão desliza e surge (opacidade e escala), o Piggy em arco; com reduce, os dois saltam`, async () => {
    const { A, meio, B } = await voo(width, height, "no-preference");
    console.log(`# balão e Piggy ${width}:`, JSON.stringify({ b: [A.b, meio.b, B.b], op: meio.op, escala: meio.escala, pg: [A.pg, meio.pg, B.pg] }));
    // No celular o balão fica no pé nos dois (anda 10 px); no desktop ele troca de lado.
    assert.ok(width < 640 || !perto(A.b, B.b, 24), "o balão muda de lugar");
    assert.ok(entre(meio.b[0], A.b[0], B.b[0]) || entre(meio.b[1], A.b[1], B.b[1]), "balão no meio");
    assert.ok(meio.op > 0 && meio.op < 1 && meio.escala > 0.96 && meio.escala < 1, `surge: ${meio.op} ${meio.escala}`);
    assert.deepEqual([B.op, B.escala], [1, 1]);
    assert.ok(entre(meio.pg[0], A.pg[0], B.pg[0]), `x do Piggy fora de A–B: ${meio.pg[0]}`);
    const reta = A.pg[1] + (meio.pg[0] - A.pg[0]) * (B.pg[1] - A.pg[1]) / (B.pg[0] - A.pg[0]);
    assert.ok(meio.pg[1] <= reta - 20, `arco: o meio está ${Math.round(reta - meio.pg[1])} px acima da reta`);
    const r = await voo(width, height, "reduce");
    assert.ok(perto(r.zero.b, r.B.b) && perto(r.zero.pg, r.B.pg) && r.zero.op === 1, "reduce: já pousados");
  });

  test(`mola ${width}×${height}: o clique só passa no pedaço do alvo que está aceso, durante e depois`, async () => {
    const { meio, quase, B } = await voo(width, height, "no-preference");
    console.log(`# toque ${width}:`, JSON.stringify({ meio: [meio.toque, meio.noToque, meio.naSobra, meio.foraDoAceso], quase: [quase.toque, quase.noToque, quase.naSobra] }));
    for (const f of [meio, quase, B]) {
      if (f.toque) assert.ok(contem(f.aceso, f.toque) && contem(f.alvo, f.toque), `toque fora do aceso ou do alvo: ${JSON.stringify(f)}`);
      assert.equal(f.foraDoAceso, "véu");
      if (f.naSobra) assert.equal(f.naSobra, "véu");
    }
    assert.ok(quase.toque, "a 450 ms o alvo já se toca");
    assert.deepEqual([quase.noToque, B.noToque, B.anel, meio.anel], ["alvo", "alvo", true, false]);
  });
}

test("bolinhas: a atual é a larga e rosa, uma por passo; o \"Passo N de 3\" fica só para o leitor de tela", async () => {
  const { ctx, page } = await abrir({ width: 375, height: 812 });
  const ler = () => page.evaluate(() => {
    const p = document.querySelector(".guia-passo");
    return [[...p.querySelectorAll("i")].map((i) => [i.getBoundingClientRect().width, i.hasAttribute("data-atual")]), p.querySelector(".sr-only")?.textContent, p.querySelector(".sr-only")?.getBoundingClientRect().width];
  });
  await bora(page);
  const p1 = await ler();
  await FAZER["mes.trocado"](page);
  await esperaTitulo(page, PASSOS[1].fala.titulo);
  const p2 = await ler();
  await ctx.close();
  assert.deepEqual(p1, [[[18, true], [6, false], [6, false]], "Passo 1 de 3", 1]);
  assert.deepEqual(p2, [[[6, false], [18, true], [6, false]], "Passo 2 de 3", 1]);
});

// O que guarda a mola e o recorte (voo, mira, último recorte, furo) é do Piggy montado: fechar em
// pleno voo e reabrir antes de a mola assentar não pode dar ao Piggy e ao balão novos o transform
// da mola órfã. Relógio de verdade: o defeito só aparece com o tempo correndo.
test("fechar em pleno voo (Pular e Esc) e reabrir pela Ajuda em < 1 s: o Piggy e o balão nascem sem a mola anterior", async () => {
  const r = [];
  for (const como of ["Pular", "Esc"]) {
    const { ctx, page, erros } = await abrir({ motion: "no-preference" });
    await bora(page);
    await page.waitForTimeout(1200);
    await entendi(page);
    await page.waitForTimeout(80); // a mola no ar
    if (como === "Esc") await page.keyboard.press("Escape");
    else await page.getByRole("button", { name: "Pular guia" }).click();
    await page.waitForTimeout(50);
    await page.getByRole("button", { name: "Ajuda" }).click();
    await esperaTitulo(page, PASSOS[0].fala.titulo);
    await page.waitForTimeout(50);
    r.push([como, await page.evaluate(() => [".guia-piggy", ".guia-balao"].map((s) => document.querySelector(s).style.transform)), erros]);
    await ctx.close();
  }
  assert.deepEqual(r, [["Pular", ["", ""], []], ["Esc", ["", ""], []]]);
});

// A entrada do Piggy (CSS `guia-entra`, 620 ms) por cima da mola: Bora tocado durante ela fazia o
// Piggy saltar ao destino e voltar quando ela acabava. Mede o Piggy a cada quadro: nenhum passo
// de quadro chega à metade do caminho todo.
test("Bora durante a entrada do Piggy: a mola sem salto (nenhum quadro anda metade do caminho)", async () => {
  const { ctx, page } = await abrir({ motion: "no-preference" });
  await page.getByRole("button", { name: "Bora", exact: true }).waitFor();
  const entrando = await page.evaluate(() => {
    const pg = document.querySelector(".guia-piggy");
    window.__q = [];
    const f = () => { const r = pg.getBoundingClientRect(); window.__q.push([r.left + r.width / 2, r.top + r.height / 2]); if (window.__q.length < 120) requestAnimationFrame(f); };
    f();
    return pg.getAnimations().some((a) => a instanceof CSSAnimation && a.playState === "running");
  });
  await page.getByRole("button", { name: "Bora", exact: true }).click();
  await page.waitForTimeout(2200);
  const q = await page.evaluate(() => window.__q);
  await ctx.close();
  const d = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);
  const passo = Math.max(...q.slice(1).map((p, i) => d(p, q[i]))), todo = d(q[0], q.at(-1));
  console.log(`# entrada × mola: maior passo ${Math.round(passo)} px de ${Math.round(todo)} px`);
  assert.ok(entrando, "o Bora não caiu dentro da entrada: o teste não mede nada");
  assert.ok(todo > 100 && passo < todo / 2, `salto de ${Math.round(passo)} px em ${Math.round(todo)} px`);
});

// Parado (reduce, nada mudando), o rAF do guia não reescreve o DOM: nem o path do véu, nem o
// `hidden` do anel (setAttribute com o mesmo valor também é escrita), nem estilo.
test("guia parado 2 s: zero escritas no path do véu, no hidden do anel e no estilo do Piggy, do balão, das faixas e do anel", async () => {
  const { ctx, page } = await abrir();
  await bora(page);
  await page.waitForTimeout(400);
  const n = await page.evaluate(() => new Promise((ok) => {
    const c = { d: 0, hidden: 0, style: 0 };
    const o = new MutationObserver((ms) => ms.forEach((m) => { if (m.target.matches(".guia-sombra path, .guia-piggy, .guia-balao, .guia-veu, .guia-anel")) c[m.attributeName]++; }));
    o.observe(document.body, { attributes: true, attributeFilter: ["d", "hidden", "style"], subtree: true });
    setTimeout(() => { o.disconnect(); ok(c); }, 2000);
  }));
  await ctx.close();
  assert.deepEqual(n, { d: 0, hidden: 0, style: 0 });
});
