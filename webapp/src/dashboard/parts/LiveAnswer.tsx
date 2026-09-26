import { useContext, useEffect, useMemo, useRef, useState, type FocusEvent, type MouseEvent, type ReactNode } from "react";
import { ActionsContext, type Actions } from "../lib/actions";
import { apply, get as getGlobal, set as setGlobal } from "../lib/store.js";
import type { Block } from "../lib/topics";
import type { DashState } from "../lib/types";
import { go, href, type Path } from "../router";
import { useDash } from "../useDash";
import { FrameScope } from "./Frame";
import { Ledger } from "./Ledger";

const MAX = 340; // altura do bloco na visão compacta (px)

// O estado dos blocos tem a vida da conversa (lib/conversation.ts): sobrevive à troca de
// página, que desmonta o chat, e some ao recarregar. Chave: o escopo da resposta e o id do bloco.
const answers = new Map<string, { s: DashState; ledger: boolean }>();
const opened = new Set<string>();

// Bloco da resposta na visão compacta: corta em MAX e esmaece embaixo; "Ver tudo" abre no
// lugar. O foco do teclado num controle escondido pelo corte também abre. `close` põe
// "Fechar extrato" na mesma linha (só o extrato aberto por "Ver N lançamentos").
function Compact({ id, close, children }: { id: string; close?: (e: MouseEvent<HTMLButtonElement>) => void; children: ReactNode }) {
  const box = useRef<HTMLDivElement>(null);
  const [tall, setTall] = useState(false);
  const [open, setOpen] = useState(() => opened.has(id));
  useEffect(() => { if (open) opened.add(id); else opened.delete(id); }, [id, open]);
  useEffect(() => {
    const inner = box.current?.firstElementChild as HTMLElement | null;
    if (!inner) return;
    const ro = new ResizeObserver(() => setTall(inner.offsetHeight > MAX));
    ro.observe(inner);
    return () => ro.disconnect();
  }, []);
  const cut = tall && !open;
  const onFocus = (e: FocusEvent<HTMLDivElement>) => {
    const b = e.currentTarget;
    // Posição no conteúdo, não na tela: o navegador pode ter rolado o bloco até o foco.
    const bottom = e.target.getBoundingClientRect().bottom - b.getBoundingClientRect().top + b.scrollTop;
    if (cut && bottom > b.clientHeight) { setOpen(true); b.scrollTop = 0; }
  };
  return (
    <>
      <div className="panel msg-block" id={id} ref={box} data-cut={cut || undefined} style={cut ? { maxHeight: MAX } : undefined} onFocus={onFocus}>{children}</div>
      {(tall || close) && (
        <div className="msg-foot">
          {tall && <button type="button" className="link msg-more" aria-expanded={open} aria-controls={id} onClick={() => setOpen(!open)}>{open ? "Mostrar menos" : "Ver tudo"}</button>}
          {close && <button type="button" className="link msg-close" onClick={close}>Fechar extrato</button>}
        </div>
      )}
    </>
  );
}

// Os blocos de uma resposta do Piggy, vivos: cada resposta tem o próprio estado (começa em
// `s0`), e mexer nela não mexe no painel nem nas outras respostas. "Ver N lançamentos"
// abre o extrato aqui mesmo; "Abrir no painel" leva o estado da resposta para a página.
// A simulação não é da resposta: nenhum bloco da conversa a escreve, então todos leem a do
// Simulador, a atual (senão a resposta mostraria uma simulação que o usuário já desfez).
export function LiveAnswer({ blocks, s0, page }: { blocks: Block[]; s0: DashState; page: Path }) {
  const scope = useContext(FrameScope);
  const kept = answers.get(scope);
  const [s, setS] = useState(kept?.s ?? s0);
  const [ledger, setLedger] = useState(kept?.ledger ?? false);
  const { sim } = useDash();
  const ref = useRef(s); // lido síncrono (o `get` do Bills), antes do próximo render
  const openLink = useRef<HTMLAnchorElement>(null);
  useEffect(() => { answers.set(scope, { s, ledger }); }, [scope, s, ledger]);
  // Em dois passos: o `set` zera o dia quando o mês muda no mesmo patch. A origem só vale
  // com o extrato aberto (store.js), então só vai junto para o extrato.
  const toPanel = (p: Path) => {
    const l = ref.current;
    setGlobal({ month: l.month });
    setGlobal({ horizon: l.horizon, filter: { ...l.filter, source: p === "/lancamentos" ? l.filter.source : "todos" }, highlight: l.highlight });
  };
  const actions = useMemo<Actions>(() => {
    const set = (patch: Partial<DashState>) => { ref.current = apply(ref.current, patch); setS(ref.current); };
    return {
      get: () => ({ ...ref.current, sim: getGlobal().sim }),
      set,
      setFilter: (patch) => set({ filter: { ...ref.current.filter, ...patch } }),
      go: (p) => { if (p === "/lancamentos") setLedger(true); else { toPanel(p); go(p); } },
    };
  }, []);

  // O foco no botão que some iria para o <body>: passa para "Abrir no painel", que fica.
  const closeLedger = (e: MouseEvent<HTMLButtonElement>) => {
    if (document.activeElement === e.currentTarget) openLink.current?.focus();
    setLedger(false);
  };
  const view = { ...s, sim };
  const nodes = blocks.map((b) => b(view));
  if (ledger) nodes.push(<Ledger s={view} />);
  // Bloco que o usuário acabou de abrir (detalhe, extrato) rola até o topo dele. Ao montar
  // (resposta nova, ou a volta à conversa com o estado guardado) só registra, não rola.
  // Suave, instantâneo com movimento reduzido e abaixo da barra de cima: o `html` já cuida
  // (scroll-behavior e scroll-padding-top em base.css e shell.css).
  const shown = nodes.map((n) => !!n);
  const was = useRef<boolean[] | null>(null);
  useEffect(() => {
    const prev = was.current;
    was.current = shown;
    const i = prev ? shown.findIndex((on, k) => on && !prev[k]) : -1;
    if (i >= 0) document.getElementById(`${scope}b${i}`)?.scrollIntoView({ block: "start" });
  });
  return (
    <ActionsContext.Provider value={actions}>
      {nodes.map((n, i) => n && <Compact key={i} id={`${scope}b${i}`} close={i === blocks.length ? closeLedger : undefined}>{n}</Compact>)}
      <a className="link msg-open" ref={openLink} href={href(page)} onClick={() => toPanel(page)}>Abrir no painel<i className="ph ph-arrow-right" aria-hidden="true" /></a>
    </ActionsContext.Provider>
  );
}
