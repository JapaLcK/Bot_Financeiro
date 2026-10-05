// O movimento do guia (parts/Guia.tsx) e a fonte única dos seus tempos. O voo é a mola do
// product-tour que o dono trouxe (#728): o Piggy, o balão e o recorte aceso andam juntos nela,
// num quadro só, no rAF do guia (o mesmo que segue a mira). O pulo é WAAPI e nunca roda com a
// mola no ar (a festa não troca a mira). Nada disso é CSS, e o 1 ms global do base.css
// (reduce) não o alcança: o portão do reduce é `calmo()`. Safari
// 14: a mola é só conta (o gerador do framer-motion, sem WAAPI); no WAAPI, sempre primeiro e
// último quadro, só `transform`, inclinação em número e nunca a promise `finished` (o cancel do
// Esc a rejeitaria).
import { spring } from "framer-motion";
import type { Caixa } from "./guia-posicao";

// `voo`: quanto a mola leva para assentar (dashboard_v2_guia_mola.test.mjs prova que assentou).
export const TEMPO = { voo: 1000, festa: 1600 };
// Calma (dono: "ritmo calmo, voo ~1 s") e sem quique: amortecimento crítico, 18 = 2·√(81·1).
// Medido em 2026-10-03, remeça se mexer nos números: em webapp/,
// `calcGeneratorDuration(spring({ keyframes: [0, 1], stiffness: 81, damping: 18, mass: 1 }), 1)`
// (framer-motion) dá 1000 ms; o maior valor em 0..3000 ms, de 1 em 1, é 1 (nenhum excesso).
const MOLA = spring({ keyframes: [0, 1], stiffness: 81, damping: 18, mass: 1 });

export const calmo = () => !matchMedia("(prefers-reduced-motion: reduce)").matches;

const minhas = new WeakMap<Element, Animation>();
function animar(el: Element, quadros: Keyframe[], duration: number, easing = "ease-out") {
  minhas.get(el)?.cancel();
  minhas.delete(el);
  if (!calmo()) return;
  minhas.set(el, el.animate(quadros, { duration, easing }));
}

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
// centro do novo). Com reduce não há voo: tudo já no destino. A entrada do Piggy (CSS
// `guia-entra`, 620 ms) acaba aqui: no ar, ela taparia o transform da mola, e o Piggy
// saltaria para o destino e de volta ao fim dela.
export function partir(pg: HTMLElement, de: DOMRect, tiltA: number, b: HTMLElement, deB: DOMRect, ultimo: Caixa | null, aceso: Caixa | null): Voo | null {
  if (!calmo()) return null;
  pg.getAnimations().forEach((a) => { if (a instanceof CSSAnimation) a.finish(); });
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
