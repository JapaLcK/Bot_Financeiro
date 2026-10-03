/**
 * /painel: o plano vem do GET /api/v2/me, e o portão (webapp/src/dashboard/parts/Entrada.tsx)
 * decide o que aparece antes de o painel montar.
 *
 *   · o plano é o da API, nunca o da URL; `free` e plano desconhecido travam todo bloco pago;
 *   · enquanto o /me não chega: só a tela de carregamento (role=status), nenhum bloco;
 *   · 401 → o auth-refresh.js renova e repete; se o refresh também dá 401, ele apaga o
 *     estado do aparelho (localStorage) e devolve o 401: o portão mostra o erro e NÃO
 *     redireciona (quem manda para o login é o servidor, no Recarregar);
 *   · qualquer erro é uma tela só, com texto fixo em português (nunca a `message` do
 *     servidor, que em 402/404 vem em inglês), sem trocar de URL; 4xx não repete, 5xx e
 *     rede tentam 3 vezes, mesmo com o navegador dizendo "offline"; "Recarregar" recarrega;
 *   · a exceção é o 403 `password_required` (conta paga sem senha): o texto pede a senha e
 *     o "Criar senha" leva à /home; nenhum outro erro tem esse botão;
 *   · o protótipo (dashboard-v2/index.html) não fala com a API e usa o ?plano= dele.
 *
 * Rodar:  npm run test:frontend   (abre o artefato commitado: mudou webapp/src, rode
 *         `npm --prefix webapp run build`)
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { PAINEL, PROTOTIPO, RAIZ, RESPOSTAS, exigeArtefatoEmDia, servir } from "./_painel.mjs";
import { WIDGET_FEATURE } from "../../webapp/src/dashboard/lib/profiles.js";

const PAGOS = Object.keys(WIDGET_FEATURE); // hero, piggy, simulador
const ERRO = "Não deu para carregar o painel";
const ALERTA = `${ERRO}Recarregue a página ou volte ao painel antigo.`;

let browser;
before(async () => { exigeArtefatoEmDia(); browser = await chromium.launch(); });
after(() => browser?.close());

async function contexto({ raiz, plano = "pro" } = {}) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: "reduce" });
  await servir(ctx, raiz, { plano });
  await ctx.addInitScript(() => localStorage.setItem("pigbank.dashboard.profile.v1", '"padrao"'));
  return ctx;
}

// Abre a página e conta o que interessa: erros de JS, pedidos ao /api/v2/me (o stream
// /api/v2/eventos tem teste próprio, painel_v2_eventos) e ao refresh, e navegações do
// documento (o reload).
async function abrir(ctx, url = `${PAINEL}#/`) {
  const page = await ctx.newPage();
  const n = { erros: [], api: 0, refresh: 0, navegacoes: 0 };
  page.on("pageerror", (e) => n.erros.push(e.message));
  page.on("request", (r) => {
    const p = new URL(r.url()).pathname;
    if (p === "/api/v2/me") n.api++;
    if (p === "/auth/refresh") n.refresh++;
  });
  page.on("framenavigated", (f) => { if (f === page.mainFrame()) n.navegacoes++; });
  await page.goto(url);
  return { page, n };
}

const montado = (page) => page.locator("#board-profile").waitFor();
const blocos = (page) => page.evaluate(() => [...document.querySelectorAll("[data-widget-id]")].map((w) => w.dataset.widgetId));
const pagosNaTela = async (page) => (await blocos(page)).filter((id) => PAGOS.includes(id)).sort();
const telaDeErro = (page) => page.getByRole("heading", { name: ERRO }).waitFor({ timeout: 15000 });
const textoDoAlerta = (page) => page.getByRole("alert").textContent();
// A tela genérica: o texto de sempre e nenhum "Criar senha" (esse é só do password_required).
const GENERICA = [ALERTA, 0];
const generica = async (page) => [await textoDoAlerta(page), await page.getByRole("link", { name: "Criar senha" }).count()];

/** Responde o /me com uma fixture de erro (ou qualquer `{ status, headers, body }`). */
const erroDoMe = (f) => (r) => r.fulfill({ status: f.status, headers: f.headers, json: f.body });

test("o plano vem da API, não da URL", async () => {
  const ctx1 = await contexto({ plano: "essencial" });
  const a = await abrir(ctx1, `${PAINEL}?plano=pro#/`);
  await montado(a.page);
  const essencial = await pagosNaTela(a.page);
  await ctx1.close();

  const ctx2 = await contexto({ plano: "pro" });
  const b = await abrir(ctx2, `${PAINEL}?plano=essencial#/`);
  await montado(b.page);
  const pro = await pagosNaTela(b.page);
  await ctx2.close();

  assert.deepEqual(essencial, []);
  assert.deepEqual(pro, [...PAGOS].sort());
});

