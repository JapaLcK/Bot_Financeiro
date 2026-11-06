// Programa de afiliados (frontend/routes/affiliates.py): rotas fora da /api/v2, sem schema OpenAPI. Os tipos são escritos aqui, à mão, e a leitura confere o formato na borda:
// resposta fora do contrato vira ErroApi(200, "contrato"), nunca uma tela quebrada. O dinheiro chega em reais (número) e só é exibido: a tela não faz conta.
import { queryOptions } from "@tanstack/react-query";
import { DEMO, ErroApi, FUSO, chamar, escrever, instante, meQuery, sessaoDe } from "../lib/v2";
import { contrato, inteiro, objeto, texto, validade } from "../familia/api";

const STATUS_COMISSAO = ["pending", "paid", "reversed"] as const;
const REEMBOLSOS = ["none", "partial", "total"] as const;
const STATUS_SAQUE = ["requested", "paid", "rejected"] as const;

export type Comissao = { id: number; invoice_amount: number; amount: number; status: (typeof STATUS_COMISSAO)[number]; available_at: string; payout_id: number | null; payment_refund: (typeof REEMBOLSOS)[number]; created_at: string };
export type Pedido = { id: number; amount: number; status: (typeof STATUS_SAQUE)[number]; requested_at: string; note: string | null };
export type Stats = { referrals: number; paying_clients: number; refunded_clients: number; generated: number; held: number; available: number; requested: number; paid: number; refunds: { total: number; partial: number } };
export type Afiliado = { code: string; link: string; status: "active" | "disabled"; commission_percent: number; has_pix_key: boolean; min_payout: number; hold_days: number; stats: Stats; commissions: Comissao[]; payouts: Pedido[] };

const valor = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v) && v >= 0;
const data = (v: unknown): v is string => texto(v) && Number.isFinite(instante(v));
const um = <T extends string>(v: unknown, lista: readonly T[]): v is T => lista.includes(v as T);

function comissaoDe(c: unknown): Comissao {
  if (!objeto(c) || !inteiro(c.id) || !valor(c.invoice_amount) || !valor(c.amount) || !um(c.status, STATUS_COMISSAO) || !data(c.available_at) || !(c.payout_id === null || inteiro(c.payout_id)) || !um(c.payment_refund, REEMBOLSOS) || !data(c.created_at)) throw contrato();
  return { id: c.id, invoice_amount: c.invoice_amount, amount: c.amount, status: c.status, available_at: c.available_at, payout_id: c.payout_id, payment_refund: c.payment_refund, created_at: c.created_at };
}

function pedidoDe(p: unknown): Pedido {
  if (!objeto(p) || !inteiro(p.id) || !valor(p.amount) || !um(p.status, STATUS_SAQUE) || !data(p.requested_at) || !(p.note === null || typeof p.note === "string")) throw contrato();
  return { id: p.id, amount: p.amount, status: p.status, requested_at: p.requested_at, note: p.note };
}

function statsDe(s: unknown): Stats {
  if (!objeto(s) || !objeto(s.refunds) || !inteiro(s.refunds.total) || !inteiro(s.refunds.partial) || !inteiro(s.referrals) || !inteiro(s.paying_clients) || !inteiro(s.refunded_clients)
    || !valor(s.generated) || !valor(s.held) || !valor(s.available) || !valor(s.requested) || !valor(s.paid)) throw contrato();
  return { referrals: s.referrals, paying_clients: s.paying_clients, refunded_clients: s.refunded_clients, generated: s.generated, held: s.held, available: s.available, requested: s.requested, paid: s.paid, refunds: { total: s.refunds.total, partial: s.refunds.partial } };
}

function afiliadoDe(v: unknown): Afiliado {
  if (!objeto(v) || !texto(v.code) || !texto(v.link) || (v.status !== "active" && v.status !== "disabled") || !valor(v.commission_percent) || typeof v.has_pix_key !== "boolean" || !valor(v.min_payout) || !inteiro(v.hold_days) || !Array.isArray(v.commissions) || !Array.isArray(v.payouts)) throw contrato();
  return { code: v.code, link: v.link, status: v.status, commission_percent: v.commission_percent, has_pix_key: v.has_pix_key, min_payout: v.min_payout, hold_days: v.hold_days, stats: statsDe(v.stats), commissions: v.commissions.map(comissaoDe), payouts: v.payouts.map(pedidoDe) };
}

// `["conta", "afiliado"]`: sem uid na chave, o main.tsx zera esta leitura ao trocar de conta. `null` = não é afiliado (404).
export const afiliadoQuery = queryOptions({
  queryKey: ["conta", "afiliado"],
  queryFn: async ({ signal }): Promise<Afiliado | null> => {
    try { return afiliadoDe(await chamar<unknown>("/api/affiliate/me", { headers: { Accept: "application/json" }, signal })); }
    catch (e) { if (e instanceof ErroApi && e.status === 404) return null; throw e; }
  },
  staleTime: 0, refetchOnWindowFocus: false, networkMode: "always", retry: meQuery.retry,
  enabled: !DEMO,
});

// O corpo é só a chave: valor e dono o servidor decide pela sessão. Função solta e não `useMutation`: a chave Pix não entra no cache do TanStack.
export const solicitarSaque = (pix_key: string) => escrever<unknown>("POST", "/api/affiliate/payout", { pix_key });

// O único 403 com texto para a tela (`raise_if_account_scheduled_for_deletion`): os outros sem `error` são de sessão, como o do CSRF. tests/test_pagina_afiliado.py prende o prefixo.
const exclusao = (e: unknown) => (e instanceof ErroApi && e.status === 403 && e.detalhe?.startsWith("Esta conta está agendada") ? e.detalhe : undefined);
export const falhaLeitura = (e: unknown) => exclusao(e) ?? "Não foi possível carregar sua área de afiliado.";

/** O erro do saque em texto da tela. O `detail` do 400 (`request_payout`) já vem escrito para a tela. */
export function falhaSaque(e: unknown) {
  if (e instanceof ErroApi && e.status === 429) return "Muitos pedidos de saque. Tente de novo mais tarde.";
  if (e instanceof ErroApi && e.status === 400 && e.detalhe) return e.detalhe;
  const agendada = exclusao(e);
  if (agendada) return agendada;
  if (sessaoDe(e)) return "Sua sessão terminou. Recarregue a página.";
  return "Não deu para confirmar o saque. Confira o histórico antes de tentar de novo."; // a resposta pode ter se perdido com o saque criado: a releitura mostra
}

const DIA = new Intl.DateTimeFormat("pt-BR", { timeZone: FUSO, day: "2-digit", month: "2-digit", year: "numeric" });
export const dia = (iso: string) => DIA.format(instante(iso));

export function situacao(c: Comissao): { rotulo: string; classe?: "gain" | "warn" } {
  if (c.status === "reversed") return { rotulo: "estornada", classe: "warn" };
  if (c.status === "paid") return { rotulo: "paga", classe: "gain" };
  if (c.payout_id !== null) return { rotulo: "em saque" };
  if (instante(c.available_at) <= Date.now()) return { rotulo: "disponível", classe: "gain" };
  return { rotulo: `libera ${validade(c.available_at)}` };
}

// Só o rótulo: a tela nunca mostra quem, quando, quanto nem como foi o reembolso.
export const ROTULO_REEMBOLSO: Record<Comissao["payment_refund"], string | null> = { none: null, partial: "reembolso parcial", total: "pagamento reembolsado" };
