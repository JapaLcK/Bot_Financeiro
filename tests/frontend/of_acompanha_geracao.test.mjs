/**
 * Onda 5, PR-E, Tester r7: a trava (`_ofRefreshVigente`, o `ocupado` do
 * acompanhamento), o prazo (`prazoTimer`) e a releitura (`_ofReleitura`) do
 * `settings.html` são do Atualizar MAIS RECENTE (`_ofRefreshGen`). Contrato em
 * docs/open_finance_estados.md §2.4. Só em 1440×900: o módulo não depende de
 * layout. Harness: tests/frontend/_of_coleta.mjs.
 *
 * Controles (CLAUDE.md §3), medidos por mutação no settings.html / módulo:
 *  - A e A': sem `clearTimeout(_ofRefreshVigente); _ofRefreshVigente = null;`
 *    no `fim` → a trava do 2º Atualizar, já terminado, segura a cadência e a
 *    volta ao foco até 60 s depois do início dele.
 *  - J (positivo de A): sem `if (gen !== _ofRefreshGen) return;` no `fim` → o
 *    fim do 1º Atualizar solta a trava do 2º, que ainda está em voo.
 *  - B: sem `if (ativo) return;` na `releitura` do of-status-poll.js → GET
 *    fora da cadência aos 90 s.
 *  - D: sem `clearTimeout(_ofReleitura)` no ramo 402 do `loadData` → lê
 *    depois do 402.
 *  - E: sem `&& gen === _ofRefreshGen` no `catch` → a releitura do 1º sai
 *    depois do 2º.
 *  - I: sem o `clearTimeout(prazoTimer)` depois do `resp.json()` → releitura
 *    espúria de um POST que não foi abortado.
 *  - F: com o prazo da trava só soltando a trava (sem o botão) → o botão fica
 *    desabilitado com o GET do 402 preso.
 * Positivos: R6-1 (a trava vale dentro do prazo) e R6-9 (o abort de verdade
 * relê uma vez), em of_acompanha_foco_post.test.mjs, e o J aqui.
 *
 * Rodar: node --test tests/frontend/of_acompanha_geracao.test.mjs
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { MIN, json, sleep, NU, abrir, assentar, andar, gets, agora } from "./_of_coleta.mjs";

const VP = { width: 1440, height: 900 };
const NUNCA = new Promise(() => {});
const SYNC_UPD = { ok: true, still_updating: 0, items: [{ item_id: "item1", institution: "Nubank", state: "updating", reason: null, detail: null }] };
const SYNC_OK = { ok: true, still_updating: 0, items: [] };
const corpo = (conexoes, sync = SYNC_UPD) => json({ ok: true, sync, connections: conexoes, accounts: [], transactions: [] });
const rel = (g, t0) => g.map((t) => (t - t0) / 1000);
const presoParaSempre = (p) => p.route("**/open-finance/1/refresh*", () => {});

// A: o GET do 1º PTR nunca assenta (_ofRefreshEmVoo preso em 1). O 2º PTR
// termina OK e a cadência conta dele: 5 s, 10 s, 20 s.
test("A: Atualizar velho preso não segura a cadência depois do 2º Atualizar OK", async () => {
  const page = await abrir(VP, (n) => (n === 3 ? { segurar: NUNCA } : { conexoes: [NU("updating")] }), {
    rotas: (p) => p.route("**/open-finance/1/refresh*", (r) => r.fulfill(corpo([NU("updating")]))) });
  try {
    await andar(page, 5000);
    await page.evaluate(() => { window.PBRefresh().catch(() => {}); });
    await andar(page, 70_000, 1000, { preso: true });
    await page.evaluate(() => { window.__p2 = window.PBRefresh().then(() => "ok", () => "erro"); });
    await sleep(300);
    const t2 = await agora(page);
    const n2 = (await gets(page)).length;              // inclui o GET do loadData do 2º PTR
    await andar(page, 40_000, 1000, { preso: true });
    assert.equal(await page.evaluate(() => window.__p2), "ok");
    assert.deepEqual(rel((await page.evaluate(() => window.__gets)).slice(n2), t2), [5, 15, 35],
      "a trava do 2º Atualizar, já terminado, segurou a cadência");
  } finally { await page.context().close(); }
});

