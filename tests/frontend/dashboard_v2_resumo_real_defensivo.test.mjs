/**
 * /painel, Etapa 1 PR C, os cantos que o Tester provou (irmão de dashboard_v2_resumo_real.test.mjs):
 *
 *   · /piggy sugere as perguntas do perfil do SERVIDOR (localStorage limpo); o protótipo, o do navegador;
 *   · saldo que não é texto decimal finito ("", "NaN", "Infinity", "1e999") é "—", nunca R$ 0,00;
 *     moeda fora do ISO (null, "", "brl", "TOOLONG") sai só o número, sem a palavra "null";
 *   · a nota e o botão contam a lista que abre, não o `fora_do_total` declarado;
 *   · com um PUT de perfil em voo o seletor fica aria-disabled e a 2ª escolha não sai;
 *   · o "hoje é" do Resumo usa o fuso de SP, o mesmo do mês (aparelho em Honolulu).
 *
 * Rodar:  npm run test:frontend   (mudou webapp/src, rode `npm --prefix webapp run build`)
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { RESPOSTAS, abrirPainel, exigeArtefatoEmDia } from "./_painel.mjs";
import { BY_PROFILE, COMMON } from "../../webapp/src/dashboard/lib/prompts.js";

let browser;
before(async () => { exigeArtefatoEmDia(); browser = await chromium.launch(); });
after(() => browser?.close());

const abrir = (opts) => abrirPainel(browser, opts);
const sugestoes = (p) => [...(BY_PROFILE[p] ?? []), ...COMMON].filter((x) => x.ask && x.topic).slice(0, 5).map((x) => x.ask);
const chips = (page) => page.locator(".chat-empty .chip").allTextContents();

// --- 1. o perfil da conversa ---------------------------------------------------------

test("/piggy: as sugestões são as do perfil do servidor, com o localStorage limpo, nos 5 perfis", async () => {
  const vistos = {};
  for (const perfil of ["economizar", "investir", "controlar", "dividas", "autonomo"]) {
    const { ctx, page, ir } = await abrir({ perfil, espera: ".chat-empty li:first-child .chip" });
    await ir("/piggy");
    vistos[perfil] = [await chips(page), await page.evaluate(() => localStorage.length)];
    await ctx.close();
  }
  for (const [perfil, [lista, guardados]] of Object.entries(vistos)) assert.deepEqual([lista, guardados], [sugestoes(perfil), 0], perfil);
  assert.ok(vistos.investir[0].includes("Minha carteira está rendendo bem comparada ao CDI?"));
});

test("positivo: no protótipo as sugestões continuam vindo do perfil salvo no navegador", async () => {
  const { ctx, page, ir } = await abrir({ demo: true, espera: ".chat-empty li:first-child .chip" });
  await ctx.addInitScript(() => localStorage.setItem("pigbank.dashboard.profile.v1", '"investir"'));
  await ir("/piggy");
  const r = await chips(page);
  await ctx.close();
  assert.deepEqual(r, sugestoes("investir"));
});

// --- 2 e 3. entradas ruins no bloco de contas -----------------------------------------

const base = RESPOSTAS.contas.todos_os_estados;
const fora = (id, saldo, moeda) => ({ ...base.contas[4], id, instituicao: `C${id}`, nome: null, saldo, moeda, motivos: [] });
const RUINS = {
  ...base,
  total: "NaN",
  contas: [base.contas[0], fora(11, "", "BRL"), fora(12, "NaN", "BRL"), fora(13, "Infinity", "BRL"), fora(14, "1e999", "BRL"),
    fora(15, "30.00", null), fora(16, "30.00", ""), fora(17, "30.00", "XXX"), fora(18, "30.00", "brl"), fora(19, "30.00", "TOOLONG"),
    fora(20, "-5.5", "BRL"), fora(21, "1E+2", "USD")],
};

test("saldo e moeda ruins: \"—\" no lugar de R$ 0,00, NaN e ∞; sem a palavra null; sem erro de página", async () => {
  const { ctx, page, erros, ir } = await abrir();
  await ctx.route("**/api/v2/contas", (r) => r.fulfill({ json: RUINS }));
  await ir();
  await page.locator("#w-contas .contas-mais").click();
  const linhas = await page.locator("#w-contas ul.contas").nth(1).locator(".conta").evaluateAll((ls) => ls.map((l) => l.innerText.replace(/\n+/g, " | ")));
  const total = await page.locator("#w-contas .stat-value").textContent();
  const texto = await page.locator("#w-contas").innerText();
  await ctx.close();
  assert.equal(total, "—");
  assert.deepEqual(linhas, ["C11 | —", "C12 | —", "C13 | —", "C14 | —", "C15 | 30,00", "C16 | 30,00", "C17 | XXX 30,00", "C18 | 30,00", "C19 | 30,00",
    "C20 | −R$ 5,50", "C21 | US$ 100,00"]); // os dois últimos: o decimal válido continua formatado
  for (const ruim of ["R$ 0,00", "null", "NaN", "∞", "TOOLONG"]) assert.ok(!texto.includes(ruim), `${ruim} em: ${texto}`);
  assert.deepEqual(erros, []);
});

