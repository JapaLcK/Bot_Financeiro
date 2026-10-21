// Ajudantes dos testes do chat do /painel com a IA (dashboard_v2_chat_ia*.test.mjs): o /painel com o freio `chat_ia` ligado e o
// `POST /ai/chat/stream` servido em PEDAÇOS que o teste controla. O `route.fulfill` do Playwright entrega o corpo inteiro de uma vez e não
// serve stream de verdade; por isso o `fetch` da página é trocado, só para essa URL, por uma Response com um ReadableStream que o teste
// enche e fecha byte a byte (corte no meio da linha, no meio de um caractere de 2 bytes). O resto passa pelo `fetch` real (e pelo `ctx.route`).
// Não é `*.test.mjs` de propósito: o `node --test tests/frontend/*.test.mjs` não o roda como suíte.
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { RESPOSTAS, abrirPainel, exigeArtefatoEmDia } from "./_painel.mjs";
export { RESPOSTAS };

let browser;
export const iniciar = async () => { exigeArtefatoEmDia(); browser = await chromium.launch(); };
export const encerrar = () => browser?.close();

export const PREPARANDO = "Preparando a resposta…";
export const PAROU = "Você parou esta resposta.";
export const SESSAO = "Sua sessão terminou. Recarregue a página.";
export const SEM_COTA = "Suas mensagens com o Piggy acabaram por este mês ou não fazem parte do seu plano.";
export const SEM_CARD = "Este card ainda não aparece aqui.";
export const GENERICO = "Não foi possível obter a resposta agora. Tente novamente em instantes.";
export const PRAZO = "A resposta demorou mais que o esperado. Tente de novo.";

const sse = (...eventos) => eventos.map((e) => `data: ${JSON.stringify(e)}\n\n`).join("");
export const fim = (reply, extra = {}) => ({ tipo: "fim", reply, message_id: 7, usage: { used: 3, limit: 100 }, chips: [], ...extra });
export const evento = sse;

/**
 * Abre o /painel#/piggy com `chat_ia` ligado (ou não). `conta.uid` é mutável: trocá-lo e disparar `visibilitychange` é a troca de conta em
 * outra aba. `cota` (mutável) é o uso que o GET /ai/messages informa. `log` guarda, em ordem, o que saiu para a rede: DELETE /ai/pending, GET /ai/messages, POST /ai/chat/stream, POST /auth/refresh.
 */
export async function abrir({ width = 1440, chatIa = true, uid = 1, usage = { used: 2, limit: 100 }, ...opts } = {}) {
  const { ctx, page, erros, ir } = await abrirPainel(browser, { width, espera: "#askbar-input", ...opts });
  const log = [], deletes = [], posts = [], conta = { uid }, cota = { ...usage };
  await ctx.route("**/api/v2/me", (r) => r.fulfill({ json: { ...RESPOSTAS.me[opts.plano ?? "pro"], chat_ia: chatIa } }));
  await ctx.route("**/auth/dashboard-profile", (r) => r.fulfill({ json: { user_id: conta.uid, display_name: null, email: null, whatsapp_linked: true } }));
  await ctx.route("**/ai/pending", (r) => {
    log.push(`${r.request().method()} /ai/pending`);
    deletes.push({ metodo: r.request().method(), csrf: r.request().headers()["x-csrf-token"] });
    return r.fulfill({ json: { cancelada: null } });
  });
  // O contrato real (`require_pro_feature("ai_chat")`, que exige used < limit): com a cota cheia o GET responde 403 pro_required, não 200 com 100/100.
  await ctx.route("**/ai/messages?limit=1", (r) => {
    log.push("GET /ai/messages");
    if (cota.used >= cota.limit) return r.fulfill({ status: 403, json: { detail: { error: "pro_required", feature: "ai_chat" } } });
    return r.fulfill({ json: { messages: [], usage: cota } });
  });
  await ctx.route("**/auth/refresh", (r) => { log.push("POST /auth/refresh"); return r.fulfill({ json: { ok: true } }); });
  await page.exposeFunction("__reg", (s, corpo, csrf) => { log.push(s); if (corpo) posts.push({ corpo, csrf }); });
  await ctx.addInitScript(() => {
    const real = window.fetch.bind(window);
    const S = window.__sse = { reqs: [], proximas: [], ignoraAbort: false };
    window.fetch = (input, init) => {
      const url = new URL(typeof input === "string" ? input : input.url, location.href);
      if (url.pathname !== "/ai/chat/stream" || init?.method !== "POST") return real(input, init);
      if (init.signal?.aborted) return Promise.reject(new DOMException("Aborted", "AbortError"));
      const req = { abortado: false, ctrl: null };
      window.__reg("POST /ai/chat/stream", JSON.parse(init.body), new Headers(init.headers).get("x-csrf-token")); // antes do push: quem espera `reqs.length` já encontra o log
      S.reqs.push(req);
      const prox = S.proximas.shift(); // {status, json, headers}: uma resposta que não é stream (401, 403, 429…)
      if (prox) return Promise.resolve(new Response(JSON.stringify(prox.json), { status: prox.status, headers: { "content-type": "application/json", ...prox.headers } }));
      const stream = new ReadableStream({ start(c) { req.ctrl = c; } });
      init.signal?.addEventListener("abort", () => { req.abortado = true; if (!S.ignoraAbort) try { req.ctrl.error(new DOMException("Aborted", "AbortError")); } catch { /* já fechado */ } });
      return Promise.resolve(new Response(stream, { status: 200, headers: { "content-type": "text/event-stream" } }));
    };
  });
  await ir("/piggy");
  await page.locator(".chat-empty").waitFor();
  const flux = {
    /** Manda texto (UTF-8) ou bytes para o stream `i`. */
    push: (i, dados) => page.evaluate(([i, d]) => {
      const bytes = typeof d === "string" ? new TextEncoder().encode(d) : Uint8Array.from(d);
      window.__sse.reqs[i].ctrl.enqueue(bytes);
    }, [i, dados]),
    fechar: (i) => page.evaluate((i) => window.__sse.reqs[i].ctrl.close(), i),
    abortado: (i) => page.evaluate((i) => window.__sse.reqs[i]?.abortado ?? false, i),
    proxima: (r) => page.evaluate((r) => window.__sse.proximas.push(r), r),
    ignoraAbort: () => page.evaluate(() => { window.__sse.ignoraAbort = true; }),
    espera: (n) => page.waitForFunction((n) => window.__sse.reqs.length >= n, n),
  };
  return { ctx, page, erros, log, deletes, posts, conta, cota, flux };
}

