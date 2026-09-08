/**
 * "Hoje" na confirmação de antecipar fatura tem de ser o dia em APP_TZ
 * (São Paulo), não o dia em UTC nem o do aparelho. Issue #257.
 *
 * O bug: `const todayIso = new Date().toISOString().slice(0, 10)` — `toISOString`
 * devolve UTC. Às 21:30 de 02/09 em São Paulo (2026-09-03T00:30:00Z) o `todayIso`
 * já vale "2026-09-03", então uma fatura que fecha AMANHÃ (`period_end`
 * "2026-09-03") falha na comparação estrita `>` e o modal "Antecipar fatura?"
 * NÃO abre. O usuário paga 3/3 antes de 1/3 sem a confirmação que existe
 * justamente para isso. Erra para 100% dos usuários das 21:00 às 23:59 de SP,
 * mesmo com o celular em Brasília — é a diferença desta ocorrência para as
 * outras da mesma família no arquivo, que leem o fuso do APARELHO e só erram
 * para quem está fora de São Paulo. A lista delas está na issue #257.
 *
 * Instante congelado: 2026-09-03T00:30:00Z.
 *   SP  → 2026-09-02   |   UTC → 2026-09-03
 *
 * Controles NEGATIVOS — MEDIDOS um a um (04/09/2026), os três injetados em casos
 * que estavam VERDES:
 *  - `todayIso` de volta para `new Date().toISOString().slice(0, 10)` (o bug
 *    original): 4 passam, 2 falham — o 1º E o 4º. O 4º entra junto porque em
 *    Tóquio o dia UTC também é 03/09; não é o alvo dele, mas conta.
 *  - a CHAMADA em `submitPayBill` (`frontend/dashboard.js`, o `const todayIso =`)
 *    trocada de `appTodayIso()` para `_isoDate(new Date())` — a tentação óbvia,
 *    "hoje" pelo fuso do APARELHO: 5 passam, falha SÓ o 4º. É a medição que
 *    justifica o caso 4 existir: sem ele este arquivo aceitaria a correção errada.
 *    O ponto de injeção importa: mutar o CORPO de `appTodayIso` (`return
 *    _isoDate(now)`) é outra mutação, porque alcança os DOIS chamadores — medida
 *    também, dá 3 passam / 3 falham (4º, 5º e 6º).
 *  - `_billToday` de volta para `{ const d = new Date(); d.setHours(0,0,0,0);
 *    return d; }`: 4 passam, falham o 5º e o 6º (em Tóquio já é 03/09, então a
 *    fatura de hoje-em-SP aparece vencida e a de ontem conta -2 dias).
 * Controles POSITIVOS: 2º e 3º provam que a confirmação NÃO passou a aparecer
 * sempre (fatura de hoje e fatura velha seguem pagando direto — se ela virasse
 * incondicional, os dois ficariam vermelhos); o 6º prova que `_billOverdue` não
 * virou constante `false`.
 *
 * Como roda: mesma receita do edit_launch_patch_body.test.mjs — o `dashboard.js`
 * é script CLÁSSICO, injetado inteiro numa página com o DOM mínimo que o topo
 * dele exige. O modal NÃO é aberto: `payBillState` é `let` global, então dá para
 * montar o estado e chamar `submitPayBill()` direto.
 *
 * Rodar:  npm run test:frontend
 *         (ou só este: node --test tests/frontend/fatura_futura_app_tz.test.mjs)
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { chromium } from "playwright";

const FRONTEND = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "frontend");
const DASHBOARD_JS = join(FRONTEND, "dashboard.js");

/** 21:30 de 02/09 em São Paulo — dentro da janela 21:00-23:59 que o bug abre. */
const AGORA = "2026-09-03T00:30:00.000Z";

/** IDs que o nível superior do dashboard.js acessa sem `?.`, mais os três que o
    `submitPayBill` toca no caminho de erro. */
const IDS = [
  "grid", "bgt-overlay", "bgt-input", "investment-detail-overlay",
  "investment-help-overlay", "launch-overlay",
  "launch-valor", "pocket-overlay", "pocket-name", "pocket-history-overlay",
  "card-overlay", "card-name", "card-closing-day", "card-due-day",
  "bill-detail-overlay", "pay-bill-overlay", "pay-bill-receipt-overlay",
  "overview-heading", "launches-title", "launches-wrap",
  "charts-title", "charts-grid", "alert-banner", "last-update",
  "categories-distribution", "launch-success-toast",
  "edit-launch-overlay", "pay-bill-error", "pay-bill-submit-btn",
];

let browser;
before(async () => { browser = await chromium.launch(); });
after(async () => { await browser?.close(); });

