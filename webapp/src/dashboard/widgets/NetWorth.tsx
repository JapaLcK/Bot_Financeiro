import NumberFlow from "@number-flow/react";
import { useState, type PointerEvent } from "react";
import { netWorth } from "../lib/api";
import { money, money0, monthYear, signed0 } from "../lib/format.js";
import { useSize } from "../useDash";
import { Frame } from "../parts/Frame";
import { hideTip, showTip } from "../parts/Tip";
import { BRL } from "./Hero";

const ROWS = netWorth();
const PARTS = [
  { key: "conta", label: "Conta" },
  { key: "caixinhas", label: "Caixinhas" },
  { key: "investimentos", label: "Investimentos" },
] as const;

export function NetWorth({ title = "Patrimônio" }: { title?: string }) {
  const [box, { width, height }] = useSize<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const last = ROWS[ROWS.length - 1];
  const first = ROWS[0];
  const vals = ROWS.map((r) => r.total);
  const lo = Math.min(...vals) * 0.96, hi = Math.max(...vals) * 1.02;
  const pad = 6;
  const x = (i: number) => pad + (i / (ROWS.length - 1)) * (width - pad * 2);
  const y = (v: number) => pad + (1 - (v - lo) / (hi - lo)) * (height - pad * 2 - 16);
  const path = ROWS.map((r, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(r.total).toFixed(1)}`).join("");
  const area = `${path}L${x(ROWS.length - 1)},${height - 16}L${x(0)},${height - 16}Z`;

  const onMove = (e: PointerEvent) => {
    const r = box.current!.getBoundingClientRect();
    const i = Math.max(0, Math.min(ROWS.length - 1, Math.round(((e.clientX - r.left - pad) / (width - pad * 2)) * (ROWS.length - 1))));
    setHover(i);
    const row = ROWS[i];
    showTip(e.clientX, e.clientY, { title: monthYear(row.date), value: money(row.total), rows: PARTS.map((p) => ({ label: p.label, value: money0(row[p.key]) })) });
  };

  return (
    <Frame id="patrimonio" title={title} className="w-nw"
      aside={<span className="w-aside-num gain num">{signed0(last.total - first.total)} <span className="faint">em 12 meses</span></span>}>
      <div className="nw-top">
        <NumberFlow className="stat-value" value={last.total} locales="pt-BR" format={BRL} />
        <dl className="nw-parts">
          {PARTS.map((p) => <div key={p.key}><dt className="faint">{p.label}</dt><dd className="num">{money0(last[p.key])}</dd></div>)}
        </dl>
      </div>
      <div className="nw-chart" ref={box} onPointerMove={onMove} onPointerLeave={() => { setHover(null); hideTip(); }}>
        {width > 0 && (
          <svg width={width} height={height} role="img"
            aria-label={`Patrimônio de ${monthYear(first.date)} a ${monthYear(last.date)}: de ${money0(first.total)} para ${money0(last.total)}.`}>
            <defs>
              <linearGradient id="nw-wash" x1="0" x2="0" y1="0" y2="1">
                <stop offset="0" stopColor="#9085e9" stopOpacity="0.28" />
                <stop offset="1" stopColor="#9085e9" stopOpacity="0" />
              </linearGradient>
            </defs>
            <path d={area} fill="url(#nw-wash)" />
            <path d={path} className="line real" />
            {hover != null && <line x1={x(hover)} x2={x(hover)} y1={pad} y2={height - 16} className="cross-line" />}
            {hover != null && <circle cx={x(hover)} cy={y(ROWS[hover].total)} r={4.5} className="cross-dot" />}
            <circle cx={x(ROWS.length - 1)} cy={y(last.total)} r={4} className="today-dot" />
            {[0, Math.floor((ROWS.length - 1) / 2), ROWS.length - 1].map((i) => (
              <text key={i} x={x(i)} y={height - 4} className="axis" textAnchor={i === 0 ? "start" : i === ROWS.length - 1 ? "end" : "middle"}>{monthYear(ROWS[i].date)}</text>
            ))}
          </svg>
        )}
        <div className="sr-only"><table>
          <caption>Patrimônio por mês</caption>
          <thead><tr><th scope="col">Mês</th>{PARTS.map((p) => <th key={p.key} scope="col">{p.label}</th>)}<th scope="col">Total</th></tr></thead>
          <tbody>
            {ROWS.map((r) => (
              <tr key={monthYear(r.date)}><th scope="row">{monthYear(r.date)}</th>{PARTS.map((p) => <td key={p.key}>{money0(r[p.key])}</td>)}<td>{money(r.total)}</td></tr>
            ))}
          </tbody>
        </table></div>
      </div>
    </Frame>
  );
}
