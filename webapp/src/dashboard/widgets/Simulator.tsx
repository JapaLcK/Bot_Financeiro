import NumberFlow from "@number-flow/react";
import { useMemo, useState } from "react";
import { CATEGORIES, GOALS, MONTHS, PACE, goalEta, monthlySaving, trajectory } from "../lib/api";
import { monthYear, money0, moneyBig, tone } from "../lib/format.js";
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
// Maior gasto fixo (R$/mês) que o simulador representa: acima disso os rótulos do gráfico
// não cabem e, perto de 1e308, a conta estoura para infinito. Não é regra de negócio.
const EXTRA_LIMIT = 1e12;

// Mensagem de erro do campo, ou null se o valor pode ir para a simulação.
function extraError(input: HTMLInputElement): string | null {
  const v = Number(input.value);
  if (input.validity.badInput || !Number.isFinite(v)) return "Digite um número válido";
  if (v < 0) return "Digite um valor positivo";
  if (v > EXTRA_LIMIT) return "Valor grande demais para simular";
  return null;
}

export function Simulator({ s, full = false }: { s: DashState; full?: boolean }) {
  const saving = monthlySaving(s.sim);
  const on = simActive(s);
  const goal = GOALS.find((g) => g.id === s.sim.goal) ?? GOALS[0];
  const before = goalEta(goal);
  const after = goalEta(goal, saving);
  const ninety = useMemo(() => trajectory(MONTHS[MONTHS.length - 1], "90", s.sim).end, [s.sim]);
  const earlier = before.months - after.months;
  // Texto em edição que não vai para a simulação: vazio (ainda digitando) ou inválido (com msg).
  // Fica no campo como o usuário deixou; a simulação mantém o último valor válido.
  const [draft, setDraft] = useState<{ text: string; msg: string | null } | null>(null);
  const bad = draft?.msg ?? null;

  return (
    <Frame id="simulador" title={full ? "Seus hábitos" : "E se…"} className="w-sim"
      aside={on && <button type="button" className="btn btn-quiet" onClick={() => { setDraft(null); resetSim(); }}>Zerar</button>}>
      <div className="presets" role="group" aria-label="Simulações prontas">
        {PRESETS.map((p) => {
          // Ligar aplica os valores do preset por cima dos cortes atuais; desligar zera só as alavancas dele
          // (um valor manual anterior nessas alavancas não volta: não guardamos histórico).
          const active = Object.entries(p.cuts).every(([k, v]) => s.sim.cuts[k] === v);
          return (
            <button key={p.label} type="button" className="chip" aria-pressed={active}
              onClick={() => setSim({ cuts: active ? Object.fromEntries(Object.entries(s.sim.cuts).filter(([k]) => !(k in p.cuts))) : { ...s.sim.cuts, ...p.cuts } })}>{p.label}</button>
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
          <input className="field num" type="number" inputMode="decimal" min={0} step={10} placeholder="R$ 0"
            value={draft ? draft.text : s.sim.extra || ""} aria-invalid={bad ? true : undefined} aria-describedby={bad ? "sim-extra-erro" : undefined}
            onChange={(e) => {
              const empty = e.target.value === "" && !e.target.validity.badInput;
              const msg = empty ? null : extraError(e.target);
              setDraft(empty || msg ? { text: e.target.value, msg } : null);
              if (!empty && !msg) setSim({ extra: Number(e.target.value) });
            }}
            onBlur={(e) => {
              // Vazio ao sair do campo: o usuário limpou de propósito.
              if (e.target.value === "" && !e.target.validity.badInput) { setDraft(null); setSim({ extra: 0 }); }
            }} />
        </label>
        {bad && <p id="sim-extra-erro" className="lever-err" role="status">{bad}</p>}
      </div>

      {!on ? (
        <div className="sim-out sim-empty" aria-live="polite">
          <p className="sim-prompt">Escolha um cenário acima ou arraste um controle: o gráfico do saldo e as metas mudam na hora.</p>
        </div>
      ) : (
      <div className="sim-out" aria-live="polite">
        <div className="sim-main">
          <span className="faint">Sobra a mais por mês</span>
          <NumberFlow className={`sim-value ${tone(saving)}`} value={Math.round(saving)} locales="pt-BR"
            format={{ ...BRL, signDisplay: "exceptZero", ...(Math.abs(saving) >= 1e6 && { notation: "compact", maximumFractionDigits: 1 }) }} />
        </div>
        <dl className="sim-facts">
          <div><dt>Em 12 meses</dt><dd className="num">{moneyBig(saving * 12)}</dd></div>
          <div><dt>Saldo em 90 dias</dt><dd className="num">{moneyBig(ninety.sim ?? ninety.value)}</dd></div>
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
