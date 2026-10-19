/**
 * O "Atualizar agora" do aviso de banco do /painel, #1076 PR 2 — os achados da passada 1 do Tester (A1, A2, A3, A5) e os mutantes que sobreviveram (M20, M24, M31, M32).
 * Cada teste foi escrito contra o defeito e VERMELHO sem o conserto (a mutação está no relato do PR). Harness: _atualizar_banco.mjs. O caminho do POST: `dashboard_v2_atualizar_banco.test.mjs`; a rodada: `…_rodada.test.mjs`.
 * Rodar:  npm run test:frontend   (artefato: `npm --prefix webapp run build`)
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { setTimeout as dorme } from "node:timers/promises";
import { chromium } from "playwright";
import { exigeArtefatoEmDia } from "./_painel.mjs";
import { LIMITE, con, item, refreshDe } from "./_bancos.mjs";
import { AVISO, ATUALIZANDO, DITO, FALHA, REGIAO, abrirBanco, assentar, ate, avancar, congelar, falado, focar, gets, instantePost, marca, pedidos, porta, texto } from "./_atualizar_banco.mjs";

let browser;
before(async () => { exigeArtefatoEmDia(); browser = await chromium.launch(); });
after(() => browser?.close());

const abre = (opts) => abrirBanco(browser, opts);
const UPDATING = () => con(11, "Nubank", "updating", "Atualizando…", "2026-10-02T14:00:00Z");
const NECESSARIA = (detalhe) => con(11, "Nubank", "needs_user_action", "Ação necessária", undefined, detalhe);
const erro = (status) => (r) => r.fulfill({ status, json: { error: { code: "x", message: "x" } } });
const BOTAO = "#avisos-topo .pilha-corpo button";

test("A1: 'updating' SEM provider_item_id não é (a0): nada recarrega em laço (relógio correndo, 3 s) e o corpo é o texto padrão (c0); COM id é (a0) e acompanha (controle)", async () => {
  const medir = async (id) => {
    const { ctx, page, abrir } = await abre({ conexoes: [{ ...UPDATING(), provider_item_id: id }] });
    await abrir(false);
    await assentar(page);
    await dorme(500);
    const base = await marca(page);
    await dorme(3000);
    const r = { pedidos: await pedidos(page, base), corpo: await texto(page) };
    await ctx.close();
    return r;
  };
  const sem = await medir(null);
  assert.ok(sem.pedidos.length <= 2, `laço: ${sem.pedidos.length} pedidos em 3 s: ${sem.pedidos.slice(0, 6)}`);
  assert.match(sem.corpo, /Ver bancos conectados/);
  assert.match(sem.corpo, /precisam ser atualizados/, "sem id: o texto padrão do aviso");
  assert.doesNotMatch(sem.corpo, /Erro em/, "sem id não há nada errado: não é (c)");
  assert.ok(!sem.corpo.includes(ATUALIZANDO), sem.corpo);
  const vazio = await medir("");
  assert.ok(vazio.pedidos.length <= 2, `id vazio: ${vazio.pedidos.length} pedidos em 3 s`);
  const com = await medir("item-11");
  assert.deepEqual(com.pedidos, [], "(a0): a 1ª leitura só sai aos 5 s");
  assert.ok(com.corpo.includes(ATUALIZANDO), com.corpo);
});

test("A2: a leitura do acompanhamento que nunca volta é cortada aos 15 s (transitória) e no teto a rodada FECHA com a falha, nunca fica em 'atualizando…'; uma por vez", async () => {
  const { ctx, page, abrir, botao, s } = await abre();
  s.refresh = (r) => r.fulfill({ json: refreshDe([item("item-11", "updated", true)], [UPDATING()]) });
  await abrir();
  await congelar(page);
  s.conexoes = [UPDATING()];
  await botao.click();
  await page.waitForFunction(() => document.querySelector("#avisos-topo .pilha-corpo")?.textContent.includes("O banco está atualizando"));
  s.segurar = new Promise(() => {}); // todo GET do snapshot pendura
  await avancar(page, 330_000, 5_000, { preso: true });
  const r = { corpo: await texto(page), botao: await botao.count(), maxVoo: await page.evaluate(() => window.__maxVoo) };
  await ctx.close();
  assert.ok(r.corpo.includes(FALHA), r.corpo); // nenhuma leitura deu certo: vale a falha, não o "demorando"
  assert.ok(!r.corpo.includes(ATUALIZANDO), r.corpo);
  assert.equal(r.botao, 0, "resultado: sem 'Atualizar agora' (sem laço)");
  assert.equal(r.maxVoo, 1);
});

test("A2b: o POST pendurado é cortado aos 60 s e a leitura de depois TAMBÉM pendura: aos 75 s a falha e o botão voltam (não fica em 'Atualizando…')", async () => {
  const { ctx, page, abrir, botao, s } = await abre();
  const portao = porta();
  s.refresh = async (r) => { await portao.p; await r.abort().catch(() => {}); };
  await abrir();
  s.segurar = new Promise(() => {});
  await congelar(page);
  await botao.click();
  await page.waitForFunction(() => window.__req.some(([m]) => m === "POST"));
  await avancar(page, 80_000, 5_000, { preso: true });
  const r = { corpo: await texto(page), botao: await botao.textContent() };
  portao.soltar();
  await ctx.close();
  assert.ok(r.corpo.includes(FALHA), r.corpo);
  assert.equal(r.botao, "Atualizar agora");
});

test("A3: o anúncio da conta 7 não sobrevive à troca para a conta 8: nada do banco da Ana no DOM nem na região de status", async () => {
  const { ctx, page, abrir, botao, s } = await abre();
  let uid = 7;
  await ctx.route((u) => u.pathname === "/auth/dashboard-profile", (r) => r.fulfill({ json: { display_name: "Ana", email: "a@b.c", user_id: uid, whatsapp_linked: false } }));
  await ctx.route((u) => u.pathname === "/open-finance/8", (r) => r.fulfill({ json: { ok: true, connections: [con(21, "Bradesco", "partial", "Dados parciais")] } }));
  await ctx.route((u) => u.pathname === "/open-finance/8/limite", (r) => r.fulfill({ json: LIMITE() }));
  s.refresh = (r) => r.fulfill({ json: refreshDe([item("item-11", "partial", false, "Segredo do banco da Ana", "ok", "BancoDaAna")], []) });
  await abrir();
  await botao.click();
  await page.waitForFunction(() => document.querySelector("#avisos-topo .pilha-corpo")?.textContent.includes("Erro em BancoDaAna."));
  await assentar(page);
  assert.ok((await falado(page)).includes("Erro em BancoDaAna."), "controle: o anúncio da conta 7 existe antes da troca");
  uid = 8;
  await focar(page);
  await ate(page, () => document.querySelector("#avisos-topo .pilha-corpo button")?.textContent === "Atualizar agora");
  await assentar(page);
  const regiao = await page.locator(REGIAO).innerText();
  const html = await page.locator("#avisos-topo").innerHTML();
  await ctx.close();
  assert.ok(!html.includes("BancoDaAna"), `vazou: ${regiao}`);
  assert.equal(regiao, "");
});

test("A5: com o /limite ainda pendurado, o aviso (b) mostra o corpo de LEITURA (texto padrão + 'Ver bancos'), nunca a frase (c) 'Erro em Nubank.'; solto, vira 'Reconectar Nubank'", async () => {
  const portao = porta();
  const { ctx, page, ir } = await abre({ conexoes: [NECESSARIA("Reautorize o banco")], limiteRes: async (r) => { await portao.p; await r.fulfill({ json: LIMITE() }); } });
  await ir("/"); // sem `abrir`: ele espera a página assentar, e o /limite fica preso de propósito
  await page.locator(AVISO).waitFor();
  await page.waitForFunction(() => window.__req.some(([, p]) => p === "/open-finance/7/limite"));
  const lendo = [];
  for (let i = 0; i < 5; i++) { lendo.push(await texto(page)); await dorme(100); }
  portao.soltar();
  await page.waitForFunction(() => document.querySelector("#avisos-topo .pilha-corpo")?.textContent.includes("Reconectar Nubank"));
  const depois = await texto(page);
  await ctx.close();
  for (const t of lendo) {
    assert.ok(!/Reautorize|Reconectar/.test(t), `estado intermediário: ${t}`);
    assert.match(t, /precisam ser atualizados/);
    assert.match(t, /Ver bancos conectados/);
  }
  assert.match(depois, /Reconectar Nubank/);
});

for (const [nome, status, segue] of [["500", 500, true], ["429", 429, true], ["404", 404, false], ["403", 403, false]]) {
  test(`M20 (GET ${nome} no acompanhamento): ${segue ? "segue a cadência (5, 15 s) e conclui quando o servidor volta" : "encerra a rodada com a falha e nenhum GET depois"}`, async () => {
    const { ctx, page, abrir, botao, s } = await abre();
    s.refresh = (r) => r.fulfill({ json: refreshDe([item("item-11", "updated", true)], [UPDATING()]) });
    await abrir();
    s.conexoes = [UPDATING()];
    await congelar(page);
    await botao.click();
    await page.waitForFunction(() => document.querySelector("#avisos-topo .pilha-corpo")?.textContent.includes("O banco está atualizando"));
    const t0 = await instantePost(page), base = await marca(page);
    s.falha = erro(status);
    await avancar(page, 6_000);
    const corpo5 = await texto(page);
    if (segue) {
      s.falha = null;
      s.conexoes = [con(11, "Nubank", "updated", "x", "2026-10-02T14:59:00Z")];
      s.emDia = true;
      await avancar(page, 10_000);
      await ate(page, () => !document.querySelector("#avisos-topo section.pilha"));
    } else {
      await avancar(page, 120_000, 10_000);
    }
    const l = (await gets(page, base)).map((t) => Math.round((t - t0) / 1000));
    const botoes = segue ? 0 : await page.locator(BOTAO).count();
    await ctx.close();
    if (segue) {
      assert.ok(corpo5.includes(ATUALIZANDO), corpo5);
      assert.deepEqual(l, [5, 15, 15], "o GET que falha segue a cadência; a recarga do fim relê junto");
    } else {
      assert.ok(corpo5.includes(FALHA), corpo5);
      assert.equal(botoes, 0);
      assert.deepEqual(l, [5], "um GET só: o 4xx encerra");
    }
  });
}

test("M24: o anúncio do banco não fica pendurado ao trocar de tela (a região de status esvazia)", async () => {
  const { ctx, page, abrir, botao, s } = await abre();
  const portao = porta();
  s.refresh = async (r) => { await portao.p; await r.fulfill({ json: refreshDe([item("item-11", "updated")], []) }); };
  await abrir();
  await botao.click();
  await page.waitForFunction((b) => document.querySelector(b)?.getAttribute("aria-disabled") === "true", BOTAO);
  const antes = await falado(page);
  await page.evaluate(() => { location.hash = "#/gastos"; });
  await page.waitForFunction(() => document.querySelector("#page-title")?.textContent.trim() !== "Início");
  const depois = await falado(page);
  portao.soltar();
  await ctx.close();
  assert.equal(antes, DITO, "controle: o anúncio existe na tela do toque");
  assert.equal(depois, "", "em /gastos o texto de /painel não fica na região");
});

test("M31 (borda): 'still_updating' que não é o booleano true (1, 'x') NÃO é coleta: o POST em dia conclui na hora, sem acompanhar; com true acompanha (controle)", async () => {
  for (const valor of [1, "x"]) {
    const { ctx, page, abrir, botao, s } = await abre();
    s.refresh = (r) => { s.emDia = true; return r.fulfill({ json: refreshDe([{ ...item("item-11", "updated", false), still_updating: valor }], []) }); };
    await abrir();
    const base = await marca(page);
    await botao.click();
    await page.locator(AVISO).waitFor({ state: "detached", timeout: 4000 });
    await assentar(page);
    const l = (await pedidos(page, base)).filter((p) => p === "GET /open-finance/7");
    await ctx.close();
    assert.equal(l.length, 1, `${JSON.stringify(valor)}: só a releitura da recarga`);
  }
  const { ctx, page, abrir, botao, s } = await abre();
  s.refresh = (r) => r.fulfill({ json: refreshDe([item("item-11", "updated", true)], [UPDATING()]) });
  await abrir();
  s.conexoes = [UPDATING()];
  await botao.click();
  await page.waitForFunction(() => document.querySelector("#avisos-topo .pilha-corpo")?.textContent.includes("O banco está atualizando"));
  await ctx.close();
});

test("M32 (borda): item do POST sem item_id não é casado a NENHUMA conexão (nem a uma de id 'x'): a 1ª leitura (5 s) conclui, não espera o teto", async () => {
  const { ctx, page, abrir, botao, s } = await abre();
  const sem = item("item-11", "updated", true);
  delete sem.item_id;
  const noX = () => ({ ...UPDATING(), provider_item_id: "x" });
  s.refresh = (r) => r.fulfill({ json: refreshDe([sem], [noX()]) });
  await abrir();
  s.conexoes = [noX()];
  await congelar(page);
  await botao.click();
  await page.waitForFunction(() => document.querySelector("#avisos-topo .pilha-corpo")?.textContent.includes("O banco está atualizando"));
  s.emDia = true;
  await avancar(page, 6_000);
  await ate(page, () => !document.querySelector("#avisos-topo section.pilha"));
  await ctx.close();
});

// ---- 2a passada do Tester: o prazo que vence DEPOIS dos headers (corpo travado) e a leitura cortada (A2c) ----
const FIM = "2026-10-02T14:59:00Z";
/**
 * Responde (200, ou `rota.status`) com os headers e um corpo que nunca fecha, enquanto `window.__trava` for verdadeiro; o abort do pedido rejeita o `.json()` com o AbortError do sinal (o que o fetch real faz no meio do corpo).
 * Um OBJETO e não um `Response` de stream: no Chromium, o stream de um `Response` sintético que erra vira `TypeError: Failed to fetch` no `.json()` (medido), e isso não é o que o fetch real entrega.
 */
