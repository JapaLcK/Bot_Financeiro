/**
 * O que os testes de Bancos conectados (dashboard_v2_bancos.test.mjs) compartilham: as conexões de exemplo e o servidor de
 * mentira das três leituras da tela. Não é `*.test.mjs` de propósito: o `node --test tests/frontend/*.test.mjs` não o roda.
 */
import { RESPOSTAS } from "./_painel.mjs";

const AGORA = "2026-10-02T14:47:59.123456+00:00"; // 12 min e uns segundos antes do relógio congelado (15:00Z), com 6 casas de fração
// Uma conexão como o snapshot a devolve (com campos a mais, que a tela não pode guardar).
export const con = (id, institution_name, state = "updated", label = "Atualizado", last_sync_at = AGORA, detail = null, logo_url) => ({
  id, institution_name, last_sync_at, provider_item_id: `item-${id}`, status: "UPDATED", ui: { state, label, detail, stale_products: [], dados_de: null },
  accounts: [{ id: 1, balance: "999.99" }], transactions: [{ id: 1, amount: "1.00" }], investments: [],
  ...(logo_url === undefined ? {} : { logo_url }), // ausente quando não passado: o servidor antigo não manda a chave
});
// O CDN de ícones da Pluggy de mentira: um SVG (quadrado roxo com um círculo claro) com width/height 512 como os de verdade (PicPay, Itaú): sem o CSS do `img`, ele ocuparia 512×512.
export const LOGO = "https://cdn.pluggy.ai/assets/connector-icons/612.svg";
export const SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 24 24"><rect width="24" height="24" fill="#8a05be"/><circle cx="12" cy="12" r="6" fill="#fff"/></svg>';
export const CDN_OK = (r) => r.fulfill({ status: 200, contentType: "image/svg+xml", body: SVG });
// As conexões da fixture `bancos.padrao`: 11 e 12 em dia, 13 pausada.
export const PADRAO = () => [con(11, "Nubank"), con(12, "Inter"), con(13, "Itaú", "paused", "Pausado")];
export const LIMITE = (o = {}) => ({ ok: true, of_banks_max: 3, em_uso: 2, pode_adicionar: true, code: null, message: null, ...o });

/**
 * `conexoes`: o snapshot; `bancos`: nome da fixture `bancos` ou o corpo; `limite`: o corpo do /limite.
 * `snap`/`bancosRes`/`limiteRes`: `(r) => r.fulfill(...)` para responder outra coisa; `perfil`: o corpo do dashboard-profile (padrão uid 7).
 * `cdn`: a resposta do CDN de logos (padrão: um SVG); `s.cdn`: as URLs que o navegador pediu a ele.
 * `s.reqs`: os caminhos pedidos (das três leituras e do perfil) na ordem; o DELETE entra como "DELETE <caminho>".
 * `del(r, s)`: responde o DELETE de uma conexão (padrão: 200 e tira o id de `s.conexoes`, que é o que o snapshot devolve);
 * `s.dels`: um [método, caminho, x-csrf-token, corpo] por DELETE. `perfil` pode ser uma função (o perfil muda no meio do teste).
 */
export function servidorBancos({ conexoes = PADRAO(), bancos, limite = LIMITE(), snap, bancosRes, limiteRes, del, cdn = CDN_OK, perfil = { display_name: "Ana", email: "ana@exemplo.com", user_id: 7, whatsapp_linked: false } } = {}) {
  const s = { reqs: [], dels: [], cdn: [], conexoes };
  s.antes = async (ctx) => {
    ctx.on("request", (q) => { const p = new URL(q.url()).pathname; if (!/open-finance|bancos|dashboard-profile/.test(p)) return; s.reqs.push(q.method() === "DELETE" ? `DELETE ${p}` : p); if (q.method() === "DELETE") s.dels.push([q.method(), p, q.headers()["x-csrf-token"] ?? null, q.postData()]); });
    await ctx.route((u) => u.pathname === "/auth/dashboard-profile", (r) => r.fulfill({ json: typeof perfil === "function" ? perfil() : perfil }));
    await ctx.route((u) => u.pathname === "/open-finance/7", (r) => snap ? snap(r) : r.fulfill({ json: { ok: true, connections: s.conexoes } }));
    await ctx.route((u) => u.pathname === "/open-finance/7/limite", (r) => limiteRes ? limiteRes(r) : r.fulfill({ json: limite }));
    await ctx.route((u) => u.hostname === "cdn.pluggy.ai", (r) => { s.cdn.push(r.request().url()); return cdn(r); });
    await ctx.route((u) => /^\/open-finance\/\d+\/connections\/\d+$/.test(u.pathname), (r) => {
      if (del) return del(r, s);
      s.conexoes = s.conexoes.filter((c) => `/open-finance/7/connections/${c.id}` !== new URL(r.request().url()).pathname);
      return r.fulfill({ json: { ok: true, deleted: 1 } });
    });
    if (bancos || bancosRes) await ctx.route((u) => u.pathname === "/api/v2/bancos", (r) => bancosRes ? bancosRes(r) : r.fulfill({ json: typeof bancos === "string" ? RESPOSTAS.bancos[bancos] : bancos }));
  };
  return s;
}
export const ERRO_500 = (r) => r.fulfill({ status: 500, json: { error: { code: "internal_error", message: "x" } } });
export const ERRO_404 = (r) => r.fulfill({ status: 404, json: { error: { code: "not_found", message: "x" } } });