// A': mesma coisa pela volta ao foco, fora de ciclo (conexão fora do Ok).
// O 1º PTR prende o GET dele (n = 2); o 2º termina OK; a volta lê na hora.
test("A': Atualizar velho preso não segura a volta ao foco depois do 2º Atualizar OK", async () => {
  const page = await abrir(VP, (n) => (n === 2 ? { segurar: NUNCA } : { conexoes: [NU("needs_user_action")] }), {
    rotas: (p) => p.route("**/open-finance/1/refresh*", (r) => r.fulfill(corpo([NU("needs_user_action")], SYNC_OK))) });
  try {
    await page.evaluate(() => { window.PBRefresh().catch(() => {}); });
    await andar(page, 70_000, 1000, { preso: true });
    await page.evaluate(() => { window.__p2 = window.PBRefresh().then(() => "ok", () => "erro"); });
    await andar(page, 3000, 1000, { preso: true });
    assert.equal(await page.evaluate(() => window.__p2), "ok");
    assert.equal(await page.evaluate(() => _ofRefreshEmVoo), 1, "o 1º PTR não ficou preso");
    const n = (await gets(page)).length;
    await page.evaluate(() => { window.__ocultar(true); window.__ocultar(false); });
    await andar(page, 3000, 1000, { preso: true });
    assert.equal((await gets(page)).length - n, 1, "a volta ao foco ficou pendente atrás da trava de um Atualizar já terminado");
  } finally { await page.context().close(); }
});

// J (positivo de A): os dois Atualizar presos; o fim do 1º (abort aos 60 s)
// não solta a trava do 2º, que começou aos 50 s e vence aos 110 s.
test("J: o fim do Atualizar velho não solta a trava do mais recente em voo", async () => {
  const page = await abrir(VP, () => ({ conexoes: [NU("updating")] }), { rotas: presoParaSempre });
  try {
    await andar(page, 5000);
    const t0 = await agora(page);
    await page.evaluate(() => { window.PBRefresh().catch(() => {}); });
    await andar(page, 50_000, 1000, { preso: true });
    await page.evaluate(() => { window.PBRefresh().catch(() => {}); });
    await andar(page, 3 * MIN, 1000, { preso: true });
    const g = rel(await page.evaluate(() => window.__gets), t0);
    assert.ok(!g.some((x) => x > 0 && x < 110), `leu dentro do prazo do 2º Atualizar: ${g}`);
    assert.ok(g.some((x) => x >= 110), "não voltou a ler depois do prazo do 2º");
    assert.equal(await page.evaluate(() => _ofRefreshEmVoo), 0);
  } finally { await page.context().close(); }
});

// B: abort com o ciclo ligado: a releitura não lê (o tique lê) e não duplica.
test("B: releitura com o ciclo ativo não duplica leitura", async () => {
  const page = await abrir(VP, () => ({ conexoes: [NU("updating")] }), { rotas: presoParaSempre });
  try {
    await andar(page, 5000);
    await page.click("#of-refresh-btn");
    const t0 = await agora(page);
    await andar(page, 3 * MIN, 1000, { preso: true });
    assert.deepEqual(rel(await page.evaluate(() => window.__gets), t0), [-5, 0, 60, 80, 120, 180],
      "a releitura leu com o ciclo ativo, fora da cadência");
  } finally { await page.context().close(); }
});

