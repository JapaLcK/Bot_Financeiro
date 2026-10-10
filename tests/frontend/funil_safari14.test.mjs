/**
 * `/admin/funil` no Safari 14: o painel e os cartões das fontes montam sem as APIs que o
 * Safari 14 não tem (`Object.hasOwn`, `.at`, `structuredClone`...). Mesmo padrão do gate do
 * v2 (`dashboard_v2_safari14.test.mjs`: `addInitScript` apagando as APIs; a lista de APIs
 * abaixo é a dele, copiada porque lá é inline) e a varredura estática do JS da página.
 *
 * Rodar:  npm run test:frontend
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { startServer } from "./_server.mjs";
import { chromium } from "playwright";
import { FRONTEND } from "./_painel.mjs";

let ORIGIN, server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer());
                     browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

// Origem: tests/frontend/dashboard_v2_safari14.test.mjs (TOKENS).
const TOKENS = {
  "Array.prototype.at": /(?<!\.)\.at\(/, structuredClone: /\bstructuredClone\b/, findLast: /\bfindLast/,
  "Object.hasOwn": /\bObject\.hasOwn\(/, toSorted: /\btoSorted\b/, toReversed: /\btoReversed\b/,
  toSpliced: /\btoSpliced\b/, "Object.groupBy": /\bObject\.groupBy\b/, withResolvers: /\bwithResolvers\b/,
  "AbortSignal.timeout": /\bAbortSignal\.timeout\b/, "AbortSignal.any": /\bAbortSignal\.any\b/,
  requestIdleCallback: /\brequestIdleCallback\b/, randomUUID: /\brandomUUID\b/, WeakRef: /\bWeakRef\b/,
  "static{": /\bstatic\s*\{/, "this.#": /this\.#/,
};

test("frontend/funil.html não usa API que o Safari 14 não tem", () => {
  const js = readFileSync(join(FRONTEND, "funil.html"), "utf8");
  assert.deepEqual(Object.keys(TOKENS).filter((k) => TOKENS[k].test(js)), []);
});

const cobr = (a, r, rec) => ({ aprovadas: a, recusadas: r, receita_liquida: rec, motivos_recusa: [{ codigo: "card_declined", n: 1 }] });
const DADOS = {
  assinaturas: { ativas: 3, em_trial: 2, em_atraso: 1, canceladas: 9, outras: 4 },
  mrr: 59.7, ticket_medio: 19.9, mrr_trial_potencial: 39.8,
  cobrancas: { "7d": cobr(5, 1, 149.5), "30d": cobr(20, 7, 1234.56), truncado: false },
};
const janelaFunil = (d) => ({
  dias: d, inicio: "2026-09-01T00:00:00+00:00", viram_precos_medido: true, emails_verificacao: 1,
  etapas: ["cadastros", "viram_precos", "abriram_checkout", "concluiram"].map((id) => ({ id, n: 1, taxa_etapa: 1, taxa_acum: 1 })),
  abandono: 0, expiraram_sem_concluir: 0,
  estado_atual: { free: 1, trial: 0, paying: 0, past_due: 0, canceled: 0, granted: 0 },
  canais: [], origens: [],
  checkout: { pessoas: 1, sessoes_abertas: 1, sessoes_concluidas: 1, sessoes_expiradas: 0, conversao: 1 },
  ativacao: { concluiram: 1, onboarding: 1, whatsapp: 1, lancamento: 1 },
  trial: { iniciaram: 0, em_trial: 0, pagando: 0, cancelaram: 0, outros: 0 },
  pix: { gerados: 0, pagos: 0, expirados: 0, cancelados: 0, abertos: 0, taxa_pago: null },
  ebook: { entregas: 0, enviados: 0, nao_comprou: 0, estornados: 0, pendentes: 0 },
});
const FUNIL = { gerado_em: "2026-10-08T12:00:00+00:00", viewed_pricing_desde: "2026-09-01T00:00:00+00:00",
  janelas: { "7d": janelaFunil(7), "30d": janelaFunil(30) }, atraso: { total: 0, alem_carencia: 0, carencia_dias: 3 },
  links: [{ painel: "Afiliados", url: "/admin", olhar: "x" }] };
const OCUP = "Já há uma consulta em andamento; tente em instantes.";
const env = (extra) => ({ fonte: "stripe", estado: "ok", mensagem: null, falta: null, buscado_em: "2026-10-08T15:30:00+00:00",
  janela: { rotulo: "Assinaturas agora; cobranças dos últimos 7 e 30 dias", fuso: "UTC" }, dados: DADOS, ...extra });
// Cartão GA4 (8b): mesmo estado do caso do Stripe, com o corpo e a env que faltam da fonte dele.
const eventos = (k) => Object.fromEntries(["page_view", "view_item_list", "begin_checkout", "sign_up", "start_trial",
  "onboarding_complete", "vsl_play", "vsl_progress", "purchase"].map((e, i) => [e, { eventos: 100 * k + i, usuarios: 10 * k + i }]));
const DADOS_GA4 = { eventos: { "7d": eventos(1), "30d": eventos(2) },
  origens: { "7d": [{ canal: "Direct", sessoes: 5, usuarios: 4 }], "30d": [{ canal: "Organic Search", sessoes: 50, usuarios: 40 }] } };
const ga4 = (e) => ({ ...e, fonte: "ga4", janela: { rotulo: "GA4", fuso: "o da propriedade GA4" },
  dados: e.dados ? DADOS_GA4 : null, falta: e.falta ? ["GA4_SERVICE_ACCOUNT_JSON"] : null });
const json = (b) => ({ status: 200, contentType: "application/json", body: JSON.stringify(b) });

const CASOS = [
  ["ok", env(), "ok"],
  ["nao_configurado", env({ estado: "nao_configurado", falta: ["STRIPE_SECRET_KEY"], dados: null }), "nao_configurado"],
  ["erro", env({ estado: "erro", mensagem: "A fonte está indisponível no momento.", dados: null }), "erro"],
  ["stale", env({ estado: "stale", mensagem: "A fonte demorou demais para responder." }), "stale"],
  ["ocupada", env({ estado: "erro", mensagem: OCUP, dados: null }), "erro"],
  ["ocupada com dados", env({ estado: "stale", mensagem: OCUP }), "stale"],
  // chaves herdadas do Object NÃO são estados: a whitelist tem de usar propriedade PRÓPRIA
  ["constructor", env({ estado: "constructor" }), "erro"],
  ["__proto__", env({ estado: "__proto__" }), "erro"],
  ["toString", env({ estado: "toString" }), "erro"],
  ["hasOwnProperty", env({ estado: "hasOwnProperty" }), "erro"],
];

for (const [nome, corpo, esperado] of CASOS) {
  test(`sem as APIs do Safari 15+: painel e cartão Stripe montam (${nome})`, async () => {
    const ctx = await browser.newContext({ viewport: { width: 390, height: 800 } });
    await ctx.addInitScript(() => {
      const TA = Object.getPrototypeOf(Int8Array.prototype);
      for (const [o, k] of [[Array.prototype, "at"], [String.prototype, "at"], [TA, "at"], [Array.prototype, "findLast"],
        [Array.prototype, "findLastIndex"], [Array.prototype, "toSorted"], [Array.prototype, "toReversed"],
        [Array.prototype, "toSpliced"], [Object, "hasOwn"], [Object, "groupBy"], [Promise, "withResolvers"],
        [AbortSignal, "timeout"], [AbortSignal, "any"], [window, "structuredClone"], [window, "requestIdleCallback"],
        [window, "WeakRef"], [Crypto.prototype, "randomUUID"]]) delete o[k];
    });
    const page = await ctx.newPage();
    const erros = [];
    page.on("pageerror", (e) => erros.push(e.message));
    page.on("console", (m) => { if (m.type() === "error") erros.push(m.text()); });
    await page.route("**/admin/api/funil", (r) => r.fulfill(json(FUNIL)));
    await page.route("**/admin/api/funil/fonte/stripe", (r) => r.fulfill(json(corpo)));
    await page.route("**/admin/api/funil/fonte/ga4", (r) => r.fulfill(json(ga4(corpo))));
    await page.goto(`${ORIGIN}/funil.html`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector("#f-stripe:not([data-estado=carregando])");
    await page.waitForSelector("#f-ga4:not([data-estado=carregando])");
    const r = await page.evaluate(() => [typeof Object.hasOwn, typeof structuredClone, typeof [].at,
      document.getElementById("f-stripe").dataset.estado, document.getElementById("main").textContent,
      document.getElementById("f-ga4").dataset.estado, document.getElementById("f-ga4").textContent]);
    await ctx.close();
    assert.deepEqual(r.slice(0, 3), ["undefined", "undefined", "undefined"]);
    assert.equal(r[3], esperado);
    assert.ok(r[4].includes("1. Cadastro até o plano") && r[4].includes("8a. Stripe") && r[4].includes("9. Onde olhar o resto"));
    assert.equal(r[5], esperado);  // o cartão GA4 acompanha o estado, sem cair em erro por API ausente
    if (esperado === "ok" || esperado === "stale") {
      assert.ok(r[6].includes("8b. GA4") && r[6].includes("Organic Search") && r[6].includes("purchase"), r[6].slice(0, 120));
    }
    if (esperado === "nao_configurado") assert.ok(r[6].includes("Defina GA4_SERVICE_ACCOUNT_JSON no Railway"));
    assert.deepEqual(erros, []);
  });
}

