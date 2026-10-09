/**
 * /painel, Etapa 1 PR C: o Resumo sobre a /api/v2 (perfil, contas e Entrou/Saiu reais).
 *
 *   · contas fora do total (outra moeda, pausada, saldo ausente) só atrás do botão; o total
 *     diz quantas; saldo ausente é "—", nunca "R$ 0,00"; sem conta fora, nem nota nem botão;
 *   · o modal de perfil só abre com o perfil `null` do servidor, nunca enquanto carrega;
 *   · escolher perfil faz PUT com x-csrf-token e o corpo certo; no 500 e no 403 desfaz e avisa; no
 *     403 `password_required` o aviso leva o "Criar senha" (para a /home), no modal e fora dele;
 *   · "Recomeçar do zero" (o servidor volta a `null` e o SSE avisa): o modal reabre;
 *   · Entrou e Saiu do fixture com centavos, o mês anterior só quando não é null, motivos como selos;
 *   · selo "demonstração" em todo bloco inventado, e nunca em Contas, Entrou e Saiu; com
 *     backend também na faixa do Piggy, no extrato e na conversa (no protótipo, onde tudo é de
 *     exemplo, não); o título do Resumo nunca leva (decisão do dono, PR C2: só os blocos);
 *   · o mês da página é o corrente em America/Sao_Paulo (não o do aparelho nem o do UTC),
 *     o seletor tem os 6 últimos e cada um pede o seu `mes=`;
 *   · título e selo inteiros, sem rolagem lateral e setas do mês com 44px, no desktop e no celular.
 *
 * Rodar:  npm run test:frontend   (abre o artefato commitado frontend/dashboard-app.*: mudou webapp/src, rode `npm --prefix webapp run build`)
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { RESPOSTAS, abrirPainel, exigeArtefatoEmDia } from "./_painel.mjs";

let browser;
before(async () => { exigeArtefatoEmDia(); browser = await chromium.launch(); });
after(() => browser?.close());

const abrir = (opts) => abrirPainel(browser, opts);

const visivel = (page, texto) => page.locator("#w-contas").getByText(texto, { exact: true }).isVisible();
const blocos = (page) => page.evaluate(() => [...document.querySelectorAll("[data-widget-id]")].map((w) => w.dataset.widgetId).sort());
// Valor de um <number-flow>: os dígitos moram no shadow DOM, um por coluna (--current).
const numero = (loc) => loc.evaluate((v) => [...v.shadowRoot.querySelectorAll(".symbol__value, [part~=digit]")]
  .map((n) => (n.matches("[part~=digit]") ? n.style.getPropertyValue("--current").trim() : n.textContent)).join("").replace(/ /g, " "));

test("contas fora do total: fechado não aparecem e o total diz quantas; abrir pelo teclado mostra os motivos, sem R$ 0,00", async () => {
  const { ctx, page, erros, ir } = await abrir({ width: 390 });
  await ir();
  const lede = await page.locator("#w-contas .w-lede").textContent();
  const fechado = [await visivel(page, "Wise · Conta em dólar"), await visivel(page, "Bradesco · Conta"), await page.locator("#w-contas .contas-mais").getAttribute("aria-expanded")];
  const botao = page.locator("#w-contas .contas-mais");
  await botao.focus();
  const foco = await botao.evaluate((b) => [b.matches(":focus-visible"), getComputedStyle(b).outlineStyle, Math.round(b.getBoundingClientRect().height)]);
  await page.keyboard.press("Enter");
  const fora = page.locator("#w-contas ul.contas").nth(1);
  const linhas = await fora.locator(".conta").evaluateAll((ls) => ls.map((l) => l.innerText.replace(/\n+/g, " | ")));
  const texto = await page.locator("#w-contas").innerText();
  const r = [await botao.getAttribute("aria-expanded"), await page.evaluate(() => document.scrollingElement.scrollWidth - document.scrollingElement.clientWidth)];
  await ctx.close();
  assert.equal(lede, "Saldo de hoje · carteira a confirmar · 3 contas fora do total");
  assert.deepEqual(fechado, [false, false, "false"]);
  assert.deepEqual(foco, [true, "solid", 44]);
  assert.deepEqual(linhas, ["Wise · Conta em dólar | US$ 30,00 | outra moeda", "Bradesco · Conta | — | conexão pausada", "Conta | — | saldo ausente"]);
  assert.ok(!texto.includes("R$ 0,00"), texto);
  assert.deepEqual(r, ["true", 0]);
  assert.deepEqual(erros, []);
});

test("contas no total: carteira com selo, cada conta com o saldo do texto decimal e o motivo", async () => {
  const { ctx, page, ir } = await abrir();
  await ir();
  const linhas = await page.locator("#w-contas ul.contas").first().locator(".conta").evaluateAll((ls) => ls.map((l) => l.innerText.replace(/\n+/g, " | ")));
  const total = await page.locator("#w-contas .stat-value").textContent();
  await ctx.close();
  assert.equal(total, "R$ 1.360,00");
  assert.deepEqual(linhas, [
    "Carteira Piggy | R$ 100,00 | a confirmar conciliação pendente movimentos pendentes espécie a conferir",
    "Nubank · Conta | R$ 1.000,00",
    "Itaú · Conta corrente | R$ 200,00 | banco desatualizado",
    "Nubank · Conta antiga | R$ 50,00 | fora da última atualização",
    "Inter | R$ 10,00 | moeda presumida",
  ]);
});

test("positivo: sem conta fora do total, nem nota nem botão", async () => {
  const { ctx, page, ir } = await abrir({ contas: "so_carteira" });
  await ir();
  const r = [await page.locator("#w-contas .w-lede").textContent(), await page.locator("#w-contas .contas-mais").count(), await page.locator("#w-contas .conta").count()];
  await ctx.close();
  assert.deepEqual(r, ["Saldo de hoje · carteira a confirmar", 0, 1]);
});

test("o modal de perfil só abre com o perfil null: não com padrao, nem enquanto o GET carrega", async () => {
  const vistos = {};
  for (const perfil of [null, "padrao", "investir"]) {
    const { ctx, page, ir } = await abrir({ perfil });
    await ir();
    vistos[perfil] = await page.locator(".picker[open]").count();
    await ctx.close();
  }
  const { ctx, page, ir } = await abrir({ perfil: null, espera: null });
  let solta;
  const preso = new Promise((r) => { solta = r; });
  await ctx.route("**/api/v2/perfil", async (r) => { await preso; return r.fulfill({ json: { perfil: null } }); });
  await ir();
  await page.locator(".board [role=status]").waitFor();
  await page.waitForTimeout(300);
  const carregando = [await page.locator(".picker[open]").count(), await page.locator("#board-profile").count()];
  solta();
  await page.locator(".picker[open]").waitFor();
  await ctx.close();
  assert.deepEqual(vistos, { null: 1, padrao: 0, investir: 0 });
  assert.deepEqual(carregando, [0, 0]);
});

