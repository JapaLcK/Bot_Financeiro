// Tooltip do /painel (parts/Tip.tsx): com `reduce`, o `*` do base.css dá 1ms de transição em
// `all`; o transform que o JS grava logo depois de medir virava transição a partir do default
// (identidade) e um quadro saía com o tooltip no canto (0,0). Mesmo mecanismo do guia (PR #789,
// dashboard_v2_guia_tela.test.mjs), aqui com transform em vez de left/top. Mede no instante da
// inserção (o .tip perde o `hidden`) e em cada regravação de style, nos dois viewports (desktop
// e mobile) × os dois modos de movimento. O controle positivo fecha o grupo: ~100ms depois o
// tooltip está visível, com título e perto do ponto do mouse (não no canto), nos dois modos.
import { before, after, test } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { PAINEL, servir, exigeArtefatoEmDia } from "./_painel.mjs";

let browser;
before(async () => {
  exigeArtefatoEmDia();
  browser = await chromium.launch();
});
after(() => browser?.close());

// O gravado é a string do JS (translate(Xpx, Ypx)); o calculado vem como
// matrix(a,b,c,d,tx,ty) ou "none" (identidade = 0,0).
const xy = (t) => {
  if (!t || t === "none") return [0, 0];
  const m = t.match(/matrix\((.+)\)/);
  if (m) { const p = m[1].split(",").map(Number); return [p[4], p[5]]; }
  const tr = t.match(/translate\((-?[\d.]+)px(?:,\s*(-?[\d.]+)px)?\)/);
  return [Number(tr[1]), Number(tr[2] ?? 0)];
};
// Folga de 1px: o calculado vem em unidade de layout; o canto erra centenas.
const diverge = (r) => {
  const g = xy(r.gravado), c = xy(r.calculado);
  return Math.abs(g[0] - c[0]) > 1 || Math.abs(g[1] - c[1]) > 1;
};

test("tooltip entra já no lugar: o transform calculado é o gravado, sem quadro no canto", async () => {
  const falhas = [];
  for (const motion of ["reduce", "no-preference"]) for (const [width, height] of [[1280, 800], [375, 812]]) {
    const caso = `${width}x${height} ${motion}`;
    const ctx = await browser.newContext({ viewport: { width, height }, reducedMotion: motion });
    // Antes da página carregar: registra, na perda do `hidden` (inserção) e em cada regravação
    // de style, o transform gravado pelo JS e o calculado no mesmo instante.
    await ctx.addInitScript(() => {
      window.__entrou = [];
      window.__moveu = [];
      const medir = (e) => ({ gravado: e.style.transform, calculado: getComputedStyle(e).transform });
      new MutationObserver((ms) => ms.forEach(({ target: e, attributeName: a }) => {
        if (!e.matches?.(".tip")) return;
        if (a === "hidden" && !e.hidden) window.__entrou.push(medir(e));
        if (a === "style") window.__moveu.push(medir(e));
      })).observe(document, { attributes: true, attributeFilter: ["hidden", "style"], subtree: true });
    });
    await servir(ctx);
    await ctx.addCookies([{ name: "csrf_token", value: "tok-123", url: "http://127.0.0.1:1" }]);
    const page = await ctx.newPage();
    await page.goto(`${PAINEL}#/`);
    await page.locator(".cal-day").first().waitFor();
    // O onPointerMove do dia chama o showTip (widgets/Calendar.tsx); o 2º dia exercita a
    // regravação. O calendário fica abaixo da dobra no viewport estreito: o hover rola até
    // o dia e move o mouse ao centro dele.
    const dias = page.locator(".cal-day:not([disabled])");
    await dias.first().hover();
    await dias.nth(1).hover();
    // Em `no-preference` o html tem scroll-behavior: smooth (base.css): a rolagem do hover
    // segue animada no compositor depois de o hover() retornar e, ao terminar, o Chromium
    // re-faz hit-test do ponteiro parado e pode disparar um pointerleave tardio no dia
    // (Calendar.tsx chama hideTip), que escondia o .tip antes da medição — a falha flaky
    // do CI, só no 375x812 no-preference. Espera a rolagem assentar (scrollY exato estável
    // por 2 amostras) e repõe o hover com mouse.move direto ao centro do dia: o
    // locator.hover() chamaria scrollIntoView de novo e reiniciaria a rolagem suave. Em
    // laço (máx. 5x): se um hit-test tardio do fim da animação esconder o tooltip já
    // depois do re-hover, repete. Em `reduce` o scroll é auto e uma passada basta.
    await page.waitForFunction(() => {
      const w = window;
      if (w.__y === w.scrollY) return true;
      w.__y = w.scrollY;
      return false;
    }, null, { polling: 50 });
    let b2;
    for (let i = 0; i < 5; i++) {
      b2 = await dias.nth(1).boundingBox();
      await page.mouse.move(b2.x + b2.width / 2, b2.y + b2.height / 2);
      await page.waitForTimeout(120);
      if (await page.evaluate(() => !document.querySelector(".tip").hidden)) break;
    }
    const [mx, my] = [b2.x + b2.width / 2, b2.y + b2.height / 2];
    await page.waitForTimeout(100);
    const [entrou, moveu, depois] = await page.evaluate(() => {
      const e = document.querySelector(".tip");
      const r = e.getBoundingClientRect();
      return [window.__entrou, window.__moveu, {
        escondido: e.hidden,
        titulo: e.querySelector(".tip-title")?.textContent ?? "",
        left: r.left, top: r.top,
      }];
    });
    await ctx.close();
    console.log(`# ${caso}: entrou`, JSON.stringify(entrou), "moveu", JSON.stringify(moveu));
    // O tooltip apareceu de verdade (sem isto, o grupo passaria no vazio).
    assert.ok(entrou.length >= 1, `${caso}: o .tip nunca apareceu`);
    assert.ok(moveu.length >= 1, `${caso}: o transform nunca foi gravado`);
    const ruins = [...entrou, ...moveu].filter(diverge);
    if (ruins.length) falhas.push([caso, ruins]);
    // Controle positivo: o caminho legítimo segue — visível, com título, perto do mouse.
    assert.equal(depois.escondido, false, caso);
    assert.ok(depois.titulo.length > 0, `${caso}: .tip-title vazio`);
    assert.ok(depois.left > 0 && depois.top > 0, `${caso}: no canto (${depois.left},${depois.top})`);
    assert.ok(Math.hypot(depois.left - mx, depois.top - my) < 400, `${caso}: longe do mouse (${depois.left},${depois.top} vs ${mx},${my})`);
  }
  assert.deepEqual(falhas, []);
});
