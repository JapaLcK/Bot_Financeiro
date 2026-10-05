import NumberFlow from "@number-flow/react";
import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import type { ResumoDoMes } from "../lib/api-v2.gen";
import { CARD, GOALS, caixinhasTotal, isCurrentMonth, keyDate, summary } from "../lib/api";
import { monthName, money, money0 } from "../lib/format.js";
import type { DashState } from "../lib/types";
import { mesDe } from "../lib/store.js";
import { DEMO, resumoMesQuery } from "../lib/v2";
import { Frame } from "../parts/Frame";
import { Selos } from "../parts/Selos";
import { BRL } from "./Hero";

// Os quatro números do mês num bloco só, separados por filetes (não quatro cartões).
// Entrou e Saiu vêm da /api/v2/resumo-do-mes, com centavos como o servidor manda (decisão
// do dono); Fatura e Guardado ainda são inventados e seguem sem casas. O mês anterior sai com
// o hífen do Intl, como o NumberFlow, e não com o menos tipográfico do `money` (decisão do dono).
const CENTAVOS = { style: "currency", currency: "BRL" } as const;
function Stat({ title, tone, value, format = BRL, delta, demo, note, children, guia }: {
  title: string;
  tone: string;
  value?: number;
  format?: typeof BRL | typeof CENTAVOS;
  delta?: { text: string; good: boolean | null };
  demo?: boolean;
  note?: ReactNode;
  children?: ReactNode;
  guia?: string;
}) {
  return (
    <section className="stat" data-guia={guia}>
      <h3 className="stat-title"><span className="stat-key" style={{ background: tone }} aria-hidden="true" />{title}</h3>
      {demo && <span className="selo">demonstração</span>}
      {value !== undefined && <NumberFlow className="stat-value" value={value} locales="pt-BR" format={format} />}
      {delta && <p className={`stat-delta ${delta.good === null ? "" : delta.good ? "gain" : "warn"}`}>{delta.text}</p>}
      {note}
      <div className="stat-foot">{children}</div>
    </section>
  );
}

// O mês anterior inteiro como referência, sem porcentagem (decisão N4).
function Real({ title, tone, k, q }: { title: string; tone: string; k: "entrou" | "saiu"; q: ReturnType<typeof useResumo> }) {
  const d = q.data;
  const guia = k === "saiu" ? "resumo.saiu" : undefined; // âncora do 1º passo do guia (parts/Guia.tsx)
  if (!d) {
    return (
      <Stat title={title} tone={tone} guia={guia} note={q.isPending ? <p role="status" className="faint stat-note">Carregando…</p> : (
        <p className="stat-note" role="alert">Não deu para carregar. <button type="button" className="btn retry btn-quiet" onClick={() => q.refetch()}>Tentar de novo</button></p>
      )} />
    );
  }
  const a = d.anterior;
  return (
    <Stat title={title} tone={tone} guia={guia} value={Number(d[k])} format={CENTAVOS} demo={DEMO}
      delta={a ? { text: `em ${monthName(keyDate(a.mes))}: ${money(Number(a[k])).replace("−", "-")}`, good: null } : undefined}
      note={<Selos motivos={d.motivos} />} />
  );
}

const useResumo = (mes: string) => useQuery<ResumoDoMes>(resumoMesQuery(mes));

function Invoice({ s }: { s: DashState }) {
  const m = summary(s.month);
  const used = m.invoice / CARD.limit;
  const [y, mm] = s.month.split("-").map(Number);
  const due = new Date(y, mm, CARD.dueDay);
  const current = isCurrentMonth(s.month);
  return (
    <Stat title={current ? "Fatura aberta" : `Fatura de ${monthName(new Date(y, mm - 1, 2))}`} tone="var(--warn)" value={m.invoice} demo
      delta={{ text: current ? `vence ${due.getDate()} de ${monthName(due)}` : `paga em ${due.getDate()} de ${monthName(due)}`, good: null }}>
      <div className="meter warn" role="meter" aria-valuemin={0} aria-valuemax={CARD.limit} aria-valuenow={m.invoice} aria-label="Uso do limite">
        <span style={{ transform: `scaleX(${Math.min(1, used)})` }} />
      </div>
      <p className="faint stat-note">{Math.round(used * 100)}% do limite de <span className="num">{money0(CARD.limit)}</span></p>
    </Stat>
  );
}

function Saved({ s }: { s: DashState }) {
  const m = summary(s.month);
  const total = caixinhasTotal();
  return (
    <Stat title="Guardado no mês" tone="#9085e9" value={m.saved} demo delta={{ text: `automático nas ${GOALS.length} caixinhas`, good: null }}>
      <p className="stat-note"><span className="faint">Total nas caixinhas</span> <b className="num">{money0(total)}</b></p>
    </Stat>
  );
}

export function MonthStats({ s }: { s: DashState }) {
  const q = useResumo(mesDe(s));
  return (
    <Frame id="resumo" title="Resumo do mês" className="w-stats" real>
      <div className="stats">
        <Real title="Entrou" tone="var(--gain)" k="entrou" q={q} />
        <Real title="Saiu" tone="var(--alert)" k="saiu" q={q} />
        <Invoice s={s} />
        <Saved s={s} />
      </div>
    </Frame>
  );
}
