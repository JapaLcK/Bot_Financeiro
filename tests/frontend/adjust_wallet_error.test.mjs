/**
 * "Ajustar carteira" falhou — o usuário tem que VER o erro.
 *
 * O `catch` de `submitAdjustWallet` escrevia em `errEl`, que só existe como
 * `const` dentro de `_adjustWalletError` (dashboard.js:9723). Fora daquele
 * escopo o nome não existe: qualquer falha do POST /adjust-balance virava
 * `ReferenceError` DENTRO do catch — a promise rejeitava, o `finally` até
 * destravava o botão, mas a mensagem nunca aparecia. E mesmo em escopo a linha
 * original não mostraria nada: falta o `classList.toggle("show", …)` que
 * `_adjustWalletError` faz, e `.modal-error` é `display:none` sem `.show`.
 *
 * Por isso o teste mede QUATRO coisas de uma vez: a promise não rejeita, o
 * texto chega, a caixa fica VISÍVEL e o botão volta a clicável. Controle
 * negativo: reverter a linha 9750 para `errEl.textContent = …` deixa
 * `rejeitou: true` e `texto: ""`.
 *
 * "Visível" aqui é estilo COMPUTADO com a dashboard.css real carregada, não
 * `classList.contains("show")` — pelo mesmo motivo de
 * tests/frontend/onboarding_visibility.test.mjs: teste sem CSS é cego à metade
 * do conserto. Renomear `.modal-error.show` (dashboard.css:1729) para
 * `.modal-error.visivel` quebra a visibilidade em produção e deixa 3 destes 4
 * testes vermelhos; medindo só o nome da classe, os 4 passavam.
 *
 * Como roda: mesmo padrão de dashboard_category_escape.test.mjs — o dashboard.js
 * é script CLÁSSICO de 10 mil linhas, injetado inteiro numa página com o DOM
 * mínimo que o topo dele exige. Zero `pageerror` no carregamento prova que o
 * arquivo executou até o fim.
 *
 * Rodar:  npm run test:frontend
 *         (ou só este: node --test tests/frontend/adjust_wallet_error.test.mjs)
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { chromium } from "playwright";

const FRONTEND = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "frontend");
const DASHBOARD_JS = join(FRONTEND, "dashboard.js");
const DASHBOARD_CSS = join(FRONTEND, "dashboard.css");

/** IDs que o nível superior do dashboard.js acessa sem `?.` (grep:
    `^document.getElementById("…").`) mais os que o `render()` toca. */
const IDS = [
  "grid", "bgt-overlay", "bgt-input", "investment-detail-overlay",
  "investment-help-overlay", "edit-launch-overlay", "launch-overlay",
  "launch-valor", "pocket-overlay", "pocket-name", "pocket-history-overlay",
  "card-overlay", "card-name", "card-closing-day", "card-due-day",
  "bill-detail-overlay", "pay-bill-overlay", "pay-bill-receipt-overlay",
  "pay-bill-amount", "overview-heading", "launches-title", "launches-wrap",
  "charts-title", "charts-grid", "alert-banner", "last-update",
  "categories-distribution",
  // O modal de ajustar carteira + o `#toast` que showToast() usa no sucesso.
  "adjust-wallet-overlay", "adjust-wallet-submit", "toast",
  "adjust-wallet-banks", "adjust-wallet-total",
];

let browser;
before(async () => { browser = await chromium.launch(); });
after(async () => { await browser?.close(); });

async function carregar() {
  const page = await browser.newPage();
  const errs = [];
  page.on("pageerror", (e) => errs.push(String(e)));
  await page.setContent(
    IDS.map((i) => `<div id="${i}"></div>`).join("") +
    // .modal-error é display:none sem .show — a classe é metade do conserto,
    // e quem a transforma em pixel é a CSS carregada logo abaixo.
    `<div id="adjust-wallet-error" class="modal-error"></div>` +
    // <input> de verdade, não <div>: openAdjustWalletModal() chama inp.select(),
    // que só existe em input — e o teste de reabrir passa por lá.
    `<input id="adjust-wallet-input">`,
  );
  // Promessa que nunca resolve: o IIFE de boot não pode navegar nem apagar o
  // body (com fetch rejeitando, ele apaga — e o modal some antes do submit).
  await page.evaluate(() => { window.fetch = () => new Promise(() => {}); });
  await page.addStyleTag({ path: DASHBOARD_CSS });
  await page.addScriptTag({ path: DASHBOARD_JS });
  assert.deepEqual(errs, [], "dashboard.js não executou até o fim");
  // DEPOIS do script, não antes: o dashboard.js DECLARA csrfHeaders (linha 78),
  // e a declaração ganharia do stub. O original lê document.cookie, que em
  // about:blank levanta SecurityError e mascararia o erro que estamos medindo.
  await page.evaluate(() => { window.csrfHeaders = () => ({}); });
  return page;
}