// D: a releitura agendada pelo abort não lê depois de um 402 (parar).
test("D: releitura agendada antes de um 402 não lê depois dele", async () => {
  let plano = true;
  const page = await abrir(VP, () => (plano ? { conexoes: [NU("needs_user_action")] } : { status: 402 }),
                           { rotas: presoParaSempre });
  try {
    await page.click("#of-refresh-btn");
    await andar(page, 65_000, 1000, { preso: true });   // abortado aos 60 s; releitura devida aos 90 s
    plano = false;
    await page.evaluate(() => { window.__ocultar(true); window.__ocultar(false); });   // lê → 402 → parar
    await assentar(page);
    const n = (await gets(page)).length;
    assert.match(await page.textContent("#connections-list"), /plano/i);
    await andar(page, 5 * MIN, 1000);
    assert.equal((await gets(page)).length, n, "leu depois do 402 (a releitura do abort)");
  } finally { await page.context().close(); }
});

// E: o 2º Atualizar começa aos 50 s, ANTES do abort do 1º, e termina OK com
// tudo Atualizado. O abort do 1º (60 s) não agenda releitura: não é o mais recente.
test("E: o abort de um Atualizar que já não é o mais recente não agenda releitura", async () => {
  let n = 0;
  const page = await abrir(VP, () => ({ conexoes: [NU("updated")] }), {
    rotas: (p) => p.route("**/open-finance/1/refresh*", (r) => (n++ === 0 ? undefined : r.fulfill(corpo([NU("updated")], SYNC_OK)))) });
  try {
    await page.evaluate(() => { window.PBRefresh().catch(() => {}); });
    await andar(page, 50_000, 1000, { preso: true });
    await page.evaluate(() => { window.__p2 = window.PBRefresh().then(() => "ok", () => "erro"); });
    await andar(page, 5 * MIN, 1000, { preso: true });
    assert.equal(await page.evaluate(() => window.__p2), "ok");
    assert.equal((await gets(page)).length, 2, "GET extra depois do 2º Atualizar (a releitura do 1º)");
  } finally { await page.context().close(); }
});

// I: PTR com POST OK rápido, veredito de erro e GET do loadData > 60 s. O
// prazo sai quando o POST assenta: nada a abortar, nada a reler.
test("I: POST OK com GET lento não deixa releitura espúria", async () => {
  let soltar; const lento = new Promise((r) => { soltar = r; });
  const ERRO = { ok: true, still_updating: 0, items: [{ item_id: "item1", institution: "Nubank", state: "error_recoverable", reason: "x", detail: "Tentaremos de novo automaticamente" }] };
  const page = await abrir(VP, (k) => (k === 2 ? { segurar: lento, conexoes: [NU("updated")] } : { conexoes: [NU("updated")] }), {
    rotas: (p) => p.route("**/open-finance/1/refresh*", (r) => r.fulfill(corpo([NU("updated")], ERRO))) });
  try {
    await page.evaluate(() => { window.__p = window.PBRefresh().then(() => "ok", () => "erro"); });
    await andar(page, 65_000, 1000, { preso: true });
    soltar();
    await assentar(page);
    await andar(page, 5 * MIN, 1000);
    assert.equal(await page.evaluate(() => window.__p), "erro");
    assert.equal((await gets(page)).length, 2, "releitura espúria depois de um POST que não foi abortado");
  } finally { await page.context().close(); }
});

// F: botão Atualizar, POST 402 e o GET do loadData preso: o botão volta no prazo.
test("F: botão Atualizar com 402 e GET preso volta no prazo", async () => {
  const page = await abrir(VP, (k) => (k >= 2 ? { segurar: NUNCA } : { conexoes: [NU("updated")] }), {
    rotas: (p) => p.route("**/open-finance/1/refresh*", (r) => r.fulfill(json({ detail: "x" }, 402))) });
  try {
    await page.click("#of-refresh-btn");
    await andar(page, 59_000, 1000, { preso: true });
    assert.equal(await page.evaluate(() => document.getElementById("of-refresh-btn").disabled), true, "voltou antes do prazo");
    await andar(page, 2000, 1000, { preso: true });
    assert.deepEqual(await page.evaluate(() => [document.getElementById("of-refresh-btn").disabled,
      document.getElementById("of-refresh-btn").textContent]), [false, "↻ Atualizar"], "o botão ficou desabilitado");
  } finally { await page.context().close(); }
});
