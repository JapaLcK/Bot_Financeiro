/**
 * /painel, troca de conta pelo marcador `pb_conta` (webapp/src/dashboard/lib/sessao.ts): marcador mudou ou sumiu = a página some e
 * recarrega uma vez, e nenhum dado da conta A entra na tela depois da troca.
 *
 *   · "viuA": um MutationObserver (init script, vale em toda carga) grava `sessionStorage.viuA` se um texto da A entrar no DOM depois
 *     de `sessionStorage.trocou` ser marcado (no mesmo evaluate que troca o marcador); as cargas contam por `page.on("load")`;
 *   · a conta A é o uid 7 (contas R$ 7.777,77, "Segredo da conta A", mês anterior R$ 7.111,11 / 7.333,33); a B, o uid 8;
 *   · prazos em `performance.now()` do Node: o `abrirPainel` congela o `Date` da página.
 *
 * N1–N8 e L1–L2 falham se a proteção correspondente sair (a mutação está no nome); P1–P3 provam que a mesma conta não recarrega nem
 * pede mais. O pedaço do stream do chat: dashboard_v2_chat_ia.test.mjs; o Cofre do MFA: dashboard_v2_conta_seguranca_cofre.test.mjs.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { performance } from "node:perf_hooks";
import { setTimeout as dorme } from "node:timers/promises";
import { join } from "node:path";
import { readFileSync } from "node:fs";
import { chromium } from "playwright";
import { FRONTEND, RESPOSTAS, abrirPainel, exigeArtefatoEmDia } from "./_painel.mjs";
import { congelar } from "./_atualizar_banco.mjs";

let browser;
before(async () => { exigeArtefatoEmDia(); browser = await chromium.launch(); });
after(() => browser?.close());

const A_TXT = ["Segredo da conta A", "7.777,77", "7.111,11", "7.333,33"];
const B_TXT = "8.888,88";
const CONTAS = '[data-widget-id="contas"]';
const PIGGY = '[data-widget-id="piggy"]';

/** O painel da conta A. `s.uid` decide o que as rotas respondem (8 = a B; `null` = deslogado: 401 no perfil e na /api/v2). */
async function abrir(opts = {}) {
  const p = await abrirPainel(browser, { perfil: "padrao", marca: "7", ...opts });
  const s = { uid: 7, insights: null, eventos: null };
  const { ctx, page } = p;
  const pedidos = []; // [pathname, é a navegação do documento]
  let cargas = 0;
  page.on("load", () => { cargas += 1; });
  page.on("request", (q) => pedidos.push([new URL(q.url()).pathname, q.isNavigationRequest()]));
  const perfis = []; // status de cada resposta do perfil
  page.on("response", (r) => { if (new URL(r.url()).pathname === "/auth/dashboard-profile") perfis.push(r.status()); });
  await ctx.addInitScript((A) => {
    const ve = (n) => A.some((a) => (n.textContent || "").includes(a));
    new MutationObserver((ms) => {
      if (!sessionStorage.getItem("trocou")) return;
      for (const m of ms) if ((m.type === "characterData" && ve(m.target)) || [...m.addedNodes].some(ve)) sessionStorage.setItem("viuA", "1");
    }).observe(document, { childList: true, subtree: true, characterData: true });
  }, A_TXT);
  await ctx.route("**/auth/dashboard-profile", (r) => (s.uid === null
    ? r.fulfill({ status: 401, headers: { "WWW-Authenticate": "Bearer" }, json: { detail: "x" } })
    : r.fulfill({ json: { user_id: s.uid, display_name: null, email: null, whatsapp_linked: true } })));
  await ctx.route("**/api/v2/contas", (r) => r.fulfill({ json: { ...RESPOSTAS.contas.todos_os_estados, total: s.uid === 7 ? "7777.77" : "8888.88" } }));
  await ctx.route((u) => u.pathname === "/api/v2/resumo-do-mes", (r) => {
    const mes = new URL(r.request().url()).searchParams.get("mes") ?? "2026-10";
    const ant = s.uid === 7 ? (mes === "2026-10" ? "7111.11" : "7333.33") : "8111.11";
    return r.fulfill({ json: { ...RESPOSTAS.resumo_do_mes.exato, mes, ate: `${mes}-28`, anterior: { mes: "2026-08", entrou: ant, saiu: "1.00" } } });
  });
  await ctx.route("**/api/v2/insights", async (r) => {
    const uid = s.uid;
    if (s.insights) await s.insights(r);
    await r.fulfill({ json: { insights: [{ title: uid === 7 ? "Segredo da conta A" : "Aviso da conta B", message: "x" }] } }).catch(() => {});
  });
  // O SSE: pendente para sempre (como o harness), ou, com `s.eventos` (uma promise), um aviso quando ela resolver: a releitura sem gesto.
  await ctx.route("**/api/v2/eventos", async (r) => {
    if (!s.eventos) return;
    await s.eventos;
    await r.fulfill({ headers: { "content-type": "text/event-stream" }, body: 'data: {"recurso":"tudo"}\n\n' }).catch(() => {});
  });
  // Por último (vence): deslogado, toda a /api/v2 dá 401; senão segue para as rotas acima.
  await ctx.route("**/api/v2/**", (r) => (s.uid === null ? r.fulfill({ status: 401, headers: { "WWW-Authenticate": "Bearer" }, json: { detail: "x" } }) : r.fallback()));
  const contaA = async () => {
    await page.locator(CONTAS).getByText("7.777,77").waitFor({ timeout: 20000 });
    await page.locator(PIGGY).getByText("Segredo da conta A").waitFor({ timeout: 20000 });
  };
  const viuA = () => page.evaluate(() => sessionStorage.getItem("viuA"));
  /** Espera `n` cargas; devolve quanto levou (ms) ou `null` se não veio em `ms`. */
  const recarga = async (n, ms) => {
    const t0 = performance.now();
    while (cargas < n) { if (performance.now() - t0 > ms) return null; await dorme(20); }
    return performance.now() - t0;
  };
  return { ...p, s, pedidos, perfis, cargas: () => cargas, contaA, viuA, recarga };
}

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
  try {
    await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: b.x + 4, y }] });
    for (let i = 1; i <= 8; i++) await cdp.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x: b.x + 4 + i * b.width * 0.08, y }] });
    await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
  } catch { /* a página já recarregou no meio do arrasto */ }
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
  // A visibilidade no `beforeunload`, que sai no instante em que a recarga é pedida (não no `pagehide`, 2,5 s depois). Em captura no
  // `window` e registrado antes do da sessão, que corta os outros quando está saindo.
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

