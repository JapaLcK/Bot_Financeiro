/**
 * /painel#/milhao (Progresso ao milhão) sobre /api/v2/progresso-milhao: o número, a data, o gráfico, os
 * sliders (teclado) e "Voltar ao padrão"; os estados (sem banco, passou, sem previsão, selos, Essencial,
 * 500), a navegação (Ferramentas e ⌘K, fora do menu) e 390/1440 sem rolagem lateral. Relógio: 2026-10-02 em SP.
 * Valores escritos à mão: R$ 48.250,37 a 10% ao ano, sem aporte, chegam a R$ 1 mi em 382 meses (31 anos e 10 meses).
 *
 * Rodar:  node --test tests/frontend/dashboard_v2_milhao.test.mjs   (mudou webapp/src, rode `npm --prefix webapp run build`)
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { abrirInfo, abrirPainel, exigeArtefatoEmDia } from "./_painel.mjs";

let browser;
before(async () => { exigeArtefatoEmDia(); browser = await chromium.launch(); });
after(() => browser?.close());

async function abrir({ resposta, ...opts } = {}) {
  const p = await abrirPainel(browser, { espera: null, ...opts });
  const pedidos = [];
  p.page.on("request", (r) => { if (r.url().includes("/progresso-milhao")) pedidos.push(new URL(r.url())); });
  if (resposta) await p.ctx.route("**/api/v2/progresso-milhao", resposta);
  await p.ir("/milhao");
  return { ...p, pedidos };
}
const card = (page) => page.locator("#w-milhao");
const pronto = async (opts) => { const p = await abrir(opts); await card(p.page).locator(".mi-titulo").first().waitFor(); return p; };
const texto = (page) => card(page).innerText();
const semRolagem = (page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth);
const slider = (page, nome) => page.getByRole("slider", { name: new RegExp(nome) });

test("padrão: R$ 48.250,37, falta R$ 951.750, chega em agosto de 2058", async () => {
  const { ctx, page, erros, pedidos } = await pronto();
  const t = await texto(page);
  const aria = await page.locator("#w-milhao svg[role=img]").getAttribute("aria-label");
  const info = await (await abrirInfo(page, "#w-milhao")).innerText();
  await ctx.close();
  assert.match(info, /Conta de juros compostos com taxa fixa: não é promessa de rendimento\. Patrimônio = Carteira, contas, caixinhas e investimentos\./);
  assert.match(t, /Faltam R\$ 951\.750/);
  assert.match(t, /Hoje R\$ 48\.250,37 · 4%/);
  assert.match(t, /Você chega em agosto de 2058/);
  assert.match(t, /Daqui a 31 anos e 10 meses/);
  assert.match(t, /Guardar por mês R\$ 0/);
  assert.match(t, /Rendimento ao ano 10%/);
  assert.doesNotMatch(t, /promessa de rendimento/); // a explicação mora no ⓘ, não no card
  assert.match(aria, /até R\$ 1 milhão em agosto de 2058/);
  assert.equal(pedidos.length, 1);
  assert.deepEqual(erros, []);
});

test("slider pelo teclado: R$ 1.000 por mês chegam em 236 meses; Voltar ao padrão restaura e desabilita", async () => {
  const { ctx, page } = await pronto();
  const voltar = page.getByRole("button", { name: "Voltar ao padrão" });
  assert.equal(await voltar.isDisabled(), true);
  const s = slider(page, "Guardar por mês");
  await s.focus();
  await s.press("Home");
  for (let i = 0; i < 20; i++) await s.press("ArrowRight"); // 20 × R$ 50
  const t = await texto(page);
  const vt = await s.getAttribute("aria-valuetext");
  assert.equal(await voltar.isDisabled(), false);
  await voltar.click();
  const depois = await texto(page);
  const desab = await voltar.isDisabled();
  await ctx.close();
  assert.match(t, /Guardar por mês R\$ 1\.000/);
  assert.match(t, /Daqui a 19 anos e 8 meses/); // 236 meses
  assert.match(t, /Você chega em junho de 2046/); // outubro de 2026 + 236 meses
  assert.equal(vt, "R$ 1.000 por mês");
  assert.match(depois, /Você chega em agosto de 2058/);
  assert.equal(desab, true);
});

test("rendimento 0%: sem aporte não há previsão, e o texto diz o que fazer", async () => {
  const { ctx, page } = await pronto();
  const s = slider(page, "Rendimento ao ano");
  await s.focus();
  await s.press("Home");
  const t = await texto(page);
  await ctx.close();
  assert.match(t, /Sem previsão/);
  assert.match(t, /Arraste o Guardar por mês para ver quando você chega\./);
  assert.doesNotMatch(t, /Você chega em/);
});

