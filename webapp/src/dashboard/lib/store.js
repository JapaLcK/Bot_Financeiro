// Estado único da página. Imutável: cada `set` troca o objeto, e o React relê
// pelo useSyncExternalStore (hook em ../useDash.ts).
import { DEMO, MESES } from "./v2";

/** @type {import("./types").DashState} */
let state = {
  month: "2026-09",
  mes: MESES[MESES.length - 1],
  horizon: "mes",
  sim: { cuts: {}, extra: 0, goal: "intercambio" },
  filter: { category: null, day: null, query: "", source: "todos" },
  highlight: null, // dia (yyyy-mm-dd) em foco, compartilhado entre gráfico e listas
  editing: false,
};

const listeners = new Set();

export const get = () => state;

/** O próximo estado depois de `patch`. Função pura: a store global e cada resposta da
 *  conversa do Piggy (estado próprio, parts/LiveAnswer.tsx) aplicam a mesma regra.
 *  @param {import("./types").DashState} prev
 *  @param {Partial<import("./types").DashState>} patch
 *  @returns {import("./types").DashState} */
export function apply(prev, patch) {
  const next = { ...prev, ...patch };
  // O dia filtrado é uma data do mês anterior: no mês novo ele esvaziaria a lista.
  // A origem fica: vale em qualquer mês enquanto o extrato está aberto (o Ledger a
  // zera ao desmontar).
  if (next.month !== prev.month) next.filter = { ...next.filter, day: null };
  return next;
}

/** @param {Partial<import("./types").DashState>} patch */
export function set(patch) {
  state = apply(state, patch);
  for (const fn of listeners) fn();
}

// O mês do seletor e do título. No protótipo é o próprio `month` sintético; com backend é o
// real (`mes`), e os blocos de exemplo seguem no último mês sintético, com o selo.
/** @param {import("./types").DashState} s */
export const mesDe = (s) => (DEMO ? s.month : s.mes);
/** @param {string} k */
export const escolherMes = (k) => set(DEMO ? { month: k } : { mes: k });

export const setFilter = (patch) => set({ filter: { ...state.filter, ...patch } });
export const setSim = (patch) => set({ sim: { ...state.sim, ...patch } });
export const setCut = (cat, value) => setSim({ cuts: { ...state.sim.cuts, [cat]: value } });
export const resetSim = () => setSim({ cuts: {}, extra: 0 });
/** @param {import("./types").DashState} [s] */
export const simActive = (s = state) => s.sim.extra > 0 || Object.values(s.sim.cuts).some((v) => v > 0);

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export const dayKey = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
