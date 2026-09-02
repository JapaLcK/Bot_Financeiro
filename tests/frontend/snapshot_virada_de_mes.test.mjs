/**
 * Virada de mês servidor × dispositivo: o snapshot não pode ser descartado.
 *
 * A causa PRIMÁRIA foi resolvida no SERVIDOR (main 8ea113a, #215): o snapshot
 * passou a usar `now_tz()` em vez de UTC cru, o que zera a divergência para
 * quem está no fuso do app. Este grupo cobre o RESÍDUO, que continua vivo:
 * `now_tz()` é um fuso ÚNICO do servidor (TZ/REPORT_TIMEZONE, default
 * America/Sao_Paulo) e o dashboard calcula viewYear/viewMonth no relógio do
 * DISPOSITIVO. Quem está em outro fuso ainda diverge no último dia do mês —
 * 31/08 12:00 em São Paulo já é 01/09 em Tóquio (+12 h) e em Auckland (+15 h).
 * Divergiu, `isCurrentViewData` descartava e a Visão Geral ficava em skeleton.
 *
 * O conserto (cliente): no branch `snapshot` do ws.onmessage, se o usuário
 * ainda NÃO navegou de mês manualmente, o mês do snapshot É a visão corrente
 * — o servidor é a fonte da verdade do mês (decisão do 8ea113a).
 *
 * Determinístico: não depende do dia real. A divergência é sintetizada — um
 * FakeWebSocket entrega um snapshot do mês SEGUINTE ao do relógio do
 * navegador (contexto em Asia/Tokyo, o caso residual), qualquer que seja a
 * data de hoje. `render` é stubado: o teste mede a DECISÃO (adotar ×
 * descartar), não o innerHTML.
 *
 * Rodar:  npm run test:frontend
 *         (ou só este: node --test tests/frontend/snapshot_virada_de_mes.test.mjs)
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { chromium } from "playwright";

const DASHBOARD_JS = join(
  dirname(fileURLToPath(import.meta.url)), "..", "..", "frontend", "dashboard.js",
);

// Mesmo DOM mínimo do dashboard_category_escape.test.mjs (topo do arquivo
// tem getElementById sem `?.`), mais os do updateMonthLabel.
const IDS = [
  "grid", "bgt-overlay", "bgt-input", "investment-detail-overlay",
  "investment-help-overlay", "edit-launch-overlay", "launch-overlay",
  "launch-valor", "pocket-overlay", "pocket-name", "pocket-history-overlay",
  "card-overlay", "card-name", "card-closing-day", "card-due-day",
  "bill-detail-overlay", "pay-bill-overlay", "pay-bill-receipt-overlay",
  "pay-bill-amount", "overview-heading", "launches-title", "launches-wrap",
  "charts-title", "charts-grid", "alert-banner", "last-update",
  "categories-distribution", "month-label", "btn-next", "btn-prev",
];

let browser;
before(async () => { browser = await chromium.launch(); });
after(async () => { await browser?.close(); });

async function bootPage() {
  const ctx = await browser.newContext({ timezoneId: "Asia/Tokyo" });
  const page = await ctx.newPage();
  const errs = [];
  page.on("pageerror", (e) => errs.push(String(e)));
  await page.setContent(
    IDS.map((i) => (i.startsWith("btn-")
      ? `<button id="${i}"></button>` : `<div id="${i}"></div>`)).join(""),
  );
  await page.evaluate(() => {
    window.fetch = () => new Promise(() => {}); // boot IIFE congela antes do connect
    window.WebSocket = class FakeWS {
      static OPEN = 1;
      constructor() { window._ws = this; this.readyState = 1; }
      close() {} send() {}
    };
  });
  await page.addScriptTag({ path: DASHBOARD_JS });
  assert.deepEqual(errs, [], "dashboard.js não executou até o fim");
  await page.evaluate(() => {
    // A decisão sob teste é adotar × descartar — o render pesado sai da frente.
    window._rendered = [];
    window.render = (d) => { window._rendered.push({ year: d.year, month: d.month }); };
    window.stopSpin = () => {};
    window.setLaunchesLoading = () => {};
    window.setStatus = () => {}; // toca #ws-status, fora do DOM mínimo
    WS_URL = "ws://fake/ws/1";
    connect(); // registra o ws.onmessage real no FakeWS
  });
  return { ctx, page };
}

function send(page, msg) {
  return page.evaluate((m) => {
    window._ws.onmessage({ data: JSON.stringify(m) });
    return { view: [viewYear, viewMonth], rendered: window._rendered.slice() };
  }, msg);
}

// Mês seguinte ao do relógio do navegador = a divergência da virada UTC×local.
function nextMonthOf(page) {
  return page.evaluate(() => {
    const y = viewYear, m = viewMonth;
    return m === 12 ? { year: y + 1, month: 1 } : { year: y, month: m + 1 };
  });
}

test("snapshot do mês 'seguinte' é ADOTADO (antes: descartado, skeleton)", async () => {
  const { ctx, page } = await bootPage();
  const nm = await nextMonthOf(page);
  const out = await send(page, { type: "snapshot", data: { year: nm.year, month: nm.month, launches: [] } });
  assert.deepEqual(out.view, [nm.year, nm.month], "viewYear/viewMonth não adotaram o mês do snapshot");
  assert.deepEqual(out.rendered, [nm], "snapshot adotado tem que pintar (render)");
  const label = await page.evaluate(() => document.getElementById("month-label").textContent);
  assert.ok(label.includes(String(nm.year)), `month-label não atualizou: "${label}"`);
  // O teto do seletor avança junto: "próximo mês" não pode abrir um mês vazio
  // depois de adotar o mês do servidor (registro BAIXA-5 do Tester).
  const nextDisabled = await page.evaluate(() => document.getElementById("btn-next").disabled);
  assert.equal(nextDisabled, true, "btn-next habilitado para um mês além do que o servidor conhece");
  await ctx.close();
});

test("depois de navegar de mês manualmente, snapshot divergente volta a ser descartado", async () => {
  const { ctx, page } = await bootPage();
  const nm = await nextMonthOf(page);
  const before = await page.evaluate(() => [viewYear, viewMonth]);
  await page.evaluate(() => { userNavigatedMonth = true; }); // o que changeMonth() seta
  const out = await send(page, { type: "snapshot", data: { year: nm.year, month: nm.month } });
  assert.deepEqual(out.view, before, "navegação manual tem que segurar a visão");
  assert.deepEqual(out.rendered, [], "snapshot divergente pós-navegação não pinta");
  await ctx.close();
});

test("month_data divergente continua descartado (só snapshot adota)", async () => {
  const { ctx, page } = await bootPage();
  const nm = await nextMonthOf(page);
  const before = await page.evaluate(() => [viewYear, viewMonth]);
  const out = await send(page, { type: "month_data", data: { year: nm.year, month: nm.month } });
  assert.deepEqual(out.view, before);
  assert.deepEqual(out.rendered, []);
  await ctx.close();
});

test("snapshot do mês corrente segue o caminho de sempre (positivo do caminho antigo)", async () => {
  const { ctx, page } = await bootPage();
  const cur = await page.evaluate(() => ({ year: viewYear, month: viewMonth }));
  const out = await send(page, { type: "snapshot", data: { year: cur.year, month: cur.month } });
  assert.deepEqual(out.view, [cur.year, cur.month]);
  assert.deepEqual(out.rendered, [cur]);
  await ctx.close();
});
