// O movimento do guia (parts/Guia.tsx) e a fonte única dos seus tempos. Tudo em WAAPI, que o
// 1 ms global do base.css (reduce) não alcança: o portão do reduce é `calmo()`. Safari 14:
// sempre primeiro e último quadro, só `transform`, inclinação em número (var() em quadro de
// WAAPI não é confiável lá) e nunca a promise `finished` (o cancel do Esc a rejeitaria).
export const TEMPO = { voo: 1000, festa: 1600, pausa: 800, aperto: 220, troca: 500 };

export const calmo = () => !matchMedia("(prefers-reduced-motion: reduce)").matches;
// Voo e aperto somem com reduce; a pausa e a troca de tela não (decisão do dono, D4).
export const dura = (k: "voo" | "aperto") => (calmo() ? TEMPO[k] : 0);

const minhas = new WeakMap<Element, Animation>();
function animar(el: Element, quadros: Keyframe[], duration: number, easing = "ease-out") {
  minhas.get(el)?.cancel();
  minhas.delete(el);
  if (!calmo()) return;
  minhas.set(el, el.animate(quadros, { duration, easing }));
}
export const parar = (el: Element | null | undefined) => { if (el) { minhas.get(el)?.cancel(); minhas.delete(el); } };
// Lido a cada quadro: com o voo no ar o véu fica inteiro e sem anel.
export const voando = (el: Element | null) => {
  const s = el && minhas.get(el)?.playState;
  return s === "running" || s === "paused";
};

const centro = (r: DOMRect) => [r.left + r.width / 2, r.top + r.height / 2];
export const tiltDe = (el: HTMLElement) => parseFloat(el.style.getPropertyValue("--tilt")) || 0;

// FLIP: o elemento já está no destino (left/top); anima de onde estava (`de`, o retângulo de
// antes) até lá. Com `tiltA` (o Piggy) em arco, inclinando; sem (o balão), em linha reta. O
// left/top segue a mira a cada quadro: o transform é relativo a ela. Destino pelo left/top
// gravado, não pelo retângulo, que traria o voo anterior e a inclinação junto. Até 24 px não
// voa: devolve se voou.
export function voar(el: HTMLElement, de: DOMRect, tiltA?: number) {
  const [ax, ay] = centro(de);
  const dx = ax - (parseFloat(el.style.left) + el.offsetWidth / 2), dy = ay - (parseFloat(el.style.top) + el.offsetHeight / 2);
  if (Math.hypot(dx, dy) <= 24) return false;
  const arco = tiltA == null ? 0 : Math.min(120, Math.hypot(dx, dy) / 3);
  const rot = (a: number) => (tiltA == null ? "" : ` rotate(${a}deg)`);
  const t = tiltDe(el);
  animar(el, [
    { transform: `translate(${dx}px, ${dy}px)${rot(tiltA ?? 0)}` },
    { offset: 0.5, transform: `translate(${dx / 2}px, ${dy / 2 - arco}px)${rot(t)}` },
    { transform: `translate(0px, 0px)${rot(t)}` },
  ], TEMPO.voo, "cubic-bezier(.45,0,.2,1)");
  return true;
}

// A comemoração: o Piggy pula no lugar.
export function pular(el: HTMLElement) {
  const t = `rotate(${tiltDe(el)}deg)`;
  animar(el, [
    { transform: t },
    { offset: 0.3, transform: "translateY(-16px) rotate(-10deg) scale(1.12)" },
    { offset: 0.55, transform: "translateY(0px) rotate(10deg) scale(1)" },
    { offset: 0.75, transform: "translateY(-6px) rotate(-4deg) scale(1)" },
    { transform: t },
  ], 760);
}

// O Piggy aperta a aba: ele desce um pouco e a aba afunda.
export function apertar(piggy: HTMLElement, aba: HTMLElement) {
  const t = tiltDe(piggy);
  animar(piggy, [{ transform: `rotate(${t}deg)` }, { offset: 0.5, transform: `translateY(6px) rotate(${t}deg)` }, { transform: `rotate(${t}deg)` }], TEMPO.aperto);
  animar(aba, [{ transform: "scale(1)" }, { offset: 0.5, transform: "scale(0.9)" }, { transform: "scale(1)" }], TEMPO.aperto);
}

// A troca de tela que o guia faz dura TEMPO.troca (styles/guia.css); a da pessoa, não.
export function troca(liga: boolean) {
  const h = document.documentElement;
  if (!liga) { h.removeAttribute("data-guia-troca"); return; }
  h.style.setProperty("--guia-troca", `${TEMPO.troca}ms`);
  h.setAttribute("data-guia-troca", "");
}
