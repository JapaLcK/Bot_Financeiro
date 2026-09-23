// Estado único da página. Imutável: cada `set` troca o objeto, e o React relê
// pelo useSyncExternalStore (hook em ../useDash.ts).
/** @type {import("./types").DashState} */
let state = {
  month: "2026-09",
  horizon: "mes",
  sim: { cuts: {}, extra: 0, goal: "intercambio" },
  filter: { category: null, day: null, query: "" },
  highlight: null, // dia (yyyy-mm-dd) em foco, compartilhado entre gráfico e listas
  editing: false,
};

const listeners = new Set();

export const get = () => state;

/** @param {Partial<import("./types").DashState>} patch */
export function set(patch) {
  state = { ...state, ...patch };
  for (const fn of listeners) fn();
}

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