test("gráfico: os dois eixos têm nome e valores (Patrimônio em R$, Ano de 8 em 8), dentro do svg", async () => {
  const { ctx, page } = await pronto();
  await page.locator("#w-milhao svg[role=img]").waitFor(); // o svg só entra depois que useSize mede a largura
  const r = await page.evaluate(() => {
    const svg = document.querySelector("#w-milhao svg[role=img]"), c = svg.getBoundingClientRect();
    const txt = (sel) => [...svg.querySelectorAll(sel)].map((e) => e.textContent.replace(/\s/g, " ")); // o Intl separa "500 mil" com espaço fino
    const fora = [...svg.querySelectorAll("text")].filter((e) => { const b = e.getBoundingClientRect(); return b.left < c.left - 1 || b.right > c.right + 1 || b.top < c.top - 1 || b.bottom > c.bottom + 1; }).map((e) => e.textContent);
    return { eixos: txt(".mi-eixo"), y: txt(".mi-y"), x: txt(".mi-x"), fora };
  });
  await ctx.close();
  assert.deepEqual(r.eixos, ["Patrimônio", "Ano"]);
  assert.deepEqual(r.y, ["R$ 0", "R$ 500 mil", "R$ 1 mi"]);
  assert.deepEqual(r.x, ["2026", "2034", "2042", "2050", "2058"]); // 382 meses: passo de 8 anos e o fim da curva (ago/2058)
  assert.deepEqual(r.fora, []);
});

test("gráfico de prazo curto (menos de 2 anos): o eixo X vira Mês, com mês/ano", async () => {
  const { ctx, page } = await pronto({ resposta: (route) => route.fulfill({ json: { patrimonio: "999000.00", motivos: [] } }) });
  await page.locator("#w-milhao svg[role=img]").waitFor();
  const r = await page.evaluate(() => [...document.querySelectorAll("#w-milhao svg .mi-eixo, #w-milhao svg .mi-x")].map((e) => e.textContent));
  await ctx.close();
  assert.equal(r.at(-1), "Mês"); // na ordem do svg: "Patrimônio", as marcas do X e o nome do X
  assert.equal(r[1], "out/26");
});

test("gráfico: svg com rótulo, tracejado no y da meta e tabela por ano para leitor de tela", async () => {
  const { ctx, page } = await pronto();
  await page.locator("#w-milhao svg[role=img]").waitFor(); // o svg só entra depois que useSize mede a largura
  const r = await page.evaluate(() => {
    const svg = document.querySelector("#w-milhao svg[role=img]");
    const meta = svg.querySelector(".mi-meta"), linha = svg.querySelector("path.line");
    const topo = svg.getBoundingClientRect().top;
    return { dash: meta.getAttribute("stroke-dasharray"), ym: meta.getBoundingClientRect().top - topo, h: svg.getBoundingClientRect().height,
      d: linha.getAttribute("d").length, anos: document.querySelectorAll("#w-milhao .sr-only tbody tr").length };
  });
  await ctx.close();
  assert.equal(r.dash, "4 5");
  assert.ok(r.ym > 0 && r.ym < r.h * 0.2, `meta no alto do gráfico: ${r.ym}/${r.h}`); // a meta é o teto (máximo × 1,05)
  assert.ok(r.d > 100);
  assert.equal(r.anos, 33); // anos 0..31 + o ponto final (382 meses)
});

test("sem banco: convite com link para os bancos, sem slider nem gráfico", async () => {
  const { ctx, page } = await abrir({ milhao: "sem_banco" });
  await card(page).locator(".convite").waitFor();
  const t = await texto(page);
  const link = await card(page).getByRole("link").getAttribute("href");
  const n = await card(page).locator("input, svg[role=img]").count();
  await ctx.close();
  assert.match(t, /Conecte um banco para ver quanto falta para o seu primeiro milhão\./);
  assert.equal(link, "#/conta/bancos");
  assert.equal(n, 0);
});

test("passou do milhão: título, próximo marco R$ 2 milhões e a falta até ele", async () => {
  const { ctx, page } = await pronto({ milhao: "passou" });
  const t = await texto(page);
  const aria = await page.locator("#w-milhao svg[role=img]").getAttribute("aria-label");
  await ctx.close();
  assert.match(t, /Rumo ao próximo milhão/);
  assert.doesNotMatch(t, /Rumo ao primeiro milhão/);
  assert.match(t, /Você já passou do milhão/);
  assert.match(t, /Próximo marco: R\$ 2 milhões\. Faltam R\$ 750\.000\./);
  assert.match(aria, /até R\$ 2 milhões em /);
  assert.match(t, /Hoje R\$ 1\.250\.000,00 · 62%/);
  assert.match(t, /Você chega aos R\$ 2 milhões em \w+ de \d{4}/);
});

