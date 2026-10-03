// Onde o Piggy e o balão do guia (parts/Guia.tsx) ficam. Âncora = o bloco do passo (elemento
// com `data-guia` que existe E está visível); alvo = o que se toca dentro dele (a seta do mês, uma
// linha de categoria, um chip de pergunta). Quem o Piggy mira em cada etapa decide o Guia.tsx.
import type { Passo } from "../lib/api-v2.gen";
import type { Path } from "../router";

// "alvo": o que se toca; "nav": a aba que leva de volta à tela; "ausente": a tela já desenhou
// os blocos e o dele não está (tirado em Organizar); "espera": a tela ainda carrega.
export type Tipo = "alvo" | "nav" | "ausente" | "espera";
export const ROTA: Record<Passo["tela"], Path> = { resumo: "/", gastos: "/gastos", piggy: "/piggy" };

const P = 44; // lado do Piggy
const M = 12; // margem da tela
const clamp = (v: number, a: number, b: number) => Math.max(a, Math.min(b, v));
const visivel = (sel: string) =>
  [...document.querySelectorAll<HTMLElement>(sel)].find((e) => e.getClientRects().length > 0) ?? null;
export const guia = (nome: string) => visivel(`[data-guia="${nome}"]`);
// A aba que leva à tela do passo. No desktop o Piggy não tem aba: perguntar na barra de
// conversa leva até a conversa (decisão do dono, D9).
export const aba = (p: Passo) => guia(`nav.${p.tela}`) ?? (p.tela === "piggy" ? guia(p.ancora) : null);

// `alvos` em ordem de preferência. Fora da tela do passo, a aba.
export function achar(p: Passo, path: Path, alvos: string[]): { el: HTMLElement | null; tipo: Tipo } {
  if (path !== ROTA[p.tela]) return { el: aba(p), tipo: "nav" };
  const el = guia(p.ancora) && (alvos.map(guia).find(Boolean) ?? null);
  if (el) return { el, tipo: "alvo" };
  return { el: null, tipo: document.querySelector("#main article.w") ? "ausente" : "espera" };
}

// Mira fora da vista (no Resumo o bloco pode estar mais abaixo): rola até ela uma vez. Devolve
// se a mira rola: a da barra de cima (a seta do mês) não, rolar a página não a traz, só tira o
// Saiu da tela.
export function trazer(el: HTMLElement) {
  if (el.closest(".topbar")) return false;
  const r = el.getBoundingClientRect();
  const topo = document.querySelector(".topbar")?.getBoundingClientRect().bottom ?? 0;
  if (r.top < topo || r.bottom > innerHeight) el.scrollIntoView({ block: "center" });
  return true;
}

// O Piggy encosta na quina de cima da mira (ou na de baixo, se a de cima ficou sob a
// barra), inclinado para ela. O balão vai na área livre (entre as barras e fora do menu
// lateral) sem encostar na âncora nem no Piggy: no desktop ao lado (direita, esquerda, em
// cima, embaixo); até 640px (o corte do Board) na largura toda, no pé, acima ou abaixo; sem
// rolar, também no topo da área livre. Nenhum cabe (âncora maior que a área livre, ex.: a lista
// de categorias): o balão vai para baixo e, uma vez por etapa (`rolar`), a página rola até a
// âncora terminar acima dele. Assim o
// balão nunca fica sobre o que o passo pede para tocar; nem sobre o que vem em `evita` (a
// âncora e o alvo do passo, quando o Piggy mira só um deles). Sem âncora: canto de baixo. Sem
// `piggy` (a tela ainda carrega), o Piggy fica onde está e só o balão se ajeita.
export function posicionar(el: HTMLElement | null, piggy: HTMLElement | null, balao: HTMLElement, rolar = false, evita: (HTMLElement | null)[] = []) {
  const vw = document.documentElement.clientWidth, vh = innerHeight;
  const fundo = (visivel(".tabbar")?.getBoundingClientRect().top ?? vh - 76) - M; // desktop: acima da barra de conversa
  const topo = (document.querySelector(".topbar")?.getBoundingClientRect().bottom ?? 0) + M;
  const esq = (visivel(".rail")?.getBoundingClientRect().right ?? 0) + M, dir = vw - M;
  balao.style.maxHeight = `${fundo - topo}px`;
  const w = balao.offsetWidth, h = balao.offsetHeight;
  const estreito = vw <= 640;
  let bx: number, by: number, px: number, py: number, tilt = 0;
  if (!el) {
    bx = estreito ? M : vw - w - 24;
    by = fundo - h;
    px = bx + 16;
    py = by - P + 8;
  } else {
    const r = el.getBoundingClientRect();
    const emCima = r.top - P + 8 >= topo - M;
    px = clamp(r.right - P * 0.8, M, vw - P - M);
    py = emCima ? r.top - P + 8 : r.bottom - 8;
    const meio = r.left + r.width / 2 - (px + P / 2);
    tilt = meio < -8 ? -12 : meio > 8 ? 12 : 0;
    // O que o balão não pode cobrir: a âncora e o Piggy.
    const o = { l: Math.min(r.left, px), t: Math.min(r.top, py), r: Math.max(r.right, px + P), b: Math.max(r.bottom, py + P) };
    const y = clamp(r.top, topo, fundo - h), x = estreito ? M : clamp(r.left, esq, dir - w);
    const baixo: [number, number] = [x, fundo - h], cima: [number, number] = [x, topo];
    const lados: [number, number][] = estreito
      ? [baixo, [x, o.t - 8 - h], [x, o.b + 16]]
      : [[o.r + 16, y], [o.l - 16 - w, y], [x, o.t - 8 - h], [x, o.b + 16], baixo];
    const fora = [o, ...evita.flatMap((e) => (e ? [e.getBoundingClientRect()] : [])).map((q) => ({ l: q.left, t: q.top, r: q.right, b: q.bottom }))];
    const livre = ([a, b]: [number, number]) => a >= esq && a + w <= dir && b >= topo && b + h <= fundo
      && fora.every((q) => a >= q.r || a + w <= q.l || b >= q.b || b + h <= q.t);
    // O topo da área livre só depois de tentar rolar: senão o balão pula de cima para baixo
    // entre o bloco e o alvo do mesmo passo.
    const lado = lados.find(livre) ?? (rolar ? undefined : [cima].find(livre));
    [bx, by] = lado ?? baixo;
    if (!lado && rolar) {
      // A âncora termina 16 px acima do balão; se não sobra lugar para o Piggy em cima dela,
      // ele desce para a quina de baixo e entra na conta.
      const alvo = by - 16, desce = r.height + P - 8 > alvo - (topo - M);
      scrollBy(0, r.bottom + (desce ? P - 8 : 0) - alvo);
      return posicionar(el, piggy, balao, false, evita);
    }
  }
  bx = clamp(bx, M, vw - w - M);
  by = clamp(by, topo, fundo - h);
  balao.style.left = `${bx}px`;
  balao.style.top = `${by}px`;
  if (!piggy) return;
  piggy.style.left = `${px}px`;
  piggy.style.top = `${py}px`;
  piggy.style.setProperty("--tilt", `${tilt}deg`);
}

