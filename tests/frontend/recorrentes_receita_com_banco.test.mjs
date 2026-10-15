/**
 * R2: a Visão geral dos Recorrentes de quem tem banco conectado não fala de receita.
 *
 * O GET `/recurring-incomes` devolve `banco_conectado: true` (e a lista vazia) para quem tem
 * banco; sem banco, `false`. Com `true`: sem "Entradas previstas", sem "Resultado", sem a
 * linha "Receitas fixas" do Resumo, sem o alerta de equilíbrio, e a aba "Receitas fixas" some.
 * A aba nasce ESCONDIDA no `dashboard.html` e só aparece com `banco_conectado: false`.
 *
 * Roda a página REAL (`dashboard.html` sem os scripts, o CSS e o `dashboard.js` de verdade) em
 * 1280x800 e em 390x844, com `fetch` simulado.
 *
 * CONTROLE NEGATIVO (medido): tire o `comBanco` de `loadRecurringOverview` (ou volte a aba para
 * visível no HTML) e os casos "com banco" caem. CONTROLE POSITIVO: os casos "sem banco" e o de
 * "GET falhou" seguem verdes num código que escondesse tudo sempre.
 *
 * Fora do alcance: WebView do app (só depois do deploy) e o servidor (testes de Python).
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { chromium } from "playwright";

const FRONTEND = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "frontend");
const HTML = readFileSync(join(FRONTEND, "dashboard.html"), "utf-8")
  .replace(/<script\b[\s\S]*?<\/script>/g, "")
  .replace(/<link\b[^>]*>/g, "");
const CSS = ["brand.css", "dashboard.css"].map((f) => readFileSync(join(FRONTEND, f), "utf-8")).join("\n");
const CSS_MOBILE = readFileSync(join(FRONTEND, "dashboard-mobile.css"), "utf-8");
const VIEWPORTS = [["desktop", { width: 1280, height: 800 }], ["mobile", { width: 390, height: 844 }]];

let browser;
before(async () => { browser = await chromium.launch(); });
after(async () => { await browser?.close(); });

const NETFLIX = [{ id: 1, name: "Netflix", amount: 40, due_day: 5, is_active: true,
  payment_mode: "autopay", frequency: "monthly", start_date: "2026-01-01" }];

/** `incomes`: o corpo do GET de receitas (ou null = o GET falhou); `gastos`: os gastos fixos simulados. */
async function abre(viewport, incomes, gastos = NETFLIX) {
  const page = await browser.newPage({ viewport });
  const errs = [];
  page.on("pageerror", (e) => errs.push(String(e)));
  await page.setContent(HTML);
  await page.addStyleTag({ content: CSS });
  await page.addStyleTag({ content: `@media (max-width: 900px) {\n${CSS_MOBILE}\n}` });
  await page.evaluate(([inc, gastos]) => {
    window.WebSocket = class { constructor() {} close() {} send() {} addEventListener() {} };
    const resp = (body, ok = true) => ({ ok, status: ok ? 200 : 500, json: async () => body, text: async () => "{}" });
    window.fetch = async (url) => {
      url = String(url);
      if (url.includes("/recurring-incomes/")) return inc === null ? resp({}, false) : resp(inc);
      if (url.includes("/recurring-expenses/")) {
        return resp({ recurring: gastos });
      }
      if (url.includes("/recurring-bills/")) return resp({ bills: [] });
      return resp({});
    };
  }, [incomes, gastos]);
  await page.addScriptTag({ path: join(FRONTEND, "dashboard.js") });
  return { page, errs };
}

const aba = (page) => page.evaluate(() => {
  const b = document.querySelector('#recurring-tabs [data-rectab="incomes"]');
  return b ? getComputedStyle(b).display : "AUSENTE";
});

async function mostraVisaoGeral(page) {
  await page.evaluate(() => navigateTo("fixed"));
  await page.waitForFunction(() => /Saídas previstas/.test(
    document.getElementById("recurring-overview-cards")?.textContent || ""));
  return page.evaluate(() => ({
    texto: document.getElementById("recurring-overview-cards").textContent.replace(/\s+/g, " "),
    rolagem: document.documentElement.scrollWidth - window.innerWidth,
    visivel: document.getElementById("recurring-overview-cards").getBoundingClientRect().width > 0,
  }));
}

