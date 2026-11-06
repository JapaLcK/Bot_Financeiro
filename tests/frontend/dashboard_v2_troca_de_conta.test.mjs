/**
 * /painel, troca de conta pelo marcador `pb_conta` (webapp/src/dashboard/lib/sessao.ts): marcador mudou ou sumiu = a página some e
 * recarrega uma vez, e nenhum dado da conta A entra na tela depois da troca.
 *
 *   · "viuA": um MutationObserver (init script, vale em toda carga) grava `sessionStorage.viuA` se um texto da A entrar no DOM depois
 *     de `sessionStorage.trocou` ser marcado (no mesmo evaluate que troca o marcador); as cargas contam por `page.on("load")`;
 *   · a conta A é o uid 7 (contas R$ 7.777,77, "Segredo da conta A", mês anterior R$ 7.111,11 / 7.333,33); a B, o uid 8;
 *   · prazos em `performance.now()` do Node: o `abrirPainel` congela o `Date` da página.
 *
 * N1–N8 falham se a proteção correspondente sair (a mutação está no nome); P1–P3 provam que a mesma conta não recarrega nem pede
 * mais. O logout (L1–L4b): dashboard_v2_troca_de_conta_logout.test.mjs; o pedaço do stream do chat: dashboard_v2_chat_ia.test.mjs; o
 * Cofre do MFA: dashboard_v2_conta_seguranca_cofre.test.mjs. O `abrir` comum: _troca_de_conta.mjs.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { performance } from "node:perf_hooks";
import { setTimeout as dorme } from "node:timers/promises";
import { join } from "node:path";
import { readFileSync } from "node:fs";
import { chromium } from "playwright";
import { FRONTEND, exigeArtefatoEmDia } from "./_painel.mjs";
import { CONTAS, PIGGY, abrir as abrirConta } from "./_troca_de_conta.mjs";
import { congelar } from "./_atualizar_banco.mjs";

let browser;
before(async () => { exigeArtefatoEmDia(); browser = await chromium.launch(); });
after(() => browser?.close());

const B_TXT = "8.888,88";
const abrir = (opts) => abrirConta(browser, opts);

// O marcador muda no navegador (como o Set-Cookie de outra aba) e o teste marca a troca no mesmo passo. `null` = o cookie sumiu.
// `acao`, no MESMO evaluate: "resumo" (volta ao Resumo) ou "oculta" (a aba some).
const trocar = (page, valor, acao) => page.evaluate(([v, a]) => {
  document.cookie = v === null ? "pb_conta=; max-age=0; path=/" : `pb_conta=${v}; path=/`;
  sessionStorage.setItem("trocou", "1");
  if (a === "resumo") location.hash = "#/";
  // "hidden": o focusManager do React Query não relê (só relê ao ficar visível), então só o ouvinte da sessão pode ver a troca.
  if (a === "oculta") {
    Object.defineProperty(document, "visibilityState", { value: "hidden", configurable: true });
    document.dispatchEvent(new Event("visibilitychange", { bubbles: true }));
  }
}, [valor, acao]);

test("N1 (sem o conferir do roteador): troca em outra aba com o Resumo fora da tela; voltar ao Resumo não mostra a A, recarrega e a B aparece", async () => {
  const { ctx, page, ir, s, contaA, viuA, recarga, cargas } = await abrir();
  await ir();
  await contaA();
  await page.evaluate(() => { location.hash = "#/conta/bancos"; });
  await page.locator(PIGGY).waitFor({ state: "detached" });
  await page.evaluate(() => new Promise((ok) => setTimeout(ok, 0))); // o GC do gcTime: 0 do Piggy notou já rodou; o bloco de contas (gcTime padrão) segue no cache
  s.uid = 8;
  await trocar(page, "8", "resumo");
  assert.notEqual(await recarga(2, 5000), null, "recarregou");
  await page.locator(CONTAS).getByText(B_TXT).waitFor({ timeout: 20000 });
  await page.locator(PIGGY).getByText("Aviso da conta B").waitFor({ timeout: 20000 });
  const r = [await viuA(), cargas()];
  await ctx.close();
  assert.deepEqual(r, [null, 2]);
});

test("N2 (sem o click): marcador muda com o intervalo parado; o clique no mês anterior (no cache) não mostra a A", async () => {
  const { ctx, page, ir, s, contaA, viuA, recarga } = await abrir({ relogio: "instalado" });
  await ir();
  await contaA();
  await page.getByRole("button", { name: "Mês anterior", exact: true }).click();
  await page.getByText("7.333,33").waitFor();
  await page.getByRole("button", { name: "Próximo mês", exact: true }).click();
  await page.getByText("7.111,11").waitFor();
  await congelar(page);
  s.uid = 8;
  await trocar(page, "8");
  await page.getByRole("button", { name: "Mês anterior", exact: true }).click({ noWaitAfter: true }).catch(() => {});
  const t = await recarga(2, 3000);
  const r = [await viuA(), t !== null];
  await ctx.close();
  assert.deepEqual(r, [null, true]);
});

test("N2b (sem o keydown): com o intervalo parado, a seta no 'Visão do donut' (dado já no cache, sem pedido) não redesenha a A", async () => {
  const { ctx, page, ir, s, viuA, recarga } = await abrir({ espera: "#page-title", relogio: "instalado" });
  await ir("/orcamento");
  const gasto = page.getByRole("radiogroup", { name: "Visão do donut" }).getByRole("radio", { name: "Gasto" });
  await gasto.waitFor({ timeout: 20000 });
  await gasto.focus();
  await congelar(page);
  s.uid = 8;
  await trocar(page, "8");
  await page.evaluate(() => new MutationObserver(() => {
    if (document.querySelector('[aria-label="Visão do donut"] [aria-checked="true"]')?.textContent === "Orçado") sessionStorage.setItem("viuA", "orcado");
  }).observe(document, { subtree: true, childList: true, characterData: true, attributes: true }));
  await page.keyboard.press("ArrowRight").catch(() => {});
  const t = await recarga(2, 3000);
  const r = [await viuA(), t !== null];
  await ctx.close();
  assert.deepEqual(r, [null, true]);
});

// O `<input type="range">` muda de valor (e chama o onChange) no mousedown/touchstart, antes de qualquer click.
async function milhaoTrocado() {
  const p = await abrir({ espera: "#page-title", relogio: "instalado" });
  await p.ir("/milhao");
  const range = p.page.locator(".mi-ctl input[type=range]").first();
  await range.waitFor({ timeout: 20000 });
  await congelar(p.page);
  p.s.uid = 8;
  await trocar(p.page, "8");
  await p.page.evaluate(() => {
    const antes = document.querySelector(".mi-quando").textContent;
    new MutationObserver(() => {
      const t = document.querySelector(".mi-quando")?.textContent;
      if (t !== antes && document.documentElement.style.visibility !== "hidden") sessionStorage.setItem("viuA", t);
    }).observe(document.querySelector(".mi-corpo"), { subtree: true, childList: true, characterData: true });
  });
  return { ...p, caixa: await range.boundingBox() };
}

test("N2c (sem o pointerdown): com o intervalo parado, só o mousedown no trilho do 'Guardar por mês' não recalcula a A", async () => {
  const { ctx, page, viuA, recarga, caixa: b } = await milhaoTrocado();
  await page.mouse.move(b.x + b.width * 0.6, b.y + b.height / 2);
  await page.mouse.down().catch(() => {});
  const t = await recarga(2, 3000);
  const r = [await viuA(), t !== null];
  await ctx.close();
  assert.deepEqual(r, [null, true]);
});

test("N2d (sem o pointerdown): com o intervalo parado, o arrasto por toque no 'Guardar por mês' (sem click) não recalcula a A", async () => {
  const { ctx, page, viuA, recarga, caixa: b } = await milhaoTrocado();
  const cdp = await ctx.newCDPSession(page);
  await cdp.send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 1 });
  const y = b.y + b.height / 2;
  // O ack de um toque enviado com a recarga já pedida nunca chega (medido): vale o que vier antes, a recarga ou o fim do arrasto.
  await Promise.race([page.waitForRequest((q) => q.isNavigationRequest(), { timeout: 0 }), (async () => {
    await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: b.x + 4, y }] });
    for (let i = 1; i <= 8; i++) await cdp.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x: b.x + 4 + i * b.width * 0.08, y }] });
    await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
  })().catch(() => {})]); // o envio preso rejeita no ctx.close
  const t = await recarga(2, 3000);
  const r = [await viuA(), t !== null];
  await ctx.close();
  assert.deepEqual(r, [null, true]);
});

test("N3 (sem o conferir depois do fetch e do json no chamar): a resposta da A chega depois da troca e não entra na tela", async () => {
  const { ctx, page, ir, s, viuA, recarga } = await abrir({ relogio: "instalado" });
  let chegou; const pedido = new Promise((ok) => { chegou = ok; });
  let soltar; const porta = new Promise((ok) => { soltar = ok; });
  s.insights = async () => { s.insights = null; chegou(); await porta; };
  await ir();
  await page.locator(CONTAS).getByText("7.777,77").waitFor({ timeout: 20000 });
  await pedido; // o GET /insights da A está no servidor
  await congelar(page);
  s.uid = 8; // o que vier depois é da B; o GET preso já leu o uid 7: o corpo dele é o da A
  await trocar(page, "8"); // o marcador vira 8 ANTES de a resposta da A sair
  soltar();
  await dorme(300);
  await page.clock.runFor(10); // as notificações do React Query (setTimeout 0) saem: sem a proteção, a A seria desenhada aqui
  await dorme(300);
  const t = await recarga(2, 3000);
  const r = [await viuA(), t !== null];
  await ctx.close();
  assert.deepEqual(r, [null, true]);
});

test("N3b (sem o conferir depois do json no chamar): a conta troca com os cabeçalhos da A já lidos e o corpo ainda chegando; ele não entra na tela", async () => {
  const { ctx, page, ir, s, viuA, recarga } = await abrir({ relogio: "instalado" });
  // O route.fulfill entrega cabeçalho e corpo juntos: aqui o 1º GET /insights da página resolve só com os cabeçalhos, e o corpo fica com o teste.
  await ctx.addInitScript(() => {
    const real = window.fetch.bind(window);
    window.fetch = (input, init) => {
      if (window.__corpo || new URL(typeof input === "string" ? input : input.url, location.href).pathname !== "/api/v2/insights") return real(input, init);
      return Promise.resolve(new Response(new ReadableStream({ start(c) { window.__corpo = c; } }), { headers: { "content-type": "application/json" } }));
    };
  });
  await ir();
  await page.locator(CONTAS).getByText("7.777,77").waitFor({ timeout: 20000 });
  await page.waitForFunction(() => window.__corpo);
  await congelar(page);
  s.uid = 8;
  await trocar(page, "8");
  await page.evaluate(() => {
    window.__corpo.enqueue(new TextEncoder().encode(JSON.stringify({ insights: [{ title: "Segredo da conta A", message: "x" }] })));
    window.__corpo.close();
  });
  await dorme(300);
  await page.clock.runFor(10);
  await dorme(300);
  const t = await recarga(2, 3000);
  const r = [await viuA(), t !== null];
  await ctx.close();
  assert.deepEqual(r, [null, true]);
});

test("N4 (sem o conferir antes do fetch): com o marcador trocado, a releitura que o aviso do SSE pede não manda pedido nenhum à /api/v2", async () => {
  // O `online` não relê neste painel (medido: nenhum GET); o aviso do /eventos invalida tudo sem gesto nenhum, como uma escrita no WhatsApp.
  const { ctx, page, ir, s, contaA, recarga, pedidos } = await abrir({ relogio: "instalado" });
  let avisar; s.eventos = new Promise((ok) => { avisar = ok; });
  await ir();
  await contaA();
  await congelar(page);
  s.uid = 8;
  await trocar(page, "8");
  const desde = pedidos.length;
  avisar();
  const t = await recarga(2, 3000);
  const nav = pedidos.findIndex(([, n], i) => i >= desde && n);
  const antes = pedidos.slice(desde, nav < 0 ? undefined : nav).filter(([p]) => p.startsWith("/api/v2/") && p !== "/api/v2/eventos");
  await ctx.close();
  assert.notEqual(t, null, "recarregou");
  assert.deepEqual(antes, [], "pedido à /api/v2 saiu com a sessão trocada");
});

test("N5 (marcador sumido tratado como nada): logout em outra aba recarrega em até 1,5 s; o login na B depois recarrega de novo, sem a A", async () => {
  const { ctx, page, ir, s, contaA, viuA, recarga, cargas, perfis } = await abrir();
  await ir();
  await contaA();
  s.uid = null;
  await trocar(page, null);
  const t = await recarga(2, 1500);
  assert.notEqual(t, null, "recarregou em até 1,5 s");
  await page.locator(CONTAS).getByText("7.777,77").waitFor({ state: "detached" });
  const t1 = performance.now();
  while (!perfis.includes(401)) { // a confirmação da carga sem marcador: 401 (dona nenhuma)
    assert.ok(performance.now() - t1 < 5000, "a confirmação da carga sem marcador (GET do perfil) não saiu em 5 s");
    await dorme(20);
  }
  s.uid = 8;
  await page.evaluate(() => { document.cookie = "pb_conta=8; path=/"; });
  assert.notEqual(await recarga(3, 3000), null, "recarregou de novo");
  await page.locator(CONTAS).getByText(B_TXT).waitFor({ timeout: 20000 });
  const r = [await viuA(), cargas()];
  await ctx.close();
  assert.deepEqual(r, [null, 3]);
});

test("N6 (no ramo sem marcador, adotar qualquer valor): sessão de antes do deploy (perfil 7) e o marcador aparece 8: recarrega, sem a A", async () => {
  const { ctx, page, ir, s, contaA, viuA, recarga } = await abrir({ marca: null });
  const perfil = page.waitForResponse((r) => new URL(r.url()).pathname === "/auth/dashboard-profile");
  await ir();
  await perfil;
  await contaA();
  s.uid = 8;
  await trocar(page, "8");
  const t = await recarga(2, 2500);
  const r = [t !== null, await viuA()];
  await ctx.close();
  assert.deepEqual(r, [true, null]);
});

test("N7 (sem o intervalo): o bloco na tela e nada acontece; o marcador muda e a página recarrega em até 2 s", async () => {
  const { ctx, page, ir, s, contaA, recarga } = await abrir();
  await ir();
  await contaA();
  s.uid = 8;
  await trocar(page, "8");
  const t = await recarga(2, 2000);
  await ctx.close();
  assert.notEqual(t, null);
});

test("N7b (sem o visibilitychange): com o relógio parado, a aba que some recarrega na hora", async () => {
  const { ctx, page, ir, s, contaA, recarga } = await abrir({ relogio: "instalado" });
  await ir();
  await contaA();
  await congelar(page);
  s.uid = 8;
  await trocar(page, "8", "oculta");
  const t = await recarga(2, 1500);
  await ctx.close();
  assert.notEqual(t, null);
});

test("N8 (sem esconder, ou escondendo tarde; sem a trava `saindo`): o /painel novo demora 2,5 s; a página da A some JÁ ao pedir a recarga, e recarrega UMA vez (o intervalo não reinicia a recarga)", async () => {
  const { ctx, page, ir, s, contaA, recarga, pedidos } = await abrir();
  // A visibilidade no `beforeunload`, que sai no instante em que a recarga é pedida (não no `pagehide`, 2,5 s depois). Registrado
  // antes de qualquer ouvinte da página (no `window`, a ordem é a de registro).
  await ctx.addInitScript(() => addEventListener("beforeunload", () => {
    if (sessionStorage.getItem("trocou") && sessionStorage.getItem("vis") === null) sessionStorage.setItem("vis", document.documentElement.style.visibility);
  }, true));
  await ir();
  await contaA();
  await ctx.route("**/painel.html", async (r) => { await dorme(2500); await r.fallback().catch(() => {}); });
  const desde = pedidos.length;
  s.uid = 8;
  await trocar(page, "8");
  const t = await recarga(2, 8000);
  const r = {
    recarregou: t !== null,
    navegacoes: pedidos.slice(desde).filter(([p, n]) => n && p === "/painel.html").length,
    vis: await page.evaluate(() => sessionStorage.getItem("vis")),
  };
  await ctx.close();
  assert.deepEqual(r, { recarregou: true, navegacoes: 1, vis: "hidden" });
});

