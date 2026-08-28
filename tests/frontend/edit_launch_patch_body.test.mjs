/**
 * O modal "Editar lançamento" só pode mandar no PATCH o campo que o usuário MEXEU.
 *
 * O bug: o corpo era `{ nota }` + `criado_em` sempre que o campo de data tinha
 * valor — e ele SEMPRE tem, porque `openEditLaunchModal` o pré-preenche com
 * `toLocalDatetimeInput(launch.criado_em)`. Salvar só a descrição reenviava o
 * MESMO instante que já estava no banco, e o backend, ao gravar `criado_em`,
 * move junto o `posted_at` (db/accounts.py). Numa linha importada sem hora
 * confiável (Open Finance legado: `criado_em` = meia-noite UTC) o `posted_at`
 * é o ÚNICO campo certo, e ele passava a valer `day_tz(meia-noite UTC)` = o DIA
 * ANTERIOR. Visão Geral, Histórico, detalhe de categoria e o `list_launches`
 * do WhatsApp leem esse campo (`fmtLaunchWhen`, dashboard.js:485) — todos
 * passavam a mostrar 09/03 no lugar de 10/03.
 *
 * A regra já existia no mesmo arquivo pra `categoria` ("o PATCH omite a chave
 * quando não muda"); aqui ela vale pra CLASSE — os três campos do modal.
 *
 * Controle NEGATIVO: volte `const reqBody = {}` + os três `if` para
 * `const reqBody = { nota }; if (categoria) …; if (criadoEmISO) …` em
 * `submitEditLaunch` (dashboard.js) — o 1º e o 4º teste ficam vermelhos.
 * E o pré-preenchimento tem controle próprio: troque o `has_time === false ?
 * posted_at` de `openEditLaunchModal` por `toLocalDatetimeInput(criado_em)` puro
 * — o 1º teste fica vermelho com "2026-03-09T21:00" (o campo abrindo um dia
 * antes do cabeçalho da própria caixa).
 * Controle POSITIVO: o 2º e o 3º teste provam que mudar a data e mudar a
 * categoria continuam chegando ao servidor.
 *
 * Como roda: mesma receita do dashboard_category_escape.test.mjs — o
 * `dashboard.js` é script CLÁSSICO de 10 mil linhas, injetado inteiro numa
 * página com o DOM mínimo que o topo dele exige. O markup do modal NÃO é
 * reescrito aqui: sai recortado do `dashboard.html` de verdade, senão o teste
 * mediria um formulário que não existe.
 *
 * Rodar:  npm run test:frontend
 *         (ou só este: node --test tests/frontend/edit_launch_patch_body.test.mjs)
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { chromium } from "playwright";

const FRONTEND = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "frontend");
const DASHBOARD_JS = join(FRONTEND, "dashboard.js");

/** O markup REAL do overlay, recortado do dashboard.html. */
function modalMarkup() {
  const html = readFileSync(join(FRONTEND, "dashboard.html"), "utf8");
  const ini = html.indexOf('<div class="overlay" id="edit-launch-overlay">');
  const fim = html.indexOf('<div class="modal-success-toast"', ini);
  assert.ok(ini > 0 && fim > ini, "o overlay de edição sumiu do dashboard.html");
  return html.slice(ini, fim);
}

/** IDs que o nível superior do dashboard.js acessa sem `?.` (menos o overlay,
    que vem do HTML de verdade acima). */
const IDS = [
  "grid", "bgt-overlay", "bgt-input", "investment-detail-overlay",
  "investment-help-overlay", "launch-overlay",
  "launch-valor", "pocket-overlay", "pocket-name", "pocket-history-overlay",
  "card-overlay", "card-name", "card-closing-day", "card-due-day",
  "bill-detail-overlay", "pay-bill-overlay", "pay-bill-receipt-overlay",
  "pay-bill-amount", "overview-heading", "launches-title", "launches-wrap",
  "charts-title", "charts-grid", "alert-banner", "last-update",
  "categories-distribution", "launch-success-toast",
];

let browser;
before(async () => { browser = await chromium.launch(); });
after(async () => { await browser?.close(); });

async function loadDashboardJs() {
  const page = await browser.newPage();
  const errs = [];
  page.on("pageerror", (e) => errs.push(String(e)));
  // Origem de verdade (e não `setContent`, que carrega about:blank): o
  // `csrfHeaders` do fluxo lê `document.cookie`, e num documento opaco isso
  // estoura com SecurityError antes de o PATCH sair. Nada vai à rede — a rota
  // é atendida aqui mesmo.
  await page.route("https://pigbank.test/**", (route) =>
    route.fulfill({
      contentType: "text/html",
      body: IDS.map((i) => `<div id="${i}"></div>`).join("") + modalMarkup(),
    }),
  );
  await page.goto("https://pigbank.test/dashboard");
  await page.evaluate(() => { window.fetch = () => new Promise(() => {}); });
  await page.addScriptTag({ path: DASHBOARD_JS });
  assert.deepEqual(errs, [], "dashboard.js não executou até o fim");
  return page;
}