test("escolher no modal: PUT com x-csrf-token e o corpo certo; o painel é o do perfil", async () => {
  const { ctx, page, ir } = await abrir({ perfil: null });
  const puts = [];
  page.on("request", (r) => { if (r.method() === "PUT") puts.push([new URL(r.url()).pathname, r.headers()["x-csrf-token"], r.postDataJSON()]); });
  await ir();
  await page.getByRole("button", { name: /^Investir/ }).click();
  await page.waitForFunction(() => document.querySelector("#board-profile")?.value === "investir" && document.querySelector('[data-widget-id="rendimento"]'));
  const r = [await page.locator(".picker[open]").count(), await blocos(page)];
  await ctx.close();
  assert.deepEqual(puts, [["/api/v2/perfil", "tok-123", { perfil: "investir" }]]);
  assert.deepEqual(r, [0, ["contas", "metas", "patrimonio", "piggy", "rendimento", "resumo", "simulador", "wealth"]]);
});

// O texto do aviso e o "Criar senha" (só no password_required), lidos do modal e do painel.
const GENERICO = ["Não foi possível salvar agora", 0];
const SENHA = ["Crie sua senha para salvar o seu painel. Depois de criar, volte para o painel novo. Criar senha", 1];
const aviso = async (page, sel) => [await page.locator(sel).textContent(), await page.locator(`${sel} a[href="/home"]`).count()];
for (const [nome, erro, esperado] of [["500", RESPOSTAS.erros["500"], GENERICO], ["403 password_required", RESPOSTAS.erros["403_password_required"], SENHA]]) {
  test(`PUT ${nome} no modal: desfaz (o modal volta) e avisa`, async () => {
    const { ctx, page, ir } = await abrir({ perfil: null });
    // O PUT só falha depois de a tela já ter trocado para a escolha (otimista): é essa troca que o
    // "desfaz" desfaz, e o seletor desmonta e volta. Sem a espera o caminho dependia de quem
    // chegava primeiro, a resposta do mock ou o quadro do React; o outro caminho é o teste seguinte.
    await ctx.route("**/api/v2/perfil", async (r) => {
      if (r.request().method() !== "PUT") return r.fallback();
      await page.waitForFunction(() => document.querySelector("#board-profile")?.value === "investir", null, { timeout: 5000 });
      return r.fulfill({ status: erro.status, json: erro.body });
    });
    await ir();
    await page.getByRole("button", { name: /^Investir/ }).click();
    await page.locator(".picker[open] .picker-aviso").waitFor();
    const r = [await aviso(page, ".picker-aviso"), await aviso(page, ".board-aviso"), await page.locator(".picker[open]").count()];
    await ctx.close();
    assert.deepEqual(r, [esperado, esperado, 1]);
  });

  // O erro chega antes de o React pintar a escolha otimista: `null → escolha → null` é agrupado e o
  // seletor não desmonta. O notify do TanStack Query vai num `setTimeout(0)`; atrasado, a resposta
  // imediata do mock ganha dele de forma determinística (ao natural, é a corrida rara do CI).
  test(`PUT ${nome} no modal, com o erro antes de a tela pintar a escolha: o modal reabre e avisa`, async () => {
    const { ctx, page, ir } = await abrir({ perfil: null });
    await ctx.addInitScript(() => { const st = window.setTimeout.bind(window); window.setTimeout = (f, d, ...a) => st(f, d || 200, ...a); });
    await ctx.route("**/api/v2/perfil", (r) => (r.request().method() === "PUT" ? r.fulfill({ status: erro.status, json: erro.body }) : r.fallback()));
    await ir();
    await page.getByRole("button", { name: /^Investir/ }).click();
    await page.locator(".picker[open] .picker-aviso").waitFor();
    const r = [await aviso(page, ".picker-aviso"), await aviso(page, ".board-aviso"), await page.locator(".picker[open]").count()];
    await ctx.close();
    assert.deepEqual(r, [esperado, esperado, 1]);
  });

  test(`PUT ${nome} pelo seletor: volta ao perfil de antes e avisa`, async () => {
    const { ctx, page, ir } = await abrir({ perfil: "padrao" });
    await ctx.route("**/api/v2/perfil", (r) => (r.request().method() === "PUT" ? r.fulfill({ status: erro.status, json: erro.body }) : r.fallback()));
    await ir();
    const antes = await blocos(page);
    await page.selectOption("#board-profile", "investir");
    await page.locator(".board-aviso", { hasText: esperado[0].slice(0, 20) }).waitFor();
    await page.waitForFunction(() => document.querySelector("#board-profile").value === "padrao");
    const r = [await blocos(page), await page.locator(".picker[open]").count(), await aviso(page, ".board-aviso")];
    await ctx.close();
    assert.deepEqual(r, [antes, 0, esperado]);
    assert.ok(antes.includes("hero") && !antes.includes("rendimento"));
  });
}

