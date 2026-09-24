import { useEffect, useState, type ReactNode } from "react";
import DraggableWidgetGrid, { type WidgetItem } from "@/components/ui/draggable-widget-grid";
import { set } from "../lib/store.js";
import type { Path } from "../router";
import { FrameLink } from "./Frame";
import type { DashState } from "../lib/types";
import { Hero } from "../widgets/Hero";
import { MonthStats } from "../widgets/Stats";
import { Categories } from "../widgets/Categories";
import { Calendar } from "../widgets/Calendar";
import { Simulator } from "../widgets/Simulator";
import { Bills } from "../widgets/Bills";
import { Piggy } from "../widgets/Piggy";
import { Goals } from "../widgets/Goals";
import { NetWorth } from "../widgets/NetWorth";

// Ordem padrão: em 4 colunas ela ladrilha sem buraco (24 células, 6 linhas).
const DEFAULT: WidgetItem[] = [
  { id: "hero", size: "lg", label: "Saldo previsto" },
  { id: "resumo", size: "lg", label: "Resumo do mês" },
  { id: "categorias", size: "tall", label: "Para onde vai" },
  { id: "calendario", size: "tall", label: "Dia a dia" },
  { id: "simulador", size: "lg", label: "E se…" },
  { id: "compromissos", size: "tall", label: "Próximos 30 dias" },
  { id: "piggy", size: "tall", label: "Piggy notou" },
  { id: "metas", size: "wide", label: "Metas e caixinhas" },
  { id: "patrimonio", size: "wide", label: "Patrimônio" },
];

const VIEWS: Record<string, (p: { s: DashState }) => ReactNode> = {
  hero: Hero, resumo: MonthStats,
  categorias: Categories, calendario: Calendar, simulador: Simulator,
  compromissos: Bills, piggy: Piggy, metas: Goals, patrimonio: () => <NetWorth />,
};

// Página de cada bloco (o Piggy não tem página própria: as ações dele levam às outras).
const PAGE: Record<string, Path | null> = {
  hero: "/previsao", resumo: "/lancamentos", categorias: "/gastos", calendario: "/gastos",
  simulador: "/simulador", compromissos: "/previsao", piggy: null, metas: "/metas", patrimonio: "/patrimonio",
};

const KEY = "pigbank.dashboard.layout.v1";

// A ordem salva vale só se tiver exatamente os mesmos widgets (e tamanhos) de hoje.
function savedLayout(): WidgetItem[] {
  try {
    const ids: string[] = JSON.parse(localStorage.getItem(KEY) ?? "null");
    if (Array.isArray(ids) && ids.length === DEFAULT.length && DEFAULT.every((d) => ids.includes(d.id)))
      return ids.map((id) => DEFAULT.find((d) => d.id === id)!);
  } catch { /* armazenamento indisponível: fica o padrão */ }
  return DEFAULT;
}
const save = (items: WidgetItem[] | null) => {
  try {
    if (items) localStorage.setItem(KEY, JSON.stringify(items.map((i) => i.id)));
    else localStorage.removeItem(KEY);
  } catch { /* idem */ }
};

function useColumns() {
  const query = "(max-width: 640px)";
  const [narrow, setNarrow] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const on = () => setNarrow(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return narrow ? 1 : 4;
}

export function Board({ s }: { s: DashState }) {
  const [items] = useState(savedLayout);
  const [version, setVersion] = useState(0);
  const [custom, setCustom] = useState(items !== DEFAULT);
  const columns = useColumns();

  const restore = () => { save(null); setCustom(false); setVersion((v) => v + 1); };

  return (
    <section className="board" data-editing={s.editing || undefined} data-fit={columns === 1 || undefined} aria-label="Seu painel">
      <div className="board-head">
        <p className="board-hint" aria-live="polite">
          {s.editing
            ? <>Arraste os blocos para organizar. No celular, segure antes de arrastar. No teclado: <kbd>Alt</kbd> + setas.</>
            : <span className="faint">Dados de demonstração · hoje é 23 de setembro de 2026</span>}
        </p>
        <div className="board-actions">
          {s.editing && custom && <button type="button" className="btn btn-quiet" onClick={restore}>Restaurar padrão</button>}
          <button type="button" className="btn btn-ghost" aria-pressed={s.editing} onClick={() => set({ editing: !s.editing })}>
            <i className={`ph ${s.editing ? "ph-check" : "ph-pencil-simple"}`} aria-hidden="true" />
            {s.editing ? "Pronto" : "Organizar"}
          </button>
        </div>
      </div>
      <DraggableWidgetGrid
        key={version}
        items={version === 0 ? items : DEFAULT}
        editable={s.editing}
        maxColumns={columns}
        cellSize={272}
        gap={14}
        radius={16}
        fitRows={columns === 1}
        onChange={(next) => { save(next); setCustom(true); }}
        renderItem={(item) => {
          const View = VIEWS[item.id];
          return View ? <FrameLink.Provider value={PAGE[item.id] ?? null}><View s={s} /></FrameLink.Provider> : null;
        }}
      />
    </section>
  );
}
