/**
 * O que os testes de Bancos conectados (dashboard_v2_bancos.test.mjs) compartilham: as conexões de exemplo e o servidor de
 * mentira das leituras da tela (snapshot, /bancos, /limite e /connectors) e do seletor de bancos (o módulo legado, em stub). Não é `*.test.mjs` de propósito: o `node --test tests/frontend/*.test.mjs` não o roda.
 */
import { join } from "node:path";
import { FRONTEND, RESPOSTAS } from "./_painel.mjs";

const AGORA = "2026-10-02T14:47:59.123456+00:00"; // 12 min e uns segundos antes do relógio congelado (15:00Z), com 6 casas de fração
// O catálogo do /connectors (a ordem alfabética do servidor): 8 populares, de ids que casam com o `institution_id` das conexões de exemplo (IDS).
const L = (n) => `https://cdn.pluggy.ai/assets/connector-icons/${n}.svg`;
const conector = (id, name, color, logo = L(id)) => ({ id, name, type: "PERSONAL_BANK", color, logo, inv: false });
export const CONECTORES = [conector(203, "Banco do Brasil", "FAE128"), conector(202, "Bradesco", "CC092F"), conector(207, "C6 Bank", "242424", ""), conector(205, "Caixa", "0070AF"),
  conector(206, "Inter", "FF7A00"), conector(201, "Itaú", "EC7000"), conector(208, "Nubank", "820AD1"), conector(204, "Santander", "EC0000")];
const IDS = { "Banco do Brasil": "203", Bradesco: "202", "C6 Bank": "207", Caixa: "205", Inter: "206", "Itaú": "201", "Itaú Unibanco": "201", Nubank: "208", Santander: "204" };
// Uma conexão como o snapshot a devolve (com campos a mais, que a tela não pode guardar).
export const con = (id, institution_name, state = "updated", label = "Atualizado", last_sync_at = AGORA, detail = null, logo_url) => ({
  id, institution_id: IDS[institution_name] ?? String(900 + id), institution_name, last_sync_at, provider_item_id: `item-${id}`, status: "UPDATED", ui: { state, label, detail, stale_products: [], dados_de: null },
  accounts: [{ id: 1, balance: "999.99" }], transactions: [{ id: 1, amount: "1.00" }], investments: [],
  ...(logo_url === undefined ? {} : { logo_url }), // ausente quando não passado: o servidor antigo não manda a chave
});
// O CDN de ícones da Pluggy de mentira: um SVG (quadrado roxo com um círculo claro) com width/height 512 como os de verdade (PicPay, Itaú): sem o CSS do `img`, ele ocuparia 512×512.
export const LOGO = "https://cdn.pluggy.ai/assets/connector-icons/612.svg";
export const SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 24 24"><rect width="24" height="24" fill="#8a05be"/><circle cx="12" cy="12" r="6" fill="#fff"/></svg>';
export const CDN_OK = (r) => r.fulfill({ status: 200, contentType: "image/svg+xml", body: SVG });
// As conexões da fixture `bancos.padrao`: 11 e 12 em dia, 13 pausada.
export const PADRAO = () => [con(11, "Nubank"), con(12, "Inter"), con(13, "Itaú", "paused", "Pausado")];
// O módulo legado /open-finance-connect.js em stub: registra o que a página faz com ele em `window.__of` (`init` com os callbacks em `cb`, `open(arg)`, `destroy`).
const STUB = `window.__of = { inits: 0, opens: [], destroys: 0, cb: null };
window.PBOpenFinance = { init(o) { __of.inits++; __of.cb = o; return this; }, open(a) { __of.opens.push(a === undefined ? null : a); }, destroy() { __of.destroys++; }, busy() { return false; } };`;
export const LIMITE = (o = {}) => ({ ok: true, of_banks_max: 3, em_uso: 2, pode_adicionar: true, code: null, message: null, ...o });

/**
 * `conexoes`: o snapshot; `bancos`: nome da fixture `bancos` ou o corpo; `limite`: o corpo do /limite.
 * `snap`/`bancosRes`/`limiteRes`: `(r) => r.fulfill(...)` para responder outra coisa; `perfil`: o corpo do dashboard-profile (padrão uid 7).
 * `cdn`: a resposta do CDN de logos (padrão: um SVG); `s.cdn`: as URLs que o navegador pediu a ele (o SDK da Pluggy não entra: vem vazio, como em `servir`).
 * `conectores`: o corpo `connectors` do /connectors (padrão: CONECTORES), `conectoresRes`: `(r) => r.fulfill(...)` para outra resposta; `semSeletor`: o script
 * do seletor não carrega (404); `seletorReal`: o módulo de verdade (frontend/open-finance-connect.js); sem isso é o stub acima.
 * `s.reqs`: os caminhos pedidos (das três leituras e do perfil) na ordem; o DELETE entra como "DELETE <caminho>".
 * `del(r, s)`: responde o DELETE de uma conexão (padrão: 200 e tira o id de `s.conexoes`, que é o que o snapshot devolve);
 * `s.dels`: um [método, caminho, x-csrf-token, corpo] por DELETE. `perfil` pode ser uma função (o perfil muda no meio do teste).
 */
