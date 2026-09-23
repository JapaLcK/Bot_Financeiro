import { useMemo } from "react";
import { MONTHS, TODAY, addDays, scheduled } from "../lib/api";
import { money, money0, relativeDays, weekday } from "../lib/format.js";
import { dayKey, set } from "../lib/store.js";
import type { DashState, Launch } from "../lib/types";
import { Frame } from "../parts/Frame";

const DAY = 86400000;

// Compromissos dos últimos 5 dias (já pagos) e dos próximos `span` dias.
export function Bills({ s, days: span = 30 }: { s: DashState; days?: number }) {
  const items = useMemo(() => scheduled(addDays(TODAY, -5), addDays(TODAY, span)), [span]);
  const due = items.filter((b) => b.date > TODAY && b.kind === "expense" && !b.transfer);
  const known = due.reduce((a, b) => a + (b.amount ?? 0), 0);
  const focus = (b: Launch | null) => set({ highlight: b ? dayKey(b.date) : null });

  return (
    <Frame id="compromissos" title={`Próximos ${span} dias`}>
      <p className="w-lede">A pagar <span className="num">{money0(known)}</span> · as marcadas como estimadas ainda podem mudar</p>
      <ol className="bills" onPointerLeave={() => focus(null)}>
        {items.map((b, i) => {
          const days = Math.round((b.date.getTime() - TODAY.getTime()) / DAY);
          const paid = days <= 0;
          const status = paid ? (b.kind === "income" ? "recebido" : "pago") : b.estimated || b.amount == null ? "estimado" : relativeDays(days);
          return (
            <li key={i}>
              <button type="button" className="bill" data-paid={paid || undefined} data-on={s.highlight === dayKey(b.date) || undefined}
                onPointerEnter={() => focus(b)} onFocus={() => focus(b)} onBlur={() => focus(null)}
                aria-label={`${b.label}, ${b.date.getDate()}/${b.date.getMonth() + 1}, ${b.amount == null ? "valor a definir" : money(b.amount)}, ${status}. Mostrar no gráfico.`}
                onClick={() => { set({ month: MONTHS[MONTHS.length - 1], horizon: days > 7 ? "30" : "mes" }); focus(b); }}>
                <span className="bill-date"><b>{b.date.getDate()}</b><span>{weekday(b.date)}</span></span>
                <span className="bill-name">
                  {b.label}
                  <span className={`bill-status ${!paid && days <= 3 ? "warn" : "faint"}`}>
                    {paid && <i className="ph ph-check" aria-hidden="true" />}{status}
                  </span>
                </span>
                <span className={`bill-amt num ${b.kind === "income" ? "gain" : ""}`}>
                  {b.amount == null ? "—" : `${b.kind === "income" ? "+" : "−"}${money0(b.amount)}`}
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </Frame>
  );
}
