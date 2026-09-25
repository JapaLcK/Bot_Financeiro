import type { ReactNode } from "react";
import { CARD, CATEGORIES, GOALS, MONTHS, TODAY, catById, goalEta, incomeHistory, installmentsAhead, keyDate, previousKey, reserveMonths, spentUntil, summary, trajectory, yieldVsCdi } from "./api";
import { money0, monthName, monthYear, signed0 } from "./format.js";
import { get } from "./store.js";
import type { DashState, Launch } from "./types";
import { Bills } from "../widgets/Bills";
import { Categories } from "../widgets/Categories";
import { CategoryDetail } from "../widgets/CategoryDetail";
import { Goals } from "../widgets/Goals";
import { Hero } from "../widgets/Hero";
import { Income } from "../widgets/Income";
import { Installments } from "../widgets/Installments";
import { Invoice } from "../widgets/Invoice";
import { LaunchList } from "../widgets/LaunchList";
import { Wealth } from "../widgets/Wealth";
import { Yield } from "../widgets/Yield";

// Respostas prontas da conversa do Piggy no protótipo: 8 assuntos, cada um com texto
// calculado dos dados, os blocos do dashboard e sugestões de próxima pergunta. Em
// produção, quem escolhe o assunto é a IA (as tools de /ai/chat), não esta tabela.
export type TopicId = "categorias" | "categoria" | "lancamentos" | "fatura" | "investimentos" | "metas" | "saldo" | "renda";
export interface Follow { label: string; topic: TopicId; cat?: string }
export interface Answer { text: ReactNode; blocks: ReactNode[]; follow: Follow[] }

const NOW = MONTHS[MONTHS.length - 1];
const DAY = TODAY.getDate();
const MONTH = monthName(keyDate(NOW));
const pct = (n: number) => `${Math.round(n * 100)}%`;
const topVariable = (byCat: Record<string, number>) => CATEGORIES.filter((c) => c.variable).sort((a, b) => byCat[b.id] - byCat[a.id])[0];
const spentIn = (key: string, cat: string) => (summary(key).launches as Launch[]).filter((l) => l.kind === "expense" && l.category === cat && l.date.getDate() <= DAY).reduce((a, l) => a + (l.amount ?? 0), 0);

// "Foto" do estado para os blocos da resposta: o mês atual e, se houver, a categoria.
const snap = (cat: string | null = null): DashState => ({ ...get(), month: NOW, filter: { category: cat, day: null, query: "", source: "todos" }, highlight: null, editing: false });

