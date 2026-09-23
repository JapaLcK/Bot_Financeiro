import NumberFlow from "@number-flow/react";
import type { ReactNode } from "react";
import { CARD, GOALS, TODAY, isCurrentMonth, keyDate, previousKey, spentUntil, summary } from "../lib/api";
import { monthName, money0, tone } from "../lib/format.js";
import type { DashState } from "../lib/types";
import { Frame } from "../parts/Frame";
import { BRL } from "./Hero";

// Os quatro números do mês num bloco só, separados por filetes (não quatro cartões).
function Stat({ title, tone, value, delta, children }: {
  title: string;
  tone: string;
  value: number;
  delta?: { text: string; good: boolean | null };
  children?: ReactNode;
}) {
  return (
    <section className="stat">
      <h3 className="stat-title"><span className="stat-key" style={{ background: tone }} aria-hidden="true" />{title}</h3>
      <NumberFlow className="stat-value" value={value} locales="pt-BR" format={BRL} />
      {delta && <p className={`stat-delta ${delta.good === null ? "" : delta.good ? "gain" : "warn"}`}>{delta.text}</p>}
      <div className="stat-foot">{children}</div>
    </section>
  );
}

const vs = (now: number, before: number) => (before ? (now - before) / before : 0);

function Income({ s }: { s: DashState }) {
  const m = summary(s.month);
  const prev = previousKey(s.month);
  const d = prev ? vs(m.income, summary(prev).income) : 0;
  const pix = m.launches.filter((l) => l.kind === "income" && l.source === "whatsapp").reduce((a, l) => a + (l.amount ?? 0), 0);
  return (
    <Stat title="Entrou" tone="var(--gain)" value={m.income}
      delta={prev ? { text: `${Math.abs(Math.round(d * 100))}% ${d >= 0 ? "a mais" : "a menos"} que ${monthName(keyDate(prev))}`, good: d >= 0 } : undefined}>
      <dl className="mini-rows">
        <div><dt>Bolsa do estágio</dt><dd className="num">{money0(m.income - pix)}</dd></div>
        <div><dt>Pix e freelas</dt><dd className="num">{money0(pix)}</dd></div>
      </dl>
    </Stat>
  );
}

function Spent({ s }: { s: DashState }) {
  const m = summary(s.month);
  const prev = previousKey(s.month);
  const current = isCurrentMonth(s.month);
  const day = current ? TODAY.getDate() : 31;
  const before = prev ? spentUntil(prev, day) : 0;
  const diff = m.expense - before;
  const diffTone = tone(-diff); // gastar menos é ganho; empate (R$ 0) fica neutro
  const scale = Math.max(m.expense, before) || 1;
  const prevName = prev ? monthName(keyDate(prev)) : "";
  return (
    <Stat title="Saiu" tone="var(--alert)" value={m.expense}
      delta={prev ? { text: `${money0(Math.abs(diff))} ${diff <= 0 ? "a menos" : "a mais"} que ${prevName}${current ? ` até dia ${day}` : ""}`, good: diffTone ? diffTone === "gain" : null } : undefined}>
      {prev && (
        <div className="pace" role="img" aria-label={`Gasto até agora ${money0(m.expense)}; ${prevName} no mesmo período ${money0(before)}`}>
          <span className="pace-bar"><i style={{ transform: `scaleX(${m.expense / scale})` }} /></span>
          <span className="pace-bar prev"><i style={{ transform: `scaleX(${before / scale})` }} /></span>
          <span className="pace-legend faint"><span>{monthName(keyDate(s.month)).slice(0, 3)}</span><span>{prevName.slice(0, 3)}</span></span>
        </div>
      )}
    </Stat>
  );
}

function Invoice({ s }: { s: DashState }) {
  const m = summary(s.month);
  const used = m.invoice / CARD.limit;
  const [y, mm] = s.month.split("-").map(Number);
  const due = new Date(y, mm, CARD.dueDay);
  const current = isCurrentMonth(s.month);
  return (
    <Stat title={current ? "Fatura aberta" : `Fatura de ${monthName(new Date(y, mm - 1, 2))}`} tone="var(--warn)" value={m.invoice}
      delta={{ text: current ? `vence ${due.getDate()} de ${monthName(due)}` : `paga em ${due.getDate()} de ${monthName(due)}`, good: null }}>
      <div className="meter warn" role="meter" aria-valuemin={0} aria-valuemax={CARD.limit} aria-valuenow={m.invoice} aria-label="Uso do limite">
        <span style={{ transform: `scaleX(${Math.min(1, used)})` }} />
      </div>
      <p className="faint stat-note">{Math.round(used * 100)}% do limite de <span className="num">{money0(CARD.limit)}</span></p>
    </Stat>
  );
}

function Saved({ s }: { s: DashState }) {
  const m = summary(s.month);
  const total = GOALS.reduce((a, g) => a + g.saved, 0);
  return (
    <Stat title="Guardado no mês" tone="#9085e9" value={m.saved} delta={{ text: `automático nas ${GOALS.length} caixinhas`, good: null }}>
      <p className="stat-note"><span className="faint">Total nas caixinhas</span> <b className="num">{money0(total)}</b></p>
    </Stat>
  );
}

export function MonthStats({ s }: { s: DashState }) {
  return (
    <Frame id="resumo" title="Resumo do mês" className="w-stats">
      <div className="stats">
        <Income s={s} />
        <Spent s={s} />
        <Invoice s={s} />
        <Saved s={s} />
      </div>
    </Frame>
  );
}