/**
 * Troca o fetch pelo desfecho pedido, preenche o input, submete e devolve o
 * estado observável do modal.
 * @param {"ok"|"offline"|"http500"} desfecho
 *   ok      = POST 200;
 *   offline = o fetch REJEITA (rede caiu);
 *   http500 = o fetch RESOLVE com !resp.ok — o outro ramo do catch, que passa
 *             por `throw new Error(await readApiError(resp))`. É por onde caem
 *             o 500 do servidor e o 403 de CSRF vencido, e ele não roda no
 *             caso "offline".
 */
const submeter = (page, desfecho) => page.evaluate(async (responder) => {
  window.fetch = () => ({
    ok: Promise.resolve({ ok: true, json: () => Promise.resolve({}) }),
    offline: Promise.reject(new Error("offline")),
    // json() rejeitando = corpo não-JSON; readApiError cai no genérico com o status.
    http500: Promise.resolve({ ok: false, status: 500, json: () => Promise.reject(new Error("não é JSON")) }),
  })[responder];
  document.getElementById("adjust-wallet-input").value = "10";
  // O modal está aberto quando o usuário submete — sem isto, "fechou" passaria
  // de graça, já que closeAdjustWalletModal() só remove a classe.
  document.getElementById("adjust-wallet-overlay").classList.add("open");
  let rejeitou = false;
  try { await submitAdjustWallet(); } catch { rejeitou = true; }
  const el = document.getElementById("adjust-wallet-error");
  return {
    rejeitou,
    texto: el.textContent,
    // O veredito é o estilo computado; `.show` fica como pista secundária.
    visivel: getComputedStyle(el).display !== "none",
    temShow: el.classList.contains("show"),
    disabled: document.getElementById("adjust-wallet-submit").disabled,
    aberto: document.getElementById("adjust-wallet-overlay").classList.contains("open"),
    submitting: _adjustWalletState.submitting,
  };
}, desfecho);

test("falha do POST mostra a mensagem no modal, visível e sem estourar", async () => {
  const page = await carregar();
  const r = await submeter(page, "offline");
  assert.equal(r.rejeitou, false, "submitAdjustWallet rejeitou — o catch estourou");
  assert.match(r.texto, /offline/, "a mensagem do erro não chegou ao modal");
  assert.equal(r.visivel, true, "a caixa de erro ficou display:none — texto invisível");
  assert.equal(r.temShow, true, "a classe .show é o mecanismo que a CSS usa");
  assert.equal(r.disabled, false, "o finally tinha que destravar o botão");
  await page.close();
});

test("erro HTTP (500) também aparece no modal, visível", async () => {
  const page = await carregar();
  const r = await submeter(page, "http500");
  assert.equal(r.rejeitou, false, "submitAdjustWallet rejeitou — o catch estourou");
  assert.match(r.texto, /erro 500/, "a mensagem do readApiError não chegou ao modal");
  assert.equal(r.visivel, true, "a caixa de erro ficou display:none — texto invisível");
  assert.equal(r.temShow, true, "a classe .show é o mecanismo que a CSS usa");
  await page.close();
});

/**
 * O espelho do bug de cima: lá, texto SEM `.show` (invisível); aqui, `.show`
 * SEM texto — a caixa vazia fica VISÍVEL, uma barra vermelha de 18px no topo do
 * modal (`.modal-error`, dashboard.css:1727). Controle negativo: trocar a linha
 * 9711 de volta por `document.getElementById("adjust-wallet-error").textContent
 * = ""` deixa a caixa acesa, com altura > 0.
 */
test("reabrir depois de uma falha não deixa caixa de erro vazia visível", async () => {
  const page = await carregar();
  const antes = await submeter(page, "offline");
  assert.equal(antes.visivel, true, "pré-condição: a falha tinha que acender a caixa");
  const depois = await page.evaluate(() => {
    closeAdjustWalletModal();
    openAdjustWalletModal();
    const el = document.getElementById("adjust-wallet-error");
    return { texto: el.textContent, altura: el.offsetHeight };
  });
  assert.equal(depois.texto, "", "o texto do erro anterior sobreviveu ao reabrir");
  assert.equal(depois.altura, 0, "caixa vazia visível = barra vermelha no topo do modal");
  await page.close();
});

test("sucesso fecha o modal e destrava o estado (controle positivo)", async () => {
  const page = await carregar();
  const r = await submeter(page, "ok");
  assert.equal(r.rejeitou, false);
  assert.equal(r.texto, "", "sucesso não pode escrever erro nenhum");
  assert.equal(r.aberto, false, "o modal tinha que fechar no sucesso");
  assert.equal(r.submitting, false, "o finally tinha que zerar o submitting");
  await page.close();
});