const travar = (page, rota) => page.evaluate((rota) => {
  window.__trava = true;
  const real = window.fetch;
  window.fetch = function (u, o) {
    const url = new URL(String((u && u.url) || u), location.href), m = ((o && o.method) || "GET").toUpperCase();
    if (!window.__trava || url.pathname !== rota.p || m !== rota.m) return real.apply(this, arguments);
    window.__req.push([m, url.pathname, Date.now()]);
    const sig = o && o.signal, status = rota.status ?? 200;
    const json = () => new Promise((_, rej) => { if (sig) sig.addEventListener("abort", () => { (window.__cortes ??= []).push(m); rej(new DOMException("The operation was aborted.", "AbortError")); }); });
    return Promise.resolve({ ok: status < 300, status, headers: new Headers({ "content-type": "application/json" }), json });
  };
}, rota);
const acompanhando = (page) => page.waitForFunction(() => document.querySelector("#avisos-topo .pilha-corpo")?.textContent.includes("O banco está atualizando"));

test("A2c: a 1a leitura (5 s) pendura e é cortada aos 20 s; a 2a (aos 30 s) responde em dia com last_sync_at novo: o aviso SAI (a leitura cortada é transitória)", async () => {
  const { ctx, page, abrir, botao, s } = await abre();
  s.refresh = (r) => r.fulfill({ json: refreshDe([item("item-11", "updated", true)], [UPDATING()]) });
  await abrir();
  s.conexoes = [UPDATING()];
  await congelar(page);
  await botao.click();
  await acompanhando(page);
  s.segurar = new Promise(() => {});
  await avancar(page, 22_000, 1000, { preso: true });
  const meio = await texto(page);
  s.segurar = null; s.emDia = true;
  s.conexoes = [con(11, "Nubank", "updated", "x", FIM)];
  await avancar(page, 12_000, 1000);
  const fim = await page.locator(AVISO).count();
  await ctx.close();
  assert.ok(meio.includes("atualizando"), `aos 22 s a rodada segue: ${meio}`);
  assert.equal(fim, 0, "aos 34 s o aviso saiu");
});

