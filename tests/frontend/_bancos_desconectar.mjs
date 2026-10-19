// Helpers dos testes de desconectar banco (dashboard_v2_bancos_desconectar*.test.mjs): seletores e a tela já com as 3 leituras prontas.
import { abrir } from "./_guia.mjs";
import { PADRAO, servidorBancos } from "./_bancos.mjs";

export const DEL = (id) => ["DELETE", `/open-finance/7/connections/${id}`, "tok-123", null];
export const semReticencias = (page) => page.waitForFunction(() => !document.querySelector(".banco-total")?.textContent.includes("…") && document.querySelector(".banco-total"), null, { timeout: 8000 });
export const esperaCards = (page, n) => page.waitForFunction((n) => document.querySelectorAll(".banco-card").length === n, n, { timeout: 8000 });
export const refoco = (page) => page.evaluate(() => window.dispatchEvent(new Event("visibilitychange"))); // o foco da aba: o React Query relê as leituras vivas
export const dlg = (page) => page.locator("dialog[open]");
export const confirmar = (page, nome = "Desconectar") => dlg(page).getByRole("button", { name: nome, exact: true });
export const manter = (page) => dlg(page).getByRole("button", { name: "Manter banco", exact: true });
export const card = (page, nome) => page.locator(".banco-card", { has: page.getByRole("heading", { name: nome, exact: true }) });
export const botao = (page, nome) => card(page, nome).getByRole("button", { name: `Desconectar ${nome}`, exact: true });
export const aviso = (page) => page.locator("#main p[role=status].sr-only");
export const alerta = (page) => page.locator(".page-grid [role=alert]");
export const foco = (page) => page.evaluate(() => document.activeElement === document.body ? "<BODY>" : document.activeElement?.id || document.activeElement?.textContent?.trim());
export const noFoco = (page, t) => page.waitForFunction((t) => (document.activeElement?.id || document.activeElement?.textContent?.trim()) === t, t, { timeout: 8000 });
export const abreGerenciar = (page, nome) => card(page, nome).locator("summary").click();
export const pedir = async (page, nome) => { await abreGerenciar(page, nome); await botao(page, nome).click(); await dlg(page).waitFor(); };
export const tiraDoSnapshot = (s, id) => { s.conexoes = s.conexoes.filter((c) => c.id !== id); };
export const lidosDepois = (s) => { const i = s.reqs.lastIndexOf("DELETE /open-finance/7/connections/11"); return i < 0 ? [] : s.reqs.slice(i + 1); };

// Abre a tela e espera as três leituras (cards, valores e "2 de 3 bancos"): só então vale clicar, refocar ou injetar erro.
export async function tela({ conexoes = PADRAO(), ...opts } = {}, o = {}) {
  const srv = servidorBancos({ conexoes, bancos: "padrao", ...opts });
  const p = await abrir({ guia: "concluido", rota: "/conta/bancos", antes: srv.antes, ...o });
  p.page.setDefaultTimeout(15000);
  await esperaCards(p.page, conexoes.length);
  await semReticencias(p.page);
  await p.page.getByText("2 de 3 bancos").waitFor();
  return { ...p, srv };
}
