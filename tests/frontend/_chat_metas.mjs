// Ajudantes dos testes do assunto `metas` do chat do /painel (dashboard_v2_chat_metas*.test.mjs).
// Não é `*.test.mjs` de propósito: o `node --test tests/frontend/*.test.mjs` não o roda como suíte.
import { chromium } from "playwright";
import { PAINEL, PROTOTIPO, RAIZ, RESPOSTAS, exigeArtefatoEmDia, servir } from "./_painel.mjs";

let browser;
export const iniciar = async () => { exigeArtefatoEmDia(); browser = await chromium.launch(); };
export const encerrar = () => browser?.close();

export const ERRO = "Não consegui buscar agora. Pergunta de novo daqui a pouco.";
export const SENHA = "Crie sua senha para ver suas metas. Depois de criar, volte para o painel novo.";
export const SEM_METAS = "Você ainda não tem metas. Crie a primeira no painel.";
export const CHIP_META = "Quanto falta pra minha próxima meta";
export const CHIP_RESERVA = "Minha reserva de emergência está no tamanho certo?";
export const CHIP_ECONOMIA = "Me ajuda a montar uma meta de economia pra este mês?";

export async function abrir({ width = 1440, metas = "varios", perfil = "padrao", falha = false, demo = false, sse = false } = {}) {
  const ctx = await browser.newContext({ viewport: { width, height: width > 500 ? 900 : 844 }, reducedMotion: "reduce" });
  await servir(ctx, demo ? RAIZ : undefined, { metas, perfil });
  if (demo) await ctx.addInitScript((p) => localStorage.setItem("pigbank.dashboard.profile.v1", JSON.stringify(p)), perfil);
  // Guarda o EventSource do painel para o teste disparar um aviso (o mesmo caminho do SSE).
  if (sse) await ctx.addInitScript(() => { const Real = window.EventSource; window.EventSource = class extends Real { constructor(...args) { super(...args); window.sseDoPainel = this; } }; });
  if (falha) {
    const e = RESPOSTAS.erros[falha];
    await ctx.route("**/api/v2/metas", (r) => (falha === "rede" ? r.abort() : r.fulfill({ status: e.status, json: e.body })));
  }
  const page = await ctx.newPage();
  const gets = [];
  page.on("request", (r) => { if (new URL(r.url()).pathname === "/api/v2/metas") gets.push(r.method()); });
  const erros = [];
  page.on("pageerror", (e) => erros.push(e.message));
  await page.goto(`${demo ? PROTOTIPO : PAINEL}#/piggy`);
  await page.locator("#page-title").waitFor();
  return { ctx, page, gets, erros };
}
export const ultima = (page) => page.locator(".chat > .msg-piggy").last();
export const chip = (page, texto) => page.locator(".chat-empty .chat-follow button", { hasText: texto }).click();
export async function perguntar(page, texto) {
  const antes = await page.locator(".chat > .msg-piggy").count();
  await page.locator("#askbar-input").fill(texto);
  await page.locator("#askbar-input").press("Enter");
  await page.waitForFunction((n) => document.querySelectorAll(".chat > .msg-piggy").length > n, antes);
}
// O texto da última resposta depois de sair do "Calculando…".
export async function resposta(page) {
  await page.waitForFunction(() => ![...document.querySelectorAll(".chat > .msg-piggy")].pop().querySelector(".msg-text").textContent.includes("Calculando"), null, { timeout: 15000 });
  return ultima(page).locator(".msg-text").innerText();
}
export const pilulas = (page) => ultima(page).locator(".pilula");
export const varios = RESPOSTAS.metas.varios;
// A resposta de /metas trocada (o `ctx.route` registrado por último vence).
export const servirMetas = (ctx, metas) => ctx.route("**/api/v2/metas", (r) => r.fulfill({ json: { ...varios, metas } }));
export const igual = (m, campos) => ({ ...m, ...campos });
export const batida = { atual: "18000.00", falta: "0.00", percentual: 100, chega_em: null };