for (const [nome, me] of [["free", RESPOSTAS.me.free], ["desconhecido", { plan_tier: "gold" }]]) {
  test(`plano ${nome}: monta sem nenhum bloco pago e sem erro de JS`, async () => {
    const ctx = await contexto();
    await ctx.route("**/api/v2/me", (r) => r.fulfill({ json: me }));
    const { page, n } = await abrir(ctx);
    await montado(page);
    const pagos = await pagosNaTela(page);
    const total = (await blocos(page)).length;
    await ctx.close();
    assert.deepEqual(pagos, []);
    assert.ok(total > 0, "os blocos de todo plano continuam");
    assert.deepEqual(n.erros, []);
  });
}

test("carregando: só a tela de carregamento até o /me chegar; depois o painel monta", async () => {
  const ctx = await contexto();
  let libera;
  const segura = new Promise((r) => { libera = r; });
  await ctx.route("**/api/v2/me", async (r) => { await segura; await r.fulfill({ json: RESPOSTAS.me.pro }); });
  const { page, n } = await abrir(ctx);
  const status = page.getByRole("status");
  await status.waitFor();
  const antes = [await status.textContent(), (await blocos(page)).length, await page.locator(".piggy-band, #board-profile, .selo").count()];
  libera();
  await montado(page);
  const depois = [await page.getByText("Carregando o painel…").count(), (await blocos(page)).length > 0];
  await ctx.close();
  assert.deepEqual(antes, ["Carregando o painel…", 0, 0]);
  assert.deepEqual(depois, [0, true]);
  assert.deepEqual(n.erros, []);
});

test("401 → refresh 200 → o mesmo pedido de novo: o painel monta, com um refresh só", async () => {
  const ctx = await contexto();
  let vezes = 0;
  await ctx.route("**/api/v2/me", (r) => (vezes++ === 0 ? erroDoMe(RESPOSTAS.erros["401"])(r) : r.fulfill({ json: RESPOSTAS.me.pro })));
  await ctx.route("**/auth/refresh", (r) => r.fulfill({ json: { ok: true } }));
  const { page, n } = await abrir(ctx);
  await montado(page);
  const pagos = await pagosNaTela(page);
  await ctx.close();
  assert.deepEqual([n.refresh, n.api], [1, 2]);
  assert.deepEqual(pagos, [...PAGOS].sort());
});

test("401 → refresh 401: tela de erro, sem redirecionar; o auth-refresh apaga o estado do aparelho", async () => {
  const ctx = await contexto();
  await ctx.route("**/api/v2/me", erroDoMe(RESPOSTAS.erros["401"]));
  await ctx.route("**/auth/refresh", (r) => r.fulfill({ status: 401, json: { detail: "x" } }));
  const { page, n } = await abrir(ctx);
  await telaDeErro(page);
  await page.waitForTimeout(1500); // um retry viria em 1 s
  const r = [page.url(), await generica(page), n.api, n.refresh, await page.evaluate(() => localStorage.getItem("pigbank.dashboard.profile.v1"))];
  await ctx.close();
  assert.deepEqual(r, [`${PAINEL}#/`, GENERICA, 1, 1, null]);
});

for (const nome of ["402", "403", "403_pro_required", "404", "404_not_found"]) {
  test(`${nome} no envelope: tela de erro com texto fixo (sem a message do servidor), sem repetir nem trocar de URL; Recarregar recarrega`, async () => {
    const f = RESPOSTAS.erros[nome];
    const ctx = await contexto();
    await ctx.route("**/api/v2/me", erroDoMe(f));
    const { page, n } = await abrir(ctx);
    await telaDeErro(page);
    await page.waitForTimeout(1500); // um retry viria em 1 s
    const r = [page.url(), await generica(page), n.api, n.refresh,
      await page.getByRole("link", { name: "Painel antigo" }).getAttribute("href")];
    const navegacoes = n.navegacoes;
    await Promise.all([page.waitForEvent("framenavigated"), page.getByRole("button", { name: "Recarregar" }).click()]);
    await telaDeErro(page);
    const depois = [n.navegacoes - navegacoes, n.api];
    await ctx.close();
    assert.deepEqual(r, [`${PAINEL}#/`, GENERICA, 1, 0, "/app"]);
    assert.ok(!r[1][0].includes(f.body.error.message), `a message "${f.body.error.message}" não vai para a tela`);
    assert.deepEqual(depois, [1, 2]);
    assert.deepEqual(n.erros, []);
  });
}

for (const [nome, responde] of [
  ["403 do CSRF (fora do envelope)", erroDoMe(RESPOSTAS.erros["403_csrf"])],
  ["502 com HTML", (r) => r.fulfill({ status: 502, contentType: "text/html", body: "<html><body>Bad Gateway</body></html>" })],
]) {
  test(`${nome}: tela de erro genérica, sem erro de JS`, async () => {
    const ctx = await contexto();
    await ctx.route("**/api/v2/me", responde);
    const { page, n } = await abrir(ctx);
    await telaDeErro(page);
    const texto = await generica(page);
    await ctx.close();
    assert.deepEqual(texto, GENERICA);
    assert.deepEqual(n.erros, []);
  });
}

