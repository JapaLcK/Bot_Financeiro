/**
 * O leitor (credencial de visualização) no /painel, para os testes dele. Não é `*.test.mjs` de propósito.
 * É o `/api/v2/me` com `somente_leitura: true` (como o servidor o devolve) e os GET que o servidor nega a ele (frontend/routes/leitura_guard.py) com 403.
 */
import { RESPOSTAS } from "./_painel.mjs";

const ME = { ...RESPOSTAS.me.pro, somente_leitura: true, familia: null, afiliado: false };
export const ehNegado = (u) => /^\/(api\/v2\/(perfil|familia)|auth\/dashboard-profile)/.test(u.pathname);
/** Registre DEPOIS de `servir`: a rota mais nova vence e o resto cai (`fallback`) no servidor de mentira. */
export const rotasDoLeitor = (ctx) => ctx.route("**/*", (r) => {
  const u = new URL(r.request().url());
  if (u.pathname === "/api/v2/me") return r.fulfill({ json: ME });
  if (ehNegado(u) && r.request().method() === "GET") return r.fulfill({ status: 403, json: { error: "somente_leitura" } });
  return r.fallback();
});
