/**
 * Cor de dinheiro (`_toneMoney`/`_toneClass`), estado vazio dos gráficos
 * (`_chartVazio`) e sessão expirada (`_sessaoExpirou`).
 *
 * Achado 14 do Raio-X: `x >= 0 ? verde : vermelho` espalhado pintava o ZERO de
 * verde, e decidia pelo valor BRUTO — resíduo de float saía verde embaixo de
 * "R$ 0,00". Achado 17: sem lançamento o Chart.js desenhava reta em R$ 0 com
 * eixo rotulado. Achado 21: 401 deixava painel em "Carregando…" pra sempre.
 *
 * CONTROLES NEGATIVOS deste grupo (desligue o conserto e ao menos um fica
 * vermelho):
 *   - `_toneMoney(0)` e o resíduo de float: com o ternário antigo, verde.
 *   - `desenhou`: sem a guarda, vira true nos 6 gráficos.
 *   - `canvasSobreviveu`: com o `innerHTML` direto do `_sessaoExpirou`, false.
 *
 * O QUE ESTE ARQUIVO NÃO DECIDE, e precisa de navegador com a página inteira:
 *   - se a caixa fica visualmente centrada/legível nos dois temas;
 *   - se o `grid-column:1/-1` do `.cl-box` resolve os 5 containers em grid (aqui
 *     os containers são `<div>` soltos, sem o CSS de grid de cada painel);
 *   - os outros 12 pontos de 401 (só 2 são exercidos aqui);
 *   - cor de dinheiro dentro de ternário interpolado (`color:${x ? … }`), que a
 *     varredura estática do último teste não enxerga.
 *
 * Rodar:  npm run test:frontend
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { chromium } from "playwright";

const FRONTEND = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "frontend");
const DASHBOARD_JS   = join(FRONTEND, "dashboard.js");
const DASHBOARD_CSS  = join(FRONTEND, "dashboard.css");
const DASHBOARD_HTML = readFileSync(join(FRONTEND, "dashboard.html"), "utf-8");

const IDS = [
  "grid", "bgt-overlay", "bgt-input", "investment-detail-overlay",
  "investment-help-overlay", "edit-launch-overlay", "launch-overlay",
  "launch-valor", "pocket-overlay", "pocket-name", "pocket-history-overlay",
  "card-overlay", "card-name", "card-closing-day", "card-due-day",
  "bill-detail-overlay", "pay-bill-overlay", "pay-bill-receipt-overlay",
  "pay-bill-amount", "overview-heading", "launches-title", "launches-wrap",
  "charts-title", "charts-grid", "alert-banner", "last-update",
  "categories-distribution", "history-title", "boleto-sim-result",
  "history-stats",
];

/**
 * O wrapper REAL de cada canvas, lido do `dashboard.html`. Fonte única: se
 * alguém trocar `.chart-wrap donut` por outra caixa, o teste passa a medir a
 * caixa nova — e se o canvas sumir do HTML, falha aqui em vez de medir um
 * ambiente fabricado (o defeito da primeira versão deste arquivo).
 */
function wrapperDe(id, idDoWrap) {
  const m = DASHBOARD_HTML.match(
    new RegExp(`(<div[^>]*>)\\s*<canvas id="${id}"[^>]*></canvas>`),
  );
  assert.ok(m, `canvas #${id} não achado no dashboard.html`);
  const abre = idDoWrap ? m[1].replace("<div", `<div id="${idDoWrap}"`) : m[1];
  return `${abre}<canvas id="${id}"></canvas></div>`;
}

// Os gráficos COM guarda de série vazia: [id, chamador real, dado vazio, cheio].
// `chart-history` não está aqui de propósito: `fetchHistory` devolve cedo com
// lista vazia e o backend só cria a chave de um mês que tem lançamento, então
// série zerada não chega ao `buildHistoryChart` — estado vazio ali seria código
// morto (foi removido).
const GRAFICOS = [
  ["chart-cat",     "buildCatChart(D)",        [], [{ categoria: "mercado", total: 80 }]],
  ["chart-day",     "buildExpenseChart(D, 7)", [{ date: "2026-01-01", total: 0 }], [{ date: "2026-01-01", total: 12 }]],
  ["mock-evolution-chart",      "renderAnalyticsEvolution(D)",     [{ month: "2026-01", expense: 0 }], [{ month: "2026-01", expense: 40 }]],
  ["mock-income-expense-chart", "renderAnalyticsIncomeExpense(D)", [{ month: "2026-01", income: 0, expense: 0 }], [{ month: "2026-01", income: 5, expense: 2 }]],
  ["mock-category-donut",       "renderAnalyticsCategoryDonut(D)", [], [{ name: "mercado", total: 80 }]],
  ["mock-weekday-chart",        "renderAnalyticsWeekday(D)",       [{ label: "seg", dow: 1, avg: 0 }], [{ label: "seg", dow: 1, avg: 7 }]],
];

let browser;
before(async () => { browser = await chromium.launch(); });
after(async () => { await browser?.close(); });

async function bootPage() {
  const page = await browser.newPage();
  const errs = [];
  page.on("pageerror", (e) => errs.push(String(e)));
  await page.setContent(
    IDS.map((i) => `<div id="${i}"></div>`).join("") +
    GRAFICOS.map(([id]) => wrapperDe(id)).join("") +
    wrapperDe("chart-history", "history-wrap"),
  );
  await page.addStyleTag({ path: DASHBOARD_CSS });   // geometria real, não fabricada
  await page.evaluate(() => { window.fetch = () => new Promise(() => {}); });
  await page.addScriptTag({ path: DASHBOARD_JS });
  // Chart.js dublê: conta instanciações (é o que prova que a guarda evitou
  // desenhar) e responde ao `Chart.defaults` que os renderers escrevem.
  await page.evaluate(() => {
    window._charts = [];
    window.Chart = class {
      constructor(el) { window._charts.push(el.id); }
      destroy() {}
    };
    window.Chart.defaults = { color: "", font: {} };
  });
  // `pageerror` é ASSÍNCRONO: checar logo depois do addScriptTag deixa passar o
  // erro que ainda não voltou ao Node. Quem cobra é `semErros`, no fim do teste.
  return { page, errs };
}

async function semErros(page, errs) {
  await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => setTimeout(r, 0))));
  assert.deepEqual(errs, [], "a página lançou erro");
}

