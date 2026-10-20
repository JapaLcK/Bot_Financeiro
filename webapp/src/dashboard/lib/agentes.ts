// Os agentes no @ da barra do Piggy (parts/AskBar.tsx): o catálogo da tela, o acesso de cada um e as rotas
// que JÁ existem em frontend/routes/agents.py (a prateleira da /agents usa as mesmas). O servidor decide
// tudo (plano, energia, cota); o painel só agrupa a lista e mostra o que ele responde.
import { queryOptions } from "@tanstack/react-query";
import { uidValido } from "../conta/api";
import { textos } from "./bancos";
import { DEMO, chamar, escrever, meQuery } from "./v2";

// Fonte da tela: kind e nome iguais a core/services/agent_chat.py::DOMAINS (tests/test_painel_agentes_catalogo.py confere).
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

// As frases do "enviando" (parts/Carregando.tsx), em ordem fixa. Só dizem o que o agente está FAZENDO: o servidor não informa etapas,
// então nenhuma afirma uma etapa concluída. Depois das do agente vêm as comuns, e a lista recomeça.
const FRASES_COMUNS = ["Desenrolando o novelo…", "Organizando a resposta…"];
const FRASES: Record<string, string[]> = {
  faria_limer: ["Abrindo a carteira…", "Pulando entre os números…", "Olhando ações e renda fixa…"],
  barao: ["Olhando a renda fixa…", "Procurando dinheiro parado…"],
  detetive: ["Procurando pistas nos lançamentos…", "Comparando cobranças parecidas…"],
  carteiro: ["Separando as contas…", "Conferindo os vencimentos…"],
  reporter: ["Juntando entradas e saídas…", "Montando o resumo…"],
  cofre: ["Abrindo as caixinhas…", "Conferindo as metas…"],
  xerife: ["Patrulhando os gastos…", "Comparando com seus limites…"],
};
export const frasesDe = (kind: string) => [...(FRASES[kind] ?? []), ...FRASES_COMUNS];

export interface Agentes {
  catalog: { kind: string; nome: string; disponivel: boolean; status: string | null; energy_cost: number }[];
  can_chat?: boolean; can_activate?: boolean; energy_enabled: boolean; energy_budget: number; energy_used: number;
}
export type Acesso = "ready" | "activate" | "no_energy" | "upgrade";

// A mesma fórmula do chat da /agents (frontend/dashboard-agent-chat.js); aqui só agrupa a lista, quem decide é o servidor.
export function acessoDe(d: Agentes, kind: string): Acesso | null {
  const c = d.catalog.find((x) => x.kind === kind);
  if (!c?.disponivel) return null;
  if (d.can_chat === false || d.can_activate === false) return "upgrade";
  const usada = Number(d.energy_used || 0), orcamento = Number(d.energy_budget || 0);
  if (d.energy_enabled && (usada > orcamento || (c.status !== "active" && usada + c.energy_cost > orcamento))) return "no_energy";
  return c.status === "active" ? "ready" : "activate";
}
// Custo do agente e energia livre, só com o modelo de energia ligado.
export function energiaDe(d: Agentes, kind: string) {
  const c = d.catalog.find((x) => x.kind === kind);
  return d.energy_enabled && c ? { custo: Number(c.energy_cost || 0), livre: Math.max(0, Number(d.energy_budget || 0) - Number(d.energy_used || 0)) } : null;
}

// --- Demonstração (protótipo): sem /auth nem /agents; "Ativar e responder" troca o estado só na memória. ---
const CUSTO: Record<string, number> = { xerife: 2, reporter: 1, carteiro: 1, detetive: 3, cofre: 2, barao: 3, faria_limer: 4 };
const ATIVOS: Record<string, string[]> = { plus: ["xerife", "reporter", "detetive"], pro: ["detetive", "barao", "xerife"] };
const ENERGIA: Record<string, number> = { plus: 9, pro: 20 };
let ativosDemo: Set<string> | undefined;
function demo(): Agentes {
  const plano = String(window.PIGBANK_DEMO_PLAN);
  const ativos = ativosDemo || (ativosDemo = new Set(ATIVOS[plano] ?? []));
  const pago = plano in ENERGIA;
  return {
    catalog: AGENTES.map((a) => ({ kind: a.kind, nome: a.nome, disponivel: true, status: ativos.has(a.kind) ? "active" : null, energy_cost: CUSTO[a.kind] })),
    can_chat: pago, can_activate: pago, energy_enabled: true, energy_budget: ENERGIA[plano] ?? 0,
    energy_used: [...ativos].reduce((s, k) => s + CUSTO[k], 0),
  };
}
const RESPOSTA_DEMO: Record<string, string> = {
  xerife: "Resposta de exemplo: nenhum gasto fugiu do seu padrão esta semana.",
  reporter: "Resposta de exemplo: neste mês entrou mais do que saiu, e sobrou uma boa parte do que você recebeu.",
  carteiro: "Resposta de exemplo: você tem duas contas a vencer nos próximos 7 dias.",
  detetive: "Resposta de exemplo: encontrei duas cobranças iguais no mesmo dia. Você reconhece as duas?",
  cofre: "Resposta de exemplo: sua meta está na metade do caminho.",
  barao: "Resposta de exemplo: tem dinheiro parado que renderia mais em renda fixa.",
  faria_limer: "Resposta de exemplo: sua carteira está concentrada em poucos ativos.",
};

