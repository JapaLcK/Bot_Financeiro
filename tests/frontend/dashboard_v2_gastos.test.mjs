/**
 * /painel, Etapa 5 PR 2: "Para onde vai" ligado à API real (`GET /api/v2/gastos` e `/gastos/categoria`) na
 * página /gastos e nos blocos "Para onde vai" e "Dia a dia" do Resumo.
 *
 *   · critério do dono: o Saiu do Resumo, a lede do bloco e a soma das linhas, em centavos inteiros;
 *   · escolher categoria pede o detalhe dela; escolher o dia abre o card de compras do dia na própria /gastos (o card em si,
 *     os números e o layout: dashboard_v2_gastos_dia.test.mjs); só 'Ver em Lançamentos' leva ao extrato, com `mes` e `dia`;
 *   · estados: erro com "Tentar de novo", mês fora do histórico, corte do plano, mês sem gasto, categoria sem gasto;
 *   · nenhum selo de demonstração nem texto do protótipo; layout em 1440 e 390.
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

const abrir = (opts) => abrirPainel(browser, { espera: "#w-categorias", ...opts });
// Espera o dado: o bloco aparece antes do /gastos responder.
const COM_DADO = { espera: "#w-cat-detalhe .detail-facts" };
const centavos = (t) => Number(t.replace(/\D/g, ""));
const numero = (loc) => loc.evaluate((v) => [...v.shadowRoot.querySelectorAll(".symbol__value, [part~=digit]")]
  .map((n) => (n.matches("[part~=digit]") ? n.style.getPropertyValue("--current").trim() : n.textContent)).join("").replace(/\s/g, " "));
const texto = (page, sel) => page.locator(sel).evaluate((e) => e.textContent.replace(/\s/g, " "));
const pedidos = (page, caminho) => {
  const urls = [];
  page.on("request", (r) => { const u = new URL(r.url()); if (u.pathname === caminho) urls.push(u.search); });
  return urls;
};

test("critério do dono: Saiu do Resumo, lede do bloco e soma das linhas são os mesmos R$ 440,00, com centavos", async () => {
  const { ctx, page, erros, ir } = await abrir({ gastos: "centavos", resumo: "centavos", espera: "#w-categorias .cats" });
  const urls = pedidos(page, "/api/v2/gastos");
  await ir("/");
  await page.locator("#w-resumo .stat-value").first().waitFor();
  const saiu = await numero(page.locator("#w-resumo .stat").nth(1).locator(".stat-value"));
  const valores = await page.locator("#w-categorias .cat-val").allTextContents();
  const lede = await texto(page, "#w-categorias .w-lede .num");
  await ctx.close();
  assert.equal(saiu, "R$ 440,00");
  assert.equal(lede, "R$ 440,00");
  assert.deepEqual(valores, ["R$ 300,50", "R$ 139,50"]); // com `money0`: R$ 301 e R$ 140 (a soma dá 441)
  assert.equal(valores.reduce((a, v) => a + centavos(v), 0), centavos(saiu));
  assert.deepEqual(erros, []);
  assert.equal(urls.length, 1, urls.join()); // o Resumo pede uma vez: categorias e calendário dividem a consulta
});

test("ícone da categoria: o `icone` do servidor vira Phosphor na lista e no detalhe, sem emoji na tela; servidor velho (sem o campo) = `tag`", async () => {
  const emoji = /\p{Extended_Pictographic}/u;
  const { ctx, page, ir } = await abrir({ ...COM_DADO });
  await ir("/gastos");
  await page.locator("#w-categorias .cat").first().waitFor();
  const ler = () => page.evaluate(() => ({ lista: [...document.querySelectorAll("#w-categorias .cat-name i")].map((i) => i.className),
    detalhe: document.querySelector("#w-cat-detalhe .detail-title i")?.className, texto: document.querySelector("#w-categorias").textContent + document.querySelector("#w-cat-detalhe .detail-title").textContent }));
  const novo = await ler();
  assert.deepEqual([novo.lista, novo.detalhe, emoji.test(novo.texto)], [["ph ph-fork-knife", "ph ph-tag"], "ph ph-fork-knife", false]);
  // `icone` que não é nome de ícone (espaço = 3 classes CSS) cai em `tag`; o vizinho válido segue (controle: sem a regex a 1ª sairia "ph ph-a b c").
  await ctx.route((u) => u.pathname === "/api/v2/gastos", (r) => r.fulfill({ json: { ...RESPOSTAS.gastos.padrao, categorias: RESPOSTAS.gastos.padrao.categorias.map((c, i) => ({ ...c, icone: i ? "shopping-cart" : "a b c" })) } }));
  await page.getByRole("button", { name: "Mês anterior", exact: true }).click();
  await page.waitForFunction(() => [...document.querySelectorAll("#w-categorias .cat-name i")].map((i) => i.className).join("|") === "ph ph-tag|ph ph-shopping-cart");
  await ctx.route((u) => u.pathname === "/api/v2/gastos", (r) => r.fulfill({ json: { ...RESPOSTAS.gastos.padrao, categorias: RESPOSTAS.gastos.padrao.categorias.map(({ icone, ...c }) => c) } }));
  await page.getByRole("button", { name: "Mês anterior", exact: true }).click();
  await page.waitForFunction(() => [...document.querySelectorAll("#w-categorias .cat-name i")].every((i) => i.className === "ph ph-tag"));
  await ctx.close();
});

test("outra moeda: o aviso diz quantas saídas ficaram fora, o selo aparece e a lede (subtotal em reais) difere do Saiu", async () => {
  const { ctx, page, ir } = await abrir({ gastos: "outra_moeda", resumo: "centavos", espera: "#w-categorias .cats" });
  await ir("/");
  await page.locator("#w-resumo .stat-value").first().waitFor();
  const r = [await numero(page.locator("#w-resumo .stat").nth(1).locator(".stat-value")), await texto(page, "#w-categorias .w-lede .num"),
    await page.locator("#w-categorias .w-lede").nth(1).textContent(), await page.locator("#w-categorias .selo").allTextContents()];
  await ctx.close();
  assert.deepEqual(r, ["R$ 440,00", "R$ 340,00", "1 saída em outra moeda ficou fora deste total", ["outra moeda"]]);
});

test("variação: do total (até o dia, e do mês inteiro no mês passado) e de cada linha, com o nome acessível dizendo o motivo do traço", async () => {
  const { ctx, page, ir } = await abrir({ ...COM_DADO });
  await ir("/gastos");
  await page.locator("#w-categorias .cat").first().waitFor();
  const atual = await texto(page, "#w-categorias .w-lede");
  const linhas = await page.locator("#w-categorias .cat").evaluateAll((bs) => bs.map((b) => [b.querySelector(".cat-delta").textContent, b.querySelector(".cat-delta").className, b.getAttribute("aria-label")]));
  // mês passado: o servidor corta no último dia do mês
  await ctx.route((u) => u.pathname === "/api/v2/gastos", (r) => r.fulfill({ json: { ...RESPOSTAS.gastos.padrao, mes: "2026-09", dia_de_corte: 30 } }));
  await page.getByRole("button", { name: "Mês anterior", exact: true }).click();
  await page.waitForFunction(() => document.querySelector("#w-categorias .w-lede")?.textContent.includes("O mês inteiro"));
  const passado = await texto(page, "#w-categorias .w-lede");
  await ctx.close();
  assert.match(atual, /Até o dia 14: 26% a mais que em setembro no mesmo dia/);
  assert.match(passado, /O mês inteiro: 26% a mais que em agosto$/);
  assert.deepEqual(linhas.map((l) => [l[0], /warn/.test(l[1])]), [["20%", true], ["—", false]]);
  assert.match(linhas[0][2], /^alimentação: R\$ 300,00, 20% a mais que no mesmo dia do mês anterior\. Filtrar lançamentos\.$/);
  assert.match(linhas[1][2], /sem gasto no mês anterior até o dia 14/);
});

test("escolher categoria pede o detalhe e o calendário dela (uma requisição); o título do detalhe muda; Limpar volta à maior", async () => {
  const { ctx, page, ir } = await abrir({ ...COM_DADO });
  await ctx.route((u) => u.pathname === "/api/v2/gastos/categoria", (r) => {
    const chave = new URL(r.request().url()).searchParams.get("categoria");
    return r.fulfill({ json: { ...RESPOSTAS.gastos_categoria.padrao, categoria: { chave, nome: chave, emoji: "🏷️", icone: "tag", cor: "#94A3B8" } } });
  });
  const urls = pedidos(page, "/api/v2/gastos/categoria");
  await ir("/gastos");
  await page.locator("#w-cat-detalhe .detail-title", { hasText: "alimentacao" }).waitFor();
  const antes = await texto(page, "#w-cat-detalhe .detail-title");
  await page.locator("#w-categorias .cat", { hasText: "sem categoria" }).click();
  await page.locator("#w-cat-detalhe .detail-title", { hasText: "sem categoria" }).waitFor();
  const depois = [await texto(page, "#w-cat-detalhe .detail-title"), await texto(page, "#w-calendario .tag-demo"),
    await page.locator("#w-categorias .cat[data-dim]").count()];
  await page.getByRole("button", { name: /^Limpar/ }).click();
  await page.locator("#w-cat-detalhe .detail-title", { hasText: "alimentacao" }).waitFor();
  await ctx.close();
  assert.match(antes, /alimentacao.*maior gasto do mês/);
  assert.deepEqual(depois, ["sem categoria", "sem categoria", 1]);
  assert.deepEqual(urls, ["?mes=2026-10&categoria=alimentacao", "?mes=2026-10&categoria=sem+categoria", "?mes=2026-10&categoria=alimentacao"]);
});

test("calendário: hoje, dia agendado, dia futuro, escala por percentil, nome acessível com os maiores; clicar no dia abre o card de compras na mesma tela (mes e dia)", async () => {
  const { ctx, page, ir } = await abrir({ ...COM_DADO, lancamentos: "dia_7" });
  const lanc = pedidos(page, "/api/v2/lancamentos");
  await ir("/gastos");
  const dia = (n) => page.locator("#w-calendario .cal-day", { hasText: new RegExp(`^${n}$`) });
  const r = await page.evaluate(() => Object.fromEntries([3, 7, 14, 20, 21].map((n) => {
    const b = [...document.querySelectorAll("#w-calendario .cal-day")][n - 1];
    return [n, [b.dataset.step, b.dataset.today ?? null, b.dataset.agendado ?? null, b.disabled]];
  })));
  const label = await dia(7).getAttribute("aria-label");
  const lede = await texto(page, "#w-calendario .w-lede");
  const caro = await texto(page, "#w-calendario .cal-foot .link");
  await dia(7).click();
  await page.locator("#gastos-compras .lanc-linha").first().waitFor();
  const card = await page.evaluate(() => ({ hash: location.hash, titulo: document.querySelector("#w-compras-h").textContent, data: document.querySelector("#gastos-compras .dia-data").textContent,
    lede: `${document.querySelector("#gastos-compras .dia-rotulo").textContent} · ${document.querySelector("#gastos-compras .dia-total").textContent}`,
    chips: document.querySelectorAll(".ledger-chips").length }));
  const ultimo = lanc.at(-1);
  await ctx.close();
  assert.deepEqual(r, { 3: ["3", null, null, false], 7: ["6", null, null, false], 14: ["0", "true", null, false], 20: ["0", null, "true", false], 21: ["0", null, null, true] });
  assert.equal(label, "qua, 7 de outubro: R$ 290,00. Maiores: Restaurante R$ 200,00, Sem descrição R$ 90,00");
  assert.equal(lede, "média R$ 27,86/dia · 12 dias sem gasto"); // (100 + 290) / 14 dias, só até hoje: o dia 20 (agendado) fica fora
  assert.equal(caro, "7 · R$ 290,00");
  assert.equal(ultimo, "?mes=2026-10&dia=2026-10-07&tipo=saida&guardado=true");
  assert.deepEqual(card, { hash: "#/gastos", titulo: "Seu dia", data: "qua, 7 de outubro", lede: "Gasto no dia · R$ 290,00", chips: 0 }); // não saiu da tela, e o filtro global não ganhou o dia
});

test("o dia é de um mês: trocar o mês fecha o card; o chip só chega por 'Ver em Lançamentos' e, no outro mês, a lista não leva `dia` nem chip; voltar traz o dia; o chip limpa", async () => {
  const { ctx, page, ir } = await abrir({ ...COM_DADO, lancamentos: "dia_7" });
  const lanc = pedidos(page, "/api/v2/lancamentos");
  await ir("/gastos");
  const dia7 = () => page.locator("#w-calendario .cal-day", { hasText: /^7$/ });
  await dia7().click();
  await page.locator("#gastos-compras").waitFor();
  await page.getByRole("button", { name: "Mês anterior", exact: true }).click();
  await page.waitForFunction(() => document.querySelector(".month-title").textContent === "Setembro 2026" && !document.querySelector("#gastos-compras"));
  await page.getByRole("button", { name: "Próximo mês", exact: true }).click();
  await page.waitForFunction(() => document.querySelector(".month-title").textContent === "Outubro 2026");
  const fechado = await page.locator("#gastos-compras").count(); // voltar ao mês não reabre
  await dia7().click();
  await page.getByRole("button", { name: "Ver em Lançamentos" }).click();
  await page.locator(".ledger-chips .chip").waitFor();
  const noMes = lanc.at(-1);
  await page.getByRole("button", { name: "Mês anterior", exact: true }).click();
  await page.waitForFunction(() => document.querySelector(".month-title").textContent === "Setembro 2026");
  await page.waitForFunction(() => !document.querySelector(".ledger-chips"));
  const outro = lanc.at(-1);
  await page.getByRole("button", { name: "Próximo mês", exact: true }).click();
  await page.locator(".ledger-chips .chip").waitFor();
  await page.locator(".ledger-chips .chip").click();
  await page.waitForFunction(() => !document.querySelector(".ledger-chips"));
  const limpo = lanc.at(-1);
  await ctx.close();
  assert.equal(fechado, 0);
  assert.deepEqual([noMes, outro, limpo], ["?mes=2026-10&dia=2026-10-07&guardado=true", "?mes=2026-09&guardado=true", "?mes=2026-10&guardado=true"]);
});

test("'Ver compras' e 'Ver em Lançamentos' do detalhe levam a categoria aos lançamentos sem `tipo`; 'Sem categoria' (fora do catálogo) entra no select", async () => {
  const { ctx, page, ir } = await abrir({ ...COM_DADO, categorias: "padrao" });
  const lanc = pedidos(page, "/api/v2/lancamentos");
  await ir("/gastos");
  await page.locator("#w-categorias .cat", { hasText: "sem categoria" }).click();
  await page.getByRole("button", { name: "Ver compras" }).click();
  await page.getByRole("button", { name: "Ver em Lançamentos" }).click();
  await page.waitForFunction(() => location.hash === "#/lancamentos");
  await page.locator(".lanc-linha").first().waitFor();
  const r = [lanc.at(-1), await page.getByRole("combobox", { name: /^Categoria/ }).inputValue(), await page.locator('select option[value="sem categoria"]').textContent()];
  await ctx.close();
  assert.deepEqual(r, ["?mes=2026-10&categoria=sem+categoria&guardado=true", "sem categoria", "Sem categoria"]);
});

test("catálogo de categorias fora do ar com categoria já escolhida: o select segue usável e 'Todas as categorias' limpa o filtro", async () => {
  const { ctx, page, ir } = await abrir({ ...COM_DADO });
  await ctx.route((u) => u.pathname === "/api/v2/categorias", (r) => r.fulfill({ status: 500, json: { error: { code: "internal_error", message: "x" } } }));
  const lanc = pedidos(page, "/api/v2/lancamentos");
  await ir("/gastos");
  await page.locator("#w-categorias .cat", { hasText: "sem categoria" }).click();
  await page.getByRole("button", { name: "Ver compras" }).click();
  await page.getByRole("button", { name: "Ver em Lançamentos" }).click();
  await page.getByText("Não foi possível carregar as categorias.").waitFor();
  const sel = page.getByRole("combobox", { name: /^Categoria/ });
  const habilitado = await sel.isEnabled();
  await sel.selectOption("");
  await page.waitForFunction(() => !document.querySelector(".ledger-chips") && document.querySelector("select.field").value === "");
  await page.locator(".lanc-linha").first().waitFor();
  const r = [habilitado, lanc.at(-1)];
  await ctx.close();
  assert.deepEqual(r, [true, "?mes=2026-10&guardado=true"]);
});

test("erro: o 500 da /gastos mostra 'Não deu para carregar.' nos três blocos e 'Tentar de novo' (44px) recupera", async () => {
  const { ctx, page, ir } = await abrir();
  let falha = true;
  await ctx.route((u) => u.pathname === "/api/v2/gastos", (r) => (falha
    ? r.fulfill({ status: 500, json: { error: { code: "internal_error", message: "x" } } }) : r.fulfill({ json: RESPOSTAS.gastos.padrao })));
  await ir("/gastos");
  await page.locator("#w-categorias [role=alert]").waitFor({ timeout: 15000 });
  const r = await page.evaluate(() => ["categorias", "calendario", "cat-detalhe"].map((id) => {
    const a = document.querySelector(`#w-${id} [role=alert]`);
    return [a?.querySelector("p").textContent, a?.querySelector("button").getBoundingClientRect().height >= 44];
  }));
  falha = false;
  await page.locator("#w-categorias [role=alert] button").click();
  await page.locator("#w-categorias .cat").first().waitFor();
  await ctx.close();
  assert.deepEqual(r, Array(3).fill(["Não deu para carregar.", true]));
});

test("mês fora do histórico: o aviso nos três blocos e nenhum R$ 0", async () => {
  const { ctx, page, ir } = await abrir({ gastos: "indisponivel", gastosCategoria: "indisponivel", espera: "#w-calendario" });
  await ir("/gastos");
  await page.locator("#w-cat-detalhe .faint").waitFor();
  const r = await page.evaluate(() => ["categorias", "calendario", "cat-detalhe"].map((id) => document.querySelector(`#w-${id} .w-body`).textContent));
  await ctx.close();
  assert.deepEqual(r, Array(3).fill("Este mês está fora do seu histórico."));
});

test("corte do plano no meio do mês: dias antes dele hachurados e desabilitados, saída fora do período, traço sem comparação, selo, série sem o mês cortado", async () => {
  const { ctx, page, ir } = await abrir({ gastos: "cortado", gastosCategoria: "cortado", espera: "#w-cat-detalhe .detail-facts" });
  await ir("/gastos");
  await page.locator("#w-categorias .cat").first().waitFor();
  const r = await page.evaluate(() => {
    const dias = [...document.querySelectorAll("#w-calendario .cal-day")];
    const t = (s) => document.querySelector(s)?.textContent.replace(/\s/g, " ");
    return {
      fora: dias.filter((b) => b.dataset.fora).length, desabilitados: dias.filter((b) => b.disabled).map((b) => b.textContent).join(),
      hachura: getComputedStyle(dias[0]).backgroundImage.startsWith("repeating-linear-gradient"), label: dias[0].getAttribute("aria-label"),
      lede: t("#w-calendario .w-lede"), nota: t("#w-calendario .cal-note"), traco: t("#w-categorias .cat-delta"),
      nome: document.querySelector("#w-categorias .cat").getAttribute("aria-label"),
      selos: [...document.querySelectorAll(".selo")].map((s) => s.textContent).join(),
      parcial: t("#w-cat-detalhe .detail-facts dt"), serie: [...document.querySelectorAll("#w-cat-detalhe .months li")].map((li) => [li.querySelector(".months-bar i") !== null, li.textContent]),
    };
  });
  await ctx.close();
  assert.equal(r.fora, 9);
  assert.equal(r.desabilitados, [1, 2, 3, 4, 5, 6, 7, 8, 9, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31].join());
  assert.equal(r.hachura, true);
  assert.equal(r.label, "qui, 1 de outubro: fora do seu histórico");
  assert.equal(r.lede, "média R$ 14,00/dia · 4 dias sem gasto"); // dia 10 a 14, só o dia 12 gastou: 70 / 5
  assert.equal(r.nota, "1 saída com data fora do período mostrado: R$ 50,00");
  assert.equal(r.traco, "—");
  assert.match(r.nome, /sem comparação: mês anterior fora do seu histórico/);
  assert.match(r.selos, /início do histórico/);
  assert.match(r.parcial, /No mês · parcial/);
  assert.deepEqual(r.serie.map((s) => s[0]), [true, true, false]);
  assert.match(r.serie[2][1], /fora do histórico.*out · mês em andamento/);
  assert.match(r.serie[1][1], /R\$ 250,00set$/);
});

test("mês sem gasto e categoria sem gasto: textos próprios, sem pedir o detalhe quando não há categoria", async () => {
  const { ctx, page, ir } = await abrir({ gastos: "vazio", espera: "#w-categorias" });
  const urls = pedidos(page, "/api/v2/gastos/categoria");
  await ir("/gastos");
  await page.locator("#w-cat-detalhe .faint").waitFor();
  const vazio = [await texto(page, "#w-categorias .w-body"), await texto(page, "#w-cat-detalhe .w-body"), await page.locator("#w-calendario .cal-day").count()];
  await ctx.close();
  assert.deepEqual(vazio, ["Nenhum gasto neste mês.", "Nenhum gasto neste mês.", 31]);
  assert.deepEqual(urls, []);

  const z = await abrir({ gastosCategoria: "zero", ...COM_DADO });
  await z.ir("/gastos");
  const zero = [await texto(z.page, "#w-cat-detalhe .detail-facts"), await z.page.locator("#w-cat-detalhe .detail-recent").count(),
    await texto(z.page, "#w-cat-detalhe .detail-cols section:last-child")];
  await z.ctx.close();
  assert.deepEqual(zero, ["No mêsR$ 0,00Compras0Valor médio—", 0, "Onde mais gastaNenhum gasto nesta categoria no mês."]);
});

test("categoria escolhida sem gasto no mês aberto: nenhuma linha atenuada e o 'Limpar' segue; com gasto, as outras atenuam", async () => {
  const { ctx, page, ir } = await abrir({ ...COM_DADO });
  await ir("/gastos");
  await page.locator("#w-categorias .cat", { hasText: "sem categoria" }).click();
  await page.locator("#w-categorias .cat[data-dim]").waitFor(); // controle positivo: no mês que a tem, a outra linha atenua
  // setembro, na fixture `muitas`: não tem "sem categoria"
  await ctx.route((u) => u.pathname === "/api/v2/gastos", (r) => r.fulfill({ json: { ...RESPOSTAS.gastos.muitas, mes: "2026-09", dia_de_corte: 30 } }));
  await page.getByRole("button", { name: "Mês anterior", exact: true }).click();
  await page.waitForFunction((n) => document.querySelectorAll("#w-categorias .cat").length === n, RESPOSTAS.gastos.muitas.categorias.length);
  const r = [await page.locator("#w-categorias .cat[data-dim]").count(), await page.getByRole("button", { name: /^Limpar/ }).count()];
  await ctx.close();
  assert.deepEqual(r, [0, 1]);
});

test("toda saída em outra moeda (sem categoria em reais): o aviso e o selo aparecem; o calendário leva o selo; o detalhe traz o aviso de outra moeda, não o 'Nenhum gasto' (sem outra moeda, ver o teste do mês vazio)", async () => {
  const { ctx, page, ir } = await abrir({ gastos: "todas_outra_moeda", espera: "#w-calendario" });
  await ir("/gastos");
  await page.locator("#w-categorias .selo").waitFor();
  const r = {
    categorias: await texto(page, "#w-categorias .w-body"), selosCat: await page.locator("#w-categorias .selo").allTextContents(),
    selosCal: await page.locator("#w-calendario .selo").allTextContents(), detalhe: await texto(page, "#w-cat-detalhe .w-body"),
  };
  await ctx.close();
  assert.equal(r.categorias, "R$ 0,00 gastos no mês3 saídas em outra moeda ficaram fora deste totaloutra moeda");
  assert.doesNotMatch(r.categorias, /Nenhum gasto/);
  assert.deepEqual([r.selosCat, r.selosCal], [["outra moeda"], ["outra moeda"]]);
  assert.equal(r.detalhe, "3 saídas em outra moeda ficaram fora deste total");
  assert.doesNotMatch(r.detalhe, /Nenhum gasto/);
});

test("selo 'soma arredondada' (arredondamento_por_grupo) aparece em Para onde vai, traduzido", async () => {
  const { ctx, page, ir } = await abrir({ espera: "#w-categorias .cats" });
  await ctx.route((u) => u.pathname === "/api/v2/gastos", (r) => r.fulfill({ json: { ...RESPOSTAS.gastos.padrao, motivos: ["arredondamento_por_grupo"] } }));
  await ir("/gastos");
  await page.locator("#w-categorias .selo").waitFor();
  const selos = await page.locator("#w-categorias .selo").allTextContents();
  await ctx.close();
  assert.deepEqual(selos, ["soma arredondada"]);
});

test("'Ver em Lançamentos' do detalhe larga o dia que estava escolhido: a lista abre sem `dia` e sem chip", async () => {
  const { ctx, page, ir } = await abrir({ ...COM_DADO });
  const lanc = pedidos(page, "/api/v2/lancamentos");
  await ir("/gastos");
  await page.locator("#w-calendario .cal-day", { hasText: /^7$/ }).click();
  await page.getByRole("button", { name: "Ver em Lançamentos" }).click();
  await page.locator(".ledger-chips .chip").waitFor(); // parte de um dia escolhido
  await page.evaluate(() => { location.hash = "#/gastos"; });
  await page.getByRole("button", { name: "Ver compras" }).click();
  await page.getByRole("button", { name: "Ver em Lançamentos" }).click();
  await page.waitForFunction(() => location.hash === "#/lancamentos");
  await page.locator(".lanc-linha").first().waitFor();
  const r = [lanc.at(-1), await page.locator(".ledger-chips .chip").count()];
  await ctx.close();
  assert.deepEqual(r, ["?mes=2026-10&categoria=alimentacao&guardado=true", 0]);
});

test("últimas compras: só a de data futura leva ' · agendado'", async () => {
  const { ctx, page, ir } = await abrir({ ...COM_DADO });
  const p = RESPOSTAS.gastos_categoria.padrao;
  await ctx.route((u) => u.pathname === "/api/v2/gastos/categoria", (r) => r.fulfill({ json: { ...p, ultimas: [{ ...p.ultimas[0], data: "2026-10-20", descricao: "Futura" }, p.ultimas[1]] } }));
  await ir("/gastos");
  await page.locator("#w-cat-detalhe .detail-recent li").nth(1).waitFor();
  const linhas = await page.locator("#w-cat-detalhe .detail-recent li").evaluateAll((l) => l.map((e) => e.textContent.replace(/\s/g, " ")));
  await ctx.close();
  assert.match(linhas[0], /Futura.*Banco · agendado/);
  assert.doesNotMatch(linhas[1], /agendado/);
});

test("sem demonstração: nenhum selo no título, nem texto do protótipo, e as âncoras do guia seguem", async () => {
  const { ctx, page, erros, ir } = await abrir({ espera: "#w-categorias .cats" });
  const r = {};
  for (const rota of ["/gastos", "/"]) {
    await ir(rota);
    await page.locator("#w-categorias .cat").first().waitFor();
    r[rota] = await page.evaluate(() => [document.querySelectorAll("#w-categorias .w-title > .selo, #w-calendario .w-title > .selo, #w-cat-detalhe .w-title > .selo").length,
      /Delivery|Moradia|maior gasto do dia a dia|demonstração/.test([...document.querySelectorAll("#w-categorias, #w-calendario, #w-cat-detalhe")].map((e) => e.textContent).join(" ")),
      document.querySelectorAll('[data-guia="categorias.lista"]').length, document.querySelectorAll('[data-guia="categorias.item"]').length,
      [...document.querySelectorAll("[data-widget-id]")].filter((w) => ["categorias", "calendario"].includes(w.dataset.widgetId)).map((w) => w.querySelector(".w-title > .selo")?.textContent ?? null).join()]);
  }
  await ctx.close();
  assert.deepEqual(r["/gastos"].slice(0, 4), [0, false, 1, 1]);
  assert.deepEqual(r["/"], [0, false, 1, 1, ","]); // no Resumo, o selo de exemplo é dos outros blocos, não destes dois
  assert.deepEqual(erros, []);
});

for (const width of [1440, 390]) {
  test(`${width}px: sem rolagem lateral; nome com reticência, valor sem quebra, colunas do detalhe e célula do calendário`, async () => {
    const { ctx, page, ir } = await abrir({ width, gastos: "muitas" });
    const medidas = {};
    for (const rota of ["/gastos", "/"]) {
      await ir(rota);
      await page.locator(rota === "/" ? "#w-categorias .cat" : "#w-cat-detalhe .detail-cols").first().waitFor();
      medidas[rota] = await page.evaluate(() => {
        const e = document.scrollingElement;
        const longo = [...document.querySelectorAll("#w-categorias .cat-nome")].find((n) => n.textContent.startsWith("Despesas gerais"));
        const val = [...document.querySelectorAll("#w-categorias .cat-val")];
        const cols = document.querySelector("#w-cat-detalhe .detail-cols");
        const dia = document.querySelector("#w-calendario .cal-day:not(:disabled)")?.getBoundingClientRect();
        return {
          lateral: e.scrollWidth - e.clientWidth, reticencia: getComputedStyle(longo).textOverflow === "ellipsis" && longo.scrollWidth > longo.clientWidth,
          valorUmaLinha: val.every((v) => v.getBoundingClientRect().height < 20 && getComputedStyle(v).whiteSpace === "nowrap"),
          colunas: cols ? getComputedStyle(cols).gridTemplateColumns.split(" ").length : null,
          celula: dia && [Math.round(dia.width * 100) / 100, Math.round(dia.height * 100) / 100],
        };
      });
    }
    await ctx.close();
    console.log(`# ${width}px:`, JSON.stringify(medidas));
    for (const m of Object.values(medidas)) assert.deepEqual([m.lateral, m.reticencia, m.valorUmaLinha], [0, true, true]);
    assert.equal(medidas["/gastos"].colunas, width === 390 ? 1 : 2); // empilhadas abaixo de 628px
    assert.ok(Math.abs(medidas["/gastos"].celula[0] - medidas["/gastos"].celula[1]) < 1, String(medidas["/gastos"].celula)); // célula quadrada
  });
}

for (const width of [1440, 390]) {
  test(`${width}px, valor máximo e nome de 42 caracteres: nada passa da largura (página, linhas de categoria, lugares, últimas compras); título do detalhe com reticência e 'Ver compras' à vista`, async () => {
    const { ctx, page, ir } = await abrir({ width, gastos: "maximo", gastosCategoria: "maximo" });
    const medidas = {};
    for (const rota of ["/gastos", "/"]) {
      await ir(rota);
      await page.locator("#w-categorias .cat").first().waitFor();
      if (rota === "/gastos") await page.locator("#w-cat-detalhe .detail-recent li").first().waitFor();
      medidas[rota] = await page.evaluate(() => {
        const e = document.scrollingElement;
        const nome = document.querySelector("#w-cat-detalhe .detail-nome");
        const abrir = [...document.querySelectorAll("#w-cat-detalhe .link")].find((b) => b.textContent.includes("Ver compras"))?.getBoundingClientRect();
        return {
          lateral: e.scrollWidth - e.clientWidth,
          estouram: [...document.querySelectorAll(".cat, .places li, .detail-recent .inv-list li")].filter((x) => x.scrollWidth > x.clientWidth).map((x) => `${x.className || x.tagName}:${x.scrollWidth}>${x.clientWidth}`),
          // o texto da variação (alinhado à direita, transborda para a esquerda) não pode entrar no valor ao lado
          cruzam: [...document.querySelectorAll("#w-categorias .cat")].filter((c) => {
            const r = document.createRange(); r.selectNodeContents(c.querySelector(".cat-delta"));
            return r.getBoundingClientRect().left < c.querySelector(".cat-val").getBoundingClientRect().right;
          }).length,
          valores: document.querySelector("#w-categorias .cat-val").textContent,
          titulo: nome && { reticencia: getComputedStyle(nome).textOverflow === "ellipsis", cortado: nome.scrollWidth > nome.clientWidth },
          abrir: abrir && [abrir.width > 0, abrir.right <= innerWidth],
        };
      });
    }
    await ctx.close();
    console.log(`# ${width}px máximo:`, JSON.stringify(medidas));
    for (const m of Object.values(medidas)) assert.deepEqual([m.lateral, m.estouram, m.cruzam, m.valores], [0, [], 0, "R$ 999.999.990,00"]);
    const g = medidas["/gastos"];
    // 42 caracteres não cabem no celular (terminam em reticência); em 1440 cabem desde que o botão virou "Ver compras" (era "Abrir lançamentos →")
    assert.deepEqual(g.titulo, { reticencia: true, cortado: width === 390 });
    assert.deepEqual(g.abrir, [true, true]);
  });
}
