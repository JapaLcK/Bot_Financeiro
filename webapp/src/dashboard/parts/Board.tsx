import { useEffect, useState, type ReactNode } from "react";
import DraggableWidgetGrid, { type WidgetItem } from "@/components/ui/draggable-widget-grid";
import { PLAN } from "../lib/api";
import { PROFILES, locked, readLayout, readProfile, saveLayout, saveProfile } from "../lib/profiles.js";
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
import { Invoice } from "../widgets/Invoice";
import { Wealth } from "../widgets/Wealth";
import { Income } from "../widgets/Income";
import { Yield } from "../widgets/Yield";
import { Installments } from "../widgets/Installments";
import { Catalog, ProfileSelect } from "./BoardControls";
import { ProfilePicker } from "./ProfilePicker";
import { PiggyBand } from "./PiggyBand";

// Ordem padrão (o perfil `padrao`): em 4 colunas ela ladrilha sem buraco (24 células, 6 linhas).
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
// Fora do padrão: só entram por perfil ou pelo catálogo do Organizar.
const EXTRA: WidgetItem[] = [
  { id: "fatura", size: "tall", label: "Fatura do cartão" },
  { id: "wealth", size: "tall", label: "Onde está o dinheiro" },
  { id: "renda", size: "tall", label: "Renda mês a mês" },
  { id: "rendimento", size: "wide", label: "Rendimento × CDI" },
  { id: "parcelas", size: "wide", label: "Parcelas futuras" },
];
const ALL = [...DEFAULT, ...EXTRA];
const KNOWN = ALL.map((w) => w.id);
const ITEM = new Map(ALL.map((w) => [w.id, w]));

const VIEWS: Record<string, (p: { s: DashState }) => ReactNode> = {
  hero: Hero, resumo: MonthStats,
  categorias: Categories, calendario: Calendar, simulador: Simulator,
  compromissos: Bills, piggy: Piggy, metas: Goals, patrimonio: () => <NetWorth />,
  fatura: Invoice, wealth: () => <Wealth />,
  renda: () => <Income />, rendimento: () => <Yield />, parcelas: () => <Installments />,
};

// Página de cada bloco (o Piggy não tem página própria: as ações dele levam às outras).
const PAGE: Record<string, Path | null> = {
  hero: "/previsao", resumo: "/lancamentos", categorias: "/gastos", calendario: "/gastos",
  simulador: "/simulador", compromissos: "/previsao", piggy: null, metas: "/metas", patrimonio: "/patrimonio",
  fatura: "/previsao", wealth: "/patrimonio",
  renda: "/lancamentos", rendimento: "/patrimonio", parcelas: "/previsao",
};

const presetOf = (p: string): string[] => PROFILES.find((x) => x.id === p)?.preset ?? DEFAULT.map((w) => w.id);
const layoutOf = (p: string): string[] => readLayout(p, presetOf(p), KNOWN, PLAN);
const shownPreset = (p: string) => presetOf(p).filter((id) => KNOWN.includes(id) && !locked(id, PLAN));

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
  const [profile, setProfile] = useState<string | null>(readProfile); // null: o modal está aberto
  const shown = profile ?? "padrao";
  const [ids, setIds] = useState(() => layoutOf(shown));
  // ponytail: o grid só lê `items` ao montar; perfil, catálogo e restaurar o remontam (key),
  // o que reanima a entrada dos blocos. Sincronizar `items` dentro do grid evita isso.
  const [version, setVersion] = useState(0);
  const [said, setSaid] = useState("");
  const columns = useColumns();
  const custom = ids.join() !== shownPreset(shown).join();

  const choose = (p: string) => {
    saveProfile(p);
    setProfile(p);
    setIds(layoutOf(p));
    setVersion((v) => v + 1);
    document.getElementById("board-profile")?.focus();
  };
  const add = (id: string) => {
    const next = [...ids, id];
    saveLayout(shown, next);
    setIds(next);
    setVersion((v) => v + 1);
    setSaid(`${ITEM.get(id)!.label} adicionado ao fim do painel`);
  };
  const restore = () => { saveLayout(shown, null); setIds(layoutOf(shown)); setVersion((v) => v + 1); };

  return (
    <section className="board" data-editing={s.editing || undefined} data-fit={columns === 1 || undefined} aria-label="Seu painel">
      <div className="board-head">
        <ProfileSelect value={shown} onPick={choose} />
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
      <PiggyBand key={shown} s={s} profile={shown} />
      {s.editing && <Catalog missing={ALL.filter((w) => !ids.includes(w.id))} onAdd={add} />}
      <p className="sr-only" aria-live="polite">{said}</p>
      {!ids.length && !s.editing && <p className="board-empty faint">Toque em Organizar para adicionar blocos</p>}
      <DraggableWidgetGrid
        key={version}
        items={ids.map((id) => ITEM.get(id)!)}
        editable={s.editing}
        onRemove
        maxColumns={columns}
        cellSize={272}
        gap={14}
        radius={16}
        fitRows={columns === 1}
        onChange={(next) => {
          const list = next.map((w) => w.id);
          saveLayout(shown, list);
          setIds(list);
          if (!list.length) document.getElementById("board-add")?.focus();
        }}
        renderItem={(item) => {
          const View = VIEWS[item.id];
          return View ? <FrameLink.Provider value={PAGE[item.id] ?? null}><View s={s} /></FrameLink.Provider> : null;
        }}
      />
      {profile === null && <ProfilePicker onPick={choose} />}
    </section>
  );
}
