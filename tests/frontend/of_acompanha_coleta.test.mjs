/**
 * Onda 5, PR-E (F1): com uma conexão em "Atualizando…", os Ajustes releem o
 * snapshot sozinhos (frontend/of-status-poll.js). O contrato é a tabela
 * estados × eventos de docs/open_finance_estados.md §2.4; cada caso abaixo é
 * uma linha dela, em 1440×900 e 390×844.
 *
 * Harness (relógio parado, contagem de GETs): tests/frontend/_of_coleta.mjs.
 * O Atualizar com `still_updating`, a seção visível, o foco e a corrida do
 * botão estão em of_acompanha_atualizar.test.mjs.
 *
 * Controles (CLAUDE.md §3), medidos com mutação do código (Coder r1 e Manager
 * r1, 2026-10-08):
 *  - sem `PBColetaOF.observar` → T1 fica parado em "Atualizando…";
 *  - sem o `++_dataGen` do onConnected → T10 repinta a lista velha;
 *  - sem o teto no `tique` → T2;
 *  - sem a checagem de `document.hidden` (`naTela`) → T3;
 *  - sem `PBColetaOF.parar()` no ramo 402 do `loadData` → T5;
 *  - `novoCiclo(data)` sem o corpo do POST → T7, T12c (e U1, no outro arquivo);
 *  - sem a leitura fora de ciclo no `retomar` → T11 e T12;
 *  - sem `esgotados` → T12.
 * O T4 é o positivo: conexão em estado final não dispara leitura nenhuma.
 * Os controles dos casos novos (B1, B1-P, O1, R3, U1d, U1d-teto) estão no
 * cabeçalho dos arquivos deles.
 *
 * Rodar: node --test tests/frontend/of_acompanha_coleta.test.mjs
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { VIEWPORTS, MIN, json, sleep, conn, NU, abrir, assentar, andar, gets, pilulas, agora } from "./_of_coleta.mjs";

for (const vp of VIEWPORTS) {
  const em = `${vp.width}×${vp.height}`;

  test(`T1 ${em}: "Atualizando…" vira "Atualizado" sozinho e o acompanhamento para`, async () => {
    const page = await abrir(vp, (n) => ({ conexoes: [NU(n === 1 ? "updating" : "updated")] }));
    try {
      assert.deepEqual(await pilulas(page), ["Atualizando…"]);
      await andar(page, 5000);
      assert.deepEqual(await pilulas(page), ["Atualizado"], "o card não mudou sozinho (F1)");
      await andar(page, 30 * MIN, MIN);
      assert.deepEqual(await gets(page), [0, 5], "leu depois do estado final");
    } finally { await page.context().close(); }
  });

  test(`T2 ${em}: cadência 5/10/20/40 s e depois 60 s, teto de 30 min`, async () => {
    const page = await abrir(vp, () => ({ conexoes: [NU("updating")] }));
    try {
      await andar(page, 75_000);           // passo de 1 s até a cadência virar 60 s
      await andar(page, 60 * MIN, MIN);    // passa do teto com folga
      const esperado = [0, 5, 15, 35, 75];
      for (let t = 135; t < 1800; t += 60) esperado.push(t);
      // Medido em 2026-10-08: 32 leituras além do boot; a última em 1755 s.
      assert.deepEqual(await gets(page), esperado);
    } finally { await page.context().close(); }
  });

  test(`T2b ${em}: nunca dois GETs do snapshot ao mesmo tempo`, async () => {
    // Cada resposta fica presa 400 ms de tempo REAL enquanto o relógio anda:
    // com `setInterval` (ou um timer armado antes da resposta) haveria sobreposição.
    const page = await abrir(vp, (n) => ({ conexoes: [NU("updating")], segurar: n > 1 ? sleep(400) : null }));
    try {
      await andar(page, 3 * MIN, 1000, { preso: true });
      assert.equal(page.__estado.maxEmVoo, 1, "dois GETs do snapshot em voo juntos");
      assert.ok((await gets(page)).length >= 3, "o teste não exercitou leitura nenhuma");
    } finally { await page.context().close(); }
  });

  test(`T3 ${em}: aba oculta não pede nada; ao voltar, 1 GET e a cadência segue`, async () => {
    const page = await abrir(vp, () => ({ conexoes: [NU("updating")] }));
    try {
      await page.evaluate(() => window.__ocultar(true));
      await andar(page, 10 * MIN, 5000);
      assert.deepEqual(await gets(page), [0], "pediu com a aba oculta");
      await page.evaluate(() => window.__ocultar(false));
      await sleep(150);                      // sem andar o relógio
      assert.deepEqual(await gets(page), [0, 600], "a volta ao foco não releu na hora");
      await andar(page, 10_000);
      assert.deepEqual(await gets(page), [0, 600, 610], "a cadência não seguiu depois da volta");
    } finally { await page.context().close(); }
  });

  test(`T4 ${em} (POSITIVO): conexão em estado final não dispara leitura`, async () => {
    for (const s of ["updated", "partial", "error_recoverable", "needs_user_action", "no_accounts"]) {
      const page = await abrir(vp, () => ({ conexoes: [NU(s, s === "updated" ? null : "Algum detalhe")] }));
      try {
        await andar(page, 30 * MIN, MIN);
        assert.deepEqual(await gets(page), [0], `${s}: leu sem coleta em andamento`);
        if (s === "updated") {
          // Tudo "Atualizado": nem a volta ao foco relê (DP3 só vale fora do verde).
          await page.evaluate(() => window.__ocultar(false));
          await sleep(150);
          assert.deepEqual(await gets(page), [0], "voltar ao foco com tudo em dia releu");
        }
      } finally { await page.context().close(); }
    }
  });

  test(`T5 ${em}: falha transitória segue sem apagar a tela; 401 e 402 param`, async () => {
    const roteiro = { 2: { status: 500 }, 3: { abortar: true }, 4: { status: 401 } };
    const page = await abrir(vp, (n) => ({ conexoes: [NU("updating")], ...roteiro[n] }));
    try {
      await andar(page, 5000);
      await andar(page, 10_000);
      assert.deepEqual(await gets(page), [0, 5, 15], "o tique seguinte à falha não aconteceu");
      assert.deepEqual(await pilulas(page), ["Atualizando…"], "a falha apagou o card");
      assert.deepEqual(await page.evaluate(() => window.__toasts), [], "falha do acompanhamento deu toast");
      await andar(page, 20_000);              // 4º GET: 401
      await andar(page, 30 * MIN, MIN);
      assert.deepEqual(await gets(page), [0, 5, 15, 35], "leu de novo depois do 401");
    } finally { await page.context().close(); }

    const p402 = await abrir(vp, (n) => (n === 1 ? { conexoes: [NU("updating")] } : { status: 402 }));
    try {
      await andar(p402, 5000);
      assert.match(await p402.textContent("#connections-list"), /plano/i, "402 não pintou 'sem plano'");
      await andar(p402, 30 * MIN, MIN);
      assert.deepEqual(await gets(p402), [0, 5], "leu de novo depois do 402");
    } finally { await p402.context().close(); }
  });

  test(`T6+T10 ${em}: conectar banco acompanha a conexão nova, e leitura velha não a apaga`, async () => {
    let soltar;
    const presa = new Promise((r) => { soltar = r; });
    const nova = conn(2, "Itaú", "updating", "Ainda não sincronizou");
    let depois = false;   // o servidor depois do POST /pluggy-item
    const page = await abrir(vp, (n) => {
      if (n === 2) return { conexoes: [NU("updating")], segurar: presa };   // lida ANTES do POST
      if (!depois) return { conexoes: [NU("updating")] };
      return { conexoes: [NU("updated"), conn(2, "Itaú", "updated")] };
    }, { rotas: async (p) => {
      await p.addInitScript(() => {
        window.PluggyConnect = function (o) { window.__pluggy = o; this.init = function () {}; };
      });
      await p.route("**/open-finance/1/connectors", (r) => r.fulfill(json({ connectors: [
        { id: 601, name: "Itaú", color: "ec7000", logo: "", inv: false }] })));
      await p.route("**/open-finance/1/connect-token", (r) => r.fulfill(json({ accessToken: "x" })));
      await p.route("**/open-finance/1/pluggy-item", (r) => { depois = true;
        return r.fulfill(json({ ok: true, connections: [NU("updating"), nova], accounts: [], transactions: [] })); });
    } });
    try {
      await andar(page, 5000, 1000, { preso: true });  // GET do tique sai e fica preso
      assert.equal(page.__estado.emVoo, 1);
      await page.click("#connect-btn");
      await page.waitForFunction(() => document.querySelectorAll("#bankpick-list .bank-row").length > 0);
      await page.click('#bankpick-list .bank-row[data-name="Itaú"]');
      await page.click("#bankpick-go");
      await page.waitForFunction(() => !!window.__pluggy);
      await page.evaluate(() => window.__pluggy.onSuccess({ item: { id: "item2" } }));
      await page.waitForFunction(() => document.querySelectorAll("#connections-list .connection-row").length === 2);
      soltar();                                       // a lista velha (só Nubank) chega agora
      await assentar(page);
      assert.deepEqual(await pilulas(page), ["Atualizando…", "Atualizando…"],
                       "a leitura velha repintou por cima da conexão nova (T10)");
      const t = await agora(page);
      await andar(page, 5000);
      const g = await page.evaluate(() => window.__gets);
      assert.equal(g.at(-1) - t, 5000, "o ciclo não recomeçou do onConnected");
      assert.deepEqual(await pilulas(page), ["Atualizado", "Atualizado"], "a conexão nova não foi acompanhada (T6)");
    } finally { await page.context().close(); }
  });

  test(`T7 ${em}: Atualizar no meio do ciclo recomeça a cadência e o tique não lê com o POST em voo`, async () => {
    let soltar;
    const presa = new Promise((r) => { soltar = r; });
    const page = await abrir(vp, () => ({ conexoes: [NU("updating")] }), { rotas: async (p) => {
      await p.route("**/open-finance/1/refresh*", async (r) => {
        await presa;
        return r.fulfill(json({ sync: { ok: true, still_updating: 0, items: [
          { item_id: "item1", institution: "Nubank", state: "updating", reason: null, detail: null }] },
          connections: [NU("updating")], accounts: [], transactions: [] }));
      });
    } });
    try {
      await andar(page, 40_000);
      assert.deepEqual(await gets(page), [0, 5, 15, 35]);
      await page.click("#of-refresh-btn");
      // O tique de 75 s cai com o POST em voo; 55 s fica abaixo do prazo do POST
      // (OF_REFRESH_PRAZO_MS, 60 s), que o abortaria.
      await andar(page, 55_000, 5000, { preso: true });
      assert.deepEqual(await gets(page), [0, 5, 15, 35], "o tique leu por cima do POST /refresh");
      soltar();
      await assentar(page);
      const t = await agora(page);
      await andar(page, 5000);
      const g = await page.evaluate(() => window.__gets);
      assert.equal(g.length, 5, "não leu 5 s depois do Atualizar");
      assert.equal(g.at(-1) - t, 5000, "a cadência não recomeçou do Atualizar");
    } finally { await page.context().close(); }
  });

  test(`T8 ${em}: duas conexões, segue até a última sair de "Atualizando…"`, async () => {
    const page = await abrir(vp, (n) => ({ conexoes: [NU("updated"), conn(2, "Itaú", n < 3 ? "updating" : "updated")] }));
    try {
      await andar(page, 15_000);
      assert.deepEqual(await pilulas(page), ["Atualizado", "Atualizado"]);
      await andar(page, 30 * MIN, MIN);
      assert.deepEqual(await gets(page), [0, 5, 15]);
    } finally { await page.context().close(); }
  });

  test(`T9 ${em}: "Remover todas as conexões" no meio para o acompanhamento`, async () => {
    let removido = false;
    const page = await abrir(vp, () => ({ conexoes: removido ? [] : [NU("updating")] }), { rotas: async (p) => {
      await p.route(/\/open-finance\/1$/, (r) => {
        if (r.request().method() !== "DELETE") return r.fallback();
        removido = true;
        return r.fulfill(json({ ok: true }));
      });
    } });
    try {
      await andar(page, 5000);
      await page.evaluate(() => { window.disconnectAll(); });
      await page.waitForSelector(".pig-modal-btn-destructive", { state: "attached" });
      await page.evaluate(() => document.querySelector(".pig-modal-btn-destructive").click());
      await page.waitForFunction(() => !!document.querySelector("#connections-list .empty-state"));
      const n = (await gets(page)).length;
      await andar(page, 30 * MIN, MIN);
      assert.equal((await gets(page)).length, n, "leu depois de remover as conexões");
    } finally { await page.context().close(); }
  });

  test(`T11 ${em} (DP3=B): volta ao foco com "Autorize no app" relê uma vez e abre o ciclo se virou coleta`, async () => {
    let autorizou = false;
    const page = await abrir(vp, () => ({ conexoes: [autorizou ? NU("updating")
      : NU("needs_user_action", "Autorize o acesso no app do banco")] }));
    try {
      await andar(page, 30 * MIN, MIN);
      assert.deepEqual(await gets(page), [0], "estado final não pode disparar timer");
      autorizou = true;                               // a Bia autorizou no app do banco
      await page.evaluate(() => { window.__ocultar(true); window.__ocultar(false); });
      await sleep(150);
      assert.deepEqual(await gets(page), [0, 1800], "a volta ao foco não releu");
      assert.deepEqual(await pilulas(page), ["Atualizando…"]);
      await andar(page, 5000);
      assert.deepEqual(await gets(page), [0, 1800, 1805], "a coleta nova não abriu ciclo");
    } finally { await page.context().close(); }
  });

  test(`T12 ${em} (DP2=A): ciclo esgotado relê uma vez na volta ao foco e não renasce; conexão nova em coleta abre`, async () => {
    let itau = "updated";
    const page = await abrir(vp, () => ({ conexoes: [NU("updating"), conn(2, "Itaú", itau)] }));
    try {
      await andar(page, 75_000);
      await andar(page, 60 * MIN, MIN);
      const esgotou = (await gets(page)).length;
      await page.evaluate(() => { window.__ocultar(true); window.__ocultar(false); });
      await sleep(150);
      assert.equal((await gets(page)).length, esgotou + 1, "a volta ao foco não releu uma vez");
      await andar(page, 30 * MIN, MIN);
      assert.equal((await gets(page)).length, esgotou + 1, "o ciclo esgotado renasceu pela mesma coleta");

      // Itaú estava "Atualizado" e agora coleta: é outra coleta, o ciclo abre.
      itau = "updating";
      await page.evaluate(() => { window.__ocultar(true); window.__ocultar(false); });
      await sleep(150);
      await andar(page, 5000);
      assert.equal((await gets(page)).length, esgotou + 3, "conexão que saiu do final para coleta não abriu ciclo");
    } finally { await page.context().close(); }
  });

  test(`T12c ${em}: ciclo esgotado reabre pelo Atualizar (evento novo)`, async () => {
    const page = await abrir(vp, () => ({ conexoes: [NU("updating")] }), { rotas: async (p) => {
      await p.route("**/open-finance/1/refresh*", (r) => r.fulfill(json({ sync: { ok: true, still_updating: 1, items: [] },
        connections: [NU("updating")], accounts: [], transactions: [] })));
    } });
    try {
      await andar(page, 75_000);
      await andar(page, 60 * MIN, MIN);
      const esgotou = (await gets(page)).length;
      await page.click("#of-refresh-btn");
      await assentar(page);
      const t = await agora(page);
      await andar(page, 5000);
      const g = await page.evaluate(() => window.__gets);
      assert.equal(g.length, esgotou + 1, "o Atualizar não reabriu o ciclo esgotado");
      assert.equal(g.at(-1) - t, 5000);
    } finally { await page.context().close(); }
  });
}
