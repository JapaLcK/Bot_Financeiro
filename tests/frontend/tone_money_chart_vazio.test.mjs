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
  // valor), e teste de comportamento só pegaria a tela que ele renderizasse. Foi
  // assim que o banner "Sobra R$ 0,00" em verde fixo escapou de uma rodada, e as
  // classes `.tx-amt red`/`.stat-delta down` escaparam da seguinte — a primeira
  // versão desta varredura só olhava cor LITERAL e passava vazia.
  const js = readFileSync(DASHBOARD_JS, "utf-8").split("\n");
  const VALOR   = /_fmtBRL\(|_fmtBRLshort\(|\bfmt\(|toFixed\(2\)/;
  const LITERAL = /color:\s*(?:"|')?var\(--(?:green|red)\)/;
  // Cor de dinheiro escrita DENTRO de ternário interpolado. Era o ponto cego
  // declarado da rodada anterior, e o defeito estava vivo lá dentro: o `line()`
  // de `_renderProjection` pintava "− R$ 0,00" de vermelho para quem não tem
  // boleto nenhum. Ponto cego declarado ainda deixa o bug em produção.
  const INTERP  = /color:\s*\$\{[^}]*var\(--(?:green|red)\)/;
  // As classes que a `dashboard.css` colore de verde/vermelho:
  //   .tx-row .tx-amt.red|.green (429-430)   .stat-tile .stat-delta.up|.down (487-488)
  const CLASSE  = /class="tx-amt (?:red|green)"|class="stat-delta (?:up|down)"/;
  const achados = js
    .map((l, i) => [i + 1, l])
    .filter(([, l]) => VALOR.test(l) && (LITERAL.test(l) || CLASSE.test(l) || INTERP.test(l)))
    .map(([n, l]) => `dashboard.js:${n}  ${l.trim().slice(0, 110)}`);
  assert.deepEqual(achados, [], "cor de dinheiro tem de passar por _toneMoney/_toneClass");
});