// Exceção na renderização do cartão (foi o que o `Object.hasOwn` ausente fazia no iOS 14, fora
// do try): o cartão tem de terminar em `erro`, nunca preso em "Buscando…".
async function comFalha(preparaFalha) {
  const page = await browser.newPage({ viewport: { width: 390, height: 800 } });
  const erros = [];
  page.on("pageerror", (e) => erros.push(e.message));
  await page.addInitScript(() => {
    const replace = String.prototype.replace;
    String.prototype.replace = function (...a) { if (window.__quebra) throw new Error("esc quebrado"); return replace.apply(this, a); };
  });
  await page.route("**/admin/api/funil", (r) => r.fulfill(json(FUNIL)));
  for (const nome of ["stripe", "ga4"]) {
    await page.route(`**/admin/api/funil/fonte/${nome}`, async (r) => {
      await page.waitForSelector("#f-stripe");  // o painel já montou; agora provoca a falha
      await preparaFalha(page);
      return r.fulfill(json(nome === "ga4" ? ga4(env()) : env()));
    });
  }
  await page.goto(`${ORIGIN}/funil.html`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector("#f-stripe:not([data-estado=carregando])", { timeout: 5000 });
  await page.waitForSelector("#f-ga4:not([data-estado=carregando])", { timeout: 5000 });
  const r = await page.evaluate(() => [document.getElementById("f-stripe").dataset.estado, document.getElementById("f-stripe").textContent,
    document.getElementById("f-ga4").dataset.estado, document.getElementById("f-ga4").textContent]);
  await page.close();
  return { r, erros };
}

test("exceção na checagem do estado (hasOwnProperty quebrado) vira cartão erro, não Buscando…", async () => {
  const { r } = await comFalha((p) => p.evaluate(() => { Object.prototype.hasOwnProperty = function () { throw new TypeError("x"); }; }));
  assert.equal(r[0], "erro");
  assert.ok(r[1].includes("Não foi possível consultar esta fonte") && !r[1].includes("Buscando"));
  assert.equal(r[2], "erro");  // o cartão GA4 também: exceção na renderização nunca deixa "Buscando…"
  assert.ok(r[3].includes("Não foi possível consultar esta fonte") && !r[3].includes("Buscando"));
});

test("exceção até na moldura do cartão (esc quebrado) cai no último recurso: erro com texto fixo", async () => {
  const { r } = await comFalha((p) => p.evaluate(() => { window.__quebra = true; }));
  assert.equal(r[0], "erro");
  assert.ok(r[1].includes("8a. Stripe") && r[1].includes("Não foi possível consultar esta fonte") && !r[1].includes("Buscando"));
  assert.equal(r[2], "erro");
  assert.ok(r[3].includes("8b. GA4") && r[3].includes("Não foi possível consultar esta fonte") && !r[3].includes("Buscando"));
});