/**
 * Abre o modal com `launch`, aplica `edicao` nos campos (o que o usuário
 * digitaria) e clica em Salvar. Devolve o corpo do PATCH — ou `null` se
 * nenhuma requisição saiu.
 */
const salvar = (page, launch, edicao) => page.evaluate(async ([l, e]) => {
  window.__req = null;
  window.fetch = (url, opts) => {
    window.__req = { url, body: JSON.parse(opts.body) };
    return Promise.resolve({
      ok: true, status: 200, json: () => Promise.resolve({ ok: true }),
    });
  };
  window.sendRefreshSilent = () => {};        // não existe WebSocket no teste
  openEditLaunchModal(l.id, l);
  const antes = {
    categoria: document.getElementById("edit-launch-categoria").value,
    nota: document.getElementById("edit-launch-nota").value,
    data: document.getElementById("edit-launch-data").value,
  };
  for (const [campo, valor] of Object.entries(e)) {
    document.getElementById("edit-launch-" + campo).value = valor;
  }
  await submitEditLaunch();
  return {
    antes,
    req: window.__req,
    aberto: document.getElementById("edit-launch-overlay").classList.contains("open"),
  };
}, [launch, edicao]);

/** Linha do Open Finance legado: instante = meia-noite UTC de 10/03, que em
    São Paulo é 09/03 21:00 — o caso em que mover `posted_at` custa um dia. */
const OF_LEGADO = {
  id: 42, tipo: "despesa", valor: 137.77, categoria: "mercado",
  alvo: "MERCADO PAGUE MENOS", nota: null,
  criado_em: "2026-03-10T00:00:00+00:00",
  posted_at: "2026-03-10", has_time: false,
};

test("editar só a descrição: o PATCH não carrega criado_em nem categoria", async () => {
  const page = await loadDashboardJs();
  const r = await salvar(page, OF_LEGADO, { nota: "corrigindo so a descricao" });

  // o campo de data ESTAVA preenchido — é daí que vinha o reenvio. E ele abre
  // no DIA que o resto da tela mostra (posted_at 10/03), não no instante cru
  // gravado (meia-noite UTC = 09/03 21:00 em São Paulo).
  assert.equal(r.antes.data, "2026-03-10T12:00");
  assert.deepEqual(r.req.body, { nota: "corrigindo so a descricao" });
  assert.equal("criado_em" in r.req.body, false, "reenviou a data sem o usuário mexer");
  assert.equal("categoria" in r.req.body, false, "reenviou a categoria sem o usuário mexer");
  assert.equal(r.aberto, false, "o modal não fechou");
  await page.close();
});

test("editar a data de fato: criado_em vai no PATCH (controle positivo)", async () => {
  const page = await loadDashboardJs();
  const r = await salvar(page, OF_LEGADO, { data: "2026-04-15T09:00" });

  assert.deepEqual(Object.keys(r.req.body), ["criado_em"]);
  // hora de PAREDE de São Paulo, não do device: 09:00 em SP = 12:00Z. Com
  // `new Date(dataVal)` num WebView em UTC sairia 09:00Z (3h a menos).
  assert.equal(
    new Date(r.req.body.criado_em).getTime(),
    Date.parse("2026-04-15T09:00:00-03:00"),
  );
  await page.close();
});

test("trocar a categoria: só ela vai no PATCH (controle positivo)", async () => {
  const page = await loadDashboardJs();
  const r = await salvar(page, OF_LEGADO, { categoria: "lazer" });

  assert.deepEqual(r.req.body, { categoria: "lazer" });
  await page.close();
});

test("salvar sem mexer em nada: nenhuma requisição sai", async () => {
  const page = await loadDashboardJs();
  const r = await salvar(page, OF_LEGADO, {});

  assert.equal(r.req, null, "PATCH disparado sem o usuário mudar nada");
  assert.equal(r.aberto, false, "o modal não fechou");
  await page.close();
});

test("lançamento manual com segundos != 0: minuto igual não é mudança", async () => {
  // `toLocalDatetimeInput` trunca segundos (o datetime-local é de minuto).
  // Comparando instante cru em vez da string do campo, TODA linha com
  // segundos != 0 pareceria alterada e o bug voltaria por essa porta.
  const page = await loadDashboardJs();
  const r = await salvar(
    page,
    { ...OF_LEGADO, criado_em: "2026-03-10T12:34:56-03:00", posted_at: null, has_time: true },
    { nota: "so a nota" },
  );

  assert.equal(r.antes.data, "2026-03-10T12:34");
  assert.deepEqual(r.req.body, { nota: "so a nota" });
  await page.close();
});
