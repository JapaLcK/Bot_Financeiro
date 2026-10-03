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
export async function abrir({ width = 1280, height = 800, guia = "oferecer", perfil = "padrao", motion = "reduce", post, rota = "/", perfilLento = 0, semDialog = false } = {}) {
  const ctx = await browser.newContext({ viewport: { width, height }, reducedMotion: motion, timezoneId: "America/Sao_Paulo" });
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
  const page = await ctx.newPage();
  await page.clock.setFixedTime(new Date("2026-10-02T15:00:00Z"));
  const erros = [];
  page.on("pageerror", (e) => erros.push(e.message));
  await page.goto(`${PAINEL}#${rota}`);
  await page.locator("#page-title").waitFor();
  return { ctx, page, s, erros };
}
export const acoes = (s) => s.posts.map((c) => c.passo ? `${c.acao}:${c.passo}` : c.acao);
export const esperaTitulo = (page, t) => page.locator("#guia-titulo", { hasText: t }).waitFor({ timeout: 5000 });
export const bora = async (page) => { await page.getByRole("button", { name: "Bora", exact: true }).click(); await esperaTitulo(page, PASSOS[0].fala.titulo); };

// A ação real de cada passo, por `acao` do roteiro.
export const FAZER = {
  "mes.trocado": (page) => page.getByRole("button", { name: "Mês anterior", exact: true }).click(),
  "categoria.aberta": async (page) => { await page.locator('.rail [data-guia="nav.gastos"]').click(); await page.locator('[data-guia="categorias.lista"] .cat').nth(1).click(); },
  "piggy.perguntou": async (page) => { await page.locator("#askbar-input").fill("Quanto gastei este mês?"); await page.locator("#askbar-input").press("Enter"); },
};
