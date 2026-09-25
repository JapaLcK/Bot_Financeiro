import { axisMoney, money0, monthName, monthShort } from "../lib/format.js";

// Seis meses em barras (renda, parcelas). O valor por barra some no bloco estreito;
// o texto para leitor de tela fica sempre.
export function MonthBars({ rows, color, on, label }: {
  rows: { key: string; date: Date; value: number }[];
  color: string;
  on?: string;
  label: string;
}) {
  const peak = Math.max(...rows.map((r) => r.value), 1);
  return (
    <ul className="months m6" aria-label={label}>
      {rows.map((r) => (
        <li key={r.key} data-on={r.key === on || undefined}>
          <span className="months-bar" aria-hidden="true"><i style={{ background: color, transform: `scaleY(${r.value / peak})` }} /></span>
          <b className="num" aria-hidden="true">{axisMoney(r.value)}</b>
          <span className="faint" aria-hidden="true">{monthShort(r.date)}</span>
          <span className="sr-only">{monthName(r.date)}: {money0(r.value)}</span>
        </li>
      ))}
    </ul>
  );
}
