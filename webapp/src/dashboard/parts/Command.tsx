import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { CATEGORIES, HORIZONS, MONTHS, summary } from "../lib/api";
import { dayMonth, money, monthTitle } from "../lib/format.js";
import { get, set, setFilter, setSim } from "../lib/store.js";
import { PRESETS } from "../widgets/Simulator";
import { ROUTES, go } from "../router";

interface Cmd { id: string; group: string; label: string; hint?: string; icon: string; run: () => void }


const norm = (t: string) => t.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();

function commands(q: string): Cmd[] {
  const s = get();
  const list: Cmd[] = [
    ...ROUTES.map((r) => ({ id: `nav-${r.path}`, group: "Ir para", label: r.label, icon: r.icon, run: () => go(r.path) })),
    { id: "edit", group: "Ações", label: s.editing ? "Terminar de organizar o painel" : "Organizar o painel", icon: "ph-pencil-simple", run: () => set({ editing: !s.editing }) },
    ...PRESETS.map((p) => ({ id: `sim-${p.label}`, group: "Ações", label: `Simular: ${p.label.toLowerCase()}`, icon: "ph-lightning", run: () => { setSim({ cuts: { ...p.cuts } }); go("/simulador"); } })),
    ...(["mes", "30", "90"] as const).map((h) => ({ id: `h-${h}`, group: "Ações", label: `Previsão: ${HORIZONS[h].toLowerCase()}`, icon: "ph-clock", run: () => { set({ horizon: h, month: MONTHS[MONTHS.length - 1] }); go("/previsao"); } })),
    ...MONTHS.map((m) => ({ id: `m-${m}`, group: "Meses", label: monthTitle(m), icon: "ph-calendar-dots", run: () => set({ month: m }) })),
    ...CATEGORIES.map((c) => ({ id: `c-${c.id}`, group: "Filtrar por categoria", label: c.label, icon: c.icon, run: () => { setFilter({ category: c.id }); go("/lancamentos"); } })),
  ];
  const nq = norm(q.trim());
  if (!nq) return list.filter((c) => c.group !== "Filtrar por categoria");
  const found = list.filter((c) => norm(c.label).includes(nq));
  const launches = summary(s.month).launches
    .filter((l) => norm(`${l.label} ${l.msg ?? ""}`).includes(nq))
    .slice(0, 6)
    .map((l, i) => ({
      id: `l-${l.id ?? i}`, group: "Lançamentos", label: l.label, icon: l.kind === "income" ? "ph-arrow-down" : "ph-receipt",
      hint: `${dayMonth(l.date)} · ${money(l.amount ?? 0)}`,
      run: () => { setFilter({ query: l.label, category: null, day: null }); go("/lancamentos"); },
    }));
  return [...found, ...launches];
}

export function Command() {
  const dlg = useRef<HTMLDialogElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);
  const [opened, setOpened] = useState(0); // relê o estado a cada abertura
  const items = useMemo(() => commands(q), [q, opened]);

  useEffect(() => {
    const open = () => { setQ(""); setActive(0); setOpened((n) => n + 1); dlg.current?.showModal(); input.current?.focus(); };
    const onKey = (e: globalThis.KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); if (dlg.current?.open) dlg.current.close(); else open(); }
      else if (e.key === "/" && !(e.target as HTMLElement).closest("input, textarea, select") && !dlg.current?.open) { e.preventDefault(); open(); }
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("dash:command", open);
    return () => { window.removeEventListener("keydown", onKey); window.removeEventListener("dash:command", open); };
  }, []);

  useEffect(() => {
    dlg.current?.querySelector(`[data-i="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active]);

  const run = (c: Cmd | undefined) => { if (!c) return; dlg.current?.close(); c.run(); };
  const onKey = (e: KeyboardEvent) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(items.length - 1, a + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(0, a - 1)); }
    else if (e.key === "Enter") { e.preventDefault(); run(items[active]); }
  };

  let lastGroup = "";
  return (
    <dialog className="cmdk" ref={dlg} aria-label="Buscar ou ir para" onClick={(e) => { if (e.target === dlg.current) dlg.current?.close(); }}>
      <div className="cmdk-field">
        <i className="ph ph-magnifying-glass" aria-hidden="true" />
        <input ref={input} value={q} onChange={(e) => { setQ(e.target.value); setActive(0); }} onKeyDown={onKey}
          placeholder="Buscar lançamento, categoria ou ação…" autoComplete="off" spellCheck={false}
          role="combobox" aria-expanded="true" aria-controls="cmdk-list" aria-activedescendant={items[active] ? `cmd-${active}` : undefined} />
        <kbd>esc</kbd>
      </div>
      <ul className="cmdk-list" id="cmdk-list" role="listbox" aria-label="Resultados">
        {items.length === 0 && <li className="cmdk-empty faint">Nada encontrado para “{q}”.</li>}
        {items.map((c, i) => {
          const head = c.group !== lastGroup ? (lastGroup = c.group) : null;
          return (
            <li key={c.id} role="presentation">
              {head && <p className="cmdk-group" aria-hidden="true">{head}</p>}
              <div id={`cmd-${i}`} data-i={i} role="option" aria-selected={i === active} className="cmdk-item"
                onPointerMove={() => setActive(i)} onClick={() => run(c)}>
                <i className={`ph ${c.icon}`} aria-hidden="true" />
                <span>{c.label}</span>
                {c.hint && <span className="faint num cmdk-hint">{c.hint}</span>}
              </div>
            </li>
          );
        })}
      </ul>
      <p className="cmdk-foot faint"><kbd>↑</kbd><kbd>↓</kbd> navegar <kbd>↵</kbd> abrir <kbd>/</kbd> ou <kbd>⌘K</kbd> chama esta busca</p>
    </dialog>
  );
}