test("A2d: GET com headers e CORPO travado: o prazo de 15 s corta no meio do .json() e segue a cadência (não vira FALHA na 1a leitura); com o corpo solto aos 30 s o aviso sai", async () => {
  const { ctx, page, abrir, botao, s } = await abre();
  s.refresh = (r) => r.fulfill({ json: refreshDe([item("item-11", "updated", true)], [UPDATING()]) });
  await abrir();
  s.conexoes = [UPDATING()];
  await congelar(page);
  await botao.click();
  await acompanhando(page);
  await travar(page, { p: "/open-finance/7", m: "GET" });
  const base = await marca(page);
  await avancar(page, 22_000, 1000, { preso: true }); // leitura aos 5 s, cortada aos 20 s
  const meio = await texto(page);
  await page.evaluate(() => { window.__trava = false; });
  s.emDia = true;
  s.conexoes = [con(11, "Nubank", "updated", "x", FIM)];
  await avancar(page, 12_000, 1000);
  const r = { gets: await gets(page, base), fim: await page.locator(AVISO).count() };
  await ctx.close();
  assert.ok(meio.includes("atualizando") && !meio.includes(FALHA), `aos 22 s: ${meio}`);
  assert.equal(r.gets[1] - r.gets[0], 25_000, "a leitura travada (5 s, cortada aos 20 s) e a seguinte (30 s)");
  assert.equal(r.fim, 0);
});

