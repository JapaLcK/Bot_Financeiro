/**
 * /painel#/previsao com backend: falha, dado antigo, corrida de horizonte, `valido_ate`,
 * SSE, downgrade e sessão (webapp/src/dashboard/widgets/Previsao.tsx, lib/v2.ts
 * `previsaoQuery`). A tela em si: dashboard_v2_previsao.test.mjs.
 *
 *   · falha sem dado nunca mostra número, gráfico nem lista vazia;
 *   · rede/5xx com dado mostra o antigo com aviso; 4xx nunca mostra dado guardado;
 *   · a resposta de um horizonte abandonado nunca aparece como a do novo;
 *   · `valido_ate` reconsulta uma vez na fronteira, sem laço (piso de 60 s);
 *   · downgrade Pro → Plus com 90 dias: nenhum detalhe Pro reaparece (`gcTime: 0`).
 *
 * Rodar:  npm run test:frontend   (abre o artefato commitado frontend/dashboard-app.*: mudou webapp/src, rode `npm --prefix webapp run build`)
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { PAINEL, RESPOSTAS, abrirPainel, exigeArtefatoEmDia, servir } from "./_painel.mjs";

let browser;
before(async () => { exigeArtefatoEmDia(); browser = await chromium.launch(); });
after(() => browser?.close());

const P = RESPOSTAS.previsao;
const ROTA = "**/api/v2/previsao**";
const erro = (nome) => (r) => r.fulfill({ status: RESPOSTAS.erros[nome].status, headers: RESPOSTAS.erros[nome].headers, json: RESPOSTAS.erros[nome].body });
const pronta = (page) => page.locator("#w-previsao .prev-hora").waitFor();
const relida = (page) => page.evaluate(() => window.dispatchEvent(new Event("visibilitychange"))); // focusManager do TanStack
// Uma promessa com a mão de fora para soltar a resposta segurada.
const portao = () => { let soltar; const p = new Promise((r) => { soltar = r; }); return [p, soltar]; };
// O pedido abandonado é abortado pelo app (gcTime 0 + signal): o fulfill dele falha, e tudo bem.
const atender = (r, corpo) => r.fulfill({ json: corpo }).catch(() => {});

async function abrir(opts = {}) {
  const r = await abrirPainel(browser, { espera: "#page-title", agora: "2026-10-06T15:00:00Z", ...opts });
  r.pedidos = [];
  r.page.on("request", (q) => { const u = new URL(q.url()); if (u.pathname === "/api/v2/previsao") r.pedidos.push(u.search); });
  return r;
}
const semNumero = (page) => page.evaluate(() => {
  const m = document.querySelector("main");
  return { rs: m.innerText.includes("R$"), svg: m.querySelectorAll("svg[role=img]").length, lista: !!m.querySelector("#w-previsao-compromissos") };
});

test("1ª carga falha (500, 3 tentativas): sem R$, sem gráfico, sem lista; Tentar de novo carrega", async () => {
  const { ctx, page, ir, pedidos } = await abrir();
  let falhar = true;
  await ctx.route(ROTA, (r) => (falhar ? erro("500")(r) : r.fulfill({ json: P.pro30 })));
  await ir("/previsao");
  await page.getByText("Não deu para carregar a previsão.").waitFor({ timeout: 15000 });
  const sem = await semNumero(page);
  const tentativas = pedidos.length;
  falhar = false;
  await page.getByRole("button", { name: "Tentar de novo" }).click();
  await pronta(page);
  const valor = await page.locator("#w-previsao .hero-value").textContent();
  await ctx.close();
  assert.deepEqual(sem, { rs: false, svg: 0, lista: false });
  assert.equal(tentativas, 3);
  assert.equal(valor, "R$ 7,33");
});

test("dado antigo: a releitura falha (503) e a tela segue com os valores e o aviso da hora", async () => {
  const { ctx, page, ir } = await abrir();
  await ir("/previsao");
  await pronta(page);
  await ctx.route(ROTA, erro("503"));
  await relida(page);
  await page.getByText("Mostrando a previsão calculada às 12:00. Não deu para atualizar.").waitFor({ timeout: 15000 });
  const r = [await page.locator("#w-previsao .hero-value").textContent(), await page.locator("#w-previsao svg[role=img]").count(), await page.locator("#w-previsao-compromissos .grupo").count()];
  await ctx.close();
  assert.deepEqual(r, ["R$ 7,33", 1, 1]);
});

