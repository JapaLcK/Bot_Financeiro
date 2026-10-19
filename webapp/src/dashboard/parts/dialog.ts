import { useEffect, useRef, type KeyboardEvent as TeclaReact } from "react";

// Safari < 15.4 não tem <dialog>: sem showModal/close/.open. Lá abre e fecha pelo
// atributo, com um fundo próprio (não há ::backdrop), o Esc tratado por quem usa, o foco
// preso nos controles `focus` enquanto aberto (o Tab circula entre eles e foco fora volta
// ao primeiro; sem `inert` antes do 15.5) e devolvido a quem abriu, como o
// showModal()/close() nativos fazem.
// ponytail: uma trava por vez; no fallback, abrir a paleta por cima do modal de perfil
// solta a trava do modal. Pilha de travas se um dia houver dois modais de verdade.
export const NATIVE = typeof HTMLDialogElement === "function" && typeof HTMLDialogElement.prototype.showModal === "function";
let back: HTMLElement | null = null;
let drop = () => {};
export const untrap = () => drop();
export const isOpen = (d: HTMLDialogElement | null) => !!d?.hasAttribute("open");

export const show = (d: HTMLDialogElement | null, focus: string) => {
  if (!d || isOpen(d)) return; // reaberto (dash:command com ele aberto) recapturaria o campo como `back`
  if (NATIVE) { d.showModal(); return; }
  drop();
  back = document.activeElement as HTMLElement | null;
  d.setAttribute("open", "");
  const keep = (e: Event) => {
    const all = [...d.querySelectorAll<HTMLElement>(focus)];
    let to = all[0];
    if (e.type === "keydown") {
      const k = e as KeyboardEvent;
      if (k.key !== "Tab") return;
      const at = all.indexOf(document.activeElement as HTMLElement);
      const next = at < 0 ? -1 : at + (k.shiftKey ? -1 : 1);
      if (next >= 0 && next < all.length) return; // o Tab anda sozinho entre os controles
      if (at >= 0) to = all[k.shiftKey ? all.length - 1 : 0];
    } else if (d.contains(e.target as Node)) return;
    e.preventDefault();
    to?.focus();
  };
  document.addEventListener("keydown", keep, true);
  document.addEventListener("focusin", keep);
  drop = () => { document.removeEventListener("keydown", keep, true); document.removeEventListener("focusin", keep); drop = () => {}; };
};

export const hide = (d: HTMLDialogElement | null) => {
  if (!d) return;
  if (NATIVE) d.close();
  else { drop(); d.removeAttribute("open"); (document.activeElement as HTMLElement | null)?.blur(); back?.focus(); }
};

// O esqueleto de modal dos formulários (LancamentoForm, MetaForm): abre ao montar, foco no
// primeiro controle, Esc no fallback, Tab preso no nativo, Cancel/clique no fundo fecham.
// O chamador espalha `props` no <dialog> e, sem NATIVE, põe o scrim `lanc-scrim` ao lado, com `onClick={fundo}`.
// O 2º clique de um duplo clique no botão que abriu o diálogo cai no fundo dele (::backdrop ou o scrim) e o
// fecharia na hora; o clique no fundo só vale passada esta janela.
const JANELA_DO_FUNDO = 400;
export const FOCUS = 'input:not(:disabled), select:not(:disabled), button:not(:disabled), [tabindex="-1"]';
// `travado`: enquanto devolver true, Esc, Cancelar, Fechar e clique no fundo não fecham (uma escrita em voo).
export function useDialogo(fechar: () => void, travado?: () => boolean) {
  const dlg = useRef<HTMLDialogElement>(null);
  const abertoEm = useRef(0);
  const desceu = useRef<EventTarget | null>(null); // onde o ponteiro desceu: soltar no fundo depois de apertar dentro do painel (arrastar a seleção) não é clique no fundo
  useEffect(() => {
    const d = dlg.current;
    abertoEm.current = performance.now();
    show(d, FOCUS);
    d?.querySelector<HTMLElement>(FOCUS)?.focus();
    const esc = (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape" && !NATIVE && isOpen(d)) { e.preventDefault(); if (!travado?.()) { hide(d); fechar(); } }
    };
    window.addEventListener("keydown", esc);
    return () => { window.removeEventListener("keydown", esc); if (isOpen(d)) hide(d); else untrap(); };
  }, []);
  const fecharDialog = () => { if (travado?.()) return; hide(dlg.current); fechar(); };
  const fundo = () => { if (performance.now() - abertoEm.current > JANELA_DO_FUNDO) fecharDialog(); };
  const props = {
    ref: dlg,
    role: NATIVE ? undefined : "dialog",
    "aria-modal": NATIVE ? undefined : true,
    onKeyDown: (e: TeclaReact<HTMLDialogElement>) => {
      if (!NATIVE || e.key !== "Tab") return;
      const all = [...e.currentTarget.querySelectorAll<HTMLElement>(FOCUS)];
      const at = all.indexOf(document.activeElement as HTMLElement);
      if ((e.shiftKey && at === 0) || (!e.shiftKey && at === all.length - 1)) { e.preventDefault(); all[e.shiftKey ? all.length - 1 : 0]?.focus(); }
    },
    onCancel: (e: { preventDefault: () => void }) => { e.preventDefault(); fecharDialog(); },
    // O Chromium só deixa o `cancel` ser vetado 2 vezes sem gesto do usuário; o 3º Esc fecha o <dialog> à força, com a
    // escrita em voo: aqui ele reabre (o React segue com o formulário montado) ou, fechado fora de voo, o estado acompanha.
    onClose: () => {
      const d = dlg.current;
      if (!travado || !d || d.open) return;
      if (travado()) d.showModal(); else fechar();
    },
    onPointerDown: (e: { target: EventTarget }) => { desceu.current = e.target; },
    onClick: (e: { target: EventTarget }) => {
      const nasceuDentro = desceu.current && desceu.current !== dlg.current; // sem ponteiro (click() programático) vale como antes
      desceu.current = null;
      if (e.target === dlg.current && !nasceuDentro) fundo();
    },
  };
  return { dlg, fecharDialog, fundo, props };
}
