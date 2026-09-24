// Cálculos derivados dos dados: resumo do mês, saldo dia a dia, previsão e simulação.
// Regra de caixa: compra no cartão não sai do saldo no dia; entra na fatura, paga no
// dia 10 do mês seguinte. Pix, débito e Open Finance saem no dia.
import { BANK_CDB_TOTAL, CATEGORIES, CARD, GOALS, INCOMES, LAUNCHES, MONTHS, NET_WORTH, OPENING_BALANCE, RECURRING, TODAY, TRANSFER_DAY, daysIn } from "./data.js";

const DAY = 86400000;
const JUNE_INVOICE = 612.4; // fatura de junho, paga em 10/07 (antes do período dos dados)
export const addDays = (d, n) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
export const sameDay = (a, b) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
export const monthKey = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
export const isCurrentMonth = (key) => key === monthKey(TODAY);
const round2 = (n) => Math.round(n * 100) / 100;

function invoiceFor(key) {
  return round2(LAUNCHES[key].filter((l) => l.source === "cartao").reduce((s, l) => s + l.amount, 0));
}

// Saldo no fim de cada dia, de 1º/07 até hoje, com os eventos do dia.
const LEDGER_DAYS = (() => {
  const days = [];
  let balance = OPENING_BALANCE;
  MONTHS.forEach((key, i) => {
    const [y, m] = key.split("-").map(Number);
    const last = isCurrentMonth(key) ? TODAY.getDate() : daysIn(key);
    const prevInvoice = i === 0 ? JUNE_INVOICE : invoiceFor(MONTHS[i - 1]);
    for (let d = 1; d <= last; d++) {
      const date = new Date(y, m - 1, d);
      const events = [];
      for (const l of LAUNCHES[key]) {
        if (!sameDay(l.date, date)) continue;
        if (l.kind === "income") { balance += l.amount; events.push(l); }
        else if (l.source !== "cartao") balance -= l.amount;
        if (l.kind === "transfer") events.push(l);
        if (l.kind === "expense" && RECURRING.some((r) => r.label === l.label)) events.push(l);
      }
      if (d === CARD.dueDay) {
        balance -= prevInvoice;
        events.push({ kind: "expense", label: "Fatura do cartão", amount: prevInvoice, category: null, source: "openfinance" });
      }
      days.push({ date, value: round2(balance), events });
    }
  });
  return days;
})();

export const BALANCE_TODAY = LEDGER_DAYS[LEDGER_DAYS.length - 1].value;

export function monthSummary(key) {
  const launches = [...LAUNCHES[key]].sort((a, b) => b.date - a.date || b.amount - a.amount);
  const byCategory = Object.fromEntries(CATEGORIES.map((c) => [c.id, 0]));
  let income = 0, expense = 0, saved = 0;
  const daily = new Array(daysIn(key)).fill(0);
  for (const l of launches) {
    if (l.kind === "income") { income += l.amount; continue; }
    if (l.kind === "transfer") { saved += l.amount; continue; }
    expense += l.amount;
    byCategory[l.category] += l.amount;
    daily[l.date.getDate() - 1] += l.amount;
  }
  const days = LEDGER_DAYS.filter((d) => monthKey(d.date) === key);
  const idx = MONTHS.indexOf(key);
  const prev = LEDGER_DAYS.filter((d) => monthKey(d.date) === MONTHS[idx - 1]);
  const opening = idx === 0 ? OPENING_BALANCE : prev[prev.length - 1].value;
  return { key, launches, income: round2(income), expense: round2(expense), saved: round2(saved), byCategory, daily, days, opening, invoice: invoiceFor(key) };
}

export const previousKey = (key) => MONTHS[MONTHS.indexOf(key) - 1] || null;

// Gasto no mês anterior até o mesmo dia do mês (para comparar ritmo).
export function spentUntil(key, day) {
  return round2(LAUNCHES[key].filter((l) => l.kind === "expense" && l.date.getDate() <= day).reduce((s, l) => s + l.amount, 0));
}

// Média diária dos gastos variáveis nos últimos 60 dias, por categoria, e o desvio do total.
export const PACE = (() => {
  const from = addDays(TODAY, -59);
  const all = MONTHS.flatMap((k) => LAUNCHES[k]).filter((l) => l.kind === "expense" && l.date >= from && !RECURRING.some((r) => r.label === l.label));
  const perCat = Object.fromEntries(CATEGORIES.map((c) => [c.id, 0]));
  const totals = new Array(60).fill(0);
  for (const l of all) {
    perCat[l.category] += l.amount / 60;
    totals[Math.round((l.date - from) / DAY)] += l.amount;
  }
  const mean = totals.reduce((s, x) => s + x, 0) / 60;
  const std = Math.sqrt(totals.reduce((s, x) => s + (x - mean) ** 2, 0) / 59);
  return { perCat, std };
})();

