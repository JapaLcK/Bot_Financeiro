/**
 * INVARIANTE: o donut "Gastos por categoria" mostra o mês QUE ESTÁ NA TELA.
 *
 * O defeito: a guarda era `if ((d.expense_categories||[]).length) buildCatChart(...)`
 * — num mês sem despesas ela simplesmente NÃO chamava nada, e a instância do
 * Chart.js do mês anterior continuava pintada no canvas. O usuário trocava de
 * mês e via os gastos do mês passado como se fossem do mês aberto. O conserto é
 * `renderCatChart()` (dashboard.js:10069): mês vazio DESTRÓI a instância, zera
 * `chartCat` e revela `#chart-cat-empty`.
 *
 * Por que o stub de Chart registra `destroyed` em vez de contar chamadas: o
 * sintoma não é "buildCatChart rodou de menos", é "sobrou pixel do mês anterior".
 * Quem prova isso é a instância VIVA com os dados velhos — daí a asserção ser
 * sobre `chartValues` no passo vazio, que é exatamente o que o controle negativo
 * devolve ([100]) quando a guarda antiga volta.
 *
 * Os DOIS caminhos são cobertos porque são dois call sites independentes:
 * render() (:10614) e o re-render do toggle de tema (:6483), que reconstruía o
 * donut a partir de `lastData` com a mesma guarda.
 *
 * Rodar: NODE_PATH=$(npm root -g) node --test tests/frontend/cat_chart_mes_vazio.test.mjs
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { chromium } from "playwright";

const DASHBOARD_JS = join(
  dirname(fileURLToPath(import.meta.url)), "..", "..", "frontend", "dashboard.js",
);

// Mesma lista do expense_chart_dedup (o render() toca todos), mais o par
// canvas/estado-vazio real, que é o objeto deste teste.
const IDS = [
  "grid", "bgt-overlay", "bgt-input", "investment-detail-overlay",
  "investment-help-overlay", "edit-launch-overlay", "launch-overlay",
  "launch-valor", "pocket-overlay", "pocket-name", "pocket-history-overlay",
  "card-overlay", "card-name", "card-closing-day", "card-due-day",
  "bill-detail-overlay", "pay-bill-overlay", "pay-bill-receipt-overlay",
  "pay-bill-amount", "overview-heading", "launches-title", "launches-wrap",
  "charts-title", "charts-grid", "alert-banner", "last-update",
  "categories-distribution",
];

const HTML = IDS.map((i) => `<div id="${i}"></div>`).join("")
  + `<div id="overview-view" class="active"></div>`
  + `<canvas id="chart-cat"></canvas>`
  + `<div id="chart-cat-empty" class="empty" hidden>Sem despesas este mês.</div>`;

const COM_DADOS  = [{ categoria: "mercado", total: 100 }];
const OUTROS     = [{ categoria: "transporte", total: 42 }];

let browser;
before(async () => { browser = await chromium.launch(); });
after(async () => { await browser?.close(); });

async function bootPage() {
  const page = await browser.newPage();
  const errs = [];
  page.on("pageerror", (e) => errs.push(String(e)));
  await page.setContent(HTML);
  await page.evaluate(() => { window.fetch = () => new Promise(() => {}); });
  await page.addScriptTag({ path: DASHBOARD_JS });
  assert.deepEqual(errs, [], "dashboard.js não executou até o fim");

  await page.evaluate(() => {
    window.fetch = () => new Promise(() => {});          // nada de rede
    window.loadExpenseChart = () => {};                  // fora do escopo
    window.buildExpenseChart = () => {};
    window.buildHistoryChart = () => {};
    window.renderInvestmentsPanel = () => {};            // exigem meia dúzia de ids
    window.renderLaunches = () => {};                    // alheios ao gráfico

    // Stub de Chart.js que guarda o que foi pintado e se morreu.
    window._charts = [];
    window.Chart = class {
      constructor(el, cfg) {
        this.el = el;
        this.destroyed = false;
        this.values = cfg.data.datasets[0].data;
        window._charts.push(this);
      }
      destroy() { this.destroyed = true; }
    };
  });
  return page;
}

/**
 * Roda render() com as categorias pedidas e espera o setTimeout(50) dos charts.
 * Grava `lastData` antes, como fazem os chamadores de verdade (:425/:7505) — é
 * de lá que o re-render do toggle de tema lê.
 */
async function renderCom(page, cats) {
  await page.evaluate((cats) => {
    const d = { timestamp: new Date().toISOString(), expense_categories: cats,
                recent_launches: [], alerts: [] };
    lastData = d;
    render(d);
  }, cats);
  await page.waitForTimeout(120);
}

