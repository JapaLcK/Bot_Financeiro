// O movimento do guia (parts/Guia.tsx) e a fonte única dos seus tempos. O voo é a mola do
// product-tour que o dono trouxe (#728): o Piggy, o balão e o recorte aceso andam juntos nela,
// num quadro só, no rAF do guia (o mesmo que segue a mira). Pulo e aperto são WAAPI e nunca
// rodam com a mola no ar (a ida espera TEMPO.voo; a festa não troca a mira). Nada disso é CSS,
// e o 1 ms global do base.css (reduce) não o alcança: o portão do reduce é `calmo()`. Safari
// 14: a mola é só conta (o gerador do framer-motion, sem WAAPI); no WAAPI, sempre primeiro e
// último quadro, só `transform`, inclinação em número e nunca a promise `finished` (o cancel do
// Esc a rejeitaria).
import { spring } from "framer-motion";
import type { Caixa } from "./guia-posicao";

// `voo`: quanto a mola leva para assentar (dashboard_v2_guia_mola.test.mjs mede).
export const TEMPO = { voo: 550, festa: 1600, pausa: 800, aperto: 220, troca: 500 };
// Sem quique: o amortecimento passa do crítico.
const MOLA = spring({ keyframes: [0, 1], stiffness: 320, damping: 32, mass: 0.7 });

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

export const tiltDe = (el: HTMLElement) => parseFloat(el.style.getPropertyValue("--tilt")) || 0;

// Um voo (FLIP): de onde partem, relativo ao destino (o left/top gravado, que segue a mira a cada
// quadro), o Piggy [dx, dy, inclinação], o balão [dx, dy] e as quatro bordas do recorte aceso.
export interface Voo { t0: number; pg: number[]; arco: number; b: number[]; aceso: number[] | null }
const desloca = (el: HTMLElement, de: DOMRect) => [
  de.left + de.width / 2 - (parseFloat(el.style.left) + el.offsetWidth / 2),
  de.top + de.height / 2 - (parseFloat(el.style.top) + el.offsetHeight / 2),
];
const bordas = (c: Caixa) => [c.left, c.top, c.right, c.bottom];

// A mira mudou: grava de onde cada um parte. O recorte parte do último pintado (no 1º, nasce do
// centro do novo). Com reduce não há voo: tudo já no destino.
export function partir(pg: HTMLElement, de: DOMRect, tiltA: number, b: HTMLElement, deB: DOMRect, ultimo: Caixa | null, aceso: Caixa | null): Voo | null {
  if (!calmo()) return null;
  const [dx, dy] = desloca(pg, de), x = aceso && (aceso.left + aceso.right) / 2, y = aceso && (aceso.top + aceso.bottom) / 2;
  const de4 = ultimo ? bordas(ultimo) : [x, y, x, y];
  return { t0: performance.now(), pg: [dx, dy, tiltA], arco: Math.min(120, Math.hypot(dx, dy) / 3), b: desloca(b, deB), aceso: aceso && bordas(aceso).map((v, i) => de4[i]! - v) };
}

// Um quadro da mola: o Piggy (em arco, inclinando) e o balão (reto, surgindo: opacidade e escala
// 0,96 → 1) no ponto dela, e o recorte aceso desse instante. Devolve também se ainda voa.
export function quadro(v: Voo | null, pg: HTMLElement, b: HTMLElement, aceso: Caixa | null): [Caixa | null, boolean] {
  const s = v && MOLA.next(performance.now() - v.t0);
  const k = !v || s!.done ? 1 : s!.value, q = 1 - k;
  pg.style.transform = v && q ? `translate(${v.pg[0] * q}px, ${v.pg[1] * q - v.arco * 4 * k * q}px) rotate(${v.pg[2] * q + tiltDe(pg) * k}deg)` : "";
  b.style.transform = v && q ? `translate(${v.b[0] * q}px, ${v.b[1] * q}px) scale(${0.96 + 0.04 * k})` : "";
  b.style.opacity = v && q ? `${k}` : "";
  const d = q ? v?.aceso : null;
  return [aceso && d ? { ...aceso, left: aceso.left + d[0] * q, top: aceso.top + d[1] * q, right: aceso.right + d[2] * q, bottom: aceso.bottom + d[3] * q } : aceso, !!q];
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
