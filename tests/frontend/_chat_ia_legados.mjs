// Ajudantes dos testes dos 6 cards "legados" da IA no chat do /painel (PR 6 da Etapa 7; dashboard_v2_chat_ia_cards_legados*.test.mjs): caixinhas, fatura, parcelas, renda, piggy
// e rendimento. Fixtures: tests/fixtures/ai_chat_recurso_*.json (as 5 legadas conferidas contra o bloco REAL e o rendimento contra o Pydantic: tests/test_ai_chat_cards_fixtures_painel.py).
// Não é `*.test.mjs` de propósito.
import { abrir, enviar, evento, fim, fixture } from "./_chat_ia.mjs";

export const FX = {
  caixinhas: fixture("ai_chat_recurso_caixinhas.json"), cartoes: fixture("ai_chat_recurso_cartoes.json"), parcelas: fixture("ai_chat_recurso_parcelas.json"),
  renda: fixture("ai_chat_recurso_renda.json"), insights: fixture("ai_chat_recurso_insights.json"), rendimento: fixture("ai_chat_recurso_rendimento.json"),
};
export const pronto = (page) => page.waitForFunction(() => document.querySelector(".askbar-send")?.getAttribute("aria-label") === "Enviar", null, { timeout: 5000 });
export const com = (r, f) => { const c = structuredClone(r); f(c); return c; };
export const P = "<img src=x onerror=\"window.__xss=1\">";
export const bloco = (card, recursos) => ({ tipo: "bloco", bloco: { card, abas: [{ mes: null, recursos }] } });

// `quebra`: passa por objeto mas NÃO passa pelo `contrato`. `fundo`: passa pelo contrato e LANÇA no render (sem o `LimiteCard` desmontaria a raiz): só o rendimento tem, porque o
// Pydantic garante o tipo dos textos e o contrato dele confere só o dinheiro e as listas; nos legados o contrato cobre tudo o que se desenha. `injeta`: põe texto livre num campo desenhado.
export const CASOS = {
  caixinhas: { titulo: "Caixinhas", manchete: "R$ 300,50", recursos: { caixinhas: FX.caixinhas },
    quebra: (r) => com(r, (c) => { c.caixinhas.goals[0].balance = "300.5"; }), fundo: null, injeta: (r, p) => com(r, (c) => { c.caixinhas.goals[0].name = p; }) },
  fatura: { titulo: "Fatura do cartão", manchete: "R$ 999,50", recursos: { cartoes: FX.cartoes },
    quebra: (r) => com(r, (c) => { c.cartoes.cards[0].credit_used = null; }), fundo: null, injeta: (r, p) => com(r, (c) => { c.cartoes.cards[0].name = p; }) },
  parcelas: { titulo: "Parcelas futuras", manchete: "1 de 3 pagas", recursos: { parcelas: FX.parcelas },
    quebra: (r) => com(r, (c) => { c.parcelas.installments[0].parcelas[0].valor = "100.5"; }), fundo: null, injeta: (r, p) => com(r, (c) => { c.parcelas.installments[0].name = p; }) },
  renda: { titulo: "Renda mês a mês", manchete: "R$ 3.100,25", recursos: { renda: FX.renda },
    quebra: (r) => com(r, (c) => { c.renda.evolution[0].month = "2026-13"; }), fundo: null, injeta: null },
  piggy: { titulo: "Piggy notou", manchete: "Mercado subiu", recursos: { insights: FX.insights },
    quebra: (r) => com(r, (c) => { c.insights.insights[0].title = 5; }), fundo: null, injeta: (r, p) => com(r, (c) => { c.insights.insights[0].title = p; }) },
  rendimento: { titulo: "Rendimentos", manchete: "+R$ 112,35", recursos: { rendimento: FX.rendimento },
    quebra: (r) => com(r, (c) => { c.rendimento.rendimentos.mes.valor = "lixo"; }), fundo: (r) => com(r, (c) => { c.rendimento.rendimentos.categorias[0].rotulo = { a: 1 }; }),
    injeta: (r, p) => com(r, (c) => { c.rendimento.rendimentos.categorias[0].rotulo = p; }) },
};
/** O evento `bloco` com o texto "__INF__" trocado por `1e999` no JSON: o `JSON.stringify` escreve Infinity como null, e o `JSON.parse` do navegador lê `1e999` como Infinity. */
export const blocoInf = (card, recursos) => `data: ${JSON.stringify(bloco(card, recursos)).replace(/"__INF__"/g, "1e999")}\n\n`;
export const IDS = Object.keys(CASOS);
export const blocoDoCaso = (id, recursos = CASOS[id].recursos) => bloco(id, recursos);

/** Abre o /painel e responde a "me mostra" com os blocos (objetos de evento) e o texto final; `cru` (texto) entra no stream antes do `fim`, para o JSON que o `JSON.stringify` não escreve (1e999). */
export async function responde(blocos, opts, cru = "") {
  const c = await abrir(opts);
  await enviar(c.page, "me mostra");
  await c.flux.espera(1);
  await c.flux.push(0, evento(...blocos) + cru + evento(fim("Texto final.")));
  await pronto(c.page);
  return c;
}

// Abre tudo o que abre agora (os "+N" também). Pelos `elementHandles`: um `locator.all()` reavalia o seletor a cada clique, e a linha aberta já não casa com `aria-expanded=false`.
export const abreTudo = async (page) => { for (const h of await page.locator(".card-ia button[aria-expanded=false]").elementHandles()) { await h.click(); await page.waitForTimeout(60); } };
export const linhasDe = (page, sel) => page.locator(sel).evaluateAll((ls) => ls.map((l) => l.innerText.replace(/\s+/g, " ").trim()));
