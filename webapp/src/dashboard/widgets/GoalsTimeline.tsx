import { GOALS, TODAY, goalEta, monthlySaving } from "../lib/api";
import { monthYear, tint } from "../lib/format.js";
import { simActive } from "../lib/store.js";
import type { DashState } from "../lib/types";
import { Frame } from "../parts/Frame";

// Linha do tempo: de hoje até o mês em que cada meta chega, num eixo comum.
// Com uma simulação ativa, a meta escolhida mostra em verde quanto ela adianta.
export function GoalsTimeline({ s }: { s: DashState }) {
  const saving = simActive(s) ? monthlySaving(s.sim) : 0;
  const rows = GOALS.map((g) => {
    const eta = goalEta(g);
    const boosted = g.id === s.sim.goal && saving > 0 ? goalEta(g, saving) : null;
    return { g, eta, boosted: boosted && boosted.months < eta.months ? boosted : null };
  });
  const span = Math.max(12, ...rows.map((r) => r.eta.months));
  const years = Array.from({ length: Math.floor(span / 12) + 1 }, (_, i) => i * 12).filter((m) => m <= span);
  const pct = (m: number) => `${(m / span) * 100}%`;

  return (
    <Frame id="linha-metas" title="Quando cada meta chega">
      <div className="tl" role="list">
        {rows.map(({ g, eta, boosted }) => (
          <div className="tl-row" role="listitem" key={g.id}
            aria-label={`${g.label}: chega em ${monthYear(eta.date)}${boosted ? `, com a simulação em ${monthYear(boosted.date)}` : ""}`}>
            <span className="tl-name"><i className={`ph ${g.icon}`} style={{ color: g.color }} aria-hidden="true" />{g.label}</span>
            <span className="tl-track" aria-hidden="true">
              <span className="tl-bar" style={{ transform: `scaleX(${eta.months / span})`, background: tint(g.color, 0.55) }} />
              {boosted && <span className="tl-bar gain" style={{ transform: `scaleX(${boosted.months / span})` }} />}
              <span className="tl-end num" style={{ left: pct(eta.months) }}>
                {boosted ? <><s className="faint">{monthYear(eta.date)}</s> <span className="gain">{monthYear(boosted.date)}</span></> : monthYear(eta.date)}
              </span>
            </span>
          </div>
        ))}
        <div className="tl-axis" aria-hidden="true">
          <span />
          <span className="tl-ticks">
            {years.map((m) => (
              <span key={m} style={{ left: pct(m) }}>{m === 0 ? "hoje" : TODAY.getFullYear() + m / 12}</span>
            ))}
          </span>
        </div>
      </div>
    </Frame>
  );
}
