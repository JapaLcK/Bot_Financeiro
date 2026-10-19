/** KPI "vs" das Análises do painel antigo na regra do PL-07 (`renderAnalyticsKPIs`, frontend/dashboard.js):
 *  o rótulo usa o N do servidor (`delta_meses`), o `title` repassa a `delta_escolha` escapada, o "—"
 *  sem número também leva o `title`, e a cor segue a PB-1 (o período inclui o mês aberto: queda neutra,
 *  alta de gasto âmbar, alta de receita verde). A frase e o N são do servidor: tests/test_analytics_kpi_comparacao.py. */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { abrirBrowser, fecharBrowser, loadDashboardJs } from "./_dashboard_loader.mjs";

before(abrirBrowser);
after(fecharBrowser);
const html = readFileSync(new URL("../../frontend/dashboard.html", import.meta.url), "utf8");
const K = { total_income: 600, total_expense: 300, savings_rate: 0.5, peak_day: null };

// Renderiza cada KPI e devolve [texto, title] dos dois deltas (Gasto médio e Receita média).
async function deltas(kpis, months) {
  const page = await loadDashboardJs();
  const r = await page.evaluate(({ html, kpis, months }) => {
    document.body.append(new DOMParser().parseFromString(html, "text/html").getElementById("analytics-view"));
    return kpis.map((k) => {
      renderAnalyticsKPIs(k, months);
      return [[...document.querySelectorAll("#analytics-stats .stat-delta")].slice(0, 2).map((e) => [e.textContent, e.getAttribute("title"), e.className]),
        document.querySelectorAll("#analytics-stats b").length];
    });
  }, { html, kpis, months });
  await page.close();
  return r;
}

test("o rótulo usa o N do servidor, no singular com 1, e o title traz a escolha escapada", async () => {
  const escolha = 'De 1º "x" <b>y</b>';
  // `delta_meses` (o N que o servidor usou, com o teto dele) manda no rótulo, não o seletor (6)
  const r = await deltas([
    { ...K, delta_pct: { income: -20, expense: 0 }, delta_escolha: escolha, delta_meses: 3 },
    { ...K, delta_pct: { income: 5, expense: null }, delta_escolha: escolha, delta_meses: 1 },
    { ...K },                                                     // Essencial: sem delta, sem title
  ], 6);
  assert.deepEqual(r, [
    [[["0,0% vs os 3 meses anteriores", escolha, "stat-delta "], ["-20,0% vs os 3 meses anteriores", escolha, "stat-delta cai"]], 0],
    [[["—", escolha, "stat-delta "], ["+5,0% vs o mês anterior", escolha, "stat-delta up"]], 0],
    [[["—", null, "stat-delta "], ["—", null, "stat-delta "]], 0],
  ]);
});

test("cor (PB-1): gasto subindo âmbar, caindo cinza; receita subindo verde, caindo cinza", async () => {
  const r = await deltas([
    { ...K, delta_pct: { expense: 12.5, income: -3 }, delta_escolha: "x", delta_meses: 3 },
    { ...K, delta_pct: { expense: -16.7, income: 8 }, delta_escolha: "x", delta_meses: 3 },
  ], 3);
  assert.deepEqual(r.map(([d]) => d.map((x) => x[2])), [["stat-delta alta", "stat-delta cai"], ["stat-delta cai", "stat-delta up"]]);
});

test("o \"—\" sem número (fora do histórico) também leva o title", async () => {
  // Mede o repasse da `delta_escolha` para o `title` quando não há número, não a frase (a frase é do
  // servidor, `FORA_DO_HISTORICO`).
  const frase = "frase do servidor sem número";
  const r = await deltas([{ ...K, delta_pct: { income: null, expense: null }, delta_escolha: frase, delta_meses: 12 }], 12);
  assert.deepEqual(r, [[[["—", frase, "stat-delta "], ["—", frase, "stat-delta "]], 0]]);
});
