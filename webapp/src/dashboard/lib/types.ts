// Formas dos dados que os módulos .js produzem, para os componentes em TSX.
export interface Launch {
  id?: string;
  date: Date;
  kind: "income" | "expense" | "transfer";
  category: string | null;
  label: string;
  amount: number | null;
  source?: "whatsapp" | "openfinance" | "cartao";
  msg?: string | null;
  estimated?: boolean;
  invoice?: boolean;
  transfer?: boolean;
}

export interface Point {
  date: Date;
  value: number;
  real: boolean;
  events: Launch[];
  sim?: number;
  lo?: number;
  hi?: number;
}

export interface Trajectory { points: Point[]; end: Point; start: number }

export interface Summary {
  key: string;
  launches: Launch[];
  income: number;
  expense: number;
  saved: number;
  byCategory: Record<string, number>;
  daily: number[];
  opening: number;
  invoice: number;
}

export interface Category { id: string; label: string; icon: string; color: string; variable: boolean }
export interface Goal { id: string; label: string; icon: string; color: string; target: number; saved: number; monthly: number }
export interface Sim { cuts: Record<string, number>; extra: number; goal: string }
export interface DashState {
  month: string;
  horizon: "mes" | "30" | "90";
  sim: Sim;
  filter: { category: string | null; day: string | null; query: string; source: NonNullable<Launch["source"]> | "todos" };
  highlight: string | null;
  editing: boolean;
}