test("_toneMoney: a cor concorda com o texto que o _fmtBRL escreve", async () => {
  const { page, errs } = await bootPage();
  const r = await page.evaluate(() => {
    const caso = (v) => ({ cor: _toneMoney(v), txt: _fmtBRL(v) });
    return {
      // 1500.20 + 300.10 - 1800.30 = 2.27e-13: a tela mostra zero.
      residuo:  caso(1500.20 + 300.10 - 1800.30),
      quaseNeg: caso(-0.004),          // "R$ -0,00"
      zero:     caso(0),
      // Meio-centavo: `Math.round(-0.5)` daria -0 (neutro) e o texto "R$ -0,01".
      meioNeg:  caso(-0.005),
      meioPos:  caso(0.005),
      nulo:     _toneMoney(null),
      naoNum:   _toneMoney("mil reais"),
      classeZero: _toneClass(0, "up", "down"),
      classePos:  _toneClass(3, "up", "down"),
      classeNeg:  _toneClass(-3, "up", "down"),
    };
  });
  // O invariante: cor neutra se e só se o texto não tem centavo diferente de zero.
  assert.equal(r.residuo.cor,  "var(--text-2)", `cor x texto: ${r.residuo.txt}`);
  assert.equal(r.quaseNeg.cor, "var(--text-2)", `cor x texto: ${r.quaseNeg.txt}`);
  assert.equal(r.zero.cor,     "var(--text-2)");
  assert.equal(r.meioNeg.txt,  "R$ -0,01", "premissa: toLocaleString arredonda meio pra longe do zero");
  assert.equal(r.meioNeg.cor,  "var(--red)", "meio-centavo negativo: texto -0,01 e cor tinham de concordar");
  assert.equal(r.meioPos.txt,  "R$ 0,01");
  assert.equal(r.meioPos.cor,  "var(--green)");
  assert.equal(r.nulo,   "var(--text-2)");
  assert.equal(r.naoNum, "var(--text-2)");
  assert.equal(r.classeZero, "", "zero não recebe classe de cor nenhuma");
  assert.equal(r.classePos,  "up");
  assert.equal(r.classeNeg,  "down");
  await semErros(page, errs);
  await page.close();
});

test("os gráficos caem no estado vazio pelo chamador real, e voltam com dado", async () => {
  const { page, errs } = await bootPage();
  const vazio = await page.evaluate((gs) => {
    window._charts = [];
    const out = {};
    for (const [id, chamada, dadoVazio] of gs) {
      const D = dadoVazio;   // eslint-disable-line no-unused-vars
      eval(chamada);         // eslint-disable-line no-eval
      const wrap = document.getElementById(id).parentElement;
      const box = wrap.querySelector(":scope > .chart-empty");
      out[id] = {
        temCaixa: !!box,
        temSticker: !!(box && box.querySelector("img.empty-sticker")),
        desenhou: window._charts.includes(id),
        wrapH: Math.round(wrap.getBoundingClientRect().height),
        caixaH: box ? Math.round(box.getBoundingClientRect().height) : 0,
      };
    }
    return out;
  }, GRAFICOS);

  for (const [id] of GRAFICOS) {
    const g = vazio[id];
    assert.equal(g.temCaixa, true,   `${id}: sem série, tem de cair no _clBox`);
    assert.equal(g.temSticker, true, `${id}: reusa o estado vazio com sticker do Piggy`);
    assert.equal(g.desenhou, false,  `${id}: não pode instanciar o Chart com série vazia`);
  }
  // Achado 3, e SÓ o donut mede algo aqui: os outros wraps têm altura própria no
  // CSS (.chart-wrap.line, e os divs de 240px das Análises), então a checagem
  // neles passaria sem o conserto. `.chart-wrap.donut` não tem height nenhum —
  // é ele que colapsava, e é o único caso em que este número discrimina.
  assert.ok(vazio["chart-cat"].wrapH >= 160,
    `donut: wrap colapsou no vazio (${vazio["chart-cat"].wrapH}px) — o min-height do _chartVazio não pegou`);
  assert.ok(vazio["chart-cat"].caixaH >= 160, "donut: caixa sem altura");

  const cheio = await page.evaluate((gs) => {
    window._charts = [];   // zera: senão o `desenhou` da fase vazia contaria aqui
    const out = {};
    for (const [id, chamada, , dadoCheio] of gs) {
      const D = dadoCheio;   // eslint-disable-line no-unused-vars
      eval(chamada);         // eslint-disable-line no-eval
      const wrap = document.getElementById(id).parentElement;
      out[id] = {
        temCaixa: !!wrap.querySelector(":scope > .chart-empty"),
        desenhou: window._charts.includes(id),
        display: document.getElementById(id).style.display,
        minH: wrap.style.minHeight,
      };
    }
    return out;
  }, GRAFICOS);

  for (const [id] of GRAFICOS) {
    assert.equal(cheio[id].temCaixa, false, `${id}: com dado, a caixa tem de sair`);
    assert.equal(cheio[id].desenhou, true,  `${id}: com dado, o gráfico volta`);
    assert.equal(cheio[id].display, "",     `${id}: o canvas volta pro fluxo`);
    assert.equal(cheio[id].minH, "",        `${id}: a altura forçada do vazio sai junto`);
  }
  await semErros(page, errs);
  await page.close();
});

test("401 nas cargas com canvas: caixa com ação, e o canvas SOBREVIVE", async () => {
  const { page, errs } = await bootPage();
  const r = await page.evaluate(async () => {
    window.fetch = async () => ({
      ok: false, status: 401,
      json: async () => ({}), text: async () => "",
    });
    await loadExpenseChart(7);
    await fetchHistory();
    const olha = (idCanvas, wrap) => {
      const box = wrap ? wrap.querySelector(":scope > .chart-empty") : null;
      return {
        // O defeito que isto prende: `innerHTML` na caixa matava o canvas, e o
        // gráfico nunca mais voltava nem depois de a sessão voltar.
        canvasSobreviveu: !!document.getElementById(idCanvas),
        texto: box ? box.textContent.replace(/\s+/g, " ").trim() : "",
        temBotao: !!(box && box.querySelector("[data-relogin]")),
        semOnclick: !!(box && !box.querySelector("[onclick]")),
      };
    };
    return {
      // O wrap do chart-day não tem id no HTML: pega pelo canvas, que é como o
      // `loadExpenseChart` também o alcança.
      expense: olha("chart-day", document.getElementById("chart-day").parentElement),
      history: olha("chart-history", document.getElementById("history-wrap")),
      wrapVisivel: document.getElementById("history-wrap").style.display,
    };
  });
  for (const nome of ["expense", "history"]) {
    assert.equal(r[nome].canvasSobreviveu, true, `${nome}: o canvas foi destruído pela caixa de 401`);
    assert.match(r[nome].texto, /sessão expirou/i, `${nome}: 401 tem de virar estado final com texto`);
    assert.equal(r[nome].temBotao, true, `${nome}: estado final sem AÇÃO não fecha o achado 21`);
    assert.equal(r[nome].semOnclick, true, `${nome}: handler é addEventListener, não atributo inline`);
  }
  assert.equal(r.wrapVisivel, "", "a seção do histórico precisa aparecer pra caixa ser vista");
  await semErros(page, errs);
  await page.close();
});

