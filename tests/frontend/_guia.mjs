/**
 * O que os testes do guia do /painel (#728) compartilham: dashboard_v2_guia.test.mjs (fluxo
 * e servidor) e dashboard_v2_guia_tela.test.mjs (véu, anel, foco, posição, movimento).
 * Não é `*.test.mjs` de propósito: o `node --test tests/frontend/*.test.mjs` não o roda.
 */
import { before, after } from "node:test";
import { chromium } from "playwright";
import { PAINEL, RESPOSTAS, exigeArtefatoEmDia, servir } from "./_painel.mjs";

let browser;
// Registra o navegador do arquivo de teste que chamou (cada arquivo roda no seu processo).
export function navegador() {
  before(async () => {
    exigeArtefatoEmDia();
    browser = await chromium.launch();
  });
  after(() => browser?.close());
  return () => browser;
}

export const PASSOS = RESPOSTAS.guia.oferecer.passos;
export const ROTA = { resumo: "/", gastos: "/gastos", piggy: "/piggy" };

// O servidor de mentira: aplica visto/feito/dispensar/reabrir como o db/guia.py; `post`
// pode responder outra coisa (status e corpo) para o POST de número `n`.
function aplicar(g, c) {
  const n = structuredClone(g);
  if (c.acao === "feito") n.passos.find((p) => p.id === c.passo).feito = true;
  n.estado = c.acao === "dispensar" ? "dispensado" : n.passos.every((p) => p.feito) ? "concluido" : "em_andamento";
  return n;
}
// `dica`: a de Assinaturas "vista" (a da fixture), "nova" (ainda não vista), "sem" (o plano não
// dá) ou "ausente" (servidor anterior ao #728, sem a chave `dicas`); `dicaLenta`: o POST
// /guia/dica responde depois de N ms ou quando a promise dada resolve. `s.dicas`: os ids postados.
export async function abrir({ width = 1280, height = 800, guia = "oferecer", perfil = "padrao", plano = "pro", motion = "reduce", post, rota = "/", perfilLento = 0, semDialog = false, antes, dica = "vista", dicaLenta = 0 } = {}) {
  const ctx = await browser.newContext({ viewport: { width, height }, reducedMotion: motion, timezoneId: "America/Sao_Paulo" });
  // A caixa do Piggy pelo left/top gravado, sem o transform: a inclinação e o voo aumentam o
  // retângulo pintado, e "encostar" é sobre onde ele pousa.
  await ctx.addInitScript(() => {
    window.caixaDoPiggy = () => {
      const e = document.querySelector(".guia-piggy"), left = parseFloat(e.style.left), top = parseFloat(e.style.top);
      return { left, top, right: left + e.offsetWidth, bottom: top + e.offsetHeight };
    };
  });
  // Safari 14 (sem <dialog>): o mesmo corte do dashboard_v2_cmdk.test.mjs.
  if (semDialog) await ctx.addInitScript(() => { delete HTMLDialogElement.prototype.showModal; delete HTMLDialogElement.prototype.close; });
  await servir(ctx, undefined, { perfil, plano });
  // Perfil chegando depois do guia: a corrida em que o convite brigaria com o modal de perfil.
  if (perfilLento) await ctx.route("**/api/v2/perfil", async (r) => { await new Promise((ok) => setTimeout(ok, perfilLento)); return r.fallback(); });
  const s = { g: structuredClone(RESPOSTAS.guia[guia]), posts: [], dicas: [] };
  if (dica === "sem") s.g.dicas = [];
  if (dica === "ausente") delete s.g.dicas;
  if (dica === "nova") s.g.dicas.forEach((d) => { d.vista = false; });
  await ctx.route("**/api/v2/guia/dica", async (r) => {
    const { dica: id } = r.request().postDataJSON();
    s.dicas.push(id);
    s.g.dicas.forEach((d) => { if (d.id === id) d.vista = true; });
    // A resposta é o guia de quando o POST chegou; a lentidão é a da volta.
    const json = structuredClone(s.g);
    if (dicaLenta) await (typeof dicaLenta === "number" ? new Promise((ok) => setTimeout(ok, dicaLenta)) : dicaLenta);
    return r.fulfill({ json });
  });
  await ctx.route("**/api/v2/guia", async (r) => {
    if (r.request().method() === "GET") return r.fulfill({ json: s.g });
    const c = r.request().postDataJSON();
    s.posts.push(c);
    const outro = await post?.(c, s.posts.length, s);
    if (outro) return r.fulfill({ status: outro.status, json: outro.json });
    s.g = aplicar(s.g, c);
    return r.fulfill({ json: s.g });
  });
  await ctx.addCookies([{ name: "csrf_token", value: "tok-123", url: "http://127.0.0.1:1" }]);
  await antes?.(ctx, s); // rotas a mais antes de a página abrir (o SSE da coreografia)
  const page = await ctx.newPage();
  await page.clock.setFixedTime(new Date("2026-10-02T15:00:00Z"));
  const erros = [];
  page.on("pageerror", (e) => erros.push(e.message));
  await page.goto(`${PAINEL}#${rota}`);
  await page.locator("#page-title").waitFor();
  return { ctx, page, s, erros };
}
export const acoes = (s) => s.posts.map((c) => c.passo ? `${c.acao}:${c.passo}` : c.acao);
// Entre passos (ou com o passo noutra tela) o guia espera o toque na aba: "Agora toca em X.",
// com o anel nela (pousado) e o vão do véu sobre ela. Toca no meio do vão, o clique de verdade.
const ABA = "Agora toca";
export const vaoDoVeu = () => {
  const [f0, f1, f2, f3] = [...document.querySelectorAll(".guia-veu")].map((e) => e.getBoundingClientRect());
  return { left: f2.right, top: f0.bottom, right: f3.left, bottom: f1.top };
};
export const tocarAba = async (page, timeout = 10000) => {
  await page.locator("#guia-titulo", { hasText: ABA }).waitFor({ timeout });
  await page.waitForFunction(() => !document.querySelector(".guia-anel").hidden, null, { timeout }); // pousou
  const v = await page.evaluate(vaoDoVeu);
  await page.mouse.click((v.left + v.right) / 2, (v.top + v.bottom) / 2);
};
// Espera o título `t`; no caminho, como a pessoa, toca a aba que o guia pede (o FAZER faz o
// mesmo com o "Entendi"). 10 s: festa (1,6 s), o voo até a aba e o da tela nova.
export const esperaTitulo = async (page, t) => {
  const alvo = page.locator("#guia-titulo", { hasText: t }), aba = page.locator("#guia-titulo", { hasText: ABA });
  for (const fim = Date.now() + 10000; Date.now() < fim && !(await alvo.count());) {
    // 500 ms: se o toque anterior já pegou, o guia navegou e o "Agora toca" não volta
    if (!String(t).startsWith(ABA) && (await aba.count())) {
      // Depois do toque, espera o "Agora toca" sair: o React troca o título um tempo depois do
      // clique, e voltar ao laço antes disso toca de novo, no mesmo ponto — que na tela nova é o
      // véu, e o clique no véu tira o foco do título (o foco com o véu acabava em <body>).
      if (await tocarAba(page, 500).then(() => true, () => false)) await aba.waitFor({ state: "detached", timeout: 2000 }).catch(() => {});
    } else await page.waitForTimeout(100);
  }
  await alvo.waitFor({ timeout: 10000 });
};
export const bora = async (page) => { await page.getByRole("button", { name: "Bora", exact: true }).click(); await esperaTitulo(page, PASSOS[0].fala.titulo); };

