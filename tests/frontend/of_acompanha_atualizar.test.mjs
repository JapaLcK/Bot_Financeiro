/**
 * Onda 5, PR-E, rodada 2 (achados do Tester r1): o acompanhamento da coleta
 * diante do Atualizar, da seção visível e do resto da página. Em 1440×900 e
 * 390×844. Harness: tests/frontend/_of_coleta.mjs; o ciclo em si está em
 * of_acompanha_coleta.test.mjs.
 *
 * Controles (CLAUDE.md §3), medidos por mutação e registrados no relato do PR:
 *  - U1: `PBColetaOF.novoCiclo()` sem o corpo do POST → 0 GET em 10 min (era
 *    o A9 do Tester: o toast prometia "aparecem aqui sozinhos" e nada relia);
 *    o `last_sync_at` fora da condição de parada → lê depois do sync novo.
 *  - U1c: sem o estado final fora de "updated"/"updating" na condição de
 *    saída do `aguardando` → segue lendo até o teto.
 *  - U1b: o teto sem `aguardando.clear()` → a leitura da volta ao foco religa.
 *  - U3: sem o `PBColetaOF.retomar()` do showSettingsSection → não relê.
 *  - U2: `caixinhas: false` tirado do `reler` → o foco sai do <select>.
 *  - U3: `visivel` tirado do `configurar` → GETs com a aba Segurança na tela.
 *  - U4: `++_dataGen` tirado do ramo do botão → a leitura velha repinta.
 *  - U1d (partial e no_accounts): a condição de saída antiga (só
 *    "updated"/"updating" seguem) → o card "Dados parciais" sai da espera e o
 *    sync novo nunca é lido; sem "no_accounts" em `SEGUE_ESPERANDO` → o mesmo
 *    com "Sem dados".
 *  - U1d-teto: sem o teto no `tique` → lê para sempre (é o positivo de que A1
 *    tem fim).
 *  - O1-a e O1-b: sem `|| tetoBatido` no `retomar` → a volta ao foco depois
 *    do teto não relê. A 2ª volta ao foco sem leitura é o positivo do consumo.
 *  - R3: sem o `ligar()` da resposta do POST no `novoCiclo` → 0 GET depois
 *    do PTR. O lado "só liga, nunca desliga" está no F1 (of_acompanha_foco_post).
 *  - U5: sem a pendência (`_caixPendente`), o tique não chama o caixinhas →
 *    o card do banco removido fica. U5c (sem ciclo) é o positivo.
 *  - U2 (caixinhas): contadas na rota; sem o `caixinhas: false` → vermelho.
 * Positivos: U1 para quando o `last_sync_at` muda (não lê para sempre); U3
 * volta a ler ao mostrar a seção de Open Finance.
 *
 * Rodar: node --test tests/frontend/of_acompanha_atualizar.test.mjs
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { VIEWPORTS, MIN, json, sleep, conn, NU, abrir, assentar, andar, gets, pilulas, agora } from "./_of_coleta.mjs";

const SYNC_ANTES = "2026-10-08T11:59:00Z";   // o last_sync_at do NU("updated")
const SYNC_DEPOIS = "2026-10-08T12:03:00Z";
const comSync = (last_sync_at) => ({ ...NU("updated"), last_sync_at });
// O POST do Atualizar: o item ainda coleta na Pluggy, mas o card é "Atualizado"
// (item UPDATING com todos os produtos; tests/test_of_health.py).
const refreshAindaColetando = (p, outras = []) => p.route("**/open-finance/1/refresh*", (r) => r.fulfill(json({
  sync: { ok: true, still_updating: 1, items: [
    { item_id: "item1", institution: "Nubank", state: "updated", reason: null, detail: null, still_updating: true }] },
  connections: [comSync(SYNC_ANTES), ...outras], accounts: [], transactions: [] })));