export const agentesQuery = (uid: unknown, ativo = true) => queryOptions({
  queryKey: ["agentes", uid],
  queryFn: async ({ signal }) => {
    if (DEMO) return demo();
    const d = await chamar<Agentes>(`/agents/${uid}`, { headers: { Accept: "application/json" }, signal });
    if (!Array.isArray(d?.catalog)) throw new Error("contrato"); // lista fora do contrato: a lista do @ não abre
    return d;
  },
  staleTime: 0, refetchOnWindowFocus: false, networkMode: "always", retry: meQuery.retry,
  enabled: ativo && (DEMO || uidValido(uid)), // o uid vem só da sessão (contaQuery), nunca de input
});

export function ativar(uid: number, kind: string) {
  if (DEMO) { demo(); ativosDemo!.add(kind); return Promise.resolve({ ok: true }); }
  return escrever<{ ok: boolean }>("POST", `/agents/${uid}/${kind}/activate`, {});
}
export interface Redirect { kind: string; name: string; question: string; access: string; needs_data?: boolean }
export interface RespostaChat { reply: string; context: string | null; redirects?: Redirect[]; card?: unknown }
/** O card com os dados do tema, abaixo da resposta (parts/CardAgente.tsx). O servidor só diz QUAL; tipo desconhecido não desenha nada. */
export type CardAgente = { tipo: "investido" | "renda_fixa" | "assinaturas" } | { tipo: "orcamentos"; mes: string } | { tipo: "resumo_mes"; mes: string; motivos: string[] };
export const MES = /^[1-9][0-9]{3}-(0[1-9]|1[0-2])$/; // o PADRAO_MES do servidor: o mês entra na URL do GET
export const cardDe = (v: unknown): CardAgente | null => {
  const c = v && typeof v === "object" ? (v as { tipo?: unknown; mes?: unknown; motivos?: unknown }) : {};
  if (c.tipo === "investido" || c.tipo === "renda_fixa" || c.tipo === "assinaturas") return { tipo: c.tipo };
  if (c.tipo === "orcamentos") return typeof c.mes === "string" && MES.test(c.mes) ? { tipo: "orcamentos", mes: c.mes } : null;
  // `motivos`: os avisos que o agente deu sobre o mês (lista de texto; fora disso, nenhum aviso).
  return c.tipo === "resumo_mes" && typeof c.mes === "string" && MES.test(c.mes) ? { tipo: "resumo_mes", mes: c.mes, motivos: textos(c.motivos) ? c.motivos as string[] : [] } : null;
};
/** Pergunta vinda do botão "Perguntar ao X": o servidor não a reclassifica, e o agente sugerido responde. */
export interface Encaminhada { needs_data: boolean }
export function conversar(uid: number, kind: string, message: string, context: string | null, encaminhada?: Encaminhada) {
  if (DEMO) return Promise.resolve<RespostaChat>({ reply: RESPOSTA_DEMO[kind], context: null });
  return escrever<RespostaChat>("POST", `/agents/${uid}/${kind}/chat`,
    encaminhada ? { message, context, forwarded: true, needs_data: encaminhada.needs_data } : { message, context });
}

// --- A menção: "@Nome" no começo do texto ou depois de espaço/quebra (assim fulano@gmail.com não conta). ---
const sem = (t: string) => t.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
/** O texto sem acento e minúsculo para comparar (a marca solta de texto colado, "e" + U+0301, também some) e, para cada
 * posição dele, a posição no original: as menções se medem no texto dobrado e se recortam no original (Codex no #1157). */
function dobra(t: string) {
  let f = "";
  const de: number[] = [];
  for (let i = 0; i < t.length; i++) for (const c of sem(t[i])) { f += c; de.push(i); }
  de.push(t.length);
  return { f, de };
}
export const semAcento = (t: string) => dobra(t).f;
const MAIS_LONGO = [...AGENTES].sort((a, b) => b.nome.length - a.nome.length).map((a) => ({ ...a, chave: semAcento(a.nome) }));

/** Toda menção válida (nome do catálogo, sem acento nem maiúscula, nome mais longo primeiro). Com `dados`, só os agentes disponíveis. */
export function mencoes(texto: string, dados?: Agentes) {
  const { f, de } = dobra(texto), out: { ini: number; fim: number; kind: string }[] = [];
  for (const m of f.matchAll(/(^|\s)@/g)) {
    const ini = m.index! + m[1].length, resto = f.slice(ini + 1);
    const a = MAIS_LONGO.find((x) => resto.startsWith(x.chave) && !/^[\p{L}\p{N}_]/u.test(resto.slice(x.chave.length)) && (!dados || acessoDe(dados, x.kind)));
    if (a) out.push({ ini: de[ini], fim: de[ini + 1 + a.chave.length], kind: a.kind });
  }
  return out;
}
/** A PRIMEIRA menção decide o agente; `texto` é a mensagem sem esse token (espaços normalizados; vazio = só havia a menção, ou só
 * pontuação). Emoji e símbolo contam como mensagem ("@Detetive 👍"; Codex no #1157). */
export function mencaoDe(texto: string, dados?: Agentes) {
  const m = mencoes(texto, dados)[0];
  if (!m) return null;
  const esq = texto.slice(0, m.ini).trimEnd(), dir = texto.slice(m.fim).trimStart();
  const msg = (esq + (/^[?!.,;:]/.test(dir) ? "" : " ") + dir).replace(/\s+/g, " ").trim();
  return { kind: m.kind, texto: /[^\p{P}\s]/u.test(msg) ? msg : "" };
}
/** O token em edição: "@" + letras até o cursor, logo depois de espaço ou no começo. */
export function tokenEm(texto: string, cursor: number) {
  const m = /(^|\s)@([\p{L}\p{M}]*)$/u.exec(texto.slice(0, cursor)); // \p{M}: o acento solto de um nome colado
  return m ? { ini: cursor - m[2].length - 1, q: semAcento(m[2]) } : null;
}