// A recarga depois do erro devolveria o valor do servidor sozinha; pendurada (rede caindo),
// só o desfazer do próprio erro tira da tela o perfil que não foi gravado.
test("PUT 500 com a recarga do perfil pendurada: desfaz na hora, sem esperar o servidor", async () => {
  const { ctx, page, ir } = await abrir({ perfil: "padrao" });
  let gets = 0;
  await ctx.route("**/api/v2/perfil", (r) => {
    if (r.request().method() === "PUT") return r.fulfill({ status: 500, json: RESPOSTAS.erros["500"].body });
    return gets++ === 0 ? r.fulfill({ json: { perfil: "padrao" } }) : new Promise(() => {});
  });
  await ir();
  await page.selectOption("#board-profile", "investir");
  await page.locator(".board-aviso", { hasText: "Não foi possível salvar agora" }).waitFor();
  await page.waitForTimeout(100);
  const r = [await page.locator("#board-profile").inputValue(), (await blocos(page)).includes("rendimento"), gets > 1];
  await ctx.close();
  assert.deepEqual(r, ["padrao", false, true]);
});

test("Recomeçar do zero: o servidor volta a null e o aviso do SSE reabre o modal", async () => {
  const { ctx, page, ir } = await abrir({ perfil: "padrao" });
  let gets = 0;
  await ctx.route("**/api/v2/perfil", (r) => r.fulfill({ json: { perfil: gets++ === 0 ? "padrao" : null } }));
  let avisa;
  const aviso = new Promise((r) => { avisa = r; });
  await ctx.route("**/api/v2/eventos", async (r) => { await aviso; return r.fulfill({ status: 200, headers: { "content-type": "text/event-stream" }, body: 'retry: 600000\ndata: {"recurso":"tudo"}\n\n' }); });
  await ir();
  const antes = await page.locator(".picker[open]").count();
  avisa();
  await page.locator(".picker[open]").waitFor();
  await ctx.close();
  assert.equal(antes, 0);
});

