// Protótipo dashboard-v2: a navegação com o Piggy no meio e a página de Ferramentas.
//   · celular: barra de baixo Resumo, Gastos, Piggy (meio), Extrato e Menu (a folha com todas as
//     telas e a Ajuda, parts/MenuTelas.tsx);
//   · menu lateral sem o Piggy (no desktop o acesso é a barra de conversa), com Ferramentas e Ajuda;
//   · "E se…" virou "Simulador" em todo lugar; o botão rosa virou "Ferramentas", com o
//     nome escrito também no celular;
//   · Ferramentas: o Simulador e o Painel antigo (/app) levam a algum lugar, o Guia do painel o abre,
//     o resto está "Em breve";
//   · 320 a 1440 sem nada saindo da barra de cima.
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { PAINEL, PROTOTIPO, RAIZ, exigeArtefatoEmDia, servir } from "./_painel.mjs";

let browser;
before(async () => {
  exigeArtefatoEmDia();
  browser = await chromium.launch();
});
after(() => browser?.close());

async function abrir(width, hash = "#/", { height = 800, semDialog = false, url = PAINEL, raiz } = {}) {
  const ctx = await browser.newContext({ viewport: { width, height }, reducedMotion: "reduce" });
  await servir(ctx, raiz);
  await ctx.addInitScript(() => localStorage.setItem("pigbank.dashboard.profile.v1", '"padrao"'));
  // Safari 14 (sem <dialog>): o mesmo corte do dashboard_v2_cmdk.test.mjs.
  if (semDialog) await ctx.addInitScript(() => { delete HTMLDialogElement.prototype.showModal; delete HTMLDialogElement.prototype.close; });
  const page = await ctx.newPage();
  await page.goto(`${url}${hash}`);
  await page.locator("#page-title").waitFor();
  return { ctx, page };
}
const textos = (page, sel) => page.locator(sel).evaluateAll((els) => els.map((e) => e.textContent.trim()));

test("celular: o Piggy é o botão do meio e leva à página dele", async () => {
  const { ctx, page } = await abrir(390);
  const abas = await textos(page, ".tabbar a, .tabbar button");
  await page.locator('.tabbar a[data-tab="piggy"]').click();
  await page.locator("#page-title", { hasText: "Converse com o Piggy" }).waitFor({ timeout: 3000 }).catch(() => {});
  const r = [await page.locator("#page-title").innerText(), await page.locator('.tabbar a[data-tab="piggy"]').getAttribute("aria-current")];
  await ctx.close();
  assert.deepEqual(abas, ["Resumo", "Gastos", "Piggy", "Extrato", "Menu"]);
  // O selo "demonstração" é por mensagem (a resposta de exemplo leva, a real não): o título não
  // leva mais. A regra por mensagem é medida em dashboard_v2_chat_investido.test.mjs.
  assert.deepEqual(r, ["Converse com o Piggy", "page"]);
});

test("desktop: menu com Simulador e Ferramentas, sem Piggy; o botão rosa abre Ferramentas", async () => {
  const { ctx, page } = await abrir(1440);
  const menu = await textos(page, ".rail-list a, .rail-list button");
  const botao = await page.locator(".topbar .btn-primary").innerText();
  await page.locator(".topbar .btn-primary").click();
  await page.waitForFunction(() => location.hash === "#/ferramentas");
  await page.locator(".tools li").first().waitFor();
  const cards = await page.locator(".tools li").evaluateAll((lis) => lis.map((li) => [li.querySelector("b").textContent, li.querySelector("a")?.getAttribute("href") ?? (li.querySelector("button") ? "botão" : li.querySelector("[aria-disabled]")?.textContent.includes("Em breve"))]));
  await ctx.close();
  assert.deepEqual(menu, ["Resumo", "Previsão", "Para onde vai", "Assinaturas", "Simulador", "Metas", "Patrimônio", "Lançamentos", "Ferramentas", "Ajuda"]);
  assert.equal(botao.trim(), "Ferramentas");
  assert.deepEqual(cards[0], ["Simulador", "#/simulador"]);
  assert.deepEqual(cards[1], ["Painel antigo", "/app"]); // sai do v2 para o dashboard de sempre
  assert.deepEqual(cards[2], ["Orçamento doméstico", "#/orcamento"]);
  assert.deepEqual(cards[3], ["Orçamentos por categoria", "#/orcamentos-categoria"]);
  assert.equal(cards.length, 8);
  assert.deepEqual(cards[7], ["Guia do painel", "botão"]); // abre o guia
  assert.deepEqual(cards[4], ["Vale a pena comprar?", "#/simulador/compra"]); // simulador de compra v2
  assert.ok([5, 6].every((i) => cards[i][1] === true), JSON.stringify(cards)); // o resto: "Em breve", sem link
});

