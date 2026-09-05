/**
 * O contrato de `tipo` que a Início consome (issue #287, PR 3).
 *
 * ╔══════════════════════════════════════════════════════════════════════════╗
 * ║ ESTE GRUPO É CONTROLE **POSITIVO** E LACRE DE CONTRATO — NÃO É CONTROLE  ║
 * ║ NEGATIVO. Ele passa COM e SEM o conserto do PR, e isso é o esperado:    ║
 * ║ sob a decisão (a) — canonizar no SERVIDOR, sem tocar no home.html — o   ║
 * ║ navegador NUNCA VÊ a forma legada. O vermelho que prova o conserto mora ║
 * ║ em N1/N2 do grupo Python (`tests/test_tipo_legado_no_dashboard.py`,     ║
 * ║ `test_projecao_nao_devolve_forma_legada_nenhuma` e                      ║
 * ║ `test_projecao_colapsa_saida_em_despesa_sem_tocar_no_valor`), que ficam ║
 * ║ vermelhos ao reverter a projeção da query 4.                            ║
 * ║                                                                          ║
 * ║ SEM ESTA FRASE ALGUÉM LÊ SETE CASOS VERDES COMO PROVA DO FIX. Não são.  ║
 * ║ O que eles provam é o OUTRO lado do contrato: dado o payload canônico,  ║
 * ║ a Início desenha certo — e volta a ficar vermelho no dia em que alguém  ║
 * ║ mexer no home.html sem saber que o servidor já canoniza.                ║
 * ╚══════════════════════════════════════════════════════════════════════════╝
 *
 * DESVIO CONSCIENTE, decidido pelo dono: o caso `saida → "Despesa"` NO
 * NAVEGADOR não existe aqui. Ele exigiria a opção (b) (ensinar a forma legada
 * ao home.html), que foi descartada em favor da (a). Se um dia a (b) entrar,
 * este é o arquivo que ganha o caso.
 *
 * O que fica coberto (payload já canônico, como o servidor passa a devolver):
 *   - despesa → classe `expense`, sinal `-`, ícone `ph-trend-down`
 *   - receita → classe `income`,  sinal `+`, ícone `ph-trend-up`
 *   - credito (perna do cartão) → ícone `ph-credit-card`, tag `expense`
 *     (o que o home.html FAZ hoje; o porquê está no comentário do caso)
 *   - despesa descrita como "cartão" → tag `credit` (o par que a alcança)
 *   - os 4 tipos internos do filtro (home.html:1134) ficam FORA da lista, e
 *     `deposito_caixinha` — que não está nesse filtro — fica DENTRO
 *   - `#greeting-sub` (home.html:944) diz "Despesa"/"Receita" e nunca valor cru
 *   - onboarding (home.html:1094) marca com 1 lançamento não-interno e não
 *     marca com 1 interno
 *   - o repaint por `sessionStorage.pb_home_1` desenha o mesmo contrato
 *
 * Rodar:  npm run test:frontend
 *         (um arquivo só: node --test tests/frontend/home_tipo_atividade.test.mjs)
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { startServer } from "./_server.mjs";
import { chromium } from "playwright";

let ORIGIN;   // porta EFÊMERA — o `before` preenche (ver _server.mjs)

const json = (body) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });

/** Ação de rota que nunca rejeita: reload/close no meio cancela a request e o
 *  fulfill estoura — mesmo motivo de `reset_cache_multiaba.test.mjs`. */
const acaoSegura = async (fn) => { try { await fn(); } catch { /* navegou/fechou */ } };

let server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer());
                     browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

const lancamento = (o) => ({
  id: 1, tipo: "despesa", valor: 50, alvo: "mercado", nota: null,
  categoria: "mercado", criado_em: new Date().toISOString(),
  is_internal_movement: false, ...o,
});

/** Snapshot mínimo que a Início aceita, com os lançamentos pedidos. */
const snapshot = (launches) => ({
  balance: 0, of_bank_balance: 0, monthly_income: 0, monthly_expense: 0,
  credit_cards: [], pockets: [], investments: [], alerts: [],
  recent_launches: launches,
  launches_pagination: { page: 1, limit: 25, total: launches.length, total_pages: 1 },
});