test("R$ 500 a 10% e sem aporte: sem previsão", async () => {
  const { ctx, page } = await pronto({ milhao: "pouco" });
  const t = await texto(page);
  await ctx.close();
  assert.match(t, /Faltam R\$ 999\.500/);
  assert.match(t, /Sem previsão/);
});

test("motivos viram selos abaixo do \"Hoje\"", async () => {
  const { ctx, page } = await pronto({ milhao: "com_motivos" });
  const selos = await card(page).locator(".selos .selo").allInnerTexts();
  const abaixo = await card(page).evaluate((c) => c.querySelector(".mi-hoje").compareDocumentPosition(c.querySelector(".selos")) & Node.DOCUMENT_POSITION_FOLLOWING);
  await ctx.close();
  assert.deepEqual(selos, ["banco desatualizado", "manual e banco juntos", "caixinha do banco fora da soma"]);
  assert.ok(abaixo);
});

test("Essencial: convite, e nenhum número do servidor na tela", async () => {
  const { ctx, page } = await abrir({ plano: "essencial" });
  await card(page).locator(".convite").waitFor();
  const t = await texto(page);
  await ctx.close();
  assert.match(t, /O Progresso ao milhão é do Plus e do Pro\./);
  assert.match(t, /Ver planos/);
  assert.doesNotMatch(t, /R\$ 48\.250/);
});

test("403 numa releitura: o número guardado some e aparece o convite do Plus", async () => {
  let nega = false;
  const { ctx, page, ir } = await abrir({ resposta: (r) => (nega ? r.fulfill({ status: 403, json: { error: { code: "pro_required", message: "x" } } }) : r.fallback()) });
  await card(page).locator(".mi-titulo").first().waitFor();
  assert.match(await texto(page), /48\.250/);
  nega = true;
  await ir("/ferramentas");
  await ir("/milhao");
  await card(page).locator(".convite").waitFor();
  const t = await texto(page);
  await ctx.close();
  assert.match(t, /O Progresso ao milhão é do Plus e do Pro\./);
  assert.doesNotMatch(t, /48\.250/);
});

for (const [status, code, re, acao] of [
  [403, "password_required", /Crie sua senha para ver o progresso ao milhão/, "Criar senha"],
  [401, "unauthorized", /Sua sessão terminou\./, "Recarregar"],
  [404, "not_found", /Não deu para abrir o progresso ao milhão/, "Recarregar"],
]) {
  test(`${status} ${code}: texto e ação "${acao}"`, async () => {
    const { ctx, page } = await abrir({ resposta: (r) => r.fulfill({ status, json: { error: { code, message: "x" } } }) });
    const alerta = card(page).getByRole("alert");
    await alerta.waitFor();
    const t = await alerta.innerText();
    const el = alerta.getByRole(acao === "Criar senha" ? "link" : "button", { name: acao });
    const n = await el.count();
    const href = acao === "Criar senha" ? await el.getAttribute("href") : null;
    await ctx.close();
    assert.match(t, re, `${status}: ${t}`);
    assert.equal(n, 1);
    assert.equal(href, acao === "Criar senha" ? "/home" : null);
  });
}

test("500 numa releitura: o número guardado continua, com o aviso", async () => {
  let cai = false;
  const { ctx, page, ir } = await abrir({ resposta: (r) => (cai ? r.fulfill({ status: 500, json: { error: { code: "internal_error", message: "x" } } }) : r.fallback()) });
  await card(page).locator(".mi-titulo").first().waitFor();
  cai = true;
  await ir("/ferramentas");
  await ir("/milhao");
  await card(page).locator(".prev-antigo").waitFor(); // o retry (1 s + 2 s) corre antes do aviso
  const t = await texto(page);
  await ctx.close();
  assert.match(t, /48\.250,37/);
  assert.match(t, /Não deu para atualizar\. Mostrando o que carregou antes\./);
});

