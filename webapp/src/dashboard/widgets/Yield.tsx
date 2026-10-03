import { TODAY, yieldVsCdi } from "../lib/api";
import { monthName, signed0, tone } from "../lib/format.js";
import { Frame } from "../parts/Frame";

const Y = yieldVsCdi();
const rate = (n: number) => `${(n * 100).toLocaleString("pt-BR", { maximumFractionDigits: 2 })}%`;

// A carteira de investimentos contra o CDI, no mês e em 12 meses. Sem gráfico por
// ativo: isso já está em "Onde está o dinheiro".
export function Yield() {
  const periods = [
    { id: "mes", title: `Em ${monthName(TODAY)}`, ...Y.month },
    { id: "ano", title: "Em 12 meses", ...Y.year },
  ];
  return (
    <Frame id="rendimento" title="Rendimento × CDI">
      <div className="yield">
        {periods.map((p) => (
          <section key={p.id} aria-label={p.title}>
            <h3 className="detail-h">{p.title}</h3>
            <p className={`stat-value num ${tone(p.value)}`}>{signed0(p.value)}</p>
            <p className="yield-cdi"><b className="num">{Math.round(p.ofCdi * 100)}% do CDI</b></p>
            <div className="meter" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.max(0, Math.min(100, Math.round(p.ofCdi * 100)))} aria-valuetext={`${Math.round(p.ofCdi * 100)}% do CDI`} aria-label={`${p.title}, percentual do CDI`}>
              <span style={{ transform: `scaleX(${Math.max(0, Math.min(1, p.ofCdi))})` }} />
            </div>
            <p className="faint num yield-rates">carteira {rate(p.rate)} · CDI {rate(p.cdi)}</p>
          </section>
        ))}
      </div>
    </Frame>
  );
}
