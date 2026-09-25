import { CATEGORIES, TODAY, isCurrentMonth, previousKey, summary } from "../lib/api";
import { LAUNCHES } from "../lib/data.js";
import { money0 } from "../lib/format.js";
import { setFilter } from "../lib/store.js";
import type { DashState, Launch } from "../lib/types";
import { Frame } from "../parts/Frame";

// Gasto de uma categoria num mês, até um dia do mês (para comparar no mesmo ponto).
function catUntil(key: string, cat: string, day: number) {
  return (LAUNCHES[key] as Launch[])
    .filter((l) => l.kind === "expense" && l.category === cat && l.date.getDate() <= day)
    .reduce((a, l) => a + (l.amount ?? 0), 0);
}

export function Categories({ s }: { s: DashState }) {
  const m = summary(s.month);
  const prev = previousKey(s.month);
  const day = isCurrentMonth(s.month) ? TODAY.getDate() : 31;
  const rows = CATEGORIES
    .map((c) => ({ c, value: m.byCategory[c.id], before: prev ? catUntil(prev, c.id, day) : null }))
    .filter((r) => r.value > 0)
    .sort((a, b) => b.value - a.value);
  const max = Math.max(...rows.map((r) => Math.max(r.value, r.before ?? 0)), 1);
  const active = s.filter.category;

  return (
    <Frame id="categorias" title="Para onde vai"
      aside={active && <button type="button" className="chip" aria-pressed="true" onClick={() => setFilter({ category: null })}>Limpar <i className="ph ph-x x" aria-hidden="true" /></button>}>
      <p className="w-lede"><span className="num">{money0(m.expense)}</span> em {rows.length} categorias{prev ? <span className="faint"> · traço = mês anterior no mesmo dia</span> : null}</p>
      <ul className="cats">
        {rows.map(({ c, value, before }) => {
          const d = before ? (value - before) / before : null;
          const on = active === c.id;
          return (
            <li key={c.id}>
              <button type="button" className="cat" aria-pressed={on} data-dim={active && !on ? "true" : undefined}
                onClick={() => setFilter({ category: on ? null : c.id })}
                aria-label={`${c.label}: ${money0(value)}${d != null ? `, ${Math.round(Math.abs(d) * 100)}% ${d > 0 ? "a mais" : "a menos"} que no mês anterior` : ""}. Filtrar lançamentos.`}>
                <span className="cat-name"><i className={`ph ${c.icon}`} style={{ color: c.color }} aria-hidden="true" />{c.label}</span>
                <span className="cat-val num">{money0(value)}</span>
                <span className={`cat-delta num ${d == null ? "" : d > 0.15 ? "warn" : d < -0.05 ? "gain" : "faint"}`}>
                  {d == null || !isFinite(d) ? "—" : Math.abs(d) < 0.005 ? "igual" : <><i className={`ph ${d > 0 ? "ph-arrow-up" : "ph-arrow-down"}`} aria-hidden="true" />{Math.round(Math.abs(d) * 100)}%</>}
                </span>
                <span className="cat-track" aria-hidden="true">
                  <span className="cat-fill" style={{ background: c.color, transform: `scaleX(${value / max})` }} />
                  {before != null && before > 0 && <span className="cat-ghost" style={{ left: `${(before / max) * 100}%` }} />}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </Frame>
  );
}
