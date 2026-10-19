import { useSyncExternalStore, type ReactNode } from "react";
import { go } from "../router";
import { DEMO, answer, type Answer, type TopicId } from "./topics";

// A conversa com o Piggy: vive enquanto a aba está aberta (troca de página não apaga;
// recarregar começa de novo). Guardar histórico é fase posterior, com backend.
export interface Msg extends Partial<Omit<Answer, "text">> { id: number; role: "user" | "piggy"; text: ReactNode }

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