test("troca rápida 60 → 90 com o 60 atrasado: a tela nunca mostra outro horizonte depois do clique no 90", async () => {
  const { ctx, page, ir } = await abrir();
  const [segura60, solta60] = portao();
  const [segura90, solta90] = portao();
  await ctx.route(ROTA, async (r) => {
    const dias = new URL(r.request().url()).searchParams.get("dias");
    if (dias === "60") await segura60;
    if (dias === "90") await segura90;
    return atender(r, P[`pro${dias ?? 30}`]);
  });
  await ir("/previsao");
  await pronta(page);
  await page.getByRole("radio", { name: "60 dias" }).click();
  await page.getByRole("radio", { name: "90 dias" }).click();
  // Anota quantos marcos extras a tela mostra (0 = 30 dias, 1 = 60, 2 = 90) desde já e a cada mudança.
  await page.evaluate(() => {
    window.__marcos = [];
    const olhar = () => { const w = document.querySelector("#w-previsao"); if (w?.querySelector(".hero-value")) window.__marcos.push(w.querySelectorAll(".marcos li").length); };
    olhar();
    new MutationObserver(olhar).observe(document.body, { subtree: true, childList: true, characterData: true });
  });
  solta60();
  await page.waitForTimeout(300);
  solta90();
  await page.waitForFunction(() => document.querySelectorAll("#w-previsao .marcos li").length === 2);
  await page.waitForTimeout(300);
  const r = [await page.evaluate(() => window.__marcos), await page.locator("#w-previsao [aria-checked=true]").textContent()];
  await ctx.close();
  assert.deepEqual([...new Set(r[0])], [2]);
  assert.equal(r[1], "90 dias");
});

// `valido_ate` da fixture `pendencia`: 12h05, calculada às 12h00.
async function comRelogio(corpo) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: "reduce", timezoneId: "America/Sao_Paulo" });
  await servir(ctx);
  await ctx.route(ROTA, (r) => r.fulfill({ json: corpo }));
  const page = await ctx.newPage();
  await page.clock.install({ time: new Date("2026-10-06T15:00:00Z") });
  const pedidos = [];
  page.on("request", (q) => { if (new URL(q.url()).pathname === "/api/v2/previsao") pedidos.push(q.url()); });
  await page.goto(`${PAINEL}#/previsao`);
  await pronta(page);
  return { ctx, page, pedidos };
}
// De segundo em segundo, com a resposta chegando entre os passos: um laço curto aparece aqui
// (no runFor de uma vez o TanStack junta os disparos num pedido só).
const andarDevagar = async (page, segundos) => { for (let i = 0; i < segundos; i++) { await page.clock.runFor(1000); await page.waitForTimeout(20); } };

test("valido_ate: +2 min nada; na fronteira (+5 min) exatamente 1 releitura, sem laço", async () => {
  const { ctx, page, pedidos } = await comRelogio(P.pendencia);
  const n0 = pedidos.length;
  await andarDevagar(page, 120);
  const n2 = pedidos.length;
  await andarDevagar(page, 181);
  const n5 = pedidos.length;
  await ctx.close();
  assert.deepEqual([n0, n2 - n0, n5 - n0], [1, 0, 1]);
});

for (const [nome, valido_ate] of [["null: sem reconsulta programada", null], ["que não é data: sem laço (NaN não vira intervalo de 0 ms)", "x"]]) {
  test(`valido_ate ${nome}, 3 min sem pedido novo`, async () => {
    const { ctx, page, pedidos } = await comRelogio({ ...P.pendencia, valido_ate });
    await andarDevagar(page, 180);
    const n = pedidos.length;
    await ctx.close();
    assert.equal(n, 1);
  });
}

test("valido_ate meio segundo depois do cálculo: o piso de 60 s dá 1 releitura por minuto, nem laço nem silêncio", async () => {
  const { ctx, page, pedidos } = await comRelogio({ ...P.pendencia, valido_ate: "2026-10-06T12:00:00.500000-03:00" });
  await andarDevagar(page, 122);
  const n = pedidos.length;
  await ctx.close();
  assert.equal(n, 3);
});