// O logout apaga o marcador (`_clear_session_cookies`): o reload da troca não pode cancelar a navegação de saída. O `/` demora 2,5 s.
async function comSaidaLenta() {
  const p = await abrir();
  await p.ctx.route((u) => u.pathname === "/", async (r) => { await dorme(2500); await r.fulfill({ contentType: "text/html", body: "<html><body>saiu</body></html>" }).catch(() => {}); });
  await p.ir();
  await p.contaA();
  return p;
}
const ondeTermina = (page) => page.waitForURL("**/?logout=1", { timeout: 6000 }).then(() => page.url(), () => page.url());

test("L1 (sem a trava no `sair` nem no `sairPara`): o 'Sair' daqui termina em /?logout=1, e não no /painel recarregado", async () => {
  const { ctx, page } = await comSaidaLenta();
  await ctx.route("**/auth/logout", (r) => r.fulfill({ headers: { "set-cookie": "pb_conta=; Max-Age=0; Path=/" }, json: {} }));
  await page.locator('button[title="Sair"]').click();
  const url = await ondeTermina(page);
  await ctx.close();
  assert.ok(url.endsWith("/?logout=1"), url);
});

test("L1b (trava só no `fim` do `sair`): a limpeza do aparelho (auth-refresh.js) demora 1,2 s depois da resposta do logout; o 'Sair' termina em /?logout=1", async () => {
  const { ctx, page, ir, contaA } = await abrir();
  await ctx.addInitScript(() => {
    const sw = navigator.serviceWorker;
    if (sw) { const real = sw.getRegistrations.bind(sw); sw.getRegistrations = () => new Promise((ok) => setTimeout(() => ok(real()), 1200)); }
  });
  await ctx.route((u) => u.pathname === "/", (r) => r.fulfill({ contentType: "text/html", body: "<html><body>saiu</body></html>" }));
  await ir();
  await contaA();
  await ctx.route("**/auth/logout", (r) => r.fulfill({ headers: { "set-cookie": "pb_conta=; Max-Age=0; Path=/" }, json: {} }));
  await page.locator('button[title="Sair"]').click();
  await dorme(3700); // sem a trava no começo, o intervalo (1 s) recarrega o /painel antes de a limpeza acabar
  const url = page.url();
  await ctx.close();
  assert.ok(url.endsWith("/?logout=1"), url);
});

test("L2 (sem a trava no `sairPara`): o 'Sair' de outra aba leva esta a /?logout=1, mesmo com o marcador sumindo no caminho", async () => {
  const { ctx, page } = await comSaidaLenta();
  await ctx.route("**/outra-aba", (r) => r.fulfill({ contentType: "text/html", body: "<html></html>" }));
  const outra = await ctx.newPage();
  await outra.goto(new URL("/outra-aba", page.url()).href);
  const saiu = page.waitForRequest((q) => q.isNavigationRequest() && new URL(q.url()).search === "?logout=1");
  await outra.evaluate(() => localStorage.setItem("finbot_logout_at", String(Date.now()))); // o `sair` da outra aba
  await saiu;
  await outra.evaluate(() => { document.cookie = "pb_conta=; max-age=0; path=/"; }); // a resposta do logout dela chega
  const url = await ondeTermina(page);
  await ctx.close();
  assert.ok(url.endsWith("/?logout=1"), url);
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
