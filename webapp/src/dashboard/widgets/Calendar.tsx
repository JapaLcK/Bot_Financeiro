import type { PointerEvent } from "react";
import { TODAY, catById, isCurrentMonth, keyDate, summary } from "../lib/api";
import { longDate, money, money0 } from "../lib/format.js";
import { useActions } from "../lib/actions";
import { dayKey } from "../lib/store.js";
import type { DashState, Launch } from "../lib/types";
import { Frame } from "../parts/Frame";
import { hideTip, showTip } from "../parts/Tip";

const WEEK = ["D", "S", "T", "Q", "Q", "S", "S"];
const STEPS = 6;

export function Calendar({ s }: { s: DashState }) {
  const { setFilter } = useActions();
  const m = summary(s.month);
  const first = keyDate(s.month);
  const days = m.daily.length;
  const cat = s.filter.category;
  const byDay: Launch[][] = Array.from({ length: days }, () => []);
  // Sem filtro, só o gasto do dia a dia: aluguel e assinaturas apagariam o resto da escala.
  const counts = (l: Launch) => l.kind === "expense" && (cat ? l.category === cat : catById(l.category)?.variable);
  for (const l of m.launches) if (counts(l)) byDay[l.date.getDate() - 1].push(l);
  const totals = byDay.map((ls) => ls.reduce((a, l) => a + (l.amount ?? 0), 0));
  const max = Math.max(...totals, 1);
  const last = isCurrentMonth(s.month) ? TODAY.getDate() : days;
  const top = totals.indexOf(Math.max(...totals));
  const lived = totals.slice(0, last);
  const avg = lived.reduce((a, b) => a + b, 0) / Math.max(1, last);
  const zero = lived.filter((t) => t === 0).length;
  const catLabel = cat ? catById(cat)?.label : null;

  const cells = [
    ...Array.from({ length: first.getDay() }, (_, i) => <span key={`pad-${i}`} className="cal-pad" />),
    ...totals.map((t, i) => {
      const date = new Date(first.getFullYear(), first.getMonth(), i + 1);
      const key = dayKey(date);
      const future = i + 1 > last;
      const step = future || t === 0 ? 0 : Math.max(1, Math.ceil((t / max) * STEPS));
      const on = s.filter.day === key;
      // Tooltip e nome acessível leem a mesma lista: teclado e leitor de tela não têm hover.
      const rows = [...byDay[i]].sort((a, b) => (b.amount ?? 0) - (a.amount ?? 0)).slice(0, 3)
        .map((l) => ({ label: l.label, value: money(l.amount ?? 0), color: catById(l.category)?.color }));
      const tip = (e: PointerEvent) => showTip(e.clientX, e.clientY, {
        title: longDate(date),
        value: future ? "ainda não aconteceu" : money(t),
        rows,
      });
      const maiores = !future && t > 0 ? `. Maiores: ${rows.map((r) => `${r.label} ${r.value}`).join(", ")}` : "";
      return (
        <button key={key} type="button" className="cal-day" data-step={step} data-future={future || undefined}
          data-today={isCurrentMonth(s.month) && i + 1 === last ? "true" : undefined} aria-pressed={on} disabled={future}
          aria-label={`${longDate(date)}: ${future ? "futuro" : money(t)}${maiores}`}
          onPointerMove={tip} onPointerLeave={hideTip}
          onClick={() => setFilter({ day: on ? null : key })}>
          <span>{i + 1}</span>
        </button>
      );
    }),
  ];

  return (
    <Frame id="calendario" title="Dia a dia" aside={catLabel && <span className="tag-demo">{catLabel}</span>}>
      <p className="w-lede">{cat ? "Só esta categoria" : "Sem contas fixas"} · média <span className="num">{money0(avg)}</span>/dia · {zero} {zero === 1 ? "dia" : "dias"} sem gasto</p>
      <div className="cal" role="group" aria-label="Gasto por dia do mês">
        {WEEK.map((w, i) => <span key={i} className="cal-wd" aria-hidden="true">{w}</span>)}
        {cells}
      </div>
      <div className="cal-foot">
        <span className="ramp" aria-hidden="true">
          <span className="faint">menos</span>
          {Array.from({ length: STEPS }, (_, i) => <i key={i} data-step={i + 1} />)}
          <span className="faint">mais</span>
        </span>
        {totals[top] > 0 && (
          <p className="cal-note">
            <span className="faint">Dia mais caro</span>{" "}
            <button type="button" className="link" onClick={() => setFilter({ day: dayKey(new Date(first.getFullYear(), first.getMonth(), top + 1)) })}>
              {top + 1} · {money0(totals[top])}
            </button>
          </p>
        )}
      </div>
    </Frame>
  );
}
