// O gráfico do Progresso ao milhão: o patrimônio projetado mês a mês e a meta tracejada.
// Molde do widgets/NetWorth.tsx (mesmas classes). Leitor de tela: rótulo resumido + um ponto por ano.
import { useState, type PointerEvent } from "react";
import { axisMoney, money0 } from "../lib/format.js";
import { mesAno } from "../lib/metas-textos";
import { useSize } from "../useDash";
import { hideTip, showTip } from "../parts/Tip";

const PAD = 6;
const BAIXO = 16;

// "AAAA-MM" do mês k depois de `inicio`.
export const mesDepois = (inicio: string, k: number) => {
  const [a, m] = inicio.split("-").map(Number);
  const t = a * 12 + (m - 1) + k;
  return `${Math.floor(t / 12)}-${String((t % 12) + 1).padStart(2, "0")}`;
};

export function MilhaoGrafico({ pontos, meta, inicio, resumo }: { pontos: number[]; meta: number; inicio: string; resumo: string }) {
  const [box, { width, height }] = useSize<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const n = pontos.length - 1;
  const lo = Math.min(0, ...pontos), hi = Math.max(meta, ...pontos) * 1.05;
  const x = (i: number) => PAD + (i / n) * (width - PAD * 2);
  const y = (v: number) => PAD + (1 - (v - lo) / (hi - lo)) * (height - PAD * 2 - BAIXO);
  const path = pontos.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
  const onMove = (e: PointerEvent) => {
    const r = box.current!.getBoundingClientRect();
    const i = Math.max(0, Math.min(n, Math.round(((e.clientX - r.left - PAD) / (width - PAD * 2)) * n)));
    setHover(i);
    showTip(e.clientX, e.clientY, { title: mesAno(mesDepois(inicio, i)), value: money0(pontos[i]) });
  };
  const anos = Array.from({ length: Math.floor(n / 12) + 1 }, (_, a) => a * 12);
  return (
    <div className="nw-chart mi-chart" ref={box} onPointerMove={onMove} onPointerLeave={() => { setHover(null); hideTip(); }}>
      {width > 0 && (
        <svg width={width} height={height} role="img" aria-label={resumo}>
          <line x1={PAD} x2={width - PAD} y1={y(meta)} y2={y(meta)} className="grid mi-meta" strokeDasharray="4 5" />
          <text x={PAD} y={y(meta) - 4} className="axis">{`Meta ${axisMoney(meta)}`}</text>
          <path d={path} className="line real" />
          {hover != null && <line x1={x(hover)} x2={x(hover)} y1={PAD} y2={height - BAIXO} className="cross-line" />}
          {hover != null && <circle cx={x(hover)} cy={y(pontos[hover])} r={4.5} className="cross-dot" />}
          <circle cx={x(0)} cy={y(pontos[0])} r={4} className="today-dot" />
          {[0, n].map((i) => (
            <text key={i} x={x(i)} y={height - 4} className="axis" textAnchor={i === 0 ? "start" : "end"}>{mesDepois(inicio, i).slice(0, 4)}</text>
          ))}
        </svg>
      )}
      <div className="sr-only"><table>
        <caption>Patrimônio projetado por ano</caption>
        <thead><tr><th scope="col">Ano</th><th scope="col">Patrimônio</th></tr></thead>
        <tbody>
          {[...anos, ...(n % 12 ? [n] : [])].map((i) => <tr key={i}><th scope="row">{mesAno(mesDepois(inicio, i))}</th><td>{money0(pontos[i])}</td></tr>)}
        </tbody>
      </table></div>
    </div>
  );
}
