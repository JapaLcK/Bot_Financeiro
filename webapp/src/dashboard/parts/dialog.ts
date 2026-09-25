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
