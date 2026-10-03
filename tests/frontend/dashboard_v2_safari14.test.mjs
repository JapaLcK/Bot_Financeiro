// Dashboard v2 no Safari 14: o alvo safari14 do build só transpila sintaxe, não faz
// polyfill de API. Três provas:
//  · o model roda sem Array.prototype.at (o NetWorth chama netWorth() no carregamento do
//    módulo: se lançar, o painel não monta);
//  · o artefato commitado não chama nenhuma API que o Safari 14 não tem (varredura
//    estática; o matcher é provado contra uma amostra com todos os tokens);
//  · o /painel monta no Chromium com essas APIs apagadas, com o plano da API.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { chromium } from "playwright";
import { FRONTEND, PAINEL, exigeArtefatoEmDia, servir } from "./_painel.mjs";

const AT = Object.getOwnPropertyDescriptor(Array.prototype, "at");
delete Array.prototype.at; // antes do import: é o que o iOS 14 vê
const { HORIZONS, monthSummary, netWorth, trajectory } = await import("../../webapp/src/dashboard/lib/model.js");
const { MONTHS } = await import("../../webapp/src/dashboard/lib/data.js");

test("sem Array.prototype.at: resumo, trajetória e patrimônio calculam", () => {
  assert.equal(Array.prototype.at, undefined);
  for (const key of MONTHS) assert.equal(typeof monthSummary(key).opening, "number"); // saldo inicial do mês
  const past = trajectory(MONTHS[0], "mes"); // mês fechado
  assert.equal(past.end, past.points[past.points.length - 1]);
  for (const h of Object.keys(HORIZONS)) { // mês corrente, com previsão
    const t = trajectory(MONTHS[MONTHS.length - 1], h);
    assert.equal(t.end, t.points[t.points.length - 1]);
    assert.equal(t.end.real, false);
  }
  const rows = netWorth();
  assert.equal(rows[rows.length - 1].total, 19806.97);
});

// API ausente no Safari 14 (e sintaxe que o esbuild não rebaixa) → como aparece no bundle.
const TOKENS = {
  "Array.prototype.at": /\.at\(/, structuredClone: /\bstructuredClone\b/, findLast: /\bfindLast/,
  "Object.hasOwn": /\bObject\.hasOwn\(/, toSorted: /\btoSorted\b/, toReversed: /\btoReversed\b/,
  toSpliced: /\btoSpliced\b/, "Object.groupBy": /\bObject\.groupBy\b/, withResolvers: /\bwithResolvers\b/,
  "AbortSignal.timeout": /\bAbortSignal\.timeout\b/, "AbortSignal.any": /\bAbortSignal\.any\b/,
  requestIdleCallback: /\brequestIdleCallback\b/, randomUUID: /\brandomUUID\b/, WeakRef: /\bWeakRef\b/,
  "static{": /\bstatic\s*\{/, "this.#": /this\.#/,
};
const achados = (js) => Object.keys(TOKENS).filter((k) => TOKENS[k].test(js));

test("o matcher acha cada token numa amostra e não confunde hasOwnProperty com hasOwn", () => {
  const amostra = "a.at(-1);structuredClone(x);b.findLast(f);Object.hasOwn(o,k);c.toSorted();c.toReversed();"
    + "c.toSpliced(1);Object.groupBy(a,f);Promise.withResolvers();AbortSignal.timeout(1);AbortSignal.any([]);"
    + "requestIdleCallback(f);crypto.randomUUID();new WeakRef(o);class A{static{}};class B{#x;m(){return this.#x}}";
  assert.deepEqual(achados(amostra), Object.keys(TOKENS));
  assert.deepEqual(achados("Object.hasOwnProperty.call(Element.prototype,`animate`);Math.atan(1)"), []);
});

test("frontend/dashboard-app.js não usa API que o Safari 14 não tem", () => {
  assert.deepEqual(achados(readFileSync(join(FRONTEND, "dashboard-app.js"), "utf8")), []);
});

test("/painel sem as APIs do Safari 15+: monta, o plano da API vale, zero erro de JS", async () => {
  Object.defineProperty(Array.prototype, "at", AT); // o node do Playwright pode usá-lo; o corte vale no navegador
  exigeArtefatoEmDia();
  const browser = await chromium.launch();
  try {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: "reduce" });
    await servir(ctx, undefined, { plano: "essencial" });
    await ctx.addInitScript(() => {
      localStorage.setItem("pigbank.dashboard.profile.v1", '"padrao"');
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
    await page.goto(`${PAINEL}#/`);
    await page.locator("#board-profile").waitFor();
    const r = await page.evaluate(() => [typeof [].at, typeof structuredClone, typeof Object.hasOwn,
      [...document.querySelectorAll("[data-widget-id]")].map((w) => w.dataset.widgetId).filter((id) => ["hero", "piggy", "simulador"].includes(id))]);
    await ctx.close();
    assert.deepEqual(r, ["undefined", "undefined", "undefined", []]);
    assert.deepEqual(erros, []);
  } finally {
    await browser.close();
  }
});