/** O que está na tela AGORA: instância viva, valores dela e visibilidade. */
const sonda = (page) => page.evaluate(() => ({
  chartCatNulo: chartCat === null,
  chartValues: chartCat ? chartCat.values : null,
  vivas: window._charts.filter((c) => !c.destroyed).length,
  destruidas: window._charts.map((c) => c.destroyed),
  emptyVisivel: !document.getElementById("chart-cat-empty").hidden,
  canvasEscondido: document.getElementById("chart-cat").hidden,
}));

test("render(): dados → mês VAZIO → dados (o vazio não conserva o mês anterior)", async () => {
  const page = await bootPage();

  await renderCom(page, COM_DADOS);
  const p1 = await sonda(page);
  assert.deepEqual(p1.chartValues, [100], "passo 1 tinha que pintar os dados");
  assert.equal(p1.emptyVisivel, false, "com dados, o estado vazio fica escondido");
  assert.equal(p1.canvasEscondido, false);

  await renderCom(page, []);
  const p2 = await sonda(page);
  assert.equal(p2.chartValues, null,
    `mês vazio ainda mostra o donut do mês anterior: ${JSON.stringify(p2.chartValues)}`);
  assert.equal(p2.chartCatNulo, true, "chartCat tinha que voltar a null no mês vazio");
  assert.deepEqual(p2.destruidas, [true], "a instância anterior tinha que ser destruída");
  assert.equal(p2.vivas, 0, "nenhuma instância viva no mês vazio");
  assert.equal(p2.emptyVisivel, true, "#chart-cat-empty tinha que aparecer");
  assert.equal(p2.canvasEscondido, true, "o canvas fica escondido (mas permanece no DOM)");

  await renderCom(page, OUTROS);
  const p3 = await sonda(page);
  assert.deepEqual(p3.chartValues, [42], "passo 3 tinha que pintar os dados NOVOS");
  assert.equal(p3.vivas, 1, "só a instância nova viva");
  assert.equal(p3.emptyVisivel, false);
  assert.equal(p3.canvasEscondido, false, "o canvas volta a aparecer com dados");

  // O canvas nunca saiu do DOM — é ele que o mês seguinte reusa.
  assert.equal(await page.evaluate(() => !!document.getElementById("chart-cat")), true);
  await page.close();
});

test("toggle de tema no mês VAZIO não ressuscita o donut do mês anterior", async () => {
  const page = await bootPage();

  await renderCom(page, COM_DADOS);

  // O ESTADO INICIAL É O PONTO DO CASO, e errar isso o torna decorativo:
  // encadear `renderCom(page, [])` aqui já zeraria o chartCat pelo OUTRO call
  // site, e o toggle partiria de chartCat===null com o estado vazio na tela.
  // Aí a guarda antiga (pular a chamada) produz resultado IDÊNTICO ao do
  // conserto, e reverter só o :6483 fica verde — foi exatamente o que a
  // revisão mediu. O que discrimina é o toggle encontrar o donut do mês
  // anterior VIVO e o dado corrente já vazio, que é o que acontece no app
  // quando o mês vira: `lastData` é trocado e só depois o tema é alternado.
  // `lastData` é preenchido pelos CHAMADORES do render (loadData/WebSocket,
  // :425/:7505), não pelo render — daí semeá-lo aqui, que é o mesmo que o app
  // faz ao receber o mês novo.
  await page.evaluate(() => {
    lastData = { timestamp: new Date().toISOString(), expense_categories: [],
                 recent_launches: [], alerts: [] };
  });
  const antes = await sonda(page);
  assert.deepEqual(antes.chartValues, [100],
    "o toggle tem que PARTIR do donut do mês anterior vivo, senão não discrimina");
  assert.equal(antes.emptyVisivel, false, "e do estado vazio ainda escondido");

  // toggleTheme() → applyTheme() → re-render dos gráficos do overview a partir
  // de `lastData` (dashboard.js:6483), o segundo call site da mesma guarda.
  await page.evaluate(() => toggleTheme());
  await page.waitForTimeout(50);

  const pos = await sonda(page);
  assert.equal(pos.chartValues, null,
    `o toggle de tema repintou o mês anterior: ${JSON.stringify(pos.chartValues)}`);
  assert.equal(pos.chartCatNulo, true);
  assert.equal(pos.vivas, 0, "nenhuma instância viva depois do toggle no mês vazio");
  assert.equal(pos.emptyVisivel, true);

  // E com dados o toggle continua reconstruindo (caminho normal intacto).
  await renderCom(page, OUTROS);
  await page.evaluate(() => toggleTheme());
  await page.waitForTimeout(50);
  const comDados = await sonda(page);
  assert.deepEqual(comDados.chartValues, [42], "toggle com dados tinha que repintar os dados");
  assert.equal(comDados.vivas, 1);
  await page.close();
});
