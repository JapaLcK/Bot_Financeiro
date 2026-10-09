/**
 * Onda 5, PR-E, apontamentos P2 do Codex no PR #903: dois `await` sem dono.
 *  - K (of-status-poll.js): a leitura do acompanhamento tem prazo
 *    (`LEITURA_PRAZO_MS`). Um GET do tique que nunca responde é abortado nele,
 *    solta `emVoo` e a cadência segue; sem isso a volta ao foco e o Atualizar
 *    OK não religavam nada até recarregar a página.
 *  - L (settings.html): o botão Atualizar é restaurado só pelo `fim` do
 *    Atualizar MAIS RECENTE. O `fim` velho (GET do 402 preso que solta depois do
 *    prazo) não reabilita o botão com outro Atualizar em voo.
 * Contrato em docs/open_finance_estados.md §2.4. Só em 1440×900: o módulo não
 * depende de layout. Harness: tests/frontend/_of_coleta.mjs.
 *
 * Controles (CLAUDE.md §3), medidos por mutação:
 *  - K e K-tarde: sem o `Promise.race` com o prazo no `ler` → 0 GET depois do
 *    tique preso. K-tarde também: sem o `signal` no fetch do `loadData` → a
 *    resposta tardia repinta e desliga o ciclo. Positivo: K-ok (GET lento
 *    dentro do prazo pinta e a cadência é a de sempre).
 *  - L: com a restauração do botão antes do `if (gen !== _ofRefreshGen)` →
 *    o botão volta com o 2º Atualizar em voo. Positivos: L-PTR (o `fim` de um
 *    PTR mais recente restaura o botão: nunca preso) e o F de
 *    of_acompanha_geracao.test.mjs (o prazo restaura).
 *    O L-PTR é estado sintético: em produção botão e PTR não coexistem (o PTR
 *    só é instalado em app, app-mode.js; o app esconde o botão, app-mode.css
 *    em html.pb-app). Ele guarda só a invariante "o botão nunca fica preso";
 *    nele, um 3º clique pode gerar POST concorrente, aceito pelo dono em
 *    2026-10-09 (o backend serializa por `claim_manual_refresh`).
 *
 * Rodar: node --test tests/frontend/of_acompanha_prazo.test.mjs
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { MIN, json, NU, abrir, assentar, andar, gets, pilulas, agora } from "./_of_coleta.mjs";

const VP = { width: 1440, height: 900 };
const NUNCA = new Promise(() => {});
const SYNC_OK = { ok: true, still_updating: 0, items: [] };
const corpo = (conexoes) => json({ ok: true, sync: SYNC_OK, connections: conexoes, accounts: [], transactions: [] });
const rel = (g, t0) => g.map((t) => (t - t0) / 1000);
const trava = () => { let s; const p = new Promise((r) => { s = r; }); return [p, s]; };
const botao = (page) => page.evaluate(() => {
  const b = document.getElementById("of-refresh-btn");
  return [b.disabled, b.textContent];
});

// K: o 1º tique (5 s) nunca responde. Volta ao foco e Atualizar OK depois
// disso: o acompanhamento tem de voltar a ler.
test("K: GET do tique que nunca responde não congela o acompanhamento", async () => {
  const page = await abrir(VP, (n) => (n === 2 ? { segurar: NUNCA } : { conexoes: [NU("updating")] }), {
    rotas: (p) => p.route("**/open-finance/1/refresh*", (r) => r.fulfill(corpo([NU("updating")]))) });
  try {
    await andar(page, 5000, 1000, { preso: true });
    const t0 = await agora(page);
    await andar(page, 30_000, 1000, { preso: true });
    await page.evaluate(() => { window.__ocultar(true); window.__ocultar(false); });
    await andar(page, 2000, 1000, { preso: true });
    await page.click("#of-refresh-btn");
    await andar(page, 3 * MIN, 1000, { preso: true });
    const g = rel(await page.evaluate(() => window.__gets), t0);
    assert.ok(g.filter((x) => x > 0).length >= 3, `o acompanhamento parou de ler depois do GET preso: ${g}`);
  } finally { await page.context().close(); }
});

