// Guia do /painel (#728, PR B): a tela de parts/Guia.tsx — o Piggy e o balão no lugar desde o
// 1º quadro, o movimento só sem `reduce`, a posição longe dos alvos, a rolagem que não puxa a
// página de quem rolou, o teclado e a barra de baixo (6 itens; 5 abaixo de 360, folga ≥ 12 px).
// Com o véu (decisão do dono): só o alvo do passo e o balão recebem toque e Tab; o Saiu fica
// claro no passo do mês sem receber toque; o convite e a comemoração escurecem tudo.
// O fluxo e o servidor estão em dashboard_v2_guia.test.mjs.
import { test } from "node:test";
import assert from "node:assert/strict";
import { PAINEL, PROTOTIPO, RAIZ, servir } from "./_painel.mjs";
import { FAZER, PASSOS, abrir, acoes, bora, esperaTitulo, navegador } from "./_guia.mjs";

const browser = navegador();

// Com `reduce`, o `*` do base.css ganha 1ms de transição em `all`: o left/top gravado pelo
// JS virava transição a partir de 0 e o 1º quadro pintava o Piggy e o balão no canto
// (0,0). Mede cada elemento no instante em que entra (o Piggy remonta a cada passo).
test("Piggy e balão entram já no lugar: o left/top calculado é o gravado, sem quadro no canto (o véu e o anel também)", async () => {
  for (const [width, height] of [[1280, 800], [375, 812]]) for (const motion of ["reduce", "no-preference"]) {
    const { ctx, page, s } = await abrir({ width, height, motion, guia: "em_andamento" });
    Object.assign(s.g.passos[0], { disponivel: true, motivo: null }); s.g.passos[1].feito = false;
    await page.evaluate(() => {
      window.__entrou = [];
      new MutationObserver(() => document.querySelectorAll(".guia-piggy, .guia-balao").forEach((e) => {
        if (e.__medido) return;
        e.__medido = 1;
        const cs = getComputedStyle(e);
        window.__entrou.push({ el: e.className, gravado: [e.style.left, e.style.top].map(parseFloat), calculado: [cs.left, cs.top].map(parseFloat) });
      })).observe(document.body, { childList: true, subtree: true });
      // O véu e o anel não remontam: valem as regravações de cada quadro (furo que muda de lugar).
      window.__moveu = [];
      new MutationObserver((ms) => ms.forEach(({ target: e }) => {
        if (!e.matches?.(".guia-veu, .guia-anel")) return;
        const cs = getComputedStyle(e), g = [e.style.left, e.style.top].map(parseFloat), c = [cs.left, cs.top].map(parseFloat);
        if (g.some((v, i) => !(Math.abs(v - c[i]) <= 1))) window.__moveu.push({ el: e.className, g, c });
      })).observe(document.body, { attributes: true, attributeFilter: ["style"], subtree: true });
    });
    await page.getByRole("button", { name: "Ajuda" }).click();
    await esperaTitulo(page, PASSOS[0].fala.titulo);
    await FAZER["mes.trocado"](page);
    await esperaTitulo(page, PASSOS[1].fala.titulo);
    const [entrou, moveu] = await page.evaluate(() => [window.__entrou, window.__moveu]);
    await ctx.close();
    const caso = `${width}x${height} ${motion}`;
    assert.ok(entrou.filter((x) => x.el === "guia-piggy").length >= 2, `${caso}: ${JSON.stringify(entrou)}`); // um Piggy por passo
    // Folga de 1px: o calculado vem em unidade de layout (318.683 → 318.682); o canto erra centenas.
    assert.deepEqual(entrou.filter((x) => x.gravado.some((v, i) => !(Math.abs(v - x.calculado[i]) <= 1))), [], caso);
    assert.deepEqual(moveu.slice(0, 3), [], caso);
  }
});

