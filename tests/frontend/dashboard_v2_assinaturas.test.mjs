/**
 * Assinaturas no dashboard v2 (webapp/src/dashboard/widgets/Subscriptions.tsx): o card do
 * Resumo e a página /assinaturas, sobre a /api/v2/assinaturas servida por um mock com
 * estado (as marcas por chave, como db/of_recurring.py).
 *
 *   · card: total, as 3 maiores ATIVAS, a seta leva à página;
 *   · página: as duas seções, dia, próxima, meio, reajuste, "parece cancelada", totais,
 *     e a etiqueta de demonstração (a barra lateral e o Piggy seguem sintéticos);
 *   · Essencial: o 403 `pro_required` vira o convite, sem ação e sem POST;
 *   · POST com o CSRF do auth-refresh.js e Content-Type JSON (sem ele o FastAPI dá 422),
 *     Desfazer voltando ao estado anterior (também depois da última, de uma falha dele e de
 *     uma falha da ação seguinte), falha com texto fixo e recarga, resposta perdida com a
 *     marca gravada vira sucesso pela recarga, botões travados enquanto
 *     o POST está no ar (também depois de sair e voltar), um POST por clique duplo, aviso
 *     que cobre o grupo da mesma chave (1 e 2 a mais), vazio "ignorou tudo" sem o link;
 *   · Ignoradas (#751): a seção recolhível depois de recarregar, "Voltar a mostrar" com a
 *     marca guardada (Smart Fit volta marcada), o Desfazer dele, "ignorou tudo" também
 *     depois de recarregar (página e card), o grupo da Apple, e a reconciliação do POST
 *     ambíguo pelas `ignoradas`;
 *   · protótipo sem API; layout a 375 e a 1440 (seção aberta); `isoDay` no fuso de São Paulo.
 *
 * Rodar:  npm run test:frontend   (abre o artefato commitado: mudou webapp/src, rode
 *         `npm --prefix webapp run build`)
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { ORIGIN, PAINEL, PROTOTIPO, RAIZ, RESPOSTAS, exigeArtefatoEmDia, servir } from "./_painel.mjs";

const CHEIA = RESPOSTAS.assinaturas.cheia;
const CSRF = "abc+def"; // no cookie vai "abc%2Bdef": prova o decodeURIComponent
const SERV = "#w-assinaturas-servicos";
const OUTRAS = "#w-assinaturas-outras";
const IGN = "#w-assinaturas-ignoradas";
const ACOES = ".sub-acoes button, .sub-aviso button";

let browser;
before(async () => { exigeArtefatoEmDia(); browser = await chromium.launch(); });
after(() => browser?.close());

// O backend em miniatura: marca por chave; "ignorar" vai para `ignoradas`, "assinatura" vai
// para serviços, "nenhuma" apaga a marca e volta ao lugar natural (a marcada da fixture só
// estava em serviços pela marca). `antes` é o `assinatura_antes` de db/of_recurring.py, com
// a mesma regra do upsert: em `ignoradas`, `marcada` é a marca guardada. Ordem por valor e
// total só dos serviços ativos. Dinheiro é texto, como na API: ordena por Number e soma em
// centavos inteiros. O estado vive no contexto: sobrevive ao `page.reload()`.
function backend() {
  const marca = new Map(CHEIA.servicos.filter((a) => a.marcada).map((a) => [a.chave, "assinatura"]));
  const antes = new Map();
  const itens = [...CHEIA.servicos.map((a) => [a, a.marcada ? "o" : "s"]), ...CHEIA.outras.map((a) => [a, "o"])];
  const marcar = (chave, status) => {
    const m = marca.get(chave);
    if (status === "nenhuma") { marca.delete(chave); antes.delete(chave); return; }
    antes.set(chave, status === "ignorar" && (m === "assinatura" || (m === "ignorar" && antes.get(chave) === true)));
    marca.set(chave, status);
  };
  const lista = () => {
    const servicos = [], outras = [], ignoradas = [];
    for (const [a, natural] of itens) {
      const m = marca.get(a.chave);
      if (m === "ignorar") { ignoradas.push({ ...a, marcada: antes.get(a.chave) === true }); continue; }
      ((m === "assinatura" ? "s" : natural) === "s" ? servicos : outras).push({ ...a, marcada: m === "assinatura" });
    }
    for (const l of [servicos, outras, ignoradas]) l.sort((x, y) => Number(y.valor) - Number(x.valor));
    const c = servicos.filter((a) => a.status === "ativa").reduce((t, a) => t + Math.round(Number(a.valor) * 100), 0);
    return { servicos, outras, ignoradas, total_mensal: (c / 100).toFixed(2), total_anual: (c * 12 / 100).toFixed(2) };
  };
  return { marcar, lista };
}

const erro = (r, nome) => { const e = RESPOSTAS.erros[nome]; return r.fulfill({ status: e.status, json: e.body }); };

// `falha`: nome do erro, ou (corpo, i) => nome|undefined por POST; "perdida" grava a marca e
// derruba a resposta. `trava.p`: promessa que segura o POST enquanto estiver posta.
async function abrir({ width = 1440, plano = "plus", perfil = "economizar", hash = "#/", cookie = true, get, falha, trava, raiz, url = PAINEL } = {}) {
  const ctx = await browser.newContext({ viewport: { width, height: width > 760 ? 900 : 812 }, reducedMotion: "reduce", timezoneId: "America/Sao_Paulo" });
  await servir(ctx, raiz, { plano });
  await ctx.addInitScript((p) => localStorage.setItem("pigbank.dashboard.profile.v1", JSON.stringify(p)), perfil);
  if (cookie) await ctx.addCookies([{ name: "csrf_token", value: encodeURIComponent(CSRF), url: ORIGIN }]);
  const srv = backend();
  const posts = [];
  const api = []; // todo pedido à /api/v2/assinaturas*
  if (plano === "plus" || plano === "pro") {
    await ctx.route((u) => u.pathname.startsWith("/api/v2/assinaturas"), async (r) => {
      const req = r.request();
      if (req.method() === "GET") return get ? get(r) : r.fulfill({ json: srv.lista() });
      const headers = req.headers();
      const corpo = req.postDataJSON();
      posts.push({ corpo, headers });
      if (trava?.p) await trava.p;
      if (headers["x-csrf-token"] !== CSRF) return erro(r, "403_csrf");
      if (!(headers["content-type"] ?? "").includes("application/json")) return r.fulfill({ status: 422, json: { detail: [{ type: "model_attributes_type" }] } });
      const f = typeof falha === "function" ? falha(corpo, posts.length - 1) : falha;
      if (f && f !== "perdida") return erro(r, f);
      srv.marcar(corpo.chave, corpo.status);
      return f ? r.abort() : r.fulfill({ json: srv.lista() });
    });
  }
  const page = await ctx.newPage();
  const tudo = [];
  page.on("request", (r) => {
    const p = new URL(r.url()).pathname;
    if (p.startsWith("/api/v2/") && p !== "/api/v2/me" && p !== "/api/v2/eventos") tudo.push(`${r.method()} ${p}`);
    if (p.startsWith("/api/v2/assinaturas")) api.push(r.method());
  });
  await page.goto(`${url}${hash}`);
  await page.locator("#page-title").waitFor();
  return { ctx, page, posts, api, tudo, gets: () => api.filter((m) => m === "GET").length };
}

const pronta = (page) => page.locator(`${SERV} .sub`).first().waitFor();
const nomes = (page, sel) => page.locator(`${sel} .sub-name`).allTextContents();
const linha = (page, sel, nome) => page.locator(`${sel} li.sub`, { has: page.locator(".sub-name", { hasText: new RegExp(`^${nome}$`) }) });
// O nome acessível leva o da cobrança: "Ignorar" repetido em cada linha não diz qual é.
const rotulo = (botao, nome) => (botao === "Ignorar" ? `Ignorar ${nome}` : `${botao}: ${nome}`);
const clicar = (page, sel, nome, botao) => linha(page, sel, nome).getByRole("button", { name: rotulo(botao, nome), exact: true }).click();
const sumir = (page, sel, nome) => linha(page, sel, nome).waitFor({ state: "detached" });

test("card (Plus, Economizar): total, as 3 maiores ativas em ordem, sem a cancelada; a seta abre a página", async () => {
  const { ctx, page } = await abrir();
  const card = page.locator("#w-assinaturas");
  await card.locator(".sub-bill").first().waitFor();
  const lede = await card.locator(".w-lede").first().textContent();
  const linhas = await card.locator(".sub-bill .bill-name").evaluateAll((els) => els.map((e) => e.firstChild.textContent));
  const dia = await card.locator(".sub-bill .bill-status").first().textContent();
  await card.locator(".w-open").click();
  await page.waitForFunction(() => location.hash === "#/assinaturas");
  await ctx.close();
  assert.equal(lede, "R$ 212,60 por mês · R$ 2.551 por ano");
  assert.deepEqual(linhas, ["Smart Fit", "Netflix", "Apple Music"]); // Globoplay (22,90) está cancelada
  assert.equal(dia, "todo dia 10");
});

test("página: duas seções, dia, próxima, meio com e sem final, reajuste, cancelada, totais; com a etiqueta de demonstração", async () => {
  const { ctx, page } = await abrir({ hash: "#/assinaturas" });
  await pronta(page);
  const texto = async (sel, nome) => (await linha(page, sel, nome).textContent()).replace(/\s+/g, " ");
  const r = {
    titulos: await page.locator(".page .w-title").allTextContents(),
    totais: await page.locator(`${SERV} .detail-facts dd`).allTextContents(),
    servicos: await nomes(page, SERV),
    outras: await nomes(page, OUTRAS),
    netflix: await texto(SERV, "Netflix"),
    smart: await texto(SERV, "Smart Fit"),
    globo: await linha(page, SERV, "Globoplay").locator(".faint").textContent(),
    icloud: await texto(SERV, "iCloud"),
    porto: await texto(OUTRAS, "Porto Seguro"),
    naoE: await page.locator(`${SERV} li.sub`).evaluateAll((ls) => ls.filter((l) => [...l.querySelectorAll("button")].some((b) => b.textContent === "Não é assinatura")).map((l) => l.querySelector(".sub-name").textContent)),
    naoEOutras: await page.getByRole("button", { name: "Não é assinatura" }).count(),
    ignorar: await page.locator(".sub-acoes button", { hasText: "Ignorar" }).count(),
    ignorarPorNome: await Promise.all([...CHEIA.servicos, ...CHEIA.outras].map((a) => page.getByRole("button", { name: rotulo("Ignorar", a.nome), exact: true }).count())),
    cartao: await linha(page, SERV, "Netflix").locator("i.ph-credit-card").count(),
    conta: await linha(page, SERV, "Smart Fit").locator("i.ph-bank").count(),
    etiqueta: await page.evaluate(() => document.body.innerText.includes("Dados de demonstração")),
    rodape: await page.locator(".foot").count(),
  };
  await ctx.close();
  assert.deepEqual(r.titulos, ["Serviços", "Outras cobranças recorrentes"]);
  assert.deepEqual(r.totais, ["R$ 212,60", "R$ 2.551"]);
  assert.deepEqual(r.servicos, ["Smart Fit", "Netflix", "Globoplay", "Apple Music", "iCloud"]);
  assert.deepEqual(r.outras, ["Condomínio Aurora", "Porto Seguro", "Porto Seguro Residencial"]);
  assert.deepEqual(r.naoE, ["Smart Fit"]); // só a linha marcada
  assert.equal(r.naoEOutras, 1);
  assert.equal(r.ignorar, 8);
  assert.deepEqual(r.ignorarPorNome, Array(8).fill(1)); // cada "Ignorar" tem nome acessível próprio
  assert.match(r.netflix, /todo dia 5 · próxima 5 out/);
  assert.match(r.netflix, /Nubank ••1234 · desde mar\/25/);
  assert.match(r.netflix, /^NetflixR\$ 55,90todo/); // "55.9" da API: a escala é a que o sync gravou
  assert.match(r.netflix, /subiu de R\$ 44,90 em 5 ago/);
  // "14.90" > "9.90" e "89.90" < "100.00" erram como texto: a comparação é numérica.
  assert.match(r.icloud, /subiu de R\$ 9,90 em 20 mar/);
  assert.match(r.smart, / Itaú · desde nov\/25/);
  assert.doesNotMatch(r.smart, /••/);
  assert.equal(r.globo, "parece cancelada · última em 15 jul");
  assert.match(r.porto, /baixou de R\$ 100,00 em 22 jun/);
  assert.equal(r.cartao, 1);
  assert.equal(r.conta, 1);
  assert.equal(r.etiqueta, true);
  assert.equal(r.rodape, 1);
});

test("sem seletor de mês na página (NO_MONTH); no Resumo ele aparece", async () => {
  const { ctx, page } = await abrir({ hash: "#/assinaturas" });
  await pronta(page);
  const naPagina = await page.locator(".month-switch").count();
  await page.evaluate(() => { location.hash = "#/"; });
  await page.locator(".board").waitFor();
  const noResumo = await page.locator(".month-switch").count();
  await ctx.close();
  assert.equal(naPagina, 0);
  assert.equal(noResumo, 1);
});

test("chave repetida: as duas linhas apple continuam certas quando a lista muda acima delas", async () => {
  const { ctx, page } = await abrir({ hash: "#/assinaturas" });
  await pronta(page);
  const antes = await nomes(page, SERV);
  await clicar(page, SERV, "Netflix", "Ignorar");
  await sumir(page, SERV, "Netflix");
  const depois = await nomes(page, SERV);
  await ctx.close();
  assert.deepEqual(antes, ["Smart Fit", "Netflix", "Globoplay", "Apple Music", "iCloud"]);
  assert.deepEqual(depois, ["Smart Fit", "Globoplay", "Apple Music", "iCloud"]);
});

test("Essencial: o 403 pro_required vira o convite, sem botão de ação, sem POST, um GET só", async () => {
  const { ctx, page, api, gets } = await abrir({ plano: "essencial" });
  const card = page.locator("#w-assinaturas");
  await card.locator(".empty").waitFor();
  await page.waitForLoadState("networkidle");
  const r = {
    texto: (await card.locator(".empty").textContent()).trim(),
    precos: await card.locator('a[href="/precos"]').count(),
    gets: gets(),
  };
  await page.evaluate(() => { location.hash = "#/assinaturas"; });
  await page.locator(".page .empty").waitFor();
  r.naPagina = await page.locator('.page a[href="/precos"]').count();
  r.acoes = await page.locator(ACOES).count();
  await ctx.close();
  assert.equal(r.texto, "Assinaturas é do Plus e do Pro.Ver planos");
  assert.equal(r.precos, 1);
  assert.equal(r.gets, 1);
  assert.equal(r.naPagina, 1);
  assert.equal(r.acoes, 0);
  assert.equal(api.filter((m) => m !== "GET").length, 0);
});

test("lista vazia: o texto e o link para conectar banco, no card e na página", async () => {
  const { ctx, page } = await abrir({ get: (r) => r.fulfill({ json: RESPOSTAS.assinaturas.vazia }) });
  const card = page.locator("#w-assinaturas .empty");
  await card.waitFor();
  const r = { texto: await card.locator("p").textContent(), link: await card.locator("a").getAttribute("href") };
  await page.evaluate(() => { location.hash = "#/assinaturas"; });
  r.pagina = await page.locator(".page .empty a").getAttribute("href");
  r.textoPagina = await page.locator(".page .empty p").textContent();
  r.secao = await page.locator(IGN).count();
  await ctx.close();
  assert.equal(r.secao, 0); // `ignoradas: []`: o vazio é de quem não conectou, não de quem ignorou
  assert.equal(r.textoPagina, r.texto);
  assert.equal(r.texto, "Nenhuma assinatura por aqui ainda. Elas aparecem sozinhas a partir das contas e cartões conectados pelo Open Finance.");
  assert.equal(r.link, "/settings?view=open-finance");
  assert.equal(r.pagina, "/settings?view=open-finance");
});

test("500: mensagem fixa e \"Tentar de novo\" pede de novo", async () => {
  const { ctx, page, gets } = await abrir({ hash: "#/assinaturas", get: (r) => erro(r, "500") });
  const alerta = page.locator(".page [role=alert]");
  await alerta.waitFor({ timeout: 15000 });
  const r = { texto: await alerta.locator("p").textContent(), antes: gets() };
  const novo = page.waitForRequest((q) => q.url().endsWith("/api/v2/assinaturas"));
  await alerta.getByRole("button", { name: "Tentar de novo" }).click();
  await novo;
  r.depois = gets();
  await ctx.close();
  assert.equal(r.texto, "Não deu para carregar as assinaturas.");
  assert.ok(r.depois > r.antes, JSON.stringify(r));
});

test("Ignorar: corpo e CSRF do cookie decodificado; a linha some e o foco vai para Desfazer", async () => {
  const { ctx, page, posts } = await abrir({ hash: "#/assinaturas" });
  await pronta(page);
  await clicar(page, SERV, "Netflix", "Ignorar");
  await sumir(page, SERV, "Netflix");
  const r = {
    aviso: await page.locator(".sub-aviso p").textContent(),
    foco: await page.evaluate(() => document.activeElement?.textContent),
    vivo: await page.locator(".sub-aviso").getAttribute("aria-live"),
  };
  await ctx.close();
  assert.equal(r.vivo, "polite");
  assert.deepEqual(posts[0].corpo, { chave: "netflix", status: "ignorar" });
  assert.equal(posts[0].headers["x-csrf-token"], CSRF);
  assert.equal(r.aviso, "Netflix ignorada.");
  assert.equal(r.foco, "Desfazer");
});

test("Desfazer volta ao estado anterior: marcada → \"assinatura\", não marcada → \"nenhuma\"", async () => {
  const { ctx, page, posts } = await abrir({ hash: "#/assinaturas" });
  await pronta(page);
  for (const nome of ["Smart Fit", "Netflix"]) {
    await clicar(page, SERV, nome, "Ignorar");
    await sumir(page, SERV, nome);
    await page.getByRole("button", { name: "Desfazer" }).click();
    await linha(page, SERV, nome).waitFor();
  }
  const servicos = await nomes(page, SERV);
  const aviso = await page.locator(".sub-aviso p").textContent();
  await ctx.close();
  assert.deepEqual(posts.map((p) => p.corpo), [
    { chave: "smart fit", status: "ignorar" }, { chave: "smart fit", status: "assinatura" },
    { chave: "netflix", status: "ignorar" }, { chave: "netflix", status: "nenhuma" },
  ]);
  assert.deepEqual(servicos, ["Smart Fit", "Netflix", "Globoplay", "Apple Music", "iCloud"]);
  assert.equal(aviso, "Desfeito.");
});

test("\"É assinatura\" em outras: a linha passa para serviços e o total sobe", async () => {
  const { ctx, page, posts } = await abrir({ hash: "#/assinaturas" });
  await pronta(page);
  await clicar(page, OUTRAS, "Condomínio Aurora", "É assinatura");
  await linha(page, SERV, "Condomínio Aurora").waitFor();
  const r = {
    servicos: await nomes(page, SERV),
    outras: await nomes(page, OUTRAS),
    total: await page.locator(`${SERV} .detail-facts dd`).first().textContent(),
    naoE: await linha(page, SERV, "Condomínio Aurora").getByRole("button", { name: "Não é assinatura" }).count(),
    aviso: await page.locator(".sub-aviso p").textContent(),
  };
  await ctx.close();
  assert.deepEqual(posts[0].corpo, { chave: "condominio aurora", status: "assinatura" });
  assert.deepEqual(r.servicos, ["Condomínio Aurora", "Smart Fit", "Netflix", "Globoplay", "Apple Music", "iCloud"]);
  assert.deepEqual(r.outras, ["Porto Seguro", "Porto Seguro Residencial"]); // a chave repetida em outras
  assert.equal(r.total, "R$ 692,60");
  assert.equal(r.naoE, 1);
  assert.equal(r.aviso, "Condomínio Aurora marcada como assinatura.");
});

for (const [caso, opcoes] of [["403 do CSRF (cookie ausente)", { cookie: false }], ["404 not_found", { falha: "404_not_found" }]]) {
  test(`POST falha (${caso}): mensagem fixa, a lista fica, o GET é refeito`, async () => {
    const { ctx, page, posts, gets } = await abrir({ hash: "#/assinaturas", ...opcoes });
    await pronta(page);
    const antes = gets();
    const recarga = page.waitForResponse((q) => q.request().method() === "GET" && q.url().endsWith("/api/v2/assinaturas"));
    await clicar(page, SERV, "Netflix", "Ignorar");
    await page.locator(".sub-aviso p").waitFor();
    await recarga;
    const r = {
      aviso: await page.locator(".sub-aviso p").textContent(),
      desfazer: await page.getByRole("button", { name: "Desfazer" }).count(),
      servicos: await nomes(page, SERV),
      gets: gets() - antes,
    };
    await ctx.close();
    assert.equal(posts.length, 1);
    assert.equal(r.aviso, "Não deu para salvar. Tente de novo.");
    assert.equal(r.desfazer, 0);
    assert.deepEqual(r.servicos, ["Smart Fit", "Netflix", "Globoplay", "Apple Music", "iCloud"]);
    assert.equal(r.gets, 1);
  });
}

test("POST no ar: todos os botões de ação travados, o Desfazer também; ao responder, voltam", async () => {
  let soltar;
  const trava = {};
  const { ctx, page, posts } = await abrir({ hash: "#/assinaturas", trava });
  await pronta(page);
  await clicar(page, SERV, "Netflix", "Ignorar");
  await sumir(page, SERV, "Netflix");
  trava.p = new Promise((ok) => { soltar = ok; });
  await clicar(page, SERV, "Globoplay", "Ignorar");
  while (posts.length < 2) await page.waitForTimeout(20);
  await page.locator(".sub-aviso button:disabled").waitFor();
  const durante = await page.locator(ACOES).evaluateAll((bs) => bs.map((b) => [b.textContent, b.disabled]));
  soltar();
  await sumir(page, SERV, "Globoplay");
  const depois = await page.locator(ACOES).evaluateAll((bs) => bs.map((b) => b.disabled));
  await ctx.close();
  assert.ok(durante.length >= 8 && durante.every(([, d]) => d), JSON.stringify(durante));
  assert.ok(durante.some(([t]) => t === "Desfazer"), JSON.stringify(durante));
  assert.ok(depois.length >= 8 && depois.every((d) => !d), JSON.stringify(depois));
});

test("dois cliques no mesmo tique (antes do re-render): um POST só, no Ignorar e no Desfazer", async () => {
  const { ctx, page, posts } = await abrir({ hash: "#/assinaturas" });
  await pronta(page);
  const duplo = (sel) => page.evaluate((s) => { const b = document.querySelector(s); b.click(); b.click(); }, sel);
  await linha(page, SERV, "Netflix").locator("button").first().evaluate((b) => b.setAttribute("data-alvo", ""));
  await duplo("[data-alvo]");
  await sumir(page, SERV, "Netflix");
  await duplo(".sub-aviso button");
  await linha(page, SERV, "Netflix").waitFor();
  await page.waitForLoadState("networkidle");
  await ctx.close();
  assert.deepEqual(posts.map((p) => p.corpo.status), ["ignorar", "nenhuma"]);
});

test("chave repetida: o aviso cobre o grupo que a marca leva junto (ignorar e \"É assinatura\")", async () => {
  const { ctx, page } = await abrir({ hash: "#/assinaturas" });
  await pronta(page);
  await clicar(page, SERV, "iCloud", "Ignorar");
  await sumir(page, SERV, "Apple Music");
  const r = { servicos: await nomes(page, SERV), ignorar: await page.locator(".sub-aviso p").textContent() };
  await clicar(page, OUTRAS, "Porto Seguro Residencial", "É assinatura");
  await linha(page, SERV, "Porto Seguro").first().waitFor();
  r.assinatura = await page.locator(".sub-aviso p").textContent();
  await clicar(page, SERV, "Smart Fit", "Não é assinatura");
  await linha(page, OUTRAS, "Smart Fit").waitFor();
  r.nenhuma = await page.locator(".sub-aviso p").textContent();
  await ctx.close();
  assert.deepEqual(r.servicos, ["Smart Fit", "Netflix", "Globoplay"]);
  assert.equal(r.ignorar, "iCloud e mais 1 cobrança do mesmo comerciante ignoradas.");
  assert.equal(r.assinatura, "Porto Seguro Residencial e mais 1 cobrança do mesmo comerciante marcadas como assinatura.");
  assert.equal(r.nenhuma, "Marca removida de Smart Fit."); // neutro: não diz para onde foi
});

test("ignorar a última: o estado vazio aparece com o aviso e o Desfazer, que traz o item de volta", async () => {
  const { ctx, page, posts } = await abrir({ hash: "#/assinaturas" });
  await pronta(page);
  for (const [sel, nome] of [[SERV, "Smart Fit"], [SERV, "Netflix"], [SERV, "Globoplay"], [SERV, "Apple Music"], [OUTRAS, "Condomínio Aurora"], [OUTRAS, "Porto Seguro"]]) {
    await clicar(page, sel, nome, "Ignorar");
    await sumir(page, sel, nome);
  }
  await page.locator(".page .empty").waitFor();
  const r = {
    aviso: await page.locator(".sub-aviso p").textContent(),
    vivo: await page.locator(".sub-aviso").getAttribute("aria-live"),
    vazio: await page.locator(".page .empty p").textContent(),
    conectar: await page.locator(".page .empty a").count(),
  };
  await page.getByRole("button", { name: "Desfazer" }).click();
  await linha(page, OUTRAS, "Porto Seguro").first().waitFor();
  r.outras = await nomes(page, OUTRAS);
  await ctx.close();
  assert.equal(r.aviso, "Porto Seguro e mais 1 cobrança do mesmo comerciante ignoradas.");
  assert.equal(r.vivo, "polite");
  // o vazio veio do Ignorar: sem o "Conectar banco" (o 1º carregamento vazio mantém o link)
  assert.equal(r.vazio, "Você ignorou todas as cobranças detectadas.");
  assert.equal(r.conectar, 0);
  assert.deepEqual(posts.at(-1).corpo, { chave: "porto seguro", status: "nenhuma" });
  assert.deepEqual(r.outras, ["Porto Seguro", "Porto Seguro Residencial"]);
});

test("Desfazer que falha: mensagem fixa, o Desfazer fica; a segunda tentativa traz o item", async () => {
  const { ctx, page, posts } = await abrir({ hash: "#/assinaturas", falha: (_, i) => (i === 1 ? "500" : undefined) });
  await pronta(page);
  await clicar(page, SERV, "Netflix", "Ignorar");
  await sumir(page, SERV, "Netflix");
  await page.getByRole("button", { name: "Desfazer" }).click();
  await page.locator(".sub-aviso p", { hasText: "desfazer" }).waitFor();
  const r = { aviso: await page.locator(".sub-aviso p").textContent(), desfazer: await page.getByRole("button", { name: "Desfazer" }).count() };
  await page.getByRole("button", { name: "Desfazer" }).click();
  await linha(page, SERV, "Netflix").waitFor();
  r.depois = await page.locator(".sub-aviso p").textContent();
  await ctx.close();
  assert.equal(r.aviso, "Não deu para desfazer. Tente de novo.");
  assert.equal(r.desfazer, 1);
  assert.deepEqual(posts.map((p) => p.corpo.status), ["ignorar", "nenhuma", "nenhuma"]);
  assert.equal(r.depois, "Desfeito.");
});

test("falha numa ação depois de outra que deu certo: o Desfazer da anterior fica e funciona", async () => {
  const { ctx, page, posts } = await abrir({ hash: "#/assinaturas", falha: (_, i) => (i === 1 ? "500" : undefined) });
  await pronta(page);
  await clicar(page, SERV, "Netflix", "Ignorar");
  await sumir(page, SERV, "Netflix");
  await clicar(page, SERV, "Globoplay", "Ignorar");
  await page.locator(".sub-aviso p", { hasText: "salvar" }).waitFor();
  const r = { aviso: await page.locator(".sub-aviso p").textContent(), desfazer: await page.getByRole("button", { name: "Desfazer" }).count() };
  if (r.desfazer) {
    await page.getByRole("button", { name: "Desfazer" }).click();
    await linha(page, SERV, "Netflix").waitFor();
  }
  r.servicos = await nomes(page, SERV);
  await ctx.close();
  assert.equal(r.aviso, "Não deu para salvar. Tente de novo.");
  assert.equal(r.desfazer, 1);
  assert.deepEqual(posts.map((p) => p.corpo), [
    { chave: "netflix", status: "ignorar" }, { chave: "globoplay", status: "ignorar" }, { chave: "netflix", status: "nenhuma" },
  ]);
  assert.deepEqual(r.servicos, ["Smart Fit", "Netflix", "Globoplay", "Apple Music", "iCloud"]);
});

test("resposta perdida com a marca gravada: a recarga mostra que pegou; aviso de sucesso e o Desfazer DESTA ação", async () => {
  const { ctx, page, posts } = await abrir({ hash: "#/assinaturas", falha: (_, i) => (i === 1 ? "perdida" : undefined) });
  await pronta(page);
  await clicar(page, SERV, "Netflix", "Ignorar");
  await sumir(page, SERV, "Netflix");
  await clicar(page, SERV, "Globoplay", "Ignorar");
  await sumir(page, SERV, "Globoplay");
  await page.waitForFunction(() => document.querySelector(".sub-aviso p")?.textContent !== "Netflix ignorada.");
  await page.locator(".sub-aviso button:enabled").waitFor();
  const r = { aviso: await page.locator(".sub-aviso p").textContent() };
  await page.getByRole("button", { name: "Desfazer" }).click();
  while (posts.length < 3) await page.waitForTimeout(20);
  await page.waitForLoadState("networkidle");
  r.servicos = await nomes(page, SERV);
  await ctx.close();
  assert.equal(r.aviso, "Globoplay ignorada.");
  assert.deepEqual(posts.map((p) => p.corpo), [
    { chave: "netflix", status: "ignorar" }, { chave: "globoplay", status: "ignorar" }, { chave: "globoplay", status: "nenhuma" },
  ]);
  assert.deepEqual(r.servicos, ["Smart Fit", "Globoplay", "Apple Music", "iCloud"]);
});

test("sair e voltar com o POST no ar: os botões da página remontada nascem travados e voltam ao responder", async () => {
  let soltar;
  const trava = { p: new Promise((ok) => { soltar = ok; }) };
  const { ctx, page, posts } = await abrir({ hash: "#/assinaturas", trava });
  await pronta(page);
  await clicar(page, SERV, "Netflix", "Ignorar");
  while (posts.length < 1) await page.waitForTimeout(20);
  await page.evaluate(() => { location.hash = "#/"; });
  await page.locator(".board").waitFor();
  await page.evaluate(() => { location.hash = "#/assinaturas"; });
  await pronta(page);
  const durante = await page.locator(ACOES).evaluateAll((bs) => bs.map((b) => b.disabled));
  soltar();
  await sumir(page, SERV, "Netflix");
  const depois = await page.locator(ACOES).evaluateAll((bs) => bs.map((b) => b.disabled));
  await ctx.close();
  assert.ok(durante.length >= 8 && durante.every(Boolean), JSON.stringify(durante));
  assert.ok(depois.length >= 8 && depois.every((d) => !d), JSON.stringify(depois));
});

test("chave com três linhas: \"e mais 2 cobranças\" no plural", async () => {
  const terceira = { ...CHEIA.outras.find((a) => a.nome === "Porto Seguro Residencial"), nome: "Porto Seguro Auto", valor: "59.90" };
  const { ctx, page } = await abrir({ hash: "#/assinaturas", get: (r) => r.fulfill({ json: { ...CHEIA, outras: [...CHEIA.outras, terceira] } }) });
  await pronta(page);
  await clicar(page, OUTRAS, "Porto Seguro", "Ignorar");
  await page.locator(".sub-aviso p").waitFor();
  const aviso = await page.locator(".sub-aviso p").textContent();
  await ctx.close();
  assert.equal(aviso, "Porto Seguro e mais 2 cobranças do mesmo comerciante ignoradas.");
});

test("card só com outras: \"N cobranças recorrentes para revisar\"", async () => {
  const { ctx, page } = await abrir({ get: (r) => r.fulfill({ json: { ...CHEIA, servicos: [], total_mensal: "0", total_anual: "0" } }) });
  const card = page.locator("#w-assinaturas");
  const lede = card.locator(".w-lede", { hasText: "revisar" });
  await lede.waitFor();
  const r = { texto: await lede.textContent(), linhas: await card.locator(".sub-bill").count() };
  await ctx.close();
  assert.equal(r.texto, "3 cobranças recorrentes para revisar");
  assert.equal(r.linhas, 0);
});

test("protótipo com Economizar: zero pedidos à /api/v2 (fora o /me, que ele também não faz)", async () => {
  const { ctx, page, tudo } = await abrir({ url: PROTOTIPO, raiz: RAIZ });
  await page.locator("#w-assinaturas .empty").waitFor();
  await page.evaluate(() => { location.hash = "#/assinaturas"; });
  await page.locator(".page .empty").waitFor();
  await page.waitForLoadState("networkidle");
  await ctx.close();
  assert.deepEqual(tudo, []);
});

// ── Ignoradas (#751) ─────────────────────────────────────────────────────────

const abrirSecao = async (page) => {
  await page.locator(`${IGN} summary`).click();
  await page.waitForFunction((sel) => document.querySelector(`${sel} details`)?.open, IGN);
};
const ignorarTodas = async (page) => {
  for (const [sel, nome] of [[SERV, "Smart Fit"], [SERV, "Netflix"], [SERV, "Globoplay"], [SERV, "Apple Music"], [OUTRAS, "Condomínio Aurora"], [OUTRAS, "Porto Seguro"]]) {
    await clicar(page, sel, nome, "Ignorar");
    await sumir(page, sel, nome);
  }
};

test("recarregar: a ignorada fica na seção fechada; Voltar a mostrar a devolve ao lugar e o Desfazer a ignora de novo", async () => {
  const { ctx, page, posts } = await abrir({ hash: "#/assinaturas" });
  await pronta(page);
  await clicar(page, SERV, "Netflix", "Ignorar");
  await sumir(page, SERV, "Netflix");
  await page.reload();
  await pronta(page);
  const det = page.locator(`${IGN} details`);
  await det.waitFor({ state: "attached" });
  const r = {
    titulo: await page.locator(`${IGN} .w-title`).textContent(),
    aberta: await det.evaluate((d) => d.open),
    resumo: await det.locator("summary").innerText(),
    ultima: await page.locator(".page .panel").last().evaluate((p) => !!p.querySelector("#w-assinaturas-ignoradas")),
  };
  await abrirSecao(page);
  await clicar(page, IGN, "Netflix", "Voltar a mostrar");
  await linha(page, SERV, "Netflix").waitFor();
  r.servicos = await nomes(page, SERV);
  r.aviso = await page.locator(".sub-aviso p").textContent();
  r.secao = await page.locator(IGN).count();
  await page.getByRole("button", { name: "Desfazer" }).click();
  await linha(page, IGN, "Netflix").waitFor({ state: "attached" });
  r.depois = await nomes(page, SERV);
  await ctx.close();
  assert.equal(r.titulo, "Ignoradas");
  assert.equal(r.aberta, false);
  assert.equal(r.resumo, "Mostrar 1 cobrança");
  assert.equal(r.ultima, true);
  assert.deepEqual(posts.map((p) => p.corpo), [
    { chave: "netflix", status: "ignorar" }, { chave: "netflix", status: "nenhuma" }, { chave: "netflix", status: "ignorar" },
  ]);
  assert.deepEqual(r.servicos, ["Smart Fit", "Netflix", "Globoplay", "Apple Music", "iCloud"]);
  assert.equal(r.aviso, "Netflix voltou para a lista.");
  assert.equal(r.secao, 0);
  assert.deepEqual(r.depois, ["Smart Fit", "Globoplay", "Apple Music", "iCloud"]);
});

test("marcada antes de ignorar: Voltar a mostrar manda \"assinatura\" e a Smart Fit volta marcada em Serviços", async () => {
  const { ctx, page, posts } = await abrir({ hash: "#/assinaturas" });
  await pronta(page);
  await clicar(page, SERV, "Smart Fit", "Ignorar");
  await sumir(page, SERV, "Smart Fit");
  await page.reload();
  await pronta(page);
  await abrirSecao(page);
  await clicar(page, IGN, "Smart Fit", "Voltar a mostrar");
  await linha(page, SERV, "Smart Fit").waitFor();
  const r = {
    naoE: await linha(page, SERV, "Smart Fit").getByRole("button", { name: "Não é assinatura: Smart Fit" }).count(),
    outras: await nomes(page, OUTRAS),
  };
  await ctx.close();
  assert.deepEqual(posts.at(-1).corpo, { chave: "smart fit", status: "assinatura" });
  assert.equal(r.naoE, 1);
  assert.deepEqual(r.outras, ["Condomínio Aurora", "Porto Seguro", "Porto Seguro Residencial"]);
});

test("ignorou tudo e recarregou: o texto sem o link, a seção com as 8, o card igual; Voltar a mostrar traz a lista", async () => {
  const { ctx, page } = await abrir({ hash: "#/assinaturas" });
  await pronta(page);
  await ignorarTodas(page);
  await page.reload();
  await page.locator(".page .empty").waitFor();
  const r = {
    vazio: await page.locator(".page .empty p").textContent(),
    conectar: await page.locator(".page .empty a").count(),
    linhas: await page.locator(`${IGN} li.sub`).count(),
    resumo: await page.locator(`${IGN} summary`).innerText(),
  };
  await page.evaluate(() => { location.hash = "#/"; });
  const card = page.locator("#w-assinaturas .empty");
  await card.waitFor();
  r.card = await card.locator("p").textContent();
  r.cardLink = await card.locator("a").count();
  await page.evaluate(() => { location.hash = "#/assinaturas"; });
  await page.locator(`${IGN} summary`).waitFor();
  await abrirSecao(page);
  await clicar(page, IGN, "Netflix", "Voltar a mostrar");
  await linha(page, SERV, "Netflix").waitFor();
  r.servicos = await nomes(page, SERV);
  r.vazioDepois = await page.locator(".page .empty").count();
  r.aberta = await page.locator(`${IGN} details`).evaluate((d) => d.open);
  await ctx.close();
  assert.equal(r.vazio, "Você ignorou todas as cobranças detectadas.");
  assert.equal(r.conectar, 0);
  assert.equal(r.linhas, 8);
  assert.equal(r.resumo, "Mostrar 8 cobranças");
  assert.equal(r.card, "Você ignorou todas as cobranças detectadas.");
  assert.equal(r.cardLink, 0);
  assert.deepEqual(r.servicos, ["Netflix"]);
  assert.equal(r.vazioDepois, 0);
  assert.equal(r.aberta, true); // saiu do retorno do vazio para o outro: o `key` manteve a seção
});

test("grupo da Apple: Voltar a mostrar no iCloud traz as duas, em ordem; a Netflix fica e a seção segue aberta", async () => {
  const { ctx, page } = await abrir({ hash: "#/assinaturas" });
  await pronta(page);
  for (const nome of ["Netflix", "Apple Music"]) {
    await clicar(page, SERV, nome, "Ignorar");
    await sumir(page, SERV, nome);
  }
  await page.reload();
  await pronta(page);
  const resumo = await page.locator(`${IGN} summary`).innerText(); // fechada: aberta diz "Esconder"
  await abrirSecao(page);
  const r = { secao: await nomes(page, IGN), resumo };
  await clicar(page, IGN, "iCloud", "Voltar a mostrar");
  await linha(page, SERV, "iCloud").waitFor();
  r.aviso = await page.locator(".sub-aviso p").textContent();
  r.servicos = await nomes(page, SERV);
  r.depois = await nomes(page, IGN);
  r.aberta = await page.locator(`${IGN} details`).evaluate((d) => d.open);
  await ctx.close();
  assert.deepEqual(r.secao, ["Netflix", "Apple Music", "iCloud"]);
  assert.equal(r.resumo, "Mostrar 3 cobranças");
  assert.equal(r.aviso, "iCloud e mais 1 cobrança do mesmo comerciante voltaram para a lista.");
  assert.deepEqual(r.servicos, ["Smart Fit", "Globoplay", "Apple Music", "iCloud"]);
  assert.deepEqual(r.depois, ["Netflix"]);
  assert.equal(r.aberta, true);
});

test("resposta do Ignorar perdida e a recarga sem a chave em lista nenhuma: não conta como salvo", async () => {
  let perdeu = false;
  const semGlobo = { ...CHEIA, servicos: CHEIA.servicos.filter((a) => a.chave !== "globoplay") };
  const { ctx, page } = await abrir({
    hash: "#/assinaturas",
    falha: () => { perdeu = true; return "perdida"; },
    get: (r) => r.fulfill({ json: perdeu ? semGlobo : CHEIA }),
  });
  await pronta(page);
  await clicar(page, SERV, "Globoplay", "Ignorar");
  await page.locator(".sub-aviso p").waitFor();
  const aviso = await page.locator(".sub-aviso p").textContent();
  await ctx.close();
  assert.equal(aviso, "Não deu para salvar. Tente de novo.");
});

test("resumo das ignoradas: fechado diz \"Mostrar…\", aberto diz \"Esconder\"; a setinha nativa fica e o alvo tem 44 px", async () => {
  const { ctx, page } = await abrir({ hash: "#/assinaturas" });
  await pronta(page);
  await clicar(page, SERV, "Netflix", "Ignorar");
  await sumir(page, SERV, "Netflix");
  const sum = page.locator(`${IGN} summary`);
  const ler = () => sum.evaluate((s) => {
    const c = getComputedStyle(s);
    return { texto: s.innerText, display: c.display, marcador: c.listStyleType, altura: s.getBoundingClientRect().height };
  });
  const fechado = await ler();
  await abrirSecao(page);
  const aberto = await ler();
  await sum.click();
  const fechouDeNovo = await page.locator(`${IGN} details`).evaluate((d) => d.open);
  await ctx.close();
  assert.equal(fechado.texto, "Mostrar 1 cobrança");
  assert.equal(aberto.texto, "Esconder");
  assert.equal(fechouDeNovo, false);
  // `list-item` com list-style é o que desenha o ::marker (a setinha); `flex` o apaga
  for (const e of [fechado, aberto]) {
    assert.equal(e.display, "list-item");
    assert.notEqual(e.marcador, "none");
    assert.ok(e.altura >= 44, String(e.altura));
  }
});

test("card com ignoradas na resposta: lista só serviços; \"para revisar\" conta só as outras", async () => {
  const [smart, netflix] = CHEIA.servicos;
  // as ignoradas valem mais e estão ativas: entrariam no top-3 se o card as lesse
  const ign = CHEIA.outras.slice(0, 2).map((a) => ({ ...a, chave: `x-${a.chave}`, nome: `Ign ${a.nome}` }));
  const json = { servicos: [smart, netflix], outras: [CHEIA.outras[2]], ignoradas: ign, total_mensal: "175.80", total_anual: "2109.60" };
  let resp = json;
  const { ctx, page } = await abrir({ get: (r) => r.fulfill({ json: resp }) });
  const card = page.locator("#w-assinaturas");
  await card.locator(".sub-bill").first().waitFor();
  const r = {
    lede: await card.locator(".w-lede").allTextContents(),
    linhas: await card.locator(".sub-bill .bill-name").evaluateAll((els) => els.map((e) => e.firstChild.textContent)),
  };
  // sem serviços: o "para revisar" conta só as outras (1), não as 2 ignoradas
  resp = { ...json, servicos: [], total_mensal: "0", total_anual: "0" };
  await page.reload();
  const revisar = card.locator(".w-lede", { hasText: "revisar" });
  await revisar.waitFor();
  r.revisar = await revisar.textContent();
  await ctx.close();
  assert.deepEqual(r.lede, ["R$ 175,80 por mês · R$ 2.110 por ano"]);
  assert.deepEqual(r.linhas, ["Smart Fit", "Netflix"]);
  assert.equal(r.revisar, "1 cobrança recorrente para revisar");
});

test("servidor sem `ignoradas` (deploy fora de ordem): página e card renderizam e o Ignorar funciona", async () => {
  const antiga = { ...CHEIA };
  delete antiga.ignoradas;
  const { ctx, page } = await abrir({ hash: "#/assinaturas", get: (r) => r.fulfill({ json: antiga }) });
  await pronta(page);
  const r = { servicos: await nomes(page, SERV), secao: await page.locator(IGN).count() };
  await clicar(page, SERV, "Netflix", "Ignorar");
  await page.locator(".sub-aviso p").waitFor();
  r.aviso = await page.locator(".sub-aviso p").textContent();
  await page.evaluate(() => { location.hash = "#/"; });
  await page.locator("#w-assinaturas .sub-bill").first().waitFor();
  await ctx.close();
  assert.deepEqual(r.servicos, ["Smart Fit", "Netflix", "Globoplay", "Apple Music", "iCloud"]);
  assert.equal(r.secao, 0);
  assert.equal(r.aviso, "Netflix ignorada.");
});

test("Desfazer de um Voltar a mostrar que esvaziou a seção: ela volta aberta, com o item, e ainda fecha", async () => {
  const { ctx, page } = await abrir({ hash: "#/assinaturas" });
  await pronta(page);
  await clicar(page, SERV, "Netflix", "Ignorar");
  await sumir(page, SERV, "Netflix");
  await abrirSecao(page);
  await clicar(page, IGN, "Netflix", "Voltar a mostrar");
  await linha(page, SERV, "Netflix").waitFor();
  const r = { secao: await page.locator(IGN).count() };
  await page.getByRole("button", { name: "Desfazer" }).click();
  await linha(page, IGN, "Netflix").waitFor({ state: "attached" });
  const det = page.locator(`${IGN} details`);
  r.aberta = await det.evaluate((d) => d.open);
  r.visivel = await linha(page, IGN, "Netflix").isVisible();
  await det.locator("summary").click();
  r.fechou = await det.evaluate((d) => d.open);
  await ctx.close();
  assert.equal(r.secao, 0);
  assert.equal(r.aberta, true);
  assert.equal(r.visivel, true);
  assert.equal(r.fechou, false);
});

// O ref da seção roda a cada render: o Ignorar seguinte re-renderiza já no `pendente`.
test("a reabertura do Desfazer vale uma vez: fechada pelo usuário, a ação seguinte não a abre de novo", async () => {
  const { ctx, page } = await abrir({ hash: "#/assinaturas" });
  await pronta(page);
  await clicar(page, SERV, "Netflix", "Ignorar");
  await sumir(page, SERV, "Netflix");
  await abrirSecao(page);
  await clicar(page, IGN, "Netflix", "Voltar a mostrar");
  await linha(page, SERV, "Netflix").waitFor();
  await page.getByRole("button", { name: "Desfazer" }).click();
  await linha(page, IGN, "Netflix").waitFor({ state: "attached" });
  const det = page.locator(`${IGN} details`);
  const r = { aberta: await det.evaluate((d) => d.open) };
  await det.locator("summary").click();
  r.fechou = await det.evaluate((d) => d.open);
  await clicar(page, SERV, "Globoplay", "Ignorar");
  await linha(page, IGN, "Globoplay").waitFor({ state: "attached" });
  r.depois = await det.evaluate((d) => d.open);
  await ctx.close();
  assert.equal(r.aberta, true);
  assert.equal(r.fechou, false);
  assert.equal(r.depois, false);
});

test("Desfazer de um Ignorar (\"assinatura\" e \"nenhuma\") com a seção fechada: ela segue fechada", async () => {
  const { ctx, page, posts } = await abrir({ hash: "#/assinaturas" });
  await pronta(page);
  for (const nome of ["Netflix", "Globoplay"]) {
    await clicar(page, SERV, nome, "Ignorar");
    await sumir(page, SERV, nome);
  }
  const det = page.locator(`${IGN} details`);
  const r = { antes: await det.evaluate((d) => d.open), depois: [] };
  for (const [sel, nome] of [[SERV, "Smart Fit"], [OUTRAS, "Condomínio Aurora"]]) {
    await clicar(page, sel, nome, "Ignorar");
    await sumir(page, sel, nome);
    await page.getByRole("button", { name: "Desfazer" }).click();
    await linha(page, sel, nome).waitFor();
    r.depois.push(await det.evaluate((d) => d.open));
  }
  await ctx.close();
  assert.deepEqual(posts.slice(2).map((p) => p.corpo), [
    { chave: "smart fit", status: "ignorar" }, { chave: "smart fit", status: "assinatura" },
    { chave: "condominio aurora", status: "ignorar" }, { chave: "condominio aurora", status: "nenhuma" },
  ]);
  assert.equal(r.antes, false);
  assert.deepEqual(r.depois, [false, false]);
});

for (const width of [375, 1440]) {
  test(`${width}px: sem rolagem lateral, botões e o resumo das ignoradas de 44 px ou mais, o card cabe na célula`, async () => {
    const { ctx, page } = await abrir({ width });
    const lateral = () => page.evaluate(() => document.scrollingElement.scrollWidth - document.scrollingElement.clientWidth);
    await page.locator("#w-assinaturas .sub-bill").first().waitFor();
    const etiqueta = () => page.locator(".demo-strip").evaluateAll((es) => es.filter((e) => e.offsetParent).length);
    const r = {
      resumo: await lateral(),
      sobra: await page.locator("#w-assinaturas").evaluate((w) => w.scrollHeight - w.clientHeight),
      etiquetaResumo: await etiqueta(),
    };
    await page.evaluate(() => { location.hash = "#/assinaturas"; });
    await pronta(page);
    r.etiquetaPagina = await etiqueta();
    await clicar(page, SERV, "Netflix", "Ignorar");
    await sumir(page, SERV, "Netflix");
    await abrirSecao(page);
    r.pagina = await lateral();
    r.alturas = await page.locator(ACOES).evaluateAll((bs) => bs.map((b) => b.getBoundingClientRect().height));
    r.voltar = await linha(page, IGN, "Netflix").getByRole("button", { name: "Voltar a mostrar: Netflix" }).evaluate((b) => b.getBoundingClientRect().height);
    r.sumario = await page.locator(`${IGN} summary`).evaluate((b) => b.getBoundingClientRect().height);
    await ctx.close();
    assert.equal(r.resumo, 0);
    // a faixa só aparece no celular (shell.css): lá ela está no Resumo e aqui também
    assert.deepEqual([r.etiquetaResumo, r.etiquetaPagina], width < 760 ? [1, 1] : [0, 0]);
    assert.ok(r.sobra <= 1, `o card transborda ${r.sobra}px`);
    assert.equal(r.pagina, 0);
    assert.ok(r.alturas.length >= 8 && r.alturas.every((h) => h >= 44), JSON.stringify(r.alturas));
    assert.ok(r.voltar >= 44 && r.sumario >= 44, JSON.stringify([r.voltar, r.sumario]));
  });
}

test("isoDay: \"2026-10-05\" é dia 5 em São Paulo (o new Date cru daria 4)", async () => {
  process.env.TZ = "America/Sao_Paulo";
  const { isoDay, dayMonth } = await import("../../webapp/src/dashboard/lib/format.js");
  assert.equal(new Date("2026-10-05").getDate(), 4); // controle: o fuso discrimina
  assert.equal(isoDay("2026-10-05").getDate(), 5);
  assert.equal(dayMonth(isoDay("2026-10-05")), "5 out");
});
