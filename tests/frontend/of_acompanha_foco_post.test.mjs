/**
 * Onda 5, PR-E, B1 do Manager r1: volta ao foco (ou à seção) com o POST
 * /refresh em voo. A leitura fica pendente e o fim do POST a cumpre
 * (`PBColetaOF.fimDoAtualizar`, no `finally` do refreshOpenFinance). Células
 * #31–#37 de .time-dev/of-onda5-e/maquina-estados.md; contrato em
 * docs/open_finance_estados.md §2.4. Só em 1440×900: o módulo não depende de
 * layout e cada caso anda até 10 min de relógio falso. Harness:
 * tests/frontend/_of_coleta.mjs.
 *
 * Controles (CLAUDE.md §3), medidos por mutação:
 *  - falha (3 portas) e B1-P: sem `PBColetaOF.fimDoAtualizar()` no `finally`
 *    → 0 GET no fim do POST e o ciclo trava (o repro do Manager).
 *  - 402 (3 portas): sem `PBColetaOF.parar()` no ramo 402 do `loadData` → lê
 *    por 10 min. É o positivo: o `fimDoAtualizar` não ressuscita leitura.
 *  - ok (3 portas) e B1-P positivo: sem `pendente = false` no `novoCiclo` →
 *    GET extra no fim do POST (a pintura do POST já é a leitura).
 *  - F1 (R3-espelho e A9): a Regra 3 antiga (`observar(conns)` no
 *    `novoCiclo`) → a lista do POST desliga o ciclo e a tela fica presa em
 *    "Atualizando…". O positivo é o R3 (of_acompanha_atualizar): ela ainda liga.
 *  - F2: sem `signal: prazo.signal` no fetch do refreshOpenFinance → o POST
 *    que nunca responde segura `_ofRefreshEmVoo` e nada lê em 10 min.
 *  - R6-1 (ok, 402): `ocupado` sem a validade (`_ofRefreshEmVoo > 0` só) →
 *    o GET preso do PTR segura a trava e nada lê. R6-1 pendência: sem o
 *    `fimDoAtualizar()` no fim do prazo (`_ofRefreshVigente`) → 0 GET.
 *  - R6-9: sem o `setTimeout(PBColetaOF.releitura, …)` no `catch` → nenhuma
 *    leitura depois do abort. O positivo é o próprio R6-9: exatamente 1, e
 *    nenhuma depois. Cancelamento: sem o `clearTimeout(_ofReleitura)` no
 *    início do Atualizar → a releitura sai depois do 2º Atualizar.
 *
 * Rodar: node --test tests/frontend/of_acompanha_foco_post.test.mjs
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { MIN, json, sleep, NU, abrir, assentar, andar, gets, pilulas, agora } from "./_of_coleta.mjs";

const VP = { width: 1440, height: 900 };
const OK = { sync: { ok: true, still_updating: 0, items: [
  { item_id: "item1", institution: "Nubank", state: "updating", reason: null, detail: null }] } };

// POST /refresh preso até `ctl.soltar()`; depois responde conforme `desfecho`.
// No 402 o GET seguinte do `loadData` também responde 402 (`ctl.e402`).
const refreshPreso = (desfecho, conexoes) => {
  const ctl = { e402: false };
  const presa = new Promise((r) => { ctl.soltar = r; });
  ctl.rotas = (p) => p.route("**/open-finance/1/refresh*", async (r) => {
    await presa;
    if (desfecho === "falha") return r.fulfill(json({ detail: "x" }, 500));
    if (desfecho === "402") { ctl.e402 = true; return r.fulfill(json({ detail: "sem plano" }, 402)); }
    return r.fulfill(json({ ...OK, connections: conexoes(), accounts: [], transactions: [] }));
  });
  return ctl;
};

const PORTAS = {
  oculta: { sair: (p) => p.evaluate(() => { document.getElementById("of-refresh-btn").click(); window.__ocultar(true); }),
            voltar: (p) => p.evaluate(() => window.__ocultar(false)) },
  secao: { sair: (p) => p.evaluate(() => { document.getElementById("of-refresh-btn").click(); window.showSettingsSection("security"); }),
           voltar: (p) => p.evaluate(() => window.showSettingsSection("open-finance")) },
  ptr: { sair: (p) => p.evaluate(() => { window.__ptr = window.PBRefresh().then(() => "ok", () => "erro"); window.__ocultar(true); }),
         voltar: (p) => p.evaluate(() => window.__ocultar(false)) },
};

