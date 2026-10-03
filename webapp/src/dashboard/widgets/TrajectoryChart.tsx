import { useEffect, useId, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import type { Point, Trajectory } from "../lib/types";
import { axisMoney, dayMonth, longDate, money, moneyBig, tone } from "../lib/format.js";
import { dayKey } from "../lib/store.js";
import { useSize } from "../useDash";
import { hideTip, showTip, type TipRow } from "../parts/Tip";

const M = { t: 12, r: 10, b: 28 };

// Largura de um rótulo do eixo Y na fonte do .axis (11px, a --font da página); um canvas só.
// O .axis usa algarismos tabulares e o canvas não: medir tudo como "0" chega à largura tabular.
let axisCtx: CanvasRenderingContext2D | null = null;
function axisWidth(s: string) {
  if (!axisCtx) {
    axisCtx = document.createElement("canvas").getContext("2d")!;
    axisCtx.font = `11px ${getComputedStyle(document.documentElement).getPropertyValue("--font")}`;
  }
  return axisCtx.measureText(s.replace(/\d/g, "0")).width;
}

function niceTicks(lo: number, hi: number, count = 4) {
  const raw = (hi - lo) / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const out: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) out.push(Math.round(v));
  return out;
}

const line = (pts: [number, number][]) => pts.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join("");

function tipFor(p: Point, simOn: boolean, simTone: string) {
  const rows: TipRow[] = [];
  if (!p.real && p.lo != null && p.hi != null) rows.push({ label: "Faixa provável", value: `${money(p.lo)} – ${money(p.hi)}` });
  if (simOn && p.sim != null) rows.push({ label: "Com a simulação", value: moneyBig(p.sim, money), color: `var(--${simTone || "ink-2"})` });
  for (const e of p.events) {
    const v = e.amount == null ? "a definir" : `${e.kind === "income" ? "+" : "−"}${money(e.amount)}`;
    rows.push({ label: e.label, value: v, color: e.kind === "income" ? "var(--gain)" : e.invoice ? "var(--warn)" : "var(--ink-3)" });
  }
  return { title: `${longDate(p.date)} · ${p.real ? "saldo" : "previsto"}`, value: money(p.value), rows };
}