export function servidorBancos({ conexoes = PADRAO(), bancos, limite = LIMITE(), snap, bancosRes, limiteRes, conectores = CONECTORES, conectoresRes, semSeletor = false, seletorReal = false, del, cdn = CDN_OK, perfil = { display_name: "Ana", email: "ana@exemplo.com", user_id: 7, whatsapp_linked: false } } = {}) {
  const s = { reqs: [], dels: [], cdn: [], conexoes };
  s.antes = async (ctx) => {
    ctx.__fim = { bancos: 0, perfil: 0 }; // respostas já entregues (ver `semReticencias`)
    ctx.on("requestfinished", (q) => { const p = new URL(q.url()).pathname; if (p === "/api/v2/bancos") ctx.__fim.bancos++; if (p === "/auth/dashboard-profile") ctx.__fim.perfil++; });
    ctx.on("request", (q) => { const p = new URL(q.url()).pathname; if (!/open-finance|bancos|dashboard-profile/.test(p)) return; s.reqs.push(q.method() === "DELETE" ? `DELETE ${p}` : p); if (q.method() === "DELETE") s.dels.push([q.method(), p, q.headers()["x-csrf-token"] ?? null, q.postData()]); });
    await ctx.route((u) => u.pathname === "/auth/dashboard-profile", (r) => r.fulfill({ json: typeof perfil === "function" ? perfil() : perfil }));
    await ctx.route((u) => u.pathname === "/open-finance/7", (r) => snap ? snap(r) : r.fulfill({ json: { ok: true, connections: s.conexoes } }));
    await ctx.route((u) => u.pathname === "/open-finance/7/limite", (r) => limiteRes ? limiteRes(r) : r.fulfill({ json: limite }));
    await ctx.route((u) => u.pathname === "/open-finance/7/connectors", (r) => conectoresRes ? conectoresRes(r) : r.fulfill({ json: { ok: true, connectors: conectores } }));
    await ctx.route((u) => u.pathname === "/open-finance-connect.js", (r) => semSeletor ? r.fulfill({ status: 404, body: "" }) : seletorReal ? r.fulfill({ path: join(FRONTEND, "open-finance-connect.js"), contentType: "application/javascript" }) : r.fulfill({ contentType: "application/javascript", body: STUB }));
    await ctx.route((u) => u.hostname === "cdn.pluggy.ai", (r) => { if (r.request().url().includes("/pluggy-connect/")) return r.fulfill({ contentType: "application/javascript", body: "" }); s.cdn.push(r.request().url()); return cdn(r); });
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

// Espera o fim da leitura de /bancos: o /bancos e a conferência da conta que vem depois dele (o 2º /auth/dashboard-profile) chegaram e o React assentou.
// Os valores só aparecem no detalhe (`abreDet`/`totalDe` abaixo), então não há "…" na lista para esperar. Quem conta é o contexto (`ctx.__fim`, posto pelo
// `servidorBancos`): o Resource Timing do navegador não vê o que o Playwright atende por rota.
export const semReticencias = async (page) => {
  const f = page.context().__fim;
  for (const fim = Date.now() + 8000; !(f.bancos >= 1 && f.perfil >= 2); await page.waitForTimeout(25)) if (Date.now() > fim) throw new Error(`a leitura de /bancos não assentou: ${JSON.stringify(f)}`);
  await page.waitForTimeout(150);
};
// O detalhe do banco `n` (dialog modal), aberto pela linha dele; `.banco-det-total` sai do "…" quando o /bancos assenta.
export const abreDet = async (page, n = 0) => { await page.locator(".banco-linha").nth(n).click(); const d = page.locator("dialog.banco-det[open]"); await d.waitFor(); return d; };
export const totalDe = async (page, n = 0, { assenta = true } = {}) => {
  const d = await abreDet(page, n);
  if (assenta) await page.waitForFunction(() => document.querySelector("dialog.banco-det[open] .banco-det-total")?.textContent !== "…", null, { timeout: 8000 });
  const t = await d.locator(".banco-det-total").innerText();
  await d.getByRole("button", { name: "Fechar" }).click();
  await d.waitFor({ state: "detached" });
  return t;
};

// A seção "Disponíveis para conectar" (região nomeada) e os nomes das linhas dela; o "+" do topo e o botão do estado vazio ("Conectar banco")
// distinguem-se dos dos disponíveis ("Conectar <nome>") só pelo nome exato.
export const regiao = (page) => page.getByRole("region", { name: "Disponíveis para conectar" });
export const disponiveisDe = (page) => regiao(page).locator(".banco-item .banco-nome").allTextContents();
export const maisExato = (page) => page.getByRole("link", { name: "Conectar banco", exact: true });