for (const [porta, { sair, voltar }] of Object.entries(PORTAS)) {
  for (const desfecho of ["falha", "402", "ok"]) {
    test(`B1 ${porta} × ${desfecho}: volta com o POST /refresh em voo não trava o acompanhamento`, async () => {
      const ctl = refreshPreso(desfecho, () => [NU("updating")]);
      const page = await abrir(VP, () => (ctl.e402 ? { status: 402 } : { conexoes: [NU("updating")] }),
                               { rotas: ctl.rotas });
      try {
        await andar(page, 5000);                       // ciclo em A
        const n = (await gets(page)).length;
        await sair(page);
        await andar(page, 15_000, 1000, { preso: true });   // o tique cai fora da tela
        await voltar(page);
        await sleep(150);
        assert.equal((await gets(page)).length, n, "leu por cima do POST /refresh em voo");
        ctl.soltar();
        await assentar(page);
        const t = await agora(page);
        const fim = (await gets(page)).length - n;
        if (desfecho === "falha") {
          assert.equal(fim, 1, "o fim do POST que falhou não cumpriu a leitura da volta (B1)");
          await andar(page, 10 * MIN, MIN);
          assert.ok((await gets(page)).length - n > 3, "a cadência não seguiu depois da falha do POST");
        } else if (desfecho === "402") {
          assert.equal(fim, 1, "o 402 não leu o snapshot pelo loadData");   // botão e PTR
          await andar(page, 10 * MIN, MIN);
          assert.equal((await gets(page)).length - n, 1, "leu depois do 402");
          assert.match(await page.textContent("#connections-list"), /plano/i, "402 não pintou 'sem plano'");
        } else {
          assert.equal(fim, porta === "ptr" ? 1 : 0, "GET extra no fim do POST OK (a pintura dele é a leitura)");
          await andar(page, 5000);
          const g = await page.evaluate(() => window.__gets);
          assert.equal(g.length - n, fim + 1, "a cadência não seguiu depois do POST OK");
          assert.equal(g.at(-1) - t, 5000, "a cadência não conta do Atualizar");
        }
        if (porta === "ptr") assert.equal(await page.evaluate(() => window.__ptr), desfecho === "falha" ? "erro" : "ok");
      } finally { await page.context().close(); }
    });
  }
}

for (const desfecho of ["falha", "ok"]) {
  test(`B1-P ${desfecho}: fora de ciclo, a volta ao foco durante o POST ${desfecho === "ok" ? "é substituída pela pintura do POST (POSITIVO)" : "lê no fim do POST"}`, async () => {
    let autorizou = false;
    const espera = NU("needs_user_action", "Autorize o acesso no app do banco");
    const ctl = refreshPreso(desfecho, () => [espera]);
    const page = await abrir(VP, () => ({ conexoes: [autorizou ? NU("updating") : espera] }), { rotas: ctl.rotas });
    try {
      await page.click("#of-refresh-btn");
      await page.evaluate(() => { window.__ocultar(true); window.__ocultar(false); });
      await sleep(150);
      assert.deepEqual(await gets(page), [0], "leu por cima do POST /refresh em voo");
      autorizou = true;                              // a Bia autorizou no app do banco
      ctl.soltar();
      await assentar(page);
      if (desfecho === "ok") {
        assert.deepEqual(await gets(page), [0], "GET extra no fim do POST OK");
        return;
      }
      assert.equal((await gets(page)).length, 2, "a leitura da volta ao foco se perdeu com o POST que falhou (B1-P)");
      assert.deepEqual(await pilulas(page), ["Atualizando…"]);
      await andar(page, 5000);
      assert.equal((await gets(page)).length, 3, "a coleta nova não abriu ciclo");
    } finally { await page.context().close(); }
  });
}

