/**
 * /painel, Etapa 1 PR C2: os acabamentos do Resumo que o dono pediu depois do PR C.
 *
 *   · "Para onde vai" não vaza da célula: no Resumo a lista rola por dentro e nenhuma categoria
 *     some, alcançável por Tab; no celular (altura livre) e na /gastos nada rola nem muda;
 *   · o seletor de perfil tem 44px de alto (só ele: o .field global segue com 32px), sem quebrar a
 *     cabeça do painel nem rolar para o lado; a seta de abrir cada bloco tem 44px de alvo;
 *   · Entrou e Saiu com centavos também acima de R$ 999.999,99 e negativos, com hífen no sinal;
 *   · o "Criar senha" do 403 `password_required` tem 44px e foco visível.
 *
 * Rodar:  npm run test:frontend   (mudou webapp/src, rode `npm --prefix webapp run build`)
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { RESPOSTAS, abrirPainel, exigeArtefatoEmDia } from "./_painel.mjs";

let browser;
before(async () => { exigeArtefatoEmDia(); browser = await chromium.launch(); });
after(() => browser?.close());

const abrir = (opts) => abrirPainel(browser, opts);
const rolagem = async (page) => (await assentar(page), page.evaluate(() => document.scrollingElement.scrollWidth - document.scrollingElement.clientWidth));

// --- "Para onde vai" -----------------------------------------------------------------

// No Resumo a lista rola por dentro: nenhuma categoria some e o bloco não transborda. Para cada
// <li>: está no DOM, visível, e rolado até ele aparece inteiro dentro da lista. Espera as
// transições: com `reducedMotion` o base.css dá 1ms a todas, e a troca do @container (min-height)
// no meio do layout pega a lista no meio do `flex` (altura 0 por um quadro).
const assentar = (page) => page.evaluate(() => Promise.all(document.getAnimations().map((a) => a.finished.catch(() => {}))));
const categorias = async (page) => (await assentar(page), page.evaluate(() => {
  const art = document.querySelector("#w-categorias");
  const ul = art.querySelector(".cats");
  const lis = [...ul.children];
  const dentro = (li) => {
    const b = li.getBoundingClientRect(), u = ul.getBoundingClientRect();
    return !!li.offsetParent && b.height > 0 && b.top >= u.top - 0.5 && b.bottom <= u.bottom + 0.5;
  };
  const semRolar = lis.every(dentro);
  const vistas = lis.filter((li) => { li.scrollIntoView({ block: "nearest" }); return dentro(li); }).length;
  ul.scrollTop = 0;
  const lista = ul.scrollHeight - ul.clientHeight;
  return {
    bloco: art.scrollHeight - art.clientHeight,
    noDom: lis.length,
    vistas,
    rolaSoSePrecisa: (lista > 0) === !semRolar,
    lateral: ul.scrollWidth - ul.clientWidth + document.scrollingElement.scrollWidth - document.scrollingElement.clientWidth,
  };
}));

// Investir não tem o bloco. 808 e 712 dão a menor célula (3 colunas no limite).
const LARGURAS = [1440, 1300, 1100, 1000, 900, 808, 800, 760, 712];
for (const perfil of ["padrao", "economizar", "controlar", "dividas", "autonomo"]) {
  test(`Para onde vai, ${perfil}: as 8 categorias alcançáveis rolando a lista, o bloco sem transbordar, de 1440 a 712px`, async () => {
    const r = {};
    for (const width of LARGURAS) {
      const { ctx, page, ir } = await abrir({ width, perfil });
      await ir();
      r[width] = await categorias(page);
      await ctx.close();
    }
    const ok = { bloco: 0, noDom: 8, vistas: 8, rolaSoSePrecisa: true, lateral: 0 };
    assert.deepEqual(r, Object.fromEntries(LARGURAS.map((w) => [w, ok])));
  });
}

// A lista que rola é alcançável por Tab, com o anel de foco global, e diz o que é.
for (const width of [1440, 1100, 808]) {
  test(`${width}px: a lista de categorias recebe foco pelo Tab, com foco visível e rótulo`, async () => {
    const { ctx, page, ir } = await abrir({ width });
    await ir();
    await page.locator("#w-categorias .w-open").focus();
    await page.keyboard.press("Tab");
    const r = await page.evaluate(() => {
      const ul = document.querySelector("#w-categorias .cats");
      const cs = getComputedStyle(ul);
      return [ul === document.activeElement && ul.matches(":focus-visible"), cs.outlineStyle, parseFloat(cs.outlineWidth) >= 2, ul.getAttribute("aria-label"), ul.getAttribute("role")];
    });
    await ctx.close();
    assert.deepEqual(r, [true, "solid", true, "Categorias do mês", null]);
  });
}

// Onde a altura é livre ou sobra, a regra de rolagem não muda nada: as posições das categorias
// são as mesmas com ela desligada, nada rola, e a /gastos nem ganha a parada de Tab.
test("positivo: a 1000px, no celular (390) e na /gastos nada rola e as categorias ficam onde estavam", async () => {
  const r = [];
  for (const [width, rota] of [[1000, "/"], [390, "/"], [1440, "/gastos"]]) {
    const { ctx, page, ir } = await abrir({ width, espera: "#w-categorias" });
    await ir(rota);
    await assentar(page);
    r.push(await page.evaluate(() => {
      const ul = document.querySelector("#w-categorias .cats");
      const pos = () => [...ul.children].map((li) => { const b = li.getBoundingClientRect(); return [b.top, b.height, b.width].map(Math.round).join(); }).join("|");
      const com = pos();
      const off = document.head.appendChild(document.createElement("style"));
      off.textContent = ".cats { overflow-y: visible !important; min-height: auto !important; }";
      const sem = pos();
      off.remove();
      return [ul.children.length, ul.scrollHeight - ul.clientHeight, com === sem];
    }));
    r[r.length - 1].push(await page.locator("#w-categorias .cats").getAttribute("tabindex"));
    await ctx.close();
  }
  assert.deepEqual(r, [[8, 0, true, "0"], [8, 0, true, "0"], [8, 0, true, null]]);
});

// A legenda dá o total de todas as categorias (cada valor vem arredondado ao real, então a soma
// pode diferir em até meio real por categoria).
const reais = (t) => Number(t.replace(/\D/g, ""));
const LEGENDA = [1440, 1100, 808];
test("Para onde vai: o total da legenda é a soma das 8 categorias", async () => {
  const r = {};
  for (const width of LEGENDA) {
    const { ctx, page, ir } = await abrir({ width });
    await ir();
    const { lede, valores } = await page.evaluate(() => {
      const art = document.querySelector("#w-categorias");
      return { lede: art.querySelector(".w-lede").textContent, valores: [...art.querySelectorAll(".cats > li .cat-val")].map((v) => v.textContent) };
    });
    await ctx.close();
    const soma = valores.reduce((a, v) => a + reais(v), 0);
    r[width] = [valores.length, Math.abs(reais(lede.split("·")[0]) - soma) <= valores.length / 2];
  }
  assert.deepEqual(r, Object.fromEntries(LEGENDA.map((w) => [w, [8, true]])));
});

// --- Alvos de toque ------------------------------------------------------------------

for (const width of [1440, 1100, 760, 390, 375]) {
  test(`${width}px: seletor de perfil com 44px, cabeça do painel inteira, sem rolagem lateral; .field de fora segue com 32`, async () => {
    const { ctx, page, ir } = await abrir({ width });
    await ir();
    const r = await page.evaluate(() => {
      const sel = document.querySelector("#board-profile").getBoundingClientRect();
      const head = document.querySelector(".board-head").getBoundingClientRect();
      const fora = [...document.querySelectorAll(".board-head > *")].filter((e) => {
        const b = e.getBoundingClientRect();
        return b.left < head.left - 0.5 || b.right > head.right + 0.5;
      });
      return { altura: Math.round(sel.height), dentro: sel.right <= innerWidth, fora: fora.length };
    });
    const solto = await page.evaluate(() => {
      const f = document.createElement("input");
      f.className = "field";
      document.querySelector(".board").append(f);
      return Math.round(f.getBoundingClientRect().height);
    });
    r.rolagem = await rolagem(page);
    await ctx.close();
    assert.deepEqual([r, solto], [{ altura: 44, dentro: true, fora: 0, rolagem: 0 }, 32]);
  });
}

// O desenho da seta segue com 28px; o alvo (o ::after) cobre 44 × 44 em volta dela, sem mexer
// na altura da cabeça (comparada com o ::after desligado).
for (const width of [1440, 390]) {
  test(`${width}px: a seta de abrir o bloco responde ao toque em 44 × 44 e a cabeça segue com a altura de antes`, async () => {
    const { ctx, page, ir } = await abrir({ width });
    await ir();
    const r = await page.locator("#w-hero .w-open").evaluate((a) => {
      a.scrollIntoView({ block: "center" });
      const altura = () => a.closest(".w-head").getBoundingClientRect().height;
      const b = a.getBoundingClientRect();
      const cx = b.left + b.width / 2, cy = b.top + b.height / 2;
      const pontos = [[cx - 21.5, cy], [cx + 21.5, cy], [cx, cy - 21.5], [cx, cy + 21.5]];
      return {
        desenho: [Math.round(b.width), Math.round(b.height)],
        alvo: pontos.map(([x, y]) => document.elementFromPoint(x, y)?.closest(".w-open") === a),
        cabeca: (() => {
          const com = altura();
          const off = document.head.appendChild(document.createElement("style"));
          off.textContent = ".w-open::after { display: none !important; }";
          const sem = altura();
          off.remove();
          return com === sem;
        })(),
      };
    });
    await ctx.close();
    assert.deepEqual(r, { desenho: [28, 28], alvo: [true, true, true, true], cabeca: true });
  });
}

// --- Centavos nos extremos -----------------------------------------------------------

// Valor de um <number-flow>: os dígitos moram no shadow DOM (o mesmo leitor do dashboard_v2_resumo_real).
const numero = (loc) => loc.evaluate((v) => [...v.shadowRoot.querySelectorAll(".symbol__value, [part~=digit]")]
  .map((n) => (n.matches("[part~=digit]") ? n.style.getPropertyValue("--current").trim() : n.textContent)).join("").replace(/\u00a0/g, " "));

test("Entrou e Saiu com centavos acima de R$ 999.999,99 e negativos; o mês anterior também, com o mesmo hífen", async () => {
  const { ctx, page, erros, ir } = await abrir();
  const resumo = { ...RESPOSTAS.resumo_do_mes.exato, entrou: "1234567.89", saiu: "-12.34", anterior: { mes: "2026-09", entrou: "1000000.50", saiu: "-0.01" } };
  await ctx.route("**/api/v2/resumo-do-mes*", (r) => r.fulfill({ json: resumo }));
  await ir();
  const st = page.locator("#w-resumo .stat");
  await st.first().locator(".stat-value").waitFor();
  const r = [];
  for (const i of [0, 1]) r.push([await numero(st.nth(i).locator(".stat-value")), await st.nth(i).locator(".stat-delta").textContent()]);
  await ctx.close();
  assert.deepEqual(r, [["R$ 1.234.567,89", "em setembro: R$ 1.000.000,50"], ["-R$ 12,34", "em setembro: -R$ 0,01"]]);
  assert.deepEqual(erros, []);
});