// O véu (decisão do dono): escurece a tela menos o `toque` (o que se toca agora) e o `claro`
// (aceso, mas sem toque: o bloco que o guia apresenta, a aba antes da troca). O anel marca
// `anelEm`. Quatro faixas transparentes em volta do toque bloqueiam o resto; o escuro é um path
// `evenodd`, que comporta os dois furos. Sem toque, uma faixa cobre a tela inteira. O furo escuro
// é o retângulo exato do elemento, o mesmo das faixas: o que está aceso é o que se toca (fora o
// claro). O anel fica F px por fora.
const F = 4; // folga do anel em volta do alvo
const furo = (r: DOMRect, raio: number) => {
  const { left: x, top: y, width: w, height: h } = r, k = Math.min(raio, w / 2, h / 2);
  return `M${x + k} ${y}h${w - 2 * k}a${k} ${k} 0 0 1 ${k} ${k}v${h - 2 * k}a${k} ${k} 0 0 1 ${-k} ${k}h${2 * k - w}a${k} ${k} 0 0 1 ${-k} ${-k}v${2 * k - h}a${k} ${k} 0 0 1 ${k} ${-k}Z`;
};
const raioDe = (el: HTMLElement) => parseFloat(getComputedStyle(el).borderTopLeftRadius) || 0;
export function cobrir(toque: HTMLElement | null, claro: HTMLElement | null, anelEm: HTMLElement | null, faixas: HTMLElement[], sombra: SVGPathElement, anel: HTMLElement) {
  const vw = document.documentElement.clientWidth, vh = innerHeight;
  const a = toque?.getBoundingClientRect() ?? { left: 0, top: 0, right: 0, bottom: 0 };
  const caixas = [[0, 0, vw, a.top], [0, a.bottom, vw, vh - a.bottom], [0, a.top, a.left, a.bottom - a.top], [a.right, a.top, vw - a.right, a.bottom - a.top]];
  faixas.forEach((f, i) => {
    const [x, y, w, h] = caixas[i];
    Object.assign(f.style, { left: `${x}px`, top: `${y}px`, width: `${Math.max(0, w)}px`, height: `${Math.max(0, h)}px` });
  });
  const furos = [toque, claro].filter((e): e is HTMLElement => !!e).map((e) => furo(e.getBoundingClientRect(), raioDe(e)));
  sombra.setAttribute("d", `M0 0H${vw}V${vh}H0Z${furos.join("")}`);
  anel.hidden = !anelEm;
  if (!anelEm) return;
  const r = anelEm.getBoundingClientRect();
  Object.assign(anel.style, { left: `${r.left - F}px`, top: `${r.top - F}px`, width: `${r.width + 2 * F}px`, height: `${r.height + 2 * F}px`, borderRadius: `${raioDe(anelEm) + F}px` });
}