// Conta paga sem senha: toda a /api/v2 dá 403 `password_required`, então ela para aqui.
for (const width of [1440, 390, 320]) {
  test(`${width}px: 403 password_required pede a senha, com "Criar senha" para a /home (44px, foco visível); o painel não monta`, async () => {
    const f = RESPOSTAS.erros["403_password_required"];
    const ctx = await contexto();
    await ctx.route("**/api/v2/me", erroDoMe(f));
    const { page, n } = await abrir(ctx);
    await page.setViewportSize({ width, height: 844 });
    await telaDeErro(page);
    await page.waitForTimeout(1500); // um retry viria em 1 s
    const link = page.getByRole("link", { name: "Criar senha" });
    for (let i = 0; i < 6 && !(await link.evaluate((a) => a === document.activeElement)); i++) await page.keyboard.press("Tab");
    const r = {
      alerta: await textoDoAlerta(page),
      link: await link.evaluate((a) => [a.getAttribute("href"), a === document.activeElement && a.matches(":focus-visible"), getComputedStyle(a).outlineStyle, Math.round(a.getBoundingClientRect().height)]),
      saidas: [await page.getByRole("link", { name: "Painel antigo" }).getAttribute("href"), await page.getByRole("button", { name: "Recarregar" }).count()],
      painel: [(await blocos(page)).length, await page.locator("#board-profile").count()],
      rolagem: await page.evaluate(() => document.scrollingElement.scrollWidth - document.scrollingElement.clientWidth),
      pedidos: n.api,
    };
    await ctx.close();
    assert.deepEqual(r, {
      alerta: `${ERRO}Para abrir o painel novo, crie a sua senha. Depois de criar, volte para o painel novo.`,
      link: ["/home", true, "solid", 44],
      saidas: ["/app", 1],
      painel: [0, 0],
      rolagem: 0,
      pedidos: 1,
    });
    assert.ok(!r.alerta.includes(f.body.error.message));
    assert.deepEqual(n.erros, []);
  });
}

for (const [nome, responde] of [
  ["500", erroDoMe(RESPOSTAS.erros["500"])],
  ["503", erroDoMe(RESPOSTAS.erros["503"])],
  ["rede fora", (r) => r.abort("internetdisconnected")],
]) {
  test(`${nome}: 3 tentativas e depois a tela de erro`, async () => {
    const ctx = await contexto();
    await ctx.route("**/api/v2/me", responde);
    const { page, n } = await abrir(ctx);
    await telaDeErro(page);
    await page.waitForTimeout(500);
    const r = [n.api, (await blocos(page)).length, await generica(page)];
    await ctx.close();
    assert.deepEqual(r, [3, 0, GENERICA]);
    assert.deepEqual(n.erros, []);
  });
}

test("não refaz /me ao voltar para a aba depois da tela de erro", async () => {
  const ctx = await contexto();
  await ctx.route("**/api/v2/me", erroDoMe(RESPOSTAS.erros["500"]));
  const { page, n } = await abrir(ctx);
  await telaDeErro(page);

  const novoPedido = page.waitForRequest((r) => new URL(r.url()).pathname === "/api/v2/me", { timeout: 1200 })
    .then(() => true, () => false);
  await page.evaluate(() => window.dispatchEvent(new Event("visibilitychange")));
  const refez = await novoPedido;
  const pedidos = n.api;
  await ctx.close();

  assert.equal(refez, false, "voltar para a aba não deve iniciar outra rodada de tentativas");
  assert.equal(pedidos, 3);
});

// O TanStack pausa query e retry quando o navegador avisa `offline` (networkMode "online",
// o padrão); o meQuery usa "always" para a rede caída virar a tela de erro, não um
// "Carregando…" eterno.
test("rede cai com o /me em voo: depois das tentativas, a tela de erro (não carregando para sempre)", async () => {
  const ctx = await contexto();
  let libera;
  const segura = new Promise((r) => { libera = r; });
  await ctx.route("**/api/v2/me", async (r) => { await segura; await r.abort("internetdisconnected"); });
  const { page, n } = await abrir(ctx);
  await page.getByRole("status").waitFor();
  await page.evaluate(() => window.dispatchEvent(new Event("offline")));
  libera();
  await telaDeErro(page);
  const r = [n.api, await generica(page), (await blocos(page)).length];
  await ctx.close();
  assert.deepEqual(r, [3, GENERICA, 0]);
  assert.deepEqual(n.erros, []);
});

test("protótipo: não fala com a API; sem ?plano= é o Pro, com ?plano=essencial trava os pagos", async () => {
  const vistos = {};
  let api = 0;
  for (const qs of ["", "?plano=essencial"]) {
    const ctx = await contexto({ raiz: RAIZ });
    const { page, n } = await abrir(ctx, `${PROTOTIPO}${qs}#/`);
    await montado(page);
    vistos[qs] = await pagosNaTela(page);
    api += n.api;
    await ctx.close();
  }
  assert.equal(api, 0);
  assert.deepEqual(vistos, { "": [...PAGOS].sort(), "?plano=essencial": [] });
});
