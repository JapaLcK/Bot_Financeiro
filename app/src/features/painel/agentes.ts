// Os agentes no @ do chat: catálogo da tela, acesso de cada um e a menção. Porta de webapp/src/dashboard/lib/agentes.ts
// (`app/` e `webapp/` são projetos npm separados: o catálogo e os textos ficam duplicados).
// O servidor decide tudo (plano, energia, cota); aqui só se agrupa a lista.
import type { z } from "zod";
import type { agentesSchema } from "@/api/schemas/agentes";

export type Agentes = z.output<typeof agentesSchema>;
// Fonte da tela: kind e nome iguais a core/services/agent_chat.py::DOMAINS.
export const AGENTES = [
  { kind: "xerife", nome: "Xerife", tema: "Gasto fora do padrão" },
  { kind: "reporter", nome: "Repórter", tema: "Resumo do mês" },
  { kind: "carteiro", nome: "Carteiro", tema: "Contas a vencer" },
  { kind: "detetive", nome: "Detetive", tema: "Assinaturas e duplicados" },
  { kind: "cofre", nome: "Banqueiro", tema: "Metas" },
  { kind: "barao", nome: "Barão", tema: "Renda fixa" },
  { kind: "faria_limer", nome: "Faria Limer", tema: "Carteira de investimentos" },
];
export const agenteDe = (kind: string) => AGENTES.find((a) => a.kind === kind);

export type Acesso = "ready" | "activate" | "no_energy" | "upgrade";
export const GRUPO: Record<Acesso, string> = { ready: "Agentes ativos", activate: "Ativar para conversar", no_energy: "Ativar para conversar", upgrade: "No PigBank+" };

export function acessoDe(d: Agentes, kind: string): Acesso | null {
  const c = d.catalog.find((x) => x.kind === kind);
  if (!c?.disponivel) return null;
  if (d.can_chat === false || d.can_activate === false) return "upgrade";
  const usada = Number(d.energy_used || 0), orcamento = Number(d.energy_budget || 0);
  if (d.energy_enabled && (usada > orcamento || (c.status !== "active" && usada + c.energy_cost > orcamento))) return "no_energy";
  return c.status === "active" ? "ready" : "activate";
}
/** Custo do agente e energia livre, só com o modelo de energia ligado. */
export function energiaDe(d: Agentes, kind: string) {
  const c = d.catalog.find((x) => x.kind === kind);
  return d.energy_enabled && c ? { custo: Number(c.energy_cost || 0), livre: Math.max(0, Number(d.energy_budget || 0) - Number(d.energy_used || 0)) } : null;
}

// --- A menção: "@Nome" no começo do texto ou depois de espaço/quebra (assim fulano@gmail.com não conta). ---
const sem = (t: string) => { const r = t.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase(); return r.length === 1 ? r : t; }; // Hangul vira 2–3 jamos: manter o tamanho
export const semAcento = (t: string) => t.split("").map(sem).join(""); // por letra: o tamanho não muda, os índices valem no texto original
/** Só quem tem `@` no começo ou depois de espaço pode ser menção (e só esse texto toca em `/agents`). */
export const TEM_ARROBA = /(^|\s)@/;
const MAIS_LONGO = [...AGENTES].sort((a, b) => b.nome.length - a.nome.length);

/** Toda menção válida (nome do catálogo, sem acento nem maiúscula, nome mais longo primeiro). Com `dados`, só os agentes disponíveis. */
export function mencoes(texto: string, dados?: Agentes) {
  const f = semAcento(texto), out: { ini: number; fim: number; kind: string }[] = [];
  for (const m of f.matchAll(new RegExp(TEM_ARROBA, "g"))) {
    const ini = m.index + m[1]!.length, resto = f.slice(ini + 1);
    const a = MAIS_LONGO.find((x) => resto.startsWith(semAcento(x.nome)) && !/^[a-z0-9_]/.test(resto.slice(x.nome.length)) && (!dados || acessoDe(dados, x.kind)));
    if (a) out.push({ ini, fim: ini + 1 + a.nome.length, kind: a.kind });
  }
  return out;
}
/** A PRIMEIRA menção decide o agente; `texto` é a mensagem sem esse token (espaços normalizados; vazio = só havia a menção). */
export function mencaoDe(texto: string, dados?: Agentes) {
  const m = mencoes(texto, dados)[0];
  if (!m) return null;
  const esq = texto.slice(0, m.ini).trimEnd(), dir = texto.slice(m.fim).trimStart();
  const msg = (esq + (/^[?!.,;:]/.test(dir) ? "" : " ") + dir).replace(/\s+/g, " ").trim();
  return { kind: m.kind, texto: /[\p{L}\d]/u.test(msg) ? msg : "" };
}
/** O token em edição: "@" + letras até o cursor, logo depois de espaço ou no começo. */
export function tokenEm(texto: string, cursor: number) {
  const m = /(^|\s)@(\p{L}*)$/u.exec(texto.slice(0, cursor));
  return m ? { ini: cursor - m[2]!.length - 1, q: semAcento(m[2]!) } : null;
}
/** Os agentes da lista do @: disponíveis, com o prefixo digitado (sem acento) e na ordem dos grupos; dentro do grupo, a do catálogo. */
export function itensMencao(d: Agentes, q: string) {
  const ordem = Object.keys(GRUPO);
  return AGENTES.flatMap((a) => { const acesso = acessoDe(d, a.kind); return acesso && semAcento(a.nome).startsWith(q) ? [{ ...a, acesso }] : []; })
    .sort((x, y) => ordem.indexOf(x.acesso) - ordem.indexOf(y.acesso));
}