for (const [declarado, esperado] of [[5, 1], [0, 1]]) {
  test(`fora_do_total ${declarado} com ${esperado} conta fora: a nota e o botão dizem o que a lista abre`, async () => {
    const { ctx, page, ir } = await abrir();
    await ctx.route("**/api/v2/contas", (r) => r.fulfill({ json: { ...base, fora_do_total: declarado, contas: [...base.contas.slice(0, 4), base.contas[4]] } }));
    await ir();
    const botao = page.locator("#w-contas .contas-mais");
    await botao.click();
    const r = [await page.locator("#w-contas .w-lede").textContent(), await botao.textContent(), await page.locator("#w-contas ul.contas").nth(1).locator(".conta").count()];
    await ctx.close();
    assert.deepEqual(r, ["Saldo de hoje · carteira a confirmar · 1 conta fora do total", "ver contas fora do total (1)", esperado]);
  });
}

// --- 4. duas trocas de perfil seguidas -----------------------------------------------

// O PUT fica preso até `solta(status)`: 200 segue para o mock do _painel.mjs (que grava), o resto é erro.
async function putPreso(ctx) {
  const puts = [];
  let solta;
  await ctx.route("**/api/v2/perfil", async (r) => {
    if (r.request().method() !== "PUT") return r.fallback();
    puts.push(r.request().postDataJSON().perfil);
    const status = await new Promise((ok) => { solta = ok; });
    return status === 200 ? r.fallback() : r.fulfill({ status, json: RESPOSTAS.erros["500"].body });
  });
  return { puts, solta: (s = 200) => solta(s) };
}
const estado = (page) => page.locator("#board-profile").evaluate((s) => [s.value, s.getAttribute("aria-disabled"), s.getAttribute("aria-busy"), document.activeElement === s]);

test("PUT de perfil em voo: seletor aria-disabled com o foco nele; a 2ª escolha não sai; ao terminar, volta", async () => {
  const { ctx, page, ir } = await abrir({ width: 390 });
  const { puts, solta } = await putPreso(ctx);
  await ir();
  const altura = () => page.locator("#board-profile").evaluate((s) => Math.round(s.getBoundingClientRect().height));
  const livre = await altura();
  await page.selectOption("#board-profile", "investir");
  await page.waitForFunction(() => document.querySelector("#board-profile").getAttribute("aria-disabled") === "true");
  const preso = await estado(page);
  const alvo = [livre, await altura()];
  // O Playwright recusa escolher em select aria-disabled: a troca vai como o navegador a entrega.
  await page.locator("#board-profile").evaluate((s) => { s.value = "economizar"; s.dispatchEvent(new Event("change", { bubbles: true })); });
  await page.waitForTimeout(200);
  const segunda = [[...puts], await page.locator("#board-profile").inputValue()];
  solta();
  await page.waitForFunction(() => !document.querySelector("#board-profile").hasAttribute("aria-disabled"));
  const depois = await estado(page);
  await page.selectOption("#board-profile", "economizar");
  await page.waitForFunction(() => document.querySelector("#board-profile").getAttribute("aria-disabled") === "true");
  const terceira = [...puts];
  solta();
  await ctx.close();
  assert.deepEqual(preso, ["investir", "true", "true", true]);
  assert.deepEqual(alvo, [32, 32]); // ocupado não muda o tamanho (o seletor tem 32px desde a main: .field)
  assert.deepEqual(segunda, [["investir"], "investir"]);
  assert.deepEqual(depois, ["investir", null, null, true]);
  assert.deepEqual(terceira, ["investir", "economizar"]);
});

