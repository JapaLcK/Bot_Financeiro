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
  // Subpágina da ferramenta "Vale a pena comprar?": fora do RAIL e do protótipo, como a Conta.
  { path: "/simulador/compra", label: "Vale a pena comprar?", short: "Comprar", icon: "ph-shopping-cart", title: "Vale a pena comprar?" },
  { path: "/metas", label: "Metas", short: "Metas", icon: "ph-target", title: "Metas" },
  { path: "/patrimonio", label: "Patrimônio", short: "Patrimônio", icon: "ph-wallet", title: "Patrimônio" },
  { path: "/lancamentos", label: "Lançamentos", short: "Extrato", icon: "ph-receipt", title: "Lançamentos" },
  // Fora do RAIL (menu lateral e Menu do celular): abre pelo card de Ferramentas e pelo ⌘K.
  { path: "/orcamento", label: "Orçamento doméstico", short: "Orçamento", icon: "ph-house", title: "Orçamento doméstico" },
  // Idem: fora do RAIL, abre pelo card de Ferramentas e pelo ⌘K.
  { path: "/orcamentos-categoria", label: "Orçamentos por categoria", short: "Orçamentos", icon: "ph-chart-pie", title: "Orçamentos por categoria" },
  // Fora do RAIL, como o Orçamento: abre pelo card de Ferramentas e pelo ⌘K.
  { path: "/milhao", label: "Progresso ao milhão", short: "Milhão", icon: "ph-trophy", title: "Progresso ao milhão" },
  // Fora do RAIL, como o Milhão: abre pelo card de Ferramentas e pelo ⌘K.
  { path: "/agentes", label: "Agentes", short: "Agentes", icon: "ph-robot", title: "Agentes" },
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
// O Piggy não entra no menu lateral: no desktop o acesso é a barra de conversa. O Orçamento doméstico, os Orçamentos por categoria e o Progresso ao milhão também não (só Ferramentas e ⌘K).
export const CONTA: Path[] = ["/conta/perfil", "/conta/cobranca", "/conta/configuracoes", "/conta/bancos"];
export const RAIL: Path[] = ROUTES.map((r) => r.path).filter((p) => p !== "/piggy" && p !== "/simulador/compra" && p !== "/orcamento" && p !== "/orcamentos-categoria" && p !== "/milhao" && p !== "/agentes" && !CONTA.includes(p));
// O protótipo (DEMO) não tem conta: as rotas da Conta não existem nele (nem hash, nem ⌘K).
export const ATIVAS = ROUTES.filter((r) => !(DEMO && (CONTA.includes(r.path) || r.path === "/simulador/compra")));
// Páginas que mostram só o presente (ignoram o mês escolhido): o topbar esconde o seletor.
// A previsão real é de hoje em diante; a do protótipo segue o mês escolhido.
export const NO_MONTH: Path[] = ["/assinaturas", "/simulador", "/simulador/compra", "/metas", "/patrimonio", "/orcamentos-categoria", "/milhao", "/agentes", "/ferramentas", "/piggy", ...CONTA, ...(DEMO ? [] : ["/previsao" as const])];

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
// Para onde vai "Conectar banco" fora da página Bancos. O protótipo (DEMO) não tem as rotas da Conta (ATIVAS): lá segue o destino antigo.
export const conectarBanco = () => (DEMO ? "/settings?view=open-finance" : href("/conta/bancos"));
export const go = (p: Path) => { window.location.hash = p; };
export const route = (p: Path) => ROUTES.find((r) => r.path === p)!;