// K-tarde: o GET preso solta DEPOIS do prazo com "Atualizado", com a aba oculta
// (nenhuma leitura nova o superou). Ele não pinta nem desliga o ciclo: na
// volta ao foco o tique lê de novo.
test("K-tarde: resposta depois do prazo não pinta nem mexe no ciclo", async () => {
  const [preso, soltar] = trava();
  const page = await abrir(VP, (n) => (n === 2 ? { segurar: preso, conexoes: [NU("updated")] } : { conexoes: [NU("updating")] }));
  try {
    await andar(page, 5000, 1000, { preso: true });
    await andar(page, 16_000, 1000, { preso: true });   // prazo da leitura (15 s) vence
    await page.evaluate(() => window.__ocultar(true));
    soltar();
    await andar(page, 3000, 1000, { preso: true });
    assert.deepEqual(await pilulas(page), ["Atualizando…"], "a resposta tardia repintou");
    const n = (await gets(page)).length;
    await page.evaluate(() => window.__ocultar(false));
    await andar(page, 2000, 1000, { preso: true });
    assert.equal((await gets(page)).length - n, 1, "a resposta tardia desligou o ciclo");
  } finally { await page.context().close(); }
});

// K-ok (positivo): GET do tique lento, mas dentro do prazo (10 s): pinta e a
// cadência segue contada do fim dele (5 s → lê; responde aos 15 s; +10 s).
test("K-ok: GET lento dentro do prazo pinta e a cadência é a de sempre", async () => {
  const [lento, soltar] = trava();
  const page = await abrir(VP, (n) => (n === 2 ? { segurar: lento, conexoes: [NU("updating")] }
    : n === 3 ? { conexoes: [NU("updated")] } : { conexoes: [NU("updating")] }));
  try {
    const t0 = await agora(page);
    await andar(page, 15_000, 1000, { preso: true });
    soltar();
    await assentar(page);
    await andar(page, 2 * MIN, 1000);
    assert.deepEqual(rel(await page.evaluate(() => window.__gets), t0), [0, 5, 25]);
    assert.deepEqual(await pilulas(page), ["Atualizado"]);
  } finally { await page.context().close(); }
});

// L: botão A, POST 402 e o GET seguinte preso; o prazo (60 s) devolve o botão.
// O botão B é clicado e o POST dele fica em voo. O GET de A solta: o botão
// continua desabilitado enquanto B não termina.
test("L: o fim de um Atualizar velho não reabilita o botão com outro em voo", async () => {
  const [getA, soltarA] = trava();
  let posts = 0;
  const page = await abrir(VP, (n) => (n === 2 ? { segurar: getA, conexoes: [NU("updated")] } : { conexoes: [NU("updated")] }), {
    rotas: (p) => p.route("**/open-finance/1/refresh*", (r) => (++posts === 1 ? r.fulfill(json({ detail: "x" }, 402)) : undefined)) });
  try {
    await page.click("#of-refresh-btn");
    await andar(page, 61_000, 1000, { preso: true });
    assert.deepEqual(await botao(page), [false, "↻ Atualizar"], "o prazo não devolveu o botão");
    await page.click("#of-refresh-btn");
    await andar(page, 2000, 1000, { preso: true });
    soltarA();
    await andar(page, 3000, 1000, { preso: true });
    assert.equal(posts, 2);
    assert.deepEqual(await botao(page), [true, "↻ Atualizando…"], "o fim de A reabilitou o botão com B em voo");
    await andar(page, 60_000, 1000, { preso: true });   // prazo de B: volta
    assert.deepEqual(await botao(page), [false, "↻ Atualizar"]);
  } finally { await page.context().close(); }
});

// L-PTR (positivo, t8-L do Tester r8): botão com 402 e GET preso para sempre;
// um PTR aos 10 s termina OK. O `fim` do PTR, o mais recente, restaura o botão.
test("L-PTR: PTR mais recente sobre o botão não deixa o botão preso", async () => {
  let posts = 0;
  const page = await abrir(VP, (k) => (k === 2 ? { segurar: NUNCA } : { conexoes: [NU("updated")] }), {
    rotas: (p) => p.route("**/open-finance/1/refresh*", (r) => (++posts === 1
      ? r.fulfill(json({ detail: "x" }, 402)) : r.fulfill(corpo([NU("updated")])))) });
  try {
    await page.click("#of-refresh-btn");
    await andar(page, 10_000, 1000, { preso: true });
    await page.evaluate(() => { window.__p2 = window.PBRefresh().then(() => "ok", () => "erro"); });
    await andar(page, 3000, 1000, { preso: true });
    assert.equal(await page.evaluate(() => window.__p2), "ok");
    assert.deepEqual(await botao(page), [false, "↻ Atualizar"], "o botão ficou preso depois do PTR");
    await andar(page, 5 * MIN, 1000, { preso: true });
    assert.deepEqual(await botao(page), [false, "↻ Atualizar"]);
  } finally { await page.context().close(); }
});
