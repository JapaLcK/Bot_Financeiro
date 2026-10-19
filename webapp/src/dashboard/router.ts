import { useSyncExternalStore } from "react";
import { flushSync } from "react-dom";
import { DEMO } from "./lib/v2";

// Rotas por hash (#/gastos): o protótipo abre de qualquer servidor estático, e cada
// página tem endereço próprio (voltar do navegador e link compartilhado funcionam).
export const ROUTES = [
  { path: "/", label: "Resumo", short: "Resumo", icon: "ph-house", title: "Resumo" },
  { path: "/previsao", label: "Previsão", short: "Previsão", icon: "ph-chart-line-up", title: "Quanto vai sobrar" },
  { path: "/gastos", label: "Para onde vai", short: "Gastos", icon: "ph-chart-bar", title: "Para onde vai o dinheiro" },
  { path: "/assinaturas", label: "Assinaturas", short: "Assinaturas", icon: "ph-arrows-clockwise", title: "Assinaturas" },
  { path: "/simulador", label: "Simulador", short: "Simulador", icon: "ph-lightning", title: "Simulador" },
  { path: "/metas", label: "Metas", short: "Metas", icon: "ph-target", title: "Metas" },
  { path: "/patrimonio", label: "Patrimônio", short: "Patrimônio", icon: "ph-wallet", title: "Patrimônio" },
  { path: "/lancamentos", label: "Lançamentos", short: "Extrato", icon: "ph-receipt", title: "Lançamentos" },
  { path: "/ferramentas", label: "Ferramentas", short: "Ferramentas", icon: "ph-wrench", title: "Ferramentas" },
  { path: "/piggy", label: "Piggy", short: "Piggy", icon: "ph-chat-circle", title: "Converse com o Piggy" },
  // A seção "Conta" da barra lateral (fora do RAIL): o Perfil e a Cobrança (conta/Cobranca.tsx, lê /billing/subscription) já são páginas de verdade; as Configurações mostram a Segurança (conta/Seguranca.tsx), os dispositivos conectados (conta/Sessoes.tsx), as Notificações (conta/Notificacoes.tsx) e o link para a /settings; os Bancos conectados são página de verdade (conta/Bancos.tsx).
  { path: "/conta/perfil", label: "Perfil", short: "Perfil", icon: "ph-user", title: "Perfil" },
  { path: "/conta/cobranca", label: "Cobrança", short: "Cobrança", icon: "ph-credit-card", title: "Cobrança" },
  { path: "/conta/configuracoes", label: "Configurações", short: "Configurações", icon: "ph-gear", title: "Configurações" },
  { path: "/conta/bancos", label: "Bancos conectados", short: "Bancos", icon: "ph-bank", title: "Bancos conectados" },
] as const;

export type Path = (typeof ROUTES)[number]["path"];
// No celular o Piggy fica no meio e o "Menu" (parts/MenuTelas.tsx) fecha a barra: toda tela que
// não está aqui (Previsão, Metas, Simulador…) abre por ele, e a Ajuda também.
export const TABBAR: Path[] = ["/", "/gastos", "/piggy", "/lancamentos"];
// O Piggy não entra no menu lateral: no desktop o acesso é a barra de conversa.
export const CONTA: Path[] = ["/conta/perfil", "/conta/cobranca", "/conta/configuracoes", "/conta/bancos"];
export const RAIL: Path[] = ROUTES.map((r) => r.path).filter((p) => p !== "/piggy" && !CONTA.includes(p));
// O protótipo (DEMO) não tem conta: as rotas da Conta não existem nele (nem hash, nem ⌘K).
export const ATIVAS = ROUTES.filter((r) => !(DEMO && CONTA.includes(r.path)));
// Páginas que mostram só o presente (ignoram o mês escolhido): o topbar esconde o seletor.
// A previsão real é de hoje em diante; a do protótipo segue o mês escolhido.
export const NO_MONTH: Path[] = ["/assinaturas", "/simulador", "/metas", "/patrimonio", "/ferramentas", "/piggy", ...CONTA, ...(DEMO ? [] : ["/previsao" as const])];

// Hash desconhecido (#/xyz) abre o Resumo e troca o endereço para "#/" sem criar entrada
// no histórico; hash vazio continua valendo como Resumo.
const current = (): Path => {
  const p = window.location.hash.replace(/^#/, "") || "/";
  const known = ATIVAS.find((r) => r.path === p)?.path;
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