test("401 no histórico: quando a sessão volta, o gráfico volta", async () => {
  // O veneno e o antídoto no mesmo painel. O estado de 401 do `chart-history` é
  // SOBREPOSTO (canvas display:none + minHeight no wrap + .chart-empty), e quem
  // limpa é o `_chartVazio(el, false)` do chamador. Os outros 6 gráficos chamam
  // isso no topo do build; o `chart-history` tinha perdido o antídoto — depois
  // de um 401, a sessão voltava, o Chart era instanciado num canvas invisível e
  // o gráfico só voltava com reload.
  const { page, errs } = await bootPage();
  const r = await page.evaluate(async () => {
    const wrap = document.getElementById("history-wrap");
    const estado = () => ({
      display: document.getElementById("chart-history").style.display,
      minH: wrap.style.minHeight,
      caixa: !!wrap.querySelector(":scope > .chart-empty"),
      secao: wrap.style.display,
    });
    window.fetch = async () => ({ ok: false, status: 401, json: async () => ({}), text: async () => "" });
    await fetchHistory();
    const doente = estado();

    // Sessão volta (outra aba renovou, ou o interceptor renovou depois).
    window._charts = [];
    window.fetch = async () => ({
      ok: true, status: 200,
      json: async () => ({ data: [{ month: "2026-01", income: 9, expense: 3 }] }),
    });
    await fetchHistory();
    await new Promise((res) => setTimeout(res, 120));   // o build vai por setTimeout(…,50)
    const curado = { ...estado(), desenhou: window._charts.includes("chart-history") };

    // E a recuperação SEM dado nenhum: a seção que o 401 revelou tem de sumir.
    window.fetch = async () => ({ ok: true, status: 200, json: async () => ({ data: [] }) });
    await fetchHistory();
    await new Promise((res) => setTimeout(res, 120));
    return { doente, curado, vazio: estado() };
  });

  assert.equal(r.doente.caixa, true, "premissa: o 401 pinta a caixa sobreposta");
  assert.equal(r.doente.display, "none", "premissa: o 401 esconde o canvas");

  assert.equal(r.curado.caixa, false, "com a sessão de volta, a caixa de 401 tem de sair");
  assert.equal(r.curado.display, "", "o canvas tem de voltar a ser visível");
  assert.equal(r.curado.minH, "", "a altura forçada do estado vazio ficou presa");
  assert.equal(r.curado.desenhou, true, "o Chart tem de ser instanciado na volta");

  assert.equal(r.vazio.caixa, false, "recuperação sem dado não pode deixar a caixa de 401");
  assert.equal(r.vazio.secao, "none", "seção revelada pelo 401 tem de voltar a sumir sem dado");
  await semErros(page, errs);
  await page.close();
});

test("revalidação com cache quente: 401 vira estado final, e o irmão com DADO não é limpo", async () => {
  // Achado do Codex/Tester no #435: no ramo stale-while-revalidate o
  // `.catch(() => {})` engolia o 401 e a tela ficava com número velho, sem
  // caixa e sem botão — e não há redirect global pro /login
  // (`frontend/static/auth-refresh.js`). É o caminho mais comum que existe:
  // `switchView` chama sem `forceFresh`.
  // NEGATIVO: troque `.catch(_revalidacaoExpirou(stats))` de volta por
  // `.catch(() => {})` em `loadFixedView` e `temCaixa`/`temBotao` caem.
  // POSITIVO: `antes` prova que o caminho legítimo (200) continua renderizando,
  // e `irmaoIntacto` prova que a regra do painel irmão não regrediu — quem tem
  // dado RENDERIZADO não é apagado pelo 401 da revalidação.
  const { page, errs } = await bootPage();
  const r = await page.evaluate(async () => {
    document.body.insertAdjacentHTML("beforeend",
      '<div id="recurring-stats"></div><div id="recurring-essentials-list"></div>' +
      '<div id="recurring-leisure-list"></div><div id="recurring-upcoming-list"></div>' +
      '<div id="recurring-adjustments-list"></div>');
    USER_ID = 1;
    // `document.cookie` lança em about:blank (setContent), e o `_fetchRecurring`
    // manda header de CSRF. Não é o que este teste mede.
    window.csrfHeaders = () => ({});
    const ok = {
      ok: true, status: 200, text: async () => "{}",
      json: async () => ({ recurring: [
        { id: 1, description: "Netflix", amount: 39.9, frequency: "monthly",
          due_day: 10, category: "lazer", active: true },
      ] }),
    };
    window.fetch = async () => ok;
    await loadFixedView(true);          // popula o cache e renderiza
    const stats = document.getElementById("recurring-stats");
    const irmao = document.getElementById("recurring-essentials-list");
    const antes = { stats: stats.textContent.trim().length > 0,
                    irmao: irmao.innerHTML };

    window.fetch = async () => ({ ok: false, status: 401, json: async () => ({}), text: async () => "" });
    await loadFixedView();              // ramo de cache: render + revalidação
    await new Promise((res) => setTimeout(res, 50));
    return {
      renderouCom200: antes.stats,
      texto: stats.textContent.replace(/\s+/g, " ").trim(),
      temBotao: !!stats.querySelector("[data-relogin]"),
      semOnclick: !stats.querySelector("[onclick]"),
      irmaoIntacto: irmao.innerHTML === antes.irmao && antes.irmao.length > 0,
    };
  });
  assert.equal(r.renderouCom200, true, "premissa: o 200 renderiza os números (caminho legítimo)");
  assert.match(r.texto, /sessão expirou/i,
    `401 na revalidação sumiu sem rastro — o painel mostra "${r.texto}"`);
  assert.equal(r.temBotao, true, "estado final sem AÇÃO não fecha o achado");
  assert.equal(r.semOnclick, true, "handler é addEventListener, não atributo inline");
  assert.equal(r.irmaoIntacto, true,
    "o painel irmão tinha DADO renderizado: o 401 da revalidação não pode limpá-lo");
  await semErros(page, errs);
  await page.close();
});

test("o puxar-pra-atualizar continua SEM estado terminal (decisão declarada)", async () => {
  // A correção acima passa perto: o ramo `{background:true}` do MESMO loader
  // tem de seguir REJEITANDO sem tocar o DOM — quem sinaliza ali é o âmbar do
  // indicador do gesto, não uma caixa por cima do dado que o usuário está
  // puxando. NEGATIVO deste caso: roteie o background pro `_sessaoExpirou` e
  // `pintou`/`rejeitou` caem.
  const { page, errs } = await bootPage();
  const r = await page.evaluate(async () => {
    document.body.insertAdjacentHTML("beforeend",
      '<div id="recurring-stats"></div><div id="recurring-essentials-list"></div>' +
      '<div id="recurring-leisure-list"></div><div id="recurring-upcoming-list"></div>' +
      '<div id="recurring-adjustments-list"></div>');
    USER_ID = 1;
    window.csrfHeaders = () => ({});
    window.fetch = async () => ({ ok: false, status: 401, json: async () => ({}), text: async () => "" });
    let rejeitou = false;
    try { await loadFixedView(false, { background: true }); } catch { rejeitou = true; }
    const stats = document.getElementById("recurring-stats");
    return { rejeitou, pintou: /sessão expirou/i.test(stats.textContent) };
  });
  assert.equal(r.rejeitou, true, "o puxão precisa REJEITAR pro indicador do gesto assentar em âmbar");
  assert.equal(r.pintou, false, "o puxão não pinta estado terminal por cima do dado (decisão do PR)");
  await semErros(page, errs);
  await page.close();
});

