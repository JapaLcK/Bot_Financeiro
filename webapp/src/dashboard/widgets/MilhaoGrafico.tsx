// O gráfico do Progresso ao milhão: o patrimônio projetado mês a mês e a meta tracejada.
// Molde do widgets/NetWorth.tsx (mesmas classes). Leitor de tela: rótulo resumido + um ponto por ano.
// Os dois eixos têm nome e valores (regra do dono: gráfico sem eixo nomeado não diz nada).
import { useState, type PointerEvent } from "react";
import { axisMoney, money0, monthYear } from "../lib/format.js";
import { mesAno } from "../lib/metas-textos";
import { useSize } from "../useDash";
import { hideTip, showTip } from "../parts/Tip";
import { axisWidth, niceTicks } from "./TrajectoryChart";

const PAD = 6;
const TOPO = 20; // o nome do eixo Y
const BAIXO = 38; // os valores e o nome do eixo X

// "AAAA-MM" do mês k depois de `inicio`.
export const mesDepois = (inicio: string, k: number) => {
  const [a, m] = inicio.split("-").map(Number);
  const t = a * 12 + (m - 1) + k;
  return `${Math.floor(t / 12)}-${String((t % 12) + 1).padStart(2, "0")}`;
};

// Até 6 marcas no eixo X, do início ao fim da curva: anos inteiros quando o prazo passa de 2 anos, senão meses ("out/26").
export function marcasX(inicio: string, n: number) {
  const emAnos = n >= 24;
  const passo = emAnos ? Math.ceil(n / 12 / 4) * 12 : Math.max(1, Math.ceil(n / 4));
  // O fim da curva sempre tem marca; a anterior só entra se ficar a mais de meio passo dele.
  const idx = [...Array.from({ length: Math.ceil((n - passo / 2) / passo) }, (_, k) => k * passo), n];
  const rotulo = (i: number) => {
    const [a, m] = mesDepois(inicio, i).split("-").map(Number);
    return emAnos ? String(a) : monthYear(new Date(a, m - 1, 1));
  };
  return { titulo: emAnos ? "Ano" : "Mês", marcas: idx.map((i) => ({ i, rotulo: rotulo(i) })) };
}

export function MilhaoGrafico({ pontos, meta, inicio, resumo }: { pontos: number[]; meta: number; inicio: string; resumo: string }) {
  const [box, { width, height }] = useSize<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const n = pontos.length - 1;
  const lo = Math.min(0, ...pontos), hi = Math.max(meta, ...pontos) * 1.05;
  const ticks = width > 0 ? niceTicks(lo, hi) : [];
  // Margem esquerda do tamanho do maior valor do eixo Y (10px de folga até o gráfico).
  const ml = Math.max(40, Math.ceil(Math.max(0, ...ticks.map((t) => axisWidth(axisMoney(t))))) + 10);
  const x = (i: number) => ml + (i / n) * (width - ml - PAD);
  const y = (v: number) => TOPO + (1 - (v - lo) / (hi - lo)) * (height - TOPO - BAIXO);
  const path = pontos.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
  const eixoX = marcasX(inicio, n);
  const onMove = (e: PointerEvent) => {
    const r = box.current!.getBoundingClientRect();
    const i = Math.max(0, Math.min(n, Math.round(((e.clientX - r.left - ml) / (width - ml - PAD)) * n)));
    setHover(i);
    showTip(e.clientX, e.clientY, { title: mesAno(mesDepois(inicio, i)), value: money0(pontos[i]) });
  };
  const anos = Array.from({ length: Math.floor(n / 12) + 1 }, (_, a) => a * 12);
  return (
    <div className="nw-chart mi-chart" ref={box} onPointerMove={onMove} onPointerLeave={() => { setHover(null); hideTip(); }}>
      {width > 0 && (
        <svg width={width} height={height} role="img" aria-label={resumo}>
          <text x={0} y={12} className="axis mi-eixo">Patrimônio</text>
          {ticks.map((t) => (
            <g key={t}>
              {t !== meta && <line x1={ml} x2={width - PAD} y1={y(t)} y2={y(t)} className={t === 0 ? "grid zero" : "grid"} />}
              <text x={ml - 10} y={y(t)} dy="0.32em" textAnchor="end" className="axis mi-y">{axisMoney(t)}</text>
            </g>
          ))}
          <line x1={ml} x2={width - PAD} y1={y(meta)} y2={y(meta)} className="grid mi-meta" strokeDasharray="4 5" />
          <text x={ml + 4} y={y(meta) + 13} className="axis">{`Meta ${axisMoney(meta)}`}</text>
          <path d={path} className="line real" />
          {hover != null && <line x1={x(hover)} x2={x(hover)} y1={TOPO} y2={height - BAIXO} className="cross-line" />}
          {hover != null && <circle cx={x(hover)} cy={y(pontos[hover])} r={4.5} className="cross-dot" />}
          <circle cx={x(0)} cy={y(pontos[0])} r={4} className="today-dot" />
          {eixoX.marcas.map(({ i, rotulo }) => (
            <text key={i} x={x(i)} y={height - BAIXO + 16} className="axis mi-x"
              textAnchor={i === 0 ? "start" : x(i) > width - PAD - 20 ? "end" : "middle"}>{rotulo}</text>
          ))}
          <text x={(ml + width - PAD) / 2} y={height - 4} textAnchor="middle" className="axis mi-eixo">{eixoX.titulo}</text>
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
