// O que os testes do card `simulador` da IA dividem (dashboard_v2_chat_ia_cards_simulador*.test.mjs): o /painel com o card na conversa e a rota `POST /api/v2/simulador/habitos`
// DO TESTE, que guarda cada corpo, segura a resposta quando ele quer e a marca (`economia_mensal` = a marca, ex.: 987.65 para "DE-B"). Um MutationObserver grava TODO texto que
// entra no DOM (`vistos`), para pegar a marca mesmo que viva um quadro só. Não é `*.test.mjs` de propósito (ver _chat_ia.mjs).
import { RESPOSTAS, abrir, enviar, evento, fim, ultima } from "./_chat_ia.mjs";
import { pronto } from "./_chat_ia_lanc.mjs";

export { ate } from "./_chat_ia_lanc.mjs";
export const PREV = RESPOSTAS.previsao.pro90_com_estimativa; // Mercado 180, Transporte 90, Lazer 60
export const GENERICO = "Não deu para calcular agora.";
export const MUDOU = "Seus gastos do dia a dia mudaram. Ajuste de novo.";
export const SEM_PLANO = "Simulador indisponível no seu plano.";
export const SESSAO = "Sua sessão terminou. Recarregue a página.";
const erro = (status, code, details) => ({ status, json: { error: { code, message: "x", details } } });
const E = RESPOSTAS.erros;
const ERROS = {
  "500": erro(500, "internal_error"), "401": erro(401, "unauthorized"),
  "403pro": { status: 403, json: E["403_pro_required"].body }, "403": { status: 403, json: E["403_csrf"].body },
  "422": erro(422, "validation_error", [{ loc: ["body", "cortes", 0, "categoria"], msg: "x", type: "categoria_fora_da_estimativa" }]),
  "422outro": erro(422, "validation_error", [{ loc: ["body", "cortes"], msg: "x", type: "categorias_repetidas" }]),
};

/** A resposta do POST para `corpo`: a base do servidor com os cortes pedidos; `economia_mensal` = `marca` (ou a soma dos cortes). */
export function respostaDe(corpo, marca) {
  const cats = Object.fromEntries(PREV.estimativa_variavel.categorias.map((c) => [c.chave, c]));
  const cortes = corpo.cortes.map((c) => ({ chave: c.categoria, nome: cats[c.categoria].nome, despesa_mensal: cats[c.categoria].despesa_mensal, percentual: c.percentual,
    economia_mensal: (Number(cats[c.categoria].despesa_mensal) * c.percentual / 100).toFixed(2), valor_pedido: null }));
  return { ...RESPOSTAS.simulador.habitos, cortes, economia_mensal: marca ?? cortes.reduce((a, c) => a + Number(c.economia_mensal), 0).toFixed(2) };
}

/** Mexe e solta o controle `loc`, como o ponteiro: `input` (rascunho) e depois `change` (o corte). O setter do protótipo passa pela detecção de mudança do React. */
export const mover = (loc, v) => loc.evaluate((el, v) => {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(el, String(v));
  el.dispatchEvent(new Event("input", { bubbles: true })); el.dispatchEvent(new Event("change", { bubbles: true }));
}, v);

/**
 * O /painel com o card `simulador` (`recursos`, na aba `mes`) na conversa. `t.modo` (e `t.modos[n]`, o do n-ésimo POST): ok | 500 | 403pro | 403 | 401 | 422 | 422outro | malformado |
 * preso (espera `t.soltar(n, marca)`) | pendurado. `t.perfil = "pendurado"`: o /auth/dashboard-profile nunca responde. `opts.sse`: o stream de avisos fica preso até `t.soltaSse()`.
 */
export async function prepararSim(opts = {}, { recursos = { previsao: PREV }, mes = "2026-10", cards = 1 } = {}) {
  const { sse, ...resto } = opts;
  const c = await abrir(resto);
  const t = { corpos: [], ordem: [], modo: "ok", modos: {}, presos: {}, falhas: [], perfil: "ok", gets: 0, me: 0, soltaSse: null, soltar: (n, marca) => t.presos[n](marca) };
  if (sse) {
    await c.ctx.route("**/api/v2/eventos", async (r) => { await new Promise((ok) => { t.soltaSse = ok; }); return r.fulfill({ status: 200, headers: { "content-type": "text/event-stream" }, body: 'retry: 600000\ndata: {"recurso":"open_finance"}\n\n' }); });
    await c.page.reload();
    await c.page.locator("#askbar-input").waitFor();
  }
  c.page.on("request", (r) => { const p = new URL(r.url()).pathname; if (p === "/api/v2/me") t.me++; if (p === "/api/v2/previsao") t.gets++; });
  await c.ctx.route("**/auth/dashboard-profile", (r) => { t.ordem.push("perfil"); if (t.perfil === "pendurado") return new Promise(() => {}); return r.fulfill({ json: { user_id: c.conta.uid, display_name: null, email: null, whatsapp_linked: true } }); });
  await c.ctx.route("**/api/v2/simulador/habitos", async (r) => {
    const corpo = r.request().postDataJSON(), n = t.corpos.length;
    t.corpos.push(corpo); t.ordem.push("POST");
    const modo = t.modos[n] ?? t.modo;
    if (modo === "pendurado") return new Promise(() => {});
    try {
      if (modo === "preso") { const marca = await new Promise((ok) => { t.presos[n] = ok; }); return await r.fulfill({ json: respostaDe(corpo, marca) }); }
      if (modo === "malformado") { const ok = respostaDe(corpo); return await r.fulfill({ json: { ...ok, economia_mensal: "abc", atual: { ...ok.atual, trajetoria: {} } } }); }
      return await r.fulfill(ERROS[modo] ?? { json: respostaDe(corpo) });
    } catch { /* o pedido foi abortado enquanto a rota esperava */ }
  });
  c.page.on("requestfailed", (r) => { if (r.url().includes("/api/v2/simulador/habitos")) t.falhas.push(r.failure()?.errorText); });
  await c.page.evaluate(() => {
    window.__vistos = [];
    new MutationObserver((ms) => { for (const m of ms) { for (const n of m.addedNodes) window.__vistos.push(n.textContent); if (m.type === "characterData") window.__vistos.push(m.target.textContent); } })
      .observe(document.documentElement, { childList: true, subtree: true, characterData: true });
  });
  await enviar(c.page, "simula cortar o mercado");
  await c.flux.espera(1);
  const bloco = { tipo: "bloco", bloco: { card: "simulador", abas: [{ mes, recursos }] } };
  await c.flux.push(0, evento(...Array(cards).fill(bloco), fim("Aqui está."))); // `cards`: quantos blocos na mesma resposta
  await ultima(c.page).locator(".card-ia, .msg-bloco").first().waitFor(); // o card, ou o marcador de card que não aparece
  await pronto(c.page);
  const card = c.page.locator(".card-ia");
  const alavanca = (nome) => card.locator(".lever-habito", { hasText: nome }).locator("input[type=range]");
  const soltar = (nome, v) => mover(alavanca(nome), v);
  const vistos = () => c.page.evaluate(() => window.__vistos.join("\n"));
  const cardSumiu = () => c.page.waitForFunction(() => document.querySelectorAll(".card-ia").length === 0 && document.querySelectorAll(".chat > .msg-piggy").length === 0, null, { timeout: 5000 });
  const sai = () => card.locator("[aria-live=polite]");
  return { ...c, t, card, alavanca, soltar, vistos, cardSumiu, sai };
}
