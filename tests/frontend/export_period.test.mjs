/** PROTÓTIPO (#852): o modal de exportar dirigido no navegador, no dashboard.html real. */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { startServer } from "./_server.mjs";
import { chromium } from "playwright";

let ORIGIN, server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer()); browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

const json = (body, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(body) });

// Março de 2026 com o histórico começando no dia 15: o 1º do mês fica ANTES do histórico.
async function abrir() {
  const page = await browser.newPage();
  await page.clock.setFixedTime(new Date("2026-03-20T12:00:00-03:00"));
  const exports = [];
  await page.route("**/*", (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== ORIGIN) return route.abort();
    if (/\.[a-z0-9]+$/i.test(url.pathname)) return route.continue();
    if (url.pathname.startsWith("/export/")) { exports.push(url.pathname + url.search); return route.fulfill(json({ detail: "vazio" }, 404)); }
    return route.fulfill(json({}));
  });
  await page.route("**/auth/validate", (r) => r.fulfill(json({ user_id: 1 })));
  await page.route("**/auth/me", (r) => r.fulfill(json({ history_earliest_date: "2026-03-15" })));
  await page.route("**/auth/dashboard-profile", (r) => r.fulfill(json({ user_id: 1, plan: "pro", feature_gates: { export: true } })));
  await page.goto(`${ORIGIN}/dashboard.html`);
  await page.waitForFunction(() => typeof featureAllowed === "function" && featureAllowed("export") && historyEarliestDate, null, { timeout: 5000 });
  await page.getByRole("button", { name: "Exportar" }).click();
  return { page, exports };
}

test("Exportar abre o mês, limitado ao começo do histórico, com as duas datas obrigatórias", async () => {
  const { page } = await abrir();
  const r = await page.evaluate(() => {
    const campo = (id) => { const e = document.getElementById(id); return [e.value, e.min, e.max, e.required]; };
    return { aberto: document.getElementById("export-overlay").classList.contains("open"),
             inicio: campo("export-start-date"), fim: campo("export-end-date") };
  });
  await page.close();
  assert.deepEqual(r, { aberto: true,
    inicio: ["2026-03-15", "2026-03-15", "9999-12-30", true],
    fim: ["2026-03-31", "2026-03-15", "9999-12-30", true] });
});

test("período invertido não sai do aparelho; o válido manda o intervalo inclusivo", async () => {
  const { page, exports } = await abrir();
  const enviar = async (inicio, fim) => {
    await page.fill("#export-start-date", inicio);
    await page.fill("#export-end-date", fim);
    await page.getByRole("button", { name: "Enviar por e-mail" }).click();
    await page.waitForFunction(() => document.getElementById("export-period-error").textContent);
    return page.locator("#export-period-error").textContent();
  };
  const invertido = await enviar("2026-03-20", "2026-03-16");
  const pedidosDoInvertido = exports.length;
  const valido = await enviar("2026-03-16", "2026-03-31");
  await page.close();
  assert.deepEqual([invertido, pedidosDoInvertido], ["A data final não pode ser anterior à data inicial.", 0]);
  assert.deepEqual([valido, exports], ["Nenhum lançamento neste período para exportar.", ["/export/1?start_date=2026-03-16&end_date=2026-03-31"]]);
});