test("voltar pra um mês JÁ VISITADO com a sessão morta: estado final com ação", async () => {
  // Terceiro achado do Codex no #435. `changeMonth` (`dashboard.js:7697`) pinta o
  // cache e chama `requestMonthPage(1, {background: Boolean(cached)})`; o
  // `_sessaoExpirou` do catch de `fetchMonthHttp` estava DENTRO do `!background`,
  // então o mês já visitado ficava com lançamentos velhos e sem ação nenhuma.
  // Este `background` NÃO é o do puxar-pra-atualizar (o caso acima prende aquele):
  // aqui ele só diz "o cache já está pintado, não mostre esqueleto".
  // NEGATIVO: devolva o `_sessaoExpirou` pra dentro do `if (!background)` e
  // `comCache.caixa`/`comCache.botao` caem.
  // POSITIVO: `semCache` prova que o caminho sem cache segue pintando, e
  // `erro500` prova que a mudança não transformou QUALQUER falha em caixa
  // terminal — só o 401.
  const { page, errs } = await bootPage();
  const r = await page.evaluate(async () => {
    document.body.insertAdjacentHTML("beforeend",
      '<div id="launches-card"></div><button id="refresh-btn"></button><div id="launches"></div>');
    USER_ID = 1;
    const card = document.getElementById("launches-card");
    const rodar = async (status, background) => {
      card.innerHTML = "<div id='velho'>lançamentos em cache</div>";
      window.fetch = async () => ({ ok: false, status, json: async () => ({}), text: async () => "" });
      await fetchMonthHttp(2026, 3, 1, 20, { background });
      return { caixa: /sessão expirou/i.test(card.textContent),
               botao: !!card.querySelector("[data-relogin]") };
    };
    return {
      comCache: await rodar(401, true),    // mês JÁ visitado: era o buraco
      semCache: await rodar(401, false),   // mês novo: já funcionava
      erro500:  await rodar(500, true),
    };
  });
  assert.equal(r.semCache.caixa, true, "premissa: o mês sem cache já pintava a caixa");
  assert.equal(r.comCache.caixa, true,
    "mês já visitado com 401 ficou com lançamentos velhos e sem ação de re-login");
  assert.equal(r.comCache.botao, true, "estado final sem AÇÃO não fecha o achado");
  assert.equal(r.erro500.caixa, false, "só o 401 é terminal: falha comum mantém o dado em cache");
  await semErros(page, errs);
  await page.close();
});