test("SSE: um aviso relê a previsão com o mesmo horizonte", async () => {
  const { ctx, page, ir, pedidos } = await abrir();
  const [segura, solta] = portao();
  await ctx.route("**/api/v2/eventos", async (r) => {
    await segura;
    r.fulfill({ status: 200, headers: { "content-type": "text/event-stream" }, body: 'retry: 600000\ndata: {"recurso":"tudo"}\n\n' }).catch(() => {});
  });
  await ir("/previsao");
  await pronta(page);
  await page.getByRole("radio", { name: "60 dias" }).click();
  await page.locator("#w-previsao .marcos li").first().waitFor();
  const antes = pedidos.length;
  solta();
  for (let t = 0; t < 80 && pedidos.length === antes; t++) await page.waitForTimeout(50);
  await page.waitForTimeout(300);
  const depois = pedidos.slice(antes);
  await ctx.close();
  assert.ok(depois.length >= 1);
  assert.deepEqual([...new Set(depois)], ["?dias=60"]);
});

test("downgrade Pro → Plus com 90 dias: aviso, e nenhum gráfico nem compromisso entre o 403 e o Plus", async () => {
  const { ctx, page, ir } = await abrir();
  await ir("/previsao");
  await pronta(page);
  await page.getByRole("radio", { name: "90 dias" }).click();
  await page.waitForFunction(() => document.querySelectorAll("#w-previsao .marcos li").length === 2);
  const [seguraPlus, soltaPlus] = portao();
  await ctx.route(ROTA, async (r) => {
    const dias = new URL(r.request().url()).searchParams.get("dias");
    if (dias === "60" || dias === "90") return erro("403_forecast_horizon_not_allowed")(r);
    await seguraPlus;
    return atender(r, P.plus30);
  });
  await page.evaluate(() => {
    window.__flash = [];
    const olhar = () => {
      const m = document.querySelector("main");
      if (!m?.innerText.includes("Esse horizonte não está no seu plano.")) return;
      window.__flash.push([m.querySelectorAll("svg[role=img]").length, m.innerText.includes("Aluguel"), m.querySelectorAll("[role=radio]").length]);
    };
    new MutationObserver(olhar).observe(document.body, { subtree: true, childList: true, characterData: true });
  });
  await relida(page);
  await page.getByText("Esse horizonte não está no seu plano.").waitFor();
  await page.waitForTimeout(200);
  soltaPlus();
  await page.locator("#w-previsao .convite").waitFor();
  const r = [await page.evaluate(() => window.__flash), await page.locator("#w-previsao .w-aside").textContent()];
  await ctx.close();
  assert.ok(r[0].length > 0, "o observador não viu o aviso");
  assert.deepEqual([...new Set(r[0].map(String))], ["0,false,0"]);
  assert.equal(r[1], "30 dias");
});

test("sessão: /previsao 401 é mensagem sem número; /me 401 com o SSE fechado tira o painel inteiro", async () => {
  const { ctx, page, ir } = await abrir();
  const [seguraSse, fechaSse] = portao();
  await ctx.route("**/api/v2/eventos", async (r) => { await seguraSse; return erro("401")(r).catch(() => {}); });
  await ctx.route("**/auth/refresh", (r) => r.fulfill({ status: 401, json: { detail: "x" } }));
  await ctx.route(ROTA, erro("401"));
  await ir("/previsao");
  await page.getByText("Sua sessão terminou.").waitFor();
  const sem = await semNumero(page);
  // A sessão cai de vez: o stream recusa, o portão relê o /me (401) e desmonta o painel.
  await ctx.route("**/api/v2/me", erro("401"));
  fechaSse();
  await page.getByText("Não deu para carregar o painel").waitFor();
  const r = [await page.evaluate(() => document.body.innerText.includes("R$")), await page.locator("#w-previsao").count()];
  await ctx.close();
  assert.deepEqual(sem, { rs: false, svg: 0, lista: false });
  assert.deepEqual(r, [false, 0]);
});
