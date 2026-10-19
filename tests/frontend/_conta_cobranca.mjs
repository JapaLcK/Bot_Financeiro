/**
 * O que os testes da Cobrança da Conta (dashboard_v2_conta_cobranca*.test.mjs) compartilham: os textos, o `abrirCobranca` e o que ele lê da tela.
 * Não é `*.test.mjs` de propósito: o `node --test tests/frontend/*.test.mjs` não o roda.
 */
import { abrir } from "./_guia.mjs";
import { servidor } from "./_conta_seguranca.mjs";

export const PROIBIDOS = /Renova em|Teste grátis|Termina em|Pagamento pendente/;
export const T4 = "Não foi possível carregar a cobrança.";
export const T25 = "Não foi possível abrir o gerenciamento da assinatura agora. Tente novamente em instantes.";
export const T26 = "Não foi possível desfazer a troca agora. Tente novamente em instantes.";
export const T27 = "Recarregue a página e tente de novo.";
export const T28 = "Troca desfeita. Seu plano continua como está.";
export const TROCA = { plan: "pro", interval: "monthly", effective_at: "2026-12-12" };

// Encurta o prazo de segurança da navegação ao Stripe (10000 ms, e só ele) para `ms` e registra o que o app pediu em `window.__seg`.
export const encurtaSeguranca = (page, ms = 400) => page.evaluate((m) => { const st = window.setTimeout; window.__seg = []; window.setTimeout = (f, d, ...a) => { if (d === 10000) { window.__seg.push(d); d = m; } return st(f, d, ...a); }; }, ms);

export async function abrirCobranca({ width = 1280, height = 800, plano = "plus", esperar = true, ...opts } = {}) {
  const s = servidor(opts);
  const p = await abrir({ guia: "concluido", width, height, plano, rota: "/conta/cobranca", antes: (ctx) => s.antes(ctx) });
  p.page.setDefaultTimeout(15000);
  if (esperar) await p.page.locator(".conta-cobranca dl, .conta-cobranca [role=alert]").first().waitFor();
  const { page } = p;
  return {
    ...p, s,
    secao: page.locator(".conta-cobranca"),
    alerta: page.locator(".conta-cobranca [role=alert]"),
    aviso: page.locator(".conta-cobranca > p[role=status]"),
    bt: (nome) => page.getByRole("button", { name: nome, exact: true }),
    link: (nome) => page.getByRole("link", { name: nome, exact: true }),
    // o que se lê da tela: o texto VISÍVEL do dl (sem o botão de ação do fim; sem o .sr-only, que é do leitor de tela) e os nomes dos botões/links
    tela: async () => ({
      texto: (await page.locator(".conta-cobranca").evaluate((e) => { const sr = [...e.querySelectorAll(".sr-only")], dl = e.querySelector("dl") ?? e; sr.forEach((x) => { x.hidden = true; }); const t = dl.innerText; sr.forEach((x) => { x.hidden = false; }); return t; })).replace(/\s+/g, " ").trim(),
      acoes: await page.locator(".conta-cobranca button, .conta-cobranca a").evaluateAll((es) => es.map((e) => e.getAttribute("aria-label") || e.textContent.trim())),
    }),
    // espera o botão (seletor CSS) perder o aria-disabled
    soltou: (sel) => page.waitForFunction((x) => !document.querySelector(x).hasAttribute("aria-disabled"), sel),
    fecha: () => p.ctx.close(),
  };
}