test("movimento: com `reduce` o Piggy e o anel ficam parados; sem a preferência, o Piggy entra pulando e o anel pulsa", async () => {
  const nomes = [];
  for (const motion of ["reduce", "no-preference"]) {
    const { ctx, page } = await abrir({ motion });
    await page.locator(".guia-piggy").waitFor();
    const piggy = await page.locator(".guia-piggy").evaluate((e) => getComputedStyle(e).animationName);
    await bora(page);
    nomes.push([piggy, await page.locator(".guia-anel").evaluate((e) => getComputedStyle(e).animationName)]);
    await ctx.close();
  }
  assert.deepEqual(nomes, [["none", "none"], ["guia-entra", "guia-pulso"]]);
});

test("barra de baixo: 6 itens a partir de 360; abaixo, a Ajuda sai e ficam 5; folga ≥ 12 px do maior rótulo de 320 a 375", async () => {
  const r = {};
  for (const width of [320, 340, 359, 360, 375]) {
    const { ctx, page } = await abrir({ width, height: 812, guia: "concluido" });
    r[width] = await page.evaluate(() => {
      const itens = [...document.querySelectorAll(".tabbar a, .tabbar button")].filter((e) => e.getClientRects().length);
      const folgas = itens.map((e) => { const s = e.querySelector("span"); const g = document.createRange(); g.selectNodeContents(s); return e.getBoundingClientRect().width - g.getBoundingClientRect().width; });
      const ajuda = document.querySelector(".tabbar button");
      return { n: itens.length, folga: Math.round(Math.min(...folgas) * 10) / 10, ajuda: ajuda.getClientRects().length, nome: ajuda.getAttribute("aria-label"), cabe: document.querySelector(".tabbar").scrollWidth <= innerWidth };
    });
    await ctx.close();
  }
  console.log("# barra de baixo:", JSON.stringify(r));
  for (const [w, x] of Object.entries(r)) {
    const larga = Number(w) >= 360;
    assert.equal(x.n, larga ? 6 : 5, w); assert.ok(x.folga >= 12, `${w}: folga ${x.folga}`); assert.equal(x.nome, "Ajuda"); assert.ok(x.cabe, w);
    assert.equal(x.ajuda > 0, larga, w);
  }
});

