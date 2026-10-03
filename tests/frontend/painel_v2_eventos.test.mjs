/**
 * /painel: avisos ao vivo do GET /api/v2/eventos (SSE), webapp/src/dashboard/lib/eventos.ts.
 *
 *   · stream aberto refaz as consultas (o /me), e cada aviso também;
 *   · stream recusado (401) com o /me de pé: abre o stream de novo;
 *   · stream recusado e o /me também (refresh 401): tela de erro, e o stream não volta;
 *   · o portão desmonta com o refetch do /me ainda em voo: o stream não volta;
 *   · o protótipo (dashboard-v2/index.html) não abre stream.
 *
 * O stream é servido com `r.fulfill` e `retry: 600000`: o corpo acaba, o EventSource
 * fica em CONNECTING e só reconectaria em 10 min — nenhum pedido a mais no teste.
 *
 * Rodar:  npm run test:frontend   (abre o artefato commitado: mudou webapp/src, rode
 *         `npm --prefix webapp run build`)
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { PAINEL, PROTOTIPO, RAIZ, RESPOSTAS, exigeArtefatoEmDia, servir } from "./_painel.mjs";

let browser;
before(async () => { exigeArtefatoEmDia(); browser = await chromium.launch(); });
after(() => browser?.close());

const ERRO = "Não deu para carregar o painel";
const sse = (corpo) => (r) => r.fulfill({ status: 200, headers: { "content-type": "text/event-stream" }, body: corpo });
const recusa401 = (r) => r.fulfill({ status: RESPOSTAS.erros["401"].status, headers: RESPOSTAS.erros["401"].headers, json: RESPOSTAS.erros["401"].body });

async function abrir({ raiz, url = `${PAINEL}#/` } = {}) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: "reduce" });
  await servir(ctx, raiz);
  await ctx.addInitScript(() => localStorage.setItem("pigbank.dashboard.profile.v1", '"padrao"'));
  const page = await ctx.newPage();
  const n = { me: 0, eventos: 0, erros: [] };
  page.on("pageerror", (e) => n.erros.push(e.message));
  page.on("request", (r) => {
    const p = new URL(r.url()).pathname;
    if (p === "/api/v2/me") n.me++;
    if (p === "/api/v2/eventos") n.eventos++;
  });
  return { ctx, page, n, ir: () => page.goto(url) };
}

const montado = (page) => page.locator("#board-profile").waitFor();
// Espera o contador chegar a `alvo`, ou o prazo acabar (e devolve o que tiver).
async function ate(n, campo, alvo, prazo = 4000) {
  const fim = Date.now() + prazo;
  while (n[campo] < alvo && Date.now() < fim) await new Promise((r) => setTimeout(r, 50));
  return n[campo];
}

for (const [nome, corpo, me] of [
  ["stream aberto (sem aviso) refaz o /me", "retry: 600000\n\n", 2],
  ["aviso refaz o /me de novo", 'retry: 600000\ndata: {"recurso":"open_finance"}\n\n', 3],
]) {
  test(nome, async () => {
    const { ctx, page, n, ir } = await abrir();
    await ctx.route("**/api/v2/eventos", sse(corpo));
    await ir();
    await montado(page);
    await ate(n, "me", me);
    await page.waitForTimeout(500); // um /me a mais apareceria aqui
    const r = [n.eventos, n.me, n.erros];
    await ctx.close();
    assert.deepEqual(r, [1, me, []]);
  });
}

test("stream recusado (401) com o /me de pé: abre o stream de novo", async () => {
  const { ctx, page, n, ir } = await abrir();
  let vezes = 0;
  await ctx.route("**/api/v2/eventos", (r) => (vezes++ === 0 ? recusa401(r) : undefined));
  await ir();
  await montado(page);
  const eventos = await ate(n, "eventos", 2);
  const r = [eventos, n.me, await page.locator("#board-profile").count(), n.erros];
  await ctx.close();
  assert.deepEqual(r, [2, 2, 1, []]);
});