test("P1 (conferir sempre recarrega): mesma conta, 3 idas e voltas (Metas), troca de mês e 3 s parado: 1 carga, um GET /insights por entrada, 0 GET de perfil, o bloco de contas volta do cache", async () => {
  const { ctx, page, ir, contaA, cargas, pedidos } = await abrir();
  await ir();
  await contaA();
  const voltas = [];
  for (let i = 0; i < 3; i++) {
    await page.evaluate(() => { location.hash = "#/metas"; });
    await page.locator(CONTAS).waitFor({ state: "detached" });
    // A 1ª amostra depois de voltar: o bloco de gcTime padrão já vem com o número (sem carregando).
    voltas.push(await page.evaluate(() => new Promise((ok) => {
      location.hash = "#/";
      const i = setInterval(() => { const w = document.querySelector('[data-widget-id="contas"]'); if (w) { clearInterval(i); ok(w.textContent.includes("7.777,77")); } }, 5);
    })));
    await page.locator(PIGGY).getByText("Segredo da conta A").waitFor();
  }
  await page.getByRole("button", { name: "Mês anterior", exact: true }).click();
  await page.getByText("7.333,33").waitFor();
  await dorme(3000);
  const r = {
    cargas: cargas(), voltas,
    insights: pedidos.filter(([p]) => p === "/api/v2/insights").length,
    perfil: pedidos.filter(([p]) => p === "/auth/dashboard-profile").length,
    visivel: await page.evaluate(() => document.documentElement.style.visibility),
  };
  await ctx.close();
  assert.deepEqual(r, { cargas: 1, voltas: [true, true, true], insights: 4, perfil: 0, visivel: "" });
});