const CAIXINHA = { caixinhas: [{ of_investment_id: 7, name: "Reserva", balance: 10, pocket_id: null }],
                   metas: [{ id: 5, name: "Viagem" }] };
// Remover as conexões com o GET do disconnectAll (n = 2) preso; `estado` é a
// pílula das conexões antes de remover.
const abrirParaRemover = (vp, estado, soltarEm) => {
  const presa = new Promise((r) => { soltarEm.soltar = r; });
  const ctl = { removido: false };
  return abrir(vp, (n) => {
    if (ctl.removido && n === 2) return { conexoes: [], segurar: presa };
    return { conexoes: ctl.removido ? [] : [NU(estado)] };
  }, { rotas: async (p) => {
    p.__ctl = ctl;
    await p.route("**/open-finance/1/caixinhas", (r) => r.fulfill(json(ctl.removido ? { caixinhas: [], metas: [] } : CAIXINHA)));
    await p.route(/\/open-finance\/1$/, (r) => r.request().method() === "DELETE" ? r.fulfill(json({ ok: true })) : r.fallback());
  } });
};
const removerComTique = async (page, soltarEm) => {
  await page.waitForSelector(".caix-select");
  page.__ctl.removido = true;
  await page.evaluate(() => { window.confirmModal = async () => true; window.disconnectAll(); });
  await sleep(200);
  assert.equal(page.__estado.emVoo, 1, "o GET do disconnect não ficou preso");
  await andar(page, 5000, 1000, { preso: true });   // tique do ciclo, se houver: GET n = 3 devolve []
  soltarEm.soltar();
  await assentar(page);
  await sleep(200);
  return page.evaluate(() => document.getElementById("caixinhas-card").style.display);
};
const clicarAtualizar = async (page) => {
  await page.click("#of-refresh-btn");
  await page.waitForFunction(() => !document.querySelector("#of-refresh-btn").disabled);
  await assentar(page);
};
const ocultarEVoltar = async (page) => {
  await page.evaluate(() => { window.__ocultar(true); window.__ocultar(false); });
  await sleep(150);
};

