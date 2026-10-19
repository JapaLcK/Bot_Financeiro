/**
 * O "Atualizar agora" do aviso de banco da pilha do /painel, #1076 PR 2 — a RODADA: o que ela julga (W10-W14), as leituras que a classificam e a disputam (W15-W16),
 * a vista e a tela (W17-W20) e as medidas (V1). O caminho do POST (W1-W9): `dashboard_v2_atualizar_banco.test.mjs`. Harness: _atualizar_banco.mjs.
 * Rodar:  npm run test:frontend   (artefato: `npm --prefix webapp run build`)
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { setTimeout as dorme } from "node:timers/promises";
import { chromium } from "playwright";
import { exigeArtefatoEmDia } from "./_painel.mjs";
import { LIMITE, con, item, refreshDe } from "./_bancos.mjs";
import { AVISO, AINDA, DITO, FALHA, REGIAO, abrirBanco, assentar, ate, avancar, congelar, falado, focar, gets, instantePost, marca, pedidos, porta, texto } from "./_atualizar_banco.mjs";

let browser;
before(async () => { exigeArtefatoEmDia(); browser = await chromium.launch(); });
after(() => browser?.close());

const abre = (opts) => abrirBanco(browser, opts);
const UPDATING = () => con(11, "Nubank", "updating", "Atualizando…", "2026-10-02T14:00:00Z");
const NECESSARIA = (detalhe) => con(11, "Nubank", "needs_user_action", "Ação necessária", undefined, detalhe);
const BOTAO = "#avisos-topo .pilha-corpo button";

test("W10 (lição 3): o POST traz um item 'updating' com motivo de FALHA (read_failed): é resultado com a frase de falha, sem 'Atualizar agora' e sem acompanhar", async () => {
  const { ctx, page, abrir, botao, s } = await abre();
  s.refresh = (r) => r.fulfill({ json: refreshDe([item("item-11", "updating", false, null, "read_failed")], []) });
  await abrir();
  const base = await marca(page);
  await congelar(page);
  await botao.click();
  await page.waitForFunction((b) => !document.querySelector(b) && document.querySelector("#avisos-topo .pilha-corpo")?.textContent.includes("Erro em Nubank."), BOTAO);
  await assentar(page);
  const corpo = await texto(page);
  await avancar(page, 120_000, 10_000);
  const l = await gets(page, base);
  await ctx.close();
  assert.match(corpo, /Erro em Nubank\. Ver bancos conectados/, "a frase é só o banco: nada entre ela e o link");
  assert.match(corpo, /Ver bancos conectados/);
  assert.equal(l.length, 1, "só a releitura da recarga; nenhuma leitura de acompanhamento");
});

test("W11 (lição 4): dois itens, A 'updating' neutro com still_updating e B com erro: o erro de B aparece NA HORA, sem esperar a coleta de A", async () => {
  const { ctx, page, abrir, botao, s } = await abre();
  s.refresh = (r) => r.fulfill({ json: refreshDe([item("item-11", "updating", true, null, "ok"), item("item-12", "error_recoverable", false, "Reconecte mais tarde", "ok", "Inter")], []) });
  await abrir();
  const base = await marca(page);
  await congelar(page);
  await botao.click();
  await page.waitForFunction(() => document.querySelector("#avisos-topo .pilha-corpo")?.textContent.includes("Erro em Inter."));
  await assentar(page);
  await avancar(page, 60_000, 10_000);
  const l = await gets(page, base);
  const r = { botao: await page.locator(BOTAO).count(), corpo: await texto(page) };
  await ctx.close();
  assert.equal(r.botao, 0);
  assert.match(r.corpo, /Erro em Inter\. Ver bancos conectados/, r.corpo);
  assert.ok(!r.corpo.includes("Nubank"), `o neutro (A, ainda coletando) não é erro e não entra na frase: ${r.corpo}`);
  assert.equal(l.length, 1, "a releitura da recarga e mais nada");
});

test("W12 (D1 e D2, as divergências do julgar do app): 'updating' neutro com still_updating:false ACOMPANHA; items:[] com ok:false é falha com o botão de volta; items:[] com ok:true é sucesso (controle)", async () => {
  {
    const { ctx, page, abrir, botao, s } = await abre();
    s.refresh = (r) => r.fulfill({ json: refreshDe([item("item-11", "updating", false, null, "ok")], [UPDATING()]) });
    await abrir();
    s.conexoes = [UPDATING()];
    await congelar(page);
    await botao.click();
    await page.waitForFunction(() => document.querySelector("#avisos-topo .pilha-corpo")?.textContent.includes("O banco está atualizando"));
    const t0 = await instantePost(page), base = await marca(page);
    await avancar(page, 6000);
    const l = (await gets(page, base)).map((t) => Math.round((t - t0) / 1000));
    await ctx.close();
    assert.deepEqual(l, [5], "D1: acompanha, o 1º GET em 5 s");
  }
  {
    const { ctx, page, abrir, botao, s } = await abre();
    s.refresh = (r) => r.fulfill({ json: { ok: true, sync: { ok: false, items: [], still_updating: 0 }, connections: [] } });
    await abrir();
    await botao.click();
    await page.waitForFunction(() => document.querySelector("#avisos-topo .pilha-corpo")?.textContent.includes("Não conseguimos atualizar agora"));
    const r = { botao: await botao.textContent(), dito: await falado(page) };
    await ctx.close();
    assert.deepEqual(r, { botao: "Atualizar agora", dito: FALHA });
  }
  {
    const { ctx, page, abrir, botao, s } = await abre();
    s.refresh = (r) => { s.emDia = true; return r.fulfill({ json: { ok: true, sync: { ok: true, items: [], still_updating: 0 }, connections: [] } }); };
    await abrir();
    await botao.click();
    await page.locator(AVISO).waitFor({ state: "detached" });
    await ctx.close();
  }
});

test("W13 ((b) e (c)): needs_user_action reautorizável vira o link 'Reconectar Nubank' para /settings?view=open-finance (sem POST, 1 GET /limite); sem vaga no plano ou de dispositivo vira a instrução do servidor + 'Ver bancos conectados'", async () => {
  const quadro = async (conexoes, limite) => {
    const { ctx, page, abrir, srv } = await abre({ conexoes, limite });
    await abrir(false);
    await page.waitForFunction(() => /Reconectar|Erro em Nubank/.test(document.querySelector("#avisos-topo .pilha-corpo")?.textContent ?? ""));
    await assentar(page);
    const r = { corpo: await texto(page), link: await page.locator(`${AVISO} a`).first().getAttribute("href"), posts: srv.posts.length, limites: srv.reqs.filter((p) => p === "/open-finance/7/limite").length };
    await ctx.close();
    return r;
  };
  const b = await quadro([NECESSARIA("Reautorize o banco")], LIMITE());
  assert.match(b.corpo, /Reconectar Nubank/);
  assert.deepEqual([b.link, b.posts, b.limites], ["/settings?view=open-finance", 0, 1]);
  const semVaga = await quadro([NECESSARIA("Reautorize o banco")], LIMITE({ of_banks_max: 0 }));
  assert.match(semVaga.corpo, /Erro em Nubank\. Ver bancos conectados/);
  assert.match(semVaga.corpo, /Ver bancos conectados/);
  assert.equal(semVaga.link, "#/conta/bancos");
  const dispositivo = await quadro([NECESSARIA("Autorize o acesso no app do banco")], LIMITE());
  assert.match(dispositivo.corpo, /Erro em Nubank\. Ver bancos conectados/);
  assert.deepEqual([dispositivo.link, dispositivo.limites], ["#/conta/bancos", 0]);
});

test("W14 ((a0)): snapshot já 'updating': parte CALADA (o texto de atualizando sozinho, a região sem inserção), a cadência 5/10/20/40 s e, no teto, o 'demorando' e nenhum GET nos 2 min seguintes (sem laço)", async () => {
  const { ctx, page, abrir } = await abre({ conexoes: [UPDATING()] });
  await abrir(false);
  await page.waitForFunction(() => document.querySelector("#avisos-topo .pilha-corpo")?.textContent.includes("O banco está atualizando"));
  await congelar(page);
  const todos = await gets(page);
  await avancar(page, 300_000, 1000);
  const apos = await gets(page);
  const fim = { corpo: await texto(page), botao: await page.locator(BOTAO).count(), regiao: await page.locator(`${REGIAO} span`).count(), dito: await falado(page) };
  const base = await marca(page);
  await avancar(page, 120_000, 10_000);
  const depois = await gets(page, base);
  await ctx.close();
  const gaps = apos.slice(todos.length - 1).slice(0, 6).map((t, i, a) => (i ? (t - a[i - 1]) / 1000 : 0)).slice(1);
  // Do FIM de uma leitura ao pedido seguinte: 5, 10, 20, 40 e 60 s (o passo de 1 s do relógio de teste soma até 1 s por leitura)
  [5, 10, 20, 40, 60].forEach((e, i) => assert.ok(gaps[i] >= e && gaps[i] <= e + 1.2, `intervalos ${gaps}`));
  assert.match(fim.corpo, new RegExp(AINDA.replace(/\./g, "\\.")));
  assert.equal(fim.botao, 0);
  assert.deepEqual([fim.regiao, fim.dito], [0, ""], "a rodada sozinha não anuncia nada");
  assert.deepEqual(depois, []);
});

test("W15 (lição 1, cache sobrevivente): o snapshot (a0) ficou em cache pela página Bancos; indo a `/` com a releitura PENDURADA nada parte; ao responder 'em dia' também não", async () => {
  const { ctx, page, abrir, s } = await abre({ conexoes: [UPDATING()], rota: "/conta/bancos" });
  await abrir(false, false);
  await page.waitForFunction(() => document.querySelector(".banco-card"));
  await assentar(page);
  const gate = porta();
  s.segurar = gate.p;
  const base = await marca(page);
  await page.evaluate(() => { location.hash = "#/"; });
  await page.locator(AVISO).waitFor();
  await congelar(page);
  await avancar(page, 40_000, 5000, { preso: true });
  await page.clock.runFor(5);
  const preso = { gets: (await gets(page, base)).length, corpo: await texto(page) };
  s.conexoes = [con(11, "Nubank", "partial", "Dados parciais")];
  s.segurar = null; gate.soltar();
  await ate(page, () => !!document.querySelector("#avisos-topo .pilha-corpo button"));
  await avancar(page, 60_000, 10_000);
  const solto = { gets: (await gets(page, base)).length, corpo: await texto(page) };
  await ctx.close();
  assert.equal(preso.gets, 1, "só a releitura pendurada: nenhuma leitura de cadência");
  assert.ok(!preso.corpo.includes("O banco está atualizando"), preso.corpo);
  assert.equal(solto.gets, 1);
  assert.ok(!solto.corpo.includes("O banco está atualizando"), solto.corpo);
});

test("W15b (lição 2, leitura superada): com a rodada (a0) em voo, o foco da aba faz UM GET a mais e a cadência continua (não recomeça); nunca dois GET em voo", async () => {
  const { ctx, page, abrir } = await abre({ conexoes: [UPDATING()] });
  await abrir(false);
  await page.waitForFunction(() => document.querySelector("#avisos-topo .pilha-corpo")?.textContent.includes("O banco está atualizando"));
  await congelar(page);
  const t0 = (await gets(page)).at(-1), base = await marca(page);
  await avancar(page, 3000, 1000);
  const tf = await page.evaluate(() => Date.now());
  await focar(page);
  await assentar(page);
  await avancar(page, 40_000, 1000);
  const todos = await gets(page, base);
  const emVoo = await page.evaluate(() => window.__maxVoo);
  await ctx.close();
  const cadencia = todos.filter((t) => t > tf + 200); // o GET do foco sai junto com o `focar`
  assert.equal(todos.length, 4, `3 de cadência e o do foco: ${todos.map((t) => (t - t0) / 1000)}`);
  // 5, 10 e 20 s entre um GET e o seguinte (o passo de 1 s do relógio de teste soma até 1 s); recomeçar daria o 1º GET da cadência nova só 5 s depois do foco
  const gaps = [t0, ...cadencia].map((t, i, a) => (i ? (t - a[i - 1]) / 1000 : 0)).slice(1);
  [5, 10, 20].forEach((e, i) => assert.ok(gaps[i] >= e - 0.05 && gaps[i] <= e + 1.2, `a cadência não recomeça: ${gaps}`));
  assert.equal(emVoo, 1);
});

test("W15c (marco): uma releitura (a0) que chega DURANTE o POST não parte rodada depois que o POST falha (a leitura é de antes do fim da rodada)", async () => {
  const { ctx, page, abrir, botao, s } = await abre();
  const portao = porta();
  s.refresh = async (r) => { await portao.p; await r.fulfill({ status: 500, json: { error: { code: "internal_error", message: "x" } } }); };
  await abrir();
  await congelar(page);
  await botao.click();
  await page.waitForFunction(() => window.__req.some(([m]) => m === "POST"));
  s.conexoes = [UPDATING()];
  const n0 = (await gets(page)).length;
  await focar(page);
  await page.waitForFunction((n) => window.__req.filter(([m, p]) => m === "GET" && p === "/open-finance/7").length > n, n0);
  await dorme(150);
  await page.clock.runFor(5); await dorme(50);
  const base = await marca(page);
  portao.soltar();
  await dorme(150);
  await avancar(page, 30_000, 5000, { preso: true });
  const l = await gets(page, base);
  await ctx.close();
  assert.deepEqual(l, [], "nenhuma rodada (a0) nasce da leitura feita durante a rodada que acabou");
});

test("W16 (troca de conta): com o acompanhamento do uid 7, o perfil passa a responder 8 e o foco relê: nenhum GET do 7 depois, nada do texto do 7, e o aviso do 8 mostra 'Atualizar agora' ativo", async () => {
  const { ctx, page, abrir, botao, s } = await abre();
  let uid = 7;
  await ctx.route((u) => u.pathname === "/auth/dashboard-profile", (r) => r.fulfill({ json: { display_name: "Ana", email: "a@b.c", user_id: uid, whatsapp_linked: false } }));
  await ctx.route((u) => u.pathname === "/open-finance/8", (r) => r.fulfill({ json: { ok: true, connections: [con(21, "Bradesco", "partial", "Dados parciais")] } }));
  await ctx.route((u) => u.pathname === "/open-finance/8/limite", (r) => r.fulfill({ json: LIMITE() }));
  s.refresh = (r) => r.fulfill({ json: refreshDe([item("item-11", "updated", true)], [UPDATING()]) });
  await abrir();
  s.conexoes = [UPDATING()];
  await congelar(page);
  await botao.click();
  await page.waitForFunction(() => document.querySelector("#avisos-topo .pilha-corpo")?.textContent.includes("O banco está atualizando"));
  await avancar(page, 7000);
  uid = 8;
  await focar(page);
  await ate(page, () => document.querySelector("#avisos-topo .pilha-corpo button")?.textContent === "Atualizar agora");
  await assentar(page);
  const base = await marca(page);
  await avancar(page, 60_000, 10_000);
  const r = { pedidos: (await pedidos(page, base)).filter((p) => p.includes("/open-finance/7")), corpo: await texto(page), aria: await botao.getAttribute("aria-disabled"), rotulo: await botao.textContent() };
  await ctx.close();
  assert.deepEqual(r.pedidos, []);
  assert.ok(!r.corpo.includes("O banco está atualizando"), r.corpo);
  assert.equal(r.aria, null);
  assert.equal(r.rotulo, "Atualizar agora");
});

test("W17 (vista): o resultado fica sem 'Atualizar' (sem laço) até a aba voltar a ficar visível; aí o botão volta", async () => {
  const { ctx, page, abrir, botao, s } = await abre();
  s.refresh = (r) => r.fulfill({ json: refreshDe([item("item-11", "partial", false, "Parte dos dados ainda não veio")], []) });
  await abrir();
  await botao.click();
  await page.waitForFunction(() => document.querySelector("#avisos-topo .pilha-corpo")?.textContent.includes("Erro em Nubank."));
  await assentar(page);
  const antes = await page.locator(BOTAO).count();
  await focar(page);
  await botao.waitFor();
  await ctx.close();
  assert.equal(antes, 0);
});

test("W18 (troca de tela com o POST em voo): em /gastos o 'Atualizando…' segue; o POST volta 500 em /piggy sem região de status nem alerta, sem anúncio ao voltar; de volta a `/` a falha e o botão", async () => {
  const { ctx, page, abrir, botao, s } = await abre();
  const portao = porta();
  s.refresh = async (r) => { await portao.p; await r.fulfill({ status: 500, json: { error: { code: "internal_error", message: "x" } } }); };
  await abrir();
  await page.evaluate(() => {
    // Tudo que já esteve numa região de status do banco (entrando ou saindo: o texto pode ser zerado no mesmo tique em que entra).
    window.__ditos = new Set();
    const guarda = (n, pai) => { if (n.nodeType === 1 && n.textContent && (n.matches("p.sr-only[role=status]") || pai.matches("p.sr-only[role=status]"))) window.__ditos.add(n.textContent); };
    new MutationObserver((ms) => ms.forEach((m) => { m.addedNodes.forEach((n) => guarda(n, m.target)); m.removedNodes.forEach((n) => guarda(n, m.target)); })).observe(document.body, { childList: true, subtree: true });
  });
  await botao.click();
  await page.waitForFunction((b) => document.querySelector(b)?.getAttribute("aria-disabled") === "true", BOTAO);
  await page.evaluate(() => { location.hash = "#/gastos"; });
  await page.waitForFunction(() => document.querySelector("#page-title")?.textContent.trim() !== "Início");
  const gastos = await page.locator(BOTAO).textContent();
  await page.evaluate(() => { location.hash = "#/piggy"; });
  await page.waitForFunction(() => !document.querySelector("#avisos-topo section.pilha"));
  portao.soltar();
  await assentar(page);
  const piggy = await page.locator("#avisos-topo [role=status], #avisos-topo [role=alert]").count();
  await page.evaluate(() => { location.hash = "#/"; });
  await page.locator(AVISO).waitFor();
  await botao.waitFor();
  const volta = { botao: await botao.textContent(), corpo: await texto(page), ditos: await page.evaluate(() => [...window.__ditos]) };
  await ctx.close();
  assert.equal(gastos, "Atualizando…");
  assert.equal(piggy, 0);
  assert.ok(volta.corpo.includes(FALHA), volta.corpo);
  assert.equal(volta.botao, "Atualizar agora");
  assert.deepEqual(volta.ditos, [DITO], "só o 'Atualizando…' do começo: a falha de /piggy não é anunciada ao voltar");
});

test("W19: o motivo some no meio do acompanhamento (o /bancos relido sem motivo): os GETs param, o aviso sai e nada é anunciado", async () => {
  const { ctx, page, abrir, botao, s } = await abre();
  s.refresh = (r) => r.fulfill({ json: refreshDe([item("item-11", "updated", true)], [UPDATING()]) });
  await abrir();
  s.conexoes = [UPDATING()];
  await congelar(page);
  await botao.click();
  await page.waitForFunction(() => document.querySelector("#avisos-topo .pilha-corpo")?.textContent.includes("O banco está atualizando"));
  await avancar(page, 6000);
  s.emDia = true;
  await focar(page);
  await ate(page, () => !document.querySelector("#avisos-topo section.pilha"));
  await assentar(page);
  const base = await marca(page);
  await avancar(page, 120_000, 10_000);
  const l = await gets(page, base);
  await ctx.close();
  assert.deepEqual(l, []);
});

test("W20 (sem roubo de foco): com o foco levado para outro lugar da página, o sucesso do POST NÃO puxa o foco de volta; navegar pela lateral deixa o foco no título da tela", async () => {
  for (const modo of ["foco em link da página", "navegação pela lateral"]) {
    const { ctx, page, abrir, botao, s } = await abre();
    const portao = porta();
    s.refresh = async (r) => { await portao.p; s.emDia = true; await r.fulfill({ json: refreshDe([item("item-11")]) }); };
    await abrir();
    await botao.click();
    await page.waitForFunction((b) => document.querySelector(b)?.getAttribute("aria-disabled") === "true", BOTAO);
    const alvo = page.locator('a[href="#/gastos"]').first();
    if (modo === "foco em link da página") await alvo.focus(); else await alvo.click();
    portao.soltar();
    await page.locator(AVISO).waitFor({ state: "detached" });
    await dorme(100);
    const foco = await page.evaluate(() => ({ id: document.activeElement?.id, href: document.activeElement?.getAttribute("href"), texto: document.activeElement?.textContent.trim().slice(0, 20) }));
    await ctx.close();
    if (modo === "foco em link da página") assert.equal(foco.href, "#/gastos", JSON.stringify(foco)); else assert.equal(foco.id, "page-title", JSON.stringify(foco));
  }
});

test("V1: a 1440 e a 375 px, 'Atualizando…' e o resultado com nome de banco de 120 caracteres sem espaço cabem no <main>, sem rolagem lateral, e o botão tem 44 px de altura", async () => {
  for (const width of [1440, 375]) {
    const longo = "B".repeat(120);
    const { ctx, page, abrir, botao, s } = await abre({ width });
    const portao = porta();
    s.refresh = async (r) => { await portao.p; await r.fulfill({ json: refreshDe([item("item-11", "partial", false, "Parte dos dados ainda não veio", "ok", longo)], []) }); };
    await abrir();
    const medir = () => page.evaluate(() => {
      const c = document.querySelector("#avisos-topo .pilha-card").getBoundingClientRect(), m = document.querySelector("main").getBoundingClientRect();
      const b = document.querySelector("#avisos-topo .pilha-corpo button, #avisos-topo .pilha-corpo a.btn");
      return { dentro: c.left >= m.left - 0.5 && c.right <= m.right + 0.5, sw: document.documentElement.scrollWidth, iw: innerWidth, alto: b.getBoundingClientRect().height };
    });
    await botao.click();
    await page.waitForFunction((b) => document.querySelector(b)?.getAttribute("aria-disabled") === "true", BOTAO);
    const enviando = await medir();
    portao.soltar();
    await page.waitForFunction((l) => document.querySelector("#avisos-topo .pilha-corpo")?.textContent.includes(l), longo);
    const resultado = await medir();
    await ctx.close();
    for (const [rotulo, m] of [["Atualizando…", enviando], ["resultado", resultado]]) {
      assert.ok(m.dentro && m.sw <= m.iw, `${width}px ${rotulo}: ${JSON.stringify(m)}`);
      assert.ok(m.alto >= 44, `${width}px ${rotulo}: botão com ${m.alto}px`);
    }
  }
});