test("nenhuma revalidação nova volta a engolir o 401 em silêncio", () => {
  // Prende a CLASSE, não a instância: eram OITO loaders com o mesmo
  // `.catch(() => {})`. Quem quiser silêncio declara por escrito (`silencio-ok`)
  // na linha de cima, com o motivo — e o revisor vê a declaração no diff.
  // NEGATIVO: tire um `silencio-ok` (ou acrescente um `.catch(() => {})` sem
  // ele) e este teste lista o arquivo:linha.
  const js = readFileSync(DASHBOARD_JS, "utf-8").split("\n");
  const achados = js
    .map((l, i) => [i + 1, l])
    // `[^`]` antes: o próprio comentário do `_revalidacaoExpirou` CITA o
    // construto entre crases, e citação não é código.
    .filter(([n, l]) => /[^`]\.catch\(\(\) => \{\}\)/.test(l) &&
      !/silencio-ok/.test(js.slice(Math.max(0, n - 6), n).join("\n")))
    .map(([n, l]) => `dashboard.js:${n}  ${l.trim().slice(0, 110)}`);
  assert.deepEqual(achados, [],
    "rejeição engolida sem sinal ao usuário: roteie pro `_sessaoExpirou` (`_revalidacaoExpirou`) ou declare `silencio-ok` com o motivo");
});

test("trocar o tema NÃO apaga a caixa de sessão expirada (chart-day e chart-history)", async () => {
  // Achado do Codex/Tester: `applyTheme` reconstrói os gráficos do overview a
  // partir do cache (`_expenseSeries`, `_lastHistory`) e o `_chartVazio(el,
  // false)` de dentro do build* — o ANTÍDOTO do teste acima — apagava a caixa e
  // instanciava o Chart com a série VELHA. A sessão expirada sumia da tela por
  // uma troca de tema, nos DOIS gráficos.
  // NEGATIVO: tire o `if (document.querySelector(".chart-empty[data-terminal]")) return;`
  // do `applyTheme` (ou o `dataset.terminal` do `_sessaoExpirou`) e os quatro
  // `depois.*` caem.
  // POSITIVO: `curado` prova que a marca não prende nada — o 200 seguinte
  // devolve os dois gráficos.
  const { page, errs } = await bootPage();
  const r = await page.evaluate(async () => {
    document.body.insertAdjacentHTML("beforeend", '<div id="overview-view" class="active"></div>');
    USER_ID = 1;
    lastData = { expense_categories: [] };
    const dia = document.getElementById("chart-day");
    const hist = document.getElementById("chart-history");
    const caixa = (el) => !!el.parentElement.querySelector(":scope > .chart-empty");
    const foto = () => ({
      caixaDia: caixa(dia), caixaHist: caixa(hist),
      displayDia: dia.style.display, displayHist: hist.style.display,
    });

    // 200 primeiro: é ele que popula `_expenseSeries` e `_lastHistory` — sem
    // cache, `applyTheme` não reconstrói nada e o teste não mediria nada.
    window.fetch = async (u) => ({
      ok: true, status: 200,
      json: async () => (String(u).includes("/history/")
        ? { data: [{ month: "2026-01", income: 9, expense: 3 }] }
        : { data: [{ date: "2026-01-01", total: 12 }] }),
    });
    await loadExpenseChart(7);
    await fetchHistory();
    await new Promise((res) => setTimeout(res, 120));
    const comCache = { series: !!_expenseSeries, history: !!(_lastHistory && _lastHistory.length) };

    window.fetch = async () => ({ ok: false, status: 401, json: async () => ({}), text: async () => "" });
    await loadExpenseChart(7);
    await fetchHistory();
    const antes = foto();

    window._charts = [];
    applyTheme("light");
    applyTheme("dark");
    const depois = { ...foto(), instanciou: window._charts.slice() };

    // Sessão volta: a marca terminal não pode prender o gráfico.
    window.fetch = async (u) => ({
      ok: true, status: 200,
      json: async () => (String(u).includes("/history/")
        ? { data: [{ month: "2026-02", income: 4, expense: 1 }] }
        : { data: [{ date: "2026-02-01", total: 7 }] }),
    });
    window._charts = [];
    await loadExpenseChart(7);
    await fetchHistory();
    await new Promise((res) => setTimeout(res, 120));
    const curado = { ...foto(), instanciou: window._charts.slice() };
    return { comCache, antes, depois, curado };
  });
  assert.deepEqual(r.comCache, { series: true, history: true },
    "premissa: o 200 populou os caches que o applyTheme reconstrói");
  assert.equal(r.antes.caixaDia, true, "premissa: o 401 pinta a caixa do chart-day");
  assert.equal(r.antes.caixaHist, true, "premissa: o 401 pinta a caixa do chart-history");

  assert.equal(r.depois.caixaDia, true, "o tema apagou a caixa de sessão expirada do chart-day");
  assert.equal(r.depois.caixaHist, true, "o tema apagou a caixa de sessão expirada do chart-history");
  assert.equal(r.depois.displayDia, "none", "o canvas do chart-day voltou por cima da caixa");
  assert.equal(r.depois.displayHist, "none", "o canvas do chart-history voltou por cima da caixa");
  assert.deepEqual(r.depois.instanciou, [],
    `o tema instanciou Chart com a série velha: ${JSON.stringify(r.depois.instanciou)}`);

  assert.equal(r.curado.caixaDia, false, "com a sessão de volta, a caixa do chart-day tem de sair");
  assert.equal(r.curado.caixaHist, false, "com a sessão de volta, a caixa do chart-history tem de sair");
  assert.ok(r.curado.instanciou.includes("chart-day") && r.curado.instanciou.includes("chart-history"),
    `a marca terminal prendeu o gráfico na volta: ${JSON.stringify(r.curado.instanciou)}`);
  await semErros(page, errs);
  await page.close();
});

test("projeção de caixa: linha zerada não sai vermelha (comportamento, não texto)", async () => {
  // A varredura estática do teste seguinte é CEGA a este defeito quando o
  // ternário mora fora do template (`const cor = positive ? … : 'var(--red)'`),
  // e foi exatamente assim que ele sobreviveu a uma rodada. Aqui o critério é o
  // DOM renderizado: quem não tem boleto nenhum recebe `boletos_ate: 0` e a
  // linha "Boletos até lá" é a única chamada incondicionalmente.
  const { page, errs } = await bootPage();
  const linhas = await page.evaluate(() => {
    _renderProjection({
      target: "2026-02-10", tranquilo: true, projetado: 0,
      saldo_atual: 0, receitas_previstas: 0, gastos_fixos_previstos: 0,
      boletos_ate: 0, n_boletos: 0, faturas_cartao: 0, boleto_novo: 0,
    });
    return [...document.querySelectorAll("#boleto-sim-result span")]
      .filter((s) => /R\$/.test(s.textContent))
      .map((s) => ({ txt: s.textContent.trim(), cor: s.style.color }));
  });
  assert.ok(linhas.length >= 2, "premissa: a projeção renderiza as linhas de valor");
  for (const l of linhas) {
    if (!/R\$ -?0,00/.test(l.txt)) continue;
    assert.ok(!/var\(--red\)|var\(--green\)/.test(l.cor),
      `zero pintado de ${l.cor} em "${l.txt}" — cor de dinheiro no zero tem de ser neutra`);
    assert.ok(!/^[+−-]/.test(l.txt), `zero não leva sinal: "${l.txt}"`);
  }
  await semErros(page, errs);
  await page.close();
});

test("Resumo rápido dos recorrentes: quem não tem nada não lê '- R$ 0,00'", async () => {
  // Irmão direto do teste da projeção, e o MESMO construto: o "- " era literal,
  // não passava pelo `_toneClass`. Quem acabou de entrar (sem gasto fixo, sem
  // boleto) recebe 0 nas duas linhas — sinal de perda num valor neutro.
  // NEGATIVO: volte `"- " + _fmtBRL(...)` nas duas linhas e este teste fica
  // vermelho ("zero não leva sinal").
  const { page, errs } = await bootPage();
  const r = await page.evaluate(async () => {
    document.body.insertAdjacentHTML("beforeend", '<div id="recurring-overview-cards"></div>');
    // Conta vazia: os 3 endpoints do overview respondem com listas vazias.
    window.fetch = async () => ({
      ok: true, status: 200,
      json: async () => ({ recurring: [], incomes: [], bills: [] }),
      text: async () => "{}",
    });
    await loadRecurringOverview();
    const wrap = document.getElementById("recurring-overview-cards");
    const linhas = [...wrap.querySelectorAll("div")]
      .filter((d) => d.children.length === 2 &&
        /^(Gastos fixos|Boletos \/ contas|Receitas fixas)$/.test(d.children[0].textContent.trim()))
      .map((d) => ({
        k: d.children[0].textContent.trim(),
        txt: d.children[1].textContent.trim(),
        cor: d.children[1].style.color,
      }));
    const icones = [...wrap.querySelectorAll("i")].map((i) => i.className);
    return { linhas, icones, html: wrap.innerHTML.slice(0, 200) };
  });
  assert.equal(r.linhas.length, 3, `premissa: as 3 linhas do Resumo rápido renderizaram — ${r.html}`);
  for (const l of r.linhas) {
    assert.match(l.txt, /R\$ -?0,00/, `premissa: "${l.k}" sem dado é zero`);
    assert.ok(!/^[+−-]/.test(l.txt), `zero não leva sinal: "${l.k}: ${l.txt}"`);
    assert.ok(!/var\(--red\)|var\(--green\)/.test(l.cor), `zero pintado de ${l.cor} em "${l.k}"`);
  }
  // Ícone do card "Resultado previsto": `positivo` (`resultado >= 0`) desenhava
  // um "+" com o valor em R$ 0,00 neutro. NEGATIVO: volte o ternário e cai aqui.
  assert.ok(!r.icones.some((c) => /ph-plus|ph-minus/.test(c)),
    `resultado zero com ícone de polaridade: ${JSON.stringify(r.icones)}`);
  await semErros(page, errs);
  await page.close();
});

test("401 nos boletos e nos lançamentos da categoria: estado final COM ação", async () => {
  // As duas cargas de painel que tinham ficado fora do achado 401:
  // `_fetchBills` e `_catLaunchesFetch` lançavam `Error` sem `.status`, o catch
  // caía no texto genérico e os boletos ainda mandavam "Toque em Atualizar" —
  // instrução FALSA com a sessão expirada.
  // NEGATIVO: volte os dois `throw new Error(...)` e os dois `temBotao` caem.
  const { page, errs } = await bootPage();
  const r = await page.evaluate(async () => {
    document.body.insertAdjacentHTML("beforeend",
      '<div id="recurring-bills-agenda"></div><div id="recurring-bills-paid-list"></div>' +
      '<div id="recurring-bills-stats"></div><div id="forecast-result"></div>');
    window.fetch = async () => ({
      ok: false, status: 401, json: async () => ({}), text: async () => "",
    });
    const olha = (el) => ({
      texto: el.textContent.replace(/\s+/g, " ").trim(),
      temBotao: !!el.querySelector("[data-relogin]"),
      semOnclick: !el.querySelector("[onclick]"),
    });
    await loadBillsView();
    const bills = olha(document.getElementById("recurring-bills-agenda"));
    await openCategoryLaunches("mercado", { tipo: "despesa", includeInternal: false });
    const cat = olha(document.getElementById("cl-list"));
    return { bills, cat };
  });
  for (const nome of ["bills", "cat"]) {
    assert.match(r[nome].texto, /sessão expirou/i,
      `${nome}: 401 tem de virar estado final de sessão expirada, e não "${r[nome].texto}"`);
    assert.equal(r[nome].temBotao, true, `${nome}: estado final sem AÇÃO não fecha o achado 401`);
    assert.equal(r[nome].semOnclick, true, `${nome}: handler é addEventListener, não atributo inline`);
  }
  assert.ok(!/Atualizar/i.test(r.bills.texto),
    `boletos: "Toque em Atualizar" é falso com a sessão expirada — "${r.bills.texto}"`);
  await semErros(page, errs);
  await page.close();
});

test("previsão de saldo: horizonte em R$ 0,00 não é anunciado como 'no positivo'", async () => {
  // A legenda vinha da flag `tranquilo` do backend, não do valor na tela.
  // NEGATIVO: volte `${ok ? "no positivo" : "no vermelho"}` e este teste cai.
  const { page, errs } = await bootPage();
  const legendas = await page.evaluate(() => {
    document.body.insertAdjacentHTML("beforeend", '<div id="forecast-result"></div>');
    _renderForecast({ horizons: { 30: { tranquilo: true, projetado: 0 },
                                  60: { tranquilo: true, projetado: 10 },
                                  90: { tranquilo: false, projetado: -10 } } });
    return [...document.querySelectorAll("#forecast-result > div > div")]
      .map((t) => [...t.children].map((c) => c.textContent.trim()));
  });
  assert.equal(legendas.length, 3, "premissa: os 3 horizontes renderizaram");
  assert.ok(legendas[0].includes("R$ 0,00"), "premissa: o horizonte de 30 dias está zerado");
  assert.ok(!legendas[0].includes("no positivo"),
    `R$ 0,00 anunciado como "no positivo": ${JSON.stringify(legendas[0])}`);
  assert.ok(legendas[1].includes("no positivo"), "positivo de verdade continua 'no positivo'");
  assert.ok(legendas[2].includes("no vermelho"), "negativo continua 'no vermelho'");
  await semErros(page, errs);
  await page.close();
});

test("nenhum VALOR de dinheiro no dashboard.js tem cor fixa (literal ou por classe)", () => {
  // Estática, e de propósito: o defeito é do CONSTRUTO (verde/vermelho colado num
  // valor), e teste de comportamento só pegaria a tela que ele renderizasse.
  //
  // NADA aqui é lista digitada à mão, porque foi sempre a lista digitada à mão
  // que mentiu: primeiro faltava a classe (`ov-val pos|neg` viveu na Início com
  // este teste verde), depois faltava o formatador (`fmtBillValue`/`fmtShort`/
  // `fmtPnl` não casavam `\bfmt\(`, e `.bill-tx .tx-val` escapava por isso).
  // Os dois conjuntos saem do código.

  // ── 1. As classes que PINTAM, lidas do dashboard.css ──────────────────────
  const css = readFileSync(DASHBOARD_CSS, "utf-8").replace(/\/\*[\s\S]*?\*\//g, "");
  const hexes = [...css.matchAll(/--(?:green|red):\s*(#[0-9a-fA-F]{3,8})/g)].map((m) => m[1]);
  // Se um tema redefinir --green com rgb()/hsl(), a lista de hexes encolhe e a
  // varredura passa a ignorar quem usa o hex — em SILÊNCIO. Aqui ela grita.
  assert.equal((css.match(/--(?:green|red):/g) || []).length, hexes.length,
    "há declaração de --green/--red que não é hex: a varredura ficaria cega para ela");
  const PINTA = new RegExp(`color\\s*:\\s*(?:var\\(--(?:green|red)\\)|${hexes.join("|")})`, "i");
  /** A classe MODIFICADORA do seletor: a última do último composto que tem duas
      (`.pkt-hist-summary .it.dep .v` → `dep`, e não a base `v`, que é neutra e
      cujo nome de uma letra acusaria linha inocente); sem nenhum composto
      duplo, a última classe que aparecer (`.cl-sum .cl-in b` → `cl-in`, que a
      versão anterior descartava inteiro por o último composto ser um `b`). */
  const modificadora = (sel) => {
    const comps = sel.trim().split(/\s+/);
    let dupla = null;
    const todas = [];
    for (const c of comps) {
      const n = [...c.matchAll(/\.([a-zA-Z0-9_-]+)/g)].map((m) => m[1]);
      if (n.length >= 2) dupla = n[n.length - 1];
      todas.push(...n);
    }
    return dupla || todas[todas.length - 1] || null;
  };
  const CLASSES = new Set();
  for (const [, sel, corpo] of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    if (!PINTA.test(corpo)) continue;
    for (const um of sel.split(",")) {
      const c = modificadora(um);
      if (c) CLASSES.add(c);
    }
  }
  assert.ok(CLASSES.has("pos") && CLASSES.has("neg") && CLASSES.has("red"),
    "o levantamento não leu o dashboard.css — a varredura mediria o vazio");
  // Os dois cômodos que a versão anterior não enxergava, cada um com o seletor
  // que os produz — são o controle negativo da regra `modificadora` acima.
  assert.ok(CLASSES.has("cl-in"),
    "`.cl-sum .cl-in b` (último composto sem classe) sumiu do levantamento");
  assert.ok(CLASSES.has("dep") && !CLASSES.has("v"),
    "`.pkt-hist-summary .it.dep .v` tem de entrar como `dep`, nunca como a base `v`");

  // ── 2. Os formatadores de DINHEIRO, lidos do dashboard.js ─────────────────
  // Semente: definição em COLUNA 0 cujo nome casa /^_?fmt/ E cujo corpo escreve
  // "R$". Depois, ponto fixo: quem chama um deles também formata dinheiro
  // (`fmtBillValue` → `fmt`, `fmtPnl` → `fmt`). `fmtDate`, `fmtPct` e os outros
  // caem fora sozinhos, sem exceção escrita.
  //
  // A âncora `fmt` no NOME é limitação assumida, e é ela que segura o escopo: um
  // `function brl(v)` ou `money(v)`, ou um formatador indentado dentro de closure,
  // escapariam. Tirar a âncora NÃO melhora: medido em 16/09/2026 rodando a mesma
  // derivação sem o `_?fmt`, o ponto fixo vai a 44 nomes, porque todo renderer que
  // chama `fmt(` passa a contar como formatador. Varrer toda definição de coluna 0
  // com "R$" no corpo não achou formatador de dinheiro fora da convenção de nome
  // (mesma data; remedir antes de reusar o resultado).
  const js = readFileSync(DASHBOARD_JS, "utf-8").split("\n");
  const defs = new Map();
  js.forEach((l, i) => {
    const m = /^(?:function|const|let)\s+(_?fmt[A-Za-z0-9_]*)\s*[=(]/.exec(l);
    if (m) defs.set(m[1], i);
  });
  const corpos = new Map();
  for (const [nome, i] of defs) {
    let fim = i + 1;
    while (fim < js.length && fim - i < 14 && !/^(function|const|let|\/\*|\})/.test(js[fim])) fim++;
    corpos.set(nome, js.slice(i, fim + 1).join("\n"));
  }
  const DINHEIRO = new Set([...corpos].filter(([, c]) => c.includes("R$")).map(([n]) => n));
  for (let mudou = true; mudou;) {
    mudou = false;
    for (const [n, c] of corpos) {
      if (DINHEIRO.has(n)) continue;
      if ([...DINHEIRO].some((d) => new RegExp(`\\b${d}\\(`).test(c))) { DINHEIRO.add(n); mudou = true; }
    }
  }
  for (const obrigatorio of ["fmt", "_fmtBRL", "fmtPnl", "fmtBillValue", "fmtShort"]) {
    assert.ok(DINHEIRO.has(obrigatorio), `a derivação perdeu o formatador ${obrigatorio}`);
  }
  assert.ok(!DINHEIRO.has("fmtDate") && !DINHEIRO.has("fmtPct"),
    "a derivação varreu formatador que não é de dinheiro");
  const VALOR = new RegExp(`\\b(?:${[...DINHEIRO].join("|")})\\(|toFixed\\(2\\)`);

  // ── 3. Cor que NÃO é sinal do valor: exceção NOMEADA, com o porquê ────────
  const EXCECOES = new Set([
    // verde de ESTADO ("esta parcela está paga"), não de valor positivo.
    "paid", "paid-amt",
    // Cor de DOMÍNIO: a lista só tem um tipo de lançamento, então a cor diz o
    // que a lista é, não se o número é bom. `.cat-val` é gasto por categoria;
    // `.bill-tx .tx-val` é item de fatura (tudo despesa) e `.refund` é o estorno
    // dentro dela; `.receipt-amount` é o valor de um comprovante de pagamento
    // feito. É o conceito `catColors()` que o docstring do `_toneMoney` manda
    // não confundir com cor de valor. Zero aqui não é ambíguo: a linha só
    // existe porque houve lançamento.
    "cat-val", "tx-val", "refund", "receipt-amount",
    // `.cl-sum .cl-in b`: o total de ENTRADAS do detalhe da categoria. Verde
    // fixo, mas o bloco está atrás de `resumo.receita > 0`, na linha logo acima
    // (`sum.innerHTML = saidas + (resumo.receita > 0 ? …`), então não existe zero
    // para pintar. Exceção pelo GUARDA, e não pela semântica — e por isso ela vem
    // PRESA a ele na asserção abaixo: comentário não trava nada, e sem a trava o
    // dia em que o `> 0` sair é o dia em que a exceção passa a esconder um bug.
    // Mesmo padrão do `tests/test_phosphor_subset.py` (CLAUDE.md §0.7).
    "cl-in",
  ]);
  const iClIn = js.findIndex((l) => /class="cl-in"/.test(l));
  assert.ok(iClIn > 0 && /resumo\.receita\s*>\s*0/.test(js.slice(iClIn - 2, iClIn + 1).join("\n")),
    'a exceção "cl-in" vale só enquanto o `resumo.receita > 0` guardar aquela ' +
    "linha: o guarda saiu, então a exceção tem de sair junto");

  // ── 4. A varredura ────────────────────────────────────────────────────────
  // JANELA: o defeito atravessa linhas. No `fmtPnl` o literal "pnl-up" estava
  // 2 linhas ACIMA do `fmt(`, e a linha do `fmt(` só via a variável `${cls}` —
  // line-based, este teste passava verde com "↑ R$ 0,00" verde na tela.
  // Por que 4, e não mais: a varredura casa NOME DE CLASSE, sem contexto de
  // seletor nem especificidade, então alargar a janela multiplica falso positivo
  // mais rápido que achado. Medido em 16/09/2026 variando só este número neste
  // arquivo — 4→0 achados, 6→4, 8→8, 10→10, 15→20, 25→38 — e classificando os 38
  // um a um: os já consertados nesta série, escadas de LIMIAR (o corte de 15% do
  // `savingsCls`; o `_deltaLabel`, que já tem ramo de zero) e casamento por nome
  // (o `fillClass` de `_renderBudgetRow` pinta a BARRA, não o valor). Nenhum
  // defeito vivo entre 5 e 25. O 4 cobre a forma conhecida; REMEDIR antes de
  // reusar o número — o comando é trocar este 4 e reclassificar os achados.
  const JANELA = 4;
  const semTone = (t) => t.replace(/_toneClass\([^)]*\)/g, "");
  const achados = [];
  js.forEach((l, i) => {
    if (!VALOR.test(l)) return;
    const volta = js.slice(Math.max(0, i - JANELA), i + 1).join("\n");
    if (!/class=|classList|style\.color/.test(volta)) return;
    const escritas = new Set();
    for (const [, attr] of semTone(l).matchAll(/class="([^"]*)"/g)) {
      for (const c of attr.split(/[^a-zA-Z0-9_-]+/)) escritas.add(c);
    }
    // Classe escrita entre aspas na vizinhança (ternário interpolado, variável
    // `cls`, `classList.add`). O `_toneClass(...)` sai antes: ele é o conserto,
    // e "pos"/"neg" ali são argumento dele, não marcação.
    // Só das linhas VIZINHAS que não são marcação: numa linha com `class="…"` a
    // aspa é do próprio atributo (`class="rv-badge"` de um irmão), e ler isso
    // como cor deste valor acusa linha inocente. O caso que importa é o do
    // `fmtPnl`: `const cls = up ? "pnl-up" : "pnl-down";`, sem `class=`.
    const vizinhas = js.slice(Math.max(0, i - JANELA), i).filter((x) => !/class="/.test(x));
    for (const [, c] of semTone(vizinhas.join("\n")).matchAll(/['"]([a-zA-Z0-9_-]+)['"]/g)) escritas.add(c);
    for (const [, c] of semTone(l).matchAll(/['"]([a-zA-Z0-9_-]+)['"]/g)) escritas.add(c);
    for (const c of escritas) {
      if (CLASSES.has(c) && !EXCECOES.has(c)) {
        achados.push(`dashboard.js:${i + 1}  [.${c}]  ${l.trim().slice(0, 90)}`);
      }
    }
  });
  const LITERAL = /color:\s*(?:"|')?var\(--(?:green|red)\)/;
  const INTERP  = /color:\s*\$\{[^}]*var\(--(?:green|red)\)/;
  js.forEach((l, i) => {
    if (VALOR.test(l) && (LITERAL.test(l) || INTERP.test(l))) {
      achados.push(`dashboard.js:${i + 1}  ${l.trim().slice(0, 110)}`);
    }
  });
  assert.deepEqual([...new Set(achados)].sort(), [],
    "cor de dinheiro tem de passar por _toneMoney/_toneClass");

  // PONTOS CEGOS que sobram, nomeados: cor aplicada por `el.style.color = …` ou
  // `classList.add(…)` a MAIS de 4 linhas do formatador, e classe montada por
  // concatenação de pedaços. Nenhum caso VIVO hoje (esta varredura roda no
  // arquivo inteiro e sai vazia); declarar só vale para forma não alcançada.
});



test("401 nos 4 painéis restantes: estado final COM ação", async () => {
  // Os que tinham ficado de fora do achado 401: afiliados, agentes, previsão de
  // saldo e simulação de prazo. Todos lançavam `Error` sem `.status` e o catch
  // caía no texto genérico ("Não consegui calcular agora", "Erro: …") — nenhum
  // deles diz ao usuário o que fazer quando a sessão é o problema.
  // NEGATIVO: volte um `throw new Error(...)` (ou tire um `_sessaoExpirou`) e o
  // painel correspondente cai.
  const { page, errs } = await bootPage();
  const r = await page.evaluate(async () => {
    document.body.insertAdjacentHTML("beforeend",
      '<div id="affiliate-stats"></div><div id="affiliate-body"></div>' +
      '<div id="agentes-shelf"></div><div id="agentes-feed"></div>' +
      '<div id="forecast-result"></div>' +
      '<input id="boleto-sim-date" value="2026-12-01"><input id="boleto-sim-amount" value="">');
    window.featureAllowed = () => true;   // 403 é paywall e tem caminho próprio
    window.fetch = async () => ({
      ok: false, status: 401, json: async () => ({ detail: "qualquer coisa" }),
      text: async () => "",
    });
    const olha = (id) => {
      const el = document.getElementById(id);
      return {
        texto: el.textContent.replace(/\s+/g, " ").trim(),
        temBotao: !!el.querySelector("[data-relogin]"),
        semOnclick: !el.querySelector("[onclick]"),
      };
    };
    await loadAffiliateView(true);
    const afiliados = olha("affiliate-body");
    const statsSobrando = document.getElementById("affiliate-stats").innerHTML.trim();
    await loadAgentesView(true);
    const agentes = olha("agentes-shelf");
    await loadForecast();
    const previsao = olha("forecast-result");
    await simularPrazo();
    const prazo = olha("boleto-sim-result");
    return { afiliados, agentes, previsao, prazo, statsSobrando };
  });
  for (const nome of ["afiliados", "agentes", "previsao", "prazo"]) {
    assert.match(r[nome].texto, /sessão expirou/i,
      `${nome}: 401 tem de virar estado final de sessão expirada, e não "${r[nome].texto}"`);
    assert.equal(r[nome].temBotao, true, `${nome}: estado final sem AÇÃO não fecha o achado 401`);
    assert.equal(r[nome].semOnclick, true, `${nome}: handler é addEventListener, não atributo inline`);
  }
  // Os skeletons de #affiliate-stats são irmãos do #affiliate-body: deixados
  // pendurados, a tela mostra "sessão expirou" embaixo de 4 blocos carregando.
  assert.equal(r.statsSobrando, "", `afiliados: skeletons pendurados — "${r.statsSobrando}"`);
  await semErros(page, errs);
  await page.close();
});

test("contagem do histórico: 0 receitas / 0 despesas saem neutras", async () => {
  // Contagem também é superfície de cor: `color: var(--green)` fixo escrevia
  // "0 receitas" em verde (boa notícia que não houve) e "0 despesas" em vermelho.
  // NEGATIVO: volte as duas cores fixas e o primeiro caso cai.
  // POSITIVO: o segundo caso prova que 12/30 seguem verde/vermelho.
  const { page, errs } = await bootPage();
  const cores = await page.evaluate(() => {
    const tiles = () => [...document.querySelectorAll("#history-stats .stat-tile .stat-value")]
      .map((v) => v.style.color);
    const medir = (s) => {
      document.getElementById("history-stats").innerHTML =
        '<div class="stat-tile"><div class="stat-value"></div></div>'.repeat(4);
      renderHistoryStats(s);
      return tiles();
    };
    return {
      zerado: medir({ avg_per_month: 0, receitas_count: 0, despesas_count: 0, total_count: 0 }),
      cheio: medir({ avg_per_month: 4, receitas_count: 12, despesas_count: 30, total_count: 42 }),
    };
  });
  assert.equal(cores.zerado[1], "var(--text-2)", `"0 receitas" saiu ${cores.zerado[1]}`);
  assert.equal(cores.zerado[2], "var(--text-2)", `"0 despesas" saiu ${cores.zerado[2]}`);
  assert.equal(cores.cheio[1], "var(--green)", `12 receitas perderam o verde: ${cores.cheio[1]}`);
  assert.equal(cores.cheio[2], "var(--red)", `30 despesas perderam o vermelho: ${cores.cheio[2]}`);
  await semErros(page, errs);
  await page.close();
});

test("401 não apaga o painel IRMÃO que já mostra dado", async () => {
  // O `stats.innerHTML = ""` do 401 dos afiliados era INCONDICIONAL: com cache
  // quente, `loadAffiliateView()` (sem argumento, como em `initDashboard`) pinta
  // os números de verdade e SÓ ENTÃO a revalidação toma 401 — a tela perdia
  // "Indicados/Disponível/Já recebido" para ganhar uma caixa. Zerar dado bom é
  // pior que deixar o skeleton.
  // NEGATIVO: tire o `if (esqueleto)` e `statsDepois` volta a ficar vazio.
  // O braço dos AGENTES não tem controle negativo, e isso é intencional: nenhuma
  // linha viva de `loadAgentesView` consegue deixá-lo vermelho (o 401 de lá não
  // toca no feed). Ele é SENTINELA de não-regressão — quebra no dia em que
  // alguém "uniformizar" os dois painéis apagando o irmão dos agentes, que é
  // exatamente o conserto errado. Não leia as duas asserções como equivalentes.
  const { page, errs } = await bootPage();
  const r = await page.evaluate(async () => {
    document.body.insertAdjacentHTML("beforeend",
      '<div id="affiliate-stats"></div><div id="affiliate-body"></div>' +
      '<div id="agentes-shelf"></div><div id="agentes-feed"></div>');
    const responde = (payload) => { window.fetch = async () => ({
      ok: true, status: 200, json: async () => payload, text: async () => "",
    }); };
    const expira = () => { window.fetch = async () => ({
      ok: false, status: 401, json: async () => ({}), text: async () => "",
    }); };
    const txt = (id) => document.getElementById(id).textContent.replace(/\s+/g, " ").trim();

    responde({ referrals: [], stats: { referrals: 3 }, code: "PIG123" });
    await loadAffiliateView(true);
    const statsAntes = txt("affiliate-stats");
    expira();
    await loadAffiliateView();                     // sem argumento: cache quente
    const statsDepois = txt("affiliate-stats");

    responde({ agents: [], events: [{ title: "Disparo info", subtitle: "—" }] });
    await loadAgentesView(true);
    const feedAntes = txt("agentes-feed");
    expira();
    await loadAgentesView();
    const feedDepois = txt("agentes-feed");
    return {
      statsAntes, statsDepois, feedAntes, feedDepois,
      skeletonSobrando: !!document.getElementById("affiliate-stats").querySelector(".sk"),
      corpo: txt("affiliate-body"), shelf: txt("agentes-shelf"),
    };
  });
  assert.notEqual(r.statsAntes, "", "o teste não chegou a pintar as estatísticas");
  assert.equal(r.skeletonSobrando, false,
    "o que ficou no irmão é ESQUELETO, não dado — o teste mediria a coisa errada");
  assert.equal(r.statsDepois, r.statsAntes,
    `afiliados: o 401 apagou estatística já renderizada ("${r.statsAntes}" → "${r.statsDepois}")`);
  assert.notEqual(r.feedAntes, "", "o teste não chegou a pintar o feed");
  assert.equal(r.feedDepois, r.feedAntes,
    `agentes: o 401 mexeu no feed já renderizado ("${r.feedAntes}" → "${r.feedDepois}")`);
  // O painel principal continua assumindo o estado final nos dois.
  assert.match(r.corpo, /sessão expirou/i);
  assert.match(r.shelf, /sessão expirou/i);
  await semErros(page, errs);
  await page.close();
});

test("fmtPnl: resultado zerado não tem cor NEM seta", async () => {
  // `fmtPnl(0)` escrevia `<span class="pnl-up">↑ R$ 0,00</span>`: verde e seta
  // pra cima num resultado que não subiu. E o zero é o DEFAULT — os dois
  // chamadores passam `sum.pnl || 0`, então renda variável sem posição cai nele.
  // A seta sai junto com a cor de propósito: "↑" afirma o mesmo que o verde.
  // NEGATIVO: volte `const up = Number(v) >= 0` e os dois primeiros casos caem.
  // POSITIVO: os dois últimos provam que ganho e perda reais seguem pintados.
  const { page, errs } = await bootPage();
  const r = await page.evaluate(() => ["zero", "residuo", "ganho", "perda"].map((nome, i) => {
    const v = [0, -2e-13, 50, -50][i];
    const el = document.createElement("div");
    el.innerHTML = fmtPnl(v, null);
    const span = el.firstElementChild;
    return { nome, classe: span.className, texto: span.textContent.trim() };
  }));
  const byName = Object.fromEntries(r.map((x) => [x.nome, x]));
  for (const nome of ["zero", "residuo"]) {
    assert.equal(byName[nome].classe, "", `${nome}: saiu com classe de cor (${byName[nome].classe})`);
    assert.ok(!/[↑↓]/.test(byName[nome].texto), `${nome}: saiu com seta — "${byName[nome].texto}"`);
    assert.match(byName[nome].texto, /R\$ 0,00/, `${nome}: o valor sumiu — "${byName[nome].texto}"`);
  }
  assert.equal(byName.ganho.classe, "pnl-up", "ganho real perdeu o verde");
  assert.match(byName.ganho.texto, /^↑ R\$ 50,00$/, `ganho: "${byName.ganho.texto}"`);
  assert.equal(byName.perda.classe, "pnl-down", "perda real perdeu o vermelho");
  assert.match(byName.perda.texto, /^↓ R\$ 50,00$/, `perda: "${byName.perda.texto}"`);
  await semErros(page, errs);
  await page.close();
});
