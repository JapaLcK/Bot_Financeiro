import { useSyncExternalStore, type ReactNode } from "react";
import { go } from "../router";
import { DEMO, answer, type Answer, type TopicId } from "./topics";
import { agenteDe, ativar, conversar, type Acesso, type Redirect } from "./agentes";
import { ErroApi } from "./v2";

// A conversa com o Piggy: vive enquanto a aba está aberta (troca de página não apaga;
// recarregar começa de novo). Guardar histórico é fase posterior, com backend.
export type EstadoAgente = "enviando" | "pronto" | "erro" | "inativo" | "sem_energia" | "sem_plano";
export interface Msg extends Partial<Omit<Answer, "text">> {
  id: number; role: "user" | "piggy"; text: ReactNode;
  // Conversa com um agente (@): `agente` é o kind (na bolha do usuário, o que ela destaca); o resto é o estado da resposta.
  agente?: string; estado?: EstadoAgente; redirects?: Redirect[]; pergunta?: string; repetir?: boolean;
  uid?: number; custo?: number; livre?: number; ativando?: boolean; falhaAtivar?: boolean;
}

let msgs: Msg[] = [];
let seq = 0;
const subs = new Set<() => void>();

export const useConversation = () => useSyncExternalStore((f) => { subs.add(f); return () => subs.delete(f); }, () => msgs);

// Texto livre que pergunta do total investido ("quanto tenho aplicado?") ou das metas ("como
// estão minhas metas?", só leitura) vai para a resposta real do servidor; o resto, sem IA no painel,
// recebe os atalhos.
const semAcento = (t: string) => t.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
const investido = (t: string) => /investid|aplicad/.test(semAcento(t));
// Meta só LEITURA: com verbo de escrita ("cria uma meta", "apaga a meta", "quero uma meta nova") a escrita é no painel
// (ou na IA, Etapa 7), então a frase segue para os atalhos. Referência: `_VERBO_ESCRITA` de core/intent_classifier.py.
const ESCRITA = /\b(?:(?:cri|cadastr|registr|apag|delet|remov|mud|alter|edit|troc|aument|atualiz|mont|defin)(?:a|e|ar|er|ue|ir)|exclu(?:i|a|ir)|diminu(?:i|a|ir)|renome(?:ia|ie|ar)|faz(?:er)?)\b|\bquero (?:uma|outra) /;
const metas = (t: string) => { const n = semAcento(t); return /\bmetas?\b/.test(n) && !ESCRITA.test(n); };

/** Envia a pergunta e abre a conversa. Sem `topic`, o Piggy responde com os atalhos. */
export function ask(q: { text: string; topic?: TopicId | null; cat?: string | null; key?: string }) {
  const topic = q.topic ?? (investido(q.text) ? "investido" : metas(q.text) ? "metas" : null);
  const a = topic ? answer(topic, q.cat ?? null, q.key) : DEMO;
  msgs = [...msgs, { id: ++seq, role: "user", text: q.text }, { id: ++seq, role: "piggy", ...a }];
  subs.forEach((f) => f());
  go("/piggy");
}

// --- Conversa com um agente (@Nome na barra) ---
// O context de cada agente (chave `uid:kind`: o servidor amarra o context ao usuário e ao agente, e a conta pode mudar noutra aba) e os que estão em voo vivem aqui, fora do React, com o `msgs`: a troca de página não os apaga.
const contextos = new Map<string, string | null>();
const emVoo = new Set<string>(); // chave `uid:kind`: uma pergunta da conta anterior ainda em voo não trava a conta nova
const notifica = () => subs.forEach((f) => f());
const patch = (id: number, p: Partial<Msg>) => { msgs = msgs.map((m) => (m.id === id ? { ...m, ...p } : m)); notifica(); };

// A conta dona da conversa. Se a sessão muda de conta noutra aba (login sem logout), a conversa e os
// contexts da anterior somem: as perguntas e respostas dela nunca aparecem para a conta nova (Codex no #1157).
let dono: number | undefined;
export function contaDaSessao(uid: number | undefined) {
  if (uid == null) return;
  if (dono != null && dono !== uid) { msgs = []; contextos.clear(); notifica(); }
  dono = uid;
}