// Posição, em cada passo (o guia leva até a tela dele), da tela larga à de 320:
//   (a) o balão inteiro na tela;
//   (b) o balão não intercepta o toque no alvo do passo nem no que ele deixa à vista (a seta
//       do mês e o Saiu; a linha de categoria; o chip) nem na navegação (menu, abas):
//       `elementFromPoint` no centro e área de interseção, que pega a linha coberta só na
//       ponta (o centro da linha larga fica livre e o nome dela, não);
//   (c) o Piggy encosta 8 px na quina de cima ou de baixo da mira (o Saiu no passo do mês,
//       que tem o alvo na barra de cima; o próprio alvo nos outros).
const ALVOS = {
  "mes.trocado": ['[data-guia="mes.trocar"]', '[data-guia="resumo.saiu"]'],
  "categoria.aberta": ['[data-guia="categorias.item"]'],
  "piggy.perguntou": ['[data-guia="piggy.chip"]'],
};
const MIRA = { "mes.trocado": "resumo.saiu", "categoria.aberta": "categorias.item", "piggy.perguntou": "piggy.chip" };
const NAV = [".rail a", ".rail button", ".tabbar a", ".tabbar button"];
for (const [width, height] of [[1280, 800], [1024, 768], [900, 600], [700, 600], [375, 812], [320, 640]]) {
  test(`posição ${width}×${height}: balão na tela, fora dos alvos do passo e da navegação; Piggy na âncora`, async () => {
    const { ctx, page, s } = await abrir({ width, height });
    await bora(page);
    const medidas = [];
    const medir = async (p, fase, ancora) => {
      await page.waitForTimeout(300);
      medidas.push([p.id, fase, await page.evaluate(([sels, ancora]) => {
        const b = document.querySelector(".guia-balao"), bb = b.getBoundingClientRect();
        const pg = window.caixaDoPiggy();
        const a = [...document.querySelectorAll(ancora)].find((e) => e.getClientRects().length).getBoundingClientRect();
        const cobertos = [];
        let alvos = 0;
        for (const e of document.querySelectorAll(sels.join(","))) {
          const r = e.getBoundingClientRect();
          const x = (r.left + r.right) / 2, y = (r.top + r.bottom) / 2;
          if (!e.getClientRects().length || x < 0 || y < 0 || x > innerWidth || y > innerHeight) continue;
          alvos++;
          const area = Math.max(0, Math.min(r.right, bb.right) - Math.max(r.left, bb.left)) * Math.max(0, Math.min(r.bottom, bb.bottom) - Math.max(r.top, bb.top));
          if (b.contains(document.elementFromPoint(x, y)) || area > 0) cobertos.push(`${e.className || e.tagName} ${Math.round(area)}px²`);
        }
        const fora = bb.left < 0 || bb.top < 0 || bb.right > innerWidth || bb.bottom > innerHeight;
        const cola = [Math.round(pg.bottom - a.top), Math.round(a.bottom - pg.top)];
        const encosta = pg.left < a.right && pg.right > a.left && cola.includes(8);
        return { balao: [bb.left, bb.top, bb.right, bb.bottom].map(Math.round), alvos, cobertos, fora, cola, encosta };
      }, [[...ALVOS[p.acao], `[data-guia="nav.${p.tela}"]`, ...NAV], ancora])]);
    };
    for (const [i, p] of PASSOS.entries()) {
      if (i) await esperaTitulo(page, p.fala.titulo);
      await page.locator(ALVOS[p.acao][0]).first().waitFor();
      await medir(p, "alvo", `[data-guia="${MIRA[p.acao]}"]`);
      await FAZER[p.acao](page);
    }
    await esperaTitulo(page, "Fechou!");
    await ctx.close();
    console.log(`# posição ${width}×${height}:`, JSON.stringify(medidas));
    assert.deepEqual(medidas.filter(([, , m]) => m.fora || m.cobertos.length || !m.encosta), []);
    assert.equal(acoes(s).length, 4);
  });
}

// A altura da página muda com o passo aberto (o refetch do SSE, lib/eventos.ts): sem a pessoa
// ter rolado, o guia volta para a âncora; depois de ela rolar com a roda, não a puxa de volta.
// `overflow-anchor: none` desliga a âncora de rolagem do Chromium, que senão compensaria o
// bloco injetado sozinha e o caso sem rolagem passaria sem o guia fazer nada.
test("altura muda com o passo aberto: re-rola até a âncora só se a pessoa não rolou", async () => {
  const r = {};
  for (const roda of [false, true]) {
    const { ctx, page } = await abrir({ width: 1024, height: 600 });
    await bora(page);
    await page.waitForTimeout(300);
    await page.evaluate(() => { document.documentElement.style.overflowAnchor = "none"; });
    if (roda) { await page.mouse.move(600, 400); await page.mouse.wheel(0, 3000); await page.waitForTimeout(300); }
    const antes = await page.evaluate(() => scrollY);
    await page.evaluate(() => {
      const d = document.createElement("div"); d.style.height = "2000px";
      document.querySelector("#main").prepend(d); // no topo da página, acima de todo bloco
    });
    await page.waitForTimeout(400);
    r[roda ? "rolou" : "parada"] = await page.evaluate((antes) => {
      const a = document.querySelector('[data-guia="resumo.saiu"]').getBoundingClientRect();
      return { antes, depois: scrollY, naTela: a.top >= 0 && a.bottom <= innerHeight };
    }, antes);
    await ctx.close();
  }
  console.log("# altura muda:", JSON.stringify(r));
  assert.equal(r.parada.naTela, true); // o layout que se mexe continua defendido
  assert.ok(r.rolou.antes > 0, "a roda não rolou a página");
  assert.ok(Math.abs(r.rolou.depois - r.rolou.antes) < 100, "voltou para a âncora"); // a rolagem da pessoa ficou
  assert.equal(r.rolou.naTela, false);
});

