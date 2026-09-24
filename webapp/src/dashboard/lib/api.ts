// Fachada tipada dos módulos .js de dados e cálculo.
import * as D from "./data.js";
import * as M from "./model.js";
import type { Category, Goal, Launch, Sim, Summary, Trajectory } from "./types";

export const TODAY: Date = D.TODAY;
export const MONTHS: string[] = D.MONTHS;
export const CATEGORIES = D.CATEGORIES as Category[];
export const GOALS = D.GOALS as Goal[];
export const CARD = D.CARD;
export const INVESTMENTS = D.INVESTMENTS;
// Do banco só sai o nome e o total: as posições nunca são listadas uma a uma.
export const BANK_CDB = D.BANK_CDB as { bank: string; via: string };
export const BANK_CDB_TOTAL: number = D.BANK_CDB_TOTAL;
export const BALANCE_TODAY: number = M.BALANCE_TODAY;
export const PACE = M.PACE as { perCat: Record<string, number>; std: number };
export const HORIZONS = M.HORIZONS as Record<"mes" | "30" | "90", string>;

export const summary = (key: string) => M.monthSummary(key) as unknown as Summary;
export const trajectory = (key: string, h: string, sim?: Sim | null) => M.trajectory(key, h, sim) as unknown as Trajectory;
export const scheduled = (from: Date, to: Date) => M.scheduled(from, to) as unknown as Launch[];
export const previousKey = (key: string): string | null => M.previousKey(key);
export const spentUntil = (key: string, day: number): number => M.spentUntil(key, day);
export const isCurrentMonth = (key: string): boolean => M.isCurrentMonth(key);
export const horizonEnd = (h: string): Date => M.horizonEnd(h);
export const addDays = (d: Date, n: number): Date => M.addDays(d, n);
export const sameDay = (a: Date, b: Date): boolean => M.sameDay(a, b);
export const monthlySaving = (sim: Sim): number => M.monthlyFromDaily(M.dailyDelta(sim));
export const goalEta = (g: Goal, extra = 0) => M.goalEta(g, extra) as { months: number; date: Date };
export const goalsTotal = (): number => M.goalsTotal();
export const caixinhasTotal = (): number => M.caixinhasTotal();
export const netWorth = () => M.netWorth() as { date: Date; conta: number; caixinhas: number; investimentos: number; total: number }[];
export const catById = (id: string | null) => CATEGORIES.find((c) => c.id === id);
export const keyDate = (key: string) => new Date(Number(key.slice(0, 4)), Number(key.slice(5, 7)) - 1, 1);
export const PLAN = D.PLAN as "essencial" | "plus" | "pro";