const nomeDe = (kind: string) => agenteDe(kind)?.nome ?? "agente";
const SEM_ENERGIA = (kind: string, e?: { custo: number; livre: number }) => e
  ? `O ${nomeDe(kind)} usa ⚡${e.custo} de energia e você tem ${e.livre} livre.` : `Você está sem energia livre para ativar o ${nomeDe(kind)}.`;
const INATIVO = (kind: string, e?: { custo: number; livre: number }) => e
  ? `Ativar o ${nomeDe(kind)} usa ⚡${e.custo} de energia. Você tem ${e.livre} livre.` : `O ${nomeDe(kind)} ainda não está ativo.`;
const estadoSemChat = (kind: string, acesso: Exclude<Acesso, "ready">, e?: { custo: number; livre: number }): Partial<Msg> =>
  acesso === "upgrade" ? { estado: "sem_plano", text: `Conversar com o ${nomeDe(kind)} faz parte do PigBank+.` }
  : acesso === "no_energy" ? { estado: "sem_energia", text: SEM_ENERGIA(kind, e) }
  : { estado: "inativo", text: INATIVO(kind, e) };

const CODIGO_ACESSO: Record<string, Exclude<Acesso, "ready">> = { activate: "activate", upgrade: "upgrade", no_energy: "no_energy" };
const detalheDe = (e: unknown): { error?: string; message?: string; retryable?: boolean } => {
  const d = (e instanceof ErroApi ? (e.corpo as { detail?: unknown } | undefined)?.detail : undefined);
  return d && typeof d === "object" ? d : {};
};

const SEM_SENHA = "Crie sua senha para conversar com os agentes. Depois de criar, volte para o painel novo.";
/** `/agents/*` fica fora do `/api/v2`: o corpo real é `{"detail":{"error":"password_required"}}`; `code` cobre o envelope v2. */
const semSenha = (e: unknown) => e instanceof ErroApi && (e.code === "password_required" || detalheDe(e).error === "password_required");
const SESSAO = "Sua sessão terminou. Recarregue a página.";
// Sessão/CSRF: o mesmo TEXTO de MetasResposta/InvestidoResposta, critério próprio (401 e 403 sem `error` de objeto, que cobre o CSRF `{"detail": "Token CSRF…"}` e o "Acesso negado para este usuário" da troca de conta em outra aba). Repetir não adianta. Vale no chat e no "Ativar e responder".
const sessaoDe = (e: unknown) => e instanceof ErroApi && (e.status === 401 || (e.status === 403 && !detalheDe(e).error));

async function enviar(id: number, kind: string, texto: string, uid: number) {
  emVoo.add(`${uid}:${kind}`);
  patch(id, { estado: "enviando", text: "Preparando a resposta…", redirects: undefined, repetir: false });
  try {
    const r = await conversar(uid, kind, texto, contextos.get(`${uid}:${kind}`) ?? null);
    if (typeof r?.reply !== "string" || !r.reply.trim()) throw new Error("contrato");
    contextos.set(`${uid}:${kind}`, r.context ?? null);
    const redirects = (Array.isArray(r.redirects) ? r.redirects : []).filter((x) => agenteDe(x?.kind) && typeof x.question === "string" && x.access !== "unavailable");
    patch(id, { estado: "pronto", text: r.reply, redirects });
  } catch (e) {
    const d = detalheDe(e), status = e instanceof ErroApi ? e.status : -1;
    const acesso = status === 403 && d.error ? CODIGO_ACESSO[d.error] : undefined;
    if (d.error === "invalid_context") contextos.delete(`${uid}:${kind}`); // o próximo envio recomeça a conversa
    if (acesso) patch(id, estadoSemChat(kind, acesso));
    else if (semSenha(e) || sessaoDe(e)) patch(id, { estado: "erro", text: semSenha(e) ? SEM_SENHA : SESSAO, repetir: false });
    else patch(id, {
      estado: "erro",
      text: typeof d.message === "string" ? d.message
        : status === 0 ? "A conexão foi interrompida antes de recebermos a resposta. Sua pergunta continua aqui."
        : status === 429 ? "Muitas mensagens em pouco tempo. Aguarde um instante antes de tentar novamente."
        : "Não foi possível obter a resposta agora. Tente novamente em instantes.",
      repetir: d.retryable === true || status === 0, // só se o servidor marcou, ou se nem houve resposta (rede)
    });
  } finally {
    emVoo.delete(`${uid}:${kind}`);
  }
}