export const HORIZONS = { mes: "Fim do mês", 30: "30 dias", 90: "90 dias" };

export function horizonEnd(h) {
  if (h === "mes") return new Date(TODAY.getFullYear(), TODAY.getMonth() + 1, 0);
  return addDays(TODAY, Number(h));
}

// Compromissos conhecidos entre duas datas (inclusive), fora os gastos variáveis.
export function scheduled(from, to) {
  const out = [];
  for (let d = new Date(from); d <= to; d = addDays(d, 1)) {
    const day = d.getDate();
    for (const r of RECURRING) if (r.day === day) out.push({ date: d, kind: "expense", label: r.label, amount: r.amount, category: r.category, source: r.source, estimated: r.label === "Conta de luz" });
    for (const i of INCOMES) if (i.day === day) out.push({ date: d, kind: "income", label: i.label, amount: i.amount });
    if (day === TRANSFER_DAY) out.push({ date: d, kind: "expense", transfer: true, label: "Guardado nas caixinhas", amount: GOALS.reduce((a, g) => a + g.monthly, 0) });
    if (day === CARD.dueDay) {
      const k = monthKey(addDays(d, -day)); // fatura do mês anterior
      const known = LAUNCHES[k] !== undefined;
      out.push({ date: d, kind: "expense", label: "Fatura do cartão", amount: known ? invoiceFor(k) : null, estimated: !known, invoice: true });
    }
  }
  return out;
}

// sim = { cuts: {categoria: 0..1}, extra: R$/mês }
export function dailyDelta(sim) {
  const saved = Object.entries(sim.cuts).reduce((s, [c, pct]) => s + (PACE.perCat[c] || 0) * pct, 0);
  return saved - (sim.extra * 12) / 365;
}
export const monthlyFromDaily = (d) => round2((d * 365) / 12);

// Série do gráfico: realizado do mês até hoje, depois previsão até o horizonte.
export function trajectory(key, h, sim) {
  const s = monthSummary(key);
  const real = s.days.map((d) => ({ date: d.date, value: d.value, events: d.events, real: true }));
  if (!isCurrentMonth(key)) return { points: real, end: real[real.length - 1], start: s.opening };
  const end = horizonEnd(h);
  const variable = Object.values(PACE.perCat).reduce((a, b) => a + b, 0);
  const delta = sim ? dailyDelta(sim) : 0;
  const points = [...real];
  let base = BALANCE_TODAY, simv = BALANCE_TODAY, n = 0;
  const events = scheduled(addDays(TODAY, 1), end);
  // Faturas futuras sem dado: a previsão já desconta o gasto variável no dia, então a
  // parte estimada da fatura seria contada duas vezes. Fica fora da soma, só marcada.
  for (let d = addDays(TODAY, 1); d <= end; d = addDays(d, 1)) {
    n++;
    const today = events.filter((e) => sameDay(e.date, d));
    const fixed = today.reduce((acc, e) => acc + (e.amount == null || e.estimated && e.invoice ? 0 : e.kind === "income" ? e.amount : -e.amount), 0);
    base += fixed - variable;
    simv += fixed - variable + delta;
    const band = PACE.std * Math.sqrt(n);
    points.push({ date: d, value: round2(base), sim: round2(simv), lo: round2(base - band), hi: round2(base + band), events: today, real: false });
  }
  return { points, end: points[points.length - 1], start: s.opening };
}

export function goalEta(goal, extraMonthly = 0) {
  const monthly = goal.monthly + Math.max(0, extraMonthly);
  const months = Math.max(0, Math.ceil((goal.target - goal.saved) / monthly));
  return { months, date: new Date(TODAY.getFullYear(), TODAY.getMonth() + months, 1) };
}

export function netWorth() {
  const rows = NET_WORTH.map((r) => ({ ...r }));
  const last = rows[rows.length - 1];
  last.conta = BALANCE_TODAY;
  last.caixinhas = caixinhasTotal();
  return rows.map((r) => ({ ...r, total: round2(r.conta + r.caixinhas + r.investimentos) }));
}

export const goalsTotal = () => GOALS.reduce((s, g) => s + g.saved, 0);
// Caixinhas = metas + o que o banco guarda (via Open Finance), que entra só pelo total.
export const caixinhasTotal = () => round2(goalsTotal() + BANK_CDB_TOTAL);
