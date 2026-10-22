// O isolamento dos cards dos agentes (tests/frontend/dashboard_v2_chat_agentes_card*.test.mjs): a /api/v2 lê o usuário da SESSÃO,
// então o teste vira a sessão para a conta 43 no exato GET do card e confere que o número de B nunca aparece; o controle negativo
// roda o bundle sem a conferência de daConta (servirSemConferencia) e vê o número de B aparecer.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { FRONTEND } from "./_painel.mjs";

// O GET de `url` devolve `corpoB` e, no mesmo instante, a sessão passa a ser a 43 (outra aba entrou em outra conta); só depois de
// `arma()` (o GET do card: o painel lê as rotas v2 em outras telas). `window.__viu`: `marca` apareceu no DOM; `__viuErro`: o erro do card.
// `corpoB` pode ser função (request, n do GET): o corpo de cada leitura (o card que lê duas rotas). `viraNoN`: a sessão só vira no N-ésimo GET.
export async function viraConta43NoGet(ctx, page, url, corpoB, marca, viraNoN = 1) {
  const observa = (m) => {
    window.__viu = false;
    window.__viuErro = false;
    new MutationObserver(() => { if (document.body.textContent.includes(m)) window.__viu = true; if (document.body.textContent.includes("Não deu para carregar")) window.__viuErro = true; }).observe(document, { subtree: true, childList: true, characterData: true });
  };
  await page.addInitScript(observa, marca); // vale nas próximas navegações...
  await page.evaluate(observa, marca); // ...e nesta
  let virou = false, armado = false, gets = 0;
  await ctx.route("**/auth/dashboard-profile", async (r) => {
    const conta = virou ? 43 : 42;
    await r.fulfill({ json: { user_id: conta, display_name: conta === 43 ? "Outra" : "Marina", email: null, whatsapp_linked: true } });
  });
  await ctx.route(url, (r) => { const n = ++gets; virou = armado && n >= viraNoN; r.fulfill({ json: typeof corpoB === "function" ? corpoB(r.request(), n) : corpoB }); });
  return () => { armado = true; };
}

/** O bundle com a conferência da conta (daConta, `user_id!==uid`) desligada. */
function bundleSemConferencia() {
  const js = readFileSync(join(FRONTEND, "dashboard-app.js"), "utf8");
  const desligado = js.replace(/user_id!==([\w$]+)\)throw new ([\w$]+);return/, "user_id===-1)throw new $2;return");
  assert.notEqual(desligado, js, "o padrão da conferência não está mais no bundle: ajuste o teste");
  return desligado;
}

/** Serve o bundle sem a conferência no lugar do real. O painel pede /dashboard-app.js?v=N (glob sem query não casa; regex casa), então
 * o controle negativo confere `.n > 0` depois do `page.reload()` (a URL só muda o hash: goto(page.url()) não recarregaria o documento). */
export async function servirSemConferencia(ctx) {
  const desligado = bundleSemConferencia(), servido = { n: 0 };
  await ctx.route(/dashboard-app\.js/, (r) => { servido.n++; return r.fulfill({ contentType: "application/javascript", body: desligado }); });
  return servido;
}
