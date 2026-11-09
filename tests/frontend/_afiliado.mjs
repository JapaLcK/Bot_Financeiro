/**
 * O que os testes da área do afiliado (dashboard_v2_afiliado*.test.mjs, afiliado_app.test.mjs) compartilham: as contas de fixture e o servidor de mentira
 * das rotas de frontend/routes/affiliates.py (mesmos campos e status). O `afiliado` do /api/v2/me é sobrescrito aqui e não em api_v2_respostas.json:
 * tests/test_api_v2_contrato.py exige que as chaves de `me` sejam as de `plan_tier`. O relógio do painel é fixo em 2026-10-02 (`abrirPainel`).
 * Não é `*.test.mjs` de propósito: o `node --test tests/frontend/*.test.mjs` não o roda.
 */
import assert from "node:assert/strict";
import { join } from "node:path";
import { FRONTEND, ORIGIN, RESPOSTAS, abrirPainel, servir } from "./_painel.mjs";

const comissao = (id, extra) => ({ id, invoice_amount: 59.9, amount: 5.99, original_amount: null, in_review: false, checking: false, status: "pending", available_at: "2026-09-20T12:00:00.000000+00:00", payout_id: null, payment_refund: "none", created_at: "2026-08-21T12:00:00.000000+00:00", ...extra });

/** Ana: afiliada ativa, 12 cadastros, 6 pagantes (1 reembolsado), R$ 120,00 para sacar. */
export const ATIVA = {
  code: "ANA2026", status: "active", link: "https://pigbankai.com/r/ANA2026", commission_percent: 10, has_pix_key: false, min_payout: 50, hold_days: 30,
  stats: { referrals: 12, held: 30, available: 120, review: 0, requested: 0, paid: 200, generated: 350, paying_clients: 6, refunded_clients: 1, refunds: { total: 2, partial: 1 } },
  commissions: [
    comissao(1, { available_at: "2026-10-20T12:00:00+00:00" }),
    comissao(2),
    comissao(3, { status: "paid", amount: 200, invoice_amount: 2000 }),
    comissao(4, { status: "reversed", payment_refund: "total" }),
    comissao(5, { payment_refund: "partial" }),
    comissao(6, { payment_refund: "total" }),
  ],
  payouts: [{ id: 8, amount: 200, status: "paid", requested_at: "2026-09-01T12:00:00+00:00", paid_at: "2026-09-03T12:00:00+00:00", note: null }],
};
export const DESATIVADA = { ...ATIVA, status: "disabled" };
export const ZERO = { ...ATIVA, commission_percent: 0, commissions: [], payouts: [], stats: { ...ATIVA.stats, held: 0, available: 0, review: 0, requested: 0, paid: 0, generated: 0, refunds: { total: 0, partial: 0 }, refunded_clients: 0 } };
export const ABAIXO = { ...ATIVA, stats: { ...ATIVA.stats, available: 49.99 } };
export const COM_SAQUE = {
  ...ATIVA,
  stats: { ...ATIVA.stats, available: 0, requested: 120, generated: 350 },
  commissions: [comissao(2, { payout_id: 9 })],
  payouts: [{ id: 9, amount: 120, status: "requested", requested_at: "2026-10-01T12:00:00+00:00", paid_at: null, note: null }],
};
/** Reembolso parcial: R$ 100 fora do saque até a revisão, e uma comissão já ajustada pelo admin. */
export const EM_REVISAO = {
  ...ATIVA,
  stats: { ...ATIVA.stats, held: 70, available: 70, review: 100, generated: 440 },
  commissions: [
    comissao(10, { amount: 100, invoice_amount: 1000, in_review: true, payment_refund: "partial" }),
    comissao(11, { amount: 60, original_amount: 100, invoice_amount: 1000, payment_refund: "partial" }),
    comissao(12, { status: "paid", amount: 200, invoice_amount: 2000, payment_refund: "total" }),
    comissao(13, { amount: 40, invoice_amount: 400, checking: true }), // vencida, mas ainda em conferência na Stripe
  ],
};
export const REJEITADO = { ...ATIVA, payouts: [{ id: 7, amount: 90, status: "rejected", requested_at: "2026-09-10T12:00:00+00:00", paid_at: null, note: "Chave Pix não encontrada" }] };

export const QUADROS = ".afil-quadros > div";
export const quadros = (page) => page.locator(QUADROS).evaluateAll((d) => Object.fromEntries(d.map((e) => [e.querySelector("dt").textContent, e.querySelector("dd").textContent])));

