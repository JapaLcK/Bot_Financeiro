import { useEffect, type RefObject } from "react";

// Teclado com o véu do guia (parts/Guia.tsx): o Tab circula entre o balão e o `furo` (o que se
// toca agora); foco que cai fora (o #page-title da troca de página, um clique do leitor de tela)
// volta para o título do balão. O foco que ficou num controle que o véu cobriu depois (a seta já
// clicada, na comemoração; a linha de categoria de onde o alvo migrou) não recebe tecla (fora
// Tab e Esc): ela vai para o título. Keydown e keyup, na captura.
const FOCAVEL = "a[href], button:not(:disabled), input:not(:disabled), [tabindex]:not([tabindex='-1'])";

export function useTecladoDoVeu(veu: boolean, balao: RefObject<HTMLElement | null>, furo: RefObject<HTMLElement | null>, prende: RefObject<boolean>) {
  useEffect(() => {
    if (!veu) return;
    prende.current = true;
    const titulo = () => document.getElementById("guia-titulo")?.focus({ preventScroll: true });
    const caixas = () => [balao.current, furo.current].filter((c): c is HTMLElement => !!c);
    const onTab = (e: KeyboardEvent) => {
      const a = document.activeElement;
      // Foco no body (o controle focado sumiu, ex. o Entendi, ou o clique caiu no véu, que não é
      // focável) deixa passar: no body nenhuma tecla aciona controle, e o Tab seguinte entra no
      // balão pela lista circular.
      if (e.key !== "Tab" && e.key !== "Escape" && a && a !== document.body && !caixas().some((c) => c.contains(a))) {
        e.preventDefault(); e.stopPropagation(); titulo();
        return;
      }
      if (e.key !== "Tab" || e.type !== "keydown") return;
      e.preventDefault();
      const l = caixas().flatMap((c) => [c, ...c.querySelectorAll<HTMLElement>(FOCAVEL)]).filter((x) => x.matches(FOCAVEL) && x.getClientRects().length > 0);
      const i = l.indexOf(document.activeElement as HTMLElement);
      const n = l[i < 0 ? (e.shiftKey ? l.length - 1 : 0) : (i + (e.shiftKey ? l.length - 1 : 1)) % l.length];
      if (n) n.focus(); else titulo();
    };
    const onFoco = (e: FocusEvent) => { if (prende.current && !caixas().some((c) => c.contains(e.target as Node))) titulo(); };
    const teclas = ["keydown", "keyup"] as const;
    teclas.forEach((t) => window.addEventListener(t, onTab, true));
    window.addEventListener("focusin", onFoco);
    return () => { prende.current = false; teclas.forEach((t) => window.removeEventListener(t, onTab, true)); window.removeEventListener("focusin", onFoco); };
  }, [veu]);
}