// O anel em volta do 1º visível de `sel`, com folga de 2 a 8 px (o voo já pousou).
const temAnel = (sel) => {
  const a = document.querySelector(".guia-anel"), e = [...document.querySelectorAll(sel)].find((x) => x.getClientRects().length);
  if (!a || a.hidden || !e) return false;
  const r = a.getBoundingClientRect(), t = e.getBoundingClientRect();
  return [t.left - r.left, t.top - r.top, r.right - t.right, r.bottom - t.bottom].every((d) => d >= 2 && d <= 8);
};
export const anelNoAlvo = (page, sel) => page.waitForFunction(temAnel, sel, { timeout: 10000 });
// O "Entendi" do balão: o passo sai da apresentação do bloco e vai para o alvo.
export const botaoEntendi = (page) => page.locator(".guia-balao").getByRole("button", { name: "Entendi", exact: true });
export const entendi = (page) => botaoEntendi(page).click();
// "Entendi" e espera o Piggy pousar: o anel em volta de `sel`.
export const irAoAlvo = async (page, sel) => { await entendi(page); await anelNoAlvo(page, sel); };

// A ação real de cada passo, por `acao` do roteiro, no alvo que o guia destaca (já na tela do
// passo: o esperaTitulo toca a aba): até o anel chegar ao alvo, toca o "Entendi" se o balão apresenta o bloco
// (ele pode aparecer um quadro depois do título); aí toca o alvo.
export const ALVO = { "mes.trocado": "mes.trocar", "categoria.aberta": "categorias.item", "piggy.perguntou": "piggy.chip" };
export const FAZER = Object.fromEntries(Object.entries(ALVO).map(([acao, a]) => [acao, async (page) => {
  const sel = `[data-guia="${a}"]`;
  for (let i = 0; i < 100 && !(await page.evaluate(temAnel, sel)); i++) {
    if (await botaoEntendi(page).count()) await entendi(page).catch(() => {});
    await page.waitForTimeout(100);
  }
  await page.locator(sel).first().click();
}]));
export const naRota = (page, h) => page.waitForFunction((h) => location.hash === h, h, { timeout: 5000 });
// Onde o Piggy encosta: [lado a lado, distância à quina de cima, à de baixo].
export const piggyEm = (page, sel) => page.evaluate((sel) => {
  const p = window.caixaDoPiggy();
  const a = [...document.querySelectorAll(sel)].find((e) => e.getClientRects().length).getBoundingClientRect();
  return [p.left < a.right && p.right > a.left, Math.round(p.bottom - a.top), Math.round(a.bottom - p.top)];
}, sel);
