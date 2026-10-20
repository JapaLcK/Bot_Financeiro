// Os agentes no @ do chat: catálogo da tela, acesso de cada um e a menção. Porta de webapp/src/dashboard/lib/agentes.ts
// (`app/` e `webapp/` são projetos npm separados: o catálogo e os textos ficam duplicados).
// O servidor decide tudo (plano, energia, cota); aqui só se agrupa a lista.
import type { z } from "zod";
import type { agentesSchema } from "@/api/schemas/agentes";

export type Agentes = z.output<typeof agentesSchema>;
// Fonte da tela: kind e nome iguais a core/services/agent_chat.py::DOMAINS, e AGENTES igual ao do /painel (tests/test_painel_agentes_catalogo.py confere).
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
// A tela Agentes: os mesmos textos de webapp/src/dashboard/widgets/Agentes.tsx (agentes_tela.test.tsx confere). `foco`: onde a cena
// recorta na faixa aberta (0 = borda esquerda, 1 = direita), o `--foco` do /painel.
export const SOBRE: Record<string, { frase: string; texto: string; ritmo: string; foco: number }> = {
  xerife: { frase: "De olho nos excessos.", texto: "Avisa sobre gastos fora do padrão e categorias que ultrapassam os limites que você definiu.", ritmo: "Diário", foco: 0.55 },
  reporter: { frase: "Seu mês, em pauta.", texto: "Reúne entradas, saídas, aportes e quanto sobrou em um resumo mensal das suas finanças.", ritmo: "Mensal", foco: 0.43 },
  carteiro: { frase: "Vencimentos no radar.", texto: "Traz lembretes de vencimento dos seus boletos no feed para ajudar você a se organizar.", ritmo: "Contínuo", foco: 0.62 },
  detetive: { frase: "Os detalhes importam.", texto: "Identifica recorrências, assinaturas e cobranças que podem estar duplicadas.", ritmo: "Semanal", foco: 0.58 },
  cofre: { frase: "Cada caixinha conta.", texto: "Acompanha aportes, saídas e o progresso das caixinhas vinculadas ao Open Finance.", ritmo: "Dia do salário", foco: 0.48 },
  barao: { frase: "Saldo parado em foco.", texto: "Observa o dinheiro em conta corrente e mostra uma estimativa de rendimento comparada ao CDI.", ritmo: "Diário", foco: 0.52 },
  faria_limer: { frase: "Sua carteira à vista.", texto: "Apresenta o valor, o resultado e a concentração da sua carteira de ações e fundos imobiliários.", ritmo: "Mensal", foco: 0.49 },
};
/** A pergunta que "Conversar"/"Ativar" deixa no campo do chat, depois do "@Nome". */
export const PERGUNTA: Record<string, string> = {
  xerife: "Algum gasto fugiu do meu padrão?",
  reporter: "Como foi o meu mês?",
  carteiro: "Quais contas vencem nos próximos dias?",
  detetive: "Tem assinatura ou cobrança duplicada?",
  cofre: "Como estão as minhas caixinhas?",
  barao: "Tenho dinheiro parado que poderia render mais?",
  faria_limer: "Como está a minha carteira?",
};
/** "@Nome pergunta" de um kind do catálogo; kind desconhecido (param de link) = null. */
export const aberturaDe = (kind: string | undefined) => { const a = kind ? agenteDe(kind) : undefined; return a ? `@${a.nome} ${PERGUNTA[a.kind]}` : null; };

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
const sem = (t: string) => t.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
/** O texto sem acento e minúsculo para comparar (a marca solta de texto colado, "e" + U+0301, também some) e, para cada
 * posição dele, a posição no original: as menções se medem no texto dobrado e se recortam no original (Hangul vira jamos). */
function dobra(t: string) {
  let f = "";
  const de: number[] = [];
  for (let i = 0; i < t.length; i++) for (const c of sem(t[i]!)) { f += c; de.push(i); }
  de.push(t.length);
  return { f, de };
}
export const semAcento = (t: string) => dobra(t).f;
/** Só quem tem `@` no começo ou depois de espaço pode ser menção (e só esse texto toca em `/agents`). */
export const TEM_ARROBA = /(^|\s)@/;
const MAIS_LONGO = [...AGENTES].sort((a, b) => b.nome.length - a.nome.length).map((a) => ({ ...a, chave: semAcento(a.nome) }));

/** Toda menção válida (nome do catálogo, sem acento nem maiúscula, nome mais longo primeiro). Com `dados`, só os agentes disponíveis. */
export function mencoes(texto: string, dados?: Agentes) {
  const { f, de } = dobra(texto), out: { ini: number; fim: number; kind: string }[] = [];
  for (const m of f.matchAll(new RegExp(TEM_ARROBA, "g"))) {
    const ini = m.index + m[1]!.length, resto = f.slice(ini + 1);
    const a = MAIS_LONGO.find((x) => resto.startsWith(x.chave) && !/^[a-z0-9_]/.test(resto.slice(x.chave.length)) && (!dados || acessoDe(dados, x.kind)));
    if (a) out.push({ ini: de[ini]!, fim: de[ini + 1 + a.chave.length]!, kind: a.kind });
  }
  return out;
}
/** A PRIMEIRA menção decide o agente; `texto` é a mensagem sem esse token (espaços normalizados; vazio = só havia a menção, ou só
 * pontuação). Emoji e símbolo contam como mensagem ("@Detetive 👍"), como no /painel desde o #1157. */
export function mencaoDe(texto: string, dados?: Agentes) {
  const m = mencoes(texto, dados)[0];
  if (!m) return null;
  const esq = texto.slice(0, m.ini).trimEnd(), dir = texto.slice(m.fim).trimStart();
  const msg = (esq + (/^[?!.,;:]/.test(dir) ? "" : " ") + dir).replace(/\s+/g, " ").trim();
  return { kind: m.kind, texto: /[^\p{P}\s]/u.test(msg) ? msg : "" };
}
/** O token em edição: "@" + letras até o cursor, logo depois de espaço ou no começo; `fim` = onde as letras dele acabam (o cursor pode estar no meio). */
export function tokenEm(texto: string, cursor: number) {
  const m = /(^|\s)@([\p{L}\p{M}]*)$/u.exec(texto.slice(0, cursor)); // \p{M}: o acento solto de um nome colado
  // fim: o token continua depois do cursor (cursor no meio de "@repórter"); escolher um item troca o token inteiro, não só o prefixo
  return m ? { ini: cursor - m[2]!.length - 1, fim: cursor + /^[\p{L}\p{M}]*/u.exec(texto.slice(cursor))![0].length, q: semAcento(m[2]!) } : null;
}
/** Os agentes da lista do @: disponíveis, com o prefixo digitado (sem acento) e na ordem dos grupos; dentro do grupo, a do catálogo. */
export function itensMencao(d: Agentes, q: string) {
  const ordem = Object.keys(GRUPO);
  return AGENTES.flatMap((a) => { const acesso = acessoDe(d, a.kind); return acesso && semAcento(a.nome).startsWith(q) ? [{ ...a, acesso }] : []; })
    .sort((x, y) => ordem.indexOf(x.acesso) - ordem.indexOf(y.acesso));
}