// `s.corpo`: o corpo do GET (null = 404, não é afiliado). `s.gets`, `s.posts`: o que saiu. `s.post(rr, q)` e `s.get(rr)`: ganchos que respondem (`rr.fulfill`/`rr.abort`) ou só esperam e deixam a resposta padrão seguir.
const tratou = async (gancho, r, q) => {
  let feito = false;
  const rr = { fulfill: (o) => { feito = true; return r.fulfill(o); }, abort: () => { feito = true; return r.abort(); } };
  if (gancho) await gancho(rr, q);
  return feito;
};
export function servidor(corpo = ATIVA) {
  const s = { corpo, gets: 0, posts: [], post: null, get: null };
  s.antes = async (ctx) => {
    await ctx.route((u) => u.pathname === "/api/affiliate/me", async (r) => {
      s.gets += 1;
      if (await tratou(s.get, r)) return;
      return s.corpo ? r.fulfill({ json: s.corpo }) : r.fulfill({ status: 404, json: { detail: "Você não faz parte do programa de afiliados." } });
    });
    await ctx.route((u) => u.pathname === "/api/affiliate/payout", async (r) => {
      const q = r.request();
      s.posts.push({ corpo: q.postData(), csrf: q.headers()["x-csrf-token"], tipo: q.headers()["content-type"], url: q.url() });
      if (await tratou(s.post, r, q)) return;
      s.corpo = COM_SAQUE;
      return r.fulfill({ json: { ok: true, payout: { id: 9, amount: 120, status: "requested", requested_at: "2026-10-02T15:00:00+00:00" } } });
    });
  };
  return s;
}

const SEM_RELEVANCIA = 1; // o `marca` do pb_conta é o uid do perfil do harness

/** O /painel em `rota` para quem tem `afiliado` no /me ("ausente" = servidor antigo, sem o campo). */
export async function abrirAfiliado(browser, { width = 1280, height = 800, afiliado = true, corpo = ATIVA, rota = "/afiliado", s = servidor(corpo), espera = "#page-title", clipboard } = {}) {
  const p = await abrirPainel(browser, { width, espera: null, perfil: "padrao", marca: String(SEM_RELEVANCIA) });
  await p.page.setViewportSize({ width, height });
  await s.antes(p.ctx);
  await p.ctx.route((u) => u.pathname === "/api/v2/me", (r) => r.fulfill({ json: afiliado === "ausente" ? RESPOSTAS.me.pro : { ...RESPOSTAS.me.pro, afiliado } }));
  if (clipboard) await p.ctx.addInitScript(clipboard);
  await p.ir(rota);
  if (espera) await p.page.locator(espera).first().waitFor();
  return { ...p, s };
}

export const comAfiliado = async (browser, opts, fn) => {
  const t = await abrirAfiliado(browser, opts);
  try { await fn(t); assert.deepEqual(t.erros, [], "pageerror"); } finally { await t.ctx.close(); }
};

/** A página sem plano (GET /afiliado): o servidor entrega o painel.html nesse caminho. `v2`: toda requisição à /api/v2 (o /me daria 402). */
export async function abrirSemPlano(browser, { width = 1280, height = 800, corpo = ATIVA, userAgent, s = servidor(corpo) } = {}) {
  const ctx = await browser.newContext({ viewport: { width, height }, reducedMotion: "reduce", timezoneId: "America/Sao_Paulo", userAgent });
  await servir(ctx, undefined, { marca: String(SEM_RELEVANCIA) });
  await ctx.addCookies([{ name: "csrf_token", value: "tok-123", url: ORIGIN }]);
  await s.antes(ctx);
  const v2 = [];
  await ctx.route((u) => u.pathname.startsWith("/api/v2/"), (r) => { v2.push(new URL(r.request().url()).pathname); return r.fulfill({ status: 402, json: { error: { code: "plan_required", message: "x" } } }); });
  await ctx.route((u) => u.pathname === "/afiliado", (r) => r.fulfill({ path: join(FRONTEND, "painel.html"), contentType: "text/html" }));
  const page = await ctx.newPage();
  await page.clock.setFixedTime(new Date("2026-10-02T15:00:00Z"));
  const erros = [];
  page.on("pageerror", (e) => erros.push(e.message));
  await page.goto(`${ORIGIN}/afiliado`);
  await page.locator("#page-title").waitFor();
  return { ctx, page, erros, s, v2 };
}

/** Preenche a chave e abre a confirmação. */
export async function pedirSaque(page, chave = "ana@pix.com") {
  await page.locator("#afil-pix").fill(chave);
  await page.getByRole("button", { name: /^Sacar/ }).click();
  return page.getByRole("dialog");
}
export const confirmar = (page) => page.getByRole("dialog").getByRole("button", { name: "Confirmar saque" });

/** Espera (no Node, sem relógio do navegador) a condição ficar verdadeira. */
export async function ate(cond, ms = 8000) {
  const fim = Date.now() + ms;
  while (!cond()) { if (Date.now() > fim) throw new Error("tempo esgotado: " + cond); await new Promise((r) => setTimeout(r, 50)); }
}