test("sem banco + 500 numa releitura: o convite continua, com o aviso", async () => {
  let cai = false;
  const { ctx, page, ir } = await abrir({ milhao: "sem_banco", resposta: (r) => (cai ? r.fulfill({ status: 500, json: { error: { code: "internal_error", message: "x" } } }) : r.fallback()) });
  await card(page).locator(".convite").waitFor();
  cai = true;
  await ir("/ferramentas");
  await ir("/milhao");
  await card(page).locator(".prev-antigo").waitFor(); // o retry (1 s + 2 s) corre antes do aviso
  const t = await texto(page);
  await ctx.close();
  assert.match(t, /Conecte um banco para ver quanto falta/);
  assert.match(t, /Não deu para atualizar\. Mostrando o que carregou antes\./);
});

test("500: \"Tentar de novo\" refaz o pedido e mostra o número", async () => {
  let cai = true;
  const { ctx, page, pedidos } = await abrir({ resposta: (r) => (cai ? r.fulfill({ status: 500, json: { error: { code: "internal_error", message: "x" } } }) : r.fallback()) });
  await page.getByRole("button", { name: "Tentar de novo" }).waitFor();
  assert.match(await texto(page), /Não deu para carregar o progresso\./);
  const antes = pedidos.length;
  cai = false;
  await page.getByRole("button", { name: "Tentar de novo" }).click();
  await card(page).locator(".mi-titulo").first().waitFor();
  const t = await texto(page);
  await ctx.close();
  assert.ok(pedidos.length > antes);
  assert.match(t, /Faltam R\$ 951\.750/);
});

test("navegação: card em Ferramentas, ⌘K lista e leva, e a tela não está no menu lateral nem no Menu do celular", async () => {
  const { ctx, page, ir } = await abrirPainel(browser, { width: 1440, espera: "#page-title" });
  await ir("/");
  const menu = await page.locator(".rail-list a, .rail-list button").allInnerTexts();
  await page.keyboard.press("Control+k");
  await page.keyboard.type("milh");
  const item = page.locator(".cmdk-item").filter({ hasText: "Progresso ao milhão" });
  await item.waitFor();
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => location.hash === "#/milhao");
  await page.locator("#w-milhao").waitFor();
  const noRail = await page.locator('.rail-list a[href="#/milhao"]').count();
  const titulo = await page.locator("#page-title").innerText();
  await ctx.close();
  assert.ok(!menu.some((m) => /milh/i.test(m)), menu.join("|"));
  assert.equal(noRail, 0);
  assert.equal(titulo, "Progresso ao milhão");
});

test("celular: o Menu da barra de baixo não lista a tela", async () => {
  const { ctx, page, ir } = await abrirPainel(browser, { width: 390, espera: "#page-title" });
  await ir("/");
  await page.locator(".tabbar").getByRole("button", { name: "Menu" }).click();
  const t = await page.locator(".menu-telas").innerText();
  await ctx.close();
  assert.doesNotMatch(t, /milh/i);
});

test("protótipo (DEMO): abre com o patrimônio de exemplo e não pede nada à API", async () => {
  const { ctx, page, pedidos, erros } = await abrir({ demo: true });
  await card(page).locator(".mi-titulo").first().waitFor();
  const t = await texto(page);
  await ctx.close();
  assert.equal(pedidos.length, 0);
  assert.match(t, /Faltam R\$ /);
  assert.deepEqual(erros, []);
});

for (const [width, alvoMin] of [[1440, 0], [390, 44]]) {
  test(`${width}px: sem rolagem lateral, sliders e botão dentro da tela${alvoMin ? ", alvos de 44px" : ""}`, async () => {
    const { ctx, page } = await pronto({ width });
    await page.locator("#w-milhao svg[role=img]").waitFor(); // o svg só entra depois que useSize mede a largura
    const r = await page.evaluate(() => {
      const c = document.querySelector("#w-milhao").getBoundingClientRect();
      const fora = [...document.querySelectorAll("#w-milhao *")].filter((e) => { const b = e.getBoundingClientRect(); return b.width && (b.right > c.right + 1 || b.left < c.left - 1); }).map((e) => e.className || e.tagName);
      const alturas = [...document.querySelectorAll("#w-milhao .btn, #w-milhao input[type=range]")].map((e) => Math.round(e.getBoundingClientRect().height));
      const g = document.querySelector("#w-milhao svg[role=img]").getBoundingClientRect();
      return { fora, alturas, grafico: [Math.round(g.width), Math.round(g.height)] };
    });
    const sem = await semRolagem(page);
    await ctx.close();
    assert.equal(sem, true);
    assert.deepEqual(r.fora, []);
    assert.ok(r.alturas.every((h) => h >= alvoMin), r.alturas.join());
    assert.ok(r.grafico[0] > 200 && r.grafico[1] >= 200, r.grafico.join("x"));
  });
}
