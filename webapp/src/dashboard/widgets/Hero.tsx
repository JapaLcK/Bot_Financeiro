import NumberFlow from "@number-flow/react";
import { useMemo } from "react";
import { BALANCE_TODAY, HORIZONS, isCurrentMonth, trajectory } from "../lib/api";
import { longDate, money0, signed0, signedBig, tone } from "../lib/format.js";
import { set, simActive } from "../lib/store.js";
import type { DashState } from "../lib/types";
import { Frame } from "../parts/Frame";
import { Seg } from "../parts/Seg";
import { TrajectoryChart } from "./TrajectoryChart";

export const BRL = { style: "currency", currency: "BRL", maximumFractionDigits: 0 } as const;

export function Hero({ s }: { s: DashState }) {
  const current = isCurrentMonth(s.month);
  const simOn = current && simActive(s);
  const traj = useMemo(() => trajectory(s.month, s.horizon, simOn ? s.sim : null), [s.month, s.horizon, s.sim, simOn]);
  const end = traj.end;
  const change = current ? end.value - BALANCE_TODAY : end.value - traj.start;
  const simGain = simOn && end.sim != null ? end.sim - end.value : 0;

  return (
    <Frame
      id="hero"
      title={current ? "Saldo previsto" : "Saldo no fim do mês"}
      className="w-hero"
      aside={current
        ? <Seg label="Horizonte da previsão" value={s.horizon} onChange={(h) => set({ horizon: h })}
            options={(["mes", "30", "90"] as const).map((k) => ({ value: k, label: HORIZONS[k] }))} />
        : <span className="tag-demo">Mês fechado</span>}
    >
      <div className="hero-top">
        <div className="hero-figure">
          <NumberFlow className="hero-value" value={end.value} locales="pt-BR" format={BRL} />
          <p className="hero-sub">
            {current ? <>em {longDate(end.date)} <span className="faint">· no ritmo dos últimos 60 dias, sem contar freelas</span></> : longDate(end.date)}
          </p>
        </div>
        <dl className="hero-facts">
          <div>
            <dt>{current ? "Até lá" : "No mês"}</dt>
            <dd className={tone(change) === "gain" ? "gain" : ""}>{signed0(change)}</dd>
          </div>
          {current && end.lo != null && end.hi != null && (
            <div>
              <dt>Faixa provável</dt>
              <dd>{money0(end.lo)} – {money0(end.hi)}</dd>
            </div>
          )}
          {simOn && (
            <div className="hero-sim">
              <dt>Com a simulação</dt>
              <dd className={tone(simGain)}>{signedBig(simGain)}</dd>
            </div>
          )}
        </dl>
      </div>
      <ul className="legend" aria-label="Legenda do gráfico">
        <li><span className="key real" />Realizado</li>
        {current && <li><span className="key forecast" />Previsão</li>}
        {current && <li><span className="key band" />Faixa provável</li>}
        {simOn && <li><span className={`key sim ${tone(simGain)}`} />Simulação</li>}
      </ul>
      <TrajectoryChart traj={traj} simOn={simOn} highlight={s.highlight} drawKey={s.month} />
    </Frame>
  );
}
