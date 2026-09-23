import NumberFlow from "@number-flow/react";
import { useMemo } from "react";
import { CATEGORIES, GOALS, MONTHS, PACE, goalEta, monthlySaving, trajectory } from "../lib/api";
import { monthYear, money0 } from "../lib/format.js";
import { resetSim, set, setCut, setSim, simActive } from "../lib/store.js";
import type { DashState } from "../lib/types";
import { Frame } from "../parts/Frame";
import { go } from "../router";
import { BRL } from "./Hero";

const LEVERS = ["delivery", "lazer", "compras", "mercado", "transporte"];
export const PRESETS: { label: string; cuts: Record<string, number>; extra?: number }[] = [
  { label: "Delivery pela metade", cuts: { delivery: 0.5 } },
  { label: "Um rolê a menos por semana", cuts: { lazer: 0.25 } },
  { label: "Sem compra por impulso", cuts: { compras: 0.6 } },
];
const perMonth = (daily: number) => (daily * 365) / 12;
const EXTRA_MAX = 50000; // teto do "Novo gasto fixo" (R$/mês): valor colado sem teto estourava os totais

export function Simulator({ s, full = false }: { s: DashState; full?: boolean }) {
  const saving = monthlySaving(s.sim);
  const on = simActive(s);
  const goal = GOALS.find((g) => g.id === s.sim.goal) ?? GOALS[0];
  const before = goalEta(goal);
  const after = goalEta(goal, saving);
  const ninety = useMemo(() => trajectory(MONTHS[MONTHS.length - 1], "90", s.sim).end, [s.sim]);
  const earlier = before.months - after.months;

  return (
    <Frame id="simulador" title={full ? "Seus hábitos" : "E se…"} className="w-sim"
      aside={on && <button type="button" className="btn btn-quiet" onClick={resetSim}>Zerar</button>}>
      <div className="presets" role="group" aria-label="Simulações prontas">
        {PRESETS.map((p) => {
          const active = Object.entries(p.cuts).every(([k, v]) => s.sim.cuts[k] === v);
          return (
            <button key={p.label} type="button" className="chip" aria-pressed={active}
              onClick={() => setSim({ cuts: active ? {} : { ...p.cuts } })}>{p.label}</button>
          );
        })}
      </div>

      <div className="levers">
        {LEVERS.map((id) => {
          const c = CATEGORIES.find((x) => x.id === id)!;
          const cut = s.sim.cuts[id] ?? 0;
          const monthly = perMonth(PACE.perCat[id] ?? 0);
          return (
            <label key={id} className="lever">
              <span className="lever-name"><i className={`ph ${c.icon}`} style={{ color: c.color }} aria-hidden="true" />{c.label}</span>
              <span className="lever-base faint num">{money0(monthly)}/mês</span>
              <input className="range" type="range" min={0} max={100} step={5} value={Math.round(cut * 100)}
                style={{ ["--p" as string]: `${cut * 100}%` }}
                aria-valuetext={`cortar ${Math.round(cut * 100)}%, ${money0(monthly * cut)} por mês`}
                onChange={(e) => setCut(id, Number(e.target.value) / 100)} />
              <span className={`lever-cut num ${cut > 0 ? "gain" : "faint"}`}>{cut > 0 ? `−${Math.round(cut * 100)}%` : "0%"}</span>
            </label>
          );
        })}
        <label className="lever lever-extra">
          <span className="lever-name"><i className="ph ph-plus" aria-hidden="true" />Novo gasto fixo</span>
          <span className="lever-base faint">por mês</span>
          <input className="field num" type="number" inputMode="decimal" min={0} max={EXTRA_MAX} step={10} value={s.sim.extra || ""} placeholder="R$ 0"
            onChange={(e) => setSim({ extra: Math.min(EXTRA_MAX, Math.max(0, Number(e.target.value) || 0)) })} />
        </label>
      </div>

      {!on ? (
        <div className="sim-out sim-empty" aria-live="polite">
          <p className="sim-prompt">Escolha um cenário acima ou arraste um controle: o gráfico do saldo e as metas mudam na hora.</p>
        </div>
      ) : (
      <div className="sim-out" aria-live="polite">
        <div className="sim-main">
          <span className="faint">Sobra a mais por mês</span>
          <NumberFlow className={`sim-value ${saving > 0 ? "gain" : saving < 0 ? "warn" : ""}`} value={Math.round(saving)} locales="pt-BR"
            format={{ ...BRL, signDisplay: "exceptZero" }} />
        </div>
        <dl className="sim-facts">
          <div><dt>Em 12 meses</dt><dd className="num">{money0(saving * 12)}</dd></div>
          <div><dt>Saldo em 90 dias</dt><dd className="num">{money0(ninety.sim ?? ninety.value)}</dd></div>
          <div className="sim-goal">
            <dt>
              <select className="field" aria-label="Meta que recebe a economia" value={goal.id} onChange={(e) => setSim({ goal: e.target.value })}>
                {GOALS.map((g) => <option key={g.id} value={g.id}>{g.label}</option>)}
              </select>
            </dt>
            <dd className="num">
              {earlier > 0 ? <><s className="faint">{monthYear(before.date)}</s> <span className="gain">{monthYear(after.date)}</span></> : monthYear(before.date)}
            </dd>
          </div>
        </dl>
        {!full && (
          <button type="button" className="btn btn-ghost sim-see"
            onClick={() => { set({ horizon: "90", month: MONTHS[MONTHS.length - 1] }); go("/simulador"); }}>
            <i className="ph ph-chart-line-up" aria-hidden="true" />Ver no gráfico
          </button>
        )}
      </div>
      )}
    </Frame>
  );
}