test("o nome antigo \"E se…\" não aparece mais", async () => {
  const { ctx, page } = await abrir(1440, "#/");
  const r = await page.evaluate(() => document.body.innerText.includes("E se…"));
  await ctx.close();
  assert.equal(r, false);
});

test("barra de cima: o botão Ferramentas fica dentro da margem de 320 a 1440", async () => {
  for (const width of [320, 340, 360, 361, 375, 383, 390, 414, 760, 1440]) { // 361–383: a busca volta e ainda tem de caber
    const { ctx, page } = await abrir(width);
    const r = await page.evaluate(() => {
      const b = document.querySelector(".topbar .btn-primary").getBoundingClientRect();
      const t = document.querySelector(".month-title");
      // Folga: o que sobra no seletor de mês além das setas e do texto visível. O CI no Linux
      // desenha o texto mais largo que o macOS; sem folga aqui, lá estoura.
      const sw = document.querySelector(".month-switch");
      const range = document.createRange(); range.selectNodeContents(t);
      const texto = [...range.getClientRects()].reduce((a, r) => a + r.width, 0);
      const setas = [...sw.querySelectorAll("button")].reduce((a, e) => a + e.getBoundingClientRect().width, 0);
      const folga = sw.getBoundingClientRect().width - setas - texto;
      return [document.documentElement.clientWidth - Math.round(b.right) >= 16, b.width > 0 && /Ferramentas/.test(document.querySelector(".topbar .btn-primary").textContent),
        t.scrollWidth <= t.clientWidth, document.scrollingElement.scrollWidth - document.scrollingElement.clientWidth, innerWidth > 760 || folga >= 12 || Math.round(folga)]; // no desktop quem absorve a sobra é o espaçador
    });
    await ctx.close();
    assert.deepEqual(r, [true, true, true, 0, true], String(width));
  }
});

test("a aba do Piggy mostra quando é a página atual, como as outras", async () => {
  const { ctx, page } = await abrir(390);
  const estilo = () => page.locator('.tabbar a[data-tab="piggy"] img').evaluate((i) => `${getComputedStyle(i).filter}|${getComputedStyle(i).opacity}`);
  const fora = await estilo();
  await page.locator('.tabbar a[data-tab="piggy"]').click();
  await page.locator('.tabbar a[data-tab="piggy"][aria-current="page"]').waitFor();
  await page.waitForFunction(() => getComputedStyle(document.querySelector('.tabbar a[data-tab="piggy"] img')).opacity === "1", null, { timeout: 3000 }).catch(() => {}); // há transição
  const dentro = await estilo();
  await ctx.close();
  assert.notEqual(fora, dentro);
  assert.equal(dentro, "none|1");
});