test("Entrou e Saiu do fixture com centavos, com o mês anterior inteiro; sem selo de demonstração", async () => {
  const { ctx, page, ir } = await abrir();
  const pedidos = [];
  page.on("request", (r) => { const u = new URL(r.url()); if (u.pathname === "/api/v2/resumo-do-mes") pedidos.push(u.search); });
  await ir();
  const st = page.locator("#w-resumo .stat");
  await st.first().locator(".stat-value").waitFor();
  const r = [];
  for (const i of [0, 1]) r.push([await numero(st.nth(i).locator(".stat-value")), await st.nth(i).locator(".stat-delta").textContent(), await st.nth(i).locator(".selo").count()]);
  await ctx.close();
  assert.deepEqual(r, [["R$ 1.040,00", "em setembro: R$ 5.200,00", 0], ["R$ 440,00", "em setembro: R$ 3.980,55", 0]]);
  assert.deepEqual([...new Set(pedidos)], ["?mes=2026-10"]);
});

test("mês anterior null: sem referência; os motivos viram selos em português", async () => {
  const { ctx, page, ir } = await abrir({ resumo: "todos_os_motivos" });
  await ir();
  const st = page.locator("#w-resumo .stat").first();
  await st.locator(".stat-value").waitFor();
  const r = [await st.locator(".stat-delta").count(), await st.locator(".selos .selo").allTextContents(), await numero(st.locator(".stat-value"))];
  await ctx.close();
  assert.deepEqual(r, [0, ["conciliação pendente", "movimentos pendentes", "banco desatualizado", "início do histórico"], "R$ 0,00"]);
});

test("selo de demonstração: em todo bloco inventado do Resumo, e não em Contas, Entrou e Saiu", async () => {
  const { ctx, page, ir } = await abrir();
  await ir();
  await page.locator("#w-resumo .stat-value").first().waitFor();
  const r = await page.evaluate(() => {
    const blocos = Object.fromEntries([...document.querySelectorAll("[data-widget-id]")].map((w) => [w.dataset.widgetId, !!w.querySelector(".w-title > .selo")]));
    const stats = [...document.querySelectorAll("#w-resumo .stat")].map((s) => s.querySelector(".selo")?.textContent ?? null);
    return { blocos, stats, etiqueta: document.body.innerText.includes("Dados de demonstração") };
  });
  await ctx.close();
  // "Saldo previsto" é a /api/v2/previsao desde a Etapa 3 PR3 (decisão do dono, Q1 = a)
  assert.deepEqual(r.blocos, { contas: false, hero: false, resumo: false, categorias: true, calendario: true, simulador: true, compromissos: true, piggy: true, metas: true, patrimonio: true });
  assert.deepEqual(r.stats, [null, null, "demonstração", "demonstração"]);
  assert.equal(r.etiqueta, false);
});

test("rendimento (preset Investir) leva o selo; a data é a de hoje, não a da demonstração", async () => {
  const { ctx, page, ir } = await abrir({ perfil: "investir" });
  await ir();
  const r = [await page.locator('[data-widget-id="rendimento"] .w-title > .selo').textContent(), await page.locator(".board-hint").textContent()];
  await ctx.close();
  assert.deepEqual(r, ["demonstração", "hoje é 2 de outubro de 2026"]); // o relógio congelado, não os 23/09 da demonstração
});

// --- O rodapé do menu: bancos via Open Finance ---------------------------------------
// A /api/v2/contas só traz conta BANK: conexão só de cartão ou investimento chega como
// `contas: []`, então zero banco vivo esconde a linha em vez de dizer "nenhum".

