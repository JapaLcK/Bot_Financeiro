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
// A resposta do POST /open-finance/{uid}/refresh (forma de `pluggy_sync.py`: `sync.items[]` por item, `still_updating` a soma, e o snapshot de depois em `connections`).
export const item = (item_id, state = "updated", still_updating = false, detail = null, reason = "ok", institution = "Nubank") =>
  ({ item_id, institution, state, label: state, detail, reason, still_updating });
export const refreshDe = (items, connections = PADRAO(), ok = true) =>
  ({ ok: true, sync: { ok, items, still_updating: items.filter((i) => i.still_updating).length }, connections });
export const LIMITE = (o = {}) => ({ ok: true, of_banks_max: 3, em_uso: 2, pode_adicionar: true, code: null, message: null, ...o });

/**
 * `conexoes`: o snapshot; `bancos`: nome da fixture `bancos` ou o corpo; `limite`: o corpo do /limite.
 * `snap`/`bancosRes`/`limiteRes`: `(r) => r.fulfill(...)` para responder outra coisa; `perfil`: o corpo do dashboard-profile (padrão uid 7).
 * `s.reqs`: os caminhos pedidos (das três leituras e do perfil) na ordem; o DELETE entra como "DELETE <caminho>".
 * `del(r, s)`: responde o DELETE de uma conexão (padrão: 200 e tira o id de `s.conexoes`, que é o que o snapshot devolve);
 * `s.dels`: um [método, caminho, x-csrf-token, corpo] por DELETE. `perfil` pode ser uma função (o perfil muda no meio do teste).
 * `refresh`: `(r) => r.fulfill(...)` do POST /open-finance/7/refresh (padrão: 200, tudo em dia); `s.posts`: um `{ headers }` por POST recebido.
 */
export function servidorBancos({ conexoes = PADRAO(), bancos, limite = LIMITE(), snap, bancosRes, limiteRes, del, refresh, perfil = { display_name: "Ana", email: "ana@exemplo.com", user_id: 7, whatsapp_linked: false } } = {}) {
  const s = { reqs: [], dels: [], posts: [], conexoes };
  s.antes = async (ctx) => {
    ctx.on("request", (q) => { const p = new URL(q.url()).pathname; if (!/open-finance|bancos|dashboard-profile/.test(p)) return; s.reqs.push(q.method() === "DELETE" ? `DELETE ${p}` : p); if (q.method() === "DELETE") s.dels.push([q.method(), p, q.headers()["x-csrf-token"] ?? null, q.postData()]); });
    await ctx.route((u) => u.pathname === "/auth/dashboard-profile", (r) => r.fulfill({ json: typeof perfil === "function" ? perfil() : perfil }));
    await ctx.route((u) => u.pathname === "/open-finance/7", (r) => snap ? snap(r) : r.fulfill({ json: { ok: true, connections: s.conexoes } }));
    await ctx.route((u) => u.pathname === "/open-finance/7/refresh", (r) => { s.posts.push({ headers: r.request().headers() }); return refresh ? refresh(r) : r.fulfill({ json: refreshDe([item("item-11")]) }); });
    await ctx.route((u) => u.pathname === "/open-finance/7/limite", (r) => limiteRes ? limiteRes(r) : r.fulfill({ json: limite }));
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
