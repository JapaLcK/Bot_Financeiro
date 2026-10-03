import { CARD, MONTHS, catById, keyDate, summary } from "../lib/api";
import { dayMonth, money, money0, monthName } from "../lib/format.js";
import type { DashState } from "../lib/types";
import { Frame } from "../parts/Frame";

// Fatura aberta do cartão: as compras que entram nela e o limite usado.
export function Invoice({ s }: { s: DashState }) {
  const key = s.month;
  const m = summary(key);
  const buys = m.launches.filter((l) => l.source === "cartao");
  const used = m.invoice / CARD.limit;
  const d = keyDate(key);
  const due = new Date(d.getFullYear(), d.getMonth() + 1, CARD.dueDay);
  const open = key === MONTHS[MONTHS.length - 1];

  return (
    <Frame id="fatura" title={open ? "Fatura aberta" : `Fatura de ${monthName(d)}`}
      aside={<span className="w-aside-num faint">{CARD.label}</span>}>
      <div className="inv-top">
        <p className="stat-value num">{money0(m.invoice)}</p>
        <p className="faint">{open ? "vence" : "paga em"} {due.getDate()} de {monthName(due)}</p>
      </div>
      <div className="meter warn" role="meter" aria-valuemin={0} aria-valuemax={CARD.limit} aria-valuenow={m.invoice} aria-label="Uso do limite">
        <span style={{ transform: `scaleX(${Math.min(1, used)})` }} />
      </div>
      <p className="faint stat-note">{Math.round(used * 100)}% do limite · <span className="num">{money0(CARD.limit - m.invoice)}</span> livre</p>
      <ul className="inv-list">
        {buys.map((l, i) => {
          const c = catById(l.category);
          return (
            <li key={l.id ?? i}>
              <i className={`ph ${c?.icon}`} style={{ color: c?.color }} aria-hidden="true" />
              <span className="inv-name">{l.label}</span>
              <span className="faint num">{dayMonth(l.date)}</span>
              <b className="num">{money(l.amount ?? 0)}</b>
            </li>
          );
        })}
      </ul>
    </Frame>
  );
}