export function TrajectoryChart({ traj, simOn, highlight, drawKey }: {
  traj: Trajectory;
  simOn: boolean;
  highlight: string | null;
  drawKey: string;
}) {
  const [box, { width, height }] = useSize<HTMLDivElement>();
  const clip = useRef<SVGRectElement>(null);
  const [hover, setHover] = useState<number | null>(null);
  // Único por instância: a conversa do Piggy pode mostrar o mesmo gráfico duas vezes.
  const clipId = `clip-${drawKey}-${useId().replace(/[^a-z0-9]/gi, "")}`;
  const pts = traj.points;
  const n = pts.length;
  const ih = Math.max(1, height - M.t - M.b);

  // Desenha a linha da esquerda para a direita uma vez por mês exibido.
  useEffect(() => {
    if (!width || !clip.current || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    clip.current.animate([{ transform: "scaleX(0)" }, { transform: "scaleX(1)" }], { duration: 900, easing: "cubic-bezier(0.23, 1, 0.32, 1)" });
  }, [drawKey, width > 0]);

  const vals = pts.flatMap((p) => [p.value, p.lo ?? p.value, p.hi ?? p.value, simOn && p.sim != null ? p.sim : p.value]);
  let lo = Math.min(...vals), hi = Math.max(...vals);
  const pad = (hi - lo) * 0.12 || 100;
  lo -= pad; hi += pad;
  const ticks = niceTicks(lo, hi);
  // Margem esquerda do tamanho do maior rótulo (10px de folga até a linha, 4 de respiro).
  const ml = Math.max(56, Math.ceil(Math.max(...ticks.map((t) => axisWidth(axisMoney(t))))) + 14);
  const iw = Math.max(1, width - ml - M.r);
  const x = (i: number) => ml + (n === 1 ? 0 : (i / (n - 1)) * iw);
  const y = (v: number) => M.t + (1 - (v - lo) / (hi - lo)) * ih;

  const realN = pts.filter((p) => p.real).length;
  const today = realN - 1;
  const future = pts.slice(today);
  const realPath = line(pts.slice(0, realN).map((p, i) => [x(i), y(p.value)]));
  const realArea = `${realPath}L${x(today).toFixed(1)},${M.t + ih}L${x(0).toFixed(1)},${M.t + ih}Z`;
  const fcPath = future.length > 1 ? line(future.map((p, i) => [x(today + i), y(p.value)])) : "";
  const band = future.length > 1
    ? line([...future.map((p, i) => [x(today + i), y(p.hi ?? p.value)] as [number, number]), ...future.map((p, i) => [x(today + i), y(p.lo ?? p.value)] as [number, number]).reverse()]) + "Z"
    : "";
  const simPts = future.map((p, i) => [x(today + i), y(p.sim ?? p.value)] as [number, number]);
  const simPath = simOn && future.length > 1 ? line(simPts) : "";
  // Verde se a simulação termina acima da previsão, alerta se abaixo, neutra no empate (a regra dos números).
  const simTone = traj.end.sim != null ? tone(traj.end.sim - traj.end.value) : "";
  const gainArea = simPath && simTone ? line([...future.map((p, i) => [x(today + i), y(p.value)] as [number, number]), ...[...simPts].reverse()]) + "Z" : "";

  const every = Math.max(1, Math.ceil(n / Math.max(2, Math.floor(iw / 76)) / 7) * 7);
  const xTicks = pts.map((p, i) => ({ p, i })).filter(({ i }) => i % every === 0);
  const hiIndex = hover ?? (highlight ? pts.findIndex((p) => dayKey(p.date) === highlight) : -1);
  const cur = hiIndex >= 0 ? pts[hiIndex] : null;

  const pick = (clientX: number) => {
    const r = box.current!.getBoundingClientRect();
    return Math.max(0, Math.min(n - 1, Math.round(((clientX - r.left - ml) / iw) * (n - 1))));
  };
  const onMove = (e: PointerEvent) => {
    const i = pick(e.clientX);
    setHover(i);
    showTip(e.clientX, e.clientY, tipFor(pts[i], simOn, simTone));
  };
  const leave = () => { setHover(null); hideTip(); };
  const onKey = (e: KeyboardEvent) => {
    const step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (!step) return;
    e.preventDefault();
    const i = Math.max(0, Math.min(n - 1, (hover ?? today) + step));
    setHover(i);
    const r = box.current!.getBoundingClientRect();
    showTip(r.left + x(i), r.top + y(pts[i].value), tipFor(pts[i], simOn, simTone));
  };

  const last = pts[n - 1];
  const label = `Saldo dia a dia de ${dayMonth(pts[0].date)} a ${dayMonth(last.date)}. Hoje ${money(pts[today].value)}; ${last.real ? "fim" : "previsto"} ${money(last.value)}.`;

  return (
    <div className="chart" ref={box}>
      {width > 0 && (
        <svg width={width} height={height} role="img" aria-label={label} tabIndex={0}
             onPointerMove={onMove} onPointerLeave={leave} onBlur={leave} onKeyDown={onKey}>
          <defs>
            <clipPath id={clipId}><rect ref={clip} x={0} y={0} width={width} height={height} className="clip-rect" /></clipPath>
            <linearGradient id={`${clipId}-wash`} x1="0" x2="0" y1="0" y2="1">
              <stop offset="0" stopColor="#ff2d8e" stopOpacity="0.16" />
              <stop offset="1" stopColor="#ff2d8e" stopOpacity="0" />
            </linearGradient>
          </defs>
          {ticks.map((t) => (
            <g key={t}>
              <line x1={ml} x2={width - M.r} y1={y(t)} y2={y(t)} className={t === 0 ? "grid zero" : "grid"} />
              <text x={ml - 10} y={y(t)} dy="0.32em" textAnchor="end" className="axis">{axisMoney(t)}</text>
            </g>
          ))}
          {xTicks.map(({ p, i }) => (
            <text key={i} x={x(i)} y={height - 8} textAnchor={i === 0 ? "start" : "middle"} className="axis">{dayMonth(p.date)}</text>
          ))}
          <g clipPath={`url(#${clipId})`}>
            <path d={realArea} fill={`url(#${clipId}-wash)`} />
            {band && <path d={band} className="band" />}
            {gainArea && <path d={gainArea} className={`gain-area ${simTone}`} />}
            {fcPath && <path d={fcPath} className="line forecast" />}
            {simPath && <path d={simPath} className={`line sim ${simTone}`} />}
            <path d={realPath} className="line real" />
            {pts.map((p, i) => p.events.length > 0 && (
              <circle key={i} cx={x(i)} cy={y(p.value)} r={3.5}
                className={`mark ${p.events.some((e) => e.kind === "income") ? "in" : p.events.some((e) => e.invoice) ? "inv" : ""}`} />
            ))}
          </g>
          {!last.real && (
            <g>
              <line x1={x(today)} x2={x(today)} y1={M.t} y2={M.t + ih} className="today-line" />
              <circle cx={x(today)} cy={y(pts[today].value)} r={4} className="today-dot" />
              <circle cx={x(today)} cy={y(pts[today].value)} r={4} className="today-pulse" />
            </g>
          )}
          {cur && (
            <g className="cross">
              <line x1={x(hiIndex)} x2={x(hiIndex)} y1={M.t} y2={M.t + ih} />
              <circle cx={x(hiIndex)} cy={y(cur.value)} r={4.5} />
              {simOn && cur.sim != null && !cur.real && <circle cx={x(hiIndex)} cy={y(cur.sim)} r={4.5} className={`sim ${simTone}`} />}
            </g>
          )}
        </svg>
      )}
      <div className="sr-only"><table>
        <caption>Saldo por semana</caption>
        <tbody>
          {pts.filter((_, i) => i % 7 === 0 || i === n - 1).map((p) => (
            <tr key={dayKey(p.date)}><th scope="row">{dayMonth(p.date)}</th><td>{money(p.value)}{p.real ? "" : " (previsto)"}</td></tr>
          ))}
        </tbody>
      </table></div>
    </div>
  );
}