test("stream recusado e o /me também (refresh 401): tela de erro, e o stream não volta", async () => {
  const { ctx, page, n, ir } = await abrir();
  let me = 0;
  await ctx.route("**/api/v2/me", (r) => (me++ === 0 ? r.fulfill({ json: RESPOSTAS.me.pro }) : recusa401(r)));
  await ctx.route("**/auth/refresh", (r) => r.fulfill({ status: 401, json: { detail: "x" } }));
  await ctx.route("**/api/v2/eventos", recusa401);
  await ir();
  await page.getByRole("heading", { name: ERRO }).waitFor({ timeout: 15000 });
  await page.waitForTimeout(2500); // a reabertura viria em 1 s
  const r = [n.eventos, n.me, n.erros];
  await ctx.close();
  assert.deepEqual(r, [1, 2, []]);
});

// O guard `if (!vivo) return` do onerror. A ordem que só ele segura: o portão desmonta
// <Eventos/> ANTES de o refetch do /me (disparado pelo stream recusado) voltar. Na vida
// real é a janela do setTimeout(0) do notifyManager: o /me do onopen (G) falha e o
// stream é recusado antes de o React renderizar o erro. O relógio falso segura essa
// janela aberta; o refetch (F) fica pendurado até o portão já ter desmontado.
test("portão desmonta com o refetch do /me em voo: o stream não reabre", async () => {
  const { ctx, page, n, ir } = await abrir();
  const erro = (r) => r.fulfill({ status: RESPOSTAS.erros["403"].status, json: RESPOSTAS.erros["403"].body });
  let soltaEs, soltaF, gFalhou;
  const esSolta = new Promise((r) => { soltaEs = r; });
  const fSolta = new Promise((r) => { soltaF = r; });
  const gFeito = new Promise((r) => { gFalhou = r; });
  let me = 0;
  await ctx.route("**/api/v2/me", async (r) => {
    me++;
    if (me === 1) return r.fulfill({ json: RESPOSTAS.me.pro });
    if (me === 2) { await erro(r); return gFalhou(); } // G: o /me do onopen
    await fSolta; return erro(r); // F: o refetch do onerror, pendurado
  });
  let es = 0;
  await ctx.route("**/api/v2/eventos", async (r) => {
    if (++es === 1) { await esSolta; return sse("retry: 300\n\n")(r); } // abre e acaba: reconecta em 300 ms
    // Margem para o G já ter virado erro. Sem ela o refetch cancelaria o G, nada desmonta
    // e o waitFor do título abaixo falha: vermelho, nunca verde falso.
    await gFeito; await new Promise((ok) => setTimeout(ok, 300));
    return recusa401(r);
  });
  await ir();
  await montado(page);
  // Daqui em diante o setTimeout(0) do notifyManager (que leva o erro ao React) só anda à mão.
  await page.clock.install();
  await page.clock.pauseAt(Date.now() + 1000);
  soltaEs();
  await ate(n, "me", 3, 8000);
  await page.clock.runFor(1); // renderiza o erro do G: <Eventos/> desmonta com o F em voo
  await page.getByRole("heading", { name: ERRO }).waitFor({ timeout: 2000 });
  soltaF();
  await page.waitForTimeout(300); // o F volta e o onerror continua
  await page.clock.runFor(5000); // a reabertura (1 s) viria aqui
  await page.waitForTimeout(500);
  const r = [n.eventos, n.me, n.erros];
  await ctx.close();
  assert.deepEqual(r, [2, 3, []]);
});

test("protótipo: não abre o stream", async () => {
  const { ctx, page, n, ir } = await abrir({ raiz: RAIZ, url: `${PROTOTIPO}#/` });
  await ir();
  await montado(page);
  await page.waitForTimeout(500);
  const r = [n.eventos, n.me];
  await ctx.close();
  assert.deepEqual(r, [0, 0]);
});