// O Menu da barra de baixo (parts/MenuTelas.tsx): uma folha com as 9 telas do menu lateral, a Ajuda e o grupo Conta (Perfil, Cobrança, Configurações, Bancos conectados, Sair).
// Nativo e com o fallback do Safari 14 (sem showModal/close): o mesmo roteiro.
const ROTULOS = ["Resumo", "Previsão", "Para onde vai", "Assinaturas", "Simulador", "Metas", "Patrimônio", "Lançamentos", "Ferramentas"];
for (const semDialog of [false, true]) {
  test(`celular 375 (${semDialog ? "fallback sem <dialog>" : "<dialog> nativo"}): o Menu abre a folha com as 9 telas e o grupo Conta; foco, Esc, toque fora, navegar e reabrir`, async () => {
    const { ctx, page } = await abrir(375, "#/", { height: 812, semDialog });
    const botao = page.locator(".tabbar").getByRole("button", { name: "Menu" });
    const folha = page.locator(".menu-telas");
    const visivel = () => folha.evaluate((e) => e.hasAttribute("open") && e.getClientRects().length > 0 && getComputedStyle(e).display !== "none");
    const foco = () => page.evaluate(() => { const a = document.activeElement; return [a?.closest(".menu-telas") != null, a?.id || a?.textContent.trim()]; });
    const r = {};
    r.fechada = await visivel();
    await botao.click();
    r.aberta = await visivel();
    r.rotulos = await textos(page, ".menu-telas a");
    r.atual = await textos(page, '.menu-telas a[aria-current="page"]');
    r.foco = await foco();
    r.guias = await page.locator(".menu-telas [data-guia]").count();
    // 17 Tabs (fecha + 9 telas + 4 da Conta + Sair + Guia do painel = 16, então dá a volta): o foco nunca para num
    // elemento de fora da folha. No nativo, a volta passa um Tab pelo navegador (document.body).
    r.tabs = [];
    for (let i = 0; i < 17; i++) { await page.keyboard.press("Tab"); r.tabs.push(await page.evaluate(() => { const a = document.activeElement; return a === document.body || a?.closest(".menu-telas") != null; })); }
    // Com a folha aberta o ⌘K não abre um segundo modal.
    await page.keyboard.press("Meta+k");
    r.cmdk = await page.locator(".cmdk[open]").count();
    await page.keyboard.press("Escape");
    r.escFechou = !(await visivel());
    r.escFoco = await page.evaluate(() => document.activeElement === [...document.querySelectorAll(".tabbar button")].at(-1));
    // Toque fora (no fundo escuro) fecha.
    await botao.click();
    await page.mouse.click(10, 10);
    r.foraFechou = !(await visivel());
    // Tocar na tela em que já está não muda a rota (nada de `hashchange`), mas fecha a folha.
    await botao.click();
    await folha.getByRole("link", { name: "Resumo" }).click();
    r.mesmaTela = !(await visivel());
    // Tocar numa tela navega, fecha e leva o foco ao título da página nova.
    await botao.click();
    await folha.getByRole("link", { name: "Previsão" }).click();
    await page.waitForFunction(() => location.hash === "#/previsao");
    await page.waitForFunction(() => document.activeElement?.id === "page-title", null, { timeout: 3000 }).catch(() => {});
    r.navegou = [await visivel(), await page.evaluate(() => [location.hash, document.activeElement?.id])];
    await botao.click();
    r.atualDepois = await textos(page, '.menu-telas a[aria-current="page"]');
    // A Ajuda do Menu abre o guia (o mesmo evento da Ajuda do trilho) e fecha a folha.
    await page.evaluate(() => { window.__guia = 0; window.addEventListener("dash:guia", () => window.__guia++); });
    await folha.getByRole("button", { name: "Guia do painel" }).click();
    r.ajuda = [await visivel(), await page.evaluate(() => window.__guia)];
    await ctx.close();
    assert.deepEqual(r, {
      fechada: false, aberta: true, rotulos: [...ROTULOS, "Perfil", "Cobrança", "Configurações", "Bancos conectados"], atual: ["Resumo"], foco: [true, "Resumo"], guias: 0,
      tabs: Array(17).fill(true), cmdk: 0, escFechou: true, escFoco: true, foraFechou: true, mesmaTela: true,
      navegou: [false, ["#/previsao", "page-title"]], atualDepois: ["Previsão"], ajuda: [false, 1],
    });
  });
}