test("teclado: do topo da página, o [Bora] do convite chega em poucos Tabs", async () => {
  const { ctx, page } = await abrir();
  await page.getByRole("button", { name: "Bora", exact: true }).waitFor();
  let tabs = 0;
  while (tabs < 150 && (await page.evaluate(() => document.activeElement?.textContent)) !== "Bora") { await page.keyboard.press("Tab"); tabs++; }
  await ctx.close();
  console.log(`# Tabs até o [Bora]: ${tabs}`);
  assert.ok(tabs <= 3, `${tabs} Tabs`);
});

test("barra de baixo do protótipo (sem Ajuda): 5 colunas e o Piggy no centro; no /painel, 5 em 320 (a Ajuda sai) e 6 em 375", async () => {
  const r = [];
  for (const [nome, url, raiz] of [["protótipo", PROTOTIPO, RAIZ], ["painel", PAINEL, undefined]]) {
    for (const width of [320, 375]) {
      const ctx = await browser().newContext({ viewport: { width, height: 812 }, reducedMotion: "reduce" });
      await servir(ctx, raiz);
      await ctx.addCookies([{ name: "csrf_token", value: "tok-123", url: "http://127.0.0.1:1" }]);
      const page = await ctx.newPage();
      await page.goto(`${url}#/`);
      await page.locator("#page-title").waitFor();
      r.push([nome, width, await page.evaluate(() => {
        const bar = document.querySelector(".tabbar");
        const pig = bar.querySelector('[data-tab="piggy"]').getBoundingClientRect();
        return [getComputedStyle(bar).gridTemplateColumns.split(" ").length, Math.round(pig.left + pig.width / 2 - innerWidth / 2) || 0]; // || 0: -0.4 arredonda para -0
      })]);
      await ctx.close();
    }
  }
  console.log("# barra de baixo [colunas, Piggy − centro px]:", JSON.stringify(r));
  assert.deepEqual(r.filter(([n]) => n === "protótipo").map(([, , [c, d]]) => [c, d]), [[5, 0], [5, 0]]);
  assert.deepEqual(r.filter(([n]) => n === "painel").map(([, w, [c, d]]) => w === 320 ? [c, d] : c), [[5, 0], 6]);
});

// O que está no ponto central de cada seletor (o 1º visível): o próprio elemento, ou o véu.
const noCentro = (page, sels) => page.evaluate((sels) => sels.map((sel) => {
  const e = [...document.querySelectorAll(sel)].find((x) => x.getClientRects().length);
  if (!e) return `${sel}: ausente`;
  const r = e.getBoundingClientRect(), x = (r.left + r.right) / 2, y = (r.top + r.bottom) / 2;
  if (x < 0 || y < 0 || x > innerWidth || y > innerHeight) return `${sel}: fora da tela`;
  const t = document.elementFromPoint(x, y);
  return e.contains(t) ? "alvo" : t?.classList.contains("guia-veu") ? "véu" : `${sel}: ${t?.className || t?.tagName}`;
}), sels);
// O escuro (o path do véu) em cada centro: true = escuro, false = furo.
const escuro = (page, sels) => page.evaluate((sels) => sels.map((sel) => {
  const r = [...document.querySelectorAll(sel)].find((x) => x.getClientRects().length).getBoundingClientRect();
  const x = (r.left + r.right) / 2, y = (r.top + r.bottom) / 2;
  return x < 0 || y < 0 || x > innerWidth || y > innerHeight ? `${sel}: fora da tela` : document.querySelector(".guia-sombra path").isPointInFill(new DOMPoint(x, y));
}), sels);
const anelEm = (page, sel) => page.evaluate((sel) => {
  const a = document.querySelector(".guia-anel"), r = a.getBoundingClientRect(), e = document.querySelector(sel).getBoundingClientRect();
  return a.hidden ? null : [e.left - r.left, e.top - r.top, r.right - e.right, r.bottom - e.bottom].map(Math.round);
}, sel);
const SAIU = '[data-guia="resumo.saiu"]';

