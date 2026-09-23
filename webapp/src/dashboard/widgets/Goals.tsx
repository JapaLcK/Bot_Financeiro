import { GOALS, goalEta, monthlySaving } from "../lib/api";
import { monthYear, money0 } from "../lib/format.js";
import { simActive } from "../lib/store.js";
import type { DashState } from "../lib/types";
import { Frame } from "../parts/Frame";

export function Goals({ s, detailed = false, title = "Metas e caixinhas" }: { s: DashState; detailed?: boolean; title?: string }) {
  const saving = simActive(s) ? monthlySaving(s.sim) : 0;
  return (
    <Frame id="metas" title={title}>
      <ul className="goals">
        {GOALS.map((g) => {
          const eta = goalEta(g);
          const boosted = g.id === s.sim.goal && saving > 0 ? goalEta(g, saving) : null;
          const p = g.saved / g.target;
          return (
            <li key={g.id} className="goal">
              <i className={`ph ${g.icon} goal-icon`} style={{ color: g.color }} aria-hidden="true" />
              <div className="goal-main">
                <p className="goal-name">{g.label}</p>
                {detailed && <p className="goal-sub faint num">{money0(g.monthly)}/mês · faltam {money0(Math.max(0, g.target - g.saved))} · {Math.round(p * 100)}%</p>}
                <div className="meter" role="meter" aria-valuemin={0} aria-valuemax={g.target} aria-valuenow={g.saved} aria-label={`${g.label}: ${Math.round(p * 100)}%`}>
                  <span style={{ transform: `scaleX(${p})`, background: g.color }} />
                </div>
              </div>
              <p className="goal-num num"><b>{money0(g.saved)}</b><span className="faint"> de {money0(g.target)}</span></p>
              <p className="goal-eta num">
                {boosted && boosted.months < eta.months
                  ? <><s className="faint">{monthYear(eta.date)}</s> <span className="gain">{monthYear(boosted.date)}</span></>
                  : <span className="faint">{eta.months === 0 ? "pronta" : monthYear(eta.date)}</span>}
              </p>
            </li>
          );
        })}
      </ul>
    </Frame>
  );
}