// F1 (Tester r5): a lista do POST /refresh só LIGA o acompanhamento; desligar é
// do que a tela pintou. PTR com o GET seguinte (o do `loadData`) falhando, com a
// tela ainda em "Atualizando…": o ciclo segue e a tela relê sozinha.
const VARIANTES_F1 = {
  "R3-espelho (POST diz Atualizado)": [NU("updated")],
  "A9 lista vazia": [],
  "A9 sem a conexão da tela": [{ ...NU("updated"), id: 9, provider_item_id: "item9" }],
};
for (const [nome, conexoes] of Object.entries(VARIANTES_F1)) {
  test(`F1 ${nome}: GET do PTR falha e a lista do POST não desliga o acompanhamento`, async () => {
    let falhar = false, terminou = false;
    const page = await abrir(VP, () => (falhar ? (falhar = false, { status: 500 })
                                               : { conexoes: [NU(terminou ? "updated" : "updating")] }), {
      rotas: (p) => p.route("**/open-finance/1/refresh*", (r) => r.fulfill(json({ ok: true,
        sync: { ok: true, still_updating: 0, items: [] }, connections: conexoes, accounts: [], transactions: [] }))) });
    try {
      await andar(page, 5000);                       // ciclo em A
      falhar = true; terminou = true;                // a coleta acabou durante o POST
      assert.equal(await page.evaluate(() => window.PBRefresh().then(() => "ok", () => "erro")), "erro");
      await assentar(page);
      assert.deepEqual(await pilulas(page), ["Atualizando…"], "o GET do PTR não falhou");
      await andar(page, 5000);
      assert.deepEqual(await pilulas(page), ["Atualizado"], "a lista do POST desligou o ciclo e a tela ficou presa (F1)");
    } finally { await page.context().close(); }
  });
}

// F2 (Tester r5, A2): o POST /refresh que nunca assenta não pode segurar
// `_ofRefreshEmVoo` para sempre. O prazo (OF_REFRESH_PRAZO_MS) aborta e o
// `finally` roda; o PTR seguinte OK deixa o acompanhamento ler.
test("F2 PTR que nunca responde: o prazo solta o acompanhamento", async () => {
  let n = 0;
  const page = await abrir(VP, () => ({ conexoes: [NU("updating")] }), {
    rotas: (p) => p.route("**/open-finance/1/refresh*", async (r) => {
      if (n++ === 0) return;                         // o 1º POST nunca responde
      await r.fulfill(json({ ...OK, connections: [NU("updating")], accounts: [], transactions: [] }));
    }) });
  try {
    await andar(page, 5000);
    await page.evaluate(() => { window.__p1 = window.PBRefresh().then(() => "ok", () => "erro"); });
    await andar(page, 13_000, 1000, { preso: true });
    await page.evaluate(() => { window.__p2 = window.PBRefresh().then(() => "ok", () => "erro"); });
    await sleep(300);
    const g0 = (await gets(page)).length;
    await andar(page, 10 * MIN, 5000, { preso: true });
    assert.ok((await gets(page)).length - g0 > 3, "o POST preso segurou o acompanhamento (F2)");
    assert.equal(await page.evaluate(() => window.__p1), "erro", "o 1º PTR não terminou pelo prazo");
    assert.equal(await page.evaluate(() => _ofRefreshEmVoo), 0);
  } finally { await page.context().close(); }
});

// R6-1 (Tester r6): o prazo do F2 só cobria o POST. No PTR (e no ramo 402) o
// `loadData` faz um GET depois do POST; se ele nunca assenta, o `finally` não
// roda e `_ofRefreshEmVoo` fica em 1. A trava (`ocupado`) vence no prazo do
// Atualizar e a cadência volta a ler; um 2º PTR OK não fica preso a ela.
for (const desfecho of ["ok", "402"]) {
  test(`R6-1 ${desfecho}: GET do PTR que nunca assenta não trava o acompanhamento`, async () => {
    let ja402 = false;
    const page = await abrir(VP, (n) => (n === 3 ? { segurar: new Promise(() => {}) } : { conexoes: [NU("updating")] }), {
      rotas: (p) => p.route("**/open-finance/1/refresh*", (r) => (desfecho === "402" && !ja402
        ? (ja402 = true, r.fulfill(json({ detail: "sem plano" }, 402)))
        : r.fulfill(json({ ...OK, connections: [NU("updating")], accounts: [], transactions: [] })))) });
    try {
      await andar(page, 5000);                       // ciclo em A
      const n0 = (await gets(page)).length;
      await page.evaluate(() => { window.PBRefresh().catch(() => {}); });
      await andar(page, 59_000, 1000, { preso: true });
      assert.equal((await gets(page)).length - n0, 1, "leu por cima do Atualizar dentro do prazo");   // só o GET preso
      await andar(page, 65_000, 5000, { preso: true });
      const n1 = (await gets(page)).length;
      assert.ok(n1 - n0 > 1, "o GET preso do PTR segurou o acompanhamento depois do prazo (R6-1)");
      await page.evaluate(() => { window.__p2 = window.PBRefresh().then(() => "ok", () => "erro"); });
      await andar(page, 10 * MIN, 5000, { preso: true });
      assert.equal(await page.evaluate(() => window.__p2), "ok");
      assert.ok((await gets(page)).length - n1 > 4, "o 2º PTR deixou a trava presa");
    } finally { await page.context().close(); }
  });
}