test("duas trocas de perfil no MESMO tick: só um PUT sai (a trava não pode esperar o React)", async () => {
  const { ctx, page, ir } = await abrir();
  const { puts, solta } = await putPreso(ctx);
  await ir();
  // As duas trocas saem dentro da mesma tarefa: o `isPending` do TanStack só chega ao React depois.
  await page.locator("#board-profile").evaluate((s) => {
    for (const v of ["investir", "economizar"]) { s.value = v; s.dispatchEvent(new Event("change", { bubbles: true })); }
  });
  await page.waitForTimeout(300);
  const emVoo = [...puts];
  solta();
  await page.waitForFunction(() => !document.querySelector("#board-profile").hasAttribute("aria-disabled"));
  await ctx.close();
  assert.deepEqual(emVoo, ["investir"]);
});

test("PUT de perfil em voo que falha: desfaz, avisa e o seletor volta a aceitar", async () => {
  const { ctx, page, ir } = await abrir();
  const { solta } = await putPreso(ctx);
  await ir();
  await page.selectOption("#board-profile", "investir");
  await page.waitForFunction(() => document.querySelector("#board-profile").getAttribute("aria-disabled") === "true");
  solta(500);
  await page.locator(".board-aviso", { hasText: "Não foi possível salvar agora" }).waitFor();
  await page.waitForFunction(() => !document.querySelector("#board-profile").hasAttribute("aria-disabled"));
  const r = await estado(page);
  await ctx.close();
  assert.deepEqual(r, ["padrao", null, null, true]);
});

// --- 5. o "hoje é" no fuso do app ----------------------------------------------------

// Honolulu está 7h atrás de SP: 03:30 UTC de 1º/10 já é outubro em SP e ainda 30/09 no aparelho.
for (const [agora, dia, mes] of [["2026-10-01T03:30:00Z", "1 de outubro de 2026", "Outubro 2026"], ["2026-10-01T02:30:00Z", "30 de setembro de 2026", "Setembro 2026"]]) {
  test(`aparelho em Honolulu, ${agora}: "hoje é ${dia}", no mesmo fuso do mês (${mes})`, async () => {
    const { ctx, page, ir } = await abrir({ agora, tz: "Pacific/Honolulu" });
    await ir();
    const r = [await page.locator(".board-hint").textContent(), await page.locator(".month-title").textContent()];
    await ctx.close();
    assert.deepEqual(r, [`hoje é ${dia}`, mes]);
  });
}

// --- 7. a etiqueta neutra que sobrou -------------------------------------------------

test('a etiqueta .tag-demo ("Mês fechado" do Hero, categoria do Calendar) segue estilizada depois do selo ganhar classe própria', async () => {
  const { ctx, page, erros, ir } = await abrir({});
  await ir("/");
  const estilo = await page.evaluate(() => {
    const el = document.createElement("span");
    el.className = "tag-demo";
    el.textContent = "Mês fechado";
    document.body.append(el);
    const c = getComputedStyle(el);
    return { altura: c.height, fundo: c.backgroundColor, fonte: c.fontSize, raio: c.borderRadius };
  });
  await ctx.close();
  assert.equal(estilo.altura, "22px");
  assert.equal(estilo.fonte, "11px");
  assert.equal(estilo.raio, "6px");
  assert.notEqual(estilo.fundo, "rgba(0, 0, 0, 0)", "sem fundo: a regra sumiu");
  assert.deepEqual(erros, []);
});
