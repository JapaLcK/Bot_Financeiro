import { useDeferredValue, useMemo, useState } from "react";
import { catById, summary } from "../lib/api";
import { longDate, money, tint } from "../lib/format.js";
import { dayKey, setFilter } from "../lib/store.js";
import type { DashState, Launch } from "../lib/types";
import { Seg } from "./Seg";

const SOURCES = {
  whatsapp: { label: "WhatsApp", icon: "ph-whatsapp-logo" },
  openfinance: { label: "Open Finance", icon: "ph-bank" },
  cartao: { label: "Cartão", icon: "ph-credit-card" },
} as const;
type Source = keyof typeof SOURCES | "todos";
const PAGE = 30;

const norm = (t: string) => t.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();

export function Ledger({ s }: { s: DashState }) {
  const [source, setSource] = useState<Source>("todos");
  const [limit, setLimit] = useState(PAGE);
  const query = useDeferredValue(s.filter.query);
  const all = summary(s.month).launches;

  const rows = useMemo(() => {
    const q = norm(query.trim());
    return all.filter((l) =>
      (source === "todos" || l.source === source) &&
      (!s.filter.category || l.category === s.filter.category) &&
      (!s.filter.day || dayKey(l.date) === s.filter.day) &&
      (!q || norm(`${l.label} ${l.msg ?? ""} ${catById(l.category)?.label ?? ""}`).includes(q)));
  }, [all, source, s.filter.category, s.filter.day, query]);

  const groups: { key: string; date: Date; items: Launch[]; net: number }[] = [];
  // Corta em dia inteiro (rows vem por data): senão o total do dia muda ao mostrar mais.
  const cut = (n: number) => {
    let i = Math.min(n, rows.length);
    while (i < rows.length && dayKey(rows[i].date) === dayKey(rows[i - 1].date)) i++;
    return i;
  };
  const shown = cut(limit);
  const next = cut(shown + PAGE);
  for (const l of rows.slice(0, shown)) {
    const key = dayKey(l.date);
    let g = groups[groups.length - 1];
    if (!g || g.key !== key) groups.push((g = { key, date: l.date, items: [], net: 0 }));
    g.items.push(l);
    g.net += l.kind === "income" ? l.amount ?? 0 : -(l.amount ?? 0);
  }
  const filtered = !!(s.filter.category || s.filter.day || s.filter.query || source !== "todos");
  const clear = () => { setFilter({ category: null, day: null, query: "" }); setSource("todos"); };

  return (
    <section id="lancamentos" className="ledger" aria-labelledby="ledger-h">
      <header className="ledger-head">
        <h2 id="ledger-h">No mês <span className="faint num">{rows.length}</span></h2>
        <div className="ledger-tools">
          <label className="search">
            <i className="ph ph-magnifying-glass" aria-hidden="true" />
            <span className="sr-only">Buscar lançamentos</span>
            <input type="search" placeholder="Buscar por nome ou mensagem" value={s.filter.query}
              onChange={(e) => { setFilter({ query: e.target.value }); setLimit(PAGE); }} />
          </label>
          <Seg label="Origem" value={source} onChange={(v) => { setSource(v); setLimit(PAGE); }}
            options={[{ value: "todos", label: "Todos" }, ...Object.entries(SOURCES).map(([k, v]) => ({ value: k as Source, label: v.label }))]} />
        </div>
      </header>
      {(s.filter.category || s.filter.day) && (
        <div className="ledger-chips">
          {s.filter.category && <button type="button" className="chip" aria-pressed="true" onClick={() => setFilter({ category: null })}>{catById(s.filter.category)?.label}<i className="ph ph-x x" aria-hidden="true" /><span className="sr-only">remover filtro</span></button>}
          {s.filter.day && <button type="button" className="chip" aria-pressed="true" onClick={() => setFilter({ day: null })}>dia {Number(s.filter.day.slice(8))}<i className="ph ph-x x" aria-hidden="true" /><span className="sr-only">remover filtro</span></button>}
        </div>
      )}

      {groups.length === 0 ? (
        <div className="empty">
          <i className="ph ph-tray" aria-hidden="true" />
          <p>Nenhum lançamento com esses filtros.</p>
          {filtered && <button type="button" className="btn btn-ghost" onClick={clear}>Limpar filtros</button>}
        </div>
      ) : (
        <div className="ledger-body">
          {groups.map((g) => (
            <div key={g.key} className="day">
              <h3 className="day-head"><span>{longDate(g.date)}</span><span className={`num ${g.net > 0 ? "gain" : "faint"}`}>{g.net > 0 ? "+" : "−"}{money(Math.abs(g.net))}</span></h3>
              <ul>
                {g.items.map((l, i) => {
                  const c = catById(l.category);
                  const src = l.source ? SOURCES[l.source] : null;
                  return (
                    <li key={l.id ?? i} className="row">
                      <span className="row-icon" style={{ color: c?.color ?? "#3ddc97", background: tint(c?.color ?? "#3ddc97", 0.13) }} aria-hidden="true">
                        <i className={`ph ${l.kind === "income" ? "ph-arrow-down" : l.kind === "transfer" ? "ph-piggy-bank" : c?.icon}`} />
                      </span>
                      <span className="row-main">
                        <span className="row-label">{l.label}</span>
                        <span className="row-sub faint">
                          {l.kind === "transfer" ? "Transferência" : l.kind === "income" ? "Entrada" : c?.label}
                          {l.msg && <q className="row-msg">{l.msg}</q>}
                        </span>
                      </span>
                      {src && <span className="row-src faint"><i className={`ph ${src.icon}`} aria-hidden="true" />{src.label}</span>}
                      <span className={`row-amt num ${l.kind === "income" ? "gain" : ""}`}>{l.kind === "income" ? "+" : "−"}{money(l.amount ?? 0)}</span>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
          {rows.length > shown && (
            <button type="button" className="btn btn-ghost more" onClick={() => setLimit(shown + PAGE)}>
              Mostrar mais {next - shown} de {rows.length - shown}
            </button>
          )}
        </div>
      )}
    </section>
  );
}