export const campo = (page) => page.locator("#askbar-input");
export const enviar = async (page, t) => { await campo(page).fill(t); await campo(page).press("Enter"); };
export const respostas = (page) => page.locator(".chat > .msg-piggy");
export const ultima = (page) => respostas(page).last();
export const texto = (page) => ultima(page).locator(".msg-ia").innerText().catch(() => "");
export const botao = (page) => page.locator(".askbar-send");
/** A troca de conta em outra aba: o foco relê o perfil, que chega ao `contaDaSessao`. */
export const trocarConta = async (page, conta, uid) => { conta.uid = uid; await page.evaluate(() => window.dispatchEvent(new Event("visibilitychange"))); };

/**
 * `POST /ai/messages/{id}/feedback` (o 👍👎): registra `{url, metodo, corpo, csrf}` em `f.reqs` e responde da fila `f.fila` (`{status, json}`, `{abortar: true}`
 * ou `{segurar: true}` = fica preso até o teste chamar `f.solta({status, json})`). Sem fila, 200. O glob não colide com `**\/ai/messages?limit=1`.
 */
export async function rotaFeedback(ctx) {
  const f = { reqs: [], fila: [], presos: [], solta: (resp = {}) => f.presos.shift()?.(resp) };
  await ctx.route("**/ai/messages/*/feedback", async (r) => {
    const req = r.request();
    f.reqs.push({ url: new URL(req.url()).pathname, metodo: req.method(), corpo: JSON.parse(req.postData()), csrf: req.headers()["x-csrf-token"] });
    let resp = f.fila.shift() ?? {};
    if (resp.segurar) resp = await new Promise((ok) => f.presos.push(ok));
    if (resp.abortar) return r.abort();
    return r.fulfill({ status: resp.status ?? 200, json: resp.json ?? { ok: true } });
  });
  return f;
}

// Ajudantes dos testes do 👍👎 (dashboard_v2_chat_ia_feedback*.test.mjs).
export const FALHA = "Não conseguimos registrar sua avaliação.";
export const esperaRotulo = (page) => page.waitForFunction(() => document.querySelector(".askbar-send")?.getAttribute("aria-label") === "Enviar", null, { timeout: 3000 });
export const ate = async (cond) => { for (let i = 0; i < 80 && !cond(); i++) await new Promise((r) => setTimeout(r, 25)); };
/** Uma conversa com a rota do feedback. */
export async function iniciarConversa(opts) {
  const c = await abrir(opts);
  c.f = await rotaFeedback(c.ctx);
  return c;
}
/** A n-ésima resposta (i = 0, 1…) já pronta com `message_id`. */
export async function responder({ page, flux }, i, id = 7, reply = `ok ${i}`) {
  await enviar(page, `pergunta ${i}`);
  await flux.espera(i + 1);
  await flux.push(i, evento(fim(reply, { message_id: id })));
  await esperaRotulo(page);
}
export const gostei = (page) => page.getByRole("button", { name: "Gostei da resposta", exact: true });
export const naoGostei = (page) => page.getByRole("button", { name: "Não gostei da resposta", exact: true });
export const motivo = (page, nome) => page.getByRole("button", { name: nome, exact: true });
export const marcado = (loc) => loc.getAttribute("aria-pressed");
export const alerta = (page) => page.getByRole("alert").filter({ hasText: FALHA });
/** Clica e espera o POST sair (e um respiro para a tela assentar). */
export async function clica(loc, f) {
  const n = f.reqs.length;
  await loc.click();
  await ate(() => f.reqs.length > n);
  assert.ok(f.reqs.length > n, "o clique tinha de mandar um POST");
  await loc.page().waitForTimeout(150);
}