async function abrir(timezoneId) {
  const page = await browser.newPage({ timezoneId });
  const errs = [];
  page.on("pageerror", (e) => errs.push(String(e)));
  // `setFixedTime` (e não `install`): congela só o relógio, sem parar os timers
  // — com os timers parados o `await submitPayBill()` poderia nunca voltar.
  // Antes do goto/addScriptTag, senão o `dashboard.js` já teria lido a hora real.
  await page.clock.setFixedTime(new Date(AGORA));
  await page.route("https://pigbank.test/**", (route) =>
    route.fulfill({
      contentType: "text/html",
      body: IDS.map((i) => `<div id="${i}"></div>`).join("")
        + `<input id="pay-bill-amount">`,
    }),
  );
  await page.goto("https://pigbank.test/dashboard");
  await page.evaluate(() => { window.fetch = () => new Promise(() => {}); });
  await page.addScriptTag({ path: DASHBOARD_JS });
  assert.deepEqual(errs, [], "dashboard.js não executou até o fim");

  // SANIDADE DUPLA. Sem as duas, o arquivo inteiro é teatro: se o relógio não
  // congelar, "hoje" é o dia real da máquina e nenhum caso mede o que diz medir;
  // se o fuso não pegar, o caso 4 vira o caso 1 com outro nome.
  const amb = await page.evaluate(() => ({
    agora: new Date().toISOString(),
    tz: Intl.DateTimeFormat().resolvedOptions().timeZone,
  }));
  assert.equal(amb.agora, AGORA, "o relógio da página não ficou congelado");
  assert.equal(amb.tz, timezoneId, "o Chromium não entrou no fuso pedido");
  return page;
}

/** Monta `payBillState`, espiona o `confirmModal` (respondendo "não", para o
    fluxo parar ali) e chama `submitPayBill`. Devolve quantas vezes perguntou. */
const pagar = (page, periodEnd) => page.evaluate(async (pe) => {
  payBillState = {
    balance: 1000,
    bills: [{
      id: 1, due_amount: 100, period_end: pe,
      card_name: "Nubank", label: "set",
    }],
    selectedId: 1,
    submitting: false,
  };
  window.__perguntas = [];
  confirmModal = (msg) => { window.__perguntas.push(msg); return Promise.resolve(false); };
  window.fetch = () => Promise.reject(new Error("rede desligada no teste"));
  document.getElementById("pay-bill-amount").value = "100";
  await submitPayBill();
  return {
    perguntou: window.__perguntas.length,
    erro: document.getElementById("pay-bill-error").textContent,
  };
}, periodEnd);

test("21:30 em SP: fatura que fecha AMANHÃ ainda pede confirmação", async () => {
  // O caso do bug. Em UTC já é 03/09, então "2026-09-03" > hoje dava false.
  const page = await abrir("America/Sao_Paulo");
  const r = await pagar(page, "2026-09-03");
  assert.equal(r.perguntou, 1, "pagou fatura futura sem perguntar (o bug da #257)");
  await page.close();
});

test("fatura que fecha HOJE em SP não pede confirmação (positivo)", async () => {
  const page = await abrir("America/Sao_Paulo");
  const r = await pagar(page, "2026-09-02");
  assert.equal(r.perguntou, 0, "passou a perguntar em fatura que já fechou");
  assert.match(r.erro, /rede desligada/, "nem chegou a tentar pagar");
  await page.close();
});

test("fatura velha não pede confirmação (positivo)", async () => {
  const page = await abrir("America/Sao_Paulo");
  const r = await pagar(page, "2026-08-20");
  assert.equal(r.perguntou, 0, "passou a perguntar em fatura vencida");
  await page.close();
});

test("aparelho em Asia/Tokyo: 'hoje' continua sendo o de SÃO PAULO", async () => {
  // No MESMO instante, em Tóquio já é 03/09. Um conserto que lesse o fuso do
  // APARELHO (`_isoDate(new Date())`) deixaria "2026-09-03" > "2026-09-03"
  // false e este caso VERMELHO — é ele que separa o conserto certo do errado.
  const page = await abrir("Asia/Tokyo");
  const r = await pagar(page, "2026-09-03");
  assert.equal(r.perguntou, 1, "leu 'hoje' pelo fuso do aparelho, não por APP_TZ");
  await page.close();
});

/** `_billToday` é o par disto na tela de faturas: mesmo defeito, mesmo remédio. */
const diasAte = (page, dueDate) => page.evaluate(
  (d) => ({
    dias: _billDaysUntil({ due_date: d }),
    vencida: _billOverdue({ due_date: d }),
  }),
  dueDate,
);

test("Asia/Tokyo: boleto que vence hoje-em-SP não aparece vencido", async () => {
  const page = await abrir("Asia/Tokyo");
  assert.deepEqual(await diasAte(page, "2026-09-02"), { dias: 0, vencida: false });
  await page.close();
});

test("Asia/Tokyo: boleto de ontem-em-SP continua vencido (positivo)", async () => {
  const page = await abrir("Asia/Tokyo");
  assert.deepEqual(await diasAte(page, "2026-09-01"), { dias: -1, vencida: true });
  await page.close();
});