test("A2e: POST com headers e CORPO travado: o prazo de 60 s corta no meio do .json() e segue para ler e acompanhar (não vira 'ocioso + falha'); a leitura em dia fecha o aviso", async () => {
  const { ctx, page, abrir, botao, s } = await abre();
  await abrir();
  s.conexoes = [UPDATING()];
  await congelar(page);
  await travar(page, { p: "/open-finance/7/refresh", m: "POST" });
  await botao.click();
  await avancar(page, 62_000, 2000, { preso: true });
  const meio = { corpo: await texto(page), pedidos: (await pedidos(page)).filter((p) => p.startsWith("POST") || p.startsWith("GET /open-finance/7")).slice(-2) };
  s.emDia = true;
  s.conexoes = [con(11, "Nubank", "updated", "x", FIM)];
  await avancar(page, 12_000, 1000);
  const fim = await page.locator(AVISO).count();
  await ctx.close();
  assert.ok(meio.corpo.includes("atualizando") && !meio.corpo.includes(FALHA), meio.corpo);
  assert.deepEqual(meio.pedidos, ["POST /open-finance/7/refresh", "GET /open-finance/7"], "depois do POST cortado, lê o snapshot");
  assert.equal(fim, 0);
});

test("A2g (controle do 4xx): POST 403 com headers e CORPO travado: aos 60 s o status SOBRA (FALHA + botão, +1 GET /auth/dashboard-profile e NENHUM GET do snapshot), o corte não vira AbortError", async () => {
  const { ctx, page, abrir, botao, s } = await abre();
  await abrir();
  s.conexoes = [UPDATING()];
  await congelar(page);
  await travar(page, { p: "/open-finance/7/refresh", m: "POST", status: 403 });
  const base = await marca(page);
  await botao.click();
  await avancar(page, 62_000, 2000, { preso: true });
  const depois = await pedidos(page, base);
  const r = { corpo: await texto(page), botao: await botao.textContent(), conta: depois.filter((p) => p === "GET /auth/dashboard-profile").length, snapshots: depois.filter((p) => p === "GET /open-finance/7").length };
  await ctx.close();
  assert.ok(r.corpo.includes(FALHA), r.corpo);
  assert.deepEqual({ botao: r.botao, conta: r.conta }, { botao: "Atualizar agora", conta: 1 });
  assert.equal(r.snapshots, 0, "o 403 não lê o snapshot (só o AbortError lê)");
});