test("celular 320×640: a folha cabe na tela e o último item se alcança rolando; sem rolagem lateral", async () => {
  const { ctx, page } = await abrir(320, "#/", { height: 640 });
  await page.locator(".tabbar").getByRole("button", { name: "Menu" }).click();
  const ultimo = page.locator(".menu-telas li").last();
  await ultimo.scrollIntoViewIfNeeded();
  const r = await page.evaluate(() => {
    const f = document.querySelector(".menu-telas").getBoundingClientRect(), u = [...document.querySelectorAll(".menu-telas li")].at(-1).getBoundingClientRect();
    return { topo: f.top >= 0, baixo: Math.round(f.bottom) === innerHeight, ultimo: u.top >= f.top && u.bottom <= innerHeight, lateral: document.documentElement.scrollWidth - innerWidth };
  });
  await ctx.close();
  assert.deepEqual(r, { topo: true, baixo: true, ultimo: true, lateral: 0 });
});

test("protótipo (DEMO, mesmo bundle): a barra é Resumo, Gastos, Piggy, Extrato, Menu; a folha tem as 9 telas e nenhuma Ajuda", async () => {
  const { ctx, page } = await abrir(375, "#/", { height: 812, url: PROTOTIPO, raiz: RAIZ });
  const abas = await textos(page, ".tabbar a, .tabbar button");
  await page.locator(".tabbar").getByRole("button", { name: "Menu" }).click();
  const r = [await textos(page, ".menu-telas a"), await page.locator(".menu-telas-sec").count()];
  await ctx.close();
  assert.deepEqual(abas, ["Resumo", "Gastos", "Piggy", "Extrato", "Menu"]);
  assert.deepEqual(r, [ROTULOS, 0]);
});

// Geometria da folha: o "Fechar" cobre o ponto onde estava o botão Menu (duplo toque, ver
// dashboard_v2_dica.test.mjs), a Ajuda fica à vista sem rolar e o Menu se destaca fora da barra.
for (const [w, h] of [[320, 568], [375, 812], [390, 844]]) {
  test(`celular ${w}×${h}: o Fechar cobre o botão Menu; a Ajuda fica no topo, à vista e com nome de grupo`, async () => {
    const { ctx, page } = await abrir(w, "#/", { height: h });
    const botao = page.locator(".tabbar").getByRole("button", { name: "Menu" });
    const b = await botao.boundingBox();
    await botao.click();
    const r = await page.evaluate((m) => {
      const f = document.querySelector(".menu-telas"), fr = f.getBoundingClientRect();
      const x = document.querySelector(".menu-telas-fechar").getBoundingClientRect(), a = document.querySelector('.menu-telas [role="group"] li:last-child').getBoundingClientRect();
      // Os 4 cantos do botão Menu e o centro: o 2º toque de um duplo toque pode cair em qualquer ponto dele.
      const [x0, y0, x1, y1] = [m.x + 1, m.y + 1, m.x + m.width - 1, m.y + m.height - 1];
      const sobre = [[x0, y0], [x1, y0], [x0, y1], [x1, y1], [(x0 + x1) / 2, (y0 + y1) / 2]].map(([px, py]) => document.elementFromPoint(px, py)?.textContent.trim());
      return { sobre, alto: Math.round(x.height) >= 48, larga: Math.round(x.width) === innerWidth, rolou: f.scrollTop,
        cobreBarra: x.top <= document.querySelector(".tabbar").getBoundingClientRect().top, // a barra inteira, não só o botão
        ajudaVisivel: a.bottom <= fr.bottom - x.height && a.top >= fr.top, grupo: document.querySelector('.menu-telas [role="group"]').getAttribute("aria-labelledby") };
    }, b);
    const fechar = await page.locator(".menu-telas-fechar").count();
    await ctx.close();
    assert.deepEqual(r, { sobre: Array(5).fill("Fechar"), alto: true, larga: true, rolou: 0, cobreBarra: true, ajudaVisivel: true, grupo: "menu-ajuda" });
    assert.equal(fechar, 1); // um só botão Fechar
  });
}