export function answer(topic: TopicId, cat: string | null = null): Answer {
  const m = summary(NOW);
  const prev = previousKey(NOW)!;
  const top = topVariable(m.byCategory);
  switch (topic) {
    case "categorias": {
      const then = spentUntil(prev, DAY);
      return {
        text: <>Em {MONTH}, até o dia {DAY}, saíram <b>{money0(m.expense)}</b>, {m.expense <= then ? `${money0(then - m.expense)} a menos` : `${money0(m.expense - then)} a mais`} que em {monthName(keyDate(prev))} no mesmo ponto. O que mais pesa é <b>{top.label}</b> ({money0(m.byCategory[top.id])}).</>,
        blocks: [<Categories s={snap()} />],
        follow: [{ label: `Me mostra o detalhe de ${top.label.toLowerCase()}`, topic: "categoria", cat: top.id }, { label: "Quais foram meus maiores gastos?", topic: "lancamentos" }, { label: "Vai sobrar até o fim do mês?", topic: "saldo" }],
      };
    }
    case "categoria": {
      const c = catById(cat) ?? top;
      const now = spentIn(NOW, c.id), then = spentIn(prev, c.id);
      const diff = then > 0 ? (now - then) / then : 0;
      return {
        text: <><b>{c.label}</b>: {money0(now)} em {MONTH} até o dia {DAY}{then > 0 && <>, {diff >= 0 ? `${pct(diff)} a mais` : `${pct(-diff)} a menos`} que em {monthName(keyDate(prev))}</>}. Cortar pela metade daria uns <b>{money0((now / DAY) * 30.4 / 2)} por mês</b> a mais.</>,
        blocks: [<CategoryDetail s={snap(c.id)} />],
        follow: [{ label: "Me mostra todas as categorias", topic: "categorias" }, { label: "Quanto falta pras minhas metas?", topic: "metas" }, { label: "Vai sobrar até o fim do mês?", topic: "saldo" }],
      };
    }
    case "lancamentos": {
      const from = new Date(TODAY.getFullYear(), TODAY.getMonth(), DAY - 6);
      const week = (m.launches as Launch[]).filter((l) => l.kind === "expense" && l.date >= from).sort((a, b) => (b.amount ?? 0) - (a.amount ?? 0));
      const total = week.reduce((a, l) => a + (l.amount ?? 0), 0);
      return {
        text: <>Nos últimos 7 dias saíram <b>{money0(total)}</b> em {week.length} gastos. O maior foi <b>{week[0].label}</b> ({money0(week[0].amount ?? 0)}).</>,
        blocks: [<LaunchList title="Maiores gastos da semana" launches={week.slice(0, 8)} />],
        follow: [{ label: "Pra onde vai meu dinheiro?", topic: "categorias" }, { label: `Me mostra o detalhe de ${top.label.toLowerCase()}`, topic: "categoria", cat: top.id }, { label: "E a minha fatura?", topic: "fatura" }],
      };
    }
    case "fatura": {
      const p = installmentsAhead(6);
      return {
        text: <>A fatura aberta está em <b>{money0(m.invoice)}</b> e vence dia {CARD.dueDay} de {monthName(new Date(TODAY.getFullYear(), TODAY.getMonth() + 1, 1))}. Da próxima, <b>{money0(p.months[0].value)}</b> já são parcelas; a última termina em {monthName(p.last)} de {p.last.getFullYear()}.</>,
        blocks: [<Invoice s={snap()} />, <Installments />],
        follow: [{ label: "Vai sobrar até o fim do mês?", topic: "saldo" }, { label: "Quais foram meus maiores gastos?", topic: "lancamentos" }, { label: "Pra onde vai meu dinheiro?", topic: "categorias" }],
      };
    }
    case "investimentos": {
      const y = yieldVsCdi();
      return {
        text: <>Sua carteira rendeu <b>{signed0(y.month.value)}</b> em {MONTH} ({pct(y.month.ofCdi)} do CDI) e <b>{signed0(y.year.value)}</b> em 12 meses ({pct(y.year.ofCdi)} do CDI).</>,
        blocks: [<Yield />, <Wealth />],
        follow: [{ label: "Quanto falta pras minhas metas?", topic: "metas" }, { label: "Quanto sobra pra investir?", topic: "saldo" }, { label: "Como anda a minha renda?", topic: "renda" }],
      };
    }
    case "metas": {
      const next = GOALS.map((g) => ({ g, eta: goalEta(g) })).sort((a, b) => a.eta.months - b.eta.months)[0];
      return {
        text: <>A meta mais perto é <b>{next.g.label}</b>: faltam {money0(next.g.target - next.g.saved)}, e no ritmo de hoje ela chega em {monthYear(next.eta.date)}. Sua reserva cobre {reserveMonths().toLocaleString("pt-BR", { maximumFractionDigits: 1 })} meses de contas fixas.</>,
        blocks: [<Goals s={snap()} />],
        follow: [{ label: "Onde dá pra cortar pra chegar antes?", topic: "categorias" }, { label: "Como estão meus investimentos?", topic: "investimentos" }, { label: "Vai sobrar até o fim do mês?", topic: "saldo" }],
      };
    }
    case "saldo": {
      const end = trajectory(NOW, "mes", null).end;
      return {
        text: <>No ritmo dos últimos 60 dias, você fecha {MONTH} com cerca de <b>{money0(end.value)}</b> na conta, sem contar freelas. Os compromissos até lá estão logo abaixo.</>,
        blocks: [<Hero s={{ ...snap(), horizon: "mes" }} />, <Bills s={snap()} />],
        follow: [{ label: "Pra onde vai meu dinheiro?", topic: "categorias" }, { label: "E a minha fatura?", topic: "fatura" }, { label: "Quanto falta pras minhas metas?", topic: "metas" }],
      };
    }
    case "renda": {
      const rows = incomeHistory();
      const vals = rows.map((r) => r.value);
      return {
        text: <>Nos últimos 6 meses sua renda foi de <b>{money0(Math.min(...vals))}</b> a <b>{money0(Math.max(...vals))}</b>; a média é {money0(vals.reduce((a, b) => a + b, 0) / vals.length)}.</>,
        blocks: [<Income />],
        follow: [{ label: "Quanto falta pras minhas metas?", topic: "metas" }, { label: "Vai sobrar até o fim do mês?", topic: "saldo" }, { label: "Como estão meus investimentos?", topic: "investimentos" }],
      };
    }
  }
}

// Texto livre no protótipo: sem IA, o Piggy oferece os assuntos que ele sabe mostrar.
export const DEMO: Answer = {
  text: <>Aqui no protótipo eu respondo pelos atalhos. No app de verdade eu entendo qualquer pergunta sobre o seu dinheiro. Quer ver um destes?</>,
  blocks: [],
  follow: [{ label: "Pra onde vai meu dinheiro?", topic: "categorias" }, { label: "E a minha fatura?", topic: "fatura" }, { label: "Como estão meus investimentos?", topic: "investimentos" }, { label: "Quanto falta pras minhas metas?", topic: "metas" }],
};