for (const [nome, vp] of VIEWPORTS) {
  test(`${nome}: com banco conectado a Visão geral não tem entradas, resultado nem a aba`, async () => {
    const { page, errs } = await abre(vp, { ok: true, incomes: [], banco_conectado: true });
    assert.equal(await aba(page), "none", "a aba nasce escondida no HTML");
    const v = await mostraVisaoGeral(page);
    assert.ok(v.visivel, "premissa: a Visão geral está na tela");
    for (const proibido of ["Entradas previstas", "Resultado previsto", "Resultado do mês", "Receitas fixas",
      "o que entra", "entradas cobrem", "Sobra", "maiores que entradas"]) {
      assert.ok(!v.texto.includes(proibido), `"${proibido}" não pode aparecer com banco: ${v.texto}`);
    }
    assert.ok(v.texto.includes("Saídas previstas") && v.texto.includes("o que sai e o que vence primeiro"));
    assert.ok(v.texto.includes("Gastos fixos") && v.texto.includes("Boletos / contas"), v.texto);
    assert.equal(await aba(page), "none", "a aba segue escondida depois do GET");
    assert.ok(v.rolagem <= 1, `rolagem horizontal de ${v.rolagem}px em ${nome}`);
    assert.deepEqual(errs, []);
    await page.close();
  });

  test(`${nome}: sem banco tudo segue como antes e a aba aparece (controle positivo)`, async () => {
    const { page, errs } = await abre(vp, { ok: true, incomes: [], banco_conectado: false });
    assert.equal(await aba(page), "none", "a aba nasce escondida mesmo para quem não tem banco");
    const v = await mostraVisaoGeral(page);
    for (const esperado of ["Entradas previstas", "Saídas previstas", "Resultado previsto", "Resultado do mês",
      "Receitas fixas", "o que entra, o que sai e o que vence primeiro"]) {
      assert.ok(v.texto.includes(esperado), `"${esperado}" some sem banco: ${v.texto}`);
    }
    assert.notEqual(await aba(page), "none", "sem banco a aba Receitas fixas aparece");
    assert.ok(v.rolagem <= 1, `rolagem horizontal de ${v.rolagem}px em ${nome}`);
    assert.deepEqual(errs, []);
    await page.close();
  });
}

// Entradas e saídas zero: a "Próxima ação" muda de texto conforme o banco (o ramo `acaoMsg`).
for (const [banco, esperado, proibido] of [
  [true, "Cadastre seus gastos fixos pra ver a previsão do mês.", "receitas"],
  [false, "Cadastre suas receitas e gastos fixos pra ver a previsão do mês.", null],
]) {
  test(`sem nenhum gasto fixo ${banco ? "com" : "sem"} banco a próxima ação ${banco ? "não fala" : "fala"} de receita`, async () => {
    const { page, errs } = await abre(VIEWPORTS[0][1], { ok: true, incomes: [], banco_conectado: banco }, []);
    const v = await mostraVisaoGeral(page);
    assert.ok(v.texto.includes(esperado), v.texto);
    if (proibido) assert.ok(!v.texto.includes(proibido), `"${proibido}" não pode aparecer: ${v.texto}`);
    assert.deepEqual(errs, []);
    await page.close();
  });
}

test("o GET de receitas falhou: a aba segue escondida e a tela não quebra", async () => {
  const { page, errs } = await abre(VIEWPORTS[0][1], null);
  const v = await mostraVisaoGeral(page);
  assert.equal(await aba(page), "none");
  assert.ok(v.texto.includes("Saídas previstas"));
  assert.deepEqual(errs, []);
  await page.close();
});

// O banco conecta com a aba "Receitas fixas" aberta: o puxão (`loadRecurringIncomeView` em
// background) e a revalidação do cache (voltar à aba) recebem `banco_conectado: true`. Sem a
// guarda, a aba e o "+ Nova receita fixa" ficavam na tela e todo salvar caía no 400 da recusa.
// CONTROLE NEGATIVO (medido): tire o `_receitasViraramBanco` de `loadRecurringIncomeView` e os
// dois casos caem. CONTROLE POSITIVO: o caso "sem banco" acima mostra a aba.
for (const caminho of ["puxão", "revalidação do cache"]) {
  test(`banco conecta com a aba Receitas fixas aberta (${caminho}): volta à Visão geral e a aba some`, async () => {
    const { page, errs } = await abre(VIEWPORTS[1][1], { ok: true, incomes: [], banco_conectado: false });
    await mostraVisaoGeral(page);
    await page.evaluate(() => { USER_ID = 7; csrfHeaders = () => ({}); setRecurringTab("incomes"); });  // sessão falsa: about:blank não lê cookie
    await page.waitForFunction(() => _recurringIncomeCache !== null);
    assert.notEqual(await aba(page), "none", "premissa: sem banco a aba aparece");
    await page.evaluate(() => {
      const anterior = window.fetch;
      window.fetch = async (url) => String(url).includes("/recurring-incomes/")
        ? { ok: true, status: 200, json: async () => ({ ok: true, incomes: [], banco_conectado: true }) }
        : anterior(url);
    });
    if (caminho === "puxão") await page.evaluate(() => loadRecurringIncomeView(true, { background: true }));
    else await page.evaluate(() => setRecurringTab("incomes"));
    await page.waitForFunction(() => _recurringTab === "overview");
    const estado = await page.evaluate(() => ({
      receitas: getComputedStyle(document.getElementById("recurring-incomes-pane")).display,
      visao: getComputedStyle(document.getElementById("recurring-overview-pane")).display,
      cache: _recurringIncomeCache,
    }));
    assert.equal(estado.receitas, "none");
    assert.notEqual(estado.visao, "none");
    assert.equal(estado.cache, null);
    assert.equal(await aba(page), "none");
    assert.deepEqual(errs, []);
    await page.close();
  });
}
