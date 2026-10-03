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
export async function abrir({ width = 1280, height = 800, guia = "oferecer", perfil = "padrao", motion = "reduce", post, rota = "/", perfilLento = 0, semDialog = false, antes } = {}) {
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
  await servir(ctx, undefined, { perfil });
  // Perfil chegando depois do guia: a corrida em que o convite brigaria com o modal de perfil.
  if (perfilLento) await ctx.route("**/api/v2/perfil", async (r) => { await new Promise((ok) => setTimeout(ok, perfilLento)); return r.fallback(); });
  const s = { g: structuredClone(RESPOSTAS.guia[guia]), posts: [] };
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
// 10 s: entre um passo e o seguinte, com `reduce`, vão a festa, a pausa e a troca (~3 s).
export const esperaTitulo = (page, t) => page.locator("#guia-titulo", { hasText: t }).waitFor({ timeout: 10000 });
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

// A ação real de cada passo, por `acao` do roteiro, no alvo que o guia destaca (ele mesmo leva
// até a tela do passo): até o anel chegar ao alvo, toca o "Entendi" se o balão apresenta o bloco
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