for (const [width, height] of [[1280, 800], [375, 812]]) {
  test(`véu ${width}×${height}: convite escuro e sem furo; no passo, furo e anel no alvo exato, o Saiu claro e sem toque; comemoração sem furo`, async () => {
    const { ctx, page } = await abrir({ width, height });
    const nav = width > 760 ? ".rail" : ".tabbar";
    await page.getByRole("button", { name: "Bora", exact: true }).waitFor();
    await page.waitForTimeout(200);
    const convite = [await noCentro(page, [".topbar .month-title", `${nav} [data-guia="nav.gastos"]`]), await escuro(page, [".topbar .month-title", '[data-guia="mes.trocar"]'])];
    await bora(page);
    await page.waitForTimeout(300);
    const alvo = '[data-guia="mes.trocar"]';
    const passo = [await noCentro(page, [alvo, SAIU, `${nav} [data-guia="nav.gastos"]`, ".topbar .cmd-trigger, .topbar a.btn"]), await escuro(page, [alvo, SAIU, ".topbar .month-title"]), await anelEm(page, alvo)];
    // O clique de verdade no ponto (o `locator.click` rola a página para achar a seta presa no
    // topo, o que tiraria o Saiu da tela; a pessoa não rola ao clicar).
    const c = await page.locator(alvo).evaluate((e) => { const r = e.getBoundingClientRect(); return [(r.left + r.right) / 2, (r.top + r.bottom) / 2]; });
    await page.mouse.click(...c);
    await page.locator(".guia-balao").getByText("Vem comigo").waitFor();
    const festa = [await noCentro(page, [alvo]), await escuro(page, [alvo, SAIU]), await anelEm(page, alvo)];
    await esperaTitulo(page, PASSOS[1].fala.titulo);
    await page.locator('[data-guia="categorias.item"]').waitFor();
    await page.waitForTimeout(300);
    const passo2 = [await noCentro(page, ['[data-guia="categorias.item"]']), await anelEm(page, '[data-guia="categorias.item"]')];
    await ctx.close();
    console.log(`# véu ${width}×${height}:`, JSON.stringify({ convite, passo, festa, passo2 }));
    assert.deepEqual(convite, [["véu", "véu"], [true, true]]);
    assert.deepEqual(passo.slice(0, 2), [["alvo", "véu", "véu", "véu"], [false, false, true]]);
    assert.ok(passo[2].every((d) => d >= 2 && d <= 8), `anel: ${passo[2]}`); // o anel envolve o alvo com folga de 2 a 8 px
    assert.deepEqual(festa, [["véu"], [true, true], null]);
    assert.equal(passo2[0][0], "alvo");
    assert.ok(passo2[1].every((d) => d >= 2 && d <= 8), `anel: ${passo2[1]}`);
  });
}

