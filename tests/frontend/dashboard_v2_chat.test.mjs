// Protótipo dashboard-v2: a conversa com o Piggy (#/piggy, parts/PiggyChat.tsx) e a
// barra de conversa (parts/AskBar.tsx).
//   · a barra flutua em todas as páginas no desktop; no celular só existe na conversa;
//   · digitar + Enter abre a conversa com a pergunta; sem IA no protótipo, texto livre
//     recebe os atalhos, e cada atalho responde com texto, blocos e próximas perguntas;
//   · os 8 assuntos respondem sem erro, sem id repetido na página;
//   · os blocos da resposta são vivos: dashboard_v2_chat_blocos.test.mjs;
//   · a conversa sobrevive à troca de página e some ao recarregar;
//   · estado vazio com as sugestões do perfil primeiro; Essencial vê o convite do Plus.
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { execSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { chromium } from "playwright";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const ORIGIN = "http://127.0.0.1:1"; // fictícia: a rota atende da raiz do repositório

let browser;
before(async () => {
  execSync("npm --prefix webapp run build:dashboard", { cwd: ROOT, stdio: "pipe" });
  browser = await chromium.launch();
});
after(() => browser?.close());

async function abrir({ width = 1440, hash = "#/", perfil = "padrao", qs = "", sorte = null } = {}) {
  const ctx = await browser.newContext({ viewport: { width, height: 900 }, reducedMotion: "reduce" });
  await ctx.route("**/*", (r) => {
    const url = new URL(r.request().url());
    if (url.origin !== ORIGIN) return r.abort();
    const path = decodeURIComponent(url.pathname).replace(/\/$/, "/index.html");
    return r.fulfill({ path: join(ROOT, path) }).catch(() => r.fulfill({ status: 404, body: "" }));
  });
  await ctx.addInitScript((p) => localStorage.setItem("pigbank.dashboard.profile.v1", JSON.stringify(p)), perfil);
  if (sorte !== null) await ctx.addInitScript((v) => { Math.random = () => v; }, sorte);
  const page = await ctx.newPage();
  const erros = [];
  page.on("pageerror", (e) => erros.push(e.message));
  await page.goto(`${ORIGIN}/dashboard-v2/${qs}${hash}`);
  await page.locator("#page-title").waitFor();
  return { ctx, page, erros };
}
const perguntar = async (page, texto) => {
  await page.locator("#askbar-input").fill(texto);
  await page.locator("#askbar-input").press("Enter");
  await page.waitForFunction(() => location.hash === "#/piggy");
};
const respostas = (page) => page.locator(".chat > .msg-piggy").count();
// Clica uma próxima pergunta da última resposta e espera a resposta dela chegar.
const seguir = async (page, nome) => {
  const antes = await respostas(page);
  await page.locator(".chat > .msg-piggy").last().locator(".chat-follow button", { hasText: nome }).click();
  await page.waitForFunction((n) => document.querySelectorAll(".chat > .msg-piggy").length > n, antes);
};

test("desktop: a barra está em todas as páginas; celular: só na conversa", async () => {
  const { ctx, page } = await abrir();
  const desktop = {};
  for (const h of ["#/", "#/gastos", "#/simulador", "#/ferramentas", "#/piggy"]) {
    await page.evaluate((x) => { location.hash = x; }, h);
    await page.waitForFunction((x) => location.hash === x, h);
    desktop[h] = await page.locator(".askbar").isVisible();
  }
  await page.setViewportSize({ width: 390, height: 844 });
  const celular = {};
  for (const h of ["#/", "#/piggy"]) {
    await page.evaluate((x) => { location.hash = x; }, h);
    await page.waitForFunction((x) => location.hash === x, h);
    celular[h] = await page.locator(".askbar").isVisible();
  }
  await ctx.close();
  assert.deepEqual(desktop, { "#/": true, "#/gastos": true, "#/simulador": true, "#/ferramentas": true, "#/piggy": true });
  assert.deepEqual(celular, { "#/": false, "#/piggy": true });
});

test("digitar numa página qualquer abre a conversa; texto livre recebe os atalhos", async () => {
  const { ctx, page, erros } = await abrir({ hash: "#/gastos" });
  await perguntar(page, "qual a capital da França?");
  await page.locator(".chat > .msg-piggy").first().waitFor();
  const r = await page.evaluate(() => [
    document.querySelector(".chat .msg-user").textContent,
    document.querySelector(".chat .msg-piggy .msg-text").textContent.startsWith("Aqui no protótipo"),
    document.querySelectorAll(".chat .msg-piggy .chat-follow button").length,
    document.querySelector("#askbar-input").value,
  ]);
  await ctx.close();
  assert.deepEqual(r, ["qual a capital da França?", true, 4, ""]);
  assert.deepEqual(erros, []);
});

test("os 8 assuntos respondem com blocos, sem erro e sem id repetido", async () => {
  const { ctx, page, erros } = await abrir();
  await perguntar(page, "oi");
  await page.locator(".chat > .msg-piggy").first().waitFor();
  const blocos = {};
  for (const [assunto, nome] of [
    ["categorias", "Pra onde vai meu dinheiro?"], ["lancamentos", "Quais foram meus maiores gastos do mês?"],
    ["categoria", /^Me mostra o detalhe/], ["saldo", "Vai sobrar até o fim do mês?"], ["fatura", "E a minha fatura?"],
    ["saldo2", "Vai sobrar até o fim do mês?"], ["metas", "Quanto falta pras minhas metas?"],
    ["investimentos", "Como estão meus investimentos?"], ["renda", "Como anda a minha renda?"],
  ]) {
    await seguir(page, nome);
    blocos[assunto] = await page.locator(".chat > .msg-piggy").last().locator(".msg-block").count();
  }
  const r = await page.evaluate(() => {
    const ids = [...document.querySelectorAll("[id]")].map((e) => e.id);
    return [ids.filter((x, i) => ids.indexOf(x) !== i), document.querySelectorAll(".chat .chat-follow").length];
  });
  await ctx.close();
  assert.deepEqual(blocos, { categorias: 1, lancamentos: 1, categoria: 1, saldo: 2, fatura: 2, saldo2: 2, metas: 1, investimentos: 2, renda: 1 });
  assert.deepEqual(r, [[], 1]); // nenhum id repetido; só a última resposta oferece próximas perguntas
  assert.deepEqual(erros, []);
});

// Perguntas da faixa que pedem um recorte do assunto: a resposta tem de responder a elas.
test("\"qual categoria mais cresceu\" e \"que dia da semana\" respondem o que foi perguntado", async () => {
  const { ctx, page } = await abrir({ hash: "#/piggy", perfil: "controlar" });
  const clicar = async (texto) => {
    const antes = await respostas(page);
    await page.locator(".chat-follow button", { hasText: texto }).first().click();
    await page.waitForFunction((n) => document.querySelectorAll(".chat > .msg-piggy").length > n, antes);
    return page.locator(".chat > .msg-piggy").last().evaluate((m) => [m.querySelector(".msg-text").textContent, [...m.querySelectorAll(".msg-block article")].map((a) => a.id.replace(/^m\d+-/, ""))]);
  };
  const cresceu = await clicar("Qual categoria de gasto mais cresceu este mês?");
  await ctx.close();
  // Delivery +20%. Antes a resposta era Mercado (o maior, que caiu 9%). Outros sobe 32% em
  // cima de ~R$ 29: fica fora pela mesma regra do "Piggy notou" (> R$ 50 no mês anterior).
  assert.match(cresceu[0], /^A que mais cresceu foi Delivery: .*\(20% a mais\)/);
  assert.deepEqual(cresceu[1], ["w-cat-detalhe"]);
  const { ctx: c2, page: p2 } = await abrir({ hash: "#/piggy", perfil: "controlar" });
  await p2.locator(".chat-follow button", { hasText: "Em que dia da semana eu mais gasto?" }).click();
  await p2.locator(".chat > .msg-piggy .msg-block").first().waitFor();
  const dia = await p2.locator(".chat > .msg-piggy").last().evaluate((m) => [m.querySelector(".msg-text").textContent, [...m.querySelectorAll(".msg-block article")].map((a) => a.id.replace(/^m\d+-/, ""))]);
  await c2.close();
  assert.match(dia[0], /^Você gasta mais (aos|às) \S+: R\$/);
  assert.deepEqual(dia[1], ["w-calendario"]);
});

test("\"qual foi meu maior gasto este mês\" olha o mês inteiro, não a semana", async () => {
  // Padrão sem perfil: 3 insights (peso 2) + 10 comuns (peso 1) = 16; "maior-gasto" é a
  // 8ª comum, na faixa [13, 14). A 1ª checagem confirma que a sorte caiu nela.
  const { ctx, page } = await abrir({ sorte: 13.5 / 16 });
  const chave = await page.locator(".piggy-band").getAttribute("data-band");
  await page.locator(".piggy-band").click();
  await page.locator(".chat > .msg-piggy .msg-block").first().waitFor();
  const texto = await page.locator(".chat > .msg-piggy .msg-text").textContent();
  await ctx.close();
  assert.equal(chave, "maior-gasto");
  assert.match(texto, /^Seu maior gasto de setembro foi Aluguel \(sua parte\) \(R\$ 950\)/);
});

test("a conversa sobrevive à troca de página e some ao recarregar", async () => {
  const { ctx, page } = await abrir();
  await perguntar(page, "oi");
  await page.locator(".chat > .msg-piggy").first().waitFor();
  await page.evaluate(() => { location.hash = "#/metas"; });
  await page.locator("#page-title", { hasText: "Metas" }).waitFor();
  await page.evaluate(() => { location.hash = "#/piggy"; });
  const volta = await page.locator(".chat > li").count();
  await page.reload();
  await page.locator("#page-title").waitFor();
  const depois = [await page.locator(".chat > li").count(), await page.locator(".chat-empty").count()];
  await ctx.close();
  assert.equal(volta, 2);
  assert.deepEqual(depois, [0, 1]);
});

test("estado vazio: as sugestões do perfil vêm primeiro e respondem", async () => {
  const { ctx, page } = await abrir({ hash: "#/piggy", perfil: "investir" });
  const ideias = await page.locator(".chat-empty .chat-follow button").evaluateAll((bs) => bs.map((b) => b.textContent));
  await page.locator(".chat-empty .chat-follow button").first().click();
  await page.locator(".chat > .msg-piggy .msg-block").first().waitFor();
  const blocos = await page.locator(".chat > .msg-piggy .msg-block").count();
  await ctx.close();
  assert.equal(ideias.length, 5);
  assert.equal(ideias[0], "Minha carteira está rendendo bem comparada ao CDI?");
  assert.equal(blocos, 2); // Rendimento × CDI + Onde está o dinheiro
});

test("Essencial: a conversa vira convite e a barra leva até ele", async () => {
  const { ctx, page } = await abrir({ hash: "#/gastos", qs: "?plano=essencial" });
  const barra = await page.locator(".askbar").evaluate((a) => [a.tagName, a.getAttribute("href"), a.textContent]);
  await page.locator(".askbar").click();
  await page.locator(".chat-plus").waitFor();
  const r = await page.evaluate(() => [!!document.querySelector("#askbar-input"), document.querySelector(".chat-plus-cta a").getAttribute("href")]);
  await ctx.close();
  assert.equal(barra[0], "A");
  assert.equal(barra[1], "#/piggy");
  assert.match(barra[2], /no Plus/);
  assert.deepEqual(r, [false, "../frontend/precos.html"]);
});

test("320 e 390: a conversa não rola para o lado e a barra fica acima da de baixo", async () => {
  for (const width of [320, 390]) {
    const { ctx, page } = await abrir({ width, hash: "#/piggy" });
    await page.locator(".chat-empty .chat-follow button").first().click();
    await page.locator(".chat > .msg-piggy .msg-block").first().waitFor();
    const r = await page.evaluate(() => {
      const a = document.querySelector(".askbar").getBoundingClientRect();
      const t = document.querySelector(".tabbar").getBoundingClientRect();
      return [document.scrollingElement.scrollWidth - document.scrollingElement.clientWidth, a.bottom <= t.top, a.left >= 0 && a.right <= innerWidth];
    });
    await ctx.close();
    assert.deepEqual(r, [0, true, true], String(width));
  }
});

test("toda pergunta pronta responde com texto, sem erro (as sugestões de cada perfil e o fim de semana)", async () => {
  const vistos = {};
  for (const perfil of ["economizar", "investir", "controlar", "dividas", "autonomo"]) for (let i = 0; i < 5; i++) {
    const { ctx, page, erros } = await abrir({ hash: "#/piggy", perfil });
    const botao = page.locator(".chat-empty .chat-follow button").nth(i);
    const pergunta = await botao.textContent();
    await botao.click();
    await page.locator(".chat > .msg-piggy .msg-text").first().waitFor();
    vistos[pergunta] = [await page.locator(".chat > .msg-piggy .msg-text").textContent(), erros.length];
    await ctx.close();
  }
  // "fim-de-semana" não está entre as sugestões: pela faixa, com a sorte na faixa [12, 13) de 16.
  const { ctx, page, erros } = await abrir({ sorte: 12.5 / 16 });
  const chave = await page.locator(".piggy-band").getAttribute("data-band");
  await page.locator(".piggy-band").click();
  await page.locator(".chat > .msg-piggy .msg-text").first().waitFor();
  const fds = await page.locator(".chat > .msg-piggy .msg-text").textContent();
  await ctx.close();
  for (const [q, [texto, n]] of Object.entries(vistos)) assert.ok(texto.length > 40 && n === 0, `${q}: ${texto} (${n} erros)`);
  assert.equal(chave, "fim-de-semana");
  assert.match(fds, /por fim de semana|segurar o fim de semana/);
  assert.deepEqual(erros, []);
});

test("a resposta nova é anunciada para leitor de tela", async () => {
  const { ctx, page } = await abrir({ hash: "#/piggy" });
  await page.locator(".chat-empty .chat-follow button").first().click();
  await page.locator(".chat > .msg-piggy").first().waitFor();
  await perguntar(page, "oi");
  await page.waitForFunction(() => document.querySelectorAll(".chat > .msg-piggy").length === 2);
  await page.waitForFunction(() => document.querySelector("[role=status]").textContent === [...document.querySelectorAll(".chat > .msg-piggy .msg-text")].pop().textContent, null, { timeout: 2000 }).catch(() => {});
  const r = await page.evaluate(() => [document.querySelector("[role=status]").textContent, [...document.querySelectorAll(".chat > .msg-piggy .msg-text")].pop().textContent]);
  // Duas respostas iguais seguidas (texto livre): a região esvazia e enche de novo, então muda.
  const mudancas = await page.evaluate(async () => {
    const st = document.querySelector("[role=status]");
    let n = 0; new MutationObserver(() => n++).observe(st, { childList: true, subtree: true, characterData: true });
    const inp = document.querySelector("#askbar-input"); const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
    set.call(inp, "oi de novo"); inp.dispatchEvent(new Event("input", { bubbles: true }));
    await new Promise((r) => setTimeout(r, 50)); inp.form.requestSubmit();
    await new Promise((r) => setTimeout(r, 400));
    return [n, st.textContent.startsWith("Aqui no protótipo")];
  });
  await ctx.close();
  assert.equal(r[0], r[1]);
  assert.ok(mudancas[0] >= 2 && mudancas[1], JSON.stringify(mudancas)); // esvaziou e encheu com a mesma resposta
});

// As que respondiam só de lado (Codex #584, 2ª rodada, e a varredura das 23 perguntas).
test("as perguntas de dívida, assinatura, resumo, reserva e economia respondem o que pediram", async () => {
  const pela = async (perfil, texto) => {
    const { ctx, page } = await abrir({ hash: "#/piggy", perfil });
    await page.locator(".chat-follow button", { hasText: texto }).click();
    await page.locator(".chat > .msg-piggy .msg-text").first().waitFor();
    const t = await page.locator(".chat > .msg-piggy .msg-text").textContent();
    await ctx.close();
    return t;
  };
  const faixa = async (sorte, chave) => {
    const { ctx, page } = await abrir({ sorte });
    assert.equal(await page.locator(".piggy-band").getAttribute("data-band"), chave);
    await page.locator(".piggy-band").click();
    await page.locator(".chat > .msg-piggy .msg-text").first().waitFor();
    const t = await page.locator(".chat > .msg-piggy .msg-text").textContent();
    await ctx.close();
    return t;
  };
  assert.match(await pela("dividas", "comprometido"), /^No mês que vem, R\$ 1\.821 já estão comprometidos: R\$ 1\.310 de contas fixas e R\$ 511 de parcelas/);
  assert.match(await pela("dividas", "volta a caber"), /acaba em março de 2027/);
  assert.match(await pela("investir", "reserva de emergência"), /^Sua reserva cobre 3,3 meses .* já passou do mínimo/);
  assert.match(await pela("dividas", "economizar este mês"), /^O que mais subiu foi Delivery/);
  // comuns fora das sugestões: pela faixa (3 insights × 2 + 10 comuns × 1 = 16)
  assert.match(await faixa(11.5 / 16, "assinaturas"), /^Você paga 3 assinaturas: Spotify .*Netflix .* e Academia .*R\$ 166,70 por mês/);
  assert.match(await faixa(14.5 / 16, "resumo"), /^1\. Entraram .*2\. O que mais pesa .*3\. No ritmo atual/);
});

// 3ª rodada do Codex no #584: o critério "o texto responde literalmente ao que foi
// perguntado?" aplicado às 23 perguntas e aos 3 insights. Estas eram as que faltavam.
test("insights e as perguntas restantes respondem o que pediram (e com o mesmo número do painel)", async () => {
  const pelaFaixa = async (sorte) => {
    const { ctx, page } = await abrir({ sorte });
    const chave = await page.locator(".piggy-band").getAttribute("data-band");
    await page.locator(".piggy-band").click();
    await page.locator(".chat > .msg-piggy .msg-text").first().waitFor();
    const t = await page.locator(".chat > .msg-piggy .msg-text").textContent();
    await ctx.close();
    return [chave, t];
  };
  const pela = async (perfil, texto) => {
    const { ctx, page } = await abrir({ hash: "#/piggy", perfil });
    await page.locator(".chat-follow button", { hasText: texto }).click();
    await page.locator(".chat > .msg-piggy .msg-text").first().waitFor();
    const t = await page.locator(".chat > .msg-piggy .msg-text").textContent();
    await ctx.close();
    return t;
  };
  // padrão: 3 insights (peso 2) nas faixas [0,2), [2,4), [4,6); "normal" é a 5ª comum, [10,11)
  const [k1, rise] = await pelaFaixa(1 / 16);
  const [k2, trend] = await pelaFaixa(3 / 16);
  const [k3, next] = await pelaFaixa(5 / 16);
  const [k4, normal] = await pelaFaixa(10.5 / 16);
  assert.deepEqual([k1, k2, k3, k4], ["insight-rise", "insight-trend", "insight-next", "normal"]);
  assert.match(rise, /^Subiu porque você pediu mais vezes: 7 contra 6 em agosto/);
  assert.match(trend, /cerca de R\$ 866 em 90 dias: R\$ 665 por mês a menos/); // o "Piggy notou" diz R$ 665
  assert.match(next, /^Sim\. Academia \(R\$ 100\) vence dia 28/);
  assert.match(normal, /média dos meses anteriores no mesmo ponto é R\$ 2\.519, então este mês está 5% acima do normal/);
  assert.match(await pela("investir", "rendendo bem"), /^Em 12 meses ela rendeu 96% do CDI .*um pouco abaixo do CDI/);
  assert.match(await pela("autonomo", "no azul"), /^Sim\. Até o dia 23 entraram R\$ 4\.300, saíram R\$ 2\.636 .*sobram R\$ 934/);
});

test("as próximas perguntas também respondem o que pedem (investir, maiores gastos, onde cortar)", async () => {
  const { ctx, page } = await abrir();
  await perguntar(page, "oi");
  await page.locator(".chat > .msg-piggy").first().waitFor();
  const texto = async () => page.locator(".chat > .msg-piggy").last().locator(".msg-text").textContent();
  await seguir(page, "Como estão meus investimentos?");
  await seguir(page, "Quanto sobra pra investir?");
  const investir = await texto();
  await seguir(page, "Quanto falta pras minhas metas?");
  await seguir(page, "Onde dá pra cortar pra chegar antes?");
  const cortar = await texto();
  await seguir(page, "Quais foram meus maiores gastos do mês?");
  const maiores = await texto();
  await ctx.close();
  assert.match(investir, /dá pra investir uns R\$ 990/); // não o saldo inteiro (~R$ 2.300)
  assert.match(cortar, /^O que mais subiu foi Delivery/); // não Mercado, que caiu
  assert.match(maiores, /^Seu maior gasto de setembro foi Aluguel/); // o mês, não a semana
});