// --- "Criar senha" -------------------------------------------------------------------

for (const width of [1440, 390]) {
  test(`${width}px: o "Criar senha" do 403 password_required tem 44px de alto e foco visível`, async () => {
    const { ctx, page, ir } = await abrir({ width });
    const e = RESPOSTAS.erros["403_password_required"];
    await ctx.route("**/api/v2/perfil", (r) => (r.request().method() === "PUT" ? r.fulfill({ status: e.status, json: e.body }) : r.fallback()));
    await ir();
    await page.selectOption("#board-profile", "investir");
    const link = page.locator(".board-aviso a");
    await link.waitFor();
    await page.locator("#board-profile").focus();
    for (let i = 0; i < 6 && !(await link.evaluate((a) => a === document.activeElement)); i++) await page.keyboard.press("Tab");
    const r = await link.evaluate((a) => [a === document.activeElement && a.matches(":focus-visible"), getComputedStyle(a).outlineStyle, Math.round(a.getBoundingClientRect().height), a.getAttribute("href")]);
    r.push(await rolagem(page));
    await ctx.close();
    assert.deepEqual(r, [true, "solid", 44, "/home", 0]);
  });
}

// --- Sem flash de rolagem lateral (#783) ----------------------------------------------

// A grade montava os blocos com `maxColumns` e os reposicionava na 1ª medida: o Motion deslizava cada
// um do lugar errado ao certo e o deslize passava da borda (108px a 760px, por ~200ms). Aqui NÃO se
// espera `assentar`: o que conta é a rolagem lateral de cada quadro desde a montagem.
for (const width of [1440, 1100, 900, 808, 760, 712, 390]) {
  test(`${width}px: nenhum quadro com rolagem lateral desde a montagem do painel`, async () => {
    const { ctx, page, ir } = await abrir({ width });
    await page.addInitScript(() => {
      window.__pior = 0;
      const tick = () => {
        const se = document.scrollingElement;
        if (se) window.__pior = Math.max(window.__pior, se.scrollWidth - se.clientWidth);
        requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    });
    await ir();
    await page.waitForTimeout(1000);
    assert.equal(await page.evaluate(() => window.__pior), 0);
    await ctx.close();
  });
}
