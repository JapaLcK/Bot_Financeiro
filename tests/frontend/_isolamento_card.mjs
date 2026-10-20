// O isolamento dos cards dos agentes (tests/frontend/dashboard_v2_chat_agentes_card*.test.mjs): a /api/v2 lê o usuário da SESSÃO,
// então o teste vira a sessão para a conta 43 no exato GET do card e confere que o número de B nunca aparece; o controle negativo
// roda o bundle sem a conferência de daConta e vê o número de B aparecer.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { FRONTEND } from "./_painel.mjs";

// O GET de `url` devolve `corpoB` e, no mesmo instante, a sessão passa a ser a 43 (outra aba entrou em outra conta); só depois de
// `arma()` (o GET do card: o painel lê as rotas v2 em outras telas). `window.__viu`: `marca` apareceu no DOM; `__viuErro`: o erro do card.
// `sem`: no controle negativo a 1ª leitura do perfil depois do GET (a de daConta, que o bundle patchado ignora) ainda diz 42 e as seguintes
// demoram 500 ms: sem isso a limpeza da conversa (a releitura do próprio painel) corre com o desenho do card e o teste não mediria nada.
// `corpoB` pode ser função (request, n do GET): o corpo de cada leitura (o card que lê duas rotas). `viraNoN`: a sessão só vira no N-ésimo GET.
export async function viraConta43NoGet(ctx, page, url, corpoB, marca, sem = false, viraNoN = 1) {
  const observa = (m) => {
    window.__viu = false;
    window.__viuErro = false;
    new MutationObserver(() => { if (document.body.textContent.includes(m)) window.__viu = true; if (document.body.textContent.includes("Não deu para carregar")) window.__viuErro = true; }).observe(document, { subtree: true, childList: true, characterData: true });
  };
  await page.addInitScript(observa, marca); // vale nas próximas navegações...
  await page.evaluate(observa, marca); // ...e nesta
  let virou = false, armado = false, depois = 0, gets = 0;
  await ctx.route("**/auth/dashboard-profile", async (r) => {
    const n = virou ? ++depois : 0, conta = virou && !(sem && n === 1) ? 43 : 42;
    if (sem && n > 1) await new Promise((ok) => setTimeout(ok, 500));
    await r.fulfill({ json: { user_id: conta, display_name: conta === 43 ? "Outra" : "Marina", email: null, whatsapp_linked: true } });
  });
  await ctx.route(url, (r) => { const n = ++gets; virou = armado && n >= viraNoN; r.fulfill({ json: typeof corpoB === "function" ? corpoB(r.request(), n) : corpoB }); });
  return () => { armado = true; };
}

/** O bundle com a conferência da conta (daConta, `user_id!==uid`) desligada. */
export function bundleSemConferencia() {
  const js = readFileSync(join(FRONTEND, "dashboard-app.js"), "utf8");
  const desligado = js.replace(/user_id!==(\w+)\)throw new (\w+);return/, "user_id===-1)throw new $2;return");
  assert.notEqual(desligado, js, "o padrão da conferência não está mais no bundle: ajuste o teste");
  return desligado;
}
