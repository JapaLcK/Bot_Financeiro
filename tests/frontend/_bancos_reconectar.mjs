// Helpers dos testes de reconectar banco (dashboard_v2_bancos_reconectar*.test.mjs). O legado (/open-finance-connect.js) é o REAL, servido do disco;
// só o SDK da Pluggy é falso. Não é `*.test.mjs` de propósito: o `node --test tests/frontend/*.test.mjs` não o roda.
import { abrir } from "./_guia.mjs";
import { PADRAO, semReticencias, servidorBancos } from "./_bancos.mjs";
import { esperaCards, linha, det } from "./_bancos_desconectar.mjs";

// O widget falso: guarda as opções em `window.__w` (os testes chamam os callbacks), conta os construídos em `window.__n` e, no `init()`, anexa ao body uma
// camada em tela cheia (`#pluggy-falso`), como o SDK de verdade faz com o iframe dele: é ela que tem de ficar ACIMA de tudo o que a página tem.
const SDK_FALSO = () => {
  window.__n = 0;
  window.PluggyConnect = function (o) {
    window.__w = o; window.__n++;
    this.init = function () { const d = document.createElement("div"); d.id = "pluggy-falso"; d.style.cssText = "position:fixed;inset:0;z-index:2147483000;background:#fff"; document.body.appendChild(d); };
  };
};

// Abre a tela com o módulo real, o SDK falso e as três leituras prontas. `o.antes(ctx, s)`: rotas a mais; `o.vagas`: o texto a esperar ("" = não espera).
export async function telaRecon({ conexoes = PADRAO(), ...opts } = {}, { antes, vagas = "2 de 3 bancos", ...o } = {}) {
  const srv = servidorBancos({ conexoes, bancos: "padrao", seletorReal: true, ...opts });
  const p = await abrir({ guia: "concluido", rota: "/conta/bancos", antes: async (ctx) => { await srv.antes(ctx); await ctx.addInitScript(SDK_FALSO); await antes?.(ctx, srv); }, ...o });
  p.page.setDefaultTimeout(15000);
  await esperaCards(p.page, conexoes.length);
  await semReticencias(p.page);
  if (vagas) await p.page.getByText(vagas).waitFor();
  return { ...p, srv };
}

export const botaoRecon = (page) => det(page).getByRole("button", { name: "Reconectar banco", exact: true });
// Abre o detalhe do banco e clica em Reconectar (o detalhe fecha sozinho).
export const reconectar = async (page, nome) => { await linha(page, nome).click(); await det(page).waitFor(); await botaoRecon(page).click(); };
export const naLinha = (page, nome) => linha(page, nome).evaluate((el) => el === document.activeElement);
export const widgetAberto = (page) => page.waitForFunction(() => window.__n === 1, null, { timeout: 8000 });
export const widgetsAbertos = (page) => page.evaluate(() => window.__n ?? 0);
// Os callbacks do SDK. Com chaves: o `onSuccess` do legado devolve uma promessa que pode ficar presa, e o evaluate esperaria por ela.
export const sucesso = (page, id = "item-11") => page.evaluate((id) => { window.__w.onSuccess({ item: { id } }); }, id);
export const fechaWidget = (page) => page.evaluate(() => { document.getElementById("pluggy-falso")?.remove(); window.__w.onClose(); }); // o SDK de verdade tira o iframe ao fechar
export const erroDoSdk = (page) => page.evaluate(() => { window.__w.onError({ message: "falhou" }); });
export const tokens = (srv) => srv.conex.filter((p) => p[1].endsWith("/connect-token"));
export const saves = (srv) => srv.conex.filter((p) => p[1].endsWith("/pluggy-item"));
export const depoisDo = (srv, caminho) => srv.reqs.slice(srv.reqs.lastIndexOf(caminho) + 1);
// Os nomes dos bancos cujo detalhe tem o botão Reconectar (abre, olha e fecha cada um).
export async function comReconectar(page, nomes) {
  const r = [];
  for (const n of nomes) {
    await linha(page, n).click();
    await det(page).waitFor();
    if (await botaoRecon(page).count()) r.push(n);
    await det(page).getByRole("button", { name: "Fechar" }).click();
    await det(page).waitFor({ state: "detached" });
  }
  return r;
}
export { semReticencias, PADRAO };
