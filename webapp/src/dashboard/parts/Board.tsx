import { useEffect, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import DraggableWidgetGrid, { type WidgetItem } from "@/components/ui/draggable-widget-grid";
import type { NovoPerfil, Perfil } from "../lib/api-v2.gen";
import { TODAY } from "../lib/api";
import { PROFILES, locked, readLayout, saveLayout, saveProfile } from "../lib/profiles.js";
import { set } from "../lib/store.js";
import { DEMO, ErroApi, FUSO, apiPut, perfilQuery, usePlan } from "../lib/v2";
import type { Path } from "../router";
import { FrameLink } from "./Frame";
import { CRIAR_SENHA } from "./Entrada";
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
import { Subscriptions } from "../widgets/Subscriptions";
import { Contas } from "../widgets/Contas";
import { Catalog, ProfileSelect } from "./BoardControls";
import { ProfilePicker } from "./ProfilePicker";
import { PiggyBand } from "./PiggyBand";

// Ordem padrão (o perfil `padrao`): em 4 colunas ela ladrilha sem buraco (28 células, 7 linhas).
const DEFAULT: WidgetItem[] = [
  { id: "contas", size: "lg", label: "Contas" },
  { id: "hero", size: "lg", label: "Saldo previsto" },
  { id: "resumo", size: "lg", label: "Resumo do mês" },
  { id: "categorias", size: "tall", label: "Para onde vai" },
  { id: "calendario", size: "tall", label: "Dia a dia" },
  { id: "simulador", size: "lg", label: "Simulador" },
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
  { id: "assinaturas", size: "tall", label: "Assinaturas" },
];
const ALL = [...DEFAULT, ...EXTRA];
const KNOWN = ALL.map((w) => w.id);
const ITEM = new Map(ALL.map((w) => [w.id, w]));

const VIEWS: Record<string, (p: { s: DashState }) => ReactNode> = {
  contas: () => <Contas />, hero: Hero, resumo: MonthStats,
  categorias: Categories, calendario: Calendar, simulador: Simulator,
  compromissos: Bills, piggy: Piggy, metas: Goals, patrimonio: () => <NetWorth />,
  fatura: Invoice, wealth: () => <Wealth />,
  renda: () => <Income />, rendimento: () => <Yield />, parcelas: () => <Installments />,
  assinaturas: () => <Subscriptions />,
};

// Página de cada bloco (o Piggy não tem página própria: as ações dele levam às outras).
const PAGE: Record<string, Path | null> = {
  contas: null, hero: "/previsao", resumo: "/lancamentos", categorias: "/gastos", calendario: "/gastos",
  simulador: "/simulador", compromissos: "/previsao", piggy: null, metas: "/metas", patrimonio: "/patrimonio",
  fatura: "/previsao", wealth: "/patrimonio",
  renda: "/lancamentos", rendimento: "/patrimonio", parcelas: "/previsao", assinaturas: "/assinaturas",
};

const presetOf = (p: string): string[] => PROFILES.find((x) => x.id === p)?.preset ?? DEFAULT.map((w) => w.id);
const layoutOf = (p: string, plan: string): string[] => readLayout(p, presetOf(p), KNOWN, plan);
const shownPreset = (p: string, plan: string) => presetOf(p).filter((id) => KNOWN.includes(id) && !locked(id, plan));

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

type Escolha = NovoPerfil["perfil"];
// Conta paga sem senha (nem Google/Apple) leva 403 `password_required` no PUT: o mesmo
// "Criar senha" do portão (Entrada).
const SEM_SENHA = <>Crie sua senha para salvar o seu painel. Depois de criar, volte para o painel novo. <a className="btn btn-ghost retry" href={CRIAR_SENHA.href}>{CRIAR_SENHA.texto}</a></>;
// Com backend, o dia no fuso do app (o mesmo de mesAtual); no protótipo, o dia da demonstração.
const hoje = () => new Intl.DateTimeFormat("pt-BR", { day: "numeric", month: "long", year: "numeric", timeZone: DEMO ? undefined : FUSO }).format(DEMO ? TODAY : new Date());

export function Board({ s }: { s: DashState }) {
  const qc = useQueryClient();
  const q = useQuery(perfilQuery);
  const profile = q.data?.perfil; // null: nunca escolheu, o modal abre
  const shown = profile ?? "padrao";
  const plan = usePlan();
  const [ids, setIds] = useState(() => layoutOf(shown, plan));
  // ponytail: o grid só lê `items` ao montar; perfil, catálogo e restaurar o remontam (key),
  // o que reanima a entrada dos blocos. Sincronizar `items` dentro do grid evita isso.
  const [version, setVersion] = useState(0);
  // O perfil muda por escolha, por erro que desfaz ou pelo servidor (outra aba, "Recomeçar
  // do zero" pelo SSE): o layout passa a ser o dele.
  const [de, setDe] = useState(shown);
  if (de !== shown) {
    setDe(shown);
    setIds(layoutOf(shown, plan));
    setVersion((v) => v + 1);
  }
  const [said, setSaid] = useState("");
  const [aviso, setAviso] = useState<ReactNode>("");
  const columns = useColumns();
  const custom = ids.join() !== shownPreset(shown, plan).join();

  // O PUT de perfil em voo, de forma síncrona: o `isPending` do TanStack só chega ao React
  // depois, e duas trocas na mesma tarefa passariam as duas pela guarda.
  const voando = useRef(false);
  // Otimista: a tela troca na hora e desfaz se o PUT falhar (inclusive 403).
  const m = useMutation({
    mutationFn: async (p: Escolha) => { if (DEMO) saveProfile(p); else await apiPut("/perfil", { perfil: p }); },
    onMutate: async (p) => {
      setAviso("");
      await qc.cancelQueries({ queryKey: perfilQuery.queryKey });
      const antes = qc.getQueryData<Perfil>(perfilQuery.queryKey);
      qc.setQueryData<Perfil>(perfilQuery.queryKey, { perfil: p });
      return antes;
    },
    onError: (e, p, antes) => {
      // Outra escolha já escreveu por cima: a dela vale, e a recarga confirma.
      if (qc.getQueryData<Perfil>(perfilQuery.queryKey)?.perfil === p) qc.setQueryData(perfilQuery.queryKey, antes);
      setAviso(e instanceof ErroApi && e.code === "password_required" ? SEM_SENHA : "Não foi possível salvar agora");
    },
    // Sem devolver a promessa: a trava do seletor é só o PUT, não a recarga. O `onSettled`
    // roda também se o `onMutate` falhar, então a trava nunca fica presa.
    onSettled: () => { voando.current = false; qc.invalidateQueries({ queryKey: perfilQuery.queryKey }); },
  });

  // Um PUT por vez: com dois em voo o mais lento chegaria por último e gravaria o penúltimo.
  const choose = (p: string) => {
    if (voando.current || m.isPending) return;
    voando.current = true;
    m.mutate(p as Escolha);
    document.getElementById("board-profile")?.focus();
  };
  const add = (id: string) => {
    const next = [...ids, id];
    saveLayout(shown, next);
    setIds(next);
    setVersion((v) => v + 1);
    setSaid(`${ITEM.get(id)!.label} adicionado ao fim do painel`);
  };
  const restore = () => { saveLayout(shown, null); setIds(layoutOf(shown, plan)); setVersion((v) => v + 1); };

  if (!q.data) {
    return (
      <section className="board" aria-label="Seu painel">
        {q.isPending ? <p role="status" className="faint">Carregando…</p> : (
          <div className="empty" role="alert">
            <p>Não deu para carregar o seu painel.</p>
            <button type="button" className="btn retry btn-ghost" onClick={() => q.refetch()}>Tentar de novo</button>
          </div>
        )}
      </section>
    );
  }

  return (
    <section className="board" data-editing={s.editing || undefined} data-fit={columns === 1 || undefined} aria-label="Seu painel">
      <div className="board-head">
        <ProfileSelect value={shown} busy={m.isPending} onPick={choose} />
        <p className="board-hint" aria-live="polite">
          {s.editing
            ? <>Arraste os blocos para organizar. No celular, segure antes de arrastar. No teclado: <kbd>Alt</kbd> + setas.</>
            : <span className="faint">hoje é {hoje()}</span>}
        </p>
        <div className="board-actions">
          {s.editing && custom && <button type="button" className="btn btn-quiet" onClick={restore}>Restaurar padrão</button>}
          <button type="button" className="btn btn-ghost" aria-pressed={s.editing} onClick={() => set({ editing: !s.editing })}>
            <i className={`ph ${s.editing ? "ph-check" : "ph-pencil-simple"}`} aria-hidden="true" />
            {s.editing ? "Pronto" : "Organizar"}
          </button>
        </div>
      </div>
      <p className="board-aviso" aria-live="polite">{aviso}</p>
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
      {profile === null && <ProfilePicker onPick={choose} aviso={aviso} />}
    </section>
  );
}
