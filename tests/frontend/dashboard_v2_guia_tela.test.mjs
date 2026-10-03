// Guia do /painel (#728, PR B): a tela de parts/Guia.tsx — o Piggy e o balão no lugar desde o
// 1º quadro, o movimento só sem `reduce`, a posição longe dos alvos, a rolagem que não puxa a
// página de quem rolou, o teclado e a barra de baixo com 6 itens (folga ≥ 12 px de 320 a 375).
// O fluxo e o servidor estão em dashboard_v2_guia.test.mjs.
import { test } from "node:test";
import assert from "node:assert/strict";
import { PAINEL, PROTOTIPO, RAIZ, servir } from "./_painel.mjs";
import { FAZER, PASSOS, abrir, acoes, bora, esperaTitulo, navegador } from "./_guia.mjs";

const browser = navegador();

// Com `reduce`, o `*` do base.css ganha 1ms de transição em `all`: o left/top gravado pelo
// JS virava transição a partir de 0 e o 1º quadro pintava o Piggy e o balão no canto
// (0,0). Mede cada elemento no instante em que entra (o Piggy remonta a cada passo).
test("Piggy e balão entram já no lugar: o left/top calculado é o gravado, sem quadro no canto", async () => {
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
    });
    await page.getByRole("button", { name: "Ajuda" }).click();
    await esperaTitulo(page, PASSOS[0].fala.titulo);
    await FAZER["mes.trocado"](page);
    await esperaTitulo(page, PASSOS[1].fala.titulo);
    const entrou = await page.evaluate(() => window.__entrou);
    await ctx.close();
    const caso = `${width}x${height} ${motion}`;
    assert.ok(entrou.filter((x) => x.el === "guia-piggy").length >= 2, `${caso}: ${JSON.stringify(entrou)}`); // um Piggy por passo
    // Folga de 1px: o calculado vem em unidade de layout (318.683 → 318.682); o canto erra centenas.
    assert.deepEqual(entrou.filter((x) => x.gravado.some((v, i) => !(Math.abs(v - x.calculado[i]) <= 1))), [], caso);
  }
});

test("movimento: com `reduce` o Piggy fica parado; sem a preferência, entra pulando", async () => {
  const nomes = [];
  for (const motion of ["reduce", "no-preference"]) {
    const { ctx, page } = await abrir({ motion });
    await page.locator(".guia-piggy").waitFor();
    nomes.push(await page.locator(".guia-piggy").evaluate((e) => getComputedStyle(e).animationName));
    await ctx.close();
  }
  assert.deepEqual(nomes, ["none", "guia-entra"]);
});

test("barra de baixo: 6 itens; folga ≥ 12 px do maior rótulo em 320, 340, 360 e 375", async () => {
  const r = {};
  for (const width of [320, 340, 359, 360, 375]) {
    const { ctx, page } = await abrir({ width, height: 812, guia: "concluido" });
    r[width] = await page.evaluate(() => {
      const itens = [...document.querySelectorAll(".tabbar a, .tabbar button")];
      const folgas = itens.map((e) => { const s = e.querySelector("span"); if (!s?.getClientRects().length) return null; const g = document.createRange(); g.selectNodeContents(s); return e.getBoundingClientRect().width - g.getBoundingClientRect().width; }).filter((f) => f != null);
      const ajuda = document.querySelector(".tabbar button");
      return { n: itens.length, folga: Math.round(Math.min(...folgas) * 10) / 10, rotuloAjuda: !!ajuda.querySelector("span").getClientRects().length, nome: ajuda.getAttribute("aria-label"), cabe: document.querySelector(".tabbar").scrollWidth <= innerWidth };
    });
    await ctx.close();
  }
  console.log("# barra de baixo:", JSON.stringify(r));
  for (const [w, x] of Object.entries(r)) {
    assert.equal(x.n, 6, w); assert.ok(x.folga >= 12, `${w}: folga ${x.folga}`); assert.equal(x.nome, "Ajuda"); assert.ok(x.cabe, w);
    assert.equal(x.rotuloAjuda, Number(w) >= 360, w);
  }
});

// Posição, em cada passo (e na fase em que ele aponta para a aba), da tela larga à de 320:
//   (a) o balão inteiro na tela;
//   (b) o balão não intercepta o toque em nenhum alvo da ação visível (o seletor de mês e o
//       Saiu; cada linha de categoria; a barra de conversa) nem na navegação (menu, abas):
//       `elementFromPoint` no centro e área de interseção, que pega a linha coberta só na
//       ponta (o centro da linha larga fica livre e o nome dela, não);
//   (c) o Piggy encosta 8 px na quina de cima ou de baixo da âncora (ou da aba).
const ALVOS = {
  "mes.trocado": ['[data-guia="mes.seletor"] button', '[data-guia="resumo.saiu"]'],
  "categoria.aberta": ['[data-guia="categorias.lista"] .cat'],
  "piggy.perguntou": ['[data-guia="piggy.pergunta"] input', '[data-guia="piggy.pergunta"] button'],
};
const NAV = [".rail a", ".rail button", ".tabbar a", ".tabbar button"];
for (const [width, height] of [[1280, 800], [1024, 768], [900, 600], [700, 600], [375, 812], [320, 640]]) {
  test(`posição ${width}×${height}: balão na tela, fora dos alvos do passo e da navegação; Piggy na âncora`, async () => {
    const { ctx, page, s } = await abrir({ width, height });
    await bora(page);
    const nav = width > 760 ? ".rail" : ".tabbar";
    const medidas = [];
    const medir = async (p, fase, ancora) => {
      await page.waitForTimeout(300);
      medidas.push([p.id, fase, await page.evaluate(([sels, ancora]) => {
        const b = document.querySelector(".guia-balao"), bb = b.getBoundingClientRect();
        const pg = document.querySelector(".guia-piggy").getBoundingClientRect();
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
      const naTela = p.tela === "resumo" || (p.tela === "piggy" && width > 760);
      if (!naTela) {
        await medir(p, "aba", `${nav} [data-guia="nav.${p.tela}"]`);
        await page.locator(`${nav} [data-guia="nav.${p.tela}"]`).click();
        await page.locator(`[data-guia="${p.ancora}"]`).first().waitFor();
      }
      await medir(p, "âncora", `[data-guia="${p.ancora}"]`);
      await (p.acao === "categoria.aberta" ? page.locator('[data-guia="categorias.lista"] .cat').nth(1).click() : FAZER[p.acao](page));
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

test("barra de baixo do protótipo (sem Ajuda): 5 colunas e o Piggy no centro; no /painel, 6", async () => {
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
  assert.deepEqual(r.filter(([n]) => n === "painel").map(([, , [c]]) => c), [6, 6]);
});
