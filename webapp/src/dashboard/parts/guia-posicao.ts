// Onde o Piggy e o balão do guia (parts/Guia.tsx) ficam. Âncora = o bloco do passo (elemento
// com `data-guia` que existe E está visível); alvo = o que se toca dentro dele (a seta do mês, uma
// linha de categoria, um chip de pergunta). Fora da tela do passo, o Piggy aponta para a aba.
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
const guia = (nome: string) => visivel(`[data-guia="${nome}"]`);

// `alvos` em ordem de preferência. `mira` é onde o Piggy encosta: o alvo, ou a âncora quando o
// alvo está na barra de cima (a seta do mês), onde não cabe Piggy nem balão. Fora da tela, no
// desktop o Piggy não tem aba: perguntar na barra de conversa leva até a conversa.
export function achar(p: Passo, path: Path, alvos: string[]): { el: HTMLElement | null; mira: HTMLElement | null; tipo: Tipo } {
  if (path !== ROTA[p.tela]) {
    const el = guia(`nav.${p.tela}`) ?? (p.tela === "piggy" ? guia(p.ancora) : null);
    return { el, mira: el, tipo: "nav" };
  }
  const ancora = guia(p.ancora);
  const el = ancora && (alvos.map(guia).find(Boolean) ?? null);
  if (el) return { el, mira: el.closest(".topbar") ? ancora : el, tipo: "alvo" };
  return { el: null, mira: null, tipo: document.querySelector("#main article.w") ? "ausente" : "espera" };
}

// Mira fora da vista (no Resumo o bloco pode estar mais abaixo): rola até ela uma vez.
export function trazer(el: HTMLElement) {
  const r = el.getBoundingClientRect();
  const topo = document.querySelector(".topbar")?.getBoundingClientRect().bottom ?? 0;
  if (r.top < topo || r.bottom > innerHeight) el.scrollIntoView({ block: "center" });
}

// O Piggy encosta na quina de cima da mira (ou na de baixo, se a de cima ficou sob a
// barra), inclinado para ela. O balão vai na área livre (entre as barras e fora do menu
// lateral) sem encostar na âncora nem no Piggy: no desktop ao lado (direita, esquerda, em
// cima, embaixo); até 640px (o corte do Board) na largura toda, embaixo ou acima. Nenhum
// cabe (âncora maior que a área livre, ex.: a lista de categorias): o balão vai para baixo
// e, uma vez por passo (`rolar`), a página rola até a âncora terminar acima dele. Assim o
// balão nunca fica sobre o que o passo pede para tocar. Sem âncora: canto de baixo.
export function posicionar(el: HTMLElement | null, piggy: HTMLElement, balao: HTMLElement, rolar = false) {
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
    const baixo: [number, number] = [x, fundo - h];
    const lados: [number, number][] = estreito
      ? [baixo, [x, o.t - 8 - h]]
      : [[o.r + 16, y], [o.l - 16 - w, y], [x, o.t - 8 - h], [x, o.b + 16], baixo];
    const livre = ([a, b]: [number, number]) => a >= esq && a + w <= dir && b >= topo && b + h <= fundo
      && (a >= o.r || a + w <= o.l || b >= o.b || b + h <= o.t);
    const lado = lados.find(livre);
    [bx, by] = lado ?? baixo;
    if (!lado && rolar) {
      // A âncora termina 16 px acima do balão; se não sobra lugar para o Piggy em cima dela,
      // ele desce para a quina de baixo e entra na conta.
      const alvo = by - 16, desce = r.height + P - 8 > alvo - (topo - M);
      scrollBy(0, r.bottom + (desce ? P - 8 : 0) - alvo);
      return posicionar(el, piggy, balao);
    }
  }
  bx = clamp(bx, M, vw - w - M);
  by = clamp(by, topo, fundo - h);
  balao.style.left = `${bx}px`;
  balao.style.top = `${by}px`;
  piggy.style.left = `${px}px`;
  piggy.style.top = `${py}px`;
  piggy.style.setProperty("--tilt", `${tilt}deg`);
}