test("P2 (sem o ramo da dona): sessão de antes do deploy (perfil 7) ganha o marcador 7: 1 carga, a página não some", async () => {
  const { ctx, page, ir, contaA, cargas, recarga } = await abrir({ marca: null });
  const perfil = page.waitForResponse((r) => new URL(r.url()).pathname === "/auth/dashboard-profile");
  await ir();
  await perfil;
  await contaA();
  await page.evaluate(() => { document.cookie = "pb_conta=7; path=/"; });
  const t = await recarga(2, 2500);
  const r = [t, cargas(), await page.evaluate(() => document.documentElement.style.visibility)];
  await ctx.close();
  assert.deepEqual(r, [null, 1, ""]);
});

test("P3: o servidor grava o marcador junto do HTML (o /painel do deploy novo): 1 carga, 0 GET de perfil", async () => {
  const { ctx, page, ir, contaA, cargas, pedidos } = await abrir({ marca: null });
  await ctx.route("**/painel.html", (r) => r.fulfill({
    headers: { "content-type": "text/html; charset=utf-8", "set-cookie": "pb_conta=7; Path=/; SameSite=Lax" },
    body: readFileSync(join(FRONTEND, "painel.html"), "utf8"),
  }));
  await ir();
  await contaA();
  await dorme(1500);
  const r = [cargas(), pedidos.filter(([p]) => p === "/auth/dashboard-profile").length, await page.evaluate(() => document.cookie.includes("pb_conta=7"))];
  await ctx.close();
  assert.deepEqual(r, [1, 0, true]);
});
