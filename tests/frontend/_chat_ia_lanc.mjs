// O que os testes da ação de LEITURA dos cards da IA dividem (dashboard_v2_chat_ia_cards_lancamentos*.test.mjs): o /painel com um card na conversa e a rota
// `/api/v2/lancamentos` DO TESTE, que guarda a URL, segura a resposta quando ele quer e marca as descrições (`DE-A-…`, `DE-B-…`). Um MutationObserver grava TODO
// texto que entra no DOM, para pegar o `DE-B` mesmo que viva um quadro só. Não é `*.test.mjs` de propósito (ver _chat_ia.mjs).
import assert from "node:assert/strict";
import { RESPOSTAS, abrir, blocoDe, enviar, evento, fim, ultima } from "./_chat_ia.mjs";

const F = RESPOSTAS;
export const FALHA = "Não deu para carregar os lançamentos.";
export const SESSAO = "Sua sessão terminou. Recarregue a página.";
export const pronto = (page) => page.waitForFunction(() => document.querySelector(".askbar-send")?.getAttribute("aria-label") === "Enviar", null, { timeout: 5000 });
export const ate = async (cond, ms = 6000) => { for (let i = 0; i < ms / 25 && !cond(); i++) await new Promise((r) => setTimeout(r, 25)); assert.ok(cond(), "a condição não chegou a tempo"); };
const marcada = (u, marca, repete = false) => {
  const base = u.searchParams.has("cursor") ? F.lancamentos.segunda : F.lancamentos.estados;
  const itens = repete && u.searchParams.has("cursor") ? [F.lancamentos.estados.itens[0], ...base.itens] : base.itens; // a lista é lida ao vivo: a página 2 repete a última linha da 1
  return { ...base, mes: u.searchParams.get("mes"), itens: itens.map((i) => ({ ...i, descricao: `${marca}-${i.id}` })) };
};
export const ERRO500 = { status: 500, json: { error: { code: "internal_error", message: "x" } } };

/** O /painel com o card `card` (os `recursos`) na conversa e a rota de lançamentos do teste. `antes(page)` roda depois do card (ex.: abrir uma linha que não busca nada); `linha` é o seletor da linha que abre a busca. `t.modo`: ok | 500 | 403 | 401 | preso (espera `t.soltar(marca)`) | pendurado (nunca responde).
 * `t.perfil = "pendurado"`: o /auth/dashboard-profile nunca responde. `t.itens`: os `itens` malformados que a rota devolve. `opts.sse`: o stream de avisos fica preso até `t.soltaSse()` (e `t.me` conta as leituras do /me). */
export async function prepararCard(opts = {}, { card = "contas", recursos, antes, linha: seletor = ".card-ia .ci-linha[aria-expanded]" }) {
  const { sse, ...resto } = opts;
  const c = await abrir(resto);
  const t = { gets: [], ordem: [], modo: "ok", repete: false, soltar: null, marca: "DE-A", falhas: [], emPagina2: false, perfil: "ok", itens: null, soltaSse: null, me: 0 };
  if (sse) {
    await c.ctx.route("**/api/v2/eventos", async (r) => { await new Promise((ok) => { t.soltaSse = ok; }); return r.fulfill({ status: 200, headers: { "content-type": "text/event-stream" }, body: 'retry: 600000\ndata: {"recurso":"open_finance"}\n\n' }); });
    await c.page.reload();
    await c.page.locator("#askbar-input").waitFor();
  }
  c.page.on("request", (r) => { if (new URL(r.url()).pathname === "/api/v2/me") t.me++; });
  await c.ctx.route("**/auth/dashboard-profile", (r) => { t.ordem.push("perfil"); if (t.perfil === "pendurado") return new Promise(() => {}); return r.fulfill({ json: { user_id: c.conta.uid, display_name: null, email: null, whatsapp_linked: true } }); });
  await c.ctx.route("**/api/v2/lancamentos*", async (r) => {
    const u = new URL(r.request().url());
    t.gets.push(u); t.ordem.push("GET");
    const pagina2 = u.searchParams.has("cursor");
    if (t.modo === "pendurado") return new Promise(() => {});
    if (t.itens) return r.fulfill({ json: { ...F.lancamentos.estados, itens: t.itens } });
    if (t.modo === "preso" && (!t.emPagina2 || pagina2)) { const marca = await new Promise((ok) => { t.soltar = ok; }); return r.fulfill({ json: marcada(u, marca) }); }
    if (t.modo === "500") return r.fulfill(ERRO500);
    if (t.modo === "403") return r.fulfill({ status: 403, json: { error: { code: "forbidden", message: "x" } } });
    if (t.modo === "401") return r.fulfill({ status: 401, json: { error: { code: "unauthorized", message: "x" } } });
    return r.fulfill({ json: marcada(u, t.marca, t.repete) });
  });
  c.page.on("requestfailed", (r) => { if (r.url().includes("/api/v2/lancamentos")) t.falhas.push(r.failure()?.errorText); });
  await c.page.evaluate(() => {
    window.__vistos = [];
    new MutationObserver((ms) => { for (const m of ms) { for (const n of m.addedNodes) window.__vistos.push(n.textContent); if (m.type === "characterData") window.__vistos.push(m.target.textContent); } })
      .observe(document.documentElement, { childList: true, subtree: true, characterData: true });
  });
  await enviar(c.page, "minhas contas");
  await c.flux.espera(1);
  await c.flux.push(0, evento(blocoDe(card, recursos), fim("Aqui estão.")));
  await ultima(c.page).locator(".card-ia").waitFor();
  await pronto(c.page);
  await antes?.(c.page);
  const linha = c.page.locator(seletor).first(); // contas: a 1ª conta do total (id 1, Nubank)
  const vistos = () => c.page.evaluate(() => window.__vistos.join("\n"));
  const linhas = () => c.page.locator(".card-ia .ci-lanc .lanc-linha");
  const cardSumiu = () => c.page.waitForFunction(() => document.querySelectorAll(".card-ia").length === 0 && document.querySelectorAll(".chat > .msg-piggy").length === 0, null, { timeout: 5000 });
  return { ...c, t, linha, vistos, linhas, cardSumiu };
}