/**
 * Abre a home REAL e deixa `loadHomeData` rodar com o `/data` injetado.
 *
 * `/history/**` é OBRIGATÓRIO stubar: na carga inicial o `Promise.all` do
 * `loadHomeData` (home.html:1569-1571) rejeita se ele não responder, o catch
 * troca o `#content` inteiro pela tela de erro (:1578-1599) e todos os asserts
 * caem por "elemento não existe" — sintoma errado da causa certa.
 *
 * `seed`: quando presente, pré-carrega `sessionStorage.pb_home_1` ANTES do
 * boot, para exercer o repaint instantâneo do `restoreHomeCache`.
 */
async function abrirHome(launches, { seed = null } = {}) {
  const ctx = await browser.newContext();
  const page = await ctx.newPage();

  await page.route("**/*", (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== ORIGIN) return acaoSegura(() => route.abort());     // CDN fora
    if (/\.[a-z0-9]+$/i.test(url.pathname)) return acaoSegura(() => route.continue());
    return acaoSegura(() => route.fulfill(json({})));                     // /auth/me etc.
  });
  await page.route("**/auth/validate", (r) => acaoSegura(() => r.fulfill(json({ user_id: 1 }))));
  await page.route("**/data/**",       (r) => acaoSegura(() => r.fulfill(json(snapshot(launches)))));
  await page.route("**/history/**",    (r) => acaoSegura(() => r.fulfill(json({ data: [] }))));

  if (seed) {
    await page.addInitScript((entrada) => {
      sessionStorage.setItem("pb_home_1", JSON.stringify(entrada));
    }, seed);
  }

  await page.goto(`${ORIGIN}/home.html`);
  page.__ctx = ctx;
  return page;
}

async function fechar(page) {
  try { await page.unrouteAll({ behavior: "ignoreErrors" }); } catch { /* já fechada */ }
  try { await page.__ctx.close(); } catch { /* já fechado */ }
}

/** Espera a lista de atividade ter linha e devolve o que cada uma desenhou. */
async function linhasDaAtividade(page) {
  await page.waitForSelector("#activity-list .activity-row", { timeout: 15000 });
  return page.$$eval("#activity-list .activity-row", (rows) => rows.map((row) => ({
    tag:   row.querySelector(".activity-tag")?.className || "",
    icone: row.querySelector(".activity-tag i")?.className || "",
    valor: row.querySelector(".activity-amount, .activity-val")?.textContent?.trim()
           || row.textContent.trim(),
  })));
}

test("despesa: tag `expense`, sinal `-` e ícone ph-trend-down", async () => {
  const page = await abrirHome([lancamento({ tipo: "despesa", valor: 50 })]);
  try {
    const [linha] = await linhasDaAtividade(page);
    assert.match(linha.tag, /\bexpense\b/, linha.tag);
    assert.match(linha.icone, /ph-trend-down/, linha.icone);
    assert.match(linha.valor, /-/, linha.valor);
  } finally { await fechar(page); }
});

test("receita: tag `income`, sinal `+` e ícone ph-trend-up", async () => {
  const page = await abrirHome([lancamento({ tipo: "receita", valor: 80, alvo: "freela" })]);
  try {
    const [linha] = await linhasDaAtividade(page);
    assert.match(linha.tag, /\bincome\b/, linha.tag);
    assert.match(linha.icone, /ph-trend-up/, linha.icone);
    assert.match(linha.valor, /\+/, linha.valor);
    assert.doesNotMatch(linha.valor, /(^|[^+])-\s*R\$/, linha.valor);
  } finally { await fechar(page); }
});

test("credito (perna do cartão): ícone ph-credit-card, tag `expense`", async () => {
  // `credito` é o tipo que o `ELSE tipo` do TIPO_CANON_SQL preserva — é o que a
  // perna de crédito da query 4 emite (`'credito' AS tipo`).
  //
  // MEDIDO, e diferente do que o plano previa: o home.html dá tag `expense`,
  // não `credit`. A tag `credit` exige `isCredito && isDespesa` (home.html:1149)
  // e `isDespesa` só é true para 'despesa'/'saida' (:1147) — nunca para
  // 'credito'. O ícone, esse sim, sai `ph-credit-card`, porque o `emoji`
  // (:1154) olha só `isCredito`. Ou seja: hoje a linha de cartão sai com ícone
  // de cartão e cor de despesa comum.
  //
  // Isto NÃO é regressão deste PR nem é da #287: acontece com zero linha
  // legada na base e a mudança de uma linha na projeção não o alcança. Fica
  // LACRADO como está — quem for consertar a inconsistência vê este caso ficar
  // vermelho e decide de propósito.
  const page = await abrirHome([lancamento({ tipo: "credito", valor: 70, alvo: "Cartão Nubank" })]);
  try {
    const [linha] = await linhasDaAtividade(page);
    assert.match(linha.icone, /ph-credit-card/, linha.icone);
    assert.match(linha.tag, /\bexpense\b/, `tag de hoje é expense, não credit: ${linha.tag}`);
  } finally { await fechar(page); }
});

