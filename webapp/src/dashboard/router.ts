import { useSyncExternalStore } from "react";
import { flushSync } from "react-dom";

// Rotas por hash (#/gastos): o protótipo abre de qualquer servidor estático, e cada
// página tem endereço próprio (voltar do navegador e link compartilhado funcionam).
export const ROUTES = [
  { path: "/", label: "Resumo", short: "Resumo", icon: "ph-house", title: "Resumo" },
  { path: "/previsao", label: "Previsão", short: "Previsão", icon: "ph-chart-line-up", title: "Quanto vai sobrar" },
  { path: "/gastos", label: "Para onde vai", short: "Gastos", icon: "ph-chart-bar", title: "Para onde vai o dinheiro" },
  { path: "/simulador", label: "Simulador", short: "Simulador", icon: "ph-lightning", title: "Simulador" },
  { path: "/metas", label: "Metas", short: "Metas", icon: "ph-target", title: "Metas e caixinhas" },
  { path: "/patrimonio", label: "Patrimônio", short: "Patrimônio", icon: "ph-wallet", title: "Patrimônio" },
  { path: "/lancamentos", label: "Lançamentos", short: "Extrato", icon: "ph-list", title: "Lançamentos" },
  { path: "/ferramentas", label: "Ferramentas", short: "Ferramentas", icon: "ph-wrench", title: "Ferramentas" },
  { path: "/piggy", label: "Piggy", short: "Piggy", icon: "ph-chat-circle", title: "Converse com o Piggy" },
] as const;

export type Path = (typeof ROUTES)[number]["path"];
// No celular o Piggy fica no meio; o Simulador sai daqui e segue em Ferramentas e no menu.
export const TABBAR: Path[] = ["/", "/gastos", "/piggy", "/metas", "/lancamentos"];
// O Piggy não entra no menu lateral: no desktop o acesso é a barra de conversa.
export const RAIL: Path[] = ROUTES.map((r) => r.path).filter((p) => p !== "/piggy");
// Páginas que mostram só o presente (ignoram o mês escolhido): o topbar esconde o seletor.
export const NO_MONTH: Path[] = ["/simulador", "/metas", "/patrimonio", "/ferramentas", "/piggy"];

// Hash desconhecido (#/xyz) abre o Resumo e troca o endereço para "#/" sem criar entrada
// no histórico; hash vazio continua valendo como Resumo.
const current = (): Path => {
  const p = window.location.hash.replace(/^#/, "") || "/";
  const known = ROUTES.find((r) => r.path === p)?.path;
  if (!known) history.replaceState(history.state, "", "#/");
  return (known ?? "/") as Path;
};

let path = current();
const subs = new Set<() => void>();
window.addEventListener("hashchange", () => {
  const next = current();
  if (next === path) return;
  const swap = () => { path = next; subs.forEach((f) => f()); };
  // Troca de página com View Transition quando o navegador tem; sem ela, troca direto.
  const vt = (document as Document & { startViewTransition?: (cb: () => void) => unknown }).startViewTransition;
  if (vt && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) vt.call(document, () => flushSync(swap));
  else swap();
  window.scrollTo(0, 0);
});

export const useRoute = () => useSyncExternalStore((f) => { subs.add(f); return () => subs.delete(f); }, () => path);
export const href = (p: Path) => `#${p}`;
export const go = (p: Path) => { window.location.hash = p; };
export const route = (p: Path) => ROUTES.find((r) => r.path === p)!;