const TODOS = RESPOSTAS.contas.todos_os_estados;
const ativa = (id, instituicao) => ({ ...TODOS.contas[0], id, instituicao });
const pausada = (id, instituicao) => ({ ...TODOS.contas[5], id, instituicao });
const rodape = async (contas) => {
  const { ctx, page, ir } = await abrir();
  await ctx.route("**/api/v2/contas", (r) => r.fulfill({ json: { ...TODOS, fora_do_total: contas.filter((c) => !c.no_total).length, contas } }));
  await ir();
  await page.locator("#w-contas .stat-value").waitFor();
  const r = await page.locator(".rail-foot").innerText();
  await ctx.close();
  return r;
};

for (const [nome, contas, esperado] of [
  ["2 instituições ativas", [ativa(1, "Nubank"), ativa(2, "Itaú"), ativa(3, "Nubank")], "2 bancos via Open Finance"],
  ["1 ativa e 1 pausada: a pausada não conta", [ativa(1, "Nubank"), pausada(2, "Bradesco")], "1 banco via Open Finance"],
  ["só pausadas: some, nunca \"Nenhum banco conectado\"", [pausada(1, "Bradesco"), pausada(2, "Itaú")], ""],
  ["contas: [] (pode ser conexão só de cartão): some, nunca \"Nenhum banco conectado\"", [], ""],
]) {
  test(`rodapé do menu, ${nome}`, async () => {
    assert.equal(await rodape(contas), esperado);
  });
}

test("rodapé do menu no protótipo: 2 bancos via Open Finance", async () => {
  const { ctx, page, ir } = await abrir({ demo: true, espera: "#page-title" });
  await ir();
  const r = await page.locator(".rail-foot").innerText();
  await ctx.close();
  assert.equal(r, "2 bancos via Open Finance");
});

// --- O mês da página ---------------------------------------------------------------

const pedidosDoMes = (page) => {
  const meses = [];
  page.on("request", (r) => { const u = new URL(r.url()); if (u.pathname === "/api/v2/resumo-do-mes") meses.push(u.searchParams.get("mes")); });
  return meses;
};
// O título sem o selo (o React parte o texto em vários nós).
const mesDaPagina = (page) => page.evaluate(() => [document.querySelector(".month-title").textContent,
  [...document.querySelector("#page-title").childNodes].filter((n) => !n.classList?.contains("selo")).map((n) => n.textContent).join("").trim()]);

test("o mês da página é o corrente: outubro selecionado, sem próximo, e o resumo pede mes=2026-10", async () => {
  const { ctx, page, erros, ir } = await abrir();
  const meses = pedidosDoMes(page);
  await ir();
  await page.locator("#w-resumo .stat-value").first().waitFor();
  const r = [await mesDaPagina(page), await page.getByRole("button", { name: "Próximo mês", exact: true }).isDisabled()];
  await ctx.close();
  assert.deepEqual(r, [["Outubro 2026", "Resumo de outubro"], true]);
  assert.deepEqual([...new Set(meses)], ["2026-10"]);
  assert.deepEqual(erros, []);
});

// O aparelho em UTC discrimina: num `new Date()` ingênuo, 02:30 UTC de 1º/11 já é novembro.
for (const [agora, esperado, mes] of [["2026-11-01T02:30:00Z", "Outubro", "2026-10"], ["2026-11-01T03:30:00Z", "Novembro", "2026-11"]]) {
  test(`virada do mês no fuso de SP: ${agora} (aparelho em UTC) é ${esperado}`, async () => {
    const { ctx, page, ir } = await abrir({ agora, tz: "UTC" });
    const meses = pedidosDoMes(page);
    await ir();
    await page.locator("#w-resumo .stat-value").first().waitFor();
    const r = await mesDaPagina(page);
    await ctx.close();
    assert.deepEqual(r, [`${esperado} 2026`, `Resumo de ${esperado.toLowerCase()}`]);
    assert.deepEqual([...new Set(meses)], [mes]);
  });
}

test("seletor: 6 meses reais; voltar pede o mês escolhido e os blocos de exemplo não quebram", async () => {
  const { ctx, page, erros, ir } = await abrir();
  const meses = pedidosDoMes(page);
  await ir();
  const antes = page.getByRole("button", { name: "Mês anterior", exact: true });
  await antes.click();
  await page.waitForFunction(() => document.querySelector(".month-title").textContent === "Setembro 2026");
  await page.locator("#w-resumo .stat-value").first().waitFor();
  const setembro = await mesDaPagina(page);
  for (let i = 0; i < 4; i++) await antes.click();
  await page.waitForFunction(() => document.querySelector(".month-title").textContent === "Maio 2026");
  await page.waitForFunction(() => document.querySelectorAll("#w-resumo .stat-value").length);
  const r = [await antes.isDisabled(), await page.locator("#w-hero .w-title > .selo").count(), await page.locator("#w-calendario .w-title > .selo").count()];
  await ctx.close();
  assert.deepEqual(setembro, ["Setembro 2026", "Resumo de setembro"]);
  assert.deepEqual([...new Set(meses)], ["2026-10", "2026-09", "2026-08", "2026-07", "2026-06", "2026-05"]);
  assert.deepEqual(r, [true, 0, 1]); // o Saldo previsto é real (Q1 = a): sem selo; o calendário segue de exemplo
  assert.deepEqual(erros, []);
});