test("A2f (controle do abort externo): corpo travado e depois o X (GET aos 6 s; POST aos 10 s): zero pedidos depois do X, o prazo não é confundido com o abort do usuário", async () => {
  for (const caso of ["GET", "POST"]) {
    const { ctx, page, abrir, botao, s } = await abre();
    s.refresh = (r) => r.fulfill({ json: refreshDe([item("item-11", "updated", true)], [UPDATING()]) });
    await abrir();
    s.conexoes = [UPDATING()];
    await congelar(page);
    if (caso === "POST") { await travar(page, { p: "/open-finance/7/refresh", m: "POST" }); await botao.click(); await avancar(page, 10_000, 1000, { preso: true }); }
    else { await botao.click(); await acompanhando(page); await travar(page, { p: "/open-finance/7", m: "GET" }); await avancar(page, 6_000, 1000, { preso: true }); }
    const base = await marca(page);
    await page.getByRole("button", { name: "Fechar aviso" }).click();
    const cortes = await page.evaluate(() => window.__cortes); // o X corta o pedido em voo (o `comPrazo` liga o sinal da rodada); o prazo só vence bem depois
    await avancar(page, 120_000, 10_000, { preso: true });
    const r = { pedidos: await pedidos(page, base), aviso: await page.locator(AVISO).count() };
    await ctx.close();
    assert.deepEqual(cortes, [caso], `${caso}: o X abortou o pedido em voo`);
    assert.deepEqual(r.pedidos, [], `${caso}: nada depois do X`);
    assert.equal(r.aviso, 0, caso);
  }
});