// O toque fora do alvo não faz nada: nem navega, nem grava, nem abre o Cmd-K ou o Organizar,
// nem pergunta pelo chip que não é o do passo. `mouse.click` no ponto: o clique de verdade, que
// o véu intercepta (o `locator.click` do Playwright recusaria clicar num elemento coberto).
for (const semDialog of [false, true]) {
  test(`clique fora do alvo bloqueado (${semDialog ? "Safari 14" : "nativo"}): menu, Organizar, busca e outro chip não respondem; o alvo responde`, async () => {
    const { ctx, page, s } = await abrir({ semDialog });
    await page.getByRole("button", { name: "Bora", exact: true }).waitFor();
    await page.waitForTimeout(300);
    const clicar = async (sels) => {
      const feitos = [];
      for (const sel of sels) {
        const c = await page.evaluate((sel) => {
          const e = [...document.querySelectorAll(sel)].find((x) => x.getClientRects().length);
          const r = e?.getBoundingClientRect();
          return r && r.top >= 0 && r.bottom <= innerHeight ? [(r.left + r.right) / 2, (r.top + r.bottom) / 2] : null;
        }, sel);
        if (!c) continue;
        await page.mouse.click(...c);
        feitos.push(sel);
      }
      await page.waitForTimeout(400);
      return feitos;
    };
    const estado = async () => [await page.evaluate(() => location.hash), acoes(s), await page.locator(".cmdk[open]").count(), await page.locator(".board[data-editing]").count(), await page.locator(".msg-user").count()];
    const fora = ['.rail [data-guia="nav.gastos"]', ".board button[aria-pressed]", ".topbar .cmd-trigger", ".topbar a.btn"];
    const noConvite = [await clicar(fora), await estado()];
    await bora(page);
    await page.waitForTimeout(300);
    const noPasso = [await clicar([...fora, SAIU]), await estado()];
    await FAZER["mes.trocado"](page);
    await esperaTitulo(page, PASSOS[1].fala.titulo);
    await FAZER["categoria.aberta"](page);
    await esperaTitulo(page, PASSOS[2].fala.titulo);
    await page.locator('[data-guia="piggy.chip"]').waitFor();
    await page.waitForTimeout(300);
    const outroChip = await clicar(['.chat-empty .chip:not([data-guia])']);
    const passo3 = await estado();
    await FAZER["piggy.perguntou"](page);
    await esperaTitulo(page, "Fechou!");
    await ctx.close();
    assert.deepEqual(noConvite, [fora, ["#/", ["visto"], 0, 0, 0]]);
    // No passo a página rolou até o Saiu: o Organizar pode ter saído da tela.
    assert.deepEqual(noPasso[0].filter((x) => x !== fora[1]), [fora[0], fora[2], fora[3], SAIU]);
    assert.deepEqual(noPasso[1], ["#/", ["visto"], 0, 0, 0]);
    assert.equal(outroChip.length, 1);
    assert.deepEqual(passo3, ["#/piggy", ["visto", "feito:resumo.saiu", "feito:gastos.categoria"], 0, 0, 0]);
    assert.deepEqual(acoes(s).at(-1), "feito:piggy.pergunta");
  });
}

// Teclado com o véu: o Tab circula entre o balão e o alvo (12 Tabs não saem dali); depois da
// ação o foco fica no controle usado; a troca de página que o guia faz devolve o foco ao
// título do balão (o App.tsx foca o #page-title); fechar devolve à página.
for (const semDialog of [false, true]) {
  test(`foco com o véu (${semDialog ? "Safari 14" : "nativo"}): Tab preso no balão e no alvo; pós-ação no controle; pós-navegação no balão; ao fechar, a página`, async () => {
    const { ctx, page } = await abrir({ semDialog });
    await bora(page);
    const onde = () => page.evaluate(() => {
      const a = document.activeElement;
      return a.closest(".guia-balao") ? `balão:${a.textContent.trim().slice(0, 12)}` : a.closest('[data-guia="mes.trocar"]') ? "alvo" : `fora:${a.id || a.className || a.tagName}`;
    });
    const tabs = [];
    for (let i = 0; i < 12; i++) { await page.keyboard.press(i % 4 === 3 ? "Shift+Tab" : "Tab"); tabs.push(await onde()); }
    await FAZER["mes.trocado"](page);
    const posAcao = await page.evaluate(() => document.activeElement?.getAttribute("aria-label"));
    await esperaTitulo(page, PASSOS[1].fala.titulo);
    await page.locator('[data-guia="categorias.item"]').waitFor();
    await page.waitForTimeout(300);
    const posNavegacao = await page.evaluate(() => document.activeElement?.id);
    await page.keyboard.press("Escape");
    await page.waitForTimeout(300);
    const fechado = await page.evaluate(() => document.activeElement?.id);
    await ctx.close();
    assert.deepEqual(tabs.filter((t) => t.startsWith("fora")), [], JSON.stringify(tabs));
    assert.ok(tabs.includes("alvo") && tabs.some((t) => t.startsWith("balão")), JSON.stringify(tabs));
    assert.equal(posAcao, "Mês anterior");
    assert.equal(posNavegacao, "guia-titulo");
    assert.equal(fechado, "page-title");
  });
}