// R6-1 pendência: o ciclo está em O (aba oculta, sem timer) e a volta ao foco
// cai com o GET do PTR preso: a leitura fica pendente. O `finally` nunca roda,
// então quem a cumpre é o fim do prazo da trava, exatamente aos 60 s.
test("R6-1 pendência: volta ao foco com o GET do PTR preso lê no fim do prazo", async () => {
  const page = await abrir(VP, (n) => (n === 3 ? { segurar: new Promise(() => {}) } : { conexoes: [NU("updating")] }), {
    rotas: (p) => p.route("**/open-finance/1/refresh*", (r) =>
      r.fulfill(json({ ...OK, connections: [NU("updating")], accounts: [], transactions: [] }))) });
  try {
    await andar(page, 5000);
    const t0 = await agora(page);
    await page.evaluate(() => { window.PBRefresh().catch(() => {}); window.__ocultar(true); });
    await andar(page, 20_000, 1000, { preso: true });   // o tique cai com a aba oculta: O
    await page.evaluate(() => window.__ocultar(false));
    await andar(page, 44_000, 1000, { preso: true });
    const g = await page.evaluate(() => window.__gets);
    assert.equal(g.length, 4, "a volta ao foco pendente não foi cumprida no fim do prazo");
    assert.equal(g.at(-1) - t0, 60_000, "a pendência não foi cumprida no fim do prazo");
  } finally { await page.context().close(); }
});

// R6-9 (Tester r6): o abort pelo prazo não cancela o servidor. Com tudo
// "Atualizado" (sem ciclo), o acompanhamento relê UMA vez 30 s depois, e
// nenhuma depois; o toast de erro segue o de sempre.
const presoParaSempre = (p) => p.route("**/open-finance/1/refresh*", () => {});
test("R6-9 abort com tudo Atualizado: UMA releitura 30 s depois, e nenhuma depois", async () => {
  const page = await abrir(VP, () => ({ conexoes: [NU("updated")] }), { rotas: presoParaSempre });
  try {
    await page.click("#of-refresh-btn");
    const t0 = await agora(page);
    await andar(page, 61_000, 1000, { preso: true });
    assert.deepEqual((await page.evaluate(() => window.__toasts)).at(-1), ["Erro ao atualizar", "error"]);
    await andar(page, 28_000, 1000);
    assert.deepEqual(await gets(page), [0], "leu antes da releitura");
    await andar(page, 2000, 1000);
    const g = await page.evaluate(() => window.__gets);
    assert.equal(g.length, 2, "o abort não deixou releitura (R6-9)");
    assert.equal(g.at(-1) - t0, 90_000, "a releitura não veio 30 s depois do abort");
    await page.evaluate(() => { window.__ocultar(true); window.__ocultar(false); });
    await andar(page, 10 * MIN, MIN);
    assert.equal((await gets(page)).length, 2, "releu mais de uma vez");
  } finally { await page.context().close(); }
});

test("R6-9 outro Atualizar antes da releitura a cancela", async () => {
  let n = 0;
  const page = await abrir(VP, () => ({ conexoes: [NU("updated")] }), {
    rotas: (p) => p.route("**/open-finance/1/refresh*", (r) => (n++ === 0 ? undefined
      : r.fulfill(json({ sync: { ok: true, still_updating: 0, items: [] }, connections: [NU("updated")], accounts: [], transactions: [] })))) });
  try {
    await page.click("#of-refresh-btn");
    await andar(page, 70_000, 1000, { preso: true });   // abortado aos 60 s
    await page.click("#of-refresh-btn");                // responde na hora e repinta
    await assentar(page);
    await andar(page, 10 * MIN, MIN);
    assert.deepEqual(await gets(page), [0], "a releitura do Atualizar abortado saiu depois de outro Atualizar");
  } finally { await page.context().close(); }
});