/** Pergunta a um agente: bolha do usuário (`original`, com as menções) e a resposta, que se atualiza por id. Falso = já há uma pergunta em voo a esse agente (o texto fica na barra). */
export function perguntarAoAgente(a: { kind: string; original: string; texto: string; uid: number; acesso: Acesso; energia?: { custo: number; livre: number } | null }) {
  contaDaSessao(a.uid); // o uid da pergunta é o da sessão agora: se mudou, a conversa velha sai antes
  if (a.acesso === "ready" && emVoo.has(`${a.uid}:${a.kind}`)) return false;
  const e = a.energia ?? undefined;
  const bolha = ++seq, resp = ++seq;
  msgs = [...msgs,
    { id: bolha, role: "user", text: a.original, agente: a.kind },
    { id: resp, role: "piggy", agente: a.kind, real: true, uid: a.uid, pergunta: a.texto, custo: e?.custo, livre: e?.livre, text: "", ...(a.acesso === "ready" ? {} : estadoSemChat(a.kind, a.acesso, e)) }];
  notifica();
  go("/piggy");
  if (a.acesso === "ready") void enviar(resp, a.kind, a.texto, a.uid);
  return true;
}

/** "Tentar de novo" de um erro que o servidor marcou como repetível. */
export function tentarDeNovo(id: number) {
  const m = msgs.find((x) => x.id === id);
  if (m?.estado === "erro" && m.repetir && m.agente && m.pergunta && m.uid != null && !emVoo.has(`${m.uid}:${m.agente}`)) void enviar(id, m.agente, m.pergunta, m.uid);
}

/** "Ativar e responder": ativa o agente e manda a pergunta guardada. Enquanto a ativação/o envio desse agente está em voo, o `emVoo` (por conta e agente) segura os outros cliques; duas bolhas inativas clicadas em sequência fazem duas ativações (o servidor aceita reativar). */
export async function ativarEResponder(id: number, depois: () => void) {
  const m = msgs.find((x) => x.id === id);
  if (!m || m.estado !== "inativo" || !m.agente || !m.pergunta || m.uid == null || emVoo.has(`${m.uid}:${m.agente}`)) return;
  emVoo.add(`${m.uid}:${m.agente}`); // o mesmo bloqueio por conta e agente do perguntarAoAgente, também durante a ativação
  patch(id, { ativando: true, falhaAtivar: false });
  try {
    await ativar(m.uid, m.agente);
  } catch (e) {
    const d = detalheDe(e), acesso = d.error === "no_energy" ? "no_energy" : d.error === "pro_required" ? "upgrade" : null;
    // Sem os números da bolha: são de quando ela nasceu, e a recusa mostra que a energia mudou depois (Codex no #1157).
    patch(id, acesso ? { ativando: false, ...estadoSemChat(m.agente, acesso) }
      : semSenha(e) || sessaoDe(e) ? { ativando: false, estado: "erro", text: semSenha(e) ? SEM_SENHA : SESSAO, repetir: false }
      : { ativando: false, falhaAtivar: true });
    emVoo.delete(`${m.uid}:${m.agente}`);
    return;
  }
  depois(); // a lista do @ relê o catálogo
  patch(id, { ativando: false });
  await enviar(id, m.agente, m.pergunta, m.uid);
}