for (const vp of VIEWPORTS) {
  const em = `${vp.width}×${vp.height}`;

  test(`U1 ${em}: Atualizar com still_updating liga o ciclo com o card "Atualizado" e para no sync novo`, async () => {
    let sincronizou = false;
    const page = await abrir(vp, () => ({ conexoes: [comSync(sincronizou ? SYNC_DEPOIS : SYNC_ANTES)] }),
                             { rotas: refreshAindaColetando });
    try {
      await andar(page, 10 * MIN, MIN);
      assert.deepEqual(await gets(page), [0], "estado final leu antes do Atualizar");
      await clicarAtualizar(page);
      const t = await agora(page);
      await andar(page, 15_000);
      const g = await page.evaluate(() => window.__gets);
      assert.deepEqual(g.slice(1).map((x) => x - t), [5000, 15000],
                       "o still_updating do Atualizar não ligou o acompanhamento");
      sincronizou = true;                     // o webhook do fim da coleta sincronizou
      await andar(page, 20_000);
      const n = (await gets(page)).length;
      assert.equal(n, 4, "não leu o snapshot com o sync novo");
      await andar(page, 30 * MIN, MIN);
      assert.equal((await gets(page)).length, n, "leu depois do last_sync_at mudar (POSITIVO: tem de parar)");
    } finally { await page.context().close(); }
  });

  test(`U1c ${em}: Atualizar com still_updating e a coleta termina em "Erro temporário" sem sync novo: para`, async () => {
    let falhou = false;                       // a falha não avança o last_sync_at
    const page = await abrir(vp, () => ({ conexoes: [falhou ? { ...NU("error_recoverable"), last_sync_at: SYNC_ANTES }
                                                            : comSync(SYNC_ANTES)] }),
                             { rotas: refreshAindaColetando });
    try {
      await clicarAtualizar(page);
      falhou = true;
      await andar(page, 5000);
      const n = (await gets(page)).length;
      assert.equal(n, 2, "o still_updating do Atualizar não leu o snapshot");
      assert.deepEqual(await pilulas(page), ["Erro temporário"]);
      await andar(page, 10 * MIN, MIN);
      assert.equal((await gets(page)).length, n, "seguiu lendo depois do estado final de erro");
    } finally { await page.context().close(); }
  });

  // A1 do Tester r2 e do Manager r1. "Dados parciais" e "Sem dados" durante a
  // coleta são a foto anterior; só erro / ação necessária encerram a espera
  // (decisão do dono). Os detalhes são os do backend (pluggy_health.py).
  const FOTO = { partial: "Dados parciais. Cartão desatualizado desde 12/08",
                 no_accounts: "O banco não devolveu contas nem investimentos" };
  const abrirFoto = (estado, terminou) => {
    const foto = { ...NU(estado, FOTO[estado]), last_sync_at: SYNC_ANTES };
    return abrir(vp, () => ({ conexoes: [terminou() ? comSync(SYNC_DEPOIS) : foto] }), {
      rotas: (p) => p.route("**/open-finance/1/refresh*", (r) => r.fulfill(json({
        sync: { ok: false, still_updating: 1, items: [{ item_id: "item1", institution: "Nubank", state: estado,
                                                        reason: null, detail: FOTO[estado], still_updating: true }] },
        connections: [foto], accounts: [], transactions: [] }))) });
  };
  for (const estado of Object.keys(FOTO)) {
    test(`U1d ${em}: Atualizar com still_updating e o card em ${estado} (foto antiga): segue lendo até o sync novo`, async () => {
      let terminou = false;
      const page = await abrirFoto(estado, () => terminou);
      try {
        await clicarAtualizar(page);
        assert.deepEqual(await pilulas(page), [estado === "partial" ? "Dados parciais" : "Sem dados"]);
        terminou = true;                        // o fim da coleta sincronizou
        await andar(page, 5000);
        assert.equal((await gets(page)).length, 2, `o card ${estado} com still_updating não releu o snapshot`);
        assert.deepEqual(await pilulas(page), ["Atualizado"], "o fim da coleta não chegou à tela");
        await andar(page, 10 * MIN, MIN);
        assert.equal((await gets(page)).length, 2, "leu depois do last_sync_at mudar (POSITIVO: tem de parar)");
      } finally { await page.context().close(); }
    });
  }

  test(`U1d-teto ${em}: "Sem dados" com still_updating e sem sync novo lê até o teto e para (A1)`, async () => {
    const page = await abrirFoto("no_accounts", () => false);
    try {
      await clicarAtualizar(page);
      await andar(page, 75_000);
      await andar(page, 60 * MIN, MIN);
      const esgotou = (await gets(page)).length;
      assert.ok(esgotou > 30, `o ciclo não leu até o teto (${esgotou} leituras)`);
      await andar(page, 10 * MIN, MIN);
      assert.equal((await gets(page)).length, esgotou, "leu depois do teto (A1 tem fim)");
    } finally { await page.context().close(); }
  });

  // O1 (Manager r1, DP2 literal): o ciclo do still_updating com tudo
  // "Atualizado" bateu o teto; a volta ao foco relê UMA vez, e não renasce.
  const esgotarSoAtualizado = async (page, oculta) => {
    await clicarAtualizar(page);
    await andar(page, 75_000);
    if (oculta) {                           // O1-b: oculta antes do teto
      await andar(page, 9 * MIN, MIN);
      await page.evaluate(() => window.__ocultar(true));
      await andar(page, 30 * MIN, MIN);
      return (await gets(page)).length;
    }
    await andar(page, 60 * MIN, MIN);
    const n = (await gets(page)).length;
    assert.ok(n > 30, `o ciclo não chegou ao teto (${n} leituras)`);
    await page.evaluate(() => window.__ocultar(true));
    return n;
  };
  for (const [id, oculta] of [["O1-a", false], ["O1-b", true]]) {
    test(`${id} ${em}: teto com tudo "Atualizado" relê uma vez na volta ao foco e não renasce`, async () => {
      const page = await abrir(vp, () => ({ conexoes: [comSync(SYNC_ANTES)] }), { rotas: refreshAindaColetando });
      try {
        const n = await esgotarSoAtualizado(page, oculta);
        await page.evaluate(() => window.__ocultar(false));
        await sleep(150);
        assert.equal((await gets(page)).length, n + 1, "a volta ao foco depois do teto não releu (O1)");
        await ocultarEVoltar(page);
        assert.equal((await gets(page)).length, n + 1, "a segunda volta ao foco releu (a leitura é uma só)");
        await andar(page, 10 * MIN, MIN);
        assert.equal((await gets(page)).length, n + 1, "o ciclo esgotado renasceu");
      } finally { await page.context().close(); }
    });
  }

  test(`R3 ${em}: PTR com POST OK e o GET seguinte falhando liga o ciclo pela resposta do POST`, async () => {
    const page = await abrir(vp, (n) => (n === 2 ? { status: 500 } : { conexoes: [comSync(SYNC_ANTES)] }),
                             { rotas: refreshAindaColetando });
    try {
      const ptr = await page.evaluate(() => window.PBRefresh().then(() => "ok", () => "erro"));
      await assentar(page);
      assert.equal(ptr, "erro", "o GET do PTR não falhou");
      assert.equal((await gets(page)).length, 2);
      const t = await agora(page);
      await andar(page, 15_000);
      const g = await page.evaluate(() => window.__gets);
      assert.deepEqual(g.slice(2).map((x) => x - t), [5000, 15000], "o still_updating do POST não ligou o ciclo (R3)");
    } finally { await page.context().close(); }
  });

  test(`U5 ${em}: remover as conexões com um tique no meio relê as caixinhas (card do banco removido some)`, async () => {
    // A3 do Tester r2: o tique (`caixinhas: false`) supera o GET do
    // disconnectAll, que sai sem loadCaixinhas; a pendência passa para o tique.
    const soltarEm = {};
    const page = await abrirParaRemover(vp, "updating", soltarEm);
    try {
      const caix = await removerComTique(page, soltarEm);
      assert.equal((await gets(page)).length, 3, "o tique não leu no meio do disconnect");
      assert.equal(caix, "none", "card de caixinhas do banco removido continua na tela");
    } finally { await page.context().close(); }
  });

  test(`U5c ${em}: o mesmo disconnect sem ciclo (card "Atualizado"): o card de caixinhas some`, async () => {
    const soltarEm = {};
    const page = await abrirParaRemover(vp, "updated", soltarEm);
    try {
      const caix = await removerComTique(page, soltarEm);
      assert.equal((await gets(page)).length, 2, "leu sem ciclo");
      assert.equal(caix, "none");
    } finally { await page.context().close(); }
  });

  test(`U1b ${em}: sem sync novo para no teto, e o mesmo still_updating não renasce na volta ao foco`, async () => {
    // O Itaú fora de "Atualizado" faz a volta ao foco reler (DP3); é essa leitura
    // que religaria o ciclo se o teto não esquecesse o still_updating.
    const itau = conn(2, "Itaú", "needs_user_action", "Reautorize o banco");
    const page = await abrir(vp, () => ({ conexoes: [comSync(SYNC_ANTES), itau] }),
                             { rotas: (p) => refreshAindaColetando(p, [itau]) });
    try {
      await clicarAtualizar(page);
      await andar(page, 75_000);
      await andar(page, 60 * MIN, MIN);
      const esgotou = (await gets(page)).length;
      assert.ok(esgotou > 30, `o ciclo não chegou ao teto (${esgotou} leituras)`);
      await ocultarEVoltar(page);
      assert.equal((await gets(page)).length, esgotou + 1, "a volta ao foco não releu uma vez (DP3)");
      await andar(page, 10 * MIN, MIN);
      assert.equal((await gets(page)).length, esgotou + 1, "o ciclo esgotado renasceu (DP2×DP3)");
      await clicarAtualizar(page);           // evento novo: reabre
      await andar(page, 5000);
      assert.equal((await gets(page)).length, esgotou + 2, "o Atualizar não reabriu o ciclo");
    } finally { await page.context().close(); }
  });

  test(`U2 ${em}: o tique não refaz o card de caixinhas nem tira o foco do <select>`, async () => {
    let nCaix = 0;                            // contado na rota: o route.fulfill não entra no performance
    const page = await abrir(vp, () => ({ conexoes: [NU("updating")] }), { rotas: async (p) => {
      await p.route("**/open-finance/1/caixinhas", (r) => { nCaix += 1; return r.fulfill(json(CAIXINHA)); });
    } });
    try {
      await page.waitForSelector(".caix-select");
      const caixAntes = nCaix;
      await page.focus(".caix-select");
      await andar(page, 15_000);
      assert.deepEqual(await gets(page), [0, 5, 15], "o tique não leu");
      assert.equal(await page.evaluate(() => document.activeElement.className), "caix-select",
                   "o tique tirou o foco do <select> da caixinha");
      assert.equal(nCaix, caixAntes, "o tique releu as caixinhas");
    } finally { await page.context().close(); }
  });

  test(`U3 ${em}: fora da seção de Open Finance não lê; voltar para ela relê e segue`, async () => {
    const page = await abrir(vp, () => ({ conexoes: [NU("updating")] }), { url: "security" });
    try {
      await andar(page, 10 * MIN, 5000);
      await ocultarEVoltar(page);            // volta ao foco na aba Segurança: também não
      assert.deepEqual(await gets(page), [0], "leu com a seção de Open Finance fora da tela");
      await page.evaluate(() => window.showSettingsSection("open-finance"));
      await sleep(150);
      assert.deepEqual(await gets(page), [0, 600], "mostrar a seção de Open Finance não releu");
      await andar(page, 10_000);
      assert.deepEqual(await gets(page), [0, 600, 610], "a cadência não seguiu na seção");
      await page.evaluate(() => window.showSettingsSection("security"));
      await andar(page, 5 * MIN, 5000);
      assert.deepEqual(await gets(page), [0, 600, 610], "sair da seção não pausou");
    } finally { await page.context().close(); }
  });

  test(`U4 ${em}: leitura do acompanhamento presa não repinta por cima do Atualizar`, async () => {
    let soltar;
    const presa = new Promise((r) => { soltar = r; });
    const page = await abrir(vp, (n) => (n === 2 ? { conexoes: [NU("updating")], segurar: presa }
                                                  : { conexoes: [NU(n === 1 ? "updating" : "updated")] }),
      { rotas: (p) => p.route("**/open-finance/1/refresh*", (r) => r.fulfill(json({
        sync: { ok: true, still_updating: 0, items: [{ item_id: "item1", state: "updated", institution: "Nubank" }] },
        connections: [NU("updated")], accounts: [], transactions: [] }))) });
    try {
      await andar(page, 5000, 1000, { preso: true });   // o GET do tique sai e fica preso
      assert.equal(page.__estado.emVoo, 1);
      await page.click("#of-refresh-btn");
      await page.waitForFunction(() => !document.querySelector("#of-refresh-btn").disabled);
      assert.deepEqual(await pilulas(page), ["Atualizado"]);
      soltar();                                          // a leitura velha chega agora
      await assentar(page);
      assert.deepEqual(await pilulas(page), ["Atualizado"], "a leitura velha repintou \"Atualizando…\"");
    } finally { await page.context().close(); }
  });
}
