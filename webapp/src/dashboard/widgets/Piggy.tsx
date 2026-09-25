import type { ReactNode } from "react";
import { BALANCE_TODAY, CATEGORIES, MONTHS, TODAY, addDays, isCurrentMonth, keyDate, previousKey, scheduled, summary, trajectory } from "../lib/api";
import { LAUNCHES } from "../lib/data.js";
import { monthName, money0, relativeDays, signed0 } from "../lib/format.js";
import { set, setCut } from "../lib/store.js";
import type { DashState, Launch } from "../lib/types";
import { Frame } from "../parts/Frame";
import { go } from "../router";

// `head` e `ask` são o que a faixa do topo do Resumo usa: a manchete e a pergunta que vai para o chat.
interface Insight { key: string; icon: string; tone: string; text: ReactNode; head?: string; ask?: string; action?: { label: string; run: () => void } }

const spentIn = (key: string, cat: string, day: number) =>
  (LAUNCHES[key] as Launch[]).filter((l) => l.kind === "expense" && l.category === cat && l.date.getDate() <= day).reduce((a, l) => a + (l.amount ?? 0), 0);

// Observações calculadas dos dados do mês, nunca texto solto: cada número sai do modelo.
export function insights(s: DashState): Insight[] {
  const out: Insight[] = [];
  const prev = previousKey(s.month);
  const current = isCurrentMonth(s.month);
  const day = current ? TODAY.getDate() : 31;
  if (prev) {
    const rise = CATEGORIES.filter((c) => c.variable)
      .map((c) => ({ c, now: spentIn(s.month, c.id, day), then: spentIn(prev, c.id, day) }))
      .filter((r) => r.then > 50)
      .map((r) => ({ ...r, d: (r.now - r.then) / r.then }))
      .sort((a, b) => b.d - a.d)[0];
    if (rise && rise.d > 0.15) {
      out.push({
        key: "rise", icon: "ph-trend-up", tone: "var(--warn)",
        head: `${rise.c.label} subiu ${Math.round(rise.d * 100)}%`,
        ask: `Por que meu gasto com ${rise.c.label.toLowerCase()} subiu ${Math.round(rise.d * 100)}% em ${monthName(keyDate(s.month))}?`,
        text: <><b>{rise.c.label}</b> subiu {Math.round(rise.d * 100)}%: {money0(rise.now)} contra {money0(rise.then)} em {monthName(keyDate(prev))}{current ? ` até o dia ${day}` : ""}.</>,
        action: { label: `Simular ${rise.c.label.toLowerCase()} −30%`, run: () => { setCut(rise.c.id, 0.3); go("/simulador"); } },
      });
    }
  }
  if (current) {
    const end = trajectory(s.month, "90", null).end;
    const perMonth = ((end.value - BALANCE_TODAY) / 90) * 30.4;
    if (perMonth < 0) {
      out.push({
        key: "trend", icon: "ph-chart-line-down", tone: "var(--pink-ink)",
        head: "Sem freelas, seu saldo desce",
        ask: "Como evito que meu saldo caia nos próximos 3 meses?",
        text: <>Sem freelas, seu saldo cai cerca de <b>{money0(-perMonth)} por mês</b>. Em 90 dias fica perto de {money0(end.value)}.</>,
        action: { label: "Ver 90 dias", run: () => { set({ horizon: "90" }); go("/previsao"); } },
      });
    }
    const next = scheduled(addDays(TODAY, 1), addDays(TODAY, 30)).find((b) => b.kind === "expense" && !b.transfer && b.source !== "cartao" && b.amount != null);
    if (next) {
      const days = Math.round((next.date.getTime() - TODAY.getTime()) / 86400000);
      out.push({
        key: "next", icon: "ph-receipt", tone: "#2fa0c8",
        head: `${next.label} vence ${relativeDays(days)}`,
        ask: `Consigo pagar ${next.label} (${money0(next.amount ?? 0)}) sem apertar o resto do mês?`,
        text: <><b>{next.label}</b> vence {relativeDays(days)} ({money0(next.amount ?? 0)}). O saldo de hoje, {money0(BALANCE_TODAY)}, {BALANCE_TODAY >= (next.amount ?? 0) ? "cobre" : "não cobre"}.</>,
        action: { label: "Ver compromissos", run: () => go("/previsao") },
      });
    }
  } else {
    const m = summary(s.month);
    const left = m.income - m.expense - m.saved;
    const name = monthName(keyDate(s.month));
    out.push({ key: "closed", icon: "ph-check-circle", tone: "var(--gain)", text: <>{name[0].toUpperCase() + name.slice(1)} fechou com <b>{signed0(left)}</b> entre o que entrou e o que saiu, depois de guardar {money0(m.saved)} nas caixinhas.</> });
    out.push({ key: "back", icon: "ph-clock-counter-clockwise", tone: "var(--ink-3)", text: <>Quer ver a previsão de agora?</>, action: { label: "Voltar para o mês atual", run: () => set({ month: MONTHS[MONTHS.length - 1] }) } });
  }
  return out;
}

export function Piggy({ s }: { s: DashState }) {
  return (
    <Frame id="piggy" title={<span className="piggy-title"><img src="../frontend/brand/icon.png" alt="" width={22} height={22} />Piggy notou</span>}>
      <ul className="insights">
        {insights(s).map((i) => (
          <li key={i.key}>
            <i className={`ph ${i.icon} insight-icon`} style={{ color: i.tone }} aria-hidden="true" />
            <p>{i.text}</p>
            {i.action && <button type="button" className="link" onClick={i.action.run}>{i.action.label}<i className="ph ph-arrow-right" aria-hidden="true" /></button>}
          </li>
        ))}
      </ul>
    </Frame>
  );
}