test("despesa descrita como cartão: aí sim a tag `credit`", async () => {
  // O caminho que ALCANÇA a tag `credit` — o par positivo do caso acima, que
  // prova que a classe não está morta.
  const page = await abrirHome([lancamento({ tipo: "despesa", valor: 70, alvo: "Cartão Nubank" })]);
  try {
    const [linha] = await linhasDaAtividade(page);
    assert.match(linha.tag, /\bcredit\b/, linha.tag);
    assert.match(linha.icone, /ph-credit-card/, linha.icone);
  } finally { await fechar(page); }
});

test("os 4 tipos internos do filtro ficam fora; deposito_caixinha fica dentro", async () => {
  // home.html:1134 exclui exatamente estes 4. `deposito_caixinha` NÃO está na
  // lista, então tem de aparecer — o servidor também o deixa passar (o
  // `NOT IN` da query 4 tem os mesmos 4 nomes).
  const fora = ["criar_caixinha", "delete_pocket", "create_investment", "delete_investment"];
  const page = await abrirHome([
    ...fora.map((tipo, i) => lancamento({ id: 10 + i, tipo, valor: 5 })),
    lancamento({ id: 99, tipo: "deposito_caixinha", valor: 20, alvo: "reserva" }),
  ]);
  try {
    const linhas = await linhasDaAtividade(page);
    assert.equal(linhas.length, 1, `só o deposito_caixinha desenha: ${JSON.stringify(linhas)}`);
  } finally { await fechar(page); }
});

test("#greeting-sub diz Despesa/Receita e nunca o valor cru do tipo", async () => {
  for (const [tipo, rotulo] of [["despesa", "Despesa"], ["receita", "Receita"]]) {
    const page = await abrirHome([lancamento({ tipo, valor: 42, alvo: "mercado" })]);
    try {
      await page.waitForSelector("#greeting-sub strong", { timeout: 15000 });
      const txt = await page.$eval("#greeting-sub", (el) => el.textContent);
      assert.match(txt, new RegExp(rotulo), txt);
      // o cru nunca aparece: nem o tipo do payload, nem a forma legada
      assert.doesNotMatch(txt, /\b(saida|entrada|despesa|receita)\b/, txt);
    } finally { await fechar(page); }
  }
});

test("onboarding: 1 lançamento não-interno marca o item; 1 interno não marca", async () => {
  const marcado = async (launches) => {
    const page = await abrirHome(launches);
    try {
      await page.waitForSelector(".onboarding .ob-item", { timeout: 15000 });
      // `return await`, não `return`: sem o await o `finally` fecha o contexto
      // antes de a promise resolver e o $$eval morre com "page has been closed".
      return await page.$$eval(".onboarding .ob-item", (its) =>
        its.some((i) => i.className.includes("done")
                        && /primeiro lançamento/i.test(i.textContent)));
    } finally { await fechar(page); }
  };

  assert.equal(await marcado([lancamento({ tipo: "despesa", valor: 50 })]), true,
               "despesa não-interna tem de marcar 'Fazer seu primeiro lançamento'");
  assert.equal(await marcado([lancamento({ tipo: "deposito_caixinha", valor: 20,
                                           is_internal_movement: true })]), false,
               "movimento interno não conta como primeiro lançamento");
});

test("repaint por sessionStorage.pb_home_1 desenha o mesmo contrato", async () => {
  // `restoreHomeCache` (home.html:1432) pinta ANTES do /data. O tipo guardado
  // no cache é o que o servidor devolveu — então já vem canônico, e o repaint
  // tem de respeitar o mesmo contrato de cor/sinal/ícone.
  const seed = {
    userId: 1, email: "", displayName: "", history: [], savedAt: Date.now(),
    snapshot: snapshot([lancamento({ tipo: "receita", valor: 80, alvo: "freela" })]),
  };
  const page = await abrirHome([lancamento({ tipo: "receita", valor: 80, alvo: "freela" })],
                               { seed });
  try {
    const [linha] = await linhasDaAtividade(page);
    assert.match(linha.tag, /\bincome\b/, linha.tag);
    assert.match(linha.icone, /ph-trend-up/, linha.icone);
  } finally { await fechar(page); }
});
