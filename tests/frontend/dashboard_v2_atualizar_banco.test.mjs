/**
 * O "Atualizar agora" do aviso de banco da pilha do /painel (webapp/src/dashboard/parts/usarAtualizarBanco.ts + CorpoAvisoBanco.tsx, a máquina em lib/atualizarBanco.ts), #1076 PR 2:
 * o POST /open-finance/{uid}/refresh de verdade e o acompanhamento da coleta, com a mesma regra do app. Este arquivo: o caminho do POST (W1-W9). O resto (W10-W20, medidas): `…_rodada.test.mjs`.
 * Harness em _atualizar_banco.mjs (relógio instalado, cada fetch anotado com o Date.now() falso). Rodar:  npm run test:frontend   (artefato: `npm --prefix webapp run build`)
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { setTimeout as dorme } from "node:timers/promises";
import { chromium } from "playwright";
import { exigeArtefatoEmDia } from "./_painel.mjs";
import { con, item, refreshDe } from "./_bancos.mjs";
import { ATUALIZANDO, AVISO, AINDA, DITO, FALHA, REGIAO, abrirBanco, assentar, avancar, congelar, falado, gets, instantePost, marca, marcar, pedidos, porta, texto } from "./_atualizar_banco.mjs";

let browser;
before(async () => { exigeArtefatoEmDia(); browser = await chromium.launch(); });
after(() => browser?.close());

const abre = (opts) => abrirBanco(browser, opts);
const emDia = (s) => { s.emDia = true; };

test("W1: toque em 'Atualizar agora': 1 POST com CSRF e JSON; o MESMO botão vira 'Atualizando…' (aria-disabled, com o foco); a região de status entra com o texto; ok em dia: o aviso some sem recarregar e o foco vai para 'Nenhum aviso.'", async () => {
  const { ctx, page, erros, abrir, botao, s, srv } = await abre();
  const portao = porta();
  s.refresh = async (r) => { await portao.p; emDia(s); await r.fulfill({ json: refreshDe([item("item-11")]) }); };
  await abrir();
  await page.evaluate(() => { window.__mesmaPagina = 1; });
  await marcar(botao, "botao"); await marcar(page.locator(REGIAO), "regiao");
  assert.equal(await page.locator(`${REGIAO} span`).count(), 0, "a região deve existir vazia antes do toque");
  await botao.click();
  await page.waitForFunction(() => document.querySelector("#avisos-topo .pilha-corpo button")?.textContent === "Atualizando…");
  const ocupado = await page.evaluate(() => { const b = document.querySelector("#avisos-topo .pilha-corpo button"); return { marca: b.dataset.marca, aria: b.getAttribute("aria-disabled"), disabled: b.disabled, foco: document.activeElement === b, regiao: document.querySelector("#avisos-topo p.sr-only[role=status]").dataset.marca, dito: document.querySelector("#avisos-topo p.sr-only[role=status]").textContent }; });
  portao.soltar();
  await page.locator(AVISO).waitFor({ state: "detached" });
  await page.waitForFunction(() => document.activeElement?.textContent === "Nenhum aviso.");
  const depois = { mesma: await page.evaluate(() => window.__mesmaPagina), pedidos: await pedidos(page) };
  await ctx.close();
  assert.equal(srv.posts.length, 1);
  assert.equal(srv.posts[0].headers["x-csrf-token"], "tok-123");
  assert.match(srv.posts[0].headers["content-type"], /application\/json/);
  assert.deepEqual(ocupado, { marca: "botao", aria: "true", disabled: false, foco: true, regiao: "regiao", dito: DITO });
  assert.equal(depois.mesma, 1, "sem recarregar a página");
  assert.deepEqual(erros, []);
});

test("W2: dois cliques no mesmo tique e mais um com o botão aria-disabled: UM POST", async () => {
  const { ctx, page, abrir, botao, s, srv } = await abre();
  const portao = porta();
  s.refresh = async (r) => { await portao.p; await r.fulfill({ json: refreshDe([item("item-11")]) }); };
  await abrir();
  await botao.evaluate((b) => { b.dispatchEvent(new MouseEvent("click", { bubbles: true })); b.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
  await page.waitForFunction(() => document.querySelector("#avisos-topo .pilha-corpo button")?.getAttribute("aria-disabled") === "true");
  await botao.click({ force: true }); // aria-disabled: o clique é ignorado (o Playwright o chama de 'não habilitado', daí o force)
  await dorme(100);
  const n = srv.posts.length;
  portao.soltar();
  await assentar(page);
  await ctx.close();
  assert.equal(n, 1);
  assert.equal(srv.posts.length, 1);
});

test("W3: still_updating: GETs do snapshot em 5, 15, 35 e 75 s, nunca dois em voo; o last_sync_at muda no 4º; o aviso some", async () => {
  const { ctx, page, abrir, botao, s } = await abre();
  let n = 0;
  s.refresh = (r) => r.fulfill({ json: refreshDe([item("item-11", "updated", true)], [con(11, "Nubank", "updating", "Atualizando…", "2026-10-02T14:00:00Z")]) });
  await abrir();
  s.conexoes = () => { n++; return [con(11, "Nubank", n < 4 ? "updating" : "updated", "x", n < 4 ? "2026-10-02T14:00:00Z" : "2026-10-02T14:59:00Z")]; };
  const base = await marca(page);
  await congelar(page);
  await botao.click();
  await page.waitForFunction(() => window.__req.some(([m]) => m === "POST"));
  await assentar(page);
  await page.waitForFunction(() => document.querySelector("#avisos-topo .pilha-corpo")?.textContent.includes("O banco está atualizando"));
  const t0 = await instantePost(page);
  emDia(s);
  await avancar(page, 80_000);
  const instantes = (await gets(page, base)).map((t) => Math.round((t - t0) / 1000));
  const emVoo = await page.evaluate(() => window.__maxVoo);
  await page.locator(AVISO).waitFor({ state: "detached" });
  await ctx.close();
  assert.deepEqual(instantes, [5, 15, 35, 75, 75], "a cadência 5/15/35/75 s e, no 4º, a releitura da recarga (o aviso some porque o servidor deixa de mandar o motivo)");
  assert.equal(emVoo, 1);
});

test("W4: a coleta nunca termina: no teto de 300 s fica o texto do 'demorando' + 'Ver bancos conectados', SEM 'Atualizar agora', e nenhum GET nos 2 min seguintes", async () => {
  const { ctx, page, abrir, botao, s } = await abre();
  s.refresh = (r) => r.fulfill({ json: refreshDe([item("item-11", "updated", true)], [con(11, "Nubank", "updating")]) });
  await abrir();
  s.conexoes = [con(11, "Nubank", "updating")];
  await congelar(page);
  await botao.click();
  await page.waitForFunction(() => window.__req.some(([m]) => m === "POST"));
  const t0 = await instantePost(page);
  await avancar(page, 300_000, 5000);
  const fim = { texto: await texto(page), botao: await botao.count(), link: await page.locator(`${AVISO} a`).getAttribute("href") };
  const antes = await marca(page);
  await avancar(page, 120_000, 10_000);
  const depois = await gets(page, antes);
  const ultimo = (await gets(page)).at(-1);
  await ctx.close();
  assert.match(fim.texto, new RegExp(AINDA.replace(/[.]/g, "\\.")));
  assert.equal(fim.botao, 0);
  assert.equal(fim.link, "#/conta/bancos");
  assert.deepEqual(depois, []);
  assert.equal(Math.round((ultimo - t0) / 1000), 300, "a última leitura cai no teto exato");
});

for (const [nome, resposta] of [["500", (r) => r.fulfill({ status: 500, json: { error: { code: "internal_error", message: "x" } } })], ["429", (r) => r.fulfill({ status: 429, json: { error: { code: "rate_limited", message: "x" } } })], ["rede", (r) => r.abort()],
  // Contrato quebrado (sem `sync.items`, ou fora de lista): falha, nunca "em dia" calado. O vazio VÁLIDO (`items: []`, `ok: true`) conclui: W5b.
  ["sem sync.items", (r) => r.fulfill({ json: { ...refreshDe([]), sync: { ok: true } } })], ["sync.items objeto", (r) => r.fulfill({ json: { ...refreshDe([]), sync: { ok: true, items: {} } } })],
  ["sem connections", (r) => r.fulfill({ json: { ok: true, sync: { ok: true, items: [], still_updating: 0 } } })],
  ["sync.ok texto", (r) => r.fulfill({ json: { ...refreshDe([]), sync: { ok: "false", items: [], still_updating: 0 } } })]]) {
  test(`W5 (${nome}): o POST falha: a falha aparece e 'Atualizar agora' volta NO MESMO nó, com o foco nele, e a falha é anunciada`, async () => {
    const { ctx, page, abrir, botao, s } = await abre();
    s.refresh = resposta;
    await abrir();
    await marcar(botao, "botao");
    await botao.click();
    await page.waitForFunction(() => document.querySelector("#avisos-topo .pilha-corpo")?.textContent.includes("Não conseguimos atualizar agora"));
    const r = await page.evaluate(() => { const b = document.querySelector("#avisos-topo .pilha-corpo button"); return { rotulo: b.textContent, marca: b.dataset.marca, aria: b.getAttribute("aria-disabled"), foco: document.activeElement === b }; });
    const dito = await falado(page);
    const corpo = await texto(page);
    await ctx.close();
    assert.deepEqual(r, { rotulo: "Atualizar agora", marca: "botao", aria: null, foco: true });
    assert.equal(dito, FALHA);
    assert.ok(corpo.includes(FALHA), corpo);
  });
}

test("W5b (controle do contrato): 'items: []' com 'ok: true' é resposta válida: conclui em dia, sem falha", async () => {
  const { ctx, page, abrir, botao, s } = await abre();
  s.refresh = (r) => { emDia(s); return r.fulfill({ json: refreshDe([]) }); };
  await abrir();
  await botao.click();
  await page.locator(AVISO).waitFor({ state: "detached" });
  const topo = await page.locator("#avisos-topo").innerText().catch(() => "");
  await ctx.close();
  assert.ok(!topo.includes(FALHA), topo);
});

for (const [nome, status, corpoErro] of [["401 sem WWW-Authenticate", 401, { detail: "Sessão" }], ["402", 402, { error: { code: "payment_required", message: "x" } }]]) {
  test(`W6 (${nome}): nenhum texto de falha, nenhum anúncio e +1 GET /api/v2/me (o portão decide)`, async () => {
    const { ctx, page, abrir, botao, s } = await abre();
    s.refresh = (r) => r.fulfill({ status, json: corpoErro });
    await abrir();
    const antes = (await pedidos(page)).filter((p) => p === "GET /api/v2/me").length;
    await botao.click();
    await page.waitForFunction(() => document.querySelector("#avisos-topo .pilha-corpo button")?.getAttribute("aria-disabled") !== "true");
    await assentar(page);
    const r = { me: (await pedidos(page)).filter((p) => p === "GET /api/v2/me").length - antes, corpo: await texto(page), dito: await falado(page) };
    await ctx.close();
    assert.equal(r.me, 1);
    assert.ok(!r.corpo.includes("Não conseguimos"), r.corpo);
    assert.equal(r.dito, DITO, "só o 'Atualizando…' do começo; nada de falha");
  });
}

test("W6b: POST 403: a falha + o botão, e +1 GET /auth/dashboard-profile (a sessão pode ser de outra conta)", async () => {
  const { ctx, page, abrir, botao, s } = await abre();
  s.refresh = (r) => r.fulfill({ status: 403, json: { detail: "Acesso negado" } });
  await abrir();
  const conta = async () => (await pedidos(page)).filter((p) => p === "GET /auth/dashboard-profile").length;
  const antes = await conta();
  await botao.click();
  await page.waitForFunction(() => document.querySelector("#avisos-topo .pilha-corpo")?.textContent.includes("Não conseguimos atualizar agora"));
  await assentar(page);
  const r = { conta: (await conta()) - antes, botao: await botao.textContent() };
  await ctx.close();
  assert.deepEqual(r, { conta: 1, botao: "Atualizar agora" });
});

test("W7: o POST pendurado: aos 60 s ele falha como abortado (requestfailed) e UM GET do snapshot sai; com 'updating' acompanha, com tudo em dia conclui", async () => {
  for (const [rotulo, estado, espera] of [["updating", "updating", "acompanha"], ["em dia", "updated", "conclui"]]) {
    const { ctx, page, abrir, botao, s } = await abre();
    const falhas = [];
    page.on("requestfailed", (q) => { if (new URL(q.url()).pathname.endsWith("/refresh")) falhas.push(q.failure()?.errorText); });
    const portao = porta();
    s.refresh = async (r) => { await portao.p; await r.abort().catch(() => {}); };
    await abrir();
    s.conexoes = [con(11, "Nubank", estado, "x", "2026-10-02T14:00:00Z")];
    if (estado === "updated") emDia(s);
    await congelar(page);
    await botao.click();
    await page.waitForFunction(() => window.__req.some(([m]) => m === "POST"));
    const t0 = await instantePost(page), base = await marca(page);
    await avancar(page, 59_000, 1000, { preso: true });
    const antes = await gets(page, base);
    await avancar(page, 2000, 1000, { preso: true });
    const esperado = espera === "acompanha" ? 1 : 2;
    await page.waitForFunction(([n, d]) => window.__req.slice(d).filter(([m, p]) => m === "GET" && p === "/open-finance/7").length >= n, [esperado, base]);
    await dorme(300); // e prova que não sai um a mais
    const l = await gets(page, base);
    const corpo = (await page.locator(AVISO).count()) ? await texto(page) : "(sem aviso)";
    portao.soltar();
    await ctx.close();
    assert.deepEqual(antes, [], `${rotulo}: nenhum GET antes dos 60 s`);
    assert.equal(Math.round((l[0] - t0) / 1000), 60, `${rotulo}: o GET do snapshot sai aos 60 s`);
    assert.equal(l.length, esperado, `${rotulo}: o do prazo (e, se conclui, a releitura da recarga); nada mais`);
    assert.equal(falhas.length, 1, `${rotulo}: o POST foi abortado pela página`);
    if (espera === "acompanha") assert.ok(corpo.includes(ATUALIZANDO), corpo); else assert.equal(corpo, "(sem aviso)", "tudo em dia: o aviso saiu, sem texto de falha");
  }
});

test("W8: o POST que responde em 20 s é o desfecho normal (sucesso), sem GET do snapshot — é o controle que discrimina o prazo de 60 s", async () => {
  const { ctx, page, abrir, botao, s } = await abre();
  const portao = porta();
  s.refresh = async (r) => { await portao.p; emDia(s); await r.fulfill({ json: refreshDe([item("item-11")]) }); };
  await abrir();
  await congelar(page);
  const base = await marca(page);
  await botao.click();
  await page.waitForFunction(() => window.__req.some(([m]) => m === "POST"));
  await avancar(page, 20_000, 1000, { preso: true });
  portao.soltar();
  await assentar(page);
  await page.clock.runFor(10); await assentar(page);
  await page.locator(AVISO).waitFor({ state: "detached" });
  const t0 = await instantePost(page);
  const l = (await gets(page, base)).map((t) => Math.round((t - t0) / 1000));
  await ctx.close();
  assert.deepEqual(l, [20], "só a releitura da recarga, DEPOIS da resposta (20 s); nenhum GET aos 15 s nem aos 60");
});

test("W9: o X durante o acompanhamento: nenhum GET nos 2 min seguintes, nenhuma inserção na região; recarregando a página o aviso volta", async () => {
  const { ctx, page, abrir, botao, s } = await abre();
  s.refresh = (r) => r.fulfill({ json: refreshDe([item("item-11", "updated", true)], [con(11, "Nubank", "updating")]) });
  await abrir();
  s.conexoes = [con(11, "Nubank", "updating")];
  await congelar(page);
  await botao.click();
  await page.waitForFunction(() => document.querySelector("#avisos-topo .pilha-corpo")?.textContent.includes("O banco está atualizando"));
  await avancar(page, 6000);
  await page.evaluate(() => { window.__ins = 0; new MutationObserver((m) => { window.__ins += m.reduce((n, x) => n + x.addedNodes.length, 0); }).observe(document.querySelector("#avisos-topo p.sr-only[role=status]"), { childList: true, subtree: true, characterData: true }); });
  const base = await marca(page);
  await page.getByRole("button", { name: "Fechar aviso" }).click();
  await avancar(page, 120_000, 10_000);
  const r = { gets: await gets(page, base), aviso: await page.locator(AVISO).count(), ins: await page.evaluate(() => window.__ins), regiao: await page.locator(REGIAO).count() };
  await page.clock.resume(); await page.reload(); await page.locator(AVISO).waitFor();
  await ctx.close();
  assert.deepEqual(r.gets, []);
  assert.equal(r.aviso, 0);
  assert.equal(r.regiao, 0);
  assert.equal(r.ins, 0, "o X não anuncia nada");
});
