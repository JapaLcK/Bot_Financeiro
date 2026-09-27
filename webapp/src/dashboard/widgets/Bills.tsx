import { useMemo, useRef, useState } from "react";
import { MONTHS, TODAY, addDays, scheduled } from "../lib/api";
import { money, money0, relativeDays, weekday } from "../lib/format.js";
import { useActions } from "../lib/actions";
import { dayKey } from "../lib/store.js";
import type { DashState, Launch } from "../lib/types";
import { Frame } from "../parts/Frame";

const DAY = 86400000;

// Compromissos dos últimos 5 dias (já pagos) e dos próximos `span` dias.
export function Bills({ s, days: span = 30 }: { s: DashState; days?: number }) {
  const { get, set } = useActions();
  const items = useMemo(() => scheduled(addDays(TODAY, -5), addDays(TODAY, span)), [span]);
  // Assinatura no cartão é paga pela fatura, que já tem linha própria.
  const due = items.filter((b) => b.date > TODAY && b.kind === "expense" && !b.transfer && b.source !== "cartao");
  const known = due.reduce((a, b) => a + (b.amount ?? 0), 0);
  // Clique fixa o dia no gráfico; passar o mouse (ou o foco) só pré-visualiza, e sair volta ao fixado.
  // O ref é lido pelo restore (sempre atual, mesmo num pointerleave que chega antes do render); o state pinta o aria-pressed.
  const [pinned, setPinned] = useState<number | null>(null);
  const pinRef = useRef<number | null>(null);
  const pin = (i: number | null) => { pinRef.current = i; setPinned(i); };
  const focus = (b: Launch) => set({ highlight: dayKey(b.date) });
  const restore = () => {
    const day = pinRef.current == null ? null : dayKey(items[pinRef.current].date);
    if (get().highlight !== day) set({ highlight: day });
  };

  return (
    <Frame id="compromissos" title={`Próximos ${span} dias`}>
      <p className="w-lede">A pagar <span className="num">{money0(known)}</span> · as marcadas como estimadas ainda podem mudar</p>
      <ol className="bills" onPointerLeave={restore}>
        {items.map((b, i) => {
          const days = Math.round((b.date.getTime() - TODAY.getTime()) / DAY);
          const card = b.source === "cartao";
          const paid = days <= 0;
          const status = card ? "no cartão" : paid ? (b.kind === "income" ? "recebido" : "pago") : b.estimated || b.amount == null ? "estimado" : relativeDays(days);
          return (
            <li key={i}>
              <button type="button" className="bill" data-paid={paid || undefined} data-on={s.highlight === dayKey(b.date) || undefined} aria-pressed={pinned === i}
                onPointerEnter={() => focus(b)} onFocus={() => focus(b)} onBlur={restore}
                aria-label={`${b.label}, ${b.date.getDate()}/${b.date.getMonth() + 1}, ${b.amount == null ? "valor a definir" : money(b.amount)}, ${status}. Mostrar no gráfico.`}
                onClick={() => {
                  if (pinRef.current === i) { pin(null); focus(b); return; } // ainda sob o cursor: fica em prévia
                  pin(i);
                  set({ month: MONTHS[MONTHS.length - 1], horizon: days > 30 ? "90" : days > 7 ? "30" : "mes", highlight: dayKey(b.date) });
                }}>
                <span className="bill-date"><b>{b.date.getDate()}</b><span>{weekday(b.date)}</span></span>
                <span className="bill-name">
                  {b.label}
                  <span className={`bill-status ${!card && !paid && days <= 3 ? "warn" : "faint"}`}>
                    {card ? <i className="ph ph-credit-card" aria-hidden="true" /> : paid && <i className="ph ph-check" aria-hidden="true" />}{status}
                  </span>
                </span>
                <span className={`bill-amt num ${b.kind === "income" ? "gain" : ""}`}>
                  {b.amount == null ? "—" : `${b.kind === "income" ? "+" : "−"}${money0(b.amount)}`}
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </Frame>
  );
}