// Duplo toque REAL (touchscreen.tap ×2) 2 px abaixo do topo do botão Menu, onde sobrava só ~3,5 px de Fechar:
// o 2º toque caía no link "Patrimônio" e navegava.
test("celular 320×568: duplo toque real no topo do botão Menu abre e fecha a folha, sem navegar", async () => {
  const ctx = await browser.newContext({ viewport: { width: 320, height: 568 }, reducedMotion: "reduce", hasTouch: true });
  await servir(ctx);
  await ctx.addInitScript(() => localStorage.setItem("pigbank.dashboard.profile.v1", '"padrao"'));
  const page = await ctx.newPage();
  await page.goto(`${PAINEL}#/`);
  await page.locator("#page-title").waitFor();
  const b = await page.locator(".tabbar").getByRole("button", { name: "Menu" }).boundingBox();
  const x = b.x + b.width / 2, y = b.y + 2;
  await page.touchscreen.tap(x, y);
  await page.touchscreen.tap(x, y);
  await page.waitForTimeout(400);
  const r = await page.evaluate(() => ({ folha: document.querySelectorAll(".menu-telas[open]").length, hash: location.hash }));
  await ctx.close();
  assert.deepEqual(r, { folha: 0, hash: "#/" });
});

// Texto ampliado do sistema (Android/Samsung/Firefox Android: ampliam em px): a barra cresce (min-height), nenhum
// rótulo sai da tela, e o Fechar herda a altura REAL dela (MenuTelas.tsx) para continuar cobrindo o botão Menu.
// Emulado como o texto ampliado ×1,5: o multiplicador no font-size do rótulo (11px) e do ícone (21px).
for (const [w, h] of [[320, 568], [375, 812]]) {
  test(`celular ${w}×${h}, texto ×1,5: os rótulos da barra ficam na tela e o Fechar cobre o botão Menu`, async () => {
    const { ctx, page } = await abrir(w, "#/", { height: h });
    await page.addStyleTag({ content: ".tabbar span { font-size: 16.5px !important } .tabbar i { font-size: 31.5px !important }" });
    const botao = page.locator(".tabbar").getByRole("button", { name: "Menu" });
    const rotulos = await page.locator(".tabbar span").evaluateAll((els) => els.map((e) => e.getBoundingClientRect().bottom <= innerHeight));
    await botao.click();
    const m = await botao.boundingBox();
    const sobre = await page.evaluate((m) => {
      const [x0, y0, x1, y1] = [m.x + 1, m.y + 1, m.x + m.width - 1, m.y + m.height - 1];
      return [[x0, y0], [x1, y0], [x0, y1], [x1, y1], [(x0 + x1) / 2, (y0 + y1) / 2]].map(([px, py]) => document.elementFromPoint(px, py)?.textContent.trim());
    }, m);
    await ctx.close();
    assert.deepEqual(rotulos, Array(5).fill(true)); // (a)
    assert.deepEqual(sobre, Array(5).fill("Fechar")); // (b)
  });
}

test("celular: o Menu fica destacado (aria-current) nas telas que só estão nele, e não nas da barra", async () => {
  const lido = async (hash) => {
    const { ctx, page } = await abrir(375, hash, { height: 812 });
    const r = await page.evaluate(() => { const m = [...document.querySelectorAll(".tabbar button")].at(-1); return [m.getAttribute("aria-current"), getComputedStyle(m).color, getComputedStyle(m.querySelector("i")).color]; });
    await ctx.close();
    return r;
  };
  const [fora, dentro] = [await lido("#/previsao"), await lido("#/")];
  assert.equal(fora[0], "true");
  assert.equal(dentro[0], null);
  assert.notEqual(fora[1], dentro[1]); // texto claro como o item ativo da barra
  assert.notEqual(fora[2], dentro[2]); // ícone rosa
});
