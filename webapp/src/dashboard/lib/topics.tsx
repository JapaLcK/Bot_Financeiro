import type { ReactNode } from "react";
import { CARD, CATEGORIES, GOALS, MONTHS, TODAY, catById, fixedMonthly, goalEta, incomeHistory, installmentsAhead, keyDate, previousKey, reserveMonths, spentUntil, summary, trajectory, yieldVsCdi } from "./api";
import { RECURRING } from "./data.js";
import { money, money0, monthName, monthYear, signed0 } from "./format.js";
import { get } from "./store.js";
import type { DashState, Launch } from "./types";
import { Bills } from "../widgets/Bills";
import { Calendar } from "../widgets/Calendar";
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

function byTopic(topic: TopicId, cat: string | null): Answer {
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

// A categoria variável que mais cresceu contra o mês anterior, no mesmo ponto do mês.
function grower() {
  const prev = previousKey(NOW)!;
  return CATEGORIES.filter((c) => c.variable)
    .map((c) => ({ c, now: spentIn(NOW, c.id), then: spentIn(prev, c.id) }))
    .filter((r) => r.then > 50)
    .sort((a, b) => (b.now - b.then) / b.then - (a.now - a.then) / a.then)[0];
}
const monthEnd = () => trajectory(NOW, "mes", null).end.value;
const WEEKDAYS = ["domingo", "segunda", "terça", "quarta", "quinta", "sexta", "sábado"];

// Perguntas da faixa que pedem um recorte próprio do assunto: o texto (e às vezes o bloco)
// muda para responder exatamente o que foi perguntado, com o número dessa pergunta.
const LEADS: Record<string, () => Partial<Answer>> = {
  "maior-gasto": () => {
    const all = (summary(NOW).launches as Launch[]).filter((l) => l.kind === "expense").sort((a, b) => (b.amount ?? 0) - (a.amount ?? 0));
    const top = all[0], variable = all.find((l) => CATEGORIES.find((c) => c.id === l.category)?.variable);
    return {
      text: <>Seu maior gasto de {MONTH} foi <b>{top.label}</b> ({money0(top.amount ?? 0)}), no dia {top.date.getDate()}.{variable && <> Fora as contas fixas, foi <b>{variable.label}</b> ({money0(variable.amount ?? 0)}).</>}</>,
      blocks: [<LaunchList title={`Maiores gastos de ${MONTH}`} launches={all.slice(0, 8)} />],
    };
  },
  "dia-semana": () => {
    const byDay = new Array(7).fill(0);
    for (const l of summary(NOW).launches as Launch[]) if (l.kind === "expense" && CATEGORIES.find((c) => c.id === l.category)?.variable) byDay[l.date.getDay()] += l.amount ?? 0;
    const total = byDay.reduce((a, b) => a + b, 0);
    const d = byDay.indexOf(Math.max(...byDay));
    return {
      text: <>Você gasta mais <b>{d === 0 || d === 6 ? `aos ${WEEKDAYS[d]}s` : `às ${WEEKDAYS[d]}s`}</b>: {money0(byDay[d])} em {MONTH}, {pct(byDay[d] / total)} dos seus gastos do dia a dia. O calendário mostra dia por dia.</>,
      blocks: [<Calendar s={snap()} />],
    };
  },
  "categoria-cresceu": () => {
    const g = grower();
    return g ? { text: <>A que mais cresceu foi <b>{g.c.label}</b>: {money0(g.now)} em {MONTH} até o dia {DAY}, contra {money0(g.then)} em {monthName(keyDate(previousKey(NOW)!))} no mesmo ponto ({pct((g.now - g.then) / g.then)} a mais).</> } : {};
  },
  "fim-de-semana": () => {
    const last = new Date(TODAY.getFullYear(), TODAY.getMonth() + 1, 0).getDate();
    let weekends = 0;
    for (let d = DAY + 1; d <= last; d++) if (new Date(TODAY.getFullYear(), TODAY.getMonth(), d).getDay() === 6) weekends++;
    const spare = monthEnd() - fixedMonthly();
    return {
      text: spare > 0
        ? <>Você fecha {MONTH} com cerca de {money0(monthEnd())}. Guardando {money0(fixedMonthly())} pras contas fixas do mês que vem, sobram uns {money0(spare)}: <b>{money0(spare / Math.max(1, weekends))} por fim de semana</b> até o fim do mês sem apertar.</>
        : <>No ritmo de hoje, o saldo de {MONTH} não cobre as contas fixas do mês que vem ({money0(fixedMonthly())}). Melhor segurar o fim de semana.</>,
    };
  },
  "quanto-investir": () => {
    const spare = monthEnd() - fixedMonthly();
    return {
      text: <>Você fecha {MONTH} com cerca de {money0(monthEnd())}. Deixando um mês de contas fixas ({money0(fixedMonthly())}) de folga, dá pra investir uns <b>{money0(Math.max(0, spare))}</b> sem apertar.</>,
    };
  },
  "antecipar": () => {
    const p = installmentsAhead(6);
    const total = p.months.reduce((a, m) => a + m.value, 0);
    return {
      text: <>Parcela no cartão sem juros não fica mais barata se você antecipar: só tira dinheiro do caixa agora. As suas somam <b>{money0(total)}</b> até {monthName(p.last)} de {p.last.getFullYear()}; vale antecipar se alguma cobrar juros.</>,
    };
  },
  "mes-fraco": () => {
    const rows = incomeHistory();
    const avg = rows.reduce((a, r) => a + r.value, 0) / rows.length;
    const worst = rows.reduce((a, r) => (r.value < a.value ? r : a));
    return {
      text: <>Seu pior mês ({monthName(worst.date)}, {money0(worst.value)}) ficou {money0(avg - worst.value)} abaixo da média. Guardar uns <b>{money0(avg - worst.value)}</b> nos meses bons cobre um mês fraco como aquele.</>,
    };
  },
  "comprometido": () => {
    const fixed = fixedMonthly(), parc = installmentsAhead(6).months[0].value;
    const avg = incomeHistory().reduce((a, r) => a + r.value, 0) / 6;
    return {
      text: <>No mês que vem, <b>{money0(fixed + parc)}</b> já estão comprometidos: {money0(fixed)} de contas fixas e {money0(parc)} de parcelas na fatura. É {pct((fixed + parc) / avg)} da sua renda média ({money0(avg)}).</>,
    };
  },
  "fatura-cabe": () => {
    const p = installmentsAhead(6);
    const drop = p.months.find((m) => m.value < p.months[0].value);
    return {
      text: <>A parte da fatura presa em parcelas fica em {money0(p.months[0].value)} até {monthName(p.months[2].date)}{drop && <>, cai para {money0(drop.value)} em {monthName(drop.date)}</>} e acaba em <b>{monthName(p.last)} de {p.last.getFullYear()}</b>. A partir daí a fatura volta a ter só os gastos do mês (a de {MONTH} está em {money0(summary(NOW).invoice)}).</>,
    };
  },
  "assinaturas": () => {
    const subs = RECURRING.filter((r) => r.category === "assinaturas");
    const total = subs.reduce((a, r) => a + r.amount, 0);
    return {
      text: <>Você paga {subs.length} assinaturas: {subs.map((r, i) => <span key={r.label}>{i ? (i === subs.length - 1 ? " e " : ", ") : ""}<b>{r.label}</b> ({money(r.amount)})</span>)}. São <b>{money(total)} por mês</b>, {money0(total * 12)} por ano.</>,
    };
  },
  "resumo": () => {
    const m = summary(NOW), top = topVariable(m.byCategory);
    return {
      text: <>1. Entraram {money0(m.income)} e saíram {money0(m.expense)} até o dia {DAY}; {money0(m.saved)} foram para as caixinhas.<br />2. O que mais pesa no dia a dia é <b>{top.label}</b> ({money0(m.byCategory[top.id])}).<br />3. No ritmo atual você fecha {MONTH} com cerca de <b>{money0(monthEnd())}</b>.</>,
    };
  },
  "reserva": () => {
    const months = reserveMonths();
    return {
      text: <>Sua reserva cobre <b>{months.toLocaleString("pt-BR", { maximumFractionDigits: 1 })} meses</b> de contas fixas. O comum é ter de 3 a 6 meses, então ela {months < 3 ? "ainda está abaixo do mínimo" : months < 6 ? "já passou do mínimo, mas pode crescer" : "já está num tamanho confortável"}.</>,
    };
  },
  "economizar-mes": () => {
    const g = grower();
    return g ? {
      text: <>O que mais subiu foi <b>{g.c.label}</b>: {money0(g.now)} contra {money0(g.then)} em {monthName(keyDate(previousKey(NOW)!))} no mesmo ponto. Cortar pela metade daria uns <b>{money0((g.now / DAY) * 30.4 / 2)} por mês</b>.</>,
      blocks: [<CategoryDetail s={snap(g.c.id)} />],
    } : {};
  },
  "meta-economia": () => ({
    text: <>Suas caixinhas já recebem <b>{money0(GOALS.reduce((a, g) => a + g.monthly, 0))} por mês</b>. Pra uma meta nova, me diz o valor e a data: no app de verdade eu monto ela com você aqui.</>,
  }),
};

/** A resposta pronta do assunto; `key` (a pergunta da faixa) ajusta o texto quando ela pede um recorte. */
export function answer(topic: TopicId, cat: string | null = null, key?: string): Answer {
  const base = byTopic(topic, key === "categoria-cresceu" ? grower()?.c.id ?? cat : cat);
  return { ...base, ...(key ? LEADS[key]?.() : undefined) };
}

// Texto livre no protótipo: sem IA, o Piggy oferece os assuntos que ele sabe mostrar.
export const DEMO: Answer = {
  text: <>Aqui no protótipo eu respondo pelos atalhos. No app de verdade eu entendo qualquer pergunta sobre o seu dinheiro. Quer ver um destes?</>,
  blocks: [],
  follow: [{ label: "Pra onde vai meu dinheiro?", topic: "categorias" }, { label: "E a minha fatura?", topic: "fatura" }, { label: "Como estão meus investimentos?", topic: "investimentos" }, { label: "Quanto falta pras minhas metas?", topic: "metas" }],
};