// --- Selo nas telas de exemplo -------------------------------------------------------

// Na conversa o selo é por mensagem (a resposta de exemplo leva, a real não): o título não leva.
const SELO = { "/": [".piggy-band-by > .selo"], "/lancamentos": ["#ledger-h > .selo"], "/piggy": ["#page-title > .selo", ".chat > .msg-piggy .msg-by > .selo"] };
// Texto livre sem assunto: o Piggy responde com os atalhos (resposta de exemplo).
async function conversar(page) {
  await page.locator("#askbar-input").fill("oi");
  await page.locator("#askbar-input").press("Enter");
  await page.locator(".chat > .msg-piggy").waitFor();
}
for (const demo of [false, true]) {
  test(`selo "demonstração" na faixa do Piggy, no extrato demonstrativo e na conversa: ${demo ? "não no protótipo" : "com backend"}; no título do Resumo, nunca`, async () => {
    const { ctx, page, ir } = await abrir({ demo, espera: "#page-title" });
    const r = {};
    for (const [rota, seletores] of Object.entries(SELO)) {
      await ir(rota);
      if (rota === "/") {
        await page.locator("#board-profile").waitFor();
        r.titulo = [await page.locator("#page-title").textContent(), await page.locator("#page-title .selo").count()];
      }
      if (rota === "/piggy") await conversar(page);
      for (const s of seletores) r[s + " " + rota] = await page.locator(s).allTextContents();
      if (rota === "/lancamentos" && !demo) assert.equal(await page.locator("#w-lancamentos").getAttribute("data-dado"), "real");
    }
    await ctx.close();
    const um = demo ? [] : ["demonstração"];
    const titulo = [demo ? "Resumo de setembro" : "Resumo de outubro", 0];
    assert.deepEqual(r, { titulo, ".piggy-band-by > .selo /": um, "#ledger-h > .selo /lancamentos": [], "#page-title > .selo /piggy": [], ".chat > .msg-piggy .msg-by > .selo /piggy": um });
  });
}

// Título e selo inteiros: nenhum dos dois com reticência nem fora da cabeça.
for (const width of [1440, 1100, 390, 375]) {
  test(`${width}px: título e selo inteiros, sem rolagem lateral, setas do mês com 44px`, async () => {
    const { ctx, page, ir } = await abrir({ width, espera: "#page-title" });
    const r = {};
    for (const rota of Object.keys(SELO)) {
      await ir(rota);
      if (rota === "/") await page.locator("#w-resumo .stat-value").first().waitFor();
      if (rota === "/piggy") await conversar(page);
      r[rota] = await page.evaluate(() => {
        const cortado = (e) => e.scrollWidth > e.clientWidth;
        const fora = (s, h) => { const a = s.getBoundingClientRect(), b = h.getBoundingClientRect(); return a.right > b.right + 0.5 || a.right > innerWidth; };
        const ruins = [];
        for (const s of document.querySelectorAll(".w-title > .selo, #page-title > .selo, #ledger-h > .selo, .piggy-band-by > .selo, .msg-by > .selo")) {
          const h = s.parentElement;
          const titulo = h.matches(".w-title") ? h.firstElementChild : h;
          if (cortado(titulo) || cortado(s) || fora(s, h)) ruins.push(h.id || h.className);
        }
        const se = document.scrollingElement;
        const setas = [...document.querySelectorAll(".month-switch button")].map((b) => Math.min(b.offsetWidth, b.offsetHeight) >= 44);
        return { ruins, rolagem: se.scrollWidth - se.clientWidth, setas };
      });
    }
    await ctx.close();
    const setas = [true, true];
    assert.deepEqual(r, { "/": { ruins: [], rolagem: 0, setas }, "/lancamentos": { ruins: [], rolagem: 0, setas }, "/piggy": { ruins: [], rolagem: 0, setas: [] } });
  });
}
