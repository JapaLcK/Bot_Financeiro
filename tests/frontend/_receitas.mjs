// Mock com estado das receitas pendentes (na pilha de avisos do topo, `#avisos-topo`) e da seção "Receitas na previsão" (respondidas, fim da /previsao), dividido entre dashboard_v2_previsao_receitas*.test.mjs.
import { RESPOSTAS, abrirPainel } from "./_painel.mjs";

export const ALFA = "extrato#a1b2c3d4e5f60718"; // pendente
export const MARIA = "extrato#0f1e2d3c4b5a6978"; // confirmada
export const JOAO = "extrato#1122334455667788"; // negada
export const STATUS = { sim: "confirmada", nao: "negada", desfazer: "pendente" };
export const CAIXA = "#avisos-topo"; // o anfitrião da pilha: o alerta de falha e a região viva moram nele, a pilha (`section.pilha`) dentro
export const PEND = `${CAIXA} ul[aria-label="Receitas a confirmar"] li`;
export const SECAO = "#w-previsao-receitas-respondidas";
export const RESP = `${SECAO} ul[aria-label="Receitas respondidas"] li`;
// Os dois recipientes (cada um anuncia o que ele mesmo fez): o texto não vazio do aviso / da região viva.
const AMBOS = (papel) => `${CAIXA} [role=${papel}], ${SECAO} [role=${papel}]`;

/**
 * Abre /previsao com o mock com estado. `opts.post(corpo, n)` pode devolver {status, json} para falhar
 * {perdida: true} grava a marca e derruba a conexão, {sumir: true} tira a chave da lista (sozinho ou com perdida), ou uma Promise (o POST fica no ar até ela resolver);
 * `opts.get(nPosts)` pode devolver uma Promise que segura o GET de releitura; `opts.receitas` troca a
 * lista (`null` = servidor sem o campo).
 */
export async function abrir(browser, { receitas, post, get, ...resto } = {}) {
  const r = await abrirPainel(browser, { espera: "#page-title", agora: "2026-10-06T15:00:00Z", previsao: "pro30_receitas", ...resto });
  const estado = structuredClone(receitas ?? RESPOSTAS.previsao.pro30_receitas.receitas_do_banco);
  const semCampo = receitas === null;
  r.posts = [];
  r.gets = [];
  const corpo = (dias) => {
    // o plano Plus recebe o corpo do Plus (só 30 dias, sem compromissos); o Pro responde o que pediu
    const base = r.ctx.__plus ? "plus30" : `pro${dias}`;
    const c = { ...structuredClone(RESPOSTAS.previsao[base]), receitas_do_banco: structuredClone(estado) };
    if (semCampo) delete c.receitas_do_banco;
    return c;
  };
  await r.ctx.route("**/api/v2/previsao**", async (rota) => {
    const u = new URL(rota.request().url());
    if (rota.request().method() === "POST") {
      const c = rota.request().postDataJSON();
      r.posts.push({ corpo: c, csrf: rota.request().headers()["x-csrf-token"] });
      const falha = await post?.(c, r.posts.length);
      const linha = estado.find((x) => x.chave === c.chave);
      if (falha?.sumir) estado.splice(estado.indexOf(linha), 1); // o servidor deixou de listar a chave (também com POST que falha)
      else if (!falha?.status && linha) linha.status = STATUS[c.resposta];
      if (falha?.status) return rota.fulfill({ status: falha.status, json: falha.json });
      if (falha?.perdida) return rota.abort("connectionreset");
      return rota.fulfill({ json: corpo(30) });
    }
    r.gets.push(u.search);
    await get?.(r.posts.length);
    return rota.fulfill({ json: corpo(u.searchParams.get("dias") ?? 30) });
  });
  if (resto.plano === "plus") r.ctx.__plus = true;
  await r.ir("/previsao");
  await r.page.locator("#w-previsao .prev-hora").waitFor();
  return r;
}
export const texto = (page, sel) => page.locator(sel).evaluateAll((els) => els.map((e) => e.innerText.replace(/\s+/g, " ").trim()));
export const dito = async (page) => (await texto(page, AMBOS("status"))).find(Boolean) ?? "";
export const alerta = async (page) => (await texto(page, AMBOS("alert"))).find(Boolean) ?? "";
/** O alerta de cada instância separado: [topo (pendentes), fim (respondidas)]. `alerta()` junta as duas e esconde o estado cruzado. */
export const alertas = (page) => Promise.all([CAIXA, SECAO].map(async (s) => (await texto(page, `${s} [role=alert]`))[0] ?? ""));
/** Painel visível = o recipiente tem caixa de verdade; no estado sr-only ele é 1 px (só o anúncio e o alvo do foco ficam no DOM). */
export const painelVisivel = (page, sel) => page.locator(sel).evaluate((a) => { const r = a.parentElement.getBoundingClientRect(); return r.width > 2 && r.height > 2; });
export const botoesDasCaixas = (page) => page.locator(`${CAIXA} button, ${SECAO} button`).evaluateAll((bs) => bs.map((b) => b.disabled));
/** A pilha tem card de verdade (no estado vazio ela é só um alvo de foco sr-only). */
export const pilhaVisivel = (page) => page.locator(`${CAIXA} section.pilha`).count().then((n) => n > 0);
/** Os pontos da pilha (um por aviso) e "ver o aviso k": com mais de um, só um card aparece por vez. */
export const pontos = (page) => page.locator(`${CAIXA} .pilha-ponto`);
export const verAviso = (page, k) => pontos(page).nth(k - 1).click();
export const semPendente = (page) => page.locator(PEND).waitFor({ state: "detached" });
