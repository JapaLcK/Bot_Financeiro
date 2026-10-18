/**
 * O que os testes de Bancos conectados (dashboard_v2_bancos.test.mjs) compartilham: as conexões de exemplo e o servidor de
 * mentira das três leituras da tela. Não é `*.test.mjs` de propósito: o `node --test tests/frontend/*.test.mjs` não o roda.
 */
import { RESPOSTAS } from "./_painel.mjs";

const AGORA = "2026-10-02T14:47:59.123456+00:00"; // 12 min e uns segundos antes do relógio congelado (15:00Z), com 6 casas de fração
// Uma conexão como o snapshot a devolve (com campos a mais, que a tela não pode guardar).
export const con = (id, institution_name, state = "updated", label = "Atualizado", last_sync_at = AGORA, detail = null) => ({
  id, institution_name, last_sync_at, provider_item_id: `item-${id}`, status: "UPDATED", ui: { state, label, detail, stale_products: [], dados_de: null },
  accounts: [{ id: 1, balance: "999.99" }], transactions: [{ id: 1, amount: "1.00" }], investments: [],
});
// As conexões da fixture `bancos.padrao`: 11 e 12 em dia, 13 pausada.
export const PADRAO = () => [con(11, "Nubank"), con(12, "Inter"), con(13, "Itaú", "paused", "Pausado")];
export const LIMITE = (o = {}) => ({ ok: true, of_banks_max: 3, em_uso: 2, pode_adicionar: true, code: null, message: null, ...o });

/**
 * `conexoes`: o snapshot; `bancos`: nome da fixture `bancos` ou o corpo; `limite`: o corpo do /limite.
 * `snap`/`bancosRes`/`limiteRes`: `(r) => r.fulfill(...)` para responder outra coisa; `perfil`: o corpo do dashboard-profile (padrão uid 7).
 * `s.reqs`: os caminhos pedidos (das três leituras e do perfil) na ordem.
 */
export function servidorBancos({ conexoes = PADRAO(), bancos, limite = LIMITE(), snap, bancosRes, limiteRes, perfil = { display_name: "Ana", email: "ana@exemplo.com", user_id: 7, whatsapp_linked: false } } = {}) {
  const s = { reqs: [] };
  s.antes = async (ctx) => {
    ctx.on("request", (q) => { const p = new URL(q.url()).pathname; if (/open-finance|bancos|dashboard-profile/.test(p)) s.reqs.push(p); });
    await ctx.route((u) => u.pathname === "/auth/dashboard-profile", (r) => r.fulfill({ json: perfil }));
    await ctx.route((u) => u.pathname === "/open-finance/7", (r) => snap ? snap(r) : r.fulfill({ json: { ok: true, connections: conexoes } }));
    await ctx.route((u) => u.pathname === "/open-finance/7/limite", (r) => limiteRes ? limiteRes(r) : r.fulfill({ json: limite }));
    if (bancos || bancosRes) await ctx.route((u) => u.pathname === "/api/v2/bancos", (r) => bancosRes ? bancosRes(r) : r.fulfill({ json: typeof bancos === "string" ? RESPOSTAS.bancos[bancos] : bancos }));
  };
  return s;
}
export const ERRO_500 = (r) => r.fulfill({ status: 500, json: { error: { code: "internal_error", message: "x" } } });
export const ERRO_404 = (r) => r.fulfill({ status: 404, json: { error: { code: "not_found", message: "x" } } });
